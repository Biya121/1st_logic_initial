"""MOH HTML에서 SGD 가격 후보 추출(태그 제거 + 키워드 주변 윈도우)."""

from __future__ import annotations

import re
from typing import Iterable

# 약가 안내 페이지에 자주 나오는 표기
_SGD_NEAR = re.compile(
    r"(?:S\$\s*|\$\s*|SGD\s*)(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{1,4}))?",
    re.I,
)


def html_to_visible_text(html: str, *, max_chars: int = 600_000) -> str:
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars]


def _parse_amount(int_part: str, frac: str | None) -> float | None:
    try:
        base = int_part.replace(",", "")
        if not base:
            return None
        if frac:
            return float(f"{base}.{frac}")
        return float(base)
    except ValueError:
        return None


def extract_sgd_amounts_near(text: str, start: int, end: int) -> list[float]:
    window = text[max(0, start) : min(len(text), end)]
    out: list[float] = []
    for m in _SGD_NEAR.finditer(window):
        val = _parse_amount(m.group(1), m.group(2))
        if val is not None and 0.01 <= val <= 99_999.0:
            out.append(val)
    return out


def pick_price_near_keywords(
    plain: str,
    keywords: Iterable[str],
    *,
    window_before: int = 280,
    window_after: int = 520,
) -> float | None:
    """키워드 첫 등장 주변에서 S$/$/SGD 붙은 금액 중 가장 그럴듯한 값(첫 매칭)."""
    lower = plain.lower()
    for raw_kw in keywords:
        kw = raw_kw.strip().lower()
        if len(kw) < 3:
            continue
        idx = lower.find(kw)
        if idx < 0:
            continue
        amounts = extract_sgd_amounts_near(
            plain, idx - window_before, idx + window_after
        )
        if not amounts:
            continue
        # 같은 윈도우에서 중복 제거 후 가장 큰 값이 약가 본문에선 종종 '총액'에 가깝다는 가정(약함).
        uniq = sorted(set(amounts))
        return uniq[-1]
    return None


def collect_sgd_candidates(plain: str, *, limit: int = 20) -> list[float]:
    """페이지 전체에서 SGD 표기 후보(중복 제거, 소액·비현실 값 제외)."""
    seen: set[float] = set()
    ordered: list[float] = []
    for m in _SGD_NEAR.finditer(plain):
        val = _parse_amount(m.group(1), m.group(2))
        if val is None or not (0.01 <= val <= 99_999.0):
            continue
        key = round(val, 2)
        if key not in seen:
            seen.add(key)
            ordered.append(val)
        if len(ordered) >= limit * 2:
            break
    ordered.sort()
    return ordered[:limit]
