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


def _raw_evidence_text(
    pbs: dict[str, Any],
    tga: dict[str, Any],
    austender: dict[str, Any],
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
    sup = austender.get("supplier_name")
    if sup:
        parts.append(f"Procurement supplier: {sup}")
    return "\n".join(parts)


def build_product_summary(
    product: dict[str, Any],
    pbs: dict[str, Any] | None,
    tga: dict[str, Any] | None,
    chemist: dict[str, Any] | None,
    austender: dict[str, Any] | None,
) -> dict[str, Any]:
    """각 소스 dict 를 australia 스키마에 맞는 단일 dict 로 병합한다."""
    data_source_count = sum(
        1 for x in (pbs, tga, chemist, austender) if x is not None
    )
    pbs = pbs or {}
    tga = tga or {}
    chemist = chemist or {}
    austender = austender or {}

    viable_result = determine_export_viable(tga)
    if pbs.get("pbs_listed") == True:
        viable_result = {"export_viable": "viable", "reason_code": "PBS_REGISTERED"}

    inn = str(product.get("inn_normalized") or "")
    pricing_case = str(product.get("pricing_case") or "ESTIMATE")
    raw_text = _raw_evidence_text(pbs, tga, austender)
    evidence = build_evidence_text(pricing_case, raw_text, inn)

    retail_aud: float | None = chemist.get("retail_price_aud")
    if retail_aud is None:
        pbs_price = pbs.get("pbs_price_aud")
        retail_aud = float(pbs_price) if isinstance(pbs_price, (int, float)) else None

    price_url = (chemist.get("price_source_url") or "") or (pbs.get("pbs_source_url") or "")

    price_name = chemist.get("price_source_name") or "PBS"

    assembled: dict[str, Any] = {
        "artg_number": tga.get("artg_number"),
        "tga_schedule": tga.get("tga_schedule"),
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
        chemist.get("price_source_url", "") or "",
        austender.get("austender_source_url", "") or "",
        pubmed_url=None,
    )

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
        "tga_schedule": tga.get("tga_schedule"),
        "tga_sponsor": tga.get("tga_sponsor"),
        "artg_source_url": tga.get("artg_source_url", ""),
        "pbs_listed": pbs.get("pbs_listed", False),
        "pbs_item_code": pbs.get("pbs_item_code"),
        "pbs_price_aud": pbs.get("pbs_price_aud"),
        "pbs_source_url": pbs.get("pbs_source_url", ""),
        "retail_price_aud": retail_aud,
        "price_source_name": price_name,
        "price_source_url": price_url,
        "price_unit": chemist.get("price_unit", "per pack"),
        "pricing_case": product["pricing_case"],
        "export_viable": viable_result.get("export_viable"),
        "reason_code": viable_result.get("reason_code"),
        "evidence_url": tga.get("artg_source_url", ""),
        "evidence_text": evidence.get("evidence_text", ""),
        "evidence_text_ko": evidence.get("evidence_text_ko", ""),
        "sites": sites,
        "completeness_ratio": completeness_ratio,
        "data_source_count": data_source_count,
        "error_type": None,
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
    from sources.austender import fetch_austender
    from sources.chemist import fetch_chemist_price
    from sources.pbs import fetch_pbs_by_ingredient, fetch_pbs_multi
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

    pbs_terms = product.get("pbs_search_terms") or []
    retail_query = str(pbs_terms[0] if pbs_terms else product.get("inn_normalized") or "")

    chemist = fetch_chemist_price(retail_query)
    austender = fetch_austender(retail_query)

    summary = build_product_summary(
        product,
        pbs,
        tga,
        chemist,
        austender,
    )
    ok = upsert_product(summary)
    print(f"[완료] product_id={product_filter} upsert={'성공' if ok else '실패'}")
    sys.exit(0 if ok else 1)


def run() -> None:
    """CLI 진입점과 동일."""
    main()


if __name__ == "__main__":
    main()
