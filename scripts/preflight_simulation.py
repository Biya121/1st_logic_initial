#!/usr/bin/env python3
"""선제 시뮬레이션: 공개 등록 CSV 규모·분포와 가상 상세 수집 시 부하·소요 시간 추정."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoadProfile:
    total_rows: int
    unique_licence_nos: int
    duplicate_licence_rows: int
    empty_licence_rows: int
    forensic_classification_counts: dict[str, int]
    top_license_holders: list[tuple[str, int]]


@dataclass(frozen=True)
class CrawlScenario:
    name: str
    requests_per_row: int
    delay_seconds_between_requests: float
    estimated_wall_seconds: float


def analyze_csv(path: Path) -> LoadProfile:
    forensic: Counter[str] = Counter()
    holders: Counter[str] = Counter()
    licence_seen: Counter[str] = Counter()
    empty_licence = 0
    rows = 0

    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if "licence_no" not in (reader.fieldnames or []):
            raise ValueError("CSV must include licence_no column")
        for row in reader:
            rows += 1
            lic = (row.get("licence_no") or "").strip()
            if not lic:
                empty_licence += 1
                continue
            licence_seen[lic] += 1
            fc = (row.get("forensic_classification") or "").strip() or "(empty)"
            forensic[fc] += 1
            holder = (row.get("license_holder") or "").strip() or "(empty)"
            holders[holder] += 1

    dup_rows = sum(c - 1 for c in licence_seen.values() if c > 1)
    top_h = holders.most_common(15)

    return LoadProfile(
        total_rows=rows,
        unique_licence_nos=len(licence_seen),
        duplicate_licence_rows=dup_rows,
        empty_licence_rows=empty_licence,
        forensic_classification_counts=dict(forensic.most_common()),
        top_license_holders=top_h,
    )


def scenarios_for_load(
    unique_products: int,
    total_rows: int,
) -> list[CrawlScenario]:
    """행당 N회 요청 + 균일 간격 지연 가정(동시성 없음)."""
    out: list[CrawlScenario] = []
    configs = [
        ("detail_1x_conservative", 1, 1.0),
        ("detail_1x_moderate", 1, 0.5),
        ("detail_2x_conservative", 2, 1.0),
    ]
    for name, rpr, delay in configs:
        # 중복 라이선스가 있으면 실제 크롤은 unique 기준이 일반적
        n = unique_products
        total_req = n * rpr
        # 간격은 (total_req - 1) * delay 근사; 첫 요청은 즉시
        wall = max(0.0, (total_req - 1) * delay) if total_req else 0.0
        out.append(
            CrawlScenario(
                name=name,
                requests_per_row=rpr,
                delay_seconds_between_requests=delay,
                estimated_wall_seconds=wall,
            )
        )
    # 전체 CSV 행 기준(중복 허용 시나리오)
    if total_rows != unique_products:
        total_req_full = total_rows * 1
        wall_full = max(0.0, (total_req_full - 1) * 1.0)
        out.append(
            CrawlScenario(
                name="all_rows_1req_1s_if_no_dedup",
                requests_per_row=1,
                delay_seconds_between_requests=1.0,
                estimated_wall_seconds=wall_full,
            )
        )
    return out


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "datas" / "ListingofRegisteredTherapeuticProducts.csv"
    if not csv_path.is_file():
        print(json.dumps({"error": f"missing {csv_path}"}, ensure_ascii=False))
        return 1

    profile = analyze_csv(csv_path)
    scens = scenarios_for_load(profile.unique_licence_nos, profile.total_rows)

    load_dict = {
        "total_rows": profile.total_rows,
        "unique_licence_nos": profile.unique_licence_nos,
        "duplicate_licence_rows": profile.duplicate_licence_rows,
        "empty_licence_rows": profile.empty_licence_rows,
        "forensic_classification_counts": profile.forensic_classification_counts,
        "top_license_holders": [
            {"license_holder": h, "row_count": c} for h, c in profile.top_license_holders
        ],
    }

    report = {
        "csv_path": str(csv_path),
        "load_profile": load_dict,
        "crawl_scenarios_sequential": [
            {
                "name": s.name,
                "requests_per_row": s.requests_per_row,
                "delay_seconds_between_requests": s.delay_seconds_between_requests,
                "estimated_wall_seconds": s.estimated_wall_seconds,
            }
            for s in scens
        ],
        "notes": [
            "고유 licence_no 기준이 일반적(중복 행은 정규화 후 1회만 요청 가정).",
            "실제 크롤 전 robots.txt·이용약관·HSA/정부 사이트 정책 및 PDPA 준수.",
            "공개 배포 CSV가 있으면 HTTP 스크래핑 대신 공식 다운로드·갱신 주기를 우선 검토.",
        ],
    }

    out_path = root / "datas" / "preflight_simulation_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
