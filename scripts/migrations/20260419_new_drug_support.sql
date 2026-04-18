-- 마이그레이션: 신약 분석 지원 + AEMP 출처 추적
-- 작성일: 2026-04-19
-- 의존성: 기존 au_products 테이블 존재, healthylife_price_aud / healthylife_url 컬럼 존재
-- 영향범위: au_products 6개 컬럼 추가, au_crawl_jobs 신규 테이블, v_au_products / v_au_crawl_jobs VIEW 재생성

-- ═══════════════════════════════════════════════════════════════════════════
-- Task 1 — au_products 컬럼 6개 추가
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.au_products
  ADD COLUMN IF NOT EXISTS is_new_drug BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.au_products
  ADD COLUMN IF NOT EXISTS aemp_source VARCHAR(30);

ALTER TABLE public.au_products
  ADD COLUMN IF NOT EXISTS pricing_case_source VARCHAR(20);

ALTER TABLE public.au_products
  ADD COLUMN IF NOT EXISTS similar_proxy_inn TEXT;

ALTER TABLE public.au_products
  ADD COLUMN IF NOT EXISTS ai_inferred_similar_inns JSONB;

ALTER TABLE public.au_products
  ADD COLUMN IF NOT EXISTS user_uploaded_pdf_extracted JSONB;

-- ═══════════════════════════════════════════════════════════════════════════
-- Task 2 — au_crawl_jobs 테이블 + 트리거 + 인덱스
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.au_crawl_jobs (
  job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_code TEXT,
  job_type VARCHAR(30) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  input_payload JSONB,
  result JSONB,
  needs_price_upload BOOLEAN NOT NULL DEFAULT FALSE,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.au_crawl_jobs_touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_au_crawl_jobs_updated_at ON public.au_crawl_jobs;
CREATE TRIGGER trg_au_crawl_jobs_updated_at
  BEFORE UPDATE ON public.au_crawl_jobs
  FOR EACH ROW EXECUTE FUNCTION public.au_crawl_jobs_touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_au_crawl_jobs_status ON public.au_crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_au_crawl_jobs_product_code ON public.au_crawl_jobs(product_code);
CREATE INDEX IF NOT EXISTS idx_au_crawl_jobs_created_at ON public.au_crawl_jobs(created_at DESC);

-- ═══════════════════════════════════════════════════════════════════════════
-- Task 3 — v_au_products VIEW 재생성 (views_dashboard.sql 정렬 + 신규 6컬럼)
-- ═══════════════════════════════════════════════════════════════════════════

DROP VIEW IF EXISTS public.v_au_products CASCADE;

CREATE VIEW public.v_au_products AS
SELECT
  last_crawled_at,
  created_at,
  updated_at,
  id,
  product_code,
  is_new_drug,
  product_name_ko,
  inn_normalized,
  strength,
  dosage_form,
  case_code,
  pricing_case_source,
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
  aemp_source,
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
  healthylife_price_aud,
  healthylife_url,
  originator_brand_name,
  originator_sponsor,
  top_generics,
  competitor_count,
  market_tier,
  situation_summary,
  confidence,
  ingredients_split,
  similar_drug_used,
  similar_proxy_inn,
  ai_inferred_similar_inns,
  user_uploaded_pdf_extracted,
  hospital_only_flag,
  ai_deep_research_raw,
  availability_status,
  match_type,
  schedule_code,
  crawler_source_urls,
  error_type,
  warnings,
  block2_market,
  block2_regulatory,
  block2_trade,
  block2_procurement,
  block2_channel,
  block3_channel,
  block3_pricing,
  block3_partners,
  block3_risks,
  block4_regulatory,
  perplexity_refs,
  llm_model,
  llm_generated_at
FROM public.au_products;

COMMENT ON VIEW public.v_au_products IS
  '호주 8품목 + 신약 분석용 통합 뷰. aemp_source/pricing_case_source로 데이터 출처 추적 가능';

GRANT SELECT ON public.v_au_products TO anon, authenticated, service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- Task 4 — v_au_crawl_jobs VIEW (대시보드용; 뷰 정의에는 ORDER BY 미사용 — 조회 시 정렬)
-- ═══════════════════════════════════════════════════════════════════════════

DROP VIEW IF EXISTS public.v_au_crawl_jobs;

CREATE VIEW public.v_au_crawl_jobs AS
SELECT
  job_id,
  product_code,
  job_type,
  status,
  needs_price_upload,
  CASE
    WHEN status = 'done' THEN '완료'
    WHEN status = 'running' THEN '크롤링 중'
    WHEN status = 'queued' THEN '대기'
    WHEN status = 'failed' THEN '실패'
    WHEN status = 'cancelled' THEN '취소'
    ELSE status
  END AS status_ko,
  input_payload,
  result,
  error_message,
  created_at,
  updated_at,
  EXTRACT(EPOCH FROM (updated_at - created_at))::INT AS duration_sec
FROM public.au_crawl_jobs;

COMMENT ON VIEW public.v_au_crawl_jobs IS
  '크롤링 잡 대시보드. status_ko로 한글 상태 표시';

GRANT SELECT ON public.v_au_crawl_jobs TO anon, authenticated, service_role;
