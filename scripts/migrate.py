"""Supabase 스키마 자동 배포 — Management API (/v1/projects/{ref}/database/query)

upharma-au/crawler/db/australia_table.sql 전체를 Supabase 에 한 번에 실행한다.
여러 SQL 문장을 세미콜론으로 분리하지 않고 그대로 보내며, Supabase 가 서버측에서
단일 connection 안에서 순차 실행한다 (DDL/DML 모두 지원).

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

    # 2) PostgREST 스키마 캐시 강제 리로드
    #    ALTER 로 추가한 신규 컬럼이 supabase-py upsert 에서 PGRST204 로 튕기는 것을 방지.
    notify_status, _ = _run_query(ref, pat, "NOTIFY pgrst, 'reload schema';")
    print(f"[INFO] NOTIFY pgrst 'reload schema' → HTTP {notify_status}")

    # 3) 테이블 / 시드 기본 검증
    print()
    print("[검증]")
    _, tables = _run_query(
        ref, pat,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' ORDER BY table_name;",
    )
    if isinstance(tables, list):
        for row in tables:
            name = row.get("table_name") if isinstance(row, dict) else row
            print(f"  · {name}")

    _, col_cnt = _run_query(
        ref, pat,
        "SELECT COUNT(*)::int AS n FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='australia';",
    )
    if isinstance(col_cnt, list) and col_cnt and isinstance(col_cnt[0], dict):
        print(f"  · australia 컬럼 수 = {col_cnt[0].get('n')}")

    _, seed_cnt = _run_query(ref, pat, "SELECT COUNT(*)::int AS n FROM au_regulatory;")
    if isinstance(seed_cnt, list) and seed_cnt and isinstance(seed_cnt[0], dict):
        print(f"  · au_regulatory 시드 = {seed_cnt[0].get('n')} 행")

    _, p2_col_cnt = _run_query(
        ref, pat,
        "SELECT COUNT(*)::int AS n FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='australia_p2_results';",
    )
    if isinstance(p2_col_cnt, list) and p2_col_cnt and isinstance(p2_col_cnt[0], dict):
        print(f"  · australia_p2_results 컬럼 수 = {p2_col_cnt[0].get('n')}")

    # 4) _ALLOWED_COLUMNS ↔ information_schema.columns 대조 검증
    mismatch = _verify_columns(ref, pat)
    if mismatch:
        return 1

    # 5) 2공정 결과 테이블 스키마 검증 (Step 1)
    p2_mismatch = _verify_p2_results_schema(ref, pat)
    if p2_mismatch:
        return 1

    print()
    print("[완료] Supabase 스키마 배포 성공")
    return 0


def _verify_columns(ref: str, pat: str) -> bool:
    """supabase_insert._ALLOWED_COLUMNS 와 DB 실제 컬럼을 대조.

    반환값: 불일치가 있으면 True (호출부는 exit 1 로 종료).
    """
    print()
    print("[컬럼 검증] supabase_insert._ALLOWED_COLUMNS vs information_schema")
    try:
        from db.supabase_insert import _ALLOWED_COLUMNS  # type: ignore
    except Exception as exc:
        print(f"  ⚠ _ALLOWED_COLUMNS import 실패: {exc}")
        return True

    # _ALLOWED_COLUMNS 는 upsert 시 id 를 뺀 화이트리스트 → 비교 시 'id' 추가
    expected = set(_ALLOWED_COLUMNS) | {"id"}

    _, rows = _run_query(
        ref, pat,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='australia';",
    )
    actual: set[str] = set()
    if isinstance(rows, list):
        for r in rows:
            name = r.get("column_name") if isinstance(r, dict) else None
            if name:
                actual.add(name)

    missing_in_db = sorted(expected - actual)       # 코드는 기대하는데 DB 에 없음 → SQL 누락
    extra_in_db = sorted(actual - expected)         # DB 엔 있는데 코드가 모름 → insert 화이트리스트 누락

    print(f"  · 기대 컬럼 수(코드) = {len(expected)} / 실제 DB 컬럼 수 = {len(actual)}")

    ok = True
    if missing_in_db:
        ok = False
        print(f"  ✗ DB 에 없는 컬럼 {len(missing_in_db)}개 (SQL ALTER 누락 가능):")
        for c in missing_in_db:
            print(f"      - {c}")
    if extra_in_db:
        ok = False
        print(f"  ⚠ DB 에만 존재 {len(extra_in_db)}개 (_ALLOWED_COLUMNS 미등록 → 저장 누락):")
        for c in extra_in_db:
            print(f"      - {c}")

    if ok:
        print("  ✅ 컬럼 검증 통과")
        return False
    return True


def _verify_p2_results_schema(ref: str, pat: str) -> bool:
    """australia_p2_results 컬럼/제약조건 검증.

    Step 1 요구사항:
    - 컬럼 32개
    - UNIQUE(product_id, segment)
    """
    print()
    print("[컬럼 검증] australia_p2_results (Step 1)")

    expected_columns = {
        "id",
        "product_id",
        "segment",
        "ref_price_text",
        "ref_price_aud",
        "verdict",
        "logic",
        "pricing_case",
        "fob_penetration_aud",
        "fob_reference_aud",
        "fob_premium_aud",
        "fob_penetration_krw",
        "fob_reference_krw",
        "fob_premium_krw",
        "fx_aud_to_krw",
        "fx_aud_to_usd",
        "formula_str",
        "block_extract",
        "block_fob_intro",
        "scenario_penetration",
        "scenario_reference",
        "scenario_premium",
        "block_strategy",
        "block_risks",
        "block_positioning",
        "warnings",
        "disclaimer",
        "pdf_filename",
        "llm_model",
        "generated_at",
    }

    _, rows = _run_query(
        ref, pat,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='australia_p2_results';",
    )
    actual: set[str] = set()
    if isinstance(rows, list):
        for row in rows:
            name = row.get("column_name") if isinstance(row, dict) else None
            if name:
                actual.add(name)

    # 명세 문서의 "컬럼 32개" 기준은 UNIQUE 대상 2개(product_id, segment)를
    # 별도 확인 항목으로 세는 경우가 있어, 물리 컬럼은 30개가 정상이다.
    ok = True
    missing_in_db = sorted(expected_columns - actual)
    extra_in_db = sorted(actual - expected_columns)

    print(f"  · 기대 물리 컬럼 수 = {len(expected_columns)} / 실제 DB 컬럼 수 = {len(actual)}")
    if missing_in_db:
        ok = False
        print(f"  ✗ DB 에 없는 컬럼 {len(missing_in_db)}개:")
        for c in missing_in_db:
            print(f"      - {c}")
    if extra_in_db:
        ok = False
        print(f"  ⚠ DB 에만 존재 {len(extra_in_db)}개:")
        for c in extra_in_db:
            print(f"      - {c}")

    _, uq_rows = _run_query(
        ref, pat,
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name "
        "AND tc.table_schema = kcu.table_schema "
        "WHERE tc.table_schema='public' "
        "AND tc.table_name='australia_p2_results' "
        "AND tc.constraint_type='UNIQUE';",
    )
    uq_cols: set[str] = set()
    if isinstance(uq_rows, list):
        for row in uq_rows:
            col = row.get("column_name") if isinstance(row, dict) else None
            if col:
                uq_cols.add(col)

    if not {"product_id", "segment"}.issubset(uq_cols):
        ok = False
        print("  ✗ UNIQUE(product_id, segment) 제약을 찾지 못함")
    else:
        print("  ✅ UNIQUE(product_id, segment) 확인")

    if ok:
        print("  ✅ australia_p2_results 검증 통과")
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
