"""피츠 법칙 기반 마우스 궤적 지터 (±15%).

보고서 §7-7: click_jitter() — 피츠 법칙 마우스 궤적 ±15%
GeBIZ 봇 탐지 우회를 위한 비선형 마우스 이동 경로 생성.
"""

from __future__ import annotations

import math
import random
from typing import NamedTuple


class Point(NamedTuple):
    x: float
    y: float


def _ease_in_out(t: float) -> float:
    """3차 ease-in-out 보간 (피츠 법칙 근사)."""
    return t * t * (3.0 - 2.0 * t)


def generate_path(
    start: Point,
    end: Point,
    *,
    steps: int = 20,
    jitter_ratio: float = 0.15,
) -> list[Point]:
    """start → end 사이 자연스러운 마우스 경로 생성.

    Args:
        start:        시작 좌표
        end:          목표 좌표
        steps:        경로 점 개수 (기본 20)
        jitter_ratio: 최대 흔들림 비율 (기본 0.15 = ±15%)

    Returns:
        Point 리스트 (start 포함, end 포함)
    """
    dx = end.x - start.x
    dy = end.y - start.y
    dist = math.hypot(dx, dy)
    max_jitter = dist * jitter_ratio

    path: list[Point] = [start]
    for i in range(1, steps):
        t = i / steps
        eased = _ease_in_out(t)
        # 선형 보간 기반
        base_x = start.x + dx * eased
        base_y = start.y + dy * eased
        # 피츠 법칙: 중간 구간에서 jitter 최대
        jitter_scale = math.sin(t * math.pi)  # 0 → 1 → 0
        jx = random.gauss(0, max_jitter * jitter_scale * 0.5)
        jy = random.gauss(0, max_jitter * jitter_scale * 0.5)
        path.append(Point(round(base_x + jx, 2), round(base_y + jy, 2)))
    path.append(end)
    return path


def click_jitter(
    page: Any,  # playwright Page
    selector: str,
    *,
    steps: int = 18,
    jitter_ratio: float = 0.15,
) -> Any:
    """Playwright Page에서 selector를 피츠 법칙 경로로 클릭하는 코루틴 반환.

    Usage:
        await click_jitter(page, "#submit-btn")
    """
    import asyncio

    async def _click() -> None:
        # 요소 좌표 취득
        elem = await page.query_selector(selector)
        if elem is None:
            raise ValueError(f"click_jitter: selector not found: {selector!r}")
        box = await elem.bounding_box()
        if box is None:
            raise ValueError(f"click_jitter: bounding_box is None for {selector!r}")

        target = Point(
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
        )
        # 현재 마우스 위치 (페이지 중심 기준 추정)
        vp = page.viewport_size or {"width": 1280, "height": 720}
        start = Point(
            random.uniform(vp["width"] * 0.1, vp["width"] * 0.9),
            random.uniform(vp["height"] * 0.1, vp["height"] * 0.9),
        )

        path = generate_path(start, target, steps=steps, jitter_ratio=jitter_ratio)

        for pt in path[:-1]:
            await page.mouse.move(pt.x, pt.y)
            await asyncio.sleep(random.uniform(0.008, 0.025))

        # 최종 클릭
        await page.mouse.move(target.x, target.y)
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.mouse.click(target.x, target.y)

    return _click()


# Any 타입 힌트를 위한 임포트 (런타임 불필요)
from typing import Any  # noqa: E402
