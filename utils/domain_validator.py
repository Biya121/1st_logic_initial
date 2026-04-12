"""Logic B 신뢰 도메인 필터."""

from __future__ import annotations

from urllib.parse import urlparse

_TRUSTED_SUFFIXES = (
    ".gov.sg",
    ".edu.sg",
    ".who.int",
    ".gov",
    ".edu",
)


def is_trusted_domain(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return any(host.endswith(s) for s in _TRUSTED_SUFFIXES)
