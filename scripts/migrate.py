"""Supabase 스키마 자동 배포 — upharma-au/crawler/db/australia_table.sql 을 실행한다.

전제
----
Supabase 는 REST 로 임의 DDL 을 실행하는 공식 엔드포인트가 없으므로,
미리 `exec_sql` 이라는 RPC 함수 1 개만 SQL Editor 에서 수동으로 만들어 둔다.
이 스크립트는 이후 모든 DDL/DML 을 해당 RPC 경유로 실행한다.

Supabase SQL Editor 에 단 한 번만 실행:

    CREATE OR REPLACE FUNCTION exec_sql(query text) RETURNS void AS $$
    BEGIN
      EXECUTE query;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER;

실행
----
    python scripts/migrate.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "upharma-au" / "crawler" / "db" / "australia_table.sql"

_HELPER_HINT = """\
-- Supabase SQL Editor 에 아래 SQL 을 한 번만 실행한 뒤 다시 시도하세요:
CREATE OR REPLACE FUNCTION exec_sql(query text) RETURNS void AS $$
BEGIN
  EXECUTE query;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
"""


def _load_env() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("[오류] .env 에 SUPABASE_URL 또는 SUPABASE_SERVICE_KEY 가 없습니다.", file=sys.stderr)
        sys.exit(1)
    return url, key


def _split_statements(sql_text: str) -> list[str]:
    """-- 주석 줄 제거 후 세미콜론으로 문장 분리.

    australia_table.sql 은 $$...$$ 함수 정의·문자열 리터럴 내 세미콜론이 없어
    단순 split(';') 로 충분하다. 추후 함수 정의가 추가되면 tokenizer 로 교체.
    """
    lines: list[str] = []
    for ln in sql_text.splitlines():
        stripped = ln.lstrip()
        if stripped.startswith("--"):
            continue
        lines.append(ln)
    cleaned = "\n".join(lines)
    raw = [s.strip() for s in cleaned.split(";")]
    return [s for s in raw if s]


def _call_exec_sql(base_url: str, key: str, statement: str) -> tuple[bool, str]:
    url = f"{base_url}/rest/v1/rpc/exec_sql"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        r = httpx.post(url, headers=headers, json={"query": statement}, timeout=60.0)
    except Exception as exc:
        return False, f"network: {exc}"
    if r.status_code in (200, 204):
        return True, "OK"
    body = r.text.strip().replace("\n", " ")[:240]
    return False, f"HTTP {r.status_code}: {body}"


def _ensure_exec_sql(base_url: str, key: str) -> bool:
    """no-op SELECT 로 exec_sql RPC 존재 여부 확인."""
    ok, msg = _call_exec_sql(base_url, key, "SELECT 1")
    if ok:
        return True
    print("[안내] exec_sql RPC 함수를 호출할 수 없습니다.")
    print(f"       응답: {msg}")
    print()
    print(_HELPER_HINT)
    return False


def main() -> int:
    base_url, key = _load_env()
    if not SQL_FILE.is_file():
        print(f"[오류] SQL 파일을 찾을 수 없습니다: {SQL_FILE}", file=sys.stderr)
        return 1

    if not _ensure_exec_sql(base_url, key):
        return 1

    sql_text = SQL_FILE.read_text(encoding="utf-8")
    statements = _split_statements(sql_text)
    total = len(statements)
    print(f"[시작] {SQL_FILE.relative_to(ROOT)} → {total} 문장 실행\n")

    success = 0
    fail = 0
    for i, stmt in enumerate(statements, 1):
        preview = re.sub(r"\s+", " ", stmt)[:90]
        ok, msg = _call_exec_sql(base_url, key, stmt)
        if ok:
            print(f"  [{i:>2}/{total}] ✓ {preview}")
            success += 1
        else:
            print(f"  [{i:>2}/{total}] ✗ {preview}")
            print(f"           → {msg}")
            fail += 1

    print()
    print(f"[완료] 성공 {success} / 실패 {fail} / 전체 {total}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
