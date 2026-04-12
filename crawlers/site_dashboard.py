"""대시보드 8개 사이트 진행 상태 이벤트 (한국어 메시지)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def emit_site(emit: Emit, site_key: str, status: str, message_ko: str) -> None:
    lv = "warn" if status == "warn" else ("info" if status != "error" else "err")
    await emit(
        {
            "phase": "site_progress",
            "site_key": site_key,
            "site_status": status,
            "message": message_ko,
            "level": lv,
        }
    )
