"""Logic A → (필요 시) Logic B 전체 오케스트레이션."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from crawlers import (
    sg_ai_discovery,
    sg_api_light,
    sg_dynamic_mid,
    sg_gebiz_weekly,
    sg_moh_news_pdf,
    sg_ndf_light,
    sg_playwright_heavy,
    sg_sar_extended,
)
from crawlers.common import map_to_schema
from crawlers.crawl_context import CrawlContext
from crawlers.sg_inn_map import register_sg_brands
from inn_normalizer import _inn
from utils import db as dbutil
from utils.combo import calc_combo_price_local
from utils.crawl_budget import CrawlBudget
from utils.normalizer import normalize_record

Emit = Callable[[dict[str, Any]], Awaitable[None]]


def load_sources(root: Path) -> dict[str, Any]:
    with (root / "sources.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_crawl_policy(root: Path) -> dict[str, Any]:
    path = root / "crawl_policy.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _persist(conn: Any, records: list[dict[str, Any]]) -> None:
    for rec in records:
        row = normalize_record(dict(rec))
        row = _inn.normalize_record(row)
        dbutil.upsert_product(conn, row)


def _product_ids_missing_price(conn: Any) -> list[str]:
    # product_key(사람이 읽는 식별자) 기준 반환 — sg_ai_discovery 내부 조회용
    cur = conn.execute(
        "SELECT product_key FROM products WHERE price_local IS NULL ORDER BY product_key"
    )
    return [str(r[0]) for r in cur.fetchall()]


async def run_full_crawl(
    root: Path,
    emit: Emit,
    *,
    db_path: Path | None = None,
    include_ai_discovery: bool = False,
) -> None:
    db_path = db_path or (root / "datas" / "local_products.db")
    csv_path = root / "datas" / "ListingofRegisteredTherapeuticProducts.csv"
    sources = load_sources(root)
    policy = load_crawl_policy(root)

    max_live = int(policy.get("max_live_http_requests_per_cycle", 1))
    delay = float(policy.get("min_delay_seconds_between_live_requests", 0.0))
    budget = CrawlBudget(remaining_live_http=max_live, delay_seconds=delay)
    ctx = CrawlContext(root=root, sources=sources, policy=policy, budget=budget)

    register_sg_brands()
    await emit(
        {
            "phase": "pipeline",
            "level": "info",
            "message": (
                f"시작 — 보고서 순서(①~⑧)로 진행합니다. "
                f"이번 사이클 라이브 HTTP 상한 {max_live}회."
            ),
        }
    )

    conn = dbutil.get_connection(db_path)

    _persist(conn, await sg_api_light.run(csv_path, emit, ctx))
    await sg_ndf_light.run(emit, ctx)
    _persist(conn, await sg_dynamic_mid.run(emit, ctx))
    await sg_moh_news_pdf.run(emit, ctx)
    _persist(conn, await sg_gebiz_weekly.run(emit, ctx))
    _persist(conn, await sg_playwright_heavy.run(emit, ctx))

    demo = {"rosuvastatin_tab": 8.5, "omega3_ee90": 42.0, "atorvastatin_tab": 7.2}
    combo_rows: list[dict[str, Any]] = []
    for pid, comps, tname in (
        ("SG_rosumeg_combigel", ["rosuvastatin_tab", "omega3_ee90"], "Rosumeg Combigel"),
        ("SG_atmeg_combigel", ["atorvastatin_tab", "omega3_ee90"], "Atmeg Combigel"),
    ):
        await emit(
            {
                "phase": "combo",
                "level": "info",
                "message": f"복합제(웹이 아니라 계산): {tname}",
            }
        )
        c = calc_combo_price_local(pid, comps, demo_prices_sgd=demo)
        item = {
            "product_id": pid,
            "trade_name": tname,
            "product_name": tname,
            "market_segment": "combo_drug",
            "price": c.get("price_local"),
            "confidence": 0.68,
            "sg_source_type": "dynamic_crawl",
            "combo_multiplier": c["raw_payload"].get("combo_multiplier"),
            "outlier_flagged": c["raw_payload"].get("outlier_flagged", False),
            "source_method": "component_estimate",
            "raw_payload": c["raw_payload"],
        }
        combo_rows.append(
            map_to_schema(
                item,
                source_url="internal://combo_calc",
                source_name="combo_local",
                source_tier=2,
                product_id=pid,
            )
        )
    _persist(conn, combo_rows)

    _persist(conn, await sg_sar_extended.run(emit, ctx))

    need = _product_ids_missing_price(conn)
    if include_ai_discovery:
        await emit(
            {
                "phase": "pipeline",
                "level": "warn",
                "message": "AI 보완(Logic B) 실행 — 신뢰도 낮은 추정이 섞일 수 있어요.",
            }
        )
        _persist(conn, await sg_ai_discovery.run(emit, need, ctx))
    else:
        await emit(
            {
                "phase": "pipeline",
                "level": "info",
                "message": "AI 자동 보완은 꺼져 있어요. 필요하면 대시보드에서 체크 후 다시 실행하세요.",
            }
        )

    rem = ctx.budget.remaining_live_http
    await emit(
        {
            "phase": "pipeline",
            "level": "info",
            "message": f"남은 라이브 HTTP 슬롯: {rem}개",
        }
    )

    conn.close()
    await emit(
        {
            "phase": "pipeline",
            "level": "info",
            "message": "모든 단계가 끝났어요. 품목 표에서 결과를 확인해 보세요.",
        }
    )


def run_full_crawl_blocking(
    root: Path,
    db_path: Path | None = None,
    *,
    include_ai_discovery: bool = False,
) -> None:
    async def emit(_e: dict[str, Any]) -> None:
        pass

    asyncio.run(
        run_full_crawl(
            root,
            emit,
            db_path=db_path,
            include_ai_discovery=include_ai_discovery,
        )
    )
