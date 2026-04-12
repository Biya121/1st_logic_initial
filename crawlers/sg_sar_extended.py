"""타입 C: SAR 3레이어 — Ciloduo · Gastiin CR.

레이어 1: Claude Haiku → HSA SAR 가능 여부 판단
레이어 2: Perplexity Sonar → 인근국 공공 DB + domain_validator
레이어 3: 정책 기본값 (레이어 1·2 모두 실패 시 static_fallback)

환경변수:
  CLAUDE_API_KEY   — Anthropic API key
  PERPLEXITY_API_KEY — Perplexity API key
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

from crawlers.common import map_to_schema
from crawlers.crawl_context import CrawlContext
from crawlers.site_dashboard import emit_site
from utils.domain_validator import is_trusted_domain

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_SAR_TARGETS = (
    {
        "trade_name": "Ciloduo",
        "product_id": "SG_ciloduo_cilosta_rosuva",
        "scientific_name": "cilostazol/rosuvastatin",
        "atc_code": "B01AC23",
        "reference_countries": ["Malaysia", "Thailand", "Indonesia"],
        "market_segment": "wholesale",
    },
    {
        "trade_name": "Gastiin CR",
        "product_id": "SG_gastiin_cr_mosapride",
        "scientific_name": "mosapride",
        "atc_code": "A03FA05",
        "reference_countries": ["Philippines", "India", "Vietnam"],
        "market_segment": "wholesale",
    },
)

# confidence 정책 (보고서 §10)
_CONF_SAR_REF = 0.36
_CONF_REGIONAL = 0.35
_CONF_FALLBACK = 0.30

_FALLBACK_PRICES = {
    "SG_ciloduo_cilosta_rosuva": 19.5,
    "SG_gastiin_cr_mosapride": 12.1,
}
_FALLBACK_REF_COUNTRY = {
    "SG_ciloduo_cilosta_rosuva": "Malaysia",
    "SG_gastiin_cr_mosapride": "Philippines",
}


# ── 레이어 1: Claude Haiku SAR 판단 ─────────────────────────────────────────

async def _layer1_haiku(target: dict[str, Any], emit: Emit) -> dict[str, Any] | None:
    """Haiku에게 SAR 가능성과 싱가포르 추정 SGD 가격을 물음."""
    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        await emit({
            "phase": "sg_sar_extended",
            "level": "info",
            "message": f"[L1] {target['trade_name']}: CLAUDE_API_KEY 미설정 — 레이어1 생략",
        })
        return None

    try:
        import anthropic
    except ImportError:
        await emit({
            "phase": "sg_sar_extended",
            "level": "warn",
            "message": "[L1] anthropic 패키지 미설치 (pip install anthropic)",
        })
        return None

    prompt = f"""You are a pharmaceutical market analyst.
Product: {target['trade_name']}
Active ingredient(s): {target['scientific_name']}
ATC code: {target.get('atc_code', 'unknown')}
Reference countries for price lookup: {', '.join(target['reference_countries'])}

Task: Estimate the Singapore SGD retail/wholesale price for this drug based on regional data.
Return ONLY valid JSON, no other text:
{{
  "sar_feasibility": "confirmed" | "possible" | "unlikely",
  "price_sgd_estimate": <number or null>,
  "reference_country": "<country name>",
  "confidence_note": "<brief reason>"
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # JSON 블록 추출
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        await emit({
            "phase": "sg_sar_extended",
            "level": "success",
            "message": (
                f"[L1 Haiku] {target['trade_name']}: "
                f"feasibility={data.get('sar_feasibility')}, "
                f"price_sgd={data.get('price_sgd_estimate')}, "
                f"ref={data.get('reference_country')}"
            ),
        })
        return data
    except Exception as e:
        await emit({
            "phase": "sg_sar_extended",
            "level": "warn",
            "message": f"[L1] {target['trade_name']}: Haiku 오류 — {type(e).__name__}: {e}",
        })
        return None


# ── 레이어 2: Perplexity Sonar 인근국 공공 DB ────────────────────────────────

async def _layer2_perplexity(target: dict[str, Any], emit: Emit) -> dict[str, Any] | None:
    """Perplexity Sonar로 인근국 공공 DB에서 가격 탐색."""
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        await emit({
            "phase": "sg_sar_extended",
            "level": "info",
            "message": f"[L2] {target['trade_name']}: PERPLEXITY_API_KEY 미설정 — 레이어2 생략",
        })
        return None

    try:
        import httpx
    except ImportError:
        return None

    countries_str = ", ".join(target["reference_countries"])
    query = (
        f"{target['trade_name']} ({target['scientific_name']}) "
        f"drug price {countries_str} government formulary SGD"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a pharmaceutical pricing researcher. "
                                "Return ONLY valid JSON with keys: "
                                "price_sgd_estimate (number or null), "
                                "reference_country (string), "
                                "source_url (string), "
                                "confidence_note (string)."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 300,
                },
            )
        if resp.status_code != 200:
            await emit({
                "phase": "sg_sar_extended",
                "level": "warn",
                "message": f"[L2] Perplexity HTTP {resp.status_code}",
            })
            return None

        content = resp.json()["choices"][0]["message"]["content"].strip()
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)

        # domain_validator로 신뢰 소스 확인
        src_url = data.get("source_url", "")
        trusted = is_trusted_domain(src_url)
        data["trusted_source"] = trusted

        await emit({
            "phase": "sg_sar_extended",
            "level": "success" if trusted else "info",
            "message": (
                f"[L2 Perplexity] {target['trade_name']}: "
                f"price={data.get('price_sgd_estimate')}, "
                f"ref={data.get('reference_country')}, "
                f"trusted={trusted}"
            ),
        })
        return data
    except Exception as e:
        await emit({
            "phase": "sg_sar_extended",
            "level": "warn",
            "message": f"[L2] {target['trade_name']}: Perplexity 오류 — {type(e).__name__}: {e}",
        })
        return None


# ── 레이어 3: 정책 기본값 ─────────────────────────────────────────────────────

def _layer3_fallback(target: dict[str, Any]) -> dict[str, Any]:
    pid = target["product_id"]
    return {
        "price_sgd_estimate": _FALLBACK_PRICES.get(pid),
        "reference_country": _FALLBACK_REF_COUNTRY.get(pid, "Malaysia"),
        "sar_feasibility": "possible",
        "source": "static_fallback",
    }


# ── 메인 run ──────────────────────────────────────────────────────────────────

async def run(emit: Emit, ctx: CrawlContext) -> list[dict[str, Any]]:
    _ = ctx
    await emit_site(
        emit, "sar", "running",
        "⑧ SAR: 미등재 품목 — 해외·국제 참고가 단계(L1 Haiku → L2 Perplexity → L3 정책 기본값)…",
    )
    await emit({"phase": "sg_sar_extended", "level": "info", "message": "SAR 3레이어 시작"})

    out: list[dict[str, Any]] = []

    for target in _SAR_TARGETS:
        pid = target["product_id"]
        trade = target["trade_name"]
        await emit({
            "phase": "sg_sar_extended",
            "level": "info",
            "message": f"SAR 처리 시작: {trade} ({pid})",
        })

        # 레이어 1
        l1 = await _layer1_haiku(target, emit)
        await asyncio.sleep(0.3)

        # 레이어 2 (L1 실패 또는 price 없을 때)
        l2 = None
        if l1 is None or l1.get("price_sgd_estimate") is None:
            l2 = await _layer2_perplexity(target, emit)
            await asyncio.sleep(0.3)

        # 결과 합성
        if l1 and l1.get("price_sgd_estimate") is not None:
            price = float(l1["price_sgd_estimate"])
            ref_country = l1.get("reference_country", target["reference_countries"][0])
            feasibility = l1.get("sar_feasibility", "possible")
            confidence = _CONF_SAR_REF
            source_type = "sar_reference"
            source_name = "sar_haiku_l1"
            source_url = f"internal://sar_haiku/{pid}"
        elif l2 and l2.get("price_sgd_estimate") is not None:
            price = float(l2["price_sgd_estimate"])
            ref_country = l2.get("reference_country", target["reference_countries"][0])
            feasibility = "possible"
            trusted = l2.get("trusted_source", False)
            confidence = _CONF_REGIONAL if trusted else _CONF_SAR_REF
            source_type = "sar_reference"
            source_name = "sar_perplexity_l2"
            source_url = l2.get("source_url", f"internal://sar_perplexity/{pid}")
        else:
            # 레이어 3: 정책 기본값
            fb = _layer3_fallback(target)
            price = fb["price_sgd_estimate"]
            ref_country = fb["reference_country"]
            feasibility = fb["sar_feasibility"]
            confidence = _CONF_FALLBACK
            source_type = "static_fallback"
            source_name = "sar_extended"
            source_url = f"internal://sar_reference/{ref_country}"
            await emit({
                "phase": "sg_sar_extended",
                "level": "warn",
                "message": f"[L3] {trade}: 레이어 1·2 모두 실패 — 정책 기본값 사용",
            })

        item = {
            "product_id": pid,
            "trade_name": trade,
            "product_name": trade,
            "scientific_name": target["scientific_name"],
            "atc_code": target.get("atc_code"),
            "market_segment": target["market_segment"],
            "price": price,
            "confidence": confidence,
            "sg_source_type": source_type,
            "sar_feasibility": feasibility,
            "reference_country": ref_country,
        }
        rec = map_to_schema(
            item,
            source_url=source_url,
            source_name=source_name,
            source_tier=4,
            product_id=pid,
        )
        out.append(rec)
        await emit({
            "phase": "sg_sar_extended",
            "level": "success",
            "message": (
                f"SAR 결과: {pid} price_local={price} SGD "
                f"feasibility={feasibility} ref={ref_country} conf={confidence}"
            ),
            "product_id": pid,
        })

    await emit_site(
        emit, "sar", "ok",
        f"⑧ SAR: {len(out)}개 품목 참고가 반영 완료.",
    )
    return out
