"""분석 대시보드 서버: SSE 실시간 로그 + 분석/보고서 API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend.dashboard_sites import DASHBOARD_SITES

STATIC = Path(__file__).resolve().parent / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_state: dict[str, Any] = {
    "events": [],
    "lock": None,
}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _state["lock"] = asyncio.Lock()
    yield


app = FastAPI(title="SG Analysis Dashboard", version="2.0.0", lifespan=_lifespan)

import os as _os
_cors_origins = _os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _emit(event: dict[str, Any]) -> None:
    payload = {**event, "ts": time.time()}
    lock = _state["lock"]
    if lock is None:
        return
    async with lock:
        _state["events"].append(payload)
        if len(_state["events"]) > 500:
            _state["events"] = _state["events"][-400:]


# ── 분석 ──────────────────────────────────────────────────────────────────────

_analysis_cache: dict[str, Any] = {"result": None, "running": False}


class AnalyzeBody(BaseModel):
    use_perplexity: bool = True
    force_refresh: bool = False


@app.post("/api/analyze")
async def trigger_analyze(body: AnalyzeBody | None = None) -> JSONResponse:
    """8품목 수출 적합성 분석 실행 (Claude API + Perplexity 보조)."""
    req = body if body is not None else AnalyzeBody()
    if _analysis_cache["running"]:
        raise HTTPException(status_code=409, detail="분석이 이미 실행 중입니다.")
    if _analysis_cache["result"] and not req.force_refresh:
        return JSONResponse({"ok": True, "message": "캐시된 분석 결과 사용. force_refresh=true로 재실행."})

    async def _run() -> None:
        _analysis_cache["running"] = True
        try:
            from analysis.sg_export_analyzer import analyze_all
            from analysis.perplexity_references import fetch_all_references

            results = await analyze_all(use_perplexity=req.use_perplexity)
            pids = [r["product_id"] for r in results]
            refs = await fetch_all_references(pids)
            for r in results:
                r["references"] = refs.get(r["product_id"], [])
            _analysis_cache["result"] = results
        finally:
            _analysis_cache["running"] = False

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "message": "분석을 백그라운드에서 시작했습니다."})


@app.get("/api/analyze/result")
async def analyze_result() -> JSONResponse:
    if _analysis_cache["running"]:
        return JSONResponse({"status": "running"}, status_code=202)
    if not _analysis_cache["result"]:
        raise HTTPException(status_code=404, detail="분석 결과 없음. POST /api/analyze 먼저 실행")
    return JSONResponse({
        "status": "done",
        "count": len(_analysis_cache["result"]),
        "results": _analysis_cache["result"],
    })


@app.get("/api/analyze/status")
async def analyze_status() -> dict[str, Any]:
    return {
        "running": _analysis_cache["running"],
        "has_result": _analysis_cache["result"] is not None,
        "product_count": len(_analysis_cache["result"]) if _analysis_cache["result"] else 0,
    }


# ── 시장 신호 · 뉴스 (Perplexity) ─────────────────────────────────────────────

_news_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_NEWS_TTL = 1800  # 30분 캐시


def _parse_perplexity_news_items(raw_text: str) -> list[dict[str, str]]:
    """Perplexity 텍스트 응답에서 뉴스 배열(JSON) 파싱."""
    import re

    text = (raw_text or "").strip()
    if not text:
        return []

    candidates: list[str] = [text]
    m = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.S)
    if m:
        candidates.append(m.group(0))

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        items: list[dict[str, str]] = []
        for row in parsed[:6]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "source": str(row.get("source", "") or "").strip(),
                    "date": str(row.get("date", "") or "").strip(),
                    "link": str(row.get("link", "") or "").strip(),
                }
            )
        if items:
            return items
    return []


@app.get("/api/news")
async def api_news() -> JSONResponse:
    """Perplexity 기반 싱가포르 제약 시장 뉴스 (30분 캐시)."""
    import time as _time
    import os
    import httpx

    if _news_cache["data"] and _time.time() - _news_cache["ts"] < _NEWS_TTL:
        return JSONResponse(_news_cache["data"])

    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not px_key:
        return JSONResponse({"ok": False, "error": "PERPLEXITY_API_KEY 미설정", "items": []})

    try:
        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a Singapore pharmaceutical market analyst. "
                        "Return ONLY JSON array with up to 6 recent news items."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Find latest Singapore pharmaceutical market/regulatory news. "
                        "Return strict JSON array. Each item must have keys: "
                        "title, source, date, link."
                    ),
                },
            ],
            "max_tokens": 900,
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {px_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json()

        content = str(
            raw.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        items = _parse_perplexity_news_items(content)
        if not items:
            return JSONResponse({"ok": False, "error": "Perplexity 응답 파싱 실패", "items": []})

        data = {"ok": True, "items": items}
        _news_cache["data"] = data
        _news_cache["ts"]   = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:120], "items": []})


# ── 거시지표 ──────────────────────────────────────────────────────────────────

@app.get("/api/macro")
async def api_macro() -> JSONResponse:
    from utils.sg_macro import get_sg_macro
    return JSONResponse(get_sg_macro())


# ── 환율 (yfinance SGD/KRW) ───────────────────────────────────────────────────

_exchange_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_EXCHANGE_TTL_SEC = 0.0


@app.get("/api/exchange")
async def api_exchange() -> JSONResponse:
    """SGD/KRW 실시간 환율 (yfinance). 짧은 캐시로 준실시간 제공."""
    import time as _time

    if _exchange_cache["data"] and _time.time() - _exchange_cache["ts"] < _EXCHANGE_TTL_SEC:
        return JSONResponse(_exchange_cache["data"])

    def _fetch() -> dict[str, Any]:
        import yfinance as yf  # type: ignore[import]
        sgd_krw = float(yf.Ticker("SGDKRW=X").fast_info.last_price)
        usd_krw = float(yf.Ticker("USDKRW=X").fast_info.last_price)
        sgd_usd = float(yf.Ticker("SGDUSD=X").fast_info.last_price)
        sgd_jpy = float(yf.Ticker("SGDJPY=X").fast_info.last_price)
        sgd_cny = float(yf.Ticker("SGDCNY=X").fast_info.last_price)
        return {
            "sgd_krw": round(sgd_krw, 2),
            "usd_krw": round(usd_krw, 2),
            "sgd_usd": round(sgd_usd, 4),
            "sgd_jpy": round(sgd_jpy, 4),
            "sgd_cny": round(sgd_cny, 4),
            "source": "Yahoo Finance",
            "fetched_at": _time.time(),
            "ok": True,
        }

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch)
        _exchange_cache["data"] = data
        _exchange_cache["ts"]   = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        fallback: dict[str, Any] = {
            "sgd_krw": 1085.0,
            "usd_krw": 1393.0,
            "sgd_usd": 0.7795,
            "sgd_jpy": 113.2,
            "sgd_cny": 5.63,
            "source": "폴백 (Yahoo Finance 연결 실패)",
            "fetched_at": _time.time(),
            "ok": False,
            "error": str(exc),
        }
        return JSONResponse(fallback)


# ── 단일 품목 파이프라인 (분석 + 논문 + PDF) ──────────────────────────────────

_pipeline_tasks: dict[str, dict[str, Any]] = {}


async def _run_pipeline_for_product(product_key: str) -> None:
    task = _pipeline_tasks[product_key]
    try:
        # 0. DB 조회 (Supabase)
        task.update({"step": "db_load", "step_label": "Supabase 데이터 로드 중…"})
        await _emit({"phase": "pipeline", "message": f"{product_key} — DB 조회 중", "level": "info"})

        from utils.db import fetch_kup_products
        kup_rows = await asyncio.to_thread(fetch_kup_products, "SG")
        db_row = next((r for r in kup_rows if r.get("product_id") == product_key), None)

        if db_row is None:
            await _emit({"phase": "pipeline", "message": f"DB에서 품목 미발견: {product_key}", "level": "warn"})

        # 1. Claude 분석
        task.update({"step": "analyze", "step_label": "Claude 분석 중…"})
        await _emit({"phase": "pipeline", "message": f"{product_key} — 분석 시작", "level": "info"})

        from analysis.sg_export_analyzer import analyze_product
        result = await analyze_product(product_key, db_row)
        task["result"] = result
        verdict = result.get("verdict") or "미분석"
        await _emit({"phase": "pipeline", "message": f"분석 완료 — {verdict}", "level": "success"})

        # 2. Perplexity 논문
        task.update({"step": "refs", "step_label": "논문 검색 중…"})
        from analysis.perplexity_references import fetch_references
        refs = await fetch_references(product_key)
        task["refs"] = refs
        if refs:
            await _emit({"phase": "pipeline", "message": f"논문 {len(refs)}건 검색 완료", "level": "success"})

        # 3. PDF 보고서 (in-process 생성 — subprocess 의존성 제거)
        task.update({"step": "report", "step_label": "PDF 생성 중…"})
        await _emit({"phase": "pipeline", "message": "PDF 보고서 생성 중…", "level": "info"})

        from datetime import datetime, timezone as _tz
        from report_generator import build_report, render_pdf

        _ts = datetime.now(_tz.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir = ROOT / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)

        # kup_rows는 Step 0에서 이미 비동기로 가져왔으므로 재사용 (DB 이중 조회 방지)
        _refs_map = {product_key: refs}
        _report = await asyncio.to_thread(
            lambda: build_report(
                kup_rows,
                datetime.now(_tz.utc).isoformat(),
                [result],
                references=_refs_map,
            )
        )
        _pdf_name = f"sg_report_{product_key}_{_ts}.pdf"
        _pdf_path = _reports_dir / _pdf_name
        await asyncio.to_thread(render_pdf, _report, _pdf_path)

        task["pdf"] = _pdf_name
        task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _emit({"phase": "pipeline", "message": "파이프라인 완료", "level": "success"})

    except Exception as exc:
        task.update({"status": "error", "step": "error", "step_label": str(exc)})
        await _emit({"phase": "pipeline", "message": f"오류: {exc}", "level": "error"})


# ── 신약(커스텀) 파이프라인 ────────────────────────────────────────────────────
# 주의: 리터럴 경로("/api/pipeline/custom/...")는 반드시 {product_key} 라우트보다 먼저 선언

_custom_task: dict[str, Any] = {}


class CustomDrugBody(BaseModel):
    trade_name: str
    inn: str
    dosage_form: str = ""


async def _run_custom_pipeline(trade_name: str, inn: str, dosage_form: str) -> None:
    global _custom_task
    try:
        # Step 1: Claude 분석
        _custom_task.update({"step": "analyze", "step_label": "Claude 분석 중…"})
        from analysis.sg_export_analyzer import analyze_custom_product
        result = await analyze_custom_product(trade_name, inn, dosage_form)
        _custom_task["result"] = result

        # Step 2: Perplexity 논문
        _custom_task.update({"step": "refs", "step_label": "논문 검색 중…"})
        from analysis.perplexity_references import fetch_references_for_custom
        refs = await fetch_references_for_custom(trade_name, inn)
        _custom_task["refs"] = refs

        # Step 3: PDF 보고서 (in-process)
        _custom_task.update({"step": "report", "step_label": "PDF 생성 중…"})
        from datetime import datetime, timezone as _tz2
        from report_generator import build_report, render_pdf
        from utils.db import fetch_kup_products

        _ts2 = datetime.now(_tz2.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir2 = ROOT / "reports"
        _reports_dir2.mkdir(parents=True, exist_ok=True)

        _products_db2 = await asyncio.to_thread(fetch_kup_products, "SG")
        _refs_map2 = {"custom": refs}
        _report2 = await asyncio.to_thread(
            lambda: build_report(
                _products_db2,
                datetime.now(_tz2.utc).isoformat(),
                [result],
                references=_refs_map2,
            )
        )
        _pdf_name2 = f"sg_report_custom_{_ts2}.pdf"
        _pdf_path2 = _reports_dir2 / _pdf_name2
        await asyncio.to_thread(render_pdf, _report2, _pdf_path2)

        _custom_task["pdf"] = _pdf_name2
        _custom_task.update({"status": "done", "step": "done", "step_label": "완료"})

    except Exception as exc:
        _custom_task.update({"status": "error", "step": "error", "step_label": str(exc)})


@app.post("/api/pipeline/custom")
async def trigger_custom_pipeline(body: CustomDrugBody) -> JSONResponse:
    global _custom_task
    if _custom_task.get("status") == "running":
        raise HTTPException(status_code=409, detail="신약 분석이 이미 실행 중입니다.")
    _custom_task = {
        "status": "running", "step": "analyze", "step_label": "시작 중…",
        "result": None, "refs": [], "pdf": None,
    }
    asyncio.create_task(_run_custom_pipeline(body.trade_name, body.inn, body.dosage_form))
    return JSONResponse({"ok": True})


@app.get("/api/pipeline/custom/status")
async def custom_pipeline_status() -> JSONResponse:
    if not _custom_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     _custom_task.get("status", "idle"),
        "step":       _custom_task.get("step", ""),
        "step_label": _custom_task.get("step_label", ""),
        "has_result": _custom_task.get("result") is not None,
        "has_pdf":    bool(_custom_task.get("pdf")),
    })


@app.get("/api/pipeline/custom/result")
async def custom_pipeline_result() -> JSONResponse:
    if not _custom_task:
        raise HTTPException(404, "신약 분석 미실행")
    return JSONResponse({
        "status": _custom_task.get("status"),
        "result": _custom_task.get("result"),
        "refs":   _custom_task.get("refs", []),
        "pdf":    _custom_task.get("pdf"),
    })


# ── 기존 품목 파이프라인 ──────────────────────────────────────────────────────

@app.post("/api/pipeline/{product_key}")
async def trigger_pipeline(product_key: str) -> JSONResponse:
    if _pipeline_tasks.get(product_key, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="이미 실행 중입니다.")
    _pipeline_tasks[product_key] = {
        "status": "running", "step": "init", "step_label": "시작 중…",
        "result": None, "refs": [], "pdf": None,
    }
    asyncio.create_task(_run_pipeline_for_product(product_key))
    return JSONResponse({"ok": True, "message": "파이프라인 시작됨"})


@app.get("/api/pipeline/{product_key}/status")
async def pipeline_status(product_key: str) -> JSONResponse:
    task = _pipeline_tasks.get(product_key)
    if not task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     task["status"],
        "step":       task["step"],
        "step_label": task["step_label"],
        "has_result": task["result"] is not None,
        "has_pdf":    bool(task["pdf"]),
        "ref_count":  len(task.get("refs", [])),
    })


@app.get("/api/pipeline/{product_key}/result")
async def pipeline_result(product_key: str) -> JSONResponse:
    task = _pipeline_tasks.get(product_key)
    if not task:
        raise HTTPException(404, "파이프라인 미실행")
    return JSONResponse({
        "status": task["status"],
        "step":   task["step"],
        "result": task.get("result"),
        "refs":   task.get("refs", []),
        "pdf":    task.get("pdf"),
    })


# ── 보고서 ────────────────────────────────────────────────────────────────────

_report_cache: dict[str, Any] = {"path": None, "running": False}

def _latest_report_pdf() -> Path | None:
    reports_dir = ROOT / "reports"
    if not reports_dir.exists():
        return None
    pdfs = [p for p in reports_dir.glob("sg_report_*.pdf") if p.is_file()]
    if not pdfs:
        return None
    return max(pdfs, key=lambda p: p.stat().st_mtime)


class ReportBody(BaseModel):
    run_analysis: bool = False
    use_perplexity: bool = False


@app.post("/api/report")
async def trigger_report(body: ReportBody | None = None) -> JSONResponse:
    req = body if body is not None else ReportBody()
    if _report_cache["running"]:
        raise HTTPException(status_code=409, detail="보고서 생성이 이미 실행 중입니다.")

    async def _run_report() -> None:
        _report_cache["running"] = True
        try:
            import subprocess
            cmd = [
                sys.executable, str(ROOT / "report_generator.py"),
                "--out", str(ROOT / "reports"),
            ]
            if req.run_analysis:
                cmd.append("--run-analysis")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: subprocess.run(cmd, capture_output=True, text=True)
            )
            reports_dir = ROOT / "reports"
            pdfs = sorted(reports_dir.glob("sg_report_*.pdf"), reverse=True)
            _report_cache["path"] = str(pdfs[0]) if pdfs else None
        finally:
            _report_cache["running"] = False

    asyncio.create_task(_run_report())
    return JSONResponse({"ok": True, "message": "보고서 생성을 백그라운드에서 시작했습니다."})


@app.get("/api/report/status")
async def report_status() -> dict[str, Any]:
    reports_dir = ROOT / "reports"
    pdfs = [p for p in reports_dir.glob("sg_report_*.pdf")] if reports_dir.exists() else []
    latest = _latest_report_pdf()
    return {
        "running": _report_cache["running"],
        "latest_pdf": str(latest) if latest else _report_cache["path"],
        "pdf_count": len(pdfs),
    }


@app.get("/api/report/download")
async def download_report(name: str | None = None, inline: bool = False) -> Any:
    """PDF 반환. inline=true면 브라우저/iframe 미리보기용(Content-Disposition: inline)."""
    reports_dir = ROOT / "reports"
    disp = "inline" if inline else "attachment"
    if name:
        target = reports_dir / Path(name).name
        if target.is_file():
            return FileResponse(
                str(target),
                media_type="application/pdf",
                filename=target.name,
                content_disposition_type=disp,
            )

    latest = _latest_report_pdf()
    if not latest:
        raise HTTPException(status_code=404, detail="생성된 보고서 없음. POST /api/report 먼저 실행")
    return FileResponse(
        str(latest),
        media_type="application/pdf",
        filename=latest.name,
        content_disposition_type=disp,
    )


# ── 2공정 가격 전략 PDF ───────────────────────────────────────────────────────

class P2ReportBody(BaseModel):
    product_name:  str   = ""
    verdict:       str   = ""
    seg_label:     str   = ""
    base_price:    float | None = None
    formula_str:   str   = ""
    mode_label:    str   = ""
    scenarios:     list  = []
    ai_rationale:  list  = []


@app.post("/api/p2/report")
async def generate_p2_report(body: P2ReportBody) -> JSONResponse:
    """2공정 수출 가격 전략 PDF 생성."""
    import re
    from datetime import datetime, timezone as _tz_p2

    _ts = datetime.now(_tz_p2.utc).strftime("%Y%m%d_%H%M%S")
    _reports_dir = ROOT / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w가-힣]", "_", body.product_name)[:30] or "product"
    pdf_name  = f"sg_p2_{safe_name}_{_ts}.pdf"
    pdf_path  = _reports_dir / pdf_name

    p2_data = {
        "product_name":  body.product_name,
        "verdict":       body.verdict,
        "seg_label":     body.seg_label,
        "base_price":    body.base_price,
        "formula_str":   body.formula_str,
        "mode_label":    body.mode_label,
        "scenarios":     body.scenarios,
        "ai_rationale":  body.ai_rationale,
    }

    from report_generator import render_p2_pdf
    await asyncio.to_thread(render_p2_pdf, p2_data, pdf_path)

    return JSONResponse({"ok": True, "pdf": pdf_name})


# ── 2공정 AI 파이프라인 (PDF → Haiku 가격 추출 → 계산 → Haiku 분석 → PDF) ────────

_p2_ai_task: dict[str, Any] = {}


async def _run_p2_ai_pipeline(report_path: str, market: str) -> None:
    global _p2_ai_task
    try:
        import json
        import os
        import re

        import anthropic

        api_key = (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY", "")
        ).strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 미설정 — 환경변수를 확인하세요.")

        # ── Step 1: PDF 텍스트 추출 ────────────────────────────────────────────
        _p2_ai_task.update({"step": "extract", "step_label": "PDF 텍스트 추출 중…"})
        await _emit({"phase": "p2_pipeline", "message": "PDF 텍스트 추출 시작", "level": "info"})

        pdf_text = ""
        try:
            from pypdf import PdfReader  # type: ignore[import]
            reader = PdfReader(report_path)
            for page in reader.pages:
                pdf_text += (page.extract_text() or "") + "\n"
        except Exception as exc_pdf:
            await _emit({"phase": "p2_pipeline", "message": f"PDF 추출 경고: {exc_pdf}", "level": "warn"})

        if not pdf_text.strip():
            raise ValueError("PDF에서 텍스트를 추출할 수 없습니다. 스캔 이미지 PDF이거나 암호화된 파일일 수 있습니다.")

        await _emit({"phase": "p2_pipeline", "message": f"텍스트 {len(pdf_text)}자 추출 완료", "level": "success"})

        # ── Step 2: Claude Haiku — 가격 정보 추출 ──────────────────────────────
        _p2_ai_task.update({"step": "ai_extract", "step_label": "AI 가격 정보 추출 중…"})
        await _emit({"phase": "p2_pipeline", "message": "Claude Haiku — 가격 정보 추출", "level": "info"})

        client = anthropic.Anthropic(api_key=api_key)

        extract_prompt = f"""다음 의약품 수출 분석 보고서에서 가격 관련 정보를 추출하세요.

보고서 내용:
{pdf_text[:7000]}

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "product_name": "제품명 (없으면 '미상')",
  "ref_price_sgd": 숫자 또는 null,
  "ref_price_currency": "SGD 또는 USD",
  "ref_price_text": "원문 가격 텍스트 (없으면 빈 문자열)",
  "competitor_prices": [{{"name": "경쟁사명", "price_sgd": 숫자}}],
  "market_context": "시장 맥락 요약 (1-2문장)",
  "hs_code": "HS 코드 (없으면 빈 문자열)",
  "verdict": "수출 적합성 판정 (적합/조건부/부적합/미상)"
}}

SGD 가격이 명시되지 않고 USD만 있다면 ref_price_sgd는 null로 설정하세요."""

        extract_resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": extract_prompt}],
            )
        )

        extracted: dict[str, Any] = {}
        try:
            raw_extract = extract_resp.content[0].text
            m_json = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw_extract, re.S)
            if m_json:
                extracted = json.loads(m_json.group(0))
        except Exception:
            extracted = {
                "product_name": "미상",
                "ref_price_sgd": None,
                "ref_price_text": "",
                "market_context": "",
                "verdict": "미상",
            }

        _p2_ai_task["extracted"] = extracted
        await _emit({
            "phase": "p2_pipeline",
            "message": f"가격 추출 완료 — 참조가: SGD {extracted.get('ref_price_sgd', '미확인')}",
            "level": "success",
        })

        # ── Step 3: 실시간 환율 (yfinance) ────────────────────────────────────
        _p2_ai_task.update({"step": "exchange", "step_label": "실시간 환율 조회 중…"})
        await _emit({"phase": "p2_pipeline", "message": "yfinance 환율 조회", "level": "info"})

        exchange_rates: dict[str, Any] = {
            "sgd_krw": 1085.0, "usd_krw": 1393.0,
            "sgd_usd": 0.7795, "source": "폴백값 (Yahoo Finance 연결 실패)",
        }
        try:
            import yfinance as yf  # type: ignore[import]

            def _fetch_rates() -> dict[str, Any]:
                return {
                    "sgd_krw": round(float(yf.Ticker("SGDKRW=X").fast_info.last_price), 2),
                    "usd_krw": round(float(yf.Ticker("USDKRW=X").fast_info.last_price), 2),
                    "sgd_usd": round(float(yf.Ticker("SGDUSD=X").fast_info.last_price), 4),
                    "source": "Yahoo Finance (실시간)",
                }

            exchange_rates = await asyncio.to_thread(_fetch_rates)
        except Exception as exc_fx:
            await _emit({"phase": "p2_pipeline", "message": f"환율 폴백: {exc_fx}", "level": "warn"})

        _p2_ai_task["exchange_rates"] = exchange_rates
        await _emit({
            "phase": "p2_pipeline",
            "message": f"환율 — 1 SGD = {exchange_rates['sgd_krw']} KRW",
            "level": "success",
        })

        # ── Step 4: Claude Haiku — 최종 가격 전략 분석 ──────────────────────────
        _p2_ai_task.update({"step": "ai_analysis", "step_label": "AI 최종 분석 중…"})
        await _emit({"phase": "p2_pipeline", "message": "Claude Haiku — 최종 가격 전략 분석", "level": "info"})

        ref_price   = extracted.get("ref_price_sgd") or 0
        sgd_krw     = exchange_rates["sgd_krw"]
        market_label = "공공 시장 (ALPS/조달청 채널)" if market == "public" else "민간 시장 (병원·약국·체인 채널)"
        verdict_src  = extracted.get("verdict", "미상")
        competitor_json = json.dumps(extracted.get("competitor_prices", []), ensure_ascii=False)

        analysis_prompt = f"""싱가포르 수출 가격 전략을 수립해주세요.

## 추출된 보고서 정보
- 제품명: {extracted.get('product_name', '미상')}
- 수출 적합성 판정: {verdict_src}
- 참조가: SGD {ref_price if ref_price else '미확인'}
- 참조가 원문: {extracted.get('ref_price_text', '없음')}
- HS 코드: {extracted.get('hs_code', '미상')}
- 시장: {market_label}
- 현재 환율: 1 SGD = {sgd_krw:.2f} KRW (실시간 Yahoo Finance)
- 경쟁사 가격: {competitor_json}
- 시장 맥락: {extracted.get('market_context', '정보 없음')}

## 요청
1. 싱가포르 제약 시장의 특성, 판정 결과, 시장 구분을 종합해 최종 수출 권고가를 산정하세요.
2. 공식(formula_str)은 어떻게 최종 가격에 도달했는지 수식으로 명확히 서술하세요.
3. 시나리오는 공격적·평균·보수 3개로 구분하고, 각각의 가격과 이유를 구체적으로 서술하세요.
4. 산정 이유(rationale)는 3-4문장으로 시장 근거·판정 근거·리스크를 포함해 서술하세요.

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "final_price_sgd": 숫자,
  "formula_str": "산정 공식 (예: SGD 5.20(참조가) × 0.30(공공비율) = SGD 1.56)",
  "rationale": "산정 이유 3-4문장",
  "scenarios": [
    {{"name": "공격적인 시나리오", "price_sgd": 숫자, "reason": "1-2문장 이유"}},
    {{"name": "평균 시나리오", "price_sgd": 숫자, "reason": "1-2문장 이유"}},
    {{"name": "보수 시나리오", "price_sgd": 숫자, "reason": "1-2문장 이유"}}
  ]
}}

참조가(ref_price_sgd)가 null이라면 시장 데이터·경쟁사·제품 특성을 기반으로 합리적인 가격을 추정하세요."""

        analysis_resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                messages=[{"role": "user", "content": analysis_prompt}],
            )
        )

        analysis: dict[str, Any] = {}
        try:
            raw_analysis = analysis_resp.content[0].text
            m_json2 = re.search(r"\{.*\}", raw_analysis, re.S)
            if m_json2:
                analysis = json.loads(m_json2.group(0))
        except Exception:
            final_est = (ref_price * 0.30) if ref_price else 0
            analysis = {
                "final_price_sgd": round(final_est, 2),
                "formula_str": f"SGD {ref_price:.2f} × 30% = SGD {final_est:.2f}",
                "rationale": "AI 응답 파싱 중 오류가 발생했습니다. 기본값 30% 비율로 산정합니다.",
                "scenarios": [
                    {"name": "공격적인 시나리오", "price_sgd": round(final_est * 0.88, 2), "reason": "경쟁사 대비 저가 진입 전략"},
                    {"name": "평균 시나리오",    "price_sgd": round(final_est, 2),         "reason": "30% 기준비율 적용 기준가"},
                    {"name": "보수 시나리오",    "price_sgd": round(final_est * 1.12, 2),  "reason": "리스크 버퍼 포함 보수 가격"},
                ],
            }

        _p2_ai_task["analysis"] = analysis
        await _emit({
            "phase": "p2_pipeline",
            "message": f"최종 분석 완료 — SGD {analysis.get('final_price_sgd', 0):.2f}",
            "level": "success",
        })

        # ── Step 5: PDF 보고서 생성 ───────────────────────────────────────────
        _p2_ai_task.update({"step": "report", "step_label": "PDF 생성 중…"})
        await _emit({"phase": "p2_pipeline", "message": "2공정 PDF 보고서 생성", "level": "info"})

        from datetime import datetime, timezone as _tz_p2ai
        import re as _re2

        _ts_p2 = datetime.now(_tz_p2ai.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir_p2 = ROOT / "reports"
        _reports_dir_p2.mkdir(parents=True, exist_ok=True)

        _safe = _re2.sub(r"[^\w가-힣]", "_", extracted.get("product_name", "product"))[:30] or "product"
        _pdf_name_p2 = f"sg_p2_{_safe}_{_ts_p2}.pdf"
        _pdf_path_p2 = _reports_dir_p2 / _pdf_name_p2

        p2_data = {
            "product_name": extracted.get("product_name", "미상"),
            "verdict":      verdict_src,
            "seg_label":    market_label,
            "base_price":   analysis.get("final_price_sgd", 0),
            "formula_str":  analysis.get("formula_str", ""),
            "mode_label":   "AI 분석 (Claude Haiku)",
            "scenarios":    analysis.get("scenarios", []),
            "ai_rationale": [analysis.get("rationale", "")],
        }

        from report_generator import render_p2_pdf
        await asyncio.to_thread(render_p2_pdf, p2_data, _pdf_path_p2)

        _p2_ai_task["pdf"] = _pdf_name_p2
        _p2_ai_task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _emit({"phase": "p2_pipeline", "message": "P2 파이프라인 완료", "level": "success"})

    except Exception as exc:
        _p2_ai_task.update({"status": "error", "step": "error", "step_label": str(exc)[:300]})
        await _emit({"phase": "p2_pipeline", "message": f"P2 오류: {exc}", "level": "error"})


class UploadBody(BaseModel):
    filename: str
    content_b64: str  # base64 인코딩된 PDF 바이너리


@app.post("/api/p2/upload")
async def upload_p2_pdf(body: UploadBody) -> JSONResponse:
    """P2 파이프라인용 PDF 업로드 (base64 JSON — python-multipart 불필요)."""
    import base64
    import re as _re_up

    fname = body.filename or "upload.pdf"
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일(.pdf)만 업로드 가능합니다.")

    try:
        content = base64.b64decode(body.content_b64)
    except Exception:
        raise HTTPException(400, "base64 디코딩 실패 — 올바른 PDF 파일인지 확인하세요.")

    safe_fname = _re_up.sub(r"[^\w가-힣\-\.]", "_", fname)[:80]
    _reports_dir = ROOT / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)
    dest = _reports_dir / f"upload_{safe_fname}"
    dest.write_bytes(content)

    return JSONResponse({"ok": True, "filename": dest.name})


class P2PipelineBody(BaseModel):
    report_filename: str = ""  # reports/ 내 파일명 (비어 있으면 최신 1공정 PDF 사용)
    market: str = "public"     # "public" | "private"


@app.post("/api/p2/pipeline")
async def trigger_p2_pipeline(body: P2PipelineBody) -> JSONResponse:
    """2공정 AI 파이프라인 실행."""
    global _p2_ai_task
    if _p2_ai_task.get("status") == "running":
        raise HTTPException(409, "P2 파이프라인이 이미 실행 중입니다.")

    if body.report_filename:
        report_path = ROOT / "reports" / Path(body.report_filename).name
    else:
        report_path = _latest_report_pdf()

    if not report_path or not Path(report_path).is_file():
        raise HTTPException(404, f"보고서 파일을 찾을 수 없습니다: {body.report_filename or '(최신 PDF 없음)'}")

    _p2_ai_task = {
        "status":   "running",
        "step":     "extract",
        "step_label": "시작 중…",
        "extracted": None,
        "exchange_rates": None,
        "analysis": None,
        "pdf":      None,
    }
    asyncio.create_task(_run_p2_ai_pipeline(str(report_path), body.market))
    return JSONResponse({"ok": True})


@app.get("/api/p2/pipeline/status")
async def p2_pipeline_status_ai() -> JSONResponse:
    if not _p2_ai_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     _p2_ai_task.get("status", "idle"),
        "step":       _p2_ai_task.get("step", ""),
        "step_label": _p2_ai_task.get("step_label", ""),
        "has_result": _p2_ai_task.get("analysis") is not None,
        "has_pdf":    bool(_p2_ai_task.get("pdf")),
    })


@app.get("/api/p2/pipeline/result")
async def p2_pipeline_result_ai() -> JSONResponse:
    if not _p2_ai_task:
        raise HTTPException(404, "P2 파이프라인 미실행")
    return JSONResponse({
        "status":         _p2_ai_task.get("status"),
        "extracted":      _p2_ai_task.get("extracted"),
        "exchange_rates": _p2_ai_task.get("exchange_rates"),
        "analysis":       _p2_ai_task.get("analysis"),
        "pdf":            _p2_ai_task.get("pdf"),
    })


# ── products 조회 ─────────────────────────────────────────────────────────────

@app.get("/api/products")
async def products() -> list[dict[str, Any]]:
    from utils.db import fetch_kup_products
    return fetch_kup_products("SG")


# ── API 키 상태 (U1) ──────────────────────────────────────────────────────────

@app.get("/api/keys/status")
async def keys_status() -> dict[str, Any]:
    """Claude·Perplexity API 키 설정 여부 반환 (실제 키 값은 노출하지 않음)."""
    import os
    claude_key     = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
    return {
        "claude":     bool(claude_key.strip()),
        "perplexity": bool(perplexity_key.strip()),
    }


# ── 데이터 소스 상태 (U5·B1) ──────────────────────────────────────────────────

@app.get("/api/datasource/status")
async def datasource_status() -> JSONResponse:
    """Supabase 연결 상태, KUP 품목 수, HSA 컨텍스트 출처 반환."""
    try:
        from utils.db import get_client, fetch_kup_products
        kup_rows = fetch_kup_products("SG")
        kup_count = len(kup_rows)

        # HSA 컨텍스트 테이블 점검
        sb = get_client()
        ctx_count = 0
        context_source = "없음"
        try:
            ctx_rows = (
                sb.table("sg_product_context")
                .select("product_id", count="exact")
                .execute()
            )
            ctx_count = ctx_rows.count or 0
            context_source = f"sg_product_context {ctx_count}건" if ctx_count else "products 테이블 폴백"
        except Exception:
            context_source = "조회 실패"

        return JSONResponse({
            "supabase":       "ok",
            "kup_count":      kup_count,
            "context_ok":     ctx_count > 0,
            "context_source": context_source,
            "message":        f"KUP {kup_count}건 로드",
        })
    except Exception as exc:
        return JSONResponse({
            "supabase":       "error",
            "kup_count":      0,
            "context_ok":     False,
            "context_source": "연결 실패",
            "message":        str(exc)[:120],
        })


# ── 상태 / SSE 스트림 ─────────────────────────────────────────────────────────

@app.get("/api/status")
async def status() -> dict[str, Any]:
    lock = _state["lock"]
    assert lock is not None
    async with lock:
        n = len(_state["events"])
    return {"event_count": n}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Render 헬스체크용 경량 엔드포인트."""
    return {"ok": True, "service": "sg-analysis-dashboard"}


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    last = 0

    async def gen() -> Any:
        nonlocal last
        while True:
            await asyncio.sleep(0.12)
            chunk: list[dict[str, Any]] = []
            lock = _state["lock"]
            assert lock is not None
            async with lock:
                while last < len(_state["events"]):
                    chunk.append(_state["events"][last])
                    last += 1
            for ev in chunk:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index() -> FileResponse:
    index_path = STATIC / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html 없음")
    return FileResponse(index_path)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="SG 분석 대시보드")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    if args.open:
        def _open_later() -> None:
            time.sleep(1.0)
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        threading.Thread(target=_open_later, daemon=True).start()

    print(f"\n  ▶ 대시보드: http://127.0.0.1:{args.port}/\n")
    uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
