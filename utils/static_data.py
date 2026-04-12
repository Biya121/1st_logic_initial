"""정적 데이터 파이프라인.

소스:
  datas/ListingofRegisteredTherapeuticProducts.csv  — HSA 등재 의약품 전체 목록
  datas/singapore_regulation.pdf                   — HSA 제품 등록 가이드 (162p)
  datas/252026싱가포르진출전략.pdf                   — KOTRA 싱가포르 진출 전략 (89p)
  datas/Review-Pricing-policies.pdf               — 가격 정책 리뷰
  datas/zjma-7-1601060.pdf                        — 의약품 시장 논문

처리 흐름:
  1. HSA CSV → 8품목별 등재 경쟁품 검색
  2. PDF → 키워드 기반 관련 문단 추출 (pypdf)
  3. 품목별 컨텍스트 dict 조합
  4. datas/static/context_cache.json 캐싱

공개 API:
  build_all_contexts(force_rebuild=False) → dict[pid, StaticContext]
  get_product_context(product_id)         → StaticContext | None
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "datas"
CACHE_PATH = DATA_DIR / "static" / "context_cache.json"

_HSA_CSV = DATA_DIR / "ListingofRegisteredTherapeuticProducts.csv"
_PDF_SOURCES = [
    {
        "path": DATA_DIR / "singapore_regulation.pdf",
        "label": "HSA 등록 가이드",
        "max_pages": 30,
    },
    {
        "path": DATA_DIR / "252026싱가포르진출전략.pdf",
        "label": "KOTRA 싱가포르 진출 전략 2026",
        "max_pages": 20,
    },
    {
        "path": DATA_DIR / "Review-Pricing-policies.pdf",
        "label": "가격 정책 리뷰",
        "max_pages": 15,
    },
    {
        "path": DATA_DIR / "zjma-7-1601060.pdf",
        "label": "의약품 시장 논문",
        "max_pages": 10,
    },
]

# 8품목 HSA CSV 검색 키워드 매핑
_PRODUCT_KEYWORDS: dict[str, list[str]] = {
    "SG_hydrine_hydroxyurea_500": ["hydroxyurea", "hydrine"],
    "SG_gadvoa_gadobutrol_604": ["gadobutrol", "gadvoa", "gadovist"],
    "SG_sereterol_activair": ["fluticasone", "salmeterol", "seretide", "sereterol"],
    "SG_omethyl_omega3_2g": ["omega-3-acid ethyl", "omethyl", "lovaza"],
    "SG_rosumeg_combigel": ["rosuvastatin", "omega-3", "rosumeg"],
    "SG_atmeg_combigel": ["atorvastatin", "omega-3", "atmeg"],
    "SG_ciloduo_cilosta_rosuva": ["cilostazol", "rosuvastatin", "ciloduo"],
    "SG_gastiin_cr_mosapride": ["mosapride", "gastiin"],
}

# PDF 추출 시 사용할 품목별 키워드
_PDF_KEYWORDS: dict[str, list[str]] = {
    "SG_hydrine_hydroxyurea_500": ["hydroxyurea", "cytotoxic", "oncology", "cancer", "haematology"],
    "SG_gadvoa_gadobutrol_604": ["gadobutrol", "contrast agent", "MRI", "gadolinium", "radiology"],
    "SG_sereterol_activair": ["fluticasone", "salmeterol", "asthma", "COPD", "inhaler", "ICS"],
    "SG_omethyl_omega3_2g": ["omega-3", "triglyceride", "cardiovascular", "lipid"],
    "SG_rosumeg_combigel": ["rosuvastatin", "statin", "omega-3", "dyslipidaemia", "combination"],
    "SG_atmeg_combigel": ["atorvastatin", "statin", "omega-3", "dyslipidaemia", "combination"],
    "SG_ciloduo_cilosta_rosuva": ["cilostazol", "peripheral artery", "rosuvastatin", "antiplatelet"],
    "SG_gastiin_cr_mosapride": ["mosapride", "gastroparesis", "gastric motility", "prokinetic"],
}


@dataclass
class HsaRecord:
    licence_no: str
    product_name: str
    forensic_classification: str
    atc_code: str
    active_ingredients: str
    approval_date: str = ""


@dataclass
class StaticContext:
    product_id: str
    hsa_matches: list[dict[str, str]] = field(default_factory=list)
    hsa_registered: bool = False
    competitor_count: int = 0
    prescription_only: bool = True
    pdf_snippets: list[dict[str, str]] = field(default_factory=list)
    regulatory_summary: str = ""
    built_at: str = ""


# ── HSA CSV 파싱 ──────────────────────────────────────────────────────────────

def _load_hsa_csv() -> list[dict[str, str]]:
    """HSA CSV 전체 로드."""
    if not _HSA_CSV.exists():
        return []
    with open(_HSA_CSV, encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def _search_hsa(rows: list[dict[str, str]], keywords: list[str]) -> list[HsaRecord]:
    """키워드로 HSA CSV 검색. 성분명·제품명 모두 검색."""
    results: list[HsaRecord] = []
    seen: set[str] = set()
    for row in rows:
        ai = row.get("active_ingredients", "").lower()
        pn = row.get("product_name", "").lower()
        if any(kw.lower() in ai or kw.lower() in pn for kw in keywords):
            lic = row.get("licence_no", "")
            if lic in seen:
                continue
            seen.add(lic)
            results.append(HsaRecord(
                licence_no=lic,
                product_name=row.get("product_name", ""),
                forensic_classification=row.get("forensic_classification", ""),
                atc_code=row.get("atc_code", ""),
                active_ingredients=row.get("active_ingredients", ""),
                approval_date=row.get("approval_d", ""),
            ))
    return results


# ── PDF 텍스트 추출 ────────────────────────────────────────────────────────────

def _extract_pdf_snippets(
    pdf_path: Path,
    keywords: list[str],
    label: str,
    max_pages: int = 20,
    context_chars: int = 400,
) -> list[dict[str, str]]:
    """PDF에서 키워드 포함 문단을 추출. pypdf 사용."""
    if not pdf_path.exists():
        return []

    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    snippets: list[dict[str, str]] = []
    seen_texts: set[str] = set()

    try:
        reader = PdfReader(str(pdf_path))
        pages_to_scan = min(len(reader.pages), max_pages)

        for page_num in range(pages_to_scan):
            try:
                text = reader.pages[page_num].extract_text() or ""
            except Exception:
                continue

            text_lower = text.lower()
            for kw in keywords:
                idx = text_lower.find(kw.lower())
                while idx != -1:
                    start = max(0, idx - context_chars // 2)
                    end = min(len(text), idx + context_chars // 2)
                    snippet = text[start:end].strip()
                    # 중복 제거 (처음 50자 기준)
                    key = snippet[:50]
                    if key not in seen_texts and len(snippet) > 50:
                        seen_texts.add(key)
                        snippets.append({
                            "source": label,
                            "page": str(page_num + 1),
                            "keyword": kw,
                            "text": snippet,
                        })
                    idx = text_lower.find(kw.lower(), idx + 1)

    except Exception:
        pass

    # 품목당 최대 5개 스니펫
    return snippets[:5]


# ── 규제 요약 생성 ─────────────────────────────────────────────────────────────

def _build_regulatory_summary(pid: str, hsa_matches: list[HsaRecord]) -> str:
    """HSA 매칭 결과로 간단한 규제 요약 텍스트 생성."""
    if not hsa_matches:
        return "HSA 등재 기록 없음 — 신규 등록 필요"

    rx_count = sum(1 for r in hsa_matches if "Prescription" in r.forensic_classification)
    otc_count = len(hsa_matches) - rx_count
    sample = hsa_matches[0]

    parts = [
        f"HSA 등재 경쟁품 {len(hsa_matches)}건 확인.",
        f"처방전 필요(Rx): {rx_count}건, 일반(OTC): {otc_count}건.",
        f"대표 사례: {sample.product_name} ({sample.licence_no}, {sample.forensic_classification})",
    ]
    if sample.approval_date:
        parts.append(f"최초 승인일: {sample.approval_date[:10]}")
    return "  ".join(parts)


# ── 전체 컨텍스트 빌드 ────────────────────────────────────────────────────────

def build_all_contexts(force_rebuild: bool = False) -> dict[str, StaticContext]:
    """8품목 전체 정적 컨텍스트 빌드.

    캐시가 있으면 재사용. force_rebuild=True이면 재파싱.
    """
    if not force_rebuild and CACHE_PATH.exists():
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            return {
                pid: _dict_to_context(pid, data)
                for pid, data in raw.items()
            }
        except Exception:
            pass

    hsa_rows = _load_hsa_csv()
    contexts: dict[str, StaticContext] = {}

    for pid, keywords in _PRODUCT_KEYWORDS.items():
        hsa_matches = _search_hsa(hsa_rows, keywords)
        pdf_keywords = _PDF_KEYWORDS.get(pid, keywords)

        pdf_snippets: list[dict[str, str]] = []
        for src in _PDF_SOURCES:
            if src["path"].exists():
                snips = _extract_pdf_snippets(
                    src["path"],
                    pdf_keywords,
                    src["label"],
                    max_pages=src["max_pages"],
                )
                pdf_snippets.extend(snips)

        hsa_dicts = [
            {
                "licence_no": r.licence_no,
                "product_name": r.product_name,
                "forensic_classification": r.forensic_classification,
                "atc_code": r.atc_code,
                "active_ingredients": r.active_ingredients[:120],
            }
            for r in hsa_matches[:10]  # 최대 10건
        ]

        rx_only = any("Prescription" in r.forensic_classification for r in hsa_matches)

        ctx = StaticContext(
            product_id=pid,
            hsa_matches=hsa_dicts,
            hsa_registered=len(hsa_matches) > 0,
            competitor_count=len(hsa_matches),
            prescription_only=rx_only,
            pdf_snippets=pdf_snippets[:8],  # 전체 최대 8개
            regulatory_summary=_build_regulatory_summary(pid, hsa_matches),
            built_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        )
        contexts[pid] = ctx

    # 캐시 저장
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({pid: asdict(ctx) for pid, ctx in contexts.items()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return contexts


def _dict_to_context(pid: str, data: dict[str, Any]) -> StaticContext:
    return StaticContext(
        product_id=pid,
        hsa_matches=data.get("hsa_matches", []),
        hsa_registered=data.get("hsa_registered", False),
        competitor_count=data.get("competitor_count", 0),
        prescription_only=data.get("prescription_only", True),
        pdf_snippets=data.get("pdf_snippets", []),
        regulatory_summary=data.get("regulatory_summary", ""),
        built_at=data.get("built_at", ""),
    )


_CONTEXT_CACHE: dict[str, StaticContext] | None = None


def get_product_context(product_id: str, force_rebuild: bool = False) -> StaticContext | None:
    """단일 품목 정적 컨텍스트 반환. 첫 호출 시 전체 빌드."""
    global _CONTEXT_CACHE
    if _CONTEXT_CACHE is None or force_rebuild:
        _CONTEXT_CACHE = build_all_contexts(force_rebuild=force_rebuild)
    return _CONTEXT_CACHE.get(product_id)


def context_to_prompt_text(ctx: StaticContext) -> str:
    """StaticContext를 Claude 프롬프트용 텍스트로 변환."""
    lines = [
        f"=== 정적 데이터 컨텍스트: {ctx.product_id} ===",
        f"HSA 등재 여부: {'등재 경쟁품 있음' if ctx.hsa_registered else '등재 기록 없음'}",
        f"경쟁품 수: {ctx.competitor_count}건",
        f"처방 분류: {'Rx (처방전 필요)' if ctx.prescription_only else 'OTC 가능'}",
        f"규제 요약: {ctx.regulatory_summary}",
    ]

    if ctx.hsa_matches:
        lines.append("\n[HSA 등재 경쟁품 상위 3건]")
        for m in ctx.hsa_matches[:3]:
            lines.append(
                f"  - {m['product_name']} ({m['licence_no']}, {m['forensic_classification']})"
            )

    if ctx.pdf_snippets:
        lines.append("\n[관련 문서 발췌]")
        for s in ctx.pdf_snippets[:3]:
            snippet_short = re.sub(r"\s+", " ", s["text"])[:200]
            lines.append(f"  [{s['source']} p.{s['page']} / 키워드: {s['keyword']}]")
            lines.append(f"  {snippet_short}...")

    return "\n".join(lines)
