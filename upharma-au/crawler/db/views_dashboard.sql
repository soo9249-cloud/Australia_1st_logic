-- Supabase SQL 에디터에서 한 번 실행: Table Editor 에서 테이블 대신 VIEW 이름으로 열면
-- 크롤/생성 시각 컬럼이 맨 앞에 보입니다. (원본 테이블·크롤러 코드는 변경 없음)
--
-- 실행: Dashboard → SQL → 아래 전체 붙여넣기 → Run

-- ── au_crawl_log: started_at / finished_at / created_at 을 앞으로 ─────────────
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

-- ── au_products: last_crawled_at / created_at / updated_at 을 앞으로 ─────────
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

-- API/에디터에서 읽기 허용 (프로젝트 정책에 맞게 조정 가능)
GRANT SELECT ON public.v_au_crawl_log TO anon, authenticated, service_role;
GRANT SELECT ON public.v_au_products TO anon, authenticated, service_role;
