"""Perplexity API로 품목별 관련 논문·레퍼런스 검색.

PERPLEXITY_API_KEY 설정 시 자동 실행.
미설정 시 빈 리스트 반환 (UI에서 "API 키 미설정" 표시).

출력 (품목별):
  [
    {"title": "...", "url": "https://...", "reason": "한 줄 근거", "source": "PubMed 등"},
    ...
  ]
"""

from __future__ import annotations

import os
from typing import Any

_QUERIES: dict[str, str] = {
    "SG_hydrine_hydroxyurea_500": (
        "hydroxyurea sickle cell disease chronic myeloid leukemia clinical evidence Singapore"
    ),
    "SG_gadvoa_gadobutrol_604": (
        "gadobutrol MRI contrast agent safety efficacy clinical trial"
    ),
    "SG_sereterol_activair": (
        "fluticasone salmeterol asthma COPD inhaler Singapore Southeast Asia"
    ),
    "SG_omethyl_omega3_2g": (
        "omega-3 ethyl esters hypertriglyceridemia cardiovascular outcomes clinical study"
    ),
    "SG_rosumeg_combigel": (
        "rosuvastatin omega-3 combination dyslipidemia fixed dose clinical trial"
    ),
    "SG_atmeg_combigel": (
        "atorvastatin omega-3 combination lipid lowering therapy evidence"
    ),
    "SG_ciloduo_cilosta_rosuva": (
        "cilostazol rosuvastatin combination peripheral artery disease clinical study"
    ),
    "SG_gastiin_cr_mosapride": (
        "mosapride gastric motility gastroparesis clinical evidence Asia"
    ),
}


async def fetch_references(
    product_id: str,
    max_refs: int = 4,
) -> list[dict[str, str]]:
    """Perplexity sonar-pro로 관련 논문 검색.

    Returns:
        [{"title", "url", "reason", "source"}, ...]
        API 키 없으면 빈 리스트.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return []

    query = _QUERIES.get(product_id)
    if not query:
        return []

    try:
        import httpx
    except ImportError:
        return []

    prompt = f"""Find {max_refs} relevant academic papers or clinical studies for:
"{query}"

Return ONLY valid JSON array, no other text:
[
  {{
    "title": "<paper title>",
    "url": "<direct URL to paper or PubMed>",
    "reason": "<한 줄 근거: 왜 이 논문이 싱가포르 수출 적합성 판단에 관련 있는지>",
    "source": "<PubMed / Lancet / NEJM 등>"
  }}
]"""

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar-pro",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a pharmaceutical research assistant. "
                                "Return only valid JSON arrays with academic paper references."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 600,
                    "return_citations": True,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()

            # JSON 블록 추출
            if "```" in content:
                for part in content.split("```"):
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("["):
                        content = part
                        break

            import json
            refs = json.loads(content)
            # URL 없는 항목 필터, 최대 max_refs개
            return [r for r in refs if r.get("url")][:max_refs]

    except Exception:
        return []


async def fetch_all_references(
    product_ids: list[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """8품목 전체 논문 검색. product_ids 미지정 시 전체."""
    import asyncio

    targets = product_ids or list(_QUERIES.keys())
    tasks = {pid: fetch_references(pid) for pid in targets}
    results = await asyncio.gather(*tasks.values())
    return dict(zip(tasks.keys(), results))
