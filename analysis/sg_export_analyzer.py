"""싱가포르 1공정 수출 적합성 분석 엔진.

LLM 우선순위 (가이드라인 §1):
  1. Claude API (claude-sonnet-4-6) — 1차 분석·판단·근거 생성 (Primary)
  2. Perplexity API (sonar-pro)    — Claude 불확실 판정 시에만 보조 검색 후 재분석
  3. 정적 폴백                     — API 미설정 시

흐름:
  Claude 1차 분석 → verdict_confidence 낮으면 → Perplexity 보조 검색
  → Claude 2차 분석 (보강된 컨텍스트) → 최종 결과

출력 스키마 (품목별):
  product_id, trade_name, verdict(적합/부적합/조건부),
  rationale(근거 문단), key_factors, sources, analyzed_at

환경변수:
  CLAUDE_API_KEY 또는 ANTHROPIC_API_KEY
  PERPLEXITY_API_KEY  (선택)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# ── 8품목 메타 (분석 컨텍스트) ────────────────────────────────────────────────

PRODUCT_META: list[dict[str, Any]] = [
    {
        "product_id": "SG_hydrine_hydroxyurea_500",
        "trade_name": "Hydrine",
        "inn": "hydroxyurea",
        "atc": "L01XX05",
        "dosage_form": "캡슐 500mg",
        "market_segment": "hospital",
        "therapeutic_area": "항암(겸상적혈구병, 만성 골수성 백혈병)",
        "hsa_reg": "예상 등재",
        "key_risk": "세포독성 약물 — 취급·운송 특별 요건",
    },
    {
        "product_id": "SG_gadvoa_gadobutrol_604",
        "trade_name": "Gadvoa Inj.",
        "inn": "gadobutrol",
        "atc": "V08CA09",
        "dosage_form": "주사제 1mmol/mL",
        "market_segment": "hospital",
        "therapeutic_area": "MRI 조영제",
        "hsa_reg": "등재 확인 필요",
        "key_risk": "콜드체인·냉장 보관 필요",
    },
    {
        "product_id": "SG_sereterol_activair",
        "trade_name": "Sereterol Activair",
        "inn": "fluticasone/salmeterol",
        "atc": "R03AK06",
        "dosage_form": "건식 분말 흡입기",
        "market_segment": "retail",
        "therapeutic_area": "천식·COPD",
        "hsa_reg": "등재",
        "key_risk": "복합제 특허 이슈 확인 필요",
    },
    {
        "product_id": "SG_omethyl_omega3_2g",
        "trade_name": "Omethyl",
        "inn": "omega-3 acid ethyl esters",
        "atc": "C10AX06",
        "dosage_form": "연질캡슐 2g",
        "market_segment": "retail",
        "therapeutic_area": "고중성지방혈증",
        "hsa_reg": "등재",
        "key_risk": "OTC 전환 여부 확인",
    },
    {
        "product_id": "SG_rosumeg_combigel",
        "trade_name": "Rosumeg Combigel",
        "inn": "rosuvastatin/omega-3 acid ethyl esters",
        "atc": "C10BA06",
        "dosage_form": "연질캡슐 (복합제)",
        "market_segment": "wholesale",
        "therapeutic_area": "이상지질혈증 복합치료",
        "hsa_reg": "등재 확인 필요",
        "key_risk": "복합제 HSA 별도 등재 요건",
    },
    {
        "product_id": "SG_atmeg_combigel",
        "trade_name": "Atmeg Combigel",
        "inn": "atorvastatin/omega-3 acid ethyl esters",
        "atc": "C10BA05",
        "dosage_form": "연질캡슐 (복합제)",
        "market_segment": "wholesale",
        "therapeutic_area": "이상지질혈증 복합치료",
        "hsa_reg": "등재 확인 필요",
        "key_risk": "복합제 HSA 별도 등재 요건",
    },
    {
        "product_id": "SG_ciloduo_cilosta_rosuva",
        "trade_name": "Ciloduo",
        "inn": "cilostazol/rosuvastatin",
        "atc": "B01AC23",
        "dosage_form": "정제 (복합제)",
        "market_segment": "wholesale",
        "therapeutic_area": "말초동맥질환·이상지질혈증",
        "hsa_reg": "SAR 검토 중",
        "key_risk": "혁신 복합제 — 비교 임상 자료 요구 가능",
    },
    {
        "product_id": "SG_gastiin_cr_mosapride",
        "trade_name": "Gastiin CR",
        "inn": "mosapride",
        "atc": "A03FA05",
        "dosage_form": "서방정",
        "market_segment": "wholesale",
        "therapeutic_area": "위장관 운동 촉진",
        "hsa_reg": "SAR 검토 중",
        "key_risk": "아시아 외 국가 승인 데이터 부족",
    },
]

_META_BY_PID: dict[str, dict[str, Any]] = {m["product_id"]: m for m in PRODUCT_META}


# ── Perplexity 보조 검색 ──────────────────────────────────────────────────────

async def _perplexity_search(query: str, api_key: str) -> str | None:
    """Perplexity sonar-pro로 규제·시장 정보 검색. 실패 시 None."""
    try:
        import httpx
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a pharmaceutical regulatory expert. "
                        "Provide factual, concise information about drug regulatory status "
                        "and market conditions in Singapore and Southeast Asia. "
                        "Always cite sources when available."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "max_tokens": 512,
            "return_citations": True,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception:
        return None


# ── Claude 분석 (Primary) ────────────────────────────────────────────────────

def _build_analysis_prompt(
    meta: dict[str, Any],
    db_row: dict[str, Any] | None,
    perplexity_context: str | None,
    static_context_text: str | None = None,
) -> str:
    price_info = "수집 전"
    if db_row and db_row.get("price_local") is not None:
        price_info = f"S${db_row['price_local']:.2f} (confidence {db_row.get('confidence', '?'):.2f})"

    reg_context = perplexity_context or "미수행"

    static_section = ""
    if static_context_text:
        static_section = f"\n## HSA 등재 DB + 현지 문서 데이터\n{static_context_text}\n"

    return f"""당신은 싱가포르 의약품 수출 가능성을 분석하는 전문 컨설턴트입니다.
아래 품목에 대해 싱가포르 1공정(현지가 수집·규제 적합성) 관점에서 수출 적합성을 판단하세요.

## 품목 정보
- 브랜드명: {meta['trade_name']}
- INN(성분): {meta['inn']}
- ATC 코드: {meta['atc']}
- 제형: {meta['dosage_form']}
- 시장 세그먼트: {meta['market_segment']}
- 치료 영역: {meta['therapeutic_area']}
- HSA 등재 상태: {meta['hsa_reg']}
- 주요 리스크: {meta['key_risk']}

## 현재 수집 가격
{price_info}
{static_section}
## 실시간 규제·시장 정보 (Perplexity)
{reg_context}

## 분석 과제
1. 싱가포르 HSA 규제 요건 충족 가능성
2. 현지 시장 경쟁 구도 및 가격 경쟁력
3. 수출 실행 시 주요 장애 요인
4. 최종 판정: 적합 / 부적합 / 조건부

반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "verdict": "적합" | "부적합" | "조건부",
  "verdict_en": "SUITABLE" | "UNSUITABLE" | "CONDITIONAL",
  "rationale": "<한국어 근거 문단 2~3단락, 최소 200자>",
  "key_factors": ["<요인1>", "<요인2>", "<요인3>"],
  "sources": [
    {{"name": "<출처명>", "url": "<URL 또는 '내부 데이터'>"}}
  ],
  "confidence_note": "<판단 근거의 신뢰도 설명>"
}}"""


async def _claude_analyze(
    meta: dict[str, Any],
    db_row: dict[str, Any] | None,
    api_key: str,
    *,
    perplexity_context: str | None = None,
    static_context_text: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> dict[str, Any] | None:
    """Claude API로 수출 적합성 분석. 실패 시 None."""
    try:
        import anthropic
    except ImportError:
        return None

    prompt = _build_analysis_prompt(meta, db_row, perplexity_context, static_context_text)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # ```json 블록 제거
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    continue
        return json.loads(raw)
    except Exception:
        return None


# ── 단일 품목 분석 ─────────────────────────────────────────────────────────────

async def analyze_product(
    product_id: str,
    db_row: dict[str, Any] | None = None,
    *,
    use_perplexity: bool = True,
) -> dict[str, Any]:
    """단일 품목 수출 적합성 분석.

    Returns:
        분석 결과 dict (verdict, rationale, key_factors, sources, analyzed_at 포함)
    """
    meta = _META_BY_PID.get(product_id)
    if meta is None:
        return {
            "product_id": product_id,
            "error": f"알 수 없는 product_id: {product_id}",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

    claude_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY") if use_perplexity else None

    # 정적 데이터 컨텍스트 로드 (HSA CSV + PDF 스니펫)
    static_context_text: str | None = None
    try:
        from utils.static_data import get_product_context, context_to_prompt_text
        ctx = get_product_context(product_id)
        if ctx:
            static_context_text = context_to_prompt_text(ctx)
    except Exception:
        pass

    result: dict[str, Any] | None = None
    analysis_model = "static_fallback"

    # Step 1: Claude 1차 분석 (HSA DB + PDF 컨텍스트 포함)
    if claude_key:
        result = await _claude_analyze(
            meta, db_row, claude_key,
            perplexity_context=None,
            static_context_text=static_context_text,
        )
        if result:
            analysis_model = "claude-sonnet-4-6"

    # Step 2: 조건부 판정 시 Perplexity 보조 검색 후 재분석
    if (
        result is not None
        and perplexity_key
        and result.get("verdict") == "조건부"
        and claude_key
    ):
        query = (
            f"Singapore HSA regulatory status and market price for {meta['trade_name']} "
            f"({meta['inn']}) in Singapore. Include any recent regulatory updates."
        )
        perplexity_context = await _perplexity_search(query, perplexity_key)
        if perplexity_context:
            result2 = await _claude_analyze(
                meta, db_row, claude_key,
                perplexity_context=perplexity_context,
                static_context_text=static_context_text,
            )
            if result2:
                result = result2
                analysis_model = "claude-sonnet-4-6+perplexity"

    # API 미설정 또는 분석 실패 시 — 보고서에 명확히 표시
    if result is None:
        no_api = not bool(claude_key)
        result = {
            "verdict": None,
            "verdict_en": None,
            "rationale": (
                "Claude API 키 미설정 — CLAUDE_API_KEY 또는 ANTHROPIC_API_KEY "
                "환경변수를 설정하면 실제 분석이 실행됩니다."
                if no_api else
                "Claude API 분석 실패 — API 키를 확인하거나 잠시 후 다시 시도하세요."
            ),
            "key_factors": [],
            "sources": [],
            "confidence_note": "API 미설정" if no_api else "분석 실패",
        }

    return {
        "product_id": product_id,
        "trade_name": meta["trade_name"],
        "inn": meta["inn"],
        "market_segment": meta["market_segment"],
        "price_local_sgd": db_row.get("price_local") if db_row else None,
        "verdict": result.get("verdict"),          # None = API 미설정
        "verdict_en": result.get("verdict_en"),
        "rationale": result.get("rationale", ""),
        "key_factors": result.get("key_factors", []),
        "sources": result.get("sources", []),
        "confidence_note": result.get("confidence_note", ""),
        "analysis_model": analysis_model,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── 전체 8품목 배치 분석 ──────────────────────────────────────────────────────

async def analyze_all(
    db_path: Path | None = None,
    *,
    use_perplexity: bool = True,
) -> list[dict[str, Any]]:
    """8품목 전체 수출 적합성 분석 실행.

    Args:
        db_path: SQLite DB 경로 (None이면 datas/local_products.db)
        use_perplexity: Perplexity 보조 검색 활성화 여부

    Returns:
        품목별 분석 결과 리스트
    """
    import asyncio
    from utils import db as dbutil

    path = db_path or (ROOT / "datas" / "local_products.db")
    db_rows: dict[str, dict[str, Any]] = {}

    if path.exists():
        conn = dbutil.get_connection(path)
        rows = dbutil.fetch_all_products(conn)
        conn.close()
        db_rows = {r["product_id"]: r for r in rows}

    tasks = [
        analyze_product(
            meta["product_id"],
            db_rows.get(meta["product_id"]),
            use_perplexity=use_perplexity,
        )
        for meta in PRODUCT_META
    ]
    return list(await asyncio.gather(*tasks))
