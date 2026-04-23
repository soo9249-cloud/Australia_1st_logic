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
from bs4 import BeautifulSoup

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
_MAX_FDC_PAGES = 5   # FDC 전체 페이지 스캔 최대치 — fluticasone+salmeterol 은 3~4 페이지에 위치
_RATE_LIMIT_SEC = 21

# ── PBS 웹 크롤링 상수 (FDC API 실패 시 fallback 전용) ──────────────
# data-api.health.gov.au API가 FDC를 못 찾을 때 pbs.gov.au 직접 크롤링.
# Subscription-Key 불필요, rate limit 2초 (API 21초 대비 대폭 단축).
_PBS_WEB_SEARCH_URL = "https://www.pbs.gov.au/pbs/search"
_PBS_WEB_ITEM_BASE  = "https://www.pbs.gov.au/medicine/item"
_PBS_WEB_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}
# PBS 가격 역산 상수 (2024-25 기준)
# 공식: DPMQ = AEMP × (1 + 도매마진%) + 조제료
# 역산: AEMP = (DPMQ - 조제료) ÷ (1 + 도매마진%)
_PBS_WEB_DISPENSING_FEE   = Decimal("8.32")    # 표준 조제료 (Standard Dispensing Fee)
_PBS_WEB_WHOLESALE_MARKUP = Decimal("0.0752")  # 도매마진 Band 1 (7.52%)
_PBS_WEB_RATE_SEC         = 2                  # 웹 크롤링 요청 간격 (초)


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
        # _return_all=True (FDC 매칭용)일 때는 limit=100 — 동일 성분 FDC가
        # 알파벳 순 뒤쪽에 올 수 있으므로 (예: fluticasone+formoterol 이 먼저,
        # fluticasone+salmeterol 이 나중). limit=10 이면 앞쪽 10개만 보고 종료.
        params_primary: dict[str, Any] = {
            "schedule_code": schedule,
            "drug_name": ing,
            "page": 1,
            "limit": 100 if _return_all else 10,
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

        # 웹 크롤링 fallback — API 3단계(primary/보조/전체스캔) 모두 실패 시
        # _return_all=True 는 fetch_pbs_fdc 내부 FDC 탐색용 → 웹 fallback 불필요
        if not _return_all:
            _web = _fetch_ingredient_web(ing_raw)
            if _web is not None:
                return [_web]

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

    drug_name / li_drug_name 필드만 사용 (schedule_form 제외).
    schedule_form 은 "250 mcg/actuation ... 60 actuations" 형태의 노이즈 토큰을
    포함하여 set-equality 를 항상 실패시키는 버그가 있었음 (2026-04-22 수정).

    예) drug_name = "fluticasone propionate + salmeterol"
        → extract_inn_set → frozenset({"fluticasone", "salmeterol"}) ✓
    예) schedule_form = "fluticasone propionate 250 mcg/actuation + salmeterol 50 mcg/actuation inhaler, 60 actuations"
        → extract_inn_set → frozenset({"fluticasone", "salmeterol", "actuation", "actuation inhaler"}) ✗
    """
    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from utils.inn_normalize import extract_inn_set

    raw_resp = dto.get("raw_response") or {}
    items = raw_resp.get("items") if isinstance(raw_resp, dict) else {}
    if not isinstance(items, dict):
        items = {}
    # drug_name 과 li_drug_name 만 사용 — 이 두 필드는 INN 이름만 포함
    # (예: "fluticasone propionate + salmeterol", "Fluticasone propionate with Salmeterol")
    return extract_inn_set(
        dto.get("drug_name"),
        items.get("drug_name"),
        items.get("li_drug_name"),
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


def _fdc_raw_page_scan(anchor: str) -> tuple[list[dict[str, Any]], str | None]:
    """FDC 매칭 전용 — anchor 로 PBS API 를 스캔하여 needle 이 포함된 raw item rows 수집.

    raw item rows 만 반환 (dispensing rule 조인 없음) — 호출부(fetch_pbs_fdc) 에서
    set-equality 필터 후 1건만 _join_dispensing_rule 로 조인하여 API 호출 최소화.

    스캔 전략 (2단계):
      1차) drug_name=anchor 필터로 _MAX_FDC_PAGES 페이지 스캔 (빠름).
           PBS API 가 HTTP 204 를 반환하거나 결과가 0 건이면 2차로 전환.
           예) anchor="fluticasone propionate" → 22 건 반환 (단독 성분만).
           예) anchor="fluticasone" → PBS API 204 반환 (exact-match 미지원) → 2차.
      2차) drug_name 필터 없이 _MAX_FALLBACK_PAGES 전 페이지 스캔 →
           _row_matches_ingredient(row, needles) 로 needle 포함 행만 수집.
           fetch_pbs_by_ingredient 의 2차 fallback 과 동일한 방식.

    예) anchor="fluticasone" → 2차 스캔 → fluticasone 단독·fluticasone+formoterol·
        fluticasone+salmeterol 전부 수집. 호출부가 set-equality 로 fluticasone+salmeterol 만 채택.

    반환: (raw_item_rows, schedule_code). 실패 시 ([], None).
    """
    needles = _pbs_needles(anchor)
    ing = needles[0] if needles else anchor.strip().lower()
    if not ing:
        return [], None

    try:
        schedule = fetch_latest_schedule_code()
        if not schedule:
            return [], None

        # ── 1차: drug_name=anchor 필터 스캔 ──────────────────────────────
        all_item_rows: list[dict[str, Any]] = []
        for page in range(1, _MAX_FDC_PAGES + 1):
            try:
                time.sleep(_RATE_LIMIT_SEC)
                r = httpx.get(
                    f"{_BASE}/items",
                    params={
                        "schedule_code": schedule,
                        "drug_name": ing,
                        "page": page,
                        "limit": 100,
                    },
                    headers=_headers(),
                    timeout=10,
                )
            except Exception:
                break
            if r.status_code != 200:
                # HTTP 204: PBS API 가 이 drug_name 에 대해 exact-match 결과 없음
                break
            try:
                payload = r.json()
            except Exception:
                break
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            total = meta.get("total_records")
            for row in rows:
                if isinstance(row, dict) and _row_matches_ingredient(row, needles):
                    all_item_rows.append(row)
            if isinstance(total, int) and page * 100 >= total:
                break

        if all_item_rows:
            return all_item_rows, schedule

        # ── 2차: drug_name 필터 없이 전체 스캔 ──────────────────────────
        # PBS API 204 등으로 1차 결과 0 건인 경우 (anchor 가 정확한 drug_name 값이 아닐 때).
        # fetch_pbs_by_ingredient 2차 fallback 과 동일 방식.
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
            try:
                payload = r2.json()
            except Exception:
                break
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                break
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            total = meta.get("total_records")
            for row in rows:
                if isinstance(row, dict) and _row_matches_ingredient(row, needles):
                    all_item_rows.append(row)
            if isinstance(total, int) and page * 100 >= total:
                break

        return all_item_rows, schedule
    except Exception:
        return [], None


# ─────────────────────────────────────────────────────────────────────
# PBS 웹 크롤링 fallback — FDC API 실패 시 pbs.gov.au 직접 크롤링
# 실측 HTML 구조 확인: 2026-04-24 (pbs_web_test.py 검증 완료)
# ─────────────────────────────────────────────────────────────────────

def _aemp_from_dpmq(dpmq: Decimal) -> Decimal | None:
    """DPMQ(최대처방량 총약가) → AEMP(승인 출고가) 역산.

    PBS 공식 (2024-25):
      DPMQ = AEMP × (1 + 도매마진%) + 조제료
      AEMP = (DPMQ - 조제료) ÷ (1 + 도매마진%)

    기준값:
      조제료  = $8.32 AUD (_PBS_WEB_DISPENSING_FEE)
      도매마진 = 7.52%   (_PBS_WEB_WHOLESALE_MARKUP, Wholesale Markup Band 1)

    ※ 실제 AEMP와 오차 ±5% 내외. API 값 없을 때 추정 전용.
    """
    if not dpmq or dpmq <= 0:
        return None
    net = dpmq - _PBS_WEB_DISPENSING_FEE
    if net <= 0:
        return None
    return (net / (1 + _PBS_WEB_WHOLESALE_MARKUP)).quantize(Decimal("0.01"))


def _strength_matches_desc(strength_str: str, description: str) -> bool:
    """자사 함량 문자열과 PBS 품목 설명 텍스트가 일치하는지 확인.

    예) "250/50" → 숫자 ['250', '50'] 추출 → 설명에 단어 경계로 모두 포함?
        "fluticasone 250 microgram + salmeterol 50 microgram" → True  ✓
        "fluticasone 250 microgram + salmeterol 25 microgram" → False ✓
           (단순 in 연산자는 '50' ⊂ '250' 오매칭 → re.search 단어경계로 해결)
    """
    if not strength_str or not description:
        return False
    nums = re.findall(r"\d+", strength_str)
    desc_l = description.lower()
    # \b 단어 경계: "50"이 "250" 안에 포함되는 오매칭 방지
    return all(bool(re.search(r"\b" + n + r"\b", desc_l)) for n in nums)


def _parse_pbs_web_item_detail(codes_str: str) -> dict[str, Any]:
    """PBS 품목 상세 페이지 크롤링 → DPMQ·브랜드 추출.

    codes_str: 단일 코드("14449L") 또는 듀얼코드("14449L-8431R") 모두 허용.
    첫 번째 코드(= 2팩 기준, 제네릭 기준가)의 DPMQ를 추출.

    실측 테이블 구조 (2026-04-24 확인):
      테이블0: Source(스케줄명) / Body System
      테이블1: 급여 유형 (Restricted Benefit / Authority Required 등)
      테이블2+: 가격 테이블 — 헤더: Code & Prescriber | 품목명 | ... | DPMQ | ... | Patient Charge
                데이터행 → "Available brands" → 브랜드 행들 순으로 구성.

    반환 키:
      pbs_code          : 첫 번째 PBS 품목 코드 (주 코드)
      drug_name         : 성분명 + 제형·함량 전체 텍스트
      dpmq_aud          : Decimal | None  ← 제네릭 기준 DPMQ
      patient_charge_aud: Decimal | None  ← 일반 환자 본인부담금
      available_brands  : 브랜드명 리스트 (오리지널 포함)
      source_url        : 원본 URL
    """
    url = f"{_PBS_WEB_ITEM_BASE}/{codes_str}"
    result: dict[str, Any] = {
        "pbs_code": codes_str.split("-")[0].upper(),
        "drug_name": None,
        "dpmq_aud": None,
        "patient_charge_aud": None,
        "available_brands": [],
        "source_url": url,
    }
    try:
        time.sleep(_PBS_WEB_RATE_SEC)
        r = httpx.get(url, headers=_PBS_WEB_HEADERS, timeout=15)
        if r.status_code != 200:
            return result
    except Exception:
        return result

    soup = BeautifulSoup(r.text, "lxml")

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = [
            th.get_text(separator=" ", strip=True)
            for th in rows[0].find_all(["th", "td"])
        ]
        if "DPMQ" not in " ".join(header_cells):
            continue  # 가격 테이블 아님 → 스킵

        headers_l  = [h.lower() for h in header_cells]
        idx_name   = next((i for i, h in enumerate(headers_l) if "medicinal" in h or "product" in h), None)
        idx_dpmq   = next((i for i, h in enumerate(headers_l) if "dpmq" in h), None)
        idx_charge = next((i for i, h in enumerate(headers_l) if "patient charge" in h or "general patient" in h), None)

        primary_found = False
        brands: list[str] = []
        in_brands = False

        for row in rows[1:]:
            cells = [td.get_text(separator=" ", strip=True) for td in row.find_all(["td", "th"])]
            if not cells or not any(cells):
                continue
            first = cells[0].strip()

            # "Available brands" 섹션 진입
            if "available brands" in first.lower():
                in_brands = True
                continue

            # 브랜드 행 수집
            if in_brands:
                brand = re.sub(r"\s*\*.*$", "", first).strip()  # "* Additional charge..." 제거
                brand = re.sub(r"\s+[a-z]\s*$", "", brand).strip()  # 각주 문자 제거
                if brand:
                    brands.append(brand)
                continue

            # PBS 코드 포함 행 = 데이터 행 (예: "14449L MP NP")
            if not re.match(r"[A-Z0-9]{5}", first):
                continue

            # 첫 번째 데이터 행만 사용 (2팩 기준, 제네릭 기준가)
            if not primary_found:
                primary_found = True
                if idx_name is not None and idx_name < len(cells):
                    name = re.sub(r"\(\s*PI\s*,?\s*CMI\s*\)", "", cells[idx_name]).strip()
                    result["drug_name"] = name
                if idx_dpmq is not None and idx_dpmq < len(cells):
                    m = re.search(r"\$(\d+\.\d+)", cells[idx_dpmq])
                    if m:
                        result["dpmq_aud"] = _safe_decimal(m.group(1))
                if idx_charge is not None and idx_charge < len(cells):
                    m = re.search(r"\$(\d+\.\d+)", cells[idx_charge])
                    if m:
                        result["patient_charge_aud"] = _safe_decimal(m.group(1))

        result["available_brands"] = brands
        break  # 첫 번째 DPMQ 테이블만 처리

    return result


def _fetch_ingredient_web(ingredient: str) -> dict[str, Any] | None:
    """fetch_pbs_by_ingredient() API 3단계 모두 실패 시 PBS 웹 크롤링 fallback.

    단일 성분(rosuvastatin, atorvastatin 등) 전용.
    FDC 복합제는 _fetch_fdc_web_fallback() 사용.

    전략:
      1. search-type=medicines 로 PBS 검색 → 성분 포함 링크 수집
      2. 첫 번째 매칭 코드(단일/듀얼 모두 허용)로 상세 페이지 크롤링
      3. DPMQ 추출 → AEMP 역산
      4. PBSItemDTO 호환 dict 반환

    실적 데이터 (2026-04-24 확인):
      rosuvastatin 5mg  (13406N): DPMQ $19.22 → AEMP ~$10.14
      rosuvastatin 10mg (13586C): DPMQ $21.32 → AEMP ~$12.09
      atorvastatin 10mg (13495G): DPMQ $19.22 → AEMP ~$10.14
      omega-3-acid ethyl esters : PBS 미등재 → None 반환 (Healthylife fallback 유지)

    반환: PBSItemDTO 호환 dict | None (크롤링 실패 or 미등재 시 None).
    """
    ing = (ingredient or "").strip()
    if not ing:
        return None

    needles = _pbs_needles(ing)
    if not needles:
        return None

    # Step 1: PBS 웹 검색
    try:
        time.sleep(_PBS_WEB_RATE_SEC)
        r = httpx.get(
            _PBS_WEB_SEARCH_URL,
            params={"term": ing, "search-type": "medicines"},
            headers=_PBS_WEB_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, "lxml")

    # Step 2: 성분 포함 첫 번째 링크 선택 (단일코드·듀얼코드 모두 허용)
    # 단, FDC 제외: 두 성분 이상의 복합 설명 링크는 스킵 ('+' 기호 있는 경우)
    item_re = re.compile(
        r"/medicine/item/([A-Z0-9]{4,6}(?:-[A-Z0-9]{4,6})?)",
        re.IGNORECASE,
    )
    # 후보 수집 (최대 20개) → 저함량 우선 정렬 → 첫 번째 선택
    # 이유: search-type=medicines 결과가 고함량(80mg) 부터 나올 수 있음
    # → mg/mcg 수치를 추출해 오름차순 정렬 → 저함량(10mg) 우선 채택
    candidates_for_sort: list[dict[str, Any]] = []

    for a_tag in soup.find_all("a", href=item_re):
        href = a_tag.get("href", "")
        m = item_re.search(href)
        if not m:
            continue
        codes_str = m.group(1).upper()
        desc = a_tag.get_text(separator=" ", strip=True).lower()

        # 성분 포함 확인
        if not any(n in desc for n in needles):
            continue

        # FDC 제외: PBS 복합제 표기 두 가지 모두 차단
        # 1) "EZETIMIBE (&) ROSUVASTATIN" — PBS 공식 FDC 표기
        # 2) "fluticasone + salmeterol"    — 성분 나열 표기
        if "(&)" in desc:
            continue
        if "+" in desc and not all(
            any(n in part for n in needles)
            for part in desc.split("+")
        ):
            continue

        # 설명에서 첫 번째 mg/mcg 수치 추출 (정렬용)
        dose_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mg|mcg|microgram)", desc)
        dose_num = float(dose_m.group(1)) if dose_m else 9999.0

        candidates_for_sort.append({
            "codes_str": codes_str,
            "dose_num": dose_num,
        })

        if len(candidates_for_sort) >= 20:
            break  # 충분한 후보 수집

    if not candidates_for_sort:
        return None

    # 저함량 우선 정렬 (10mg < 80mg)
    candidates_for_sort.sort(key=lambda x: x["dose_num"])
    chosen_codes: str = candidates_for_sort[0]["codes_str"]

    if not chosen_codes:
        return None

    # Step 3: 아이템 상세 페이지 크롤링 → DPMQ 추출
    web_detail = _parse_pbs_web_item_detail(chosen_codes)
    dpmq = web_detail.get("dpmq_aud")
    if not isinstance(dpmq, Decimal) or dpmq <= 0:
        return None

    # Step 4: AEMP 역산
    aemp_est = _aemp_from_dpmq(dpmq)

    # Step 5: PBSItemDTO 호환 dict 구성
    dto = _empty_dto()
    dto["pbs_found"]             = True
    dto["pbs_code"]              = web_detail.get("pbs_code")
    dto["drug_name"]             = web_detail.get("drug_name")
    dto["aemp_aud"]              = aemp_est       # 추정값 (DPMQ 역산)
    dto["dpmq_aud"]              = dpmq           # 실측값
    dto["mn_pharmacy_price_aud"] = web_detail.get("patient_charge_aud")
    dto["source_url"]            = web_detail.get("source_url") or _PBS_WEB_ITEM_BASE
    dto["source_name"]           = "pbs_web_fallback"
    dto["crawled_at"]            = now_kst_iso()
    dto["sponsors"]              = web_detail.get("available_brands") or []
    dto["_source"]               = "pbs_web_fallback"
    dto["_aemp_estimated"]       = True
    dto["confidence_override"]   = 0.6
    dto["warnings"] = [
        f"aemp_estimated_from_dpmq:{float(dpmq):.2f}",
        f"dispensing_fee_assumed:{float(_PBS_WEB_DISPENSING_FEE)}",
        f"wholesale_markup_assumed:{float(_PBS_WEB_WHOLESALE_MARKUP) * 100:.2f}pct",
    ]
    return dto


def _fetch_fdc_web_fallback(
    components: list[str],
    strengths: list[str] | None = None,
    fdc_search_term: str | None = None,
) -> dict[str, Any] | None:
    """fetch_pbs_fdc() API 스캔 실패 시 pbs.gov.au 웹 크롤링 fallback.

    전략:
      1. search-type=medicines 파라미터로 성분 검색
         → FDC 듀얼코드 링크 수집 (예: /medicine/item/14449L-8431R)
      2. 성분 INN 포함 확인 (모든 components 가 설명에 존재)
      3. strengths 매칭 → 자사 함량에 맞는 코드 선택
         (예: "250/50" → 14449L-8431R, "500/50" → 14450M-8432T)
      4. 아이템 상세 페이지 크롤링 → DPMQ 실측값 추출
      5. AEMP 역산: (DPMQ - 조제료 $8.32) ÷ (1 + 도매마진 7.52%)
      6. PBSItemDTO 호환 dict 반환

    반환: PBSItemDTO 호환 dict | None (크롤링 실패 시 None → 호출부에서 component_sum).

    태그:
      _source            : "pbs_web_fallback"
      _aemp_estimated    : True  (AEMP는 DPMQ 역산 추정값)
      confidence_override: 0.6
      pricing_case_applied: "DIRECT_FDC_web_fallback"
    """
    anchor = fdc_search_term or (components[0] if components else "")
    if not anchor:
        return None

    # Step 1: PBS 웹 검색 (search-type=medicines)
    try:
        time.sleep(_PBS_WEB_RATE_SEC)
        r = httpx.get(
            _PBS_WEB_SEARCH_URL,
            params={"term": anchor, "search-type": "medicines"},
            headers=_PBS_WEB_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, "lxml")

    # Step 2: 듀얼코드 링크 수집 — FDC 개별 함량 링크는 코드 정확히 2개 (dash 1개)
    # 예) /medicine/item/14449L-8431R  ← 대상 (dash 1개, 2팩+1팩 쌍)
    # 제외) /medicine/item/14311F-14413N-14414P-...  ← 헤딩 mega-link (dash 多, 전체 약품군)
    dual_re = re.compile(
        r"/medicine/item/([A-Z0-9]{4,6}-[A-Z0-9]{4,6})",
        re.IGNORECASE,
    )
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a_tag in soup.find_all("a", href=dual_re):
        href = a_tag.get("href", "")
        m = dual_re.search(href)
        if not m:
            continue
        codes_str = m.group(1).upper()
        # mega-link 완전 차단 — captured group은 regex 특성상 항상 dash 1개라
        # group 만 보면 의미 없음. href 전체 경로의 dash 개수로 판별해야 정확.
        # 예) /medicine/item/14311F-14413N-14414P-... → path에 dash 2개 이상 → skip
        _item_path = href.split("/medicine/item/")[-1].split("?")[0].split("#")[0].upper()
        if _item_path.count("-") != 1:
            continue
        if codes_str in seen:
            continue
        seen.add(codes_str)

        desc = a_tag.get_text(separator=" ", strip=True).lower()

        # 모든 components INN이 설명에 포함돼야 FDC 후보
        comps_present = all(
            any(needle in desc for needle in _pbs_needles(c))
            for c in components
            if c
        )
        if not comps_present:
            continue

        candidates.append({"codes_str": codes_str, "desc": desc})

    if not candidates:
        return None

    # Step 3: strengths 매칭 → 자사 함량에 맞는 코드 우선 선택
    chosen: str | None = None
    if strengths:
        for strength in strengths:
            for cand in candidates:
                if _strength_matches_desc(strength, cand["desc"]):
                    chosen = cand["codes_str"]
                    break
            if chosen:
                break

    if not chosen:
        chosen = candidates[0]["codes_str"]  # 함량 매칭 실패 → 첫 번째 후보

    # Step 4: 아이템 상세 페이지 크롤링 → DPMQ 실측
    web_detail = _parse_pbs_web_item_detail(chosen)
    dpmq = web_detail.get("dpmq_aud")
    if not isinstance(dpmq, Decimal) or dpmq <= 0:
        return None

    # Step 5: AEMP 역산
    aemp_est = _aemp_from_dpmq(dpmq)

    # Step 6: PBSItemDTO 호환 dict 구성
    dto = _empty_dto()
    dto["pbs_found"]               = True
    dto["pbs_code"]                = web_detail.get("pbs_code")
    dto["drug_name"]               = web_detail.get("drug_name")
    dto["aemp_aud"]                = aemp_est       # 추정값 (DPMQ 역산)
    dto["dpmq_aud"]                = dpmq           # 실측값
    dto["mn_pharmacy_price_aud"]   = web_detail.get("patient_charge_aud")
    dto["source_url"]              = web_detail.get("source_url") or _PBS_WEB_ITEM_BASE
    dto["source_name"]             = "pbs_web_fallback"
    dto["crawled_at"]              = now_kst_iso()
    # 브랜드 목록 (바이어 후보 풀 겸용)
    brand_list: list[str] = web_detail.get("available_brands") or []
    dto["sponsors"]   = brand_list
    dto["pbs_brands"] = [
        {
            "brand_name":    b,
            "dpmq_aud":      None,
            "originator_brand": None,
            "pbs_code":      dto["pbs_code"],
        }
        for b in brand_list
    ]
    # 추정값 메타 태그
    dto["_source"]           = "pbs_web_fallback"
    dto["_aemp_estimated"]   = True   # AEMP는 DPMQ 역산값 (실측 아님)
    dto["confidence_override"]      = 0.6
    dto["pricing_case_applied"]     = "DIRECT_FDC_web_fallback"
    dto["warnings"] = [
        f"aemp_estimated_from_dpmq:{float(dpmq):.2f}",
        f"dispensing_fee_assumed:{float(_PBS_WEB_DISPENSING_FEE)}",
        f"wholesale_markup_assumed:{float(_PBS_WEB_WHOLESALE_MARKUP) * 100:.2f}pct",
    ]
    return dto


def fetch_pbs_fdc(
    components: list[str],
    fdc_search_term: str | None = None,
    *,
    strengths: list[str] | None = None,
) -> dict[str, Any]:
    """Case 1 DIRECT — 복합제가 PBS 에 FDC (Fixed-Dose Combination, 고정용량복합제)
    한 줄로 등재된 경우.

    Phase Sereterol v2 수정 (2026-04-22):
      기존 fetch_pbs_by_ingredient(anchor, _return_all=True) 는 primary_matched 에
      단일성분(Serevent 등) 항목이 있으면 조기 반환 → 알파벳 뒤쪽 페이지에 있는
      FDC(예: fluticasone+salmeterol) 를 영구적으로 놓침.
      → _fdc_raw_page_scan(anchor) 로 교체:
         drug_name=anchor 로 _MAX_FDC_PAGES 전 페이지 스캔 → raw rows 전부 수집 →
         set-equality 필터 후 1건만 dispensing rule 조인 (API 호출 최소화).

    전략:
      1) anchor = fdc_search_term or components[0] 으로 PBS 전 페이지 스캔.
         (Sereterol: anchor="fluticasone" → "fluticasone propionate/salmeterol xinafoate"
          항목들이 "fluticasone propionate/formoterol fumarate" 보다 알파벳 뒤쪽 페이지에
          있어도 전부 수집)
      2) Quick DTO (dispensing rule 조인 없이) → base INN set-equality 필터.
         정확 일치 행만 통과.
      3) 0 건 → fetch_pbs_component_sum() fallback + flag 기록.
      4) 다 건 중 선택:
         (a) 자사 함량(strengths) 와 market_strength 비교 우선
         (b) AEMP 최저가 우선 (dispensing rule 없이 items.determined_price 기준)
      5) 선택된 1건에 대해서만 dispensing rule 조인.
      6) originator_brand_name / originator_sponsor 식별 —
         matched quick DTOs 에서 innovator_indicator='Y' 행 정보 주입.

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
    # FDC 전용 전체 페이지 스캔 — raw item rows 수집 (dispensing rule 조인 없음).
    # _return_all=True 인 fetch_pbs_by_ingredient 는 primary_matched 가 있으면
    # 조기 반환 → 알파벳 뒤에 있는 FDC 를 놓침. _fdc_raw_page_scan 이 이를 해결.
    raw_rows, schedule = _fdc_raw_page_scan(anchor)

    # Quick DTO (dispensing rule 조인 없이) — set-equality 필터용
    quick_dtos: list[dict[str, Any]] = []
    if raw_rows and schedule:
        quick_dtos = [_row_to_dto(r, None, schedule_code=schedule) for r in raw_rows]

    # base INN set-equality 필터
    matched_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = [
        (raw_rows[i], quick_dtos[i])
        for i in range(len(quick_dtos))
        if _dto_inn_set(quick_dtos[i]) == expected_inns
    ]
    if not matched_pairs:
        # 1차 fallback: 웹 크롤링 — API FDC 검색 실패 시 pbs.gov.au 직접 크롤링
        # (세레테롤처럼 API drug_name 검색이 FDC를 못 잡는 케이스 대응)
        _web = _fetch_fdc_web_fallback(
            components,
            strengths=list(strengths) if strengths else None,
            fdc_search_term=fdc_search_term,
        )
        if _web is not None:
            return _web
        # 2차 fallback: component_sum (웹 크롤링도 실패 시)
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
        for raw, quick in matched_pairs:
            cand = _normalize_strength_tok(
                quick.get("market_strength")
                or quick.get("strength")
                or ""
            )
            # 부분 포함 매칭 — "250/50" in "250/50" 또는 거꾸로
            if cand and any(w and (w in cand or cand in w) for w in wanted):
                preferred.append((raw, quick))
        if preferred:
            matched_pairs = preferred

    # (b) AEMP 최저로 best 선택 (dispensing rule 없이 items.determined_price 기준)
    def _aemp_sort(pair: tuple[dict[str, Any], dict[str, Any]]) -> Decimal:
        _, q = pair
        v = q.get("aemp_aud")
        d = _safe_decimal(v) if v is not None else None
        return d if d is not None else Decimal("999999")

    best_raw, _best_quick = min(matched_pairs, key=_aemp_sort)

    # 선택된 1건에 대해서만 dispensing rule 조인
    best = dict(_join_dispensing_rule(best_raw, schedule))
    best["pricing_case_applied"] = "DIRECT_FDC"
    best["_fdc_match_count"] = len(matched_pairs)

    # pbs_brands / competitor_brands — quick DTOs 에서 구성 (dispensing rule 없이)
    total_brands = len({q.get("brand_name") for _, q in matched_pairs if q.get("brand_name")})
    best["pbs_total_brands"] = total_brands
    best["pbs_brands"] = [
        {
            "brand_name": q.get("brand_name"),
            "aemp_aud": q.get("aemp_aud"),
            "dpmq_aud": q.get("dpmq_aud"),
            "originator_brand": q.get("originator_brand"),
            "pbs_code": q.get("pbs_code"),
            "manufacturer_code": q.get("manufacturer_code"),
            "brand_premium_aud": q.get("brand_premium_aud"),
        }
        for _, q in matched_pairs
    ]
    best["competitor_brands"] = [
        b for b in best["pbs_brands"]
        if not (
            b.get("pbs_code") == best.get("pbs_code")
            and b.get("brand_name") == best.get("brand_name")
        )
    ]

    # originator 식별 — matched quick DTOs 에서 innovator_indicator='Y' 행 정보 주입
    best = _attach_originator_info(best, [q for _, q in matched_pairs])

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
    result = fetch_pbs_by_ingredient("hydroxycarbamide")
    for dto in result:
        # Decimal 은 json.dumps 불가 → 간이 출력
        print({k: (str(v) if isinstance(v, Decimal) else v) for k, v in dto.items() if k != "raw_response"})
