"""Logic A Very Hard: GeBIZ — session_cache·ttl_guard·click_jitter 통합.

보고서 §7-7:
  ① session_cache.py — Supabase 암호화 쿠키 저장/로드
  ② ttl_guard()      — 만료 5분 전 선제 갱신
  ③ click_jitter()   — 피츠 법칙 마우스 궤적 ±15%
  실패: ttl_guard 실패 → cold start 1회 → 2회 재시도 → 경고 emit

실행 환경:
  GEBIZ_LIVE=1   — Playwright 실브라우저 스모크 + 낙찰가 추출
  (기본)          — HTTP 프로브 + 시뮬 가격
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Awaitable, Callable

from crawlers.common import map_to_schema
from crawlers.crawl_context import CrawlContext
from crawlers.http_probe import live_get
from crawlers.site_dashboard import emit_site
from utils.session_cache import clear_session, load_session, save_session, ttl_guard

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_GEBIZ_DEFAULT = "https://www.gebiz.gov.sg/"

# 시뮬 가격 (실 Playwright 없을 때)
_DEMO_ROWS = (
    ("Hydrine",     "SG_hydrine_hydroxyurea_500",  118.0, "DEMO-AWARD-HY-001"),
    ("Gadvoa Inj.", "SG_gadvoa_gadobutrol_604",    640.0, "DEMO-AWARD-GAD-002"),
)


def _gebiz_url(ctx: CrawlContext) -> str:
    block = (ctx.sources.get("sources") or {}).get("gebiz_weekly") or {}
    return str(block.get("url_seed", _GEBIZ_DEFAULT))


# ── 세션 갱신 (cold start) ───────────────────────────────────────────────────

async def _cold_start_session(url: str, emit: Emit) -> dict[str, Any] | None:
    """Playwright로 GeBIZ에 접속하여 신규 세션 발급 후 저장."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        await emit({
            "phase": "sg_gebiz_weekly",
            "level": "warn",
            "message": "playwright 미설치 — cold start 불가",
        })
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx_pw = await browser.new_context()
            page = await ctx_pw.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)

            # 쿠키 + localStorage 저장
            cookies = await ctx_pw.cookies()
            session_data = {
                "cookies": cookies,
                "expires_at": time.time() + 3600,  # 1시간 TTL
                "created_at": time.time(),
            }
            await browser.close()

        save_session(session_data)
        await emit({
            "phase": "sg_gebiz_weekly",
            "level": "success",
            "message": f"GeBIZ cold start 완료 — 쿠키 {len(cookies)}개 저장",
        })
        return session_data
    except Exception as e:
        await emit({
            "phase": "sg_gebiz_weekly",
            "level": "warn",
            "message": f"GeBIZ cold start 실패: {type(e).__name__}: {e}",
        })
        return None


# ── 실 낙찰가 추출 ──────────────────────────────────────────────────────────

async def _extract_gebiz_prices(
    url: str,
    session_data: dict[str, Any],
    emit: Emit,
) -> list[tuple[str, str, float | None, str | None]]:
    """GeBIZ에서 Hydrine·Gadvoa 낙찰가 추출.
    Returns: [(trade_name, product_id, price, award_no), ...]
    """
    try:
        from playwright.async_api import async_playwright
        from utils.click_jitter import click_jitter
    except ImportError:
        return []

    results: list[tuple[str, str, float | None, str | None]] = []
    search_terms = [
        ("Hydrine", "SG_hydrine_hydroxyurea_500", "hydroxyurea"),
        ("Gadvoa Inj.", "SG_gadvoa_gadobutrol_604", "gadobutrol"),
    ]

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx_pw = await browser.new_context()

            # 저장된 쿠키 복원
            if session_data.get("cookies"):
                await ctx_pw.add_cookies(session_data["cookies"])

            page = await ctx_pw.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.0)

            for trade, pid, keyword in search_terms:
                try:
                    # 검색창 클릭 (click_jitter 적용)
                    search_sel = "input[type='text'], input[name*='search'], input[placeholder*='search']"
                    await click_jitter(page, search_sel)
                    await page.keyboard.type(keyword, delay=80)
                    await asyncio.sleep(0.5)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(2.0)

                    # 금액 파싱 시도 (SGD 패턴)
                    import re
                    content = await page.content()
                    prices = re.findall(r"S?\$?\s*([\d,]+\.?\d*)", content)
                    price_vals = [float(p.replace(",", "")) for p in prices
                                  if 10 < float(p.replace(",", "")) < 100000]
                    price = price_vals[0] if price_vals else None

                    # 낙찰 번호 파싱
                    awards = re.findall(r"GeBIZ\s*[-#]?\s*(\w{6,})", content, re.I)
                    award = awards[0] if awards else None

                    results.append((trade, pid, price, award))
                    await emit({
                        "phase": "sg_gebiz_weekly",
                        "level": "success" if price else "warn",
                        "message": f"GeBIZ 추출: {trade} price={price} award={award}",
                    })
                except Exception as e:
                    results.append((trade, pid, None, None))
                    await emit({
                        "phase": "sg_gebiz_weekly",
                        "level": "warn",
                        "message": f"GeBIZ 추출 실패 {trade}: {type(e).__name__}: {e}",
                    })

            await browser.close()
    except Exception as e:
        await emit({
            "phase": "sg_gebiz_weekly",
            "level": "warn",
            "message": f"GeBIZ Playwright 전체 실패: {type(e).__name__}: {e}",
        })

    return results


# ── 메인 run ──────────────────────────────────────────────────────────────────

async def run(emit: Emit, ctx: CrawlContext) -> list[dict[str, Any]]:
    gcfg = ctx.policy.get("gebiz_weekly") or {}
    env_key = str(gcfg.get("live_env", "GEBIZ_LIVE"))
    live = os.environ.get(env_key) == "1"
    http_probe = bool(gcfg.get("http_probe_when_not_live", True))
    url = _gebiz_url(ctx)

    await emit_site(
        emit, "gebiz", "running",
        "⑤ GeBIZ: 정부 조달·낙찰 단계(session_cache → ttl_guard → click_jitter)…",
    )

    # ── 라이브 모드 ─────────────────────────────────────────────────────────
    extracted: list[tuple[str, str, float | None, str | None]] = []

    if live:
        await emit({
            "phase": "sg_gebiz_weekly",
            "level": "info",
            "message": f"{env_key}=1 — GeBIZ 라이브 모드 시작",
        })

        # ① 세션 로드 + TTL 확인
        session = load_session()
        valid = ttl_guard(session)

        # ② ttl_guard 실패 → cold start
        if not valid:
            await emit({
                "phase": "sg_gebiz_weekly",
                "level": "info",
                "message": "ttl_guard: 세션 없음 또는 만료 — cold start 시도",
            })
            session = await _cold_start_session(url, emit)

            # cold start 실패 → 재시도 1회
            if session is None:
                await asyncio.sleep(3.0)
                await emit({
                    "phase": "sg_gebiz_weekly",
                    "level": "warn",
                    "message": "cold start 재시도 (1회)…",
                })
                session = await _cold_start_session(url, emit)

            # 재시도도 실패 → 경고 emit (PagerDuty 대체)
            if session is None:
                await emit({
                    "phase": "sg_gebiz_weekly",
                    "level": "warn",
                    "message": (
                        "GeBIZ cold start 2회 모두 실패 — "
                        "시뮬 가격으로 폴백합니다. "
                        "[PagerDuty] 수동 확인 필요"
                    ),
                })
        else:
            await emit({
                "phase": "sg_gebiz_weekly",
                "level": "success",
                "message": "ttl_guard: 세션 유효 — 재사용",
            })

        # ③ 낙찰가 추출 (세션 있을 때)
        if session is not None:
            extracted = await _extract_gebiz_prices(url, session, emit)

    else:
        await emit({
            "phase": "sg_gebiz_weekly",
            "level": "info",
            "message": f"시뮬 모드 — {env_key}=1 설정 시 라이브 추출",
        })
        if http_probe:
            status, _text, err = await live_get(ctx, url)
            if err == "budget_exhausted":
                await emit({
                    "phase": "sg_gebiz_weekly",
                    "level": "info",
                    "message": "GeBIZ HTTP 프로브 생략(라이브 예산 소진)",
                })
            elif err:
                await emit({
                    "phase": "sg_gebiz_weekly",
                    "level": "warn",
                    "message": f"GeBIZ HTTP 실패: {err}",
                })
            else:
                await emit({
                    "phase": "sg_gebiz_weekly",
                    "level": "success",
                    "message": f"GeBIZ HTTP GET → {status}",
                })

    await asyncio.sleep(0.6 if not live else 0.1)
    await emit_site(emit, "gebiz", "ok", "⑤ GeBIZ: 조달 가격 반영 완료.")

    # ── 레코드 생성 (추출 결과 or 시뮬) ────────────────────────────────────
    rows: list[dict[str, Any]] = []

    # 추출 결과 맵
    extracted_map: dict[str, tuple[float | None, str | None]] = {
        pid: (price, award) for _, pid, price, award in extracted
    }

    for trade, pid, demo_price, demo_award in _DEMO_ROWS:
        ext_price, ext_award = extracted_map.get(pid, (None, None))
        price = ext_price if ext_price is not None else demo_price
        award = ext_award or demo_award
        source_type = "api_realtime" if ext_price is not None else "static_fallback"
        conf = 0.87 if ext_price is not None else 0.72

        item = {
            "product_id": pid,
            "trade_name": trade,
            "product_name": trade,
            "market_segment": "tender",
            "price": price,
            "confidence": conf,
            "sg_source_type": source_type,
            "sg_gebiz_award": award,
        }
        rec = map_to_schema(
            item,
            source_url=url,
            source_name="gebiz_crawler",
            source_tier=1,
            product_id=pid,
        )
        rows.append(rec)
        await emit({
            "phase": "sg_gebiz_weekly",
            "level": "success",
            "message": f"조달 레코드: {award} → {pid} SGD {price} [{source_type}]",
            "product_id": pid,
        })

    return rows
