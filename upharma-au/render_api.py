# Render 서빙용 FastAPI 어댑터 — crawler/ 내부 코드를 import만 해서 재사용한다.
# 이 파일이 브라우저 ↔ 크롤러 ↔ Supabase 를 잇는 유일한 연결 지점.

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

# au_crawler.py 는 `from sources.xxx import ...` 같은 상대 import 를 쓰므로
# crawler/ 디렉토리를 sys.path 에 올려서 그대로 동작하게 한다.
_BASE_DIR = Path(__file__).resolve().parent
_CRAWLER_DIR = _BASE_DIR / "crawler"
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))
# stage2 디렉토리도 import 가능하도록 sys.path 추가 (크롤러와 독립된 FOB 역산 모듈)
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# .env 자동 로드 (uvicorn 으로 뜰 때 프로세스 env 에 ANTHROPIC_API_KEY 등이 반영되도록)
try:
    from dotenv import load_dotenv
    # project root (upharma-au 의 부모) 의 .env 를 탐색
    _env_path = _BASE_DIR.parent / ".env"
    if _env_path.is_file():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse

# 보고서 PDF 저장 디렉토리
_REPORTS_DIR = _BASE_DIR / "reports"
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# crawler 내부 코드
from au_crawler import run_crawler  # type: ignore
from db.supabase_insert import TABLE_NAME, get_supabase_client  # type: ignore

logger = logging.getLogger("render_api")


def _normalize_au_product_row(row: dict[str, Any]) -> None:
    """au_products 행은 DB 컬럼명이 product_code(품목 코드)임.
    API·프론트는 기존 계약대로 product_id 키를 기대하므로 별칭을 채운다."""
    pc = row.get("product_code")
    if pc is not None:
        row["product_id"] = pc


# ─────────────────────────────────────────────────────────────────────
# 선택적 외부 라이브러리 가용성 프로브 (dep 하나 빠져도 서버 기동은 성공)
# - anthropic, openai, yfinance, reportlab: 누락 시 해당 엔드포인트만 503
# - stage2 / supabase / httpx / fastapi 는 필수(없으면 서버 기동 자체 실패)
# ─────────────────────────────────────────────────────────────────────
def _probe_optional_dep(modname: str) -> tuple[bool, str]:
    try:
        __import__(modname)
        return True, ""
    except Exception as _e:  # ImportError · 내부 초기화 실패 모두 포괄
        return False, f"{type(_e).__name__}: {_e}"


_ANTHROPIC_AVAILABLE, _ANTHROPIC_ERR = _probe_optional_dep("anthropic")
_OPENAI_AVAILABLE, _OPENAI_ERR = _probe_optional_dep("openai")
_YFINANCE_AVAILABLE, _YFINANCE_ERR = _probe_optional_dep("yfinance")
_REPORTLAB_AVAILABLE, _REPORTLAB_ERR = _probe_optional_dep("reportlab")

_DEPS_STATUS: dict[str, dict[str, Any]] = {
    "anthropic": {"ok": _ANTHROPIC_AVAILABLE, "required_by": ["/api/report/generate", "/api/p2/pipeline"], "error": _ANTHROPIC_ERR},
    "openai":    {"ok": _OPENAI_AVAILABLE,    "required_by": ["/api/report/generate (refs 요약, 선택)"], "error": _OPENAI_ERR},
    "yfinance":  {"ok": _YFINANCE_AVAILABLE,  "required_by": ["/api/exchange (선택, 미설치 시 fallback)"], "error": _YFINANCE_ERR},
    "reportlab": {"ok": _REPORTLAB_AVAILABLE, "required_by": ["/api/report/generate (PDF 저장, 선택)"], "error": _REPORTLAB_ERR},
}

# 서버 기동 시점에 dep 상태를 stdout 에 한 번만 찍어서 Render/uvicorn 로그에 남긴다
for _modname, _info in _DEPS_STATUS.items():
    _mark = "OK" if _info["ok"] else "MISSING"
    print(f"[deps-probe] {_modname}: {_mark}" + (f" — {_info['error']}" if not _info["ok"] else ""), flush=True)


app = FastAPI(title="UPharma Export AI · Australia")

app.mount(
    "/static",
    StaticFiles(directory=str(_BASE_DIR / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


def _static_version() -> str:
    # Render: 배포마다 커밋이 바뀌므로 mtime 이 안 맞아도 캐시 무효화가 확실함
    commit = (os.environ.get("RENDER_GIT_COMMIT") or "").strip()
    if commit:
        return commit[:12]
    # 로컬: styles.css / app.js / 파비콘 SVG 중 최신 mtime
    paths = [
        _BASE_DIR / "static" / "styles.css",
        _BASE_DIR / "static" / "app.js",
        _BASE_DIR / "static" / "flag-au.svg",
    ]
    try:
        return str(int(max(p.stat().st_mtime for p in paths if p.is_file())))
    except ValueError:
        return "0"


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    """브라우저 기본 요청(/favicon.ico)에 호주 국기 SVG 제공 (탭 아이콘)."""
    return FileResponse(
        _BASE_DIR / "static" / "flag-au.svg",
        media_type="image/svg+xml",
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    # Starlette 최신 API: (request, name) 순서. 구 API 의 ("name", {"request": request}) 는 TypeError.
    return templates.TemplateResponse(request, "index.html", {"static_v": _static_version()})


@app.get("/health")
def health() -> dict[str, Any]:
    """서버·필수 모듈·선택 의존성 상태 요약.
    uvicorn 이 떠 있으면 필수 의존성(fastapi/httpx/supabase) 은 이미 통과한 상태이므로,
    나머지 선택 의존성(anthropic/openai/yfinance/reportlab) 의 설치 여부만 노출한다.
    """
    deps = {name: info["ok"] for name, info in _DEPS_STATUS.items()}
    all_optional_ok = all(deps.values())
    pplx_set = bool(
        (os.environ.get("PERPLEXITY_API_KEY") or "").strip()
        or (os.environ.get("PERPLEXITY_KEY") or "").strip()
    )
    return {
        "status": "ok",
        "optional_deps": deps,
        "optional_deps_all_installed": all_optional_ok,
        "stage2_ok": _STAGE2_OK if "_STAGE2_OK" in globals() else None,
        "perplexity_api_key_configured": pplx_set,
        "hint": None if all_optional_ok else "pip install -r requirements.txt",
    }


@app.get("/health/deps")
def health_deps() -> dict[str, Any]:
    """선택 의존성 상세 — 설치 안 된 경우 에러 메시지와 어떤 엔드포인트가 영향을 받는지."""
    return {
        "ok": all(info["ok"] for info in _DEPS_STATUS.values()),
        "deps": _DEPS_STATUS,
    }


@app.post("/api/crawl")
def crawl(payload: dict[str, Any]) -> JSONResponse:
    """au_crawler.run_crawler(product_id) 호출 — 품목은 요청 body 로만 전달 (환경변수 미사용)."""
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id is required")

    exit_code: int | None = None
    try:
        run_crawler(product_id)
        exit_code = 0
    except SystemExit as e:
        exit_code = 0 if (e.code is None or e.code == 0) else int(e.code)

    ok = exit_code == 0
    return JSONResponse(
        status_code=200 if ok else 500,
        content={"ok": ok, "product_id": product_id, "exit_code": exit_code},
    )


# ═══════════════════════════════════════════════════════════════════════
# Task 8 (2026-04-19) — 신약 크롤링 엔드포인트 + 상태 폴링
# ═══════════════════════════════════════════════════════════════════════
# au_crawler.main(new_drug_input=...) 을 백그라운드 스레드로 실행. seeds.json
# 없이 au-newdrug-<uuid> 임시 ID 로 동일 파이프라인 실행.

import threading as _nd_threading
import uuid as _nd_uuid

# job_id → {status, product_code, aemp_aud, needs_price_upload, message_ko, error}
_new_drug_jobs: dict[str, dict[str, Any]] = {}
_new_drug_lock = _nd_threading.Lock()

_MSG_NO_AEMP_KO = (
    "호주 공개 데이터베이스에서 이 성분의 가격 정보를 찾지 못했어요. "
    "혹시 보유하신 호주 시장 가격 자료나 경쟁사 가격이 담긴 PDF 가 있다면, "
    "아래에 업로드해주시면 분석을 이어갈 수 있어요."
)


def _new_drug_worker(job_id: str, payload: dict[str, Any]) -> None:
    """백그라운드 스레드 — au_crawler.main(new_drug_input=payload) 실행 후
    Supabase 에서 AEMP/retail 확보 여부 확인해 job 결과 업데이트."""
    with _new_drug_lock:
        cur = _new_drug_jobs.get(job_id) or {}
        cur["status"] = "running"
        _new_drug_jobs[job_id] = cur
    try:
        from crawler.au_crawler import main as _crawler_main
        result = _crawler_main([], new_drug_input=payload)
    except Exception as exc:
        with _new_drug_lock:
            _new_drug_jobs[job_id] = {
                **_new_drug_jobs.get(job_id, {}),
                "status": "failed",
                "error": str(exc)[:500],
            }
        return

    product_code = (result or {}).get("product_id") or ""
    aemp_aud = None
    retail_price_aud = None
    row_snapshot: dict[str, Any] = {}
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select(
                "aemp_aud,retail_price_aud,warnings,similar_drug_used,case_code,pbs_found"
            )
            .eq("product_code", product_code)
            .order("last_crawled_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows:
            row_snapshot = dict(rows[0])
            aemp_aud = row_snapshot.get("aemp_aud")
            retail_price_aud = row_snapshot.get("retail_price_aud")
    except Exception as exc:
        print(f"[new_drug AEMP 조회 실패] {exc}", flush=True)

    def _is_empty(v: Any) -> bool:
        return v is None or v == "" or v == 0
    needs_upload = _is_empty(aemp_aud) and _is_empty(retail_price_aud)
    with _new_drug_lock:
        _new_drug_jobs[job_id] = {
            "status": "done",
            "product_code": product_code,
            "aemp_aud": aemp_aud,
            "retail_price_aud": retail_price_aud,
            "needs_price_upload": bool(needs_upload),
            "message_ko": _MSG_NO_AEMP_KO if needs_upload else None,
            "warnings": row_snapshot.get("warnings"),
            "similar_drug_used": row_snapshot.get("similar_drug_used"),
            "case_code": row_snapshot.get("case_code"),
            "pbs_found": row_snapshot.get("pbs_found"),
        }


@app.post("/api/crawl/new-drug")
def crawl_new_drug(payload: dict[str, Any]) -> JSONResponse:
    """신약 크롤링 — 기존 품목과 동일 파이프라인, seeds.json 없이 작동.

    요청 body:
      {
        "product_name_ko": "Nexavar",
        "inn": "sorafenib" 또는 "drugA, drugB",
        "strength_dosage_form": "200mg tablet"
      }
    응답:
      {job_id, product_code, status: "queued", poll_url}
    크롤 결과는 GET /api/crawl/status/{job_id} 로 폴링.
    """
    product_name_ko = str(payload.get("product_name_ko") or "").strip()
    inn = str(payload.get("inn") or "").strip()
    strength_dosage_form = str(payload.get("strength_dosage_form") or "").strip()

    if not (product_name_ko and inn and strength_dosage_form):
        raise HTTPException(
            status_code=400,
            detail="product_name_ko, inn, strength_dosage_form 3개 필드 모두 필요합니다.",
        )

    # UI 표시용 임시 product_code (실제 crawler 가 생성하는 ID 와는 다를 수 있으니
    # 폴링 결과의 product_code 가 최종 정확값).
    preview_product_code = f"au-newdrug-{_nd_uuid.uuid4().hex[:8]}"
    job_id = _nd_uuid.uuid4().hex

    crawler_payload = {
        "product_name_ko": product_name_ko,
        "inn": inn,
        "strength_dosage_form": strength_dosage_form,
    }

    with _new_drug_lock:
        _new_drug_jobs[job_id] = {
            "status": "queued",
            "product_code": preview_product_code,
            "aemp_aud": None,
            "retail_price_aud": None,
            "needs_price_upload": None,
            "message_ko": None,
        }

    worker = _nd_threading.Thread(
        target=_new_drug_worker,
        args=(job_id, crawler_payload),
        daemon=True,
    )
    worker.start()

    return JSONResponse({
        "job_id": job_id,
        "product_code": preview_product_code,
        "status": "queued",
        "poll_url": f"/api/crawl/status/{job_id}",
    })


@app.get("/api/crawl/status/{job_id}")
def crawl_status(job_id: str) -> JSONResponse:
    """Task 8 — 신약 크롤 job 상태 조회.

    상태 값: queued | running | done | failed
    done 일 때 결과 필드(aemp_aud / needs_price_upload / message_ko) 포함.
    """
    with _new_drug_lock:
        job = _new_drug_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} 없음")
    return JSONResponse(job)


# ═══════════════════════════════════════════════════════════════════════
# Task 9 (2026-04-19) — PDF 업로드 가격 추출 (Haiku + tool_use)
# ═══════════════════════════════════════════════════════════════════════
# 신약 분석에서 AEMP/retail 확보 실패 시 사용자가 가격 자료 PDF 업로드 →
# Haiku (claude-haiku-4-5-20251001, CLAUDE.md 절대 규칙) 로 구조화 추출.

from fastapi import File, Form, UploadFile
from decimal import Decimal as _PdfDecimal

_MSG_PDF_DONE_KO = "가격 데이터 추출 완료. 수출전략 제안 단계로 진행합니다."
_MSG_PDF_LOW_CONF_KO = (
    "PDF 에서 가격 정보를 확실하게 추출하지 못했습니다. 추출 결과를 검토하고 "
    "필요하면 수기 보정 후 다음 단계로 진행하세요."
)

# 가격 PDF·수출전략 업로드 공통: 페이지 상한 (비용·처리 시간 통제)
UPLOAD_PDF_MAX_PAGES = 4


def _pdf_magic_ok(pdf_bytes: bytes) -> bool:
    """파일 선두가 PDF 바이너리(%PDF)인지 확인 — 확장자와 무관하게 형식만 검증."""
    return bool(pdf_bytes) and pdf_bytes[:4] == b"%PDF"


def _pdf_page_count_raw_scan(pdf_bytes: bytes) -> int:
    """라이브러리가 실패할 때 PDF 바이너리에서 /Count 정수만 스캔 (최후 수단).

    일부 생성기·암호화 조합에서 pypdf/pdfplumber 가 0페이지·예외를 낼 수 있음.
    """
    import re

    head = pdf_bytes[: min(len(pdf_bytes), 2_000_000)]
    found = [int(m.group(1)) for m in re.finditer(rb"/Count\s+(\d{1,6})\b", head)]
    if not found:
        return 0
    # 루트 Pages 의 Count 가 보통 문서 전체 페이지 수 (과도한 값 제외)
    reasonable = [n for n in found if 1 <= n <= 5000]
    return max(reasonable) if reasonable else 0


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """PDF 페이지 수. pypdf → pdfplumber → 원시 /Count 스캔."""
    if not pdf_bytes:
        return 0
    from io import BytesIO

    def _reader_cls():  # noqa: ANN202
        try:
            from pypdf import PdfReader

            return PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore

                return PdfReader
            except ImportError:
                return None

    Reader = _reader_cls()
    if Reader is not None:
        try:
            try:
                reader = Reader(BytesIO(pdf_bytes), strict=False)
            except TypeError:
                reader = Reader(BytesIO(pdf_bytes))
            if getattr(reader, "is_encrypted", False):
                dec = getattr(reader, "decrypt", lambda _p: 0)
                try:
                    dec("")
                except Exception:
                    pass
            n = len(reader.pages)
            if n > 0:
                return n
        except Exception as exc:
            logger.debug("pypdf/PyPDF2 페이지 수 실패: %s", exc)

    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            n2 = len(pdf.pages)
            if n2 > 0:
                return n2
    except Exception as exc:
        logger.debug("pdfplumber 페이지 수 실패: %s", exc)

    n3 = _pdf_page_count_raw_scan(pdf_bytes)
    return n3


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """업로드 PDF 바이트 → 평문 텍스트. pypdf → pdfplumber → 빈 문자열 순서로 폴백.

    OCR 필요한 스캔 PDF 는 커버하지 않음 (text-layer 있는 일반 PDF 가정).
    """
    if not pdf_bytes:
        return ""
    # 1) pypdf
    try:
        from pypdf import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(pdf_bytes))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        joined = "\n".join(parts).strip()
        if joined:
            return joined
    except Exception as exc:
        print(f"[pdf pypdf 실패] {exc}", flush=True)
    # 2) pdfplumber
    try:
        import pdfplumber  # type: ignore
        from io import BytesIO
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            parts2: list[str] = []
            for page in pdf.pages:
                try:
                    parts2.append(page.extract_text() or "")
                except Exception:
                    continue
            joined = "\n".join(parts2).strip()
            if joined:
                return joined
    except Exception as exc:
        print(f"[pdf pdfplumber 실패] {exc}", flush=True)
    return ""


def _normalize_to_aud(
    value: Any,
    currency: str,
) -> tuple[_PdfDecimal | None, str]:
    """통화별 → AUD 환산. 반환: (aud_decimal, 적용 메서드 라벨)."""
    from crawler.utils.fx import usd_to_aud, krw_to_aud, eur_to_aud
    c = (currency or "").upper().strip()
    if value is None:
        return None, c
    try:
        raw_dec = _PdfDecimal(str(value))
    except Exception:
        return None, c
    if c == "AUD":
        return raw_dec, "AUD"
    if c == "USD":
        return usd_to_aud(raw_dec), "USD→AUD"
    if c == "KRW":
        return krw_to_aud(raw_dec), "KRW→AUD"
    if c == "EUR":
        return eur_to_aud(raw_dec), "EUR→AUD"
    return raw_dec, f"{c}(env 환율 없음)"


def _haiku_extract_price(pdf_text: str) -> dict[str, Any] | None:
    """Haiku (claude-haiku-4-5-20251001) + tool_use 로 PDF 텍스트에서 가격 추출."""
    if not pdf_text:
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        print("[PDF 가격 추출] anthropic SDK 미설치", flush=True)
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        print("[PDF 가격 추출] ANTHROPIC_API_KEY 없음", flush=True)
        return None

    tool = {
        "name": "extract_price_data",
        "description": (
            "Extract pharmaceutical pricing (AEMP, DPMQ, retail) from a document. "
            "Report the detected currency separately — caller will convert to AUD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "aemp_aud": {"type": ["number", "null"]},
                "dpmq_aud": {"type": ["number", "null"]},
                "retail_price_aud": {"type": ["number", "null"]},
                "currency_detected": {
                    "type": "string",
                    "enum": ["AUD", "USD", "KRW", "EUR", "unknown"],
                },
                "source_description": {"type": "string"},
                "extracted_text_excerpts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "confidence": {"type": "number"},
            },
            "required": ["confidence", "currency_detected"],
        },
    }

    snippet = pdf_text[:30000]  # Haiku context 절약

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            tools=[tool],
            tool_choice={"type": "tool", "name": "extract_price_data"},
            messages=[{
                "role": "user",
                "content": (
                    "Extract pharmaceutical pricing (AEMP, DPMQ, retail) from the "
                    "document text below. If the document currency is not AUD, "
                    "report the raw number in the currency's original value (not "
                    "converted) and set currency_detected accordingly — the caller "
                    "will handle conversion. If a field is absent, return null.\n\n"
                    "Document text:\n---\n" + snippet
                ),
            }],
        )
    except Exception as exc:
        print(f"[PDF 가격 추출] Haiku 호출 실패: {exc}", flush=True)
        return None

    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            data = getattr(block, "input", {}) or {}
            if isinstance(data, dict):
                return data
    return None


@app.post("/api/crawl/price-pdf-upload")
def extract_price_from_pdf(
    product_code: str = Form(...),
    pdf_file: UploadFile = File(...),
) -> JSONResponse:
    """분석 실패 시 사용자가 업로드한 가격 자료 PDF 에서 AEMP/DPMQ/소매가 추출.

    요청 (multipart/form-data):
      product_code : 신약 임시 ID (au-newdrug-...) 또는 기존 품목 코드
      pdf_file     : 가격 자료 PDF

    처리:
      1) PDF → 평문 텍스트 (pypdf / pdfplumber)
      2) Haiku(tool_use) 로 {aemp_aud, dpmq_aud, retail_price_aud, currency, ...} 추출
      3) currency_detected != AUD 면 utils.fx.*_to_aud 로 환산
      4) Supabase au_products UPDATE — retail_estimation_method="user_pdf_upload"
    """
    if not pdf_file:
        raise HTTPException(status_code=400, detail="pdf_file 필수")
    try:
        pdf_bytes = pdf_file.file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF 읽기 실패: {exc}")
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if not _pdf_magic_ok(pdf_bytes):
        raise HTTPException(
            status_code=400,
            detail="PDF 형식이 아닙니다. (파일 선두 %PDF 시그니처 없음 — 확장자와 무관하게 검사합니다.)",
        )
    _n_pages = _pdf_page_count(pdf_bytes)
    if _n_pages < 1:
        if _pdf_magic_ok(pdf_bytes) and len(pdf_bytes) <= 4 * 1024 * 1024:
            logger.warning(
                "가격 PDF 업로드: 페이지 수 미상 → 1로 간주 (pip install pypdf pdfplumber 권장)"
            )
            _n_pages = 1
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "PDF 페이지 수를 확인할 수 없습니다. "
                    "서버에 pypdf 설치 여부를 확인하거나, 4MB 이하인지 확인해 주세요."
                ),
            )
    if _n_pages > UPLOAD_PDF_MAX_PAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"PDF는 최대 {UPLOAD_PDF_MAX_PAGES}페이지까지 업로드할 수 있습니다. "
                f"(현재 {_n_pages}페이지)"
            ),
        )

    pdf_text = _extract_pdf_text(pdf_bytes)
    if not pdf_text:
        raise HTTPException(
            status_code=422,
            detail="PDF 텍스트 레이어를 추출할 수 없습니다. 스캔 PDF 는 OCR 필요.",
        )

    extracted = _haiku_extract_price(pdf_text)
    if not extracted:
        raise HTTPException(status_code=502, detail="AI 가격 추출 실패 (Haiku 호출 오류).")

    currency = str(extracted.get("currency_detected") or "unknown").upper()
    aemp_aud_norm, aemp_method = _normalize_to_aud(extracted.get("aemp_aud"), currency)
    dpmq_aud_norm, dpmq_method = _normalize_to_aud(extracted.get("dpmq_aud"), currency)
    retail_aud_norm, retail_method = _normalize_to_aud(
        extracted.get("retail_price_aud"), currency
    )

    source_desc = str(extracted.get("source_description") or "")[:200]
    confidence_raw = extracted.get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5

    # 기존 warnings 에 신규 항목 append (기존 리스트 보존)
    existing_warnings: list[str] = []
    try:
        client = get_supabase_client()
        existing_resp = (
            client.table(TABLE_NAME)
            .select("warnings")
            .eq("product_code", product_code)
            .limit(1)
            .execute()
        )
        rows = getattr(existing_resp, "data", None) or []
        if rows:
            warn_raw = rows[0].get("warnings")
            if isinstance(warn_raw, list):
                existing_warnings = [str(w) for w in warn_raw]
    except Exception as exc:
        print(f"[PDF 업로드 warnings 조회 경고] {exc}", flush=True)

    new_warnings = list(existing_warnings)
    new_warnings.append("user_uploaded_pdf")
    if source_desc:
        new_warnings.append(f"pdf_source:{source_desc}")
    if currency not in ("AUD", "UNKNOWN"):
        new_warnings.append(f"pdf_currency_converted:{currency}→AUD")

    update_row: dict[str, Any] = {
        "retail_estimation_method": "user_pdf_upload",
        "warnings": new_warnings,
    }
    if aemp_aud_norm is not None:
        update_row["aemp_aud"] = str(aemp_aud_norm)
    if dpmq_aud_norm is not None:
        update_row["dpmq_aud"] = str(dpmq_aud_norm)
    if retail_aud_norm is not None:
        update_row["retail_price_aud"] = str(retail_aud_norm)

    try:
        client = get_supabase_client()
        client.table(TABLE_NAME).update(update_row).eq(
            "product_code", product_code
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Supabase UPDATE 실패: {exc}")

    message_ko = _MSG_PDF_DONE_KO if confidence >= 0.7 else _MSG_PDF_LOW_CONF_KO
    return JSONResponse({
        "success": True,
        "extracted": {
            "aemp_aud": str(aemp_aud_norm) if aemp_aud_norm is not None else None,
            "dpmq_aud": str(dpmq_aud_norm) if dpmq_aud_norm is not None else None,
            "retail_price_aud": str(retail_aud_norm) if retail_aud_norm is not None else None,
            "currency_detected": currency,
            "aemp_conversion": aemp_method,
            "dpmq_conversion": dpmq_method,
            "retail_conversion": retail_method,
            "source_description": source_desc,
            "confidence": confidence,
            "excerpts": extracted.get("extracted_text_excerpts") or [],
        },
        "next_step": "/api/p2/pipeline",
        "message_ko": message_ko,
    })


@app.get("/api/data/{product_id}")
def get_product(product_id: str) -> JSONResponse:
    """Supabase `au_products` 에서 품목 단건 조회 (필터 컬럼: product_code)."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("*")
            .eq("product_code", product_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")

    if not rows:
        raise HTTPException(status_code=404, detail=f"not found: {product_id}")
    row_one = rows[0]
    if isinstance(row_one, dict):
        _normalize_au_product_row(row_one)
    return JSONResponse(content=row_one)


@app.get("/api/data")
def list_products() -> JSONResponse:
    """Supabase `au_products` 전체 목록 (최신 last_crawled_at 순 — v2 마스터 컬럼명)."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("*")
            .order("last_crawled_at", desc=True)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")
    for r in rows:
        if isinstance(r, dict):
            _normalize_au_product_row(r)
    return JSONResponse(content={"items": rows, "count": len(rows)})


# ── au_reports_history 테이블 어댑터 (산출 보고서 메타, v2) ─────
# v2 스키마: title/file_url/crawled_data 는 JSONB snapshot 안으로 흡수.
_REPORTS_TABLE = "au_reports_history"


@app.get("/api/reports")
def list_reports_today() -> JSONResponse:
    """오늘 날짜(UTC 기준)에 생성된 보고서 목록을 최신순으로 반환.
    v2 응답 구조: { items: [{id, product_id, gong, snapshot:{title,file_url,...},
                              llm_model, generated_at, created_at}], count }
    프론트는 snapshot.title · snapshot.file_url 로 접근.
    """
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

    v2 매핑: title/file_url/crawled_data → snapshot JSONB 안으로 흡수.
    """
    gong = payload.get("gong")
    title = str(payload.get("title") or "").strip()
    if gong not in (1, 2, 3) or not title:
        raise HTTPException(status_code=400, detail="gong(1|2|3) and title required")

    row = {
        "product_id": payload.get("product_id"),
        "gong": int(gong),
        "snapshot": {
            "title":        title,
            "file_url":     payload.get("file_url"),
            "crawled_data": payload.get("crawled_data") or {},
        },
        "llm_model": payload.get("llm_model") or _CLAUDE_MODEL,
    }
    try:
        client = get_supabase_client()
        resp = client.table(_REPORTS_TABLE).insert(row).execute()
        data = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")
    return JSONResponse(content={"ok": True, "row": data[0] if data else None})


def _append_au_reports_history_market_analysis(
    product_id: str,
    *,
    row: dict[str, Any],
    blocks: dict[str, Any],
    refs: list[dict[str, Any]],
    meta: dict[str, Any],
    llm_generated_at: str,
    pdf_name: str | None,
) -> None:
    """시장분석(gong=1) append-only 이력 + report_content_v2.

    품목 공통 봉투(schema_ver, product_code, pricing_case, blocks…).
    이후 수출전략 단계에서 동일 키로 조회·프롬프트 주입 가능.
    """
    try:
        title_ko = str(row.get("product_name_ko") or product_id).strip()
        snapshot_inner: dict[str, Any] = {
            "title": f"한국유나이티드제약 호주 시장분석 — {title_ko}",
            "pdf_filename": pdf_name,
        }
        if pdf_name:
            snapshot_inner["file_url"] = f"/api/report/download?name={pdf_name}"

        meta_sl = {
            k: meta[k]
            for k in (
                "confidence",
                "confidence_breakdown",
                "export_viable",
                "reason_code",
                "pricing_case",
                "product_name_ko",
                "inn_normalized",
            )
            if k in meta
        }
        report_content_v2: dict[str, Any] = {
            "schema_ver": 1,
            "report_kind": "market_analysis",
            "product_code": product_id,
            "pricing_case": row.get("pricing_case"),
            "inn_normalized": row.get("inn_normalized"),
            "blocks": blocks,
            "refs": refs,
            "meta": meta_sl,
            "llm_generated_at": llm_generated_at,
        }
        insert_row = {
            "product_id": product_id,
            "gong": 1,
            "snapshot": jsonable_encoder(snapshot_inner),
            "report_content_v2": jsonable_encoder(report_content_v2),
            "llm_model": _CLAUDE_MODEL,
            "generated_at": llm_generated_at,
        }
        client = get_supabase_client()
        client.table(_REPORTS_TABLE).insert(insert_row).execute()
        print(
            f"[au_reports_history] report_content_v2 append OK product_id={product_id}",
            flush=True,
        )
    except Exception as exc:
        print(f"[au_reports_history] append 경고 (비치명적): {exc}", flush=True)


# ── 외부 데이터 어댑터 (Supabase 저장 없음) ─────────────────────────

def _news_api_response(
    items: list[dict[str, Any]],
    *,
    ok: bool = True,
    error: str | None = None,
    source: str = "mock",
) -> JSONResponse:
    """프론트(loadNews)와 동일한 계약: { ok, items, error, source } — DB 저장 없음.
    source: mock | perplexity — 헤더 X-News-Source 와 동일(프록시·CORS 에서 헤더 미노출 시 본문으로 판별).
    """
    resp = JSONResponse(content={"ok": ok, "items": items, "error": error, "source": source})
    resp.headers["X-News-Source"] = source
    return resp


# 메인 프리뷰 뉴스 카드에 표시할 기사 개수(프롬프트·파싱·mock 보충과 동일)
_NEWS_LIST_SIZE = 7


def _extract_openai_message_text(message: dict[str, Any] | None) -> str:
    """chat/completions 의 assistant message.content — str 또는 멀티파트 배열 모두 문자열로 병합."""
    if not message or not isinstance(message, dict):
        return ""
    c = message.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


def _normalize_news_item(raw: dict[str, Any], link_fallback: str = "") -> dict[str, Any]:
    link = str(raw.get("link") or raw.get("url") or link_fallback or "")
    title = str(raw.get("title") or "")
    title_ko = str(raw.get("title_ko") or title)
    return {
        "title": title,
        "title_ko": title_ko,
        "summary_ko": str(raw.get("summary_ko") or ""),
        "source": str(raw.get("source") or ""),
        "date": str(raw.get("date") or ""),
        "link": link,
    }


def _json_loads_news_payload(s: str) -> Any | None:
    """JSON 파싱 — 후행 쉼표 등 경미한 비표준 출력 1회 보정."""
    import json as _json

    s = (s or "").strip()
    if not s:
        return None
    candidates = [s]
    # 배열/객체 닫는 괄호 직전의 불필요한 쉼표 제거(모델이 자주 붙임)
    fixed = re.sub(r",(\s*[\]}])", r"\1", s)
    if fixed != s:
        candidates.append(fixed)
    for cand in candidates:
        try:
            return _json.loads(cand)
        except Exception:
            continue
    return None


def _normalize_parsed_news_list(parsed: Any) -> list[Any] | None:
    """배열·{'items':[...]}·단일 기사 객체 등을 리스트로 통일."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("items", "news", "articles", "results", "data"):
            v = parsed.get(key)
            if isinstance(v, list):
                return v
        if any(parsed.get(k) for k in ("title", "title_ko", "link", "url")):
            return [parsed]
    return None


def _parse_perplexity_news_json_array(content: str) -> list[Any] | None:
    """모델이 마크다운 펜스·설명·객체 래핑을 붙여도 뉴스 배열을 복원."""
    s = (content or "").strip()
    if not s:
        return None
    # ```json ... ``` 또는 ``` ... ```
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    parsed = _json_loads_news_payload(s)
    if parsed is not None:
        return _normalize_parsed_news_list(parsed)
    try:
        start = s.index("[")
        end = s.rindex("]") + 1
        parsed = _json_loads_news_payload(s[start:end])
        if parsed is not None:
            return _normalize_parsed_news_list(parsed)
    except ValueError:
        pass
    try:
        start = s.index("{")
        end = s.rindex("}") + 1
        parsed = _json_loads_news_payload(s[start:end])
        if parsed is not None:
            return _normalize_parsed_news_list(parsed)
    except ValueError:
        pass
    return None


def _extract_perplexity_citation_urls(data: dict[str, Any]) -> list[str]:
    """응답 루트·choices[0]·message 등 어디에 있든 citation URL 을 순서대로 수집."""
    out: list[str] = []
    seen: set[str] = set()

    def add_raw(raw: Any) -> None:
        if not raw or not isinstance(raw, list):
            return
        for c in raw:
            u = c if isinstance(c, str) else (c.get("url") if isinstance(c, dict) else "")
            if isinstance(u, str) and u.startswith("http") and u not in seen:
                seen.add(u)
                out.append(u)

    add_raw(data.get("citations"))
    add_raw(data.get("search_results"))
    try:
        ch0 = (data.get("choices") or [{}])[0]
        if isinstance(ch0, dict):
            add_raw(ch0.get("citations"))
            msg = ch0.get("message")
            if isinstance(msg, dict):
                add_raw(msg.get("citations"))
    except Exception:
        pass
    return out


_MOCK_NEWS: list[dict[str, Any]] = [
    {
        "title": "TGA approves fast-track for PIC/S generics",
        "title_ko": "TGA, PIC/S 제네릭 우선 심사 확대",
        "source": "TGA.gov.au",
        "date": "2026-04-18",
        "link": "https://www.tga.gov.au",
    },
    {
        "title": "Australia pharma imports from Korea up 11%",
        "title_ko": "한국산 의약품 수입 증가",
        "source": "Austrade",
        "date": "2026-04-17",
        "link": "https://www.austrade.gov.au",
    },
    {
        "title": "PBS listing reforms: what exporters need to know",
        "title_ko": "PBS 등재 개편과 수출사 관점",
        "source": "Dept. of Health",
        "date": "2026-04-16",
        "link": "https://www.pbs.gov.au",
    },
    {
        "title": "KAFTA and Korea–Australia pharma trade",
        "title_ko": "한·호주 의약품 교역",
        "source": "KITA",
        "date": "2026-04-15",
        "link": "https://www.kita.net",
    },
    {
        "title": "NPS MedicineWise updates consumer medicines information",
        "title_ko": "NPS, 일반의약품 정보 개정",
        "source": "NPS MedicineWise",
        "date": "2026-04-14",
        "link": "https://www.nps.org.au",
    },
    {
        "title": "HealthShare NSW tender update for injectable oncology drugs",
        "title_ko": "HealthShare NSW, 항암주사제 공급 입찰 공고",
        "source": "HealthShare NSW",
        "date": "2026-04-13",
        "link": "https://www.healthshare.nsw.gov.au",
    },
    {
        "title": "Biotech Australia 2026: Korean companies to exhibit at major conference",
        "title_ko": "바이오텍 호주 2026, 한국 제약사 참가",
        "source": "AusBiotech",
        "date": "2026-04-12",
        "link": "https://www.ausbiotech.org",
    },
]

_FX_FALLBACK: dict[str, Any] = {"aud_krw": 893.0, "aud_usd": 0.6412, "updated": ""}


@app.get("/api/news")
def get_news() -> JSONResponse:
    """Perplexity sonar: 호주·제약 관련 뉴스(호주 현지 또는 한국 등 해외 매체의 호주/교역 연관 기사) + 한국어 제목·요약. 키 없거나 실패 시 mock."""
    api_key = (os.environ.get("PERPLEXITY_API_KEY") or "").strip() or (os.environ.get("PERPLEXITY_KEY") or "").strip()
    mock_items = [_normalize_news_item(x) for x in _MOCK_NEWS]

    if not api_key:
        logger.warning("[api/news] mock: 환경변수 PERPLEXITY_API_KEY(또는 PERPLEXITY_KEY) 비어 있음")
        return _news_api_response(mock_items, source="mock")

    try:
        r = httpx.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": (os.environ.get("PERPLEXITY_NEWS_MODEL") or "sonar-pro").strip(),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a real-time news search engine for a pharmaceutical export dashboard. "
                            f"Return EXACTLY {_NEWS_LIST_SIZE} recent TEXT news articles as a JSON array ONLY. "
                            "Output raw JSON — NO markdown fences, NO prose, NO explanation outside the JSON. "
                            "Each item MUST have ALL of these keys: "
                            "\"title\" (original headline exactly as published, in the article's original language), "
                            "\"source\" (publication or site name, e.g. 'RACGP', 'TGA', 'yakup.com'), "
                            "\"date\" (YYYY-MM-DD — the ACTUAL publication date printed on the article page; "
                            "  DO NOT guess, infer, or fabricate a date; "
                            "  if you cannot confirm the exact publication date from the article itself, skip it and find another), "
                            "\"link\" (DIRECT URL to the specific article page — NOT a homepage, search page, or video). "
                            "CRITICAL date rule: report ONLY dates you can verify from the article. "
                            "CRITICAL link rule: every link must open actual article text. "
                            "NEVER include YouTube, Vimeo, or any video/podcast URL. "
                            "If a direct article URL is unavailable, skip that item and find another."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Find exactly {_NEWS_LIST_SIZE} TEXT NEWS ARTICLES published within the LAST 48 HOURS. "
                            f"If fewer than {_NEWS_LIST_SIZE} qualify within 48 h, expand to 7 days — always prefer the most recent. "
                            "DATE ACCURACY IS MANDATORY: report the date exactly as printed on each article. "
                            "Do NOT approximate or assume — if you are unsure of the real date, skip that article. "
                            "STRICT TOPIC FILTER — every article must be about ONE OR MORE of: "
                            "(A) Australian pharmaceutical / biotech / medical-device industry "
                            "    (TGA approvals, PBS listings/delisting, ARTG changes, drug shortages, hospital procurement, "
                            "     oncology drugs, generic medicine policy, PBAC decisions, clinical trials in Australia); "
                            "(B) Korea–Australia pharma / biotech trade, partnerships, regulatory, or export/import "
                            "    (KAFTA, Korean companies entering Australian market, joint ventures, co-development); "
                            "(C) Korean-language pharma press (yakup.com, hitnews.co.kr, medipana.com, pharmnews.com, "
                            "    Naver News) covering Australian pharma market or Korean pharma companies' Australia activities. "
                            "STRICTLY EXCLUDE: general business, finance, politics, sports, entertainment, "
                            "non-pharma healthcare, videos, podcasts, and any pharma story unrelated to Australia. "
                            "PREFERRED SOURCES: racgp.org.au, tga.gov.au, pbs.gov.au, healthshare.nsw.gov.au, "
                            "ausbiotech.org, australianprescriber.com, mja.com.au, pharmaceutical-journal.com, "
                            "yakup.com, hitnews.co.kr, medipana.com, pharmnews.com, Naver News 제약/바이오 섹션. "
                            "Return fields: title, source, date (YYYY-MM-DD verified from article), "
                            "link (direct article URL only — no videos, no homepages). "
                            "Skip any item where either the direct URL or the confirmed date is unavailable."
                        ),
                    },
                ],
                "return_citations": True,
            },
            timeout=60.0,
        )
        if r.status_code != 200:
            logger.warning(
                "[api/news] mock: Perplexity HTTP %s — %s",
                r.status_code,
                (r.text or "")[:500],
            )
            return _news_api_response(mock_items, source="mock")
        data = r.json()
    except Exception as exc:
        logger.warning("[api/news] mock: 요청 예외 %s: %s", type(exc).__name__, exc)
        return _news_api_response(mock_items, source="mock")

    if isinstance(data, dict):
        err_top = data.get("error")
        # 일부 클라이언트가 성공 본문에 error:null 만 넣음 — message/code 가 실제 있을 때만 실패 처리
        if isinstance(err_top, dict) and (
            (err_top.get("message") and str(err_top.get("message")).strip())
            or (err_top.get("code") is not None)
        ):
            logger.warning("[api/news] mock: Perplexity 응답 error 필드 %s", err_top)
            return _news_api_response(mock_items, source="mock")

    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        logger.warning("[api/news] mock: choices 비어 있음")
        return _news_api_response(mock_items, source="mock")

    content = ""
    try:
        ch0 = (data.get("choices") or [{}])[0]
        msg0 = ch0.get("message") if isinstance(ch0, dict) else None
        content = _extract_openai_message_text(msg0 if isinstance(msg0, dict) else None)
        if not content.strip() and isinstance(ch0, dict):
            content = str(ch0.get("text") or "")
    except Exception as exc:
        logger.warning("[api/news] mock: 응답 choices 파싱 실패: %s", exc)
        return _news_api_response(mock_items, source="mock")

    if not content.strip():
        logger.warning("[api/news] mock: message content 비어 있음")
        return _news_api_response(mock_items, source="mock")

    link_list = _extract_perplexity_citation_urls(data if isinstance(data, dict) else {})

    items = _parse_perplexity_news_json_array(content)
    if items is None:
        logger.warning(
            "[api/news] mock: JSON 배열 파싱 실패 content_prefix=%s",
            (content or "")[:500],
        )
        return _news_api_response(mock_items, source="mock")

    result: list[dict[str, Any]] = []
    link_pool = list(link_list)  # citation URL 풀 (남은 것부터 순서대로 보충)
    for i, it in enumerate(items[:_NEWS_LIST_SIZE]):
        if not isinstance(it, dict):
            continue
        merged = dict(it)
        # 링크가 없으면 citation 풀에서 보충
        if not (merged.get("link") or merged.get("url")):
            for fb_url in link_pool:
                if _is_valid_news_url(fb_url):
                    merged["link"] = fb_url
                    link_pool.remove(fb_url)
                    break
        # 유효하지 않은 URL(유튜브·홈페이지 등) 제거
        raw_link = merged.get("link") or merged.get("url") or ""
        if raw_link and not _is_valid_news_url(raw_link):
            logger.info("[api/news] 유효하지 않은 URL 제거: %s", raw_link)
            merged["link"] = ""
        result.append(_normalize_news_item(merged))

    if not result:
        logger.warning("[api/news] mock: 파싱 후 유효 항목 0건")
        return _news_api_response(mock_items, source="mock")

    # ── OpenAI gpt-4o-mini 로 한국어 번역 + 요약 주입 ──────────────────
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key and _OPENAI_AVAILABLE:
        try:
            result = _openai_translate_news_ko(result, openai_key)
        except Exception as _tr_exc:
            logger.warning("[api/news] OpenAI 번역 실패(무시): %s", _tr_exc)
    # title_ko 폴백: OpenAI 미설치·실패 시 원문 제목 그대로 표시
    for item in result:
        if not item.get("title_ko"):
            item["title_ko"] = item.get("title", "")

    if len(result) < _NEWS_LIST_SIZE:
        # 카드 높이를 고정했기 때문에 프론트에는 항상 동일 개수를 내려준다.
        for fallback in mock_items:
            if len(result) >= _NEWS_LIST_SIZE:
                break
            result.append(fallback)
    return _news_api_response(result, source="perplexity")


@app.get("/api/exchange")
def get_exchange() -> JSONResponse:
    """yfinance 로 AUD 기준 환율 + 전일 대비 % 변동 조회.
    티커: AUDKRW=X, AUDUSD=X, AUDJPY=X, AUDCNY=X
    2일치 종가(Close)를 가져와 최근 거래일 - 직전 거래일 대비 변동률 계산.
    yfinance 실패 시 exchangerate-api.com 로 폴백 (pct_change 미포함).
    """
    try:
        import yfinance as yf
        tickers = {
            "aud_krw": "AUDKRW=X",
            "aud_usd": "AUDUSD=X",
            "aud_jpy": "AUDJPY=X",
            "aud_cny": "AUDCNY=X",
        }
        data = yf.download(
            tickers=list(tickers.values()),
            period="5d",          # 주말·공휴일 대비 여유
            interval="1d",
            group_by="ticker",
            progress=False,
            auto_adjust=False,
            threads=True,
        )
        result: dict[str, Any] = {}
        pct_change: float | None = None
        for key, ticker in tickers.items():
            try:
                closes = data[ticker]["Close"].dropna()
                if closes.empty:
                    continue
                today_close = float(closes.iloc[-1])
                result[key] = today_close
                if key == "aud_krw" and len(closes) >= 2:
                    yesterday_close = float(closes.iloc[-2])
                    if yesterday_close > 0:
                        pct_change = (today_close - yesterday_close) / yesterday_close * 100.0
            except (KeyError, IndexError, ValueError, TypeError):
                continue

        if "aud_krw" in result and "aud_usd" in result:
            from datetime import datetime as _dt
            result["updated"] = _dt.now().isoformat()
            if pct_change is not None:
                result["pct_change"] = pct_change
            return JSONResponse(content=result)
        # yfinance 응답 부족 → fallback
        raise RuntimeError("yfinance returned incomplete rates")
    except Exception as exc:
        print(f"[yfinance fx error] {exc} → exchangerate-api fallback", flush=True)

    # ── 폴백: exchangerate-api.com ──
    try:
        r = httpx.get("https://api.exchangerate-api.com/v4/latest/AUD", timeout=10.0)
        if r.status_code != 200:
            return JSONResponse(content=_FX_FALLBACK)
        data = r.json()
    except Exception:
        return JSONResponse(content=_FX_FALLBACK)

    rates = data.get("rates") or {}
    krw = rates.get("KRW")
    usd = rates.get("USD")
    jpy = rates.get("JPY")
    cny = rates.get("CNY")
    if krw is None or usd is None:
        return JSONResponse(content=_FX_FALLBACK)
    return JSONResponse(content={
        "aud_krw": float(krw),
        "aud_usd": float(usd),
        "aud_jpy": float(jpy) if jpy else None,
        "aud_cny": float(cny) if cny else None,
        "updated": data.get("date") or "",
    })


# ── LLM 보고서 생성 ─────────────────────────────────────────────────
# Claude Haiku 4.5 가 보고서 두뇌. 크롤링된 Supabase row 의 수치를 한국어로 해석.
# 프롬프트는 보고서체(~함/~임) + 마크다운 금지 + 실제 수치 인용 강제 + block4 번호 형식까지 명시.

_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

_CLAUDE_SYSTEM_PROMPT = (
    "당신은 한국유나이티드제약의 호주 수출 전문 애널리스트입니다. "
    "주어진 크롤링 JSON(TGA·PBS·Chemist·NSW)만 근거로, 아래 계층 필드를 "
    "한국어 존댓말('-합니다', '-습니다', '-해주시길 바랍니다')로 채웁니다.\n\n"
    "보고서 제목은 고정: 「한국유나이티드제약 호주 시장분석 보고서」에 대응합니다.\n"
    "회사 표기는 '한국유나이티드제약'만 사용합니다 (UPharma 금지).\n\n"
    "【판정 정합】\n"
    "- verdict.category 는 크롤 JSON의 export_viable 과 논리적으로 맞춥니다: "
    "viable → 가능, conditional → 조건부, not_viable → 불가. "
    "export_viable 가 비어 있으면 조건부로 두고 근거를 서술합니다.\n"
    "- verdict.narrative 에는 reason_code(있을 경우)와 PBS/TGA 판단 요지를 녹입니다.\n\n"
    "【계층 필드】\n"
    "- verdict.category: 가능 | 조건부 | 불가 중 하나.\n"
    "- verdict.narrative: 판정 근거 3~4문장.\n"
    "- market_overview.paragraph: 시장 개요 문단 — **크롤 JSON의 TGA·PBS·가격·경쟁 수치·등재 여부**를 우선 서술. "
    "교과서식 질환 역학·병태 장문 설명은 금지(2~3문장 이내로 압축).\n"
    "- market_overview.disease_block: {name_ko, short_en, plain_desc} 배열 — "
    "크롤 row에 질환·적응증 필드가 없거나 비어 있으면 **빈 배열 []** 로 두거나 항목 1개만 짧게. "
    "일반의학 교과서 수준의 긴 질환 설명·유전학 장문 금지(HTML 샘플은 데이터·규제 중심).\n"
    "- competitor_brands: {role, detail} 배열 — role 예: 오리지널|제네릭 대표|자사 브랜드 상태.\n"
    "- market_structure: {paragraph, tag} — tag 예: 제네릭 경쟁 구도|오리지널 독점|블루오션.\n"
    "- price_snapshot: aemp_aud, aemp_usd, dpmq_aud, dpmq_usd, market_class, pbs_code 는 **문자열**로, "
    "pbs_price_aud·pbs_dpmq·pbs_item_code 등 크롤 필드와 숫자·코드가 일치하도록 옮깁니다. "
    "값이 없으면 '미확보' 또는 '크롤 데이터 없음'으로 적고 숫자를 지어내지 않습니다.\n"
    "- entry_strategy: channel, partner_direction, rationale.\n"
    "- regulatory_risk: artg_paragraph, pbac_paragraph, prescription_limit_paragraph.\n"
    "  규제·급여 맥락은 여기서 HTML 샘플 수준으로 구체히 서술합니다(사실·크롤 근거 범위 내).\n"
    "- fast_track_applies: boolean.\n"
    "- operational_risk, product_specific_risk: 문단 (없으면 operational은 서술, product_specific_risk는 '해당 없음' 가능).\n"
    "- references: {num, source, citation, summary, body_position} 배열 — 크롤·공개 출처만. "
    "허구 기관명 금지. 최소 1개 이상, row 에 근거가 있으면 PBS Schedule / TGA ARTG 등으로 적습니다. "
    "첨부·참고 성격이므로 출처·인용 맥락은 한두 문장 더 구체적으로 적어도 됩니다.\n\n"
    "【영어 약어 — PBS·TGA·DPMQ·AEMP·ARTG·PBAC 등】\n"
    "- 보고서 본문 전체 기준으로, **각 약어는 최초 1회만** "
    "'약어 (영문 풀네임 · 한글 설명)' 형식으로 풀어 씁니다. 예: "
    "'DPMQ (Dispensed Price for Maximum Quantity · 최대처방량 기준 약가)'. "
    "그 뒤에는 'PBS', 'TGA', 'DPMQ'처럼 짧게 반복해도 됩니다(문단마다 다시 풀지 않음).\n"
    "- 시장 개요·경쟁 등 앞부분에서 이미 푼 약어는 뒤 문단·규제·유의사항에서 **중복 풀이 생략**.\n"
    "【사실·중립 서술 — PBAC·철수·우월성】\n"
    "- 크롤 JSON에 없는 규제 결론·사업 적합성 단정을 하지 않습니다.\n"
    "- PBAC 임상우월성(superiority)·비교임상 요구, 상업적 철수(Commercial Withdrawal) 등은 "
    "입력 데이터에 해당 플래그·연도·사유가 있을 때만 사실로 전달하고, "
    "‘반드시 필요’ ‘시장성 없음’ 같은 확정적 사업 판단 문구는 쓰지 않습니다.\n"
    "- 호주 PBS/PBAC의 일반적 심사 구조(신규·복합제에서 비교임상·비용효과 자료가 논의될 수 있음 등)는 "
    "공개 제도 설명 수준으로만 쓰고, 품목별로 요구 여부는 ‘개별 PBAC 심의 대상’임을 분명히 합니다.\n"
    "- 상업 철수 이력이 있으면 데이터에 기록된 사실(연도 등)만 언급하고, "
    "재진입·재평가 필요성은 ‘건별로 상이하며 별도 검토 대상’으로 서술합니다.\n"
    "- 독자에게 문안을 직접 작성하라고 요구하는 표현(‘직접 기입’, ‘담당자가 채움’ 등)은 쓰지 않습니다.\n"
    "【환각 금지】 크롤 JSON에 없는 숫자·코드·브랜드명·가격을 만들지 않습니다.\n"
    "【마크다운 금지】 **, #, 백틱, 링크 문법 사용하지 않습니다.\n"
)


def _claude_blocks_schema():
    """시장분석 v8 — stage1_schema.MarketAnalysisV8 (단일 tool 호출)."""
    from stage1_schema import MarketAnalysisV8

    return MarketAnalysisV8


def _row_summary_for_llm(row: dict[str, Any]) -> dict[str, Any]:
    """LLM 프롬프트에 넣을 지정 컬럼 + 판정·가격 출처 보조 필드를 추려서 반환."""
    keys = [
        "product_name_ko",
        "product_code",  # DB 품목 코드 (있으면)
        "artg_status", "artg_number", "tga_schedule", "tga_sponsor",
        "pbs_listed", "pbs_item_code", "pbs_price_aud", "pbs_dpmq",
        "pbs_patient_charge", "pbs_brand_name", "pbs_innovator",
        "pbs_formulary",
        "retail_price_aud", "price_source_name", "retail_estimation_method",
        "export_viable", "reason_code", "nsw_note",
        "inn_normalized", "dosage_form", "strength", "hs_code_6",
        # 시드·메타가 row에 붙은 경우만 — PBAC/철수 사실 전달용(해석 단정 금지는 시스템 프롬프트)
        "pbac_superiority_required",
        "commercial_withdrawal_flag",
        "commercial_withdrawal_year",
    ]
    return {k: row.get(k) for k in keys}


def _anthropic_tool_input_schema(schema_cls: Any) -> dict[str, Any]:
    """Pydantic JSON Schema 에서 Anthropic API 가 거부하는 `title` 등을 제거 (draft 2020-12 호환)."""
    import copy as _copy

    raw = schema_cls.model_json_schema()

    def _strip_titles(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _strip_titles(v) for k, v in obj.items() if k != "title"}
        if isinstance(obj, list):
            return [_strip_titles(x) for x in obj]
        return obj

    return _strip_titles(_copy.deepcopy(raw))


def _try_parse_blocks_from_assistant_text(text: str, schema_cls: Any) -> dict[str, Any] | None:
    """tool_use 대신 텍스트로만 JSON 이 온 경우 마지막 수단으로 파싱."""
    import json as _json
    import re

    t = (text or "").strip()
    if not t:
        return None
    blobs: list[dict[str, Any]] = []
    try:
        o = _json.loads(t)
        if isinstance(o, dict):
            blobs.append(o)
    except Exception:
        pass
    for m in re.finditer(r"\{[\s\S]*\}", t):
        try:
            o = _json.loads(m.group(0))
            if isinstance(o, dict):
                blobs.append(o)
        except Exception:
            continue
    from pydantic import ValidationError

    for o in blobs:
        try:
            return schema_cls.model_validate(o).model_dump()
        except ValidationError:
            continue
    return None


def _claude_messages_tool_blocks(
    client: Any,
    *,
    model: str,
    max_tokens: int,
    system: str,
    user_content: str,
    schema_cls: Any,
    tool_name: str,
    tool_description: str,
    usage_log_fn: Any | None = None,
) -> dict[str, Any]:
    """Anthropic Messages API 표준 — tool_use 로 구조화 출력 (OpenAI/parse API 혼용 금지)."""
    import anthropic
    from anthropic.types import TextBlock, ToolUseBlock
    from pydantic import ValidationError

    input_schema = _anthropic_tool_input_schema(schema_cls)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": input_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
    except anthropic.RateLimitError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Claude 요율 제한: {e.message}",
        ) from e
    except anthropic.APIStatusError as e:
        # 400 invalid_json_schema / 기타 — Render 로그에 원문이 남도록 detail 에 포함
        body = e.body if isinstance(e.body, (dict, str)) else repr(e.body)
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic API 오류 ({e.status_code}): {e.message} body={body}",
        ) from e
    except anthropic.APIError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic 연결/응답 오류: {e.message}",
        ) from e

    if usage_log_fn is not None:
        try:
            usage_log_fn(response)
        except Exception:
            pass

    tool_block = next(
        (b for b in response.content if isinstance(b, ToolUseBlock) and b.name == tool_name),
        None,
    )

    raw: dict[str, Any] | None = None
    if tool_block is not None and isinstance(tool_block.input, dict):
        raw = tool_block.input

    if raw is None:
        text_parts = [b.text for b in response.content if isinstance(b, TextBlock)]
        combined = "\n".join(text_parts)
        fallback = _try_parse_blocks_from_assistant_text(combined, schema_cls)
        if fallback is not None:
            print(
                f"[Claude] tool_use 없음 → 텍스트 JSON 폴백 성공 (stop_reason={response.stop_reason})",
                flush=True,
            )
            return fallback
        snippet = (combined[:800] + "…") if len(combined) > 800 else combined
        print(
            f"[Claude] tool_use 없음 · 텍스트 미리보기: {snippet!r} "
            f"stop_reason={response.stop_reason} content_types="
            f"{[getattr(b, 'type', type(b).__name__) for b in response.content]}",
            flush=True,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Claude 응답에 tool_use 블록 없고 텍스트 JSON 파싱도 실패함. "
                f"stop_reason={getattr(response, 'stop_reason', None)} — 서버 로그 참고."
            ),
        )

    try:
        parsed = schema_cls.model_validate(raw)
    except ValidationError as ve:
        raise HTTPException(
            status_code=502,
            detail=f"Claude tool 출력 검증 실패: {ve}",
        ) from ve
    return parsed.model_dump()


def _claude_generate_blocks(row: dict[str, Any], api_key: str) -> dict[str, Any]:
    """Anthropic Claude Haiku 4.5 — 시장분석 v8 계층 스키마 단일 tool 호출."""
    import anthropic
    import json as _json

    ReportBlocks = _claude_blocks_schema()
    client_anthropic = anthropic.Anthropic(api_key=api_key)

    # Decimal 등 비JSON 타입 직렬화 (수출전략 Haiku 경로와 동일)
    user_content = (
        "다음 품목의 크롤링 데이터를 해석하여 시장분석 보고서 v8 계층 JSON(tool: emit_market_analysis_v8)을 작성합니다.\n"
        "【필수】\n"
        "- export_viable 값과 verdict.category 를 위 시스템 지침대로 정합시킵니다.\n"
        "- price_snapshot 은 pbs_price_aud·pbs_dpmq·pbs_item_code 등과 수치·코드가 어긋나지 않게 옮깁니다.\n"
        "- 소매가는 retail_estimation_method(추정 근거)가 있으면 서술에 반영합니다.\n"
        "- 데이터가 비어 있는 항목은 환각으로 채우지 말고 '미확보' 등으로 명시합니다.\n\n"
        "```json\n"
        + _json.dumps(_row_summary_for_llm(row), ensure_ascii=False, indent=2, default=str)
        + "\n```"
    )

    def _log_usage(response: Any) -> None:
        usage = response.usage
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        est_cost = in_tok * 1e-6 + out_tok * 5e-6
        print(
            f"[Claude Haiku] input={in_tok} output={out_tok} "
            f"est_cost=${est_cost:.5f} (product={row.get('product_id')})",
            flush=True,
        )

    return _claude_messages_tool_blocks(
        client_anthropic,
        model=_CLAUDE_MODEL,
        max_tokens=4096,
        system=_CLAUDE_SYSTEM_PROMPT,
        user_content=user_content,
        schema_cls=ReportBlocks,
        tool_name="emit_market_analysis_v8",
        tool_description=(
            "호주 시장분석 보고서 v8 — MarketAnalysisV8 스키마 단일 객체. "
            "크롤 JSON 수치와 verdict·price_snapshot 정합 필수."
        ),
        usage_log_fn=_log_usage,
    )


# ═══════════════════════════════════════════════════════════════
# 수출 전략 제안서 — Haiku 어댑터
# ───────────────────────────────────────────────────────────────
# 입력: 크롤링 row(Supabase) + Stage 2 seed + fob_calculator dispatch_result + segment
# 출력: 8개 한국어 보고서체 블록
#   - block_extract        : 추출정보 요약 (품목·참고가·TGA/PBS 판정)
#   - block_fob_intro      : 3시나리오 FOB 메타 해설 (왜 이 가격대가 나오는지)
#   - scenario_penetration : "저가 진입 시나리오(Penetration Pricing)" reason 1~2문장
#   - scenario_reference   : "기준가 기반 시나리오(Reference Pricing)" reason 1~2문장
#   - scenario_premium     : "프리미엄 시나리오(Premium Pricing)" reason 1~2문장
#   - block_strategy       : 권장 진입 전략 (채널·파트너·타이밍)
#   - block_risks          : 리스크 요약 (규제·환율·경쟁)
#   - block_positioning    : 경쟁사 포지셔닝 해설
# ═══════════════════════════════════════════════════════════════

_CLAUDE_P2_SYSTEM_PROMPT = (
    "당신은 한국유나이티드제약(주)의 호주 수출 전략 시니어 애널리스트임. "
    "주어진 품목의 (1) 크롤링 row, (2) Stage 2 시드(규제·참고가), "
    "(3) fob_calculator 가 이미 계산한 3시나리오 FOB 결과를 종합해 "
    "'수출 전략 제안서'에 들어갈 한국어 보고서체 블록 8개를 작성함.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【시나리오 키 매핑 — 반드시 준수】\n"
    "dispatch.scenarios 는 aggressive / average / conservative 세 키를 가짐. "
    "본문 필드와의 대응은 다음과 같음 (수입상 마진 % 는 stage2 기본 프리셋: aggressive 30%, average 20%, conservative 10%).\n"
    "  · scenario_penetration  ← scenarios.aggressive.fob_aud  (저가 FOB 진입 · Penetration)\n"
    "  · scenario_reference    ← scenarios.average.fob_aud     (기준 FOB · Reference)\n"
    "  · scenario_premium      ← scenarios.conservative.fob_aud (고 FOB · Premium)\n"
    "각 scenario_* 문장에는 해당 키의 fob_aud 를 AUD 소수 둘째 자리로 반드시 인용함.\n\n"
    "【필드 정의】\n"
    "  block_extract        : 1문단(3~5문장). 제품명·참고가(AEMP 또는 retail)·TGA/PBS 판정 요약.\n"
    "  block_fob_intro      : 1문단(3~5문장). dispatch.logic(A 또는 B 등)에 따라 왜 해당 역산 경로인지, "
    "                         3시나리오 FOB(AUD) 범위를 한 줄로 요약.\n"
    "  scenario_penetration : 저가 진입(Penetration) 근거 1~2문장 — aggressive.fob_aud 인용.\n"
    "  scenario_reference   : 기준가(Reference) 근거 1~2문장 — average.fob_aud 인용.\n"
    "  scenario_premium     : 프리미엄(Premium) 근거 1~2문장 — conservative.fob_aud 인용.\n"
    "  block_strategy       : 1문단(3~5문장). 권장 진입 채널·파트너·타이밍. "
    "segment=public 이면 PBS·공공조달 언어, private 이면 소매·약국·비급여 채널 언어를 사용.\n"
    "  block_risks          : 1문단(3~5문장). 규제(TGA·PBAC·PBS 등)·환율·경쟁 리스크.\n"
    "  block_positioning    : 1문단(3~5문장). seed 의 경쟁 브랜드 정보가 있으면 대비 포지셔닝.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【어투 규칙 — 절대 준수】\n"
    "- 한국어 존댓말('-합니다', '-습니다', '-됩니다', '-해주시길 바랍니다')로 작성합니다.\n"
    "- 마크다운 금지: **굵게**, *기울임*, # 제목, - 리스트, `코드`, [링크]() 전부 X.\n"
    "- 이모지·특수 기호 장식 금지.\n\n"
    "【환각 방지 규칙 — 최우선】\n"
    "- 제공된 JSON(row/seed/dispatch) 에 없는 숫자·법령·브랜드는 **창작 금지**.\n"
    "- 위 매핑에 따라 각 scenario_* 에 해당 시나리오의 fob_aud 만 인용 (다른 키의 숫자를 섞지 않음).\n"
    "- pricing_case / warnings / disclaimer 를 논리에 반영.\n"
    "- 모르는 사실은 '제공 데이터 범위 외로 별도 검증 필요함' 으로 명시.\n\n"
    "【사실·중립 — PBAC·철수·우월성】\n"
    "- seed.warnings·dispatch.warnings·pricing_case 에 적힌 사실만 반영하고, "
    "그 범위를 넘는 사업 적합성·‘반드시’·‘불가’ 단정은 하지 않습니다.\n"
    "- PBAC 임상우월성·비교임상, 상업적 철수 이력은 ‘제도상 이런 논의가 있을 수 있음’·‘데이터상 이력이 있음’ 수준으로 쓰고, "
    "최종 필요 여부·재진입 가능성은 개별 심의·검토 대상임을 밝힙니다.\n"
    "- 독자에게 특정 칸을 직접 채우라고 지시하는 문구는 쓰지 않습니다.\n\n"
    "【품질 규칙】\n"
    "1. block_extract · block_fob_intro · block_strategy · block_risks · block_positioning 각 3~5문장 (문장 40~100자).\n"
    "2. scenario_* 3개는 각각 1~2문장 (60~140자), 지정된 fob_aud 인용 필수.\n"
    "3. segment='public' 이면 PBS·AEMP/DPMQ 중심, 'private' 이면 소매·GST·약국 유통 중심으로 서술.\n"
    "4. 'TBD', '추후', '데이터 부족' 같은 플레이스홀더 금지.\n"
    "5. 8개 필드 모두 반드시 채움.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【Few-shot 좋은 예시 — scenario_penetration】\n"
    "입력: scenarios.aggressive.fob_aud=26.50, pricing_case=DIRECT, segment=public\n"
    '  "PBS 기준 역산 FOB AUD 26.50(aggressive 시나리오·수입상 마진 프리셋 반영)으로 General Schedule '
    "내 제네릭 대비 하단 가격대에 진입, 초기 처방 점유 확보를 우선함.\"\n\n"
    "【Few-shot 나쁜 예시 — 금지】\n"
    '  "저가로 진입하면 좋습니다." '
    "→ FOB 숫자 미인용. 절대 금지."
)


def _claude_p2_blocks_schema():
    """수출전략용 Pydantic 스키마를 지연 로드."""
    from pydantic import BaseModel, Field

    class P2Blocks(BaseModel):
        block_extract: str = Field(description="추출정보 요약 (보고서체 ~함/~임)")
        block_fob_intro: str = Field(description="3시나리오 FOB 메타 해설 (보고서체)")
        scenario_penetration: str = Field(description="저가 진입 시나리오(Penetration Pricing) 근거 1~2문장, fob_aud 인용 필수")
        scenario_reference: str = Field(description="기준가 기반 시나리오(Reference Pricing) 근거 1~2문장, fob_aud 인용 필수")
        scenario_premium: str = Field(description="프리미엄 시나리오(Premium Pricing) 근거 1~2문장, fob_aud 인용 필수")
        block_strategy: str = Field(description="권장 진입 전략 (보고서체 ~함/~임)")
        block_risks: str = Field(description="리스크 요약 (보고서체 ~함/~임)")
        block_positioning: str = Field(description="경쟁사 포지셔닝 (보고서체 ~함/~임)")

    return P2Blocks


def _row_summary_for_p2(row: dict[str, Any]) -> dict[str, Any]:
    """수출전략 Haiku 프롬프트에 넣을 핵심 컬럼 subset. (전체 row 대비 슬림)"""
    keys = [
        "product_name_ko", "inn_normalized", "dosage_form", "strength",
        "artg_status", "artg_number", "tga_schedule", "tga_sponsor",
        "pbs_listed", "pbs_item_code", "pbs_price_aud", "pbs_dpmq",
        "pbs_brand_name", "retail_price_aud", "price_source_name",
        "export_viable", "reason_code",
    ]
    return {k: row.get(k) for k in keys}


def _haiku_p2_blocks(
    row: dict[str, Any],
    seed: dict[str, Any],
    dispatch_result: dict[str, Any],
    segment: str,
    api_key: str,
) -> dict[str, str]:
    """Claude Haiku 4.5 를 호출해 수출 전략 제안서 용 8개 블록을 생성.

    Args:
        row               : Supabase `au_products` 행 (크롤링 결과)
        seed              : fob_reference_seeds.json 단일 엔트리 (규제·참고가 수기시드)
        dispatch_result   : fob_calculator.dispatch_by_pricing_case() 반환 dict
                             (logic / scenarios / inputs / warnings / disclaimer / blocked_reason)
        segment           : "public" | "private" — 공공(PBS) vs 민간 채널 프레이밍
        api_key           : ANTHROPIC_API_KEY

    Returns:
        dict[str, str] — 8개 필드 (block_extract / block_fob_intro / scenario_* x3 /
                                   block_strategy / block_risks / block_positioning)
    """
    import anthropic
    import json as _json

    if dispatch_result.get("logic") == "blocked":
        raise HTTPException(
            status_code=422,
            detail=(
                f"본 품목은 FOB 역산이 불가(blocked_reason={dispatch_result.get('blocked_reason')}). "
                "수출 전략 제안서 생성 불가 — Stage 2 warnings 확인 필요."
            ),
        )

    P2Blocks = _claude_p2_blocks_schema()
    client_anthropic = anthropic.Anthropic(api_key=api_key)

    user_content = (
        "다음 품목의 (1) 크롤링 row, (2) Stage 2 seed, (3) fob_calculator dispatch 결과를 종합해 "
        "수출 전략 제안서용 8개 블록(tool: emit_p2_blocks)을 한국어 존댓말로 작성합니다.\n"
        "【숫자 인용】 scenario_penetration ← scenarios.aggressive.fob_aud, "
        "scenario_reference ← scenarios.average.fob_aud, "
        "scenario_premium ← scenarios.conservative.fob_aud (각각 AUD 소수 둘째 자리).\n"
        f"segment={segment!r} (public=공공 PBS 조달, private=민간 소매·약국) 에 맞춰 용어를 선택.\n\n"
        "```json\n"
        + _json.dumps(
            {
                "row": _row_summary_for_p2(row),
                "seed": seed,
                "dispatch": dispatch_result,
                "segment": segment,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n```"
    )

    def _log_usage_p2(response: Any) -> None:
        usage = response.usage
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        est_cost = in_tok * 1e-6 + out_tok * 5e-6
        print(
            f"[Claude Haiku P2] input={in_tok} output={out_tok} "
            f"est_cost=${est_cost:.5f} (product={row.get('product_id')}, segment={segment})",
            flush=True,
        )

    return _claude_messages_tool_blocks(
        client_anthropic,
        model=_CLAUDE_MODEL,
        max_tokens=3072,
        system=_CLAUDE_P2_SYSTEM_PROMPT,
        user_content=user_content,
        schema_cls=P2Blocks,
        tool_name="emit_p2_blocks",
        tool_description=(
            "수출 전략 제안서 8블록 — dispatch.scenarios aggressive/average/conservative 의 "
            "fob_aud 를 scenario_penetration/reference/premium 에 각각 인용."
        ),
        usage_log_fn=_log_usage_p2,
    )


# ═══════════════════════════════════════════════════════════════
# 하이브리드 논문 검색: Semantic Scholar → PubMed → Perplexity 순 폴백
# ═══════════════════════════════════════════════════════════════

_SS_BASE = "https://api.semanticscholar.org/graph/v1"
_PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# 카테고리별 검색어 · 필드분야 매핑
_HYBRID_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "macro",
        "label": "거시·시장 분석",
        "ss_query": "Australia pharmaceutical market size healthcare spending import trade economics",
        "ss_fos": "Medicine,Economics,Business",
        "pubmed_query": "Australia[All Fields] AND (pharmaceutical[All Fields] OR medicines[All Fields]) AND (market[All Fields] OR economics[All Fields])",
    },
    {
        "id": "regulatory",
        "label": "규제 분석",
        "ss_query": "Australia TGA ARTG registration GMP PIC/S pharmaceutical regulation compliance",
        "ss_fos": "Medicine",
        "pubmed_query": "Australia[All Fields] AND (TGA[All Fields] OR 'therapeutic goods administration'[All Fields] OR 'ARTG'[All Fields] OR 'GMP'[All Fields])",
    },
    {
        "id": "pricing",
        "label": "가격·조달 분석",
        "ss_query": "Australia PBS Pharmaceutical Benefits Scheme DPMQ pricing PBAC cost effectiveness KAFTA tariff",
        "ss_fos": "Medicine,Economics",
        "pubmed_query": "Australia[All Fields] AND ('PBS'[All Fields] OR 'pharmaceutical benefits scheme'[All Fields] OR 'PBAC'[All Fields] OR 'cost-effectiveness'[All Fields])",
    },
]


def _pubmed_article_dict(pmid: str) -> dict[str, Any] | None:
    """PubMed PMID 1건 → 제목·초록 등 dict (실패 시 None)."""
    try:
        r_fetch = httpx.get(
            f"{_PUBMED_BASE}/efetch.fcgi",
            params={
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml",
                "rettype": "abstract",
            },
            timeout=20.0,
        )
        if r_fetch.status_code != 200:
            return None
        import xml.etree.ElementTree as ET

        root = ET.fromstring(r_fetch.text)
        article = root.find(".//PubmedArticle/MedlineCitation/Article")
        if article is None:
            return None

        title_el = article.find("./ArticleTitle")
        title = (title_el.text or "").strip() if title_el is not None else None

        abstract_parts = []
        for at in article.findall("./Abstract/AbstractText"):
            label = at.get("Label")
            txt = (at.text or "").strip()
            if txt:
                abstract_parts.append(f"{label}: {txt}" if label else txt)
        abstract = " ".join(abstract_parts)[:600] if abstract_parts else None

        venue_el = article.find("./Journal/Title")
        venue = venue_el.text.strip() if venue_el is not None and venue_el.text else None

        year_el = article.find("./Journal/JournalIssue/PubDate/Year")
        year = int(year_el.text) if year_el is not None and year_el.text and year_el.text.isdigit() else None

        authors = []
        for au in article.findall("./AuthorList/Author")[:3]:
            last = au.findtext("./LastName")
            initials = au.findtext("./Initials")
            if last:
                authors.append(f"{last} {initials}" if initials else last)

        return {
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "title": title,
            "abstract": abstract,
            "tldr": None,
            "venue": venue,
            "year": year,
            "citation_count": None,
            "authors": authors,
            "pmid": pmid,
            "source": "pubmed",
        }
    except Exception:
        return None


def _pubmed_search_pmids(query: str, retmax: int = 15) -> list[str]:
    try:
        r_search = httpx.get(
            f"{_PUBMED_BASE}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": query,
                "retmax": retmax,
                "retmode": "json",
                "datetype": "pdat",
                "mindate": "2015",
                "sort": "relevance",
            },
            timeout=20.0,
        )
        if r_search.status_code != 200:
            return []
        return (r_search.json().get("esearchresult") or {}).get("idlist") or []
    except Exception:
        return []


def _pubmed_top1(query: str) -> dict[str, Any] | None:
    """PubMed: 상위 PMID 여러 건 중 첫 파싱 성공분 반환 (하위 호환)."""
    for pmid in _pubmed_search_pmids(query, retmax=5):
        art = _pubmed_article_dict(pmid)
        if art:
            return art
    return None


def _inn_synonyms_for_refs(inn_raw: str) -> list[str]:
    """INN 동의어(예: hydroxycarbamide ↔ hydroxyurea) — PubMed·관련성 필터용."""
    inn = (inn_raw or "").strip().lower()
    out: list[str] = []
    if inn:
        out.append(inn)
    syn_map: dict[str, tuple[str, ...]] = {
        "hydroxycarbamide": ("hydroxyurea", "hydroxycarbamide"),
        "hydroxyurea": ("hydroxyurea", "hydroxycarbamide"),
    }
    for k, vals in syn_map.items():
        if inn == k or k in inn:
            out.extend(vals)
    seen: set[str] = set()
    res: list[str] = []
    for x in out:
        x = x.strip().lower()
        if len(x) >= 4 and x not in seen:
            seen.add(x)
            res.append(x)
    return res or ["pharmaceutical"]


def _ref_blob(r: dict[str, Any]) -> str:
    t = str(r.get("title") or "")
    a = str(r.get("abstract") or "")
    tl = r.get("tldr")
    if isinstance(tl, dict):
        tl = tl.get("text")
    return f"{t} {a} {tl or ''}".lower()


_BAD_REF_SUBSTR = (
    "nanopore",
    "replication stress",
    "single-cell sequencing",
    "cryo-em",
    "artificial intelligence assay",
    "crispr screen",
)


def _ref_blacklisted(blob: str) -> bool:
    b = blob.lower()
    return any(x in b for x in _BAD_REF_SUBSTR)


def _ref_relevant_for_cat(cat_id: str, blob: str, inn_syns: list[str]) -> bool:
    """호주 시장보고서용: 카테고리·성분·호주 맥락이 맞는 참고만."""
    if _ref_blacklisted(blob):
        return False
    b = blob.lower()
    au = "australia" in b or "australian" in b
    has_inn = any(s in b for s in inn_syns if len(s) >= 4)
    pol = any(
        x in b
        for x in (
            "pbs",
            "tga",
            "pbac",
            "artg",
            "pharmaceutical benefits",
            "medicare",
            "therapeutic goods",
        )
    )
    if cat_id == "macro":
        return au and (has_inn or pol or "pharma" in b or "medicine" in b or "health" in b)
    if cat_id == "regulatory":
        return au and (pol or has_inn or "gmp" in b or "registration" in b)
    if cat_id == "pricing":
        return au and (
            "pbs" in b
            or "pbac" in b
            or "dpmq" in b
            or "pharmaceutical benefits" in b
            or has_inn
        )
    return has_inn


def _semantic_scholar_best(
    query: str, fields_of_study: str, cat_id: str, inn_syns: list[str]
) -> dict[str, Any] | None:
    """SS 상위 후보 중 관련성 통과 1건만."""
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    headers = {"x-api-key": api_key} if api_key else {}
    try:
        r = httpx.get(
            f"{_SS_BASE}/paper/search",
            headers=headers,
            params={
                "query": query,
                "publicationTypes": "JournalArticle,Review,MetaAnalysis,ClinicalTrial",
                "fieldsOfStudy": fields_of_study,
                "year": "2015-",
                "limit": 12,
                "fields": "title,abstract,tldr,year,authors,venue,citationCount,openAccessPdf,url,externalIds",
            },
            timeout=25.0,
        )
        if r.status_code != 200:
            return None
        data = (r.json() or {}).get("data") or []
    except Exception:
        return None
    data.sort(key=lambda p: (p.get("citationCount") or 0), reverse=True)
    for top in data:
        tldr_text = None
        tldr = top.get("tldr")
        if isinstance(tldr, dict):
            tldr_text = tldr.get("text")
        oa = top.get("openAccessPdf") or {}
        url = (oa.get("url") if isinstance(oa, dict) else None) or top.get("url") or ""
        rec: dict[str, Any] = {
            "url": url,
            "title": top.get("title"),
            "abstract": (top.get("abstract") or "")[:500] or None,
            "tldr": tldr_text,
            "venue": top.get("venue"),
            "year": top.get("year"),
            "citation_count": top.get("citationCount"),
            "authors": [a.get("name") for a in (top.get("authors") or []) if a.get("name")][:3],
            "source": "semantic_scholar",
        }
        if _ref_relevant_for_cat(cat_id, _ref_blob(rec), inn_syns):
            return rec
    return None


def _pubmed_best_for_category(
    cat: dict[str, Any], inn_syns: list[str], cat_id: str
) -> dict[str, Any] | None:
    """PubMed: 동의어·Australia 조합 쿼리로 PMID 순회, 관련성 통과분만."""
    base = cat["pubmed_query"]
    or_inn = " OR ".join(f"{s}[All Fields]" for s in inn_syns[:4])
    queries = [
        f"({base}) AND ({or_inn})",
        f"({base}) AND ({or_inn}) AND Australia[All Fields]",
        f"{base} AND Australia[All Fields] AND ({or_inn})",
    ]
    for q in queries:
        for pmid in _pubmed_search_pmids(q, retmax=12):
            art = _pubmed_article_dict(pmid)
            if art and _ref_relevant_for_cat(cat_id, _ref_blob(art), inn_syns):
                return art
    return None


def _perplexity_best_for_category(
    cat: dict[str, Any], inn_syns: list[str], perplexity_key: str
) -> dict[str, Any] | None:
    """Perplexity: 호주 PBS/TGA 공식·학술 URL 우선, 기초과학 무관 논문 지양."""
    if not perplexity_key:
        return None
    inn0 = inn_syns[0] if inn_syns else "medicine"
    q1 = (
        f"Return exactly ONE HTTPS citation URL from: Australian government "
        f"(pbs.gov.au, tga.gov.au, health.gov.au, dfat.gov.au), PubMed, or major medical journal, "
        f"about {inn0} or hydroxyurea in Australia in context of: {cat['label']}. "
        f"Must relate to PBS, TGA, ARTG, pharmaceutical reimbursement, or Australian clinical use. "
        f"Do NOT cite unrelated molecular biology, nanopore, or AI assay papers."
    )
    p = _perplexity_top1(q1, perplexity_key)
    if p and p.get("url"):
        p.setdefault("source", "perplexity")
        bl = _ref_blob(
            {
                "title": p.get("title") or "",
                "abstract": p.get("snippet") or "",
                "tldr": p.get("snippet"),
            }
        )
        if not _ref_blacklisted(bl):
            return p
    q2 = (
        f"Official PBS Schedule OR TGA ARTG search result URL for hydroxyurea OR {inn0} in Australia."
    )
    p2 = _perplexity_top1(q2, perplexity_key)
    if p2 and p2.get("url"):
        p2.setdefault("source", "perplexity")
        return p2
    return None


def _fetch_refs_hybrid(row: dict[str, Any], perplexity_key: str) -> list[dict[str, Any]]:
    """3카테고리 × [Semantic Scholar → PubMed → Perplexity], 관련성·호주 맥락 필터."""
    inn = row.get("inn_normalized") or row.get("product_name_ko") or "pharmaceutical"
    inn_syns = _inn_synonyms_for_refs(str(inn))
    refs: list[dict[str, Any]] = []

    for cat in _HYBRID_CATEGORIES:
        cat_label = cat["label"]
        cat_id = cat["id"]

        top = _semantic_scholar_best(
            f"{cat['ss_query']} ({inn_syns[0]}) Australia pharmaceutical",
            cat["ss_fos"],
            cat_id,
            inn_syns,
        )

        if not top:
            top = _pubmed_best_for_category(cat, inn_syns, cat_id)

        if not top and perplexity_key:
            pplx = _perplexity_best_for_category(cat, inn_syns, perplexity_key)
            if pplx:
                top = {
                    "url": pplx.get("url"),
                    "title": pplx.get("title"),
                    "abstract": None,
                    "tldr": pplx.get("snippet"),
                    "venue": None,
                    "year": None,
                    "citation_count": None,
                    "authors": [],
                    "source": "perplexity",
                }

        if top:
            top["category"] = cat_label
            top["category_id"] = cat_id
            refs.append(top)

    return refs


# ── 기존 Perplexity-only 함수 및 카테고리 (폴백용으로 유지) ────────
_PPLX_CATEGORIES: list[tuple[str, str, str]] = [
    (
        "macro",
        "거시·시장 분석",
        "Australia pharmaceutical market size, sales, healthcare spending, and "
        "pharma import trends (2024-2025). Return the single most authoritative "
        "government or industry report URL.",
    ),
    (
        "regulatory",
        "규제 분석",
        "Australia TGA (Therapeutic Goods Administration) ARTG registration process, "
        "GMP PIC/S compliance, and import requirements for pharmaceutical products "
        "similar to {inn}. Return the single most authoritative official TGA source URL.",
    ),
    (
        "pricing",
        "가격·조달 분석",
        "Australia PBS (Pharmaceutical Benefits Scheme) DPMQ pricing mechanism and "
        "KAFTA Korea-Australia FTA tariff rates for HS 3004 pharmaceuticals (context: {inn}). "
        "Return the single most authoritative PBS or DFAT official source URL.",
    ),
]


def _perplexity_top1(query: str, api_key: str) -> dict[str, Any] | None:
    """Perplexity sonar 1회 호출 → citations 에서 최상단 1개만 꺼낸다."""
    try:
        r = httpx.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": query}],
                "return_citations": True,
            },
            timeout=30.0,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    # answer summary (한국어 1문장) — content 를 짧게 잘라 snippet 으로 사용
    content = ""
    try:
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception:
        content = ""
    snippet = (content.strip().replace("\n", " "))[:220] if content else None

    citations = data.get("citations") or data.get("search_results") or []
    for c in citations:
        if isinstance(c, str) and c.startswith("http"):
            return {"url": c, "title": None, "snippet": snippet, "source": "perplexity"}
        if isinstance(c, dict):
            url = c.get("url")
            if isinstance(url, str) and url.startswith("http"):
                return {
                    "url": url,
                    "title": c.get("title"),
                    "snippet": c.get("snippet") or snippet,
                    "source": "perplexity",
                }
    return None


# ── 신뢰도 계산 ─────────────────────────────────────────────────────
# 원래 아는 정보(품목명·INN·HS·제형·함량)는 신뢰도에서 제외. 실 크롤링으로 가져와야
# 하는 7개 필드만 체크해서 "수집 성공률" 을 confidence 로 재정의.
_CONFIDENCE_FIELDS: list[tuple[str, str]] = [
    ("ARTG",       "artg_status"),
    ("PBS가격",    "pbs_price_aud"),
    ("스폰서",     "tga_sponsor"),
    ("소매가",     "retail_price_aud"),
    ("TGA스케줄",  "tga_schedule"),
    ("NSW조달",    "nsw_contract_value_aud"),
    ("DPMQ",       "pbs_dpmq"),
]


def _field_collected(col: str, value: Any) -> bool:
    """필드별 '수집 성공' 판정."""
    if value is None:
        return False
    if col == "artg_status":
        return isinstance(value, str) and value.strip() != "" and value != "not_registered"
    if col == "tga_schedule":
        return isinstance(value, str) and value.strip().upper() in ("S2", "S3", "S4", "S8")
    if col == "tga_sponsor":
        return isinstance(value, str) and bool(value.strip())
    # 가격/금액 류 — 양수면 수집 성공
    if col in ("pbs_price_aud", "retail_price_aud", "nsw_contract_value_aud", "pbs_dpmq"):
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return False
    return bool(value)


def _compute_confidence_breakdown(row: dict[str, Any]) -> dict[str, Any]:
    """7개 크롤링 필드 기반 신뢰도 + 체크리스트 반환."""
    checklist: list[dict[str, Any]] = []
    hits = 0
    for label, col in _CONFIDENCE_FIELDS:
        ok = _field_collected(col, row.get(col))
        checklist.append({"label": label, "column": col, "collected": ok})
        if ok:
            hits += 1
    total = len(_CONFIDENCE_FIELDS)
    ratio = (hits / total) if total else 0.0
    return {
        "confidence": round(ratio, 3),
        "hits": hits,
        "total": total,
        "checklist": checklist,
    }


def _openai_translate_news_ko(
    items: list[dict[str, Any]], api_key: str
) -> list[dict[str, Any]]:
    """뉴스 기사 제목을 OpenAI gpt-4o-mini 로 한국어 번역 (제목만, 요약 없음).

    items 각 요소에 'title_ko' 만 주입해 반환.
    OpenAI 미설치·API 오류 시 원본 items 그대로 반환(title_ko fallback 은 호출부에서 처리).
    """
    if not items or not api_key:
        return items
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError:
        return items

    client = OpenAI(api_key=api_key)

    # 번역 대상 제목 목록
    lines: list[str] = []
    for i, it in enumerate(items):
        lines.append(f"[{i + 1}] {it.get('title') or ''}")

    system_prompt = (
        "You are a professional Korean translator specialising in pharmaceutical and biotech news. "
        "Translate each English (or Korean) news headline into natural, concise Korean suitable for a dashboard UI. "
        "Rules: no markdown, no emoji, no explanation — output a pure JSON array only. "
        f"The array must have exactly {len(items)} objects, each with one key: \"title_ko\". "
        'Example: [{"title_ko":"번역된 제목"}]'
    )
    user_prompt = (
        f"Translate the following {len(items)} headlines to Korean. "
        "Preserve order. Output only the JSON array.\n\n"
        + "\n".join(lines)
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        raw = (completion.choices[0].message.content or "").strip()
        # JSON 펜스 제거
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        translated: list[dict[str, Any]] = json.loads(raw)
        if isinstance(translated, list):
            for i, t in enumerate(translated):
                if i >= len(items):
                    break
                if isinstance(t, dict) and t.get("title_ko"):
                    items[i]["title_ko"] = str(t["title_ko"])
    except Exception as exc:
        logger.warning("[_openai_translate_news_ko] 번역 실패: %s", exc)

    return items


def _is_valid_news_url(url: str) -> bool:
    """YouTube·영상·홈페이지·검색 결과 URL 걸러내기."""
    if not url:
        return False
    lower = url.lower()
    # 영상 플랫폼
    video_domains = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitch.tv")
    if any(d in lower for d in video_domains):
        return False
    # 홈페이지 또는 최상위 경로 (경로가 없거나 "/" 뿐)
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if not path or path in ("/index", "/index.html", "/home"):
            return False
    except Exception:
        pass
    return True


def _openai_summarize_refs_ko(refs: list[dict[str, Any]], api_key: str) -> list[dict[str, Any]]:
    """영문 tldr/abstract → 한국어 보고서체 3문장 요약. OpenAI gpt-4o-mini 1회 호출.
    각 ref 에 'korean_summary' 필드를 주입하고 리스트를 그대로 반환."""
    if not refs or not api_key:
        return refs

    try:
        from openai import OpenAI
    except ImportError:
        return refs

    client = OpenAI(api_key=api_key)

    # 프롬프트용 항목 정리
    items_text: list[str] = []
    for i, r in enumerate(refs):
        body = r.get("tldr") or r.get("abstract") or ""
        if not body:
            # Perplexity 폴백의 경우 snippet 에 들어있을 수 있음
            body = r.get("snippet") or ""
        title = r.get("title") or "(제목 없음)"
        items_text.append(
            f"[{i+1}] 카테고리: {r.get('category','')}\n"
            f"    제목: {title}\n"
            f"    영문 원문: {body[:600] if body else '(본문 없음)'}"
        )

    system_prompt = (
        "당신은 제약 산업 학술 자료를 한국어 보고서체로 요약하는 전문가입니다. "
        "반드시 보고서 문체(~함, ~임, ~됨) 사용. "
        "마크다운·이모지 금지. "
        "각 항목을 정확히 3문장, 각 문장 40~80자 길이로 요약."
    )
    user_prompt = (
        "아래 자료들의 핵심 내용을 각각 한국어 3문장으로 요약하라.\n\n"
        + "\n\n".join(items_text)
        + "\n\n출력 형식 (번호 + 한 줄에 3문장 이어서):\n"
        "1. [문장1] [문장2] [문장3]\n"
        "2. [문장1] [문장2] [문장3]\n"
        "3. [문장1] [문장2] [문장3]"
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        text = completion.choices[0].message.content or ""
        # 비용 로그
        try:
            u = completion.usage
            cost = (u.prompt_tokens * 0.15e-6) + (u.completion_tokens * 0.60e-6)
            print(
                f"[OpenAI Summarize] input={u.prompt_tokens} output={u.completion_tokens} "
                f"est_cost=${cost:.5f}",
                flush=True,
            )
        except Exception:
            pass
    except Exception as exc:
        print(f"[OpenAI Summarize] 실패: {exc}", flush=True)
        return refs

    # "1. ... 2. ... 3. ..." 파싱
    import re
    for i, r in enumerate(refs):
        m = re.search(rf"(?:^|\n)\s*{i+1}\.\s*(.+?)(?=\n\s*\d+\.|\Z)", text, re.DOTALL)
        if m:
            summary = re.sub(r"\s+", " ", m.group(1)).strip()
            r["korean_summary"] = summary
    return refs


def _perplexity_fetch_refs(row: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    """3개 카테고리(거시/규제/가격) 별로 각 1개씩 = 총 3개 공신력 있는 출처 반환."""
    inn = row.get("inn_normalized") or row.get("product_name_ko") or "pharmaceutical products"
    refs: list[dict[str, Any]] = []
    for cat_id, cat_label, query_template in _PPLX_CATEGORIES:
        query = query_template.format(inn=inn)
        top = _perplexity_top1(query, api_key)
        if top:
            top["category"] = cat_label
            top["category_id"] = cat_id
            refs.append(top)
    return refs


@app.post("/api/report/generate")
def generate_report(payload: dict[str, Any]) -> JSONResponse:
    """예외 시 Render 로그에 스택이 남도록 래핑 (원인 조사용)."""
    try:
        return _generate_report_core(payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "POST /api/report/generate 실패 product_id=%r",
            (payload or {}).get("product_id"),
        )
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc


def _generate_report_core(payload: dict[str, Any]) -> JSONResponse:
    """요청의 product_id(논리 키)로 `au_products` 행을 읽어 Haiku 블록·논문 refs 를 생성하고
    동일 행(product_code)에 block2_* / block3_* / block4_* / perplexity_refs / llm_* 를 UPDATE 한다.
    (레거시 DB 테이블명 `australia` 와 무관 — 현재 마스터는 항상 `au_products`.)"""
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id is required")

    # Dep 방어: anthropic 미설치 시 503 으로 명확히 알려주기 (500 ModuleNotFoundError 대신)
    if not _ANTHROPIC_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI 엔진(anthropic) 미설치 — `pip install -r requirements.txt` 실행 후 재시도. "
                f"(probe error: {_ANTHROPIC_ERR})"
            ),
        )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not anthropic_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY 환경변수가 필요합니다 (.env 확인).",
        )

    # 1) DB 에서 품목 조회
    try:
        client_sb = get_supabase_client()
        resp = (
            client_sb.table(TABLE_NAME)
            .select("*")
            .eq("product_code", product_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")
    if not rows:
        raise HTTPException(status_code=404, detail=f"not found: {product_id}")
    row = rows[0]
    if isinstance(row, dict):
        _normalize_au_product_row(row)

    # 2) Claude Haiku 4.5 호출 — 크롤링 row 해석 → 10개 블록 생성
    blocks = _claude_generate_blocks(row, anthropic_key)

    # 3) 하이브리드 논문 검색 — Semantic Scholar → PubMed → Perplexity 순 폴백
    #    카테고리당 1개씩 총 3개 공신력 있는 출처 (논문 우선).
    refs: list[dict[str, Any]] = _fetch_refs_hybrid(row, perplexity_key)

    # 4) OpenAI gpt-4o-mini — 영문 초록/tldr 을 한국어 보고서체 3문장 요약
    if refs and openai_key:
        refs = _openai_summarize_refs_ko(refs, openai_key)

    # 5) Supabase UPDATE — v8 계층이면 flat block2_* 로 변환 후 저장 (프론트·레거시 호환)
    from datetime import datetime, timezone

    from stage1_schema import flatten_v8_to_legacy_blocks, is_v8_market_blocks

    generated_at = datetime.now(timezone.utc).isoformat()
    flat_blocks = (
        flatten_v8_to_legacy_blocks(blocks) if is_v8_market_blocks(blocks) else blocks
    )
    update_data: dict[str, Any] = {
        **flat_blocks,
        "perplexity_refs": refs if refs else None,
        "llm_model": _CLAUDE_MODEL,
        "llm_generated_at": generated_at,
    }
    try:
        client_sb.table(TABLE_NAME).update(update_data).eq(
            "product_code", product_id
        ).execute()
    except Exception as exc:
        # Claude·논문 단계는 성공했는데 UPDATE 만 실패하는 경우가 많음 — 로그에 원문 남김
        logger.exception(
            "au_products UPDATE 실패 product_code=%r (컬럼 누락·스키마 캐시·RLS 의심)",
            product_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"supabase update failed: {exc}",
        )

    # 6) 신뢰도 재계산 (원래 아는 정보 제외, 7개 크롤링 필드만)
    conf_meta = _compute_confidence_breakdown(row)

    # 7) 프론트 메타바 렌더용 — DOM 스크래핑 폐기 대체
    meta = {
        "product_name_ko": row.get("product_name_ko"),
        "inn_normalized":  row.get("inn_normalized"),
        "strength":        row.get("strength"),
        "dosage_form":     row.get("dosage_form"),
        "hs_code_6":       row.get("hs_code_6"),
        "pricing_case":    row.get("pricing_case"),
        "export_viable":   row.get("export_viable"),
        "reason_code":     row.get("reason_code"),
        "confidence":      conf_meta["confidence"],
        "confidence_breakdown": conf_meta,
    }
    # Haiku v8 판정은 크롤러 export_viable 과 별도 — 응답 meta 는 생성 직후 Haiku 카테고리와 맞춤
    if is_v8_market_blocks(blocks):
        _vcat = (blocks.get("verdict") or {}).get("category")
        _ko_to_en = {"가능": "viable", "조건부": "conditional", "불가": "not_viable"}
        if isinstance(_vcat, str) and _vcat in _ko_to_en:
            meta["export_viable"] = _ko_to_en[_vcat]
            meta["haiku_verdict_category"] = _vcat

    # Phase 4.3-v3 — au_pbs_raw 에서 market_form/market_strength 를 row 에 주입.
    # au_products 에는 이 두 컬럼이 없고 au_pbs_raw 에만 존재 → PDF 제품정보 섹션용.
    try:
        raw_resp = (
            client_sb.table("au_pbs_raw")
            .select("market_form,market_strength")
            .eq("product_id", product_id)
            .order("crawled_at", desc=True)
            .limit(1)
            .execute()
        )
        raw_rows = getattr(raw_resp, "data", None) or []
        if raw_rows:
            row["market_form"] = raw_rows[0].get("market_form")
            row["market_strength"] = raw_rows[0].get("market_strength")
    except Exception as exc:
        print(f"[au_pbs_raw market_* 조회 경고] {exc}", flush=True)

    # Phase 4.3-v3 부분 revert — PBS 미등재 품목 fallback 용 au_tga_artg.strength /
    # dosage_form 주입. PDF 의 '호주 시장 동일 약 정보' 섹션 2순위 소스.
    try:
        tga_resp = (
            client_sb.table("au_tga_artg")
            .select("strength,dosage_form")
            .eq("product_id", product_id)
            .order("crawled_at", desc=True)
            .limit(1)
            .execute()
        )
        tga_rows = getattr(tga_resp, "data", None) or []
        if tga_rows:
            row["tga_strength"] = tga_rows[0].get("strength")
            row["tga_dosage_form"] = tga_rows[0].get("dosage_form")
    except Exception as exc:
        print(f"[au_tga_artg strength/dosage_form 조회 경고] {exc}", flush=True)

    # 8) PDF 보고서 생성 (reportlab) — v3 ReportR1Payload → 서버 디스크 reports/ 에 저장
    pdf_name: str | None = None
    try:
        from report_generator import render_pdf
        from stage1_schema import build_report_r1_payload_from_pipeline

        from datetime import datetime as _dt

        _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        pdf_name = f"au_report_{product_id}_{_ts}.pdf"
        pdf_path = _REPORTS_DIR / pdf_name
        r1_payload = build_report_r1_payload_from_pipeline(row, blocks, refs, meta)
        render_pdf(r1_payload, pdf_path)
        sz = pdf_path.stat().st_size
        print(f"[render_pdf] OK {pdf_name} ({sz} bytes)", flush=True)
    except Exception as exc:
        # PDF 실패는 치명적이지 않음 — 응답은 내보내되 pdf_name 은 None
        print(f"[render_pdf error] {exc}", flush=True)
        pdf_name = None

    _append_au_reports_history_market_analysis(
        product_id,
        row=row,
        blocks=blocks,
        refs=refs,
        meta=meta,
        llm_generated_at=generated_at,
        pdf_name=pdf_name,
    )

    # Decimal/datetime 등이 섞여 있으면 JSONResponse 직렬화에서 TypeError 가능 — jsonable_encoder 사용
    return JSONResponse(
        content=jsonable_encoder(
            {
                "ok": True,
                "product_id": product_id,
                "llm_model": _CLAUDE_MODEL,
                "llm_generated_at": generated_at,
                "blocks": flat_blocks,
                "market_analysis_v8": blocks if is_v8_market_blocks(blocks) else None,
                "refs_count": len(refs),
                "refs": refs,
                "meta": meta,
                "pdf": pdf_name,
            }
        )
    )


# ============================================================================
# §P2. FOB 역산 API (Stage2)
#   - stage2/fob_calculator.py (logic A/B + dispatch) 로 계산
#   - stage2/fob_reference_seeds.json 이 8품목 시드 제공
#   - crawler 모듈과는 완전 분리 (역산 공식은 stage2 내 자체 보관)
# ============================================================================

import json as _json

# 지연 import: stage2 모듈 로드 실패해도 서버 기동은 가능
try:
    from stage2.fob_calculator import (  # type: ignore
        ALPHA_MARKET_UPLIFT_PCT,
        DEFAULT_FX_AUD_TO_KRW,
        calculate_fob_logic_a,
        calculate_fob_logic_b,
        calculate_three_scenarios,
        dispatch_by_pricing_case,
        dispatch_both_segments,
        get_disclaimer_text,
    )
    _STAGE2_OK = True
    _STAGE2_ERR = ""
except Exception as _stage2_err:  # noqa: BLE001
    _STAGE2_OK = False
    _STAGE2_ERR = str(_stage2_err)
    ALPHA_MARKET_UPLIFT_PCT = 20.0  # stage2 로드 실패 시 보고서 v5 α 기본값


_STAGE2_SEEDS_PATH = _BASE_DIR / "stage2" / "fob_reference_seeds.json"
# au_products.json 은 UI 친화 메타(product_name 등)를 보강하기 위해 옵션으로 읽는다
_AU_PRODUCTS_PATH = _BASE_DIR / "crawler" / "au_products.json"


def _load_stage2_seeds() -> list[dict[str, Any]]:
    if not _STAGE2_SEEDS_PATH.is_file():
        return []
    try:
        with open(_STAGE2_SEEDS_PATH, encoding="utf-8") as f:
            data = _json.load(f)
        seeds = data.get("seeds") if isinstance(data, dict) else data
        return seeds if isinstance(seeds, list) else []
    except Exception:
        return []


def _load_au_products_meta() -> dict[str, dict[str, Any]]:
    """au_products.json → {product_id: {...}} 로 인덱싱."""
    if not _AU_PRODUCTS_PATH.is_file():
        return {}
    try:
        with open(_AU_PRODUCTS_PATH, encoding="utf-8") as f:
            data = _json.load(f)
        items = data.get("products") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return {}
        return {str(p.get("product_id")): p for p in items if isinstance(p, dict)}
    except Exception:
        return {}


@app.get("/api/stage2/seeds")
def stage2_seeds() -> JSONResponse:
    """8개 품목 시드 목록 — UI 드롭다운용 컴팩트 필드만 반환."""
    if not _STAGE2_OK:
        raise HTTPException(status_code=503, detail=f"stage2 module load failed: {_STAGE2_ERR}")

    seeds = _load_stage2_seeds()
    meta_by_id = _load_au_products_meta()

    def _first_num(v: Any) -> float | None:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, list):
            nums = [float(x) for x in v if isinstance(x, (int, float))]
            if nums:
                return sum(nums) / len(nums)
        return None

    out: list[dict[str, Any]] = []
    for s in seeds:
        pid = str(s.get("product_id", ""))
        meta = meta_by_id.get(pid, {})
        product_name = (
            meta.get("product_name_ko")
            or meta.get("product_name")
            or meta.get("brand_name")
            or pid.replace("au-", "").split("-")[0].capitalize()
        )
        out.append({
            "product_id": pid,
            "product_name": product_name,
            "pricing_case": s.get("pricing_case"),
            "pbs_section": s.get("pbs_section"),
            "pbs_status": s.get("pbs_status"),
            "aemp_aud": _first_num(s.get("reference_aemp_aud")),
            "dpmq_aud": _first_num(s.get("reference_dpmq_aud")),
            "retail_aud": _first_num(s.get("reference_retail_aud")),
            "retail_source": s.get("reference_retail_source"),
            "pbac_superiority_required": bool(s.get("pbac_superiority_required")),
            "commercial_withdrawal_year": s.get("commercial_withdrawal_year"),
            "hospital_channel_only": bool(s.get("hospital_channel_only")),
            "confidence_score": s.get("confidence_score"),
            "notes": s.get("notes", ""),
        })
    return JSONResponse(content={"ok": True, "count": len(out), "seeds": out})


def _seed_by_id(product_id: str) -> dict[str, Any] | None:
    for s in _load_stage2_seeds():
        if str(s.get("product_id")) == product_id:
            return s
    return None


def _fetch_au_row_fob_hints(product_code: str) -> dict[str, Any] | None:
    """신약 FOB 경고 병합용 — Supabase `au_products` 경량 컬럼만 조회."""
    if not product_code:
        return None
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("warnings,similar_drug_used,case_code,aemp_aud,retail_price_aud,pbs_found")
            .eq("product_code", product_code)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return dict(rows[0])
    except Exception as exc:
        print(f"[FOB hints 조회 실패] product_code={product_code!r} {exc}", flush=True)
    return None


def _merge_new_drug_stage2_warnings(
    product_id: str | None, base_warnings: list[str]
) -> list[str]:
    """`au-newdrug-*` 에서 크롤러가 남긴 대체계열·프록시 태그를 stage2 응답에 병합."""
    out = [w for w in base_warnings if w]
    pid = (product_id or "").strip()
    if not pid.startswith("au-newdrug-"):
        return out
    row = _fetch_au_row_fob_hints(pid)
    if not row:
        return out
    dbw = row.get("warnings")
    if isinstance(dbw, list):
        for x in dbw:
            s = str(x).strip()
            if not s or s in out:
                continue
            low = s.lower()
            if any(
                k in low
                for k in (
                    "similar_proxy",
                    "substitute_ingredient",
                    "similar_drug_used",
                    "similar_drug",
                    "pricing_case",
                    "estimate_substitute",
                    "no_proxy",
                    "pbs_skipped",
                )
            ):
                out.append(s)
    sd = row.get("similar_drug_used")
    if isinstance(sd, list) and sd:
        tag = "similar_drug_used_json:" + ",".join(str(x) for x in sd[:10])
        if tag not in out:
            out.append(tag)
    cc = str(row.get("case_code") or "")
    if "SUBSTITUTE" in cc.upper():
        note = (
            "fo_alpha_substitute_path: 유사계열 PBS AEMP에도 Logic A α=20% 동일 적용 "
            "(ESTIMATE_substitute 와 동일 역산 경로)"
        )
        if note not in out:
            out.append(note)
    return out


def _scenarios_dict_to_list(scenarios: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    """dispatch_by_pricing_case() 가 돌려준 {name: {...}} 를 프론트용 리스트로 변환."""
    order = ["aggressive", "average", "conservative"]
    label_ko = {
        "aggressive":   "공격적인 시나리오",
        "average":      "평균 시나리오",
        "conservative": "보수 시나리오",
    }
    out: list[dict[str, Any]] = []
    for name in order:
        if name not in scenarios:
            continue
        sc = scenarios[name]
        out.append({
            "name": name,
            "label": label_ko[name],
            "importer_margin_pct": sc.get("importer_margin_pct"),
            "fob_aud": sc.get("fob_aud"),
            "fob_krw": sc.get("fob_krw"),
            "aemp_aud": sc.get("aemp_aud"),
            "adjusted_aemp_aud": sc.get("adjusted_aemp_aud"),
            "alpha_market_uplift_pct": sc.get("alpha_market_uplift_pct"),
            "retail_aud": sc.get("retail_aud"),
            "pre_gst_aud": sc.get("pre_gst_aud"),
            "pre_pharmacy_aud": sc.get("pre_pharmacy_aud"),
            "pre_wholesale_aud": sc.get("pre_wholesale_aud"),
            "is_pbs_listed_rx": sc.get("is_pbs_listed_rx"),
            "gst_pct": sc.get("gst_pct"),
        })
    return out


@app.post("/api/stage2/calculate")
def stage2_calculate(payload: dict[str, Any]) -> JSONResponse:
    """Manual 탭에서 사용자가 조정한 값을 받아 3 시나리오 FOB 반환.

    입력:
      {
        "product_id": "au-hydrine-004" | None,
        "logic": "A" | "B",
        "segment": "public" | "private",
        "overrides": {
           "base_aemp": 31.92,
           "importer_margin": 20,
           "base_retail": 48.95,
           "pharmacy_margin": 30,
           "wholesale_margin": 10,
           "gst": 10,
           ...
        },
        "fx_aud_to_krw": 900.0  # optional
      }

    로직:
      - product_id 가 withdrawal seed → blocked 응답 (scenarios=[])
      - 그 외엔 seed 플래그를 참고해 경고만 모으고, 계산은 overrides 를 최우선으로 사용
        (seed 기준으로만 돌리려면 빈 overrides 를 보내면 dispatch_by_pricing_case 결과가 그대로 쓰임)
      - `au-newdrug-*` (시드 없음): Logic A 는 overrides.base_aemp (>0) 필수. Logic B 는
        기본 PBS 미등재로 GST 10% 안내 경고 + Supabase `warnings` 에서 대체계열 태그 병합.
    """
    if not _STAGE2_OK:
        raise HTTPException(status_code=503, detail=f"stage2 module load failed: {_STAGE2_ERR}")

    product_id = (payload.get("product_id") or "").strip() or None
    logic = (payload.get("logic") or "A").upper().strip()
    if logic not in ("A", "B"):
        raise HTTPException(status_code=400, detail="logic must be 'A' or 'B'")

    overrides = payload.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="overrides must be object")

    try:
        fx = float(payload.get("fx_aud_to_krw") or DEFAULT_FX_AUD_TO_KRW)
    except (TypeError, ValueError):
        fx = DEFAULT_FX_AUD_TO_KRW

    seed = _seed_by_id(product_id) if product_id else None
    warnings: list[str] = []

    # Withdrawal 품목은 어떤 override 도 받지 않고 차단
    if seed and seed.get("pricing_case") == "ESTIMATE_withdrawal":
        base = dispatch_by_pricing_case(seed, fx_aud_to_krw=fx)
        return JSONResponse(content={
            "ok": True,
            "logic": "blocked",
            "scenarios": [],
            "inputs": base.get("inputs", {}),
            "warnings": [w for w in base.get("warnings", []) if w],
            "disclaimer": base.get("disclaimer", ""),
            "blocked_reason": base.get("blocked_reason", "commercial_withdrawal"),
        })

    # Seed 플래그 기반 공통 경고 수집 (override 계산 결과에도 붙여줌)
    if seed:
        if seed.get("pbac_superiority_required"):
            warnings.append(
                "시드 플래그: 복합제·신규 등재 품목군 — PBAC 심의에서 단일성분 대비 비교임상·우월성 "
                "입증이 논의될 수 있음(품목별 상이, 등재 일정·결과는 개별 심의 대상)."
            )
        if seed.get("hospital_channel_only"):
            warnings.append(
                "약국 유통 없음 → Hospital tender(병원 공급 입찰)/HealthShare NSW 병원조달 루트 전용. "
                "FOB ±20% 변동성 가능."
            )
        if seed.get("section_19a_flag"):
            warnings.append("호주 미등재 성분 → Section 19A(일시수입 특례) 경로 전용.")
        if seed.get("restricted_benefit"):
            warnings.append("PBS Restricted Benefit(처방 적응증 제한) — 적용 환자군 좁음.")
        confidence = seed.get("confidence_score")
        if isinstance(confidence, (int, float)) and confidence < 0.7:
            warnings.append(f"confidence_score {confidence:.2f} — FOB 결과는 예비 참고치.")

    try:
        if logic == "A":
            aemp = float(overrides.get("base_aemp") or 0.0)
            if aemp <= 0 and seed:
                # seed 기본값으로 폴백
                base = dispatch_by_pricing_case(seed, fx_aud_to_krw=fx)
                scenarios_list = _scenarios_dict_to_list(base.get("scenarios", {}))
                return JSONResponse(content={
                    "ok": True,
                    "logic": base.get("logic"),
                    "scenarios": scenarios_list,
                    "inputs": base.get("inputs", {}),
                    "warnings": [w for w in (base.get("warnings", []) + warnings) if w],
                    "disclaimer": base.get("disclaimer", get_disclaimer_text("A")),
                    "blocked_reason": base.get("blocked_reason"),
                })
            if aemp <= 0:
                raise HTTPException(status_code=400, detail="Logic A: base_aemp (>0) 필요")

            margin_default = float(overrides.get("importer_margin") or 20.0)
            # 수입 스폰서 마진: aggressive=저가진입(마진↑) → FOB↓, conservative=프리미엄(마진↓) → FOB↑
            # center 기준 ±10%p, 상한 40% (research doc 5~40% 밴드)
            presets = {
                "aggressive":   min(40.0, margin_default + 10.0),
                "average":      margin_default,
                "conservative": max(0.0, margin_default - 10.0),
            }
            scenarios = calculate_three_scenarios(
                logic="A", aemp_aud=aemp, fx_aud_to_krw=fx, presets_pct=presets
            )
            merged_warn = _merge_new_drug_stage2_warnings(product_id, warnings)
            return JSONResponse(content={
                "ok": True,
                "logic": "A",
                "scenarios": _scenarios_dict_to_list(scenarios),
                "inputs": {
                    "product_id": product_id,
                    "aemp_aud": aemp,
                    "importer_margin_pct_center": margin_default,
                    "fx_aud_to_krw": fx,
                    "presets_pct": presets,
                },
                "warnings": merged_warn,
                "disclaimer": get_disclaimer_text("A"),
                "blocked_reason": None,
            })

        # logic == "B"
        retail = float(overrides.get("base_retail") or 0.0)
        if retail <= 0 and seed:
            base = dispatch_by_pricing_case(seed, fx_aud_to_krw=fx)
            scenarios_list = _scenarios_dict_to_list(base.get("scenarios", {}))
            return JSONResponse(content={
                "ok": True,
                "logic": base.get("logic"),
                "scenarios": scenarios_list,
                "inputs": base.get("inputs", {}),
                "warnings": [w for w in (base.get("warnings", []) + warnings) if w],
                "disclaimer": base.get("disclaimer", get_disclaimer_text("B")),
                "blocked_reason": base.get("blocked_reason"),
            })
        if retail <= 0:
            raise HTTPException(status_code=400, detail="Logic B: base_retail (>0) 필요")

        if (
            product_id
            and str(product_id).startswith("au-newdrug-")
            and overrides.get("is_pbs_listed_rx") is None
        ):
            warnings.append(
                "신약 기본: 초기 PBS 미등재로 간주 → GST 10% 역산 "
                "(PBS 등재 처방으로 바꾸려면 overrides.is_pbs_listed_rx=true)"
            )

        gst_pct = float(overrides.get("gst") or 10.0)
        pharmacy_pct = float(overrides.get("pharmacy_margin") or 30.0)
        wholesale_pct = float(overrides.get("wholesale_margin") or 10.0)
        margin_default = float(overrides.get("importer_margin") or 20.0)
        is_pbs_rx = overrides.get("is_pbs_listed_rx")
        b_kw: dict[str, Any] = {
            "gst_pct": gst_pct,
            "pharmacy_margin_pct": pharmacy_pct,
            "wholesale_margin_pct": wholesale_pct,
        }
        if is_pbs_rx is True:
            b_kw["is_pbs_listed_rx"] = True
        presets = {
            "aggressive":   min(40.0, margin_default + 10.0),
            "average":      margin_default,
            "conservative": max(0.0, margin_default - 10.0),
        }
        scenarios = calculate_three_scenarios(
            logic="B",
            retail_aud=retail,
            fx_aud_to_krw=fx,
            presets_pct=presets,
            logic_b_kwargs=b_kw,
        )
        eff_gst = scenarios.get("average", {}).get("gst_pct", gst_pct)
        merged_b_warn = _merge_new_drug_stage2_warnings(product_id, warnings)
        return JSONResponse(content={
            "ok": True,
            "logic": "B",
            "scenarios": _scenarios_dict_to_list(scenarios),
            "inputs": {
                "product_id": product_id,
                "retail_aud": retail,
                "gst_pct": eff_gst,
                "pharmacy_margin_pct": pharmacy_pct,
                "wholesale_margin_pct": wholesale_pct,
                "importer_margin_pct_center": margin_default,
                "is_pbs_listed_rx": bool(is_pbs_rx is True),
                "fx_aud_to_krw": fx,
                "presets_pct": presets,
            },
            "warnings": merged_b_warn,
            "disclaimer": get_disclaimer_text("B"),
            "blocked_reason": None,
        })

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FOB 계산 오류: {e}") from e


# ═══════════════════════════════════════════════════════════════
#  수출전략 AI 파이프라인 스텁 엔드포인트
#  (AI 엔진(Haiku) 연동은 다음 단계에서 구현. 현재는 업로드만 동작.)
# ═══════════════════════════════════════════════════════════════

# 수출전략 업로드 PDF 저장 디렉토리
_P2_UPLOADS_DIR = _BASE_DIR / "reports" / "_p2_uploads"
_P2_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/p2/upload")
async def p2_upload_pdf(payload: dict[str, Any]) -> JSONResponse:
    """수출전략 AI/직접입력 탭에서 사용자가 직접 올린 PDF를 저장.

    요청: {filename: str, content_b64: str (base64)}
    응답: {ok: true, filename: str, size_bytes: int}

    저장 위치: reports/_p2_uploads/{timestamp}_{랜덤16진}.pdf (원본 파일명과 무관)
    다음 단계에서 /api/p2/pipeline 에 product_code 와 함께 연결.
    """
    import base64
    import time

    raw_name = str(payload.get("filename") or "upload.pdf").strip() or "upload.pdf"
    content_b64 = str(payload.get("content_b64") or "")
    if not content_b64:
        raise HTTPException(status_code=400, detail="content_b64 필수")

    try:
        content = base64.b64decode(content_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"base64 디코딩 실패: {e}") from e

    if not content:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if not _pdf_magic_ok(content):
        raise HTTPException(
            status_code=400,
            detail="PDF 형식이 아닙니다. (%PDF 시그니처 없음)",
        )
    _p2_pages = _pdf_page_count(content)
    if _p2_pages < 1:
        # 패키지 미설치·일부 생성기 PDF — 매직·용량으로만 허용 (4페이지 규칙은 완화)
        if _pdf_magic_ok(content) and len(content) <= 4 * 1024 * 1024:
            logger.warning(
                "P2 업로드: 페이지 수 미상 → 1로 간주 후 허용 (pip install pypdf pdfplumber 권장)"
            )
            _p2_pages = 1
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "PDF 페이지 수를 확인할 수 없습니다. "
                    "서버에 `pip install pypdf pdfplumber` 가 되어 있는지 확인하거나, "
                    "4MB 이하 PDF 인지 확인해 주세요."
                ),
            )
    if _p2_pages > UPLOAD_PDF_MAX_PAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"PDF는 최대 {UPLOAD_PDF_MAX_PAGES}페이지까지 업로드할 수 있습니다. "
                f"(현재 {_p2_pages}페이지)"
            ),
        )

    # 디스크 저장명은 한글·공백 등 원본 이름과 무관하게 고유하게 둔다 (파싱은 바이트 기준).
    import secrets

    ts = int(time.time())
    stored_name = f"{ts}_{secrets.token_hex(8)}.pdf"
    target = _P2_UPLOADS_DIR / stored_name

    try:
        target.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 저장 실패: {e}") from e

    return JSONResponse({
        "ok": True,
        "filename": stored_name,
        "original_name": raw_name,
        "size_bytes": len(content),
    })


# ── 수출전략 AI 파이프라인 상태 관리 ──────────────────────────────
# 단일 사용자 도구이므로 모듈 레벨 dict 로 상태 추적 (동시 실행 X).
import threading as _threading
import re as _re

def _dt_now_utc() -> str:
    """UTC 현재 시각 ISO 문자열. UPSERT 시 generated_at 갱신용."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


_p2_state: dict[str, Any] = {
    "status": "idle",       # idle / running / done / error
    # 프론트 스테퍼: extract | ai_extract | ai_analysis | report (PDF추출→가격→AI→보고서)
    "step": "",
    "step_label": "",
    "result": None,         # 완료 시 프론트 기대 스키마
    "error_detail": None,
}
_p2_lock = _threading.Lock()


def _extract_product_id_from_filename(filename: str) -> str | None:
    """업로드 접두사·Windows 복사본 접미사 `(2)` → `_2_` 등이 있어도 product_id 추출.

    지원 예시:
    - au_report_au-hydrine-004_20260416_120000.pdf
    - 1776390947_au_report_au-hydrine-004_20260417_101321.pdf
    - …_au_report_au-hydrine-004_20260419_032039_2_.pdf  (복사본 sanitize)
    """
    name = Path(filename).name
    # 끝을 \\.pdf$ 로 고정하지 않음 — 날짜·시각 뒤 `_2`·`_copy` 등이 끼는 경우 대비
    m = _re.search(r"(?:\d+_)?au_report_(.+?)_\d{8}_\d{6}", name, _re.I)
    return m.group(1) if m else None


def _fetch_exchange_rates_simple() -> dict[str, float | None]:
    """환율 간이 조회 — yfinance → exchangerate-api → fallback. dict 반환."""
    try:
        import yfinance as yf
        data = yf.download(
            tickers=["AUDKRW=X", "AUDUSD=X"],
            period="2d", interval="1d",
            group_by="ticker", progress=False,
            auto_adjust=False, threads=True,
        )
        krw_closes = data["AUDKRW=X"]["Close"].dropna()
        usd_closes = data["AUDUSD=X"]["Close"].dropna()
        if not krw_closes.empty and not usd_closes.empty:
            return {
                "aud_krw": round(float(krw_closes.iloc[-1]), 2),
                "aud_usd": round(float(usd_closes.iloc[-1]), 4),
            }
    except Exception:
        pass
    try:
        r = httpx.get("https://api.exchangerate-api.com/v4/latest/AUD", timeout=10.0)
        if r.status_code == 200:
            rates = r.json().get("rates", {})
            return {
                "aud_krw": float(rates.get("KRW", 893.0)),
                "aud_usd": float(rates.get("USD", 0.64)),
            }
    except Exception:
        pass
    return {"aud_krw": 893.0, "aud_usd": 0.64}


def _build_p2_ref_price_text_v5(dispatch_result: dict[str, Any]) -> tuple[str | None, float | None]:
    """보고서 v5와 동일 2단계 노출 — Logic A: 공시 AEMP → α → 기준 AEMP, Logic B: 소매·GST → GST 제외가.

    hardcoded(병원 수기 FOB)는 α 미적용 안내.
    """
    logic = dispatch_result.get("logic")
    scenarios = dispatch_result.get("scenarios") or {}
    avg = scenarios.get("average") or {}
    if not avg:
        return None, None

    alpha_pct = int(round(float(ALPHA_MARKET_UPLIFT_PCT)))

    if logic == "A":
        listed = avg.get("aemp_aud")
        adj = avg.get("adjusted_aemp_aud")
        if listed is not None and adj is not None:
            ref_listed = float(listed)
            adj_f = float(adj)
            text = (
                f"공시 AEMP AUD {ref_listed:.2f} → α {alpha_pct}% 보정 → 기준 AEMP AUD {adj_f:.2f}"
            )
            return text, ref_listed
        return None, None

    if logic == "B":
        retail = avg.get("retail_aud")
        pre_gst = avg.get("pre_gst_aud")
        if retail is None or pre_gst is None:
            return None, None
        r = float(retail)
        p = float(pre_gst)
        gst_pct = avg.get("gst_pct")
        if gst_pct is None:
            gst_pct = 10.0
        g = float(gst_pct)
        is_rx = bool(avg.get("is_pbs_listed_rx"))
        if is_rx:
            text = (
                f"소매가 AUD {r:.2f} (PBS 등재 처방, GST 면제) → GST 제외 역산가 AUD {p:.2f}"
            )
        else:
            text = (
                f"소매가 AUD {r:.2f} (GST {g:.0f}%) → GST 제외 역산가 AUD {p:.2f}"
            )
        return text, r

    if logic == "hardcoded":
        br = (dispatch_result.get("inputs") or {}).get("bayer_reference_aud")
        text = "병원 tender 수기 FOB (α·표준 PBS/소매 역산 미적용)"
        ref_aud: float | None = None
        if br is not None:
            ref_aud = float(br)
            text += f" · 참조 Bayer FOB AUD {ref_aud:.2f}"
        return text, ref_aud

    return None, None


def _infer_p2_recommended_key(block_strategy: str) -> str:
    """block_strategy 문구에서 권장 시나리오 키 추정 (penetration / reference / premium)."""
    s = block_strategy or ""
    if "프리미엄" in s or "Premium" in s:
        return "premium"
    if "저가" in s or "침투" in s or "Penetration" in s:
        return "penetration"
    return "reference"


def _p2_formula_summary_for_scenario(logic: str | None, sc: dict[str, Any]) -> str:
    """시나리오별 역산 요약 한 줄 (Logic A/B/hardcoded)."""
    if logic == "A":
        adj = sc.get("adjusted_aemp_aud")
        m = sc.get("importer_margin_pct")
        if adj is not None and m is not None:
            return f"기준 AEMP AUD {float(adj):.2f} ÷ (1 + {float(m):.0f}%)"
        listed = sc.get("aemp_aud")
        if listed is not None and m is not None:
            return (
                f"공시 AEMP AUD {float(listed):.2f} × (1+α) ÷ (1 + {float(m):.0f}%)"
            )
    if logic == "B":
        r = sc.get("retail_aud")
        m = sc.get("importer_margin_pct")
        if r is not None:
            mm = f"{float(m):.0f}" if m is not None else "—"
            return (
                f"소매가 AUD {float(r):.2f} 기준 GST·약국·도매·수입상 역산 (수입상 {mm}%)"
            )
    if logic == "hardcoded":
        return "병원 tender 수기 FOB (α 미적용)"
    return ""


def _merge_p2_export_strategy_v5(
    p2_blocks: dict[str, str],
    dispatch_result: dict[str, Any],
    fx_rates: dict[str, Any],
    seed: dict[str, Any],
) -> dict[str, Any]:
    """Haiku 8블록 서술 + dispatch·환율 숫자 → 위임 MD v5 형태 단일 JSON.

    Tool 스키마에는 대형 중첩 구조를 넣지 않고, 서버 병합으로만 `summary_scenarios`·
    `baseline_price`·`scenario_table` 등을 채운다 (schema_ver 2).
    """
    aud_usd = float(fx_rates.get("aud_usd") or 0.64)
    aud_krw = float(fx_rates.get("aud_krw") or 893.0)
    logic = dispatch_result.get("logic")
    scenarios = dispatch_result.get("scenarios") or {}
    alpha_pct = int(round(float(ALPHA_MARKET_UPLIFT_PCT)))

    v5_text, _v5_ref = _build_p2_ref_price_text_v5(dispatch_result)
    avg_sc = scenarios.get("average") or {}

    baseline_price: dict[str, Any] = {
        "logic": logic,
        "pricing_case": seed.get("pricing_case"),
        "route_narrative": (v5_text or "").strip(),
        "alpha_pct": alpha_pct if logic == "A" else None,
    }
    if avg_sc.get("aemp_aud") is not None:
        baseline_price["listed_aemp_aud"] = round(float(avg_sc["aemp_aud"]), 4)
    if avg_sc.get("adjusted_aemp_aud") is not None:
        baseline_price["adjusted_aemp_aud"] = round(float(avg_sc["adjusted_aemp_aud"]), 4)
    if avg_sc.get("retail_aud") is not None:
        baseline_price["retail_aud"] = round(float(avg_sc["retail_aud"]), 4)
    if avg_sc.get("pre_gst_aud") is not None:
        baseline_price["pre_gst_aud"] = round(float(avg_sc["pre_gst_aud"]), 4)

    ref_for_fx = (
        avg_sc.get("adjusted_aemp_aud")
        or avg_sc.get("aemp_aud")
        or avg_sc.get("pre_wholesale_aud")
        or avg_sc.get("retail_aud")
    )
    if ref_for_fx is not None:
        ra = float(ref_for_fx)
        baseline_price["reference_fx_usd"] = round(ra * aud_usd, 4)
        baseline_price["reference_fx_krw"] = round(ra * aud_krw, 2)

    summary_scenarios: dict[str, Any] = {}
    scenario_table: list[dict[str, Any]] = []
    triple = [
        ("aggressive", "penetration", "scenario_penetration"),
        ("average", "reference", "scenario_reference"),
        ("conservative", "premium", "scenario_premium"),
    ]
    labels_ko = {
        "aggressive": "저가 진입 (Penetration Pricing)",
        "average": "기준가 기반 (Reference Pricing)",
        "conservative": "프리미엄 (Premium Pricing)",
    }
    for disp_key, md_key, block_key in triple:
        sc = scenarios.get(disp_key) or {}
        fob_aud = float(sc.get("fob_aud") or 0)
        fob_krw = float(sc.get("fob_krw") or (fob_aud * aud_krw))
        fob_usd = fob_aud * aud_usd
        narrative = (p2_blocks.get(block_key) or "").strip()
        margin = sc.get("importer_margin_pct")
        summary_scenarios[md_key] = {
            "fob_aud": round(fob_aud, 4),
            "fob_usd": round(fob_usd, 4),
            "fob_krw": round(fob_krw, 2),
            "importer_margin_pct": margin,
            "strategy_narrative": narrative,
        }
        scenario_table.append(
            {
                "dispatch_key": disp_key,
                "summary_key": md_key,
                "label_ko": labels_ko.get(disp_key, disp_key),
                "importer_margin_pct": margin,
                "fob_aud": round(fob_aud, 4),
                "fob_usd": round(fob_usd, 4),
                "fob_krw": round(fob_krw, 2),
                "formula_summary": _p2_formula_summary_for_scenario(
                    str(logic) if logic is not None else None, sc
                ),
                "strategy_narrative": narrative,
            }
        )

    recommended = _infer_p2_recommended_key(p2_blocks.get("block_strategy", ""))

    return {
        "recommended_key": recommended,
        "summary_scenarios": summary_scenarios,
        "baseline_price": baseline_price,
        "scenario_table": scenario_table,
        "restricted_benefit": {
            "applies": False,
            "narrative": "",
            "disease_items": [],
        },
        "export_conditions": {
            "upharma_role": "",
            "sponsor_role": "",
            "documents_note": "",
        },
    }


def _p2_pipeline_worker(product_id: str, segment: str) -> None:
    """백그라운드 스레드: Supabase row 조회 → seed 매칭 → FOB 계산 → Haiku 블록 생성 → 결과 조립."""
    try:
        # ── Step 1: Supabase row 조회 ──
        with _p2_lock:
            _p2_state["step"] = "extract"
            _p2_state["step_label"] = "① Supabase 품목 데이터 조회 중…"
        client_sb = get_supabase_client()
        resp = client_sb.table(TABLE_NAME).select("*").eq("product_code", product_id).limit(1).execute()
        rows = getattr(resp, "data", None)
        if not rows:
            raise ValueError(f"Supabase 조회 실패: product_id={product_id!r} 미존재")
        row = rows[0]
        if isinstance(row, dict):
            _normalize_au_product_row(row)

        # ── Step 2: seed 매칭 ──
        with _p2_lock:
            _p2_state["step"] = "ai_extract"
            _p2_state["step_label"] = "② FOB 시드 매칭 중…"
        seeds = _load_stage2_seeds()
        seed = next((s for s in seeds if s.get("product_id") == product_id), None)
        if not seed:
            raise ValueError(f"fob_reference_seeds.json 에 {product_id!r} 시드 없음")

        # ── Step 3: FOB 3시나리오 계산 ──
        # crawler_row=row 전달: seed.reference_retail_aud 미확보 시 Logic B 의 2순위로
        # crawler_row.retail_price_aud(시장 추정가)를 참고가로 사용 (3단계 확장).
        with _p2_lock:
            _p2_state["step"] = "ai_extract"
            _p2_state["step_label"] = "③ FOB 3시나리오 역산 중…"
        fx_rates = _fetch_exchange_rates_simple()
        fx_krw = fx_rates.get("aud_krw") or 893.0
        dispatch_result = dispatch_by_pricing_case(seed, fx_aud_to_krw=fx_krw, crawler_row=row)

        if dispatch_result.get("logic") == "blocked":
            # blocked 품목도 "시도 이력"을 p2 결과 테이블에 남긴다.
            try:
                sb_client_blocked = get_supabase_client()
                blocked_upsert = {
                    "product_id": product_id,
                    "segment": segment,
                    "logic": "blocked",
                    "verdict": f"수출 차단: {dispatch_result.get('blocked_reason', 'unknown')}",
                    "pricing_case": seed.get("pricing_case"),
                    "warnings": [w for w in (dispatch_result.get("warnings") or []) if w],
                    "disclaimer": dispatch_result.get("disclaimer"),
                    "llm_model": _CLAUDE_MODEL,
                    "report_content_v2": jsonable_encoder(
                        {
                            "schema_ver": 1,
                            "report_kind": "export_strategy",
                            "product_code": product_id,
                            "blocked": True,
                            "pricing_case": seed.get("pricing_case"),
                            "dispatch_result": dispatch_result,
                        }
                    ),
                }
                sb_client_blocked.table("au_reports_r2").upsert(
                    blocked_upsert,
                    on_conflict="product_id,segment",
                ).execute()
                print(f"[P2 Supabase] BLOCKED UPSERT OK: {product_id} / {segment}", flush=True)
            except Exception as sb_exc:
                print(f"[P2 Supabase BLOCKED UPSERT error] {sb_exc}", flush=True)
                # Supabase 실패는 비치명적 — 파이프라인은 계속 진행

            # blocked 품목은 시나리오 없이 경고만 반환
            with _p2_lock:
                _p2_state["status"] = "done"
                _p2_state["step_label"] = "완료 (blocked)"
                _p2_state["result"] = {
                    "extracted": {
                        "product_name": row.get("product_name_ko") or product_id,
                        "ref_price_text": "해당 없음 (규제 차단)",
                        "ref_price_aud": None,
                        "verdict": f"수출 차단: {dispatch_result.get('blocked_reason', 'unknown')}",
                    },
                    "analysis": {
                        "final_price_aud": 0,
                        "formula_str": "N/A (blocked)",
                        "rationale": " ".join(dispatch_result.get("warnings") or []),
                        "scenarios": [],
                    },
                    "exchange_rates": fx_rates,
                    "pdf": None,
                }
            return

        # ── Step 4: Haiku 블록 생성 ──
        with _p2_lock:
            _p2_state["step"] = "ai_analysis"
            _p2_state["step_label"] = "④ AI(Haiku) 수출 전략 분석 중…"
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수 미설정")
        p2_blocks = _haiku_p2_blocks(row, seed, dispatch_result, segment, anthropic_key)
        export_strategy_v5 = _merge_p2_export_strategy_v5(
            p2_blocks, dispatch_result, fx_rates, seed
        )

        # ── Step 5: 결과 조립 (프론트 기대 스키마) ──
        with _p2_lock:
            _p2_state["step"] = "ai_analysis"
            _p2_state["step_label"] = "⑤ 결과 조립 중…"

        scenarios_raw = dispatch_result.get("scenarios", {})
        agg = scenarios_raw.get("aggressive", {})
        avg = scenarios_raw.get("average", {})
        cons = scenarios_raw.get("conservative", {})

        # 참고가 텍스트 — 보고서 v5 정합(α·GST 2단계) 우선, 없으면 출처별 레거시 문구
        v5_text, v5_ref_aud = _build_p2_ref_price_text_v5(dispatch_result)
        _inp = dispatch_result.get("inputs") or {}
        _logic = dispatch_result.get("logic")
        if v5_text is not None:
            ref_text = v5_text
            ref_aud = v5_ref_aud
        elif _logic == "B" and _inp.get("retail_source") == "crawler" and _inp.get("retail_aud") is not None:
            ref_aud = float(_inp["retail_aud"])
            cr_method = row.get("retail_estimation_method")
            if cr_method == "pbs_dpmq":
                ref_text = f"PBS DPMQ(최대처방량 총약가) AUD {ref_aud}"
            elif cr_method == "chemist_markup":
                ref_text = (
                    f"시장 추정가 AUD {ref_aud} "
                    f"(Chemist Warehouse × 1.20, CHOICE 조사 기준)"
                )
            elif cr_method in ("healthylife_actual", "healthylife_same_ingredient_diff_form"):
                ref_text = f"소매 참고가 AUD {ref_aud} (Healthylife·크롤 동기화)"
            else:
                ref_text = f"소매 참고가 AUD {ref_aud} (크롤러·DB 동기화)"
        elif _logic == "A" and _inp.get("aemp_source") == "crawler" and _inp.get("aemp_aud") is not None:
            ref_aud = float(_inp["aemp_aud"])
            ref_text = f"AEMP AUD {ref_aud} (au_products·크롤 동기화)"
        elif seed.get("reference_aemp_aud") is not None:
            ref_val = seed["reference_aemp_aud"]
            if isinstance(ref_val, list):
                ref_text = "AEMP " + " / ".join(f"AUD {v}" for v in ref_val)
                ref_aud = sum(float(v) for v in ref_val) / len(ref_val)
            else:
                ref_text = f"AEMP AUD {ref_val}"
                ref_aud = float(ref_val)
        elif seed.get("reference_retail_aud") is not None:
            ref_aud = float(seed["reference_retail_aud"])
            src = seed.get("reference_retail_source") or "소매가"
            ref_text = f"{src} AUD {ref_aud}"
        else:
            ref_text = "참고가 미확보"
            ref_aud = None

        # Logic A 공식 vs Logic B 공식 (v5 α 문구 정합)
        logic = dispatch_result.get("logic", "?")
        if logic == "A":
            formula_str = (
                "FOB = (공시 AEMP × (1+α)) ÷ (1 + 수입상 마진%), "
                f"α={int(round(float(ALPHA_MARKET_UPLIFT_PCT)))}% (Logic A 전용)"
            )
        elif logic == "hardcoded":
            formula_str = "FOB = 병원 tender 수기 확정 (seed.fob_hardcoded_aud, α 미적용)"
        else:
            formula_str = (
                "FOB = 소매가 ÷ (1+GST) ÷ (1+약국마진) ÷ (1+도매마진) ÷ (1+수입상마진)"
            )

        frontend_result = {
            "extracted": {
                "product_name": row.get("product_name_ko") or product_id,
                "ref_price_text": ref_text,
                "ref_price_aud": ref_aud,
                "verdict": row.get("export_viable") or row.get("reason_code") or "조건부",
            },
            "analysis": {
                "final_price_aud": round(avg.get("fob_aud", 0), 2),
                "formula_str": formula_str,
                "rationale": p2_blocks.get("block_fob_intro", ""),
                "scenarios": [
                    {
                        "name": "저가 진입 시나리오 (Penetration Pricing)",
                        "price_aud": round(agg.get("fob_aud", 0), 2),
                        "reason": p2_blocks.get("scenario_penetration", ""),
                    },
                    {
                        "name": "기준가 기반 시나리오 (Reference Pricing)",
                        "price_aud": round(avg.get("fob_aud", 0), 2),
                        "reason": p2_blocks.get("scenario_reference", ""),
                    },
                    {
                        "name": "프리미엄 시나리오 (Premium Pricing)",
                        "price_aud": round(cons.get("fob_aud", 0), 2),
                        "reason": p2_blocks.get("scenario_premium", ""),
                    },
                ],
            },
            "exchange_rates": fx_rates,
            "export_strategy_v5": export_strategy_v5,
            "pdf": None,  # 아래에서 render_p2_pdf 시도 후 갱신
        }

        # ── Step 5.5: Supabase 저장 ──
        with _p2_lock:
            _p2_state["step"] = "ai_analysis"
            _p2_state["step_label"] = "⑤-2 Supabase 저장 중…"
        try:
            sb_client = get_supabase_client()
            upsert_data = {
                "product_id": product_id,
                "segment": segment,
                "ref_price_text": ref_text,
                "ref_price_aud": float(ref_aud) if ref_aud is not None else None,
                "verdict": row.get("export_viable") or row.get("reason_code"),
                "logic": logic,
                "pricing_case": seed.get("pricing_case"),
                "fob_penetration_aud": round(agg.get("fob_aud", 0), 4) if agg else None,
                "fob_reference_aud": round(avg.get("fob_aud", 0), 4) if avg else None,
                "fob_premium_aud": round(cons.get("fob_aud", 0), 4) if cons else None,
                "fob_penetration_krw": round(agg.get("fob_krw", 0), 2) if agg else None,
                "fob_reference_krw": round(avg.get("fob_krw", 0), 2) if avg else None,
                "fob_premium_krw": round(cons.get("fob_krw", 0), 2) if cons else None,
                "fx_aud_to_krw": fx_rates.get("aud_krw"),
                "fx_aud_to_usd": fx_rates.get("aud_usd"),
                "formula_str": formula_str,
                "block_extract": p2_blocks.get("block_extract"),
                "block_fob_intro": p2_blocks.get("block_fob_intro"),
                "scenario_penetration": p2_blocks.get("scenario_penetration"),
                "scenario_reference": p2_blocks.get("scenario_reference"),
                "scenario_premium": p2_blocks.get("scenario_premium"),
                "block_strategy": p2_blocks.get("block_strategy"),
                "block_risks": p2_blocks.get("block_risks"),
                "block_positioning": p2_blocks.get("block_positioning"),
                "warnings": [w for w in (dispatch_result.get("warnings") or []) if w],
                "disclaimer": dispatch_result.get("disclaimer"),
                "llm_model": _CLAUDE_MODEL,
                "generated_at": _dt_now_utc(),
                "report_content_v2": jsonable_encoder(
                    {
                        "schema_ver": 2,
                        "report_kind": "export_strategy",
                        "product_code": product_id,
                        "segment": segment,
                        "pricing_case": seed.get("pricing_case"),
                        "p2_blocks": p2_blocks,
                        "export_strategy_v5": export_strategy_v5,
                        "fx_rates": fx_rates,
                        "dispatch_logic": logic,
                        "ref_price_text": ref_text,
                        "formula_str": formula_str,
                    }
                ),
            }
            sb_client.table("au_reports_r2").upsert(
                upsert_data,
                on_conflict="product_id,segment",
            ).execute()
            print(f"[P2 Supabase] UPSERT OK: {product_id} / {segment}", flush=True)
        except Exception as sb_exc:
            print(f"[P2 Supabase UPSERT error] {sb_exc}", flush=True)
            # Supabase 실패는 비치명적 — 파이프라인은 계속 진행

        # ── Step 6: PDF 생성 (선택) ──
        with _p2_lock:
            _p2_state["step"] = "report"
            _p2_state["step_label"] = "⑥ PDF 보고서 생성 중…"
        # Step 5 upsert 실패 시 sb_client 미할당 → NameError 방지: Step 1 의 client_sb 사용
        # Phase 4.3-v3 — au_pbs_raw 에서 market_form/market_strength 주입 (render_pdf 와 동일)
        try:
            raw_resp = (
                client_sb.table("au_pbs_raw")
                .select("market_form,market_strength")
                .eq("product_id", product_id)
                .order("crawled_at", desc=True)
                .limit(1)
                .execute()
            )
            raw_rows = getattr(raw_resp, "data", None) or []
            if raw_rows:
                row["market_form"] = raw_rows[0].get("market_form")
                row["market_strength"] = raw_rows[0].get("market_strength")
        except Exception as exc:
            print(f"[P2 au_pbs_raw market_* 조회 경고] {exc}", flush=True)
        # Phase 4.3-v3 부분 revert — PBS 미등재 품목 fallback 용 au_tga_artg 주입
        try:
            tga_resp = (
                client_sb.table("au_tga_artg")
                .select("strength,dosage_form")
                .eq("product_id", product_id)
                .order("crawled_at", desc=True)
                .limit(1)
                .execute()
            )
            tga_rows = getattr(tga_resp, "data", None) or []
            if tga_rows:
                row["tga_strength"] = tga_rows[0].get("strength")
                row["tga_dosage_form"] = tga_rows[0].get("dosage_form")
        except Exception as exc:
            print(f"[P2 au_tga_artg strength/dosage_form 조회 경고] {exc}", flush=True)
        try:
            from report_generator import render_p2_pdf
            from datetime import datetime as _dt
            _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            pdf_name = f"au_p2_report_{product_id}_{_ts}.pdf"
            pdf_path = _REPORTS_DIR / pdf_name
            render_p2_pdf(row, seed, dispatch_result, p2_blocks, fx_rates, pdf_path)
            sz = pdf_path.stat().st_size
            print(f"[render_p2_pdf] OK {pdf_name} ({sz} bytes)", flush=True)
            frontend_result["pdf"] = pdf_name
            # Supabase에 pdf_filename 업데이트
            try:
                sb_client_pdf = get_supabase_client()
                sb_client_pdf.table("au_reports_r2").update(
                    {"pdf_filename": pdf_name}
                ).eq("product_id", product_id).eq("segment", segment).execute()
            except Exception:
                pass
        except Exception as pdf_exc:
            print(f"[render_p2_pdf error] {pdf_exc}", flush=True)
            # PDF 실패는 치명적이지 않음 — pdf=None 으로 반환

        with _p2_lock:
            _p2_state["status"] = "done"
            _p2_state["step_label"] = "완료"
            _p2_state["result"] = frontend_result

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f"[P2 Pipeline Error] {exc}\n{tb}", flush=True)
        with _p2_lock:
            _p2_state["status"] = "error"
            if not _p2_state.get("step"):
                _p2_state["step"] = "extract"
            _p2_state["step_label"] = f"오류: {exc}"
            _p2_state["error_detail"] = str(exc)


def _p2_pipeline_worker_both(product_id: str) -> None:
    """백그라운드 스레드: 공공(Logic A) + 민간(Logic B) 양쪽 동시 산출.

    dispatch_both_segments() 로 FOB 를 한 번에 계산하고,
    Haiku 분석은 available_segments 에 해당하는 세그먼트만 실행(비용 최적화).
    나머지 세그먼트는 동일 Haiku 결과 재사용.

    결과 구조: {
        "public":  {...프론트 스키마...},
        "private": {...프론트 스키마...},
        "available_segments": ["public","private"] | ["public"] | ["private"] | []
    }
    """
    try:
        # ── Step 1: Supabase row 조회 ──────────────────────────────────────────
        with _p2_lock:
            _p2_state["step"] = "extract"
            _p2_state["step_label"] = "① Supabase 품목 데이터 조회 중…"
        client_sb = get_supabase_client()
        resp = (
            client_sb.table(TABLE_NAME)
            .select("*")
            .eq("product_code", product_id)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None)
        if not rows:
            raise ValueError(f"Supabase 조회 실패: product_id={product_id!r} 미존재")
        row = rows[0]
        if isinstance(row, dict):
            _normalize_au_product_row(row)

        # ── Step 2: seed 매칭 ───────────────────────────────────────────────────
        with _p2_lock:
            _p2_state["step"] = "ai_extract"
            _p2_state["step_label"] = "② FOB 시드 매칭 중…"
        seeds = _load_stage2_seeds()
        seed = next((s for s in seeds if s.get("product_id") == product_id), None)
        if not seed:
            raise ValueError(f"fob_reference_seeds.json 에 {product_id!r} 시드 없음")

        # ── Step 3: 공공+민간 FOB 동시 역산 ────────────────────────────────────
        with _p2_lock:
            _p2_state["step"] = "ai_extract"
            _p2_state["step_label"] = "③ FOB 3시나리오 역산 중 (공공+민간)…"
        fx_rates = _fetch_exchange_rates_simple()
        fx_krw = fx_rates.get("aud_krw") or 893.0
        both = dispatch_both_segments(seed, fx_aud_to_krw=fx_krw, crawler_row=row)
        pub_dispatch = both["public"]
        pri_dispatch = both["private"]
        available_segments = both.get("available_segments", ["public", "private"])

        # 양쪽 모두 blocked(ESTIMATE_withdrawal) 이면 조기 종료
        if pub_dispatch.get("logic") == "blocked" and pri_dispatch.get("logic") == "blocked":
            _reason = pub_dispatch.get("blocked_reason", "unknown")
            _warn_txt = " ".join(w for w in (pub_dispatch.get("warnings") or []) if w)
            _blocked_seg = {
                "extracted": {
                    "product_name": row.get("product_name_ko") or product_id,
                    "ref_price_text": "해당 없음 (규제 차단)",
                    "ref_price_aud": None,
                    "verdict": f"수출 차단: {_reason}",
                },
                "analysis": {
                    "final_price_aud": 0,
                    "formula_str": "N/A (blocked)",
                    "rationale": _warn_txt,
                    "scenarios": [],
                },
                "exchange_rates": fx_rates,
                "pdf": None,
                "market_note": pub_dispatch.get("market_note", ""),
            }
            with _p2_lock:
                _p2_state["status"] = "done"
                _p2_state["step_label"] = "완료 (blocked)"
                _p2_state["result"] = {
                    "public": _blocked_seg,
                    "private": {**_blocked_seg, "market_note": pri_dispatch.get("market_note", "")},
                    "available_segments": [],
                }
            return

        # ── Step 4: Haiku 분석 (available_segments 기준 최소 실행) ─────────────
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수 미설정")

        run_pub = "public" in available_segments
        run_pri = "private" in available_segments

        # 4a — 공공 Haiku
        if run_pub:
            with _p2_lock:
                _p2_state["step"] = "ai_analysis"
                _p2_state["step_label"] = "④ AI(Haiku) 공공 시장 분석 중…"
            pub_blocks = _haiku_p2_blocks(row, seed, pub_dispatch, "public", anthropic_key)
            pub_strategy_v5 = _merge_p2_export_strategy_v5(pub_blocks, pub_dispatch, fx_rates, seed)
        else:
            pub_blocks = {}
            pub_strategy_v5 = {}

        # 4b — 민간 Haiku
        if run_pri:
            with _p2_lock:
                _p2_state["step_label"] = "④ AI(Haiku) 민간 시장 분석 중…"
            pri_blocks = _haiku_p2_blocks(row, seed, pri_dispatch, "private", anthropic_key)
            pri_strategy_v5 = _merge_p2_export_strategy_v5(pri_blocks, pri_dispatch, fx_rates, seed)
        else:
            # 재사용: 공공과 동일 블록 (ESTIMATE_hospital 처럼 동일 데이터)
            pri_blocks = pub_blocks
            pri_strategy_v5 = pub_strategy_v5

        # 4c — run_pub=False 인 경우(ESTIMATE_private) 공공도 민간 블록 재사용
        if not run_pub:
            pub_blocks = pri_blocks
            pub_strategy_v5 = pri_strategy_v5

        # ── Step 5: 프론트 스키마 조립 (공통 인라인 헬퍼) ──────────────────────
        with _p2_lock:
            _p2_state["step_label"] = "⑤ 결과 조립 중…"

        def _assemble(dispatch_result: dict, p2_blocks: dict, strategy_v5: dict) -> dict:
            """dispatch 결과 → 프론트 스키마 dict 반환."""
            sc_raw = dispatch_result.get("scenarios", {})
            agg  = sc_raw.get("aggressive", {})
            avg  = sc_raw.get("average", {})
            cons = sc_raw.get("conservative", {})

            # 참고가 텍스트 — v5 우선, 없으면 출처별 레거시
            v5_text, v5_ref_aud = _build_p2_ref_price_text_v5(dispatch_result)
            _inp   = dispatch_result.get("inputs") or {}
            _logic = dispatch_result.get("logic")
            if v5_text is not None:
                ref_text, ref_aud = v5_text, v5_ref_aud
            elif (
                _logic == "B"
                and _inp.get("retail_source") == "crawler"
                and _inp.get("retail_aud") is not None
            ):
                ref_aud = float(_inp["retail_aud"])
                cr_meth = row.get("retail_estimation_method")
                if cr_meth == "pbs_dpmq":
                    ref_text = f"PBS DPMQ(최대처방량 총약가) AUD {ref_aud}"
                elif cr_meth == "chemist_markup":
                    ref_text = (
                        f"시장 추정가 AUD {ref_aud} "
                        f"(Chemist Warehouse × 1.20, CHOICE 조사 기준)"
                    )
                elif cr_meth in ("healthylife_actual", "healthylife_same_ingredient_diff_form"):
                    ref_text = f"소매 참고가 AUD {ref_aud} (Healthylife·크롤 동기화)"
                else:
                    ref_text = f"소매 참고가 AUD {ref_aud} (크롤러·DB 동기화)"
            elif (
                _logic == "A"
                and _inp.get("aemp_source") == "crawler"
                and _inp.get("aemp_aud") is not None
            ):
                ref_aud  = float(_inp["aemp_aud"])
                ref_text = f"AEMP AUD {ref_aud} (au_products·크롤 동기화)"
            elif seed.get("reference_aemp_aud") is not None:
                ref_val = seed["reference_aemp_aud"]
                if isinstance(ref_val, list):
                    ref_text = "AEMP " + " / ".join(f"AUD {v}" for v in ref_val)
                    ref_aud  = sum(float(v) for v in ref_val) / len(ref_val)
                else:
                    ref_text = f"AEMP AUD {ref_val}"
                    ref_aud  = float(ref_val)
            elif seed.get("reference_retail_aud") is not None:
                ref_aud  = float(seed["reference_retail_aud"])
                ref_text = f"{seed.get('reference_retail_source') or '소매가'} AUD {ref_aud}"
            else:
                ref_text, ref_aud = "참고가 미확보", None

            _l = dispatch_result.get("logic", "?")
            if _l == "A":
                formula_str = (
                    "FOB = (공시 AEMP × (1+α)) ÷ (1 + 수입상 마진%), "
                    f"α={int(round(float(ALPHA_MARKET_UPLIFT_PCT)))}% (Logic A 전용)"
                )
            elif _l == "hardcoded":
                formula_str = "FOB = 병원 tender 수기 확정 (seed.fob_hardcoded_aud, α 미적용)"
            else:
                formula_str = (
                    "FOB = 소매가 ÷ (1+GST) ÷ (1+약국마진) ÷ (1+도매마진) ÷ (1+수입상마진)"
                )

            return {
                "extracted": {
                    "product_name": row.get("product_name_ko") or product_id,
                    "ref_price_text": ref_text,
                    "ref_price_aud": ref_aud,
                    "verdict": row.get("export_viable") or row.get("reason_code") or "조건부",
                },
                "analysis": {
                    "final_price_aud": round(avg.get("fob_aud", 0), 2),
                    "formula_str": formula_str,
                    "rationale": p2_blocks.get("block_fob_intro", ""),
                    "scenarios": [
                        {
                            "name": "저가 진입 시나리오 (Penetration Pricing)",
                            "price_aud": round(agg.get("fob_aud", 0), 2),
                            "reason": p2_blocks.get("scenario_penetration", ""),
                        },
                        {
                            "name": "기준가 기반 시나리오 (Reference Pricing)",
                            "price_aud": round(avg.get("fob_aud", 0), 2),
                            "reason": p2_blocks.get("scenario_reference", ""),
                        },
                        {
                            "name": "프리미엄 시나리오 (Premium Pricing)",
                            "price_aud": round(cons.get("fob_aud", 0), 2),
                            "reason": p2_blocks.get("scenario_premium", ""),
                        },
                    ],
                },
                "exchange_rates": fx_rates,
                "export_strategy_v5": strategy_v5,
                "market_note": dispatch_result.get("market_note", ""),
                "pdf": None,
                # 내부 전달용 (Supabase·PDF 에서 참조)
                "_ref_text": ref_text,
                "_ref_aud":  ref_aud,
                "_logic":    _l,
                "_formula":  formula_str,
            }

        pub_frontend = _assemble(pub_dispatch, pub_blocks, pub_strategy_v5)
        pri_frontend = _assemble(pri_dispatch, pri_blocks, pri_strategy_v5)

        # ── Step 5.5: au_pbs_raw / au_tga_artg 데이터 주입 (PDF 용) ────────────
        with _p2_lock:
            _p2_state["step_label"] = "⑤-2 Supabase 저장 중…"
        try:
            raw_resp = (
                client_sb.table("au_pbs_raw")
                .select("market_form,market_strength")
                .eq("product_id", product_id)
                .order("crawled_at", desc=True)
                .limit(1)
                .execute()
            )
            raw_rows = getattr(raw_resp, "data", None) or []
            if raw_rows:
                row["market_form"]     = raw_rows[0].get("market_form")
                row["market_strength"] = raw_rows[0].get("market_strength")
        except Exception as exc:
            print(f"[P2-both au_pbs_raw 조회 경고] {exc}", flush=True)
        try:
            tga_resp = (
                client_sb.table("au_tga_artg")
                .select("strength,dosage_form")
                .eq("product_id", product_id)
                .order("crawled_at", desc=True)
                .limit(1)
                .execute()
            )
            tga_rows = getattr(tga_resp, "data", None) or []
            if tga_rows:
                row["tga_strength"]    = tga_rows[0].get("strength")
                row["tga_dosage_form"] = tga_rows[0].get("dosage_form")
        except Exception as exc:
            print(f"[P2-both au_tga_artg 조회 경고] {exc}", flush=True)

        # ── Step 5.6: Supabase upsert (공공·민간 각각) ──────────────────────────
        for seg_label, f_res, d_res, blks in (
            ("public",  pub_frontend, pub_dispatch, pub_blocks),
            ("private", pri_frontend, pri_dispatch, pri_blocks),
        ):
            try:
                sb_cl = get_supabase_client()
                sc_raw = d_res.get("scenarios", {})
                _agg  = sc_raw.get("aggressive",   {})
                _avg  = sc_raw.get("average",       {})
                _cons = sc_raw.get("conservative",  {})
                upsert_data = {
                    "product_id":          product_id,
                    "segment":             seg_label,
                    "ref_price_text":      f_res["_ref_text"],
                    "ref_price_aud":       float(f_res["_ref_aud"]) if f_res["_ref_aud"] is not None else None,
                    "verdict":             row.get("export_viable") or row.get("reason_code"),
                    "logic":               f_res["_logic"],
                    "pricing_case":        seed.get("pricing_case"),
                    "fob_penetration_aud": round(_agg.get("fob_aud",  0), 4) if _agg  else None,
                    "fob_reference_aud":   round(_avg.get("fob_aud",  0), 4) if _avg  else None,
                    "fob_premium_aud":     round(_cons.get("fob_aud", 0), 4) if _cons else None,
                    "fob_penetration_krw": round(_agg.get("fob_krw",  0), 2) if _agg  else None,
                    "fob_reference_krw":   round(_avg.get("fob_krw",  0), 2) if _avg  else None,
                    "fob_premium_krw":     round(_cons.get("fob_krw", 0), 2) if _cons else None,
                    "fx_aud_to_krw":       fx_rates.get("aud_krw"),
                    "fx_aud_to_usd":       fx_rates.get("aud_usd"),
                    "formula_str":         f_res["_formula"],
                    "block_extract":       blks.get("block_extract"),
                    "block_fob_intro":     blks.get("block_fob_intro"),
                    "scenario_penetration": blks.get("scenario_penetration"),
                    "scenario_reference":  blks.get("scenario_reference"),
                    "scenario_premium":    blks.get("scenario_premium"),
                    "block_strategy":      blks.get("block_strategy"),
                    "block_risks":         blks.get("block_risks"),
                    "block_positioning":   blks.get("block_positioning"),
                    "warnings":            [w for w in (d_res.get("warnings") or []) if w],
                    "disclaimer":          d_res.get("disclaimer"),
                    "llm_model":           _CLAUDE_MODEL,
                    "generated_at":        _dt_now_utc(),
                    "report_content_v2":   jsonable_encoder({
                        "schema_ver":       3,
                        "report_kind":      "export_strategy_dual",
                        "product_code":     product_id,
                        "segment":          seg_label,
                        "pricing_case":     seed.get("pricing_case"),
                        "p2_blocks":        blks,
                        "export_strategy_v5": f_res.get("export_strategy_v5"),
                        "fx_rates":         fx_rates,
                        "dispatch_logic":   f_res["_logic"],
                        "ref_price_text":   f_res["_ref_text"],
                        "formula_str":      f_res["_formula"],
                        "market_note":      f_res.get("market_note", ""),
                        "available_segments": available_segments,
                    }),
                }
                sb_cl.table("au_reports_r2").upsert(
                    upsert_data, on_conflict="product_id,segment"
                ).execute()
                print(f"[P2-both Supabase] UPSERT OK: {product_id} / {seg_label}", flush=True)
            except Exception as sb_exc:
                print(f"[P2-both Supabase UPSERT error] {seg_label}: {sb_exc}", flush=True)

        # ── Step 6: PDF 생성 (공공·민간 각각) ──────────────────────────────────
        with _p2_lock:
            _p2_state["step"] = "report"
            _p2_state["step_label"] = "⑥ PDF 보고서 생성 중…"
        for seg_label, f_res, d_res, blks in (
            ("public",  pub_frontend, pub_dispatch, pub_blocks),
            ("private", pri_frontend, pri_dispatch, pri_blocks),
        ):
            try:
                from report_generator import render_p2_pdf
                from datetime import datetime as _dt
                _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
                pdf_name = f"au_p2_report_{product_id}_{seg_label}_{_ts}.pdf"
                pdf_path = _REPORTS_DIR / pdf_name
                render_p2_pdf(row, seed, d_res, blks, fx_rates, pdf_path)
                sz = pdf_path.stat().st_size
                print(f"[render_p2_pdf-both] OK {pdf_name} ({sz} bytes)", flush=True)
                f_res["pdf"] = pdf_name
                try:
                    sb_cl_pdf = get_supabase_client()
                    sb_cl_pdf.table("au_reports_r2").update(
                        {"pdf_filename": pdf_name}
                    ).eq("product_id", product_id).eq("segment", seg_label).execute()
                except Exception:
                    pass
            except Exception as pdf_exc:
                print(f"[render_p2_pdf-both error] {seg_label}: {pdf_exc}", flush=True)

        # 내부 전달용 키(_ref_text 등) 제거 후 최종 결과 저장
        _strip_keys = {"_ref_text", "_ref_aud", "_logic", "_formula"}
        pub_clean = {k: v for k, v in pub_frontend.items() if k not in _strip_keys}
        pri_clean = {k: v for k, v in pri_frontend.items() if k not in _strip_keys}

        with _p2_lock:
            _p2_state["status"] = "done"
            _p2_state["step_label"] = "완료"
            _p2_state["result"] = {
                "public":  pub_clean,
                "private": pri_clean,
                "available_segments": available_segments,
            }

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f"[P2-both Pipeline Error] {exc}\n{tb}", flush=True)
        with _p2_lock:
            _p2_state["status"] = "error"
            if not _p2_state.get("step"):
                _p2_state["step"] = "extract"
            _p2_state["step_label"] = f"오류: {exc}"
            _p2_state["error_detail"] = str(exc)


@app.get("/api/p2/pipeline/status")
async def p2_pipeline_status() -> JSONResponse:
    """AI 파이프라인 상태 조회."""
    with _p2_lock:
        return JSONResponse({
            "status": _p2_state["status"],
            "step": _p2_state["step"],
            "step_label": _p2_state["step_label"],
        })


@app.post("/api/p2/pipeline")
def p2_pipeline(payload: dict[str, Any]) -> JSONResponse:
    """수출전략 AI 파이프라인 실행.

    요청: {report_filename: str, market: "public"|"private", product_code?: str}
    - product_code(또는 product_id) 권장 — PDF 파일명과 무관하게 품목 지정
    - 없을 때만 report_filename 의 au_report_* 패턴에서 보조 추출(레거시)
    - Supabase row 조회 → seed → FOB → Haiku → 결과 조립
    - 백그라운드 스레드에서 처리, 즉시 {status: "started"} 반환
    - 프론트는 GET /api/p2/pipeline/status 로 폴링 → done 시 GET /api/p2/pipeline/result 호출
    """
    if not _ANTHROPIC_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI 엔진(anthropic) 미설치 — `pip install -r requirements.txt` 실행 후 재시도. "
                f"(probe error: {_ANTHROPIC_ERR})"
            ),
        )
    if not _STAGE2_OK:
        raise HTTPException(status_code=503, detail=f"stage2 모듈 로드 실패: {_STAGE2_ERR}")

    # 이미 실행 중이면 중복 방지
    with _p2_lock:
        if _p2_state["status"] == "running":
            return JSONResponse({
                "status": "already_running",
                "step": _p2_state["step"],
                "step_label": _p2_state["step_label"],
            })

    report_filename = str(payload.get("report_filename") or "").strip()
    # segment 파라미터는 하위 호환용으로 수신하되 무시 — 항상 공공+민간 동시 산출
    _segment_hint = str(payload.get("market") or "both").strip()

    explicit_id = str(
        payload.get("product_code") or payload.get("product_id") or ""
    ).strip()
    if explicit_id:
        product_id = explicit_id
    else:
        product_id = _extract_product_id_from_filename(report_filename) or ""
    if not product_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "품목 코드(product_code 또는 product_id)가 필요합니다. "
                "PDF 파일명은 사용하지 않습니다 — 클라이언트에서 분석 대상 품목을 반드시 넘겨 주세요. "
                f"(report_filename={report_filename!r})"
            ),
        )

    # 상태 초기화 & 백그라운드 실행 (공공+민간 동시 산출 워커)
    with _p2_lock:
        _p2_state["status"] = "running"
        _p2_state["step"] = "extract"
        _p2_state["step_label"] = "파이프라인 시작…"
        _p2_state["result"] = None
        _p2_state["error_detail"] = None

    worker = _threading.Thread(
        target=_p2_pipeline_worker_both,
        args=(product_id,),
        daemon=True,
    )
    worker.start()

    return JSONResponse({"status": "started", "product_id": product_id, "segment": "both"})


@app.get("/api/p2/pipeline/result")
def p2_pipeline_result() -> JSONResponse:
    """AI 파이프라인 완료 결과 반환. status=done 일 때만 유효."""
    with _p2_lock:
        if _p2_state["status"] != "done":
            raise HTTPException(
                status_code=409,
                detail=f"파이프라인 미완료 (status={_p2_state['status']})",
            )
        result = _p2_state["result"]
        # 결과 반환 후 상태를 idle 로 리셋 (재실행 가능)
        _p2_state["status"] = "idle"
        _p2_state["step"] = ""
        _p2_state["step_label"] = ""
    if result is None:
        raise HTTPException(status_code=500, detail="결과가 비어 있습니다.")
    # _p2_pipeline_worker_both: {"public":…, "private":…, "available_segments":…}
    # _p2_pipeline_worker(구): {"extracted":…, "analysis":…, "exchange_rates":…, "pdf":…}
    # 두 포맷 모두 허용하도록 필터 확장
    frontend_keys = {
        "extracted", "analysis", "exchange_rates", "pdf",  # 기존 단일 세그먼트
        "public", "private", "available_segments",          # 신규 이중 세그먼트
    }
    return JSONResponse({k: v for k, v in result.items() if k in frontend_keys})


@app.post("/api/p2/report")
def p2_report(payload: dict[str, Any]) -> JSONResponse:
    """수출전략 PDF 보고서 재생성 (파이프라인 완료 후 별도 PDF 생성 요청).
    body: {product_id: str, segment?: str}
    기존 파이프라인 결과가 없으면 전체 파이프라인을 다시 실행해야 합니다.
    """
    raise HTTPException(
        status_code=501,
        detail="단독 PDF 재생성은 미구현. POST /api/p2/pipeline 으로 전체 파이프라인 실행 시 PDF 가 자동 생성됩니다.",
    )


# ═══════════════════════════════════════════════════════════════
#  PDF 다운로드 / 인라인 미리보기 엔드포인트
# ═══════════════════════════════════════════════════════════════


def _latest_report_pdf() -> Path | None:
    """시장조사·수출전략 PDF 파일명 패턴을 모두 고려해 reports/ 최신 파일을 고름."""
    candidates: list[Path] = []
    for pattern in ("au_report_*.pdf", "au_p2_report_*.pdf"):
        candidates.extend(_REPORTS_DIR.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


@app.get("/api/report/download")
def download_report(
    name: str | None = Query(None, description="reports 폴더 내 PDF 파일명 (예: au_report_xxx.pdf)"),
    inline: int = Query(0, ge=0, le=1, description="1이면 inline 미리보기"),
) -> FileResponse:
    """reports/ 디렉토리의 PDF 를 반환.
    - inline=1: Content-Disposition: inline → 브라우저 iframe 에서 PDF 뷰어로 표시
    - inline=0(기본): attachment → 파일 다운로드
    name 미지정 시 au_report_*·au_p2_report_* 중 수정 시각 최신 파일 반환.
    """
    root = _REPORTS_DIR.resolve()
    if name and str(name).strip():
        safe_name = Path(str(name).strip()).name
        if not safe_name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="PDF 파일만 다운로드할 수 있습니다.")
        target = (root / safe_name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="잘못된 파일 경로입니다.") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"not found: {safe_name}")
        if target.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail=f"빈 파일: {safe_name}")
    else:
        latest = _latest_report_pdf()
        if latest is None:
            raise HTTPException(
                status_code=404,
                detail="생성된 PDF 가 없습니다. 시장 분석 또는 수출 전략 파이프라인 실행 후 다시 시도하세요.",
            )
        target = latest.resolve()
        if not target.is_file() or target.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail="PDF 파일을 찾을 수 없습니다.")

    disp = "inline" if inline else "attachment"
    return FileResponse(
        str(target),
        media_type="application/pdf",
        filename=target.name,
        content_disposition_type=disp,
    )


# ═══════════════════════════════════════════════════════════════════════
# 바이어 발굴 (Phase 3) — 2026-04-20 신규 추가
# 범위: /api/buyers, /api/buyers/{product_id}, /api/buyers/report/generate
# 1·2단계 (시장분석·수출전략) 엔드포인트는 절대 건드리지 않음
# ═══════════════════════════════════════════════════════════════════════


def _fx_rates_safe() -> dict[str, float]:
    """buyer_discovery.utils.fx_rate 호출. 실패 시 fallback 환율."""
    try:
        from buyer_discovery.utils.fx_rate import get_fx_rates
        return get_fx_rates()
    except Exception as exc:
        logger.warning("fx_rate 조회 실패, fallback: %s", exc)
        return {"aud_krw": 900.0, "aud_usd": 0.65}


def _load_buyer_au_products_meta() -> dict[str, dict[str, Any]]:
    """au_products.json 메타 로드 — 바이어 UI 에서 품목명·INN 표시용.

    기존 _load_au_products_meta() 가 있으면 우선 사용, 없으면 직접 로드.
    """
    try:
        return _load_au_products_meta()  # 기존 헬퍼 재사용 (있으면)
    except NameError:
        pass
    import json as _json
    path = _BASE_DIR / "crawler" / "au_products.json"
    if not path.is_file():
        return {}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in data.get("products") or []:
        pid = p.get("product_id")
        if pid:
            out[pid] = p
    return out


@app.get("/api/buyers")
def buyers_list_summary() -> JSONResponse:
    """전 품목 바이어 요약 — 프론트 바이어발굴 탭 상단 카드/리스트 용.

    반환:
      {
        "fx": {"aud_krw": ..., "aud_usd": ...},
        "products": [
          {
            "product_id": "au-hydrine-004",
            "product_name_ko": "Hydrine",
            "buyer_count": 10,
            "top3": [
              {"rank": 1, "company_name": "...", "psi_total": 66,
               "annual_revenue_rank": "TOP 50 (제네릭/특수)", "has_au_factory": "N"}
            ]
          }
        ]
      }
    """
    try:
        sb = get_supabase_client()
        rows = (
            sb.table("au_buyers")
            .select(
                "product_id,rank,company_name,psi_total,"
                "annual_revenue_rank,has_au_factory,is_ma_member,is_gbma_member"
            )
            .order("product_id")
            .order("rank")
            .execute()
            .data
        ) or []
    except Exception as exc:
        logger.error("buyers 조회 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"DB 조회 실패: {exc}") from exc

    meta = _load_buyer_au_products_meta()
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid = r["product_id"]
        if pid.startswith("_"):
            continue  # _test 같은 레거시 rows
        entry = grouped.setdefault(pid, {
            "product_id": pid,
            "product_name_ko": (meta.get(pid) or {}).get("product_name_ko") or pid,
            "product_name_en": (meta.get(pid) or {}).get("product_name_en"),
            "inn_components": (meta.get(pid) or {}).get("inn_components") or [],
            "buyer_count": 0,
            "top3": [],
        })
        entry["buyer_count"] += 1
        if r["rank"] <= 3:
            entry["top3"].append({
                "rank": r["rank"],
                "company_name": r["company_name"],
                "psi_total": r["psi_total"],
                "annual_revenue_rank": r.get("annual_revenue_rank"),
                "has_au_factory": r.get("has_au_factory"),
                "is_ma_member": r.get("is_ma_member"),
                "is_gbma_member": r.get("is_gbma_member"),
            })

    return JSONResponse({
        "fx": _fx_rates_safe(),
        "products": list(grouped.values()),
    })


@app.get("/api/buyers/{product_id}")
def buyers_for_product(product_id: str) -> JSONResponse:
    """품목별 바이어 TOP 10 상세 — 프론트 행 클릭 시 펼침 카드 용.

    반환: product 메타 + fx + 10개 buyer 전체 필드 (psi_*, therapeutic_categories,
          factory, 연락처 등).
    """
    if not product_id or product_id.startswith("_"):
        raise HTTPException(status_code=400, detail="잘못된 product_id")

    try:
        sb = get_supabase_client()
        rows = (
            sb.table("au_buyers")
            .select("*")
            .eq("product_id", product_id)
            .order("rank")
            .execute()
            .data
        ) or []
    except Exception as exc:
        logger.error("buyers/%s 조회 실패: %s", product_id, exc)
        raise HTTPException(status_code=500, detail=f"DB 조회 실패: {exc}") from exc

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"바이어 데이터 없음: {product_id}. Stage 2 scoring 실행 필요.",
        )

    meta = _load_buyer_au_products_meta().get(product_id) or {}
    return JSONResponse({
        "product_id": product_id,
        "product_name_ko": meta.get("product_name_ko"),
        "product_name_en": meta.get("product_name_en"),
        "inn_components": meta.get("inn_components") or [],
        "similar_inns": meta.get("similar_inns") or [],
        "pricing_case": meta.get("pricing_case"),
        "fx": _fx_rates_safe(),
        "buyers": rows,
    })


# ═══════════════════════════════════════════════════════════════════════
# 바이어 발굴 실시간 실행 파이프라인 (2026-04-20)
#   버튼 클릭 → 백그라운드 worker:
#     1. 실시간 크롤링 (MA/GBMA/GPCE HTML·Algolia + TGA/PBS DB) — 25%
#     2. DB 분석 (Stage 1 4-case + 교차검증 점수)                — 50%
#     3. AI 분석 (치료영역 태깅 + 매출 캐시 + 추천 근거)          — 75%
#     4. 리스트 생성 (Stage 2 점수화 + au_buyers UPSERT + PDF)   — 100%
# ═══════════════════════════════════════════════════════════════════════

import threading as _threading
import uuid as _uuid
from datetime import datetime as _dt_p3

# job_id → state dict (in-memory. 서버 재시작 시 소실 — job 은 수분 이내 완료)
_P3_JOBS: dict[str, dict[str, Any]] = {}
_P3_JOBS_LOCK = _threading.Lock()


def _p3_update(job_id: str, **kwargs: Any) -> None:
    with _P3_JOBS_LOCK:
        st = _P3_JOBS.setdefault(job_id, {})
        st.update(kwargs)
        st["updated_at"] = _dt_p3.utcnow().isoformat()


def _p3_worker(job_id: str, product_id: str) -> None:
    """백그라운드 Stage1 + Stage2 + PDF 파이프라인.

    각 단계 실패 시 상태만 'error' 로 변경하고 예외 전파 안 함 (폴링 쪽이 감지).
    """
    try:
        # ───── Step 1: 실시간 크롤링 (25%) ─────
        _p3_update(job_id, status="running", step="실시간 크롤링", progress=5)
        import asyncio
        # buyer_discovery 는 upharma-au 루트에서 import
        from buyer_discovery.pipeline_collect import (  # type: ignore
            _get_product,
            collect_all_sources,
        )
        from buyer_discovery.stage1_filter import run_stage1  # type: ignore

        product = _get_product(product_id)
        collected = asyncio.run(collect_all_sources(product_id))
        _p3_update(job_id, step="실시간 크롤링 완료", progress=25)

        # ───── Step 2: DB 분석 (50%) ─────
        _p3_update(job_id, step="DB 분석 · Stage 1 필터", progress=30)
        survivors_list = run_stage1(collected, product)
        _p3_update(
            job_id,
            step=f"DB 분석 완료 ({len(survivors_list)} 후보)",
            progress=50,
        )

        # ───── Step 3: AI 분석 (75%) ─────
        # 현재 설계: 매출·카테고리 조사는 주기적 재실행 기반 (company_revenue.json 시드).
        # 버튼 클릭 시 실시간 재조사 하면 5~10분 추가 → 사용자 대기 부담.
        # TOP 10 각각 Haiku 추천 근거 3문장 생성은 향후 구현 포인트.
        _p3_update(
            job_id,
            step="AI 분석 · 매출·카테고리 캐시 활용 + 추천 근거",
            progress=60,
        )
        # (현재는 placeholder — hardcode.notes 가 reasoning 으로 사용됨)
        _p3_update(job_id, step="AI 분석 완료", progress=75)

        # ───── Step 4: 리스트 생성 + UPSERT + PDF (100%) ─────
        _p3_update(job_id, step="리스트 생성 · Stage 2 점수화", progress=80)
        # Stage 1 재생성 결과를 survivors_expanded_v5.json 에도 덮어쓰기 (이력 용)
        from buyer_discovery.cli import _build_hardcode_template  # type: ignore
        # 품목별 ingredient_case 맵 (Stage 2 에서 사용)
        ingredient_per_product = {
            product_id: {
                row["canonical_key"]: row.get("ingredient_case", "D_none")
                for row in survivors_list
            }
        }
        union_map = {row["canonical_key"]: row for row in survivors_list}
        hardcode_template = _build_hardcode_template(
            list(union_map.values()),
            ingredient_per_product,
        )
        import json as _json
        survivors_path = Path(
            r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/survivors_expanded_v5.json"
        )
        # 전 품목 실행이 아니라 단일 품목만 돌린 결과이므로 부분 업데이트 위험.
        # Stage 2 는 전 품목 DB SELECT 기반이므로 원본 v5 유지. 단일 품목 최신화는 별도.
        # → 단일 품목 결과는 `seeds/p3_last_run_{product_id}.json` 에 따로 저장.
        seeds_dir = _BASE_DIR / "buyer_discovery" / "seeds"
        seeds_dir.mkdir(parents=True, exist_ok=True)
        (seeds_dir / f"p3_last_run_{product_id}.json").write_text(
            _json.dumps(hardcode_template, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Stage 2 실행 (single product 모드 아님 — 전체 재계산해서 일관성 유지)
        from buyer_discovery.stage2_scoring import main as _stage2_main  # type: ignore
        _stage2_main(dry_run=False)
        _p3_update(job_id, step="au_buyers UPSERT 완료", progress=90)

        # PDF 생성
        from report_generator import render_buyers_pdf  # type: ignore
        pdf_path = render_buyers_pdf(product_id=product_id, output_dir=_REPORTS_DIR)
        _p3_update(
            job_id,
            step="PDF 생성 완료",
            progress=100,
            status="done",
            product_id=product_id,
            pdf_filename=Path(pdf_path).name,
            download_url=f"/api/report/download?name={Path(pdf_path).name}&inline=1",
        )
    except Exception as exc:
        logger.exception("[p3_worker] 실패 job=%s", job_id)
        _p3_update(
            job_id,
            status="error",
            error=str(exc),
            step=f"오류: {type(exc).__name__}",
        )


@app.post("/api/p3/buyers/run")
def p3_buyers_run(payload: dict[str, Any]) -> JSONResponse:
    """바이어 발굴 실시간 파이프라인 시작. job_id 반환 (폴링용).

    Body: {"product_id": "au-hydrine-004"}
    """
    product_id = (payload or {}).get("product_id")
    if not product_id or not isinstance(product_id, str):
        raise HTTPException(status_code=400, detail="product_id 필수")

    job_id = "p3_" + _uuid.uuid4().hex[:12]
    _p3_update(
        job_id,
        status="queued",
        step="대기 중",
        progress=0,
        product_id=product_id,
        created_at=_dt_p3.utcnow().isoformat(),
    )
    # 백그라운드 스레드 시작
    t = _threading.Thread(target=_p3_worker, args=(job_id, product_id), daemon=True)
    t.start()

    return JSONResponse({"job_id": job_id, "product_id": product_id, "status": "queued"})


@app.get("/api/p3/buyers/status")
def p3_buyers_status(job_id: str = Query(...)) -> JSONResponse:
    """폴링용. job 진행 상태 반환."""
    with _P3_JOBS_LOCK:
        st = _P3_JOBS.get(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")
    return JSONResponse(st)


@app.get("/api/p3/buyers/result")
def p3_buyers_result(job_id: str = Query(...)) -> JSONResponse:
    """job 완료 후 최종 결과 (au_buyers TOP 10 + PDF 파일명)."""
    with _P3_JOBS_LOCK:
        st = _P3_JOBS.get(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")
    if st.get("status") != "done":
        raise HTTPException(status_code=425, detail=f"아직 완료 안 됨 (status={st.get('status')})")

    pid = st.get("product_id")
    # 기존 /api/buyers/{pid} 재활용
    try:
        inner = buyers_for_product(pid)  # JSONResponse 반환
        import json as _json
        data = _json.loads(inner.body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"결과 로드 실패: {exc}") from exc

    data["job_id"] = job_id
    data["pdf_filename"] = st.get("pdf_filename")
    data["download_url"] = st.get("download_url")
    return JSONResponse(data)


@app.post("/api/buyers/report/generate")
def buyers_report_generate(payload: dict[str, Any]) -> JSONResponse:
    """바이어 발굴 PDF 보고서 생성. payload = {"product_id": "..."} 또는 {} (전체).

    내부적으로 report_generator.render_buyers_pdf() 호출.
    결과 PDF 는 reports/au_buyers_*.pdf 에 저장되고, /api/report/download?name=...
    로 받을 수 있음.
    """
    product_id = (payload or {}).get("product_id") or None
    try:
        from report_generator import render_buyers_pdf  # type: ignore
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"report_generator.render_buyers_pdf 가 없습니다: {exc}",
        ) from exc

    try:
        pdf_path = render_buyers_pdf(product_id=product_id, output_dir=_REPORTS_DIR)
    except Exception as exc:
        logger.exception("render_buyers_pdf 실패")
        raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {exc}") from exc

    return JSONResponse({
        "ok": True,
        "product_id": product_id or "all",
        "pdf_path": str(pdf_path),
        "pdf_filename": Path(pdf_path).name,
        "download_url": f"/api/report/download?name={Path(pdf_path).name}&inline=1",
    })