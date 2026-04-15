# Render 서빙용 FastAPI 어댑터 — crawler/ 내부 코드를 import만 해서 재사용한다.
# 이 파일이 브라우저 ↔ 크롤러 ↔ Supabase 를 잇는 유일한 연결 지점.

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
    # Starlette 최신 API: (request, name) 순서. 구 API 의 ("name", {"request": request}) 는 TypeError.
    return templates.TemplateResponse(request, "index.html")


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


# ── 외부 데이터 어댑터 (Supabase 저장 없음) ─────────────────────────

_MOCK_NEWS: list[dict[str, Any]] = [
    {"title": "TGA approves fast-track for PIC/S generics",
     "source": "TGA.gov.au", "date": "2025-07-14",
     "link": "https://www.tga.gov.au"},
    {"title": "Australia pharma imports from Korea up 11%",
     "source": "Austrade", "date": "2025-07-13",
     "link": "https://www.austrade.gov.au"},
    {"title": "PBS listing reforms: what exporters need to know",
     "source": "Dept. of Health", "date": "2025-07-12",
     "link": "https://www.pbs.gov.au"},
    {"title": "KAFTA 10주년 — 한-호주 의약품 교역 현황",
     "source": "KITA", "date": "2025-07-10",
     "link": "https://www.kita.net"},
]

_FX_FALLBACK: dict[str, Any] = {"aud_krw": 893.0, "aud_usd": 0.6412, "updated": ""}


@app.get("/api/news")
def get_news() -> JSONResponse:
    """SerpAPI google_news 로 호주 의약품 뉴스 4건. 키 없거나 실패 시 mock."""
    api_key = os.environ.get("SERPAPI_KEY", "").strip()
    if not api_key:
        return JSONResponse(content=_MOCK_NEWS)
    try:
        r = httpx.get(
            "https://serpapi.com/search",
            params={
                "engine": "google_news",
                "q": "Australia TGA PBS pharmaceutical",
                "gl": "au",
                "hl": "en",
                "num": 4,
                "api_key": api_key,
            },
            timeout=12.0,
        )
        if r.status_code != 200:
            return JSONResponse(content=_MOCK_NEWS)
        payload = r.json()
    except Exception:
        return JSONResponse(content=_MOCK_NEWS)

    results = payload.get("news_results") or []
    items: list[dict[str, Any]] = []
    for it in results[:4]:
        if not isinstance(it, dict):
            continue
        src = it.get("source")
        src_name = src.get("name") if isinstance(src, dict) else src
        items.append({
            "title": it.get("title"),
            "source": src_name,
            "date": it.get("date"),
            "link": it.get("link"),
        })
    return JSONResponse(content=items if items else _MOCK_NEWS)


@app.get("/api/exchange")
def get_exchange() -> JSONResponse:
    """exchangerate-api.com 무료 엔드포인트로 AUD 기준 환율 조회. 실패 시 fallback."""
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
    if krw is None or usd is None:
        return JSONResponse(content=_FX_FALLBACK)
    return JSONResponse(content={
        "aud_krw": float(krw),
        "aud_usd": float(usd),
        "updated": data.get("date") or "",
    })


# ── LLM 보고서 생성 ─────────────────────────────────────────────────
# Claude Haiku 4.5 가 보고서 두뇌. 크롤링된 Supabase row 의 수치를 한국어로 해석.
# 프롬프트는 보고서체(~함/~임) + 마크다운 금지 + 실제 수치 인용 강제 + block4 번호 형식까지 명시.

_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

_CLAUDE_SYSTEM_PROMPT = (
    "너는 한국 제약회사의 호주 수출 전문 애널리스트임. "
    "주어진 품목의 실제 크롤링 데이터(TGA·PBS·Chemist·NSW)를 기반으로 "
    "아래 10개 필드를 한국어 보고서체로 작성함.\n\n"
    "Block 2 — 수출 적합성 판정 근거 (5축):\n"
    "  block2_market      : 시장/의료 현황 분석\n"
    "  block2_regulatory  : TGA/ARTG 규제 분석 (등재번호·스케줄·면허 카테고리)\n"
    "  block2_trade       : KAFTA 관세/무역 분석 (HS 코드별 관세율)\n"
    "  block2_procurement : PBS/NSW 조달 경로 분석 (급여·DPMQ·공공조달)\n"
    "  block2_channel     : 유통 채널 분석 (스폰서·브랜드 구조)\n\n"
    "Block 3 — 시장 진출 전략 (4축):\n"
    "  block3_channel     : 진입 채널 전략\n"
    "  block3_pricing     : 가격 포지셔닝 전략 (PBS DPMQ 기준 FOB 역산 고려)\n"
    "  block3_partners    : 파트너 발굴 전략 (스폰서·유통사 섭외)\n"
    "  block3_risks       : 리스크 및 선결 조건 (TGA 등재 일정·GMP·환율·경쟁)\n\n"
    "Block 4 — 규제 체크포인트 (5개 법령, 이 품목 수출 시 실무적 영향):\n"
    "  block4_regulatory : 아래 5개 법령이 해당 품목 수출 시 실무적으로 어떤 영향을 주는지\n"
    "    각 법령당 1~2문장으로 작성. 실제 데이터 수치(ARTG 번호·스케줄·PBS 상태·DPMQ 등) 반드시 인용.\n"
    "    반드시 아래 번호 형식으로 정확히 작성 (프론트 파싱용):\n"
    "    ① TGA Act 1989: [이 품목 영향]\n"
    "    ② GMP PIC/S: [이 품목 영향]\n"
    "    ③ PBS National Health Act 1953: [이 품목 영향]\n"
    "    ④ KAFTA: [이 품목 영향]\n"
    "    ⑤ Customs Regulations: [이 품목 영향]\n"
    "    (①~⑤ 사이에 줄바꿈만. 다른 서식·마크다운 금지.)\n\n"
    "⚠️ 어투 규칙 (절대 준수):\n"
    "- 보고서 문체: 종결어미 '~함', '~임', '~됨', '~가능함', '~필요함' 사용.\n"
    "  예) '~입니다', '~합니다', '~있습니다' 금지.\n"
    "- 마크다운 일체 금지: **굵게**, *기울임*, # 제목, - 리스트, `코드`, [링크]() 전부 X.\n"
    "- 이모지 금지.\n\n"
    "⚠️ 품질 규칙:\n"
    "1. Block 2·3 각 필드는 3~5문장, Block 4는 법령당 1~2문장.\n"
    "2. 실제 데이터의 숫자/값을 반드시 인용: ARTG 번호, PBS item code, DPMQ, 소매가, 스폰서명.\n"
    "3. 데이터에 없는 내용은 업계 일반 지식 기반으로 보수적으로만 언급 (단정 금지).\n"
    "4. '생성 예정', 'TBD', '추후 분석', '데이터 부족' 같은 플레이스홀더 문구 금지.\n"
    "5. 모든 10개 필드를 반드시 채워서 반환."
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


def _claude_generate_blocks(row: dict[str, Any], api_key: str) -> dict[str, str]:
    """Anthropic Claude Haiku 4.5 호출. Pydantic structured output 으로 10 필드 파싱.
    크롤링 row 의 수치/필드를 읽어 한국어 보고서체 블록을 생성한다."""
    import anthropic
    import json as _json

    ReportBlocks = _claude_blocks_schema()
    client_anthropic = anthropic.Anthropic(api_key=api_key)

    user_content = (
        "다음 품목의 크롤링 데이터를 해석하여 10개 블록을 보고서체(~함/~임)로 작성하라.\n"
        "실제 숫자/문자열 값(ARTG 번호, DPMQ, PBS item code, 스폰서명 등)을 본문에 반드시 인용.\n\n"
        "```json\n"
        + _json.dumps(_row_summary_for_llm(row), ensure_ascii=False, indent=2)
        + "\n```"
    )

    response = client_anthropic.messages.parse(
        model=_CLAUDE_MODEL,
        max_tokens=4096,
        system=_CLAUDE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        output_format=ReportBlocks,
    )

    # 비용 모니터링용 — 실제 토큰 사용량을 서버 로그에 찍는다
    try:
        usage = response.usage
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        # Haiku 4.5 단가: 입력 $1/1M, 출력 $5/1M (2026 기준)
        est_cost = in_tok * 1e-6 + out_tok * 5e-6
        print(
            f"[Claude Haiku] input={in_tok} output={out_tok} "
            f"est_cost=${est_cost:.5f} (product={row.get('product_id')})",
            flush=True,
        )
    except Exception:
        pass

    parsed = response.parsed_output
    if parsed is None:
        stop_reason = getattr(response, "stop_reason", "unknown")
        raise HTTPException(
            status_code=502,
            detail=f"Claude 응답 파싱 실패 (stop_reason={stop_reason})",
        )
    return parsed.model_dump()


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
    """product_id 의 boundary 데이터를 읽어 LLM 으로 Block2/3 + Perplexity refs 를 생성하고
    australia 테이블에 UPDATE 한다. 공통 6컬럼(id, product_id, market_segment,
    fob_estimated_usd, confidence, crawled_at) 은 건드리지 않는다."""
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id is required")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
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
            .eq("product_id", product_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"supabase error: {exc}")
    if not rows:
        raise HTTPException(status_code=404, detail=f"not found: {product_id}")
    row = rows[0]

    # 2) Claude Haiku 4.5 호출 — 크롤링 row 해석 → 10개 블록 생성
    blocks = _claude_generate_blocks(row, anthropic_key)

    # 3) Perplexity 호출 — 거시/규제/가격 3개 카테고리당 각 1개씩, 총 3개 공신력 URL
    refs: list[dict[str, Any]] = []
    if perplexity_key:
        refs = _perplexity_fetch_refs(row, perplexity_key)

    # 4) Supabase UPDATE — 공통 6컬럼 제외
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
            "product_id", product_id
        ).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"supabase update failed: {exc}",
        )

    # 5) 신뢰도 재계산 (원래 아는 정보 제외, 7개 크롤링 필드만)
    conf_meta = _compute_confidence_breakdown(row)

    # 6) 프론트 메타바 렌더용 — DOM 스크래핑 폐기 대체
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

    return JSONResponse(content={
        "ok": True,
        "product_id": product_id,
        "llm_model": _CLAUDE_MODEL,
        "llm_generated_at": generated_at,
        "blocks": blocks,
        "refs_count": len(refs),
        "refs": refs,
        "meta": meta,
    })
