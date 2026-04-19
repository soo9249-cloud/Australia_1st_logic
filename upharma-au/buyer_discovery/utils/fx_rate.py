"""yfinance 기반 실시간 환율 유틸. Stage 2 매출 환산 + 보고서 화폐 병기용.

용도:
  · Stage 2 바이어 매출 환산 (AUD 매출 → USD/KRW 병기)
  · PDF 보고서·프론트엔드 상단 "시장 규모 AUD X / USD Y / ₩ Z" 표시

사용 통화 (확장 가능):
  · AUD → USD (미국 달러 — 해외 레퍼런스용)
  · AUD → KRW (한국 원화 — 한국유나이티드 본사 기준)

캐시 정책:
  · `functools.lru_cache` + 날짜 키 → 같은 파이썬 프로세스 내에서 24시간 고정.
  · yfinance 네트워크 실패 시 fallback 값 반환 (최근 12개월 평균치 근사).

Fallback 근거 (2025-2026 평균):
  · AUD/KRW ≈ 900       (최근 12개월 평균)
  · AUD/USD ≈ 0.65

참고: yfinance 티커 포맷
  · AUDKRW=X  → 1 AUD 당 KRW
  · AUDUSD=X  → 1 AUD 당 USD
"""
from __future__ import annotations

import datetime as _dt
from functools import lru_cache
from typing import Any

try:
    import yfinance as yf  # type: ignore
except Exception:  # pragma: no cover — 개발 환경에서 설치 전 방어
    yf = None  # type: ignore

# ─────────────────────────────────────────────────────────────────────
# Fallback 상수 — yfinance 실패 시
# ─────────────────────────────────────────────────────────────────────

_FALLBACK_AUD_KRW: float = 900.0
_FALLBACK_AUD_USD: float = 0.65

# yfinance 티커 매핑 — 확장 시 여기만 추가.
_FX_TICKERS: dict[str, str] = {
    "aud_krw": "AUDKRW=X",
    "aud_usd": "AUDUSD=X",
}

_FALLBACK_RATES: dict[str, float] = {
    "aud_krw": _FALLBACK_AUD_KRW,
    "aud_usd": _FALLBACK_AUD_USD,
}


# ─────────────────────────────────────────────────────────────────────
# 실시간 환율 조회
# ─────────────────────────────────────────────────────────────────────

def _fetch_single_rate(ticker: str) -> float | None:
    """yfinance 에서 단일 티커의 최근 종가 조회. 실패 시 None."""
    if yf is None:
        return None
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="5d")
        if hist is None or hist.empty:
            return None
        close = hist["Close"].dropna()
        if close.empty:
            return None
        return float(close.iloc[-1])
    except Exception as exc:
        print(f"[fx_rate] {ticker} 조회 실패: {exc}", flush=True)
        return None


@lru_cache(maxsize=8)
def _cached_rates(date_key: str) -> tuple[tuple[str, float], ...]:
    """날짜(YYYY-MM-DD) 키로 lru_cache — 같은 날 반복 호출 시 네트워크 1회만.

    반환 형태는 dict 가 아니라 tuple-of-pairs 여야 lru_cache 가능 (hashable).
    """
    out: dict[str, float] = {}
    for key, ticker in _FX_TICKERS.items():
        rate = _fetch_single_rate(ticker)
        out[key] = rate if rate and rate > 0 else _FALLBACK_RATES[key]
    return tuple(out.items())


def get_fx_rates() -> dict[str, float]:
    """캐시된 실시간 환율. 키: 'aud_krw', 'aud_usd'.

    날짜 단위 캐시 (같은 프로세스에서 하루 1회만 네트워크).
    """
    date_key = _dt.date.today().isoformat()
    return dict(_cached_rates(date_key))


# ─────────────────────────────────────────────────────────────────────
# 변환·포맷 헬퍼
# ─────────────────────────────────────────────────────────────────────

def convert_aud(amount_aud: float, to: str) -> float:
    """AUD → USD/KRW 변환. `to='usd'` or `'krw'`.

    허용 키: 'usd', 'krw' (대소문자 무시). 알 수 없는 키는 ValueError.
    """
    try:
        amt = float(amount_aud)
    except (TypeError, ValueError):
        return 0.0
    key = f"aud_{(to or '').strip().lower()}"
    rates = get_fx_rates()
    if key not in rates:
        raise ValueError(f"지원하지 않는 통화: {to!r} (허용: usd, krw)")
    return amt * rates[key]


def _fmt_int(value: float) -> str:
    """정수 반올림 + 천단위 콤마. 'AUD 1,234,567' 표기용."""
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_compact(value: float) -> str:
    """축약 단위 포맷 — 1.5B, 320M, 45K. 보고서·프론트 메인 표시용."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "0"
    absv = abs(v)
    if absv >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if absv >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if absv >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{int(round(v)):,}"


def format_tri_currency(amount_aud: float) -> str:
    """보고서·프론트 표시용 — USD 메인, AUD 원본 병기, KRW 작게.

    포맷 예 (2026-04-20 Jisoo 요청 반영):
      "USD 357.8M (AUD 500.0M · ₩525.5B)"

    USD 를 가장 먼저·큰 비중으로 보여주고, 원본 AUD 와 원화는 괄호 안.
    변환 실패 시에도 fallback 환율로 최소 포맷 유지.
    """
    try:
        amt = float(amount_aud)
    except (TypeError, ValueError):
        amt = 0.0
    usd = convert_aud(amt, "usd")
    krw = convert_aud(amt, "krw")
    return f"USD {_fmt_compact(usd)} (AUD {_fmt_compact(amt)} · ₩{_fmt_compact(krw)})"


def format_tri_currency_full(amount_aud: float) -> str:
    """정확 자릿수 포맷 — 상세 페이지·PDF 하단 주석용.

    포맷 예: 'USD 357,763,827 (AUD 500,000,000 · ₩525,478,881,836)'
    """
    try:
        amt = float(amount_aud)
    except (TypeError, ValueError):
        amt = 0.0
    usd = convert_aud(amt, "usd")
    krw = convert_aud(amt, "krw")
    return f"USD {_fmt_int(usd)} (AUD {_fmt_int(amt)} · ₩{_fmt_int(krw)})"


__all__ = [
    "get_fx_rates",
    "convert_aud",
    "format_tri_currency",
    "format_tri_currency_full",
]
