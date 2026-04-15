-- UPharma Export AI · Australia Supabase 스키마 (Phase 1 최종)
-- Region: Northeast Asia (Seoul) 기준, Supabase SQL Editor 에 그대로 붙여 넣는다.
--
-- 원칙
--   - 공통 6컬럼(id, product_id, market_segment, fob_estimated_usd, confidence, crawled_at)
--     은 이름·타입·기본값을 절대 변경하지 않는다.
--   - product_id 는 TEXT (UUID 아님). UNIQUE 제약은 upsert on_conflict=product_id 에 필수.
--   - 확장 컬럼은 ALTER TABLE ... ADD COLUMN IF NOT EXISTS 로 idempotent 하게 추가한다.
--
-- 테이블 구성
--   1) australia           — 1공정(크롤링) + 2공정(FOB) 통합 행
--   2) australia_history   — 매 크롤링마다 전체 스냅샷 누적
--   3) australia_buyers    — 3공정 바이어 후보 (AHP PSI 5축 점수)
--   4) reports             — 1/2/3공정 산출 보고서 파일 메타


-- ════════════════════════════════════════════════════════════
-- 1) australia — 품목 단일 행 (on_conflict = product_id)
-- ════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS australia (
  -- 공통 6컬럼 (변경 금지)
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id        TEXT NOT NULL UNIQUE,
  market_segment    TEXT DEFAULT 'public',
  fob_estimated_usd DECIMAL,          -- 1공정 항상 NULL, 2공정에서 최종 추정가로 채운다
  confidence        DECIMAL,
  crawled_at        TIMESTAMPTZ DEFAULT now(),

  -- 품목 마스터 (au_products.json)
  product_name_ko   TEXT,
  inn_normalized    TEXT,
  hs_code_6         TEXT,
  dosage_form       TEXT,
  strength          TEXT,
  pricing_case      TEXT,               -- DIRECT | COMPONENT_SUM | ESTIMATE

  -- TGA ARTG
  artg_number           TEXT,
  artg_status           TEXT,
  tga_schedule          TEXT,             -- S2/S3/S4/S8 만 저장 (RE 등 라이선스 코드 제외)
  tga_licence_category  TEXT,
  tga_licence_status    TEXT,
  tga_sponsor           TEXT,
  artg_source_url       TEXT,

  -- PBS API + 웹
  pbs_listed            BOOLEAN DEFAULT false,
  pbs_item_code         TEXT,
  pbs_price_aud         DECIMAL,
  pbs_dpmq              DECIMAL,
  pbs_patient_charge    DECIMAL,
  pbs_determined_price  DECIMAL,
  pbs_pack_size         INTEGER,
  pbs_pricing_quantity  INTEGER,
  pbs_benefit_type      TEXT,
  pbs_program_code      TEXT,
  pbs_brand_name        TEXT,
  pbs_innovator         TEXT,
  pbs_first_listed_date TEXT,
  pbs_repeats           INTEGER,
  pbs_formulary         TEXT,
  pbs_restriction       BOOLEAN,
  pbs_total_brands      INTEGER,
  pbs_brands            JSONB,
  pbs_source_url        TEXT,
  pbs_web_source_url    TEXT,             -- NEW: fetch_pbs_web canonical URL

  -- 민간 소매 (Chemist Warehouse)
  retail_price_aud  DECIMAL,
  price_source_name TEXT,
  price_source_url  TEXT,
  price_unit        TEXT,

  -- buy.nsw.gov.au (NSW 주정부 공공조달 공고)     NEW: 4개 독립 컬럼 + nsw_note(안내문)
  nsw_contract_value_aud DECIMAL,
  nsw_supplier_name      TEXT,
  nsw_contract_date      TEXT,
  nsw_source_url         TEXT,
  nsw_note               TEXT,          -- 매칭 없을 때 화면/보고서 표시용 일반 안내문

  -- 수출성 판정
  export_viable   TEXT,                    -- viable | conditional | not_viable
  reason_code     TEXT,                    -- ARTG_REGISTERED / PBS_REGISTERED / SCHEDULE_8 / TGA_NOT_APPROVED ...

  -- 증거 (영/한 분리)
  evidence_url     TEXT,
  evidence_text    TEXT,
  evidence_text_ko TEXT,

  -- 2공정 — FOB 역산 5개                     NEW
  fob_local_ref_aud    DECIMAL,            -- 2공정에 쓴 현지 기준가 (PBS DPMQ 또는 소매가)
  fob_conservative_usd DECIMAL,            -- 보수 시나리오
  fob_base_usd         DECIMAL,            -- 기준 시나리오 (fob_estimated_usd 와 함께 사용)
  fob_aggressive_usd   DECIMAL,            -- 공격 시나리오
  fob_confidence       DECIMAL,            -- 2공정 신뢰도 (0~1)

  -- 메타
  sites              JSONB,
  completeness_ratio DECIMAL,
  data_source_count  INTEGER,
  error_type         TEXT,

  -- Claude Haiku 생성 (Block 2 판정 근거)
  block2_market      TEXT,
  block2_regulatory  TEXT,
  block2_trade       TEXT,
  block2_procurement TEXT,
  block2_channel     TEXT,

  -- Claude Haiku 생성 (Block 3 시장 진출 전략)
  block3_channel     TEXT,
  block3_pricing     TEXT,
  block3_partners    TEXT,
  block3_risks       TEXT,

  -- LLM 생성 (Block 4 규제 체크포인트 — 5개 법령 ①~⑤ 번호 형식 텍스트)
  block4_regulatory  TEXT,

  -- Perplexity 논문 레퍼런스
  perplexity_refs    JSONB,

  -- LLM 메타
  llm_model          TEXT,
  llm_generated_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_australia_product_id ON australia(product_id);
CREATE INDEX IF NOT EXISTS idx_australia_crawled_at ON australia(crawled_at DESC);


-- 기존 테이블이 있는 환경에서도 멱등 실행 가능하도록 ALTER 로 누락 컬럼을 보충한다.
ALTER TABLE australia
  ADD COLUMN IF NOT EXISTS pricing_case                TEXT,
  ADD COLUMN IF NOT EXISTS pbs_dpmq                    DECIMAL,
  ADD COLUMN IF NOT EXISTS pbs_patient_charge          DECIMAL,
  ADD COLUMN IF NOT EXISTS pbs_determined_price        DECIMAL,
  ADD COLUMN IF NOT EXISTS pbs_pack_size               INTEGER,
  ADD COLUMN IF NOT EXISTS pbs_pricing_quantity        INTEGER,
  ADD COLUMN IF NOT EXISTS pbs_benefit_type            TEXT,
  ADD COLUMN IF NOT EXISTS pbs_program_code            TEXT,
  ADD COLUMN IF NOT EXISTS pbs_brand_name              TEXT,
  ADD COLUMN IF NOT EXISTS pbs_innovator               TEXT,
  ADD COLUMN IF NOT EXISTS pbs_first_listed_date       TEXT,
  ADD COLUMN IF NOT EXISTS pbs_repeats                 INTEGER,
  ADD COLUMN IF NOT EXISTS pbs_formulary               TEXT,
  ADD COLUMN IF NOT EXISTS pbs_restriction             BOOLEAN,
  ADD COLUMN IF NOT EXISTS pbs_total_brands            INTEGER,
  ADD COLUMN IF NOT EXISTS pbs_brands                  JSONB,
  ADD COLUMN IF NOT EXISTS pbs_web_source_url          TEXT,
  ADD COLUMN IF NOT EXISTS tga_licence_category        TEXT,
  ADD COLUMN IF NOT EXISTS tga_licence_status          TEXT,
  ADD COLUMN IF NOT EXISTS nsw_contract_value_aud      DECIMAL,
  ADD COLUMN IF NOT EXISTS nsw_supplier_name           TEXT,
  ADD COLUMN IF NOT EXISTS nsw_contract_date           TEXT,
  ADD COLUMN IF NOT EXISTS nsw_source_url              TEXT,
  ADD COLUMN IF NOT EXISTS nsw_note                    TEXT,
  ADD COLUMN IF NOT EXISTS fob_local_ref_aud           DECIMAL,
  ADD COLUMN IF NOT EXISTS fob_conservative_usd        DECIMAL,
  ADD COLUMN IF NOT EXISTS fob_base_usd                DECIMAL,
  ADD COLUMN IF NOT EXISTS fob_aggressive_usd          DECIMAL,
  ADD COLUMN IF NOT EXISTS fob_confidence              DECIMAL,
  ADD COLUMN IF NOT EXISTS block2_market               TEXT,
  ADD COLUMN IF NOT EXISTS block2_regulatory           TEXT,
  ADD COLUMN IF NOT EXISTS block2_trade                TEXT,
  ADD COLUMN IF NOT EXISTS block2_procurement          TEXT,
  ADD COLUMN IF NOT EXISTS block2_channel              TEXT,
  ADD COLUMN IF NOT EXISTS block3_channel              TEXT,
  ADD COLUMN IF NOT EXISTS block3_pricing              TEXT,
  ADD COLUMN IF NOT EXISTS block3_partners             TEXT,
  ADD COLUMN IF NOT EXISTS block3_risks                TEXT,
  ADD COLUMN IF NOT EXISTS block4_regulatory           TEXT,
  ADD COLUMN IF NOT EXISTS perplexity_refs             JSONB,
  ADD COLUMN IF NOT EXISTS llm_model                   TEXT,
  ADD COLUMN IF NOT EXISTS llm_generated_at            TIMESTAMPTZ;


-- ════════════════════════════════════════════════════════════
-- 2) australia_history — 크롤링 스냅샷 append-only
-- ════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS australia_history (
  id         BIGSERIAL PRIMARY KEY,
  product_id TEXT,
  snapshot   JSONB,
  crawled_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_australia_history_product_id ON australia_history(product_id);
CREATE INDEX IF NOT EXISTS idx_australia_history_crawled_at ON australia_history(crawled_at DESC);


-- ════════════════════════════════════════════════════════════
-- 3) australia_buyers — 3공정 바이어 후보 (AHP PSI 5축)
-- ════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS australia_buyers (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         TEXT,
  company_name       TEXT,
  abn                TEXT,

  -- AHP PSI 5축 점수 (합계 100점 만점)
  psi_sales_scale    DECIMAL,          -- 매출규모      (0 ~ 30)
  psi_pipeline       DECIMAL,          -- 파이프라인    (0 ~ 25)
  psi_manufacturing  DECIMAL,          -- 제조소 보유   (0 ~ 20)
  psi_import_exp     DECIMAL,          -- 수입경험      (0 ~ 15)
  psi_pharmacy_chain DECIMAL,          -- 약국체인      (0 ~ 10)
  psi_total          DECIMAL,          -- 5축 합계      (0 ~ 100)

  -- 출처 플래그 (어느 소스에서 후보로 잡혔는지)
  source_tga         BOOLEAN DEFAULT false,
  source_pbs         BOOLEAN DEFAULT false,
  source_nsw         BOOLEAN DEFAULT false,

  -- 필터 플래그
  has_gmp            BOOLEAN DEFAULT false,
  has_pharmacy_chain BOOLEAN DEFAULT false,

  -- 접근 제안문 + 근거 URL
  approach_text_ko   TEXT,
  reference_urls     JSONB,

  crawled_at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_australia_buyers_product_id ON australia_buyers(product_id);
CREATE INDEX IF NOT EXISTS idx_australia_buyers_psi_total  ON australia_buyers(psi_total DESC);


-- ════════════════════════════════════════════════════════════
-- 4) reports — 1/2/3공정 산출 보고서 메타
-- ════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS reports (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id   TEXT,
  gong         INTEGER,               -- 1 / 2 / 3
  title        TEXT,
  file_url     TEXT,                  -- Supabase storage URL
  crawled_data JSONB,                 -- 보고서 생성 시점 전체 스냅샷
  created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reports_product_id ON reports(product_id);
CREATE INDEX IF NOT EXISTS idx_reports_gong       ON reports(gong);
CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports(created_at DESC);


-- ════════════════════════════════════════════════════════════
-- 5) au_regulatory — 호주 규제 체크포인트 시드 데이터
-- ════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS au_regulatory (
  id          SERIAL PRIMARY KEY,
  title       TEXT NOT NULL UNIQUE,
  description TEXT,
  badge       TEXT,
  badge_color TEXT,
  source_url  TEXT,
  content     TEXT,
  updated_at  DATE DEFAULT CURRENT_DATE
);

INSERT INTO au_regulatory (title, description, badge, badge_color, source_url) VALUES
('Therapeutic Goods Act 1989',
 'ARTG 등재 의무 · TGA 심사 12–18개월 소요. 처방의약품은 Registered 또는 Listed 경로.',
 '핵심 장벽', 'orange',
 'https://www.legislation.gov.au/C2004A03952'),
('GMP 기준 (PIC/S 상호인정)',
 '한국 PIC/S 정회원(2014~). 호주 TGA와 제조소 실사 면제 협의 가능.',
 '유리', 'green',
 'https://www.tga.gov.au/industry/manufacturing/overseas-manufacturers'),
('National Health Act 1953 (PBS)',
 'PBS 등재 시 가격 통제 수반. PBAC 심사 필요. 민간 유통 병행 전략 권장.',
 '공공조달', 'blue',
 'https://www.legislation.gov.au/C2004A07357'),
('KAFTA (한-호주 FTA)',
 '2014년 발효. Chapter 30 의약품 관세 철폐 완료. HS 3004.90 / 3006.30 모두 0%.',
 '활성', 'green',
 'https://ftaportal.dfat.gov.au/tariff/KAFTA'),
('Customs (Prohibited Imports) Regulations 1956',
 '항암제·향정신성 성분 수입 시 별도 허가 필요. Hydrine(hydroxyurea) 해당 여부 사전 확인.',
 '확인 필요', 'gray',
 'https://www.legislation.gov.au/F1997B00390')
ON CONFLICT (title) DO NOTHING;
