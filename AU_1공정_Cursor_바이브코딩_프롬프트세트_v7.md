# 호주 1공정 MVP — Cursor 바이브코딩 프롬프트 세트

> **목적:** 한국 제약회사(한국유나이티드제약)의 수출 지원 자동화 시스템.  
> 호주 시장 8개 품목의 인허가·가격 정보를 실시간 수집 → Supabase 저장 → Next.js 화면 조회 → PDF 시장조사 보고서 출력.  
> **최종 목표:** 2시간 안에 크롤링 → Supabase 저장 → Actions 실행 → Next.js 조회까지 동작하는 MVP.

---

## 📋 내가 먼저 준비할 것 (프롬프트 시작 전)

| 항목 | 확인 방법 |
|------|---------|
| PBS API URL | `https://data-api.health.gov.au/pbs/api/v3/schedules` curl 확인 |
| TGA ARTG URL | `https://www.tga.gov.au/resources/artg?s=hydroxyurea` 브라우저 확인 |
| Chemist Warehouse 검색 URL | `https://www.chemistwarehouse.com.au/search?searchstr=` 확인 |
| AusTender 검색 URL | `https://www.austender.gov.au/contract/search` 확인 |
| Supabase URL + service_role key | Supabase → Settings → API |
| OpenAI API key | OpenAI 대시보드 |
| GitHub PAT | GitHub → Settings → Developer Settings → PAT (repo + workflow 권한) |

**.env 파일 (최상단 1개로 통일)**
```
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
OPENAI_API_KEY=
GH_TOKEN=
GITHUB_OWNER=
GITHUB_REPO=
```

**.gitignore**
```
.env
```

---

## PROMPT 1 — 폴더 구조 및 기본 파일 생성

```
너는 한국 제약회사 수출 자동화 시스템의 시니어 개발자다.
아래 스펙대로 프로젝트 폴더 구조와 기본 파일을 생성해라.

[프로젝트 목적]
호주 의약품 시장조사 자동화 시스템.
8개 품목의 TGA 인허가 + PBS 약가 + 민간 소매가를 수집해서
Supabase에 저장하고, Next.js로 조회하고, PDF 보고서를 출력한다.

[생성할 폴더 구조]
upharma-au/
├── crawler/
│   ├── au_crawler.py          # 메인 크롤러 (나중에 채울 것)
│   ├── au_products.json       # 8개 품목 정의 (나중에 채울 것)
│   ├── sources/
│   │   ├── pbs.py             # PBS 수집 함수
│   │   ├── tga.py             # TGA 수집 함수
│   │   ├── chemist.py         # Chemist Warehouse 파싱 함수
│   │   └── austender.py       # AusTender 파싱 함수
│   ├── utils/
│   │   ├── enums.py           # ErrorType, PricingCase, ExportViable enum
│   │   ├── scoring.py         # completeness_score 함수
│   │   └── evidence.py        # evidence_text 생성 함수
│   ├── db/
│   │   └── supabase_insert.py # Supabase INSERT 함수
│   └── requirements.txt
├── .github/
│   └── workflows/
│       └── au_crawl.yml       # workflow_dispatch 트리거
└── next-app/
    ├── pages/
    │   ├── au/
    │   │   └── index.tsx      # 품목 카드 목록 페이지
    │   └── api/
    │       ├── trigger.ts     # workflow_dispatch 호출
    │       └── au/
    │           └── products.ts # Supabase 조회
    ├── components/
    │   ├── ProductCard.tsx
    │   ├── ProductDetail.tsx
    │   └── PdfReport.tsx
    └── lib/
        └── supabase.ts        # Supabase 클라이언트

[각 파일에 넣을 것]
- 모든 .py 파일: 상단에 파일 목적 주석 + 비어있는 함수 시그니처만
- 모든 .tsx/.ts 파일: 상단에 파일 목적 주석 + 빈 컴포넌트/함수 뼈대만
- requirements.txt: httpx, selectolax, trafilatura, supabase-py, python-dotenv, tenacity

[제약]
- 실제 구현 코드는 아직 채우지 마라. 뼈대만.
- 각 파일 상단에 한국어 주석으로 파일 역할을 한 줄 설명해라.
- 공통 라이브러리 만들지 마라. 파일 간 import는 최소화.
```

**✅ 완료 조건:** 폴더 구조가 생성되고 모든 파일이 존재하는 것을 확인.

---

## PROMPT 2 — au_products.json 작성

```
crawler/au_products.json 파일을 아래 스펙대로 작성해라.

[목적]
호주 1공정 시장조사 대상 8개 품목 정의 파일.
크롤러가 이 파일을 읽어서 각 품목별로 수집을 수행한다.

[8개 품목 데이터]
1. 제품명: Omethyl Cutielet / 성분: Omega-3-Acid Ethyl Esters 90 / 규격: 2g / 제형: capsule / HS: 300490
2. 제품명: Gadvoa Inj. / 성분: Gadobutrol / 규격: 604.72mg / 제형: injection / HS: 300640
3. 제품명: Sereterol Activair / 성분: Fluticasone + Salmeterol / 규격: 복합 / 제형: inhaler / HS: 300460
4. 제품명: Hydrine / 성분: Hydroxyurea / 규격: 500mg / 제형: capsule / HS: 300490
5. 제품명: Rosumeg Combigel / 성분: Rosuvastatin + Omega-3-EE90 / 규격: 복합 / 제형: capsule / HS: 300490
6. 제품명: Atmeg Combigel / 성분: Atorvastatin + Omega-3-EE90 / 규격: 복합 / 제형: capsule / HS: 300490
7. 제품명: Ciloduo / 성분: Cilostazol + Rosuvastatin / 규격: 복합 / 제형: tablet / HS: 300490
8. 제품명: Gastiin CR / 성분: Mosapride Citrate / 규격: 15mg / 제형: tablet / HS: 300490

[각 품목 JSON 구조]
{
  "product_id": "제품명 기반 고정 TEXT ID로 직접 작성해라. 예: 'au-omethyl-001'. UUID 형식 금지.",
  "product_name_ko": "제품명",
  "inn_normalized": "성분 INN 소문자",
  "inn_components": ["성분1", "성분2"],  // 복합제는 배열, 단일성분은 1개
  "strength": "규격",
  "dosage_form": "제형",
  "hs_code_6": "HS코드",
  "pricing_case": "DIRECT | COMPONENT_SUM | ESTIMATE",
  "pbs_search_terms": ["PBS 검색어1", "PBS 검색어2"],  // 성분명 기준
  "tga_search_terms": ["TGA 검색어"],
  "market_segment": "public"  // 호주는 전부 public으로 시작
}

[Pricing Case 배정 기준]
- PBS 등재 단일 성분 → DIRECT (1,2,3,4번)
- 복합제, 개별 성분은 PBS 등재 → COMPONENT_SUM (5,6번)
- 복합제 ARTG 미등재 또는 성분 자체 미승인 → ESTIMATE (7,8번)

[제약]
- product_id는 TEXT 타입. 'au-{제품명약어}-{번호}' 형식으로 8개 직접 작성. (예: au-omethyl-001, au-gadvoa-002)
- inn_normalized는 preon 라이브러리 기준 소문자 INN명으로.
- 파일은 products 배열 하나만 가진 JSON이어야 한다.
```

**✅ 완료 조건:** `python -c "import json; data=json.load(open('crawler/au_products.json')); print(len(data['products']))"` 실행 시 `8` 출력.

---

## PROMPT 3 — PBS / TGA 수집 함수 작성

```
crawler/sources/pbs.py 와 crawler/sources/tga.py 를 작성해라.

[공통 제약]
- httpx 사용. requests 금지.
- LLM 호출 없음. 결정론적 파싱만 수행.
- 함수 하나당 역할 하나. 과설계 금지.
- 에러는 try/except로 잡고 None 반환. raise 금지.
- 타임아웃: httpx.get(..., timeout=10)
- .env의 환경변수(PBS_SUBSCRIPTION_KEY)를 사용하기 위해 python-dotenv와 os.getenv 활용.

━━━━━━━━━━━━━━━━━━━━━━━━
[pbs.py 작성 스펙]
━━━━━━━━━━━━━━━━━━━━━━━━
PBS(Pharmaceutical Benefits Scheme)는 호주 정부의 공식 약가 공개 데이터다.
공개 REST API(v3)를 사용하며, 아까 확인된 '공용 구독 키'를 반드시 포함해야 한다.

[⚠️ PBS API 실제 특이사항 — 반드시 반영]
- 인증: .env에서 PBS_SUBSCRIPTION_KEY를 읽어와 Header의 'Ocp-Apim-Subscription-Key'에 설정할 것.
  (현재 공용 키: )
- 전략: 
  1) /schedules 엔드포인트로 최신 schedule_code를 먼저 가져온다.
  2) /items?schedule_code={code}&filter=DRUG_NAME+like+'{ingredient}' 로 성분 필터링.
- Rate limit: 1요청/20초 준수. 요청 전후에 time.sleep(21) 반드시 추가.

함수 1: fetch_latest_schedule_code() -> str | None
- GET https://data-api.health.gov.au/pbs/api/v3/schedules
- 최신 schedule_code (예: "202604") 반환.

함수 2: fetch_pbs_by_ingredient(ingredient: str) -> dict | None
- 입력: INN 성분명 (예: "hydroxyurea")
- GET https://data-api.health.gov.au/pbs/api/v3/items
- 응답 JSON에서 DPMQ_PRICE(float), PBS_CODE, DRUG_NAME, RESTRICTION_TEXT 파싱.
- 반환: {
    "pbs_item_code": str | None,
    "pbs_listed": bool,
    "pbs_price_aud": float | None,
    "pbs_source_url": str,
    "restriction_text": str | None
  }

━━━━━━━━━━━━━━━━━━━━━━━━
[tga.py 작성 스펙]
━━━━━━━━━━━━━━━━━━━━━━━━
TGA(Therapeutic Goods Administration)는 호주 의약품 인허가 기관이다.
ARTG 등재 여부를 확인한다.

[⚠️ TGA 실제 특이사항 — 반드시 반영]
- API가 없으므로 웹 크롤링 수행. 파싱은 selectolax.parser.HTMLParser 사용.
- 검색 URL: https://www.tga.gov.au/resources/artg?s={ingredient}
- 필수 로직: 
  1) 검색 결과 테이블의 첫 번째 행에서 상세 페이지 URL(artg_id 포함)을 추출한다.
  2) 상세 페이지(https://www.tga.gov.au/resources/artg/{artg_id})로 추가 GET 요청을 보낸다.
  3) 상세 페이지 내에서 'Schedule' 정보(S2, S3, S4, S8 등)를 반드시 파싱한다.

함수 1: fetch_tga_artg(ingredient: str) -> dict | None
- 결과 있으면 registered, 없으면 not_registered.
- 반환: {
    "artg_number": str | None,
    "artg_status": "registered" | "not_registered",
    "tga_schedule": str | None,
    "tga_sponsor": str | None,
    "artg_source_url": str
  }

함수 2: determine_export_viable(artg_result: dict) -> dict
- schedule == "S8" -> not_viable, SCHEDULE_8
- artg_status == "registered" -> viable, ARTG_REGISTERED
- 그 외 -> not_viable, TGA_NOT_APPROVED
- 반환: { "export_viable": str, "reason_code": str }

**✅ 완료 조건:**
```bash
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
from crawler.sources.pbs import fetch_pbs_by_ingredient
from crawler.sources.tga import fetch_tga_artg, determine_export_viable

ingredient = 'hydroxyurea'
print(f'\n🚀 [테스트 시작] 성분명: {ingredient}')

# 1. PBS 테스트
print('1. PBS API 호출 (21초 대기 발생)...')
pbs = fetch_pbs_by_ingredient(ingredient)
print('   결과:', pbs if pbs else '❌ 실패 (키 또는 통신 확인)')

# 2. TGA 테스트
print('2. TGA 크롤링 및 상세 페이지 분석...')
tga = fetch_tga_artg(ingredient)
if tga:
    print(f'   결과: 등록번호 {tga[\'artg_number\']}, 스케줄 {tga[\'tga_schedule\']}, 스폰서 {tga[\'tga_sponsor\']}')
    viable = determine_export_viable(tga)
    print(f'   판단: {viable[\'export_viable\']} ({viable[\'reason_code\']})')
else:
    print('   결과: ❌ 실패 (HTML 구조 변경 확인 필요)')
"
```
오류 없이 dict 또는 None 반환되면 통과.

---

## PROMPT 4 — Chemist Warehouse / AusTender 파싱 함수 작성

```
crawler/sources/chemist.py 와 crawler/sources/austender.py 를 작성해라.

[공통 제약]
- httpx + selectolax 사용
- User-Agent 헤더 반드시 포함 (봇 차단 방지)
  → "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
- LLM 호출 없음
- 타임아웃: 10초
- 파싱 실패 시 None 반환, 예외 전파 금지

━━━━━━━━━━━━━━━━━━━━━━━━
[chemist.py 작성 스펙]
━━━━━━━━━━━━━━━━━━━━━━━━
Chemist Warehouse는 호주 최대 민간 약국 체인이다.
소매 판매가를 수집하는 용도로 사용한다.

함수: fetch_chemist_price(search_term: str) -> dict | None
- 입력: 검색어 (성분명 또는 제품명)
- GET https://www.chemistwarehouse.com.au/search?searchstr={search_term}
- selectolax로 첫 번째 검색 결과의 가격 파싱
- 파싱 대상 셀렉터 (Triple Fallback 순서대로 시도):
  1) span.product-price (또는 실제 클래스명) — 하드코딩 셀렉터
  2) div[data-price] — 속성 기반 셀렉터
  3) 위 두 가지 모두 실패 시 → HTML 전체 텍스트에서 정규식으로 `\$\d+\.\d{2}` 형태의 가격 패턴을 탐색하고, 가격 주변 텍스트(앞뒤 30자)를 분석해 가장 첫 번째 매칭값을 반환하는 Fallback 로직을 추가해라.
  최종 실패 시만 None 반환.
- 반환: {
    "retail_price_aud": float | None,
    "price_unit": str,              // "per pack" 등
    "price_source_name": "Chemist Warehouse",
    "price_source_url": str         // 실제 상품 URL
  }

[주의]
Chemist Warehouse는 JavaScript 렌더링이 있을 수 있다.
이번 MVP에서는 정적 HTML 파싱만 시도하고, 실패 시 None으로 처리한다.
Playwright는 사용하지 않는다.

━━━━━━━━━━━━━━━━━━━━━━━━
[austender.py 작성 스펙]
━━━━━━━━━━━━━━━━━━━━━━━━
AusTender는 호주 정부 조달 공고 사이트다.
병원·정부기관 납품 계약가 정보를 수집하는 용도로 사용한다.

함수: fetch_austender(search_term: str) -> dict | None
- 입력: 검색어 (성분명)
- GET https://www.austender.gov.au/contract/search?keyword={search_term}
- selectolax로 첫 번째 계약 결과 파싱
- 파싱 대상: 계약금액, 공급업체명, 계약일자
- 반환: {
    "contract_value_aud": float | None,
    "supplier_name": str | None,
    "contract_date": str | None,
    "austender_source_url": str
  }
- 결과 없으면 전체 None dict 반환 (오류 아님)

[sites 배열 구성 함수]
함수: build_sites(pbs_url, tga_url, chemist_url, austender_url, pubmed_url=None) -> dict
- 반환:
  {
    "public_procurement": [{"name": "PBS", "url": pbs_url}, {"name": "AusTender", "url": austender_url}],
    "private_price": [{"name": "Chemist Warehouse", "url": chemist_url}],
    "paper": [{"name": "PubMed", "url": pubmed_url}] if pubmed_url else []
  }
- 이 함수는 chemist.py에 같이 넣어도 된다.
```

**✅ 완료 조건:**
```bash
python -c "
from sources.chemist import fetch_chemist_price, build_sites
from sources.austender import fetch_austender
print(fetch_chemist_price('hydroxyurea'))
print(fetch_austender('hydroxyurea'))
"
```
오류 없이 실행, None 이어도 통과 (파싱 실패는 허용).

---

## PROMPT 5 — product_summary 스키마 매핑 함수 작성

```
crawler/utils/enums.py, crawler/utils/scoring.py, crawler/utils/evidence.py 를 작성하고,
crawler/au_crawler.py 에서 각 소스 결과를 하나의 product_summary dict로 조립하는
build_product_summary() 함수를 작성해라.

━━━━━━━━━━━━━━━━━━━━━━━━
[enums.py]
━━━━━━━━━━━━━━━━━━━━━━━━
from enum import Enum

class ErrorType(str, Enum):
    AUTH_FAIL   = "auth_fail"
    RATE_LIMIT  = "rate_limit"
    WAF_BLOCK   = "waf_block"
    PARSE_ERROR = "parse_error"
    TIMEOUT     = "timeout"

class PricingCase(str, Enum):
    DIRECT        = "DIRECT"         # PBS 공시가 직접 수집
    COMPONENT_SUM = "COMPONENT_SUM"  # 복합제 성분별 합산
    ESTIMATE      = "ESTIMATE"       # 민간가 추정 또는 수집 불가

class ExportViable(str, Enum):
    VIABLE      = "viable"
    CONDITIONAL = "conditional"
    NOT_VIABLE  = "not_viable"

━━━━━━━━━━━━━━━━━━━━━━━━
[scoring.py]
━━━━━━━━━━━━━━━━━━━━━━━━
AU_REQUIRED_FIELDS = [
    "artg_number", "tga_schedule", "pbs_item_code",
    "retail_price_aud", "price_source_url",
    "export_viable", "dosage_form"
]
# fob_estimated_usd는 1공정에서 항상 null → 감점 제외

def completeness_score(data: dict, base: float = 0.95) -> float:
    # 필드 채움률 계산
    # 치명 필드 미충족 시 추가 감점:
    #   artg_number 없으면 -0.20
    #   retail_price_aud 없으면 -0.15
    # 반환: 0~0.95 범위 DECIMAL, round 2자리

━━━━━━━━━━━━━━━━━━━━━━━━
[evidence.py]
━━━━━━━━━━━━━━━━━━━━━━━━
# 환경변수: OPENAI_API_KEY

def translate_to_korean(text: str) -> str:
    # OpenAI GPT-4o-mini로 영어 → 한국어 번역
    # 시스템 프롬프트: "아래 텍스트를 한국어로 번역해라. 원문에 없는 내용 추가 금지."
    # 실패 시 원문 그대로 반환 (예외 전파 금지)

def build_evidence_text(pricing_case: str, raw_text: str, inn: str) -> dict:
    # 반환: {"evidence_text": 영어원문, "evidence_text_ko": 한국어번역본}

    if pricing_case in ("DIRECT", "COMPONENT_SUM"):
        # Case A / B → raw_text 앞 300자 사용, OpenAI로 번역
        en_text = raw_text[:300]
        ko_text = translate_to_korean(en_text)
        return {"evidence_text": en_text, "evidence_text_ko": ko_text}

    # Case C → OpenAI에게 근거 요약 + 한국어로 동시에 요청 (1회 호출)
    # 프롬프트: "{inn}의 호주 TGA 미승인 또는 조건부 수출 근거를 아래 텍스트에서 찾아 한국어 300자 이내로 요약해라. 텍스트에 없는 내용 추가 금지."
    # 모델: gpt-4o-mini
    en_text = raw_text[:300]
    ko_text = translate_to_korean(raw_text[:1500])  # Case C는 더 긴 텍스트 번역
    return {"evidence_text": en_text, "evidence_text_ko": ko_text}

━━━━━━━━━━━━━━━━━━━━━━━━
[au_crawler.py — build_product_summary()]
━━━━━━━━━━━━━━━━━━━━━━━━
import uuid
from datetime import datetime, timezone

def build_product_summary(product: dict, pbs: dict, tga: dict, chemist: dict, austender: dict) -> dict:
    # ── 먼저 아래 4개 중간 변수를 이 함수 안에서 직접 정의하고 계산해라 ──
    # viable_result: determine_export_viable(tga)를 호출한 결과 dict
    # assembled: 아래 반환 dict를 구성하기 전 모든 필드를 담은 임시 dict
    #   (completeness_score에 넘길 용도. 반환 dict와 동일 구조여도 됨)
    # completeness_ratio: len([f for f in AU_REQUIRED_FIELDS if assembled.get(f)]) / len(AU_REQUIRED_FIELDS)
    # data_source_count: pbs, tga, chemist, austender 중 None이 아닌 것의 개수 (0~4)
    # ─────────────────────────────────────────────────────────────────────
    return {
        # ── 헌법 공통 6컬럼 (절대 변경 금지) ──
        "id":                str(uuid.uuid4()),
        "product_id":        product["product_id"],
        "market_segment":    product["market_segment"],
        "fob_estimated_usd": None,           # 1공정 항상 null
        "confidence":        completeness_score(assembled),
        "crawled_at":        datetime.now(timezone.utc).isoformat(),

        # ── 제품 기본 정보 ──
        "product_name_ko":   product["product_name_ko"],
        "inn_normalized":    product["inn_normalized"],
        "hs_code_6":         product["hs_code_6"],
        "dosage_form":       product["dosage_form"],
        "strength":          product["strength"],

        # ── TGA 규제 정보 ──
        "artg_number":       tga.get("artg_number"),
        "artg_status":       tga.get("artg_status"),
        "tga_schedule":      tga.get("tga_schedule"),
        "tga_sponsor":       tga.get("tga_sponsor"),
        "artg_source_url":   tga.get("artg_source_url", ""),

        # ── PBS 정보 ──
        "pbs_listed":        pbs.get("pbs_listed", False),
        "pbs_item_code":     pbs.get("pbs_item_code"),
        "pbs_price_aud":     pbs.get("pbs_price_aud"),
        "pbs_source_url":    pbs.get("pbs_source_url", ""),

        # ── 시장 가격 정보 ──
        "retail_price_aud":  chemist.get("retail_price_aud") or pbs.get("pbs_price_aud"),
        "price_source_name": chemist.get("price_source_name", "PBS"),
        "price_source_url":  chemist.get("price_source_url", "") or pbs.get("pbs_source_url", ""),
        "price_unit":        chemist.get("price_unit", "per pack"),
        "pricing_case":      product["pricing_case"],

        # ── 수출 가능 여부 ──
        "export_viable":     viable_result.get("export_viable"),
        "reason_code":       viable_result.get("reason_code"),
        "evidence_url":      tga.get("artg_source_url", ""),
        "evidence_text":     evidence.get("evidence_text", ""),      # 영어 원문
        "evidence_text_ko":  evidence.get("evidence_text_ko", ""),   # DeepL 한국어 번역본

        # ── 관련 사이트 ──
        "sites":             build_sites(...),

        # ── 품질 지표 ──
        "completeness_ratio": completeness_ratio,
        "data_source_count":  data_source_count,
        "error_type":         None
    }
```

**✅ 완료 조건:**
```bash
python -c "
from au_crawler import build_product_summary
import json
product = json.load(open('au_products.json'))['products'][3]  # Hydrine
result = build_product_summary(product, {}, {}, {}, {})
print(result['id'], result['fob_estimated_usd'], result['pricing_case'])
"
```
`fob_estimated_usd`가 `None`이고 `id`가 UUID 형식이면 통과.

---

## PROMPT 6 — Supabase INSERT 함수 작성

```
crawler/db/supabase_insert.py 를 작성해라.

[목적]
build_product_summary()로 만든 dict를 Supabase 'australia' 테이블에 INSERT한다.
JSON 파일 저장은 사용하지 않는다. Supabase 직접 적재만.

[사용 라이브러리]
supabase-py (from supabase import create_client)

[환경변수]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service_role key 사용 (RLS 우회)

[테이블명]
TABLE_NAME = "australia"

[작성할 함수]

함수 1: get_supabase_client() -> Client
- create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) 반환
- 클라이언트 재사용 (모듈 레벨 싱글턴 패턴)

함수 2: upsert_product(summary: dict) -> bool
- australia 테이블에 upsert
- on_conflict 기준: "product_id" 단일 컬럼 (crawled_at 제외)
  → 같은 product_id가 있으면 최신 크롤링 결과로 덮어씀
- 성공: True 반환
- 실패: 에러 출력 후 False 반환 (예외 전파 금지)
- 로그: print(f"[INSERT] {summary['product_name_ko']} → {result}")

함수 3: upsert_all(summaries: list[dict]) -> dict
- summaries 리스트 전체를 순서대로 upsert_product 호출
- 반환: {"success": N, "fail": M}

[Supabase australia 테이블 CREATE 구문도 같이 생성]
-- Supabase 프로젝트 설정: Region = Northeast Asia (Seoul) 기준
-- 이 SQL을 Supabase SQL Editor에 붙여넣으면 된다
-- 공통 6컬럼은 절대 변경하지 말 것
-- product_id는 TEXT 타입 (UUID 아님)
-- PRIMARY KEY는 id (UUID)
-- product_id에는 INDEX 추가
CREATE TABLE IF NOT EXISTS australia (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id        TEXT NOT NULL UNIQUE,      -- TEXT 타입 + UNIQUE 제약 (upsert on_conflict 필수)
  market_segment    TEXT DEFAULT 'public',
  fob_estimated_usd DECIMAL,                  -- 1공정 null 허용, 2공정에서 채움
  confidence        DECIMAL,
  crawled_at        TIMESTAMPTZ DEFAULT now(),
  -- 이하 확장 컬럼
  product_name_ko   TEXT,
  inn_normalized    TEXT,
  hs_code_6         TEXT,
  dosage_form       TEXT,
  strength          TEXT,
  artg_number       TEXT,
  artg_status       TEXT,
  tga_schedule      TEXT,
  tga_sponsor       TEXT,
  artg_source_url   TEXT,
  pbs_listed        BOOLEAN DEFAULT false,
  pbs_item_code     TEXT,
  pbs_price_aud     DECIMAL,
  pbs_source_url    TEXT,
  retail_price_aud  DECIMAL,
  price_source_name TEXT,
  price_source_url  TEXT,
  price_unit        TEXT,
  pricing_case      TEXT,
  export_viable     TEXT,
  reason_code       TEXT,
  evidence_url      TEXT,
  evidence_text     TEXT,                -- 영어 원문
  evidence_text_ko  TEXT,               -- DeepL 한국어 번역본 (PDF 출력용)
  sites             JSONB,
  completeness_ratio DECIMAL,
  data_source_count  INTEGER,
  error_type        TEXT
);
CREATE INDEX IF NOT EXISTS idx_australia_product_id ON australia(product_id);
```

**✅ 완료 조건:**
```bash
SUPABASE_URL=your_url SUPABASE_SERVICE_KEY=your_key python -c "
from db.supabase_insert import get_supabase_client
client = get_supabase_client()
print('Supabase 연결 성공:', client is not None)
"
```
연결 성공 메시지 출력되면 통과.

---

## PROMPT 7 — GitHub Actions workflow_dispatch yml 작성

```
.github/workflows/au_crawl.yml 을 작성해라.

[목적]
Next.js 대시보드의 "크롤링 실행" 버튼이 눌리면
GitHub Actions가 au_crawler.py를 실행하고
결과를 Supabase에 직접 INSERT한다.

[트리거]
- workflow_dispatch만. cron 없음.
- 수동 실행 및 API 트리거 모두 가능해야 한다.

[실행 환경]
- ubuntu-latest
- Python 3.12(venv 가상환경이랑 같아야함)
- Render에 별도 서버를 띄우지 않고 Actions runner에서 직접 실행

[Secrets 사용]
아래 5개를 GitHub Repository Secrets에 등록해야 한다.
yml에서는 ${{ secrets.XXX }} 형식으로 참조:
- SUPABASE_URL
- SUPABASE_SERVICE_KEY
- OPENAI_API_KEY
- GH_TOKEN              (Next.js trigger 호출용, yml에선 불필요)
- GITHUB_OWNER
- GITHUB_REPO
- PBS_SUBSCRIPTION_KEY

[yml 내용]
name: 호주 1공정 크롤러 실행

on:
  workflow_dispatch:
    inputs:
      product_filter:
        description: '실행할 product_id (필수. 예: au-hydrine-004)'
        required: true   # ← 반드시 1개 지정. 전체 실행 없음.
        default: ''

jobs:
  crawl-au:
    runs-on: ubuntu-latest
    steps:
      - name: 코드 체크아웃
      - name: Python 3.11 설정
      - name: 의존성 설치 (requirements.txt)
      - name: 크롤러 실행
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          PRODUCT_FILTER: ${{ github.event.inputs.product_filter }}
          PBS_SUBSCRIPTION_KEY: ${{ secrets.PBS_SUBSCRIPTION_KEY }}
        run: cd crawler && python au_crawler.py
      - name: 실행 결과 요약 출력
        # upsert_product 결과 출력

[au_crawler.py main() 함수도 같이 작성]
- au_products.json 읽기
- PRODUCT_FILTER 환경변수 읽기
- PRODUCT_FILTER가 비어있으면 즉시 에러 출력 후 종료 (전체 실행 금지)
- PRODUCT_FILTER와 일치하는 product_id 1개만 찾아서 실행
- 해당 품목에 대해서만 수집 함수 순서대로 호출:
  1. fetch_tga_artg()
  2. determine_export_viable()
  3. fetch_pbs_by_ingredient() 또는 fetch_pbs_multi()
  4. fetch_chemist_price()
  5. fetch_austender()
  6. build_product_summary()
  7. upsert_product()
- 완료 후 결과 print
```

**✅ 완료 조건:**
GitHub Actions → Actions 탭 → "호주 1공정 크롤러 실행" → "Run workflow" 버튼이 보이면 통과.
실제 실행 후 Supabase australia 테이블에 행이 들어오면 최종 통과.

---

## PROMPT 8 — Next.js Supabase 조회 페이지 및 트리거 API 작성

```
next-app/ 안에 아래 파일들을 작성해라.
MVP 기준. 디자인은 최소화, 동작 우선.

[파일 1] next-app/lib/supabase.ts
- @supabase/supabase-js 사용
- NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY 환경변수
- createClient로 싱글턴 클라이언트 export

[파일 2] next-app/pages/api/au/products.ts
- GET 요청 처리
- supabase.from('australia').select('*').order('crawled_at', { ascending: false })
- 반환: { data: ProductSummary[], count: number }
- 에러 시 500 반환

[파일 3] next-app/pages/api/trigger.ts
- POST 요청 처리
- request body에서 `product_id: string` 읽기 (필수. 없으면 400 반환)
- GitHub REST API 호출: workflow_dispatch
- URL: https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/au_crawl.yml/dispatches
- Header: Authorization: Bearer {GH_TOKEN}
- Body: { ref: "main", inputs: { product_filter: product_id } }
  → product_filter에 선택한 product_id를 반드시 넣어서 보낸다
- 환경변수: GH_TOKEN, GITHUB_OWNER, GITHUB_REPO (서버사이드만, NEXT_PUBLIC 금지)
- 성공: { message: "크롤링 시작됨", product_id } 반환
- 실패: 에러 메시지 반환

[파일 4] next-app/pages/au/index.tsx
MVP 기준. 1개 제품 선택 → 실행 → 그 제품만 폴링 갱신.

- 상단: "호주 1공정 시장조사" 제목
- 품목 선택 UI:
    <select>로 8개 품목 드롭다운 (product_id + product_name_ko 표시)
    선택 후 "선택 제품 크롤링 실행" 버튼 1개
- 버튼 클릭 시:
  1. 선택된 product_id를 body에 담아 /api/trigger POST 호출
  2. 해당 행을 "실행 중..." 상태로 표시
  3. setInterval로 3초마다 /api/au/products 폴링
     → 받아온 데이터 중 **선택한 product_id의 crawled_at**만 확인
     → 이전 crawled_at보다 갱신됐으면 폴링 중지 + "실행 중..." 해제
  4. 폴링 최대 40회(2분) 초과 시 자동 중지 + "시간 초과" 안내
- 8개 품목을 단순 테이블로 표시:
  컬럼: 제품명 | 성분 | 수출가능여부 | Pricing Case | PBS 등재 | 소매가(AUD) | 신뢰도 | 수집일시 | 실행
  → "실행" 컬럼: 각 행에 "▶ 실행" 버튼 (버튼 클릭 시 해당 product_id로 trigger 호출해도 됨)
- export_viable 값에 따라 텍스트 색상:
  viable → green, conditional → orange, not_viable → red
- 각 행 클릭 시 evidence_text 펼침 (토글)
- "PDF 출력" 버튼 (다음 단계에서 구현, 지금은 빈 onClick)
- TypeScript interface ProductSummary는 product_summary dict 구조 그대로

[핵심 원칙]
항상 1개 제품씩 실행, 1개 제품씩 저장, 1개 제품씩 화면 갱신.
전체 일괄 실행 버튼은 만들지 않는다.

[환경변수]
최상단 .env 파일 하나로 통일. 크롤러/Next.js 모두 동일 파일 참조.
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
OPENAI_API_KEY=
GH_TOKEN=
GITHUB_OWNER=
GITHUB_REPO=
```

**✅ 완료 조건:**
```bash
cd next-app && npm run dev
```
`http://localhost:3000/au` 접속 시 드롭다운에서 제품 선택 후 "선택 제품 크롤링 실행" 버튼 클릭 →
해당 행만 "실행 중..." 표시되고, 해당 product_id의 crawled_at이 갱신되면 통과.

---

## PROMPT 9 — PDF 양식 데이터 매핑 및 출력 구현

```
next-app/components/PdfReport.tsx 를 작성하고,
next-app/pages/au/index.tsx 의 "PDF 출력" 버튼에 연결해라.

[목적]
드롭다운에서 선택한 제품 1개의 크롤링 결과를
1공정 시장조사 보고서 양식에 맞춰 PDF로 출력한다.
8개 전체 출력 아님. 선택한 product_id 1개만.

[PDF 1페이지 구조 — 제품 1개 기준]
────────────────────────────────────
제품명 (product_name_ko)
성분 (inn_normalized) | HS코드 (hs_code_6)
────────────────────────────────────
[관련 사이트]
  공공조달: sites.public_procurement[] → 사이트명 + URL
  민간(가격): sites.private_price[] → 사이트명 + URL
  핵심 논문: sites.paper[] → 사이트명 + URL (없으면 "-")

[수출 가능 여부]
  판정: export_viable → "가능" | "조건부" | "불가"

[근거]
  링크: evidence_url
  내용: evidence_text_ko  ← DeepL 한국어 번역본 출력
────────────────────────────────────

[구현 방법]
- @react-pdf/renderer 사용 (npm install @react-pdf/renderer)
- 컴포넌트: <PdfReport product={ProductSummary} />  ← 단수 product (배열 아님)
- 버튼 클릭 시 pdf.save("{product_name_ko}_1공정_시장조사.pdf") 실행
- 파일명 예시: "하이드린_1공정_시장조사.pdf"

[export_viable → 한국어 변환]
viable      → "가능"
conditional → "조건부"
not_viable  → "불가"

[주의]
- @react-pdf/renderer는 SSR 불가. dynamic import 사용:
  const PdfReport = dynamic(() => import('../../components/PdfReport'), { ssr: false })
- react-pdf Document, Page, Text, View, StyleSheet만 사용
- 외부 폰트는 MVP에서 생략 (한글 깨짐 허용, 이후 개선)
- evidence_text_ko가 없으면 evidence_text(영어 원문) fallback 출력

[index.tsx에서 연결]
- "PDF 출력" 버튼: 현재 선택된 product의 데이터가 있을 때만 활성화
- 클릭 시 현재 선택된 product 1개 데이터를 PdfReport에 넘겨서 다운로드
```

**✅ 완료 조건:**
드롭다운에서 제품 선택 → 크롤링 실행 → 데이터 갱신 확인 → "PDF 출력" 클릭 시
`{제품명}_1공정_시장조사.pdf` 파일이 다운로드되고,
해당 제품 1개 내용이 양식대로 들어있으면 최종 통과.

---

## 📋 실행 순서 및 역할 분담

### 내가 직접 할 일 (Cursor 전에 먼저)

| 순서 | 할 일 |
|------|------|
| [1] | Supabase 프로젝트 생성 → 서울 리전 선택 → SQL Editor에서 `australia` 테이블 CREATE 실행 |
| [2] | Supabase URL / anon key / service_role key 복사해두기 |
| [3] | GitHub PAT 발급 (Settings → Developer Settings → PAT, repo + workflow 권한) |
| [4] | GitHub Repository Secrets에 5개 등록: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY`, `GH_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO` |
| [5] | PBS/TGA/Chemist/AusTender 실제 접근 URL 브라우저에서 직접 확인 |
| [6] | Next.js 프로젝트 생성 + GitHub 저장소 연결 |

### Cursor에 넣을 순서 (한 단계 성공 확인 후 다음으로)

> ⚠️ 한 번에 전체 붙이지 말 것. 단계별로 넣고, 완료 조건 확인 후 다음 단계.

| Cursor 순서 | 프롬프트 | 이유 |
|------------|---------|------|
| **1단계** | PROMPT 1 | 폴더 뼈대 먼저 |
| **2단계** | PROMPT 2 | 품목 데이터 정의 |
| **3단계** | PROMPT 6 | ← Supabase 연결부터 확인. DB 안 되면 뒤가 전부 막힘 |
| **4단계** | PROMPT 3 | PBS/TGA 수집 함수 |
| **5단계** | PROMPT 4 | Chemist/AusTender 파싱 |
| **6단계** | PROMPT 5 | product_summary 조립 |
| **7단계** | PROMPT 7 | GitHub Actions yml + main() |
| **8단계** | PROMPT 8 | Next.js 조회 페이지 |
| **9단계** | PROMPT 9 | PDF 출력 (마지막) |

---

## 📊 전체 단계별 완료 체크리스트

> **구현 우선순위:** ① 크롤링 → Supabase INSERT 성공 → ② GitHub Actions 실행 → ③ Next.js 데이터 조회 → ④ PDF 출력 (마지막)

| 단계 | 프롬프트 | 완료 조건 | 예상 소요 | 우선순위 |
|------|---------|---------|---------|---------|
| 1 | 폴더 구조 생성 | 모든 파일 존재 확인 | 5분 | ① |
| 2 | au_products.json | `len(products) == 8` + product_id TEXT 형식 확인 | 5분 | ① |
| 3 | PBS / TGA 함수 | 오류 없이 None/dict 반환 | 20분 | ① |
| 4 | Chemist / AusTender | 오류 없이 실행 (None 허용) | 15분 | ① |
| 5 | product_summary 조립 | fob=None, product_id TEXT 형식 | 15분 | ① |
| 6 | Supabase INSERT | 연결 성공 + australia 테이블 생성 (Seoul region) | 10분 | ① |
| 7 | GitHub Actions yml | Run workflow → Supabase 행 INSERT 확인 | 15분 | ② |
| 8 | Next.js 조회 페이지 | 테이블 표시 + 폴링 동작 확인 | 20분 | ③ |
| 9 | PDF 출력 | PDF 다운로드 + 8개 품목 포함 | 15분 | ④ 마지막 |
| **합계** | | | **~2시간** | |

---

## ⚠️ 각 프롬프트 공통 주의사항

1. **공통 6컬럼 절대 변경 금지:** `id, product_id, market_segment, fob_estimated_usd, confidence, crawled_at`
2. **fob_estimated_usd는 1공정에서 항상 `null`**. 계산 코드 작성하지 마라.
3. **LLM 호출은 Case C evidence_text 생성에만 허용**. 나머지 판단은 결정론적 룰.
4. **공통 라이브러리 금지**. 파일 간 import는 최소화.
5. **Playwright 사용 금지**. 정적 HTML + JSON API만.
6. **각 프롬프트는 이전 단계 파일이 존재한다고 가정**하고 작성한다.

---

*UPharma Export AI · KITA 무역AX 1기 · 한국유나이티드제약 5조*  
*목적: 한국 제약회사 수출 지원 자동화 — 실시간 시장조사 보고서 PDF 출력 시스템*  
*작성일: 2026-04-12*
