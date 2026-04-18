# UPharma Export AI · Australia

호주 수출 시장조사용 **크롤링 → Supabase 저장 → FOB 역산·보고서** 파이프라인입니다. 웹 UI는 **FastAPI + Jinja + Vanilla JS**(`upharma-au/templates`, `upharma-au/static`)이며 Next.js는 사용하지 않습니다.

**에이전트/개발 규칙·용어·구조 상세:** 저장소 루트 [`CLAUDE.md`](./CLAUDE.md)

---

## 동작 원칙 (요약)

- `POST /api/crawl` 은 **항상 외부 소스를 다시 조회**해 DB를 갱신합니다. “이미 있으면 스킵” 캐시는 두지 않습니다.
- Anthropic 호출은 **`claude-haiku-4-5-20251001` 고정** (Sonnet/Opus 사용 금지).
- 크롤·스키마·API 세부 필드·2공정 수식은 **코드와 `CLAUDE.md`** 를 기준으로 합니다.

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
    ├── crawler/            # 1공정 크롤러
    ├── stage2/             # FOB 역산
    └── reports/            # 생성 PDF 등 (git 제외)
```

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

## API·스키마·품목 목록

엔드포인트 전체는 `upharma-au/render_api.py`, 테이블·컬럼은 `upharma-au/crawler/db/`, 품목 마스터는 `au_products.json` / `fob_reference_seeds.json` 을 보세요.
