# AusTender 계약 검색 결과에서 첫 행 단서를 파싱한다.

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _empty_row(source_url: str) -> dict[str, Any]:
    return {
        "contract_value_aud": None,
        "supplier_name": None,
        "contract_date": None,
        "austender_source_url": source_url,
    }


def fetch_austender(search_term: str) -> dict[str, Any] | None:
    """첫 계약 행에서 금액·공급자·일자를 추출한다. 없으면 None 필드만 채운 dict."""
    q = (search_term or "").strip()
    search_url = f"https://www.austender.gov.au/contract/search?keyword={quote(q)}"
    empty = _empty_row(search_url)
    try:
        r = httpx.get(
            search_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return empty

        tree = HTMLParser(r.text)
        trs = tree.css("table tbody tr")
        if not trs:
            trs = tree.css("tr")

        header_like = frozenset(
            {"supplier", "contract", "value", "date", "keyword", "title", "agency"}
        )

        for tr in trs:
            tds = tr.css("td")
            if len(tds) < 2:
                continue
            texts = [td.text(strip=True) for td in tds]
            joined = " ".join(texts).lower()
            if not joined:
                continue
            first = texts[0].lower() if texts else ""
            if first in header_like or any(
                h in joined for h in ("supplier name", "contract id", "publish date")
            ):
                continue

            val: float | None = None
            for cell in texts:
                m = re.search(
                    r"(?:\$|AUD\s*)?\s*([\d,]+(?:\.\d+)?)\s*(?:AUD)?",
                    cell,
                    flags=re.IGNORECASE,
                )
                if m:
                    try:
                        candidate = float(m.group(1).replace(",", ""))
                        if candidate > 0:
                            val = candidate
                            break
                    except ValueError:
                        pass

            supplier: str | None = texts[1] if len(texts) > 1 else None

            date_s: str | None = None
            for cell in texts:
                dm = re.search(
                    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
                    cell,
                )
                if dm:
                    date_s = dm.group(1)
                    break

            sup_clean = (supplier or "").strip()
            if val is None and not sup_clean and date_s is None:
                continue

            return {
                "contract_value_aud": val,
                "supplier_name": supplier,
                "contract_date": date_s,
                "austender_source_url": search_url,
            }

        return empty
    except Exception:
        return empty
