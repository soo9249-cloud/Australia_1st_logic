-- Stage 2 바이어발굴 au_buyers 컬럼 확장 (2026-04-20)
-- 실행: python scripts/migrate.py (또는 Supabase SQL Editor 에서 본문만 붙여넣기)

ALTER TABLE au_buyers
  ADD COLUMN IF NOT EXISTS therapeutic_categories JSONB DEFAULT '[]'::jsonb;

ALTER TABLE au_buyers
  ADD COLUMN IF NOT EXISTS last_researched_at TIMESTAMPTZ;

-- product_id + rank 유니크: greenfield 는 australia_table.sql 의 idx_au_buyers_product_rank 로 이미 보장.
-- 인덱스가 없고 제약도 없을 때만 CONSTRAINT 추가 (기존 유니크 인덱스와 중복 생성 방지)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND tablename = 'au_buyers' AND indexname = 'idx_au_buyers_product_rank'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema = 'public' AND table_name = 'au_buyers'
      AND constraint_type = 'UNIQUE'
      AND constraint_name = 'au_buyers_product_rank_unique'
  ) THEN
    ALTER TABLE au_buyers
      ADD CONSTRAINT au_buyers_product_rank_unique UNIQUE (product_id, rank);
  END IF;
END$$;

COMMENT ON COLUMN au_buyers.therapeutic_categories IS '치료영역 분류 JSON 배열 (company_categories 등)';
COMMENT ON COLUMN au_buyers.last_researched_at IS '바이어 조사 마지막 실행 시각 (주기 갱신 추적)';
