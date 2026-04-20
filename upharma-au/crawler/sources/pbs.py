# PBS 공개 API v3 — v2 스키마 준수. `PBSItemDTO` (딕셔너리 형태) 반환.
#
# 중간안 (위임지서 03a 결정 4c):
#   - /schedules + /items (기존)
#   - /item-dispensing-rule-relationships (신규 — DPMQ·brand_premium·TGP·SPC 획득)
#
# 다음 위임(v2-pbs-full)에서 추가될 엔드포인트:
#   - /fees                 (dispensing_fee_aud / ahi_fee_aud)
#   - /markup-bands         (markup_variable_pct / markup_offset / markup_fixed)
#   - /copayments           (copay_general / copay_concessional / safety_net)
#   - /item-atc-relationships (atc_code)
#
# 스펙 참조:
#   - /AX 호주 final/01_보고서필드스키마_v1.md §13-3 (엔드포인트 우선순위)
#   - /AX 호주 final/01_보고서필드스키마_v1.md §13-5-1 (PBSItemDTO)
#   - /AX 호주 final/01_보고서필드스키마_v1.md §14-3-1 (au_products 컬럼)
#
# 정책:
#   - 금융 숫자는 Decimal (§1-5). DB 저장 직전에 supabase_insert._jsonify_decimals 가 str 변환.
#   - 매 API 호출 사이 time.sleep(_RATE_LIMIT_SEC) — PBS rate limit 준수.
#   - Subscription-Key 누락·HTTP 실패 시 _empty_dto() 반환 (예외 전파 안 함).
#
# 다른 파일에서 사용하는 키(하위호환) : au_crawler.py 가 v2 컬럼으로 dict 생성 시 이 DTO 를 참조.

from __future__ import annotations

import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv

from utils.crawl_time import now_kst_iso
import httpx

# 프로젝트 루트 .env 로드 (cwd 무관하게 상위 경로 탐색)
_env_dir = Path(__file__).resolve().parent
for _ in range(8):
    _env_file = _env_dir / ".env"
    if _env_file.is_file():
        load_dotenv(_env_file)
        break
    if _env_dir.parent == _env_dir:
        load_dotenv()
        break
    _env_dir = _env_dir.parent
else:
    load_dotenv()

_BASE = "https://data-api.health.gov.au/pbs/api/v3"
_MAX_FALLBACK_PAGES = 10
_RATE_LIMIT_SEC = 21


def _headers() -> dict[str, str]:
    return {"Subscription-Key": os.environ["PBS_SUBSCRIPTION_KEY"]}


def _pbs_public_url(pbs_code: str | None) -> str:
    if pbs_code:
        return f"https://www.pbs.gov.au/browse/medicine?search={quote(str(pbs_code))}"
    return "https://www.pbs.gov.au/browse/medicine"


def _split_li_form(raw: Any) -> tuple[str | None, str | None]:
    """PBS API `li_form` 합쳐진 문자열을 (form, strength) 튜플로 분리.

    실측 예시 (Hydrine 재크롤링 로그):
      "Capsule 500 mg"            → ("Capsule", "500 mg")
      "Tablet 10 mg"              → ("Tablet", "10 mg")
      "Injection 10 mg/mL"        → ("Injection", "10 mg/mL")
      "Solution for infusion 1 g/100 mL" → ("Solution for infusion", "1 g/100 mL")
      "Powder for injection"      → ("Powder for injection", None)   # strength 없음
      ""                          → (None, None)

    분리 규칙: 끝에서부터 "<숫자><단위(mg|mcg|g|mL|IU|%|units 등)>" 를 찾고,
    그 앞까지를 form, 뒤에서부터를 strength 로 자름. 숫자+단위 패턴을 못 찾으면
    전체를 form 으로 반환 (strength=None).
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    # strength = 첫 "<숫자>...단위..." 부터 문자열 끝까지.
    # 단위 뒤의 슬래시·백분율·용기 문구 (/100 mL, /mL) 도 흡수.
    m = re.search(
        r"(\d[\d.,]*\s*(?:mg|mcg|µg|g|ml|mL|kg|iu|IU|units?|%)"
        r"(?:\s*/\s*[\d.]*\s*(?:mg|mcg|µg|g|ml|mL|kg|iu|IU|units?|%)?)?.*)$",
        s,
    )
    if not m:
        # strength 패턴 없음 → form 만
        return s, None
    strength = m.group(1).strip()
    form = s[: m.start()].strip()
    if not form:
        # "500 mg" 같이 strength 만 들어있는 비정상 케이스 — 역할 반전 방지
        return None, strength
    return form, strength


def _safe_decimal(v: Any) -> Decimal | None:
    """Any → Decimal | None. float/int/str 모두 허용. 파싱 실패 시 None.

    금융 숫자 처리 표준 헬퍼 — 위임지서 03a §1-5 "float 금지, Decimal 사용".
    """
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            return Decimal(s)
        except (InvalidOperation, ValueError):
            return None
    return None


def _derive_section_85_100(program_code: str | None) -> str | None:
    """program_code → section_85_100 유도 (§13-5-1).

    매핑 근거 (§13-7 Case 6 분기 기준):
      IN, IP          → S100_HSD (Highly Specialised Drugs, 병원 전용)
      TY, TZ          → S100_EFC (Efficient Funding, 효율적 조달)
      DB              → DB (Doctor's Bag)
      EP              → EP (Emergency Pharmaceutical)
      GE, R1, 기타    → S85 (일반처방약 섹션)
    """
    if not program_code:
        return None
    code = str(program_code).upper().strip()
    if code in {"IN", "IP"}:
        return "S100_HSD"
    if code in {"TY", "TZ"}:
        return "S100_EFC"
    if code == "DB":
        return "DB"
    if code == "EP":
        return "EP"
    return "S85"


def _empty_dto(pbs_source_url: str | None = None) -> dict[str, Any]:
    """PBS 미등재 품목용 빈 PBSItemDTO. pbs_found=False.

    위임지서 03a §2-1 "빈 값 정책": PBS 미등재 품목이면 PBS 관련 컬럼 전부 None
    (key 는 반드시 유지 — 스키마 정합성).
    """
    return {
        "pbs_found": False,
        "pbs_code": None,
        "li_item_id": None,
        "schedule_code": None,
        # 품목 정보
        "drug_name": None,
        "brand_name": None,
        "manufacturer_code": None,
        "organisation_id": None,
        "pack_size": None,
        "pricing_quantity": None,
        "maximum_prescribable_pack": None,
        "number_of_repeats": None,
        "pack_not_to_be_broken": None,
        # Phase 4.3-v3 (2026-04-18) — 호주 PBS 시장 등재 약의 제형·강도.
        # au_products.json 의 자사 제품 dosage_form/strength 와 구분 (시장조사 비교용).
        "market_form": None,        # PBS API items.form       — "호주 PBS 시장 제형"
        "market_strength": None,    # PBS API items.strength   — "호주 PBS 시장 강도"
        # 프로그램 분류
        "program_code": None,
        "section_85_100": None,
        "formulary": None,
        "benefit_type_code": None,
        # 가격 (AUD)
        "aemp_aud": None,
        "spd_aud": None,
        "claimed_price_aud": None,
        "dpmq_aud": None,
        "mn_pharmacy_price_aud": None,
        "brand_premium_aud": None,
        "therapeutic_group_premium_aud": None,
        "special_patient_contrib_aud": None,
        # 마진·수수료 (TODO(v2-pbs-full): /fees /markup-bands 엔드포인트 추가)
        "wholesale_markup_band": None,
        "pharmacy_markup_code": None,
        "markup_variable_pct": None,
        "markup_offset_aud": None,
        "markup_fixed_aud": None,
        "dispensing_fee_aud": None,
        "ahi_fee_aud": None,
        # 분류·정책 플래그
        "originator_brand": None,
        "therapeutic_group_id": None,
        "therapeutic_group_title": None,
        "brand_substitution_group_id": None,
        "atc_code": None,  # TODO(v2-pbs-full): /item-atc-relationships 엔드포인트 추가
        "policy_imdq60": None,
        "policy_biosim": None,
        "section_19a_expiry_date": None,
        "supply_only": None,
        # 환자 본인부담 (TODO(v2-pbs-full): /copayments 엔드포인트 추가)
        "copay_general_aud": None,
        "copay_concessional_aud": None,
        "safety_net_general_aud": None,
        "safety_net_concessional_aud": None,
        # 처방 제한
        "authority_method": None,
        "written_authority_required": None,
        # 이력
        "first_listed_date": None,
        "non_effective_date": None,
        "advanced_notice_date": None,
        "supply_only_end_date": None,
        # 가격 변동 이력
        "price_change_events": [],
        # 바이어 후보 풀용 (§13-7-B) — manufacturer_code 또는 organisation 에서 회사명
        "sponsors": [],
        # au_pbs_raw 저장용 — /items + /item-dispensing-rule-relationships 원본 통째
        "raw_response": {},
        # 메타
        "source_url": pbs_source_url or _pbs_public_url(None),
        "source_name": "pbs_api_v3",
        "crawled_at": None,
        # 검색 결과 다수 매칭 시 자사 브랜드 외 경쟁자 (수출전략 포지셔닝용, 하위호환)
        "pbs_brands": None,
        "competitor_brands": None,
        "pbs_total_brands": None,
    }


def _row_matches_ingredient(row: dict[str, Any], needles: list[str]) -> bool:
    """PubChem 정규화가 브랜드명으로 왜곡될 수 있어, 원문·정규화명 둘 다로 부분일치."""
    needles = [n.strip().lower() for n in needles if n and str(n).strip()]
    if not needles:
        return False
    parts: list[str] = []
    for key in ("drug_name", "li_drug_name", "generic_name", "product_name"):
        v = row.get(key)
        if isinstance(v, str):
            parts.append(v.lower())
    blob = " ".join(parts)
    return any(n in blob for n in needles)


def _row_to_dto(
    item_row: dict[str, Any],
    dispensing_rule_row: dict[str, Any] | None = None,
    *,
    schedule_code: str | None = None,
) -> dict[str, Any]:
    """/items row + /item-dispensing-rule-relationships row → PBSItemDTO.

    중간안 — /fees, /markup-bands, /copayments, /item-atc-relationships 는 NULL.
    TODO(v2-pbs-full): 위 4 엔드포인트 추가로 NULL 컬럼 14 개 중 8 개 채우기.
    """
    dto = _empty_dto()
    dto["pbs_found"] = True

    # ── 식별자 ─────────────────────────────────────────────────
    raw_code = item_row.get("pbs_code")
    dto["pbs_code"] = str(raw_code) if raw_code is not None else None
    dto["li_item_id"] = item_row.get("li_item_id")
    dto["schedule_code"] = schedule_code

    # ── 품목 정보 ───────────────────────────────────────────────
    dto["drug_name"] = item_row.get("drug_name") or item_row.get("li_drug_name")
    dto["brand_name"] = item_row.get("brand_name")
    dto["manufacturer_code"] = item_row.get("manufacturer_code")
    dto["organisation_id"] = item_row.get("organisation_id")
    dto["pack_size"] = item_row.get("pack_size")
    dto["pricing_quantity"] = item_row.get("pricing_quantity")
    dto["maximum_prescribable_pack"] = item_row.get("maximum_prescribable_pack")
    dto["number_of_repeats"] = item_row.get("number_of_repeats")
    dto["pack_not_to_be_broken"] = item_row.get("pack_not_to_be_broken_ind")

    # Phase 4.3-v3 — 호주 PBS 시장 제형·강도 (시장조사 비교용).
    # 추가 버그 수정 (Jisoo 2026-04-19) — PBS API 실제 키는 `li_form` 으로 합쳐진
    # 문자열 ("Capsule 500 mg"). 분리 필요. 독립 키 form/strength 는 fallback 유지.
    li_form_raw = item_row.get("li_form")
    if li_form_raw:
        _mf, _ms = _split_li_form(li_form_raw)
        dto["market_form"] = _mf
        dto["market_strength"] = _ms
    else:
        # 일부 응답에 독립 키로 올 수도 있어 예비 경로 유지.
        dto["market_form"] = item_row.get("form")
        dto["market_strength"] = item_row.get("strength")

    # ── 프로그램 분류 ───────────────────────────────────────────
    program_code = item_row.get("program_code")
    dto["program_code"] = program_code
    dto["section_85_100"] = _derive_section_85_100(program_code)
    dto["formulary"] = item_row.get("formulary")
    dto["benefit_type_code"] = item_row.get("benefit_type_code")

    # ── 가격 (AUD) — /items 로 채우는 것 ─────────────────────────
    dto["aemp_aud"] = _safe_decimal(item_row.get("determined_price"))
    dto["spd_aud"] = _safe_decimal(item_row.get("weighted_avg_disclosed_price"))
    dto["claimed_price_aud"] = _safe_decimal(item_row.get("claimed_price"))

    # ── 가격 (AUD) — /item-dispensing-rule-relationships 조인 ───
    if dispensing_rule_row:
        dto["dpmq_aud"] = _safe_decimal(dispensing_rule_row.get("cmnwlth_dsp_price_max_qty"))
        dto["mn_pharmacy_price_aud"] = _safe_decimal(dispensing_rule_row.get("mn_pharmacy_price"))
        dto["brand_premium_aud"] = _safe_decimal(dispensing_rule_row.get("brand_premium"))
        dto["therapeutic_group_premium_aud"] = _safe_decimal(dispensing_rule_row.get("therapeutic_group_premium"))
        dto["special_patient_contrib_aud"] = _safe_decimal(dispensing_rule_row.get("special_patient_contribution"))
        dto["wholesale_markup_band"] = dispensing_rule_row.get("mn_price_wholesale_markup")
        dto["pharmacy_markup_code"] = dispensing_rule_row.get("mn_pharmacy_markup_code")

    # ── 분류·정책 플래그 ───────────────────────────────────────
    # Phase 4.5 — 'Y'/'N' 문자열 플래그는 bool() 로 씌우면 둘 다 True 가 되므로
    # 반드시 .upper()=='Y' 로 비교. PBS API 는 innovator_indicator(신규) ·
    # originator_brand_indicator(구) 둘 중 하나만 채워줄 수 있으므로 양쪽 수용.
    def _yn_to_bool(val: Any) -> bool | None:
        if val is None:
            return None
        return str(val).strip().upper() == "Y"

    innov_raw = (
        item_row.get("originator_brand_indicator")
        if item_row.get("originator_brand_indicator") is not None
        else item_row.get("innovator_indicator")
    )
    dto["originator_brand"] = _yn_to_bool(innov_raw)
    dto["therapeutic_group_id"] = item_row.get("therapeutic_group_id")
    dto["therapeutic_group_title"] = item_row.get("therapeutic_group_title")
    dto["brand_substitution_group_id"] = item_row.get("brand_substitution_group_id")
    dto["policy_imdq60"] = _yn_to_bool(item_row.get("policy_applied_imdq60_flag"))
    dto["policy_biosim"] = _yn_to_bool(item_row.get("policy_applied_bio_sim_up_flag"))
    dto["section_19a_expiry_date"] = item_row.get("section_19a_expiry_date")
    dto["supply_only"] = _yn_to_bool(item_row.get("supply_only_indicator"))

    # ── 처방 제한 ─────────────────────────────────────────────
    dto["authority_method"] = item_row.get("authority_method")
    dto["written_authority_required"] = item_row.get("written_authority_required")

    # ── 이력 ──────────────────────────────────────────────────
    dto["first_listed_date"] = item_row.get("first_listed_date")
    dto["non_effective_date"] = item_row.get("non_effective_date")
    dto["advanced_notice_date"] = item_row.get("advanced_notice_date")
    dto["supply_only_end_date"] = item_row.get("supply_only_end_date")

    # ── 바이어 후보 풀 (§13-7-B) — manufacturer_code 또는 brand_name 에서 회사명 ──
    # PBS API 는 sponsor_name 직접 필드 없음. manufacturer_code(2-letter) 로 추정.
    # 회사명 정확 매핑은 /organisations 엔드포인트 필요 (TODO(v2-pbs-full)).
    sponsors: list[str] = []
    mnfr = item_row.get("manufacturer_code") or item_row.get("manufacturer_name")
    if mnfr and str(mnfr).strip():
        sponsors.append(str(mnfr).strip())
    dto["sponsors"] = sponsors

    # ── au_pbs_raw 저장용 원본 ────────────────────────────────
    dto["raw_response"] = {
        "items": item_row,
        "dispensing_rule": dispensing_rule_row or {},
    }

    # ── 메타 ──────────────────────────────────────────────────
    dto["source_url"] = _pbs_public_url(dto["pbs_code"])
    dto["crawled_at"] = now_kst_iso()

    return dto


# ─────────────────────────────────────────────────────────────────────
# API 호출 래퍼
# ─────────────────────────────────────────────────────────────────────

def fetch_latest_schedule_code() -> str | None:
    """/schedules data[0].schedule_code 문자열 반환. 실패 시 None."""
    try:
        time.sleep(_RATE_LIMIT_SEC)
        r = httpx.get(f"{_BASE}/schedules", headers=_headers(), timeout=10)
        if r.status_code != 200:
            return None
        payload = r.json()
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        code = data[0].get("schedule_code")
        return str(code) if code is not None else None
    except Exception:
        return None


def fetch_item_dispensing_rule(
    li_item_id: str | None,
    schedule_code: str | None,
) -> dict[str, Any] | None:
    """/item-dispensing-rule-relationships 에서 해당 li_item_id 행 1건 반환.

    중간안 추가 엔드포인트 — DPMQ·brand_premium·TGP·SPC·markup_code·wholesale_markup 채움.
    실패·매칭 없음 시 None (호출부에서 NULL 처리).
    """
    if not li_item_id or not schedule_code:
        return None
    try:
        time.sleep(_RATE_LIMIT_SEC)
        params: dict[str, Any] = {
            "schedule_code": schedule_code,
            "li_item_id": li_item_id,
            "page": 1,
            "limit": 5,
        }
        r = httpx.get(
            f"{_BASE}/item-dispensing-rule-relationships",
            params=params,
            headers=_headers(),
            timeout=10,
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
    except Exception:
        return None
    return None


# ─────────────────────────────────────────────────────────────────────
# 다중 결과 필터 — 오리지널 1 건 우선, 없으면 최저가 제네릭
# ─────────────────────────────────────────────────────────────────────

def _dto_price_sort_key(dto: dict[str, Any]) -> float:
    """_filter_results 정렬용 — DPMQ 가 최저인 행 선호 (없으면 AEMP)."""
    for key in ("dpmq_aud", "aemp_aud"):
        v = dto.get(key)
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (int, float)):
            return float(v)
    return float("inf")


def _filter_results(dtos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """매칭된 여러 브랜드 중 1 건만 반환 (오리지널 Y 우선, 없으면 최저가 제네릭)."""
    if not dtos:
        return dtos
    if not dtos[0].get("pbs_found"):
        return dtos

    # originator_brand True 인 행 우선. 없으면 False 중 최저가.
    originals = [d for d in dtos if d.get("originator_brand") is True]
    if originals:
        chosen = dict(originals[0])
    else:
        generics = [d for d in dtos if d.get("originator_brand") is False]
        pool = generics if generics else dtos
        chosen = dict(min(pool, key=_dto_price_sort_key))

    # 수출전략 포지셔닝용 — 경쟁 브랜드 정보 (하위호환 키 이름 유지)
    total = len({d.get("brand_name") for d in dtos if d.get("brand_name")})
    chosen["pbs_total_brands"] = total
    pbs_brands = [
        {
            "brand_name": d.get("brand_name"),
            "aemp_aud": d.get("aemp_aud"),
            "dpmq_aud": d.get("dpmq_aud"),
            "originator_brand": d.get("originator_brand"),
            "pbs_code": d.get("pbs_code"),
            "manufacturer_code": d.get("manufacturer_code"),
            "brand_premium_aud": d.get("brand_premium_aud"),
        }
        for d in dtos
    ]
    chosen["pbs_brands"] = pbs_brands
    chosen["competitor_brands"] = [
        b for b in pbs_brands
        if not (b.get("pbs_code") == chosen.get("pbs_code") and b.get("brand_name") == chosen.get("brand_name"))
    ]
    # 모든 브랜드의 sponsors 합치기 (바이어 후보 풀 — §13-7-B)
    all_sponsors: list[str] = list(chosen.get("sponsors") or [])
    for d in dtos:
        for s in (d.get("sponsors") or []):
            if s and s not in all_sponsors:
                all_sponsors.append(s)
    chosen["sponsors"] = all_sponsors

    # Phase Sereterol — 제네릭 선택 시에도 originator 정보를 별도 컬럼에 보존.
    # pool 에서 innovator_indicator='Y' 행의 브랜드·스폰서를 chosen 에 주입.
    chosen = _attach_originator_info(chosen, list(dtos))

    return [chosen]


# ─────────────────────────────────────────────────────────────────────
# 메인 엔트리 포인트 — 성분 기반 검색
# ─────────────────────────────────────────────────────────────────────

def _pbs_needles(ing_raw: str) -> list[str]:
    """원문 성분 + PubChem 정규화명(브랜드명으로 왜곡될 수 있음) 모두로 매칭."""
    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from utils.inn_normalize import normalize_inn

    raw = ing_raw.strip().lower()
    if not raw:
        return []
    norm = normalize_inn(ing_raw).strip().lower()
    return list(dict.fromkeys([x for x in (raw, norm) if x]))


def fetch_pbs_by_ingredient(
    ingredient: str,
    *,
    _return_all: bool = False,
) -> list[dict[str, Any]]:
    """ingredient 로 PBS 품목을 찾아 PBSItemDTO(dict) 리스트 반환.

    매칭 다중 시 1 건만: originator_brand 우선, 없으면 dpmq 최저.
    없으면 [_empty_dto()]. Key 이름은 §13-5-1 PBSItemDTO 기준.

    중간안: /items + /item-dispensing-rule-relationships 조인.

    Phase Sereterol 수정 (2026-04-19):
      `_return_all=True` 이면 `_filter_results` 적용 없이 매칭된 전체 DTO 리스트
      반환 — FDC set-equality 필터·originator 식별용. 내부 API.
    """
    ing_raw = (ingredient or "").strip()
    if not ing_raw:
        return [_empty_dto()]
    needles = _pbs_needles(ing_raw)
    ing = needles[0] if needles else ing_raw.lower()
    out_empty = _empty_dto()

    def _finalize(dtos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return dtos if _return_all else _filter_results(dtos)

    try:
        schedule = fetch_latest_schedule_code()
        if not schedule:
            return [out_empty]

        # 1 차: drug_name 파라미터
        params_primary: dict[str, Any] = {
            "schedule_code": schedule,
            "drug_name": ing,
            "page": 1,
            "limit": 10,
        }
        try:
            time.sleep(_RATE_LIMIT_SEC)
            r1 = httpx.get(
                f"{_BASE}/items",
                params=params_primary,
                headers=_headers(),
                timeout=10,
            )
        except Exception:
            r1 = None

        primary_matched: list[dict[str, Any]] = []
        if r1 is not None and r1.status_code == 200:
            try:
                payload = r1.json()
            except Exception:
                payload = {}
            rows = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and _row_matches_ingredient(row, needles):
                        primary_matched.append(row)

        if primary_matched:
            return _finalize([
                _join_dispensing_rule(r, schedule) for r in primary_matched
            ])

        # 1 차 보조: 정규화명으로 재조회
        if (
            not primary_matched
            and len(needles) > 1
            and needles[1] != needles[0]
        ):
            try:
                time.sleep(_RATE_LIMIT_SEC)
                r1b = httpx.get(
                    f"{_BASE}/items",
                    params={
                        "schedule_code": schedule,
                        "drug_name": needles[1],
                        "page": 1,
                        "limit": 10,
                    },
                    headers=_headers(),
                    timeout=10,
                )
            except Exception:
                r1b = None
            if r1b is not None and r1b.status_code == 200:
                try:
                    payload_b = r1b.json()
                except Exception:
                    payload_b = {}
                rows_b = payload_b.get("data") if isinstance(payload_b, dict) else None
                if isinstance(rows_b, list):
                    for row in rows_b:
                        if isinstance(row, dict) and _row_matches_ingredient(row, needles):
                            primary_matched.append(row)
            if primary_matched:
                return _finalize([
                    _join_dispensing_rule(r, schedule) for r in primary_matched
                ])

        # 2 차: filter 없이 페이지 순회, drug_name / li_drug_name 부분일치
        fallback_matched: list[dict[str, Any]] = []
        for page in range(1, _MAX_FALLBACK_PAGES + 1):
            try:
                time.sleep(_RATE_LIMIT_SEC)
                r2 = httpx.get(
                    f"{_BASE}/items",
                    params={"schedule_code": schedule, "page": page, "limit": 100},
                    headers=_headers(),
                    timeout=10,
                )
            except Exception:
                break
            if r2.status_code != 200:
                break
            payload = r2.json()
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if isinstance(row, dict) and _row_matches_ingredient(row, needles):
                    fallback_matched.append(row)
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            total = meta.get("total_records")
            if isinstance(total, int) and page * 100 >= total:
                break

        if fallback_matched:
            return _finalize([
                _join_dispensing_rule(r, schedule) for r in fallback_matched
            ])

        return [out_empty]
    except Exception:
        return [out_empty]


def _join_dispensing_rule(item_row: dict[str, Any], schedule: str) -> dict[str, Any]:
    """/item-dispensing-rule-relationships 조회 후 DTO 로 변환."""
    li_item_id = item_row.get("li_item_id")
    rule_row = fetch_item_dispensing_rule(li_item_id, schedule)
    return _row_to_dto(item_row, rule_row, schedule_code=schedule)


def fetch_pbs_multi(ingredients: list[str]) -> list[dict[str, Any]]:
    """여러 성분에 대해 fetch_pbs_by_ingredient 결과를 이어 붙인다."""
    if not ingredients:
        return [_empty_dto()]
    acc: list[dict[str, Any]] = []
    for raw in ingredients:
        acc.extend(fetch_pbs_by_ingredient(raw))
    return acc if acc else [_empty_dto()]


# ─────────────────────────────────────────────────────────────────────
# Case별 조회 함수 (pricing_case 분기용, v2 크롤링 로직 L150~534 기준)
# 위임지서: /AX 호주 final/CLAUDE_CODE_수정지시_Case별_크롤링_분기.md Phase 1.1
# ─────────────────────────────────────────────────────────────────────

def _dto_inn_set(dto: dict[str, Any]) -> frozenset[str]:
    """DTO 에서 base INN set 추출. FDC set-equality 매칭용.

    drug_name / brand_name / raw_response.items.li_drug_name·schedule_form 등
    가능한 텍스트 필드를 모두 넘겨 utils.inn_normalize.extract_inn_set() 호출.
    """
    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from utils.inn_normalize import extract_inn_set

    raw_resp = dto.get("raw_response") or {}
    items = raw_resp.get("items") if isinstance(raw_resp, dict) else {}
    if not isinstance(items, dict):
        items = {}
    return extract_inn_set(
        dto.get("drug_name"),
        dto.get("brand_name"),
        items.get("drug_name"),
        items.get("li_drug_name"),
        items.get("schedule_form"),
    )


def _attach_originator_info(
    chosen: dict[str, Any],
    pool: list[dict[str, Any]],
) -> dict[str, Any]:
    """pool 에서 innovator_indicator='Y' (originator_brand=True) 행을 찾아
    originator 브랜드명·스폰서를 chosen DTO 에 주입.

    PBS 제네릭 품목 여러 개가 등재돼 있을 때 오리지널 브랜드(예: Seretide Accuhaler)
    의 정보를 별도 컬럼에 보존 — chosen 자체는 가격·선택 로직으로 결정되지만
    originator 식별은 모든 매칭 행 풀에서 판정.
    """
    originator_row = next((r for r in pool if r.get("originator_brand") is True), None)
    if originator_row is None:
        # raw_response.items.innovator_indicator='Y' 직접 검사 (DTO 매핑 실패 대응)
        for r in pool:
            raw_items = (r.get("raw_response") or {}).get("items") or {}
            flag = raw_items.get("innovator_indicator") or raw_items.get("originator_brand_indicator")
            if flag and str(flag).strip().upper() == "Y":
                originator_row = r
                break
    if originator_row is not None:
        chosen["originator_brand_name"] = (
            originator_row.get("brand_name")
            or (originator_row.get("raw_response") or {}).get("items", {}).get("brand_name")
        )
        # sponsor / manufacturer_name 양쪽 수용
        raw_items = (originator_row.get("raw_response") or {}).get("items") or {}
        chosen["originator_sponsor"] = (
            raw_items.get("manufacturer_name")
            or raw_items.get("sponsor")
            or originator_row.get("manufacturer_code")
        )
        # chosen 자체가 originator 인지 여부와 무관하게 식별 정보는 채움
        if chosen.get("originator_brand") is None:
            # 명시 값 없음 → chosen 이 originator 행과 동일인지 비교
            chosen["originator_brand"] = (
                originator_row.get("pbs_code") == chosen.get("pbs_code")
                and originator_row.get("brand_name") == chosen.get("brand_name")
            )
    return chosen


def fetch_pbs_fdc(
    components: list[str],
    fdc_search_term: str | None = None,
    *,
    strengths: list[str] | None = None,
) -> dict[str, Any]:
    """Case 1 DIRECT — 복합제가 PBS 에 FDC (Fixed-Dose Combination, 고정용량복합제)
    한 줄로 등재된 경우.

    Phase Sereterol 수정 (2026-04-19):
      기존 "drug_name 에 각 component 문자열 substring 포함" 부분 매칭은
      "fluticasone+formoterol" 같은 다른 조합도 통과시켜 14449L 대신 10007Q 를
      선택하던 버그. → base INN set-equality 로 강화.

    전략:
      1) fdc_search_term(있으면) 또는 components[0] 을 anchor 로 drug_name 조회.
         `_return_all=True` 로 매칭된 전체 DTO 받기 (기존 _filter_results 우회).
      2) 각 DTO 의 활성성분 set (염 접미사 제거한 base INN) vs
         expected = set(strip_salt(c) for c in components) 비교.
         정확 일치 행만 통과.
      3) 0 건 → fetch_pbs_component_sum() fallback + flag 기록.
      4) 다 건 중 선택:
         (a) 자사 함량 (`strengths` 리스트 와 match_strength 비교) 우선
         (b) DPMQ 최저가 우선
      5) originator_brand_name / originator_sponsor 식별 — pool 에 innovator_indicator='Y'
         가 있으면 chosen 에 주입.

    반환: 단일 PBSItemDTO(dict). Fallback 된 경우 'fdc_fallback': True 표시.
    """
    if not components:
        return _empty_dto()

    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from utils.inn_normalize import strip_inn_salt

    expected_inns: frozenset[str] = frozenset(
        s for s in (strip_inn_salt(c) for c in components) if s
    )
    if not expected_inns:
        return _empty_dto()

    anchor = fdc_search_term or components[0]
    # Phase Sereterol — 전체 매칭 행 수집 (_filter_results 우회)
    all_rows = fetch_pbs_by_ingredient(anchor, _return_all=True)

    # base INN set-equality 필터
    matched = [r for r in all_rows if _dto_inn_set(r) == expected_inns]

    if not matched:
        fallback = fetch_pbs_component_sum(components)
        fallback["pricing_case_applied"] = "DIRECT_FDC_fallback_to_component_sum"
        fallback["fdc_fallback"] = True
        return fallback

    # (a) 자사 함량과 일치하는 행 우선
    def _normalize_strength_tok(s: str) -> str:
        # "250/50", "250/50 DPI", "500 mcg/50 mcg" 등을 단순히 숫자+슬래시 패턴으로 축약
        t = (s or "").lower().replace(" ", "")
        # 단위 제거
        for u in ("mcg", "mg", "µg", "g", "ml", "iu", "units", "%"):
            t = t.replace(u, "")
        return t

    if strengths:
        wanted = [_normalize_strength_tok(s) for s in strengths if s]
        preferred = []
        for r in matched:
            cand = _normalize_strength_tok(
                r.get("market_strength")
                or r.get("strength")
                or ""
            )
            # 부분 포함 매칭 — "250/50" in "250/50" 또는 거꾸로
            if cand and any(w and (w in cand or cand in w) for w in wanted):
                preferred.append(r)
        if preferred:
            matched = preferred

    # (b) DPMQ 최저 선택
    def _dpmq(r: dict[str, Any]) -> Decimal:
        v = r.get("dpmq_aud") if r.get("dpmq_aud") is not None else r.get("pbs_dpmq")
        d = _safe_decimal(v) if v is not None else None
        return d if d is not None else Decimal("999999")

    best = dict(min(matched, key=_dpmq))
    best["pricing_case_applied"] = "DIRECT_FDC"
    best["_fdc_match_count"] = len(matched)

    # pbs_brands / competitor_brands (_filter_results 스타일) 메타 주입
    total_brands = len({r.get("brand_name") for r in matched if r.get("brand_name")})
    best["pbs_total_brands"] = total_brands
    best["pbs_brands"] = [
        {
            "brand_name": r.get("brand_name"),
            "aemp_aud": r.get("aemp_aud"),
            "dpmq_aud": r.get("dpmq_aud"),
            "originator_brand": r.get("originator_brand"),
            "pbs_code": r.get("pbs_code"),
            "manufacturer_code": r.get("manufacturer_code"),
            "brand_premium_aud": r.get("brand_premium_aud"),
        }
        for r in matched
    ]
    best["competitor_brands"] = [
        b for b in best["pbs_brands"]
        if not (
            b.get("pbs_code") == best.get("pbs_code")
            and b.get("brand_name") == best.get("brand_name")
        )
    ]

    # originator 식별 — 풀에서 innovator_indicator='Y' 행 정보 주입
    best = _attach_originator_info(best, matched)

    return best


def _component_aemp_markup() -> Decimal:
    """COMPONENT_SUM fallback 에서 소매가 → AEMP 역산에 쓰는 배수.

    기본 1.6 (호주 약국 마크업·GST·조제료 역산 근사). 환경변수
    COMPONENT_AEMP_MARKUP 로 오버라이드 가능. 실측 근거 나오면
    utils/enums.py 상수화.
    """
    import os
    raw = (os.environ.get("COMPONENT_AEMP_MARKUP") or "1.6").strip()
    try:
        v = Decimal(raw)
        return v if v > Decimal("0") else Decimal("1.6")
    except (InvalidOperation, ValueError):
        return Decimal("1.6")


def _seeds_lookup_by_ingredient(ingredient: str) -> Decimal | None:
    """fob_reference_seeds.json 에서 같은 성분을 가진 다른 제품의
    reference_retail_aud 를 찾아 반환.

    동작: au_products.json 을 로드해서 inn_components 에 ingredient 가
    포함된 제품들의 product_id 집합을 얻고, seeds.json 에서 해당 product_id
    매칭 + reference_retail_aud non-null 인 첫 seed 의 값 반환.

    예) ingredient="omega-3-acid ethyl esters" →
        au-omethyl-001 (inn_components 포함) → seeds au-omethyl-001
        → reference_retail_aud=48.95 반환.

    실패 시 None.
    """
    if not ingredient:
        return None
    import json
    from pathlib import Path
    ing_low = ingredient.strip().lower()
    if not ing_low:
        return None

    # au_products.json 경로 (crawler/ 하위). 이 파일에서 2 단계 상위.
    crawler_dir = Path(__file__).resolve().parent.parent
    products_path = crawler_dir / "au_products.json"
    seeds_path = crawler_dir.parent / "stage2" / "fob_reference_seeds.json"

    try:
        with products_path.open(encoding="utf-8") as f:
            products_data = json.load(f)
        with seeds_path.open(encoding="utf-8") as f:
            seeds_data = json.load(f)
    except Exception:
        return None

    # ingredient 포함 제품 product_id 모으기
    candidates: list[str] = []
    for p in products_data.get("products", []):
        inn_comps = p.get("inn_components") or []
        inn_norm = (p.get("inn_normalized") or "").lower()
        comp_low = [str(c).lower() for c in inn_comps]
        if ing_low in comp_low or ing_low in inn_norm:
            pid = p.get("product_id")
            if pid:
                candidates.append(pid)

    if not candidates:
        return None

    # seeds 에서 해당 product_id 매칭 + reference_retail_aud non-null 첫 건
    for seed in seeds_data.get("seeds", []):
        if seed.get("product_id") in candidates:
            rr = seed.get("reference_retail_aud")
            if isinstance(rr, (int, float)) and float(rr) > 0:
                return Decimal(str(rr))
    return None


def fetch_pbs_component_sum(components: list[str]) -> dict[str, Any]:
    """Case 2 COMPONENT_SUM — 복합제 FDC 미등재, 각 단일성분 PBS 등재 → 합산.

    전략: 성분별로 fetch_pbs_by_ingredient 호출 → _merge_pbs_rows (au_crawler 쪽).

    Phase 4.6 — PBS 미등재 성분(예: Rosumeg/Atmeg 의 omega-3-acid ethyl esters)은
    Chemist Warehouse 소매가를 폴백으로 조회하고 AEMP 역산(소매가 ÷ 1.6 근사)한
    가짜 행을 추가해 merge. 결과에 missing_from_pbs + confidence_override 기록.

    Task 1 (2026-04-19) — PBS 미등재 성분 3단 fallback 체인:
      1. Chemist Warehouse (기존)
      2. Healthylife (Chemist 실패·비신뢰 시)
      3. fob_reference_seeds.json 의 같은 성분 reference_retail_aud
         (omega-3-acid ethyl esters 같은 건강기능식품성 성분 대응)
    모두 실패하면 missing_from_pbs 에 기록.

    각 fallback 행에 `_source` 로 출처 구분:
      "chemist_fallback" / "healthylife_fallback" / "seeds_reference_retail"

    AEMP 역산 배수는 _component_aemp_markup() — 환경변수 COMPONENT_AEMP_MARKUP 오버라이드.
    """
    markup = _component_aemp_markup()

    acc: list[dict[str, Any]] = []
    missing_from_pbs: list[str] = []
    for c in components:
        if not c:
            continue
        rows = fetch_pbs_by_ingredient(c)
        valid = [r for r in rows if r.get("pbs_found")]
        if valid:
            acc.extend(valid)
        else:
            missing_from_pbs.append(c)

    # Task 1 — PBS 없는 성분: Chemist → Healthylife → seeds 3단 fallback
    if missing_from_pbs:
        try:
            from sources.chemist import fetch_chemist_price
        except ImportError:
            from .chemist import fetch_chemist_price  # type: ignore
        try:
            from sources.healthylife import fetch_healthylife_price
        except ImportError:
            try:
                from .healthylife import fetch_healthylife_price  # type: ignore
            except Exception:
                fetch_healthylife_price = None  # type: ignore

        still_missing: list[str] = []
        for c in missing_from_pbs:
            # 1) Chemist Warehouse
            try:
                ch = fetch_chemist_price(c)
            except Exception:
                ch = None
            price: Decimal | None = None
            price_source = None
            if ch:
                raw = ch.get("price_aud") if ch.get("price_aud") is not None else ch.get("retail_price_aud")
                price = _safe_decimal(raw)
                if price is not None and price > 0:
                    price_source = "chemist_fallback"
                else:
                    price = None

            # 2) Healthylife
            if price is None and fetch_healthylife_price is not None:
                try:
                    hl = fetch_healthylife_price(c)
                except Exception:
                    hl = None
                if hl:
                    raw = hl.get("price_aud") if hl.get("price_aud") is not None else hl.get("retail_price_aud")
                    price = _safe_decimal(raw)
                    if price is not None and price > 0:
                        price_source = "healthylife_fallback"
                    else:
                        price = None

            # 3) seeds.reference_retail_aud (같은 성분 다른 제품)
            if price is None:
                seed_price = _seeds_lookup_by_ingredient(c)
                if seed_price is not None and seed_price > 0:
                    price = seed_price
                    price_source = "seeds_reference_retail"

            if price is None or price_source is None:
                # 3단 전부 실패 — missing 으로 남김
                still_missing.append(c)
                continue

            aemp_est = (price / markup).quantize(Decimal("0.01"))
            acc.append({
                "pbs_found": False,            # 실제 PBS 등재 아님 — 추정값
                "drug_name": c,
                "aemp_aud": aemp_est,
                "dpmq_aud": price,             # 소매가를 DPMQ 위치로 투영 (합산시 참고)
                "_source": price_source,
                "_confidence": 0.5 if price_source == "chemist_fallback" else (
                    0.45 if price_source == "healthylife_fallback" else 0.4
                ),
                "pbs_code": None,
                "sponsors": [],
            })

        # missing_from_pbs 최종 = 3단 전부 실패한 것만
        missing_from_pbs = still_missing

    if not acc:
        return _empty_dto()

    # ── 성분별 개별 가격 분리 (Stage 2 FOB 역산 전용) ──────────────────────────
    # 크롤러는 날 것 데이터만 저장, 마진 역산·합산은 Stage 2(fob_calculator)에서 수행.
    # pbs_prices  : PBS 등재 성분 → AEMP 직접 공시값 {ingredient_lower: aemp_aud}
    # retail_prices: 미등재 성분 → OTC 소매가 원본 {ingredient_lower: retail_price_aud}
    #                (dpmq_aud 위치에 소매가가 투영되어 저장됨 — line 1036 참조)
    pbs_prices: dict[str, float] = {}
    retail_prices: dict[str, float] = {}
    for _row in acc:
        _name = (_row.get("drug_name") or "").lower().strip()
        if not _name:
            continue
        if _row.get("pbs_found"):
            _a = _row.get("aemp_aud")
            if _a is not None:
                try:
                    pbs_prices[_name] = float(_a)
                except (TypeError, ValueError):
                    pass
        else:
            # 미등재 성분: dpmq_aud 에 소매가가 투영됨
            _r = _row.get("dpmq_aud")
            if _r is not None:
                try:
                    retail_prices[_name] = float(_r)
                except (TypeError, ValueError):
                    pass
    # ─────────────────────────────────────────────────────────────────────────

    from importlib import import_module
    au = import_module("au_crawler")
    merged = au._merge_pbs_rows(acc)
    merged["pricing_case_applied"] = "COMPONENT_SUM"
    merged["_component_rows"] = acc  # 감사 로그용
    merged["missing_from_pbs"] = missing_from_pbs
    merged["pbs_prices"] = pbs_prices        # Stage 2 전달용 — PBS 등재 성분 개별 AEMP
    merged["retail_prices"] = retail_prices  # Stage 2 전달용 — 미등재 성분 소매가 원본
    # Task 1 — 3단 fallback 전부 실패한 성분이 있으면 confidence 하향 (0.3).
    # fallback 으로 메꿨으면 0.6, 모든 성분 PBS 정등재면 0.85.
    if missing_from_pbs:
        merged["confidence_override"] = 0.3
    elif any(r.get("_source") in ("chemist_fallback", "healthylife_fallback", "seeds_reference_retail")
             for r in acc):
        merged["confidence_override"] = 0.6
    else:
        merged["confidence_override"] = 0.85
    return merged


def fetch_pbs_withdrawal(
    components: list[str],
    withdrawn_component: str,
    similar_inns: list[str],
) -> dict[str, Any]:
    """Case 3 ESTIMATE_withdrawal — 복합제 성분 중 하나가 시장 철수. Phase 1.1 원안.

    **롤백 결정 (Jisoo, 2026-04-18 재결정, Phase 4.9 수정 4 폐기)**:
    Ciloduo 는 cilostazol + rosuvastatin 복합제. rosuvastatin 만 조회하면
    반쪽 AEMP → FOB 역산 의미 없음. clopidogrel 은 PBS 등재 확인됨
    (13365K/13399F, 75mg×28, DPMQ $21.98, 제네릭 다수) + ATC B01AC 상위 분류
    (항혈소판제) → cilostazol 대체로 약리학적 정당성 확보. proxy fetch 복귀.

    전략:
      1) withdrawn_component 제외한 나머지 성분은 fetch_pbs_by_ingredient 로 AEMP 확보
      2) 철수 성분은 similar_inns[0] 유사계열 (Cilostazol → Clopidogrel) 로 AEMP 추정
      3) 두 AEMP 를 _merge_pbs_rows 로 합산
      4) 메타 태깅: withdrawn_component, similar_proxy_inns, confidence_override=0.3
    """
    acc: list[dict[str, Any]] = []
    for c in components:
        if c and c.lower() != (withdrawn_component or "").lower():
            rows = fetch_pbs_by_ingredient(c)
            valid = [r for r in rows if r.get("pbs_found")]
            if valid:
                acc.extend(valid)

    # 철수 성분 → 유사계열 프록시 fetch (Phase 1.1 원안, Jisoo 2026-04-18 재승인)
    if similar_inns:
        sim_rows = fetch_pbs_by_ingredient(similar_inns[0])
        valid_sim = [r for r in sim_rows if r.get("pbs_found")]
        for r in valid_sim:
            r["_estimated_for"] = withdrawn_component
            r["_similar_proxy"] = similar_inns[0]
        acc.extend(valid_sim)

    if not acc:
        dto = _empty_dto()
        dto["pricing_case_applied"] = "ESTIMATE_withdrawal"
        dto["withdrawn_component"] = withdrawn_component
        dto["similar_proxy_inns"] = similar_inns[:1] if similar_inns else []
        dto["confidence_override"] = 0.3
        return dto

    from importlib import import_module
    au = import_module("au_crawler")
    merged = au._merge_pbs_rows(acc)
    merged["pricing_case_applied"] = "ESTIMATE_withdrawal"
    merged["withdrawn_component"] = withdrawn_component
    merged["similar_proxy_inns"] = similar_inns[:1] if similar_inns else []
    merged["confidence_override"] = 0.3
    return merged


def fetch_pbs_similar(inn: str, similar_inns: list[str]) -> dict[str, Any]:
    """Case 4 ESTIMATE_substitute — 크롤러는 조회하지 않음 (레거시, deprecated).

    결정 (Jisoo, 2026-04-18): 유사약 PBS/Chemist 폴백 체인은 rate limit 21초 추가
    소요 + 품질 낮음. 크롤러는 'TGA(호주 의약품 등록 시스템) 미등재' 만 마킹하고
    유사약 서술은 보고서 생성기(Haiku 프롬프트) 가 similar_inns 배열을 받아 처리.
    (위임지서 Phase 4.9 수정 1 — Case 4 크롤러 축소)

    2026-04-19 dispatcher 는 fetch_pbs_substitute() 를 호출하도록 변경 예정.
    이 함수는 하위호환용 유지.
    """
    dto = _empty_dto()
    dto["pricing_case_applied"] = "ESTIMATE_substitute"
    dto["_not_registered_au"] = True
    dto["_similar_inns_hint"] = list(similar_inns) if similar_inns else []
    dto["confidence_override"] = 0.1
    return dto


def fetch_pbs_substitute(
    ingredient: str,
    similar_inns: list[str],
) -> dict[str, Any]:
    """Case 4 ESTIMATE_substitute (신설 — Task 2, 2026-04-19).

    기존 `fetch_pbs_similar` 는 조회 없이 빈 DTO 만 반환 → FOB 역산 불가 버그.
    이 함수는 similar_inns[0] 로 실제 PBS 조회 후 AEMP/DPMQ 를 주입.

    전략:
      1) similar_inns[0] 로 fetch_pbs_by_ingredient() 호출
      2) PBS 등재 확인되면 AEMP·DPMQ 를 반환 DTO 에 주입
      3) 메타 필드:
         - _substitute_for        : 원본 성분 (예: "mosapride")
         - _substitute_proxy      : 실제 사용된 성분 (예: "domperidone")
         - similar_drug_used      : similar_inns 전체 리스트 (기존 유지)
         - pricing_case_applied   : "ESTIMATE_substitute"
         - confidence_override    : 0.3
         - warnings               : ["similar_proxy_used:<proxy>"] 추가
      4) similar_inns 전부 PBS 에도 없으면 → _empty_dto() + warnings=["no_proxy_available"]

    반환: 단일 PBSItemDTO(dict).
    """
    similar_inns = [s for s in (similar_inns or []) if s]

    if not similar_inns:
        dto = _empty_dto()
        dto["pricing_case_applied"] = "ESTIMATE_substitute"
        dto["_substitute_for"] = ingredient
        dto["similar_drug_used"] = []
        dto["confidence_override"] = 0.3
        dto["warnings"] = ["no_proxy_available"]
        return dto

    # similar_inns 를 순회하며 첫 PBS 등재 proxy 발견 시 채택
    chosen_row: dict[str, Any] | None = None
    proxy_used: str | None = None
    for proxy in similar_inns:
        try:
            rows = fetch_pbs_by_ingredient(proxy)
        except Exception:
            rows = []
        valid = [r for r in rows if r.get("pbs_found")]
        if valid:
            # DPMQ 최저 (제네릭 우선 — _filter_results 가 이미 1건 반환하므로 대개 단일)
            def _dpmq(r: dict[str, Any]) -> Decimal:
                v = r.get("dpmq_aud") if r.get("dpmq_aud") is not None else r.get("pbs_dpmq")
                d = _safe_decimal(v) if v is not None else None
                return d if d is not None else Decimal("999999")
            chosen_row = dict(min(valid, key=_dpmq))
            proxy_used = proxy
            break

    if chosen_row is None:
        # similar_inns 전부 PBS 미등재
        dto = _empty_dto()
        dto["pricing_case_applied"] = "ESTIMATE_substitute"
        dto["_substitute_for"] = ingredient
        dto["similar_drug_used"] = list(similar_inns)
        dto["confidence_override"] = 0.3
        dto["warnings"] = ["no_proxy_available"]
        return dto

    # 성공 — proxy DTO 에 substitute 메타 주입
    chosen_row["pricing_case_applied"] = "ESTIMATE_substitute"
    chosen_row["_substitute_for"] = ingredient
    chosen_row["_substitute_proxy"] = proxy_used
    chosen_row["similar_drug_used"] = list(similar_inns)
    chosen_row["confidence_override"] = 0.3
    existing_warn = list(chosen_row.get("warnings") or [])
    existing_warn.append(f"similar_proxy_used:{proxy_used}")
    # FOB/보고서 검색용 표준 태그 (stage2 Logic A α=20% 와 동일 경로임을 추적)
    existing_warn.append(f"substitute_ingredient:{proxy_used}")
    existing_warn.append(f"similar_drug_used:{proxy_used}")
    chosen_row["warnings"] = existing_warn
    return chosen_row


def fetch_pbs_same_ingredient(reference_inn: str) -> dict[str, Any]:
    """Case 5 ESTIMATE_private — 동일 성분 다른 제형/함량 등재
    (예: Omethyl 2g pouch ↔ OMACOR 1g 캡슐).

    전략: reference_inn 으로 PBS 조회해서 AEMP 확보
    (미팅 결정: 동일 성분은 같은 가격 기준 OK).
    """
    rows = fetch_pbs_by_ingredient(reference_inn)
    if not rows:
        return _empty_dto()

    # 유효한 PBS 등재만 고려
    valid = [r for r in rows if r.get("pbs_found")]
    if not valid:
        return _empty_dto()

    def _dpmq(r: dict[str, Any]) -> Decimal:
        v = r.get("dpmq_aud") if r.get("dpmq_aud") is not None else r.get("pbs_dpmq")
        d = _safe_decimal(v) if v is not None else None
        return d if d is not None else Decimal("999999")

    best = dict(min(valid, key=_dpmq))
    best["pricing_case_applied"] = "ESTIMATE_private"
    best["_reference_inn"] = reference_inn
    best["confidence_override"] = 0.6
    return best


def fetch_pbs_hospital_skip() -> dict[str, Any]:
    """Case 6 ESTIMATE_hospital — 병원 조달 품목 (Gadvoa). PBS 조회 skip.

    반환: 빈 DTO + 메타 태그. FOB 는 fob_calculator 가 메모리 확정값 사용
    (Bayer 오리지널 $16.49/병 + 제네릭 3시나리오).
    """
    dto = _empty_dto()
    dto["pricing_case_applied"] = "ESTIMATE_hospital"
    dto["_pbs_skipped_reason"] = "hospital_procurement_only"
    dto["confidence_override"] = 0.3
    return dto


# ─────────────────────────────────────────────────────────────────────
# Web 보강 — Jina Reader (API 실패·pbs_web_source_url 용). DTO 반환 아님.
# ─────────────────────────────────────────────────────────────────────

def fetch_pbs_web(pbs_item_code: str) -> dict[str, Any]:
    """Jina Reader 로 PBS 품목 페이지 마크다운을 받아 DPMQ·환자부담 파싱.

    API 실패 시 최후 fallback. DTO 가 아니라 보조 dict 반환 (하위호환 유지 —
    au_crawler 가 DTO 에 이 값을 merge 할 때만 사용).

    TODO(v2-pbs-full): 이 함수의 반환도 DTO 형식으로 통일 예정 (향후 위임).
    """

    def _dollar(cell: str) -> Decimal | None:
        m = re.search(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)", cell.strip())
        if not m:
            return None
        return _safe_decimal(m.group(1).replace(",", ""))

    def _strip_md_links(cell: str) -> str:
        s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cell)
        return re.sub(r"\s+", " ", s).strip()

    def _code_in_first_cell(first_cell: str, want: str) -> bool:
        s = first_cell.strip()
        m_link = re.match(r"\[([^\]]+)\]\(", s)
        if m_link:
            return m_link.group(1).strip().upper() == want.upper()
        return s.upper().startswith(want.upper())

    code = (pbs_item_code or "").strip()
    canonical = f"https://www.pbs.gov.au/medicine/item/{code}" if code else ""
    empty: dict[str, Any] = {
        "dpmq_aud": None,
        "pbs_patient_charge": None,
        "pbs_web_source_url": canonical,
        "brand_name": None,
        "originator_brand": None,
        "pbs_brands": None,
    }
    if not code:
        return empty

    jina_url = f"https://r.jina.ai/https://www.pbs.gov.au/medicine/item/{code}"
    try:
        r = httpx.get(jina_url, timeout=15)
        if r.status_code != 200:
            return empty
        text = r.text or ""
    except Exception:
        return empty

    rows_out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line_st = line.strip()
        if not line_st.startswith("|"):
            continue
        parts = [p.strip() for p in line_st.split("|")]
        while parts and parts[0] == "":
            parts.pop(0)
        while parts and parts[-1] == "":
            parts.pop()
        if len(parts) < 8:
            continue
        if not _code_in_first_cell(parts[0], code):
            continue
        brand_txt = _strip_md_links(parts[1]) if len(parts) > 1 else ""
        dpmq_v = _dollar(parts[5])
        pat_v = _dollar(parts[7])
        rows_out.append(
            {
                "brand_name": brand_txt or None,
                "dpmq_aud": dpmq_v,
                "pbs_patient_charge": pat_v,
                "originator_brand": None,
                "pbs_code": code,
            }
        )

    if not rows_out:
        return empty

    first = rows_out[0]
    pbs_brands: list[dict[str, Any]] = [
        {
            "brand_name": r.get("brand_name"),
            "dpmq_aud": r.get("dpmq_aud"),
            "originator_brand": r.get("originator_brand"),
            "pbs_code": r.get("pbs_code"),
        }
        for r in rows_out
    ]
    return {
        "dpmq_aud": first.get("dpmq_aud"),
        "pbs_patient_charge": first.get("pbs_patient_charge"),
        "pbs_web_source_url": canonical,
        "brand_name": first.get("brand_name"),
        "originator_brand": None,
        "pbs_brands": pbs_brands,
    }


if __name__ == "__main__":
    import json as _json
    result = fetch_pbs_by_ingredient("hydroxycarbamide")
    for dto in result:
        # Decimal 은 json.dumps 불가 → 간이 출력
        print({k: (str(v) if isinstance(v, Decimal) else v) for k, v in dto.items() if k != "raw_response"})
