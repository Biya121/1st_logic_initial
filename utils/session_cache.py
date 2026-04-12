"""GeBIZ 세션 쿠키 캐시 (Supabase 암호화 저장 또는 로컬 파일 폴백).

보고서 §7-7:
  ① session_cache.py — Supabase 암호화 쿠키 저장/로드
  ② ttl_guard()      — 만료 5분 전 선제 갱신
  ③ click_jitter()   — 피츠 법칙 마우스 궤적 ±15%

환경변수:
  SUPABASE_URL, SUPABASE_KEY — 설정 시 Supabase에 저장
  SESSION_ENCRYPT_KEY        — Fernet 키 (미설정 시 자동 생성)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


# ── 암호화 헬퍼 ─────────────────────────────────────────────────────────────

def _get_fernet():
    """Fernet 인스턴스 반환. cryptography 미설치 시 None."""
    key = os.environ.get("SESSION_ENCRYPT_KEY")
    try:
        from cryptography.fernet import Fernet
        if not key:
            key = Fernet.generate_key().decode()
            os.environ["SESSION_ENCRYPT_KEY"] = key
        return Fernet(key.encode() if isinstance(key, str) else key)
    except ImportError:
        return None


def _encrypt(data: str) -> str:
    f = _get_fernet()
    if f is None:
        return data  # 암호화 불가 시 평문 폴백
    return f.encrypt(data.encode()).decode()


def _decrypt(token: str) -> str:
    f = _get_fernet()
    if f is None:
        return token
    try:
        return f.decrypt(token.encode()).decode()
    except Exception:
        return token


# ── 저장/로드 ─────────────────────────────────────────────────────────────────

_LOCAL_CACHE = Path(".gebiz_session_cache.json")


def _save_local(session_data: dict[str, Any]) -> None:
    payload = json.dumps(session_data, ensure_ascii=False)
    encrypted = _encrypt(payload)
    _LOCAL_CACHE.write_text(json.dumps({"enc": encrypted}), encoding="utf-8")


def _load_local() -> dict[str, Any] | None:
    if not _LOCAL_CACHE.is_file():
        return None
    try:
        raw = json.loads(_LOCAL_CACHE.read_text(encoding="utf-8"))
        decrypted = _decrypt(raw["enc"])
        return json.loads(decrypted)
    except Exception:
        return None


def save_session(session_data: dict[str, Any], *, table: str = "gebiz_sessions") -> None:
    """세션 데이터 저장. Supabase 우선, 실패 시 로컬 파일."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if url and key:
        try:
            from supabase import create_client
            sb = create_client(url, key)
            payload = json.dumps(session_data, ensure_ascii=False)
            encrypted = _encrypt(payload)
            sb.table(table).upsert(
                {"id": "gebiz_main", "encrypted_session": encrypted,
                 "updated_at": time.time()},
                on_conflict="id",
            ).execute()
            return
        except Exception:
            pass  # 로컬 폴백
    _save_local(session_data)


def load_session(*, table: str = "gebiz_sessions") -> dict[str, Any] | None:
    """세션 데이터 로드. Supabase 우선, 실패 시 로컬 파일."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if url and key:
        try:
            from supabase import create_client
            sb = create_client(url, key)
            res = sb.table(table).select("*").eq("id", "gebiz_main").execute()
            rows = res.data or []
            if rows:
                encrypted = rows[0]["encrypted_session"]
                decrypted = _decrypt(encrypted)
                return json.loads(decrypted)
        except Exception:
            pass
    return _load_local()


# ── TTL 가드 ──────────────────────────────────────────────────────────────────

_TTL_MARGIN_SECONDS = 300  # 만료 5분 전 선제 갱신


def ttl_guard(session_data: dict[str, Any] | None) -> bool:
    """세션이 유효(만료 5분 이상 남음)하면 True, 갱신 필요 시 False.

    session_data 형태: {"expires_at": <unix_timestamp>, ...}
    """
    if session_data is None:
        return False
    expires_at = session_data.get("expires_at")
    if expires_at is None:
        return False
    remaining = float(expires_at) - time.time()
    return remaining > _TTL_MARGIN_SECONDS


def clear_session() -> None:
    """캐시 삭제 (cold start 강제)."""
    if _LOCAL_CACHE.is_file():
        _LOCAL_CACHE.unlink()
