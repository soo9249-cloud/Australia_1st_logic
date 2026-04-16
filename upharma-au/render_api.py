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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse

# 보고서 PDF 저장 디렉토리
_REPORTS_DIR = _BASE_DIR / "reports"
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# crawler 내부 코드 (수정하지 않고 import 만)
from au_crawler import main as run_crawler  # type: ignore
from db.supabase_insert import TABLE_NAME, get_supabase_client  # type: ignore


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
    # styles.css / app.js 중 최신 mtime → 정적 자원 캐시 무효화 키
    paths = [_BASE_DIR / "static" / "styles.css", _BASE_DIR / "static" / "app.js"]
    try:
        return str(int(max(p.stat().st_mtime for p in paths if p.is_file())))
    except ValueError:
        return "0"


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
    return {
        "status": "ok",
        "optional_deps": deps,
        "optional_deps_all_installed": all_optional_ok,
        "stage2_ok": _STAGE2_OK if "_STAGE2_OK" in globals() else None,
        "hint": None if all_optional_ok else "pip install -r upharma-au/requirements.txt",
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
    """Perplexity sonar 로 호주 제약·규제·건강 뉴스 4건. 키 없거나 실패 시 mock."""
    api_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(content=_MOCK_NEWS)
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
                            "You are a news aggregator. Return EXACTLY 6 recent news items "
                            "as a JSON array. Each item must have: "
                            "{\"title\": string, \"source\": string, \"date\": string (YYYY-MM-DD), \"link\": string}. "
                            "CRITICAL: The 'link' field MUST be the DIRECT URL to the specific article page "
                            "where the user can READ that article — NOT the homepage or main site URL. "
                            "Example: 'https://www.pharmainfocus.com.au/news/article-slug-123' NOT 'https://www.pharmainfocus.com.au'. "
                            "No markdown, no explanation, ONLY the JSON array."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Find the 6 most recent news articles from the LAST 24 HOURS about: "
                            "Australia pharmaceutical industry, TGA regulations, PBS policy, "
                            "healthcare legislation, public health trends, disease outbreaks. "
                            "For EACH item you MUST provide the DIRECT URL to that specific article page "
                            "(the full URL path where the article text can be read, NOT the site homepage). "
                            "If you cannot find the direct article URL, skip that article and find another one. "
                            "Prioritize official government sources and major news outlets."
                        ),
                    },
                ],
                "return_citations": True,
            },
            timeout=20.0,
        )
        if r.status_code != 200:
            return JSONResponse(content=_MOCK_NEWS)
        data = r.json()
    except Exception:
        return JSONResponse(content=_MOCK_NEWS)

    import json as _json
    content = ""
    try:
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception:
        return JSONResponse(content=_MOCK_NEWS)

    citations = data.get("citations") or []
    link_list = [c if isinstance(c, str) else (c.get("url") if isinstance(c, dict) else "") for c in citations]

    try:
        start = content.index("[")
        end = content.rindex("]") + 1
        items = _json.loads(content[start:end])
    except Exception:
        return JSONResponse(content=_MOCK_NEWS)

    result: list[dict[str, Any]] = []
    for i, it in enumerate(items[:6]):
        if not isinstance(it, dict):
            continue
        link = it.get("link") or it.get("url") or ""
        if not link and i < len(link_list):
            link = link_list[i]
        result.append({
            "title": it.get("title", ""),
            "source": it.get("source", ""),
            "date": it.get("date", ""),
            "link": link,
        })
    return JSONResponse(content=result if result else _MOCK_NEWS)


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
    """product_id 의 boundary 데이터를 읽어 LLM 으로 Block2/3 + Perplexity refs 를 생성하고
    australia 테이블에 UPDATE 한다. 공통 6컬럼(id, product_id, market_segment,
    fob_estimated_usd, confidence, crawled_at) 은 건드리지 않는다."""
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id is required")

    # Dep 방어: anthropic 미설치 시 503 으로 명확히 알려주기 (500 ModuleNotFoundError 대신)
    if not _ANTHROPIC_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI 엔진(anthropic) 미설치 — `pip install -r upharma-au/requirements.txt` 실행 후 재시도. "
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
            "product_id", product_id
        ).execute()
    except Exception as exc:
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

    # 8) PDF 보고서 생성 (reportlab) — 서버 디스크 reports/ 에 저장
    pdf_name: str | None = None
    try:
        from report_generator import render_pdf
        from datetime import datetime as _dt
        _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        pdf_name = f"au_report_{product_id}_{_ts}.pdf"
        pdf_path = _REPORTS_DIR / pdf_name
        render_pdf(row, blocks, refs, meta, pdf_path)
    except Exception as exc:
        # PDF 실패는 치명적이지 않음 — 응답은 내보내되 pdf_name 은 None
        print(f"[render_pdf error] {exc}", flush=True)
        pdf_name = None

    return JSONResponse(content={
        "ok": True,
        "product_id": product_id,
        "llm_model": _CLAUDE_MODEL,
        "llm_generated_at": generated_at,
        "blocks": blocks,
        "refs_count": len(refs),
        "refs": refs,
        "meta": meta,
        "pdf": pdf_name,
    })


# ============================================================================
# §P2. 2공정 FOB 역산 API (Stage2)
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
            # 사용자가 입력한 단일 margin 을 average 로 고정하되,
            # 공격(-10)/보수(+10) 범위 제한은 seed 의 typical band 를 따른다
            presets = {
                "aggressive":   max(0.0, margin_default - 10.0),
                "average":      margin_default,
                "conservative": margin_default + 10.0,
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
            "aggressive":   max(0.0, margin_default - 5.0),
            "average":      margin_default,
            "conservative": margin_default + 10.0,
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
#  2공정 AI 파이프라인 스텁 엔드포인트
#  (AI 엔진(Haiku) 연동은 다음 단계에서 구현. 현재는 업로드만 동작.)
# ═══════════════════════════════════════════════════════════════

# 2공정 업로드 PDF 저장 디렉토리
_P2_UPLOADS_DIR = _BASE_DIR / "reports" / "_p2_uploads"
_P2_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/p2/upload")
async def p2_upload_pdf(payload: dict[str, Any]) -> JSONResponse:
    """2공정 AI/직접입력 탭에서 사용자가 직접 올린 PDF를 저장.

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


@app.get("/api/p2/pipeline/status")
async def p2_pipeline_status() -> JSONResponse:
    """AI 파이프라인 상태 조회. 현재는 대기 상태만 반환 (엔진 미구현)."""
    return JSONResponse({
        "status": "idle",
        "step_label": "AI 엔진(Haiku) 연결 대기 중 — fob_calculator 연동 예정",
    })


@app.post("/api/p2/pipeline")
def p2_pipeline_stub(payload: dict[str, Any]) -> JSONResponse:
    """AI 파이프라인 실행 — Haiku 엔진 연동 전. 업로드된 PDF → Haiku 추출 → fob_calculator 실행 예정."""
    # Haiku 엔진 실구현 시 이 체크가 맨 위로 이동됨. 지금은 스텁이지만 미리 dep 상태를 노출.
    if not _ANTHROPIC_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI 엔진(anthropic) 미설치 — `pip install -r upharma-au/requirements.txt` 실행 후 재시도. "
                f"(probe error: {_ANTHROPIC_ERR})"
            ),
        )
    raise HTTPException(status_code=501, detail="AI 파이프라인 실행은 준비 중입니다. (Haiku 엔진 연동 후 가용)")


@app.get("/api/p2/pipeline/result")
def p2_pipeline_result_stub() -> JSONResponse:
    raise HTTPException(status_code=501, detail="AI 파이프라인 결과는 준비 중입니다.")


@app.post("/api/p2/report")
def p2_report_stub(payload: dict[str, Any]) -> JSONResponse:
    raise HTTPException(status_code=501, detail="PDF 생성은 준비 중입니다.")


# ═══════════════════════════════════════════════════════════════
#  PDF 다운로드 / 인라인 미리보기 엔드포인트
# ═══════════════════════════════════════════════════════════════


@app.get("/api/report/download")
def download_report(name: str | None = None, inline: int = 0) -> FileResponse:
    """reports/ 디렉토리의 PDF 를 반환.
    - inline=1: Content-Disposition: inline → 브라우저 iframe 에서 PDF 뷰어로 표시
    - inline=0(기본): attachment → 파일 다운로드
    name 미지정 시 가장 최신 파일 반환.
    """
    if name:
        target = _REPORTS_DIR / Path(name).name
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"not found: {name}")
    else:
        pdfs = sorted(_REPORTS_DIR.glob("au_report_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not pdfs:
            raise HTTPException(status_code=404, detail="생성된 PDF 가 없습니다. POST /api/report/generate 먼저 실행")
        target = pdfs[0]

    disp = "inline" if inline else "attachment"
    return FileResponse(
        str(target),
        media_type="application/pdf",
        filename=target.name,
        content_disposition_type=disp,
    )
