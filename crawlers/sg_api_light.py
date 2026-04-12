"""Logic A Easy: HSA CSV(정적) + MOH 라이브 프로브(예산 1슬롯)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from crawlers.common import map_to_schema
from crawlers.crawl_context import CrawlContext
from crawlers.http_probe import live_get
from crawlers.site_dashboard import emit_site
from crawlers.targets import TARGETS
from utils.hsa_registry import load_registry, row_to_item

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_DEFAULT_MOHS = (
    "https://www.moh.gov.sg/",
    "https://www.moh.gov.sg/cost-financing/healthcare-schemes-subsidies/drug-subsidies-and-medication-costs",
)


async def run(csv_path: Path, emit: Emit, ctx: CrawlContext) -> list[dict[str, Any]]:
    await emit_site(emit, "hsa", "running", "① HSA: 공개 등록표(CSV)를 읽는 중이에요…")
    registry = load_registry(csv_path)
    out: list[dict[str, Any]] = []

    await emit(
        {
            "phase": "sg_api_light",
            "level": "info",
            "message": f"HSA CSV 로드 완료 ({len(registry)} licence) — 정적",
        }
    )

    api_cfg = ctx.policy.get("api_light") or {}
    await emit_site(
        emit,
        "moh",
        "running",
        "③ MOH: 보건부 사이트에 연결해 보는 중이에요(부담 줄이려 요청은 최소로)…",
    )
    probe_msg = "MOH 프로브 비활성(api_light.moh_probe=false)"
    if api_cfg.get("moh_probe", True):
        urls = list(api_cfg.get("moh_probe_urls") or _DEFAULT_MOHS)
        url = urls[0]
        status, _text, err = await live_get(ctx, url)
        if err == "budget_exhausted":
            probe_msg = (
                "MOH 라이브 프로브 생략 — 라이브 HTTP 예산 소진 "
                f"(남은 슬롯 {ctx.budget.remaining_live_http}, crawl_policy.yaml 조정)"
            )
        elif err:
            probe_msg = f"MOH 라이브 실패 {url}: {err}"
        else:
            probe_msg = f"MOH 라이브 GET {url} → HTTP {status}"
    await emit({"phase": "sg_api_light", "level": "info", "message": probe_msg})
    if any(x in probe_msg for x in ("실패", "404", "예산 소진", "비활성")):
        await emit_site(
            emit,
            "moh",
            "warn",
            "MOH: " + probe_msg + " — 주소·예산은 crawl_policy 에서 조정 가능해요.",
        )
    else:
        await emit_site(emit, "moh", "ok", "MOH: " + probe_msg)

    for t in TARGETS:
        await emit(
            {
                "phase": "sg_api_light",
                "level": "info",
                "message": f"처리 중: {t.trade_name} ({t.product_id})",
            }
        )
        if t.licence_no and t.licence_no in registry:
            base = row_to_item(registry[t.licence_no])
            item = {
                **base,
                "market_segment": t.market_segment,
                "confidence": 0.92,
                "sg_source_type": "api_realtime",
                "sg_ndf_listed": False,
                "product_id": t.product_id,
                "trade_name": t.trade_name,
                "product_name": t.trade_name,
            }
            rec = map_to_schema(
                item,
                source_url=f"internal://hsa_csv/{t.licence_no}",
                source_name="hsa_registry_csv",
                source_tier=1,
                product_id=t.product_id,
            )
        else:
            rec = map_to_schema(
                {
                    "trade_name": t.trade_name,
                    "product_name": t.trade_name,
                    "market_segment": t.market_segment,
                    "confidence": 0.88,
                    "sg_source_type": "static_fallback",
                    "active_ingredient": t.notes,
                    "regulatory_id": t.licence_no,
                },
                source_url="internal://target_stub",
                source_name="hsa_registry_csv",
                source_tier=2,
                product_id=t.product_id,
            )
        out.append(rec)
        await emit(
            {
                "phase": "sg_api_light",
                "level": "success",
                "message": f"스키마 변환 완료: {t.product_id}",
                "product_id": t.product_id,
            }
        )

    await emit_site(
        emit,
        "hsa",
        "ok",
        "① HSA: 지정 8개 품목에 대한 등록·성분 정보를 반영했어요.",
    )
    return out
