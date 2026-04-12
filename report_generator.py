#!/usr/bin/env python3
"""싱가포르 1공정 크롤링 결과 보고서 생성기.

출력 형식:
  reports/sg_report_YYYYMMDD_HHMMSS.json  — 전체 데이터 (기계 판독용)
  reports/sg_report_YYYYMMDD_HHMMSS.pdf   — 양식 기준 보고서 (사람 판독용)

PDF 구조 (품목별 반복):
  1. 품목 헤더
  2. 관련 사이트 표 (공공조달 / 민간 가격정보 / 핵심 논문)
  3. 수출 판정 표 (가능 / 조건부 / 불가)
  4. 수출 가능 시 / 불가 시 근거 문단 + 출처

실행:
  python report_generator.py
  python report_generator.py --db datas/local_products.db --out reports/
  python report_generator.py --analysis-json path/to/analysis.json  (분석 결과 주입)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ── 8개 품목 기대 product_id ──────────────────────────────────────────────────

_EXPECTED_PRODUCTS = [
    "SG_hydrine_hydroxyurea_500",
    "SG_gadvoa_gadobutrol_604",
    "SG_sereterol_activair",
    "SG_omethyl_omega3_2g",
    "SG_rosumeg_combigel",
    "SG_atmeg_combigel",
    "SG_ciloduo_cilosta_rosuva",
    "SG_gastiin_cr_mosapride",
]

_TRADE_NAMES = {
    "SG_hydrine_hydroxyurea_500": "Hydrine",
    "SG_gadvoa_gadobutrol_604": "Gadvoa Inj.",
    "SG_sereterol_activair": "Sereterol Activair",
    "SG_omethyl_omega3_2g": "Omethyl",
    "SG_rosumeg_combigel": "Rosumeg Combigel",
    "SG_atmeg_combigel": "Atmeg Combigel",
    "SG_ciloduo_cilosta_rosuva": "Ciloduo",
    "SG_gastiin_cr_mosapride": "Gastiin CR",
}

_INN_NAMES = {
    "SG_hydrine_hydroxyurea_500": "Hydroxyurea 500mg",
    "SG_gadvoa_gadobutrol_604": "Gadobutrol 604.72mg",
    "SG_sereterol_activair": "Fluticasone / Salmeterol",
    "SG_omethyl_omega3_2g": "Omega-3-Acid Ethyl Esters 90 2g",
    "SG_rosumeg_combigel": "Rosuvastatin + Omega-3-EE90",
    "SG_atmeg_combigel": "Atorvastatin + Omega-3-EE90",
    "SG_ciloduo_cilosta_rosuva": "Cilostazol + Rosuvastatin",
    "SG_gastiin_cr_mosapride": "Mosapride Citrate",
}

_SUCCESS_PROB = {
    "SG_hydrine_hydroxyurea_500": 0.85,
    "SG_gadvoa_gadobutrol_604": 0.65,
    "SG_sereterol_activair": 0.80,
    "SG_omethyl_omega3_2g": 0.70,
    "SG_rosumeg_combigel": 0.70,
    "SG_atmeg_combigel": 0.70,
    "SG_ciloduo_cilosta_rosuva": 0.60,
    "SG_gastiin_cr_mosapride": 0.55,
}

# 품목별 관련 사이트 (양식 §1)
_RELATED_SITES: dict[str, dict[str, list[tuple[str, str]]]] = {
    pid: {
        "public": [
            ("HSA eService Portal", "https://www.hsa.gov.sg/e-services"),
            ("GeBIZ (공공조달)", "https://www.gebiz.gov.sg"),
            ("data.gov.sg 약가 데이터셋", "https://data.gov.sg"),
        ],
        "private": [
            ("MIMS Singapore", "https://www.mims.com/singapore"),
            ("Watsons SG", "https://www.watsons.com.sg"),
            ("Guardian SG", "https://www.guardian.com.sg"),
        ],
        "papers": [
            ("PubMed Central", "https://www.ncbi.nlm.nih.gov/pmc"),
            ("싱가포르 보건부 Clinical Practice Guidelines",
             "https://www.moh.gov.sg/resources-statistics/guidelines-and-guidelines"),
        ],
    }
    for pid in _EXPECTED_PRODUCTS
}


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_products(db_path: Path) -> list[dict]:
    from utils import db as dbutil
    conn = dbutil.get_connection(db_path)
    cur = conn.execute(
        "SELECT * FROM products WHERE country='SG' ORDER BY confidence DESC, product_id"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── 보고서 데이터 조합 ────────────────────────────────────────────────────────

def build_report(
    products: list[dict],
    generated_at: str,
    analysis: list[dict] | None = None,
    references: dict[str, list[dict[str, str]]] | None = None,
) -> dict:
    by_pid: dict[str, dict] = {p["product_id"]: p for p in products}
    analysis_by_pid: dict[str, dict] = (
        {a["product_id"]: a for a in analysis} if analysis else {}
    )
    refs_by_pid: dict[str, list] = references or {}

    items = []
    price_filled = 0
    total = len(_EXPECTED_PRODUCTS)

    for pid in _EXPECTED_PRODUCTS:
        row = by_pid.get(pid)
        trade = _TRADE_NAMES.get(pid, pid)
        inn = _INN_NAMES.get(pid, "")
        ana = analysis_by_pid.get(pid, {})

        if row:
            price = row.get("price_local")
            raw = {}
            try:
                raw = json.loads(row.get("raw_payload") or "{}")
            except Exception:
                pass
            item: dict[str, Any] = {
                "product_id": pid,
                "trade_name": row.get("trade_name") or trade,
                "inn_label": inn,
                "price_local_sgd": price,
                "currency": "SGD",
                "confidence": row.get("confidence"),
                "source_name": row.get("source_name"),
                "source_tier": row.get("source_tier"),
                "market_segment": row.get("market_segment"),
                "inn_name": row.get("inn_name"),
                "inn_match_type": row.get("inn_match_type"),
                "regulatory_id": row.get("regulatory_id"),
                "scientific_name": row.get("scientific_name"),
                "sg_source_type": raw.get("sg_source_type"),
                "sar_feasibility": raw.get("sar_feasibility"),
                "outlier_flagged": raw.get("outlier_flagged", False),
                "crawled_at": row.get("crawled_at"),
                "success_prob": _SUCCESS_PROB.get(pid),
                "status": "collected" if price is not None else "price_missing",
            }
            if price is not None:
                price_filled += 1
        else:
            item = {
                "product_id": pid,
                "trade_name": trade,
                "inn_label": inn,
                "price_local_sgd": None,
                "status": "not_crawled",
                "success_prob": _SUCCESS_PROB.get(pid),
            }

        # 분석 결과 병합
        item["verdict"] = ana.get("verdict")          # None = API 미설정
        item["verdict_en"] = ana.get("verdict_en")
        item["rationale"] = ana.get("rationale", "")
        item["key_factors"] = ana.get("key_factors", [])
        item["analysis_sources"] = ana.get("sources", [])
        item["analysis_model"] = ana.get("analysis_model", "")

        # ── 관련 사이트 — DB 소스 + Perplexity 논문 ────────────────────────────
        base_sites = _RELATED_SITES.get(pid, {"public": [], "private": [], "papers": []})

        # Perplexity 논문 결과가 있으면 교체, 없으면 기본값 유지
        paper_refs = refs_by_pid.get(pid, [])
        if paper_refs:
            papers_list = [(r["title"], r["url"]) for r in paper_refs if r.get("title") and r.get("url")]
        else:
            papers_list = base_sites.get("papers", [])

        # DB에서 수집된 소스 URL로 공공/민간 사이트 보강
        public_extra: list[tuple[str, str]] = []
        private_extra: list[tuple[str, str]] = []
        if row:
            src_name = row.get("source_name", "")
            src_url = row.get("source_url", "")
            src_tier = row.get("source_tier", 4)
            if src_name and src_url and src_url not in ("", "—"):
                label = src_name.replace("_", " ").title()
                if src_tier <= 2:
                    public_extra.append((label, src_url))
                else:
                    private_extra.append((label, src_url))

        item["related_sites"] = {
            "public":  base_sites.get("public", []) + public_extra,
            "private": base_sites.get("private", []) + private_extra,
            "papers":  papers_list,
        }

        items.append(item)

    coverage = round(price_filled / total, 3) if total > 0 else 0.0
    avg_conf = (
        round(
            sum(r["confidence"] for r in products if r.get("confidence"))
            / max(len(products), 1),
            3,
        )
        if products
        else 0.0
    )

    return {
        "meta": {
            "generated_at": generated_at,
            "country": "SG",
            "currency": "SGD",
            "total_products": total,
            "price_collected": price_filled,
            "coverage_ratio": coverage,
            "avg_confidence": avg_conf,
            "note": "fob_estimated_usd 는 2공정 FOB 역산 모듈에 위임",
        },
        "products": items,
    }


# ── PDF 렌더링 ────────────────────────────────────────────────────────────────

def _register_korean_font() -> str:
    """한글 지원 폰트를 등록하고 폰트명을 반환. 등록 실패 시 Helvetica 반환."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("AppleGothic",  "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        ("AppleGothic",  "/Library/Fonts/AppleGothic.ttf"),
        ("NanumGothic",  "/Library/Fonts/NanumGothic.ttf"),
        ("MalgunGothic", "C:/Windows/Fonts/malgun.ttf"),  # Windows 대비
    ]
    for name, path in candidates:
        if Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                pdfmetrics.registerFont(TTFont(f"{name}-Bold", path))
                return name
            except Exception:
                continue
    return "Helvetica"


def render_pdf(report: dict, out_path: Path) -> None:
    """보고서 데이터를 양식 기준 PDF로 저장.

    양식: 1공정_시장조사_보고서_양식.docx 기준
    품목별 구조:
      ① 품목 헤더 (네이비 배경 · 흰 글씨)
      ② 관련 사이트 표 (공공조달 / 민간 / 핵심 논문)
      ③ 수출 적합 시  (초록 레이블 + 2행 표)
      ④ 수출 부적합 시 (빨간 레이블 + 2행 표)
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    W, _H = A4
    MARGIN = 20 * mm
    CONTENT_W = W - 2 * MARGIN

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold"

    # ── 템플릿 색상 ────────────────────────────────────────────────────────────
    C_NAVY   = colors.HexColor("#1B2A4A")
    C_GREEN  = colors.HexColor("#27AE60")
    C_RED    = colors.HexColor("#E74C3C")
    C_ALT    = colors.HexColor("#F4F6F9")   # 홀수 행 배경
    C_LINK   = colors.HexColor("#E8EDF5")   # 근거 사이트 링크 행
    C_BODY   = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")

    # ── 컬럼 너비 (양식 2800:6226 비율) ───────────────────────────────────────
    COL1 = CONTENT_W * 2800 / 9026
    COL2 = CONTENT_W * 6226 / 9026

    # ── 스타일 ─────────────────────────────────────────────────────────────────
    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_hdr_cell = ps("HdrCell",  fontName=bold_font, fontSize=11, textColor=colors.white,
                    leading=17, wordWrap="CJK")
    s_lbl_navy = ps("LblNavy",  fontName=bold_font, fontSize=10, textColor=C_NAVY,
                    leading=15, spaceBefore=7, spaceAfter=3)
    s_lbl_green = ps("LblGreen", fontName=bold_font, fontSize=10, textColor=C_GREEN,
                     leading=15, spaceBefore=9, spaceAfter=3)
    s_lbl_red   = ps("LblRed",   fontName=bold_font, fontSize=10, textColor=C_RED,
                     leading=15, spaceBefore=9, spaceAfter=3)
    s_tbl_hdr   = ps("TblHdr",   fontName=bold_font, fontSize=9,  textColor=colors.white,
                     leading=13, wordWrap="CJK")
    s_cell_bold = ps("CellBold", fontName=bold_font, fontSize=9,  textColor=C_NAVY,
                     leading=13, wordWrap="CJK")
    s_cell      = ps("Cell",     fontName=base_font, fontSize=9,  textColor=C_BODY,
                     leading=14, wordWrap="CJK")

    _PAD = dict(
        TOPPADDING=5, BOTTOMPADDING=5, LEFTPADDING=8, RIGHTPADDING=8,
    )

    def _base_style(extra: list | None = None) -> list:
        cmds = [
            ("GRID",   (0, 0), (-1, -1), 0.5, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ]
        if extra:
            cmds.extend(extra)
        return cmds

    # ── HS CODE 매핑 ───────────────────────────────────────────────────────────
    _HS = {
        "SG_hydrine_hydroxyurea_500":  "3004.90",
        "SG_gadvoa_gadobutrol_604":    "3006.30",
        "SG_sereterol_activair":       "3004.90",
        "SG_omethyl_omega3_2g":        "3004.90",
        "SG_rosumeg_combigel":         "3004.90",
        "SG_atmeg_combigel":           "3004.90",
        "SG_ciloduo_cilosta_rosuva":   "3004.90",
        "SG_gastiin_cr_mosapride":     "3004.90",
    }

    # ── 관련 사이트 표 ─────────────────────────────────────────────────────────
    def _sites_table(sites: dict) -> Table:
        rows = [
            [Paragraph("유형", s_tbl_hdr), Paragraph("사이트명 + URL", s_tbl_hdr)],
        ]
        for key, label in [
            ("public",  "공공조달 데이터 사이트"),
            ("private", "민간 사이트 (가격정보 등)"),
            ("papers",  "핵심 논문 사이트"),
        ]:
            entries = sites.get(key, [])
            right = "\n".join(f"{name}  {url}" for name, url in entries) if entries else ""
            rows.append([Paragraph(label, s_cell_bold), Paragraph(right, s_cell)])

        t = Table(rows, colWidths=[COL1, COL2])
        t.setStyle(TableStyle(_base_style([
            ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
            ("BACKGROUND", (0, 1), (-1, 1), C_ALT),   # 공공조달
            # 행2 민간: 흰 배경 (기본)
            ("BACKGROUND", (0, 3), (-1, 3), C_ALT),   # 핵심 논문
        ])))
        return t

    # ── 근거 표 (적합/부적합 공용) ────────────────────────────────────────────
    def _rationale_table(src_text: str, rationale_text: str) -> Table:
        rows = [
            [Paragraph("근거 사이트 링크",  s_cell_bold), Paragraph(src_text or "—",       s_cell)],
            [Paragraph("근거 한 문단 정리", s_cell_bold), Paragraph(rationale_text or "—", s_cell)],
        ]
        t = Table(rows, colWidths=[COL1, COL2])
        t.setStyle(TableStyle(_base_style([
            ("BACKGROUND", (0, 0), (-1, 0), C_LINK),
            ("BACKGROUND", (0, 1), (-1, 1), C_ALT),
        ])))
        return t

    # ── 문서 조립 ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="싱가포르 1공정 시장조사 보고서",
    )

    story: list = []

    for i, product in enumerate(report["products"]):
        pid        = product.get("product_id", "")
        trade      = product.get("trade_name", "")
        inn_label  = product.get("inn_label", "")
        hs         = _HS.get(pid, "3004.90")
        sites      = product.get("related_sites", {})
        rationale  = product.get("rationale", "") or "—"
        key_factors = product.get("key_factors", [])
        sources    = product.get("analysis_sources", [])

        # 출처 텍스트 — related_sites의 실제 사이트명+URL 사용
        # (analysis_sources는 내부 플레이스홀더이므로 사용하지 않음)
        src_parts: list[str] = []
        for key, prefix in [("public", "공공조달"), ("private", "민간"), ("papers", "논문")]:
            for name, url in sites.get(key, []):
                src_parts.append(f"[{prefix}] {name}  {url}")
        src_text = "\n".join(src_parts) if src_parts else "—"

        # 적합/부적합 근거 텍스트 분기
        verdict = product.get("verdict")   # None = API 미설정
        if verdict is None:
            # API 키 미설정 — 분석 미실행 상태를 명확히 표시
            pro_text = rationale   # rationale에 "API 키 미설정" 메시지 담겨 있음
            con_text = rationale
        elif verdict == "적합":
            pro_text  = rationale
            con_text  = "해당 없음"
        elif verdict == "부적합":
            pro_text  = "해당 없음"
            con_text  = rationale
        else:  # 조건부
            factors_str = "  /  ".join(key_factors) if key_factors else ""
            pro_text  = rationale
            con_text  = factors_str or rationale

        # ① 품목 헤더 (네이비 배경 전폭 테이블)
        verdict_tag = ""
        if verdict == "적합":
            verdict_tag = "  ▶ 수출 적합"
        elif verdict == "부적합":
            verdict_tag = "  ▶ 수출 부적합"
        elif verdict == "조건부":
            verdict_tag = "  ▶ 조건부 적합"
        hdr_text = f"{trade}  —  {inn_label} (HS CODE : {hs}){verdict_tag}"
        hdr_tbl = Table(
            [[Paragraph(hdr_text, s_hdr_cell)]],
            colWidths=[CONTENT_W],
        )
        hdr_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), C_NAVY),
            ("TOPPADDING",    (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING",   (0, 0), (-1, -1), 12),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ]))
        story.append(hdr_tbl)
        story.append(Spacer(1, 5))

        # ② 관련 사이트
        story.append(Paragraph("관련 사이트", s_lbl_navy))
        story.append(_sites_table(sites))
        story.append(Spacer(1, 3))

        # ③ 수출 적합 시
        story.append(Paragraph("수출 적합 시", s_lbl_green))
        story.append(_rationale_table(src_text, pro_text))
        story.append(Spacer(1, 3))

        # ④ 수출 부적합 시
        story.append(Paragraph("수출 부적합 시", s_lbl_red))
        story.append(_rationale_table(src_text, con_text))

        if i < len(report["products"]) - 1:
            story.append(PageBreak())

    doc.build(story)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="싱가포르 1공정 보고서 생성")
    parser.add_argument("--db", default=str(ROOT / "datas" / "local_products.db"))
    parser.add_argument("--out", default=str(ROOT / "reports"))
    parser.add_argument(
        "--analysis-json",
        default=None,
        help="기존 분석 결과 JSON 파일 경로 (없으면 정적 폴백으로 실행)",
    )
    parser.add_argument(
        "--run-analysis",
        action="store_true",
        help="Claude API로 분석 실행 후 보고서 생성",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    generated_at = now.isoformat()

    # 분석 결과 로드
    analysis: list[dict] | None = None

    if args.analysis_json:
        analysis_path = Path(args.analysis_json)
        if analysis_path.exists():
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            print(f"[report] 분석 결과 로드: {analysis_path} ({len(analysis)}건)")
        else:
            print(f"[report] 경고: {analysis_path} 없음 — 정적 폴백 사용")

    if analysis is None and args.run_analysis:
        print("[report] Claude API로 분석 실행 중...")
        from analysis.sg_export_analyzer import analyze_all
        analysis = asyncio.run(analyze_all(db_path=db_path, use_perplexity=True))
        # 분석 결과 JSON 저장
        ana_path = out_dir / f"sg_analysis_{ts}.json"
        ana_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] 분석 JSON → {ana_path}")

    if analysis is None:
        print("[report] Claude API 분석 실행 중... (API 키 없으면 미실행 메시지 표시)")
        from analysis.sg_export_analyzer import analyze_all
        analysis = asyncio.run(analyze_all(db_path=db_path, use_perplexity=True))

    # Perplexity 논문 검색
    print("[report] Perplexity 논문 검색 중... (API 키 없으면 기본 사이트 사용)")
    from analysis.perplexity_references import fetch_all_references
    references = asyncio.run(fetch_all_references())
    ref_count = sum(len(v) for v in references.values())
    print(f"[report] 논문 검색 완료: {ref_count}건")

    # DB에서 제품 로드 + IQR confidence 필터
    products = load_products(db_path)
    from utils.iqr import filter_products_by_confidence
    normal, caution, insufficient = filter_products_by_confidence(products)
    outlier_count = len(insufficient)
    outlier_rate = outlier_count / max(len(products), 1)
    if outlier_rate > 0.03:
        print(f"[report] 경고: 데이터 부족 품목 {outlier_count}개 ({outlier_rate:.1%}) — 이상치 3% 초과")
    else:
        print(f"[report] 이상치 {outlier_count}개 ({outlier_rate:.1%}) — 기준 충족")
    for p in insufficient:
        print(f"[report]   데이터부족: {p['product_id']} (confidence={p.get('confidence')})")

    report = build_report(products, generated_at, analysis, references=references)

    # JSON 저장
    json_path = out_dir / f"sg_report_{ts}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[report] JSON → {json_path}")

    # PDF 저장
    pdf_path = out_dir / f"sg_report_{ts}.pdf"
    render_pdf(report, pdf_path)
    print(f"[report] PDF  → {pdf_path}")

    meta = report["meta"]
    print(
        f"\n[report] 커버리지 {int(meta['coverage_ratio'] * 100)}% "
        f"({meta['price_collected']}/{meta['total_products']}), "
        f"평균 confidence {meta['avg_confidence']:.3f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
