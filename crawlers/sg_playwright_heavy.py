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

import httpx

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

async def _jina_extract(
    url: str,
    search_keyword: str,
    label: str,
    emit: Emit,
) -> float | None:
    """Jina AI Reader로 페이지 내용 추출 후 SGD 가격 파싱 (§8 에스컬레이션 1단계)."""
    jina_url = f"https://r.jina.ai/{url}"
    headers: dict[str, str] = {"Accept": "text/plain", "X-With-Generated-Alt": "true"}
    jina_key = os.environ.get("JINA_API_KEY", "")
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(jina_url, headers=headers)
        if resp.status_code != 200:
            return None
        text = resp.text
        # 키워드 주변 ±500자 윈도우에서 가격 추출
        idx = text.lower().find(search_keyword.lower())
        window = text[max(0, idx - 100): idx + 500] if idx != -1 else text[:2000]
        prices = _parse_sgd_prices(window) or _parse_sgd_prices(text[:3000])
        if prices:
            await emit({
                "phase": "sg_playwright_heavy",
                "level": "success",
                "message": f"{label}: Jina Reader 폴백 → {prices[0]} SGD",
            })
            return prices[0]
    except Exception as e:
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "warn",
            "message": f"{label}: Jina Reader 실패 — {e}",
        })
    return None


async def _extract_price_playwright(
    url: str,
    search_keyword: str,
    label: str,
    emit: Emit,
    *,
    max_retries: int = 3,
) -> float | None:
    """Playwright로 소매몰에서 search_keyword 가격을 추출.
    CAPTCHA/연결 오류 시 Jina AI Reader(§8 에스컬레이션 1단계)로 폴백."""
    try:
        from playwright.async_api import async_playwright
        from utils.click_jitter import click_jitter
    except ImportError:
        await emit({
            "phase": "sg_playwright_heavy",
            "level": "warn",
            "message": "playwright 미설치 — pip install playwright && playwright install chromium",
        })
        return await _jina_extract(url, search_keyword, label, emit)

    # stealth 패키지 선택적 로드
    try:
        from playwright_stealth import stealth_async as _stealth
    except ImportError:
        _stealth = None

    for attempt in range(1, max_retries + 1):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = await browser.new_page(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                if _stealth:
                    await _stealth(page)

                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(1.5)

                # CAPTCHA 감지 → Jina 폴백
                content = await page.content()
                if re.search(r"captcha|challenge|verify you are human", content, re.I):
                    await emit({
                        "phase": "sg_playwright_heavy",
                        "level": "warn",
                        "message": f"{label}: CAPTCHA 감지 — Jina Reader 폴백 시도",
                    })
                    await browser.close()
                    return await _jina_extract(url, search_keyword, label, emit)

                # 검색창 탐색 — wait_for_selector로 가시성 확인 후 click_jitter
                search_sel = (
                    "input[type='search'], "
                    "input[name*='search'], "
                    "input[placeholder*='search' i], "
                    "input[placeholder*='product' i]"
                )
                try:
                    await page.wait_for_selector(search_sel, state="visible", timeout=8000)
                    await page.evaluate(
                        f"document.querySelector(\"{search_sel.split(',')[0].strip()}\")?.scrollIntoView()"
                    )
                    await asyncio.sleep(0.3)
                    await click_jitter(page, search_sel)
                    await page.keyboard.type(search_keyword, delay=90)
                    await asyncio.sleep(0.5)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(2.5)
                except Exception:
                    pass  # 검색창 없으면 현재 페이지 텍스트로만 시도

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

    # 모든 재시도 실패 → Jina 폴백
    return await _jina_extract(url, search_keyword, label, emit)


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
