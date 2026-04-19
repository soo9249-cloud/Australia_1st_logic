-- Stage 2 바이어발굴 au_buyers 컬럼 확장
-- 실행: Supabase Dashboard → SQL Editor → 복붙 → Run
-- 실행 시점: buyer_discovery/scripts/stage2_scoring.py 돌리기 직전

-- 1. 치료영역 배열 (company_categories.json 결과 반영용)
ALTER TABLE au_buyers
  ADD COLUMN IF NOT EXISTS therapeutic_categories JSONB DEFAULT '[]'::jsonb;

-- 2. 조사 타임스탬프 (주 1회 갱신 추적)
ALTER TABLE au_buyers
  ADD COLUMN IF NOT EXISTS last_researched_at TIMESTAMPTZ;

-- 3. 등급 문자열 보강 (매출 등급 표시용 — annual_revenue_rank 는 기존에 있음)
--    기존 컬럼 확인용: SELECT column_name FROM information_schema.columns WHERE table_name='au_buyers';

-- 4. product_id + rank 유니크 — 품목별 1~10 순위 보장
--    australia_table.sql 에 이미 CREATE UNIQUE INDEX idx_au_buyers_product_rank 가 있으면
--    동일 제약이므로 CONSTRAINT 추가 생략 (중복 시 DDL 오류 방지)
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

-- 검증 쿼리 (Stage 2 실행 후)
-- SELECT product_id, rank, company_name, psi_total, annual_revenue_rank, has_au_factory
-- FROM au_buyers
-- ORDER BY product_id, rank;
