import pytest

from app.schemas.analysis import EvidenceLevel, MarketTemperature


def test_list_companies(client):
    response = client.get("/api/v1/companies")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["companies"][0]["company_name"] == "삼성전자"


def test_guest_analysis_hides_detail(client):
    response = client.post("/api/v1/analyses", json={"query": "삼성전자"})
    body = response.json()

    assert response.status_code == 200
    assert body["access_level"] == "guest"
    assert body["requires_login"] is True
    assert body["detail"] is None
    assert body["personalized_checkpoints"] is None
    assert body["price"]["volume_basis"] == "last_session"
    assert body["price"]["volume_as_of"] == "2026-09-03"


def test_member_analysis_includes_personalization(client, member_token):
    headers = {"Authorization": f"Bearer {member_token}"}
    response = client.post("/api/v1/analyses", json={"query": "삼성전자"}, headers=headers)
    body = response.json()

    assert response.status_code == 200
    assert body["access_level"] == "member"
    assert body["requires_login"] is False
    assert body["detail"] is not None
    assert body["detail"]["market_temperature"]["weight_covered"] == 100
    assert body["personalized_checkpoints"]["priority_checks"]


@pytest.mark.parametrize(("payload", "expected"), [({}, 100), ({"weight_covered": 55}, 55)])
def test_market_temperature_weight_covered_default_and_passthrough(payload, expected):
    temperature = MarketTemperature(
        score=60,
        label="보통",
        data_coverage=["price", "news"],
        **payload,
    )

    assert temperature.weight_covered == expected


def test_market_temperature_passes_components_through():
    temperature = MarketTemperature(score=60, label="보통", data_coverage=["price", "news"], components={"news_attention": 21})

    assert temperature.components == {"news_attention": 21}
    assert MarketTemperature(score=60, label="보통", data_coverage=["price"]).components == {}


@pytest.mark.parametrize("weight_covered", [-1, 101])
def test_market_temperature_rejects_out_of_range_weight_covered(weight_covered):
    with pytest.raises(ValueError):
        MarketTemperature(
            score=60,
            label="보통",
            data_coverage=["price", "news"],
            weight_covered=weight_covered,
        )


def test_unsupported_company(client):
    response = client.post("/api/v1/analyses", json={"query": "존재하지않는회사"})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "unsupported_company"
    assert "actions" in body


def test_passes_through_mcp_client_narrative(client, member_token):
    # 서사는 MCP Client가 책임진다. Backend는 한 줄·개인화를 다시 조립하지 않는다.
    response = client.post(
        "/api/v1/analyses",
        json={"query": "삼성전자"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    body = response.json()

    assert body["one_line_summary"].endswith("(Mock).")
    assert body["personalized_checkpoints"]["personal_summary"].startswith("장기 관점에서 보면")


def test_evidence_level_accepts_and_preserves_issue_match_fields():
    evidence = EvidenceLevel(
        level="high",
        reason="공급계약 공시와 맞아요",
        matched=[
            {
                "issue": "대규모 공급계약 효과",
                "report_name": "단일판매ㆍ공급계약체결",
                "receipt_number": "202609040101",
                "published_at": "2026-09-04T00:00:00Z",
            }
        ],
        unmatched=["업황"],
        material_count=1,
    )

    assert evidence.model_dump()["matched"][0]["receipt_number"] == "202609040101"


def test_passes_through_mcp_client_fallback_when_llm_failed(client, member_token, monkeypatch):
    # LLM이 실패해도 MCP Client가 대체 서사를 채워 보내므로 Backend는 그대로 전달한다.
    from app.clients.mcp_client import client as mcp_client_module

    original = mcp_client_module.fetch_common_analysis

    async def failed_agent(*args, **kwargs):
        raw = await original(*args, **kwargs)
        raw["partial_failures"] = [{"service": "openai", "status": "model_error", "message": "x"}]
        return raw

    monkeypatch.setattr("app.services.analysis.service.mcp_client.fetch_common_analysis", failed_agent)
    response = client.post(
        "/api/v1/analyses",
        json={"query": "삼성전자"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    body = response.json()

    assert body["status"] == "success"
    assert body["one_line_summary"].endswith("(Mock).")
    assert body["personalized_checkpoints"]["personal_summary"].startswith("장기 관점에서 보면")
