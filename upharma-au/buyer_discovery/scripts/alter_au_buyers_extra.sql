-- Stage 2 UPSERT 전 실행 — 누락된 컬럼 10개 + 프론트용 연락처 3개
-- 실행: Supabase Dashboard → SQL Editor → 복붙 → Run
-- (2026-04-20 실측: annual_revenue_rank 등이 실제 DB 에 없어서 INSERT 실패)

ALTER TABLE au_buyers
  ADD COLUMN IF NOT EXISTS annual_revenue_rank TEXT,
  ADD COLUMN IF NOT EXISTS has_au_factory TEXT,
  ADD COLUMN IF NOT EXISTS factory_locations JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS is_ma_member BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_gbma_member BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_gpce_exhibitor BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS tga_artg_count INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS company_key TEXT,
  ADD COLUMN IF NOT EXISTS reasoning TEXT,
  ADD COLUMN IF NOT EXISTS notes TEXT,
  ADD COLUMN IF NOT EXISTS website TEXT,
  ADD COLUMN IF NOT EXISTS email TEXT,
  ADD COLUMN IF NOT EXISTS phone TEXT;

-- 실행 후 확인 쿼리:
-- SELECT column_name, data_type FROM information_schema.columns WHERE table_name='au_buyers' ORDER BY ordinal_position;
