-- migrate_v1_to_v2_rollback.sql — 실패 시 v1 복원
-- 작성일: 2026-04-18
-- 스펙: /AX 호주 final/02_ClaudeCode_위임지시서_v1.md §8
--
-- 실행 시점: migrate_v1_to_v2.sql 적용 후 문제 발견 시 (최대 1 주일 내)
-- 효과: 신규 10 테이블 DROP + legacy 5 테이블 RENAME back
--
-- 주의:
--   - 신규 테이블에만 쌓인 데이터는 전부 사라짐 (v2 로 재마이그레이션 시 재크롤 필요)
--   - 코드 롤백(supabase_insert.py, migrate.py, render_api.py) 은 별도로 `git revert` 필요

BEGIN;

-- ── 1. 신규 테이블 DROP (의존 순서 역순) ─────────────────────────────────────
DROP TABLE IF EXISTS au_report_refs       CASCADE;
DROP TABLE IF EXISTS au_crawl_log         CASCADE;
DROP TABLE IF EXISTS au_reports_history   CASCADE;
DROP TABLE IF EXISTS au_reports_r1        CASCADE;
DROP TABLE IF EXISTS au_reports_r2        CASCADE;
DROP TABLE IF EXISTS au_reports_r3        CASCADE;
DROP TABLE IF EXISTS au_buyers            CASCADE;
DROP TABLE IF EXISTS au_pbs_raw           CASCADE;
DROP TABLE IF EXISTS au_tga_artg          CASCADE;
DROP TABLE IF EXISTS au_products          CASCADE;

-- au_regulatory 는 v1 에도 존재하므로 DROP 하지 않음 (유지)

-- au_set_updated_at() 함수 제거 (다른 테이블에서 참조 중 아닌 경우)
DROP FUNCTION IF EXISTS au_set_updated_at();


-- ── 2. legacy 테이블 RENAME back ────────────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia_legacy_20260418') THEN
    EXECUTE 'ALTER TABLE australia_legacy_20260418 RENAME TO australia';
    RAISE NOTICE '[rollback] australia_legacy_20260418              → australia';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia_history_legacy_20260418') THEN
    EXECUTE 'ALTER TABLE australia_history_legacy_20260418 RENAME TO australia_history';
    RAISE NOTICE '[rollback] australia_history_legacy_20260418      → australia_history';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia_buyers_legacy_20260418') THEN
    EXECUTE 'ALTER TABLE australia_buyers_legacy_20260418 RENAME TO australia_buyers';
    RAISE NOTICE '[rollback] australia_buyers_legacy_20260418       → australia_buyers';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia_p2_results_legacy_20260418') THEN
    EXECUTE 'ALTER TABLE australia_p2_results_legacy_20260418 RENAME TO australia_p2_results';
    RAISE NOTICE '[rollback] australia_p2_results_legacy_20260418   → australia_p2_results';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'reports_legacy_20260418') THEN
    EXECUTE 'ALTER TABLE reports_legacy_20260418 RENAME TO reports';
    RAISE NOTICE '[rollback] reports_legacy_20260418                → reports';
  END IF;
END
$$;


-- ── 3. PostgREST 스키마 캐시 리로드 ─────────────────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;

-- ── 확인 쿼리 (수동 실행) ───────────────────────────────────────────────────
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public' AND table_name LIKE 'australia%'
-- ORDER BY table_name;
