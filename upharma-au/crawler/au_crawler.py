# 호주 의약품 크롤링 파이프라인 — 품목별 수집 결과를 product_summary 로 조립한다.

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sources.chemist import build_sites
from sources.tga import determine_export_viable
from utils.evidence import build_evidence_text
from utils.scoring import AU_REQUIRED_FIELDS, completeness_score

_CRAWLER_DIR = Path(__file__).resolve().parent


def _chemist_retail_trustworthy(
    chemist: dict[str, Any],
    pbs_price: object,
) -> tuple[float | None, bool]:
    """Chemist 첫 검색 가격이 소매 정상가로 볼 수 있을 때만 True(저가·오매칭·부분파싱 배제)."""
    raw = chemist.get("retail_price_aud")
    if raw is None or not isinstance(raw, (int, float)):
        return None, False
    r = float(raw)
    if r <= 0:
        return None, False
    if r < 5.0:
        return None, False
    if isinstance(pbs_price, (int, float)) and float(pbs_price) > 0:
        if r < float(pbs_price) * 0.15:
            return None, False
    return r, True


def _tga_schedule_s2348_only(raw: object) -> str | None:
    """tga_schedule 컬럼에는 S2/S3/S4/S8만 저장(RE 등 라이선스 코드 제외)."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if s in ("S2", "S3", "S4", "S8") else None


def _raw_evidence_text(
    pbs: dict[str, Any],
    tga: dict[str, Any],
    nsw: dict[str, Any],
) -> str:
    """PBS·TGA·조달 텍스트를 이어 붙여 근거 원문으로 쓴다."""
    parts: list[str] = []
    rt = pbs.get("restriction_text")
    if isinstance(rt, str) and rt.strip():
        parts.append(rt.strip())
    st = tga.get("tga_schedule")
    if st:
        parts.append(f"Schedule: {st}")
    sp = tga.get("tga_sponsor")
    if sp:
        parts.append(f"Sponsor: {sp}")
    ast = tga.get("artg_status")
    if ast:
        parts.append(f"ARTG status: {ast}")
    sup = nsw.get("supplier_name")
    if sup:
        parts.append(f"Procurement supplier: {sup}")
    return "\n".join(parts)


def build_product_summary(
    product: dict[str, Any],
    pbs: dict[str, Any] | None,
    tga: dict[str, Any] | None,
    chemist: dict[str, Any] | None,
    nsw: dict[str, Any] | None,
) -> dict[str, Any]:
    """각 소스 dict 를 australia 스키마에 맞는 단일 dict 로 병합한다."""
    data_source_count = sum(
        1 for x in (pbs, tga, chemist, nsw) if x is not None
    )
    pbs = pbs or {}
    tga = tga or {}
    chemist = chemist or {}
    nsw = nsw or {}

    tga_sched = _tga_schedule_s2348_only(tga.get("tga_schedule"))
    tga_norm = {**tga, "tga_schedule": tga_sched}

    viable_result = determine_export_viable(tga_norm)
    if pbs.get("pbs_listed") == True:
        viable_result = {"export_viable": "viable", "reason_code": "PBS_REGISTERED"}

    inn = str(product.get("inn_normalized") or "")
    pricing_case = str(product.get("pricing_case") or "ESTIMATE")
    raw_text = _raw_evidence_text(pbs, tga_norm, nsw)
    evidence = build_evidence_text(pricing_case, raw_text, inn)

    pbs_price = pbs.get("pbs_price_aud")
    cr_trusted, chemist_ok = _chemist_retail_trustworthy(chemist, pbs_price)
    retail_aud: float | None
    price_url: str
    price_name: str
    if chemist_ok and cr_trusted is not None:
        retail_aud = cr_trusted
        price_url = (chemist.get("price_source_url") or "") or (
            pbs.get("pbs_source_url") or ""
        )
        price_name = chemist.get("price_source_name") or "Chemist Warehouse"
    elif isinstance(pbs_price, (int, float)) and float(pbs_price) > 0:
        retail_aud = float(pbs_price)
        price_url = pbs.get("pbs_source_url") or ""
        price_name = "PBS"
    else:
        retail_aud = None
        price_url = (chemist.get("price_source_url") or "") or (
            pbs.get("pbs_source_url") or ""
        )
        price_name = "PBS"

    chemist_url_for_sites = (
        (chemist.get("price_source_url") or "") if chemist_ok else ""
    )

    assembled: dict[str, Any] = {
        "artg_number": tga.get("artg_number"),
        "tga_schedule": tga_sched,
        "pbs_item_code": pbs.get("pbs_item_code"),
        "retail_price_aud": retail_aud,
        "price_source_url": price_url,
        "export_viable": viable_result.get("export_viable"),
        "dosage_form": product.get("dosage_form"),
    }

    completeness_ratio = (
        len([f for f in AU_REQUIRED_FIELDS if assembled.get(f)])
        / len(AU_REQUIRED_FIELDS)
    )

    sites = build_sites(
        pbs.get("pbs_source_url", "") or "",
        tga.get("artg_source_url", "") or "",
        chemist_url_for_sites,
        nsw.get("nsw_source_url", "") or "",
        pubmed_url=None,
    )

    error_type: str | None = None
    if (
        pbs.get("pbs_item_code")
        and pbs.get("pbs_listed") is True
        and pbs.get("pbs_brand_name") is None
        and pbs.get("pbs_innovator") is None
        and pbs.get("pbs_brands") is None
    ):
        error_type = "PBS_WEB_ENRICHMENT_INCOMPLETE"

    return {
        "id": str(uuid.uuid4()),
        "product_id": product["product_id"],
        "market_segment": product["market_segment"],
        "fob_estimated_usd": None,
        "confidence": completeness_score(assembled),
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "product_name_ko": product["product_name_ko"],
        "inn_normalized": product["inn_normalized"],
        "hs_code_6": product["hs_code_6"],
        "dosage_form": product["dosage_form"],
        "strength": product["strength"],
        "artg_number": tga.get("artg_number"),
        "artg_status": tga.get("artg_status"),
        "tga_schedule": tga_sched,
        "tga_licence_category": tga.get("tga_licence_category"),
        "tga_licence_status": tga.get("tga_licence_status"),
        "tga_sponsor": tga.get("tga_sponsor"),
        "artg_source_url": tga.get("artg_source_url", ""),
        "pbs_listed": pbs.get("pbs_listed", False),
        "pbs_item_code": pbs.get("pbs_item_code"),
        "pbs_price_aud": pbs.get("pbs_price_aud"),
        "pbs_dpmq": pbs.get("pbs_dpmq"),
        "pbs_patient_charge": pbs.get("pbs_patient_charge"),
        "pbs_determined_price": pbs.get("pbs_determined_price"),
        "pbs_pack_size": pbs.get("pbs_pack_size"),
        "pbs_pricing_quantity": pbs.get("pbs_pricing_quantity"),
        "pbs_benefit_type": pbs.get("pbs_benefit_type"),
        "pbs_program_code": pbs.get("pbs_program_code"),
        "pbs_brand_name": pbs.get("pbs_brand_name"),
        "pbs_innovator": pbs.get("pbs_innovator"),
        "pbs_first_listed_date": pbs.get("pbs_first_listed_date"),
        "pbs_repeats": pbs.get("pbs_repeats"),
        "pbs_formulary": pbs.get("pbs_formulary"),
        "pbs_restriction": pbs.get("pbs_restriction"),
        "pbs_total_brands": pbs.get("pbs_total_brands"),
        "pbs_brands": pbs.get("pbs_brands"),
        "pbs_source_url": pbs.get("pbs_source_url", ""),
        "pbs_web_source_url": pbs.get("pbs_web_source_url"),
        "nsw_contract_value_aud": nsw.get("contract_value_aud"),
        "nsw_supplier_name": nsw.get("supplier_name"),
        "nsw_contract_date": nsw.get("contract_date"),
        "nsw_source_url": nsw.get("nsw_source_url"),
        "retail_price_aud": retail_aud,
        "price_source_name": price_name,
        "price_source_url": price_url,
        "price_unit": (
            chemist.get("price_unit", "per pack")
            if chemist_ok
            else "per pack"
        ),
        "pricing_case": product["pricing_case"],
        "export_viable": viable_result.get("export_viable"),
        "reason_code": viable_result.get("reason_code"),
        "evidence_url": tga.get("artg_source_url", ""),
        "evidence_text": evidence.get("evidence_text", ""),
        "evidence_text_ko": evidence.get("evidence_text_ko", ""),
        "sites": sites,
        "completeness_ratio": completeness_ratio,
        "data_source_count": data_source_count,
        "error_type": error_type,
        # LLM/Perplexity 메타 — 1공정에서는 None, 이후 단계에서 채움
        "block2_market": None,
        "block2_regulatory": None,
        "block2_trade": None,
        "block2_procurement": None,
        "block2_channel": None,
        "block3_channel": None,
        "block3_pricing": None,
        "block3_partners": None,
        "block3_risks": None,
        "perplexity_refs": None,
        "llm_model": None,
        "llm_generated_at": None,
    }


def _load_products() -> list[dict[str, Any]]:
    path = _CRAWLER_DIR / "au_products.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("products", []))


def _merge_pbs_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """PBS 다행(복합 성분 등)을 요약용 단일 dict 로 합친다."""
    if not rows:
        return {}
    if len(rows) == 1:
        return dict(rows[0])
    merged: dict[str, Any] = dict(rows[0])
    merged["pbs_listed"] = any(r.get("pbs_listed") for r in rows)
    prices: list[float] = []
    for r in rows:
        v = r.get("pbs_price_aud")
        if isinstance(v, (int, float)):
            prices.append(float(v))
    if prices:
        merged["pbs_price_aud"] = sum(prices)
    codes: list[str] = []
    for r in rows:
        c = r.get("pbs_item_code")
        if c is not None:
            codes.append(str(c))
    if codes:
        merged["pbs_item_code"] = "+".join(codes)
    restrs = [r.get("restriction_text") for r in rows if r.get("restriction_text")]
    if restrs:
        merged["restriction_text"] = " | ".join(str(x) for x in restrs)
    return merged


def main() -> None:
    """PRODUCT_FILTER 로 지정한 1개 품목만 수집·요약·Supabase upsert 한다."""
    product_filter = (os.environ.get("PRODUCT_FILTER") or "").strip()
    if not product_filter:
        print(
            "[오류] PRODUCT_FILTER 가 비어 있습니다. product_id 1개를 지정해야 합니다(전체 실행 없음).",
            file=sys.stderr,
        )
        sys.exit(1)

    products = _load_products()
    product = next((p for p in products if p.get("product_id") == product_filter), None)
    if product is None:
        print(
            f"[오류] product_id={product_filter!r} 품목을 au_products.json 에서 찾을 수 없습니다.",
            file=sys.stderr,
        )
        sys.exit(1)

    from db.supabase_insert import upsert_product
    from sources.buynsw import fetch_buynsw
    from sources.chemist import fetch_chemist_price
    from sources.pbs import fetch_pbs_by_ingredient, fetch_pbs_multi, fetch_pbs_web
    from sources.tga import fetch_tga_artg

    tga_terms = product.get("tga_search_terms") or []
    tga_query = str(tga_terms[0] if tga_terms else product.get("inn_normalized") or "")

    tga = fetch_tga_artg(tga_query)
    determine_export_viable(tga)

    components = [str(c) for c in (product.get("inn_components") or []) if c]
    if not components:
        components = [str(product.get("inn_normalized") or "")]
    if len(components) > 1:
        pbs_rows = fetch_pbs_multi(components)
    else:
        pbs_rows = fetch_pbs_by_ingredient(components[0])
    pbs = _merge_pbs_rows(pbs_rows)
    if pbs.get("pbs_item_code"):
        codes = [c.strip() for c in str(pbs["pbs_item_code"]).split("+") if c.strip()]
        api_inno = pbs.get("pbs_innovator")
        api_bn = pbs.get("pbs_brand_name")
        api_brs = pbs.get("pbs_brands")
        agg_brands: list[dict[str, Any]] = []
        for c in codes:
            web = fetch_pbs_web(c)
            if web.get("pbs_dpmq") is not None:
                pbs["pbs_dpmq"] = web.get("pbs_dpmq")
            if web.get("pbs_patient_charge") is not None:
                pbs["pbs_patient_charge"] = web.get("pbs_patient_charge")
            if web.get("pbs_web_source_url"):
                pbs["pbs_web_source_url"] = web.get("pbs_web_source_url")
            if web.get("pbs_brand_name") and not pbs.get("pbs_brand_name"):
                pbs["pbs_brand_name"] = web.get("pbs_brand_name")
            if web.get("pbs_brands"):
                agg_brands.extend(web["pbs_brands"])
        if agg_brands:
            pbs["pbs_brands"] = agg_brands
        elif api_brs is not None:
            pbs["pbs_brands"] = api_brs
        if pbs.get("pbs_brand_name") is None:
            pbs["pbs_brand_name"] = api_bn
        if pbs.get("pbs_innovator") is None:
            pbs["pbs_innovator"] = api_inno

    pbs_terms = product.get("pbs_search_terms") or []
    retail_query = str(pbs_terms[0] if pbs_terms else product.get("inn_normalized") or "")

    chemist = fetch_chemist_price(retail_query)
    nsw = fetch_buynsw(retail_query)

    summary = build_product_summary(
        product,
        pbs,
        tga,
        chemist,
        nsw,
    )
    ok = upsert_product(summary)
    print(f"[완료] product_id={product_filter} upsert={'성공' if ok else '실패'}")
    sys.exit(0 if ok else 1)


def run() -> None:
    """CLI 진입점과 동일."""
    main()


if __name__ == "__main__":
    main()
