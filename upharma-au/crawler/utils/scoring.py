# product_summary 완전성 점수(completeness_score) 계산.
#
# Phase 4.3-v3 (2026-04-18) — tga_schedule 폐기에 따라 AU_REQUIRED_FIELDS 에서
# 제거. (TGA 4필드 폐기: schedule/route/first_reg_date/sponsor_abn 전부 삭제,
# au_products.tga_schedule 컬럼도 DROP 완료.)

from __future__ import annotations

# fob_estimated_usd는 시장조사 단계에서 항상 null → 감점 제외
AU_REQUIRED_FIELDS: list[str] = [
    "artg_number",
    "pbs_item_code",
    "retail_price_aud",
    "price_source_url",
    "export_viable",
    "dosage_form",
]


def _is_field_filled(data: dict[str, object], key: str) -> bool:
    v = data.get(key)
    if v is None:
        return False
    if v == "":
        return False
    return True


def completeness_score(data: dict[str, object], base: float = 0.95) -> float:
    """필드 채움률과 치명 필드 감점으로 0~0.95 점수를 반환한다."""
    if not data:
        return 0.0
    filled = sum(1 for f in AU_REQUIRED_FIELDS if _is_field_filled(data, f))
    ratio = filled / len(AU_REQUIRED_FIELDS)
    score = ratio * base
    if not _is_field_filled(data, "artg_number"):
        score -= 0.20
    if not _is_field_filled(data, "retail_price_aud"):
        score -= 0.15
    score = max(0.0, min(0.95, score))
    return round(score, 2)
