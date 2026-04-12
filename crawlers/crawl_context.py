"""파이프라인이 각 크롤러에 넘기는 공통 컨텍스트."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.crawl_budget import CrawlBudget


@dataclass
class CrawlContext:
    root: Path
    sources: dict[str, Any]
    policy: dict[str, Any]
    budget: CrawlBudget
