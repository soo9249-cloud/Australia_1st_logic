-- ============================================================================
-- Supabase SQL Editor → 전체 복사 → Run (한 번)
-- 저장소: upharma-au/crawler/db/australia_table.sql + views_dashboard.sql 와 정합
--
-- 역할
--   · 실행 시간·기록: 크롤러(au_crawler.py + supabase_insert.py)가
--     au_crawl_log.started_at / finished_at / duration_ms,
--     au_products.last_crawled_at 등에 씁니다. (이 스크립트는 DB 쪽 보조)
--   · 아래는 (1) 예전 DB에 빠진 시간 컬럼 보강 (2) 조회용 인덱스 (3) 대시보드 VIEW
-- ============================================================================

-- ── 1) 시간·소요 컬럼 보강 (이미 있으면 스킵) ─────────────────────────────
ALTER TABLE public.au_crawl_log
  ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

ALTER TABLE public.au_products
  ADD COLUMN IF NOT EXISTS last_crawled_at TIMESTAMPTZ;

COMMENT ON COLUMN public.au_crawl_log.started_at IS '소스 호출 시작 시각 (크롤러 now_kst_iso 등)';
COMMENT ON COLUMN public.au_crawl_log.finished_at IS '소스 호출 종료 시각';
COMMENT ON COLUMN public.au_crawl_log.duration_ms IS '해당 소스 처리 소요(ms)';
COMMENT ON COLUMN public.au_products.last_crawled_at IS '메인 크롤 배치 마지막 시각';

-- ── 2) 시간 역순 조회용 인덱스 (australia_table.sql 과 동일 이름) ───────────
CREATE INDEX IF NOT EXISTS idx_au_crawl_log_started_at
  ON public.au_crawl_log (started_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_au_products_last_crawled_at
  ON public.au_products (last_crawled_at DESC NULLS LAST);

-- ── 3) 대시보드 VIEW: 시간 컬럼을 맨 앞에 (읽기 전용, 적재 대상 아님) ─────
CREATE OR REPLACE VIEW public.v_au_crawl_log AS
SELECT
  started_at,
  finished_at,
  created_at,
  id,
  run_id,
  product_id,
  source_name,
  endpoint,
  status,
  http_status,
  retry_count,
  error_message,
  duration_ms,
  raw_response_truncated
FROM public.au_crawl_log;

COMMENT ON VIEW public.v_au_crawl_log IS
  '대시보드용: 실행 시각 컬럼 우선. 원본은 public.au_crawl_log';

CREATE OR REPLACE VIEW public.v_au_products AS
SELECT
  last_crawled_at,
  created_at,
  updated_at,
  id,
  product_code,
  product_name_ko,
  inn_normalized,
  strength,
  dosage_form,
  case_code,
  case_risk_text_ko,
  tga_found,
  tga_artg_ids,
  tga_sponsors,
  pbs_found,
  pbs_code,
  program_code,
  section_85_100,
  formulary,
  aemp_aud,
  aemp_usd,
  aemp_krw,
  spd_aud,
  claimed_price_aud,
  dpmq_aud,
  dpmq_usd,
  dpmq_krw,
  mn_pharmacy_price_aud,
  brand_premium_aud,
  therapeutic_group_premium_aud,
  special_patient_contrib_aud,
  wholesale_markup_band,
  pharmacy_markup_code,
  markup_variable_pct,
  markup_offset_aud,
  markup_fixed_aud,
  dispensing_fee_aud,
  ahi_fee_aud,
  originator_brand,
  therapeutic_group_id,
  brand_substitution_group_id,
  atc_code,
  policy_imdq60,
  policy_biosim,
  section_19a_expiry,
  authority_method,
  copay_general_aud,
  copay_concessional_aud,
  first_listed_date,
  pack_size,
  pricing_quantity,
  maximum_prescribable_pack,
  retail_price_aud,
  retail_estimation_method,
  chemist_price_aud,
  chemist_url,
  originator_brand_name,
  originator_sponsor,
  top_generics,
  competitor_count,
  market_tier,
  situation_summary,
  confidence,
  ingredients_split,
  similar_drug_used,
  hospital_only_flag,
  ai_deep_research_raw,
  schedule_code,
  crawler_source_urls,
  error_type,
  warnings
FROM public.au_products;

COMMENT ON VIEW public.v_au_products IS
  '대시보드용: 크롤 시각·메타 시각 컬럼 우선. 원본은 public.au_products';

GRANT SELECT ON public.v_au_crawl_log TO anon, authenticated, service_role;
GRANT SELECT ON public.v_au_products TO anon, authenticated, service_role;
