# 크롤링·수출 판단에 사용하는 문자열 Enum (프롬프트 v7 PROMPT 5).

from enum import Enum


class ErrorType(str, Enum):
    AUTH_FAIL = "auth_fail"
    RATE_LIMIT = "rate_limit"
    WAF_BLOCK = "waf_block"
    PARSE_ERROR = "parse_error"
    TIMEOUT = "timeout"


class PricingCase(str, Enum):
    DIRECT = "DIRECT"  # PBS 공시가 직접 수집
    COMPONENT_SUM = "COMPONENT_SUM"  # 복합제 성분별 합산
    ESTIMATE = "ESTIMATE"  # 민간가 추정 또는 수집 불가


class ExportViable(str, Enum):
    VIABLE = "viable"
    CONDITIONAL = "conditional"
    NOT_VIABLE = "not_viable"
