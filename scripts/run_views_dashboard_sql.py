"""sql_editor_bundle.sql(또는 views_dashboard.sql)을 Postgres에 적용한다.

Supabase: Settings → Database → URI 복사 후 DATABASE_URL 사용.

사용:
  pip install "psycopg[binary]"
  set DATABASE_URL=postgresql://postgres.[ref]:[비밀번호]@...
  python scripts/run_views_dashboard_sql.py

또는:
  python scripts/run_views_dashboard_sql.py "postgresql://..."
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _strip_sql_comments(sql: str) -> str:
    out: list[str] = []
    for line in sql.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        out.append(line)
    return "\n".join(out)


def _split_statements(sql: str) -> list[str]:
    """세미콜론으로 문장 분리 (뷰/COMMENT/GRANT 용 단순 스크립트 전제)."""
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    i = 0
    text = sql
    while i < len(text):
        c = text[i]
        if c == "'" and (i == 0 or text[i - 1] != "\\"):
            in_single = not in_single
            buf.append(c)
            i += 1
            continue
        if c == ";" and not in_single:
            stmt = "".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def main() -> None:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if len(sys.argv) > 1 and sys.argv[1].strip().startswith("postgresql"):
        dsn = sys.argv[1].strip()

    if not dsn:
        print(
            "DATABASE_URL(또는 SUPABASE_DB_URL)이 없습니다.\n"
            "Supabase 대시보드 → Project Settings → Database → Connection string (URI) 를 복사해\n"
            "환경변수로 넣거나, 인자로 넘기세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    root = Path(__file__).resolve().parent.parent
    db_dir = root / "upharma-au" / "crawler" / "db"
    bundle = db_dir / "sql_editor_bundle.sql"
    fallback = db_dir / "views_dashboard.sql"
    sql_path = bundle if bundle.is_file() else fallback
    if not sql_path.is_file():
        print(f"파일 없음: {bundle} 또는 {fallback}", file=sys.stderr)
        sys.exit(1)

    raw = sql_path.read_text(encoding="utf-8")
    cleaned = _strip_sql_comments(raw)
    statements = _split_statements(cleaned)

    try:
        import psycopg
    except ImportError:
        print('필요: pip install "psycopg[binary]"', file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)

    print(f"적용 완료: {len(statements)}개 문장 ({sql_path.name})")


if __name__ == "__main__":
    main()
