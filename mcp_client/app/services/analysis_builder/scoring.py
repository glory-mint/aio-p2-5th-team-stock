import math
from typing import Any

from app.schemas.analysis import CollectedData, EvidenceLevel, MarketTemperature
from app.services.analysis_builder.issues import extract_issues, short_issue
from app.services.analysis_builder.narrative import josa
from app.services.analysis_builder.matching import disclosure_date_label, match_issues


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _component_score(value: float, divisor: float, weight: int) -> int:
    normalized = max(0.0, min(value / divisor, 1.0))
    return round(normalized * weight)


# 뉴스 관심 = 관련 기사 100건이 쌓이는 데 걸리는 시간. 6시간이면 만점, 7일(168시간) 이상이면 0점, 사이는 로그 스케일.
_NEWS_HOT_HOURS_PER_100 = 6.0
_NEWS_COLD_HOURS_PER_100 = 168.0


def _news_attention_score(count: float, span_hours: float | None, weight: int) -> int:
    if span_hours is None or span_hours <= 0 or count <= 0:
        # 구버전 News MCP(span 없음) 폴백: 절대 건수 80건 만점
        return _component_score(count, 80.0, weight)
    hours_per_100 = span_hours * 100.0 / count
    clamped = max(_NEWS_HOT_HOURS_PER_100, min(hours_per_100, _NEWS_COLD_HOURS_PER_100))
    normalized = (math.log(_NEWS_COLD_HOURS_PER_100) - math.log(clamped)) / (
        math.log(_NEWS_COLD_HOURS_PER_100) - math.log(_NEWS_HOT_HOURS_PER_100)
    )
    return round(normalized * weight)


def _temperature_label(score: int) -> str:
    if score < 20:
        return "관심 낮음"
    if score < 40:
        return "관심 다소 낮음"
    if score < 60:
        return "보통"
    if score < 80:
        return "관심 높음"
    return "관심 매우 높음"


def calculate_market_temperature(data: CollectedData) -> MarketTemperature:
    components: dict[str, int] = {}
    weight_covered = 0

    if data.price.get("status") == "success":
        volume_ratio = _optional_float(data.price.get("volume_ratio_20d"))
        if volume_ratio is None:
            volume_change_rate = _optional_float(data.price.get("volume_change_rate"))
            if volume_change_rate is not None:
                volume_ratio = 1.0 + volume_change_rate / 100.0
        if volume_ratio is not None:
            components["volume_activity"] = _component_score(volume_ratio, 3.0, 30)
            weight_covered += 30

    if data.news.get("status") == "success":
        news_count = _optional_float(data.news.get("relevant_count"))
        if news_count is None:
            news_count = _optional_float(data.news.get("result_count"))
        if news_count is not None:
            components["news_attention"] = _news_attention_score(
                news_count, _optional_float(data.news.get("span_hours")), 25
            )
            weight_covered += 25

    if data.community.get("status") == "success":
        activity = data.community.get("activity")
        activity_ratio = (
            _optional_float(activity.get("ratio")) if isinstance(activity, dict) else None
        )
        if activity_ratio is not None:
            components["community_activity"] = _component_score(activity_ratio, 3.0, 25)
            weight_covered += 25

        fgi_latest = data.community.get("fgi_latest")
        fgi = _optional_float(fgi_latest.get("fgi")) if isinstance(fgi_latest, dict) else None
        if fgi is not None:
            components["fear_greed_intensity"] = round(
                max(0.0, min(abs(fgi - 50.0) / 50.0, 1.0)) * 20
            )
            weight_covered += 20

    score = (
        max(0, min(100, round(sum(components.values()) / weight_covered * 100)))
        if weight_covered
        else 0
    )
    coverage = []
    if data.price.get("status") == "success":
        coverage.append("price")
    if data.news.get("status") == "success":
        coverage.append("news")
    if data.disclosures.get("status") == "success" or data.annual_report.get("status") == "success":
        coverage.append("disclosure")
    if data.community.get("status") in {"success", "partial_success"}:
        coverage.append("community")

    return MarketTemperature(
        score=score,
        label=_temperature_label(score),
        data_coverage=coverage,
        components=components,
        weight_covered=weight_covered,
    )


def calculate_evidence_level(data: CollectedData, coverage: list[str]) -> EvidenceLevel:
    # coverage는 이전 호출부 호환을 위해 유지한다. 근거 단계는 자료 종류 수로 판정하지 않는다.
    _ = coverage
    issues = extract_issues(data, str(data.price.get("company_name") or ""))
    status = data.material_disclosures.get("status")
    if status not in {"success", "no_data"}:
        return EvidenceLevel(
            level="low",
            reason="최근 공시를 확인하지 못했어요",
            unmatched=issues,
        )

    result = match_issues(issues, data.material_disclosures)
    if result.matched:
        first = result.matched[0]
        extra = f" 외 {len(result.matched) - 1}건" if len(result.matched) > 1 else ""
        issue_label = first.issue if len(first.issue) <= 30 else first.issue[:29] + "…"
        reason = (
            f"{josa(issue_label, '이', '가')} {disclosure_date_label(first.published_at)} "
            f"{first.report_name.strip()} 공시와 맞아요{extra}"
        )
        level = "high"
    elif result.material_count:
        reason = (
            f"최근 30일 주요 공시 {result.material_count}건은 있지만 "
            f"지금 화제({short_issue(issues[0])})와 직접 연결되진 않아요"
            if issues
            else f"최근 30일 주요 공시 {result.material_count}건은 있지만 화제로 잡힌 이슈가 없어요"
        )
        level = "medium"
    else:
        reason = (
            "최근 30일 안에 주요 공시가 없어요. 정기보고서만 있어요"
            if issues
            else "화제로 잡힌 이슈가 없어요"
        )
        level = "low"

    return EvidenceLevel(
        level=level,
        reason=reason,
        matched=result.matched,
        unmatched=result.unmatched,
        material_count=result.material_count,
    )
