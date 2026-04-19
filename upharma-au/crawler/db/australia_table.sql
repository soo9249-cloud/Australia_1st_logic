-- v2 schema 2026-04-18 — Cowork §14 spec
-- ============================================================================
-- UPharma Export AI · Australia — DB v2 (au_ prefix 10 tables)
-- Source: /AX 호주 final/01_보고서필드스키마_v1.md §14-3
-- Delegation: /AX 호주 final/02_ClaudeCode_위임지시서_v1.md
--
-- 원칙
--   1. 모든 테이블 이름에 `au_` prefix (팀 공유 Supabase 충돌 방지)
--   2. PK: 마스터 테이블은 UUID, 로그·스냅샷 테이블은 BIGSERIAL
--   3. FK 키워드 사용 금지 — `product_id` 는 TEXT 로 느슨하게 연결 (loose coupling)
--   4. 금융 숫자: AUD/USD 는 DECIMAL(12,2), KRW 는 DECIMAL(14,0), 비율은 DECIMAL(6,4)
--   5. 타임스탬프 전부 TIMESTAMPTZ. created_at/updated_at 모든 테이블 필수
--   6. updated_at 자동 갱신: au_set_updated_at() 함수 + 트리거 재사용
--   7. JSONB 컬럼은 NOT NULL DEFAULT '{}' 또는 '[]' (NULL 회피)
--   8. CASCADE 안 씀, soft-delete(deleted_at) 도 안 씀 (기존 코드 패턴 유지)
--
-- 테이블 순서
--   1) au_products          — 메인 마스터 (8 품목 스냅샷) [UUID]
--   2) au_pbs_raw           — PBS API 원본 보관소
--   3) au_tga_artg          — TGA 등재 원본 (1:N)
--   4) au_reports_r1        — 보고서 ① 시장분석 [UUID]
--   5) au_reports_r2        — 보고서 ② 수출전략 FOB [UUID]
--   6) au_reports_r3        — 보고서 ③ TOP10 바이어 접근 [UUID]
--   7) au_buyers            — 바이어 마스터 + AHP PSI 5축 [UUID]
--   8) au_report_refs       — 하이브리드 참고자료 (Perplexity/PubMed/SS)
--   9) au_crawl_log         — 크롤 이력·에러
--  10) au_reports_history   — Haiku 응답 append-only 스냅샷 (§14-7 결정 3)
--  11) au_regulatory        — 호주 규제 체크포인트 시드 (v1 유지)
-- ============================================================================


-- ── 공통: updated_at 자동 갱신 함수 ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION au_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ════════════════════════════════════════════════════════════════════════
-- 1) au_products — 메인 마스터 (8 품목 스냅샷)
-- ════════════════════════════════════════════════════════════════════════
-- 용도: 앱 메인 카드(가격·경쟁품·바이어) + 보고서 헤더가 전부 이 테이블에서 읽음.
-- 설계 원칙: 읽기 속도 우선 → denormalize. PBS/TGA/Chemist 최신 값 복제 저장.
-- 원본은 au_pbs_raw / au_tga_artg 등 별도 테이블에 보관.

CREATE TABLE IF NOT EXISTS au_products (
  id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_code                  TEXT NOT NULL UNIQUE,
  product_name_ko               TEXT,
  inn_normalized                TEXT,
  strength                      TEXT,
  dosage_form                   TEXT,
  -- Case 분기 (내부 전용, UI 노출 금지) — Supabase 마이그레이션과 동일하게 TEXT
  case_code                     TEXT,
  case_risk_text_ko             TEXT,
  -- TGA 블록 (최신 스냅샷 — 원본은 au_tga_artg)
  tga_found                     BOOLEAN DEFAULT false,
  tga_artg_ids                  JSONB   NOT NULL DEFAULT '[]'::jsonb,
  tga_sponsors                  JSONB   NOT NULL DEFAULT '[]'::jsonb,
  -- PBS 블록 (최신 스냅샷 — 원본은 au_pbs_raw)
  pbs_found                     BOOLEAN DEFAULT false,
  pbs_code                      TEXT,
  program_code                  TEXT,
  section_85_100                TEXT,
  formulary                     TEXT,
  aemp_aud                      DECIMAL(12,2),
  aemp_usd                      DECIMAL(12,2),
  aemp_krw                      DECIMAL(14,0),
  spd_aud                       DECIMAL(12,2),
  claimed_price_aud             DECIMAL(12,2),
  dpmq_aud                      DECIMAL(12,2),
  dpmq_usd                      DECIMAL(12,2),
  dpmq_krw                      DECIMAL(14,0),
  mn_pharmacy_price_aud         DECIMAL(12,2),
  brand_premium_aud             DECIMAL(12,2),
  therapeutic_group_premium_aud DECIMAL(12,2),
  special_patient_contrib_aud   DECIMAL(12,2),
  wholesale_markup_band         TEXT,
  pharmacy_markup_code          TEXT,
  markup_variable_pct           DECIMAL(6,4),
  markup_offset_aud             DECIMAL(10,2),
  markup_fixed_aud              DECIMAL(10,2),
  dispensing_fee_aud            DECIMAL(10,2),
  ahi_fee_aud                   DECIMAL(10,2),
  originator_brand              BOOLEAN,
  therapeutic_group_id          TEXT,
  brand_substitution_group_id   TEXT,
  atc_code                      TEXT,
  policy_imdq60                 BOOLEAN,
  policy_biosim                 BOOLEAN,
  section_19a_expiry            DATE,
  authority_method              TEXT,
  copay_general_aud             DECIMAL(10,2),
  copay_concessional_aud        DECIMAL(10,2),
  first_listed_date             DATE,
  pack_size                     INTEGER,
  pricing_quantity              INTEGER,
  maximum_prescribable_pack     INTEGER,
  -- 소매 가격 블록 (Chemist / Healthylife 통합)
  retail_price_aud              DECIMAL(12,2),
  retail_estimation_method      TEXT,
  chemist_price_aud             DECIMAL(12,2),
  chemist_url                   TEXT,
  healthylife_price_aud         DECIMAL(10,2),
  healthylife_url               TEXT,
  -- 경쟁 현황 (보고서 ① · 카드용)
  originator_brand_name         TEXT,
  originator_sponsor            TEXT,
  top_generics                  JSONB   NOT NULL DEFAULT '[]'::jsonb,
  competitor_count              INTEGER,
  market_tier                   TEXT,
  -- 내부 (UI 노출 금지)
  situation_summary             TEXT,
  confidence                    DECIMAL(3,2),
  ingredients_split             JSONB   NOT NULL DEFAULT '[]'::jsonb,
  similar_drug_used             JSONB   NOT NULL DEFAULT '[]'::jsonb,
  hospital_only_flag            BOOLEAN DEFAULT false,
  ai_deep_research_raw          TEXT,
  availability_status           TEXT,
  match_type                    TEXT,
  -- 메타
  schedule_code                 TEXT,
  last_crawled_at               TIMESTAMPTZ,
  crawler_source_urls           JSONB   NOT NULL DEFAULT '{}'::jsonb,
  error_type                    TEXT,
  warnings                      JSONB   NOT NULL DEFAULT '[]'::jsonb,
  -- 보고서 ① — POST /api/report/generate 가 Haiku 블록·참고문헌 UPDATE (레거시 australia 테이블과 동일 슬롯)
  block2_market                 TEXT,
  block2_regulatory             TEXT,
  block2_trade                  TEXT,
  block2_procurement            TEXT,
  block2_channel                TEXT,
  block3_channel                TEXT,
  block3_pricing                TEXT,
  block3_partners               TEXT,
  block3_risks                  TEXT,
  block4_regulatory             TEXT,
  perplexity_refs               JSONB,
  llm_model                     TEXT,
  llm_generated_at              TIMESTAMPTZ,
  created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_au_products_pbs_code         ON au_products(pbs_code);
CREATE INDEX IF NOT EXISTS idx_au_products_inn_normalized   ON au_products(inn_normalized);
CREATE INDEX IF NOT EXISTS idx_au_products_case_code        ON au_products(case_code);
CREATE INDEX IF NOT EXISTS idx_au_products_last_crawled_at  ON au_products(last_crawled_at DESC);

-- Supabase SQL 에디터 마이그레이션과 동일: 기존 DB 에 컬럼만 추가되는 경우
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS availability_status TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS match_type TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS healthylife_price_aud DECIMAL(10,2);
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS healthylife_url TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block2_market TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block2_regulatory TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block2_trade TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block2_procurement TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block2_channel TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block3_channel TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block3_pricing TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block3_partners TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block3_risks TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS block4_regulatory TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS perplexity_refs JSONB;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS llm_model TEXT;
ALTER TABLE au_products ADD COLUMN IF NOT EXISTS llm_generated_at TIMESTAMPTZ;

-- Phase 4.3-v3 — 레거시 컬럼 제거 (에디터 스크립트와 동일)
-- 참고: case_code SMALLINT→TEXT 는 v_au_products 가 case_code 를 참조하므로
--   먼저 DROP VIEW … 후 ALTER (Supabase SQL 에디터 일괄 스크립트와 동일 순서).
ALTER TABLE au_products DROP COLUMN IF EXISTS tga_schedule;

DROP TRIGGER IF EXISTS trg_au_products_updated_at ON au_products;
CREATE TRIGGER trg_au_products_updated_at
  BEFORE UPDATE ON au_products
  FOR EACH ROW EXECUTE FUNCTION au_set_updated_at();

-- 컬럼 코멘트 (비개발자 가독성)
COMMENT ON COLUMN au_products.product_code               IS '8 품목 내부 코드 (예: "hydrine_500")';
COMMENT ON COLUMN au_products.product_name_ko            IS '한국 품목명';
COMMENT ON COLUMN au_products.inn_normalized             IS '정규화된 성분명 (PubChem 기반)';
COMMENT ON COLUMN au_products.case_code                  IS
  '크롤링 분기 Case 코드 (문자열): DIRECT | COMPONENT_SUM | ESTIMATE_withdrawal | ESTIMATE_substitute | ESTIMATE_private | ESTIMATE_hospital';
COMMENT ON COLUMN au_products.tga_found                  IS 'TGA 등재 여부';
COMMENT ON COLUMN au_products.tga_artg_ids               IS '매칭된 ARTG ID 배열 (1:N)';
COMMENT ON COLUMN au_products.pbs_found                  IS 'PBS 등재 여부';
COMMENT ON COLUMN au_products.pbs_code                   IS 'PBS 아이템 코드';
COMMENT ON COLUMN au_products.section_85_100             IS 'S85 / S100_HSD / S100_EFC 등';
COMMENT ON COLUMN au_products.formulary                  IS 'F1 / F2 / F2A / F2T / CDL';
COMMENT ON COLUMN au_products.aemp_aud                   IS 'PBS 정부 승인 출고가 (AUD)';
COMMENT ON COLUMN au_products.spd_aud                    IS '가격공개제 인하가 (AUD)';
COMMENT ON COLUMN au_products.dpmq_aud                   IS '최대처방량 총약가 (AUD)';
COMMENT ON COLUMN au_products.retail_price_aud           IS '시장 추정 소매가 (PBS 등재=DPMQ, 미등재=Chemist × 1.20)';
COMMENT ON COLUMN au_products.retail_estimation_method   IS 'pbs_dpmq / chemist_x120 / ai_estimate / chemist_raw';
COMMENT ON COLUMN au_products.chemist_price_aud          IS 'Chemist Warehouse 원본 크롤 가격 (참고)';
COMMENT ON COLUMN au_products.market_tier                IS 'originator_monopoly / generic_competition / unlisted';
COMMENT ON COLUMN au_products.situation_summary          IS 'DB 만 유지, UI 노출 금지';
COMMENT ON COLUMN au_products.last_crawled_at            IS '메인 크롤 배치 마지막 시각 (소스별은 raw 테이블 crawled_at 참조)';


-- ════════════════════════════════════════════════════════════════════════
-- 2) au_pbs_raw — PBS API 원본 보관소
-- ════════════════════════════════════════════════════════════════════════
-- 용도: PBS API v3 9 개 엔드포인트 응답을 JSONB 그대로 저장.
--        가격 이력 추적·디버깅·AI 재해석용. 13 개월 rolling 후 Storage 아카이브.

CREATE TABLE IF NOT EXISTS au_pbs_raw (
  id                           BIGSERIAL PRIMARY KEY,
  product_id                   TEXT NOT NULL,
  pbs_code                     TEXT,
  schedule_code                TEXT,
  effective_date               DATE,
  endpoint_items               JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_dispensing_rules    JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_fees                JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_markup_bands        JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_copayments          JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_organisations       JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_summary_of_changes  JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_atc                 JSONB NOT NULL DEFAULT '{}'::jsonb,
  endpoint_restrictions        JSONB NOT NULL DEFAULT '{}'::jsonb,
  api_fetched_at               TIMESTAMPTZ,
  crawled_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  market_form                  TEXT,
  market_strength              TEXT
);

ALTER TABLE au_pbs_raw ADD COLUMN IF NOT EXISTS market_form TEXT;
ALTER TABLE au_pbs_raw ADD COLUMN IF NOT EXISTS market_strength TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_au_pbs_raw_code_schedule
  ON au_pbs_raw(pbs_code, schedule_code);
CREATE INDEX IF NOT EXISTS idx_au_pbs_raw_product_id      ON au_pbs_raw(product_id);
CREATE INDEX IF NOT EXISTS idx_au_pbs_raw_effective_date  ON au_pbs_raw(effective_date DESC);

COMMENT ON COLUMN au_pbs_raw.schedule_code IS '월별 스냅샷 식별자 (13 개월 rolling window)';
COMMENT ON COLUMN au_pbs_raw.effective_date IS 'PBS 스케줄 유효 시작일';


-- ════════════════════════════════════════════════════════════════════════
-- 3) au_tga_artg — TGA 등재 원본 (1:N)
-- ════════════════════════════════════════════════════════════════════════
-- 용도: TGA ARTG 크롤 결과. 한 품목당 여러 ARTG 매칭 가능 (함량별·제형별 분리).
-- Phase 4.3-v3 (2026-04-18): schedule / route_of_administration / first_registered_date /
--   sponsor_abn 컬럼은 보고서에서 미사용으로 Supabase 에서 DROP (에디터 마이그레이션과 정합).

CREATE TABLE IF NOT EXISTS au_tga_artg (
  id                       BIGSERIAL PRIMARY KEY,
  product_id               TEXT NOT NULL,
  artg_id                  TEXT NOT NULL UNIQUE,
  product_name             TEXT,
  sponsor_name             TEXT,
  active_ingredients       JSONB NOT NULL DEFAULT '[]'::jsonb,
  strength                 TEXT,
  dosage_form              TEXT,
  status                   TEXT,
  artg_url                 TEXT,
  match_type               TEXT,
  crawled_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE au_tga_artg ADD COLUMN IF NOT EXISTS match_type TEXT;

-- 레거시 DB: Phase 4.3-v3 에서 DROP 한 4컬럼이 남아 있으면 제거 (에디터와 동일)
ALTER TABLE au_tga_artg
  DROP COLUMN IF EXISTS schedule,
  DROP COLUMN IF EXISTS route_of_administration,
  DROP COLUMN IF EXISTS first_registered_date,
  DROP COLUMN IF EXISTS sponsor_abn;

CREATE INDEX IF NOT EXISTS idx_au_tga_artg_product_id    ON au_tga_artg(product_id);
CREATE INDEX IF NOT EXISTS idx_au_tga_artg_sponsor_name  ON au_tga_artg(sponsor_name);

DROP TRIGGER IF EXISTS trg_au_tga_artg_updated_at ON au_tga_artg;
CREATE TRIGGER trg_au_tga_artg_updated_at
  BEFORE UPDATE ON au_tga_artg
  FOR EACH ROW EXECUTE FUNCTION au_set_updated_at();

COMMENT ON COLUMN au_tga_artg.status IS 'Active / Cancelled / Suspended';


-- ════════════════════════════════════════════════════════════════════════
-- 4) au_reports_r1 — 보고서 ① 시장분석 AI 출력
-- ════════════════════════════════════════════════════════════════════════
-- 용도: Haiku 가 생성한 보고서 ① JSON 보관. 1 품목 = 1 행 (재생성 시 UPSERT).

CREATE TABLE IF NOT EXISTS au_reports_r1 (
  id                             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id                     TEXT NOT NULL UNIQUE,
  market_overview_ko             TEXT,
  entry_channel_ko               TEXT,
  partner_direction_ko           TEXT,
  sponsor_priority_rationale_ko  TEXT,
  case_risk_text_ko              TEXT,
  full_json_raw                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  haiku_model_version            TEXT DEFAULT 'claude-haiku-4-5-20251001',
  haiku_temperature              DECIMAL(3,2),
  haiku_generated_at             TIMESTAMPTZ,
  validation_passed              BOOLEAN DEFAULT false,
  validation_errors              JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_au_reports_r1_product_id ON au_reports_r1(product_id);

DROP TRIGGER IF EXISTS trg_au_reports_r1_updated_at ON au_reports_r1;
CREATE TRIGGER trg_au_reports_r1_updated_at
  BEFORE UPDATE ON au_reports_r1
  FOR EACH ROW EXECUTE FUNCTION au_set_updated_at();

COMMENT ON COLUMN au_reports_r1.market_overview_ko            IS '[1-1] KOTRA 톤 시장 개요 (600 자 이내)';
COMMENT ON COLUMN au_reports_r1.entry_channel_ko              IS '[2-1] 진입 채널 (400 자 이내)';
COMMENT ON COLUMN au_reports_r1.partner_direction_ko          IS '[2-2] 파트너 방향성 (300 자 이내)';
COMMENT ON COLUMN au_reports_r1.sponsor_priority_rationale_ko IS '[2-3] 스폰서 협력 근거 (300 자 이내)';
COMMENT ON COLUMN au_reports_r1.case_risk_text_ko             IS '[3-2] 양식 통일 슬롯 (400 자 또는 "해당없음")';


-- ════════════════════════════════════════════════════════════════════════
-- 5) au_reports_r2 — 보고서 ② 수출전략 FOB 3 시나리오
-- ════════════════════════════════════════════════════════════════════════
-- 용도: 3 시나리오 FOB + 사용자 조정. 기존 australia_p2_results 교체.

CREATE TABLE IF NOT EXISTS au_reports_r2 (
  id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id                      TEXT NOT NULL UNIQUE,

  -- 가격 기준선
  aemp_usd                        DECIMAL(12,2),
  aemp_krw                        DECIMAL(14,0),
  dpmq_usd                        DECIMAL(12,2),
  dpmq_krw                        DECIMAL(14,0),
  cw_ref_usd                      DECIMAL(12,2),
  cw_ref_krw                      DECIMAL(14,0),
  component_sum_basis             JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- 3 시나리오 FOB (3 x 3 = 9 컬럼, §2-1 슬래시 풀어쓰기)
  fob_penetration_aud             DECIMAL(12,2),
  fob_penetration_usd             DECIMAL(12,2),
  fob_penetration_krw             DECIMAL(14,0),
  fob_reference_aud               DECIMAL(12,2),
  fob_reference_usd               DECIMAL(12,2),
  fob_reference_krw               DECIMAL(14,0),
  fob_premium_aud                 DECIMAL(12,2),
  fob_premium_usd                 DECIMAL(12,2),
  fob_premium_krw                 DECIMAL(14,0),

  -- 비율·수수료 (3 시나리오 × 4 타입 = 12 컬럼)
  fob_ratio_penetration           DECIMAL(6,4) DEFAULT 0.20,
  fob_ratio_reference             DECIMAL(6,4) DEFAULT 0.35,
  fob_ratio_premium               DECIMAL(6,4) DEFAULT 0.52,
  agent_fee_ratio_penetration     DECIMAL(6,4),
  agent_fee_ratio_reference       DECIMAL(6,4),
  agent_fee_ratio_premium         DECIMAL(6,4),
  freight_ratio_penetration       DECIMAL(6,4),
  freight_ratio_reference         DECIMAL(6,4),
  freight_ratio_premium           DECIMAL(6,4),
  port_fee_aud_penetration        DECIMAL(10,2),
  port_fee_aud_reference          DECIMAL(10,2),
  port_fee_aud_premium            DECIMAL(10,2),

  recommended_scenario            TEXT,
  recommended_scenario_label_ko   TEXT,

  -- 사용자 조정
  user_adjusted_mode              TEXT,
  user_adjust_value               DECIMAL(10,4),
  user_final_fob_usd              DECIMAL(12,2),
  user_final_fob_krw              DECIMAL(14,0),
  user_adjust_note_ko             TEXT,

  -- 마케팅 앵글
  marketing_angle_key             TEXT,
  marketing_angle_text_ko         TEXT,

  -- AI 생성 메타
  full_json_raw                   JSONB NOT NULL DEFAULT '{}'::jsonb,
  report_content_v2               JSONB,
  haiku_model_version             TEXT DEFAULT 'claude-haiku-4-5-20251001',
  haiku_generated_at              TIMESTAMPTZ,
  validation_passed               BOOLEAN DEFAULT false,

  created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_au_reports_r2_product_id           ON au_reports_r2(product_id);
CREATE INDEX IF NOT EXISTS idx_au_reports_r2_recommended_scenario ON au_reports_r2(recommended_scenario);

DROP TRIGGER IF EXISTS trg_au_reports_r2_updated_at ON au_reports_r2;
CREATE TRIGGER trg_au_reports_r2_updated_at
  BEFORE UPDATE ON au_reports_r2
  FOR EACH ROW EXECUTE FUNCTION au_set_updated_at();

COMMENT ON COLUMN au_reports_r2.recommended_scenario         IS 'penetration / reference / premium';
COMMENT ON COLUMN au_reports_r2.user_adjusted_mode           IS 'percentage / absolute';
COMMENT ON COLUMN au_reports_r2.marketing_angle_key          IS 'complex_convenience / form_differentiation / hospital_procurement 등';
COMMENT ON COLUMN au_reports_r2.component_sum_basis          IS 'Case 2·3 성분별 합산 {성분A:$8, 성분B:$17, 합:$25}';


-- ════════════════════════════════════════════════════════════════════════
-- 6) au_reports_r3 — 보고서 ③ TOP10 바이어 접근문
-- ════════════════════════════════════════════════════════════════════════
-- 용도: Haiku 가 생성한 Top3 접근 제안문. Top10 원본은 au_buyers.

CREATE TABLE IF NOT EXISTS au_reports_r3 (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id           TEXT NOT NULL UNIQUE,
  psi_weights          JSONB NOT NULL DEFAULT '{}'::jsonb,
  top3_approach_ko     JSONB NOT NULL DEFAULT '[]'::jsonb,
  full_json_raw        JSONB NOT NULL DEFAULT '{}'::jsonb,
  haiku_model_version  TEXT DEFAULT 'claude-haiku-4-5-20251001',
  haiku_generated_at   TIMESTAMPTZ,
  validation_passed    BOOLEAN DEFAULT false,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_au_reports_r3_product_id ON au_reports_r3(product_id);

DROP TRIGGER IF EXISTS trg_au_reports_r3_updated_at ON au_reports_r3;
CREATE TRIGGER trg_au_reports_r3_updated_at
  BEFORE UPDATE ON au_reports_r3
  FOR EACH ROW EXECUTE FUNCTION au_set_updated_at();

COMMENT ON COLUMN au_reports_r3.psi_weights      IS 'AHP 가중치 스냅샷 {sales_scale:30, pipeline:25, ...}';
COMMENT ON COLUMN au_reports_r3.top3_approach_ko IS '배열 길이 3 [{rank, company_name, approach_text_ko}]';


-- ════════════════════════════════════════════════════════════════════════
-- 7) au_buyers — 바이어 마스터 + AHP PSI 5축
-- ════════════════════════════════════════════════════════════════════════
-- 용도: 품목별 Top10 바이어 + AHP PSI 점수. 기존 australia_buyers 교체.

CREATE TABLE IF NOT EXISTS au_buyers (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id             TEXT NOT NULL,
  rank                   SMALLINT,
  company_name           TEXT,
  abn                    TEXT,
  state                  TEXT,
  -- PSI 5 축 (100 점 만점)
  psi_sales_scale        SMALLINT,
  psi_pipeline           SMALLINT,
  psi_manufacturing      SMALLINT,
  psi_import_exp         SMALLINT,
  psi_pharmacy_chain     SMALLINT,
  psi_total              SMALLINT,
  -- 출처 플래그 · 근거
  source_flags           JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_urls          JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_au_buyers_product_rank ON au_buyers(product_id, rank);
CREATE INDEX IF NOT EXISTS idx_au_buyers_psi_total           ON au_buyers(psi_total DESC);
CREATE INDEX IF NOT EXISTS idx_au_buyers_abn                 ON au_buyers(abn);

DROP TRIGGER IF EXISTS trg_au_buyers_updated_at ON au_buyers;
CREATE TRIGGER trg_au_buyers_updated_at
  BEFORE UPDATE ON au_buyers
  FOR EACH ROW EXECUTE FUNCTION au_set_updated_at();

COMMENT ON COLUMN au_buyers.psi_sales_scale     IS '매출규모 (30 점)';
COMMENT ON COLUMN au_buyers.psi_pipeline        IS '파이프라인 (25 점)';
COMMENT ON COLUMN au_buyers.psi_manufacturing   IS '제조소 보유 (20 점)';
COMMENT ON COLUMN au_buyers.psi_import_exp      IS '수입 경험 (15 점)';
COMMENT ON COLUMN au_buyers.psi_pharmacy_chain  IS '약국 체인 (10 점)';
COMMENT ON COLUMN au_buyers.source_flags        IS '{tga:true, pbs:true, nsw:false} 점수 근거 소스';

-- ════════════════════════════════════════════════════════════════════════
-- 바이어발굴 (Phase 1, 2026-04-19) — au_buyers ALTER ADD COLUMN only.
-- 기존 컬럼 수정/DROP 금지. psi_* 점수 컬럼은 그대로 재사용 (30/25/20/15/10).
-- ════════════════════════════════════════════════════════════════════════
ALTER TABLE au_buyers
  -- 카드 닫힘 시 6컬럼 표시용
  ADD COLUMN IF NOT EXISTS annual_revenue_rank  TEXT,                    -- 하드코딩 자유텍스트 ("TOP 5 (제네릭 1위)")
  ADD COLUMN IF NOT EXISTS primary_products_kr  JSONB DEFAULT '[]'::jsonb,  -- Haiku 한국어 3개 이내
  ADD COLUMN IF NOT EXISTS has_au_factory       TEXT,                    -- "Y" / "N" / "unknown"
  ADD COLUMN IF NOT EXISTS factory_locations    JSONB DEFAULT '[]'::jsonb, -- 하드코딩 도시 배열
  ADD COLUMN IF NOT EXISTS ingredient_case      TEXT,                    -- "A_competitor"/"B_ideal_buyer"/"C_partial"/"D_none"
  ADD COLUMN IF NOT EXISTS ingredient_label     TEXT,                    -- 카드 표시용 ("별개 보유 (rosuvastatin)")
  -- 카드 펼침 시 표시
  ADD COLUMN IF NOT EXISTS business_model       TEXT,                    -- originator/generic/hybrid/unknown
  ADD COLUMN IF NOT EXISTS represented_brands   JSONB DEFAULT '[]'::jsonb, -- GPCE 크롤 원본
  ADD COLUMN IF NOT EXISTS tga_artg_count       INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS pbs_listed_count     INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS is_ma_member         BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_gbma_member       BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_gpce_exhibitor    BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS reasoning            TEXT,                    -- Haiku 추천 근거 3문장
  ADD COLUMN IF NOT EXISTS notes                TEXT,                    -- 하드코딩 자유 메모
  ADD COLUMN IF NOT EXISTS company_key          TEXT,                    -- canonical_key (정규화 키)
  ADD COLUMN IF NOT EXISTS website              TEXT,
  ADD COLUMN IF NOT EXISTS email                TEXT,
  ADD COLUMN IF NOT EXISTS phone                TEXT;

-- 신규 인덱스 (기존 idx_au_buyers_product_rank · _psi_total · _abn 유지)
CREATE INDEX IF NOT EXISTS idx_au_buyers_company_key      ON au_buyers(company_key);
CREATE INDEX IF NOT EXISTS idx_au_buyers_ingredient_case  ON au_buyers(ingredient_case);

-- 점수 매핑 주석 (재사용):
--   score_total    → psi_total           (기존)
--   score_revenue  → psi_sales_scale     (기존, 30 만점)
--   score_pipeline → psi_pipeline        (기존, 25 만점)
--   score_mfg      → psi_manufacturing   (기존, 20 만점)
--   score_import   → psi_import_exp      (기존, 15 만점)
--   score_pharmacy → psi_pharmacy_chain  (기존, 10 만점)
COMMENT ON COLUMN au_buyers.annual_revenue_rank IS '하드코딩 매출 티어 자유텍스트';
COMMENT ON COLUMN au_buyers.has_au_factory      IS '하드코딩 Y/N/unknown';
COMMENT ON COLUMN au_buyers.ingredient_case     IS '4-case 성분 보유 분류 (A_competitor/B_ideal_buyer/C_partial/D_none)';
COMMENT ON COLUMN au_buyers.company_key         IS 'buyer_discovery.stage1_filter.normalize_name() 결과 (canonical key)';


-- ════════════════════════════════════════════════════════════════════════
-- 8) au_report_refs — 하이브리드 참고자료 (Perplexity / PubMed / Semantic Scholar)
-- ════════════════════════════════════════════════════════════════════════
-- 용도: 보고서 본문 각주 ↔ 서지 정보 매핑.

CREATE TABLE IF NOT EXISTS au_report_refs (
  id              BIGSERIAL PRIMARY KEY,
  product_id      TEXT NOT NULL,
  report_type     TEXT NOT NULL,
  citation_index  SMALLINT NOT NULL,
  source          TEXT NOT NULL,
  title           TEXT,
  url             TEXT,
  authors         JSONB NOT NULL DEFAULT '[]'::jsonb,
  published_date  DATE,
  accessed_at     TIMESTAMPTZ,
  snippet_ko      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_au_report_refs_citation
  ON au_report_refs(product_id, report_type, citation_index);

COMMENT ON COLUMN au_report_refs.report_type IS 'r1 / r2 / r3';
COMMENT ON COLUMN au_report_refs.source      IS 'perplexity / pubmed / semantic_scholar';


-- ════════════════════════════════════════════════════════════════════════
-- 9) au_crawl_log — 크롤 이력·에러 (APPEND-ONLY)
-- ════════════════════════════════════════════════════════════════════════
-- 용도: 크롤 실행별 성공·실패·재시도 기록. 디버깅 + SLA 모니터링.

CREATE TABLE IF NOT EXISTS au_crawl_log (
  id                      BIGSERIAL PRIMARY KEY,
  run_id                  UUID,
  product_id              TEXT,
  source_name             TEXT,
  endpoint                TEXT,
  status                  TEXT,
  http_status             SMALLINT,
  retry_count             SMALLINT DEFAULT 0,
  error_message           TEXT,
  duration_ms             INTEGER,
  started_at              TIMESTAMPTZ,
  finished_at             TIMESTAMPTZ,
  raw_response_truncated  TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_au_crawl_log_run_id      ON au_crawl_log(run_id);
CREATE INDEX IF NOT EXISTS idx_au_crawl_log_product_id  ON au_crawl_log(product_id);
CREATE INDEX IF NOT EXISTS idx_au_crawl_log_status      ON au_crawl_log(status);
CREATE INDEX IF NOT EXISTS idx_au_crawl_log_started_at  ON au_crawl_log(started_at DESC);

COMMENT ON COLUMN au_crawl_log.source_name IS 'pbs_api_v3 / tga / chemist_warehouse / buy_nsw / healthylife';
COMMENT ON COLUMN au_crawl_log.status      IS 'success / partial / failed / skipped';


-- ════════════════════════════════════════════════════════════════════════
-- 10) au_reports_history — Haiku 응답 append-only 스냅샷 (§14-7 결정 3)
-- ════════════════════════════════════════════════════════════════════════
-- 용도: Haiku 호출할 때마다 append. 보고서 버저닝·감사 추적용.

CREATE TABLE IF NOT EXISTS au_reports_history (
  id             BIGSERIAL PRIMARY KEY,
  product_id     TEXT NOT NULL,
  gong           SMALLINT NOT NULL,
  snapshot       JSONB NOT NULL DEFAULT '{}'::jsonb,
  report_content_v2 JSONB,
  llm_model      TEXT DEFAULT 'claude-haiku-4-5-20251001',
  generated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_au_reports_history_product_id  ON au_reports_history(product_id);
CREATE INDEX IF NOT EXISTS idx_au_reports_history_gong        ON au_reports_history(gong);
CREATE INDEX IF NOT EXISTS idx_au_reports_history_generated   ON au_reports_history(generated_at DESC);

COMMENT ON COLUMN au_reports_history.gong IS '단계 구분 (1=시장분석, 2=수출전략, 3=바이어)';


-- ════════════════════════════════════════════════════════════════════════
-- 11) au_regulatory — 호주 규제 체크포인트 시드 (v1 유지)
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS au_regulatory (
  id           SERIAL PRIMARY KEY,
  title        TEXT NOT NULL UNIQUE,
  description  TEXT,
  badge        TEXT,
  badge_color  TEXT,
  source_url   TEXT,
  content      TEXT,
  updated_at   DATE DEFAULT CURRENT_DATE
);

INSERT INTO au_regulatory (title, description, badge, badge_color, source_url) VALUES
('Therapeutic Goods Act 1989',
 'ARTG 등재 의무 · TGA 심사 12–18 개월 소요. 처방의약품은 Registered 또는 Listed 경로.',
 '핵심 장벽', 'orange',
 'https://www.legislation.gov.au/C2004A03952'),
('GMP 기준 (PIC/S 상호인정)',
 '한국 PIC/S 정회원(2014~). 호주 TGA 와 제조소 실사 면제 협의 가능.',
 '유리', 'green',
 'https://www.tga.gov.au/industry/manufacturing/overseas-manufacturers'),
('National Health Act 1953 (PBS)',
 'PBS 등재 시 가격 통제 수반. PBAC 심사 필요. 민간 유통 병행 전략 권장.',
 '공공조달', 'blue',
 'https://www.legislation.gov.au/C2004A07357'),
('KAFTA (한-호주 FTA)',
 '2014 년 발효. Chapter 30 의약품 관세 철폐 완료. HS 3004.90 / 3006.30 모두 0%.',
 '활성', 'green',
 'https://ftaportal.dfat.gov.au/tariff/KAFTA'),
('Customs (Prohibited Imports) Regulations 1956',
 '항암제·향정신성 성분 수입 시 별도 허가 필요. Hydrine(hydroxyurea) 해당 여부 사전 확인.',
 '확인 필요', 'gray',
 'https://www.legislation.gov.au/F1997B00390')
ON CONFLICT (title) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════
-- RLS (Row Level Security) 정책 — §14-6
-- ════════════════════════════════════════════════════════════════════════
-- 전 테이블: service_role 키로 백엔드(FastAPI)만 접근. 프론트 직접 접근 금지.
-- au_crawl_log: APPEND-ONLY (읽기·삽입만, 수정·삭제 불가).

ALTER TABLE au_products          ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_pbs_raw           ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_tga_artg          ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_reports_r1        ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_reports_r2        ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_reports_r3        ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_buyers            ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_report_refs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_crawl_log         ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_reports_history   ENABLE ROW LEVEL SECURITY;
ALTER TABLE au_regulatory        ENABLE ROW LEVEL SECURITY;

-- service_role 전용 정책 (모든 CRUD 허용)
DO $$
DECLARE
  tbl TEXT;
BEGIN
  FOR tbl IN
    SELECT unnest(ARRAY[
      'au_products','au_pbs_raw','au_tga_artg','au_reports_r1','au_reports_r2',
      'au_reports_r3','au_buyers','au_report_refs','au_crawl_log',
      'au_reports_history','au_regulatory'
    ])
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I_service_role_all ON %I;', tbl, tbl);
    EXECUTE format(
      'CREATE POLICY %I_service_role_all ON %I FOR ALL TO service_role USING (true) WITH CHECK (true);',
      tbl, tbl
    );
  END LOOP;
END
$$;

-- au_crawl_log: UPDATE · DELETE 차단 (APPEND-ONLY)
DROP POLICY IF EXISTS au_crawl_log_no_update ON au_crawl_log;
CREATE POLICY au_crawl_log_no_update ON au_crawl_log FOR UPDATE TO service_role USING (false);
DROP POLICY IF EXISTS au_crawl_log_no_delete ON au_crawl_log;
CREATE POLICY au_crawl_log_no_delete ON au_crawl_log FOR DELETE TO service_role USING (false);
