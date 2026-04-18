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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

from sources.chemist import build_sites
from sources.tga import determine_export_viable
from utils.crawl_time import now_kst_iso
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


def _raw_evidence_text(
    pbs: dict[str, Any],
    tga: dict[str, Any],
    nsw: dict[str, Any],
) -> str:
    """PBS·TGA·조달 텍스트를 이어 붙여 근거 원문으로 쓴다.

    Phase 4.3-v3 (2026-04-18) — TGA Schedule 라인 제거 (4필드 폐기).
    """
    parts: list[str] = []
    rt = pbs.get("restriction_text")
    if isinstance(rt, str) and rt.strip():
        parts.append(rt.strip())
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

    # Phase 4.3-v3 — TGA 4필드 폐기. tga_schedule 정규화·tga_norm dict 사본 제거.
    # determine_export_viable 은 이제 artg_status 만 참조.

    # 수출 적합성 판정 — TGA ARTG 등재 여부. PBS 등재면 무조건 viable 로 덮어쓰기.
    viable_result = determine_export_viable(tga)
    pbs_found_flag = bool(pbs.get("pbs_found") if pbs.get("pbs_found") is not None else pbs.get("pbs_listed"))
    if pbs_found_flag:
        viable_result = {"export_viable": "viable", "reason_code": "PBS_REGISTERED"}

    inn = str(product.get("inn_normalized") or "")
    pricing_case = str(product.get("pricing_case") or "ESTIMATE")
    raw_text = _raw_evidence_text(pbs, tga, nsw)
    evidence = build_evidence_text(pricing_case, raw_text, inn)

    # 소매가 추정 (Decimal 반환)
    pbs_price_for_trust = pbs.get("aemp_aud") if pbs.get("aemp_aud") is not None else pbs.get("pbs_price_aud")
    cr_trusted, chemist_ok = _chemist_retail_trustworthy(chemist, pbs_price_for_trust)
    chemist_price_aud: Decimal | None = cr_trusted

    retail_aud, retail_estimation_method = _estimate_retail_price(pbs, chemist_price_aud)

    # Task 5 — ESTIMATE_hospital seeds 하드코딩 AEMP 가 주입되어 있으면 해당 라벨·retail 로 덮어씀.
    # pbs DTO 에 retail_estimation_method="fob_hardcoded_trademap" 이 찍혀 온 경우.
    if pbs.get("retail_estimation_method") == "fob_hardcoded_trademap":
        retail_estimation_method = "fob_hardcoded_trademap"
        aemp_from_pbs = _to_decimal(pbs.get("aemp_aud"))
        if aemp_from_pbs is not None and aemp_from_pbs > 0:
            retail_aud = aemp_from_pbs.quantize(Decimal("0.01"))

    # Phase 4.7 — Omethyl Case 5: Healthylife OMACOR 가격을 retail_price_aud 로 직접 사용.
    # chemist dict 이 _from_healthylife_case5=True 플래그를 갖고 있으면 Chemist × 1.20 배수
    # 우회하고 HL 가격 그대로 적용. retail_estimation_method 는 고유 라벨로 구분.
    # Task 3 (2026-04-19) — Case 5 외 일반 Healthylife fallback 경로도 수용.
    #   _from_healthylife_case5=True → "healthylife_same_ingredient_diff_form" (Omethyl 전용)
    #   chemist.source_name=="healthylife" 그 외 → "healthylife_actual" (신약 일반 경로)
    hl_case5_flag = bool(chemist.get("_from_healthylife_case5"))
    hl_source_flag = (chemist.get("source_name") == "healthylife")
    if hl_case5_flag:
        hl_price = _to_decimal(chemist.get("price_aud") or chemist.get("retail_price_aud"))
        if hl_price is not None and hl_price > 0:
            retail_aud = hl_price.quantize(Decimal("0.01"))
            retail_estimation_method = "healthylife_same_ingredient_diff_form"
    elif hl_source_flag:
        hl_price = _to_decimal(chemist.get("price_aud") or chemist.get("retail_price_aud"))
        if hl_price is not None and hl_price > 0:
            retail_aud = hl_price.quantize(Decimal("0.01"))
            retail_estimation_method = "healthylife_actual"

    # price_source_name / url (하위호환)
    if retail_estimation_method == "pbs_dpmq":
        price_name = "PBS"
        price_url = pbs.get("source_url") or pbs.get("pbs_source_url") or ""
    elif retail_estimation_method == "chemist_markup":
        price_name = "Chemist Warehouse"
        price_url = chemist.get("product_url") or chemist.get("price_source_url") or ""
    elif retail_estimation_method == "healthylife_same_ingredient_diff_form":
        price_name = f"Healthylife — {chemist.get('brand_name') or 'OMACOR'}"
        price_url = chemist.get("product_url") or chemist.get("price_source_url") or ""
    elif retail_estimation_method == "healthylife_actual":
        # Task 3 — 신약·일반 경로: Healthylife 실제 장바구니 가격 직접 사용
        price_name = f"Healthylife — {chemist.get('brand_name') or 'Healthylife'}"
        price_url = chemist.get("product_url") or chemist.get("price_source_url") or ""
    elif retail_estimation_method == "fob_hardcoded_trademap":
        # Task 5 — Gadvoa hospital tender 하드코딩값 (TradeMap NZ→DE 역산)
        price_name = "TradeMap 2024 NZ→DE (Bayer Gadovist 오리지널 FOB 역산)"
        price_url = ""
    else:
        price_name = "PBS"
        price_url = (
            chemist.get("product_url") or chemist.get("price_source_url") or ""
        ) or (pbs.get("source_url") or pbs.get("pbs_source_url") or "")

    chemist_url_for_sites = (
        (chemist.get("product_url") or chemist.get("price_source_url") or "") if chemist_ok else ""
    )

    # completeness_score 계산용 assembled (v1 키 — scoring.py AU_REQUIRED_FIELDS 호환).
    # Phase 4.3-v3 — tga_schedule 제거 (4필드 폐기). scoring.AU_REQUIRED_FIELDS 도
    # tga_schedule 을 더 이상 요구하지 않아야 함 (별도 위임에서 정리).
    assembled_for_score: dict[str, Any] = {
        "artg_number": tga.get("artg_number") or tga.get("artg_id"),
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

    # Phase 4.7 — Case 5 Healthylife 경로일 땐 private_price 라벨을 Healthylife 로 교체
    if hl_case5_flag and isinstance(sites, dict):
        brand = chemist.get("brand_name") or "OMACOR"
        hl_url = chemist.get("product_url") or chemist.get("price_source_url") or ""
        if hl_url:
            sites["private_price"] = [{
                "name": f"Healthylife — {brand}",
                "url": hl_url,
            }]

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
        # TGA 하위호환 (Phase 4.3-v3 — tga_schedule 제거, au_products 컬럼도 DROP 완료)
        "artg_number": tga.get("artg_number") or tga.get("artg_id"),
        "artg_status": tga.get("artg_status") or tga.get("status"),
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
        # Phase Sereterol — originator 브랜드/스폰서 별도 컬럼 (제네릭 선택 시에도 보존).
        # PBS 매칭 풀에 innovator_indicator='Y' 행 존재 시 그 브랜드·스폰서 정보.
        "originator_brand_name": pbs.get("originator_brand_name"),
        "originator_sponsor": pbs.get("originator_sponsor"),
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

        # Task 3 (2026-04-19) — Healthylife 가격·URL 전용 컬럼.
        # chemist dict 이 Healthylife 로 대체된 경우 source_name='healthylife' 표기.
        "healthylife_price_aud": (
            _to_decimal(chemist.get("price_aud") or chemist.get("retail_price_aud"))
            if chemist.get("source_name") == "healthylife"
            else None
        ),
        "healthylife_url": (
            chemist.get("product_url") or chemist.get("price_source_url")
            if chemist.get("source_name") == "healthylife"
            else None
        ),

        # Phase Omethyl (2026-04-19) — 호주 시장 재고 상태 + TGA 대표 match_type.
        # availability_status: Healthylife 로 chemist 대체된 Case 5 경로에서만 채워짐
        # (in_stock / temporarily_unavailable). 그 외 경로는 None.
        # match_type: 대표 ARTG 매칭 유형 — exact / same_ingredient_diff_form / None.
        "availability_status": chemist.get("availability_status") if chemist else None,
        "match_type": tga.get("match_type"),

        # Phase 4.4 — case_code / ingredients_split DB 전파 (기존 결정 3 '보존' 정책 해제)
        # au_products.case_code 컬럼에 pricing_case 값(DIRECT / COMPONENT_SUM / ESTIMATE_* 등)
        # 을 직접 기록해 보고서·분석 단계에서 JOIN 없이 조회 가능하도록.
        "case_code": product.get("pricing_case"),
        "ingredients_split": (
            {"components": product.get("inn_components", [])}
            if product.get("inn_components") else {"components": []}
        ),
        "ai_deep_research_raw": None,    # AI 붙을 때 채움

        # 메타
        "last_crawled_at": now_kst_iso(),
        "crawled_at": now_kst_iso(),  # rename → last_crawled_at
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

    # Phase 4.4 — case_code 는 이제 의도적으로 기록 (결정 3 정책 반전).
    # 기존 "pop" 방어 삭제 — pricing_case 값이 DB 에 전달되어야 분석 레이어에서 JOIN 없이 사용 가능.

    # Phase 4.8 — _extract_pbs_derived 로 null 필드 복원.
    # multi/withdrawal 경로 merged DTO 는 대표 1건만 유지하므로 program_code/formulary
    # 등이 None 일 수 있음 → raw_response.items/dispensing_rule 에서 직접 뽑아 채움.
    raw_resp = pbs.get("raw_response") or {}
    if isinstance(raw_resp, dict) and raw_resp.get("items"):
        derived = _extract_pbs_derived(raw_resp.get("items"), raw_resp.get("dispensing_rule"))
        for k, v in derived.items():
            if v is not None and out.get(k) is None:
                out[k] = v

    # 위임지서 §4.4 — dispatcher 가 붙여준 추정 메타를 warnings/situation_summary 에 전파
    case_applied = pbs.get("pricing_case_applied")
    if case_applied and case_applied not in ("DIRECT", "DIRECT_FDC", "COMPONENT_SUM"):
        # fallback·추정 케이스만 사용자에 노출
        current = list(out.get("warnings") or [])
        current.append(f"pricing_case_applied={case_applied}")
        out["warnings"] = current

    similar_proxy = pbs.get("_similar_proxy")
    if similar_proxy:
        existing = out.get("similar_drug_used") or []
        if similar_proxy not in existing:
            out["similar_drug_used"] = list(existing) + [similar_proxy]

    skip_reason = pbs.get("_pbs_skipped_reason")
    if skip_reason:
        w = list(out.get("warnings") or [])
        w.append(f"pbs_skipped:{skip_reason}")
        out["warnings"] = w

    # Phase 4.9 수정 3 — Case 4 ESTIMATE_substitute situation_summary + 메타 기록
    pricing_case_upper = pricing_case.upper()
    if pricing_case_upper == "ESTIMATE_SUBSTITUTE" and not out.get("tga_found"):
        out["situation_summary"] = (
            "TGA(호주 의약품 등록 시스템) 미등재 상태입니다. "
            "호주 진출을 위해서는 먼저 TGA ARTG 등록 절차가 필요합니다. "
            "동일 치료 영역에 등재된 유사 효능 의약품은 보고서에서 별도로 참조합니다."
        )
        existing_warn = list(out.get("warnings") or [])
        if "not_registered_au" not in existing_warn:
            existing_warn.append("not_registered_au")
        out["warnings"] = existing_warn
        out["similar_drug_used"] = list(product.get("similar_inns") or [])
        out["confidence"] = 0.1

    # Case 3 ESTIMATE_withdrawal situation_summary + 메타 기록.
    # Phase 1.1 원안 복귀 (Jisoo 2026-04-18 재결정): 유사계열 프록시 fetch 복귀,
    # AEMP 는 '등재 나머지 성분 + 프록시' 합산. 서술 문구도 합산 반영으로 업데이트.
    if pricing_case_upper == "ESTIMATE_WITHDRAWAL":
        withdrawn = product.get("withdrawn_component") or ""
        similar = list(product.get("similar_inns") or [])
        proxy_used = similar[0] if similar else "없음"
        out["situation_summary"] = (
            f"복합제 성분 중 {withdrawn} 는 호주 시장에서 상업적으로 철수한 상태입니다. "
            f"등재된 나머지 성분 + 유사계열 프록시({proxy_used}) AEMP(정부 승인 출고가) 를 "
            f"합산해 반영했습니다. 철수 배경과 재진입 장벽(TGA 소명·PBAC 재심의)은 "
            f"보고서에서 별도 서술됩니다."
        )
        existing_warn = list(out.get("warnings") or [])
        if "withdrawal_proxy_used" not in existing_warn:
            existing_warn.append("withdrawal_proxy_used")
        out["warnings"] = existing_warn
        # similar_drug_used 는 실제 fetch 로 쓰인 프록시만 (첫 similar_inns)
        out["similar_drug_used"] = similar[:1] if similar else []
        out["confidence"] = 0.3

    # Phase 4.5 — originator_brand 판정 정상화 (fallback).
    # `pbs.get("originator_brand")` 가 None 이면 endpoint_items 원본에서 innovator_indicator
    # 를 직접 확인 (API·웹 편차 방지). 'Y' 만 True, 'N' 은 False, 그 외 None.
    if out.get("originator_brand") is None:
        raw_resp = pbs.get("raw_response") or {}
        items = raw_resp.get("items") if isinstance(raw_resp, dict) else {}
        innov = (
            pbs.get("innovator_indicator")
            or (items.get("innovator_indicator") if isinstance(items, dict) else None)
            or (items.get("originator_brand_indicator") if isinstance(items, dict) else None)
        )
        if innov is not None:
            out["originator_brand"] = str(innov).strip().upper() == "Y"

    return out


# ─────────────────────────────────────────────────────────────────────
# 기존 유틸 — 품목 로드 + 복합 성분 병합
# ─────────────────────────────────────────────────────────────────────

def _load_products() -> list[dict[str, Any]]:
    path = _CRAWLER_DIR / "au_products.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("products", []))


def _load_seeds_index() -> dict[str, dict[str, Any]]:
    """stage2/fob_reference_seeds.json 을 product_id → seed 맵으로 로드.

    Task 6 (2026-04-19) — auto_classify_case 결과보다 seeds 의 수동 기재가
    정확도가 높으므로, 기존 8 품목은 seeds 의 pricing_case / similar_inns 를
    우선 참조. 파일 로드 실패·키 없음 → 빈 dict.
    """
    seeds_path = _CRAWLER_DIR.parent / "stage2" / "fob_reference_seeds.json"
    try:
        with seeds_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for seed in data.get("seeds", []):
        pid = seed.get("product_id")
        if pid:
            out[pid] = seed
    return out


def auto_classify_case(
    tga_result: dict[str, Any],
    pbs_result: dict[str, Any],
    product_input: dict[str, Any],
) -> str:
    """Task 6 (2026-04-19) — seeds 없는 신약 대응 pricing_case 자동 판정.

    판정 테이블 (프롬프트 §5-A):
      - TGA 동일 성분·함량·제형 일치 + PBS 등재        → DIRECT_MATCH (Case A)
      - TGA 복합제 미등재 but 단일성분 등재 + PBS 복합 미등재 but 단일 등재
                                                     → COMPONENT_SUM (Case B)
      - TGA cancelled 또는 PBS 철수 이력               → ESTIMATE_withdrawal (Case C)
      - TGA·PBS 둘 다 성분 전무 + 유사 ATC 성분 존재   → ESTIMATE_substitute (Case D)
      - TGA 동일 성분 있으나 제형·함량 다름 + PBS 없음 → ESTIMATE_form_diff (Case E)
      - TGA 동일 성분 + PBS 없음 + hospital_only / 독점 패턴
                                                     → ESTIMATE_hospital (Case F)

    반환 문자열은 기존 dispatcher 가 이해하는 값으로 정규화:
      DIRECT_MATCH / DIRECT → "DIRECT"
      ESTIMATE_form_diff → "ESTIMATE_private"   (같은 성분 다른 제형 = 기존 Case 5)
      그 외 → 그대로.
    기본값 (판정 불능) → "ESTIMATE_substitute" (가장 보수적 — 보고서 신뢰도 낮게).
    """
    tga_found = bool(
        tga_result.get("tga_found")
        if tga_result.get("tga_found") is not None
        else (tga_result.get("artg_status") or tga_result.get("status")) == "registered"
    )
    pbs_found = bool(
        pbs_result.get("pbs_found")
        if pbs_result.get("pbs_found") is not None
        else pbs_result.get("pbs_listed")
    )
    match_type = (tga_result.get("match_type") or "").lower()
    artg_status = (tga_result.get("artg_status") or tga_result.get("status") or "").lower()

    components = [str(c) for c in (product_input.get("inn_components") or []) if c]
    is_fdc = len(components) > 1
    hospital_only = bool(product_input.get("hospital_only_flag")) or bool(
        product_input.get("skip_chemist")
    )

    # Case C — TGA cancelled 또는 PBS 철수 이력
    if artg_status in ("cancelled", "withdrawn") or pbs_result.get("commercial_withdrawal"):
        return "ESTIMATE_withdrawal"

    # Case A — TGA match_type=='exact' + PBS 등재
    if tga_found and match_type == "exact" and pbs_found:
        return "DIRECT"

    # Case B — 복합제 & PBS 복합 미등재 but 각 성분 등재
    #   (tga.tga_artg_details 가 비었거나 exact 없음) AND components 전부 PBS 단일 등재 시
    #   실제 검증은 pbs.fetch_pbs_component_sum 단계에서 이뤄짐. 힌트만 제공.
    if is_fdc and tga_found and not pbs_found:
        return "COMPONENT_SUM"

    # Case F — 병원 전용 (hospital_only / 독점 패턴)
    if tga_found and not pbs_found and hospital_only:
        return "ESTIMATE_hospital"

    # Case E — TGA 성분 있으나 제형·함량 다름 (match_type='same_ingredient_diff_form') + PBS 없음
    if tga_found and match_type == "same_ingredient_diff_form" and not pbs_found:
        return "ESTIMATE_private"

    # Case D — TGA·PBS 둘 다 미등재 → 유사계열 proxy 필요
    if not tga_found and not pbs_found:
        return "ESTIMATE_substitute"

    # DIRECT 기본 — tga_found AND pbs_found 인데 다른 조건 안 맞는 경우 DIRECT 로.
    if tga_found and pbs_found:
        return "DIRECT"

    # 기본: 가장 보수적 라벨
    return "ESTIMATE_substitute"


def resolve_pricing_case(
    product_id: str | None,
    tga_result: dict[str, Any],
    pbs_result: dict[str, Any],
    input_data: dict[str, Any],
) -> str:
    """Task 6 (2026-04-19) — seeds 우선 정책 + 신약 자동 판정.

    기존 품목(product_id 있음)은 seeds.pricing_case 를 우선 (사람이 검토한 값이
    더 정확). 신약이거나 seeds 에 없으면 auto_classify_case 호출.
    """
    if product_id:
        seeds = _load_seeds_index()
        seed = seeds.get(product_id) or {}
        seed_case = seed.get("pricing_case")
        if seed_case:
            return str(seed_case)
    return auto_classify_case(tga_result, pbs_result, input_data)


def _load_fob_hardcoded(product_id: str | None) -> dict[str, Any] | None:
    """stage2/fob_reference_seeds.json 에서 product_id 의 fob_hardcoded_aud 블록 반환.

    Task 5 (2026-04-19) — Gadvoa 같은 ESTIMATE_hospital 품목의 Bayer 오리지널 FOB
    하드코딩값(TradeMap NZ→DE 역산) 을 AEMP 프록시로 주입할 때 사용.
    반환 dict 예:
      {
        "bayer_reference_aud": 16.49,
        "aggressive": 9.89, "average": 12.37, "conservative": 14.02,
        "bayer_reference_source": "TradeMap 2024 NZ→DE HS 300630 ...",
      }
    seeds 없음·블록 없음·파일 로드 실패 → None.
    """
    if not product_id:
        return None
    seeds_path = _CRAWLER_DIR.parent / "stage2" / "fob_reference_seeds.json"
    try:
        with seeds_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    for seed in data.get("seeds", []):
        if seed.get("product_id") == product_id:
            block = seed.get("fob_hardcoded_aud")
            if isinstance(block, dict):
                return block
            return None
    return None


def _dispatch_pbs_by_case(product: dict[str, Any]) -> dict[str, Any]:
    """au_products.json 의 pricing_case 기준으로 PBS 조회 경로 선택.

    Case 1 DIRECT         → 단성분 fetch_pbs_by_ingredient, 복합제 fetch_pbs_fdc
    Case 2 COMPONENT_SUM  → fetch_pbs_component_sum
    Case 3 ESTIMATE_withdrawal → fetch_pbs_withdrawal (withdrawn_component + similar_inns)
    Case 4 ESTIMATE_substitute → fetch_pbs_similar (similar_inns)
    Case 5 ESTIMATE_private    → fetch_pbs_same_ingredient (reference_inn)
    Case 6 ESTIMATE_hospital   → fetch_pbs_hospital_skip (PBS skip)

    반환: 단일 PBSItemDTO(dict). 빈 DTO 도 유효값.
    """
    from sources.pbs import (
        fetch_pbs_by_ingredient,
        fetch_pbs_component_sum,
        fetch_pbs_fdc,
        fetch_pbs_hospital_skip,
        fetch_pbs_multi,
        fetch_pbs_same_ingredient,
        fetch_pbs_similar,         # 레거시 (하위호환)
        fetch_pbs_substitute,      # Task 2 신설 — 실제 proxy AEMP 조회
        fetch_pbs_withdrawal,
    )

    case = str(product.get("pricing_case") or "").upper()
    components = [str(c) for c in (product.get("inn_components") or []) if c]
    inn = str(product.get("inn_normalized") or "")

    # Case 6 — 병원 조달, PBS skip.
    # Task 5 (2026-04-19) — seeds.fob_hardcoded_aud 의 bayer_reference_aud 를 AEMP 프록시로
    # 주입 (Gadvoa 는 PBS 가격 없음 → FOB 역산이 AEMP 를 요구하므로 하드코딩 수기값 활용).
    # warnings 에 근거·소스 함께 기록.
    if case == "ESTIMATE_HOSPITAL":
        dto = fetch_pbs_hospital_skip()
        try:
            hardcoded = _load_fob_hardcoded(product.get("product_id"))
        except Exception:
            hardcoded = None
        if hardcoded:
            bayer_ref = hardcoded.get("bayer_reference_aud")
            if isinstance(bayer_ref, (int, float)) and float(bayer_ref) > 0:
                dto["aemp_aud"] = Decimal(str(bayer_ref))
                dto["retail_estimation_method"] = "fob_hardcoded_trademap"
                src_note = hardcoded.get("bayer_reference_source")
                warnings_list = list(dto.get("warnings") or [])
                warnings_list.append("hardcoded_aemp:true")
                if src_note:
                    warnings_list.append(f"ai_research_source:{src_note}")
                dto["warnings"] = warnings_list
        return dto

    # Case 1 — DIRECT
    if case == "DIRECT":
        if len(components) > 1:
            # Phase Sereterol — 자사 함량 우선순위 매칭용 strengths 리스트 전달.
            # strengths(복수) 가 없으면 단일 strength 로 단일-요소 리스트 구성.
            strengths = product.get("strengths")
            if not strengths and product.get("strength"):
                strengths = [str(product.get("strength"))]
            return fetch_pbs_fdc(
                components,
                product.get("fdc_search_term"),
                strengths=strengths,
            )
        ing = components[0] if components else inn
        rows = fetch_pbs_by_ingredient(ing)
        if rows:
            # 단일 성분은 fetch_pbs_by_ingredient 가 이미 최적 1건만 반환 (_filter_results)
            out = dict(rows[0])
            out["pricing_case_applied"] = "DIRECT"
            return out
        return {}

    # Case 2 — COMPONENT_SUM
    if case == "COMPONENT_SUM":
        return fetch_pbs_component_sum(components)

    # Case 3 — ESTIMATE_withdrawal
    if case == "ESTIMATE_WITHDRAWAL":
        withdrawn = product.get("withdrawn_component") or ""
        similar = product.get("similar_inns") or []
        return fetch_pbs_withdrawal(components, withdrawn, similar)

    # Case 4 — ESTIMATE_substitute
    # Task 2 (2026-04-19) — fetch_pbs_similar(빈 DTO) → fetch_pbs_substitute(실제 proxy AEMP 조회) 로 전환.
    # Gastiin(mosapride) 같은 미등재 품목에 대해 similar_inns[0] (domperidone) 의
    # 실제 PBS AEMP/DPMQ 를 주입해 FOB 역산 가능하게 함.
    if case == "ESTIMATE_SUBSTITUTE":
        similar = product.get("similar_inns") or []
        return fetch_pbs_substitute(inn, similar)

    # Case 5 — ESTIMATE_private
    if case == "ESTIMATE_PRIVATE":
        ref = product.get("reference_inn") or inn
        return fetch_pbs_same_ingredient(ref)

    # fallback — 기존 동작 유지 (pricing_case 비어 있거나 알 수 없는 값)
    if len(components) > 1:
        rows = fetch_pbs_multi(components)
    else:
        rows = fetch_pbs_by_ingredient(components[0] if components else inn)
    return _merge_pbs_rows(rows)


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

    # Phase 4.8 — 원본 rows 보존 (au_pbs_raw 복수행 INSERT 용). endpoint_items NOT NULL
    # 위반 방지: 대표 1건의 items/dispensing_rule 를 merged.raw_response 에도 복구.
    merged["_raw_rows"] = list(rows)
    for r in rows:
        rr = r.get("raw_response") or {}
        if isinstance(rr, dict) and rr.get("items"):
            merged["raw_response"] = {
                "items": rr.get("items"),
                "dispensing_rule": rr.get("dispensing_rule") or {},
            }
            break

    return merged


def _extract_pbs_derived(
    row_items: dict[str, Any] | None,
    row_disp: dict[str, Any] | None,
) -> dict[str, Any]:
    """endpoint_items + endpoint_dispensing_rules 에서 au_products 파생필드 추출.

    Phase 4.8 — multi/withdrawal 경로(`_merge_pbs_rows`)에서 DTO 는 합산되지만
    program_code/formulary/pack_size 등 개별 필드는 대표 1건이 필요. DIRECT 경로와
    동일한 추출 로직을 공용 함수화해 summary 에서 호출.

    row_items   : PBS /items 원본 dict
    row_disp    : PBS /item-dispensing-rule-relationships 원본 dict

    반환은 au_products 컬럼명 기준 dict (미검출 필드는 None).
    """
    items = row_items or {}
    disp = row_disp or {}

    def _yn(v: Any) -> bool | None:
        if v is None:
            return None
        return str(v).strip().upper() == "Y"

    # s85/s100 파생 — dispensing_rule_mnem 이 's90...' 으로 시작하면 s85 (Section 85)
    mnem = (disp.get("dispensing_rule_mnem") or "").lower()
    section = "S85" if mnem.startswith("s90") else None

    return {
        "program_code": items.get("program_code"),
        "formulary": items.get("formulary"),
        "pack_size": items.get("pack_size"),
        "pricing_quantity": items.get("pricing_quantity"),
        "maximum_prescribable_pack": items.get("maximum_prescribable_pack"),
        "first_listed_date": items.get("first_listed_date"),
        # schedule_code 는 PBS 버전번호("3963")라 TGA 와 충돌. au_products 에는
        # TGA 값이 들어가야 하므로 여기선 반환하지 않음 (build_product_summary 가
        # TGA 경로에서 별도 주입).
        "mn_pharmacy_price_aud": _to_decimal(disp.get("mn_pharmacy_price")),
        "brand_premium_aud": _to_decimal(disp.get("brand_premium")),
        "therapeutic_group_premium_aud": _to_decimal(disp.get("therapeutic_group_premium")),
        "special_patient_contrib_aud": _to_decimal(disp.get("special_patient_contribution")),
        "wholesale_markup_band": disp.get("mn_price_wholesale_markup"),
        "pharmacy_markup_code": disp.get("mn_pharmacy_markup_code"),
        "dispensing_fee_aud": _to_decimal(disp.get("fee_dispensing")),
        "ahi_fee_aud": _to_decimal(disp.get("fee_extra")),
        "section_85_100": section,
        "therapeutic_group_id": items.get("therapeutic_group_id"),
        "brand_substitution_group_id": items.get("brand_substitution_group_id"),
        "policy_imdq60": _yn(items.get("policy_applied_imdq60_flag")),
        "policy_biosim": _yn(items.get("policy_applied_bio_sim_up_flag")),
    }


# ─────────────────────────────────────────────────────────────────────
# main — 단일/전체 품목 크롤 + upsert + 바이어 풀 + 로그
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
    # Phase Sereterol — expected_inns 로 base INN set 매칭 필터 적용.
    tga_expected = [str(c) for c in (product.get("inn_components") or []) if c]
    # Phase Omethyl — pricing_case 별 match_mode 결정.
    #   DIRECT (FDC 포함) → 'strict' (set-equality 엄격)
    #   ESTIMATE_* / COMPONENT_SUM → 'ingredient_only' (expected ⊆ ARTG 허용,
    #   함량·제형 상이 품목 수용. 보고서 레이어가 match_type 으로 구분)
    _case_upper = str(product.get("pricing_case") or "").upper()
    tga_match_mode = "strict" if _case_upper == "DIRECT" else "ingredient_only"
    _t0 = time.time()
    _tga_started = now_kst_iso()
    try:
        tga = fetch_tga_artg(
            tga_query,
            expected_inns=tga_expected or None,
            match_mode=tga_match_mode,
            expected_strength=product.get("strength"),
            expected_dosage_form=product.get("dosage_form"),
        )
        determine_export_viable(tga)
        log_crawl(
            run_id=run_id, product_code=product_filter, source="tga", status="success",
            endpoint="/resources/artg",
            duration_ms=int((time.time() - _t0) * 1000),
            started_at=_tga_started,
            finished_at=now_kst_iso(),
        )
    except Exception as exc:
        tga = {}
        log_crawl(
            run_id=run_id, product_code=product_filter, source="tga", status="failed",
            endpoint="/resources/artg",
            error_message=str(exc)[:500],
            duration_ms=int((time.time() - _t0) * 1000),
            started_at=_tga_started,
            finished_at=now_kst_iso(),
        )

    # ── PBS ──────────────────────────────────────────────────
    # pricing_case 기반 dispatcher — Case 1~6 분기 (위임지서 Phase 1.2)
    components = [str(c) for c in (product.get("inn_components") or []) if c]
    if not components:
        components = [str(product.get("inn_normalized") or "")]

    _t0 = time.time()
    _pbs_started = now_kst_iso()
    try:
        pbs = _dispatch_pbs_by_case(product)
        pbs_rows = pbs.get("_component_rows") if isinstance(pbs, dict) else None
        log_crawl(
            run_id=run_id, product_code=product_filter, source="pbs_api_v3", status="success",
            endpoint=f"/items,/item-dispensing-rule-relationships (case={product.get('pricing_case')})",
            duration_ms=int((time.time() - _t0) * 1000),
            started_at=_pbs_started,
            finished_at=now_kst_iso(),
        )
    except Exception as exc:
        pbs_rows = []
        pbs = {}
        log_crawl(
            run_id=run_id, product_code=product_filter, source="pbs_api_v3", status="failed",
            endpoint=f"/items (case={product.get('pricing_case')})",
            error_message=str(exc)[:500],
            duration_ms=int((time.time() - _t0) * 1000),
            started_at=_pbs_started,
            finished_at=now_kst_iso(),
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
    # pricing_case 기반 분기 (위임지서 Phase 2.1) —
    #   Case 6 ESTIMATE_hospital / skip_chemist=true  → skip (약국 유통 없음)
    #   Case 4 ESTIMATE_substitute (성분 미등재)      → skip (약국에도 당연히 없음)
    #   나머지: chemist_search_term > pbs_search_terms[0] > inn_normalized 순으로 쿼리.
    case = str(product.get("pricing_case") or "").upper()
    pbs_terms = product.get("pbs_search_terms") or []
    retail_query = str(
        product.get("chemist_search_term")
        or (pbs_terms[0] if pbs_terms else None)
        or product.get("inn_normalized")
        or ""
    )

    if case == "ESTIMATE_HOSPITAL" or product.get("skip_chemist") or case == "ESTIMATE_SUBSTITUTE":
        chemist = None
        # Phase Omethyl — 사유별 세분화. 이전엔 skip_chemist=true 인 모든 케이스를
        # "hospital_procurement_only" 로 일괄 라벨링했는데, Omethyl(ESTIMATE_private +
        # skip_chemist=true) 이 병원 조달 품목으로 잘못 표시되던 버그. 사유별 분기:
        if case == "ESTIMATE_HOSPITAL":
            skip_reason = "hospital_procurement_only"
        elif product.get("hospital_only_flag"):
            skip_reason = "hospital_procurement_only"
        elif case == "ESTIMATE_PRIVATE" and product.get("skip_chemist"):
            skip_reason = "private_rx_skip_chemist"
        elif case == "ESTIMATE_SUBSTITUTE":
            skip_reason = "not_registered_au_case4"
        elif product.get("skip_chemist"):
            skip_reason = "skip_chemist_flag"
        else:
            skip_reason = "skipped"
        log_crawl(
            run_id=run_id, product_code=product_filter, source="chemist_warehouse",
            status="skipped",
            endpoint=f"/search (skip: {skip_reason})",
            duration_ms=0,
            started_at=now_kst_iso(),
            finished_at=now_kst_iso(),
        )
        _t0 = time.time()  # no-op but keeps subsequent var-scoping balanced
        _ch_started = now_kst_iso()
    else:
        _t0 = time.time()
        _ch_started = now_kst_iso()
        try:
            chemist = fetch_chemist_price(retail_query)
            log_crawl(
                run_id=run_id, product_code=product_filter, source="chemist_warehouse",
                status="success" if chemist else "partial",
                endpoint="/search",
                duration_ms=int((time.time() - _t0) * 1000),
                started_at=_ch_started,
                finished_at=now_kst_iso(),
            )
        except Exception as exc:
            chemist = None
            log_crawl(
                run_id=run_id, product_code=product_filter, source="chemist_warehouse", status="failed",
                endpoint="/search",
                error_message=str(exc)[:500],
                duration_ms=int((time.time() - _t0) * 1000),
                started_at=_ch_started,
                finished_at=now_kst_iso(),
            )

    # ── Healthylife 보강 (PBS 미등재 Private 처방약 참고가) ───
    # 기존 로직 유지 (§1-6). 조건: healthylife_slug 지정 + Chemist 실패/저가 시 대체.
    hl_slug = product.get("healthylife_slug")
    if hl_slug:
        _t0 = time.time()
        _hl_started = now_kst_iso()
        try:
            from sources.healthylife import fetch_healthylife_price
            hl = fetch_healthylife_price(str(hl_slug))
            log_crawl(
                run_id=run_id, product_code=product_filter, source="healthylife",
                status="success" if hl and hl.get("price_aud") else "partial",
                endpoint=f"/products/{hl_slug}",
                duration_ms=int((time.time() - _t0) * 1000),
                started_at=_hl_started,
                finished_at=now_kst_iso(),
            )
        except Exception as exc:
            hl = None
            log_crawl(
                run_id=run_id, product_code=product_filter, source="healthylife", status="failed",
                endpoint=f"/products/{hl_slug}",
                error_message=str(exc)[:500],
                duration_ms=int((time.time() - _t0) * 1000),
                started_at=_hl_started,
                finished_at=now_kst_iso(),
            )
        if hl and hl.get("price_aud") is not None:
            ch_price = (chemist or {}).get("price_aud") if chemist else None
            if ch_price is None:
                ch_price = (chemist or {}).get("retail_price_aud") if chemist else None
            ch_dec = _to_decimal(ch_price)
            chemist_is_empty = ch_dec is None or ch_dec < Decimal("5.0")
            if chemist_is_empty:
                # Phase 4.7 — Case 5 ESTIMATE_private 전용 마커. Omethyl 같이 PBS·
                # Chemist 미검색 품목은 Healthylife 가격을 retail_price_aud 로 직접 사용
                # (Chemist × 1.20 배수 우회). build_product_summary 가 이 플래그 보고 판단.
                is_case5 = str(product.get("pricing_case") or "").upper() == "ESTIMATE_PRIVATE"
                hl_availability = hl.get("availability_status")  # Phase Omethyl
                chemist = {
                    "product_url": hl.get("product_url") or hl.get("price_source_url") or "",
                    "brand_name": hl.get("brand_name"),
                    "price_aud": _to_decimal(hl.get("price_aud")),
                    "pack_size": hl.get("pack_size"),
                    "in_stock": (hl_availability != "temporarily_unavailable"),
                    "category": hl.get("category"),
                    "source_name": "healthylife",
                    "crawled_at": hl.get("crawled_at"),
                    "_from_healthylife_case5": is_case5,
                    # Phase Omethyl — 재고 상태 플래그 보존 (build_product_summary 가 읽음)
                    "availability_status": hl_availability,
                    # 하위호환
                    "retail_price_aud": _to_decimal(hl.get("price_aud")),
                    "price_unit": "per pack",
                    "price_source_name": hl.get("source") or "Healthylife",
                    "price_source_url": hl.get("product_url") or hl.get("price_source_url") or "",
                }

    # ── buy.nsw.gov.au ───────────────────────────────────────
    # Phase 4.9 수정 2 — Case 4 ESTIMATE_substitute (TGA/PBS 미등재) → NSW 조달에도
    # 당연히 없으므로 skip. 로그만 status='skipped' 로 기록.
    case = str(product.get("pricing_case") or "").upper()
    if case == "ESTIMATE_SUBSTITUTE":
        nsw = {}
        log_crawl(
            run_id=run_id, product_code=product_filter, source="buy_nsw",
            status="skipped",
            endpoint="/notices/search (skip: not_registered_au_case4)",
            duration_ms=0,
            started_at=now_kst_iso(),
            finished_at=now_kst_iso(),
        )
    else:
        _t0 = time.time()
        _nsw_started = now_kst_iso()
        try:
            nsw = fetch_buynsw(retail_query)
            log_crawl(
                run_id=run_id, product_code=product_filter, source="buy_nsw",
                status="success" if nsw and nsw.get("contract_value_aud") is not None else "partial",
                endpoint="/notices/search",
                duration_ms=int((time.time() - _t0) * 1000),
                started_at=_nsw_started,
                finished_at=now_kst_iso(),
            )
        except Exception as exc:
            nsw = {}
            log_crawl(
                run_id=run_id, product_code=product_filter, source="buy_nsw", status="failed",
                endpoint="/notices/search",
                error_message=str(exc)[:500],
                duration_ms=int((time.time() - _t0) * 1000),
                started_at=_nsw_started,
                finished_at=now_kst_iso(),
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
    # Phase 4.8 — multi/withdrawal 경로는 `_raw_rows` 에 성분별 DTO 가 있음.
    # 각 성분별로 1행씩 insert. endpoint_items 가 비어있으면 skip (NOT NULL 위반 방지).
    if pbs.get("pbs_found") or pbs.get("pbs_listed"):
        raw_row_list: list[dict[str, Any]] = []
        candidate_rows = pbs.get("_raw_rows") if isinstance(pbs.get("_raw_rows"), list) else None
        if candidate_rows:
            raw_row_list = candidate_rows
        else:
            raw_row_list = [pbs]

        for r in raw_row_list:
            try:
                r_raw = r.get("raw_response") or {}
                endpoint_items = r_raw.get("items") if isinstance(r_raw, dict) else None
                endpoint_disp = r_raw.get("dispensing_rule") if isinstance(r_raw, dict) else None
                # Phase 4.8 — endpoint_items 가 없으면 insert 스킵 (NOT NULL 위반 방지).
                # Chemist fallback 성분행처럼 items 가 비어있는 경우 감당.
                if not endpoint_items:
                    continue
                snapshot = {
                    "product_id": product_filter,
                    "pbs_code": r.get("pbs_code") or r.get("pbs_item_code"),
                    "schedule_code": r.get("schedule_code"),
                    "effective_date": r.get("first_listed_date"),
                    "endpoint_items": endpoint_items,
                    "endpoint_dispensing_rules": endpoint_disp or {},
                    # Phase 4.3-v3 — 호주 PBS 시장 제형·강도 (시장조사 비교용).
                    # DTO 는 `market_form`/`market_strength`, raw API 는 `form`/`strength`.
                    "market_form": r.get("market_form") or (endpoint_items.get("form") if isinstance(endpoint_items, dict) else None),
                    "market_strength": r.get("market_strength") or (endpoint_items.get("strength") if isinstance(endpoint_items, dict) else None),
                    # TODO(v2-pbs-full): /fees, /markup-bands, /copayments, /atc raw 보관
                    "api_fetched_at": now_kst_iso(),
                    "crawled_at": now_kst_iso(),
                }
                upsert_pbs_raw(snapshot)
            except Exception as exc:
                print(f"[au_pbs_raw upsert 경고] {exc}", flush=True)

    # ── au_tga_artg (§14-3-3) — TGA 원본 보관 ───────────────
    # Phase Omethyl — tga_artg_details 배열 전부 iterate 해서 다행 INSERT.
    # 대표 ARTG 1건만 저장하던 기존 방식은 ESTIMATE_private 같은 케이스에서
    # "같은 성분 다른 제품" 을 전부 놓쳤음. 각 엔트리는 match_type 으로 라벨링
    # (exact / same_ingredient_diff_form). tga_artg_details 가 비어있으면
    # 레거시 단일 ARTG 경로 fallback.
    artg_details = tga.get("tga_artg_details") or []
    if artg_details:
        for entry in artg_details:
            try:
                aid = entry.get("artg_id")
                if not aid:
                    continue
                artg_row = {
                    "product_id": product_filter,
                    "artg_id": str(aid),
                    "product_name": product.get("product_name_ko"),
                    "sponsor_name": entry.get("sponsor_name"),
                    "active_ingredients": entry.get("active_ingredients") or [],
                    "strength": entry.get("strength") or product.get("strength"),
                    "dosage_form": entry.get("dosage_form") or product.get("dosage_form"),
                    "status": "registered",
                    "artg_url": f"https://www.tga.gov.au/resources/artg/{aid}",
                    "match_type": entry.get("match_type"),  # Phase Omethyl 신규
                    "crawled_at": now_kst_iso(),
                }
                upsert_tga_artg(artg_row)
            except Exception as exc:
                print(f"[au_tga_artg upsert 경고] {exc}", flush=True)
    elif tga.get("artg_id") or tga.get("artg_number"):
        # Fallback (expected_inns 미제공·구버전 DTO 호환)
        try:
            artg_row = {
                "product_id": product_filter,
                "artg_id": tga.get("artg_id") or str(tga.get("artg_number") or ""),
                "product_name": product.get("product_name_ko"),
                "sponsor_name": tga.get("sponsor_name") or tga.get("tga_sponsor"),
                "active_ingredients": tga.get("active_ingredients") or [],
                "strength": tga.get("strength") or product.get("strength"),
                "dosage_form": tga.get("dosage_form") or product.get("dosage_form"),
                "status": tga.get("status") or tga.get("artg_status"),
                "artg_url": tga.get("artg_url") or tga.get("artg_source_url"),
                "match_type": tga.get("match_type"),
                "crawled_at": now_kst_iso(),
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


def main(argv: list[str] | None = None, product_id: str | None = None) -> None:
    """CLI 진입점 — argparse + DRY_RUN 지원.

    argv / product_id:
      - **CLI** (``argv is None``): ``sys.argv[1:]`` 를 argparse 로 파싱 — ``--product`` / ``--all``.
      - **프로그래밍** (``product_id`` 비어 있지 않음): 해당 품목만 처리. FastAPI 등에서는
        ``main([], product_id=...)`` 로 호출 — uvicorn 이 넣은 ``sys.argv`` 와 분리.
      - ``argv == []`` 이고 ``product_id`` 없음: 잘못된 임베드 호출 (종료 코드 2).

    사용 예:
      # 단일 품목 (CLI)
      python -m crawler.au_crawler --product au-hydrine-004
      # 전체 8 품목 순회
      python -m crawler.au_crawler --all
      # DB 쓰기 skip (dry-run)
      DRY_RUN=1 python -m crawler.au_crawler --product au-hydrine-004

    종료 코드: 전 품목 성공 0, 실패 하나라도 있으면 1. 잘못된 임베드 호출 2.
    """
    # DRY_RUN — 1, true, yes 모두 수용
    dry_run_raw = (os.environ.get("DRY_RUN") or "").strip().lower()
    dry_run = dry_run_raw in {"1", "true", "yes", "on"}

    pid_arg = (product_id or "").strip()
    selected_ids: list[str] | None

    if pid_arg:
        selected_ids = [pid_arg]
    elif argv is not None and len(argv) == 0:
        print(
            "[오류] 프로그래밍 호출 시 product_id 인자가 필요합니다. "
            "예: run_crawler('au-hydrine-004') 또는 main([], product_id='…').",
            file=sys.stderr,
        )
        sys.exit(2)
    else:
        parser = argparse.ArgumentParser(description="호주 의약품 크롤러 v2 (위임지서 03a)")
        parser.add_argument(
            "--product",
            metavar="PRODUCT_ID",
            help="단일 품목 크롤 (예: au-hydrine-004).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="au_products.json 에 정의된 전체 품목 순회. --product 없을 때만 적용.",
        )
        args = parser.parse_args(argv) if argv is not None else parser.parse_args()
        if args.product:
            selected_ids = [args.product.strip()]
        elif args.all:
            selected_ids = None
        else:
            print(
                "[오류] 대상 품목 지정 필요 — --product <id> 또는 --all",
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


def run_crawler(product_id: str) -> None:
    """웹 API 등에서 호출 — 품목은 인자로만 전달 (프로세스 환경변수로 품목 지정하지 않음)."""
    main([], product_id=product_id)


if __name__ == "__main__":
    main()
