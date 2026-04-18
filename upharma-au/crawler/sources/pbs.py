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
        # 검색 결과 다수 매칭 시 자사 브랜드 외 경쟁자 (2공정 포지셔닝용, 하위호환)
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

    # Phase 4.3-v3 — 호주 PBS 시장 제형·강도 (시장조사 비교용)
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

    # 2공정 포지셔닝용 — 경쟁 브랜드 정보 (하위호환 키 이름 유지)
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


def fetch_pbs_by_ingredient(ingredient: str) -> list[dict[str, Any]]:
    """ingredient 로 PBS 품목을 찾아 PBSItemDTO(dict) 리스트 반환.

    매칭 다중 시 1 건만: originator_brand 우선, 없으면 dpmq 최저.
    없으면 [_empty_dto()]. Key 이름은 §13-5-1 PBSItemDTO 기준.

    중간안: /items + /item-dispensing-rule-relationships 조인.
    """
    ing_raw = (ingredient or "").strip()
    if not ing_raw:
        return [_empty_dto()]
    needles = _pbs_needles(ing_raw)
    ing = needles[0] if needles else ing_raw.lower()
    out_empty = _empty_dto()

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
            return _filter_results([
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
                return _filter_results([
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
            return _filter_results([
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

def fetch_pbs_fdc(
    components: list[str],
    fdc_search_term: str | None = None,
) -> dict[str, Any]:
    """Case 1 DIRECT — 복합제가 PBS 에 FDC (Fixed-Dose Combination, 고정용량복합제)
    한 줄로 등재된 경우.

    전략:
      1) fdc_search_term(있으면) 또는 components[0] 을 anchor 로 drug_name 조회
      2) 응답 중 drug_name·brand_name 이 모든 components 를 포함하는 행만 필터
      3) 여러 개면 dpmq 최저행 반환 (제네릭 우선)
      4) 매칭 0건이면 fetch_pbs_component_sum() 으로 폴백 + flag 기록

    반환: 단일 PBSItemDTO(dict). 폴백됐으면 'fdc_fallback': True 표시.
    """
    if not components:
        return _empty_dto()

    # 1차: FDC 통째 쿼리 (API 가 and 검색 지원 안 함 — 첫 성분 기준 + 응답 필터)
    anchor = fdc_search_term or components[0]
    rows = fetch_pbs_by_ingredient(anchor)

    def _all_in(dn: str) -> bool:
        dn_low = (dn or "").lower()
        return all(c.lower() in dn_low for c in components)

    matched = [
        r for r in rows
        if _all_in(r.get("drug_name") or r.get("brand_name") or "")
    ]

    if matched:
        def _dpmq(r: dict[str, Any]) -> Decimal:
            v = r.get("dpmq_aud") if r.get("dpmq_aud") is not None else r.get("pbs_dpmq")
            d = _safe_decimal(v) if v is not None else None
            return d if d is not None else Decimal("999999")

        best = dict(min(matched, key=_dpmq))
        best["pricing_case_applied"] = "DIRECT_FDC"
        return best

    # 폴백: FDC 행 없음 → 성분별 합산으로 전환
    fallback = fetch_pbs_component_sum(components)
    fallback["pricing_case_applied"] = "DIRECT_FDC_fallback_to_component_sum"
    fallback["fdc_fallback"] = True
    return fallback


def fetch_pbs_component_sum(components: list[str]) -> dict[str, Any]:
    """Case 2 COMPONENT_SUM — 복합제 FDC 미등재, 각 단일성분 PBS 등재 → 합산.

    전략: 성분별로 fetch_pbs_by_ingredient 호출 → _merge_pbs_rows (au_crawler 쪽).

    Phase 4.6 — PBS 미등재 성분(예: Rosumeg/Atmeg 의 omega-3-acid ethyl esters)은
    Chemist Warehouse 소매가를 폴백으로 조회하고 AEMP 역산(소매가 ÷ 1.6 근사)한
    가짜 행을 추가해 merge. 결과에 missing_from_pbs + confidence_override 기록.

    ※ AEMP 역산 배수 1.6 은 임시값 — 실측 근거 확보 시 utils/enums.py 상수화 예정.
    """
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

    # PBS 없는 성분 → Chemist 소매가 역산으로 보강 (Phase 4.6)
    if missing_from_pbs:
        try:
            from sources.chemist import fetch_chemist_price
        except ImportError:
            from .chemist import fetch_chemist_price  # type: ignore
        for c in missing_from_pbs:
            try:
                ch = fetch_chemist_price(c)
            except Exception:
                ch = None
            if not ch:
                continue
            # price_aud(v2) 우선, retail_price_aud(하위호환) 보조
            raw = ch.get("price_aud") if ch.get("price_aud") is not None else ch.get("retail_price_aud")
            price = _safe_decimal(raw)
            if price is None or price <= 0:
                continue
            aemp_est = (price / Decimal("1.6")).quantize(Decimal("0.01"))
            acc.append({
                "pbs_found": False,            # 실제 PBS 등재 아님 — 추정값
                "drug_name": c,
                "aemp_aud": aemp_est,
                "dpmq_aud": price,              # 소매가를 DPMQ 위치로 투영 (합산시 참고용)
                "_source": "chemist_fallback",
                "_confidence": 0.5,
                "pbs_code": None,
                "sponsors": [],
            })

    if not acc:
        return _empty_dto()

    from importlib import import_module
    au = import_module("au_crawler")
    merged = au._merge_pbs_rows(acc)
    merged["pricing_case_applied"] = "COMPONENT_SUM"
    merged["_component_rows"] = acc  # 감사 로그용
    merged["missing_from_pbs"] = missing_from_pbs
    merged["confidence_override"] = 0.6 if missing_from_pbs else 0.85
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
    """Case 4 ESTIMATE_substitute — 크롤러는 조회하지 않음.

    결정 (Jisoo, 2026-04-18): 유사약 PBS/Chemist 폴백 체인은 rate limit 21초 추가
    소요 + 품질 낮음. 크롤러는 'TGA(호주 의약품 등록 시스템) 미등재' 만 마킹하고
    유사약 서술은 보고서 생성기(Haiku 프롬프트) 가 similar_inns 배열을 받아 처리.
    (위임지서 Phase 4.9 수정 1 — Case 4 크롤러 축소)
    """
    dto = _empty_dto()
    dto["pricing_case_applied"] = "ESTIMATE_substitute"
    dto["_not_registered_au"] = True
    dto["_similar_inns_hint"] = list(similar_inns) if similar_inns else []
    dto["confidence_override"] = 0.1
    return dto


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

    반환: 빈 DTO + 메타 태그. FOB 는 2공정 fob_calculator 가 메모리 확정값 사용
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
