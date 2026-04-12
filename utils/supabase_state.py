"""서킷브레이커 + 토큰버킷 (Supabase products 테이블 공유).

보고서 §9: supabase_state.py — 서킷브레이커/토큰버킷, products 테이블 공유

서킷브레이커 상태:
  CLOSED   → 정상 운영 (오류율 threshold 미만)
  OPEN     → 차단 (일정 시간 후 HALF_OPEN 전환)
  HALF_OPEN → 탐침 1회 허용 → 성공 시 CLOSED, 실패 시 OPEN

토큰버킷:
  Supabase 무료 티어 기준 초당 2 요청(RATE_LIMIT_RPS) 준수
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """단순 에러율 기반 서킷브레이커."""

    failure_threshold: int = 5       # 연속 실패 N회 → OPEN
    recovery_timeout: float = 60.0   # OPEN 상태 유지 시간(초)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failure_count += 1
        if (self._state == CircuitState.HALF_OPEN or
                self._failure_count >= self.failure_threshold):
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


@dataclass
class TokenBucket:
    """토큰버킷 레이트리미터."""

    rate_rps: float = 2.0       # Supabase 무료 티어 기본
    capacity: float = 10.0      # 최대 버스트
    _tokens: float = field(default=0.0, init=False)
    _last_refill: float = field(default_factory=time.monotonic, init=False)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_rps)
        self._last_refill = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """토큰 확보까지 대기."""
        while True:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            wait = (tokens - self._tokens) / self.rate_rps
            await asyncio.sleep(wait)


# ── 전역 싱글톤 ───────────────────────────────────────────────────────────────

_circuit = CircuitBreaker()
_bucket = TokenBucket(rate_rps=2.0)


async def guarded_upsert(
    record: dict,
    *,
    db_path=None,
) -> bool:
    """서킷브레이커 + 토큰버킷을 통과한 upsert.

    Returns:
        True  — upsert 성공
        False — 서킷 OPEN 또는 오류
    """
    if not _circuit.allow_request():
        return False

    await _bucket.acquire()

    try:
        from pathlib import Path
        from utils import db as dbutil

        path = Path(db_path) if db_path else Path("datas/local_products.db")
        conn = dbutil.get_connection(path)
        dbutil.upsert_product(conn, record)
        conn.close()
        _circuit.record_success()
        return True
    except Exception:
        _circuit.record_failure()
        return False


def circuit_state() -> str:
    return _circuit.state.value
