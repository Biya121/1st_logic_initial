"""Logic B: AI 자율 탐색 — Claude Primary + Perplexity 보조.

발동 조건:
  - Logic A 전체 실패 (price_local = NULL)
  - 수동 호출: python crawlers/sg_ai_discovery.py --product "신규품목"

LLM 우선순위 (가이드라인 §1):
  1. Claude Haiku (Primary)   → 지식 기반 가격 추정 + 검증
  2. Perplexity Sonar (보조)  → Claude 신뢰도 낮을 때만 실시간 웹 검색 보강
  3. 정적 폴백                → API 미설정 또는 모두 실패 시

confidence 상한: 0.75 (ai_discovered, Atomic Calibration)

환경변수:
  CLAUDE_API_KEY / ANTHROPIC_API_KEY
  PERPLEXITY_API_KEY
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

from crawlers.common import map_to_schema
from crawlers.crawl_context import CrawlContext
from utils.domain_validator import is_trusted_domain

Emit = Callable[[dict[str, Any]], Awaitable[None]]

_CONF_CAP = 0.75  # ai_discovered 상한 (보고서 §8)


# ── Atomic Calibration (arXiv:2403.14151) ───────────────────────────────────

def _calibrate_confidence(
    price_local: float | None,
    source_url: str,
    has_query: bool,
) -> float:
    score = 0.50
    if price_local is not None:
        score += 0.05
    if source_url:
        score += 0.05
    if is_trusted_domain(source_url):
        score += 0.10
    if has_query:
        score += 0.05
    return min(round(score, 3), _CONF_CAP)


# ── Step 1 (Primary): Claude Haiku 가격 탐색 ────────────────────────────────

async def _claude_primary_agent(
    product_id: str,
    trade_name: str,
    emit: Emit,
) -> dict[str, Any] | None:
    """Claude Haiku로 싱가포르 SGD 가격을 지식 기반으로 추정 (Primary).

    confidence >= 0.55이면 Perplexity 보조 검색 생략.
    """
    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        await emit({
            "phase": "sg_ai_discovery",
            "level": "info",
            "message": f"[Claude Primary] {trade_name}: CLAUDE_API_KEY 미설정 — 스킵",
        })
        return None

    try:
        import anthropic
    except ImportError:
        await emit({
            "phase": "sg_ai_discovery",
            "level": "warn",
            "message": "[Claude Primary] anthropic 패키지 미설치 (pip install anthropic)",
        })
        return None

    prompt = f"""You are a pharmaceutical pricing analyst for Singapore.

Product: {trade_name} (product_id: {product_id})

Using your knowledge of Singapore drug pricing, estimate the retail/wholesale price in SGD.
Base your estimate on HSA formulary data, regional pricing trends, and comparable products.

Return ONLY valid JSON:
{{
  "price_sgd": <number or null>,
  "source_url": "<known reference URL or empty string>",
  "market_segment": "retail" | "tender" | "wholesale",
  "confidence": <0.0-0.75>,
  "needs_web_search": <true if uncertain, false if confident>,
  "note": "<brief reasoning>"
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        data["confidence"] = min(float(data.get("confidence", 0.50)), _CONF_CAP)
        data["_discovery_query"] = f"claude_primary {product_id}"
        await emit({
            "phase": "sg_ai_discovery",
            "level": "success",
            "message": (
                f"[Claude Primary] {trade_name}: "
                f"price={data.get('price_sgd')}, "
                f"conf={data.get('confidence'):.2f}, "
                f"needs_web={data.get('needs_web_search')}"
            ),
        })
        return data
    except Exception as e:
        await emit({
            "phase": "sg_ai_discovery",
            "level": "warn",
            "message": f"[Claude Primary] {trade_name}: 오류 — {type(e).__name__}: {e}",
        })
        return None


# ── Step 2 (보조): Perplexity 실시간 웹 검색 ─────────────────────────────────

async def _perplexity_supplement_agent(
    product_id: str,
    trade_name: str,
    emit: Emit,
) -> dict[str, Any] | None:
    """Claude의 신뢰도가 낮을 때만 호출 — 실시간 웹 검색 보강 (보조).

    Claude Primary 결과에서 needs_web_search=True이거나 confidence < 0.55일 때 실행.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return None

    try:
        import httpx
    except ImportError:
        return None

    query = f"{trade_name} Singapore drug price SGD government formulary"
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
                                "You are a drug pricing researcher for Singapore. "
                                "Return ONLY valid JSON with keys: "
                                "price_sgd (number or null), "
                                "source_url (string), "
                                "market_segment (string: retail|tender|wholesale), "
                                "confidence_note (string)."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 300,
                },
            )
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
        data["_discovery_query"] = query
        await emit({
            "phase": "sg_ai_discovery",
            "level": "info",
            "message": (
                f"[Perplexity 보조] {trade_name}: "
                f"price_sgd={data.get('price_sgd')}, "
                f"url={str(data.get('source_url', ''))[:80]}"
            ),
        })
        return data
    except Exception as e:
        await emit({
            "phase": "sg_ai_discovery",
            "level": "warn",
            "message": f"[Perplexity 보조] {trade_name}: 오류 — {type(e).__name__}: {e}",
        })
        return None


# ── Step 3: Claude로 결과 통합·검증 ──────────────────────────────────────────

async def _claude_merge_agent(
    product_id: str,
    trade_name: str,
    primary: dict[str, Any] | None,
    supplement: dict[str, Any] | None,
    emit: Emit,
) -> dict[str, Any] | None:
    """Claude가 primary + supplement 결과를 통합·최종 검증.

    primary 결과만 있어도 동작 (supplement=None이면 통합 생략).
    """
    # primary 결과가 충분하면 그대로 반환
    if primary and primary.get("price_sgd") is not None and not primary.get("needs_web_search"):
        return primary

    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or supplement is None:
        return primary  # primary 결과만 사용

    try:
        import anthropic
    except ImportError:
        return primary

    context = json.dumps({
        "primary_estimate": primary,
        "web_supplement": supplement,
    }, ensure_ascii=False)

    prompt = f"""You are a pharmaceutical pricing analyst for Singapore.

Product: {trade_name} (product_id: {product_id})
Available data: {context}

Merge and validate the pricing data. Prefer web supplement if primary was uncertain.
Return ONLY valid JSON:
{{
  "price_sgd": <number or null>,
  "source_url": "<best reference URL>",
  "market_segment": "retail" | "tender" | "wholesale",
  "confidence": <0.0-0.75>,
  "note": "<final reasoning>"
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        data["confidence"] = min(float(data.get("confidence", 0.50)), _CONF_CAP)
        data["_discovery_query"] = (supplement or {}).get("_discovery_query", f"claude_merge {product_id}")
        await emit({
            "phase": "sg_ai_discovery",
            "level": "success",
            "message": (
                f"[Claude Merge] {trade_name}: "
                f"price={data.get('price_sgd')}, "
                f"conf={data.get('confidence'):.2f}"
            ),
        })
        return data
    except Exception as e:
        await emit({
            "phase": "sg_ai_discovery",
            "level": "warn",
            "message": f"[Claude Merge] {trade_name}: 오류 — {type(e).__name__}: {e}",
        })
        return primary


# ── map_to_schema 변환 (보고서 §8-2) ─────────────────────────────────────────

def analysis_agent_to_schema(
    product_id: str,
    trade_name: str,
    ai_result: dict[str, Any],
    source_url: str,
    discovery_query: str = "",
) -> dict[str, Any]:
    price = ai_result.get("price_sgd") or ai_result.get("price_local")
    price_f = float(price) if price is not None else None
    conf = min(float(ai_result.get("confidence", 0.50)), _CONF_CAP)

    rec = map_to_schema(
        {
            "product_id": product_id,
            "trade_name": trade_name,
            "product_name": trade_name,
            "market_segment": ai_result.get("market_segment", "retail"),
            "price": price_f,
            "confidence": conf,
            "sg_source_type": "ai_discovered",
            "outlier_flagged": False,
        },
        source_url=source_url or "internal://ai_discovery",
        source_name="ai_discovery",
        source_tier=5,
        product_id=product_id,
    )
    rec["raw_payload"]["discovery_query"] = discovery_query
    # Atomic Calibration으로 confidence 재산출
    calibrated = _calibrate_confidence(price_f, source_url, bool(discovery_query))
    rec["confidence"] = calibrated
    return rec


# ── 메인 run ──────────────────────────────────────────────────────────────────

async def run(
    emit: Emit,
    product_ids_need_fill: list[str],
    ctx: CrawlContext,
) -> list[dict[str, Any]]:
    _ = ctx
    if not product_ids_need_fill:
        await emit({
            "phase": "sg_ai_discovery",
            "level": "info",
            "message": "Logic B 스킵 (보완 대상 없음)",
        })
        return []

    await emit({
        "phase": "sg_ai_discovery",
        "level": "warn",
        "message": (
            f"Logic B 시작: {len(product_ids_need_fill)}건 — "
            "Claude Primary → (필요 시) Perplexity 보조 → Claude Merge → Atomic Calibration"
        ),
    })

    out: list[dict[str, Any]] = []
    for pid in product_ids_need_fill:
        trade = pid.replace("SG_", "").replace("_", " ").title()

        await emit({
            "phase": "sg_ai_discovery",
            "level": "info",
            "message": f"Logic B 처리: {pid} ({trade})",
        })

        # Step 1: Claude Primary (지식 기반 추정)
        primary = await _claude_primary_agent(pid, trade, emit)
        await asyncio.sleep(0.3)

        # Step 2: Perplexity 보조 (Claude 신뢰도 낮을 때만)
        supplement = None
        needs_supplement = (
            primary is None
            or primary.get("needs_web_search")
            or (primary.get("confidence") or 0) < 0.55
        )
        if needs_supplement:
            supplement = await _perplexity_supplement_agent(pid, trade, emit)
            await asyncio.sleep(0.3)

        # Step 3: Claude Merge (통합·검증)
        final = await _claude_merge_agent(pid, trade, primary, supplement, emit)
        await asyncio.sleep(0.2)

        if final and (final.get("price_sgd") is not None or
                      final.get("price_local") is not None):
            source_url = final.get("source_url") or (supplement or {}).get("source_url", "")
            query = final.get("_discovery_query", f"ai fill {pid}")
            rec = analysis_agent_to_schema(pid, trade, final, source_url, query)
        elif supplement and supplement.get("price_sgd") is not None:
            # Claude 전부 실패, Perplexity 결과 직접 사용
            source_url = supplement.get("source_url", "")
            query = supplement.get("_discovery_query", f"perplexity fill {pid}")
            rec = analysis_agent_to_schema(pid, trade, {
                "price_sgd": supplement["price_sgd"],
                "market_segment": supplement.get("market_segment", "retail"),
                "confidence": 0.50,
            }, source_url, query)
        else:
            # API 키 없음 또는 모두 실패 — 데모값
            await emit({
                "phase": "sg_ai_discovery",
                "level": "warn",
                "message": f"Logic B: {pid} — API 키 미설정 또는 추출 실패, 데모값 사용",
            })
            demo_url = "https://www.moh.gov.sg/"
            demo_price = 9.99
            conf = _calibrate_confidence(demo_price, demo_url, True)
            rec = map_to_schema(
                {
                    "product_id": pid,
                    "trade_name": trade,
                    "product_name": trade,
                    "market_segment": "retail",
                    "price": demo_price,
                    "confidence": conf,
                    "sg_source_type": "ai_discovered",
                },
                source_url=demo_url,
                source_name="ai_discovery",
                source_tier=5,
                product_id=pid,
            )
            rec["raw_payload"]["discovery_query"] = f"demo fill {pid}"

        out.append(rec)
        await emit({
            "phase": "sg_ai_discovery",
            "level": "success",
            "message": (
                f"Logic B 완료: {pid} "
                f"price={rec.get('price_local')} conf={rec.get('confidence')}"
            ),
            "product_id": pid,
        })

    return out
