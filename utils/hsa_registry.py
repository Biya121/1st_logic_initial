"""HSA 공개 등록 CSV 로더 (datas/ListingofRegisteredTherapeuticProducts.csv)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def load_registry(csv_path: Path) -> dict[str, dict[str, Any]]:
    by_licence: dict[str, dict[str, Any]] = {}
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lic = (row.get("licence_no") or "").strip()
            if lic:
                by_licence[lic] = row
    return by_licence


def row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    """map_to_schema 입력용 dict."""
    ingredients = (row.get("active_ingredients") or "").replace("&&", " + ")
    return {
        "reg_no": (row.get("licence_no") or "").strip(),
        "product_name": (row.get("product_name") or "").strip(),
        "trade_name": (row.get("product_name") or "").strip(),
        "active_ingredient": ingredients,
        "strength": (row.get("strength") or "").strip(),
        "dosage_form": (row.get("dosage_form") or "").strip().lower(),
        "atc_code": (row.get("atc_code") or "").strip(),
        "segment": "retail",
    }
