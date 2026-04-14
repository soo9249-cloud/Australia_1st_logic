# 호주 1공정 MVP (Australia_1st_logic)

한국 제약회사 수출 지원을 위한 **호주 시장조사(1공정)** 파이프라인입니다.  
`au_products.json`에 정의된 품목별로 **TGA ARTG**, **PBS(공식 API + 품목 웹 보강)**, **Chemist Warehouse(민간 소매)**, **AusTender(공공 조달)** 데이터를 수집해 단일 요약 행으로 만들고, **Supabase `australia` 테이블**에 `product_id` 기준으로 upsert합니다. 현재는 **FastAPI(`render_api.py`) + 정적 프론트(`templates/index.html`, `static/*`)**로 조회/실행 UI를 제공합니다.

상세 프롬프트 순서·원 스펙은 [`AU_1공정_Cursor_바이브코딩_프롬프트세트_v7.md`](./AU_1공정_Cursor_바이브코딩_프롬프트세트_v7.md)를 참고합니다.

---

## 기술 스택 (상세)

### 런타임·언어

| 구분 | 버전·비고 |
|------|-----------|
| Python | **3.12** (GitHub Actions `setup-python@v5`와 동일) |
| Node.js | (선택) 레거시 `next-app` 점검 시 LTS 권장 |

### 크롤러 (Python) — `upharma-au/crawler/`

| 패키지 / 도구 | 용도 |
|----------------|------|
| **httpx** | PBS API, TGA/PBS/Chemist용 **Jina Reader** 프록시 URL, PubChem REST, OpenAI HTTP 등 동기 HTTP 클라이언트 |
| **python-dotenv** | `pbs.py`·`supabase_insert.py` 등에서 상위 디렉터리 `.env` 탐색 후 로드 (`override=False`) |
| **supabase-py** | `create_client` + `table("australia").upsert(..., on_conflict="product_id")` |
| **selectolax** | (레거시/HTML 직접 파싱 경로가 남아 있을 수 있음) DOM 파싱 보조 |
| **openai** | `utils/evidence.py`에서 `gpt-4o-mini`로 근거 문구 번역·요약 (키 없으면 원문 유지) |

### 외부 서비스·API

| 서비스 | 역할 |
|--------|------|
| **Australian PBS API v3** | `https://data-api.health.gov.au/pbs/api/v3` — `Subscription-Key` 헤더, `schedules` → `items` |
| **PubChem PUG REST** | `utils/inn_normalize.py` — 성분명 동의어 후보에서 WHO INN에 가까운 표기 추정 (실패 시 소문자 원문) |
| **Jina AI Reader** (`https://r.jina.ai/` + 원본 HTTPS URL) | TGA·PBS 품목·Chemist 등 **정적 차단 우회** 및 마크다운/텍스트 수신 |
| **OpenAI Chat Completions** | 근거 필드 `evidence_text` / `evidence_text_ko` 생성 |
| **Supabase (Postgres)** | 테이블 `australia`, PostgREST 경유 upsert |
| **GitHub Actions** | `workflow_dispatch`로 크롤러 1품목 실행 |

### 프론트·API — `upharma-au/render_api.py`, `upharma-au/templates/`, `upharma-au/static/`

| 기술 | 용도 |
|------|------|
| **FastAPI** | `/api/crawl`, `/api/data`, `/api/reports`, `/health` 엔드포인트 |
| **Jinja2 Templates** | `/` 진입 시 `templates/index.html` 렌더 |
| **Vanilla JS + CSS** | `static/app.js`, `static/styles.css` 기반 대시보드 UI |
| **Uvicorn** | Render 및 로컬에서 FastAPI ASGI 서버 실행 |

### 인프라·배포

| 항목 | 설명 |
|------|------|
| **GitHub Actions** | `.github/workflows/au_crawl.yml` — 수동 입력 `product_filter` → `PRODUCT_FILTER` 환경변수로 `python au_crawler.py` |
| **Supabase** | 서울 리전 등 프로젝트 설정, SQL Editor에서 `australia_table.sql`의 `CREATE` + `ALTER` 적용 |
| **Render** | 루트 `render.yaml`로 Python Web Service 배포 (`uvicorn render_api:app`) |

---

## 프로젝트 구조

| 경로 | 설명 |
|------|------|
| `upharma-au/crawler/` | **`au_crawler.py`** — 엔트리·`build_product_summary`·`main()` |
| `upharma-au/crawler/sources/` | **`pbs.py`**, **`tga.py`**, **`chemist.py`**, **`austender.py`** |
| `upharma-au/crawler/utils/` | **`inn_normalize.py`**, **`scoring.py`**, **`evidence.py`** |
| `upharma-au/crawler/db/` | **`australia_table.sql`**, **`supabase_insert.py`** |
| `upharma-au/crawler/au_products.json` | 카탈로그 8품목 (`product_id`, 성분, `pricing_case`, 검색어 등) |
| `upharma-au/render_api.py` | FastAPI 어댑터 (크롤러 실행/조회/보고서 API) |
| `upharma-au/templates/index.html` | 대시보드 HTML 템플릿 |
| `upharma-au/static/app.js` | 프론트 로직 (크롤 실행, 결과 카드, 보고서 저장 UI) |
| `upharma-au/static/styles.css` | 대시보드 스타일 |
| `render.yaml` | Render 배포 정의 |
| `.github/workflows/au_crawl.yml` | 크롤러 수동 실행 워크플로 |

---

## 환경 변수

### 크롤러·Actions 공통 (저장소 Secrets / 로컬 `.env`)

| 변수 | 용도 |
|------|------|
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_SERVICE_KEY` | **service_role** — 크롤러 upsert 시 RLS 우회 |
| `PBS_SUBSCRIPTION_KEY` | PBS API v3 `Subscription-Key` |
| `OPENAI_API_KEY` | 근거 문구 번역·요약 (없어도 크롤은 동작, 근거 한글 품질만 저하) |
| `PRODUCT_FILTER` | **단일** `product_id` (예: `au-atmeg-006`). 비어 있으면 `main()` 즉시 종료 |

### 웹 서버(FastAPI/Render)

| 변수 | 용도 |
|------|------|
| `SUPABASE_URL` | `render_api.py`에서 Supabase 조회/저장 시 사용 |
| `SUPABASE_SERVICE_KEY` | `render_api.py` 및 크롤러 upsert에 사용 |
| `PBS_SUBSCRIPTION_KEY` | `/api/crawl` 내부 `au_crawler.main()` 실행 시 사용 |
| `OPENAI_API_KEY` | 근거 번역/요약 품질 향상(없어도 실행 가능) |
| `PYTHON_VERSION` | Render 배포 시 Python 버전 고정(`render.yaml`) |

> `.env`·`.env.local`은 **커밋하지 않음** (`.gitignore`에 포함).

---

## FastAPI 엔드포인트

`upharma-au/render_api.py` 기준으로 현재 제공되는 API는 아래와 같습니다.

| 메서드 | 경로 | 설명 |
|------|------|------|
| `GET` | `/` | 대시보드 HTML 렌더 |
| `GET` | `/health` | 헬스체크 (`{"status":"ok"}`) |
| `POST` | `/api/crawl` | `product_id` 1건 크롤링 실행 (`au_crawler.main()` 호출) |
| `GET` | `/api/data` | `australia` 전체 목록(최신 `crawled_at` 순) |
| `GET` | `/api/data/{product_id}` | `australia` 단건 조회 |
| `GET` | `/api/reports` | 오늘(UTC) 생성된 `reports` 목록 |
| `POST` | `/api/reports` | 보고서 메타 1건 저장 (`gong`, `title` 필수) |

> 프론트의 `신약 직접 입력(manual)` 모드는 현재 UI 안내대로 **미지원**이며, `au_products.json`에 있는 `product_id` 8개만 실행됩니다.

---

## 크롤링 로직 (핵심 — 자세히)

전체는 **`au_crawler.py`의 `main()`** 한 줄기로, **품목 1개만** 처리합니다 (`PRODUCT_FILTER` 필수).

### 1) 품목 로드

- `au_products.json`의 `products` 배열에서 `product_id == PRODUCT_FILTER`인 객체 **1건**을 찾습니다.
- 없으면 stderr 메시지 후 `sys.exit(1)`.

### 2) TGA (`sources/tga.py`)

1. **검색어**  
   - `product["tga_search_terms"][0]`가 있으면 사용, 없으면 `inn_normalized`.
2. **Jina Reader**  
   - URL 형식: `https://r.jina.ai/https://www.tga.gov.au/resources/artg?keywords={인코딩된 검색어}`  
   - `httpx.get`, 타임아웃 **20초**, 예외는 삼기고 `not_registered` 계열 기본값으로 폴백.
3. **등록 여부**  
   - 마크다운에 `result(s) found`(대소문자 무시)가 있으면 결과 있음으로 간주.
4. **첫 ARTG ID**  
   - 정규식 `### ... (숫자) ](` 형태에서 **첫 번째** ARTG 숫자만 추출.
5. **검색 페이지 스폰서**  
   - `Sponsor` 섹션과 `## Published date` 사이에서, 첫 `* - [x] 스폰서명(건수) [` 패턴의 스폰서 문자열 추출.
6. **상세 병합**  
   - ARTG ID가 있으면 `fetch_tga_detail(artg_id)` 호출:  
     `https://r.jina.ai/https://www.tga.gov.au/resources/artg/{id}`  
   - 마크다운에서 `Sponsor` 링크 텍스트, `Licence category` 다음 줄, `Licence status` 다음 줄 파싱.
7. **스케줄 vs 라이선스**  
   - **의약품 스케줄(S2/S3/S4/S8)** 만 `tga_schedule`에 넣기 위해, 상세 **전체 텍스트**에 대해 `\bS(?:2|3|4|8)\b` **첫 매칭**만 사용. 없으면 `None`.  
   - **RE 등 라이선스 구분**은 `tga_licence_category` / `tga_licence_status`에만 저장 (`tga_schedule`에 RE를 넣지 않음).
8. **`determine_export_viable`**  
   - `tga_schedule`에 S8 포함 시 `not_viable`, 그 외 `artg_status == "registered"`이면 `viable` 등 (함수 시그니처 유지).

### 3) PBS — 공식 API (`sources/pbs.py`)

#### 3-1. 스케줄 코드

- `GET .../schedules` — 응답 `data[0].schedule_code` 사용.  
- 호출 전 **`time.sleep(21)`** — PBS API 속도 제한 완화용(문서/운영 정책에 맞춘 간격).

#### 3-2. 성분 매칭 needle (중요)

- `_pbs_needles(ing_raw)`  
  - **① 원문 소문자** `ing_raw.strip().lower()`  
  - **② `normalize_inn(ing_raw)`** (PubChem 동의어 휴리스틱)  
  - 순서 유지한 중복 제거 리스트.
- **문제 해결 사례:** `atorvastatin`만 PubChem에 넘기면 첫 동의어가 브랜드명(`cardyl`)으로 바뀌어 PBS `drug_name`과 안 맞는 경우가 있어, **원문 needle을 반드시 포함**해 부분일치합니다.

#### 3-3. `_row_matches_ingredient(row, needles)`

- `drug_name`, `li_drug_name`, `generic_name`, `product_name`을 소문자로 이어 붙인 문자열에, **needle 중 하나라도 부분 문자열로 포함**되면 매칭.

#### 3-4. 1차 검색 (`/items`)

- 파라미터: `schedule_code`, **`drug_name` = needles[0]**(보통 원문 성분), `page=1`, `limit=10`.  
- 200이면 `data` 각 행에 대해 위 매칭으로 필터 → `primary_matched`.

#### 3-5. 1차 보조 (원문 매칭 실패 시)

- `primary_matched`가 비었고 needle이 2개 이상이며 서로 다르면, **`drug_name = needles[1]`** 로 동일 limit 재요청(역시 `sleep(21)` 후).  
- 다시 `_row_matches_ingredient`로 채움.

#### 3-6. 다단계 폴백 (페이지 순회)

- 여전히 비면 `page` 1…`_MAX_FALLBACK_PAGES`(10)까지 **`drug_name` 없이** `limit=100`으로 `/items` 순회.  
- `_meta.total_records`로 마지막 페이지 판단.  
- 각 페이지 행에 동일 needle 부분일치.

#### 3-7. API 행 → 내부 dict (`_row_to_result`)

- PBS 코드, 가격(`determined_price` / `claimed_price` 우선순위), 제한 문구, DPMQ·pack·benefit type·브랜드·innovator·formulary 등.  
- **`pbs_restriction`**: `benefit_type_code in ("R", "S")` (Restricted / Special).

#### 3-8. 브랜드 다건 → 대표 1행 (`_filter_results`)

- 매칭 행이 여러 브랜드일 때 **리스트에는 dict 1개만** 반환.  
- **오리지널** `innovator_indicator == "Y"`가 있으면 그중 첫 행, 없으면 **제네릭 N** 중 `pbs_price_aud` 최저 1행.  
- `pbs_total_brands`: 서로 다른 `pbs_brand_name` 개수.  
- **`pbs_brands`**: 전 행을 `{ brand_name, pbs_price_aud, pbs_innovator, pbs_item_code }` 리스트로 JSONB 적재용 보존.

#### 3-9. 복합 성분 (`fetch_pbs_multi` + `_merge_pbs_rows`)

- `inn_components`가 2개 이상이면 성분마다 `fetch_pbs_by_ingredient` → 결과 리스트를 이어 붙임.  
- `_merge_pbs_rows`:  
  - `pbs_listed`: 한 행이라도 True면 True  
  - `pbs_price_aud`: 숫자만 **합산** (복합제 요약용)  
  - `pbs_item_code`: 문자열로 **`+` 연결**  
  - `restriction_text`: ` | `로 결합

### 4) PBS — 품목 웹 보강 (`fetch_pbs_web`)

- **`pbs_item_code`가 있으면** (복합이면 `+`로 분리한) **코드마다** 호출:  
  `https://r.jina.ai/https://www.pbs.gov.au/medicine/item/{코드}`  
- 마크다운 **표 행**(`|` 구분)에서 PBS 코드 열이 일치하는 행들을 모읍니다.  
- **DPMQ·General Patient Charge**는 지정 열(6·8번째 `$` 금액) 정규식 추출.  
- **브랜드**: 2열 텍스트에서 마크다운 링크 제거 후 `pbs_brand_name`(첫 행)·`pbs_brands` 배열 구축.  
- 웹만으로 innovator 구분이 어려우면 `None` — **`main()`에서 API가 준 `pbs_innovator`를 덮어쓰지 않도록** 병합 순서 유지.

### 5) Chemist (`sources/chemist.py`)

- 검색 URL을 Jina로 감싼 뒤 응답 **전체 텍스트**에서 `\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)` 반복, **0 초과인 첫 금액**을 소매가로 사용(구현은 파일 기준 최신 주석 참고).

### 6) AusTender (`sources/austender.py`)

- 키워드 검색 URL로 HTML 수신 후 테이블 첫 데이터 행에서 금액·공급자 등 추출(실패 시 빈 필드 dict).

### 7) 요약 조립 (`build_product_summary`)

- **`tga_schedule` 저장용:** `tga`에서 읽은 값을 `_tga_schedule_s2348_only`로 한 번 더 걸러 **S2/S3/S4/S8만** `assembled`/반환 dict에 반영.  
- **`determine_export_viable`:** 위 정규화된 `tga` dict로 호출.  
- **PBS 등재 시:** `export_viable`을 PBS 기준으로 viable 덮어쓰기(기존 정책).  
- **`retail_price_aud`:**  
  1. **`_chemist_retail_trustworthy`**: Chemist 가격이 없거나 ≤0, **5 AUD 미만**, 또는 PBS 가격 대비 **15% 미만**이면 신뢰하지 않음(잘못된 첫 검색·저가 오매칭 완화).  
  2. 신뢰 시 Chemist 가격·출처명·URL.  
  3. 아니면 PBS `pbs_price_aud`가 양수일 때만 소매 자리에 PBS 가격 사용.  
  4. 둘 다 안 되면 `None`.  
- **`pbs_patient_charge`:** PBS API + `fetch_pbs_web`에서 온 값만 사용(소매가와 혼동 없음).  
- **`error_type`:** `pbs_item_code` 있고 `pbs_listed`인데 `pbs_brand_name`·`pbs_innovator`·`pbs_brands`가 **모두** `None`이면 `PBS_WEB_ENRICHMENT_INCOMPLETE`.  
- **`completeness_ratio` / `confidence`:** `utils/scoring.py`의 필드 채움 규칙.  
- **`evidence_*`:** `utils/evidence.py` + `_raw_evidence_text`(PBS 제한·TGA 스케줄·스폰서·ARTG 상태·조달 등).

### 8) DB 적재

- `db/supabase_insert.py`: `_ALLOWED_COLUMNS` 화이트리스트만 PostgREST로 전송, **`on_conflict="product_id"`** upsert.  
- 스키마 확장 컬럼은 `australia_table.sql`의 **`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`** 블록 참고 (PBS 확장, `pbs_brands` JSONB, `tga_licence_*` 등).

---

## 소스 파일별 크롤링 로직 (PBS / TGA / Chemist)

아래는 **AusTender 점검 전**까지 동작이 확인된 세 파일 기준으로, 파일 단위로 정리한 요약입니다. (전체 파이프라인 순서는 위 「크롤링 로직 (핵심)」과 동일.)

### `sources/pbs.py`

| 항목 | 내용 |
|------|------|
| **환경** | `PBS_SUBSCRIPTION_KEY` — `GET` 시 헤더 `Subscription-Key`. 상위 경로 `.env` 자동 로드. |
| **스케줄** | `GET /schedules` → `data[0].schedule_code`. 호출 전 `sleep(21)` (속도 제한 완화). |
| **성분 needle** | `_pbs_needles`: **원문 소문자** + **`normalize_inn`(PubChem)** 둘 다 리스트에 넣어 중복 제거. |
| **행 매칭** | `_row_matches_ingredient(row, needles)`: `drug_name`·`li_drug_name`·`generic_name`·`product_name`을 합친 소문자 문자열에, needle 중 **하나라도 부분 문자열**이면 매칭. |
| **1차 `/items`** | `schedule_code` + `drug_name=needles[0]`, `page=1`, `limit=10` → 매칭 행만 수집. |
| **1차 보조** | 1차 매칭이 비었고 needle이 2개 이상이면 `drug_name=needles[1]`로 **한 번 더** 동일 limit 조회. |
| **폴백** | `drug_name` 없이 `page` 1…10, `limit=100` 순회하며 동일 needle 매칭. |
| **행 → dict** | `_row_to_result`: PBS 코드, 가격, 제한문, pack, benefit, brand, innovator 등. `pbs_restriction` = `benefit_type_code in ("R","S")`. |
| **다브랜드** | `_filter_results`: 오리지널(Y) 1행 우선, 없으면 제네릭(N) 중 최저가 1행. `pbs_brands`·`pbs_total_brands` 부가. |
| **복합** | `fetch_pbs_multi` + (호출부) `_merge_pbs_rows`: 가격 합산, `pbs_item_code`는 `+` 연결. |
| **웹** | `fetch_pbs_web(pbs_item_code)`: Jina `r.jina.ai/https://www.pbs.gov.au/medicine/item/{코드}` → 표 행에서 DPMQ·환자부담·브랜드 목록 파싱. |

**단품 스모크 (crawler 디렉터리):**

```powershell
python -c "from sources.pbs import fetch_pbs_by_ingredient; print(fetch_pbs_by_ingredient('atorvastatin')[0])"
```

### `sources/tga.py`

| 항목 | 내용 |
|------|------|
| **검색** | Jina: `r.jina.ai/https://www.tga.gov.au/resources/artg?keywords={quote(검색어)}`, 타임아웃 20초. |
| **등록 여부** | 본문에 `result(s) found` 있으면 결과 있음. |
| **ARTG ID** | `### ... (숫자)](` 패턴에서 **첫** ID. |
| **검색창 스폰서** | `Sponsor` ~ `## Published date` 블록에서 첫 `[x]` 스폰서 줄 파싱. |
| **상세** | `fetch_tga_detail(artg_id)`: Jina로 `/resources/artg/{id}` — Sponsor 링크 텍스트, Licence category/status 다음 줄. |
| **스케줄** | 상세 **전체 텍스트**에 `\bS(?:2|3|4|8)\b` **첫 매칭만** `tga_schedule` (없으면 `None`). RE 등은 `tga_licence_category`만. |
| **병합** | `fetch_tga_artg`가 상세 dict를 검색 결과에 merge. |
| **수출 판정** | `determine_export_viable` — S8이면 불가, 등록이면 viable 등 (시그니처 고정). |

**단품 스모크 (구 `test_tga.py` 대체):**

```powershell
python -c "from sources.tga import fetch_tga_artg, determine_export_viable; r=fetch_tga_artg('hydroxycarbamide'); print(r); print(determine_export_viable(r))"
```

### `sources/chemist.py`

| 항목 | 내용 |
|------|------|
| **URL** | 원본: `https://www.chemistwarehouse.com.au/search?query={quote(검색어)}`. |
| **Jina** | `https://r.jina.ai/{원본URL}` — `Accept: text/event-stream`, `User-Agent` 지정, 타임아웃 30초. |
| **가격** | 응답 전체 텍스트에서 `\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)` 전역 검색, **0보다 큰 첫 값**을 `retail_price_aud`로 사용 후 중단. |
| **반환** | `retail_price_aud`, `price_unit`(per pack), `price_source_name`, `price_source_url`(원본 검색 URL). 실패·예외 시 `None`. |
| **기타** | `build_sites(...)`: PBS·TGA·Chemist·AusTender·선택 PubMed URL을 `sites` JSON 구조로 묶음. |

**단품 스모크:**

```powershell
python -c "from sources.chemist import fetch_chemist_price; print(fetch_chemist_price('atorvastatin'))"
```

### `test_tga.py` 제거

- 저장소 어디에서도 import/호출되지 않음.  
- 위 **한 줄 `python -c`** 로 TGA만 빠르게 검증 가능하므로 **삭제해도 됨** (이 README에 대체 명령을 적어 둠).

---

## 로컬 실행 (크롤러 1품목)

```powershell
cd C:\Users\user\Desktop\Australia_1st_logic\upharma-au\crawler
$env:PRODUCT_FILTER="au-hydrine-004"
python au_crawler.py
```

- PBS API는 호출 간 **수십 초**가 걸릴 수 있음 (`sleep(21)` 등).  
- Python 의존성: `pip install -r ..\requirements.txt` (또는 프로젝트 안내 경로).

---

## 로컬 실행 (FastAPI 서버)

```powershell
cd C:\Users\user\Desktop\Australia_1st_logic
pip install -r .\upharma-au\requirements.txt
uvicorn render_api:app --app-dir upharma-au --reload --host 127.0.0.1 --port 8000
```

- 브라우저: `http://127.0.0.1:8000/`
- 헬스체크: `http://127.0.0.1:8000/health`
- 크롤링 API 예시:

```powershell
curl -X POST http://127.0.0.1:8000/api/crawl `
  -H "Content-Type: application/json" `
  -d "{\"product_id\":\"au-hydrine-004\"}"
```

---

## Render 배포

루트 `render.yaml` 기준 설정은 다음과 같습니다.

- `buildCommand`: `pip install -r upharma-au/requirements.txt`
- `startCommand`: `uvicorn render_api:app --app-dir upharma-au --host 0.0.0.0 --port $PORT`
- 필수 환경변수: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- 권장 추가: `PBS_SUBSCRIPTION_KEY`, `OPENAI_API_KEY`

---

## Git / 용량 정리

- **제외:** `venv/`, `.env`, `.env.local`, `__pycache__/`, **`upharma-au/next-app/node_modules/`**, **`upharma-au/next-app/.next/`** (`.gitignore`에 명시).  
- Next 앱 소스(`package.json`, `pages/`, `lib/` 등)는 커밋 대상으로 두고, 생성물만 제외하는 것이 일반적입니다.

---

## 권장 구현 순서 (프롬프트 세트 v7 요약)

| 단계 | 내용 |
|------|------|
| PROMPT 1~2 | 폴더·`au_products.json` |
| PROMPT 6 | Supabase DDL·`supabase_insert` |
| PROMPT 3~5 | PBS/TGA/Chemist/AusTender·요약·점수·근거 |
| PROMPT 7 | GitHub Actions |
| PROMPT 8 | Next 조회·트리거 |
| PROMPT 9 | PDF (미구현 시 README 체크리스트 유지) |

---

## 진행 현황 (체크리스트)

| # | 항목 | 상태 |
|---|------|------|
| 1 | 폴더 구조 및 기본 파일 | 완료 |
| 2 | `au_products.json` 8품목 | 완료 |
| 3 | PBS / TGA (API + Jina 상세·웹 보강, 복합 병합) | 완료 |
| 4 | Chemist(Jina) / AusTender | 완료 |
| 5 | `build_product_summary`·점수·근거 | 완료 |
| 6 | Supabase upsert·확장 컬럼 SQL | 완료 |
| 7 | GitHub Actions `au_crawl.yml` | 완료 |
| 8 | FastAPI 조회·크롤링·보고서 API + 정적 대시보드 | 완료 |
| 9 | PDF 출력 | 미완료 |

---

## 업데이트 이력

### 2026-04-12

- PROMPT 1~7 및 README 최초 작성, Supabase·크롤러 스켈레톤 연동 등 (상세는 이전 README 본문과 동일).

### 2026-04-14

- **README:** `pbs.py` / `tga.py` / `chemist.py` **소스 파일별 크롤링 요약** 및 단품 스모크 명령 추가.  
- **`crawler/test_tga.py` 삭제:** 동일 검증은 README의 `python -c` 한 줄로 대체.
- **웹 레이어 반영:** `render_api.py`(FastAPI), `templates/index.html`, `static/app.js`, `static/styles.css`, `render.yaml` 기준으로 실행/배포 문서 갱신.

### 2026-04-13

- **PBS:** PubChem `normalize_inn`만 쓸 때 생기는 오매칭(예: atorvastatin → cardyl) 보완을 위해 **`_pbs_needles`(원문+정규화)** 및 **`_row_matches_ingredient` 다중 needle** 도입. 1차 실패 시 **`drug_name` 두 번째 needle** 재조회, 이후 **페이지 순회 fallback** 유지.  
- **`fetch_pbs_web`:** Jina로 PBS **품목 페이지** 마크다운 수신, 표에서 DPMQ·환자부담금·브랜드 행(`pbs_brands`) 파싱.  
- **`au_crawler` `main`:** `pbs_item_code`가 있으면 **`+` 분리 후 코드별 `fetch_pbs_web`**, API 브랜드/innovator와 병합.  
- **TGA:** 직접 HTML 스크래핑 대신 **Jina Reader**로 검색·상세 마크다운 파싱, **`tga_licence_category` / `tga_licence_status`**, 스케줄은 **S2~S8 정규식만** `tga_schedule`.  
- **`build_product_summary`:** Chemist 소매가 **신뢰 구간 검사** 후에만 사용, 아니면 PBS 가격, **`pbs_patient_charge` 혼동 방지**, 웹 보강 실패 시 **`error_type`**.  
- **`.gitignore`:** `next-app/node_modules/`, `.next/` 제외로 Git 푸시 부담 완화.  
- **실측:** `PRODUCT_FILTER`로 Sereterol / Rosumeg / Atmeg 크롤 후 Supabase upsert 성공 로그 확인.

---

*참고: UPharma Export AI · KITA 무역AX 1기 · 한국유나이티드제약 5조 — 프롬프트 세트 v7 기반 문서.*
