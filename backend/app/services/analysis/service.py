import logging

from datetime import datetime, timezone
from uuid import uuid4

from app.clients.mcp_client import client as mcp_client
from app.clients.mcp_client.client import MCPClientError, MCPClientTimeout, MCPClientUnavailable
from app.clients.redis import client as redis_client
from app.repositories import analysis_repository
from app.schemas.analysis import (
    AnalysisDetail,
    AnalysisResponse,
    CompanyRef,
    EvidenceLevel,
    MarketTemperature,
    PriceSnapshot,
    UnsupportedCompanyResponse,
)
from app.schemas.errors import ErrorDetail, ErrorResponse
from app.schemas.user import CurrentUser
from app.services.analysis import companies
from app.schemas.analysis import PersonalizedCheckpoints
from app.services.profile import service as profile_service

logger = logging.getLogger(__name__)

UNSUPPORTED_MESSAGE = (
    "아직 이 기업의 분석 정보는 제공하지 않습니다. "
    "현재는 2026년 9월 1일 기준 코스피 시가총액 상위 20개 기업만 지원하고 있어요."
)
UNSUPPORTED_ACTIONS = ["지원 기업 20개 보기", "다른 종목 검색하기"]


def _mcp_client_error_response(request_id: str, exc: Exception) -> ErrorResponse:
    if isinstance(exc, MCPClientTimeout):
        status, code, message, retryable = (
            "timeout",
            "MCP_CLIENT_TIMEOUT",
            "분석 요청이 시간 초과되었습니다.",
            True,
        )
    elif isinstance(exc, MCPClientUnavailable):
        status, code, message, retryable = (
            "external_api_error",
            "MCP_CLIENT_UNAVAILABLE",
            "분석 서버에 일시적으로 연결할 수 없습니다.",
            True,
        )
    else:
        status, code, message, retryable = (
            "internal_error",
            "MCP_CLIENT_INVALID_RESPONSE",
            "분석 결과를 처리하지 못했습니다.",
            False,
        )
    logger.warning("mcp_client 호출 실패 request_id=%s status=%s error=%s", request_id, status, exc)
    return ErrorResponse(
        request_id=request_id,
        status=status,
        error=ErrorDetail(service="mcp_client", code=code, message=message, retryable=retryable),
    )


async def _remember_recent_search(user_id: str, company_name: str, stock_code: str, searched_at: datetime) -> None:
    await redis_client.set_state(
        user_id,
        recent_company_name=company_name,
        recent_stock_code=stock_code,
        searched_at=searched_at.isoformat().replace("+00:00", "Z"),
    )


async def run_analysis(
    query: str, current_user: CurrentUser | None
) -> AnalysisResponse | UnsupportedCompanyResponse | ErrorResponse:
    company = companies.resolve_company(query)
    if company is None:
        return UnsupportedCompanyResponse(message=UNSUPPORTED_MESSAGE, actions=UNSUPPORTED_ACTIONS)

    company_name = company["company_name"]
    stock_code = company["stock_code"]
    request_id = str(uuid4())

    profile = await profile_service.get_profile(current_user.user_id) if current_user else None
    requested_at = datetime.now(timezone.utc)
    try:
        raw = await mcp_client.fetch_common_analysis(company_name, stock_code, profile, request_id)
    except (MCPClientTimeout, MCPClientUnavailable, MCPClientError) as exc:
        return _mcp_client_error_response(request_id, exc)

    common = raw["common_analysis"]
    sources = raw.get("sources", [])
    temperature = common["market_temperature"]
    evidence = common["evidence_level"]
    # 서사(한 줄·개인화)는 MCP Client가 책임진다. LLM이 실패하면 MCP Client가 규칙 기반 대체 서사를 채워 보낸다.
    personalized = raw.get("personalized_checkpoints")

    response = AnalysisResponse(
        request_id=raw["request_id"],
        status=raw["status"],
        access_level="guest",
        requires_login=True,
        company=CompanyRef(company_name=company_name, stock_code=stock_code, supported=True),
        price=PriceSnapshot(**raw["price"]),
        one_line_summary=common["one_line_summary"],
    )

    if profile is not None:
        response.access_level = "member"
        response.requires_login = False
        response.detail = AnalysisDetail(
            market_temperature=MarketTemperature(**temperature),
            evidence_level=EvidenceLevel(**evidence),
            news_summary=common["news_summary"],
            disclosure_summary=common["disclosure_summary"],
            community_summary=common["community_summary"],
            sources=sources,
        )
        response.personalized_checkpoints = PersonalizedCheckpoints(**personalized) if personalized else None
        await _remember_recent_search(current_user.user_id, company_name, stock_code, requested_at)

    await analysis_repository.save_run(
        request_id=response.request_id,
        user_id=current_user.user_id if current_user else None,
        company_name=company_name,
        stock_code=stock_code,
        access_level=response.access_level,
        status=response.status,
        one_line_summary=response.one_line_summary,
        sources=sources,
        partial_failures=raw.get("partial_failures", []),
        personalized_checkpoints=personalized,
        requested_at=requested_at,
        collected_at=raw.get("collected_at"),
    )
    return response
