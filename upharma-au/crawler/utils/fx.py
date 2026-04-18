# 환율 환산 — AUD → USD, KRW
#
# 호주 PBS API 는 AUD 기준 가격을 반환한다. 보고서/UI는 USD·KRW도 함께 표기하므로
# 크롤러 단계에서 AUD 값을 받으면 즉시 USD/KRW 두 컬럼을 같이 채운다.
#
# 정책:
#   - 고정 환율 사용 (매일 갱신 API 도입은 다음 위임).
#   - 환경변수 FX_AUD_USD / FX_AUD_KRW 로 덮어쓰기 가능 (운영·테스트 편의).
#   - 금융 정밀도 보호 위해 float 금지 — Decimal 만 사용. supabase 전송 직전 변환은
#     supabase_insert.py 레이어에서 str() 로 처리.
#   - None 입력은 그대로 None 반환 (PBS 미등재 품목 대응).
#
# 근거:
#   - 위임지서 03a §1-5 : "금융 숫자는 float 금지, Decimal 사용".
#   - 위임지서 03a §2-3 : fx.py 설계 예시.

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

# 기본 환율 — 2026-04 기준 근사값. 운영 배포 전 매일 갱신 API 로 교체 예정.
_DEFAULT_AUD_USD = "0.65"
_DEFAULT_AUD_KRW = "920"


def _rate(env_key: str, default: str) -> Decimal:
    """환경변수에서 환율 읽기 — 파싱 실패 시 디폴트로 폴백."""
    raw = (os.environ.get(env_key) or default).strip()
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def aud_to_usd(aud: Decimal | int | float | str | None) -> Decimal | None:
    """AUD → USD 환산. 소수점 둘째 자리로 반올림.

    입력이 None 이면 None 반환. float/int/str 도 허용하되 내부는 Decimal 로 처리.
    """
    if aud is None:
        return None
    try:
        value = Decimal(str(aud))
    except (InvalidOperation, ValueError):
        return None
    return (value * _rate("FX_AUD_USD", _DEFAULT_AUD_USD)).quantize(Decimal("0.01"))


def aud_to_krw(aud: Decimal | int | float | str | None) -> Decimal | None:
    """AUD → KRW 환산. 원 단위 반올림 (소수점 없음).

    입력이 None 이면 None 반환.
    """
    if aud is None:
        return None
    try:
        value = Decimal(str(aud))
    except (InvalidOperation, ValueError):
        return None
    return (value * _rate("FX_AUD_KRW", _DEFAULT_AUD_KRW)).quantize(Decimal("1"))


# Task 9 (2026-04-19) — PDF 업로드 경로용 역환산 헬퍼.
# 사용자가 USD/KRW/EUR 로 된 가격 자료 PDF 업로드 시 AUD 로 정규화해 저장.
_DEFAULT_AUD_EUR = "0.60"   # 2026-04 근사. env FX_AUD_EUR 로 override.


def _to_dec(v: Any) -> Decimal | None:  # type: ignore[name-defined]
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def usd_to_aud(usd: Decimal | int | float | str | None) -> Decimal | None:
    """USD → AUD. 소수점 둘째 자리 반올림. None 은 그대로 None."""
    v = _to_dec(usd)
    if v is None:
        return None
    rate_aud_usd = _rate("FX_AUD_USD", _DEFAULT_AUD_USD)
    if rate_aud_usd == 0:
        return None
    return (v / rate_aud_usd).quantize(Decimal("0.01"))


def krw_to_aud(krw: Decimal | int | float | str | None) -> Decimal | None:
    """KRW → AUD. 소수점 둘째 자리 반올림."""
    v = _to_dec(krw)
    if v is None:
        return None
    rate_aud_krw = _rate("FX_AUD_KRW", _DEFAULT_AUD_KRW)
    if rate_aud_krw == 0:
        return None
    return (v / rate_aud_krw).quantize(Decimal("0.01"))


def eur_to_aud(eur: Decimal | int | float | str | None) -> Decimal | None:
    """EUR → AUD. FX_AUD_EUR (AUD→EUR 환율) 역산. 기본 0.60."""
    v = _to_dec(eur)
    if v is None:
        return None
    rate_aud_eur = _rate("FX_AUD_EUR", _DEFAULT_AUD_EUR)
    if rate_aud_eur == 0:
        return None
    return (v / rate_aud_eur).quantize(Decimal("0.01"))
