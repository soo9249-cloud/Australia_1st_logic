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
