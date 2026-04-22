-- 비파괴 스키마 수렴 마이그레이션 (환경별 컬럼 차이 보정)
-- 원칙:
--   1) ADD COLUMN IF NOT EXISTS 중심
--   2) 기존 데이터 삭제 없음
--   3) au_reports_r2 의 단일 product_id unique 는 public/private 동시 저장을 위해 완화

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) au_reports_r2 수렴
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.au_reports_r2
  ADD COLUMN IF NOT EXISTS segment TEXT DEFAULT 'public',
  ADD COLUMN IF NOT EXISTS ref_price_text TEXT,
  ADD COLUMN IF NOT EXISTS ref_price_aud NUMERIC(14,4),
  ADD COLUMN IF NOT EXISTS verdict TEXT,
  ADD COLUMN IF NOT EXISTS logic TEXT,
  ADD COLUMN IF NOT EXISTS pricing_case TEXT,
  ADD COLUMN IF NOT EXISTS fob_penetration_aud NUMERIC(14,4),
  ADD COLUMN IF NOT EXISTS fob_reference_aud NUMERIC(14,4),
  ADD COLUMN IF NOT EXISTS fob_premium_aud NUMERIC(14,4),
  ADD COLUMN IF NOT EXISTS fob_penetration_krw NUMERIC(16,2),
  ADD COLUMN IF NOT EXISTS fob_reference_krw NUMERIC(16,2),
  ADD COLUMN IF NOT EXISTS fob_premium_krw NUMERIC(16,2),
  ADD COLUMN IF NOT EXISTS fx_aud_to_krw NUMERIC(14,6),
  ADD COLUMN IF NOT EXISTS fx_aud_to_usd NUMERIC(14,6),
  ADD COLUMN IF NOT EXISTS formula_str TEXT,
  ADD COLUMN IF NOT EXISTS block_market_macro TEXT,
  ADD COLUMN IF NOT EXISTS block_extract TEXT,
  ADD COLUMN IF NOT EXISTS block_fob_intro TEXT,
  ADD COLUMN IF NOT EXISTS scenario_penetration TEXT,
  ADD COLUMN IF NOT EXISTS scenario_reference TEXT,
  ADD COLUMN IF NOT EXISTS scenario_premium TEXT,
  ADD COLUMN IF NOT EXISTS block_strategy TEXT,
  ADD COLUMN IF NOT EXISTS block_risks TEXT,
  ADD COLUMN IF NOT EXISTS block_positioning TEXT,
  ADD COLUMN IF NOT EXISTS warnings JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS disclaimer TEXT,
  ADD COLUMN IF NOT EXISTS llm_model TEXT,
  ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS pdf_filename TEXT,
  ADD COLUMN IF NOT EXISTS report_content_v2 JSONB;

-- product_id only unique 는 이중 세그먼트(public/private) 저장을 막음.
-- 존재하면 제거 후 product_id+segment unique 로 수렴.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema='public'
      AND table_name='au_reports_r2'
      AND constraint_name='au_reports_r2_product_id_key'
      AND constraint_type='UNIQUE'
  ) THEN
    ALTER TABLE public.au_reports_r2 DROP CONSTRAINT au_reports_r2_product_id_key;
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema='public'
      AND table_name='au_reports_r2'
      AND constraint_name='au_reports_r2_product_segment_unique'
      AND constraint_type='UNIQUE'
  ) THEN
    ALTER TABLE public.au_reports_r2
      ADD CONSTRAINT au_reports_r2_product_segment_unique UNIQUE (product_id, segment);
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_au_reports_r2_generated_at
  ON public.au_reports_r2 (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_au_reports_r2_segment
  ON public.au_reports_r2 (segment);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2) au_reports_history 수렴
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.au_reports_history
  ADD COLUMN IF NOT EXISTS report_content_v2 JSONB,
  ADD COLUMN IF NOT EXISTS llm_model TEXT,
  ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_au_reports_history_generated_at
  ON public.au_reports_history (generated_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3) au_buyers 수렴 (stage2_scoring insert/upsert 컬럼 보장)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.au_buyers
  ADD COLUMN IF NOT EXISTS company_key TEXT,
  ADD COLUMN IF NOT EXISTS annual_revenue_rank TEXT,
  ADD COLUMN IF NOT EXISTS has_au_factory TEXT,
  ADD COLUMN IF NOT EXISTS factory_locations JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS therapeutic_categories JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS is_ma_member BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_gbma_member BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_gpce_exhibitor BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS tga_artg_count INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS source_flags JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS evidence_urls JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS reasoning TEXT,
  ADD COLUMN IF NOT EXISTS notes TEXT,
  ADD COLUMN IF NOT EXISTS website TEXT,
  ADD COLUMN IF NOT EXISTS email TEXT,
  ADD COLUMN IF NOT EXISTS phone TEXT,
  ADD COLUMN IF NOT EXISTS state TEXT,
  ADD COLUMN IF NOT EXISTS last_researched_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema='public'
      AND table_name='au_buyers'
      AND constraint_name='au_buyers_product_rank_unique'
      AND constraint_type='UNIQUE'
  ) THEN
    ALTER TABLE public.au_buyers
      ADD CONSTRAINT au_buyers_product_rank_unique UNIQUE (product_id, rank);
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_au_buyers_product_rank
  ON public.au_buyers (product_id, rank);

COMMIT;

