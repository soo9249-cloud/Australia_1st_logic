# UPharma Export AI · Australia

호주 수출 분석 자동화 파이프라인입니다.

- 1단계: 시장조사(P1)
- 2단계: 가격산출/수출전략(P2)
- 3단계: 바이어 발굴(P3)
- 최종: 표지 + P2 + P3 + P1 병합 PDF

핵심 구현은 `upharma-au/render_api.py`, `upharma-au/report_generator.py`에 있습니다.

---

## README 목적

이 문서는 아래 3가지를 가장 빠르게 파악하기 위한 운영 문서입니다.

1. 이 프로젝트가 무엇을 자동화하는지(비즈니스 목적)
2. 보고서가 실제로 어떤 코드 경로를 타고 생성되는지(실행 흐름)
3. 장애가 났을 때 어디부터 확인해야 하는지(운영/디버깅 기준)

상세 스키마/이력 문서는 `upharma-au/REPORT_SCHEMA.md`, 내부 규칙/컨텍스트는 `CLAUDE.md`를 함께 참고합니다.

---

## 0) 빠른 시작

프로젝트 루트에서 실행합니다.

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python scripts/migrate.py
uvicorn render_api:app --app-dir upharma-au --reload --port 8000
```

필수 환경변수 예시:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `ANTHROPIC_API_KEY`
- `PERPLEXITY_API_KEY`
- `OPENAI_API_KEY`

---

## 아키텍처 한눈에

```text
Crawler/DB(au_products, seed) 
  -> FastAPI(render_api.py)
     -> AI 보강/계산(Claude Haiku, FOB 계산기, Perplexity 보조근거)
        -> PDF 렌더(report_generator.py)
           -> Supabase 이력/결과 저장
              -> 최종 병합(P2 + P3 + P1 + Cover)
```

---

## 1) 단계별 파이프라인 개요

### 1-1. 시장조사(P1)

- 엔드포인트: `POST /api/report/generate`
- 입력: `{"product_id":"au-hydrine-004"}`
- 동작:
  - `au_products`에서 품목 row 조회
  - Claude v8 블록 생성 (실패 시 rule-based fallback)
  - 참고근거(refs): Semantic Scholar -> PubMed -> Perplexity 하이브리드
  - `report_generator`로 P1 PDF 생성
  - `au_reports_history` 및 `au_products` 관련 필드 업데이트

추가 규칙:

- PBS 제한급여/Authority 단서는 DB + Perplexity 보조단서 기반으로 반영
- 데이터 근거 없는 문구는 생성하지 않도록 보호 로직 포함

주요 코드 경로:

- 진입: `generate_report()` -> `_generate_report_core()`
- 보강: `_fetch_refs_hybrid()`, `_enrich_v8_blocks_from_au_row()`
- PDF: `report_generator.py`의 P1 렌더 함수 호출

### 1-2. 가격산출/수출전략(P2)

- 시작: `POST /api/p2/pipeline`
- 상태: `GET /api/p2/pipeline/status`
- 결과: `GET /api/p2/pipeline/result`

입력:

- 권장: `{"product_id":"au-hydrine-004"}`
- `market` 파라미터는 하위호환이며 현재는 공공+민간 동시 산출

동작:

- Stage2 seed + FOB 계산(dispatch) + AI 블록을 결합
- 공공(`public`) / 민간(`private`) 각각 PDF 생성
- 3개 가격 시나리오(저가/기준/프리미엄) 근거 문장을 강제 보강
- `au_reports_r2`에 결과 저장

주요 코드 경로:

- 진입: `p2_pipeline()`
- 실행 워커: `_p2_pipeline_worker_both()`
- 근거 강제: `_enforce_p2_evidence_anchors()`
- PDF: `report_generator.py`의 `render_p2_pdf()`

### 1-3. 바이어발굴(P3)

- 시작: `POST /api/p3/buyers/run`
- 상태: `GET /api/p3/buyers/status?job_id=...`
- 결과: `GET /api/p3/buyers/result?job_id=...`

입력:

- `{"product_id":"au-hydrine-004"}`

동작:

- Stage1 필터 -> Stage2 점수화/순위화 -> `au_buyers` UPSERT -> P3 PDF 생성
- TOP10이 부족한 경우 유통 파트너 보충 로직 적용
- 바이어 제외 조건은 프롬프트 + 코드 하드필터 이중 적용

주요 코드 경로:

- 진입: `p3_buyers_run()`
- 실행 워커: `_p3_worker()`
- 필터/점수화: `buyer_discovery/stage1_filter.py`, `buyer_discovery/stage2_scoring.py`
- PDF: `report_generator.py`의 `render_buyers_pdf()`

---

## 1-4) 최종 보고서 생성 흐름(코드 기준)

- 엔드포인트: `POST /api/final-report`
- 핵심 함수: `final_report_generate()`
- 동작 순서:
  1) 병합 대상 `product_id` 추론
  2) 최신 P2/P3/P1 PDF 탐색
  3) 표지 PDF 생성
  4) 최종 병합(**표지 -> P2 -> P3 -> P1**)
  5) 다운로드 URL 반환

---

## 2) 바이어 제외 조건(가중치와 별개)

실제 제외 로직은 아래 두 층에서 적용됩니다.

1) Perplexity 조사 프롬프트 정책

- API-only 원료 회사 제외
- 다국적 글로벌 대형사 제외
- 오리지널-only 회사 제외
- 완제품(FDF, 특히 ETC/Rx) + 호주 현지 유통/제조 가능 회사 우대

2) Stage2 코드 하드필터 (`buyer_discovery/stage2_scoring.py`)

- `_exclude_reason_from_hard_filter()`에서 실제 제거
- 제외 코드: `api_only`, `multinational_global`, `original_innovator`

---

## 2-1) 메인 폴더 구조

```text
Australia_1st_logic/
├── scripts/                    # DB 마이그레이션(migrate.py, migrations/*.sql) 등
├── upharma-au/
│   ├── render_api.py           # FastAPI 앱·P1/P2/P3/최종 API
│   ├── report_generator.py     # P1·P2·P3·표지·병합 PDF
│   ├── stage1_schema.py        # P1 스키마(v8)
│   ├── stage2/                 # FOB·가격 시드·fob_calculator
│   ├── buyer_discovery/        # 바이어 파이프라인(stage1/2, seeds, Perplexity 등)
│   ├── crawler/                # au_products 시드·PBS/TGA 등 소스, db/
│   ├── static/, templates/     # UI 정적·템플릿
│   ├── fonts/                  # PDF 폰트
│   ├── REPORT_SCHEMA.md
│   └── (기타: stage2_scoring.json 저장 경로는 buyer_discovery/seeds 등)
├── requirements.txt
├── render.yaml
├── README.md
└── CLAUDE.md (내부 AI 컨텍스트)
```

---

## 3) 주요 파일

- `upharma-au/render_api.py`: FastAPI 엔드포인트 + 파이프라인 오케스트레이션
- `upharma-au/report_generator.py`: P1/P2/P3 PDF 렌더링
- `upharma-au/stage1_schema.py`: 시장조사(P1) 스키마(v8 포함)
- `upharma-au/stage2/`: FOB 계산/가격 시드
- `upharma-au/buyer_discovery/`: 바이어 수집/필터/점수화
- `upharma-au/REPORT_SCHEMA.md`: 스키마 정리 문서

---

## 4) 운영 체크리스트

- P1/P2/P3 실행 전 `product_id`가 `au_products.product_code`와 일치하는지 확인
- P2는 완료 후 반드시 `result`를 호출해야 상태가 `idle`로 리셋됨
- 최종 병합 전 P1/P2/P3 PDF가 모두 생성되어 있어야 함
- AI 키 누락 시 일부 단계는 fallback으로 동작하지만, 품질 저하 가능
- 상세 장애 대응은 아래 `장애 대응 플레이북` 섹션 참고
- FOB 계산 로직 수정 전/후에는 아래 `FOB 검증용 테스트`를 반드시 실행

---

## 4-1) FOB 검증용 테스트 (유지 대상)

`upharma-au/stage2/test_fob_calculator.py`는 삭제 대상이 아닙니다.

- 목적: `stage2/fob_calculator.py`의 핵심 공식(Logic A/B, 시나리오, 경계값, 예외처리) 회귀 방지
- 성격: 운영 런타임 import 대상은 아니지만, 수치 안정성 검증에 필수
- 권장 시점: FOB 계산기 수정 직후, 배포 전

실행:

```bash
python -m stage2.test_fob_calculator
```

또는:

```bash
python upharma-au/stage2/test_fob_calculator.py
```

---

## 5) 장애 대응 플레이북

### 6-1. P1 시장조사 생성 실패

증상:

- `POST /api/report/generate`가 4xx/5xx 반환

우선 점검:

1. 요청 body에 `product_id`가 있는지
2. `au_products`에 해당 `product_code` 행이 있는지
3. `ANTHROPIC_API_KEY` 누락 여부(누락 시 fallback 동작 확인)
4. Supabase update 실패 메시지(RLS/컬럼 누락) 로그

조치:

- 키/DB 연결 복구 후 재실행
- AI 실패 시 fallback 결과라도 생성되는지 먼저 확인

### 6-2. P2 파이프라인이 끝나지 않음

증상:

- `GET /api/p2/pipeline/status`가 계속 `running`
- 또는 `result` 호출 시 409

우선 점검:

1. `p2_pipeline` 중복 실행 여부(`already_running`)
2. 워커 로그에서 Haiku 스키마 검증 경고/오류 여부
3. `stage2` 모듈 로드 오류 여부

조치:

- status가 `done`이 될 때까지 폴링
- 완료 후 반드시 `GET /api/p2/pipeline/result` 호출(상태 idle 리셋)
- 필드 누락 경고가 반복되면 `render_api.py`의 P2 후처리/기본값 보강 로직 점검

### 6-3. P3 바이어 결과가 비정상(후보 수 부족/기대한 업체 제외)

증상:

- TOP10이 부족하거나 예상 기업이 제외됨

우선 점검:

1. `buyer_discovery/stage2_scoring.py`의 `_exclude_reason_from_hard_filter()` 적용 여부
2. `stage2_scoring_report.json`의 `ranking_meta.shortfall_reason`/fallback 기록
3. 하드코드 시트(`au_buyers_hardcode.json`)의 notes/팩토리 정보

조치:

- 제외 정책(API-only/다국적/오리지널-only)이 의도와 맞는지 먼저 확인
- 부족 시 distributor fallback이 붙었는지 확인
- 데이터 근거 수정 후 P3 재실행

### 6-4. 최종 보고서 병합 실패(409/500)

증상:

- `POST /api/final-report`가 409 또는 500 반환

우선 점검:

1. P1/P2/P3 PDF가 모두 존재하는지
2. `product_id` 추론 실패 여부(가능하면 요청에 명시 전달)
3. `pypdf` 병합 단계 오류 로그

조치:

- P1/P2/P3를 먼저 생성 완료 후 최종 병합 재실행
- 가능하면 `{"product_id":"..."}`를 명시해서 호출

### 6-5. 보고서 문구가 어색하거나 잘림

증상:

- 문장이 중간에 끊기거나 근거 약한 전략 문구 출력

우선 점검:

1. `render_api.py`의 프롬프트/후처리(근거 앵커, 제한급여 단서 보강)
2. `report_generator.py`의 `_trunc` 길이와 표/문단 배치

조치:

- 문구 품질은 `render_api.py`에서, 레이아웃/절단은 `report_generator.py`에서 분리해 수정

---

## 6) 실행 런북 (curl 예시)

기본 주소:

- `http://127.0.0.1:8000`

예시 품목:

- `au-hydrine-004`

### 7-1. P1 시장조사 생성

```bash
curl -X POST "http://127.0.0.1:8000/api/report/generate" ^
  -H "Content-Type: application/json" ^
  -d "{\"product_id\":\"au-hydrine-004\"}"
```

### 7-2. P2 가격산출 파이프라인 시작

```bash
curl -X POST "http://127.0.0.1:8000/api/p2/pipeline" ^
  -H "Content-Type: application/json" ^
  -d "{\"product_id\":\"au-hydrine-004\"}"
```

상태 확인:

```bash
curl "http://127.0.0.1:8000/api/p2/pipeline/status"
```

완료 후 결과 조회(중요: 조회해야 idle 리셋):

```bash
curl "http://127.0.0.1:8000/api/p2/pipeline/result"
```

### 7-3. P3 바이어발굴 시작

```bash
curl -X POST "http://127.0.0.1:8000/api/p3/buyers/run" ^
  -H "Content-Type: application/json" ^
  -d "{\"product_id\":\"au-hydrine-004\"}"
```

`job_id`를 받은 뒤 상태 확인:

```bash
curl "http://127.0.0.1:8000/api/p3/buyers/status?job_id=p3_xxxxxxxxxxxx"
```

완료 후 결과 조회:

```bash
curl "http://127.0.0.1:8000/api/p3/buyers/result?job_id=p3_xxxxxxxxxxxx"
```

### 7-4. 최종 보고서 병합 생성

```bash
curl -X POST "http://127.0.0.1:8000/api/final-report" ^
  -H "Content-Type: application/json" ^
  -d "{\"product_id\":\"au-hydrine-004\"}"
```

### 7-5. PDF 다운로드

`download_url` 또는 `pdf_filename`을 받은 뒤:

```bash
curl -L "http://127.0.0.1:8000/api/report/download?name=au_final_report_au-hydrine-004_YYYYMMDD_HHMMSS.pdf" -o final_report.pdf
```
