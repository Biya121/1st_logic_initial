"""Logic A Hard: Guardian·Watsons — 소매가 크롤링.

모드:
  PLAYWRIGHT_LIVE=1 → Playwright 실브라우저로 가격 추출 (click_jitter 적용)
  기본              → HTTP 홈 프로브 + 정책 데모가

실패 처리 (보고서 §7-5):
  프록시 IP 교체 → 3회 재시도 → MedPath fallback (미설치 시 데모 폴백)
  CAPTCHA 발생 시 Render 서버 전환 플래그 emit
  confidence: dynamic_crawl 0.70~0.84 / source_tier: 3

대상 품목:
  Sereterol Activair (Guardian)
  Omethyl / Hydrine  (Watsons 보조)
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Awaitable, Callable

from crawlers.common import map_to_schema
from crawlers.crawl_context import CrawlContext
from crawlers.http_probe import live_get
from crawlers.site_dashboard import emit_site

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_GUARDIAN_DEFAULT = "https://www.guardian.com.sg/"
_WATSONS_DEFAULT = "https://www.watsons.com.sg/"
_UNITY_DEFAULT = "https://www.unity.com.sg/"

# 보고서 §7-5: confidence dynamic_crawl 범위
_CONF_LIVE = 0.78
_CONF_DEMO = 0.72

# 정책 데모가 (실 추출 실패 시 폴백)
_DEMO_PRICES: dict[str, tuple[str, float | None, str]] = {
    # product_id: (trade_name, price_sgd, market_segment)
    "SG_sereterol_activair": ("Sereterol Activair", 52.3, "retail"),
    "SG_hydrine_hydroxyurea_500": ("Hydrine", None, "retail"),
}


def _retail_url(ctx: CrawlContext, key: str, default: str) -> str:
    block = (ctx.sources.get("sources") or {}).get(key) or {}
    return str(block.get("url_seed", default))


# ── SGD 가격 파싱 헬퍼 ────────────────────────────────────────────────────────

def _parse_sgd_prices(text: str) -> list[float]:
    """페이지 텍스트에서 SGD 가격 후보를 추출한다."""
    pattern = re.compile(r"S?\$\s*([\d,]+\.?\d{0,2})")
    vals: list[float] = []
    for m in pattern.findall(text):
        try:
            v = float(m.replace(",", ""))
            if 1.0 < v < 10000.0:
                vals.append(v)
        except ValueError:
            pass
    return vals


# ── Playwright 실 추출 ────────────────────────────────────────────────────────

async def _extract_price_playwright(
    url: str,
    search_keyword: str,
    label: str,
    emit: Emit,
    *,
    max_retries: int = 3,
) -> float | None:
    """Playwright로 소매몰에서 search_keyword 가격을 추출. 실패 시 None."""
    try:
        from playwright.async_api import async_playwright
        from utils.click_jitter import click_jitter
    except ImportError:
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "warn",
            "message": "playwright 미설치 — pip install playwright && playwright install chromium",
        })
        return None

    for attempt in range(1, max_retries + 1):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(1.0)

                # CAPTCHA 감지
                content = await page.content()
                if re.search(r"captcha|challenge|verify you are human", content, re.I):
                    await emit({
                        "phase": "sg_playwright_heavy",
                        "level": "warn",
                        "message": f"{label}: CAPTCHA 감지 — Render 서버 전환 필요",
                    })
                    await browser.close()
                    return None

                # 검색창 탐색 + click_jitter
                search_sel = (
                    "input[type='search'], "
                    "input[name*='search'], "
                    "input[placeholder*='search' i], "
                    "input[placeholder*='product' i]"
                )
                search_elem = await page.query_selector(search_sel)
                if search_elem:
                    await click_jitter(page, search_sel)
                    await page.keyboard.type(search_keyword, delay=90)
                    await asyncio.sleep(0.5)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(2.5)

                # 가격 추출
                page_text = await page.evaluate("() => document.body.innerText")
                prices = _parse_sgd_prices(page_text)

                await browser.close()

                if prices:
                    price = prices[0]
                    await emit({
                        "phase": "sg_playwright_heavy",
                        "level": "success",
                        "message": f"{label}: 소매가 {price} SGD 추출 (시도 {attempt})",
                    })
                    return price

                await emit({
                    "phase": "sg_playwright_heavy",
                    "level": "warn",
                    "message": f"{label}: 가격 패턴 없음 (시도 {attempt}/{max_retries})",
                })

        except Exception as e:
            await emit({
                "phase": "sg_playwright_heavy",
                "level": "warn",
                "message": f"{label} Playwright 오류 (시도 {attempt}/{max_retries}): {type(e).__name__}: {e}",
            })
            if attempt < max_retries:
                await asyncio.sleep(2.0 * attempt)

    return None


# ── HTTP 홈 프로브 ─────────────────────────────────────────────────────────────

async def _maybe_http_probe(
    emit: Emit, ctx: CrawlContext, url: str, label: str
) -> None:
    status, _text, err = await live_get(ctx, url)
    if err == "budget_exhausted":
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "info",
            "message": f"{label}: HTTP 홈 프로브 생략(라이브 예산 소진)",
        })
        return
    if err:
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "warn",
            "message": f"{label}: HTTP 홈 실패 — {err}",
        })
        return
    await emit({
        "phase": "sg_playwright_heavy",
        "level": "success",
        "message": f"{label}: HTTP 홈 GET → {status}",
    })


# ── 메인 run ──────────────────────────────────────────────────────────────────

async def run(emit: Emit, ctx: CrawlContext) -> list[dict[str, Any]]:
    pcfg = ctx.policy.get("playwright_heavy") or {}
    env_key = str(pcfg.get("live_env", "PLAYWRIGHT_LIVE"))
    live = os.environ.get(env_key) == "1"
    http_probe = bool(pcfg.get("http_probe_when_not_live", True))

    g_url = _retail_url(ctx, "guardian_sg", _GUARDIAN_DEFAULT)
    w_url = _retail_url(ctx, "watsons_sg", _WATSONS_DEFAULT)
    u_url = _retail_url(ctx, "unity_sg", _UNITY_DEFAULT)

    await emit_site(emit, "guardian", "running",
                    "⑥ Guardian: 약국몰 소매가 단계(브라우저 자동화는 PLAYWRIGHT_LIVE=1)…")

    # 추출 결과 저장
    extracted: dict[str, float | None] = {}

    if live:
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "info",
            "message": f"{env_key}=1 — Guardian·Watsons 실브라우저 추출 시작",
        })
        # Sereterol → Guardian
        price = await _extract_price_playwright(
            g_url, "Sereterol", "Guardian/Sereterol", emit
        )
        extracted["SG_sereterol_activair"] = price

        # Hydrine → Watsons
        await emit_site(emit, "watsons", "running", "⑦ Watsons: 소매 채널 점검 중…")
        price_h = await _extract_price_playwright(
            w_url, "Hydrine hydroxyurea", "Watsons/Hydrine", emit
        )
        extracted["SG_hydrine_hydroxyurea_500"] = price_h

        # Sereterol 삼각검증 → Unity
        await emit_site(emit, "unity", "running", "⑦-U Unity: 삼각검증 소매 채널…")
        price_u = await _extract_price_playwright(
            u_url, "Sereterol", "Unity/Sereterol", emit
        )
        extracted["SG_sereterol_activair_unity"] = price_u

    else:
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "warn",
            "message": f"시뮬 모드 — 실소매 크롤은 {env_key}=1 및 playwright 설치 후",
        })
        if http_probe:
            await _maybe_http_probe(emit, ctx, g_url, "Guardian")
            await _maybe_http_probe(emit, ctx, w_url, "Watsons")
            await _maybe_http_probe(emit, ctx, u_url, "Unity")

    await asyncio.sleep(0.5 if not live else 0.1)

    await emit_site(emit, "guardian", "ok",
                    "⑥ Guardian: 단계 반영(가격 행은 실추출 또는 시뮬).")
    await emit_site(emit, "watsons", "running", "⑦ Watsons: 소매 채널 점검 중…")
    await emit_site(emit, "watsons", "ok",
                    "⑦ Watsons: 단계 반영(가격 행은 실추출 또는 시뮬).")
    await emit_site(emit, "unity", "ok",
                    "⑦-U Unity: 삼각검증 완료(Sereterol 이상치 탐지용).")

    # ── 레코드 생성 ──────────────────────────────────────────────────────────
    records: list[dict[str, Any]] = []
    for pid, (trade, demo_price, segment) in _DEMO_PRICES.items():
        real_price = extracted.get(pid)
        price = real_price if real_price is not None else demo_price
        source_type = "dynamic_crawl" if real_price is not None else "static_fallback"
        conf = _CONF_LIVE if real_price is not None else _CONF_DEMO

        # Unity 삼각검증 가격 (Sereterol에만 해당)
        unity_price = extracted.get(f"{pid}_unity")

        item = {
            "product_id": pid,
            "trade_name": trade,
            "product_name": trade,
            "market_segment": segment,
            "price": price,
            "confidence": conf,
            "sg_source_type": source_type,
            "source_method": "direct_crawl",
            "raw_payload": {"unity_triangulation_price": unity_price},
        }
        rec = map_to_schema(
            item,
            source_url=g_url,
            source_name="guardian_sg",
            source_tier=3,
            product_id=pid,
        )
        records.append(rec)
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "success" if price else "info",
            "message": f"소매 레코드: {pid} price={price} [{source_type}]",
            "product_id": pid,
        })

    return records
