# 호주 1공정 MVP (Australia_1st_logic)

한국 제약회사 수출 지원 자동화를 위한 호주 시장조사 파이프라인입니다.  
8개 품목의 TGA 인허가·PBS 약가·민간 소매가를 수집해 Supabase에 저장하고, Next.js로 조회한 뒤 PDF 시장조사 보고서로 출력하는 것을 목표로 합니다.

상세 스펙·프롬프트 순서는 저장소 내 [`AU_1공정_Cursor_바이브코딩_프롬프트세트_v7.md`](./AU_1공정_Cursor_바이브코딩_프롬프트세트_v7.md)를 기준으로 합니다.

---

## 프로젝트 구조

| 경로 | 설명 |
|------|------|
| `upharma-au/` | 크롤러(Python), Next.js 앱 뼈대 |
| `upharma-au/crawler/` | `au_crawler.py`, 소스별 모듈(`sources/`), 유틸, `db/supabase_insert.py` |
| `upharma-au/crawler/db/australia_table.sql` | Supabase `australia` 테이블 CREATE 스크립트(PROMPT 6) |
| `upharma-au/next-app/` | 조회 UI·API·컴포넌트 (스켈레톤) |
| `.github/workflows/` | `au_crawl.yml` (workflow_dispatch, 저장소 루트) |

---

## 사전 준비 (문서 요약)

- **환경 변수** (프로젝트 최상단 `.env` 한 곳에 통일): `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY`, `GH_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`
- **데이터 소스 URL**: PBS API, TGA ARTG, Chemist Warehouse 검색, AusTender 검색 (문서 표 참고)
- **GitHub**: PAT(repo + workflow), 저장소 Secrets 연동

---

## 권장 구현 순서 (프롬프트 세트 v7)

문서의 **Cursor에 넣을 순서**를 따릅니다. DB가 막히면 이후 단계가 진행되므로 **PROMPT 6(Supabase)** 을 앞당겨 두는 것이 권장됩니다.

| 단계 | 프롬프트 | 비고 |
|------|-----------|------|
| 1 | PROMPT 1 | 폴더·파일 뼈대 |
| 2 | PROMPT 2 | `au_products.json` 8개 품목 |
| 3 | **PROMPT 6** | Supabase 연결·INSERT (우선) |
| 4 | PROMPT 3 | PBS / TGA |
| 5 | PROMPT 4 | Chemist / AusTender |
| 6 | PROMPT 5 | `product_summary` 매핑 |
| 7 | PROMPT 7 | GitHub Actions + `main()` |
| 8 | PROMPT 8 | Next.js 조회·트리거 API |
| 9 | PROMPT 9 | PDF 출력 |

---

## 진행 현황 (체크리스트)

| # | 항목 | 상태 |
|---|------|------|
| 1 | 폴더 구조 및 기본 파일 (PROMPT 1) | 완료 |
| 2 | `au_products.json` (PROMPT 2) | 완료 |
| 3 | PBS / TGA 수집 (PROMPT 3) | 완료 |
| 4 | Chemist / AusTender (PROMPT 4) | 완료 |
| 5 | product_summary 매핑 (PROMPT 5) | 완료 |
| 6 | Supabase INSERT (PROMPT 6) | 완료 |
| 7 | GitHub Actions (PROMPT 7) | 완료 |
| 8 | Next.js 조회·API (PROMPT 8) | 미완료 |
| 9 | PDF 출력 (PROMPT 9) | 미완료 |

---

## 업데이트 이력

### 2026-04-12

- **PROMPT 1 완료:** `upharma-au/` 이하에 문서에 정의된 폴더·파일 생성. Python/TS는 주석·시그니처·스텁만 포함(실구현 없음). `crawler/requirements.txt`에 httpx, selectolax, trafilatura, supabase-py, python-dotenv, tenacity 명시. `.github/workflows/au_crawl.yml`은 workflow_dispatch 스켈레톤만 배치(PROMPT 7에서 본 구현 예정).
- **README.md 최초 작성:** 본 문서로 진행 상황을 추적하기 시작함.
- **PROMPT 2 완료:** `upharma-au/crawler/au_products.json`에 8개 품목을 `products` 배열로 정의. `product_id`는 `au-{약어}-{번호}` TEXT 형식, `pricing_case`는 문서 기준(DIRECT 1~4, COMPONENT_SUM 5~6, ESTIMATE 7~8), `market_segment`는 모두 `public`. 완료 조건: `upharma-au`에서 `python -c "import json; data=json.load(open('crawler/au_products.json')); print(len(data['products']))"` → `8` 출력 확인.
- **PROMPT 6 완료:** `upharma-au/crawler/db/supabase_insert.py`에 `get_supabase_client()`(싱글턴·`create_client`), `upsert_product()`, `upsert_all()` 구현. 환경변수는 `SUPABASE_URL`·`SUPABASE_SERVICE_KEY`; 로컬에서는 프로젝트 루트 `.env`를 자동 탐색해 `python-dotenv`로 로드(이미 설정된 값은 유지). 테이블 DDL은 `crawler/db/australia_table.sql`에 동봉(Supabase SQL Editor에서 미적용 시 실행). 완료 조건: `upharma-au/crawler`에서 `python -c "from db.supabase_insert import get_supabase_client; ..."` → `Supabase 연결 성공: True` 확인.
- **PROMPT 3 완료:** `crawler/sources/pbs.py` — `Subscription-Key`만 사용, `/schedules`로 `schedule_code` 후 `/items`는 필터 없이 `page=1&limit=10` 첫 행만 파싱(`pbs_code`, `determined_price`/`claimed_price`). `crawler/sources/tga.py` — 검색 페이지에 ARTG 링크 있으면 `registered`·없으면 `not_registered`, 상세 요청 없음·스케줄은 `None` 허용.
- **PROMPT 4 완료:** `crawler/sources/chemist.py` — `fetch_chemist_price()`(셀렉터 2단계 + 본문 정규식 폴백, 가격 없으면 `None`), `build_sites()`(PBS·AusTender·Chemist·선택 PubMed URL 묶음). `crawler/sources/austender.py` — `fetch_austender()`로 계약 검색 테이블 첫 데이터 행에서 금액·공급자·일자 추출, 없거나 오류 시 필드 `None`·`austender_source_url`만 채운 dict. 공통: `httpx`+`selectolax`, UA·타임아웃 10초, 예외 삼킴. Chemist는 Cloudflare 등으로 정적 GET이 막히면 `None`이 될 수 있음(MVP는 Playwright 없음). 완료 조건: `crawler`에서 `python -c "from sources.chemist import fetch_chemist_price, build_sites; from sources.austender import fetch_austender; ..."` 오류 없이 실행.
- **PROMPT 5 완료:** `crawler/utils/enums.py`(ErrorType·PricingCase·ExportViable `str, Enum`), `utils/scoring.py`(`AU_REQUIRED_FIELDS`, `completeness_score`·치명 필드 감점), `utils/evidence.py`(`translate_to_korean`·`build_evidence_text`, OpenAI `gpt-4o-mini`, 키 없으면 번역 생략·원문 유지). `crawler/au_crawler.py`의 `build_product_summary()`가 PBS/TGA/Chemist/AusTender·`determine_export_viable`·`build_sites`·근거 텍스트를 단일 dict로 조립. 완료 조건: `crawler`에서 `python -c "from au_crawler import build_product_summary; ... au_products.json products[3] ..."` → `fob_estimated_usd is None`, `id`가 UUID 형식.
- **PROMPT 7 완료:** `.github/workflows/au_crawl.yml`(저장소 루트) — `workflow_dispatch`만, 입력 `product_filter`(필수), Ubuntu·Python 3.12, `upharma-au/requirements.txt` 설치 후 `upharma-au/crawler`에서 `python au_crawler.py`. Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY`, `PBS_SUBSCRIPTION_KEY`. `au_crawler.py`의 `main()`은 `PRODUCT_FILTER` 없으면 즉시 종료(전체 실행 없음), `au_products.json`에서 해당 `product_id` 1건만 TGA→PBS(복합은 `fetch_pbs_multi`+행 병합)→Chemist→AusTender→`build_product_summary`→`upsert_product` 순 실행. 완료 조건: Actions에서 워크플로 이름 **「호주 1공정 크롤러 실행」**이 보이고, 수동 실행 시 Supabase `australia`에 해당 행 upsert.

---

*참고: UPharma Export AI · KITA 무역AX 1기 · 한국유나이티드제약 5조 — 프롬프트 세트 v7 (2026-04-12)*


