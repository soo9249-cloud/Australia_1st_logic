# UPharma Export AI · Australia

한국유나이티드제약(주)의 **호주 수출 시장조사 자동화** 파이프라인입니다. `au_products.json` 에 정의된 8개 품목에 대해 **TGA ARTG · PBS · Chemist Warehouse · buy.nsw.gov.au** 4개 소스를 **순차 크롤링**하여 **Supabase `australia` 테이블**에 `product_id` 기준 upsert 합니다. (PBS 미등재 Private 처방약 참고가용 `sources/healthylife.py` 는 독립 유틸리티로 별도 보관 — §4.6 참조.) 이후 **Stage2 FOB 역산(Logic A/B × 3 시나리오)** 으로 수출 예상가를 계산하고, Claude Haiku 기반 AI 파이프라인이 1공정 보고서를 2공정 가격 분석으로 연결합니다.

- **백엔드:** FastAPI (`render_api.py`) — 크롤러·Stage2·LLM 어댑터를 얇게 감싼 단일 서버
- **프론트:** Vanilla HTML/CSS/JS — `templates/index.html` + `static/app.js` (프레임워크 없음)
- **DB:** Supabase (PostgREST + Management API)
- **LLM:** Claude Haiku (`claude-haiku-4-5-20251001`) — **Sonnet/Opus 사용 절대 금지**

> 프론트엔드는 **Next.js 가 아닙니다.** 초기 설계에 있던 `next-app/` 폴더는 2026-04 정리되어 삭제되었고, 현재는 Python 서버가 `templates/index.html` 을 직접 서빙합니다.

> **변경 이력은 이 README 의 §14 "변경 이력" 한 곳에만 기록합니다.** 별도 변경 리포트 파일을 만들지 않습니다.

---

## 🎯 설계 원칙 — 실시간성 우선

> 이 시스템의 목적은 **실시간 시장분석을 위한 데이터 수집**입니다. 항상 최신 데이터를 보장해야 합니다.

- **`POST /api/crawl` 은 무조건 재크롤링**합니다. "DB에 이미 있으니 스킵" 같은 캐시 로직은 없습니다.
- TGA ARTG · PBS API · Chemist Warehouse · buy.nsw 4곳을 매 호출마다 실시간 재조회하고 Supabase `australia` 테이블을 최신 값으로 덮어씁니다 (`upsert on_conflict=product_id`).
- 버튼 한 번에 ~100초 소요되지만, 이는 **의도적 트레이드오프** — 실시간성을 속도보다 우선합니다.
- **캐시 레이어를 추가하지 마세요.** 속도 최적화가 필요하면 병렬화(asyncio)·rate limit 완화·소스별 타임아웃 조정 방향으로 접근하고, "DB에 있으면 스킵" 구조는 금지합니다.

---

## 1. 기술 스택

### 런타임 · 언어

| 구분 | 버전 · 비고 |
|---|---|
| Python | 3.11 (Render) / 3.12 (GitHub Actions) |
| 프론트 | Vanilla HTML/CSS/JS (프레임워크 없음) |
| LLM 모델 | Claude Haiku `claude-haiku-4-5-20251001` 고정 (Sonnet/Opus 금지) |

### 웹 서버 · 핵심

| 패키지 | 용도 | 필수 여부 |
|---|---|---|
| **FastAPI** | `render_api.py` — 크롤러·Stage2·AI 어댑터 | 필수 |
| **uvicorn[standard]** | ASGI 서버 (Render startCommand) | 필수 |
| **jinja2** | `templates/index.html` 서빙 | 필수 |
| **httpx** | HTTP 클라이언트 (PBS API, Jina Reader, Supabase Management) | 필수 |
| **supabase-py** | `table("australia").upsert(on_conflict="product_id")` | 필수 |
| **python-dotenv** | 상위 폴더 `.env` 자동 탐색 (`override=False`) | 필수 |

### 크롤러 부속 (Python)

| 패키지 | 용도 |
|---|---|
| **selectolax** | HTML DOM 파싱 (대부분 Jina Reader 마크다운 우회로 대체) |
| **trafilatura** | 본문 추출 백업 경로 |
| **tenacity** | 재시도 로직 |

### 선택 의존성 (설치 누락 시 해당 엔드포인트만 `503` 반환)

| 패키지 | 용도 | 누락 시 영향 |
|---|---|---|
| **anthropic** | Claude Haiku 호출 | `POST /api/report/generate`, `POST /api/p2/pipeline` → 503 |
| **openai** | `/api/report/generate` 의 refs 요약 (선택 기능) | 요약 없이 원문만 표시 |
| **yfinance** | `/api/exchange` 환율 조회 주경로 | exchangerate-api.com 폴백 사용 |
| **reportlab** | `/api/report/generate` PDF 저장 | 텍스트 응답만 반환, PDF 미생성 |
| **pydantic** | Claude Haiku structured output 스키마 | LLM 블록 생성 실패 |

> 서버 기동 시 `[deps-probe] <모듈>: OK | MISSING` 로그가 stdout 에 찍히고, 런타임에서는 `GET /health` · `GET /health/deps` 로 확인 가능합니다.

### 외부 서비스 · API

| 서비스 | 인증 | 역할 |
|---|---|---|
| **Supabase (PostgreSQL)** | `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (sb_secret_*) | 데이터 저장 (PostgREST) |
| **Supabase Management API** | `SUPABASE_ACCESS_TOKEN` (PAT, sbp_*) | 스키마 DDL 실행 (`scripts/migrate.py`) |
| **PBS API v3** | `PBS_SUBSCRIPTION_KEY` | `data-api.health.gov.au/pbs/api/v3/items` |
| **Jina Reader** | 불필요 | `r.jina.ai/{url}` — SPA · Cloudflare 우회용 마크다운 프록시 |
| **PubChem REST** | 불필요 | INN 정규화 (`hydroxyurea` → `hydroxycarbamide`) |
| **Anthropic Claude** | `ANTHROPIC_API_KEY` | 보고서 블록 생성 + 2공정 AI 파이프라인 (Haiku 고정) |
| **SerpAPI** | `SERPAPI_KEY` (선택) | `/api/news` (없으면 mock) |
| **exchangerate-api.com** | 불필요 | `/api/exchange` fallback |
| **Render.com** | `RENDER_API_KEY` + `RENDER_SERVICE_ID` | 배포 트리거 (`scripts/deploy_render.py`) |

---

## 2. 폴더 구조

```
Australia_1st_logic/
├── .env                                      ← 모든 환경 변수 (git 제외)
├── .gitignore / .gitattributes
├── README.md                                 ← 이 파일 (변경 이력 단일 진실)
├── render.yaml                               ← Render Web Service 배포 스펙
├── upharma_demo_v3.html                      ← 디자인 레퍼런스 원본 (참고용)
│
├── .github/workflows/
│   └── au_crawl.yml                          ← GitHub Actions (Render 백업 실행 경로)
│
├── scripts/
│   ├── migrate.py                            ← Supabase 스키마 배포 (Management API)
│   └── deploy_render.py                      ← Render 배포 트리거
│
└── upharma-au/
    ├── requirements.txt
    ├── render_api.py                         ★ FastAPI 어댑터 (엔드포인트 전체)
    ├── report_generator.py                   ← 1공정 PDF 생성기 (reportlab, 한글 폰트 자동 등록, 2페이지 레이아웃)
    │
    ├── templates/index.html                  ← v3 UI (Jinja 템플릿, 단일 HTML)
    │
    ├── static/
    │   ├── styles.css                        ← 분리된 CSS (1공정 + 2공정 통합)
    │   └── app.js                            ← 프론트 로직 + /api/* fetch (2공정 포함)
    │
    ├── crawler/                              ← 1공정 백엔드 (크롤링)
    │   ├── au_crawler.py                     ← 메인 파이프라인 (main → run_crawler)
    │   ├── au_products.json                  ← 8품목 마스터
    │   ├── sources/
    │   │   ├── tga.py                        ← TGA ARTG + 상세 파싱
    │   │   ├── pbs.py                        ← PBS API v3 + 웹 보강
    │   │   ├── chemist.py                    ← Chemist Warehouse (Jina) + build_sites
    │   │   ├── buynsw.py                     ← buy.nsw.gov.au notices (Jina)
    │   │   └── healthylife.py                ← 독립 유틸리티 — PBS 미등재 Private 처방약 소매가 (메인 파이프라인 외)
    │   ├── utils/
    │   │   ├── inn_normalize.py              ← PubChem 정규화
    │   │   ├── scoring.py                    ← completeness_score, AU_REQUIRED_FIELDS
    │   │   ├── evidence.py                   ← build_evidence_text (영/한)
    │   │   └── enums.py
    │   └── db/
    │       ├── australia_table.sql           ← 5테이블 DDL
    │       ├── supabase_insert.py            ← upsert_product, _ALLOWED_COLUMNS
    │       └── __init__.py
    │
    ├── stage2/                               ← 2공정 FOB 역산 (신규)
    │   ├── fob_calculator.py                 ← Logic A/B + dispatch_by_pricing_case
    │   ├── fob_reference_seeds.json          ← 8품목 시드 (pricing_case · AEMP/DPMQ/retail 기준)
    │   └── test_fob_calculator.py            ← 단위 테스트
    │
    └── reports/                              ← 런타임 생성물 (git 제외)
        ├── au_report_*.pdf                   ← /api/report/generate 산출 PDF
        └── _p2_uploads/                      ← 2공정 업로드 PDF (/api/p2/upload)
```

---

## 3. 클라이언트가 요청한 8개 품목

| product_id | 품목명 | INN | 함량 · 제형 | HS 코드 | Stage2 pricing_case |
|---|---|---|---|---|---|
| `au-omethyl-001` | Omethyl Cutielet | omega-3-acid ethyl esters | 2g · Pouch | 300490 | `ESTIMATE_private` |
| `au-gadvoa-002` | Gadvoa Inj. | gadobutrol | 604.72mg · 주사 | 300640 | `ESTIMATE_hospital` |
| `au-sereterol-003` | Sereterol Activair | fluticasone + salmeterol | 복합 · Inhaler | 300460 | `DIRECT` |
| `au-hydrine-004` | Hydrine | hydroxyurea | 500mg · Cap | 300490 | `DIRECT` |
| `au-rosumeg-005` | Rosumeg Combigel | rosuvastatin + omega-3 | 복합 · Cap | 300490 | `COMPONENT_SUM` |
| `au-atmeg-006` | Atmeg Combigel | atorvastatin + omega-3 | 복합 · Cap | 300490 | `COMPONENT_SUM` |
| `au-ciloduo-007` | Ciloduo | cilostazol + rosuvastatin | 복합 · Tab | 300490 | `ESTIMATE_withdrawal` |
| `au-gastiin-008` | Gastiin CR | mosapride citrate | 15mg · Tab | 300490 | `ESTIMATE_substitute` |

**pricing_case 의미 (2공정 분기 기준)**

| 케이스 | 설명 | Logic |
|---|---|---|
| `DIRECT` | PBS 등재 단일 성분 — AEMP 직접 사용 | Logic A 기본 |
| `COMPONENT_SUM` | 복합제인데 PBS 에 복합 조합 없음 → 단일 성분 AEMP 합 추정. **PBAC(호주 의약품급여자문위원회) 임상우월성 입증 필요** | Logic A + 경고 |
| `ESTIMATE_private` | PBS 미등재 (민간 판매 전용) → Chemist Warehouse 소매가 역산 | Logic B |
| `ESTIMATE_hospital` | 약국 유통 없음 → Hospital tender(병원 공급 입찰) · HealthShare NSW 병원조달 전용. FOB ±20% 변동성 | Logic A·B 모두 ±20% 경고 |
| `ESTIMATE_substitute` | 성분 자체가 TGA 미등재 → 유사계열 대체품 AEMP 기반 추정 | Logic A + substitute 경고 |
| `ESTIMATE_withdrawal` | Commercial Withdrawal(2021) 이력 — 재등재 불가 상태 | 계산 차단 (`logic=blocked`) |

---

## 4. 1공정 — 크롤링 순서 · 각 단계의 반환값

**순차 실행** (병렬 아님). 한 품목당 실측 **~100초**. 병렬화는 안정성을 위해 의도적으로 하지 않음.

```
PRODUCT_FILTER=au-hydrine-004
        ↓ au_products.json 매칭
┌───────────────────────────────────────────────────────────────┐
│ ① TGA ARTG               sources/tga.py       ~5초            │
│ ② PBS API                sources/pbs.py       ~45초 (성분 1개)│
│ ③ PBS 웹 보강            sources/pbs.py       ~7초            │
│ ④ Chemist Warehouse      sources/chemist.py   ~20초           │
│ ⑤ buy.nsw.gov.au         sources/buynsw.py    ~20초           │
└───────────────────────────────────────────────────────────────┘
        ↓ 각 소스 dict 를 한 dict 로 병합
build_product_summary()  →  75컬럼 summary
        ↓
supabase_insert.upsert_product()  →  australia (on_conflict=product_id)
```

### ① TGA ARTG — `sources/tga.py`

호주 의약품·의료기기 공식 등록부 (Australian Register of Therapeutic Goods). Jina Reader 로 검색 페이지를 파싱.

| 반환 필드 | 의미 | 예시 |
|---|---|---|
| `artg_number` | ARTG 등록번호 (호주 의약품 고유 ID) | `313760` |
| `artg_status` | 등록 상태 | `registered` / `not_registered` |
| `tga_schedule` | 스케줄 약물 분류 (S2/S3/S4/S8 만 저장) | `S4` = 처방약, `S8` = 마약성 |
| `tga_sponsor` | 호주 내 판매 대행 "스폰서" 업체 | `Medsurge Pharma Pty Ltd` |
| `tga_licence_category` | 라이선스 카테고리 | `RE` (Registered) / `L` (Listed) |
| `tga_licence_status` | 라이선스 상태 | `A` (Active) 등 |
| `artg_source_url` | ARTG 상세 페이지 URL | `https://www.tga.gov.au/resources/artg/313760` |

이후 `determine_export_viable()` 판정:
- `S8` → `not_viable` / `SCHEDULE_8`
- `registered` → `viable` / `ARTG_REGISTERED`
- 그 외 → `not_viable` / `TGA_NOT_APPROVED`
- (PBS 등재가 확인되면 `export_viable=viable` / `PBS_REGISTERED` 로 덮어씀)

### ② PBS API — `sources/pbs.py`

호주 공적 급여 약가 (Pharmaceutical Benefits Scheme). 공식 API v3.

**호출 순서:**
1. `fetch_latest_schedule_code()` → 최신 schedule_code 획득
2. `drug_name=INN` 으로 `/items` primary 조회 → 매칭 없으면 PubChem 정규화명으로 재시도
3. 그래도 없으면 fallback: `drug_name` 없이 페이지 순회(최대 10페이지) + 부분 일치
4. 매칭 결과 **1개로 축약** → 오리지널(`innovator=Y`) 우선, 없으면 제네릭 중 **최저가**
5. 복합 성분은 `fetch_pbs_multi()` 로 성분별 결과 병합

| 반환 필드 | 의미 | 예시 |
|---|---|---|
| `pbs_listed` | PBS 급여 등재 여부 | `True` |
| `pbs_item_code` | PBS 품목 코드 | `3093T` |
| `pbs_price_aud` | **결정가** (determined_price) — 제조사가 정부에 공급하는 가격 (AEMP 역할) | `31.92` |
| `pbs_dpmq` | **DPMQ** = Dispensed Price for Maximum Quantity — 약국 조제 시 최대수량 기준 판매가 | `48.11` |
| `pbs_patient_charge` | 환자 본인부담금 | `25.0` |
| `pbs_pack_size` | 한 팩에 담긴 단위 수 | `100` (캡슐) |
| `pbs_pricing_quantity` | 가격 산정 기준 수량 | `100` |
| `pbs_benefit_type` | 급여 유형 | `R` (Restricted Benefit) / `S` (Special Authority) / `U` (Unrestricted) |
| `pbs_program_code` | 프로그램 구분 | `GE` (General) |
| `pbs_brand_name` | 브랜드명 | `Hydrea` |
| `pbs_innovator` | 오리지널 의약품 여부 | `Y` (오리지널) / `N` (제네릭) |
| `pbs_first_listed_date` | 최초 PBS 등재일 | `1991-08-01` |
| `pbs_repeats` | 처방 갱신 가능 횟수 | `3` |
| `pbs_formulary` | formulary 분류 | `F1` (특허 보호) / `F2` (제네릭 경쟁) |
| `pbs_restriction` | 급여 제한 여부 (`benefit_type` 이 R/S 인지) | `True` |
| `pbs_total_brands` | 해당 성분 전체 브랜드 수 | `2` |
| `pbs_brands` | 브랜드 리스트 JSONB | `[{...}, {...}]` |
| `pbs_source_url` | PBS 검색 URL | `https://www.pbs.gov.au/browse/medicine?search=3093T` |
| `restriction_text` | 급여 제한 사유 원문 | — |

**주의 — PBS API Rate Limit:** 각 API 호출 전 `time.sleep(21)`. 호출 수 × 21초가 전체 시간의 50%+.

### ③ PBS 웹 보강 — `sources/pbs.py` `fetch_pbs_web()`

`pbs_item_code` 가 있을 때만 실행. `https://www.pbs.gov.au/medicine/item/{code}` 페이지를 Jina Reader 마크다운으로 받아 가격 표 파싱. API 값이 `null` 이면 웹 값으로 덮어씀.

복합 성분은 `pbs_item_code` 가 `+` 로 연결(`"3093T+1234X"`)되어 각 code 별 호출 후 합친다.

### ④ Chemist Warehouse — `sources/chemist.py`

호주 최저가 할인약국 체인. **Cloudflare 우회 2단계** 전략: 직접 호출 먼저 시도 → 차단 감지 시 Jina AI Reader 폴백.

URL: `https://www.chemistwarehouse.com.au/search?query={INN}`

| 반환 필드 | 의미 | 예시 |
|---|---|---|
| `retail_price_aud` | Chemist 크롤링 원본 가격 (검색 결과 첫 양수 `$` 값) | `37.90` |
| `price_source_name` | **항상 `"Chemist Warehouse"` 고정** (경로 구분은 로그에만 — app.js 정확 매칭 호환) | |
| `price_source_url` | 원본 검색 URL | |
| `price_unit` | `"per pack"` | |

**호출 순서 (2단 폴백):**

1. **직접 호출** (`_fetch_direct`) — `httpx.get` 으로 Chemist 서버 직격. `User-Agent`/`Accept-Language: en-AU`/timeout 15초. 정상 HTML 받으면 파싱.
2. **Jina AI Reader 폴백** (`_fetch_jina`) — Cloudflare 차단 감지 시 `https://r.jina.ai/{원본 URL}` 로 우회 (timeout 30초).

**Cloudflare 차단 감지** (`_is_cloudflare_blocked`): HTTP `403/503/520~527` 또는 본문에 `cloudflare` · `cf-ray` · `attention required` · `challenge-platform` · `checking your browser` · `just a moment` · `please enable javascript` 포함 시, 또는 `200` 응답이더라도 본문 길이 `< 1500` 바이트 + 차단 키워드 존재 시 즉시 Jina 폴백.

**가격 추출** (`_extract_first_price`): 본문에서 `$XX.XX` 정규식으로 `0` 초과 첫 값만 픽업 (장바구니 `$0.00` 무시).

**신뢰 검증** (`_chemist_retail_trustworthy`): PBS 가격의 15% 미만이거나 `$5` 미만이면 오매칭·부분파싱으로 간주 → Chemist 가격 버리고 `retail_price_aud = None`. 이 경우 Omethyl 처럼 `healthylife_slug` 지정된 품목은 ⑥ Healthylife 로 자동 폴백(아래 참고).

> **주의:** 최종 `retail_price_aud` 컬럼 값은 **Chemist 원본이 아닌 시장 추정가**임 (PBS DPMQ 또는 Chemist × 1.20). 자세한 로직은 §4 최종 병합 아래 "소매가 추정" 섹션 참조.

### ⑤ buy.nsw.gov.au — `sources/buynsw.py`

뉴사우스웨일스 주정부 공공 조달 공고 (Contract Award Notice + Annual Procurement Plan). SPA이므로 Jina Reader 경유.

URL: `https://buy.nsw.gov.au/notices/search?mode=regular&query={INN}&noticeTypes=can%2Capp`

| 반환 필드 | 의미 | 예시 |
|---|---|---|
| `nsw_contract_value_aud` | 첫 공고의 계약 금액 | `1690000.00` |
| `nsw_supplier_name` | 발주 기관 (Agency) 명 | `Venues NSW` |
| `nsw_contract_date` | Publish date | `11-Nov-2025` |
| `nsw_source_url` | 검색 URL | |

> **supplier_name** 필드는 호환성을 위한 이름으로, buy.nsw 문맥에서는 실제로 "공급자"가 아닌 **발주처(Agency)** 를 담습니다.

### ⑥ Healthylife (Chemist 폴백) — `sources/healthylife.py`

> **통합 완료 (2026-04-17):** `au_crawler.py` 의 `main()` 에서 ④ Chemist 호출 직후 조건부 폴백으로 호출됨. `au_products.json` 에 `healthylife_slug` 가 지정된 품목만 대상 (현재 **Omethyl** 1개). Chemist 가격이 `None` 또는 `$5` 미만이면 Healthylife 결과로 `chemist` dict 전체가 치환됨 → 이후 파이프라인은 Healthylife 값을 Chemist 자리에 넣고 계속 진행.

**목적:** PBS 미등재 Private 처방약 (대표적으로 **Omethyl**, Omega-3 제품) 의 소매 참고가 수집. Chemist Warehouse 가 해당 품목을 검색 결과에 노출하지 않을 때 보조 소스로 사용.

**호출 순서 (3단 폴백):**

1. **Next.js JSON API** — `GET https://www.healthylife.com.au/api/products/{slug}` (`price` · `salePrice` · `currentPrice` · `priceInCents` 중 첫 양수)
2. **공개 HTML 페이지** — `GET /products/{slug}` → `$XX.XX` 정규식 + `<h1>` 또는 마크다운 제목 추출
3. **Jina AI Reader 폴백** — `https://r.jina.ai/https://www.healthylife.com.au/products/{slug}` (Cloudflare/SPA 차단 대응)

**Cloudflare 감지** (`_is_blocked`): HTTP `403 / 503 / 520~527` 또는 본문에 `cloudflare` · `cf-ray` · `attention required` · `challenge-platform` · `checking your browser` · `just a moment` 포함 시 즉시 폴백 진행.

**반환 dict 형식:**

| 필드 | 의미 | 예시 |
|---|---|---|
| `slug` | 쿼리 slug | `omacor-1000mg-cap-28` |
| `brand_name` | 제품명 | `OMACOR 1000mg Capsules 28 Pack` |
| `price_aud` | 소매가 (AUD) | `48.95` |
| `is_pbs` | PBS 등재 여부 | `False` (고정 — Private 전용) |
| `prescription` | 처방 여부 | `True` / `False` / `None` |
| `source` | 경로 식별 | `Healthylife JSON API` / `Healthylife HTML 파싱` / `Healthylife (via Jina AI Reader)` |
| `confidence` | 신뢰도 (경로별) | `0.85` (JSON) / `0.75` (HTML) / `0.70` (Jina) |
| `price_source_url` | 원본 상품 URL | — |

**호출 설정:** `_REQUEST_DELAY = 1.5초`, `_TIMEOUT = 12초` (Jina 경로는 30초), 성공 시 `time.sleep(1.5)` 로 예의 지킴.

**CLI 실행 (단건 테스트):**
```bash
cd upharma-au/crawler
python -m sources.healthylife       # __main__ 의 기본 slug = omacor-1000mg-cap-28
```

**통합 방식 (현재):** `au_crawler.main()` 에서 ④ Chemist 호출 직후, `product.healthylife_slug` 지정 품목에 한해 `fetch_healthylife_price(slug)` 를 호출. 반환된 `price_aud` 가 숫자이고 Chemist 측 가격이 비어있거나 `$5` 미만이면 `chemist` dict 를 다음 구조로 덮어씀:

```python
chemist = {
    "retail_price_aud": float(hl["price_aud"]),
    "price_unit": "per pack",
    "price_source_name": hl.get("source") or "Healthylife",
    "price_source_url": hl.get("price_source_url") or "",
}
```

이후 `build_product_summary` 는 Healthylife 가격을 Chemist 가격처럼 취급하여 소매가 추정 로직(다음 섹션)에 투입. 단 `price_source_name` 이 `"Chemist Warehouse"` 가 아니므로 app.js 의 정확 매칭에선 "해당없음" 으로 떨어질 수 있음 — 프론트 리팩토링 시 `retail_estimation_method` 기반 매칭으로 전환 필요.

### Jina Reader 사용 전략 (공통)

여러 소스가 Jina Reader (`https://r.jina.ai/{url}`) 를 공통 우회 프록시로 사용합니다. 각 소스에서 쓰는 이유를 한 곳에 정리:

| 소스 | Jina 사용 이유 |
|---|---|
| TGA ARTG (①) | 검색 페이지가 JS 렌더 — 마크다운 프록시로 정적 파싱 |
| PBS 웹 보강 (③) | 가격 표 HTML 이 복잡 — 마크다운으로 받아 정규식 파싱 간소화 |
| Chemist Warehouse (④) | Cloudflare + SPA — **직접 호출 먼저 시도, 차단 감지 시 Jina 폴백** (2단 전략) |
| buy.nsw.gov.au (⑤) | Angular SPA — `httpx` 직접 요청 시 빈 HTML |
| Healthylife (⑥) | JSON API/HTML 실패 시 최후 폴백 |

**Jina Reader 의 트레이드오프:**
- **장점:** Cloudflare/SPA/JS 렌더 페이지를 **인증 없이** 마크다운으로 받음
- **단점:** 외부 서비스 의존 (Jina 다운 시 전체 파이프라인 영향), 마크다운 파싱 결과가 사이트 레이아웃 변경에 민감
- **장애 시 폴백:** 현재 명시적 폴백 로직은 Chemist Warehouse 가격에만 존재 (PBS 15% 미만 → PBS 값 사용). 나머지 소스는 **Jina 실패 시 빈 dict 반환 + confidence 감점**으로 처리.

### 최종 병합 — `build_product_summary`

5개 소스 dict + `au_products.json` 의 품목 메타를 한 dict 로 합치고 75컬럼을 채움.

| 필드 | 의미 |
|---|---|
| `retail_price_aud` | **시장 추정 소매가** (PBS DPMQ 또는 Chemist × 1.20) — 자세한 로직은 다음 섹션 |
| `chemist_price_aud` | **신규** Chemist Warehouse 원본 크롤링 가격 (오매칭·저가 배제 후, 참고용) |
| `retail_estimation_method` | **신규** 추정 경로 — `"pbs_dpmq"` / `"chemist_markup"` / `None` |
| `price_source_name` | `"PBS"` (DPMQ 경로) 또는 `"Chemist Warehouse"` (markup 경로) |
| `price_source_url` | 선택된 출처의 URL |
| `export_viable` / `reason_code` | 수출 적합 판정 |
| `evidence_url` | 대표 증거 URL (ARTG 상세) |
| `evidence_text` | 영어 원문 (Sponsor / ARTG status / PBS 제한 등) |
| `evidence_text_ko` | GPT-4o-mini 한국어 번역 (키 없으면 원문) |
| `sites` JSONB | `{public_procurement:[{name,url},...], private_price:[...], paper:[]}` |
| `completeness_ratio` | `AU_REQUIRED_FIELDS` 중 값 있는 비율 (0 ~ 1) |
| `confidence` | `completeness_score()` 가중치 기반 신뢰도 (0 ~ 1) |
| `data_source_count` | 수집 시도한 소스 수 (일반적으로 4) |
| `error_type` | `PBS_WEB_ENRICHMENT_INCOMPLETE` 등 오류 코드 (정상 시 null) |
| `pricing_case` | `DIRECT` / `COMPONENT_SUM` / `ESTIMATE_*` — 2공정 분기 기준 |
| `fob_*` 5개 | **1공정 NULL** — 2공정에서 채움 |
| `block2_*`, `block3_*`, `perplexity_refs`, `llm_*` | **1공정 NULL** — LLM 연동 후 채움 |

### 소매가 추정 로직 — `_estimate_retail_price()` (2026-04-17 추가)

`retail_price_aud` 컬럼은 **크롤링 원본 가격이 아니라 시장 추정가**. Chemist Warehouse(호주 최저가 체인) 는 일반 약국 평균 대비 `~20%` 낮은 것으로 알려져 있어(CHOICE 조사 기준), 그대로 쓰면 수출 전략의 소매가 기준이 과소평가됨. 아래 우선순위로 **시장 평균 소매가**를 추정:

| 순위 | 조건 | `retail_price_aud` 값 | `retail_estimation_method` |
|---|---|---|---|
| 1 | `pbs_listed == True` + `pbs_dpmq > 0` | `pbs_dpmq` 그대로 | `"pbs_dpmq"` |
| 2 | 1순위 실패 + Chemist 신뢰 가격 존재 | `chemist_price_aud × 1.20` | `"chemist_markup"` |
| 3 | 1·2순위 실패 + `pbs_price_aud > 0` | `pbs_price_aud` 그대로 | `"pbs_dpmq"` |
| 4 | 전부 없음 | `None` | `None` |

**근거:**
- **1순위 DPMQ** — PBS(호주 의약품 급여 제도) 등재 품목의 DPMQ(최대처방량 총약가) 는 이미 AEMP(정부 승인 출고가) + 도매 마크업 + 약국 마크업(15%) + 조제 수수료($8.88) 가 포함된 시장 소매가.
- **2순위 Chemist × 1.20** — CHOICE(호주 소비자 단체) 시장조사 기준 일반 오프라인 약국 평균가가 Chemist Warehouse 대비 약 `+20%`. 환경변수 `RETAIL_MARKUP_MULTIPLIER` 로 배수 조정 가능 (기본 `1.20`).

**환경변수 설정 (선택):**
```bash
# copy.env 또는 Render 환경변수
RETAIL_MARKUP_MULTIPLIER=1.20    # 기본값. CHOICE 조사 업데이트 시 튜닝
```

**엣지케이스:**
- Chemist 가격이 PBS 가격의 `15%` 미만이거나 `$5` 미만 → 오매칭으로 간주, `chemist_price_aud = None` (`_chemist_retail_trustworthy`)
- PBS 등재지만 DPMQ 환자부담금 미만 — 그대로 DPMQ 사용(약국 총매출 정의상 일치)
- 복합 성분 품목의 DPMQ 합산 — 현재 `_merge_pbs_rows` 는 첫 행만 취함 (별도 과제로 분리)

### `au-hydrine-004` 실측 예시

```
product_id           = au-hydrine-004
product_name_ko      = Hydrine
inn_normalized       = hydroxycarbamide    ← hydroxyurea 가 PubChem 정규화됨
artg_number/status   = 313760 / registered
tga_sponsor          = Medsurge Pharma Pty Ltd
pbs_listed           = True
pbs_item_code        = 3093T
pbs_price_aud / dpmq = A$31.92 / A$48.11   ← AEMP / DPMQ
pbs_brand_name       = Hydrea              ← 오리지널 (innovator=Y)
retail_price_aud     = A$25.00             ← Chemist WH
price_source_name    = Chemist Warehouse
export_viable        = viable
reason_code          = PBS_REGISTERED
confidence           = 0.81
completeness_ratio   = 0.857
data_source_count    = 4
```

---

## 5. 2공정 — FOB 역산 시스템

### 5.1 개요

1공정에서 얻은 **AEMP(`pbs_price_aud`)** 또는 **소매가(`retail_price_aud`)** 를 기준으로, 한국 제조사가 호주에 수출할 때의 **FOB(Free On Board, 선적항 인도가)** 를 역산합니다. 1개 품목에 대해 `aggressive` · `average` · `conservative` 세 시나리오를 제공합니다.

구현: `upharma-au/stage2/fob_calculator.py` (450줄, 표준 라이브러리만 사용 — `anthropic` 의존 없음)

### 5.2 두 가지 계산 방식

**Logic A — PBS AEMP 기반 (공적 급여 품목)**

```
FOB (AUD) = AEMP / (1 + importer_margin_pct / 100)
FOB (KRW) = FOB (AUD) × fx_aud_to_krw  (기본 900)
```

- 호주 수입상 마진만 역산해서 제조사 출하가로 환원
- `DISPENSING_FEE_READY = $8.88` 는 참고값 (AEMP 가 이미 dispensing fee 를 제외한 값이라 계산엔 안 씀)

**Logic B — 민간 소매가 기반 (PBS 미등재 품목)**

```
FOB (AUD) = Retail
          / (1 + gst_pct / 100)             ← GST 공제 (Rx 0% · OTC 10%)
          / (1 + pharmacy_margin_pct / 100) ← 약국 마진 공제 (기본 30%)
          / (1 + wholesale_margin_pct / 100)← 도매 마진 공제 (기본 10%)
          / (1 + importer_margin_pct / 100) ← 수입상 마진 공제 (기본 20%)
```

**Logic B 참고가 우선순위 (2026-04-17 확장):**

`dispatch_by_pricing_case(seed, *, crawler_row=None)` 은 Logic B 입력 `Retail` 을 아래 순서로 결정:

| 순위 | 출처 | 조건 | `inputs.retail_source` |
|---|---|---|---|
| 1 | `seed.reference_retail_aud` (수기 검증된 참고가) | `> 0` | `"seed"` |
| 2 | `crawler_row.retail_price_aud` (시장 추정가, §4 소매가 추정 로직) | `> 0` | `"crawler"` |
| 3 | 차단 | 1·2 모두 없음 | — (`blocked_reason=no_reference_price`) |

2순위 사용 시 `warnings[]` 에 "소매 참고가 출처(크롤러 실시간): {method_label}. 수기 시드 미확보로 크롤러 추정가 사용" 문구 자동 추가. `method_label` 은 `retail_estimation_method` 에 따라 "PBS DPMQ(최대처방량 총약가)" 또는 "Chemist Warehouse × 1.20 (CHOICE 조사 기준 시장 평균)".

**하위호환:** `crawler_row` 인자 생략 시 기존 호출(seed 기반)과 100% 동일하게 동작.

### 5.3 시나리오 프리셋

| 시나리오 | Logic A | Logic B |
|---|---|---|
| `aggressive` (공격적) | `importer_margin - 10` | `importer_margin - 5` |
| `average` (평균) | `importer_margin` | `importer_margin` |
| `conservative` (보수) | `importer_margin + 10` | `importer_margin + 10` |

### 5.4 GST 정책 (호주 — 처방약 면제)

호주 GST 는 10% 이지만 **처방약(S4/S8) 은 GST-free**. 2공정 UI 는 보고서 선택 시 자동으로 전환합니다.

| 품목 | GST | 근거 |
|---|---|---|
| Hydrine · Sereterol · Gadvoa · Rosumeg · Atmeg · Ciloduo · Gastiin CR | **0%** (면제) | S4/S8 처방약 — GST-free |
| Omethyl (Omega-3) | **10%** (과세) | OTC · 건강기능식품 |

**구현 위치:** `static/app.js` 의 `_p2ClassifyGst(report)` + `_p2ApplyGstForReport(report)` · `_calcP2Manual` 의 `gst_fixed` 동적 rate 분기 · `_p2OptionCardHtml` valDisplay.

### 5.5 Withdrawal · PBAC · Hospital 경고 시스템

`fob_reference_seeds.json` 의 플래그를 읽어 `/api/stage2/calculate` 응답의 `warnings[]` 에 자동 포함:

| 플래그 | UI 경고 문구 |
|---|---|
| `pricing_case == "ESTIMATE_withdrawal"` | 계산 차단 (`logic=blocked`, `scenarios=[]`, `blocked_reason=commercial_withdrawal`) |
| `pbac_superiority_required` | 복합제/신규 등재 품목: PBAC(호주 의약품급여자문위원회) 임상우월성 입증 필요 (등재 지연·거절 리스크) |
| `hospital_channel_only` | 약국 유통 없음 → Hospital tender(병원 공급 입찰) · HealthShare NSW 병원조달 루트 전용. FOB ±20% 변동성 가능 |
| `section_19a_flag` | 호주 미등재 성분 → Section 19A(일시수입 특례) 경로 전용 |
| `restricted_benefit` | PBS Restricted Benefit(처방 적응증 제한) — 적용 환자군 좁음 |
| `confidence_score < 0.7` | `confidence_score X.XX — FOB 결과는 예비 참고치` |

### 5.6 2공정 UI 구성

`templates/index.html` 의 `<div id="p2">` 섹션 (약 185줄). 팀원(싱가포르 원본) 에서 이식 후 호주 기준으로 치환.

**두 입력 경로 (탭 전환):**
- **AI 파이프라인 탭** — 1공정 보고서 드롭다운 선택 또는 PDF 직접 업로드 → Haiku 가 가격 추출 → `fob_calculator` 자동 실행
- **직접 입력 탭** — 동일하게 보고서 선택 또는 PDF 업로드 → 사용자가 옵션(AEMP, retail, margin, GST 등) 수동 조정 → `/api/stage2/calculate` 호출

**시장 세그먼트:**
- **공공 시장** — PBS 공공급여 채널 · 주별 병원조달(HealthShare NSW 등) 기준 → Logic A
- **민간 시장** — Chemist Warehouse 등 약국 체인 · 소매 유통 구조 기준 → Logic B

**결과 UI:**
1. 추출 가격 정보 카드 (제품명 · 참조가 · 수출 적합성 판정 · 환율)
2. 최종 산정가 카드 + 산정 공식 (`÷ 1.10 (GST 10%)` 식으로 단계별 표기)
3. 가격 시나리오 3종 카드
4. 산정 이유 (seed warnings 노출 자리)
5. 보고서 다운로드 (PDF)

---

## 6. Supabase 스키마 — 6 테이블

### 1) `australia` — 1·2공정 통합 (75컬럼)

| 섹션 | 컬럼 수 |
|---|---|
| **공통 6** (변경 금지) — `id, product_id, market_segment, fob_estimated_usd, confidence, crawled_at` | 6 |
| 품목 마스터 | 6 |
| TGA ARTG | 7 |
| PBS API + 웹 | 20 |
| **민간 소매 (시장 추정)** — `retail_price_aud` · `chemist_price_aud` · `retail_estimation_method` · `price_source_name` · `price_source_url` · `price_unit` | 6 |
| NSW Procurement | 4 |
| 수출성 판정 | 2 |
| 증거 (영/한) | 3 |
| **2공정 FOB** (보수/기준/공격 시나리오) | 5 |
| 메타 (`sites` JSONB 등) | 4 |
| **LLM 블록** (Claude Haiku Block 2/3 + Perplexity + llm_meta) | 12 |

> **2026-04-17 변경:** `retail_price_aud` 의미 재정의 (Chemist 원본 → 시장 추정가). Chemist 원본은 `chemist_price_aud` 로 분리, 추정 경로는 `retail_estimation_method` 로 추적. Supabase ALTER 는 `crawler/db/australia_table.sql` 하단의 멱등 블록 참고.

### 2) `australia_history` — 스냅샷 append-only

### 3) `australia_buyers` — 3공정 바이어 + AHP PSI 5축 (합계 100점)

| 축 | 컬럼 | 배점 |
|---|---|---|
| 매출규모 | `psi_sales_scale` | 30 |
| 파이프라인 | `psi_pipeline` | 25 |
| 제조소 보유 | `psi_manufacturing` | 20 |
| 수입경험 | `psi_import_exp` | 15 |
| 약국체인 | `psi_pharmacy_chain` | 10 |

### 4) `reports` — 1/2/3공정 산출 보고서 메타

### 5) `australia_p2_results` — 2공정 수출 전략 제안 결과 (품목×세그먼트 1행)

- 핵심 제약: `UNIQUE(product_id, segment)`
- 세그먼트 제약: `segment IN ('public', 'private')`
- 인덱스: `idx_p2_results_product (product_id)`

### 6) `au_regulatory` — 호주 규제 체크포인트 시드 5행 (`title UNIQUE + ON CONFLICT DO NOTHING`)

---

## 7. API 엔드포인트 (`render_api.py`)

### 7.1 헬스체크 · 의존성 상태

| 메서드 · 경로 | 용도 |
|---|---|
| `GET /health` | `{status, optional_deps:{anthropic,openai,yfinance,reportlab}, stage2_ok, hint}` |
| `GET /health/deps` | 선택 의존성 상세 — `{ok, deps:{<name>:{ok, required_by[], error}}}` |

### 7.2 1공정 (크롤링 · 데이터)

| 메서드 · 경로 | 용도 |
|---|---|
| `GET /` | `templates/index.html` 서빙 |
| `POST /api/crawl` | `{product_id}` → `au_crawler.main()` 래핑 (SystemExit catch, env `PRODUCT_FILTER` 주입) |
| `GET /api/data` | `australia` 전체 목록 (최신 crawled_at 순) |
| `GET /api/data/{product_id}` | 단건 조회 |
| `GET /api/reports` | 오늘(UTC) 저장된 보고서 목록 |
| `POST /api/reports` | `reports.insert()` |
| `POST /api/report/generate` | Claude Haiku 로 Block2/3 + Perplexity refs 생성 → `australia` UPDATE → `report_generator.render_pdf()` 로 PDF 저장. `anthropic` 미설치 시 **503**, `reportlab` 미설치 시 텍스트만 반환 |
| `GET /api/news` | SerpAPI google_news 4건 (키 없으면 mock) |
| `GET /api/exchange` | AUD 기준 환율 `{aud_krw, aud_usd, updated}` — yfinance 주경로, 실패 시 exchangerate-api 폴백 |
| `GET /api/report/download` | reports/ 의 PDF 반환 (`inline=1` 로 브라우저 미리보기) |

**크롤러 호출 방식:** `from au_crawler import main as run_crawler` → `os.environ["PRODUCT_FILTER"] = product_id` 후 `run_crawler()` 호출 → `SystemExit` 예외로 종료 코드 감지. **단일 워커 전제** (동시 요청 시 env 경합 가능).

**PDF 생성 모듈 — `upharma-au/report_generator.py` (363줄)**

- `render_pdf(row, blocks, refs, meta) -> str` — 품목 1건 → `reports/au_report_{product_key}_{YYYYMMDD_HHMMSS}.pdf` 반환
- **입력 4종:**
  - `row` — `australia` 테이블 한 행 (품목 메타 · TGA · PBS · NSW · Chemist 컬럼)
  - `blocks` — Claude Haiku 가 생성한 `block2_*` / `block3_*` / `block4_regulatory` 10개 필드
  - `refs` — 하이브리드 학술 검색 결과 (Semantic Scholar · PubMed · Perplexity)
  - `meta` — `export_viable` · `confidence` · `confidence_breakdown` 등 판정 메타
- **한글 폰트 자동 탐색:** `fonts/NanumGothic.ttf` (번들) → AppleGothic (mac) → MalgunGothic (Windows) → `HYSMyeongJo-Medium` CID 폴백 → Helvetica (한글 깨짐 가능)
- **PDF 레이아웃 (품목당 2페이지):**
  - p1 — 타이틀 + 제품바 + 1.판정 + 2.판정근거(5축) + 3.시장진출전략(4축)
  - p2 — 4. 근거·출처 (4-1 Perplexity 추천 논문 / 4-2 사용된 DB·기관)
- **의존성:** `reportlab` 필수. 미설치 시 `render_api.py` 는 텍스트 블록만 반환하고 PDF 파일은 생성하지 않음 (§1 선택 의존성 표 참조).

### 7.3 2공정 Stage2 (FOB 역산)

| 메서드 · 경로 | 용도 |
|---|---|
| `GET /api/stage2/seeds` | 8품목 시드 목록 — UI 드롭다운용 컴팩트 필드 (`product_id`, `product_name`, `pricing_case`, `aemp_aud`, `dpmq_aud`, `retail_aud`, 플래그 4종, `confidence_score`) |
| `POST /api/stage2/calculate` | `{product_id, logic:"A"|"B", overrides:{base_aemp|base_retail, importer_margin, gst, pharmacy_margin, wholesale_margin}, fx_aud_to_krw?}` → `{logic, scenarios:[aggressive,average,conservative], inputs, warnings, disclaimer, blocked_reason}` |

### 7.4 2공정 AI 파이프라인 (Haiku)

| 메서드 · 경로 | 상태 |
|---|---|
| `POST /api/p2/upload` | ✅ 구현 — PDF base64 디코딩 후 `reports/_p2_uploads/{ts}_{safe}.pdf` 저장 |
| `GET /api/p2/pipeline/status` | ✅ 구현 — 현재는 `{status:"idle", step_label:"AI 엔진(Haiku) 연결 대기 중"}` 반환 |
| `POST /api/p2/pipeline` | 🔴 Stub (503 if anthropic missing, 501 otherwise) — Haiku 추출 + fob_calculator 연동 예정 |
| `GET /api/p2/pipeline/result` | 🔴 Stub (501) — 파이프라인 결과 조회 |
| `POST /api/p2/report` | 🔴 Stub (501) — PDF 보고서 생성 |

---

## 8. 프론트엔드 UI

5개 탭 — **메인 / 1공정 / 2공정 / 3공정 / 보고서**.

- **메인** — 거시지표 · 관세 · 환율(API) · 뉴스(API) · 파이프라인. GST 는 2줄로 표시: 처방약 0% (녹색) + OTC 10% (주황, Omethyl 명시)
- **1공정** — TODO 스텝 → 품목 선택 → `POST /api/crawl` → `GET /api/data/{id}` → 카드 렌더 → 보고서 산출
- **2공정** — AI 파이프라인 탭 + 직접 입력 탭 (§5.6 참조)
- **3공정** — 현재 데모 데이터 (향후 PSI 스코어링 로직 연동)
- **보고서** — `GET /api/reports` 초기 로드, 저장 버튼 → `POST /api/reports`

**실패 폴백:** API 실패 시 `app.js` 의 `PRODS` 배열(하드코딩 mock 8개)로 카드 렌더.

---

## 9. 운영 스크립트

### `scripts/migrate.py` — Supabase 스키마 배포

Supabase Management API (`POST /v1/projects/{ref}/database/query`) 로 `australia_table.sql` 전체를 한 번에 실행.

**실행 흐름 (4단계)**

1. **SQL 배포** — `australia_table.sql` 전체를 한 번에 POST → 서버가 순차 실행
2. **PostgREST 스키마 캐시 자동 리로드** — `NOTIFY pgrst, 'reload schema';` 발송 → `ALTER TABLE` 직후 supabase-py upsert 가 `PGRST204` 로 튕기는 버그 방지
3. **기본 검증** — `public` 스키마 테이블 목록 / `australia` 컬럼 수 / `au_regulatory` 시드 행 수
4. **`_ALLOWED_COLUMNS` ↔ DB 컬럼 대조 검증** — `supabase_insert._ALLOWED_COLUMNS` 를 import 해 실제 `information_schema.columns` 와 양방향 차집합 비교
   - `expected − actual` → SQL 에는 있는데 DB 에 없음 (ALTER 누락)
   - `actual − expected` → DB 엔 있는데 `_ALLOWED_COLUMNS` 누락 (insert 시 silent drop)
   - 전부 일치 → `✅ 컬럼 검증 통과`, 불일치 → exit 1

```bash
python scripts/migrate.py
```

### `scripts/deploy_render.py` — Render 배포 트리거

```bash
python scripts/deploy_render.py
```

---

## 10. 환경 변수 (`.env`)

| 변수 | 필수 | 용도 |
|---|---|---|
| `SUPABASE_URL` | ✅ | `https://{ref}.supabase.co` |
| `SUPABASE_SERVICE_KEY` | ✅ | `sb_secret_...` (PostgREST upsert) |
| `SUPABASE_ACCESS_TOKEN` | ✅ (migrate.py) | `sbp_...` PAT (Management API) |
| `PBS_SUBSCRIPTION_KEY` | ✅ | PBS API v3 구독 키 |
| `ANTHROPIC_API_KEY` | `/api/report/generate`, `/api/p2/pipeline` 사용 시 | Claude Haiku 호출 키 |
| `OPENAI_API_KEY` | 선택 | evidence.py 영→한 번역, refs 요약 |
| `SERPAPI_KEY` | 선택 | `/api/news` (없으면 mock) |
| `RENDER_API_KEY` | Render 배포 시 | `rnd_...` |
| `RENDER_SERVICE_ID` | Render 배포 시 | `srv-...` |

---

## 11. 로컬 실행

### 매 세션마다 (가상환경은 이미 만들어 둔 경우)

**`python -m venv` 나 `pip install` 을 매번 할 필요는 없습니다.** 새 터미널을 열 때마다 **활성화(activate)** 만 하면 됩니다.

프로젝트 루트(`Australia_1st_logic/`, 여기서 `dir` 시 `venv` 폴더가 보이는 위치)에서:

| 쉘 | 프롬프트 뒤에 입력할 명령 (한 줄) |
|---|---|
| **PowerShell** | `.\venv\Scripts\Activate.ps1` |
| **cmd** | `venv\Scripts\activate.bat` |
| **Git Bash** | `source venv/Scripts/activate` |

성공하면 프롬프트 앞에 `(venv)` 가 붙습니다. 이후 §11.3 의 `uvicorn` 으로 서버를 띄우면 됩니다.

> **PowerShell** 에서 `Activate.ps1` 실행이 막히면(스크립트 실행 정책), **현재 사용자**에게만 허용: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 를 한 번 실행한 뒤 다시 `.\venv\Scripts\Activate.ps1` 을 실행합니다.

### 11.1 최초 세팅 (1회)

```bash
python -m venv venv                        # 가상환경 폴더 생성 (프로젝트당 1회)
# venv 활성화 (쉘별로 위 표 또는 아래 11.2 참고)
# 아래 pip 는 저장소 루트(Australia_1st_logic/)에서 실행한다.
pip install -r requirements.txt
python scripts/migrate.py                  # Supabase 스키마 배포
```

### 11.2 venv 활성화 (쉘별)

| 쉘 | 활성화 명령 |
|---|---|
| **Git Bash** | `source venv/Scripts/activate` |
| **PowerShell** | `.\venv\Scripts\Activate.ps1` (최초 1회 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 필요할 수 있음) |
| **cmd** | `venv\Scripts\activate.bat` |

활성화되면 프롬프트 앞에 `(venv)` 가 붙는다.

### 11.3 웹 UI 로컬 서버 실행

**루트(`Australia_1st_logic/`)에서:**
```bash
uvicorn render_api:app --app-dir upharma-au --reload --port 8000
```

**`upharma-au/` 안에서 (render_api.py 와 같은 디렉토리):**
```bash
uvicorn render_api:app --reload --port 8000
```

정상 기동 화면:
```
[deps-probe] anthropic: OK
[deps-probe] openai: OK
[deps-probe] yfinance: OK
[deps-probe] reportlab: OK
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process using WatchFiles
INFO:     Started server process [XXXXX]
INFO:     Application startup complete.
```

**플래그 의미**

| 플래그 | 필요 | 설명 |
|---|---|---|
| `--app-dir upharma-au` | 루트에서 실행 시 | Python import path 에 추가 (`upharma-au/` 안에서 실행하면 생략) |
| `--reload` | 개발 시 권장 | 코드 수정 시 자동 재시작 |
| `--port 8000` | 선택 | 기본 8000, 충돌 시 다른 포트로 |
| `--host 0.0.0.0` | 외부 접속 필요 시 | 같은 네트워크 다른 기기에서 볼 때 |

### 11.4 브라우저 접속 · 종료

- 접속: http://localhost:8000 또는 http://127.0.0.1:8000
- 1공정 탭 → Hydrine 선택 → 🔍 크롤링 실행
- 종료: 터미널에서 `Ctrl + C`

### 11.5 크롤러 단건 실행 (CLI, 웹 없이)

```bash
cd upharma-au/crawler
# Git Bash / PowerShell
PRODUCT_FILTER=au-hydrine-004 python au_crawler.py
# cmd
set PRODUCT_FILTER=au-hydrine-004 && python au_crawler.py
```

### 11.6 포트 8000 충돌 확인 · 해제

```bash
# 점유 프로세스 확인
netstat -ano | findstr :8000

# 강제 종료 (PID 는 위 결과에서)
taskkill /PID <PID> /F
```

### 11.7 Render 배포 트리거 (선택)

```bash
python scripts/deploy_render.py
```

**Render — `requirements.txt` 위치:** 관례대로 **저장소 루트**(README 와 같은 위치)에만 둔다. [Monorepo 문서](https://render.com/docs/monorepo-support)에 따르면 서비스 **Root Directory** 를 `upharma-au` 로 잡으면 **그 폴더 밖 파일은 빌드에 포함되지 않아** 루트의 `requirements.txt` 를 `pip` 가 열 수 없다. 따라서 Render 대시보드 → 해당 Web Service → **Settings → Build & Deploy → Root Directory 를 비운다**(저장소 루트 사용). `buildCommand` 는 `pip install -r requirements.txt`, `startCommand` 는 `render.yaml` 처럼 `--app-dir upharma-au` 로 앱만 지정하면 된다. Python 버전은 `.python-version` 과 `render.yaml` 의 `PYTHON_VERSION` 으로 맞춘다.

### 11.8 전체 실행 흐름 요약

```
(프로젝트 루트에서) venv 활성화 — PowerShell: .\venv\Scripts\Activate.ps1
  ↓
pip install -r requirements.txt   (venv 최초 생성 직후 또는 requirements 갱신 시)
  ↓
python scripts/migrate.py                     (스키마 변경 있을 때)
  ↓
uvicorn render_api:app --app-dir upharma-au --reload --port 8000
  ↓
브라우저 → http://localhost:8000
```

---

## 12. 의존성 설치 및 오류 대응

서버는 **선택 의존성(anthropic, openai, yfinance, reportlab)이 누락되어도 기동 자체는 성공**하도록 방어 설계되어 있습니다 (§1 참조). 관련 엔드포인트만 `503` 을 반환합니다.

### 12.1 최초 · 갱신 설치

```powershell
# Windows PowerShell
python -m pip install -r requirements.txt
```

```bash
# Git Bash · macOS · Linux
python -m pip install -r requirements.txt
```

### 12.2 빠른 헬스체크

```bash
# (1) dep 직접 import 확인
python -c "import anthropic, yfinance, openai, reportlab; print('deps ok')"

# (2) 서버 띄운 뒤 /health 로 dep 상태 조회
python -c "import requests; print(requests.get('http://127.0.0.1:8000/health', timeout=10).json())"

# (3) 상세 dep 상태 (어떤 엔드포인트가 영향받는지까지)
python -c "import requests, json; print(json.dumps(requests.get('http://127.0.0.1:8000/health/deps').json(), indent=2, ensure_ascii=False))"
```

**정상 응답 예시 (`/health`):**
```json
{
  "status": "ok",
  "optional_deps": {"anthropic": true, "openai": true, "yfinance": true, "reportlab": true},
  "optional_deps_all_installed": true,
  "stage2_ok": true,
  "hint": null
}
```

**누락 응답 예시 (anthropic 미설치):**
```json
{
  "status": "ok",
  "optional_deps": {"anthropic": false, "openai": true, "yfinance": true, "reportlab": true},
  "optional_deps_all_installed": false,
  "stage2_ok": true,
  "hint": "pip install -r requirements.txt"
}
```

### 12.3 자주 발생하는 오류와 해결

#### (a) `ModuleNotFoundError: No module named 'anthropic'`
- **원인:** 서버 실행 중인 파이썬 환경에 `anthropic` 미설치
- **증상:** 서버는 기동됨. `POST /api/report/generate` 또는 `POST /api/p2/pipeline` 호출 시 **`503 AI 엔진(anthropic) 미설치`**
- **해결:**
  ```bash
  python -m pip install anthropic
  # 또는
  python -m pip install -r requirements.txt
  ```

#### (b) `[yfinance fx error] No module named 'yfinance'`
- **원인:** 환율 보조 라이브러리 미설치
- **증상:** `GET /api/exchange` 는 여전히 동작 (exchangerate-api.com 폴백 사용, `pct_change` 없음)
- **해결:**
  ```bash
  python -m pip install yfinance
  ```

#### (c) `stage2 module load failed`
- **원인:** `upharma-au/stage2/fob_calculator.py` import 실패
- **증상:** `GET /api/stage2/seeds` · `POST /api/stage2/calculate` → `503 stage2 module load failed: <err>`
- **해결:** `upharma-au/stage2/` 폴더와 `fob_calculator.py`, `fob_reference_seeds.json` 두 파일이 존재하는지 확인. `python -c "from stage2.fob_calculator import dispatch_by_pricing_case; print('ok')"` 로 로컬 확인.

#### (d) `supabase` 관련 기동 실패
- **원인:** `supabase-py` 미설치는 **필수 의존성**이라 서버 자체가 안 뜸
- **해결:** `pip install -r requirements.txt` 로 전체 재설치. 개별 설치는 `python -m pip install supabase`

#### (e) `PGRST204 Could not find column`
- **원인:** `ALTER TABLE` 후 PostgREST 스키마 캐시가 리로드되지 않음
- **해결:** `scripts/migrate.py` 는 자동 `NOTIFY pgrst, 'reload schema';` 발송. 대시보드에서 수동 DDL 을 돌렸다면 Supabase 대시보드 → Database → Reload Schema

### 12.4 권장 사항

- **프로젝트 전용 가상환경(venv/conda env) 사용** — 동일 머신의 다른 프로젝트 패키지와 충돌 방지
- `pip list | grep -iE "anthropic|supabase|fastapi|yfinance|reportlab"` 으로 현재 환경 패키지 버전을 주기적으로 확인
- **miniforge 환경**과 **venv 환경**을 섞지 마세요 — 서버를 띄운 Python 과 `pip install` 을 실행한 Python 이 달라 재현이 까다로운 버그 원인이 됩니다.

---

## 13. 운영 주의사항

### PostgREST 스키마 캐시 (PGRST204)

`ALTER TABLE` 후 supabase-py upsert 가 `Could not find column ...` 로 실패하는 경우가 있음.

- **`scripts/migrate.py` 는 SQL 실행 직후 `NOTIFY pgrst, 'reload schema';` 자동 발송** → migrate.py 를 통한 배포는 이 버그가 발생하지 않음
- 대시보드나 psql 로 DDL 을 **직접** 실행한 경우에만 주의. 수동 NOTIFY 또는 Supabase 대시보드 → Database → Reload Schema

```sql
-- migrate.py 를 거치지 않고 DDL 을 직접 돌렸을 때만 필요
NOTIFY pgrst, 'reload schema';
```

### 컬럼 화이트리스트 정합성

`supabase_insert._ALLOWED_COLUMNS` 는 upsert 시 허용 컬럼 화이트리스트. **DB 에만 있고 여기 없는 컬럼은 저장되지 않음** → `au_crawler.build_product_summary` 가 값을 생성해도 silent drop. `migrate.py` 의 §4 컬럼 검증이 이 미스매치를 즉시 감지함. 새 컬럼을 SQL 에 추가했다면 반드시 `_ALLOWED_COLUMNS` 에도 추가하고 `python scripts/migrate.py` 로 재검증할 것.

### PBS Rate Limit

`pbs.py` 는 매 API 호출 전 `time.sleep(21)`. 1개 품목당 2~4회 호출 → 최대 90초. 배치 실행 시 주의.

### 공통 6컬럼 변경 금지

`id, product_id, market_segment, fob_estimated_usd, confidence, crawled_at` — 이름·타입·기본값 변경 금지 (헌법). `fob_estimated_usd` 는 1공정 항상 NULL, 2공정에서 채움.

### 병렬화 안 하는 이유

이론적으로 `asyncio.gather` 로 ①·④·⑤ 병렬 가능 (~55초) 하지만:
- PBS 가 전체 시간 50%+ 를 차지하고 rate limit 으로 직렬 유지 필요
- 현재 `httpx` 동기 클라이언트 기반으로 안정적 동작 중
- 병렬 전환은 디버깅 복잡도 증가 → **의도적으로 순차 100초 유지**

### 캐시 금지 / 실시간성 우선

맨 위 "🎯 설계 원칙" 섹션 참고. `/api/crawl` 은 항상 외부 API 를 재호출하고 DB 를 덮어씁니다. "최근에 크롤링 돌렸으니 DB 값 재사용" 식의 캐시 분기를 넣으면 **이 시스템의 존재 의의(실시간 시장분석)가 깨집니다.**

### LLM 모델 고정 — Haiku Only

**모든 Anthropic API 호출은 `claude-haiku-4-5-20251001` 로 고정.** Sonnet/Opus 사용 절대 금지 (예외 없음). 이유: 비용 관리 + 구조적 출력 일관성. 모델 변경이 필요하면 절대 코드에 하드코딩하지 말고 env var 로 분리해서 검토.

### 선택 의존성 방어

`render_api.py` 는 기동 시 `_probe_optional_dep()` 으로 `anthropic`, `openai`, `yfinance`, `reportlab` 설치 여부를 확인하고 `_DEPS_STATUS` 에 저장합니다. 각 엔드포인트는 필요한 dep 가 없으면 `503` 을 반환하고 재설치 안내 메시지를 포함합니다 — 서버 전체가 `ModuleNotFoundError` 로 죽는 일이 없도록 설계되어 있습니다.

---

## 14. 변경 이력

변경사항은 이 섹션에만 누적 기록합니다. 별도 변경 리포트 파일을 만들지 않습니다.

### 14.0 2026-04-17 — 로컬 실행 문서 보강 (venv)

- §11 상단에 **「매 세션마다」** 블록 추가: `python -m venv` / 전체 `pip install` 은 반복 불필요, **터미널마다 activate 만** 하면 됨을 명시
- PowerShell 기준 한 줄 명령 `.\venv\Scripts\Activate.ps1` 을 표로 정리(프로젝트 루트 기준)
- §11.8 요약 다이어그램에 PowerShell 활성화 예시 한 줄 포함

### 14.0b 2026-04-17 — 복사본 크롤러 합병 + 소매가 추정 로직 백엔드 설계

**범위:** 백엔드만 — 프론트(app.js)/PDF(report_generator) 반영은 리팩토링 후 별도 사이클.

**A. 복사본(`Australia_1st_logic - claudecoworkonly/`) 크롤러 개선 메인 합병 (커밋 `3c5f015`)**

1. **`render_api.py`** — `stage2` 디렉토리 `sys.path.insert` 3줄 추가 (FOB 역산 모듈 import 안정성)
2. **`crawler/sources/pbs.py`** — `_safe_float()` · `_brand_premium_from_row()` 헬퍼 + `pbs_manufacturer` · `pbs_brand_premium` · `competitor_brands` 신규 필드. `pbs_brands` 리스트 내부에도 `pbs_dpmq` · `pbs_determined_price` · `pbs_manufacturer` · `brand_premium` 확장 (최상위 신규 3필드는 `_ALLOWED_COLUMNS` 밖이라 silently drop — 향후 FOB 포지셔닝 분석에 필요해지면 SQL ALTER)
3. **`crawler/sources/chemist.py`** — Cloudflare 우회 2단계 전략 (§4 ④ 참조). `price_source_name` 은 `"Chemist Warehouse"` 로 고정 (app.js 정확 매칭 호환, 옵션 A)
4. **`crawler/au_crawler.py`** — Healthylife 가격 fallback 통합 (§4 ⑥ 참조)

**B. 소매가 추정 로직 (1~4단계, 커밋 `c37fff2` + 후속)**

배경: Chemist Warehouse 는 호주 최저가 체인이라 일반 시장 소매가 대비 `~20%` 낮음 (CHOICE 호주 소비자 단체 조사). 그대로 `retail_price_aud` 에 저장하면 FOB 역산의 Retail 기준값이 과소평가됨.

| 단계 | 파일 | 변경 |
|---|---|---|
| 1 | `crawler/db/australia_table.sql` · `crawler/db/supabase_insert.py` | 신규 2컬럼 `chemist_price_aud` · `retail_estimation_method`. `_ALLOWED_COLUMNS` 화이트리스트 동기화. `australia` 테이블 컬럼 수 73 → 75 |
| 2 | `crawler/au_crawler.py` | `_estimate_retail_price()` 헬퍼 + `RETAIL_MARKUP_MULTIPLIER` 환경변수 (기본 `1.20`). 3분기 우선순위: PBS DPMQ → Chemist × 1.20 → pbs_price_aud fallback. 반환 dict 2필드 추가 |
| 3 | `stage2/fob_calculator.py` | `dispatch_by_pricing_case(seed, *, crawler_row=None)` 확장. Logic B 참고가 2순위 fallback (seed → crawler). `inputs.retail_source` 로 출처 구분 (`"seed"` / `"crawler"`). Logic A 및 하위호환 유지 |
| 4 | `render_api.py` | `_p2_pipeline_worker()` Step 3 에서 `crawler_row=row` 전달. Step 5 `ref_text` 조립에 crawler 분기 추가 (`PBS DPMQ` / `Chemist × 1.20 (CHOICE 조사 기준)` / `크롤러 실시간`) |

**환경변수 (선택):** `RETAIL_MARKUP_MULTIPLIER=1.20` — `copy.env` 참고.

**스키마 배포:** Supabase SQL Editor 에 `crawler/db/australia_table.sql` 하단 ALTER 블록 재실행 (`IF NOT EXISTS` 로 멱등).

**보류 항목:**
- 5단계 PDF/UI 반영 — 프론트 리팩토링 사이클로 미룸
- 복합 성분 DPMQ 합산 (`_merge_pbs_rows`) — 별도 과제
- `pbs_manufacturer`/`pbs_brand_premium`/`competitor_brands` 컬럼을 실제로 Supabase 에 저장 — FOB 포지셔닝 분석 도입 시 SQL ALTER + `_ALLOWED_COLUMNS` 추가

### 14.0c 2026-04-18 — 프론트엔드 UI/UX 싱가포르 통일 이식 (Stage 1~5)

**배경**: 팀원 싱가포르 공통 템플릿(`Desktop/frontend_0417/`) 의 UI/UX 를 호주 프로젝트에 이식. 내용(데이터·API·백엔드) 은 호주 그대로, 화면 구조·레이아웃·컴포넌트는 싱가포르와 100% 통일.

**범위 원칙**: 백엔드 파일 (`render_api.py`, `crawler/*`, `stage2/*`, Supabase 스키마) **0 수정**. 모든 변경은 `templates/index.html` + `static/styles.css` + `static/app.js` 3개 파일에만 한정.

#### Stage 0 — 결정 확정 (5개)

| # | 결정 | 결과 |
|---|---|---|
| Q1 | 매크로 지표 4카드 데이터 소스 | HTML 하드코딩 (IMF·ABS·BMI·IMF 수치) |
| Q2 | API 키 배지 섹션 (/api/keys/status 호주 미지원) | 섹션 삭제 |
| Q3 | 신약 분석 폼 (/api/pipeline/custom 호주 미지원) | 섹션 삭제 (8 품목 고정) |
| Q4 | 호주 전용 기능 A/B/C/D/E/F 유지 여부 | A(A4 미리보기)·C(체크리스트)·D(크롤 스택) 삭제 / B(환율 상세)·E(직접입력 탭)·F(GST 자동 전환) 유지 |
| Q5 | 로고 | 호주 `logo_1.png` / `logo_2.png` (fallback) 유지 |

추가로 UI 표시 정책: 모든 가격 **USD 메인 + ≈ KRW 보조** (호주 AUD 는 프론트 파생 환산 — 백엔드 응답 키는 AUD 그대로 유지).

#### Stage 1 — `static/styles.css` 신규 블록 append (+479 줄)

공통 클래스는 건드리지 않고 frontend_0417 전용 블록만 파일 하단에 append. 호주 전용 클래스(a4-*, cc-*, crawl-*) 보존.

- `:root` 1공정 호환 변수 5개 (`--ok`, `--ok-bg`, `--err`, `--err-bg`, `--warn-bg`)
- `.flag-emoji` 국기 이모지
- API 키 배지 5개 (Stage 0 Q2 에서 HTML 은 제거했지만 CSS 는 향후 재활성화 대비 유지)
- `.entry-pathway`, `.btn-download`, `.btn-report-del`
- 매크로 지표 4카드 (`.macro-grid`, `.macro-card`, `.macro-label`, `.macro-value`, `.macro-source`)
- 지도+뉴스 그리드 + Leaflet (`.map-news-grid`, `.map-card-wrap`, `.sg-leaflet-map`, `.map-ocean/land/grid/gl`)
- `.p1-result-note` 1공정 완료 노트
- P2 3열 시나리오 카드 (`.p2-three-col`, `.p2-col`, `.p2-col--agg/avg/cons`, `.p2-col-rank-row`, `.p2-col-price`, `.p2-col-currency`, `.p2-col-sub`, `.p2-col-detail`, `.p2-col-input-row`, `.p2-col-input-label`, `.p2-col-input`, `.p2-col-foot-note`, `.p2-dist-*`)
- P2 컬럼 커스텀 옵션 (`.p2-custom-opts`, `.p2c-opt-row`, `.p2c-opt-name`, `.p2c-opt-type-label`, `.p2c-opt-val`, `.p2c-opt-del`, `.p2c-add-row`, `.p2c-opt-name-input`, `.p2c-opt-type-select`, `.p2c-confirm-btn`, `.p2c-add-btn`)
- 반응형 보강 (`.macro-grid`, `.map-news-grid`, `.p2-three-col` breakpoint)

결과: `styles.css` 1,350 → **1,829 줄**.

#### Stage 2 — `templates/index.html` 전면 교체 (+77 줄)

호주 5 탭 구조 → 싱가포르 **1 페이지 아코디언** + 보고서 탭 2탭 체계로 교체. 호주 컨텍스트 치환 + Stage 0 결정 반영.

**치환 내역**:
- 🇸🇬 Singapore → 🇦🇺 Australia, sg-map → au-map, [1.3521, 103.8198] → [-33.8688, 151.2093] (시드니)
- product_id 8개: SG_hydrine_hydroxyurea_500 등 → au-hydrine-004 / au-omethyl-001 / au-gadvoa-002 / au-sereterol-003 / au-rosumeg-005 / au-atmeg-006 / au-ciloduo-007 / au-gastiin-008
- 규제 기관: HSA/NDF/MOH/GeBIZ/ALPS → TGA/PBS/NSW Health/HealthShare/Chemist Warehouse
- SGD → USD (프론트 표시만. 백엔드 응답 AUD 유지)
- 시나리오 라벨: 싱가포르 "공격적/평균/보수적" → 호주 확정본 "저가 진입 (Penetration) / 기준가 기반 (Reference) / 프리미엄 (Premium)" (CLAUDE.md §2공정 절대 규칙)

**삭제 섹션**: A4 보고서 미리보기(rptA4·rptBlocks 등), 신약 분석 폼(m_name·m_inn 등), 크롤링 결과 스택, 체크리스트(todoList), 5탭 구조, API 키 배지

**유지된 호주 전용**: 환율 상세 카드(fx-main·fx-usd-aud·fx-aud-krw, JPY/CNY 제거 · USD/KRW 메인 + USD/AUD·AUD/KRW 2페어), 2공정 직접입력 서브탭(p2-manual-*)

**Leaflet 지도 추가**: CDN 2개 + 시드니 좌표 인라인 초기화 스크립트

결과: `index.html` 463 → **540 줄**.

#### Stage 3 — `static/app.js` 싱가포르 베이스 이식 + 호주 API 매핑 + 어댑터 (1,771 → 1,862 줄, 5체크리스트)

Stage 3 은 5단계로 진행:

**[1] `loadExchange()` 스키마 치환**
- 싱가포르 DOM(`exchange-main-rate`, `exchange-sub`, `exchange-source`) → 호주 DOM(`fx-main`, `fx-usd-aud`, `fx-aud-krw`, `fx-chg`, `fxTimestamp`)
- 응답 키: `sgd_krw/sgd_usd/sgd_jpy/sgd_cny` → `aud_krw/aud_usd/aud_jpy/aud_cny`
- USD 파생: `usd_krw = aud_krw / aud_usd`, `usd_aud = 1 / aud_usd`
- `pct_change` 는 AUD/KRW 기준 → 라벨 "AUD/KRW 전일" 명시해 오해 방지
- `window._exchangeRates` 전역에 호주 원본 + 파생 2개 동시 저장 (2공정 재사용)

**[2] 파이프라인 호출 전환** (싱가포르 비동기 폴링 → 호주 동기 2단 + /api/data 조회)
- `POST /api/pipeline/{key}` → `POST /api/crawl {product_id}` (동기 블로킹)
- `GET /status` 폴링 루프 **완전 제거** (호주는 동기라 불필요)
- `GET /result` → `POST /api/report/generate` + `GET /api/data/{product_id}` (row 조회 추가)
- `setProgress()` 4단계를 await 2회 경계에서 수동 업데이트 (db_load → analyze → refs·report done)
- `pollPipeline()` 함수 삭제

**[3] 싱가포르 잔재 텍스트 치환** (7 곳 핵심)
- 파일 헤더 주석: "싱가포르 대시보드" → "호주 대시보드 (싱가포르 원본 베이스 이식)"
- TODO_LS_KEY / REPORTS_LS_KEY: `sg_upharma_*` → `au_upharma_*`
- typeLabel / option / unit: SGD → USD
- GST 옵션 hint: "싱가포르 GST 9% 고정" → "호주 GST — 처방약 0% · OTC 10% (Stage 4 `_p2ClassifyGst` 복원 예정)"

**[4] 추가 청소**
- 1237 줄 "/" 오타 — 확인 결과 오타 없음 (Stage 2 파일 전체 교체로 해결됨)
- LS 키 prefix `sg_` → `au_` 완료 (2 곳)

**[5] 응답 구조 어댑터** (핵심)
- 1공정 어댑터 `_auToRenderResult(auRow, blocks, meta)` 신규:
  - 호주 3 응답 (australia row 73~75 컬럼 + Haiku blocks 10개 + meta) → 싱가포르 `renderResult()` shape 재포장
  - 호주 원본 `_au_raw / _au_blocks / _au_meta` 로 전량 보존 (데이터 안 버림)
  - export_viable 영어 → 한국어 판정 매핑 (viable → 적합 등)
  - PBS 한 줄 요약 자동 생성 (PBS 등재 여부 + DPMQ + retail 추정 방법)
- 2공정 렌더러 `_renderP2AiResult()` 내부 재작성:
  - 호주 응답 `extracted.ref_price_aud / analysis.final_price_aud / scenarios[].price_aud / exchange_rates.aud_*` 직접 사용
  - AUD → USD/KRW 파생 유틸 (`audToUsd`, `audToKrw`, `fmtUSD`, `fmtKRW`) 로컬 헬퍼
  - 최종가·시나리오 카드: USD 메인 + ≈ KRW 보조 (1억/1만원 단위 자동 포맷)
  - `_p2ScenarioRaw` 에 USD 값 + 원본 AUD 동시 보존 (`agg_aud/avg_aud/cons_aud` 신규 키)

#### Stage 4 — 호주 전용 기능 복원 (1,862 → 1,940 줄, SGD 잔재 0)

**Sereterol 강도 분리**: INN_MAP + HTML 드롭다운에 "(250/50·500/50 DPI)" 병기. product_id 는 단일 유지 (seed 에서 aemp [27.07, 36.65] 배열 처리).

**Mosapride CR 한국어 병기**: "(서방형)" 추가.

**GST 품목별 자동 전환** (신규 함수 2개):
- `_p2ClassifyGst(productId)` — 호주 8 품목 중 Omethyl(OTC) 만 10%, 나머지 7 처방약 0%
- `_p2ApplyGstForReport(report)` — 보고서 onchange 시 `_p2Manual.private.gst.value` 자동 갱신 + hint 문구 동적
- `initP2Strategy()` 에 이벤트 리스너 연결 (AI 탭 + 직접입력 탭 모두)

**2공정 직접입력 탭 전면 재설계** (SGD 18 곳 → USD 전부):
- `_makeP2Defaults()` 옵션 배열 재설계 — 키 이름 호주 맥락 통일: `retail/partner/distribution` → `pharmacy/wholesale/importer` (fob_calculator.py DEFAULT_* 상수와 정확 일치)
- 기본값: pharmacy 30%, wholesale 10%, importer 20%, gst 0% (처방 초기), exchange 0.65 (AUD→USD)
- `_calcP2Manual()` 공식 재작성 — 싱가포르 차감 방식 `× (1-m%)` → 호주 나눗셈 방식 `÷ (1+m%)` (fob_calculator Logic B 공식과 일치)
- GST 하드코딩 `÷ 1.09` → 동적 `÷ (1 + gstRate/100)` (0% 또는 10%)
- 모든 formulaStr: `KUP SGD` → `FOB USD`
- `_extractSgdHint` → `_extractPriceHint` 개명 + 정규식 AUD/USD/$ 우선순위

#### Stage 5 — 통합 검증 (이번 세션 · 백엔드 0 영향 확인)

**프론트 fetch 10 엔드포인트 ↔ 호주 render_api.py 엔드포인트 매칭 표**:

| 프론트 fetch | render_api.py 위치 | 응답 매칭 |
|---|---|---|
| `GET /api/exchange` | 358 줄 | `{aud_krw, aud_usd, aud_jpy, aud_cny, updated, pct_change?, ok?}` ✓ |
| `GET /api/news` | 274 줄 | `{items: [{title, source, date, link}]}` ✓ |
| `POST /api/crawl` | 130 줄 | `{ok, product_id, exit_code}` ✓ |
| `POST /api/report/generate` | 1194 줄 | `{ok, product_id, blocks, refs_count, refs, meta, pdf}` ✓ |
| `GET /api/data/{pid}` | 162 줄 | australia row 전체 (73~75 컬럼) ✓ |
| `POST /api/p2/upload` | 1651 줄 | `{ok, filename, size_bytes}` ✓ |
| `POST /api/p2/pipeline` | 2017 줄 | `{status: 'started'}` ✓ |
| `GET /api/p2/pipeline/status` | 2007 줄 | `{status, step_label}` ✓ |
| `GET /api/p2/pipeline/result` | 2073 줄 | `{extracted, analysis, exchange_rates, pdf}` ✓ |
| `POST /api/p2/report` | 2093 줄 | `{ok, pdf}` ✓ |

**호주 백엔드 0 수정 확인**:
- `render_api.py`, `crawler/*`, `stage2/*`, `Supabase 스키마`, `report_generator.py`, `au_products.json`, `fob_reference_seeds.json`, `render.yaml`, `requirements.txt`, GitHub Actions 모두 이번 이식 사이클에서 변경 없음

**데이터 보존 확인**:
- 호주 `australia` 73~75 컬럼 전체가 어댑터에서 접근 가능 (`_au_raw` 로 원본 보존)
- 호주 백엔드 응답 키 (`aud_krw`, `aud_usd`, `ref_price_aud`, `final_price_aud`, `scenarios[].price_aud`) 그대로 수용 → 프론트에서 파생 환산만
- CLAUDE.md 확정 시나리오 라벨 ("저가 진입/기준가 기반/프리미엄") 적용

#### 최종 파일 상태 (2026-04-18 05:42)

```
static/styles.css       1,350 →  1,829 줄 (+ 479, frontend_0417 신규 블록 append)
templates/index.html      463 →    540 줄 (+ 77,  싱가포르 1페이지 아코디언 + 호주 전용 유지)
static/app.js           1,771 →  1,940 줄 (+ 169, 싱가포르 베이스 이식 후 호주화 · 어댑터 · Stage 4 복원 순 변동)
```

#### 이번 이식 사이클에 건드리지 않은 것

- 호주 백엔드 파일 일체
- 호주 Supabase 스키마
- 호주 `au_products.json` / `fob_reference_seeds.json`
- 호주 `crawler/db/supabase_insert.py` `_ALLOWED_COLUMNS`
- GitHub Actions · Render 배포 설정
- `report_generator.py` PDF 렌더러

### 14.1 2026-04-16 — 2공정(P2) UI/백엔드 전면 투입

작업 일자 전부 2026-04-16 (목).

| 시각 | 버전 | 주제 |
|---|---|---|
| 19:35 KST | v1.0 | 팀원 싱가포르 2공정 UI 원본 이식 (templates/index.html, static/styles.css, static/app.js) |
| 20:00 KST | v2.0 | 호주화 — SGD→AUD (23회), GST 9%→10%, ALPS→PBS/Chemist Warehouse/HealthShare, API 응답 키 `_sgd`→`_aud` |
| 20:30 KST | v2.1 | GST 정밀화 — 메인 화면 GST 2줄 분리 (Rx 0% / OTC 10%), 품목별 자동 전환 (`_p2ClassifyGst` + `_p2ApplyGstForReport`), `_calcP2Manual` 동적 rate |
| 21:00 KST | v2.2 | 직접 입력 탭 파일 업로드 UI 통일, `/api/p2/upload` + `/api/p2/pipeline/status` 엔드포인트 실구현 |
| 21:30 KST | v2.3 | 백업 폴더 → 작업 폴더 Stage2 FOB 백엔드 이식 (`/api/stage2/seeds`, `/api/stage2/calculate`), 백업 원본 대비 한국어 규제 용어 병기 추가 |
| 22:00 KST | v2.4 | 선택 의존성 방어 시스템 추가 — `_probe_optional_dep`, `/health/deps`, `/api/report/generate` + `/api/p2/pipeline` 503 가드, 기동 로그 `[deps-probe]`. README 전면 리팩터(P2 리포트 통합) |
| 23:00 KST | v2.5 | README 문서 정합성 보강 — 누락 모듈 2종 반영: `crawler/sources/healthylife.py` (독립 유틸리티, Omethyl Private 참고가용) + `upharma-au/report_generator.py` (1공정 PDF 생성기 363줄). §4 ⑥ Healthylife 서브섹션 + Jina Reader 사용 전략 공통 정리 추가. §2 폴더 구조 업데이트 |

### 14.2 팀원 원본 (싱가포르) → 호주화 치환 상세 (v2.0)

| 영역 | 이전(싱가포르) | 이후(호주) |
|---|---|---|
| 통화 | `SGD` (23회) | `AUD` |
| GST | 9% 고정 (`÷1.09`) | 10% (OTC) / 면제 (Rx) 동적 |
| 공공조달 | `ALPS 조달청 · 27개 공공기관` | `PBS 공공급여 · HealthShare NSW 병원조달` |
| 민간유통 | 병원·약국·체인 일반 | Chemist Warehouse 중심 |
| API 응답 키 | `ref_price_sgd`, `final_price_sgd`, `scenarios[].price_sgd`, `rates.sgd_krw`, `rates.sgd_usd` | `ref_price_aud`, `final_price_aud`, `price_aud`, `aud_krw`, `aud_usd` |
| 함수명 | `_extractSgdHint` | `_extractAudHint` |
| 가격 정규식 | `/SGD\s*([0-9]+)/` | `/(?:AUD\|A\$\|\$)\s*([0-9]+)/` (Chemist `$` 표기 지원) |

### 14.3 GST 자동 전환 로직 (v2.1)

`static/app.js` 에 두 헬퍼 함수 추가:

```js
function _p2ClassifyGst(report) {
  // Omethyl / omega-3 / 오메가 / omacor 키워드 매칭 시 OTC(10%), 그 외 전부 Rx(0%, GST-free)
  const src = String(report?.report_title || report?.product || report || '');
  const isOtc = /omethyl|omega\s*-?\s*3|오메가|omacor/i.test(src);
  return isOtc
    ? { rate: 10, kind: 'otc', label: 'GST 공제 (÷1.10) · OTC 10%', hint: '호주 GST 10% (Omega-3 건강기능식품은 과세)' }
    : { rate: 0,  kind: 'rx',  label: 'GST 공제 (면제) · 처방약 0%', hint: '호주 처방약(S4/S8)은 GST-free — 공제 없음' };
}

function _p2ApplyGstForReport(report) {
  // _p2Manual.private의 'gst' 항목을 classify 결과에 맞게 value/min/max/label/hint/enabled 일괄 갱신
}
```

- `_p2FillBaseFromReport` → 가격 힌트 채운 직후 `_p2ApplyGstForReport(report)` 호출
- `_calcP2Manual` 의 `gst_fixed` 분기 → 하드코딩 `÷1.10` 제거, `rate>0` 이면 `÷(1+rate/100)` 동적 계산, `rate==0` 이면 공제 생략 후 `'GST 면제 (처방약)'` 표기

### 14.4 Stage2 FOB 백엔드 이식 (v2.3)

**이식 범위 — `upharma-au/render_api.py`:**

| 블록 | 내용 |
|---|---|
| Stage2 섹션 주석·import | `stage2.fob_calculator` 에서 `DEFAULT_FX_AUD_TO_KRW`, `calculate_fob_logic_a/b`, `calculate_three_scenarios`, `dispatch_by_pricing_case`, `get_disclaimer_text` 지연 import (`_STAGE2_OK` 플래그) |
| 경로 상수 | `_STAGE2_SEEDS_PATH`, `_AU_PRODUCTS_PATH` |
| 헬퍼 함수 | `_load_stage2_seeds()`, `_load_au_products_meta()`, `_seed_by_id()`, `_scenarios_dict_to_list()` |
| `GET /api/stage2/seeds` | 8품목 시드 목록 — UI 드롭다운용 컴팩트 필드 |
| `POST /api/stage2/calculate` | Manual 탭 계산 — Logic A/B × 3 시나리오, withdrawal 차단, PBAC/hospital/confidence 경고 |
| `POST /api/p2/pipeline` (stub) | AI 파이프라인 실행 — Haiku 미설치 시 503, 설치 시 501 |
| `GET /api/p2/pipeline/result` (stub) | 501 |
| `POST /api/p2/report` (stub) | 501 |

**백업 원본 대비 호주화 보강:**
- 경고 문구에 한국어 괄호 설명 병기: `PBAC(호주 의약품급여자문위원회)`, `Hospital tender(병원 공급 입찰)`, `Section 19A(일시수입 특례)`, `PBS Restricted Benefit(처방 적응증 제한)`
- `raise ... from e` 예외 체이닝 명시 (PEP 3134)

**검증 결과 (TestClient):**
```
GET  /api/stage2/seeds              → 200, count=8
POST /api/stage2/calculate (Hydrine · Logic A · AEMP $31.92 · margin 20%)
  → 200, 3 시나리오 (FOB AUD 29.02/26.60/24.55, KRW ₩26,116/₩23,940/₩22,098)
POST /api/stage2/calculate (Omethyl · Logic B · retail $48.95)
  → 200, 3 시나리오 (FOB AUD 27.06/25.93/23.94)
POST /api/stage2/calculate (Ciloduo · withdrawal)
  → 200, logic=blocked, scenarios=[], blocked_reason=commercial_withdrawal ✓
POST /api/p2/pipeline                → 501 (stub, anthropic OK 환경에서)
```

### 14.5 선택 의존성 방어 시스템 (v2.4)

**목적:** 하나의 선택 dep(특히 `anthropic`) 가 빠져서 서버 전체가 `ModuleNotFoundError` 로 죽는 사고를 차단. Stage2·1공정 크롤러 엔드포인트는 Haiku 없이도 계속 동작해야 함.

**구현 (`upharma-au/render_api.py`):**
- `_probe_optional_dep(modname)` — 모듈 import 시도 후 `(ok, err_msg)` 반환
- 4개 dep 를 기동 시 프로브: `anthropic`, `openai`, `yfinance`, `reportlab`
- `_DEPS_STATUS` 딕셔너리에 `{ok, required_by[], error}` 저장
- stdout 에 `[deps-probe] <name>: OK | MISSING — <error>` 로그
- `GET /health` 에 `optional_deps` 필드 + `hint` (pip 명령) 추가
- `GET /health/deps` 신규 — 상세 의존성 상태
- `POST /api/report/generate` 진입 시 `_ANTHROPIC_AVAILABLE` 가드 → 503 + 명확한 메시지
- `POST /api/p2/pipeline` 스텁에도 동일 가드 (실구현 전 미리 방어)

**검증 결과 (anthropic 미설치 샌드박스에서):**
```
[deps-probe] anthropic: MISSING — ModuleNotFoundError: No module named 'anthropic'
[deps-probe] openai: OK
[deps-probe] yfinance: MISSING — ModuleNotFoundError: No module named 'yfinance'
[deps-probe] reportlab: OK

GET /health              → 200, optional_deps_all_installed=false, hint="pip install -r ..."
GET /api/stage2/seeds    → 200, count=8        ← anthropic 없어도 정상 동작 ✓
POST /api/p2/pipeline    → 503, detail="AI 엔진(anthropic) 미설치 ..."
```

### 14.6 문서 정합성 보강 (v2.5)

작업 폴더 전체를 스캔해 README 에 누락된 파일 2종을 발견·반영:

| 파일 | 라인 | 역할 | 이전 상태 |
|---|---|---|---|
| `upharma-au/crawler/sources/healthylife.py` | 223 | PBS 미등재 Private 처방약 (Omethyl 등) 소매가 크롤러. Next.js API → HTML → Jina Reader 3단 폴백 | §2·§4 모두 미언급 |
| `upharma-au/report_generator.py` | 363 | 1공정 PDF 생성기 (reportlab). 한글 폰트 자동 탐색, 2페이지 레이아웃, `render_pdf(row, blocks, refs, meta)` | §2 미언급, §7.2 에서 "PDF 저장" 한 단어로만 처리 |

**반영 위치:**
- §1 개요 — "4개 소스" 문장 뒤에 healthylife 독립 유틸리티 각주
- §2 폴더 구조 — 두 파일 추가
- §4 ⑥ — Healthylife 신규 서브섹션 (3단 폴백 · Cloudflare 감지 · 반환 dict · CLI 실행법). **`au_crawler.py` 에 통합되지 않은 독립 모듈임을 명시** — `from sources.healthylife import ...` 가 `au_crawler.py` 에 없음을 grep 으로 확인
- §4 "Jina Reader 사용 전략" 블록 신규 — 5개 소스의 Jina 사용 이유 · 트레이드오프 · 폴백 정책 한 곳 정리
- §7.2 — `report_generator.py` 모듈 레벨 설명 추가 (입력 4종, 한글 폰트 폴백, PDF 레이아웃, 의존성 조건)

**누락 발견 경위:**
- 사용자 요청 "리드미 MD에 크롤링 로직도 잘 구현해놨어?" 에 대응해 `find . -name "*.py"` 전체 스캔
- 기존 README 는 메인 파이프라인 5단계 (TGA/PBS×2/Chemist/buy.nsw) 만 문서화 — healthylife 와 report_generator 는 이 흐름 밖에 있어 누락된 상태였음
- `.claude/settings.json` 등 에이전트 설정 파일은 의도적 생략

### 14.7 원본 UI/UX 작업본에 반영 필요 항목 (누적)

`Australia_1st_logic` 폴더는 **작업 복사본**입니다. 원본 UI/UX 작업본에 아래 변경사항을 순차 반영해야 합니다:

- `templates/index.html`
  - 2공정 섹션 178줄 (팀원 원본 이식)
  - 메인 화면 GST 2줄 분리 (Rx 0% · OTC 10%)
  - 직접 입력 탭 업로드 블록 HTML (`p2-manual-upload-area`, `p2-manual-pdf-file` 등)
- `static/styles.css`
  - 파일 끝 2공정 전용 CSS 블록 418줄 (`.p2-*` 96개 클래스)
- `static/app.js`
  - 호환 shim (`_escHtml`→`_escapeHtml`, `_loadReports`→`reportStore`, `_setText` 신규)
  - 팀원 p2 JS 원본 블록 666줄 (함수 25개 · `_extractAudHint` 리네임 포함)
  - v2.1: `_p2ClassifyGst`, `_p2ApplyGstForReport` 함수 + `_p2FillBaseFromReport`/`_calcP2Manual`/`_p2OptionCardHtml` 3곳 수정
  - v2.2: `handleP2ManualFileSelect` 함수 + `_p2ManualUploadedFilename` 상태 + 보고서 select 이벤트 변경
- `upharma-au/render_api.py`
  - v2.2: `/api/p2/upload` + `/api/p2/pipeline/status` 엔드포인트
  - v2.3: Stage2 섹션 전체 (import/helpers/`/api/stage2/seeds`/`/api/stage2/calculate`) + p2 stub 3종
  - v2.4: `_probe_optional_dep` + `_DEPS_STATUS` + `/health/deps` + 503 가드

---

## 15. 진행 상황 (2026-04-16 기준)

### ✅ 완료
- Phase 1 — SQL 스키마 · 5테이블 73컬럼 · Supabase 배포
- Phase 2 — render_api.py 1공정 엔드포인트 · v3 UI 연동
- next-app/ 레거시 제거 (300MB 삭제)
- `scripts/migrate.py` · `scripts/deploy_render.py` 자동화
- `au-hydrine-004` 실크롤링 → Supabase upsert 검증 (confidence 0.81)
- 2공정 UI 이식 (v1.0~v2.2) — 싱가포르 원본 호주화 + GST 자동 전환 + 업로드 UI 통일
- 2공정 Stage2 FOB 백엔드 (`/api/stage2/seeds`, `/api/stage2/calculate`)
- 선택 의존성 방어 시스템 (`/health/deps`)

### 🟡 진행 중 / 대기
- 나머지 7개 품목 크롤링 실행
- 로컬 uvicorn + 브라우저 UI 검증 (배포 URL 확인은 2공정 마무리 후)
- 2공정 AI 파이프라인 실구현 — Haiku 기반 `POST /api/p2/pipeline` (업로드 PDF → Haiku 추출 → fob_calculator → 결과 JSON)
- 2공정 PDF 생성 실구현 (`POST /api/p2/report`)
- 2공정 Supabase 저장 스키마 설계 (계산 결과 이력 테이블)
- 3공정 바이어 수집 + PSI 스코어링
- 1공정 LLM 블록(`/api/report/generate`) 실환경 검증 — Claude Haiku Block 2/3 + Perplexity 레퍼런스
