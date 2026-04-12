"""로컬 대시보드: SSE 실시간 로그 + products JSON API."""

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

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawlers.pipeline import run_full_crawl
from utils import db as dbutil
from web.dashboard_sites import DASHBOARD_SITES, initial_site_states

DB_PATH = ROOT / "datas" / "local_products.db"
STATIC = Path(__file__).resolve().parent / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_state: dict[str, Any] = {
    "events": [],
    "lock": None,
    "running": False,
}

_site_states: dict[str, dict[str, Any]] = initial_site_states()


def _reset_site_states() -> None:
    global _site_states
    _site_states = initial_site_states()


class RunCrawlBody(BaseModel):
    use_ai_discovery: bool = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Lock은 실행 중인 이벤트 루프에 묶여야 함 (모듈 최상단 생성 금지)
    _state["lock"] = asyncio.Lock()
    _reset_site_states()
    yield


app = FastAPI(title="SG Crawl Dashboard", version="0.1.0", lifespan=_lifespan)


async def _emit(event: dict[str, Any]) -> None:
    payload = {**event, "ts": time.time()}
    lock = _state["lock"]
    assert lock is not None
    async with lock:
        _state["events"].append(payload)
        if len(_state["events"]) > 500:
            _state["events"] = _state["events"][-400:]
        if event.get("phase") == "site_progress" and event.get("site_key"):
            sk = str(event["site_key"])
            if sk in _site_states:
                _site_states[sk] = {
                    "status": event.get("site_status", "ok"),
                    "message": event.get("message", ""),
                    "ts": payload["ts"],
                }


async def _run_pipeline_task(use_ai_discovery: bool = False) -> None:
    _state["running"] = True
    try:
        await run_full_crawl(
            ROOT,
            _emit,
            db_path=DB_PATH,
            include_ai_discovery=use_ai_discovery,
        )
    finally:
        _state["running"] = False


@app.post("/api/run")
async def trigger_run(body: RunCrawlBody | None = None) -> JSONResponse:
    req = body if body is not None else RunCrawlBody()
    if _state["running"]:
        raise HTTPException(status_code=409, detail="이미 크롤이 실행 중입니다.")
    _reset_site_states()
    asyncio.create_task(_run_pipeline_task(req.use_ai_discovery))
    return JSONResponse({"ok": True, "message": "크롤 작업을 백그라운드에서 시작했습니다."})


@app.get("/api/sites")
async def api_sites() -> list[dict[str, Any]]:
    lock = _state["lock"]
    assert lock is not None
    async with lock:
        snap = {k: dict(v) for k, v in _site_states.items()}
    out: list[dict[str, Any]] = []
    for s in DASHBOARD_SITES:
        st = snap.get(s["id"], {})
        out.append(
            {
                "id": s["id"],
                "name": s["name"],
                "hint": s["hint"],
                "domain": s["domain"],
                "status": st.get("status", "pending"),
                "message": st.get("message", "아직 시작 전이에요"),
            }
        )
    return out


@app.get("/api/status")
async def status() -> dict[str, Any]:
    lock = _state["lock"]
    assert lock is not None
    async with lock:
        n = len(_state["events"])
    return {"running": _state["running"], "event_count": n}


@app.get("/api/products")
async def products() -> list[dict[str, Any]]:
    conn = dbutil.get_connection(DB_PATH)
    try:
        rows = dbutil.fetch_all_products(conn)
    finally:
        conn.close()
    for r in rows:
        if r.get("raw_payload"):
            try:
                r["raw_payload"] = json.loads(r["raw_payload"])
            except (json.JSONDecodeError, TypeError):
                pass
    return rows


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
                line = json.dumps(ev, ensure_ascii=False)
                yield f"data: {line}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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

    parser = argparse.ArgumentParser(description="싱가포르 크롤 로컬 대시보드")
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="바인드 주소 (macOS는 localhost 대신 127.0.0.1 권장)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--open",
        action="store_true",
        help="서버 기동 후 브라우저 자동 열기",
    )
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/"
    if args.host == "0.0.0.0":
        url = f"http://127.0.0.1:{args.port}/"

    print(
        "\n  ▶ 대시보드 주소 (주소창에 그대로 복사):\n"
        f"    http://127.0.0.1:{args.port}/\n"
        "  ▶ Safari/Chrome에서 'localhost'만 쓰면 IPv6(::1)로 붙어 연결이 안 될 수 있습니다.\n"
        "    반드시 127.0.0.1 을 사용하세요.\n"
        f"  ▶ 서버 바인드: {args.host}:{args.port}\n",
        flush=True,
    )

    if args.open:

        def _open_later() -> None:
            time.sleep(1.0)
            webbrowser.open(url)

        threading.Thread(target=_open_later, daemon=True).start()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
