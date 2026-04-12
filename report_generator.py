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
) -> dict:
    by_pid: dict[str, dict] = {p["product_id"]: p for p in products}
    analysis_by_pid: dict[str, dict] = (
        {a["product_id"]: a for a in analysis} if analysis else {}
    )

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
        item["verdict"] = ana.get("verdict", "조건부")
        item["verdict_en"] = ana.get("verdict_en", "CONDITIONAL")
        item["rationale"] = ana.get("rationale", "")
        item["key_factors"] = ana.get("key_factors", [])
        item["analysis_sources"] = ana.get("sources", [])
        item["analysis_model"] = ana.get("analysis_model", "")
        item["related_sites"] = _RELATED_SITES.get(pid, {})

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

def _verdict_color(verdict: str):
    """판정에 따른 RGB 색상."""
    from reportlab.lib import colors
    return {
        "적합": colors.HexColor("#1a7a3c"),
        "조건부": colors.HexColor("#c87000"),
        "부적합": colors.HexColor("#c0392b"),
    }.get(verdict, colors.HexColor("#555555"))


def render_pdf(report: dict, out_path: Path) -> None:
    """보고서 데이터를 양식 기준 PDF로 저장."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    W, H = A4
    MARGIN = 20 * mm
    CONTENT_W = W - 2 * MARGIN

    styles = getSampleStyleSheet()
    base_font = "Helvetica"

    style_title = ParagraphStyle(
        "ReportTitle",
        fontName=f"{base_font}-Bold",
        fontSize=18,
        textColor=colors.HexColor("#1a3a6b"),
        spaceAfter=4,
    )
    style_subtitle = ParagraphStyle(
        "ReportSubtitle",
        fontName=base_font,
        fontSize=10,
        textColor=colors.HexColor("#666666"),
        spaceAfter=12,
    )
    style_product_h = ParagraphStyle(
        "ProductH",
        fontName=f"{base_font}-Bold",
        fontSize=13,
        textColor=colors.HexColor("#1a3a6b"),
        spaceBefore=10,
        spaceAfter=4,
    )
    style_section = ParagraphStyle(
        "Section",
        fontName=f"{base_font}-Bold",
        fontSize=9,
        textColor=colors.HexColor("#444444"),
        spaceBefore=6,
        spaceAfter=2,
    )
    style_body = ParagraphStyle(
        "Body",
        fontName=base_font,
        fontSize=8.5,
        textColor=colors.HexColor("#222222"),
        leading=13,
        spaceAfter=4,
    )
    style_link = ParagraphStyle(
        "Link",
        fontName=base_font,
        fontSize=8,
        textColor=colors.HexColor("#1155cc"),
        leading=11,
    )
    style_cell = ParagraphStyle(
        "Cell",
        fontName=base_font,
        fontSize=8,
        leading=11,
    )
    style_cell_bold = ParagraphStyle(
        "CellBold",
        fontName=f"{base_font}-Bold",
        fontSize=8,
        leading=11,
    )

    TBL_HDR = colors.HexColor("#1a3a6b")
    TBL_ALT = colors.HexColor("#f0f4ff")
    TBL_BORDER = colors.HexColor("#cccccc")

    def _sites_table(sites: dict) -> Table:
        """관련 사이트 표 (공공조달 / 민간 / 논문)."""
        rows_data = [
            [
                Paragraph("<b>유형</b>", style_cell_bold),
                Paragraph("<b>사이트명 + URL</b>", style_cell_bold),
            ]
        ]
        type_labels = {
            "public": "공공조달 데이터 사이트",
            "private": "민간 사이트 (가격정보 등)",
            "papers": "핵심 논문 사이트",
        }
        for key, label in type_labels.items():
            entries = sites.get(key, [])
            links_text = "<br/>".join(
                f'<link href="{url}">{name}</link>  {url}' for name, url in entries
            )
            rows_data.append([
                Paragraph(label, style_cell),
                Paragraph(links_text or "—", style_link),
            ])

        col_w = [CONTENT_W * 0.28, CONTENT_W * 0.72]
        t = Table(rows_data, colWidths=col_w)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TBL_HDR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, 1), TBL_ALT),
            ("BACKGROUND", (0, 3), (-1, 3), TBL_ALT),
            ("GRID", (0, 0), (-1, -1), 0.3, TBL_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    def _verdict_table(verdict: str, verdict_en: str, price: float | None, prob: float | None) -> Table:
        """수출 판정 표."""
        verdict_color = _verdict_color(verdict)
        price_str = f"S${price:.2f}" if price is not None else "미수집"
        prob_str = f"{int(prob * 100)}%" if prob else "—"

        rows_data = [
            [
                Paragraph("<b>판정</b>", style_cell_bold),
                Paragraph("<b>가능</b>", style_cell_bold),
                Paragraph("<b>조건부</b>", style_cell_bold),
                Paragraph("<b>불가</b>", style_cell_bold),
                Paragraph("<b>현지가 (SGD)</b>", style_cell_bold),
                Paragraph("<b>성공 확률</b>", style_cell_bold),
            ],
            [
                Paragraph(f"<b>{verdict}</b>", ParagraphStyle(
                    "VerdictVal", fontName=f"{base_font}-Bold",
                    fontSize=9, textColor=verdict_color
                )),
                Paragraph("●" if verdict == "적합" else "○", style_cell),
                Paragraph("●" if verdict == "조건부" else "○", style_cell),
                Paragraph("●" if verdict == "부적합" else "○", style_cell),
                Paragraph(price_str, style_cell),
                Paragraph(prob_str, style_cell),
            ],
        ]
        col_w = [
            CONTENT_W * 0.14,
            CONTENT_W * 0.12,
            CONTENT_W * 0.12,
            CONTENT_W * 0.12,
            CONTENT_W * 0.26,
            CONTENT_W * 0.24,
        ]
        t = Table(rows_data, colWidths=col_w)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TBL_HDR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, TBL_BORDER),
            ("ALIGN", (1, 0), (3, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    def _rationale_section(product: dict) -> list:
        """수출 가능/불가 시 근거 문단 + 출처 표."""
        verdict = product.get("verdict", "조건부")
        rationale = product.get("rationale", "")
        key_factors = product.get("key_factors", [])
        sources = product.get("analysis_sources", [])
        model = product.get("analysis_model", "")

        elems = []

        # 근거 문단 제목
        if verdict == "부적합":
            section_label = "수출 불가 근거"
        elif verdict == "적합":
            section_label = "수출 가능 근거"
        else:
            section_label = "조건부 수출 근거"

        elems.append(Paragraph(section_label, style_section))

        if rationale:
            elems.append(Paragraph(rationale, style_body))
        else:
            elems.append(Paragraph("분석 결과 없음 — Claude API 키 설정 후 재실행 권장", style_body))

        # 핵심 요인
        if key_factors:
            factors_text = "  •  ".join(key_factors)
            elems.append(Paragraph(f"핵심 요인:  {factors_text}", style_body))

        # 출처 표
        if sources:
            elems.append(Paragraph("출처", style_section))
            src_rows = [[
                Paragraph("<b>출처명</b>", style_cell_bold),
                Paragraph("<b>URL</b>", style_cell_bold),
            ]]
            for s in sources:
                url = s.get("url", "")
                name = s.get("name", "")
                url_para = (
                    Paragraph(f'<link href="{url}">{url}</link>', style_link)
                    if url.startswith("http")
                    else Paragraph(url, style_cell)
                )
                src_rows.append([Paragraph(name, style_cell), url_para])

            col_w = [CONTENT_W * 0.35, CONTENT_W * 0.65]
            src_t = Table(src_rows, colWidths=col_w)
            src_t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#444444")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, TBL_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]))
            elems.append(src_t)

        if model:
            elems.append(Paragraph(
                f"분석 모델: {model}",
                ParagraphStyle("Note", fontName=base_font, fontSize=7,
                               textColor=colors.HexColor("#999999"), spaceAfter=2)
            ))

        return elems

    # ── 문서 조립 ──────────────────────────────────────────────────────────────
    meta = report["meta"]
    products = report["products"]

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="싱가포르 1공정 시장조사 보고서",
        author="1공정 크롤링 엔진",
    )

    story = []

    # 커버 헤더
    story.append(Paragraph("싱가포르 1공정 시장조사 보고서", style_title))
    cov_pct = int(meta["coverage_ratio"] * 100)
    story.append(Paragraph(
        f"생성: {meta['generated_at']}  |  "
        f"가격 커버리지: {cov_pct}% ({meta['price_collected']}/{meta['total_products']})  |  "
        f"평균 Confidence: {meta['avg_confidence']:.3f}  |  "
        f"통화: SGD  |  헌법 v3 준수",
        style_subtitle,
    ))
    story.append(HRFlowable(width=CONTENT_W, thickness=1.5,
                            color=colors.HexColor("#1a3a6b"), spaceAfter=12))

    # 품목별 섹션
    for i, product in enumerate(products):
        trade = product.get("trade_name", "")
        inn_label = product.get("inn_label", "")
        pid = product.get("product_id", "")

        # 품목 헤더
        story.append(Paragraph(
            f"{trade}  —  {inn_label}",
            style_product_h,
        ))
        story.append(HRFlowable(width=CONTENT_W, thickness=0.5,
                                color=colors.HexColor("#cccccc"), spaceAfter=4))

        # 관련 사이트 표
        story.append(Paragraph("관련 사이트", style_section))
        sites = product.get("related_sites", {})
        story.append(_sites_table(sites))
        story.append(Spacer(1, 4))

        # 수출 판정 표
        story.append(Paragraph("수출 판정", style_section))
        story.append(_verdict_table(
            product.get("verdict", "조건부"),
            product.get("verdict_en", "CONDITIONAL"),
            product.get("price_local_sgd"),
            product.get("success_prob"),
        ))
        story.append(Spacer(1, 4))

        # 근거 문단 + 출처
        story.extend(_rationale_section(product))

        # 품목 사이 구분 (마지막 제외)
        if i < len(products) - 1:
            story.append(PageBreak())

    # 꼬리말 주석
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width=CONTENT_W, thickness=0.5,
                            color=colors.HexColor("#cccccc"), spaceAfter=4))
    story.append(Paragraph(
        "※ price_local은 SGD 현지가. fob_estimated_usd는 2공정 FOB 역산 모듈에서 산출됩니다.  "
        "※ confidence 1.00 금지 (헌법 §2 명시).",
        ParagraphStyle("Footer", fontName=base_font, fontSize=7,
                       textColor=colors.HexColor("#888888")),
    ))

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
        analysis = asyncio.run(analyze_all(db_path=db_path))
        # 분석 결과 JSON 저장
        ana_path = out_dir / f"sg_analysis_{ts}.json"
        ana_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] 분석 JSON → {ana_path}")

    if analysis is None:
        print("[report] 정적 폴백으로 분석 실행 중...")
        from analysis.sg_export_analyzer import analyze_all
        analysis = asyncio.run(analyze_all(db_path=db_path, use_perplexity=False))

    # DB에서 제품 로드
    products = load_products(db_path)
    report = build_report(products, generated_at, analysis)

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
