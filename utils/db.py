"""products 테이블 래퍼.

우선순위:
  1. Supabase (SUPABASE_URL + SUPABASE_KEY 환경변수 설정 시)
  2. 로컬 SQLite (기본, datas/local_products.db)

헌법 준수:
  - 1공정은 price_local(SGD)만 저장. fob_estimated_usd는 2공정 위임.
  - product_id 충돌 시 upsert(갱신).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── SQLite 스키마 ─────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  product_id TEXT UNIQUE NOT NULL,
  country TEXT NOT NULL,
  currency TEXT NOT NULL,
  trade_name TEXT,
  market_segment TEXT NOT NULL,
  confidence REAL NOT NULL,
  source_url TEXT NOT NULL,
  source_tier INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  regulatory_id TEXT,
  scientific_name TEXT,
  strength TEXT,
  dosage_form TEXT,
  price_local REAL,
  fob_estimated_usd REAL,
  atc_code TEXT,
  raw_payload TEXT,
  inn_name TEXT,
  inn_id TEXT,
  inn_match_type TEXT,
  crawled_at TEXT NOT NULL
);
"""

# 기존 DB에 fob_estimated_usd 컬럼이 없을 경우 마이그레이션
_MIGRATION = "ALTER TABLE products ADD COLUMN fob_estimated_usd REAL;"


# ── SQLite 헬퍼 ───────────────────────────────────────────────────────────────

def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 기존 DB 마이그레이션: fob_estimated_usd 컬럼 추가 (헌법 §2 필수)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
    if "fob_estimated_usd" not in cols:
        conn.execute(_MIGRATION)
        conn.commit()
    return conn


def upsert_product(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    raw = row.get("raw_payload")
    raw_s = json.dumps(raw, ensure_ascii=False) if isinstance(raw, dict) else (raw or "{}")

    cur = conn.execute("SELECT id FROM products WHERE product_id = ?", (row["product_id"],))
    existing = cur.fetchone()
    pid = existing["id"] if existing else str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO products (
          id, product_id, country, currency, trade_name, market_segment, confidence,
          source_url, source_tier, source_name, regulatory_id, scientific_name,
          strength, dosage_form, price_local, fob_estimated_usd, atc_code, raw_payload,
          inn_name, inn_id, inn_match_type, crawled_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_id) DO UPDATE SET
          trade_name=excluded.trade_name,
          market_segment=excluded.market_segment,
          confidence=excluded.confidence,
          source_url=excluded.source_url,
          source_tier=excluded.source_tier,
          source_name=excluded.source_name,
          regulatory_id=excluded.regulatory_id,
          scientific_name=excluded.scientific_name,
          strength=excluded.strength,
          dosage_form=excluded.dosage_form,
          price_local=excluded.price_local,
          fob_estimated_usd=excluded.fob_estimated_usd,
          atc_code=excluded.atc_code,
          raw_payload=excluded.raw_payload,
          inn_name=excluded.inn_name,
          inn_id=excluded.inn_id,
          inn_match_type=excluded.inn_match_type,
          crawled_at=excluded.crawled_at
        """,
        (
            pid,
            row["product_id"],
            row["country"],
            row["currency"],
            row.get("trade_name"),
            row["market_segment"],
            float(row["confidence"]),
            row["source_url"],
            int(row["source_tier"]),
            row["source_name"],
            row.get("regulatory_id"),
            row.get("scientific_name"),
            row.get("strength"),
            row.get("dosage_form"),
            row.get("price_local"),
            row.get("fob_estimated_usd"),  # 2공정이 채움, 1공정은 NULL
            row.get("atc_code"),
            raw_s,
            row.get("inn_name"),
            row.get("inn_id"),
            row.get("inn_match_type"),
            now,
        ),
    )
    conn.commit()


def fetch_all_products(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM products ORDER BY crawled_at DESC, product_id"
    )
    return [dict(r) for r in cur.fetchall()]


# ── Supabase 래퍼 ─────────────────────────────────────────────────────────────

def _get_supabase_client():
    """SUPABASE_URL + SUPABASE_KEY 환경변수 설정 시 클라이언트 반환. 없으면 None."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except ImportError:
        return None
    except Exception:
        return None


def upsert_product_supabase(row: dict[str, Any]) -> bool:
    """Supabase products 테이블에 upsert. 실패 시 False 반환.

    헌법: product_id 충돌 시 갱신.
    fob_estimated_usd: 컬럼은 포함하되 1공정은 NULL, 2공정이 역산 후 채움.
    """
    sb = _get_supabase_client()
    if sb is None:
        return False

    now = datetime.now(timezone.utc).isoformat()
    raw = row.get("raw_payload")
    raw_payload = raw if isinstance(raw, dict) else {}

    record: dict[str, Any] = {
        "product_id": row["product_id"],
        "country": row["country"],
        "currency": row["currency"],
        "trade_name": row.get("trade_name"),
        "market_segment": row["market_segment"],
        "confidence": float(row["confidence"]),
        "source_url": row["source_url"],
        "source_tier": int(row["source_tier"]),
        "source_name": row["source_name"],
        "regulatory_id": row.get("regulatory_id"),
        "scientific_name": row.get("scientific_name"),
        "strength": row.get("strength"),
        "dosage_form": row.get("dosage_form"),
        "price_local": row.get("price_local"),
        "fob_estimated_usd": row.get("fob_estimated_usd"),  # 2공정이 채움, 1공정 NULL
        "atc_code": row.get("atc_code"),
        "raw_payload": raw_payload,
        "inn_name": row.get("inn_name"),
        "inn_id": row.get("inn_id"),
        "inn_match_type": row.get("inn_match_type"),
        "crawled_at": now,
    }

    try:
        sb.table("products").upsert(record, on_conflict="product_id").execute()
        return True
    except Exception:
        return False


def upsert_product_auto(
    row: dict[str, Any],
    *,
    sqlite_conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> None:
    """Supabase → 실패 시 SQLite 순서로 upsert.

    Args:
        row:         헌법 공통 스키마 레코드
        sqlite_conn: 열려 있는 SQLite 연결 (없으면 db_path로 새로 열기)
        db_path:     SQLite 파일 경로 (sqlite_conn 없을 때 사용)
    """
    # 1. Supabase 시도
    if upsert_product_supabase(row):
        return

    # 2. SQLite 폴백
    if sqlite_conn is not None:
        upsert_product(sqlite_conn, row)
        return

    path = db_path or Path("datas/local_products.db")
    conn = get_connection(path)
    upsert_product(conn, row)
    conn.close()
