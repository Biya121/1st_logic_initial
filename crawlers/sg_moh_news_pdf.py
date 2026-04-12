"""④ MOH 뉴스·고시 — HTML에서 PDF 링크만 수집(파일 바이너리는 다운로드하지 않음)."""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin

from crawlers.crawl_context import CrawlContext
from crawlers.http_probe import extract_html_title, live_get
from crawlers.site_dashboard import emit_site

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_PDF_HREF = re.compile(
    r"""href\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']""",
    re.I,
)


def collect_pdf_hrefs(html: str, base_url: str, *, limit: int = 40) -> list[str]:
    seen: list[str] = []
    out: list[str] = []
    for m in _PDF_HREF.findall(html):
        abs_u = urljoin(base_url, m.strip())
        if abs_u not in seen:
            seen.append(abs_u)
            out.append(abs_u)
        if len(out) >= limit:
            break
    return out


async def run(emit: Emit, ctx: CrawlContext) -> list[dict[str, Any]]:
    await emit_site(
        emit,
        "moh_pdf",
        "running",
        "④ MOH 뉴스·공고: 페이지에서 PDF 링크를 찾는 중이에요…",
    )
    cfg = ctx.policy.get("moh_pdf") or {}
    if not cfg.get("enabled", True):
        await emit_site(
            emit,
            "moh_pdf",
            "skip",
            "④ MOH PDF: crawl_policy.moh_pdf.enabled=false 로 꺼 두었어요.",
        )
        return []

    src = (ctx.sources.get("sources") or {}).get("moh_pdf") or {}
    url = str(src.get("url_seed", "https://www.moh.gov.sg/news-highlights"))

    status, text, err = await live_get(ctx, url)
    if err == "budget_exhausted":
        await emit_site(
            emit,
            "moh_pdf",
            "warn",
            "④ MOH PDF: 예산이 없어 뉴스 페이지 조회를 건너뛰었어요. "
            "max_live_http_requests_per_cycle 를 늘려 주세요.",
        )
        await emit(
            {
                "phase": "sg_moh_news_pdf",
                "level": "warn",
                "message": "MOH 뉴스 GET 생략 (예산)",
            }
        )
        return []

    if err:
        await emit_site(
            emit,
            "moh_pdf",
            "warn",
            f"④ MOH PDF: 연결 오류 ({err})",
        )
        await emit(
            {
                "phase": "sg_moh_news_pdf",
                "level": "warn",
                "message": f"MOH 뉴스 GET 실패: {err}",
            }
        )
        return []

    title = extract_html_title(text) or ""
    found = collect_pdf_hrefs(text, url, limit=40)

    if status != 200:
        await emit_site(
            emit,
            "moh_pdf",
            "warn",
            f"④ MOH PDF: HTTP {status} — PDF 링크 {len(found)}건(부분 HTML일 수 있음)",
        )
    elif found:
        await emit_site(
            emit,
            "moh_pdf",
            "ok",
            f"④ MOH PDF: 뉴스 페이지에서 PDF 링크 {len(found)}건 발견(다운로드·파싱은 별도 단계).",
        )
    else:
        await emit_site(
            emit,
            "moh_pdf",
            "ok",
            f"④ MOH PDF: HTTP 200, PDF 링크 0건 — 제목「{title[:40] or '없음'}」(레이아웃·JS 로딩일 수 있음).",
        )

    preview = "; ".join(u[:60] for u in found[:3])
    await emit(
        {
            "phase": "sg_moh_news_pdf",
            "level": "info",
            "message": f"MOH news {url} HTTP {status} pdf_links={len(found)} {preview!r}",
        }
    )
    return []
