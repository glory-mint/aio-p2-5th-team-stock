from collections import defaultdict
from typing import Any

from app.schemas.analysis import CollectedData, EvidenceLevel, SourceItem
from app.services.analysis_builder.issues import extract_issues
from app.services.analysis_builder.matching import match_issues, recent_material_disclosures


def _meta(**values: object) -> dict[str, object]:
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def community_topics(community: dict[str, Any]) -> list[dict[str, Any]]:
    top_topics = community.get("top_topics") or {}
    topics: list[dict[str, object]] = []
    for key, sentiment in (("expectations", "positive"), ("concerns", "negative")):
        for rank, text in enumerate(top_topics.get(key) or []):
            if text:
                topics.append(
                    {"text": str(text), "sentiment": sentiment, "weight": max(5 - rank, 1)}
                )
    return topics


def _disclosure_source(
    disclosure: dict[str, object], *, confirmed: list[str] | None = None
) -> SourceItem:
    return SourceItem(
        source_type="disclosure",
        title=" ".join(str(disclosure.get("report_name") or "전자공시").split()),
        url=disclosure.get("source_url") or None,
        published_at=disclosure.get("published_at") or None,
        meta=_meta(
            confirmed=confirmed,
            disclosure_kind=disclosure.get("disclosure_kind"),
            receipt_number=disclosure.get("receipt_number"),
            document_type=disclosure.get("document_type"),
        ),
    )


def collect_sources(data: CollectedData, evidence: EvidenceLevel | None = None) -> list[SourceItem]:
    sources: list[SourceItem] = []
    if data.price.get("status") == "success":
        sources.append(
            SourceItem(source_type="price", title=str(data.price.get("source_name") or "현재 주가"))
        )

    for article in (data.news.get("articles") or [])[:5]:
        sources.append(
            SourceItem(
                source_type="news",
                title=str(article.get("headline") or "뉴스"),
                url=article.get("source_url") or None,
                published_at=article.get("published_at") or None,
                meta=_meta(publisher=article.get("publisher")),
            )
        )

    if evidence is None:
        issues = extract_issues(data, str(data.price.get("company_name") or ""))
        match = match_issues(issues, data.material_disclosures)
        matched = match.matched
        unconfirmed = match.unmatched
    else:
        matched = evidence.matched
        unconfirmed = evidence.unmatched

    disclosure_sources: list[SourceItem] = []
    material = recent_material_disclosures(data.material_disclosures)
    material_by_receipt = {
        str(item.get("receipt_number")): item for item in material if item.get("receipt_number")
    }
    confirmed_by_receipt: dict[str, list[str]] = defaultdict(list)
    matched_receipts: list[str] = []
    for item in matched:
        if item.receipt_number not in confirmed_by_receipt:
            matched_receipts.append(item.receipt_number)
        confirmed_by_receipt[item.receipt_number].append(item.issue)

    used_receipts: set[str] = set()
    for receipt in matched_receipts:
        disclosure = material_by_receipt.get(receipt)
        if disclosure is None:
            continue
        disclosure_sources.append(
            _disclosure_source(disclosure, confirmed=confirmed_by_receipt[receipt])
        )
        used_receipts.add(receipt)
        if len(disclosure_sources) == 4:
            break

    if len(disclosure_sources) < 4:
        remaining_material = next(
            (
                item
                for item in material
                if str(item.get("receipt_number") or "") not in used_receipts
            ),
            None,
        )
        if remaining_material is not None:
            disclosure_sources.append(_disclosure_source(remaining_material))
            receipt = str(remaining_material.get("receipt_number") or "")
            if receipt:
                used_receipts.add(receipt)

    if len(disclosure_sources) < 4:
        periodic = next(
            (
                item
                for item in (data.disclosures.get("disclosures") or [])
                if str(item.get("receipt_number") or "") not in used_receipts
            ),
            None,
        )
        if periodic is not None:
            periodic = {"disclosure_kind": "periodic", **periodic}
            disclosure_sources.append(_disclosure_source(periodic))
            receipt = str(periodic.get("receipt_number") or "")
            if receipt:
                used_receipts.add(receipt)

    if len(disclosure_sources) < 4 and data.annual_report.get("status") == "success":
        annual_report = {
            "report_name": data.annual_report.get("report_name") or "최신 사업보고서",
            "source_url": data.annual_report.get("source_url"),
            "published_at": data.annual_report.get("published_at"),
            "receipt_number": data.annual_report.get("receipt_number"),
            "document_type": data.annual_report.get("document_type") or "annual_report",
            "disclosure_kind": "periodic",
        }
        disclosure_sources.append(_disclosure_source(annual_report))

    if disclosure_sources and unconfirmed:
        disclosure_sources[0].meta["unconfirmed"] = list(unconfirmed)
    sources.extend(disclosure_sources)

    if data.community.get("status") in {"success", "partial_success"}:
        sentiment = data.community.get("sentiment") or {}
        fgi_latest = data.community.get("fgi_latest") or {}
        sources.append(
            SourceItem(
                source_type="community",
                title=str(data.community.get("source_name") or "커뮤니티 반응"),
                meta=_meta(
                    samples=data.community.get("sample_size"),
                    positive=sentiment.get("positive_count"),
                    neutral=sentiment.get("neutral_count"),
                    negative=sentiment.get("negative_count"),
                    fgi=fgi_latest.get("fgi"),
                    fgi_label=fgi_latest.get("label"),
                    topics=community_topics(data.community),
                ),
            )
        )
    return sources
