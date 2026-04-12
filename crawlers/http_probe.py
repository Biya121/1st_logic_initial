"""라이브 HTTP GET — CrawlBudget 과 공통 User-Agent."""

from __future__ import annotations

import re

import httpx

from crawlers.crawl_context import CrawlContext

DEFAULT_UA = "1st_logic-crawler/0.3 (+local research)"


def extract_html_title(html: str) -> str | None:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I | re.DOTALL)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()


async def live_get(
    ctx: CrawlContext,
    url: str,
    *,
    timeout: float = 18.0,
) -> tuple[int | None, str, str | None]:
    """budget 소모 후 GET. (status, text, error_token).

    error_token: ``budget_exhausted`` | ``ConnectTimeout`` 등 예외 클래스 이름 | None
    """
    if not await ctx.budget.try_consume_live_http():
        return None, "", "budget_exhausted"
    headers = {"User-Agent": DEFAULT_UA}
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            r = await client.get(url)
            return r.status_code, r.text, None
    except httpx.HTTPError as e:
        return None, "", type(e).__name__
