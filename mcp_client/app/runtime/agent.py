from dataclasses import dataclass, field
import json
from typing import Any

from app.agents import StockAnalysisAgent
from app.agents.policy import RISK_READ, action_risk
from app.clients.base import MCPClientError
from app.clients.disclosure import DisclosureMCPClient
from app.providers.openai import FunctionCall, NarrativeProvider, ProviderError
from app.runtime.verifier import Violation, verify_narrative
from app.schemas.analysis import Narrative, ToolFailure
from app.services.analysis_builder.narrative import build_fallback_narrative
from app.services.progress_reporter import ProgressReporter


@dataclass
class ReflectionEvent:
    kind: str
    detail: str
    attempt: int
    resolved: bool = False


@dataclass
class AgentResult:
    narrative: Narrative
    termination_reason: str
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    completed_tools: list[str] = field(default_factory=list)
    failed_tools: list[str] = field(default_factory=list)
    failures: list[ToolFailure] = field(default_factory=list)
    reflections: list[ReflectionEvent] = field(default_factory=list)
    reflection_calls: int = 0


def _stop(result: AgentResult, reason: str, message: str) -> AgentResult:
    result.termination_reason = reason
    result.failures.append(ToolFailure(
        service="openai", status=reason, message=message, retryable=reason == "model_error",
    ))
    return result


def _call_output(call: FunctionCall, detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call_output", "call_id": call.call_id,
        "output": json.dumps(detail, ensure_ascii=False),
    }


class StockAgentRuntime:
    def __init__(
        self,
        provider: NarrativeProvider,
        disclosure: DisclosureMCPClient,
        max_steps: int,
        *,
        reflection_enabled: bool = False,
        max_reflections: int = 2,
    ) -> None:
        self.provider = provider
        self.disclosure = disclosure
        self.max_steps = max_steps
        self.profile = StockAnalysisAgent()
        self.reflection_enabled = reflection_enabled
        self.max_reflections = max(0, max_reflections)

    @staticmethod
    def _tools(receipt_numbers: list[str]) -> list[dict[str, Any]]:
        if not receipt_numbers:
            return []
        return [
            {
                "type": "function",
                "name": "get_disclosure_detail",
                "description": "최근 공시 제목만으로 설명이 부족할 때 공식 공시 상세 내용을 조회합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "receipt_number": {
                            "type": "string",
                            "enum": receipt_numbers,
                            "description": "기본 조회 결과에 포함된 DART 접수번호",
                        }
                    },
                    "required": ["receipt_number"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ]

    async def run(
        self,
        context: dict[str, Any],
        receipt_numbers: list[str],
        reporter: ProgressReporter,
    ) -> AgentResult:
        if self.reflection_enabled:
            return await self._run_reflecting(context, receipt_numbers, reporter)
        return await self._run_legacy(context, receipt_numbers, reporter)

    def _call_error(
        self, call: FunctionCall, allowed: set[str], used: set[str], tools_enabled: bool,
    ) -> tuple[str | None, Violation | None]:
        risk = action_risk(call.name, self.profile.allowed_tools)
        if risk != RISK_READ:
            return None, Violation("tool_selection_error", f"위험도 {risk}: 자동 실행하지 않습니다")
        if not tools_enabled:
            return None, Violation("tool_selection_error", "현재 허용되지 않은 Tool입니다.")
        try:
            arguments = json.loads(call.arguments)
        except (json.JSONDecodeError, TypeError):
            return None, Violation("parameter_error", "arguments는 유효한 JSON Object여야 합니다.")
        if not isinstance(arguments, dict) or not isinstance(arguments.get("receipt_number"), str):
            return None, Violation("parameter_error", "receipt_number 문자열이 필요합니다.")
        if set(arguments) != {"receipt_number"}:
            return None, Violation("parameter_error", "receipt_number 외의 인자는 허용하지 않습니다.")
        receipt = arguments["receipt_number"]
        if receipt not in allowed or receipt in used or len(used) >= 2:
            return None, Violation("tool_selection_error", "목록 밖·중복 접수번호 또는 상세 2건 상한 위반입니다.")
        return receipt, None

    async def _fetch_detail(
        self, call: FunctionCall, receipt: str, reporter: ProgressReporter, result: AgentResult,
    ) -> tuple[dict[str, Any], bool]:
        await reporter.publish(
            "tool_started", "analyzing", "running",
            "중요한 공시의 상세 근거를 확인하고 있어요.", 80,
            tool_name=call.name, service="disclosure_mcp",
        )
        result.tool_calls += 1
        try:
            detail = await self.disclosure.get_disclosure_detail(receipt)
        except MCPClientError as error:
            result.failed_tools.append(call.name)
            result.failures.append(ToolFailure(
                service=error.service, status=error.code,
                message=error.message, retryable=error.retryable,
            ))
            return {"status": error.code, "error": {
                "message": error.message, "retryable": error.retryable,
            }}, False
        result.completed_tools.append(call.name)
        return detail, True

    async def _run_reflecting(
        self, context: dict[str, Any], receipt_numbers: list[str], reporter: ProgressReporter,
    ) -> AgentResult:
        ordered = list(dict.fromkeys(receipt_numbers))[:5]
        tools = self._tools(ordered)
        active_tools = tools
        result = AgentResult(build_fallback_narrative(context), "model_error")
        verification_context = {
            **context, "failed_tools": list(context.get("failed_tools") or []),
            "data": {**context["data"], "disclosure_details": []},
        }
        used: set[str] = set()
        pending: list[ReflectionEvent] = []
        schema_retried = narrative_retried = feedback_pending = False
        tools_closed = False
        response_id = None
        outputs: list[dict[str, Any]] = []

        await reporter.publish("llm_started", "analyzing", "running", "수집한 자료를 종합하고 있어요.", 70)
        # Preserve the existing bound: one initial call, then at most max_steps continuations.
        for step in range(self.max_steps + 1):
            result.llm_calls += 1
            result.reflection_calls += int(feedback_pending)
            errors: list[Violation] = []
            turn = None
            try:
                turn = (
                    await self.provider.first_turn(context, active_tools) if response_id is None
                    else await self.provider.next_turn(response_id, outputs, active_tools)
                )
            except ProviderError as error:
                if not error.response_id:
                    return _stop(result, "model_error", "Luna 분석을 완료하지 못해 기본 설명을 제공합니다.")
                response_id = error.response_id
                result.input_tokens += error.input_tokens
                result.output_tokens += error.output_tokens
                errors = [Violation("schema_mismatch", str(error))]
            except Exception:
                return _stop(result, "model_error", "Luna 분석을 완료하지 못해 기본 설명을 제공합니다.")
            else:
                response_id = turn.response_id
                result.input_tokens += turn.input_tokens
                result.output_tokens += turn.output_tokens
                if not turn.calls:
                    if turn.narrative is None:
                        return _stop(result, "model_error", "Luna가 분석 문장을 반환하지 않았습니다.")
                    errors = verify_narrative(turn.narrative, verification_context)
                else:
                    outputs = []
                    for call in turn.calls:
                        await reporter.publish(
                            "model_selected_tool", "analyzing", "running", "모델이 Tool을 요청했어요.", 80,
                            tool_name=call.name, owner="ai_agent",
                        )
                        receipt, error = self._call_error(call, set(ordered), used, bool(active_tools))
                        if error:
                            await reporter.publish(
                                "policy_blocked_call", "analyzing", "running",
                                f"{error.kind}: {error.detail}", 80, tool_name=call.name, owner="policy",
                            )
                            errors.append(error)
                            detail = {
                                "status": "invalid_tool_call",
                                "error": {"kind": error.kind, "message": error.detail},
                                "allowed_receipt_numbers": ordered,
                            }
                        else:
                            if step == self.max_steps:
                                continue  # Inspect the rest of the batch before recording/terminating.
                            used.add(receipt)
                            detail, ok = await self._fetch_detail(call, receipt, reporter, result)
                            if ok:
                                verification_context["data"]["disclosure_details"].append(detail)
                            else:
                                verification_context["failed_tools"].append(call.name)
                        outputs.append(_call_output(call, detail))

            kinds = {error.kind for error in errors}
            for event in pending:
                can_verify = turn is not None and (
                    not turn.calls or event.kind in {"tool_selection_error", "parameter_error"}
                )
                event.resolved = can_verify and event.kind not in kinds
            pending = [event for event in pending if not event.resolved]
            feedback_pending = bool(errors)
            if errors:
                detected = [ReflectionEvent(error.kind, error.detail, result.reflection_calls + 1) for error in errors]
                result.reflections.extend(detected)
                pending.extend(detected)
                schema_error = "schema_mismatch" in kinds
                prose_error = bool(kinds & {"hallucination", "inconsistency"})
                if (
                    result.reflection_calls >= self.max_reflections or step == self.max_steps
                    or (schema_error and schema_retried) or (prose_error and narrative_retried)
                ):
                    return _stop(result, "reflection_exhausted", "성찰 상한 또는 재검증 실패로 기본 설명을 제공합니다.")
                if schema_error or prose_error:
                    tools_closed = True
                    schema_retried |= schema_error
                    narrative_retried |= prose_error
                    outputs = [{"role": "user", "content": (
                        "형식 오류 피드백: " if schema_error else "서술 검증 피드백: "
                    ) + " / ".join(error.detail for error in errors)
                        + " 제공된 자료만 사용해 위반을 수정하고 전체 JSON을 다시 반환하세요. Tool은 사용하지 마세요."}]
                    active_tools = []
                    await reporter.publish(
                        "reflection_requested", "analyzing", "running",
                        "형식·서술 검증 피드백으로 재호출합니다.", 80, owner="policy",
                    )
                else:
                    active_tools = tools if len(used) < 2 and not tools_closed else []
            elif turn is not None and not turn.calls:
                result.narrative = turn.narrative
                result.termination_reason = "completed"
                await reporter.publish(
                    "llm_completed", "analyzing", "running", "자료 종합을 마쳤어요.", 90, service="openai",
                )
                return result
            else:
                if step == self.max_steps:
                    return _stop(result, "max_steps_exceeded", "Agent 최대 반복 횟수를 초과했습니다.")
                active_tools = tools if len(used) < 2 and not tools_closed else []
        return _stop(result, "max_steps_exceeded", "Agent 최대 반복 횟수를 초과했습니다.")

    async def _run_legacy(
        self,
        context: dict[str, Any],
        receipt_numbers: list[str],
        reporter: ProgressReporter,
    ) -> AgentResult:
        ordered_receipts = list(dict.fromkeys(receipt_numbers))[:5]
        allowed_receipts = set(ordered_receipts)
        tools = self._tools(ordered_receipts)
        fallback = build_fallback_narrative(context)
        result = AgentResult(narrative=fallback, termination_reason="model_error")

        await reporter.publish(
            "llm_started",
            "analyzing",
            "running",
            "수집한 자료를 종합하고 있어요.",
            70,
        )
        try:
            turn = await self.provider.first_turn(context, tools)
        except Exception:
            _stop(result, "model_error", "Luna 분석을 완료하지 못해 규칙 기반 설명을 제공합니다.")
            await reporter.publish(
                "llm_failed",
                "analyzing",
                "partial_success",
                "AI 설명 생성에 실패해 기본 설명을 사용했어요.",
                90,
                service="openai",
            )
            return result

        result.llm_calls += 1
        result.input_tokens += turn.input_tokens
        result.output_tokens += turn.output_tokens
        used_receipts: set[str] = set()

        for _step in range(1, self.max_steps + 1):
            if not turn.calls:
                if turn.narrative is None:
                    return _stop(result, "model_error", "Luna가 분석 문장을 반환하지 않았습니다.")
                result.narrative = turn.narrative
                result.termination_reason = "completed"
                await reporter.publish(
                    "llm_completed",
                    "analyzing",
                    "running",
                    "자료 종합을 마쳤어요.",
                    90,
                    service="openai",
                )
                return result

            outputs = []
            for call in turn.calls:
                try:
                    risk = action_risk(call.name, self.profile.allowed_tools)
                    if risk != RISK_READ:
                        raise ValueError(f"위험도 {risk}: 자동 실행하지 않습니다")
                    arguments = json.loads(call.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments는 JSON Object여야 합니다.")
                    receipt_number = arguments.get("receipt_number")
                    if receipt_number not in allowed_receipts:
                        raise ValueError("기본 조회 결과에 없는 접수번호입니다.")
                    if receipt_number in used_receipts:
                        raise ValueError("같은 공시를 반복 조회할 수 없습니다.")
                except (json.JSONDecodeError, TypeError, ValueError) as error:
                    return _stop(result, "invalid_tool_call", str(error))

                used_receipts.add(receipt_number)
                detail, _ = await self._fetch_detail(call, receipt_number, reporter, result)
                outputs.append(_call_output(call, detail))

            active_tools = [] if len(used_receipts) >= 2 else tools
            try:
                turn = await self.provider.next_turn(turn.response_id, outputs, active_tools)
            except Exception:
                return _stop(result, "model_error", "상세 공시 확인 후 Luna 분석을 완료하지 못했습니다.")
            result.llm_calls += 1
            result.input_tokens += turn.input_tokens
            result.output_tokens += turn.output_tokens

        if not turn.calls and turn.narrative is not None:
            result.narrative = turn.narrative
            result.termination_reason = "completed"
            return result
        return _stop(result, "max_steps_exceeded", "Agent 최대 반복 횟수를 초과해 기본 설명을 제공합니다.")
