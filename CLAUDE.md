# CLAUDE.md — Australia_1st_logic 프로젝트 컨텍스트

## 프로젝트 개요

한국 유나이티드 제약(UPharma)의 **호주 수출 시장조사 자동화 파이프라인**.
1공정(시장조사 크롤링) → 2공정(FOB 역산 기반 수출 전략 제안서 자동 생성)까지 커버.

### 기술 스택
- **백엔드**: FastAPI (Python 3.11/3.12), uvicorn
- **프론트엔드**: Vanilla JS + Jinja2 (templates/index.html, static/app.js)
- **DB**: Supabase (`australia` 테이블 73컬럼, `australia_p2_results` 테이블)
- **PDF**: reportlab (report_generator.py)
- **AI**: Anthropic Claude API (반드시 Haiku만 — 아래 규칙 참고)
- **배포**: Render (render.yaml), GitHub Actions (au_crawl.yml)

---

## 절대 규칙 (반드시 지킬 것)

### 1. Claude API는 반드시 Haiku 모델만 사용
모든 Anthropic API 호출은 `claude-haiku-4-5-20251001` 고정.
Sonnet/Opus 절대 금지. 예외 없음. 비용 통제 및 제품 요구사항.
- render_api.py, 크롤러, 보고서 생성, 2공정 AI 파이프라인 등 모든 AI 호출 지점 공통 적용
- 설정 파일·환경변수에 Haiku 기본값 박아두고 다른 모델 fallback도 만들지 말 것

### 2. 모든 코드성 라벨은 한국어 괄호 설명 병기
UI, 보고서, 로그 등 모든 텍스트에서 축약 용어에 한국어 설명 필수.
사용자(Jisoo)는 비개발자이므로 코드 그대로 노출하면 이해 불가.
- 내부 분류: "Case A (완전일치)", "Case B (TGA 미등재)", "Case C (복합제 부분등재)", "Case D (함량·제형만 다름)"
- 호주 공식 용어: "S85 (일반 처방약 섹션)", "ARTG (호주 의약품 등록 시스템)", "PBAC (약값 심사 위원회)", "AEMP (정부 승인 출고가)", "DPMQ (최대처방량 총약가)"
- 한 문서 안에서 반복 등장해도 최소 섹션별 1회는 괄호 설명 유지

### 3. 중복 파일 생성 금지
기존 파일 구조 유지. 새 파일 함부로 만들지 말 것.
(과거에 au_price_crawler.py, au_products_v2.json 만들었다가 삭제한 전례 있음)

---

## 프로젝트 구조

```
Australia_1st_logic/
├── README.md                    # 프로젝트 문서 (v2.5, 1053줄)
├── render.yaml                  # Render 배포 설정
├── requirements.txt
├── copy.env                     # 환경변수 템플릿
├── scripts/
│   ├── migrate.py               # Supabase DDL 배포
│   └── deploy_render.py         # Render 트리거
├── .github/workflows/
│   └── au_crawl.yml             # GitHub Actions
└── upharma-au/                  # 메인 프로젝트
    ├── render_api.py            # FastAPI 서버 (모든 엔드포인트, ~2100줄)
    ├── report_generator.py      # PDF 생성 (render_pdf, render_p2_pdf, ~640줄)
    ├── templates/index.html     # Jinja2 UI
    ├── static/app.js            # 프론트엔드 로직
    ├── crawler/
    │   ├── au_crawler.py        # 크롤링 파이프라인 오케스트레이터
    │   ├── au_products.json     # 8개 품목 마스터 데이터
    │   ├── sources/
    │   │   ├── tga.py           # TGA ARTG 크롤러
    │   │   ├── pbs.py           # PBS API 크롤러
    │   │   ├── chemist.py       # Chemist Warehouse 크롤러
    │   │   ├── buynsw.py        # buy.nsw.gov.au 크롤러
    │   │   └── healthylife.py   # Healthylife 크롤러
    │   ├── utils/
    │   │   ├── enums.py
    │   │   ├── evidence.py
    │   │   ├── inn_normalize.py
    │   │   └── scoring.py
    │   └── db/
    │       ├── supabase_insert.py
    │       └── __init__.py
    └── stage2/
        ├── fob_calculator.py           # FOB 역산 계산기
        ├── test_fob_calculator.py      # 단위 테스트
        └── fob_reference_seeds.json    # 가격 시나리오 시드
```

---

## 2공정 AI 파이프라인 (구현 완료)

### 핵심 코드
- `render_api.py`: `_haiku_p2_blocks()` (8블록 Haiku 어댑터) + `_p2_pipeline_worker()` (백그라운드 스레드)
- 엔드포인트: `/api/p2/pipeline`, `/api/p2/pipeline/status`, `/api/p2/pipeline/result`
- `report_generator.py`: `render_p2_pdf()` (2페이지 PDF — 추출정보/FOB 3시나리오/전략/리스크/포지셔닝/면책)
- Supabase: `australia_p2_results` 테이블 UPSERT

### 확정된 시나리오 라벨
- aggressive → "저가 진입 시나리오 (Penetration Pricing)" — 마진 10%
- average → "기준가 기반 시나리오 (Reference Pricing)" — 마진 20%
- conservative → "프리미엄 시나리오 (Premium Pricing)" — 마진 30%

---

## 현재 상태 및 남은 작업 (2026-04-18 기준)

### ✅ 완료된 작업 (2026-04-18 세션)
1. 크롤러 합병 4개 항목 반영 완료 (render_api.py sys.path, pbs.py, chemist.py 옵션A, au_crawler.py healthylife)
2. 소매가 추정 로직 4단계 구현 완료:
   - Supabase ALTER (chemist_price_aud, retail_estimation_method 컬럼 추가)
   - au_crawler.py `_estimate_retail_price()` — PBS→DPMQ / 미등재→Chemist×1.20 / fallback
   - fob_calculator.py Logic B `crawler_row` 2순위 fallback
   - render_api.py 연결 (`dispatch_by_pricing_case(seed, crawler_row=row)`)
3. CSS 이식 완료 (styles.css +479줄 append, 기존 0줄 변경)
4. HTML 전면 교체 완료 (Stage 1 — 싱가포르 아코디언 구조 + 호주 내용)

### 🔄 진행 중 — 프론트엔드 싱가포르 UI/UX 전면 이식
소스: `frontend_0417/` (Desktop, 팀원이 만든 싱가포르 공통 템플릿)
원칙: UI/UX는 싱가포르와 완전 통일, 내용만 호주. 백엔드 수정 가능.

- ✅ Stage 0: 결정 확정 (매크로 하드코딩, API키배지 삭제, 신약폼 삭제, 직접입력+GST 유지)
- ✅ Stage 1: templates/index.html 전면 교체 (540줄)
- 🔄 Stage 2: static/app.js 싱가포르 베이스 이식 + 호주 치환
- ⬜ Stage 3: API 매핑 재작성 (호주 render_api.py 엔드포인트에 맞춤)
- ⬜ Stage 4: 호주 전용 기능 재통합 (직접입력 탭, GST 로직)
- ⬜ Stage 5: 로컬 통합 테스트 · 디버깅
- ⬜ Stage 6: README 변경 이력 기록

### 프론트 이식 완료 후 남은 작업
1. Phase 5 — 로컬 uvicorn 동작 테스트
2. 2공정 역산 로직 재검토/수정
3. PDF 보고서 양식 수정
4. 보고서 작성 프롬프트 설계 (Haiku 지시문)
5. README v2.6 갱신

### 보류 중
- Omethyl 크롤러 연동 — healthylife.py → au_crawler.py 연결 (retail_price_aud 채우기)

---

## 사용자 정보
- 이름: Jisoo
- 역할: 비개발자, 제약사 수출 담당
- 선호: 한국어 커뮤니케이션, 코드 용어에 항상 쉬운 설명 병기
- 변경사항은 매번 명시적으로 리포트
