#!/usr/bin/env python3
"""datas/static/products_seed.jsonl → products 테이블 초기 시딩.

실행:
    python scripts/seed_db.py
    python scripts/seed_db.py --db datas/local_products.db   # 경로 지정
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inn_normalizer import _inn
from crawlers.sg_inn_map import register_sg_brands
from utils import db as dbutil
from utils.normalizer import normalize_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed products DB from JSONL")
    parser.add_argument("--db", default=str(ROOT / "datas" / "local_products.db"))
    parser.add_argument(
        "--seed",
        default=str(ROOT / "datas" / "static" / "products_seed.jsonl"),
    )
    args = parser.parse_args(argv)

    seed_path = Path(args.seed)
    db_path = Path(args.db)

    if not seed_path.is_file():
        print(f"[seed_db] ERROR: seed file not found: {seed_path}", file=sys.stderr)
        return 1

    register_sg_brands()
    conn = dbutil.get_connection(db_path)

    inserted = 0
    skipped = 0
    for lineno, line in enumerate(seed_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"[seed_db] WARN line {lineno}: JSON parse error — {e}", file=sys.stderr)
            skipped += 1
            continue

        # 필수 컬럼 검증 (source_url은 빈 문자열 허용 — 시딩 초기 단계)
        missing = [c for c in ("country", "currency", "product_id", "market_segment",
                               "confidence", "source_tier", "source_name")
                   if row.get(c) is None or row.get(c) == ""]
        if row.get("source_url") is None:
            missing.append("source_url")
        if missing:
            print(f"[seed_db] WARN line {lineno}: 필수 컬럼 누락 {missing} — 건너뜀", file=sys.stderr)
            skipped += 1
            continue

        row = normalize_record(row)
        row = _inn.normalize_record(row)
        dbutil.upsert_product(conn, row)
        inserted += 1
        print(f"[seed_db] upserted: {row['product_id']}")

    conn.close()
    print(f"\n[seed_db] 완료 — inserted/updated: {inserted}, skipped: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
