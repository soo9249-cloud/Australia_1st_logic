# 크롤링·가격 산정에 사용할 열거형(구현 예정).

from enum import Enum


class ErrorType(Enum):
    """수집·검증 오류 유형."""
    PLACEHOLDER = "placeholder"


class PricingCase(Enum):
    """가격 시나리오 분류."""
    PLACEHOLDER = "placeholder"


class ExportViable(Enum):
    """수출 적합성 판단."""
    PLACEHOLDER = "placeholder"
