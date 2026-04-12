# TGA ARTG: 검색 → 첫 링크 → 상세 1회 GET 후 Schedule·스폰서 파싱.

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from selectolax.parser import HTMLParser

_BASE = "https://www.tga.gov.au"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _abs_url(href: str) -> str | None:
    if not href or href.startswith("#"):
        return None
    return urljoin(_BASE + "/", href.lstrip("/"))


def _first_artg_href(html: str) -> str | None:
    import re

    matches = re.findall(r"/resources/artg/\d+", html)
    if matches:
        return _abs_url(matches[0])
    return None


def _artg_number_from_href(href: str) -> str | None:
    m = re.search(r"/resources/artg/(\d+)", href)
    return m.group(1) if m else None


def _text_blob(tree: HTMLParser) -> str:
    body = tree.body
    if body:
        return body.text(separator=" ", strip=True)
    return tree.text(separator=" ", strip=True)


def _parse_schedule_from_detail(text: str) -> str | None:
    """상세 본문에서 Schedule 및 S2/S3/S4/S8 등을 추출한다."""
    t = text
    # Schedule 근처
    m = re.search(
        r"Schedule[^\n]{0,120}?\b(S[2348])\b",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).upper()
    # 본문에 나오는 스케줄 코드 (우선순위: S8 … S2)
    for code in ("S8", "S4", "S3", "S2"):
        if re.search(rf"\b{code}\b", t, flags=re.IGNORECASE):
            return code
    return None


def _parse_sponsor_from_detail(text: str) -> str | None:
    m = re.search(
        r"Sponsor\s*[:\-]?\s*([^\n\r]{2,160}?)(?:\n|ARTG|Schedule|Dosage|Form|Medicine|$)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" -:\t")
    return None


def fetch_tga_artg(ingredient: str) -> dict[str, Any]:
    """검색 후 상세 1회 조회. 항상 dict 반환."""
    q = (ingredient or "").strip()
    search_url = f"{_BASE}/resources/artg?keywords={quote(q)}" if q else f"{_BASE}/resources/artg"

    not_reg: dict[str, Any] = {
        "artg_number": None,
        "artg_status": "not_registered",
        "tga_schedule": None,
        "tga_sponsor": None,
        "artg_source_url": search_url,
    }

    try:
        r0 = httpx.get(search_url, headers=_headers(), timeout=30, follow_redirects=True)
        if r0.status_code != 200:
            return not_reg

        detail_url = _first_artg_href(r0.text)
        if not detail_url:
            return not_reg

        artg_number = _artg_number_from_href(detail_url)
        sched: str | None = None
        sponsor: str | None = None

        try:
            r1 = httpx.get(detail_url, headers=_headers(), timeout=30, follow_redirects=True)
            if r1.status_code == 200:
                tree = HTMLParser(r1.text)
                blob = _text_blob(tree)
                sched = _parse_schedule_from_detail(blob)
                sponsor = _parse_sponsor_from_detail(blob)
        except Exception as e:
            print(f"[TGA DETAIL] {e}")

        return {
            "artg_number": artg_number,
            "artg_status": "registered",
            "tga_schedule": sched,
            "tga_sponsor": sponsor,
            "artg_source_url": detail_url,
        }
    except Exception as e:
        print(f"[TGA ERROR] {e}")
        return not_reg


def determine_export_viable(artg_result: dict[str, Any]) -> dict[str, str]:
    sched = (artg_result.get("tga_schedule") or "").upper()
    status = artg_result.get("artg_status")

    if sched == "S8" or "S8" in sched:
        return {"export_viable": "not_viable", "reason_code": "SCHEDULE_8"}

    if status == "registered":
        return {"export_viable": "viable", "reason_code": "ARTG_REGISTERED"}

    return {"export_viable": "not_viable", "reason_code": "TGA_NOT_APPROVED"}
