/** Supabase `australia` 행 / build_product_summary 결과에 대응 */

export interface SiteLink {
  name: string;
  url: string;
}

export interface SitesPayload {
  public_procurement?: SiteLink[];
  private_price?: SiteLink[];
  paper?: SiteLink[];
}

export interface ProductSummary {
  id?: string;
  product_id: string;
  product_name_ko?: string | null;
  inn_normalized?: string | null;
  strength?: string | null;
  hs_code_6?: string | null;
  export_viable?: string | null;
  reason_code?: string | null;
  pricing_case?: string | null;
  pbs_listed?: boolean | null;
  pbs_item_code?: string | null;
  pbs_price_aud?: number | null;
  retail_price_aud?: number | null;
  artg_number?: string | null;
  tga_sponsor?: string | null;
  confidence?: number | null;
  crawled_at?: string | null;
  evidence_text?: string | null;
  evidence_text_ko?: string | null;
  sites?: SitesPayload | Record<string, unknown> | null;
}

export interface CatalogProduct {
  product_id: string;
  product_name_ko: string;
  inn_normalized: string;
  pricing_case: string;
  strength?: string;
  dosage_form?: string;
  hs_code_6?: string;
}
