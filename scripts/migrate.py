"""Supabase 스키마 자동 배포 — Management API (/v1/projects/{ref}/database/query)

upharma-au/crawler/db/australia_table.sql 전체를 Supabase 에 한 번에 실행한다.
여러 SQL 문장을 세미콜론으로 분리하지 않고 그대로 보내며, Supabase 가 서버측에서
단일 connection 안에서 순차 실행한다 (DDL/DML 모두 지원).

v2 (2026-04-18): au_ prefix 10 테이블 검증으로 전환.
  - 기존 _verify_columns(australia), _verify_p2_results_schema(australia_p2_results)
    → _verify_au_products + _verify_au_reports_r2 + 8개 신규 verify 함수

전제 (.env)
-----------
    SUPABASE_URL            https://{ref}.supabase.co
    SUPABASE_ACCESS_TOKEN   Personal Access Token (sbp_...)
                            https://supabase.com/dashboard/account/tokens 에서 발급

실행
----
    python scripts/migrate.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "upharma-au" / "crawler" / "db" / "australia_table.sql"
# 신약 분석·AEMP 출처 추적 등 순차 DDL (파일이 있으면 australia_table.sql 직후 실행)
EXTRA_MIGRATION_SQL = ROOT / "scripts" / "migrations" / "20260419_new_drug_support.sql"
_MGMT_API = "https://api.supabase.com/v1/projects/{ref}/database/query"

# crawler/db/supabase_insert.py 에서 _ALLOWED_COLUMNS 를 직접 import 하기 위한 경로.
# upsert 시 id 는 항상 제거되므로 _ALLOWED_COLUMNS 에 id 가 없다 → 비교 시 {'id'} 추가.
_CRAWLER_DIR = ROOT / "upharma-au" / "crawler"
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))


def _load_env() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    pat = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    if not url or not pat:
        print("[오류] .env 에 아래 2 개 변수가 필요합니다:", file=sys.stderr)
        print("  SUPABASE_URL=https://{ref}.supabase.co", file=sys.stderr)
        print("  SUPABASE_ACCESS_TOKEN=sbp_xxxxxxxxxxxxxxxx", file=sys.stderr)
        print("  (PAT 발급: https://supabase.com/dashboard/account/tokens)", file=sys.stderr)
        sys.exit(1)
    return url, pat


def _project_ref(url: str) -> str:
    m = re.match(r"https://([a-z0-9]+)\.supabase\.co", url)
    if not m:
        print(f"[오류] SUPABASE_URL 에서 project ref 추출 실패: {url}", file=sys.stderr)
        sys.exit(1)
    return m.group(1)


def _run_query(ref: str, pat: str, sql: str, *, timeout: float = 120.0) -> tuple[int, object]:
    url = _MGMT_API.format(ref=ref)
    headers = {"Authorization": f"Bearer {pat}", "Content-Type": "application/json"}
    r = httpx.post(url, headers=headers, json={"query": sql}, timeout=timeout)
    try:
        body: object = r.json() if r.content else []
    except Exception:
        body = r.text
    return r.status_code, body


def main() -> int:
    url, pat = _load_env()
    ref = _project_ref(url)

    if not SQL_FILE.is_file():
        print(f"[오류] SQL 파일 없음: {SQL_FILE}", file=sys.stderr)
        return 1
    sql = SQL_FILE.read_text(encoding="utf-8")

    print(f"[INFO] project ref      = {ref}")
    print(f"[INFO] SQL 파일         = {SQL_FILE.relative_to(ROOT)} ({len(sql):,} chars)")
    print(f"[INFO] POST {_MGMT_API.format(ref=ref)}")

    # 1) SQL 전체 실행
    try:
        status, body = _run_query(ref, pat, sql)
    except Exception as exc:
        print(f"[오류] 네트워크: {exc}", file=sys.stderr)
        return 1

    ok = 200 <= status < 300
    print(f"[INFO] HTTP {status} {'✓' if ok else '✗'}")
    if not ok:
        print("[BODY]", json.dumps(body, indent=2, ensure_ascii=False)[:1200])
        return 1

    # 1b) 타임스탬프 마이그레이션 (선택): 파일이 있으면 같은 Management API 로 순차 실행
    if EXTRA_MIGRATION_SQL.is_file():
        extra = EXTRA_MIGRATION_SQL.read_text(encoding="utf-8")
        print(f"[INFO] 추가 마이그레이션   = {EXTRA_MIGRATION_SQL.relative_to(ROOT)} ({len(extra):,} chars)")
        try:
            status_m, body_m = _run_query(ref, pat, extra, timeout=180.0)
        except Exception as exc:
            print(f"[오류] 추가 마이그레이션 네트워크: {exc}", file=sys.stderr)
            return 1
        ok_m = 200 <= status_m < 300
        print(f"[INFO] HTTP {status_m} (추가 마이그레이션) {'✓' if ok_m else '✗'}")
        if not ok_m:
            print("[BODY]", json.dumps(body_m, indent=2, ensure_ascii=False)[:1200])
            return 1

    # 2) PostgREST 스키마 캐시 강제 리로드
    #    ALTER 로 추가한 신규 컬럼이 supabase-py upsert 에서 PGRST204 로 튕기는 것을 방지.
    notify_status, _ = _run_query(ref, pat, "NOTIFY pgrst, 'reload schema';")
    print(f"[INFO] NOTIFY pgrst 'reload schema' → HTTP {notify_status}")

    # 3) 테이블 목록 출력 (au_ prefix 10 개 + au_regulatory 확인)
    print()
    print("[검증] public 스키마 테이블 목록")
    _, tables = _run_query(
        ref, pat,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' ORDER BY table_name;",
    )
    if isinstance(tables, list):
        for row in tables:
            name = row.get("table_name") if isinstance(row, dict) else row
            print(f"  · {name}")

    _, seed_cnt = _run_query(ref, pat, "SELECT COUNT(*)::int AS n FROM au_regulatory;")
    if isinstance(seed_cnt, list) and seed_cnt and isinstance(seed_cnt[0], dict):
        print(f"  · au_regulatory 시드 = {seed_cnt[0].get('n')} 행")

    # 4) v2 테이블 10 개 각각 검증 — 하나라도 fail 시 exit 1
    print()
    print("[v2 스키마 검증] 10 개 테이블 컬럼 대조")
    results: list[tuple[str, bool]] = [
        ("au_products",         _verify_au_products(ref, pat)),
        ("au_pbs_raw",          _verify_au_pbs_raw(ref, pat)),
        ("au_tga_artg",         _verify_au_tga_artg(ref, pat)),
        ("au_reports_r1",       _verify_au_reports_r1(ref, pat)),
        ("au_reports_r2",       _verify_au_reports_r2(ref, pat)),
        ("au_reports_r3",       _verify_au_reports_r3(ref, pat)),
        ("au_buyers",           _verify_au_buyers(ref, pat)),
        ("au_report_refs",      _verify_au_report_refs(ref, pat)),
        ("au_crawl_log",        _verify_au_crawl_log(ref, pat)),
        ("au_reports_history",  _verify_au_reports_history(ref, pat)),
    ]

    failed = [name for name, ok in results if not ok]
    if failed:
        print()
        print(f"[실패] 검증 실패 테이블 {len(failed)}개: {', '.join(failed)}")
        return 1

    print()
    print("[완료] Supabase v2 스키마 배포 성공 (au_ prefix 10 tables)")
    return 0


# ═══════════════════════════════════════════════════════════════════════
# 공통 헬퍼: 테이블의 information_schema 컬럼 집합 조회
# ═══════════════════════════════════════════════════════════════════════

def _fetch_actual_columns(ref: str, pat: str, table: str) -> set[str]:
    """information_schema.columns 에서 해당 테이블의 실제 컬럼 이름 집합."""
    _, rows = _run_query(
        ref, pat,
        f"SELECT column_name FROM information_schema.columns "
        f"WHERE table_schema='public' AND table_name='{table}';",
    )
    actual: set[str] = set()
    if isinstance(rows, list):
        for row in rows:
            name = row.get("column_name") if isinstance(row, dict) else None
            if name:
                actual.add(name)
    return actual


def _verify_table_columns(
    ref: str, pat: str, table: str, expected: set[str],
) -> bool:
    """공통 검증 로직: 기대 컬럼 set vs 실제 DB 컬럼 set 대조.

    반환: 통과 True / 실패 False
    """
    print()
    print(f"[컬럼 검증] {table}")
    actual = _fetch_actual_columns(ref, pat, table)
    missing_in_db = sorted(expected - actual)
    extra_in_db   = sorted(actual - expected)

    print(f"  · 기대 컬럼 수 = {len(expected)} / 실제 DB 컬럼 수 = {len(actual)}")

    ok = True
    if missing_in_db:
        ok = False
        print(f"  ✗ DB 에 없는 컬럼 {len(missing_in_db)}개 (DDL 누락):")
        for c in missing_in_db:
            print(f"      - {c}")
    if extra_in_db:
        # 엄격 실패까지는 아니고 경고 (RLS/트리거가 붙인 숨은 컬럼은 거의 없지만 미래 대비)
        print(f"  ⚠ DB 에만 존재 {len(extra_in_db)}개 (미추적 — expected set 업데이트 검토):")
        for c in extra_in_db:
            print(f"      - {c}")

    if ok:
        print(f"  ✅ {table} 검증 통과")
    return ok


# ═══════════════════════════════════════════════════════════════════════
# 테이블별 verify 함수 10 개 (§14-3 스펙 기준)
# ═══════════════════════════════════════════════════════════════════════

def _verify_au_products(ref: str, pat: str) -> bool:
    """au_products — supabase_insert._ALLOWED_COLUMNS + {id, created_at, updated_at}."""
    try:
        from db.supabase_insert import _ALLOWED_COLUMNS  # type: ignore
    except Exception as exc:
        print(f"[컬럼 검증] au_products — _ALLOWED_COLUMNS import 실패: {exc}")
        return False
    expected = set(_ALLOWED_COLUMNS) | {"id", "created_at", "updated_at"}
    return _verify_table_columns(ref, pat, "au_products", expected)


def _verify_au_pbs_raw(ref: str, pat: str) -> bool:
    expected = {
        "id",
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
        "created_at",
        "market_form",
        "market_strength",
    }
    return _verify_table_columns(ref, pat, "au_pbs_raw", expected)


def _verify_au_tga_artg(ref: str, pat: str) -> bool:
    # Phase 4.3-v3 — schedule / route_of_administration / first_registered_date / sponsor_abn DROP
    expected = {
        "id",
        "product_id",
        "artg_id",
        "product_name",
        "sponsor_name",
        "active_ingredients",
        "strength",
        "dosage_form",
        "status",
        "artg_url",
        "match_type",
        "crawled_at",
        "created_at",
        "updated_at",
    }
    return _verify_table_columns(ref, pat, "au_tga_artg", expected)


def _verify_au_reports_r1(ref: str, pat: str) -> bool:
    expected = {
        "id",
        "product_id",
        "market_overview_ko",
        "entry_channel_ko",
        "partner_direction_ko",
        "sponsor_priority_rationale_ko",
        "case_risk_text_ko",
        "full_json_raw",
        "haiku_model_version",
        "haiku_temperature",
        "haiku_generated_at",
        "validation_passed",
        "validation_errors",
        "created_at",
        "updated_at",
    }
    return _verify_table_columns(ref, pat, "au_reports_r1", expected)


def _verify_au_reports_r2(ref: str, pat: str) -> bool:
    """au_reports_r2 — FOB 3 시나리오 + 사용자 조정 + AI 메타 (§14-3-5)."""
    expected = {
        "id",
        "product_id",
        # 가격 기준선
        "aemp_usd",
        "aemp_krw",
        "dpmq_usd",
        "dpmq_krw",
        "cw_ref_usd",
        "cw_ref_krw",
        "component_sum_basis",
        # 3 시나리오 FOB (9 컬럼)
        "fob_penetration_aud",
        "fob_penetration_usd",
        "fob_penetration_krw",
        "fob_reference_aud",
        "fob_reference_usd",
        "fob_reference_krw",
        "fob_premium_aud",
        "fob_premium_usd",
        "fob_premium_krw",
        # 비율·수수료 (12 컬럼)
        "fob_ratio_penetration",
        "fob_ratio_reference",
        "fob_ratio_premium",
        "agent_fee_ratio_penetration",
        "agent_fee_ratio_reference",
        "agent_fee_ratio_premium",
        "freight_ratio_penetration",
        "freight_ratio_reference",
        "freight_ratio_premium",
        "port_fee_aud_penetration",
        "port_fee_aud_reference",
        "port_fee_aud_premium",
        "recommended_scenario",
        "recommended_scenario_label_ko",
        # 사용자 조정
        "user_adjusted_mode",
        "user_adjust_value",
        "user_final_fob_usd",
        "user_final_fob_krw",
        "user_adjust_note_ko",
        # 마케팅 앵글
        "marketing_angle_key",
        "marketing_angle_text_ko",
        # AI 생성 메타
        "full_json_raw",
        "haiku_model_version",
        "haiku_generated_at",
        "validation_passed",
        "created_at",
        "updated_at",
    }
    return _verify_table_columns(ref, pat, "au_reports_r2", expected)


def _verify_au_reports_r3(ref: str, pat: str) -> bool:
    expected = {
        "id",
        "product_id",
        "psi_weights",
        "top3_approach_ko",
        "full_json_raw",
        "haiku_model_version",
        "haiku_generated_at",
        "validation_passed",
        "created_at",
        "updated_at",
    }
    return _verify_table_columns(ref, pat, "au_reports_r3", expected)


def _verify_au_buyers(ref: str, pat: str) -> bool:
    expected = {
        "id",
        "product_id",
        "rank",
        "company_name",
        "abn",
        "state",
        "psi_sales_scale",
        "psi_pipeline",
        "psi_manufacturing",
        "psi_import_exp",
        "psi_pharmacy_chain",
        "psi_total",
        "source_flags",
        "evidence_urls",
        "created_at",
        "updated_at",
    }
    return _verify_table_columns(ref, pat, "au_buyers", expected)


def _verify_au_report_refs(ref: str, pat: str) -> bool:
    expected = {
        "id",
        "product_id",
        "report_type",
        "citation_index",
        "source",
        "title",
        "url",
        "authors",
        "published_date",
        "accessed_at",
        "snippet_ko",
        "created_at",
    }
    return _verify_table_columns(ref, pat, "au_report_refs", expected)


def _verify_au_crawl_log(ref: str, pat: str) -> bool:
    expected = {
        "id",
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
        "created_at",
    }
    return _verify_table_columns(ref, pat, "au_crawl_log", expected)


def _verify_au_reports_history(ref: str, pat: str) -> bool:
    expected = {
        "id",
        "product_id",
        "gong",
        "snapshot",
        "llm_model",
        "generated_at",
        "created_at",
    }
    return _verify_table_columns(ref, pat, "au_reports_history", expected)


if __name__ == "__main__":
    sys.exit(main())
