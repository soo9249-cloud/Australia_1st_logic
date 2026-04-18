-- migrate_v1_to_v2.sql — 기존 6 테이블 → 신규 10 테이블 마이그레이션
-- 작성일: 2026-04-18
-- 스펙: /AX 호주 final/02_ClaudeCode_위임지시서_v1.md §3
--
-- 실행 전제:
--   1. australia_table.sql v2 가 먼저 실행되어 신규 10 테이블이 생성된 상태
--   2. 기존 테이블(australia, australia_history, australia_buyers, australia_p2_results, reports)
--      은 아직 RENAME 되지 않음
--
-- 실행 정책:
--   - 기존 테이블은 RENAME `_legacy_20260418` 로 보존 (DROP 금지, 1 주일 뒤 수동 삭제)
--   - 데이터 이관은 최소 매핑만. 컬럼 의미가 1:1 매칭되는 것만 INSERT.
--   - INSERT 직전 SELECT COUNT 로 행수 검증 (RAISE NOTICE)

BEGIN;

-- ── 0. 기존 테이블 존재 확인 + 행수 기록 ──────────────────────────────────
DO $$
DECLARE
  c_australia       INTEGER := 0;
  c_australia_hist  INTEGER := 0;
  c_australia_buy   INTEGER := 0;
  c_australia_p2    INTEGER := 0;
  c_reports         INTEGER := 0;
BEGIN
  SELECT COUNT(*) INTO c_australia
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'australia';
  IF c_australia > 0 THEN
    EXECUTE 'SELECT COUNT(*) FROM australia' INTO c_australia;
    RAISE NOTICE '[v1] australia                = % rows', c_australia;
  END IF;

  SELECT COUNT(*) INTO c_australia_hist
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'australia_history';
  IF c_australia_hist > 0 THEN
    EXECUTE 'SELECT COUNT(*) FROM australia_history' INTO c_australia_hist;
    RAISE NOTICE '[v1] australia_history        = % rows', c_australia_hist;
  END IF;

  SELECT COUNT(*) INTO c_australia_buy
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'australia_buyers';
  IF c_australia_buy > 0 THEN
    EXECUTE 'SELECT COUNT(*) FROM australia_buyers' INTO c_australia_buy;
    RAISE NOTICE '[v1] australia_buyers         = % rows', c_australia_buy;
  END IF;

  SELECT COUNT(*) INTO c_australia_p2
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'australia_p2_results';
  IF c_australia_p2 > 0 THEN
    EXECUTE 'SELECT COUNT(*) FROM australia_p2_results' INTO c_australia_p2;
    RAISE NOTICE '[v1] australia_p2_results     = % rows', c_australia_p2;
  END IF;

  SELECT COUNT(*) INTO c_reports
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reports';
  IF c_reports > 0 THEN
    EXECUTE 'SELECT COUNT(*) FROM reports' INTO c_reports;
    RAISE NOTICE '[v1] reports                  = % rows', c_reports;
  END IF;
END
$$;


-- ── 1. australia → au_products (데이터 이관, 매핑 가능 컬럼만) ────────────────
-- 매핑 원칙: 이름이 동일하거나 의미가 1:1 인 컬럼만 이관.
-- 사라진 컬럼(pbs_brands, block2_*/block3_*/block4_* 등)은 폐기.
-- 신규 컬럼(case_code, originator_brand_name, ...)은 NULL 로 두고 재크롤링 시 채움.
INSERT INTO au_products (
  product_code,
  product_name_ko,
  inn_normalized,
  strength,
  dosage_form,
  tga_found,
  pbs_found,
  pbs_code,
  program_code,
  formulary,
  aemp_aud,
  claimed_price_aud,
  dpmq_aud,
  pack_size,
  pricing_quantity,
  retail_price_aud,
  retail_estimation_method,
  chemist_price_aud,
  chemist_url,
  confidence,
  last_crawled_at
)
SELECT
  a.product_id                      AS product_code,
  a.product_name_ko,
  a.inn_normalized,
  a.strength,
  a.dosage_form,
  COALESCE(a.artg_status = 'registered', false)  AS tga_found,
  COALESCE(a.pbs_listed, false)                  AS pbs_found,
  a.pbs_item_code                                AS pbs_code,
  a.pbs_program_code                             AS program_code,
  a.pbs_formulary                                AS formulary,
  a.pbs_determined_price                         AS aemp_aud,
  a.pbs_price_aud                                AS claimed_price_aud,
  a.pbs_dpmq                                     AS dpmq_aud,
  a.pbs_pack_size                                AS pack_size,
  a.pbs_pricing_quantity                         AS pricing_quantity,
  a.retail_price_aud,
  a.retail_estimation_method,
  a.chemist_price_aud,
  a.price_source_url                             AS chemist_url,
  a.confidence,
  a.crawled_at                                   AS last_crawled_at
FROM australia a
WHERE EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = 'public' AND table_name = 'australia'
)
ON CONFLICT (product_code) DO NOTHING;

DO $$
DECLARE n INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM au_products;
  RAISE NOTICE '[v2] au_products              = % rows inserted', n;
END
$$;


-- ── 2. australia_p2_results → au_reports_r2 (UPSERT) ─────────────────────────
-- p2 결과는 컬럼이 거의 1:1 매칭. product_id 는 TEXT 그대로, 신규 FOB USD 는 NULL.
INSERT INTO au_reports_r2 (
  product_id,
  fob_penetration_aud,
  fob_reference_aud,
  fob_premium_aud,
  fob_penetration_krw,
  fob_reference_krw,
  fob_premium_krw,
  full_json_raw,
  haiku_model_version,
  haiku_generated_at,
  validation_passed
)
SELECT
  p.product_id,
  p.fob_penetration_aud,
  p.fob_reference_aud,
  p.fob_premium_aud,
  p.fob_penetration_krw,
  p.fob_reference_krw,
  p.fob_premium_krw,
  jsonb_build_object(
    'ref_price_text',      p.ref_price_text,
    'ref_price_aud',       p.ref_price_aud,
    'verdict',             p.verdict,
    'logic',               p.logic,
    'pricing_case',        p.pricing_case,
    'fx_aud_to_krw',       p.fx_aud_to_krw,
    'fx_aud_to_usd',       p.fx_aud_to_usd,
    'formula_str',         p.formula_str,
    'block_extract',       p.block_extract,
    'block_fob_intro',     p.block_fob_intro,
    'scenario_penetration', p.scenario_penetration,
    'scenario_reference',  p.scenario_reference,
    'scenario_premium',    p.scenario_premium,
    'block_strategy',      p.block_strategy,
    'block_risks',         p.block_risks,
    'block_positioning',   p.block_positioning,
    'warnings',            p.warnings,
    'disclaimer',          p.disclaimer,
    'pdf_filename',        p.pdf_filename,
    'segment',             p.segment
  )                                 AS full_json_raw,
  p.llm_model                       AS haiku_model_version,
  p.generated_at                    AS haiku_generated_at,
  (p.logic IS NOT NULL AND p.logic <> 'blocked')  AS validation_passed
FROM australia_p2_results p
WHERE EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = 'public' AND table_name = 'australia_p2_results'
)
ON CONFLICT (product_id) DO NOTHING;

DO $$
DECLARE n INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM au_reports_r2;
  RAISE NOTICE '[v2] au_reports_r2            = % rows inserted', n;
END
$$;


-- ── 3. australia_buyers → au_buyers (컬럼 거의 동일) ─────────────────────────
INSERT INTO au_buyers (
  product_id,
  company_name,
  abn,
  psi_sales_scale,
  psi_pipeline,
  psi_manufacturing,
  psi_import_exp,
  psi_pharmacy_chain,
  psi_total,
  source_flags,
  evidence_urls
)
SELECT
  b.product_id,
  b.company_name,
  b.abn,
  b.psi_sales_scale,
  b.psi_pipeline,
  b.psi_manufacturing,
  b.psi_import_exp,
  b.psi_pharmacy_chain,
  b.psi_total,
  jsonb_build_object(
    'tga',              COALESCE(b.source_tga, false),
    'pbs',              COALESCE(b.source_pbs, false),
    'nsw',              COALESCE(b.source_nsw, false),
    'has_gmp',          COALESCE(b.has_gmp, false),
    'has_pharmacy_chain', COALESCE(b.has_pharmacy_chain, false)
  )                                 AS source_flags,
  COALESCE(b.reference_urls, '[]'::jsonb) AS evidence_urls
FROM australia_buyers b
WHERE EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = 'public' AND table_name = 'australia_buyers'
);

DO $$
DECLARE n INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM au_buyers;
  RAISE NOTICE '[v2] au_buyers                = % rows inserted', n;
END
$$;


-- ── 4. reports → au_reports_history ─────────────────────────────────────────
-- 기존 reports 테이블: id, product_id, gong, title, file_url, crawled_data, created_at
-- au_reports_history: id, product_id, gong, snapshot(JSONB), llm_model, generated_at, created_at
INSERT INTO au_reports_history (
  product_id,
  gong,
  snapshot,
  llm_model,
  generated_at,
  created_at
)
SELECT
  r.product_id,
  COALESCE(r.gong, 1),
  jsonb_build_object(
    'title',        r.title,
    'file_url',     r.file_url,
    'crawled_data', COALESCE(r.crawled_data, '{}'::jsonb)
  )                                 AS snapshot,
  'legacy_import'                   AS llm_model,
  r.created_at                      AS generated_at,
  r.created_at
FROM reports r
WHERE EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = 'public' AND table_name = 'reports'
);

DO $$
DECLARE n INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM au_reports_history;
  RAISE NOTICE '[v2] au_reports_history       = % rows inserted', n;
END
$$;


-- ── 5. australia_history 는 폐기 (pbs 원본 보관소 의미 변질 방지) ────────────
-- au_pbs_raw 는 PBS API v3 9 엔드포인트 원본만 받는 테이블 → 기존 snapshot 과 의미 다름.
-- 따라서 australia_history 의 snapshot 은 이관하지 않고 _legacy 로 보존만.


-- ── 6. 기존 테이블 RENAME to _legacy_20260418 ───────────────────────────────
-- DROP 하지 않고 1 주일 보존 (롤백 대비). §8 롤백 절차 참조.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia') THEN
    EXECUTE 'ALTER TABLE australia RENAME TO australia_legacy_20260418';
    RAISE NOTICE '[rename] australia              → australia_legacy_20260418';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia_history') THEN
    EXECUTE 'ALTER TABLE australia_history RENAME TO australia_history_legacy_20260418';
    RAISE NOTICE '[rename] australia_history      → australia_history_legacy_20260418';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia_buyers') THEN
    EXECUTE 'ALTER TABLE australia_buyers RENAME TO australia_buyers_legacy_20260418';
    RAISE NOTICE '[rename] australia_buyers       → australia_buyers_legacy_20260418';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'australia_p2_results') THEN
    EXECUTE 'ALTER TABLE australia_p2_results RENAME TO australia_p2_results_legacy_20260418';
    RAISE NOTICE '[rename] australia_p2_results   → australia_p2_results_legacy_20260418';
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = 'reports') THEN
    EXECUTE 'ALTER TABLE reports RENAME TO reports_legacy_20260418';
    RAISE NOTICE '[rename] reports                → reports_legacy_20260418';
  END IF;
END
$$;


-- ── 7. PostgREST 스키마 캐시 리로드 ─────────────────────────────────────────
NOTIFY pgrst, 'reload schema';


COMMIT;

-- ── 최종 행수 검증 (트랜잭션 외부에서 수동 실행 권장) ────────────────────────
-- SELECT
--   (SELECT COUNT(*) FROM au_products)          AS au_products,
--   (SELECT COUNT(*) FROM au_reports_r2)        AS au_reports_r2,
--   (SELECT COUNT(*) FROM au_buyers)            AS au_buyers,
--   (SELECT COUNT(*) FROM au_reports_history)   AS au_reports_history;
