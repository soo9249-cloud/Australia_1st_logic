"""6개 소스 병렬 수집 오케스트레이터.

asyncio + run_in_executor 로 blocking I/O (httpx·Supabase 등) 병렬 실행.
각 소스 실패해도 `return_exceptions=True` 로 다른 소스는 계속 수집.
반환: 6 배열이 담긴 dict (실패 소스는 빈 배열).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .sources.db_sponsors import (
    fetch_pbs_sponsors_for_product,
    fetch_sponsors_by_inn,
    fetch_tga_sponsors_for_product,
)
from .sources.gbma_members import fetch_gbma_members
from .sources.gpce_exhibitors import fetch_gpce_exhibitors
from .sources.ma_members import fetch_ma_members

_PRODUCTS_PATH = Path(__file__).resolve().parent.parent / "crawler" / "au_products.json"


def _get_product(product_code: str) -> dict[str, Any]:
    """au_products.json 에서 product_id 매칭되는 엔트리 반환."""
    products = json.loads(_PRODUCTS_PATH.read_text(encoding="utf-8"))["products"]
    for p in products:
        if p.get("product_id") == product_code:
            return p
    raise ValueError(f"product_code not found in au_products.json: {product_code}")


async def collect_all_sources(product_code: str) -> dict[str, list[dict[str, Any]]]:
    """6개 소스 병렬 수집.

    Returns:
      {
        "tga_sponsors":       [{name, source="tga", artg_count}, ...],
        "pbs_sponsors":       [{name, source="pbs", pbs_listed=True}, ...],
        "ma_members":         [{name, source="ma", is_ma_member=True, website}, ...],
        "gbma_members":       [{name, source="gbma", is_gbma_member=True, website}, ...],
        "gpce_exhibitors":    [{name, source="gpce", website, email, ...}, ...],
        "inn_match_sponsors": [{name, source="tga_inn_match", artg_id, matched_inn}, ...],
      }
    """
    product = _get_product(product_code)
    inns = product.get("inn_components", []) or []
    similar = product.get("similar_inns", []) or []

    loop = asyncio.get_event_loop()
    tga_task  = loop.run_in_executor(None, fetch_tga_sponsors_for_product, product_code)
    pbs_task  = loop.run_in_executor(None, fetch_pbs_sponsors_for_product, product_code)
    ma_task   = loop.run_in_executor(None, fetch_ma_members)
    gbma_task = loop.run_in_executor(None, fetch_gbma_members)
    gpce_task = loop.run_in_executor(None, fetch_gpce_exhibitors)
    inn_task  = loop.run_in_executor(None, fetch_sponsors_by_inn, inns, similar)

    tga, pbs, ma, gbma, gpce, inn = await asyncio.gather(
        tga_task, pbs_task, ma_task, gbma_task, gpce_task, inn_task,
        return_exceptions=True,
    )

    def _safe(x: Any) -> list[dict[str, Any]]:
        if isinstance(x, Exception):
            print(f"[pipeline_collect] source 실패: {x!r}", flush=True)
            return []
        return x or []

    return {
        "tga_sponsors":        _safe(tga),
        "pbs_sponsors":        _safe(pbs),
        "ma_members":          _safe(ma),
        "gbma_members":        _safe(gbma),
        "gpce_exhibitors":     _safe(gpce),
        "inn_match_sponsors":  _safe(inn),
    }
