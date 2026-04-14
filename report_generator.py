#!/usr/bin/env python3
"""싱가포르 시장 분석 보고서 생성기 (Supabase 기반).

출력 형식:
  reports/sg_report_YYYYMMDD_HHMMSS.json  — 전체 데이터 (기계 판독용)
  reports/sg_report_YYYYMMDD_HHMMSS.pdf   — 양식 기준 보고서 (사람 판독용)

PDF 구조 (품목별 2페이지):
  페이지1: 회사명·제목·제품 바·1 판정·2 근거(시장/규제/무역+PBS 참고가)·3 전략(채널·가격·리스크)
  페이지2: 4 근거·출처(논문·출처 요약 표·DB/기관)

실행:
  python report_generator.py
  python report_generator.py --out reports/
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

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

# ── 8개 품목 기대 product_id ──────────────────────────────────────────────────

_EXPECTED_PRODUCTS = [
    "SG_omethyl_omega3_2g",
    "SG_sereterol_activair",
    "SG_hydrine_hydroxyurea_500",
    "SG_gadvoa_gadobutrol_604",
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

# verdict 기반 확률 매핑 — 하드코딩 수치 제거
_VERDICT_TO_PROB: dict[str | None, float] = {
    "적합":   0.80,
    "조건부": 0.50,
    "부적합": 0.15,
    None:     0.00,
}

def _get_success_prob(verdict: str | None) -> float:
    return _VERDICT_TO_PROB.get(verdict, 0.00)

# 품목별 관련 사이트 (양식 §1) — 가격/GeBIZ 제거
_RELATED_SITES: dict[str, dict[str, list[tuple[str, str]]]] = {
    pid: {
        "public": [
            ("HSA eService Portal", "https://www.hsa.gov.sg/e-services"),
            ("MOH Singapore", "https://www.moh.gov.sg"),
            ("data.gov.sg", "https://data.gov.sg"),
        ],
        "private": [],
        "papers": [
            ("PubMed Central", "https://www.ncbi.nlm.nih.gov/pmc"),
            ("싱가포르 보건부 Clinical Practice Guidelines",
             "https://www.moh.gov.sg/resources-statistics/guidelines-and-guidelines"),
        ],
    }
    for pid in _EXPECTED_PRODUCTS
}


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_products() -> list[dict]:
    """Supabase products 테이블에서 KUP 싱가포르 품목을 조회."""
    from utils.db import fetch_kup_products
    return fetch_kup_products("SG")


# ── 보고서 데이터 조합 ────────────────────────────────────────────────────────

def build_report(
    products: list[dict],
    generated_at: str,
    analysis: list[dict] | None = None,
    references: dict[str, list[dict[str, str]]] | None = None,
) -> dict:
    # product_key(사람이 읽는 식별자)로 인덱싱 — _EXPECTED_PRODUCTS와 동일한 키 체계
    by_pid: dict[str, dict] = {p.get("product_key") or p["product_id"]: p for p in products}
    analysis_by_pid: dict[str, dict] = (
        {a["product_id"]: a for a in analysis} if analysis else {}
    )
    refs_by_pid: dict[str, list] = references or {}

    items = []
    if analysis:
        ordered = [a.get("product_id", "") for a in analysis if a.get("product_id")]
        target_pids = [pid for pid in _EXPECTED_PRODUCTS if pid in ordered]
    else:
        target_pids = list(_EXPECTED_PRODUCTS)
    total = len(target_pids)

    for pid in target_pids:
        row = by_pid.get(pid)
        trade = _TRADE_NAMES.get(pid, pid)
        inn = _INN_NAMES.get(pid, "")
        ana = analysis_by_pid.get(pid, {})

        if row:
            item: dict[str, Any] = {
                "product_id": pid,
                "trade_name": row.get("trade_name") or trade,
                "inn_label": inn,
                "market_segment": row.get("market_segment"),
                "regulatory_id": row.get("regulatory_id"),
                "db_confidence": row.get("confidence"),
                "status": "loaded",
            }
        else:
            item = {
                "product_id": pid,
                "trade_name": trade,
                "inn_label": inn,
                "market_segment": None,
                "regulatory_id": None,
                "db_confidence": None,
                "status": "not_loaded",
            }

        # 분석 결과 병합
        verdict = ana.get("verdict")
        item["verdict"] = verdict                      # None = API 미설정
        item["verdict_en"] = ana.get("verdict_en")
        item["rationale"] = ana.get("rationale", "")
        item["basis_market_medical"] = ana.get("basis_market_medical", "")
        item["basis_regulatory"] = ana.get("basis_regulatory", "")
        item["basis_trade"] = ana.get("basis_trade", "")
        item["key_factors"] = ana.get("key_factors", [])
        item["entry_pathway"] = ana.get("entry_pathway", "")
        item["price_positioning_pbs"] = ana.get("price_positioning_pbs", "")
        item["pbs_listing_url"] = ana.get("pbs_listing_url")
        item["pbs_schedule_drug_name"] = ana.get("pbs_schedule_drug_name")
        item["pbs_pack_description"] = ana.get("pbs_pack_description")
        item["pbs_dpmq_aud"] = ana.get("pbs_dpmq_aud")
        item["pbs_dpmq_sgd_hint"] = ana.get("pbs_dpmq_sgd_hint")
        item["pbs_methodology_label_ko"] = ana.get("pbs_methodology_label_ko") or "(PBS, 방법론적 추산)"
        item["pbs_search_hit"] = ana.get("pbs_search_hit")
        item["pbs_fetch_error"] = ana.get("pbs_fetch_error")
        item["risks_conditions"] = ana.get("risks_conditions", "")
        item["hsa_reg"] = ana.get("hsa_reg", "")
        item["product_type"] = ana.get("product_type", "")
        item["analysis_sources"] = ana.get("sources", [])
        item["analysis_model"] = ana.get("analysis_model", "")
        item["analysis_error"] = ana.get("analysis_error")
        item["claude_model_id"] = ana.get("claude_model_id", "")
        item["claude_error_detail"] = ana.get("claude_error_detail")
        item["success_prob"] = _get_success_prob(verdict)

        # ── 관련 사이트 — DB 소스 + Perplexity 논문 ────────────────────────────
        base_sites = _RELATED_SITES.get(pid, {"public": [], "private": [], "papers": []})

        # Perplexity 논문 결과가 있으면 우선 사용, 없으면 기본값 유지
        paper_refs = refs_by_pid.get(pid, [])
        if paper_refs:
            papers_list = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "summary_ko": r.get("reason", ""),
                    "source": r.get("source", ""),
                }
                for r in paper_refs
                if r.get("title") and r.get("url")
            ]
        else:
            papers_list = [
                {"title": name, "url": url, "summary_ko": "기본 참고 출처"}
                for name, url in base_sites.get("papers", [])
            ]

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

        used_data_sources: list[dict[str, str]] = []
        if row:
            src_name = str(row.get("source_name", "") or "")
            src_url = str(row.get("source_url", "") or "")
            if src_name:
                desc = "Supabase products 테이블의 수집 원천 레코드"
                if src_name == "SG:kup_pipeline":
                    desc = "KUP 파이프라인 표준 행(제품 식별/세그먼트/신뢰도)"
                used_data_sources.append(
                    {
                        "name": src_name,
                        "description": desc,
                        "url": src_url,
                    }
                )
        for s in item.get("analysis_sources", []) or []:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name", "") or "").strip()
            url = str(s.get("url", "") or "").strip()
            if not name:
                continue
            if any(d["name"] == name and d.get("url", "") == url for d in used_data_sources):
                continue
            used_data_sources.append(
                {
                    "name": name,
                    "description": "분석 단계에서 실제로 참조된 근거 출처",
                    "url": url,
                }
            )
        pbs_url = item.get("pbs_listing_url")
        if isinstance(pbs_url, str) and pbs_url.strip():
            if not any(
                d.get("url", "") == pbs_url.strip() for d in used_data_sources
            ):
                used_data_sources.append(
                    {
                        "name": "PBS Australia",
                        "description": (
                            "호주 PBS 공개 스케줄에서 추출한 DPMQ 등 "
                            "(PBS, 방법론적 추산 — 싱가포르 약가 아님)"
                        ),
                        "url": pbs_url.strip(),
                    }
                )
        item["used_data_sources"] = used_data_sources

        items.append(item)

    verdict_counts = {
        "적합": sum(1 for it in items if it.get("verdict") == "적합"),
        "조건부": sum(1 for it in items if it.get("verdict") == "조건부"),
        "부적합": sum(1 for it in items if it.get("verdict") == "부적합"),
        "미분석": sum(1 for it in items if it.get("verdict") is None),
    }

    return {
        "meta": {
            "generated_at": generated_at,
            "country": "SG",
            "currency": "SGD",
            "total_products": total,
            "verdict_summary": verdict_counts,
            "data_sources": [
                "HSA (Supabase)",
                "WHO EML (Supabase)",
                "GLOBOCAN (Supabase)",
                "규제 PDF",
                "Perplexity API",
                "PBS Australia (공개 스케줄, 방법론적 추산)",
            ],
            "reference_pricing": {
                "primary_label": "(PBS, 방법론적 추산)",
                "aud_field": "pbs_dpmq_aud (DPMQ)",
                "sgd_note": "pbs_dpmq_sgd_hint 는 참고 환산(환율 변동)",
            },
            "note": (
                "싱가포르 공개 병원·소매 약가는 본 파이프라인에서 직접 수집하지 않습니다. "
                "호주 PBS 공개 스케줄의 DPMQ를 (PBS, 방법론적 추산)으로 표기해 참고합니다."
            ),
        },
        "products": items,
    }


# ── PDF 렌더링 ────────────────────────────────────────────────────────────────

def _register_korean_font() -> str:
    """한글 지원 폰트를 등록하고 폰트명을 반환. 등록 실패 시 Helvetica 반환."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        # Render/Linux 배포환경 — download_fonts.py 가 빌드 시 받아놓은 파일
        ("NanumGothic",  str(ROOT / "fonts" / "NanumGothic.ttf")),
        # macOS 시스템 폰트
        ("AppleGothic",  "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        ("AppleGothic",  "/Library/Fonts/AppleGothic.ttf"),
        ("NanumGothic",  "/Library/Fonts/NanumGothic.ttf"),
        # Windows
        ("MalgunGothic", "C:/Windows/Fonts/malgun.ttf"),
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
    """보고서 데이터를 2페이지 템플릿(회사 헤더·제품 바·번호 섹션·PBS 가격)으로 PDF 저장."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER
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
    C_BODY   = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT    = colors.HexColor("#F4F6F9")

    COL1 = CONTENT_W * 0.26
    COL2 = CONTENT_W * 0.74

    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_title = ps(
        "Title",
        fontName=bold_font,
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        textColor=C_NAVY,
        spaceAfter=4,
    )
    s_date = ps(
        "Date",
        fontName=base_font,
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#6B7280"),
    )
    s_section = ps(
        "Section",
        fontName=bold_font,
        fontSize=11,
        textColor=C_NAVY,
        leading=15,
        spaceBefore=8,
        spaceAfter=4,
    )
    s_cell_h = ps("CellH", fontName=bold_font, fontSize=9, textColor=C_NAVY, leading=13, wordWrap="CJK")
    s_cell = ps("Cell", fontName=base_font, fontSize=9, textColor=C_BODY, leading=14, wordWrap="CJK")
    s_company = ps(
        "Co",
        fontName=bold_font,
        fontSize=11,
        alignment=TA_CENTER,
        textColor=C_NAVY,
        spaceAfter=2,
    )
    s_bar = ps(
        "Bar",
        fontName=bold_font,
        fontSize=9,
        textColor=colors.white,
        leading=13,
        wordWrap="CJK",
    )
    s_hdr = ps(
        "HdrWhite",
        fontName=bold_font,
        fontSize=9,
        textColor=colors.white,
        leading=13,
        wordWrap="CJK",
    )
    s_cell_sm = ps(
        "CellSm",
        fontName=base_font,
        fontSize=7,
        textColor=colors.HexColor("#6B7280"),
        leading=10,
        wordWrap="CJK",
    )

    def _rx(text: str) -> str:
        return (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    _ACE_NOTE = (
        "ACE는 호주·캐나다·영국 등 HTA 기관 결정을 참고해 싱가포르 적용 가능성을 검토합니다."
    )

    def _pbs_one_line(p: dict[str, Any]) -> str:
        aud = p.get("pbs_dpmq_aud")
        sgd = p.get("pbs_dpmq_sgd_hint")
        if isinstance(aud, (int, float)):
            line = f"DPMQ AUD {aud:.2f}"
            if isinstance(sgd, (int, float)):
                line += f", 참고 SGD {sgd:.2f}"
            return f"{line} / {_ACE_NOTE}"
        haiku = str(p.get("pbs_haiku_estimate") or "").strip()
        if haiku:
            return haiku
        return _ACE_NOTE

    def _triple_table(rows: list[tuple[str, str, str]]) -> Table:
        w1, w2, w3 = CONTENT_W * 0.28, CONTENT_W * 0.14, CONTENT_W * 0.58
        pdata = [
            [
                Paragraph(_rx(a), s_cell_h),
                Paragraph(_rx(b), s_cell),
                Paragraph(_rx(c), s_cell),
            ]
            for a, b, c in rows
        ]
        t = Table(pdata, colWidths=[w1, w2, w3])
        t.setStyle(TableStyle(_base_style()))
        return t

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

    def _simple_table(rows: list[list[str]], *, shade_alt: bool = True) -> Table:
        pdata = [
            [Paragraph(_rx(r[0]), s_cell_h), Paragraph(_rx(r[1]), s_cell)]
            for r in rows
        ]
        t = Table(pdata, colWidths=[COL1, COL2])
        extras: list[tuple[Any, ...]] = []
        if shade_alt:
            for i in range(len(rows)):
                if i % 2 == 1:
                    extras.append(("BACKGROUND", (0, i), (-1, i), C_ALT))
        t.setStyle(TableStyle(_base_style(extras)))
        return t

    def _fmt_date(raw: str) -> str:
        try:
            return datetime.fromisoformat(raw).strftime("%Y-%m-%d")
        except Exception:
            return raw[:10]

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="싱가포르 1공정 시장조사 보고서",
    )

    story: list = []

    for idx, product in enumerate(report["products"]):
        generated_date = _fmt_date(report.get("meta", {}).get("generated_at", ""))
        trade = str(product.get("trade_name", "") or "—")
        inn = str(product.get("inn_label", "") or "—")
        verdict = str(product.get("verdict", "") or "미분석")

        # 1페이지 — 파나마 양식 스타일(회사명·제목·제품 바·번호 섹션)
        story.append(Paragraph(_rx("Korea United Pharm. Inc."), s_company))
        story.append(Paragraph(_rx("싱가포르 시장 분석 보고서"), s_title))
        story.append(Paragraph(_rx(generated_date), s_date))
        story.append(Spacer(1, 6))

        reg_id = str(product.get("regulatory_id", "") or "—")
        conf_raw = product.get("db_confidence")
        if isinstance(conf_raw, (int, float)):
            conf_s = f"{float(conf_raw):.2f}"
        else:
            conf_s = "—"
        bar_txt = f"{trade} — {inn} | regulatory_id: {reg_id} | confidence {conf_s}"
        bar_tbl = Table([[Paragraph(_rx(bar_txt), s_bar)]], colWidths=[CONTENT_W])
        bar_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#4B5563")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(bar_tbl)
        story.append(Spacer(1, 10))

        story.append(Paragraph(_rx("1. 진출 적합 판정"), s_section))
        story.append(
            _simple_table([["판정", verdict]], shade_alt=False),
        )
        story.append(Spacer(1, 6))

        story.append(Paragraph(_rx("2. 판정 근거"), s_section))
        pbs_line = _pbs_one_line(product)
        story.append(
            _simple_table(
                [
                    ["시장/의료", str(product.get("basis_market_medical", "") or "—")],
                    ["규제", str(product.get("basis_regulatory", "") or "—")],
                    ["무역", str(product.get("basis_trade", "") or "—")],
                    ["참고 가격", pbs_line],
                ]
            )
        )
        story.append(Spacer(1, 6))

        story.append(Paragraph(_rx("3. 시장 진출 전략"), s_section))
        price_txt = str(product.get("price_positioning_pbs", "") or "").strip() or pbs_line
        story.append(
            _simple_table(
                [
                    ["진입 채널 권고", str(product.get("entry_pathway", "") or "—")],
                    ["가격 포지셔닝", price_txt],
                    ["리스크 + 조건", str(product.get("risks_conditions", "") or "—")],
                ]
            )
        )

        story.append(PageBreak())

        # 2페이지
        story.append(Paragraph(_rx("4. 근거 및 출처"), s_section))

        # ── 4-1. Perplexity 추천 논문 (표 형식) ────────────────────────────────
        story.append(Paragraph(_rx("4-1. Perplexity 추천 논문"), s_section))
        papers = product.get("related_sites", {}).get("papers", []) or []
        valid_papers = [p for p in papers if isinstance(p, dict) and (p.get("title") or p.get("url"))]

        if valid_papers:
            w_no    = CONTENT_W * 0.05
            w_title = CONTENT_W * 0.56
            w_sum   = CONTENT_W * 0.39

            paper_tbl: list[list] = [[
                Paragraph("No.", s_hdr),
                Paragraph("논문 제목 / 출처", s_hdr),
                Paragraph("한국어 요약", s_hdr),
            ]]
            extras_p: list[tuple] = [
                ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
            ]
            for i, p in enumerate(valid_papers, 1):
                title   = str(p.get("title",      "") or "")
                url     = str(p.get("url",         "") or "")
                source  = str(p.get("source",      "") or "")
                summary = str(p.get("summary_ko",  "") or "관련성 설명 없음")

                title_lines = _rx(title)
                if source:
                    title_lines += f"\n[{_rx(source)}]"
                if url:
                    short_url = url[:75] + ("…" if len(url) > 75 else "")
                    title_lines += f"\n{short_url}"

                paper_tbl.append([
                    Paragraph(str(i), s_cell),
                    Paragraph(title_lines, s_cell),
                    Paragraph(_rx(summary), s_cell),
                ])
                if i % 2 == 0:
                    extras_p.append(("BACKGROUND", (0, i), (-1, i), C_ALT))

            pt = Table(paper_tbl, colWidths=[w_no, w_title, w_sum])
            pt.setStyle(TableStyle(_base_style(extras_p)))
            story.append(pt)
        else:
            story.append(_simple_table([["Perplexity 논문", "사용된 논문 링크 없음"]], shade_alt=False))

        story.append(Spacer(1, 8))

        # ── 4-2. 출처 요약 ─────────────────────────────────────────────────────
        pbs_n   = "1" if product.get("pbs_search_hit") else "0"
        paper_n = str(len(valid_papers))
        story.append(Paragraph(_rx("4-2. 출처 요약 (건수·비고)"), s_section))
        story.append(
            _triple_table(
                [
                    ("출처", "건수", "신뢰도 / 비고"),
                    ("Perplexity 논문", paper_n, "문헌 링크·요약 참고"),
                    ("PBS Australia", pbs_n, "(PBS, 방법론적 추산) — 싱가포르 약가 아님"),
                ]
            )
        )
        story.append(Spacer(1, 8))

        # ── 4-3. 사용된 DB/기관 (3컬럼 표) ────────────────────────────────────
        story.append(Paragraph(_rx("4-3. 사용된 DB/기관"), s_section))
        db_sources = [
            src for src in (product.get("used_data_sources", []) or [])
            if isinstance(src, dict) and src.get("name")
        ]
        if db_sources:
            w_name = CONTENT_W * 0.25
            w_desc = CONTENT_W * 0.45
            w_link = CONTENT_W * 0.30

            db_tbl: list[list] = [[
                Paragraph("DB/기관명", s_hdr),
                Paragraph("설명", s_hdr),
                Paragraph("링크", s_hdr),
            ]]
            extras_d: list[tuple] = [
                ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
            ]
            for i, src in enumerate(db_sources, 1):
                name = str(src.get("name",        "") or "")
                desc = str(src.get("description", "") or "")
                url  = str(src.get("url",         "") or "")
                short_url = (url[:55] + "…" if len(url) > 55 else url) if url else "—"
                db_tbl.append([
                    Paragraph(_rx(name),      s_cell),
                    Paragraph(_rx(desc),      s_cell),
                    Paragraph(_rx(short_url), s_cell_sm),
                ])
                if i % 2 == 0:
                    extras_d.append(("BACKGROUND", (0, i), (-1, i), C_ALT))

            dt = Table(db_tbl, colWidths=[w_name, w_desc, w_link])
            dt.setStyle(TableStyle(_base_style(extras_d)))
            story.append(dt)
        else:
            story.append(_simple_table([["사용된 DB/기관", "이번 분석에서 확인된 DB 출처 정보 없음"]], shade_alt=False))

        if idx < len(report["products"]) - 1:
            story.append(PageBreak())

    doc.build(story)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="싱가포르 시장 분석 보고서 생성 (Supabase 기반)")
    parser.add_argument("--out", default=str(ROOT / "reports"))
    parser.add_argument(
        "--analysis-json",
        default=None,
        help="기존 분석 결과 JSON 파일 경로 (없으면 Claude API로 실행)",
    )
    parser.add_argument(
        "--no-perplexity",
        action="store_true",
        help="Perplexity 논문 검색 건너뜀",
    )
    args = parser.parse_args(argv)

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
            print(f"[report] 경고: {analysis_path} 없음 — Claude API로 실행")

    if analysis is None:
        print("[report] Claude API로 분석 실행 중... (API 키 없으면 미실행 메시지 표시)")
        from analysis.sg_export_analyzer import analyze_all
        analysis = asyncio.run(analyze_all(use_perplexity=not args.no_perplexity))
        # 분석 결과 JSON 저장
        ana_path = out_dir / f"sg_analysis_{ts}.json"
        ana_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] 분석 JSON → {ana_path}")

    # Perplexity 논문 검색
    references: dict[str, list] = {}
    if not args.no_perplexity:
        print("[report] Perplexity 논문 검색 중... (API 키 없으면 기본 사이트 사용)")
        from analysis.perplexity_references import fetch_all_references
        references = asyncio.run(fetch_all_references())
        ref_count = sum(len(v) for v in references.values())
        print(f"[report] 논문 검색 완료: {ref_count}건")

    # Supabase에서 KUP 제품 로드
    print("[report] Supabase에서 품목 데이터 로드 중...")
    products = load_products()
    print(f"[report] 품목 로드 완료: {len(products)}건")

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
    vs = meta.get("verdict_summary", {})
    print(
        f"\n[report] 판정 결과 — "
        f"적합: {vs.get('적합', 0)}건 / "
        f"조건부: {vs.get('조건부', 0)}건 / "
        f"부적합: {vs.get('부적합', 0)}건 / "
        f"미분석: {vs.get('미분석', 0)}건 "
        f"(총 {meta['total_products']}품목)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
