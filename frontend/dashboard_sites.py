"""대시보드에 표시할 보고서 기준 8개 웹·공공 소스 (한국어 라벨)."""

from __future__ import annotations

from typing import Any, TypedDict


class SiteDef(TypedDict):
    id: str
    name: str
    hint: str
    domain: str


# 순서 = 화면에 나열 순서 (보고서 sources.yaml + SAR)
DASHBOARD_SITES: tuple[SiteDef, ...] = (
    {
        "id": "hsa",
        "name": "HSA · 보건과학청",
        "hint": "싱가포르 등록 치료제 공개 목록(정적 CSV)",
        "domain": "hsa.gov.sg",
    },
    {
        "id": "ndf",
        "name": "NDF · 국가 필수약 목록",
        "hint": "ndf.gov.sg HTTP 연결·키워드 확인(목록 파서는 사이트 구조에 맞춰 확장)",
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
        "hint": "뉴스 HTML에서 PDF 링크 수집(파일 내용 파싱은 다음 단계)",
        "domain": "moh.gov.sg",
    },
    {
        "id": "gebiz",
        "name": "GeBIZ · 정부 조달",
        "hint": "낙찰·조달가 (브라우저 자동화 단계)",
        "domain": "gebiz.gov.sg",
    },
    {
        "id": "guardian",
        "name": "Guardian · 약국몰",
        "hint": "소매 시판가 참고",
        "domain": "guardian.com.sg",
    },
    {
        "id": "watsons",
        "name": "Watsons · 약국몰",
        "hint": "소매 시판가 참고",
        "domain": "watsons.com.sg",
    },
    {
        "id": "sar",
        "name": "SAR · 해외·국제 참고",
        "hint": "미등재 품목 인근국·국제 약가",
        "domain": "내부·다국기관",
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
