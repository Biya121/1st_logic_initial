"""map_to_schema() — 모든 sg_*.py가 이 형태로 레코드를 반환."""

from __future__ import annotations

from typing import Any

COUNTRY = "SG"
CURRENCY = "SGD"


def map_to_schema(
    item: dict[str, Any],
    *,
    source_url: str,
    source_name: str,
    source_tier: int,
    product_id: str | None = None,
) -> dict[str, Any]:
    pid = product_id or item.get("product_id")
    if not pid:
        reg = item.get("reg_no") or item.get("regulatory_id") or "unknown"
        pid = f"SG_{reg}".replace(" ", "_")

    conf = float(item.get("confidence", 0.90))
    if conf >= 1.0:
        conf = 0.94
    if conf > 0.94 and item.get("sg_source_type") == "api_realtime":
        conf = 0.94

    raw_base = item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}
    raw: dict[str, Any] = {
        "sg_source_type": item.get("sg_source_type", "api_realtime"),
        "sg_gebiz_award": item.get("sg_gebiz_award"),
        "sg_ndf_listed": bool(item.get("sg_ndf_listed", False)),
        "combo_multiplier": item.get("combo_multiplier"),
        "outlier_flagged": bool(item.get("outlier_flagged", False)),
        "sar_feasibility": item.get("sar_feasibility"),
        "reference_country": item.get("reference_country"),
        "source_method": item.get("source_method"),
        "hsa_registered": item.get("hsa_registered"),
    }
    raw.update(raw_base)

    return {
        "country": COUNTRY,
        "currency": CURRENCY,
        "product_key": pid,   # 사람이 읽는 식별자 — upsert 기준 / 내부 조회용
        "product_id": pid,    # db.py upsert_product()가 UUID v5로 변환
        "trade_name": item.get("product_name") or item.get("trade_name"),
        "market_segment": item.get("market_segment", item.get("segment", "retail")),
        "confidence": conf,
        "source_url": source_url,
        "source_tier": source_tier,
        "source_name": source_name,
        "regulatory_id": item.get("reg_no") or item.get("regulatory_id"),
        "scientific_name": item.get("active_ingredient") or item.get("scientific_name"),
        "strength": item.get("strength"),
        "dosage_form": item.get("dosage_form"),
        "price_local": item.get("price") or item.get("price_local") or item.get("price_sgd"),
        "atc_code": item.get("atc_code"),
        "raw_payload": raw,
    }
