"""함량·제형 문자열 정규화 (경량)."""

from __future__ import annotations

import re
from typing import Any


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("dosage_form"):
        df = str(record["dosage_form"]).lower()
        df = re.sub(r"\s+", " ", df).strip()
        record["dosage_form"] = df
    if record.get("strength"):
        record["strength"] = re.sub(r"\s+", " ", str(record["strength"])).strip()
    if record.get("trade_name"):
        record["trade_name"] = str(record["trade_name"]).strip()
    return record
