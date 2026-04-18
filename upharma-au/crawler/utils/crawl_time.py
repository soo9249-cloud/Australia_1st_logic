# Supabase 등 DB에 적재하는 크롤 시각 — 한국 표준시(Asia/Seoul) ISO 문자열.

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


def now_kst_iso() -> str:
    """현재 시각을 Asia/Seoul 기준 ISO-8601 문자열로 반환 (+09:00 오프셋 포함)."""
    return datetime.now(_KST).isoformat()
