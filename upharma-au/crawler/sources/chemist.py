# Chemist Warehouse 검색 페이지에서 첫 상품 가격을 정적 HTML로 파싱한다.

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from selectolax.parser import HTMLParser

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BASE = "https://www.chemistwarehouse.com.au"


def _parse_dollar_aud(fragment: str) -> float | None:
    m = re.search(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", fragment.replace("\u00a0", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def fetch_chemist_price(search_term: str) -> dict[str, Any] | None:
    """검색어로 첫 결과 가격·출처 URL을 반환한다. 실패 시 None."""
    try:
        q = (search_term or "").strip()
        if not q:
            return None
        search_url = f"{_BASE}/search?searchstr={quote(q)}"
        r = httpx.get(
            search_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return None
        tree = HTMLParser(r.text)
        retail: float | None = None

        n = tree.css_first("span.product-price")
        if n is not None:
            retail = _parse_dollar_aud(n.text())

        if retail is None:
            n2 = tree.css_first("div[data-price]")
            if n2 is not None:
                attr = n2.attributes.get("data-price")
                if attr:
                    try:
                        retail = float(str(attr).replace(",", ""))
                    except ValueError:
                        retail = _parse_dollar_aud(str(attr))
                if retail is None:
                    retail = _parse_dollar_aud(n2.text())

        if retail is None:
            blob = tree.text(separator=" ")
            for m in re.finditer(r"\$\d+\.\d{2}", blob):
                retail = _parse_dollar_aud(m.group())
                if retail is not None:
                    break

        if retail is None:
            return None

        product_url = search_url
        for a in tree.css("a"):
            href = (a.attributes.get("href") or "").strip()
            if "/buy/" in href.lower():
                if href.startswith("http"):
                    product_url = href.split("?")[0]
                else:
                    product_url = urljoin(_BASE, href.split("?")[0])
                break

        return {
            "retail_price_aud": retail,
            "price_unit": "per pack",
            "price_source_name": "Chemist Warehouse",
            "price_source_url": product_url,
        }
    except Exception:
        return None


def build_sites(
    pbs_url: str,
    tga_url: str,
    chemist_url: str,
    austender_url: str,
    pubmed_url: str | None = None,
) -> dict[str, Any]:
    """출처 URL들을 sites JSON 구조로 묶는다. tga_url 은 v7 반환 스키마에 항목이 없어 현재 미사용(시그니처만 유지)."""
    out: dict[str, Any] = {
        "public_procurement": [
            {"name": "PBS", "url": pbs_url},
            {"name": "AusTender", "url": austender_url},
        ],
        "private_price": [{"name": "Chemist Warehouse", "url": chemist_url}],
        "paper": [],
    }
    if pubmed_url:
        out["paper"] = [{"name": "PubMed", "url": pubmed_url}]
    return out
