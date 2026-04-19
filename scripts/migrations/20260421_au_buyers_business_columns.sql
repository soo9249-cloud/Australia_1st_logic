-- au_buyers — 매출·공장·협회·연락처 등 비즈니스 필드 (idempotent)
-- 실행: Supabase SQL Editor 붙여넣기 → Run, 또는 python scripts/migrate.py (전체 파이프라인)
-- 이미 australia_table.sql Phase1 로 들어간 DB 는 ADD COLUMN IF NOT EXISTS 만 통과·나머지 스킵

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
