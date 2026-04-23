"""바이어 심층 조사 — 2차 수집.

CPHI 전시회 상세 페이지 전체 텍스트 → Claude Haiku 파싱.
Perplexity Sonar로 target_country 관련성 실시간 검증 후 Claude 컨텍스트에 주입.
국가 변수(target_country/target_region)로 전체 로직 제어.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Awaitable, Callable

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

_SCHEMA_DESC = {
    "revenue":                   "연 매출 규모 (예: ~$50M, $200M+) — 불명확 시 '-'",
    "employees":                 "임직원 수 (예: 500+, 1200) — 불명확 시 '-'",
    "founded":                   "설립연도 (예: 1990) — 불명확 시 '-'",
    "territories":               "주요 영업 국가/지역 배열 (예: [\"Singapore\",\"Malaysia\"])",
    "has_target_country_presence": "target_country 시장 진출/영업 여부 (true/false/null)",
    "has_gmp":                   "GMP 인증 보유 여부 (true/false/null)",
    "import_history":            "수입 이력 여부 (true/false/null)",
    "procurement_history":       "공공조달 낙찰 이력 여부 (true/false/null)",
    "has_pharmacy_chain":        "약국 체인 보유 여부 (true/false/null)",
    "public_channel":            "공공 채널(병원/조달) 취급 여부 (true/false/null)",
    "private_channel":           "민간 채널(약국/도매) 취급 여부 (true/false/null)",
    "mah_capable":               "MAH(위생등록) 대행 가능 여부 (true/false/null)",
    "korea_experience":          "한국 기업 거래 경험 (예: '없음', '있음(미확인)') — 불명확 시 '-'",
    "certifications":            "보유 인증 목록 (예: [\"USFDA\",\"EU GMP\",\"KFDA\"])",
    "source_urls":               "참조 출처 URL 배열",
    "company_overview_kr":       "CPHI 페이지 기반 기업 개요 (한국어 2~3문장)",
    "recommendation_reason":     "파트너 후보 추천 이유 (한국어 3~5문장, 제품 연관성+강점+근거)",
}

_NULL_ENRICH: dict[str, Any] = {
    "revenue": "-",
    "employees": "-",
    "founded": "-",
    "territories": [],
    "has_target_country_presence": None,
    "has_gmp": None,
    "import_history": None,
    "procurement_history": None,
    "has_pharmacy_chain": None,
    "public_channel": None,
    "private_channel": None,
    "mah_capable": None,
    "korea_experience": "-",
    "certifications": [],
    "source_urls": [],
    "company_overview_kr": "-",
    "recommendation_reason": "-",
}


async def _claude_extract(
    company_name: str,
    country: str,
    full_page_text: str,
    product_label: str,
    target_country: str = "Singapore",
    target_region: str = "Asia",
    perplexity_text: str = "",
) -> dict[str, Any]:
    """CPHI 페이지 텍스트 + Perplexity 검증 결과를 Claude Haiku로 파싱하여 구조화."""
    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return dict(_NULL_ENRICH)

    schema_str = json.dumps(_SCHEMA_DESC, ensure_ascii=False, indent=2)

    if full_page_text:
        cphi_context = f"[CPHI 전시회 등록 페이지 전체 텍스트]\n{full_page_text}"
    else:
        cphi_context = f"회사명: {company_name}, 국가: {country} (CPHI 페이지 텍스트 없음)"

    pplx_context = ""
    if perplexity_text:
        pplx_context = (
            f"\n\n[Perplexity 실시간 웹 검색 결과 — {target_country} 관련성]\n"
            f"{perplexity_text}\n"
            f"※ 위 웹 검색 결과를 최우선 근거로 삼아 has_target_country_presence 및 "
            f"recommendation_reason을 작성하세요."
        )

    prompt = f"""아래 정보를 종합하여 제약 기업 정보를 추출하고 JSON으로 반환하세요.

분석 대상: {company_name} ({country})
탐색 목적 제품: {product_label}
타깃 시장: {target_country} / {target_region}

추출 항목 (키: 설명):
{schema_str}

{cphi_context}{pplx_context}

작성 규칙:
- CPHI 텍스트와 Perplexity 웹 검색 결과를 모두 참조하여 작성.
- territories: 언급된 영업 국가/지역 배열
- certifications: USFDA / EU GMP / KFDA / EDQM 등 언급된 인증 배열
- has_target_country_presence: Perplexity 결과에 {target_country} 진출 증거가 있으면 true,
  명시적으로 없다면 false, 불명확하면 null
- has_gmp: GMP 관련 인증 텍스트 있으면 true
- company_overview_kr: 기업 소개 한국어 2~3문장 요약
- recommendation_reason:
    첫 문장: "{product_label}"과의 성분/치료군 연관성
    이후: {target_country} 시장 진출 여부(Perplexity 근거 포함)·인증·규모·강점을
    근거로 3~5문장 한국어 작성
    문체 규칙:
      · ** 등 마크다운 기호 사용 금지 (일반 문장으로만 작성)
      · 문장 끝을 "어렵습니다", "불확실합니다", "없습니다" 등 부정·단정형으로 끝내지 말 것
      · 대신 "가능성이 있습니다", "검토할 만합니다", "기대할 수 있습니다" 등 개방형·긍정형 표현 사용
- JSON만 반환 (```json 마크다운 없이)
"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            parsed = json.loads(m.group(0))
            for k, v in _NULL_ENRICH.items():
                if k not in parsed or parsed[k] == "":
                    parsed[k] = v
            if isinstance(parsed.get("recommendation_reason"), str):
                parsed["recommendation_reason"] = re.sub(r"\*+", "", parsed["recommendation_reason"])
            if isinstance(parsed.get("company_overview_kr"), str):
                parsed["company_overview_kr"] = re.sub(r"\*+", "", parsed["company_overview_kr"])
            return parsed
    except Exception as ex:
        import logging
        logging.getLogger(__name__).warning("Claude extract failed for %s: %s", company_name, ex)
    return dict(_NULL_ENRICH)


async def enrich_company(
    company: dict[str, Any],
    product_label: str = "",
    target_country: str = "Singapore",
    target_region: str = "Asia",
    emit: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """단일 기업 심층조사 — CPHI 텍스트 + Perplexity 검증 → Claude Haiku."""
    name    = company.get("company_name", "-")
    country = company.get("country", "-")
    website = company.get("website", "-")

    # overview_text 우선 (full_page_text는 JS 트래킹 코드 혼재로 Claude 파싱 불량)
    full_page_text = company.get("overview_text", "") or company.get("full_page_text", "")
    if not full_page_text:
        parts: list[str] = []
        if company.get("address") and company["address"] != "-":
            parts.append(f"주소: {company['address']}")
        if company.get("email") and company["email"] != "-":
            parts.append(f"이메일: {company['email']}")
        if company.get("category") and company["category"] != "-":
            parts.append(f"카테고리: {company['category']}")
        if company.get("products_cphi"):
            parts.append(f"제품 목록: {', '.join(company['products_cphi'][:15])}")
        if country and country != "-":
            parts.append(f"국가: {country}")
        full_page_text = "\n".join(parts)

    # ── Perplexity 실시간 검증 ───────────────────────────────────────────────
    perplexity_text = ""
    perplexity_citations: list[str] = []
    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()

    # CF-prefixed ID는 실제 기업명이 아니므로 검색 스킵
    is_real_name = bool(name) and name != "-" and not re.match(r"^CF\w+$", name)

    if px_key and is_real_name:
        try:
            from utils.perplexity_searcher import verify_company as pplx_verify
            products_hint = ", ".join(company.get("products_cphi", [])[:5])
            if emit:
                await emit(f"    ↳ Perplexity 검증: {name}")
            pplx = await pplx_verify(
                name, products_hint, target_country, target_region
            )
            perplexity_text     = pplx.get("text", "")
            perplexity_citations = pplx.get("citations", [])
        except Exception as e:
            if emit:
                await emit(f"    ↳ Perplexity 오류: {e}")

    enriched = await _claude_extract(
        name, country, full_page_text,
        product_label, target_country, target_region,
        perplexity_text=perplexity_text,
    )

    # 웹사이트 + Perplexity 인용 출처를 source_urls에 추가
    existing_urls: list[str] = enriched.get("source_urls", [])
    for url in perplexity_citations:
        if url and url not in existing_urls:
            existing_urls.append(url)
    if website and website != "-" and website not in existing_urls:
        existing_urls.insert(0, website)
    enriched["source_urls"] = existing_urls

    for k, v in _NULL_ENRICH.items():
        if k not in enriched:
            enriched[k] = v

    return {**company, "enriched": enriched}


async def discover_companies_via_perplexity(
    ingredient: str,
    therapeutic: str,
    target_country: str = "Singapore",
    target_region: str = "Asia",
    emit: Callable[[str], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    """CPHI 결과 없을 때 fallback — Perplexity 검색 텍스트를 Claude Haiku로 파싱해 stub 기업 목록 반환."""
    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not px_key:
        return []

    try:
        from utils.perplexity_searcher import search_by_product
        if emit:
            await emit(f"  Perplexity 직접 탐색: {ingredient} / {therapeutic} in {target_country}")
        results = await search_by_product(ingredient, therapeutic, target_country, target_region, emit=emit)
    except Exception as e:
        if emit:
            await emit(f"  Perplexity 탐색 오류: {e}")
        return []

    combined_text = "\n\n".join(r.get("text", "") for r in results if r.get("text"))
    all_citations: list[str] = []
    for r in results:
        all_citations.extend(r.get("citations", []))

    if not combined_text:
        return []

    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    prompt = f"""아래 텍스트에서 {target_country}에서 {ingredient} / {therapeutic} 관련 제약 유통·수입 기업 목록을 추출하세요.

[Perplexity 검색 결과]
{combined_text}

출력 형식 (JSON 배열, ```json 없이):
[
  {{
    "company_name": "기업명",
    "country": "{target_country}",
    "website": "URL 또는 빈 문자열",
    "overview_text": "기업 설명 1-2문장 (영어)"
  }}
]

규칙:
- {target_country} 또는 {target_region}에 실제로 언급된 기업만 포함
- 최대 10개, 중복 제거, 불명확한 기업 제외
- JSON 배열만 반환"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            return []
        parsed: list[dict] = json.loads(m.group(0))

        stubs = []
        for item in parsed:
            name = item.get("company_name", "").strip()
            if not name:
                continue
            overview = item.get("overview_text", "")
            stubs.append({
                "company_name": name,
                "country": item.get("country", target_country),
                "website": item.get("website", "-") or "-",
                "email": "-", "phone": "-", "fax": "-", "address": "-", "booth": "-",
                "category": therapeutic,
                "products_cphi": [ingredient],
                "overview_text": overview,
                "full_page_text": overview,
                "matched_ingredients": [ingredient],
                "ingredient_match": True,
                "source_region": "perplexity_fallback",
                "perplexity_text": combined_text,
                "perplexity_citations": all_citations,
            })

        if emit:
            await emit(f"  Perplexity fallback 파싱 완료 — {len(stubs)}개 기업 추출")
        return stubs
    except Exception as e:
        if emit:
            await emit(f"  Perplexity fallback 파싱 오류: {e}")
        return []


async def _enrich_partial(
    company: dict[str, Any],
    product_label: str,
    target_country: str,
) -> dict[str, Any]:
    """엑셀 pre-filled 기업의 null 필드(mah_capable·korea_experience·company_overview_kr)만 보완."""
    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return company

    existing: dict[str, Any] = dict(company.get("enriched", {}))
    name         = company.get("company_name", "-")
    company_type = existing.get("_company_type", company.get("company_type", "-"))
    revenue      = existing.get("revenue", "-")
    pipeline     = existing.get("_pipeline_text", "")
    note         = existing.get("recommendation_reason", "-")

    context = (
        f"회사명: {name}\n"
        f"국가: {target_country}\n"
        f"회사유형: {company_type}\n"
        f"매출규모: {revenue}\n"
        f"파이프라인/취급이력: {pipeline}\n"
        f"비고: {note}\n"
        f"탐색 대상 제품: {product_label}"
    )

    prompt = f"""아래 정보를 바탕으로 JSON만 반환하세요 (```json 없이).

{context}

추출 항목:
- mah_capable: MAH(위생등록) 대행 가능 여부 (true/false/null — 불명확 시 null)
- korea_experience: 한국 기업 거래 경험 (예: "없음", "있음(미확인)") — 불명확 시 "-"
- company_overview_kr: 기업 소개 한국어 2~3문장 (** 등 마크다운 기호 사용 금지)
- recommendation_reason: 파트너 후보 추천 이유 한국어 3~5문장 (비고 내용을 정제·보강, 마크다운 기호 금지, 개방형·긍정형 어조)
"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            patch = json.loads(m.group(0))
            for field in ("mah_capable", "korea_experience", "company_overview_kr", "recommendation_reason"):
                if field in patch and patch[field] not in ("", None) if field != "mah_capable" else field in patch:
                    existing[field] = patch[field]
            for k in ("company_overview_kr", "recommendation_reason"):
                if isinstance(existing.get(k), str):
                    existing[k] = re.sub(r"\*+", "", existing[k])
    except Exception:
        pass

    # 내부 전용 키 제거
    existing.pop("_pipeline_text", None)
    existing.pop("_company_type", None)

    return {**company, "enriched": existing}


async def enrich_all(
    companies: list[dict[str, Any]],
    product_label: str = "",
    target_country: str = "Singapore",
    target_region: str = "Asia",
    emit: Callable[[str], Awaitable[None]] | None = None,
    excel_prefilled: bool = False,
) -> list[dict[str, Any]]:
    """전체 기업 심층조사 (순차 — API 부하 조절).

    excel_prefilled=True: 엑셀에서 pre-fill된 기업 — null 필드만 Claude로 보완.
    """
    results: list[dict[str, Any]] = []
    total = len(companies)

    if not excel_prefilled:
        px_available = bool(os.environ.get("PERPLEXITY_API_KEY", "").strip())
        model_info = "Claude Haiku + Perplexity" if px_available else "Claude Haiku"
        if emit:
            await emit(f"심층조사 시작 / 모델: {model_info} / 타깃: {target_country} ({target_region})")

    for i, company in enumerate(companies, 1):
        name = company.get("company_name", company.get("exid", f"#{i}"))
        if emit:
            await emit(f"  [{i}/{total}] {name} 분석 중…")
        try:
            if excel_prefilled:
                enriched = await _enrich_partial(company, product_label, target_country)
            else:
                enriched = await enrich_company(
                    company, product_label, target_country, target_region, emit
                )
        except Exception as e:
            if emit:
                await emit(f"  [{i}/{total}] {name} 오류: {e} → 폴백")
            enriched = {**company, "enriched": dict(_NULL_ENRICH)}
        results.append(enriched)
        await asyncio.sleep(0.8)

    return results
