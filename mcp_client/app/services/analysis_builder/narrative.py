from typing import Any

from app.schemas.analysis import Narrative, PersonalizedCheckpoints
from app.services.analysis_builder.issues import short_issue
from app.services.analysis_builder.matching import disclosure_date_label


PREFERRED_CHECK = {
    "financial": "최근 사업보고서의 매출·영업이익 흐름",
    "news": "최근 기사 내용이 공시로 확인되는지",
    "market": "거래량이 평소보다 늘었는지",
    "risk": "사업보고서의 위험 요인 중 지금 현실화된 게 있는지",
}


def _josa(word: str, consonant: str, vowel: str) -> str:
    if word:
        code = ord(word[-1])
        if 0xAC00 <= code <= 0xD7A3:
            return f"{word}{consonant if (code - 0xAC00) % 28 else vowel}"
    return f"{word}{vowel}"


def build_fallback_narrative(context: dict[str, Any]) -> Narrative:
    temperature = context["market_temperature"]
    evidence = context["evidence_level"]
    news = context["data"].get("news") or {}
    annual_report = context["data"].get("annual_report") or {}
    community = context["data"].get("community") or {}

    news_count = int(news.get("result_count") or 0)
    passage_count = len(annual_report.get("matched_passages") or [])
    sample_size = int(community.get("sample_size") or 0)

    personalized = None
    profile = context.get("investment_profile")
    if profile:
        preferred_check = PREFERRED_CHECK.get(profile["preferred_evidence"], "공식 근거")
        matches = evidence.get("matched") or []
        evidence_check = (
            f"{disclosure_date_label(matches[0].get('published_at'))} "
            f"{matches[0].get('report_name')} 공시 내용"
            if matches
            else "현재 관심이 실제 기업 변화와 연결되는지"
        )
        horizons = {"short": "단기", "medium": "중기", "long": "장기"}
        personalized = PersonalizedCheckpoints(
            personal_summary=(
                f"{horizons.get(profile['investment_horizon'], profile['investment_horizon'])} 관점에서는 "
                f"{preferred_check}부터 확인해 보세요."
            ),
            priority_checks=[
                preferred_check,
                evidence_check,
            ],
            caution="관심 온도는 가격 방향이나 수익을 예측하는 지표가 아닙니다.",
        )

    topic = next(
        (item.get("issue") for item in (evidence.get("matched") or []) if item.get("issue")),
        None,
    )
    if not topic:
        topic = next(iter(evidence.get("unmatched") or []), None)
    if not topic:
        top_topics = community.get("top_topics") or {}
        topic = next(iter(top_topics.get("expectations") or []), "최근 이슈")
    change = int(context["data"].get("price", {}).get("change") or 0)
    if evidence["level"] == "low" and temperature["score"] >= 60:
        one_line = (
            f"뉴스는 {_josa(topic, '으로', '로')} 시끄러운데, 공시로 확인된 건 거의 없어요. "
            "기사만으로 띄우는 흐름일 수 있어요."
        )
    else:
        evidence_copy = {
            "high": "관련 공시가 실제로 있어요",
            "medium": "주요 공시는 있지만 지금 화제와는 달라요",
            "low": "공식 확인은 아직 조금이에요",
        }[evidence["level"]]
        community_copy = (
            "커뮤니티는 기대가 앞서요" if change >= 0 else "커뮤니티는 조심스러워요"
        )
        one_line = f"뉴스는 {topic}에 쏠려 있고, {evidence_copy}. {community_copy}."

    matches = evidence.get("matched") or []
    if matches:
        first = matches[0]
        disclosure_summary = (
            f"{disclosure_date_label(first.get('published_at'))} {first.get('report_name')} 공시에서 "
            f"{_josa(short_issue(str(first.get('issue') or '')), '을', '를')} 확인했어요."
        )
    else:
        disclosure_summary = (
            f"최근 주요 공시 {evidence.get('material_count', 0)}건을 확인했지만 "
            "현재 화제와 직접 연결된 공시는 없어요."
        )
    if passage_count == 0:
        disclosure_summary += " 사업보고서에서 관련 내용을 찾지 못했어요."

    return Narrative(
        one_line_summary=one_line,
        news_summary=f"최근 확인된 관련 뉴스는 {news_count}건입니다. 기사 제목과 출처를 함께 확인해야 합니다.",
        disclosure_summary=disclosure_summary,
        community_summary=(
            f"최근 커뮤니티 표본은 {sample_size}건입니다. 커뮤니티 반응은 관심 분위기를 보여줄 뿐 사실 확인 자료는 아닙니다."
        ),
        personalized_checkpoints=personalized,
    )
