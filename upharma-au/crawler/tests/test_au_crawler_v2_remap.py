"""au_crawler v2 remap 단위 테스트 — 위임지서 03a §3.

실행:
  cd upharma-au
  pytest crawler/tests/test_au_crawler_v2_remap.py -v

또는 프로젝트 루트에서:
  pytest upharma-au/crawler/tests/test_au_crawler_v2_remap.py -v

필요 패키지: pytest, python-dotenv, supabase, httpx (requirements.txt 기준).
supabase client 는 호출하지 않음 (순수 유닛 — 모킹·환경변수 불필요).
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

# crawler 디렉토리를 import path 에 추가 (au_crawler.py 가 `from sources.xxx`, `from utils.xxx` 사용)
_CRAWLER_DIR = Path(__file__).resolve().parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

# Supabase 호출 방지 — dotenv 로드 차단 (빈 SUPABASE_URL 등 필요 없음)
os.environ.setdefault("FX_AUD_USD", "0.65")
os.environ.setdefault("FX_AUD_KRW", "920")


# ─────────────────────────────────────────────────────────────────────
# Test 1 — v1 키 backward compat
# ─────────────────────────────────────────────────────────────────────

def test_v1_to_v2_key_rename() -> None:
    """옛 v1 키(pbs_item_code, pbs_determined_price 등) 로 만든 summary dict 를
    _row_for_upsert 에 넣어도 rename 매핑으로 v2 컬럼(pbs_code, aemp_aud 등) 에
    정확히 도달하는지 확인. 지시서 §2-1 "rename 매핑 no-op 방어용 유지" 보장.
    """
    from db.supabase_insert import _row_for_upsert

    v1_summary = {
        "product_id": "au-hydrine-004",
        "product_name_ko": "Hydrine",
        "inn_normalized": "hydroxycarbamide",
        "pbs_item_code": "1234A",
        "pbs_determined_price": Decimal("31.92"),
        "pbs_dpmq": Decimal("48.11"),
        "pbs_program_code": "GE",
        "pbs_formulary": "F1",
        "pbs_pack_size": 100,
        "pbs_pricing_quantity": 1,
        "pbs_listed": True,
        "crawled_at": "2026-04-18T14:00:00Z",
        "price_source_url": "https://www.chemistwarehouse.com.au/search?query=hydroxyurea",
        "artg_status": "registered",
    }

    row = _row_for_upsert(v1_summary)

    # v2 컬럼으로 rename 됨
    assert row["product_code"] == "au-hydrine-004", "product_id → product_code"
    assert row["pbs_code"] == "1234A", "pbs_item_code → pbs_code"
    assert row["aemp_aud"] == Decimal("31.92"), "pbs_determined_price → aemp_aud"
    assert row["dpmq_aud"] == Decimal("48.11"), "pbs_dpmq → dpmq_aud"
    assert row["program_code"] == "GE", "pbs_program_code → program_code"
    assert row["formulary"] == "F1", "pbs_formulary → formulary"
    assert row["pack_size"] == 100, "pbs_pack_size → pack_size"
    assert row["pricing_quantity"] == 1, "pbs_pricing_quantity → pricing_quantity"
    assert row["pbs_found"] is True, "pbs_listed → pbs_found"
    assert row["last_crawled_at"] == "2026-04-18T14:00:00Z", "crawled_at → last_crawled_at"
    assert row["chemist_url"] == v1_summary["price_source_url"], "price_source_url → chemist_url"

    # artg_status='registered' → tga_found=True 파생
    assert row["tga_found"] is True, "artg_status='registered' → tga_found=True"


# ─────────────────────────────────────────────────────────────────────
# Test 2 — PBS 미등재 품목 NULL 채움
# ─────────────────────────────────────────────────────────────────────

def test_pbs_unlisted_fills_nulls() -> None:
    """PBS 미등재 품목(Omethyl 등 OTC) → pbs_found=False, aemp_aud/dpmq_aud 등
    PBS 관련 컬럼이 전부 None 이어야. 컬럼 key 자체는 반드시 존재해야 함
    (위임지서 §2-1 "PBS 미등재 시 None 으로 채움, key 누락 금지").
    """
    from sources.pbs import _empty_dto

    dto = _empty_dto()

    # 미등재 플래그
    assert dto["pbs_found"] is False
    # PBS 관련 key 는 전부 존재하지만 값은 None
    pbs_fields = [
        "pbs_code", "li_item_id", "schedule_code",
        "drug_name", "brand_name", "manufacturer_code",
        "program_code", "section_85_100", "formulary",
        "aemp_aud", "spd_aud", "claimed_price_aud",
        "dpmq_aud", "mn_pharmacy_price_aud",
        "brand_premium_aud", "therapeutic_group_premium_aud", "special_patient_contrib_aud",
        "wholesale_markup_band", "pharmacy_markup_code",
        "markup_variable_pct", "markup_offset_aud", "markup_fixed_aud",
        "dispensing_fee_aud", "ahi_fee_aud",
        "originator_brand", "atc_code",
        "policy_imdq60", "policy_biosim",
        "copay_general_aud", "copay_concessional_aud",
        "authority_method", "first_listed_date",
    ]
    for key in pbs_fields:
        assert key in dto, f"PBSItemDTO 에 '{key}' 키가 반드시 있어야 함"
        assert dto[key] is None, f"PBS 미등재 시 '{key}' 는 None 이어야 함 (실제: {dto[key]!r})"

    # sponsors 는 빈 배열 (None 아님)
    assert dto["sponsors"] == []
    # raw_response 는 빈 dict
    assert dto["raw_response"] == {}
    # 메타는 채워져 있음 (source_name, source_url)
    assert dto["source_name"] == "pbs_api_v3"
    assert dto["source_url"]  # non-empty


# ─────────────────────────────────────────────────────────────────────
# Test 3 — FX 환산 (AUD → USD, KRW)
# ─────────────────────────────────────────────────────────────────────

def test_fx_conversion() -> None:
    """utils/fx.py 환산 결과 검증. 환경변수 FX_AUD_USD=0.65, FX_AUD_KRW=920 기준.

      aemp_aud = Decimal("10.00")  →  aemp_usd = Decimal("6.50"), aemp_krw = Decimal("9200")
      aemp_aud = Decimal("48.11")  →  aemp_usd = Decimal("31.27"), aemp_krw = Decimal("44261")
    """
    from utils.fx import aud_to_usd, aud_to_krw

    # 기본 케이스
    assert aud_to_usd(Decimal("10.00")) == Decimal("6.50")
    assert aud_to_krw(Decimal("10.00")) == Decimal("9200")

    # 실제 DPMQ 값 샘플 (Hydrine 48.11)
    assert aud_to_usd(Decimal("48.11")) == Decimal("31.27")
    assert aud_to_krw(Decimal("48.11")) == Decimal("44261")

    # None 은 그대로 None
    assert aud_to_usd(None) is None
    assert aud_to_krw(None) is None

    # int/float/str 도 Decimal 처리
    assert aud_to_usd(10) == Decimal("6.50")
    assert aud_to_usd(10.0) == Decimal("6.50")
    assert aud_to_usd("10.00") == Decimal("6.50")

    # 잘못된 입력 → None
    assert aud_to_usd("invalid") is None


# ─────────────────────────────────────────────────────────────────────
# Test 4 — 바이어 후보 풀 중복 병합
# ─────────────────────────────────────────────────────────────────────

def test_buyer_candidates_dedup() -> None:
    """같은 회사명이 TGA sponsor 와 PBS sponsor 양쪽에서 나오면
    source_flags={"tga": True, "pbs": True} 1 행으로 병합되어야 함 (§13-7-B).
    """
    from au_crawler import _collect_buyer_candidates

    # TGA sponsor + PBS sponsor 둘 다 "Apotex" 라는 가정
    tga = {
        "tga_found": True,
        "tga_sponsors": ["Apotex"],
        "sponsor_name": "Apotex",
        "tga_sponsor": "Apotex",
    }
    pbs = {
        "pbs_found": True,
        "sponsors": ["Apotex"],
    }
    nsw = {
        "agency": "NSW Health",  # 다른 회사 — 별도 행
    }

    candidates = _collect_buyer_candidates(
        product_id="au-hydrine-004", tga=tga, pbs=pbs, nsw=nsw,
    )

    # Apotex 1 행 + NSW Health 1 행 = 2 행
    assert len(candidates) == 2

    # Apotex 찾기 (대소문자 무관)
    apotex = next((c for c in candidates if c["company_name"].upper() == "APOTEX"), None)
    assert apotex is not None, "Apotex 병합된 후보가 있어야 함"
    # TGA 와 PBS 소스 플래그 둘 다 True
    assert apotex["source_flags"].get("tga") is True
    assert apotex["source_flags"].get("pbs") is True
    # NSW 는 다른 회사이므로 False (또는 미설정)
    assert apotex["source_flags"].get("nsw") is not True
    # PSI 점수는 이번 위임 범위 밖 → None
    assert apotex["psi_total"] is None
    assert apotex["rank"] is None
    # product_id 전달됨
    assert apotex["product_id"] == "au-hydrine-004"

    # NSW Health 찾기
    nsw_row = next((c for c in candidates if c["company_name"].upper() == "NSW HEALTH"), None)
    assert nsw_row is not None, "NSW Health 후보가 있어야 함"
    assert nsw_row["source_flags"].get("nsw") is True
    assert nsw_row["source_flags"].get("tga") is not True
    assert nsw_row["source_flags"].get("pbs") is not True
