"""대시보드에 표시할 보고서 기준 소스 (한국어 라벨).

Guardian · Watsons · SAR 제거:
  - Guardian/Watsons: 처방의약품 판매 채널 아님 + playwright 크롤 불가
  - SAR: 타국 참조값 — 싱가포르 데이터 아님
"""

from __future__ import annotations

from typing import Any, TypedDict


class SiteDef(TypedDict):
    id: str
    name: str
    hint: str
    domain: str


DASHBOARD_SITES: tuple[SiteDef, ...] = (
    {
        "id": "hsa",
        "name": "HSA · 보건과학청",
        "hint": "싱가포르 등록 치료제 공개 목록 (정적 CSV — datas/ListingofRegisteredTherapeuticProducts.csv)",
        "domain": "hsa.gov.sg",
    },
    {
        "id": "ndf",
        "name": "NDF · 국가 필수약 목록",
        "hint": "ndf.gov.sg HTTP 연결·키워드 확인",
        "domain": "ndf.gov.sg",
    },
    {
        "id": "moh",
        "name": "MOH · 보건부 (약가·안내)",
        "hint": "사이트 연결 확인 + 약가 안내 페이지",
        "domain": "moh.gov.sg",
    },
    {
        "id": "moh_pdf",
        "name": "MOH · 뉴스·공고·PDF",
        "hint": "뉴스 HTML에서 PDF 링크 수집",
        "domain": "moh.gov.sg",
    },
    {
        "id": "gebiz",
        "name": "GeBIZ · 정부 조달",
        "hint": "낙찰·조달 이력 (로컬 CSV + 브라우저 자동화 — datas/GovernmentProcurementviaGeBIZ.csv)",
        "domain": "gebiz.gov.sg",
    },
)


def initial_site_states() -> dict[str, dict[str, Any]]:
    return {
        s["id"]: {
            "status": "pending",
            "message": "아직 시작 전이에요",
            "ts": 0.0,
        }
        for s in DASHBOARD_SITES
    }
