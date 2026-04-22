# UPharma Export AI · Australia

호주 수출용 **크롤링 → Supabase → 보고서(P1·P2·P3) → 최종 PDF** 파이프라인.  
구현·규칙·스키마 상세는 [`CLAUDE.md`](./CLAUDE.md) 를 봅니다.

---

## 보고서 양식 (기준)

아래 틀만 PDF·프롬프트 정합의 기준으로 씁니다. 그 외 예시·구버전·별첨·교차인용 문구는 넣지 않습니다.

### P1 `{국가} 시장보고서 - {의약품명}`

1. 의료 거시환경 파악 — 해당 국가·해당 품목 **거시 시장 사이즈**  
2. 무역/규제 환경 — **등록 여부**(개수/미등재/신규 등록 필요성), **Fast Track·관세** 맥락  
3. 참고 가격 — **크롤링 수치**, **출처**, 영어는 **괄호(원문+한글)**  
4. 리스크/조건 — **심사 기간·난이도**  
5. 근거 및 출처 — **5-1** 퍼플렉시티(표: No/제목·출처/요약), **5-2** 사용 DB·기관

### P2 `{국가} 수출가격 전략 보고서 - {의약품명}`

1. **거시 시장** (3~4문장)  
2. **단가(시장기준가)** — 표: 기준가(USD) / 산정 방식 / 시장 구분(공공·민간)  
3. **거래처 참고 가격** — 표: 업체명 / 제품명 / 성분함량 / 시장가  
4. **가격 시나리오** — **4-1 공공**, **4-2 민간** (데이터 소스·저가/기준/프리미엄·근거·FOB 역산)  
5. *면책* — AI 추정, 최종 검토 문구

### P3 바이어 후보 리스트

(요청하신 틀) 후보 표 + **TOP10 상세** + 선정 기준(가중치).

### 최종 PDF

**표지 + P2 + P3 + P1** (`POST /api/final-report`).

---

## 최소 실행

**루트**에서 (최초: `python -m venv venv`, `pip install -r requirements.txt`, `python scripts/migrate.py`).

```bash
uvicorn render_api:app --app-dir upharma-au --reload --port 8000
```

`.env` 에 `SUPABASE_*`, `PBS_SUBSCRIPTION_KEY`, `ANTHROPIC_API_KEY` 등 — 자세한 키 표는 `CLAUDE.md` 또는 `copy.env` 를 참고.

---

## 폴더

| 경로 | 내용 |
|------|------|
| `upharma-au/render_api.py` | FastAPI |
| `upharma-au/report_generator.py` | P1·P2·P3 PDF |
| `upharma-au/crawler/` | 크롤 |
| `scripts/migrate.py` | DB 마이그레이션 |
