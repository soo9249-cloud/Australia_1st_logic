# build_product_summary로 만든 dict를 Supabase australia 테이블에 UPSERT한다.

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from supabase import Client, create_client

TABLE_NAME = "australia"

# PostgREST에 전달 가능한 컬럼만 허용(알 수 없는 키로 인한 오류 방지)
_ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        "product_id",
        "market_segment",
        "fob_estimated_usd",
        "confidence",
        "crawled_at",
        "product_name_ko",
        "inn_normalized",
        "hs_code_6",
        "dosage_form",
        "strength",
        "artg_number",
        "artg_status",
        "tga_schedule",
        "tga_licence_category",
        "tga_licence_status",
        "tga_sponsor",
        "artg_source_url",
        "pbs_listed",
        "pbs_item_code",
        "pbs_price_aud",
        "pbs_dpmq",
        "pbs_patient_charge",
        "pbs_determined_price",
        "pbs_pack_size",
        "pbs_pricing_quantity",
        "pbs_benefit_type",
        "pbs_program_code",
        "pbs_brand_name",
        "pbs_innovator",
        "pbs_first_listed_date",
        "pbs_repeats",
        "pbs_formulary",
        "pbs_restriction",
        "pbs_total_brands",
        "pbs_brands",
        "pbs_source_url",
        "pbs_web_source_url",
        "nsw_contract_value_aud",
        "nsw_supplier_name",
        "nsw_contract_date",
        "nsw_source_url",
        "fob_local_ref_aud",
        "fob_conservative_usd",
        "fob_base_usd",
        "fob_aggressive_usd",
        "fob_confidence",
        "retail_price_aud",
        "price_source_name",
        "price_source_url",
        "price_unit",
        "pricing_case",
        "export_viable",
        "reason_code",
        "evidence_url",
        "evidence_text",
        "evidence_text_ko",
        "sites",
        "completeness_ratio",
        "data_source_count",
        "error_type",
        "block2_market",
        "block2_regulatory",
        "block2_trade",
        "block2_procurement",
        "block2_channel",
        "block3_channel",
        "block3_pricing",
        "block3_partners",
        "block3_risks",
        "perplexity_refs",
        "llm_model",
        "llm_generated_at",
    }
)

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
    """id는 제거하고 허용 컬럼만 남긴다(INSERT 시 id는 DB 기본값)."""
    out: dict[str, Any] = {}
    for k, v in summary.items():
        if k == "id":
            continue
        if k in _ALLOWED_COLUMNS:
            out[k] = v
    return out


def upsert_product(summary: dict[str, Any]) -> bool:
    """australia 테이블에 UPSERT. 충돌 기준: product_id."""
    label = summary.get("product_name_ko") or summary.get("product_id") or "?"
    try:
        client = get_supabase_client()
        row = _row_for_upsert(summary)
        response = (
            client.table(TABLE_NAME)
            .upsert(row, on_conflict="product_id")
            .execute()
        )
        result = response.data
        print(f"[INSERT] {label} → {result}")
        return True
    except Exception as exc:  # 스펙: 예외 전파 금지
        print(f"[INSERT 실패] {label}: {exc}")
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
