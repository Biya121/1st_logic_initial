"""사이클당 라이브 HTTP 예산 (서버 부하 완화)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class CrawlBudget:
    remaining_live_http: int
    delay_seconds: float

    async def try_consume_live_http(self) -> bool:
        if self.remaining_live_http <= 0:
            return False
        self.remaining_live_http -= 1
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        return True
