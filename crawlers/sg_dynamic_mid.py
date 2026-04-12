"""Logic A Medium: MOH HTML 선택적 라이브 GET + Omethyl 가격(HTML 파싱 또는 데모)."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from crawlers.common import map_to_schema
from crawlers.crawl_context import CrawlContext
from crawlers.http_probe import extract_html_title, live_get
from crawlers.moh_price_parse import (
    collect_sgd_candidates,
    html_to_visible_text,
    pick_price_near_keywords,
)
from crawlers.site_dashboard import emit_site

Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def run(emit: Emit, ctx: CrawlContext) -> list[dict[str, Any]]:
    await emit_site(
        emit,
        "moh",
        "running",
        "③ MOH: 약가·안내 페이지 단계(시뮬 또는 라이브) 처리 중…",
    )
    mid = ctx.policy.get("dynamic_mid") or {}
    policy_demo = float(mid.get("omethyl_demo_price_sgd", 86.4))
    parse_html = bool(mid.get("parse_price_from_html", True))
    kw_default = ("Omethyl", "omega-3", "omega 3", "ethyl esters", "Omacor")
    omethyl_keywords = tuple(mid.get("omethyl_keywords") or kw_default)
    moh_url = str(
        ctx.sources.get("sources", {})
        .get("moh_drug_prices", {})
        .get(
            "url_seed",
            "https://www.moh.gov.sg/cost-financing/healthcare-schemes-subsidies/drug-subsidies-and-medication-costs",
        )
    )

    extra_meta: dict[str, Any] = {}
    source_type = "dynamic_crawl"
    confidence = 0.78
    used_url = moh_url
    resolved_price = policy_demo
    price_source = "demo_policy_simulated"

    if mid.get("live_fetch", False):
        status, text, err = await live_get(ctx, moh_url)
        if err == "budget_exhausted":
            await emit(
                {
                    "phase": "sg_dynamic_mid",
                    "level": "warn",
                    "message": "dynamic_mid.live_fetch 이지만 라이브 HTTP 예산 없음 — 시뮬",
                }
            )
        elif err:
            extra_meta["moh_fetch_error"] = err
            await emit(
                {
                    "phase": "sg_dynamic_mid",
                    "level": "warn",
                    "message": f"MOH 라이브 실패, 데모가 유지됩니다: {err}",
                }
            )
        else:
            extra_meta["moh_http_status"] = status
            extra_meta["moh_page_title"] = extract_html_title(text) or ""
            extra_meta["moh_bytes"] = len(text.encode("utf-8"))
            resolved_price = policy_demo
            price_source = "demo_policy_fallback"
            if status == 200 and text and parse_html:
                plain = html_to_visible_text(text)
                parsed = pick_price_near_keywords(plain, omethyl_keywords)
                extra_meta["moh_sgd_candidates"] = collect_sgd_candidates(
                    plain, limit=18
                )
                if parsed is not None:
                    resolved_price = float(parsed)
                    price_source = "moh_html_keyword_window"
                    confidence = 0.85
                else:
                    extra_meta["moh_parse_note"] = (
                        "키워드 주변 SGD 금액 없음 — 정책 데모가 유지됩니다."
                    )
            if status == 200 and price_source == "demo_policy_fallback":
                confidence = max(confidence, 0.80)
            extra_meta["omethyl_price_source"] = price_source
            await emit(
                {
                    "phase": "sg_dynamic_mid",
                    "level": "success",
                    "message": (
                        f"MOH 라이브 HTML 수신 HTTP {status}, "
                        f"title={extra_meta.get('moh_page_title', '')[:60]!r}, "
                        f"Omethyl 출처={price_source}"
                    ),
                }
            )
            if status == 200:
                confidence = min(confidence, 0.90)
    else:
        extra_meta["omethyl_price_source"] = price_source
        await emit(
            {
                "phase": "sg_dynamic_mid",
                "level": "info",
                "message": "MOH/Customs — 시뮬 모드 (dynamic_mid.live_fetch=false)",
            }
        )

    await asyncio.sleep(0.35)

    item = {
        "product_id": "SG_omethyl_omega3_2g",
        "trade_name": "Omethyl",
        "product_name": "Omethyl",
        "market_segment": "retail",
        "price": resolved_price,
        "confidence": confidence,
        "sg_source_type": source_type,
        "raw_payload": {
            "sg_source_type": source_type,
            "moh_probe_meta": extra_meta,
        },
    }
    rec = map_to_schema(
        item,
        source_url=used_url,
        source_name="moh_drug_prices",
        source_tier=2,
        product_id="SG_omethyl_omega3_2g",
    )
    src_note = (
        "HTML 키워드 인근 파싱"
        if price_source == "moh_html_keyword_window"
        else "정책 데모 또는 미파싱"
    )
    await emit(
        {
            "phase": "sg_dynamic_mid",
            "level": "success",
            "message": (
                f"Omethyl price_local(SGD)={resolved_price} ({src_note}, 출처={price_source})"
            ),
            "product_id": rec["product_id"],
        }
    )
    await emit_site(
        emit,
        "moh",
        "ok",
        f"③ MOH: Omethyl {resolved_price} SGD — {src_note}.",
    )
    return [rec]
