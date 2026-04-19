-- 보고서 본문 계층 스키마(v8/v5) 저장용 JSONB — 기존 flat 컬럼·snapshot 과 병행
-- 적용: python scripts/migrate.py (australia_table.sql 이후 순차 실행)

ALTER TABLE public.au_reports_history
  ADD COLUMN IF NOT EXISTS report_content_v2 JSONB;

ALTER TABLE public.au_reports_r2
  ADD COLUMN IF NOT EXISTS report_content_v2 JSONB;

COMMENT ON COLUMN public.au_reports_history.report_content_v2 IS
  '시장분석 등 gong 단계별 보고서 본문 (schema_ver, report_kind, blocks …). 품목 공통 봉투.';

COMMENT ON COLUMN public.au_reports_r2.report_content_v2 IS
  '수출전략 보고서 본문 스냅샷 (schema_ver, report_kind, p2_blocks …). full_json_raw 과 병행 가능.';
