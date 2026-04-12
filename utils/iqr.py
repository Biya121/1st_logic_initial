"""IQR 이상치 플래그 (ESCoE TR-12 개념 반영, 단순화)."""

from __future__ import annotations


def iqr_outlier(values: list[float]) -> tuple[float | None, float | None, bool]:
    if len(values) < 4:
        return None, None, False
    xs = sorted(values)
    n = len(xs)
    q1 = xs[n // 4]
    q3 = xs[(3 * n) // 4]
    iqr = q3 - q1
    if iqr <= 0:
        return q1, q3, False
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    latest = xs[-1]
    return lo, hi, not (lo <= latest <= hi)


def moving_average_30d(_component_key: str, history: list[float] | None = None) -> float | None:
    """30일 이동평균 자리 — 로컬 데모는 history 또는 None 시 플레이스홀더."""
    if history:
        return round(sum(history) / len(history), 4)
    return None
