"""복합제 price_local 합산 (1공정). FOB·환산은 2공정 위임."""

from __future__ import annotations

from typing import Any

from utils.iqr import moving_average_30d


def calc_combo_price_local(
    product_id: str,
    components: list[str],
    *,
    demo_prices_sgd: dict[str, float] | None = None,
) -> dict[str, Any]:
    demo_prices_sgd = demo_prices_sgd or {}
    prices: list[float] = []
    for comp in components:
        p = moving_average_30d(comp)
        if p is None:
            p = demo_prices_sgd.get(comp)
        if p is None:
            return {
                "product_id": product_id,
                "price_local": None,
                "raw_payload": {
                    "combo_multiplier": 0.85,
                    "sg_source_type": "dynamic_crawl",
                    "outlier_flagged": True,
                },
            }
        prices.append(p)

    price_local = round(sum(prices), 4)
    return {
        "product_id": product_id,
        "price_local": price_local,
        "raw_payload": {
            "combo_multiplier": 0.85,
            "sg_source_type": "dynamic_crawl",
            "outlier_flagged": False,
        },
    }
