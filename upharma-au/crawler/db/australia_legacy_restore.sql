-- ============================================================================
-- australia 레거시 테이블 복구 (Phase-1 전체 스키마 · 77 컬럼)
-- 원본: australia_table.sql.bak_20260418 §1
--
-- 용도: SQL Editor 에서 잘못 붙여넣은 DROP + 단순 CREATE 로 테이블이 깨졌을 때
--       구조만 정상으로 되돌린다. **DROP CASCADE 시 기존 australia 행은 삭제됨** (데이터 복구 불가).
--
-- 실행: python scripts/migrate.py (v2 DDL 후 이 파일이 자동 실행됨)
-- ============================================================================

DROP TABLE IF EXISTS australia CASCADE;

CREATE TABLE australia (
  -- 공통 6컬럼 (변경 금지)
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id        TEXT NOT NULL UNIQUE,
  market_segment    TEXT DEFAULT 'public',
  fob_estimated_usd DECIMAL,
  confidence        DECIMAL,
  crawled_at        TIMESTAMPTZ DEFAULT now(),

  -- 품목 마스터 (au_products.json)
  product_name_ko   TEXT,
  inn_normalized    TEXT,
  hs_code_6         TEXT,
  dosage_form       TEXT,
  strength          TEXT,
  pricing_case      TEXT,

  -- TGA ARTG
  artg_number           TEXT,
  artg_status           TEXT,
  tga_schedule          TEXT,
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
  pbs_web_source_url    TEXT,

  retail_price_aud         DECIMAL,
  chemist_price_aud        DECIMAL,
  retail_estimation_method TEXT,
  price_source_name        TEXT,
  price_source_url         TEXT,
  price_unit               TEXT,

  nsw_contract_value_aud DECIMAL,
  nsw_supplier_name      TEXT,
  nsw_contract_date      TEXT,
  nsw_source_url         TEXT,
  nsw_note               TEXT,

  export_viable   TEXT,
  reason_code     TEXT,

  evidence_url     TEXT,
  evidence_text    TEXT,
  evidence_text_ko TEXT,

  fob_local_ref_aud    DECIMAL,
  fob_conservative_usd DECIMAL,
  fob_base_usd         DECIMAL,
  fob_aggressive_usd   DECIMAL,
  fob_confidence       DECIMAL,

  sites              JSONB,
  completeness_ratio DECIMAL,
  data_source_count  INTEGER,
  error_type         TEXT,

  block2_market      TEXT,
  block2_regulatory  TEXT,
  block2_trade       TEXT,
  block2_procurement TEXT,
  block2_channel     TEXT,

  block3_channel     TEXT,
  block3_pricing     TEXT,
  block3_partners    TEXT,
  block3_risks       TEXT,

  block4_regulatory  TEXT,

  perplexity_refs    JSONB,

  llm_model          TEXT,
  llm_generated_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_australia_product_id ON australia(product_id);
CREATE INDEX IF NOT EXISTS idx_australia_crawled_at ON australia(crawled_at DESC);
