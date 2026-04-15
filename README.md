# UPharma Export AI · Australia (1공정)

한국유나이티드제약(주)의 **호주 수출 시장조사 자동화** 파이프라인입니다.
`au_products.json`에 정의된 8개 품목에 대해 **TGA ARTG · PBS · Chemist Warehouse · buy.nsw.gov.au** 4개 소스를 **순차 크롤링**하여 **Supabase `australia` 테이블**에 `product_id` 기준 upsert합니다. **FastAPI(`render_api.py`) + Jinja2 템플릿 + Vanilla JS** 프론트엔드로 크롤링 실행 / 조회 / 보고서 UI를 제공합니다.

> 프론트엔드는 **Next.js 가 아닙니다.** 초기 설계에 있던 `next-app/` 폴더는 2026-04 정리되어 삭제되었고, 현재는 Python 서버가 `templates/index.html` 을 직접 서빙합니다.

---

## 🎯 설계 원칙 — 실시간성 우선

> 이 시스템의 목적은 **실시간 시장분석을 위한 데이터 수집**입니다. 항상 최신 데이터를 보장해야 합니다.

- **`POST /api/crawl` 은 무조건 재크롤링**합니다. "DB에 이미 있으니 스킵" 같은 캐시 로직은 없습니다.
- TGA ARTG · PBS API · Chemist Warehouse · buy.nsw 4곳을 매 호출마다 실시간 재조회하고 Supabase `australia` 테이블을 최신 값으로 덮어씁니다 (`upsert on_conflict=product_id`).
- 버튼 한 번에 ~100초 소요되지만, 이는 **의도적 트레이드오프** — 실시간성을 속도보다 우선합니다.
- **캐시 레이어를 추가하지 마세요.** 속도 최적화가 필요하면 병렬화(asyncio)·rate limit 완화·소스별 타임아웃 조정 방향으로 접근하고, "DB에 있으면 스킵" 구조는 금지합니다. 이 원칙은 이후 개발자가 성능 최적화 한답시고 잘못된 방향으로 가는 것을 방지하기 위함입니다.

---

## 1. 기술 스택

### 런타임 · 언어

| 구분 | 버전 · 비고 |
|---|---|
| Python | 3.11 (Render) / 3.12 (GitHub Actions) |
| 프론트 | Vanilla HTML/CSS/JS (프레임워크 없음) |

### 웹 서버

| 패키지 | 용도 |
|---|---|
| **FastAPI** | `render_api.py` — 크롤러를 import 만 해서 재사용하는 얇은 어댑터 |
| **uvicorn[standard]** | ASGI 서버 (Render startCommand) |
| **jinja2** | `templates/index.html` 서빙 |

### 크롤러 (Python)

| 패키지 | 용도 |
|---|---|
| **httpx** | 동기 HTTP 클라이언트 — PBS API, Jina Reader 프록시, Supabase Management API |
| **selectolax** | HTML DOM 파싱 (현재는 대부분 Jina Reader 마크다운 우회) |
| **trafilatura** | 본문 추출 백업 경로 |
| **supabase-py** | `create_client` + `table("australia").upsert(on_conflict="product_id")` |
| **python-dotenv** | 상위 폴더 `.env` 자동 탐색 (`override=False`) |
| **tenacity** | 재시도 로직 |
| **openai** | `utils/evidence.py` 에서 `gpt-4o-mini` 로 영→한 번역 (키 없으면 원문 유지) |

### 외부 서비스 · API

| 서비스 | 인증 | 역할 |
|---|---|---|
| **Supabase (PostgreSQL)** | `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (sb_secret_*) | 데이터 저장 (PostgREST) |
| **Supabase Management API** | `SUPABASE_ACCESS_TOKEN` (PAT, sbp_*) | 스키마 DDL 실행 (`scripts/migrate.py`) |
| **PBS API v3** | `PBS_SUBSCRIPTION_KEY` | `data-api.health.gov.au/pbs/api/v3/items` |
| **Jina Reader** | 불필요 | `r.jina.ai/{url}` — SPA·Cloudflare 우회용 마크다운 프록시 |
| **PubChem REST** | 불필요 | INN 정규화 (`hydroxyurea` → `hydroxycarbamide`) |
| **SerpAPI** | `SERPAPI_KEY` (선택) | `/api/news` (없으면 mock) |
| **exchangerate-api.com** | 불필요 | `/api/exchange` (실패 시 fallback) |
| **Render.com** | `RENDER_API_KEY` + `RENDER_SERVICE_ID` | 배포 트리거 (`scripts/deploy_render.py`) |

---

## 2. 폴더 구조 (next-app 제거 후)

```
Australia_1st_logic/
├── .env                                      ← 모든 환경 변수 (git 제외)
├── .gitignore / .gitattributes
├── README.md                                 ← 이 파일
├── render.yaml                               ← Render Web Service 배포 스펙
├── upharma_demo_v3.html                      ← 디자인 레퍼런스 원본 (참고용, 분리된 templates/static 이 실제 사용)
│
├── .github/workflows/
│   └── au_crawl.yml                          ← GitHub Actions workflow_dispatch (Render 백업 실행 경로)
│
├── scripts/
│   ├── migrate.py                            ← Supabase 스키마 배포 (Management API)
│   └── deploy_render.py                      ← Render 배포 트리거
│
└── upharma-au/
    ├── requirements.txt
    ├── render_api.py                         ★ FastAPI 어댑터 (엔드포인트 9개)
    │
    ├── templates/index.html                  ← v3 UI (Jinja 템플릿, 단일 HTML)
    │
    ├── static/
    │   ├── styles.css                        ← 분리된 CSS
    │   └── app.js                            ← 프론트 로직 + /api/* fetch
    │
    └── crawler/                              ← 백엔드 크롤링
        ├── au_crawler.py                     ← 메인 파이프라인 (main → run_crawler 로 import)
        ├── au_products.json                  ← 8품목 마스터
        ├── sources/
        │   ├── tga.py                        ← TGA ARTG + 상세 파싱
        │   ├── pbs.py                        ← PBS API v3 + 웹 보강
        │   ├── chemist.py                    ← Chemist Warehouse (Jina) + build_sites
        │   └── buynsw.py                     ← buy.nsw.gov.au notices (Jina)
        ├── utils/
        │   ├── inn_normalize.py              ← PubChem 정규화
        │   ├── scoring.py                    ← completeness_score, AU_REQUIRED_FIELDS
        │   ├── evidence.py                   ← build_evidence_text (영/한)
        │   └── enums.py
        └── db/
            ├── australia_table.sql           ← 5테이블 DDL
            ├── supabase_insert.py            ← upsert_product, _ALLOWED_COLUMNS
            └── __init__.py
```

---

## 3. 클라이언트가 요청한 8개 품목

| product_id | 품목명 | INN | 함량 · 제형 | HS 코드 |
|---|---|---|---|---|
| `au-omethyl-001` | Omethyl Cutielet | omega-3-acid ethyl esters | 2g · Pouch | 300490 |
| `au-gadvoa-002` | Gadvoa Inj. | gadobutrol | 604.72mg · 주사 | 300640 |
| `au-sereterol-003` | Sereterol Activair | fluticasone + salmeterol | 복합 · Inhaler | 300460 |
| `au-hydrine-004` | Hydrine | hydroxyurea | 500mg · Cap | 300490 |
| `au-rosumeg-005` | Rosumeg Combigel | rosuvastatin + omega-3 | 복합 · Cap | 300490 |
| `au-atmeg-006` | Atmeg Combigel | atorvastatin + omega-3 | 복합 · Cap | 300490 |
| `au-ciloduo-007` | Ciloduo | cilostazol + rosuvastatin | 복합 · Tab | 300490 |
| `au-gastiin-008` | Gastiin CR | mosapride citrate | 15mg · Tab | 300490 |

---

## 4. 크롤링 순서 · 각 단계의 반환값

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
build_product_summary()  →  73컬럼 summary
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
| `pbs_price_aud` | **결정가** (determined_price) — 제조사가 정부에 공급하는 가격 | `31.92` |
| `pbs_determined_price` | 결정가 원본 (위와 동일) | `31.92` |
| `pbs_dpmq` | **DPMQ** = Dispensed Price for Maximum Quantity — 약국 조제 시 최대수량 기준 판매가 | `48.11` |
| `pbs_patient_charge` | 환자 본인부담금 (일반/연금 수급자 공통 표준) | `25.0` |
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
| `pbs_brands` | 브랜드 리스트 JSONB (모든 브랜드의 이름·가격·innovator) | `[{...}, {...}]` |
| `pbs_source_url` | PBS 검색 URL | `https://www.pbs.gov.au/browse/medicine?search=3093T` |
| `restriction_text` | 급여 제한 사유 원문 (evidence 에 사용) | — |

**주의 — PBS API Rate Limit:** 각 API 호출 전 `time.sleep(21)`. 호출 수 × 21초가 전체 시간의 50%+.

### ③ PBS 웹 보강 — `sources/pbs.py` `fetch_pbs_web()`

`pbs_item_code` 가 있을 때만 실행. `https://www.pbs.gov.au/medicine/item/{code}` 페이지를 Jina Reader 마크다운으로 받아 가격 표 파싱.

| 반환 필드 | 의미 |
|---|---|
| `pbs_dpmq` | 웹에서 재추출한 DPMQ (API 값이 null 이면 덮어씀) |
| `pbs_patient_charge` | 환자부담금 (API 값이 null 이면 덮어씀) |
| `pbs_web_source_url` | `https://www.pbs.gov.au/medicine/item/{code}` |
| `pbs_brand_name` | 브랜드명 보강 |
| `pbs_brands` | 브랜드 표 (모든 행) |

복합 성분은 `pbs_item_code` 가 `+` 로 연결(`"3093T+1234X"`)되어 각 code 별 호출 후 합친다.

### ④ Chemist Warehouse — `sources/chemist.py`

호주 최대 민간 약국 체인. Cloudflare 보호를 받아서 **Jina Reader 로 우회**.

URL: `https://www.chemistwarehouse.com.au/search?query={INN}`
(실제 호출은 `https://r.jina.ai/...` 로 래핑)

| 반환 필드 | 의미 | 예시 |
|---|---|---|
| `retail_price_aud` | 민간 소매가 (검색 결과 첫 양수 `$` 값) | `25.00` |
| `price_source_name` | `"Chemist Warehouse"` | |
| `price_source_url` | 원본 검색 URL | |
| `price_unit` | `"per pack"` | |

**신뢰 검증** (`_chemist_retail_trustworthy`): PBS 가격의 15% 미만이거나 `$5` 미만이면 오매칭·부분파싱으로 간주 → **PBS 가격으로 폴백** (`price_source_name = "PBS"`).

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

### 최종 병합 — `build_product_summary`

5개 소스 dict + `au_products.json` 의 품목 메타를 한 dict 로 합치고 73컬럼을 채움.

| 필드 | 의미 |
|---|---|
| `export_viable` / `reason_code` | 수출 적합 판정 |
| `evidence_url` | 대표 증거 URL (ARTG 상세) |
| `evidence_text` | 영어 원문 (Sponsor / ARTG status / PBS 제한 등) |
| `evidence_text_ko` | GPT-4o-mini 한국어 번역 (키 없으면 원문) |
| `sites` JSONB | `{public_procurement:[{name,url},...], private_price:[...], paper:[]}` |
| `completeness_ratio` | `AU_REQUIRED_FIELDS` 중 값 있는 비율 (0 ~ 1) |
| `confidence` | `completeness_score()` 가중치 기반 신뢰도 (0 ~ 1) |
| `data_source_count` | 수집 시도한 소스 수 (일반적으로 4) |
| `error_type` | `PBS_WEB_ENRICHMENT_INCOMPLETE` 등 오류 코드 (정상 시 null) |
| `pricing_case` | `DIRECT` (단일 성분) / `COMPONENT_SUM` (복합) / `ESTIMATE` (미등재 추정) |
| `fob_*` 5개 | **1공정 NULL** — 2공정에서 채움 |
| `block2_*`, `block3_*`, `perplexity_refs`, `llm_*` | **1공정 NULL** — LLM 연동 후 채움 |

### `au-hydrine-004` 실측 예시

```
product_id           = au-hydrine-004
product_name_ko      = Hydrine
inn_normalized       = hydroxycarbamide    ← hydroxyurea 가 PubChem 정규화됨
artg_number/status   = 313760 / registered
tga_sponsor          = Medsurge Pharma Pty Ltd
pbs_listed           = True
pbs_item_code        = 3093T
pbs_price_aud / dpmq = A$31.92 / A$48.11   ← 제조사 공급가 / 약국 판매가
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

## 5. Supabase 스키마 — 5 테이블

### 1) `australia` — 1·2공정 통합 (73컬럼)

| 섹션 | 컬럼 수 |
|---|---|
| **공통 6** (변경 금지) — `id, product_id, market_segment, fob_estimated_usd, confidence, crawled_at` | 6 |
| 품목 마스터 | 6 |
| TGA ARTG | 7 |
| PBS API + 웹 | 20 |
| Chemist 소매 | 4 |
| NSW Procurement | 4 |
| 수출성 판정 | 2 |
| 증거 (영/한) | 3 |
| **2공정 FOB** (보수/기준/공격 시나리오) | 5 |
| 메타 (`sites` JSONB 등) | 4 |
| **LLM 블록** (Claude Haiku Block 2/3 + Perplexity + llm_meta) | 12 |

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

### 5) `au_regulatory` — 호주 규제 체크포인트 시드 5행 (`title UNIQUE + ON CONFLICT DO NOTHING`)

---

## 6. API 엔드포인트 (`render_api.py`)

| 메서드 · 경로 | 용도 |
|---|---|
| `GET /` | `templates/index.html` 서빙 |
| `GET /health` | 헬스체크 |
| `POST /api/crawl` | `{product_id}` → `au_crawler.main()` 래핑 (SystemExit catch, env `PRODUCT_FILTER` 주입) |
| `GET /api/data` | `australia` 전체 목록 (최신 crawled_at 순) |
| `GET /api/data/{product_id}` | 단건 조회 |
| `GET /api/reports` | 오늘(UTC) 저장된 보고서 목록 |
| `POST /api/reports` | `reports.insert()` |
| `GET /api/news` | SerpAPI google_news 4건 (키 없으면 mock) |
| `GET /api/exchange` | AUD 기준 환율 `{aud_krw, aud_usd, updated}` (실패 시 fallback) |

**크롤러 호출 방식:** `from au_crawler import main as run_crawler` → `os.environ["PRODUCT_FILTER"] = product_id` 후 `run_crawler()` 호출 → `SystemExit` 예외로 종료 코드 감지. **단일 워커 전제** (동시 요청 시 env 경합 가능).

---

## 7. 프론트엔드 UI

5개 탭 — 메인 / 1공정 / 2공정 / 3공정 / 보고서.

- 메인: 거시지표 · 관세 · **환율(API)** · **뉴스(API)** · 파이프라인
- 1공정: TODO 스텝 → 품목 선택 → **POST /api/crawl** → **GET /api/data/{id}** → 카드 렌더 → 보고서 산출
- 2·3공정: 현재 데모 데이터 (향후 FOB 역산 · PSI 스코어링 로직 연동)
- 보고서: **GET /api/reports** 초기 로드, 저장 버튼 → **POST /api/reports**

**실패 폴백:** API 실패 시 `app.js` 의 `PRODS` 배열(하드코딩 mock 8개)로 카드 렌더.

---

## 8. 운영 스크립트

### `scripts/migrate.py` — Supabase 스키마 배포

Supabase Management API (`POST /v1/projects/{ref}/database/query`) 로 `australia_table.sql` 전체를 한 번에 실행.

**실행 흐름 (4단계)**

1. **SQL 배포** — `australia_table.sql` 전체를 한 번에 POST → 서버가 순차 실행
2. **PostgREST 스키마 캐시 자동 리로드** — `NOTIFY pgrst, 'reload schema';` 발송
   → `ALTER TABLE` 직후 supabase-py upsert 가 `PGRST204` 로 튕기는 버그 방지
3. **기본 검증** — `public` 스키마 테이블 목록 / `australia` 컬럼 수 / `au_regulatory` 시드 행 수
4. **`_ALLOWED_COLUMNS` ↔ DB 컬럼 대조 검증** — `supabase_insert._ALLOWED_COLUMNS` 를 import 해 실제 `information_schema.columns` 와 양방향 차집합 비교
   - `expected − actual` → SQL 에는 있는데 DB 에 없음 (ALTER 누락)
   - `actual − expected` → DB 엔 있는데 `_ALLOWED_COLUMNS` 누락 (insert 시 해당 컬럼 값 저장 누락)
   - 전부 일치 → `✅ 컬럼 검증 통과`, 불일치 → exit 1

```bash
python scripts/migrate.py
```

**정상 출력 예시**
```
[INFO] HTTP 201 ✓
[INFO] NOTIFY pgrst 'reload schema' → HTTP 201

[검증]
  · au_regulatory / australia / australia_buyers / australia_history / reports
  · australia 컬럼 수 = 73
  · au_regulatory 시드 = 5 행

[컬럼 검증] supabase_insert._ALLOWED_COLUMNS vs information_schema
  · 기대 컬럼 수(코드) = 73 / 실제 DB 컬럼 수 = 73
  ✅ 컬럼 검증 통과

[완료] Supabase 스키마 배포 성공
```

### `scripts/deploy_render.py` — Render 배포 트리거

```bash
python scripts/deploy_render.py
```

---

## 9. 환경 변수 (`.env`)

| 변수 | 필수 | 용도 |
|---|---|---|
| `SUPABASE_URL` | ✅ | `https://{ref}.supabase.co` |
| `SUPABASE_SERVICE_KEY` | ✅ | `sb_secret_...` (PostgREST upsert) |
| `SUPABASE_ACCESS_TOKEN` | ✅ (migrate.py) | `sbp_...` PAT (Management API) |
| `PBS_SUBSCRIPTION_KEY` | ✅ | PBS API v3 구독 키 |
| `OPENAI_API_KEY` | 선택 | evidence.py 영→한 번역 |
| `SERPAPI_KEY` | 선택 | `/api/news` (없으면 mock) |
| `RENDER_API_KEY` | Render 배포 시 | `rnd_...` |
| `RENDER_SERVICE_ID` | Render 배포 시 | `srv-...` |

---

## 10. 로컬 실행

### 10.1 최초 세팅 (1회)

```bash
python -m venv venv                        # 가상환경 생성
# venv 활성화 (쉘별로 아래 10.2 참고)
pip install -r upharma-au/requirements.txt
python scripts/migrate.py                  # Supabase 스키마 배포
```

### 10.2 venv 활성화 (쉘별)

| 쉘 | 활성화 명령 |
|---|---|
| **Git Bash** | `source venv/Scripts/activate` |
| **PowerShell** | `.\venv\Scripts\Activate.ps1` (최초 1회 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 필요할 수 있음) |
| **cmd** | `venv\Scripts\activate.bat` |

활성화되면 프롬프트 앞에 `(venv)` 가 붙는다.

### 10.3 웹 UI 로컬 서버 실행

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

### 10.4 브라우저 접속 · 종료

- 접속: http://localhost:8000 또는 http://127.0.0.1:8000
- 1공정 탭 → Hydrine 선택 → 🔍 크롤링 실행 (DB 에 이미 있으면 즉시, 신규는 ~100초)
- 종료: 터미널에서 `Ctrl + C`

### 10.5 크롤러 단건 실행 (CLI, 웹 없이)

```bash
cd upharma-au/crawler
# Git Bash / PowerShell
PRODUCT_FILTER=au-hydrine-004 python au_crawler.py
# cmd
set PRODUCT_FILTER=au-hydrine-004 && python au_crawler.py
```

### 10.6 포트 8000 충돌 확인 · 해제

```bash
# 점유 프로세스 확인
netstat -ano | findstr :8000

# 강제 종료 (PID 는 위 결과에서)
taskkill /PID <PID> /F
```

### 10.7 Render 배포 트리거 (선택)

```bash
python scripts/deploy_render.py
```

### 10.8 전체 실행 흐름 요약

```
venv 활성화
  ↓
pip install -r upharma-au/requirements.txt   (최초 1회)
  ↓
python scripts/migrate.py                     (스키마 변경 있을 때)
  ↓
uvicorn render_api:app --app-dir upharma-au --reload --port 8000
  ↓
브라우저 → http://localhost:8000
```

---

## 11. 운영 주의사항

### PostgREST 스키마 캐시 (PGRST204)

`ALTER TABLE` 후 supabase-py upsert 가 `Could not find column ...` 로 실패하는 경우가 있음.

- **`scripts/migrate.py` 는 SQL 실행 직후 `NOTIFY pgrst, 'reload schema';` 자동 발송** → migrate.py 를 통한 배포는 이 버그가 발생하지 않음
- 대시보드나 psql 로 DDL 을 **직접** 실행한 경우에만 주의. 수동 NOTIFY 또는 Supabase 대시보드 → Database → Reload Schema

```sql
-- migrate.py 를 거치지 않고 DDL 을 직접 돌렸을 때만 필요
NOTIFY pgrst, 'reload schema';
```

### 컬럼 화이트리스트 정합성

`supabase_insert._ALLOWED_COLUMNS` 는 upsert 시 허용 컬럼 화이트리스트. **DB 에만 있고 여기 없는 컬럼은 저장되지 않음** → `au_crawler.build_product_summary` 가 값을 생성해도 silent drop.
`migrate.py` 의 §4 컬럼 검증이 이 미스매치를 즉시 감지함. 새 컬럼을 SQL 에 추가했다면 반드시 `_ALLOWED_COLUMNS` 에도 추가하고 `python scripts/migrate.py` 로 재검증할 것.

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

맨 위 "🎯 설계 원칙" 섹션 참고. `/api/crawl` 은 항상 외부 API 를 재호출하고 DB 를 덮어씁니다. "최근에 크롤링 돌렸으니 DB 값 재사용" 식의 캐시 분기를 넣으면 **이 시스템의 존재 의의(실시간 시장분석)가 깨집니다.** 같은 이유로, 프론트엔드가 `/api/crawl` 호출 전에 `/api/data/{id}` 로 먼저 조회해서 스킵 여부를 결정하는 로직도 넣지 않습니다.

---

## 12. 진행 상황 (2026-04-14 기준)

- ✅ Phase 1 — SQL 스키마 · 5테이블 73컬럼 · Supabase 배포
- ✅ Phase 2 — render_api.py 9 엔드포인트 · v3 UI 연동
- ✅ next-app/ 레거시 제거 (300MB 삭제)
- ✅ `scripts/migrate.py` · `scripts/deploy_render.py` 자동화
- ✅ `au-hydrine-004` 실크롤링 → Supabase upsert 검증 (confidence 0.81)
- 🟡 나머지 7개 품목 크롤링 실행
- 🟡 로컬 uvicorn + 브라우저 UI 검증
- 🟡 2공정 FOB 역산 로직
- 🟡 3공정 바이어 수집 + PSI 스코어링
- 🟡 LLM (Claude Haiku) Block 2/3 · Perplexity 레퍼런스
