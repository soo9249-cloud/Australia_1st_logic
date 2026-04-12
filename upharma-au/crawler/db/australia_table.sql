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
