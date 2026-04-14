# Render 서빙용 FastAPI 어댑터 — crawler/ 내부 코드를 import만 해서 재사용한다.
# 규칙: crawler/, next-app/ 안의 파일은 수정하지 않는다. 이 파일이 유일한 연결 지점.

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# au_crawler.py 는 `from sources.xxx import ...` 같은 상대 import 를 쓰므로
# crawler/ 디렉토리를 sys.path 에 올려서 그대로 동작하게 한다.
_BASE_DIR = Path(__file__).resolve().parent
_CRAWLER_DIR = _BASE_DIR / "crawler"
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse

# crawler 내부 코드 (수정하지 않고 import 만)
from au_crawler import main as run_crawler  # type: ignore
from db.supabase_insert import TABLE_NAME, get_supabase_client  # type: ignore

app = FastAPI(title="UPharma Export AI · Australia")

app.mount(
    "/static",
    StaticFiles(directory=str(_BASE_DIR / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/crawl")
def crawl(payload: dict[str, Any]) -> JSONResponse:
    """au_crawler.py 의 main() 을 직접 호출한다.
    - PRODUCT_FILTER env 를 세팅한 뒤 main() 실행 (단일 워커 전제).
    - main() 내부의 sys.exit(code) 는 SystemExit 으로 잡아서 성공 여부만 판단한다.
    """
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id is required")

    prev_filter = os.environ.get("PRODUCT_FILTER")
    os.environ["PRODUCT_FILTER"] = product_id
    exit_code: int | None = None
    try:
        try:
            run_crawler()
            exit_code = 0
        except SystemExit as e:
            exit_code = 0 if (e.code is None or e.code == 0) else int(e.code)
    finally:
        if prev_filter is None:
            os.environ.pop("PRODUCT_FILTER", None)
        else:
            os.environ["PRODUCT_FILTER"] = prev_filter

    ok = exit_code == 0
    return JSONResponse(
        status_code=200 if ok else 500,
        content={"ok": ok, "product_id": product_id, "exit_code": exit_code},
    )


@app.get("/api/data/{product_id}")
def get_product(product_id: str) -> JSONResponse:
    """Supabase australia 테이블에서 product_id 단건 조회."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("*")
            .eq("product_id", product_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")

    if not rows:
        raise HTTPException(status_code=404, detail=f"not found: {product_id}")
    return JSONResponse(content=rows[0])


@app.get("/api/data")
def list_products() -> JSONResponse:
    """Supabase australia 테이블 전체 목록 (최신 crawled_at 순)."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("*")
            .order("crawled_at", desc=True)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")
    return JSONResponse(content={"items": rows, "count": len(rows)})


# ── reports 테이블 어댑터 (1/2/3공정 산출 보고서 메타) ─────────────
_REPORTS_TABLE = "reports"


@app.get("/api/reports")
def list_reports_today() -> JSONResponse:
    """오늘 날짜(UTC 기준)에 생성된 보고서 목록을 최신순으로 반환."""
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    try:
        client = get_supabase_client()
        resp = (
            client.table(_REPORTS_TABLE)
            .select("*")
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .order("created_at", desc=True)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")
    return JSONResponse(content={"items": rows, "count": len(rows)})


@app.post("/api/reports")
def create_report(payload: dict[str, Any]) -> JSONResponse:
    """보고서 저장 버튼이 호출하는 엔드포인트.
    body: { product_id?, gong: 1|2|3, title, file_url?, crawled_data? }
    """
    gong = payload.get("gong")
    title = str(payload.get("title") or "").strip()
    if gong not in (1, 2, 3) or not title:
        raise HTTPException(status_code=400, detail="gong(1|2|3) and title required")

    row = {
        "product_id": payload.get("product_id"),
        "gong": int(gong),
        "title": title,
        "file_url": payload.get("file_url"),
        "crawled_data": payload.get("crawled_data"),
    }
    try:
        client = get_supabase_client()
        resp = client.table(_REPORTS_TABLE).insert(row).execute()
        data = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")
    return JSONResponse(content={"ok": True, "row": data[0] if data else None})
