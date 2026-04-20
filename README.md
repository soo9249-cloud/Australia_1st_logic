# UPharma Export AI · Australia

호주 수출 시장조사용 **크롤링 → Supabase 저장 → FOB 역산·보고서** 파이프라인입니다. 웹 UI는 **FastAPI + Jinja + Vanilla JS**(`upharma-au/templates`, `upharma-au/static`)이며 Next.js는 사용하지 않습니다.

**에이전트/개발 규칙·용어·구조 상세:** 저장소 루트 [`CLAUDE.md`](./CLAUDE.md)

> **문서 정리 예정:** 운영·개발 안내를 `README.md`와 `CLAUDE.md`에 나눠 두었는데, 이후 **한 번에 읽는 통합 README**로 재구성할 계획입니다. 당시에는 중복·충돌 문단을 정리하고, 본 파일은 “실행·배포·업로드 지침”, `CLAUDE.md`는 “규칙·톤·스키마·에이전트”에 가깝게 유지하는 방향을 검토합니다.

---

## 동작 원칙 (요약)

- `POST /api/crawl` 은 **항상 외부 소스를 다시 조회**해 DB를 갱신합니다. “이미 있으면 스킵” 캐시는 두지 않습니다.
- Anthropic 호출은 **`claude-haiku-4-5-20251001` 고정** (Sonnet/Opus 사용 금지).
- 크롤·스키마·API 세부 필드·FOB 역산 수식은 **코드와 `CLAUDE.md`** 를 기준으로 합니다.

---

## 폴더 요약

```
Australia_1st_logic/
├── .env                    # 로컬 비밀 (git 제외)
├── requirements.txt        # 의존성 단일 소스 (루트)
├── render.yaml             # Render Blueprint
├── scripts/
│   ├── migrate.py          # Supabase 스키마 배포 + 컬럼 검증
│   └── deploy_render.py    # Render 배포 트리거
└── upharma-au/
    ├── render_api.py       # FastAPI 앱
    ├── templates/index.html
    ├── static/             # app.js, styles.css, 정적 자산
    ├── crawler/            # 시장조사 크롤러
    ├── stage2/             # FOB 역산
    └── reports/            # 생성 PDF 등 (git 제외)
```

---

## 기술 스택 (실제 코드 기준)

- 백엔드 런타임: Python 3.11/3.12 + FastAPI + Uvicorn
- API/서버 진입점: `upharma-au/render_api.py` (시장조사, P2 가격전략, P3 바이어, PDF 다운로드 포함)
- 데이터 저장소: Supabase PostgREST (`australia`, `au_reports_history`, `au_reports_r2`, `au_buyers`)
- 크롤링 소스: TGA / PBS / Chemist Warehouse / buy.nsw / Healthylife
- AI 모델: Anthropic Claude Haiku (`claude-haiku-4-5-20251001` 고정)
- PDF 생성: `reportlab` (`upharma-au/report_generator.py`)
- 프론트엔드: Jinja2 템플릿 + Vanilla JS (`upharma-au/templates/index.html`, `upharma-au/static/app.js`)

> 프론트는 사용자 흐름(실행 버튼/진행 단계/다운로드) 중심으로 단순화되어 있고, 핵심 복잡도는 백엔드 수집·정합성·점수화·보고서 생성 파이프라인에 있습니다.

---

## 백엔드 파이프라인 상세 (크롤링 중심)

### 1) 시장조사 파이프라인 (`POST /api/crawl`)

`render_api.py` 가 품목 코드를 받아 `crawler/au_crawler.py::run_crawler()` 를 호출합니다.  
핵심 목적은 **외부 소스 재조회 → 단일 스키마 정규화 → Supabase upsert** 입니다.

- `sources/tga.py`: ARTG 등재 상태, sponsor 등 규제 근거 수집
- `sources/pbs.py`: AEMP/DPMQ/제약조건(restriction) 등 급여·가격 근거 수집
- `sources/chemist.py`: 소매 가격 관측치 수집
- `sources/buynsw.py`: 조달 관련 근거 수집
- `sources/healthylife.py`: 보조 소매 근거 수집

`au_crawler.py` 내부에서 소스별 DTO를 병합해 제품 단위 summary를 만들고,  
`crawler/db/supabase_insert.py` 의 화이트리스트 필터로 허용 컬럼만 upsert 합니다.

### 2) 가격 정합·추정 로직 (크롤 단계 내부)

`au_crawler.py` 의 `_estimate_retail_price()` 우선순위는 다음과 같습니다.

1. PBS 등재 + DPMQ 존재 시 DPMQ 우선
2. Chemist 실측치가 신뢰 가능하면 `chemist × RETAIL_MARKUP_MULTIPLIER(기본 1.20)`
3. AEMP fallback
4. 값이 없으면 null 유지

핵심은 “무조건 숫자를 채우기”가 아니라, **신뢰도 기준을 통과한 가격만 반영**하는 것입니다.

### 3) 보고서 생성 파이프라인 (`POST /api/report/generate`)

시장조사 결과를 바탕으로 백엔드가 다음을 한 번에 수행합니다.

- Haiku 호출로 분석 블록 생성
- 참고자료(뉴스/레퍼런스) 결합
- PDF 렌더링 (`report_generator.py`)
- 결과 메타/본문을 보고서 테이블(`au_reports_history`, `au_reports_r2`)에 기록

즉, UI는 단일 버튼이지만 서버는 **수집 데이터 + AI 해석 + 산출물 저장**까지 연쇄적으로 처리합니다.

### 4) P2 수출가격 전략 파이프라인 (`/api/p2/pipeline*`)

- `POST /api/p2/pipeline`: 작업 시작
- `GET /api/p2/pipeline/status`: 단계 상태 반환
- `GET /api/p2/pipeline/result`: 완료 결과 반환

`stage2/fob_calculator.py` 로 역산한 FOB 시나리오와 AI 블록을 결합해 보고서/PDF를 구성합니다.

### 5) P3 바이어 파이프라인 (현재 구현 포인트)

`render_api.py` 에서 실시간 파이프라인 엔드포인트를 제공합니다.

- `POST /api/p3/buyers/run`
- `GET /api/p3/buyers/status`
- `GET /api/p3/buyers/result`
- `POST /api/buyers/report/generate`

백그라운드 worker가 `buyer_discovery` 모듈을 호출해 Stage1 필터링, Stage2 점수화, `au_buyers` 반영, PDF 생성까지 처리합니다.

---

## 환경 변수 (`.env`)

| 변수 | 필수 | 용도 |
|---|---|---|
| `SUPABASE_URL` | 예 | PostgREST |
| `SUPABASE_SERVICE_KEY` | 예 | `sb_secret_...` |
| `SUPABASE_ACCESS_TOKEN` | migrate 시 | Management API PAT `sbp_...` |
| `PBS_SUBSCRIPTION_KEY` | 예 | PBS API |
| `ANTHROPIC_API_KEY` | 보고서·P2 AI 사용 시 | Haiku |
| `OPENAI_API_KEY` | 선택 | 번역·요약 등 |
| `SERPAPI_KEY` | 선택 | `/api/news` (없으면 mock) |
| `RENDER_API_KEY` / `RENDER_SERVICE_ID` | 배포 트리거 시 | `deploy_render.py` |

선택: `RETAIL_MARKUP_MULTIPLIER` (소매가 추정, 기본 `1.20`) — 동작은 `au_crawler` 코드 참고.

---

## 로컬 실행

**최초 1회 (프로젝트 루트):**

```bash
python -m venv venv
# Windows PowerShell: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/migrate.py
```

**매 세션:** 새 터미널마다 venv만 활성화하면 됩니다 (`pip install` 반복 불필요).

**서버 (루트에서):**

```bash
uvicorn render_api:app --app-dir upharma-au --reload --port 8000
```

브라우저: `http://127.0.0.1:8000`  
`upharma-au` 안에서 실행할 때는 `--app-dir upharma-au` 생략 가능.

**크롤러만 CLI (예):**

```bash
cd upharma-au/crawler
python au_crawler.py --product au-hydrine-004
# 전체 품목: python au_crawler.py --all
```

품목 지정은 **`--product` / `--all` 만 사용**합니다. 과거에 쓰이던 `PRODUCT_FILTER` 환경변수는 제거되었으며, 동시 요청 시 프로세스 간 경합을 피하기 위해 **코드에서 읽지 않습니다.** Render 대시보드에 `PRODUCT_FILTER` 를 넣어 두었다면 **삭제해도 됩니다** (웹의 `/api/crawl` 은 요청 body 의 `product_id` 만 사용).

---

## 가격 자료 PDF 업로드 (지침)

크롤로 AEMP·소매가 등을 못 잡았을 때, 사용자가 **가격 근거 PDF**를 올리면 서버가 숫자를 추출해 `au_products`에 반영하는 경로입니다. **엔드포인트:** `POST /api/crawl/price-pdf-upload` (`multipart`: `product_code`, `pdf_file`).

### 처리 순서 (내부 동작)

1. **텍스트 추출:** `pypdf` → 실패 시 `pdfplumber`. **스캔 PDF**(글자 레이어 없음)는 텍스트가 안 나와 실패할 수 있음.
2. **구조화 추출:** 추출된 텍스트(앞부분 약 3만 자)를 **Claude Haiku**에 넘기고, 도구 호출로 다음을 채움 — `aemp_aud`, `dpmq_aud`, `retail_price_aud`, `currency_detected`(AUD/USD/KRW/EUR/unknown), `confidence` 등.
3. **환산:** 통화가 AUD가 아니면 서버에서 환율 로직으로 AUD에 맞춤.
4. **DB:** 해당 `product_code` 행에 가격 필드 갱신, `retail_estimation_method`는 `user_pdf_upload` 등으로 표시.

### 업로드용 PDF 작성 시 권장 사항

- **가능하면 “선택 가능한 텍스트” PDF** (워드·엑셀에서 PDF 저장, 또는 PBS/공식 자료의 텍스트 레이어). **스캔 이미지만 있는 PDF**는 OCR 없이는 추출이 안 됨.
- 문서 안에 **AEMP(정부 승인 출고가)·DPMQ·소매가** 등 **어떤 가격인지 구분되는 표기**가 있으면 추출이 안정적임. 통화는 **AUD**를 명시하거나, 원문 통화를 적어 두면 `currency_detected`에 반영됨.
- **한 PDF에는 가능하면 한 품목**만 (여러 품목이 섞이면 모델이 혼동할 수 있음).
- **페이지 수:** 서버 정책상 **최대 4페이지**까지 업로드 허용(비용·처리 시간 통제). `requirements.txt`에 `pypdf`·`pdfplumber`가 설치되어 있어야 페이지 수·텍스트 추출이 동작함.

> **참고:** 수출가격 전략 탭의 **PDF 직접 업로드**(`/api/p2/upload` → `product_code`는 UI 상단 품목 선택값)은 **별도 저장 경로**이며, 위 “가격 자료 PDF 업로드”와 목적이 다를 수 있음. 통합 지침은 향후 UI·문서 정리 시 한 장으로 묶을 예정.

---

## Render 배포

- **의존성:** 저장소 **루트**의 `requirements.txt` 한 곳.
- **`render.yaml`** 의 `rootDir: .` 에 맞추려면 대시보드 **Root Directory 를 비우거나** 동일하게 유지.
- 빌드가 `requirements.txt` 없음으로 실패하거나 로그에 **예전 Build Command** 만 보이면: **Settings → Build Command 를 비워** Blueprint/`render.yaml` 이 적용되게 하거나, 저장소와 동기화를 다시 맞출 것.
- **시작:** 루트면 `uvicorn render_api:app --app-dir upharma-au --host 0.0.0.0 --port $PORT` (`render.yaml` 의 `startCommand` 와 동일한 분기).

---

## 스크립트

```bash
python scripts/migrate.py      # DDL + PostgREST NOTIFY + 컬럼 정합성 검사
python scripts/deploy_render.py
```

---

## 헬스·의존성

기동 후 `GET /health`, 상세는 `GET /health/deps`. 선택 패키지(`anthropic` 등)가 없어도 서버는 뜨고, 해당 API 만 `503` 일 수 있습니다.

---

## 운영 시 자주 걸리는 것

- **`PGRST204` / 컬럼 없음:** DDL 을 대시보드에서만 직접 돌렸다면 **Database → Reload Schema** 또는 `NOTIFY pgrst, 'reload schema';`. `migrate.py` 경로면 NOTIFY 가 포함됩니다.
- **`supabase_insert._ALLOWED_COLUMNS`:** DB에만 컬럼이 있고 화이트리스트에 없으면 **upsert 시 값이 버려집니다.** 컬럼 추가 시 SQL + 코드 동기화 + `migrate.py` 검증.
- **Render 빌드:** 루트 `requirements.txt` 가 빌드 컨텍스트에 안 잡히면 **서비스 루트가 잘못 지정된 것**일 때가 많습니다 (위 Render 절 참고).

---

## 보고서·크롤 파이프라인 (스키마 안정성)

크롤링 → DB 적재 → 시장분석 보고서 → 가격(FOB) 산출·수출전략 보고서 → 바이어 추천까지 **한 줄기 워크플로**로 묶여 있어도, DB와 API에서 다루는 데이터는 **역할을 나누는 것**이 안전합니다.

- **원천·가격·단계 플래그 (느리게 바꾸는 층)**  
  `au_products` 등에 쌓이는 크롤 결과, `pricing_case`, PBS/TGA 식별자, FOB 숫자, 단계 구분용 키는 **다음 단계가 공통으로 읽는 계약**입니다. 컬럼 의미를 자주 바꾸면 파이프라인 전반이 흔들립니다. 변경 시에는 마이그레이션·호환 읽기·`migrate.py` 검증을 함께 가져갑니다.

- **보고서 본문 (자주 바뀌는 층)**  
  섹션 구성·문체·디자인(v8/v5 등)은 제품 스펙에 가깝습니다. 이를 `block2_*` 같은 컬럼으로 쪼개 두면 양식만 바꿔도 **ALTER와 코드 전역 수정**이 반복됩니다. 팀 합의에 따라 **보고서 스냅샷은 JSONB 한 컬럼(`report_content_v2` 등) + 필요 시 `schema_ver`** 로 두고, 긴 서술은 그 안에서만 진화시키는 편이 **원천 스키마와 보고서 스키마를 분리**하는 데 유리합니다.

- **단계 간 연결은 얇게**  
  다음 API는 “긴 문단 전체”보다 `product_id`, 단계(`gong`), 시나리오 키, FOB 요약처럼 **짧고 안정적인 필드**만 고정해 두고, 상세 본문은 DB/JSON 또는 PDF 경로로 참조하는 방식이 유지보수에 유리합니다.

- **바이어 Top10 등**  
  추천 로직이 읽는 것은 **크롤·바이어 후보 테이블과 공통 키**로 두고, 시장분석 **문단 텍스트 스키마**와는 가능한 한 **결합을 느슨하게** 두는 것이 좋습니다.

### 브라우저 캐시 vs DB에 보고서 저장

다른 팀원이 말한 “시장분석 생성 때 켜져 있는 웹에 캐시해 두고 다음 단계에서 읽는다”는 것은 보통 다음을 뜻합니다.

- **브라우저 측:** `localStorage` / `sessionStorage` 또는 메모리 변수에, `POST /api/report/generate` 응답(JSON)을 **같은 탭·같은 세션** 안에서만 보관해 두고, 수출전략 단계 버튼에서 그걸 다시 꺼내 쓰거나 요청 body에 실어 보내는 방식입니다. 구현은 가볍지만 **다른 기기·새로고침·서버만 재실행**하면 사라지고, **백그라운드 작업·감사·재현**에는 불리합니다.

- **DB에 텍스트(또는 JSON) 저장:** 같은 `product_id`로 나중에 **서버가 수출전략 AI를 호출할 때** 시장분석 요약·본문을 **확실히 읽을 수 있고**, 여러 사용자·배포 환경에서도 동일합니다. **AI가 시장분석을 보고 수출전략을 더 잘 쓰게 하려면**, 그 맥락을 프롬프트에 넣어야 하므로 **저장소가 브라우저뿐이면 한계**가 있습니다. DB(또는 서버 디스크의 단일 스냅샷 + DB 메타)에 두면 **“이전 단계 보고서”를 근거로 넣기**가 구조적으로 쉬워집니다.

정리하면, **UI 편의용으로 브라우저에 잠깐 두는 것**과 **파이프라인·감사·AI 입력으로서의 단일 진실**은 목적이 다릅니다. 후자가 필요하면 **DB(또는 이미 쓰는 `au_reports_history` 등)에 스냅샷을 남기는 쪽**이 맞고, 팀에서 논의 중인 `report_content_v2` JSONB는 그 방향과 맞습니다.

**구현 상태 (저장소 기준):** `scripts/migrations/20260420_report_content_v2.sql` 로 `au_reports_history`·`au_reports_r2` 에 `report_content_v2` 컬럼이 추가됩니다. 적용 후 `POST /api/report/generate` 성공 시 시장분석 행이 `au_reports_history`(gong=1)에 append 되며, 수출전략 파이프라인은 `au_reports_r2.report_content_v2` 에 본문 봉투(`schema_ver`, `product_code`, `pricing_case`, `p2_blocks` 등)를 함께 기록합니다. **Hydrine 전용이 아니라 `product_code` 기준으로 모든 품목에 동일한 형태**입니다. DDL 반영: `python scripts/migrate.py`. 기존 `au_products.block2_*` 업데이트는 당분간 유지되어 프론트·호환을 깨지 않습니다(후속 단계에서 중단 예정).

---

## API·스키마·품목 목록

엔드포인트 전체는 `upharma-au/render_api.py`, 테이블·컬럼은 `upharma-au/crawler/db/`, 품목 마스터는 `au_products.json` / `fob_reference_seeds.json` 을 보세요.
