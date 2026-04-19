# Render 서빙용 FastAPI 어댑터 — crawler/ 내부 코드를 import만 해서 재사용한다.
# 이 파일이 브라우저 ↔ 크롤러 ↔ Supabase 를 잇는 유일한 연결 지점.

from __future__ import annotations

import logging
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
from fastapi import FastAPI, HTTPException
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
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("aemp_aud,retail_price_aud")
            .eq("product_code", product_code)
            .order("last_crawled_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows:
            aemp_aud = rows[0].get("aemp_aud")
            retail_price_aud = rows[0].get("retail_price_aud")
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
    if not pdf_file or not pdf_file.filename:
        raise HTTPException(status_code=400, detail="pdf_file 필수")
    if not (pdf_file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 허용됩니다.")
    try:
        pdf_bytes = pdf_file.file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF 읽기 실패: {exc}")

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


# ── 외부 데이터 어댑터 (Supabase 저장 없음) ─────────────────────────

def _news_api_response(
    items: list[dict[str, Any]],
    *,
    ok: bool = True,
    error: str | None = None,
    source: str = "mock",
) -> JSONResponse:
    """프론트(loadNews)와 동일한 계약: { ok, items, error } — DB 저장 없음.
    source: mock | perplexity (진단용 헤더 X-News-Source)
    """
    resp = JSONResponse(content={"ok": ok, "items": items, "error": error})
    resp.headers["X-News-Source"] = source
    return resp


# 메인 프리뷰 뉴스 카드에 표시할 기사 개수(프롬프트·파싱·mock 보충과 동일)
_NEWS_LIST_SIZE = 5


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


_MOCK_NEWS: list[dict[str, Any]] = [
    {
        "title": "TGA approves fast-track for PIC/S generics",
        "title_ko": "TGA, PIC/S 제네릭 우선 심사 확대",
        "summary_ko": "PIC/S 동등성 제네릭에 대한 심사·허가 절차 강화 등 규제 동향을 다룬 샘플 요약입니다.",
        "source": "TGA.gov.au",
        "date": "2026-04-18",
        "link": "https://www.tga.gov.au",
    },
    {
        "title": "Australia pharma imports from Korea up 11%",
        "title_ko": "한국산 의약품 수입 증가",
        "summary_ko": "교역 통계와 수입 품목·정책 맥락을 짧게 정리한 샘플 요약입니다.",
        "source": "Austrade",
        "date": "2026-04-17",
        "link": "https://www.austrade.gov.au",
    },
    {
        "title": "PBS listing reforms: what exporters need to know",
        "title_ko": "PBS 등재 개편과 수출사 관점",
        "summary_ko": "등재·급여 변화가 수출·가격 전략에 주는 시사점을 담은 샘플 요약입니다.",
        "source": "Dept. of Health",
        "date": "2026-04-16",
        "link": "https://www.pbs.gov.au",
    },
    {
        "title": "KAFTA and Korea–Australia pharma trade",
        "title_ko": "한·호주 의약품 교역",
        "summary_ko": "무역협정·관세·규제 환경을 개괄한 샘플 요약입니다.",
        "source": "KITA",
        "date": "2026-04-15",
        "link": "https://www.kita.net",
    },
    {
        "title": "NPS MedicineWise updates consumer medicines information",
        "title_ko": "NPS, 일반의약품 정보 개정",
        "summary_ko": "소비자 대상 복약·안내 정보 갱신을 다룬 샘플 요약입니다.",
        "source": "NPS MedicineWise",
        "date": "2026-04-14",
        "link": "https://www.nps.org.au",
    },
]

_FX_FALLBACK: dict[str, Any] = {"aud_krw": 893.0, "aud_usd": 0.6412, "updated": ""}


@app.get("/api/news")
def get_news() -> JSONResponse:
    """Perplexity sonar: 호주 제약 뉴스 검색 + 한국어 제목·요약 + 기사 직링크. 키 없거나 실패 시 mock."""
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
                "model": "sonar",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a news aggregator for a Korean pharmaceutical export dashboard. "
                            f"Return EXACTLY {_NEWS_LIST_SIZE} recent news items as a JSON array ONLY (no markdown, no prose). "
                            "Each item MUST have these keys: "
                            "\"title\" (English headline as published), "
                            "\"title_ko\" (Korean, concise headline for UI), "
                            "\"summary_ko\" (Korean, 1–2 sentences: what the article is about for a business reader), "
                            "\"source\" (outlet or site name), "
                            "\"date\" (YYYY-MM-DD), "
                            "\"link\" (string). "
                            "CRITICAL: \"link\" MUST be the DIRECT URL of the specific article page where the full text can be read — "
                            "NOT a site homepage. "
                            "All Korean text must be natural and professional."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Find exactly {_NEWS_LIST_SIZE} online TEXT articles (not video pages) published within the LAST 24 HOURS. "
                            f"If {_NEWS_LIST_SIZE} are not available in 24h, you may include the most recent from YESTERDAY only — "
                            "do NOT use anything older than the previous calendar day. "
                            "Topics: Australia pharmaceutical industry, TGA, PBS, healthcare policy, public health, hospital/pharmacy. "
                            "Prefer: Australian outlets and .gov.au media releases, major newspapers' article URLs, "
                            "global pharma news sites (e.g. industry trade press). "
                            "For each item give title, title_ko, summary_ko, source, date (publication date), and the DIRECT article URL. "
                            "If you cannot find a direct article URL, skip and substitute another article."
                        ),
                    },
                ],
                "return_citations": True,
            },
            timeout=45.0,
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

    import json as _json
    content = ""
    try:
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception as exc:
        logger.warning("[api/news] mock: 응답 choices 파싱 실패: %s", exc)
        return _news_api_response(mock_items, source="mock")

    if not content.strip():
        logger.warning("[api/news] mock: message content 비어 있음")
        return _news_api_response(mock_items, source="mock")

    citations = data.get("citations") or []
    link_list = [c if isinstance(c, str) else (c.get("url") if isinstance(c, dict) else "") for c in citations]

    try:
        start = content.index("[")
        end = content.rindex("]") + 1
        items = _json.loads(content[start:end])
    except Exception as exc:
        logger.warning(
            "[api/news] mock: JSON 배열 파싱 실패 (%s) content_prefix=%s",
            exc,
            (content or "")[:400],
        )
        return _news_api_response(mock_items, source="mock")

    result: list[dict[str, Any]] = []
    for i, it in enumerate(items[:_NEWS_LIST_SIZE]):
        if not isinstance(it, dict):
            continue
        link_fb = ""
        if i < len(link_list):
            link_fb = link_list[i] or ""
        merged = dict(it)
        if not (merged.get("link") or merged.get("url")) and link_fb:
            merged["link"] = link_fb
        result.append(_normalize_news_item(merged))

    if not result:
        logger.warning("[api/news] mock: 파싱 후 유효 항목 0건")
        return _news_api_response(mock_items, source="mock")
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
    "당신은 한국유나이티드제약(주)의 호주 수출 전문 애널리스트임. "
    "주어진 품목의 실제 크롤링 데이터(TGA·PBS·Chemist·NSW)만을 근거로 "
    "아래 10개 필드를 한국어 보고서체로 작성함.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "Block 2 — 수출 적합성 판정 근거 (5축):\n"
    "  block2_market      : 시장/의료 현황 분석 (호주 시장에서 해당 품목의 위치, 경쟁 구도)\n"
    "  block2_regulatory  : TGA/ARTG 규제 분석 (등재번호·스케줄·라이선스 카테고리)\n"
    "  block2_trade       : KAFTA 관세/무역 분석 (HS 코드별 관세율, 원산지증명)\n"
    "  block2_procurement : PBS/NSW 조달 경로 분석 (급여·DPMQ·공공조달 경로)\n"
    "  block2_channel     : 유통 채널 분석 (스폰서·브랜드·도매 구조)\n\n"
    "Block 3 — 시장 진출 전략 (4축):\n"
    "  block3_channel     : 진입 채널 전략 (PBS vs 민간 vs 병원 입찰)\n"
    "  block3_pricing     : 가격 포지셔닝 전략 (PBS DPMQ 기준 FOB 역산 고려)\n"
    "  block3_partners    : 파트너 발굴 전략 (현지 스폰서·유통사 섭외 방향)\n"
    "  block3_risks       : 리스크 및 선결 조건 (TGA 등재 일정·GMP·환율·경쟁)\n\n"
    "Block 4 — 규제 체크포인트 (5개 법령, 이 품목 실무 영향):\n"
    "  block4_regulatory : 반드시 아래 번호 형식으로 작성 (프론트 파싱용):\n"
    "    ① TGA Act 1989: [이 품목 영향 1~2문장]\n"
    "    ② GMP PIC/S: [이 품목 영향 1~2문장]\n"
    "    ③ PBS National Health Act 1953: [이 품목 영향 1~2문장]\n"
    "    ④ KAFTA: [이 품목 영향 1~2문장]\n"
    "    ⑤ Customs Regulations: [이 품목 영향 1~2문장]\n"
    "    (①~⑤ 사이 줄바꿈만. 다른 서식·마크다운 금지.)\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【어투 규칙 — 절대 준수】\n"
    "- 보고서 문체: 종결어미 '~함', '~임', '~됨', '~가능함', '~필요함'만 사용.\n"
    "- 금지 종결어미: '~입니다', '~합니다', '~있습니다', '~해요', '~이에요' 일체 금지.\n"
    "- 마크다운 금지: **굵게**, *기울임*, # 제목, - 리스트, `코드`, [링크]() 전부 X.\n"
    "- 이모지·특수 기호 장식 금지.\n\n"
    "【환각 방지 규칙 — 최우선】\n"
    "- 제공된 JSON 데이터에 없는 숫자·날짜·법령 조항·통계는 **절대 창작 금지**.\n"
    "- 모르는 사실은 '제공 데이터 범위 외이므로 별도 검증 필요함' 으로 명시.\n"
    "- 일반 지식을 쓸 때는 연도·출처 기관을 구체적으로 언급하지 말 것 (예: 'WHO 2023 통계' X).\n"
    "- 제공 데이터의 값이 null 인 필드는 언급하지 말거나 '데이터 미수집'으로 명시.\n\n"
    "【품질 규칙】\n"
    "1. Block 2·3 각 필드: 3~5 문장, 각 문장 40~100자.\n"
    "2. Block 4 각 법령: 1~2 문장.\n"
    "3. 각 필드에 **제공 데이터의 실제 값 최소 2개** 구체 인용 "
    "(ARTG 번호, PBS item code, DPMQ, 소매가, 스폰서명 등).\n"
    "4. '생성 예정', 'TBD', '추후 분석', '데이터 부족' 같은 플레이스홀더 문구 금지.\n"
    "5. 모든 10개 필드를 반드시 채워서 반환.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【Few-shot 좋은 예시】\n"
    "block2_regulatory (입력: Hydrine, artg_number=313760, tga_sponsor=Medsurge Pharma, pbs_innovator=Y):\n"
    '  "Hydrine은 ARTG 313760으로 Registered 상태이며, 스폰서 Medsurge Pharma Pty Ltd가 호주 '
    "내 판매 대행을 수행함. PBS innovator 지위(Y)를 보유하여 참고 의약품 지정 대상에 해당함. "
    "PIC/S 회원국 한국의 제조시설은 TGA 실사 면제 협의가 가능하여 규제 진입 장벽이 상대적으로 낮음. "
    '정식 ARTG 등재를 확보한 상태이므로 병렬 수입 가능성 없이 스폰서 경로로만 진입 가능함."\n\n'
    "【Few-shot 나쁜 예시 — 금지】\n"
    '  "TGA 규제가 적용됩니다. 등재가 필요해요. 추가 검토가 필요합니다."  '
    "→ 구체 수치 없음, 보고서체 위반, 빈약함. 절대 이렇게 작성하지 말 것."
)


def _claude_blocks_schema():
    """Pydantic 모델을 지연 로드(임포트 부담 줄이기)."""
    from pydantic import BaseModel, Field

    class ReportBlocks(BaseModel):
        block2_market: str = Field(description="시장·의료 관점 분석 (보고서체 ~함/~임)")
        block2_regulatory: str = Field(description="규제 관점 분석 (보고서체 ~함/~임)")
        block2_trade: str = Field(description="무역 관점 분석 (보고서체 ~함/~임)")
        block2_procurement: str = Field(description="조달 관점 분석 (보고서체 ~함/~임)")
        block2_channel: str = Field(description="유통 관점 분석 (보고서체 ~함/~임)")
        block3_channel: str = Field(description="진입 채널 전략 (보고서체 ~함/~임)")
        block3_pricing: str = Field(description="가격 포지셔닝 전략 (보고서체 ~함/~임)")
        block3_partners: str = Field(description="파트너·스폰서 전략 (보고서체 ~함/~임)")
        block3_risks: str = Field(description="리스크·선결 조건 (보고서체 ~함/~임)")
        block4_regulatory: str = Field(
            description=(
                "5개 법령의 이 품목 영향. 반드시 다음 번호 형식으로 작성: "
                "'① TGA Act 1989: ...\\n② GMP PIC/S: ...\\n③ PBS National Health Act 1953: ...\\n"
                "④ KAFTA: ...\\n⑤ Customs Regulations: ...' — 보고서체 ~함/~임, 각 법령 1~2문장."
            )
        )

    return ReportBlocks


def _row_summary_for_llm(row: dict[str, Any]) -> dict[str, Any]:
    """LLM 프롬프트에 넣을 21개 지정 컬럼 + product_name_ko 를 추려서 반환."""
    keys = [
        "product_name_ko",                                              # 품목 식별용
        "artg_status", "artg_number", "tga_schedule", "tga_sponsor",    # TGA (4)
        "pbs_listed", "pbs_item_code", "pbs_price_aud", "pbs_dpmq",
        "pbs_patient_charge", "pbs_brand_name", "pbs_innovator",
        "pbs_formulary",                                                # PBS (8)
        "retail_price_aud", "price_source_name",                        # Chemist (2)
        "export_viable", "reason_code", "nsw_note",                     # 판정/NSW (3)
        "inn_normalized", "dosage_form", "strength", "hs_code_6",       # 품목 메타 (4)
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


def _try_parse_blocks_from_assistant_text(text: str, schema_cls: Any) -> dict[str, str] | None:
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
) -> dict[str, str]:
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


def _claude_generate_blocks(row: dict[str, Any], api_key: str) -> dict[str, str]:
    """Anthropic Claude Haiku 4.5 호출. tool_use 로 10 필드 파싱.
    크롤링 row 의 수치/필드를 읽어 한국어 보고서체 블록을 생성한다."""
    import anthropic
    import json as _json

    ReportBlocks = _claude_blocks_schema()
    client_anthropic = anthropic.Anthropic(api_key=api_key)

    # Decimal 등 비JSON 타입 직렬화 (수출전략 Haiku 경로와 동일)
    user_content = (
        "다음 품목의 크롤링 데이터를 해석하여 10개 블록을 보고서체(~함/~임)로 작성하라.\n"
        "실제 숫자/문자열 값(ARTG 번호, DPMQ, PBS item code, 스폰서명 등)을 본문에 반드시 인용.\n\n"
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
        tool_name="emit_report_blocks",
        tool_description="10개 보고서 블록을 구조화해서 반환",
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
    "【필드 정의】\n"
    "  block_extract        : 1문단(3~5문장). 제품명·참고가(AEMP 또는 retail)·TGA/PBS 판정 요약.\n"
    "  block_fob_intro      : 1문단(3~5문장). 왜 본 품목이 Logic A(AEMP) 또는 Logic B(Private) 로 역산되는지, "
    "                         3시나리오 가격대(AUD 범위)를 한 줄로 언급.\n"
    "  scenario_penetration : '저가 진입 시나리오 (Penetration Pricing)' 근거 1~2문장.\n"
    "    — 경쟁사보다 낮은 가격으로 수출, 초기 시장점유율 빠르게 확대 후 필요 시 가격 인상. (수입상 마진 10% 전제.)\n"
    "  scenario_reference   : '기준가 기반 시나리오 (Reference Pricing)' 근거 1~2문장.\n"
    "    — 타깃 시장의 경쟁사 가격이나 시장 평균 가격을 준거로 자사 FOB 설정, 협상 기반 마련. (마진 20% 전제.)\n"
    "  scenario_premium     : '프리미엄 시나리오 (Premium Pricing)' 근거 1~2문장.\n"
    "    — 제품 혁신성·고품질 강조, 경쟁제품보다 높은 가격 책정으로 고마진 추구, 고객층 차별화. (마진 30% 전제.)\n"
    "  block_strategy       : 1문단(3~5문장). 권장 진입 채널(PBS/Private/Hospital)·파트너 발굴·타이밍.\n"
    "  block_risks          : 1문단(3~5문장). 규제(TGA/PBAC/Section 19A 등)·환율·경쟁 리스크.\n"
    "  block_positioning    : 1문단(3~5문장). 경쟁사 브랜드(seed.competitor_brands_on_pbs) 대비 포지셔닝.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【어투 규칙 — 절대 준수】\n"
    "- 보고서 문체: 종결어미 '~함', '~임', '~됨', '~가능함', '~필요함'만 사용.\n"
    "- 금지 종결어미: '~입니다', '~합니다', '~있습니다', '~해요', '~이에요' 일체 금지.\n"
    "- 마크다운 금지: **굵게**, *기울임*, # 제목, - 리스트, `코드`, [링크]() 전부 X.\n"
    "- 이모지·특수 기호 장식 금지.\n\n"
    "【환각 방지 규칙 — 최우선】\n"
    "- 제공된 JSON(row/seed/dispatch) 에 없는 숫자·법령·브랜드는 **창작 금지**.\n"
    "- dispatch.scenarios 의 fob_aud 숫자는 scenario_* 필드 본문에 **반드시 소수점 2자리로 인용**.\n"
    "- pricing_case / pbs_section / pbs_status / flags 값을 논리 전개에 활용하되 값 자체를 정확히 읽음.\n"
    "- 모르는 사실은 '제공 데이터 범위 외로 별도 검증 필요함' 으로 명시.\n\n"
    "【품질 규칙】\n"
    "1. block_extract · block_fob_intro · block_strategy · block_risks · block_positioning 각 3~5문장 (문장 40~100자).\n"
    "2. scenario_* 3개는 각각 1~2문장 (60~140자), 반드시 fob_aud 값 인용.\n"
    "3. segment='public'(PBS/공공) 이면 PBS AEMP/DPMQ 기반 논리, 'private' 이면 소매가 역산·비급여 채널 논리로 각각 다르게 작성.\n"
    "4. 'TBD', '추후', '데이터 부족' 같은 플레이스홀더 금지.\n"
    "5. 8개 필드 모두 반드시 채움.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【Few-shot 좋은 예시 — scenario_penetration】\n"
    "입력: fob_aud=26.50, product=Hydrine, pricing_case=DIRECT, pbs_section=85\n"
    '  "Hydrine FOB AUD 26.50(수입상 마진 10% 전제)로 PBS General Schedule 내 제네릭 경쟁 가격대 하단에 '
    "진입, 초기 처방 점유율 확보를 우선함. 제조원가 대비 마진은 제한적이나 채널 확보 후 물량 확대 가능함.\"\n\n"
    "【Few-shot 나쁜 예시 — 금지】\n"
    '  "저가로 진입하면 좋습니다. 마진이 낮아요." '
    "→ FOB 숫자 미인용, 보고서체 위반, 근거 빈약. 절대 이렇게 작성 금지."
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
        "수출 전략 제안서용 8개 블록을 보고서체(~함/~임) 로 작성하라.\n"
        "scenario_* 3개는 dispatch.scenarios 안의 fob_aud 값을 반드시 소수점 2자리로 인용.\n"
        f"segment={segment!r} 기준으로 공공/민간 채널 프레이밍 구분.\n\n"
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
        tool_description="수출 전략 제안서용 8개 블록을 구조화해서 반환",
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


def _semantic_scholar_top1(query: str, fields_of_study: str) -> dict[str, Any] | None:
    """Semantic Scholar /paper/search — 학술 논문 타입만 + 2015년 이후 + 인용수 정렬 후 Top 1."""
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
                "limit": 5,
                "fields": "title,abstract,tldr,year,authors,venue,citationCount,openAccessPdf,url,externalIds",
            },
            timeout=25.0,
        )
        if r.status_code != 200:
            return None
        data = (r.json() or {}).get("data") or []
    except Exception:
        return None
    if not data:
        return None

    # 인용수 내림차순 정렬 후 Top 1
    data.sort(key=lambda p: (p.get("citationCount") or 0), reverse=True)
    top = data[0]
    tldr_text = None
    tldr = top.get("tldr")
    if isinstance(tldr, dict):
        tldr_text = tldr.get("text")

    oa = top.get("openAccessPdf") or {}
    url = (oa.get("url") if isinstance(oa, dict) else None) or top.get("url") or ""

    return {
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


def _pubmed_top1(query: str) -> dict[str, Any] | None:
    """PubMed E-utilities: esearch 로 PMID 획득 → efetch 로 제목·초록 파싱."""
    try:
        # 1) 검색 → PMID 리스트 (JSON)
        r_search = httpx.get(
            f"{_PUBMED_BASE}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": query,
                "retmax": 3,
                "retmode": "json",
                "datetype": "pdat",
                "mindate": "2015",
                "sort": "relevance",
            },
            timeout=20.0,
        )
        if r_search.status_code != 200:
            return None
        pmid_list = (r_search.json().get("esearchresult") or {}).get("idlist") or []
        if not pmid_list:
            return None
        pmid = pmid_list[0]

        # 2) 초록 + 메타 가져오기 (XML)
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

        # 초록: AbstractText 여러 개 붙이기
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


def _fetch_refs_hybrid(row: dict[str, Any], perplexity_key: str) -> list[dict[str, Any]]:
    """3카테고리 × [Semantic Scholar → PubMed → Perplexity] 순 폴백."""
    inn = row.get("inn_normalized") or row.get("product_name_ko") or "pharmaceutical"
    refs: list[dict[str, Any]] = []

    for cat in _HYBRID_CATEGORIES:
        cat_label = cat["label"]
        cat_id = cat["id"]

        # 1차: Semantic Scholar
        top = _semantic_scholar_top1(f"{cat['ss_query']} {inn}", cat["ss_fos"])

        # 2차: PubMed
        if not top:
            pm_query = f"{cat['pubmed_query']} AND {inn}[All Fields]"
            top = _pubmed_top1(pm_query)

        # 3차: Perplexity 폴백
        if not top and perplexity_key:
            pplx_query = (
                f"Find peer-reviewed academic papers and journal articles "
                f"(PubMed, Google Scholar, academic journals only — NO news, YouTube, retail) "
                f"about {cat['ss_query']} for {inn}"
            )
            pplx = _perplexity_top1(pplx_query, perplexity_key)
            if pplx:
                top = {
                    "url": pplx.get("url"),
                    "title": pplx.get("title"),
                    "abstract": None,
                    "tldr": pplx.get("snippet"),  # Perplexity의 answer 본문 발췌
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

    # 5) Supabase UPDATE — 공통 6컬럼 제외
    from datetime import datetime, timezone
    generated_at = datetime.now(timezone.utc).isoformat()
    update_data: dict[str, Any] = {
        **blocks,
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
    except Exception as exc:
        # PDF 실패는 치명적이지 않음 — 응답은 내보내되 pdf_name 은 None
        print(f"[render_pdf error] {exc}", flush=True)
        pdf_name = None

    # Decimal/datetime 등이 섞여 있으면 JSONResponse 직렬화에서 TypeError 가능 — jsonable_encoder 사용
    return JSONResponse(
        content=jsonable_encoder(
            {
                "ok": True,
                "product_id": product_id,
                "llm_model": _CLAUDE_MODEL,
                "llm_generated_at": generated_at,
                "blocks": blocks,
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
        DEFAULT_FX_AUD_TO_KRW,
        calculate_fob_logic_a,
        calculate_fob_logic_b,
        calculate_three_scenarios,
        dispatch_by_pricing_case,
        get_disclaimer_text,
    )
    _STAGE2_OK = True
    _STAGE2_ERR = ""
except Exception as _stage2_err:  # noqa: BLE001
    _STAGE2_OK = False
    _STAGE2_ERR = str(_stage2_err)


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
            "retail_aud": sc.get("retail_aud"),
            "pre_gst_aud": sc.get("pre_gst_aud"),
            "pre_pharmacy_aud": sc.get("pre_pharmacy_aud"),
            "pre_wholesale_aud": sc.get("pre_wholesale_aud"),
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
                "복합제/신규 등재 품목: PBAC(호주 의약품급여자문위원회) 임상우월성 입증 필요 (등재 지연·거절 리스크)."
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
                "warnings": warnings,
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

        gst_pct = float(overrides.get("gst") or 10.0)
        pharmacy_pct = float(overrides.get("pharmacy_margin") or 30.0)
        wholesale_pct = float(overrides.get("wholesale_margin") or 10.0)
        margin_default = float(overrides.get("importer_margin") or 20.0)
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
            logic_b_kwargs={
                "gst_pct": gst_pct,
                "pharmacy_margin_pct": pharmacy_pct,
                "wholesale_margin_pct": wholesale_pct,
            },
        )
        return JSONResponse(content={
            "ok": True,
            "logic": "B",
            "scenarios": _scenarios_dict_to_list(scenarios),
            "inputs": {
                "product_id": product_id,
                "retail_aud": retail,
                "gst_pct": gst_pct,
                "pharmacy_margin_pct": pharmacy_pct,
                "wholesale_margin_pct": wholesale_pct,
                "importer_margin_pct_center": margin_default,
                "fx_aud_to_krw": fx,
                "presets_pct": presets,
            },
            "warnings": warnings,
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

    저장 위치: reports/_p2_uploads/{timestamp}_{safe_name}.pdf
    다음 단계에서 /api/p2/pipeline 이 이 파일을 읽어 Haiku로 분석.
    """
    import base64
    import re
    import time

    raw_name = str(payload.get("filename") or "").strip()
    content_b64 = str(payload.get("content_b64") or "")
    if not raw_name or not content_b64:
        raise HTTPException(status_code=400, detail="filename 과 content_b64 필수")
    if not raw_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드 가능")

    try:
        content = base64.b64decode(content_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"base64 디코딩 실패: {e}") from e

    # 파일명 안전화 (경로 구분자·특수문자 제거)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name)
    # 너무 길면 잘라내기
    if len(safe_name) > 100:
        safe_name = safe_name[-100:]
    ts = int(time.time())
    stored_name = f"{ts}_{safe_name}"
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
    """업로드 접두사 포함 파일명에서도 product_id 를 안전 추출.

    지원 예시:
    - au_report_au-hydrine-004_20260416_120000.pdf
    - 1776390947_au_report_au-hydrine-004_20260417_101321.pdf
    """
    name = Path(filename).name
    m = _re.search(r"(?:\d+_)?au_report_(.+?)_\d{8}_\d{6}\.pdf$", name)
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

        # ── Step 5: 결과 조립 (프론트 기대 스키마) ──
        with _p2_lock:
            _p2_state["step"] = "ai_analysis"
            _p2_state["step_label"] = "⑤ 결과 조립 중…"

        scenarios_raw = dispatch_result.get("scenarios", {})
        agg = scenarios_raw.get("aggressive", {})
        avg = scenarios_raw.get("average", {})
        cons = scenarios_raw.get("conservative", {})

        # 참고가 텍스트 조립 — 우선순위: seed AEMP → seed retail → crawler retail → 미확보
        if seed.get("reference_aemp_aud") is not None:
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
        elif dispatch_result.get("inputs", {}).get("retail_source") == "crawler":
            # Logic B 에서 seed.reference_retail_aud 미확보 → crawler_row 참고가 사용
            ref_aud = float(dispatch_result["inputs"]["retail_aud"])
            cr_method = row.get("retail_estimation_method")
            if cr_method == "pbs_dpmq":
                ref_text = f"PBS DPMQ(최대처방량 총약가) AUD {ref_aud}"
            elif cr_method == "chemist_markup":
                ref_text = (
                    f"시장 추정가 AUD {ref_aud} "
                    f"(Chemist Warehouse × 1.20, CHOICE 조사 기준)"
                )
            else:
                ref_text = f"시장 추정가 AUD {ref_aud} (크롤러 실시간)"
        else:
            ref_text = "참고가 미확보"
            ref_aud = None

        # Logic A 공식 vs Logic B 공식
        logic = dispatch_result.get("logic", "?")
        if logic == "A":
            formula_str = "FOB = AEMP ÷ (1 + 수입상 마진%)"
        else:
            formula_str = "FOB = 소매가 ÷ (1+GST) ÷ (1+약국마진) ÷ (1+도매마진) ÷ (1+수입상마진)"

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
        # Phase 4.3-v3 — au_pbs_raw 에서 market_form/market_strength 주입 (render_pdf 와 동일)
        try:
            raw_resp = (
                sb_client.table("au_pbs_raw")
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
                sb_client.table("au_tga_artg")
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

    요청: {report_filename: str, market: "public"|"private"}
    - report_filename 에서 product_id 추출 → Supabase row 조회 → seed → FOB → Haiku → 결과 조립
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
    segment = str(payload.get("market") or "public").strip()
    if segment not in ("public", "private"):
        segment = "public"

    # 파일명에서 product_id 추출
    product_id = _extract_product_id_from_filename(report_filename)
    if not product_id:
        raise HTTPException(
            status_code=400,
            detail=f"report_filename 에서 product_id 추출 불가: {report_filename!r}. "
                   "형식: au_report_{{product_id}}_YYYYMMDD_HHMMSS.pdf",
        )

    # 상태 초기화 & 백그라운드 실행
    with _p2_lock:
        _p2_state["status"] = "running"
        _p2_state["step"] = "extract"
        _p2_state["step_label"] = "파이프라인 시작…"
        _p2_state["result"] = None
        _p2_state["error_detail"] = None

    worker = _threading.Thread(
        target=_p2_pipeline_worker,
        args=(product_id, segment),
        daemon=True,
    )
    worker.start()

    return JSONResponse({"status": "started", "product_id": product_id, "segment": segment})


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
    # _p2_blocks / _dispatch / _seed 는 내부용이므로 프론트에는 제외
    frontend_keys = {"extracted", "analysis", "exchange_rates", "pdf"}
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
def download_report(name: str | None = None, inline: int = 0) -> FileResponse:
    """reports/ 디렉토리의 PDF 를 반환.
    - inline=1: Content-Disposition: inline → 브라우저 iframe 에서 PDF 뷰어로 표시
    - inline=0(기본): attachment → 파일 다운로드
    name 미지정 시 au_report_*·au_p2_report_* 중 수정 시각 최신 파일 반환.
    """
    if name:
        target = _REPORTS_DIR / Path(name).name
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"not found: {name}")
    else:
        latest = _latest_report_pdf()
        if latest is None:
            raise HTTPException(
                status_code=404,
                detail="생성된 PDF 가 없습니다. 시장 분석 또는 수출 전략 파이프라인 실행 후 다시 시도하세요.",
            )
        target = latest

    disp = "inline" if inline else "attachment"
    return FileResponse(
        str(target),
        media_type="application/pdf",
        filename=target.name,
        content_disposition_type=disp,
    )