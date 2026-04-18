# 호주 의약품 크롤링 파이프라인 v2 — au_products 컬럼(§14-3-1) 직접 매핑.
#
# 위임지서 03a 구현:
#   §2-1 build_product_summary() 가 v2 컬럼으로 직접 dict 생성 (rename 매핑은 백업 방어용만 유지)
#   §2-3 utils/fx.py 로 AUD → USD/KRW 환산 즉시 주입
#   §2-6 §13-7-B 바이어 후보 풀 자동 수집 (TGA + PBS + buyNSW sponsors → au_buyers)
#   §2-7 PGRST204 방지 — 모든 upsert 직전에 _ALLOWED_COLUMNS 화이트리스트 필터
#
# 사용자 보완:
#   [결정 2] 금융 값은 Decimal 내부 유지, supabase_insert._jsonify_decimals 가 str 변환
#   [결정 3] case_code 는 payload dict 에 넣지 않음 — 기존 DB 값 보존 (빈 값 덮어쓰기 금지)
#   [결정 4] 중간안 — /items + /item-dispensing-rule-relationships 만 호출.
#           NULL 컬럼 목록은 사용자 보고용 (pbs.py TODO(v2-pbs-full) 주석 참조)
#
# 기존 _estimate_retail_price() 로직 유지 (§1-6 명시).

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

from sources.chemist import build_sites
from sources.tga import determine_export_viable
from utils.evidence import build_evidence_text
from utils.fx import aud_to_krw, aud_to_usd
from utils.scoring import AU_REQUIRED_FIELDS, completeness_score

_CRAWLER_DIR = Path(__file__).resolve().parent


# ─────────────────────────────────────────────────────────────────────
# 환경 설정 — 소매가 추정 배수 (기존 로직 유지, §1-6)
# ─────────────────────────────────────────────────────────────────────

def _retail_markup_multiplier() -> Decimal:
    """Chemist Warehouse × X — 일반 약국 평균 소매가 추정 배수 (CHOICE 조사 기준).

    금융 정밀도 위해 Decimal. 환경변수 RETAIL_MARKUP_MULTIPLIER 로 덮어쓰기 가능.
    """
    raw = (os.environ.get("RETAIL_MARKUP_MULTIPLIER") or "1.20").strip()
    try:
        v = Decimal(raw)
        return v if v > Decimal("0") else Decimal("1.20")
    except (InvalidOperation, ValueError):
        return Decimal("1.20")


RETAIL_MARKUP_MULTIPLIER = _retail_markup_multiplier()


def _to_decimal(v: Any) -> Decimal | None:
    """Any → Decimal | None. 파싱 실패 시 None."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# 헬퍼 — 소매가 추정 (기존 로직 유지, Decimal 으로 업그레이드)
# ─────────────────────────────────────────────────────────────────────

def _chemist_retail_trustworthy(
    chemist: dict[str, Any],
    pbs_price: Any,
) -> tuple[Decimal | None, bool]:
    """Chemist 가격이 소매 정상가로 볼 수 있을 때만 True.

    v2 ChemistDTO 의 `price_aud` 키 + 하위호환 `retail_price_aud` 둘 다 수용.
    저가(<$5)·오매칭(PBS 가격의 15% 미만) 배제.
    """
    raw = chemist.get("price_aud") if chemist.get("price_aud") is not None else chemist.get("retail_price_aud")
    r = _to_decimal(raw)
    if r is None or r <= Decimal("0"):
        return None, False
    if r < Decimal("5.0"):
        return None, False
    pbs_dec = _to_decimal(pbs_price)
    if pbs_dec is not None and pbs_dec > 0:
        if r < pbs_dec * Decimal("0.15"):
            return None, False
    return r, True


def _estimate_retail_price(
    pbs: dict[str, Any],
    chemist_price_aud: Decimal | None,
) -> tuple[Decimal | None, str | None]:
    """시장 추정 소매가 — §1-6 "기존 로직 유지" 명시. Decimal 반환.

    v2 DTO 키 우선, v1 키 fallback:
      pbs_found / pbs_listed   — 등재 여부
      dpmq_aud / pbs_dpmq      — DPMQ (우선 사용)
      aemp_aud / pbs_price_aud — AEMP (보조)

    우선순위:
      1) PBS 등재 + dpmq_aud > 0 → DPMQ 그대로 (method = 'pbs_dpmq')
      2) Chemist 신뢰 가격 → chemist × RETAIL_MARKUP_MULTIPLIER (method = 'chemist_markup')
      3) aemp_aud 로 fallback (method = 'pbs_dpmq')
      4) 모두 없음 → (None, None)
    """
    pbs_found = pbs.get("pbs_found")
    if pbs_found is None:
        pbs_found = bool(pbs.get("pbs_listed"))

    dpmq = _to_decimal(pbs.get("dpmq_aud") if pbs.get("dpmq_aud") is not None else pbs.get("pbs_dpmq"))
    aemp = _to_decimal(pbs.get("aemp_aud") if pbs.get("aemp_aud") is not None else pbs.get("pbs_price_aud"))

    if pbs_found and dpmq is not None and dpmq > Decimal("0"):
        return dpmq.quantize(Decimal("0.01")), "pbs_dpmq"
    if chemist_price_aud is not None:
        estimated = chemist_price_aud * RETAIL_MARKUP_MULTIPLIER
        return estimated.quantize(Decimal("0.01")), "chemist_markup"
    if aemp is not None and aemp > Decimal("0"):
        return aemp.quantize(Decimal("0.01")), "pbs_dpmq"
    return None, None


def _tga_schedule_s2348_only(raw: object) -> str | None:
    """tga_schedule 컬럼에는 S2/S3/S4/S8 만 저장."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if s in ("S2", "S3", "S4", "S8") else None


def _raw_evidence_text(
    pbs: dict[str, Any],
    tga: dict[str, Any],
    nsw: dict[str, Any],
) -> str:
    """PBS·TGA·조달 텍스트를 이어 붙여 근거 원문으로 쓴다.

    v2 DTO · v1 하위호환 키 양쪽 수용.
    """
    parts: list[str] = []
    rt = pbs.get("restriction_text")
    if isinstance(rt, str) and rt.strip():
        parts.append(rt.strip())
    st = tga.get("tga_schedule") or tga.get("schedule_code")
    if st:
        parts.append(f"Schedule: {st}")
    sp = tga.get("tga_sponsor") or tga.get("sponsor_name")
    if sp:
        parts.append(f"Sponsor: {sp}")
    ast = tga.get("artg_status") or tga.get("status")
    if ast:
        parts.append(f"ARTG status: {ast}")
    sup = nsw.get("agency") or nsw.get("awarded_to") or nsw.get("supplier_name")
    if sup:
        parts.append(f"Procurement supplier: {sup}")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
# 바이어 후보 풀 수집 (§13-7-B, §2-6)
# ─────────────────────────────────────────────────────────────────────

def _normalize_company_name(name: str | None) -> str:
    """회사명 정규화 — 공백 정리. "Apotex Pty Ltd" → "Apotex" 같은 suffix 제거는
    향후 위임(Haiku PSI 계산 전) 에서 강화.
    """
    if not name:
        return ""
    return " ".join(str(name).split()).strip()


def _collect_buyer_candidates(
    product_id: str,
    tga: dict[str, Any],
    pbs: dict[str, Any],
    nsw: dict[str, Any],
) -> list[dict[str, Any]]:
    """§13-7-B 바이어 후보 풀 자동 수집.

    한 품목 크롤 완료 후 TGA sponsor / PBS sponsor / buyNSW supplier 회사명 추출,
    회사명 기준 중복 병합 (같은 회사 여러 소스에서 나오면 source_flags 병합),
    au_buyers INSERT 용 candidates 리스트 반환.

    PSI 점수는 범위 밖 (Haiku 가 나중에 계산 후 UPDATE).
    """
    buckets: dict[str, dict[str, Any]] = {}  # key = company_name 대문자

    def _add(name: str | None, flag: str) -> None:
        norm = _normalize_company_name(name)
        if not norm:
            return
        key = norm.upper()
        if key not in buckets:
            buckets[key] = {
                "product_id": product_id,
                "company_name": norm,
                "abn": None,
                "source_flags": {},
                "evidence_urls": [],
                # PSI 는 이번 위임 밖 — NULL 진입
                "rank": None,
                "psi_sales_scale": None,
                "psi_pipeline": None,
                "psi_manufacturing": None,
                "psi_import_exp": None,
                "psi_pharmacy_chain": None,
                "psi_total": None,
                "state": None,
            }
        buckets[key]["source_flags"][flag] = True

    # TGA 복수 sponsors (v2 JSONB array) + 단일 sponsor (하위호환)
    for sp in (tga.get("tga_sponsors") or []):
        _add(sp, "tga")
    _add(tga.get("tga_sponsor") or tga.get("sponsor_name"), "tga")

    # PBS sponsors (현재는 manufacturer_code 정도 — §13-7-B)
    for sp in (pbs.get("sponsors") or []):
        _add(sp, "pbs")

    # buyNSW 낙찰사 (awarded_to) 또는 발주처(agency) — 현재 파싱은 agency 까지만
    _add(nsw.get("awarded_to"), "nsw")
    _add(nsw.get("agency") or nsw.get("supplier_name"), "nsw")

    return list(buckets.values())


# ─────────────────────────────────────────────────────────────────────
# build_product_summary — v2 컬럼으로 직접 매핑
# ─────────────────────────────────────────────────────────────────────

def build_product_summary(
    product: dict[str, Any],
    pbs: dict[str, Any] | None,
    tga: dict[str, Any] | None,
    chemist: dict[str, Any] | None,
    nsw: dict[str, Any] | None,
) -> dict[str, Any]:
    """각 소스 DTO 를 au_products 스키마(§14-3-1) 에 맞는 단일 dict 로 병합.

    v2 컬럼명 기준. 하위호환 v1 키도 일부 포함 (scoring · rename fallback 방어).
    case_code 는 반환 dict 에서 제외 (결정 3 보완).

    supabase_insert.upsert_product() 가 _ALLOWED_COLUMNS 화이트리스트로 필터하므로
    알 수 없는 키는 자동 드롭 (PGRST204 방지).
    """
    data_source_count = sum(1 for x in (pbs, tga, chemist, nsw) if x is not None)
    pbs = pbs or {}
    tga = tga or {}
    chemist = chemist or {}
    nsw = nsw or {}

    # TGA 스케줄 정규화
    tga_sched = _tga_schedule_s2348_only(tga.get("tga_schedule") or tga.get("schedule_code"))
    tga_norm = {**tga, "tga_schedule": tga_sched}

    # 수출 적합성 판정 — TGA 기반. PBS 등재면 무조건 viable 로 덮어쓰기.
    viable_result = determine_export_viable(tga_norm)
    pbs_found_flag = bool(pbs.get("pbs_found") if pbs.get("pbs_found") is not None else pbs.get("pbs_listed"))
    if pbs_found_flag:
        viable_result = {"export_viable": "viable", "reason_code": "PBS_REGISTERED"}

    inn = str(product.get("inn_normalized") or "")
    pricing_case = str(product.get("pricing_case") or "ESTIMATE")
    raw_text = _raw_evidence_text(pbs, tga_norm, nsw)
    evidence = build_evidence_text(pricing_case, raw_text, inn)

    # 소매가 추정 (Decimal 반환)
    pbs_price_for_trust = pbs.get("aemp_aud") if pbs.get("aemp_aud") is not None else pbs.get("pbs_price_aud")
    cr_trusted, chemist_ok = _chemist_retail_trustworthy(chemist, pbs_price_for_trust)
    chemist_price_aud: Decimal | None = cr_trusted

    retail_aud, retail_estimation_method = _estimate_retail_price(pbs, chemist_price_aud)

    # price_source_name / url (하위호환)
    if retail_estimation_method == "pbs_dpmq":
        price_name = "PBS"
        price_url = pbs.get("source_url") or pbs.get("pbs_source_url") or ""
    elif retail_estimation_method == "chemist_markup":
        price_name = "Chemist Warehouse"
        price_url = chemist.get("product_url") or chemist.get("price_source_url") or ""
    else:
        price_name = "PBS"
        price_url = (
            chemist.get("product_url") or chemist.get("price_source_url") or ""
        ) or (pbs.get("source_url") or pbs.get("pbs_source_url") or "")

    chemist_url_for_sites = (
        (chemist.get("product_url") or chemist.get("price_source_url") or "") if chemist_ok else ""
    )

    # completeness_score 계산용 assembled (v1 키 — scoring.py AU_REQUIRED_FIELDS 호환)
    assembled_for_score: dict[str, Any] = {
        "artg_number": tga.get("artg_number") or tga.get("artg_id"),
        "tga_schedule": tga_sched,
        "pbs_item_code": pbs.get("pbs_code") or pbs.get("pbs_item_code"),
        "retail_price_aud": retail_aud,
        "price_source_url": price_url,
        "export_viable": viable_result.get("export_viable"),
        "dosage_form": product.get("dosage_form"),
    }
    completeness_ratio = (
        len([f for f in AU_REQUIRED_FIELDS if assembled_for_score.get(f)])
        / len(AU_REQUIRED_FIELDS)
    )

    sites = build_sites(
        pbs.get("source_url") or pbs.get("pbs_source_url") or "",
        tga.get("artg_url") or tga.get("artg_source_url") or "",
        chemist_url_for_sites,
        nsw.get("source_url") or nsw.get("nsw_source_url") or "",
        pubmed_url=None,
    )

    # PBS 웹 보강 미완 감지
    error_type: str | None = None
    pbs_code_val = pbs.get("pbs_code") or pbs.get("pbs_item_code")
    if (
        pbs_code_val
        and pbs_found_flag
        and pbs.get("brand_name") is None
        and pbs.get("pbs_brand_name") is None
        and pbs.get("originator_brand") is None
        and pbs.get("pbs_innovator") is None
    ):
        error_type = "PBS_WEB_ENRICHMENT_INCOMPLETE"

    # FX 환산 (Decimal → Decimal)
    aemp_aud_raw = pbs.get("aemp_aud") if pbs.get("aemp_aud") is not None else pbs.get("pbs_price_aud")
    dpmq_aud_raw = pbs.get("dpmq_aud") if pbs.get("dpmq_aud") is not None else pbs.get("pbs_dpmq")
    aemp_aud = _to_decimal(aemp_aud_raw)
    dpmq_aud = _to_decimal(dpmq_aud_raw)

    # Warnings — 크롤 중 경고 수집 (지금은 빈 리스트, JSONB array)
    warnings: list[str] = []
    if error_type:
        warnings.append(error_type)

    out: dict[str, Any] = {
        # 식별자 — v2 컬럼 직접 + v1 (product_id) rename fallback 용
        "product_id": product["product_id"],     # _row_for_upsert → product_code
        "product_code": product["product_id"],   # v2 직접
        "market_segment": product["market_segment"],
        "product_name_ko": product["product_name_ko"],
        "inn_normalized": product["inn_normalized"],
        "hs_code_6": product["hs_code_6"],
        "dosage_form": product["dosage_form"],
        "strength": product["strength"],

        # TGA 블록 — v2 JSONB 배열 (§14-3-1)
        "tga_found": bool(
            tga.get("tga_found")
            if tga.get("tga_found") is not None
            else (tga.get("artg_status") or tga.get("status")) == "registered"
        ),
        "tga_artg_ids": tga.get("tga_artg_ids")
            or ([str(tga.get("artg_number"))] if tga.get("artg_number") else []),
        "tga_sponsors": tga.get("tga_sponsors")
            or ([tga.get("tga_sponsor")] if tga.get("tga_sponsor") else []),
        # TGA 하위호환
        "artg_number": tga.get("artg_number") or tga.get("artg_id"),
        "artg_status": tga.get("artg_status") or tga.get("status"),
        "tga_schedule": tga_sched,
        "tga_licence_category": tga.get("tga_licence_category"),
        "tga_licence_status": tga.get("tga_licence_status"),
        "tga_sponsor": tga.get("tga_sponsor") or tga.get("sponsor_name"),
        "artg_source_url": tga.get("artg_url") or tga.get("artg_source_url", ""),

        # PBS 블록 — v2 DTO (Decimal 값은 supabase_insert 에서 str 변환)
        "pbs_found": pbs_found_flag,
        "pbs_code": pbs_code_val,
        "program_code": pbs.get("program_code") or pbs.get("pbs_program_code"),
        "section_85_100": pbs.get("section_85_100"),
        "formulary": pbs.get("formulary") or pbs.get("pbs_formulary"),
        "pack_size": pbs.get("pack_size") or pbs.get("pbs_pack_size"),
        "pricing_quantity": pbs.get("pricing_quantity") or pbs.get("pbs_pricing_quantity"),
        "maximum_prescribable_pack": pbs.get("maximum_prescribable_pack"),
        "first_listed_date": pbs.get("first_listed_date") or pbs.get("pbs_first_listed_date"),
        "authority_method": pbs.get("authority_method"),
        "originator_brand": pbs.get("originator_brand"),
        "therapeutic_group_id": pbs.get("therapeutic_group_id"),
        "brand_substitution_group_id": pbs.get("brand_substitution_group_id"),
        "atc_code": pbs.get("atc_code"),                       # TODO(v2-pbs-full) NULL
        "policy_imdq60": pbs.get("policy_imdq60"),
        "policy_biosim": pbs.get("policy_biosim"),
        "section_19a_expiry": pbs.get("section_19a_expiry_date") or pbs.get("section_19a_expiry"),
        # 가격 (AUD 원본 + FX 환산)
        "aemp_aud": aemp_aud,
        "aemp_usd": aud_to_usd(aemp_aud),
        "aemp_krw": aud_to_krw(aemp_aud),
        "dpmq_aud": dpmq_aud,
        "dpmq_usd": aud_to_usd(dpmq_aud),
        "dpmq_krw": aud_to_krw(dpmq_aud),
        "spd_aud": _to_decimal(pbs.get("spd_aud")),
        "claimed_price_aud": _to_decimal(pbs.get("claimed_price_aud")),
        "mn_pharmacy_price_aud": _to_decimal(pbs.get("mn_pharmacy_price_aud")),
        "brand_premium_aud": _to_decimal(pbs.get("brand_premium_aud")),
        "therapeutic_group_premium_aud": _to_decimal(pbs.get("therapeutic_group_premium_aud")),
        "special_patient_contrib_aud": _to_decimal(pbs.get("special_patient_contrib_aud")),
        "wholesale_markup_band": pbs.get("wholesale_markup_band"),
        "pharmacy_markup_code": pbs.get("pharmacy_markup_code"),
        # TODO(v2-pbs-full): 아래 9 필드는 /fees /markup-bands /copayments /atc-relationships 엔드포인트 추가 시 채움
        "markup_variable_pct": _to_decimal(pbs.get("markup_variable_pct")),
        "markup_offset_aud": _to_decimal(pbs.get("markup_offset_aud")),
        "markup_fixed_aud": _to_decimal(pbs.get("markup_fixed_aud")),
        "dispensing_fee_aud": _to_decimal(pbs.get("dispensing_fee_aud")),
        "ahi_fee_aud": _to_decimal(pbs.get("ahi_fee_aud")),
        "copay_general_aud": _to_decimal(pbs.get("copay_general_aud")),
        "copay_concessional_aud": _to_decimal(pbs.get("copay_concessional_aud")),

        # 소매 가격 블록
        "retail_price_aud": retail_aud,
        "chemist_price_aud": chemist_price_aud,
        "retail_estimation_method": retail_estimation_method,
        "chemist_url": (chemist.get("product_url") or chemist.get("price_source_url")) if chemist_ok else None,

        # 내부 필드 — case_code 는 의도적 제외 (결정 3 보완)
        "ingredients_split": (
            {"components": product.get("inn_components", [])}
            if product.get("inn_components") else None
        ),
        "ai_deep_research_raw": None,    # AI 붙을 때 채움

        # 메타
        "last_crawled_at": datetime.now(timezone.utc).isoformat(),
        "crawled_at": datetime.now(timezone.utc).isoformat(),  # rename → last_crawled_at
        "crawler_source_urls": sites,
        "schedule_code": pbs.get("schedule_code"),
        "error_type": error_type,
        # NOT NULL DEFAULT '[]' 제약 — None 대신 빈 배열 (Hydrine dry-run 23502 위반 수정)
        "warnings": warnings,

        # 하위호환 v1 키 (_row_for_upsert rename 매핑 + scoring 용)
        "pbs_item_code": pbs_code_val,
        "pbs_price_aud": aemp_aud,
        "pbs_dpmq": dpmq_aud,
        "pbs_patient_charge": _to_decimal(pbs.get("pbs_patient_charge")),
        "pbs_determined_price": aemp_aud,
        "pbs_pack_size": pbs.get("pack_size") or pbs.get("pbs_pack_size"),
        "pbs_pricing_quantity": pbs.get("pricing_quantity") or pbs.get("pbs_pricing_quantity"),
        "pbs_benefit_type": pbs.get("benefit_type_code") or pbs.get("pbs_benefit_type"),
        "pbs_program_code": pbs.get("program_code") or pbs.get("pbs_program_code"),
        "pbs_brand_name": pbs.get("brand_name") or pbs.get("pbs_brand_name"),
        "pbs_innovator": (
            pbs.get("pbs_innovator")
            if pbs.get("pbs_innovator") is not None
            else ("Y" if pbs.get("originator_brand") is True else ("N" if pbs.get("originator_brand") is False else None))
        ),
        "pbs_first_listed_date": pbs.get("first_listed_date") or pbs.get("pbs_first_listed_date"),
        "pbs_repeats": pbs.get("number_of_repeats") or pbs.get("pbs_repeats"),
        "pbs_formulary": pbs.get("formulary") or pbs.get("pbs_formulary"),
        "pbs_restriction": pbs.get("pbs_restriction"),
        "pbs_total_brands": pbs.get("pbs_total_brands"),
        "pbs_brands": pbs.get("pbs_brands"),
        "pbs_listed": pbs_found_flag,
        "pbs_source_url": pbs.get("source_url") or pbs.get("pbs_source_url") or "",
        "pbs_web_source_url": pbs.get("pbs_web_source_url"),

        # NSW 블록 — v2 + 하위호환
        "nsw_contract_value_aud": _to_decimal(nsw.get("contract_value_aud")),
        "nsw_supplier_name": nsw.get("agency") or nsw.get("supplier_name"),
        "nsw_contract_date": nsw.get("start_date") or nsw.get("contract_date"),
        "nsw_source_url": nsw.get("source_url") or nsw.get("nsw_source_url"),
        "nsw_note": nsw.get("nsw_note"),

        # 가격 출처 (하위호환)
        "price_source_name": price_name,
        "price_source_url": price_url,
        "price_unit": chemist.get("price_unit", "per pack") if chemist_ok else "per pack",

        # 판정 (하위호환)
        "pricing_case": product["pricing_case"],
        "export_viable": viable_result.get("export_viable"),
        "reason_code": viable_result.get("reason_code"),

        # 증거 (하위호환)
        "evidence_url": tga.get("artg_url") or tga.get("artg_source_url", ""),
        "evidence_text": evidence.get("evidence_text", ""),
        "evidence_text_ko": evidence.get("evidence_text_ko", ""),
        "sites": sites,
        "completeness_ratio": completeness_ratio,
        "data_source_count": data_source_count,
        "confidence": completeness_score(assembled_for_score),

        # LLM 메타 (1공정에선 None)
        "block2_market": None,
        "block2_regulatory": None,
        "block2_trade": None,
        "block2_procurement": None,
        "block2_channel": None,
        "block3_channel": None,
        "block3_pricing": None,
        "block3_partners": None,
        "block3_risks": None,
        "perplexity_refs": None,
        "llm_model": None,
        "llm_generated_at": None,
    }

    # case_code 방어적 제거 — 혹시라도 상위에서 섞여 들어왔을 때 (결정 3 보완)
    out.pop("case_code", None)

    return out


# ─────────────────────────────────────────────────────────────────────
# 기존 유틸 — 품목 로드 + 복합 성분 병합
# ─────────────────────────────────────────────────────────────────────

def _load_products() -> list[dict[str, Any]]:
    path = _CRAWLER_DIR / "au_products.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("products", []))


def _merge_pbs_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """PBS 다행(복합 성분 등)을 요약용 단일 DTO 로 합친다. v2 DTO 기준."""
    if not rows:
        return {}
    if len(rows) == 1:
        return dict(rows[0])
    merged: dict[str, Any] = dict(rows[0])

    # pbs_found: any
    any_found = any(r.get("pbs_found") or r.get("pbs_listed") for r in rows)
    merged["pbs_found"] = any_found
    merged["pbs_listed"] = any_found

    # aemp_aud / dpmq_aud 합산 (Decimal)
    def _sum_dec(key_primary: str, key_legacy: str) -> Decimal | None:
        total: Decimal | None = None
        for r in rows:
            v = r.get(key_primary) if r.get(key_primary) is not None else r.get(key_legacy)
            d = _to_decimal(v)
            if d is not None:
                total = d if total is None else total + d
        return total

    aemp_sum = _sum_dec("aemp_aud", "pbs_price_aud")
    if aemp_sum is not None:
        merged["aemp_aud"] = aemp_sum
        merged["pbs_price_aud"] = aemp_sum
    dpmq_sum = _sum_dec("dpmq_aud", "pbs_dpmq")
    if dpmq_sum is not None:
        merged["dpmq_aud"] = dpmq_sum
        merged["pbs_dpmq"] = dpmq_sum

    # pbs_code 합치기
    codes: list[str] = []
    for r in rows:
        c = r.get("pbs_code") or r.get("pbs_item_code")
        if c is not None:
            codes.append(str(c))
    if codes:
        merged["pbs_code"] = "+".join(codes)
        merged["pbs_item_code"] = merged["pbs_code"]

    # sponsors 합치기 (바이어 후보 풀용)
    all_sponsors: list[str] = []
    for r in rows:
        for s in (r.get("sponsors") or []):
            if s and s not in all_sponsors:
                all_sponsors.append(s)
    merged["sponsors"] = all_sponsors

    # restriction_text 합치기
    restrs = [r.get("restriction_text") for r in rows if r.get("restriction_text")]
    if restrs:
        merged["restriction_text"] = " | ".join(str(x) for x in restrs)

    return merged


# ─────────────────────────────────────────────────────────────────────
# main — PRODUCT_FILTER 1개 품목 크롤 + upsert + 바이어 풀 + 로그
# ─────────────────────────────────────────────────────────────────────

def _process_one_product(product: dict[str, Any], *, dry_run: bool = False) -> bool:
    """단일 품목 크롤 + upsert. 성공 True, 실패 False. dry_run 이면 DB 쓰기 skip.

    v2 확장:
      - run_id (uuid4) 생성 → 한 배치에서 모든 log_crawl 호출 공유
      - 각 소스 호출 전후 log_crawl
      - PBS 성공 시 upsert_pbs_raw, TGA 성공 시 upsert_tga_artg (원본 보관)
      - 최종 바이어 후보 풀 수집 → upsert_buyer_candidates
    """
    product_filter = product["product_id"]

    # Lazy import (크롤러만 돌릴 때 supabase-py 로드 최소화)
    from db.supabase_insert import (
        insert_crawl_log,
        log_crawl,
        upsert_buyer_candidates,
        upsert_pbs_raw,
        upsert_product,
        upsert_tga_artg,
    )
    from sources.buynsw import fetch_buynsw
    from sources.chemist import fetch_chemist_price
    from sources.pbs import fetch_pbs_by_ingredient, fetch_pbs_multi, fetch_pbs_web
    from sources.tga import fetch_tga_artg

    run_id = str(uuid.uuid4())
    print(f"[run] product_id={product_filter} run_id={run_id}", flush=True)

    # ── TGA ──────────────────────────────────────────────────
    tga_terms = product.get("tga_search_terms") or []
    tga_query = str(tga_terms[0] if tga_terms else product.get("inn_normalized") or "")
    _t0 = time.time()
    try:
        tga = fetch_tga_artg(tga_query)
        determine_export_viable(tga)
        log_crawl(
            run_id=run_id, product_code=product_filter, source="tga", status="success",
            endpoint="/resources/artg",
            duration_ms=int((time.time() - _t0) * 1000),
        )
    except Exception as exc:
        tga = {}
        log_crawl(
            run_id=run_id, product_code=product_filter, source="tga", status="failed",
            endpoint="/resources/artg",
            error_message=str(exc)[:500],
            duration_ms=int((time.time() - _t0) * 1000),
        )

    # ── PBS ──────────────────────────────────────────────────
    components = [str(c) for c in (product.get("inn_components") or []) if c]
    if not components:
        components = [str(product.get("inn_normalized") or "")]

    _t0 = time.time()
    try:
        if len(components) > 1:
            pbs_rows = fetch_pbs_multi(components)
        else:
            pbs_rows = fetch_pbs_by_ingredient(components[0])
        pbs = _merge_pbs_rows(pbs_rows)
        log_crawl(
            run_id=run_id, product_code=product_filter, source="pbs_api_v3", status="success",
            endpoint="/items,/item-dispensing-rule-relationships",
            duration_ms=int((time.time() - _t0) * 1000),
        )
    except Exception as exc:
        pbs_rows = []
        pbs = {}
        log_crawl(
            run_id=run_id, product_code=product_filter, source="pbs_api_v3", status="failed",
            endpoint="/items",
            error_message=str(exc)[:500],
            duration_ms=int((time.time() - _t0) * 1000),
        )

    # PBS 웹 보강 (Jina Reader — DPMQ 가 API 에서 못 얻었을 때 fallback)
    pbs_code_for_web = pbs.get("pbs_code") or pbs.get("pbs_item_code")
    if pbs_code_for_web:
        codes = [c.strip() for c in str(pbs_code_for_web).split("+") if c.strip()]
        api_bn = pbs.get("brand_name") or pbs.get("pbs_brand_name")
        api_brs = pbs.get("pbs_brands")
        agg_brands: list[dict[str, Any]] = []
        for c in codes:
            try:
                web = fetch_pbs_web(c)
            except Exception:
                continue
            if web.get("dpmq_aud") is not None and pbs.get("dpmq_aud") is None:
                pbs["dpmq_aud"] = web.get("dpmq_aud")
                pbs["pbs_dpmq"] = web.get("dpmq_aud")
            if web.get("pbs_patient_charge") is not None:
                pbs["pbs_patient_charge"] = web.get("pbs_patient_charge")
            if web.get("pbs_web_source_url"):
                pbs["pbs_web_source_url"] = web.get("pbs_web_source_url")
            if web.get("brand_name") and not (pbs.get("brand_name") or pbs.get("pbs_brand_name")):
                pbs["brand_name"] = web.get("brand_name")
                pbs["pbs_brand_name"] = web.get("brand_name")
            if web.get("pbs_brands"):
                agg_brands.extend(web["pbs_brands"])
        if agg_brands:
            pbs["pbs_brands"] = agg_brands
        elif api_brs is not None:
            pbs["pbs_brands"] = api_brs
        if not (pbs.get("brand_name") or pbs.get("pbs_brand_name")):
            pbs["pbs_brand_name"] = api_bn

    # ── Chemist Warehouse ─────────────────────────────────────
    pbs_terms = product.get("pbs_search_terms") or []
    retail_query = str(pbs_terms[0] if pbs_terms else product.get("inn_normalized") or "")

    _t0 = time.time()
    try:
        chemist = fetch_chemist_price(retail_query)
        log_crawl(
            run_id=run_id, product_code=product_filter, source="chemist_warehouse",
            status="success" if chemist else "partial",
            endpoint="/search",
            duration_ms=int((time.time() - _t0) * 1000),
        )
    except Exception as exc:
        chemist = None
        log_crawl(
            run_id=run_id, product_code=product_filter, source="chemist_warehouse", status="failed",
            endpoint="/search",
            error_message=str(exc)[:500],
            duration_ms=int((time.time() - _t0) * 1000),
        )

    # ── Healthylife 보강 (PBS 미등재 Private 처방약 참고가) ───
    # 기존 로직 유지 (§1-6). 조건: healthylife_slug 지정 + Chemist 실패/저가 시 대체.
    hl_slug = product.get("healthylife_slug")
    if hl_slug:
        _t0 = time.time()
        try:
            from sources.healthylife import fetch_healthylife_price
            hl = fetch_healthylife_price(str(hl_slug))
            log_crawl(
                run_id=run_id, product_code=product_filter, source="healthylife",
                status="success" if hl and hl.get("price_aud") else "partial",
                endpoint=f"/products/{hl_slug}",
                duration_ms=int((time.time() - _t0) * 1000),
            )
        except Exception as exc:
            hl = None
            log_crawl(
                run_id=run_id, product_code=product_filter, source="healthylife", status="failed",
                endpoint=f"/products/{hl_slug}",
                error_message=str(exc)[:500],
                duration_ms=int((time.time() - _t0) * 1000),
            )
        if hl and hl.get("price_aud") is not None:
            ch_price = (chemist or {}).get("price_aud") if chemist else None
            if ch_price is None:
                ch_price = (chemist or {}).get("retail_price_aud") if chemist else None
            ch_dec = _to_decimal(ch_price)
            chemist_is_empty = ch_dec is None or ch_dec < Decimal("5.0")
            if chemist_is_empty:
                chemist = {
                    "product_url": hl.get("product_url") or hl.get("price_source_url") or "",
                    "brand_name": hl.get("brand_name"),
                    "price_aud": _to_decimal(hl.get("price_aud")),
                    "pack_size": hl.get("pack_size"),
                    "in_stock": True,
                    "category": hl.get("category"),
                    "source_name": "healthylife",
                    "crawled_at": hl.get("crawled_at"),
                    # 하위호환
                    "retail_price_aud": _to_decimal(hl.get("price_aud")),
                    "price_unit": "per pack",
                    "price_source_name": hl.get("source") or "Healthylife",
                    "price_source_url": hl.get("product_url") or hl.get("price_source_url") or "",
                }

    # ── buy.nsw.gov.au ───────────────────────────────────────
    _t0 = time.time()
    try:
        nsw = fetch_buynsw(retail_query)
        log_crawl(
            run_id=run_id, product_code=product_filter, source="buy_nsw",
            status="success" if nsw and nsw.get("contract_value_aud") is not None else "partial",
            endpoint="/notices/search",
            duration_ms=int((time.time() - _t0) * 1000),
        )
    except Exception as exc:
        nsw = {}
        log_crawl(
            run_id=run_id, product_code=product_filter, source="buy_nsw", status="failed",
            endpoint="/notices/search",
            error_message=str(exc)[:500],
            duration_ms=int((time.time() - _t0) * 1000),
        )

    # ── 최종 dict 조립 ─────────────────────────────────────────
    summary = build_product_summary(product, pbs, tga, chemist, nsw)

    # ── DRY_RUN 분기 ───────────────────────────────────────────
    if dry_run:
        _logger.info(
            "[DRY_RUN] product=%s pbs_found=%s tga_found=%s aemp_aud=%s dpmq_aud=%s "
            "retail_price_aud=%s (method=%s) warnings=%d keys=%d",
            product_filter,
            summary.get("pbs_found"),
            summary.get("tga_found"),
            summary.get("aemp_aud"),
            summary.get("dpmq_aud"),
            summary.get("retail_price_aud"),
            summary.get("retail_estimation_method"),
            len(summary.get("warnings") or []),
            len(summary),
        )
        # 보조 테이블도 dry-run: 호출 대상만 출력
        pbs_would = bool(pbs.get("pbs_found") or pbs.get("pbs_listed"))
        tga_would = bool(tga.get("artg_id") or tga.get("artg_number"))
        try:
            candidates_preview = _collect_buyer_candidates(product_filter, tga, pbs, nsw or {})
        except Exception:
            candidates_preview = []
        _logger.info(
            "[DRY_RUN] would upsert — au_pbs_raw=%s au_tga_artg=%s au_buyers=%d candidates",
            pbs_would, tga_would, len(candidates_preview),
        )
        print(f"[DRY_RUN 완료] product_id={product_filter}")
        return True

    # ── 실제 DB 쓰기 경로 ─────────────────────────────────────
    ok = upsert_product(summary)

    # ── au_pbs_raw (§14-3-2) — PBS 원본 보관 ────────────────
    if pbs.get("pbs_found") or pbs.get("pbs_listed"):
        try:
            raw_resp = pbs.get("raw_response") or {}
            snapshot = {
                "product_id": product_filter,  # FK 변환은 DB 레벨에서 (TEXT vs BIGINT 주의)
                "pbs_code": pbs.get("pbs_code") or pbs.get("pbs_item_code"),
                "schedule_code": pbs.get("schedule_code"),
                "effective_date": pbs.get("first_listed_date"),
                "endpoint_items": raw_resp.get("items") if isinstance(raw_resp, dict) else None,
                "endpoint_dispensing_rules": raw_resp.get("dispensing_rule") if isinstance(raw_resp, dict) else None,
                # TODO(v2-pbs-full): /fees, /markup-bands, /copayments, /atc 엔드포인트 raw 보관
                "api_fetched_at": datetime.now(timezone.utc).isoformat(),
                "crawled_at": datetime.now(timezone.utc).isoformat(),
            }
            upsert_pbs_raw(snapshot)
        except Exception as exc:
            print(f"[au_pbs_raw upsert 경고] {exc}", flush=True)

    # ── au_tga_artg (§14-3-3) — TGA 원본 보관 ───────────────
    if tga.get("artg_id") or tga.get("artg_number"):
        try:
            artg_row = {
                "product_id": product_filter,
                "artg_id": tga.get("artg_id") or str(tga.get("artg_number") or ""),
                "product_name": product.get("product_name_ko"),
                "sponsor_name": tga.get("sponsor_name") or tga.get("tga_sponsor"),
                "sponsor_abn": tga.get("sponsor_abn"),
                "active_ingredients": tga.get("active_ingredients") or [],
                "strength": product.get("strength"),
                "dosage_form": product.get("dosage_form"),
                "route_of_administration": tga.get("route_of_administration"),
                "schedule": tga.get("schedule") or tga.get("tga_schedule"),
                "first_registered_date": tga.get("first_registered_date"),
                "status": tga.get("status") or tga.get("artg_status"),
                "artg_url": tga.get("artg_url") or tga.get("artg_source_url"),
                "crawled_at": datetime.now(timezone.utc).isoformat(),
            }
            upsert_tga_artg(artg_row)
        except Exception as exc:
            print(f"[au_tga_artg upsert 경고] {exc}", flush=True)

    # ── au_buyers (§14-3-7, §13-7-B) — 바이어 후보 풀 자동 수집 ──
    try:
        candidates = _collect_buyer_candidates(product_filter, tga, pbs, nsw or {})
        if candidates:
            upsert_buyer_candidates(candidates)
    except Exception as exc:
        print(f"[au_buyers upsert 경고] {exc}", flush=True)

    print(f"[완료] product_id={product_filter} upsert={'성공' if ok else '실패'}")
    return ok


def main() -> None:
    """CLI 진입점 — argparse + DRY_RUN 지원.

    사용 예:
      # 단일 품목
      python -m crawler.au_crawler --product au-hydrine-004
      # 전체 8 품목 순회
      python -m crawler.au_crawler --all
      # 환경변수 PRODUCT_FILTER 도 호환 (우선순위: --product > PRODUCT_FILTER > --all)
      PRODUCT_FILTER=au-omethyl-001 python -m crawler.au_crawler
      # DB 쓰기 skip (dry-run)
      DRY_RUN=1 python -m crawler.au_crawler --product au-hydrine-004

    종료 코드: 전 품목 성공 0, 실패 하나라도 있으면 1.
    """
    parser = argparse.ArgumentParser(description="호주 의약품 크롤러 v2 (위임지서 03a)")
    parser.add_argument(
        "--product",
        metavar="PRODUCT_ID",
        help="단일 품목 크롤 (예: au-hydrine-004). PRODUCT_FILTER env 보다 우선.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="au_products.json 에 정의된 전체 품목 순회. --product/PRODUCT_FILTER 없을 때만 적용.",
    )
    args = parser.parse_args()

    # DRY_RUN — 1, true, yes 모두 수용
    dry_run_raw = (os.environ.get("DRY_RUN") or "").strip().lower()
    dry_run = dry_run_raw in {"1", "true", "yes", "on"}

    # 우선순위: --product > PRODUCT_FILTER env > --all
    env_filter = (os.environ.get("PRODUCT_FILTER") or "").strip()
    selected_ids: list[str] | None
    if args.product:
        selected_ids = [args.product.strip()]
    elif env_filter:
        selected_ids = [env_filter]
    elif args.all:
        selected_ids = None  # None = 전체
    else:
        print(
            "[오류] 대상 품목 지정 필요 — --product <id> / --all / PRODUCT_FILTER env 중 하나.",
            file=sys.stderr,
        )
        sys.exit(1)

    products = _load_products()
    if selected_ids is None:
        targets = products
        print(f"[all] {len(targets)} 품목 전체 순회 (dry_run={dry_run})", flush=True)
    else:
        targets = []
        for pid in selected_ids:
            p = next((x for x in products if x.get("product_id") == pid), None)
            if p is None:
                print(
                    f"[오류] product_id={pid!r} 를 au_products.json 에서 찾을 수 없습니다.",
                    file=sys.stderr,
                )
                sys.exit(1)
            targets.append(p)

    if dry_run:
        print("[DRY_RUN] 활성 — Supabase 쓰기 skip, summary 주요 필드만 로그에 출력.", flush=True)
        # dry-run 에서 INFO 로그가 보이도록 basicConfig 설정 (외부 로거 설정이 있으면 무시됨)
        if not logging.getLogger().handlers:
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    all_ok = True
    for product in targets:
        ok = _process_one_product(product, dry_run=dry_run)
        all_ok = all_ok and ok

    sys.exit(0 if all_ok else 1)


def run() -> None:
    """모듈 외부 호출용 진입점 — main() 과 동일."""
    main()


if __name__ == "__main__":
    main()
