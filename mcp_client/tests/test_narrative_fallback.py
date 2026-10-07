import pytest

from app.services.analysis_builder.narrative import (
    compose_one_liner,
    compose_personal,
    gap_state,
    josa,
    pick_topic,
)

PROFILE = {
    "experience_level": "beginner",
    "risk_profile": "conservative",
    "investment_horizon": "long",
    "preferred_evidence": "financial",
}


@pytest.mark.parametrize(
    ("word", "expected"),
    [("삼성", "삼성은"), ("삼성전자", "삼성전자는"), ("HBM", "HBM는")],
)
def test_josa_matches_frontend_rule(word, expected):
    assert josa(word, "은", "는") == expected


def test_pick_topic_uses_highest_weight_community_topic():
    community = {
        "status": "success",
        "top_topics": {"expectations": ["HBM 메모리", "파운드리"], "concerns": ["환율"]},
    }

    assert pick_topic(community) == "HBM 메모리"


@pytest.mark.parametrize(
    "community",
    [
        {},
        {"status": "success", "top_topics": {}},
        {"status": "external_api_error", "top_topics": {"expectations": ["HBM 메모리"]}},
    ],
)
def test_pick_topic_uses_fixed_fallback_without_community_topics(community):
    assert pick_topic(community) == "최근 이슈"


def test_guest_one_liner_uses_frontend_rule():
    assert compose_one_liner("HBM 메모리", "low", 50, 1) == (
        "뉴스는 HBM 메모리에 쏠려 있고, 공식 확인은 아직 조금이에요. 커뮤니티는 기대가 앞서요."
    )


def test_one_liner_warns_when_attention_outruns_evidence():
    assert compose_one_liner("자사주 매입", "low", 60, -1).startswith(
        "뉴스는 자사주 매입으로 시끄러운데, 공시로 확인된 건 거의 없어요."
    )


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("high", "관련 공시가 실제로 있어요"),
        ("medium", "주요 공시는 있지만 지금 화제와는 달라요"),
    ],
)
def test_one_liner_uses_issue_connection_evidence_copy(level, expected):
    assert expected in compose_one_liner("공급계약", level, 60, 1)


def test_member_personal_summary_uses_risk_gap_rule():
    result = compose_personal("삼성전자", "HBM 메모리", 50, "low", PROFILE)

    assert result.personal_summary == (
        "관심과 근거가 균형을 이루고 있어요. 삼성전자는 시장의 관심과 확인된 재료가 비슷해요. "
        "손실을 피하는 걸 우선하는 오래 들고 가는 편이라면 HBM 메모리 실제 흐름만 꾸준히 따라가면 돼요."
    )


@pytest.mark.parametrize(
    ("preferred_evidence", "expected"),
    [
        ("financial", "최근 사업보고서의 매출·영업이익 흐름"),
        ("news", "최근 기사 내용이 공시로 확인되는지"),
        ("market", "거래량이 평소보다 늘었는지"),
        ("risk", "사업보고서의 위험 요인 중 지금 현실화된 게 있는지"),
    ],
)
def test_personalized_first_check_follows_preferred_evidence(preferred_evidence, expected):
    profile = {**PROFILE, "preferred_evidence": preferred_evidence}

    result = compose_personal("삼성전자", "공급계약", 60, "high", profile)

    assert result.priority_checks[0] == expected


@pytest.mark.parametrize(
    ("score", "level", "expected"),
    [
        (60, "low", "large"),
        (59, "low", "small"),
        (60, "medium", "some"),
        (80, "high", "some"),
        (44, "high", "quiet"),
        (45, "high", "small"),
        (52, "medium", "small"),
    ],
)
def test_gap_state_uses_temperature_v2_scale(score, level, expected):
    # 온도 v2(평소=50, 라벨 40/60/80) 기준 문턱값 60/60/80/45 — 프론트 deriveGapCheck·mock gapState와 동일
    assert gap_state(score, level) == expected
