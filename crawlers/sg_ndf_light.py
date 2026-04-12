"""② NDF — ndf.gov.sg 라이브 연결 확인(예산 1슬롯). 목록 파싱은 사이트 구조에 맞춰 확장."""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from crawlers.crawl_context import CrawlContext
from crawlers.http_probe import extract_html_title, live_get
from crawlers.site_dashboard import emit_site

Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def run(emit: Emit, ctx: CrawlContext) -> list[dict[str, Any]]:
    await emit_site(
        emit,
        "ndf",
        "running",
        "② NDF: 국가 필수약 관련 사이트에 연결해 보는 중이에요…",
    )
    cfg = ctx.policy.get("ndf") or {}
    if not cfg.get("enabled", True):
        await emit_site(
            emit,
            "ndf",
            "skip",
            "② NDF: crawl_policy.ndf.enabled=false 로 꺼 두었어요.",
        )
        return []

    src = (ctx.sources.get("sources") or {}).get("ndf_api") or {}
    url = str(src.get("url_seed", "https://www.ndf.gov.sg/"))

    status, text, err = await live_get(ctx, url)
    if err == "budget_exhausted":
        await emit_site(
            emit,
            "ndf",
            "warn",
            "② NDF: 라이브 HTTP 예산이 없어 ndf.gov.sg 조회를 건너뛰었어요. "
            "crawl_policy.yaml 의 max_live_http_requests_per_cycle 를 늘려 보세요.",
        )
        await emit(
            {
                "phase": "sg_ndf_light",
                "level": "warn",
                "message": "NDF GET 생략 (예산)",
            }
        )
        return []

    if err:
        await emit_site(
            emit,
            "ndf",
            "warn",
            f"② NDF: 연결 오류 ({err}) — 네트워크·방화벽을 확인해 주세요.",
        )
        await emit(
            {
                "phase": "sg_ndf_light",
                "level": "warn",
                "message": f"NDF GET 실패: {err}",
            }
        )
        return []

    title = extract_html_title(text) or ""
    t_short = (title[:72] + "…") if len(title) > 72 else title
    looks_ndf = bool(
        re.search(r"\bNDF\b|National Drug|Essential Medicine|ndf\.gov", text[:80000], re.I)
    )
    hint = "본문에 NDF·필수약 관련 키워드가 보여요." if looks_ndf else "본문 키워드는 약함(SPA·리다이렉트일 수 있음)."

    if status == 200:
        await emit_site(
            emit,
            "ndf",
            "ok",
            f"② NDF: HTTP 200 — {hint} 페이지 제목: {t_short or '(없음)'}",
        )
    else:
        await emit_site(
            emit,
            "ndf",
            "warn",
            f"② NDF: HTTP {status} — 응답은 받았지만 정상 페이지인지 확인이 필요해요.",
        )

    await emit(
        {
            "phase": "sg_ndf_light",
            "level": "info" if status == 200 else "warn",
            "message": f"NDF probe {url} → HTTP {status}, title={t_short!r}",
        }
    )
    return []
