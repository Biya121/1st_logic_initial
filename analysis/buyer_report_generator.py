"""바이어 발굴 보고서 PDF 생성기.

구조:
  표지: 제품명 + 분석일
  요약 테이블: Top 10 기업 한눈에 보기
  기업별 상세 페이지:
    기업 개요 / 추천 이유 / 기본 정보 / 기업 규모 / 역량·실적 / 채널·파트너십 / 출처
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _esc(text: Any) -> str:
    """XML 특수문자 이스케이프 — ReportLab Paragraph 파싱 오류 방지."""
    return _html.escape(str(text)) if text is not None else ""

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── 한글 폰트 등록 ────────────────────────────────────────────────────────────
_FONT_DIR = Path(__file__).resolve().parents[1] / "fonts"
_FONT_REGULAR = _FONT_DIR / "NanumGothic.ttf"
_FONT_BOLD    = _FONT_DIR / "NanumGothicBold.ttf"

def _register_fonts() -> tuple[str, str]:
    """한글 폰트 등록. Regular만 있어도 동작; 없으면 Helvetica 폴백."""
    if not _FONT_REGULAR.is_file():
        return "Helvetica", "Helvetica-Bold"
    try:
        pdfmetrics.registerFont(TTFont("Korean", str(_FONT_REGULAR)))
        bold_name = "Korean"
        if _FONT_BOLD.is_file():
            pdfmetrics.registerFont(TTFont("Korean-Bold", str(_FONT_BOLD)))
            bold_name = "Korean-Bold"
        pdfmetrics.registerFontFamily(
            "Korean",
            normal="Korean",
            bold=bold_name,
            italic="Korean",
            boldItalic=bold_name,
        )
        return "Korean", bold_name
    except Exception:
        return "Helvetica", "Helvetica-Bold"

_FONT, _FONT_BOLD_NAME = _register_fonts()

# ── 색상 ──────────────────────────────────────────────────────────────────────
_NAVY   = colors.Color(23/255, 63/255, 120/255)
_GREEN  = colors.Color(39/255, 174/255, 96/255)
_ORANGE = colors.Color(230/255, 126/255, 34/255)
_LIGHT  = colors.Color(245/255, 247/255, 250/255)
_MUTED  = colors.Color(120/255, 130/255, 150/255)
_REASON = colors.Color(235/255, 245/255, 255/255)  # 추천이유 배경
_WHITE  = colors.white

W, H = A4


def _styles() -> dict:
    base = getSampleStyleSheet()

    def _s(name, parent="Normal", **kw) -> ParagraphStyle:
        return ParagraphStyle(name, parent=base[parent], **kw)

    return {
        "cover_title": _s("cover_title", fontSize=22, leading=30, textColor=_NAVY,
                          fontName=_FONT_BOLD_NAME, spaceAfter=4, wordWrap="CJK"),
        "cover_sub":   _s("cover_sub",   fontSize=13, leading=18, textColor=_MUTED,
                          fontName=_FONT, spaceAfter=12, wordWrap="CJK"),
        "section":     _s("section",     fontSize=10, leading=14, textColor=_NAVY,
                          fontName=_FONT_BOLD_NAME, spaceBefore=8, spaceAfter=3, wordWrap="CJK"),
        "body":        _s("body",        fontSize=9,  leading=14, textColor=colors.black,
                          fontName=_FONT, spaceAfter=2, wordWrap="CJK"),
        "small":       _s("small",       fontSize=8,  leading=12, textColor=_MUTED,
                          fontName=_FONT, wordWrap="CJK"),
        "reason":      _s("reason",      fontSize=9,  leading=15, textColor=colors.black,
                          fontName=_FONT, spaceAfter=2, wordWrap="CJK",
                          backColor=_REASON, borderPadding=(6, 8, 6, 8)),
        "overview":    _s("overview",    fontSize=9,  leading=14, textColor=colors.black,
                          fontName=_FONT, spaceAfter=2, wordWrap="CJK"),
        "link":        _s("link",        fontSize=8,  leading=12, textColor=colors.blue,
                          fontName=_FONT, wordWrap="CJK"),
    }


def _yn(val: Any) -> str:
    if val is True:  return "있음"
    if val is False: return "없음"
    return "-"


def _dash(val: Any) -> str:
    if val is None or str(val).strip() in ("", "None", "null", "-"):
        return "-"
    return str(val)


def _build_cover(product_label: str, company_count: int, styles: dict) -> list:
    """커버 페이지 — 하위 호환용으로 유지하나 기본 흐름에서는 사용하지 않음."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return [
        Spacer(1, 30*mm),
        Paragraph("바이어 발굴 보고서", styles["cover_title"]),
        Paragraph(f"제품: {_esc(product_label)}", styles["cover_sub"]),
        Paragraph(f"발굴 기업 수: {company_count}개  |  분석일: {_esc(now)}", styles["small"]),
        Spacer(1, 6*mm),
        HRFlowable(width="100%", thickness=1.5, color=_NAVY),
        Spacer(1, 4*mm),
        Paragraph(
            "본 보고서는 CPHI Japan 전시회 참가 기업 크롤링 및 Claude AI 심층조사를 통해 "
            "자동 생성된 바이어 발굴 분석 결과입니다. "
            "성분/치료군 일치 기업 및 싱가포르·ASEAN 대상 사업자를 대상으로 수집하였습니다.",
            styles["body"],
        ),
        PageBreak(),
    ]


def _build_summary_table(companies: list[dict], styles: dict, product_label: str = "") -> list:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = len(companies)

    title_txt = f"싱가포르 바이어 후보 리스트 — {product_label}" if product_label else "싱가포르 바이어 후보 리스트"
    elems: list = [
        Paragraph(_esc(title_txt), styles["cover_title"]),
        Paragraph(f"Singapore  |  {now}", styles["cover_sub"]),
        Spacer(1, 3*mm),
        Paragraph(
            "※ 아래 바이어 후보는 CPHI 등록 및 Perplexity 웹 분석을 통해 도출되었으며, "
            "개별 기업의 싱가포르 진출 현황 및 제품 연관성은 추가 실사가 필요합니다.",
            styles["small"],
        ),
        Spacer(1, 4*mm),
        Paragraph(f"1. 바이어 후보 리스트 (전체 {count}개사)", styles["section"]),
        Spacer(1, 2*mm),
    ]

    header = ["#", "기업명", "국가", "카테고리", "이메일"]
    rows   = [header]
    for i, c in enumerate(companies, 1):
        rows.append([
            str(i),
            _esc(c.get("company_name") or "-")[:28],
            _esc(c.get("country") or "-"),
            _esc(c.get("category") or "-")[:20],
            _esc(c.get("email") or "-")[:30],
        ])

    col_w = [8*mm, 55*mm, 25*mm, 38*mm, 48*mm]  # 총 174mm = A4 content_w
    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), _WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), _FONT_BOLD_NAME),
        ("FONTNAME",      (0, 1), (-1, -1), _FONT),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_LIGHT, _WHITE]),
        ("GRID",          (0, 0), (-1, -1), 0.3, _MUTED),
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems += [tbl, PageBreak()]
    return elems


def _build_company_page(c: dict, idx: int, styles: dict) -> list:
    elems: list = []
    name    = _dash(c.get("company_name"))
    country = _dash(c.get("country"))
    e       = c.get("enriched", {})

    # ── 헤더 ──────────────────────────────────────────────────────────────
    hdr_data = [[
        Paragraph(
            f"{idx}.  {_esc(name)}",
            ParagraphStyle("hdr", fontSize=14, textColor=_NAVY,
                           fontName=_FONT_BOLD_NAME, leading=18, wordWrap="CJK"),
        ),
        Paragraph(
            f"{_esc(country)}  ·  {_esc(_dash(c.get('category')))}",
            ParagraphStyle("hdr_r", fontSize=9, textColor=_MUTED,
                           fontName=_FONT, leading=12, wordWrap="CJK"),
        ),
    ]]
    hdr_tbl = Table(hdr_data, colWidths=[114*mm, 60*mm])  # 총 174mm
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "BOTTOM"),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, _NAVY),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
    ]))
    elems += [hdr_tbl, Spacer(1, 3*mm)]

    # ── ▸ 기업 개요 ──────────────────────────────────────────────────────
    overview = _dash(e.get("company_overview_kr"))
    if overview != "-":
        elems.append(Paragraph("▸ 기업 개요", styles["section"]))
        elems.append(Paragraph(_esc(overview), styles["overview"]))
        elems.append(Spacer(1, 2*mm))

    # ── ▸ 추천 이유 (①~⑤ 구조화 테이블 + 근거 텍스트) ────────────────────
    elems.append(Paragraph("▸ 추천 이유", styles["section"]))
    reason = _dash(e.get("recommendation_reason"))
    if reason != "-":
        elems.append(Paragraph(_esc(reason), styles["reason"]))
        elems.append(Spacer(1, 2*mm))

    circled = ["①", "②", "③", "④", "⑤"]
    reason_items = [
        ("매출 규모",     _dash(e.get("revenue"))),
        ("파이프라인",    ", ".join(c.get("products_cphi", [])[:5]) or "-"),
        ("제조소 보유",   "GMP 인증: " + _yn(e.get("has_gmp"))),
        ("수입 경험",     _yn(e.get("import_history"))),
        ("약국 체인 운영", _yn(e.get("has_pharmacy_chain"))),
    ]
    reason_rows = [
        [
            Paragraph(_esc(f"{circled[i]} {lbl}"), styles["small"]),
            Paragraph(_esc(val), styles["body"]),
        ]
        for i, (lbl, val) in enumerate(reason_items)
        if val != "-"
    ]
    if reason_rows:
        r_tbl = Table(reason_rows, colWidths=[30*mm, 144*mm])
        r_tbl.setStyle(TableStyle([
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_LIGHT, _WHITE]),
            ("GRID",          (0, 0), (-1, -1), 0.2, _MUTED),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        elems += [r_tbl, Spacer(1, 3*mm)]
    else:
        elems.append(Spacer(1, 3*mm))

    # ── 기본 정보 (값 있는 항목만) ──────────────────────────────────────────
    def _info_row(l1, v1, l2, v2):
        if v1 == "-" and v2 == "-":
            return None
        return [Paragraph(_esc(l1) if v1 != "-" else "", styles["small"]),
                Paragraph(_esc(v1), styles["body"]),
                Paragraph(_esc(l2) if v2 != "-" else "", styles["small"]),
                Paragraph(_esc(v2), styles["body"])]

    website_val = _dash(c.get("website"))
    website_cell = (
        Paragraph(f'<a href="{_esc(website_val)}"><u>{_esc(website_val)}</u></a>', styles["link"])
        if website_val != "-" else None
    )

    info_candidates = [
        _info_row("주소",   _dash(c.get("address")), "전화",    _dash(c.get("phone"))),
        _info_row("팩스",   _dash(c.get("fax")),     "이메일",  _dash(c.get("email"))),
        _info_row("설립연도", _dash(e.get("founded")), "",       ""),
    ]
    if website_cell:
        info_candidates.append([Paragraph("웹사이트", styles["small"]), website_cell,
                                 Paragraph("", styles["small"]), Paragraph("", styles["body"])])
    info_rows = [r for r in info_candidates if r is not None]
    if info_rows:
        elems.append(Paragraph("기본 정보", styles["section"]))
        info_tbl = Table(info_rows, colWidths=[20*mm, 67*mm, 20*mm, 67*mm])  # 총 174mm
        info_tbl.setStyle(TableStyle([
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_LIGHT, _WHITE]),
            ("GRID",          (0, 0), (-1, -1), 0.2, _MUTED),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        elems += [info_tbl, Spacer(1, 3*mm)]

    # ── 기업 규모 (값 있는 항목만) ──────────────────────────────────────────
    territories = ", ".join(e.get("territories", []))
    sz_revenue   = _dash(e.get("revenue"))
    sz_employees = _dash(e.get("employees"))
    size_candidates = [
        _info_row("연 매출", sz_revenue, "임직원 수", sz_employees),
        _info_row("사업 지역", territories or "-", "", "") if territories else None,
    ]
    size_rows = [r for r in size_candidates if r is not None]
    if size_rows:
        elems.append(Paragraph("기업 규모", styles["section"]))
        size_tbl = Table(size_rows, colWidths=[20*mm, 67*mm, 20*mm, 67*mm])  # 총 174mm
        size_tbl.setStyle(TableStyle([
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_LIGHT, _WHITE]),
            ("GRID",          (0, 0), (-1, -1), 0.2, _MUTED),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        elems += [size_tbl, Spacer(1, 3*mm)]

    # ── 역량 · 실적 · 채널 (true/false 있는 항목만) ──────────────────────────
    def _yn_row(l1, v1, l2, v2):
        has1 = v1 is True or v1 is False
        has2 = v2 is True or v2 is False
        if not has1 and not has2:
            return None
        return [Paragraph(_esc(l1) if has1 else "", styles["small"]),
                Paragraph(_esc(_yn(v1)) if has1 else "", styles["body"]),
                Paragraph(_esc(l2) if has2 else "", styles["small"]),
                Paragraph(_esc(_yn(v2)) if has2 else "", styles["body"])]

    korea_exp = _dash(e.get("korea_experience"))
    cap_candidates = [
        _yn_row("GMP 인증",     e.get("has_gmp"),            "수입 이력",    e.get("import_history")),
        _yn_row("공공조달 이력", e.get("procurement_history"), "공공 채널",    e.get("public_channel")),
        _yn_row("민간 채널",    e.get("private_channel"),     "약국 체인",    e.get("has_pharmacy_chain")),
        _yn_row("MAH 대행",     e.get("mah_capable"),         "한국 거래 경험", None),
    ]
    cap_rows = [r for r in cap_candidates if r is not None]
    if korea_exp != "-":
        cap_rows.append([Paragraph("한국 거래 경험", styles["small"]),
                         Paragraph(_esc(korea_exp), styles["body"]),
                         Paragraph("", styles["small"]), Paragraph("", styles["body"])])
    if cap_rows:
        elems.append(Paragraph("역량 · 실적 · 채널", styles["section"]))
        cap_tbl = Table(cap_rows, colWidths=[25*mm, 62*mm, 25*mm, 62*mm])  # 총 174mm
        cap_tbl.setStyle(TableStyle([
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_LIGHT, _WHITE]),
            ("GRID",          (0, 0), (-1, -1), 0.2, _MUTED),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elems += [cap_tbl, Spacer(1, 3*mm)]

    # ── CPHI 등록 제품 ────────────────────────────────────────────────────
    cphi_prods = c.get("products_cphi", [])
    if cphi_prods:
        elems.append(Paragraph("CPHI 등록 제품", styles["section"]))
        elems.append(Paragraph(_esc(" / ".join(cphi_prods[:15])), styles["small"]))
        elems.append(Spacer(1, 2*mm))

    # ── 참조 출처 ─────────────────────────────────────────────────────────
    if e.get("source_urls") or c.get("perplexity_text"):
        elems.append(Paragraph("출처", styles["section"]))
        elems.append(Paragraph("Perplexity 분석", styles["body"]))

    elems.append(PageBreak())
    return elems


def build_buyer_pdf(
    companies: list[dict[str, Any]],
    product_label: str,
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18*mm,
        rightMargin=18*mm,
        topMargin=16*mm,
        bottomMargin=16*mm,
    )
    styles = _styles()
    elems: list = []
    if companies:
        # Section 1: 전체 요약 테이블 (커버 없음)
        elems += _build_summary_table(companies, styles, product_label)

        # Section 2: 상위 3개사 상세 정보
        top3 = companies[:3]
        sec2_note = (
            "※ 하기 3개사는 Gadvoa Inj.의 조영제 성분과 연관성, APAC 지역 네트워크, "
            "싱가포르 진출 가능성을 종합 평가하여 선정하였습니다."
        ) if not product_label else (
            f"※ 하기 {len(top3)}개사는 {product_label}의 성분 연관성, APAC 지역 네트워크, "
            "싱가포르 진출 가능성을 종합 평가하여 선정하였습니다."
        )
        elems += [
            Paragraph(f"2. 우선 접촉 바이어 상세 정보 (상위 {len(top3)}개사)", styles["section"]),
            Spacer(1, 2*mm),
            Paragraph(_esc(sec2_note), styles["small"]),
            Spacer(1, 4*mm),
        ]
        for i, c in enumerate(top3, 1):
            elems += _build_company_page(c, i, styles)
    doc.build(elems)
