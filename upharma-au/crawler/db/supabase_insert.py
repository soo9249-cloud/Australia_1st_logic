# build_product_summary로 만든 dict를 Supabase au_products 테이블에 UPSERT한다.
# v2 스키마 (au_ prefix 10 테이블) — 2026-04-18
# 스펙: /AX 호주 final/01_보고서필드스키마_v1.md §14-3

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from supabase import Client, create_client

# ── v2 스키마: 메인 마스터 테이블 이름 변경 ──────────────────────────────────
TABLE_NAME = "au_products"

# 보조 테이블 이름 (upsert 헬퍼 함수용)
TABLE_PBS_RAW          = "au_pbs_raw"
TABLE_TGA_ARTG         = "au_tga_artg"
TABLE_BUYERS           = "au_buyers"
TABLE_REPORTS_R1       = "au_reports_r1"
TABLE_REPORTS_R2       = "au_reports_r2"
TABLE_REPORTS_R3       = "au_reports_r3"
TABLE_REPORT_REFS      = "au_report_refs"
TABLE_CRAWL_LOG        = "au_crawl_log"
TABLE_REPORTS_HISTORY  = "au_reports_history"


# ── au_products 화이트리스트 (§14-3-1 컬럼 전부, id/created_at/updated_at 제외) ─
# PostgREST 에 전달 가능한 컬럼만 허용 (알 수 없는 키로 인한 오류 방지).
# 카테고리별 정렬: 식별자 → TGA → PBS → 가격 → 경쟁 → 내부 → 메타
_ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        # 식별자
        "product_code",
        "product_name_ko",
        "inn_normalized",
        "strength",
        "dosage_form",
        # Case 분기
        "case_code",
        "case_risk_text_ko",
        # TGA 블록
        "tga_found",
        "tga_artg_ids",
        "tga_sponsors",
        # PBS 블록
        "pbs_found",
        "pbs_code",
        "program_code",
        "section_85_100",
        "formulary",
        "aemp_aud",
        "aemp_usd",
        "aemp_krw",
        "spd_aud",
        "claimed_price_aud",
        "dpmq_aud",
        "dpmq_usd",
        "dpmq_krw",
        "mn_pharmacy_price_aud",
        "brand_premium_aud",
        "therapeutic_group_premium_aud",
        "special_patient_contrib_aud",
        "wholesale_markup_band",
        "pharmacy_markup_code",
        "markup_variable_pct",
        "markup_offset_aud",
        "markup_fixed_aud",
        "dispensing_fee_aud",
        "ahi_fee_aud",
        "originator_brand",
        "therapeutic_group_id",
        "brand_substitution_group_id",
        "atc_code",
        "policy_imdq60",
        "policy_biosim",
        "section_19a_expiry",
        "authority_method",
        "copay_general_aud",
        "copay_concessional_aud",
        "first_listed_date",
        "pack_size",
        "pricing_quantity",
        "maximum_prescribable_pack",
        # 소매 가격 블록 (Chemist / Healthylife 통합)
        "retail_price_aud",
        "retail_estimation_method",
        "chemist_price_aud",
        "chemist_url",
        # 경쟁 현황
        "originator_brand_name",
        "originator_sponsor",
        "top_generics",
        "competitor_count",
        "market_tier",
        # 내부 (UI 노출 금지)
        "situation_summary",
        "confidence",
        "ingredients_split",
        "similar_drug_used",
        "hospital_only_flag",
        "ai_deep_research_raw",
        # 메타
        "schedule_code",
        "last_crawled_at",
        "crawler_source_urls",
        "error_type",
        "warnings",
    }
)


# ── au_crawler 반환 dict 키 → au_products 컬럼 rename 매핑 ────────────────────
# au_crawler.build_product_summary() 가 "product_id" 키로 값을 넘겨주므로
# v2 테이블의 "product_code" 로 자동 rename. 그 외 키는 의미 동일 + rename 불필요.
_KEY_RENAME_AU_PRODUCTS: dict[str, str] = {
    "product_id":            "product_code",
    # 기존 pbs_item_code → 신규 pbs_code (같은 의미)
    "pbs_item_code":         "pbs_code",
    # 기존 pbs_determined_price → 신규 aemp_aud (PBS AEMP 공식값)
    "pbs_determined_price":  "aemp_aud",
    # 기존 pbs_dpmq → 신규 dpmq_aud
    "pbs_dpmq":              "dpmq_aud",
    # 기존 pbs_program_code → 신규 program_code
    "pbs_program_code":      "program_code",
    # 기존 pbs_formulary → 신규 formulary
    "pbs_formulary":         "formulary",
    # 기존 pbs_pack_size → 신규 pack_size
    "pbs_pack_size":         "pack_size",
    # 기존 pbs_pricing_quantity → 신규 pricing_quantity
    "pbs_pricing_quantity":  "pricing_quantity",
    # 기존 pbs_listed → 신규 pbs_found
    "pbs_listed":            "pbs_found",
    # 기존 artg_status='registered' → 신규 tga_found (BOOLEAN 로 별도 변환)
    "crawled_at":            "last_crawled_at",
    "price_source_url":      "chemist_url",
}


_client: Client | None = None


def _load_dotenv_if_present() -> None:
    """상위 경로에서 .env를 찾아 로드한다(이미 설정된 환경변수는 덮어쓰지 않음)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    p = Path(__file__).resolve().parent
    for _ in range(8):
        env_path = p / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            return
        if p.parent == p:
            break
        p = p.parent


def get_supabase_client() -> Client:
    """Supabase 동기 클라이언트(모듈 단일 인스턴스)."""
    global _client
    if _client is not None:
        return _client
    _load_dotenv_if_present()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    _client = create_client(url, key)
    return _client


def _row_for_upsert(summary: dict[str, Any]) -> dict[str, Any]:
    """au_crawler 반환 dict → au_products 컬럼 dict 로 변환.

    1. 키 rename (product_id → product_code, pbs_item_code → pbs_code 등)
    2. id / created_at / updated_at 제거 (자동 채워짐)
    3. _ALLOWED_COLUMNS 화이트리스트 필터
    4. artg_status 필드는 존재하면 tga_found BOOLEAN 으로 변환 (파생)
    """
    out: dict[str, Any] = {}

    # artg_status 가 있으면 tga_found 파생 (summary 원본 유지)
    artg_status_val = summary.get("artg_status")
    if artg_status_val is not None and "tga_found" not in summary:
        out["tga_found"] = (str(artg_status_val).lower() == "registered")

    for k, v in summary.items():
        if k in ("id", "created_at", "updated_at"):
            continue
        # 1) 키 rename
        new_key = _KEY_RENAME_AU_PRODUCTS.get(k, k)
        # 2) 화이트리스트 필터
        if new_key in _ALLOWED_COLUMNS:
            out[new_key] = v
    return out


def upsert_product(summary: dict[str, Any]) -> bool:
    """au_products 테이블에 UPSERT. 충돌 기준: product_code."""
    label = summary.get("product_name_ko") or summary.get("product_id") or "?"
    try:
        client = get_supabase_client()
        row = _row_for_upsert(summary)
        response = (
            client.table(TABLE_NAME)
            .upsert(row, on_conflict="product_code")
            .execute()
        )
        result = response.data
        print(f"[INSERT au_products] {label} → {result}")
        return True
    except Exception as exc:  # 스펙: 예외 전파 금지
        print(f"[INSERT au_products 실패] {label}: {exc}")
        return False


def upsert_all(summaries: list[dict[str, Any]]) -> dict[str, int]:
    """summaries를 순서대로 upsert_product 호출."""
    success = 0
    fail = 0
    for item in summaries:
        if upsert_product(item):
            success += 1
        else:
            fail += 1
    return {"success": success, "fail": fail}


# ═══════════════════════════════════════════════════════════════════════
# 신규 보조 함수 — au_pbs_raw / au_tga_artg / au_crawl_log (§14-3-2/3/9)
# PBS · TGA 는 원본 보관소이므로 INSERT only (UPSERT 아님).
# au_crawl_log 는 APPEND-ONLY (UPDATE · DELETE 차단, RLS 정책으로 보장).
# ═══════════════════════════════════════════════════════════════════════

_PBS_RAW_ALLOWED: frozenset[str] = frozenset({
    "product_id",
    "pbs_code",
    "schedule_code",
    "effective_date",
    "endpoint_items",
    "endpoint_dispensing_rules",
    "endpoint_fees",
    "endpoint_markup_bands",
    "endpoint_copayments",
    "endpoint_organisations",
    "endpoint_summary_of_changes",
    "endpoint_atc",
    "endpoint_restrictions",
    "api_fetched_at",
    "crawled_at",
})


_TGA_ARTG_ALLOWED: frozenset[str] = frozenset({
    "product_id",
    "artg_id",
    "product_name",
    "sponsor_name",
    "sponsor_abn",
    "active_ingredients",
    "strength",
    "dosage_form",
    "route_of_administration",
    "schedule",
    "first_registered_date",
    "status",
    "artg_url",
    "crawled_at",
})


_CRAWL_LOG_ALLOWED: frozenset[str] = frozenset({
    "run_id",
    "product_id",
    "source_name",
    "endpoint",
    "status",
    "http_status",
    "retry_count",
    "error_message",
    "duration_ms",
    "started_at",
    "finished_at",
    "raw_response_truncated",
})


def _filter_cols(row: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k in allowed}


def upsert_pbs_raw(snapshot: dict[str, Any]) -> bool:
    """au_pbs_raw 에 INSERT. (pbs_code, schedule_code) UNIQUE 충돌 시 UPDATE.

    snapshot 키 예시:
      product_id, pbs_code, schedule_code, effective_date,
      endpoint_items (JSONB), endpoint_dispensing_rules (JSONB), ...
      api_fetched_at, crawled_at
    """
    label = f"{snapshot.get('pbs_code', '?')} / {snapshot.get('schedule_code', '?')}"
    try:
        client = get_supabase_client()
        row = _filter_cols(snapshot, _PBS_RAW_ALLOWED)
        response = (
            client.table(TABLE_PBS_RAW)
            .upsert(row, on_conflict="pbs_code,schedule_code")
            .execute()
        )
        print(f"[INSERT au_pbs_raw] {label} → {response.data}")
        return True
    except Exception as exc:
        print(f"[INSERT au_pbs_raw 실패] {label}: {exc}")
        return False


def upsert_tga_artg(row: dict[str, Any]) -> bool:
    """au_tga_artg 에 UPSERT. artg_id UNIQUE 기준.

    row 키: product_id, artg_id, product_name, sponsor_name, sponsor_abn,
            active_ingredients(JSONB), strength, dosage_form, ...
    """
    artg_id = row.get("artg_id") or "?"
    try:
        client = get_supabase_client()
        data = _filter_cols(row, _TGA_ARTG_ALLOWED)
        response = (
            client.table(TABLE_TGA_ARTG)
            .upsert(data, on_conflict="artg_id")
            .execute()
        )
        print(f"[INSERT au_tga_artg] {artg_id} → {response.data}")
        return True
    except Exception as exc:
        print(f"[INSERT au_tga_artg 실패] {artg_id}: {exc}")
        return False


def insert_crawl_log(row: dict[str, Any]) -> bool:
    """au_crawl_log 에 INSERT only (APPEND-ONLY — UPDATE/DELETE 금지).

    row 키: run_id(UUID), product_id, source_name, endpoint, status,
            http_status, retry_count, error_message, duration_ms,
            started_at, finished_at, raw_response_truncated
    """
    src = row.get("source_name", "?")
    try:
        client = get_supabase_client()
        data = _filter_cols(row, _CRAWL_LOG_ALLOWED)
        response = client.table(TABLE_CRAWL_LOG).insert(data).execute()
        # 로그가 너무 시끄러울 수 있으므로 실패 외에는 짧게만
        print(f"[INSERT au_crawl_log] {src}/{row.get('endpoint', '-')} [{row.get('status', '-')}]")
        return True
    except Exception as exc:
        print(f"[INSERT au_crawl_log 실패] {src}: {exc}")
        return False
