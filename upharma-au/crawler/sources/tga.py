# TGA ARTG: Jina Reader로 검색·상세 마크다운을 받아 ARTG·라이선스·스폰서를 파싱한다.

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

_JINA = "https://r.jina.ai/"
_TGA_BASE = "https://www.tga.gov.au"
_TIMEOUT = 20.0


def _jina_wrap(https_url: str) -> str:
    return f"{_JINA}{https_url}"


def _parse_schedule_s2348(full_text: str) -> str | None:
    """상세 본문 전체에서 \\bS(?:2|3|4|8)\\b 첫 매칭만 S2~S8 형태로 반환. 없으면 None."""
    m = re.search(r"\bS(?:2|3|4|8)\b", full_text, flags=re.IGNORECASE)
    return m.group(0).upper() if m else None


def fetch_tga_detail(artg_id: str) -> dict[str, Any]:
    """ARTG 상세 페이지 마크다운에서 스폰서·라이선스 정보를 추출한다. 실패 시 None 필드."""
    empty: dict[str, Any] = {
        "tga_sponsor": None,
        "tga_licence_category": None,
        "tga_licence_status": None,
        "tga_schedule": None,
    }
    aid = (artg_id or "").strip()
    if not aid:
        return empty

    url = _jina_wrap(f"{_TGA_BASE}/resources/artg/{aid}")
    try:
        r = httpx.get(url, timeout=_TIMEOUT)
        if r.status_code != 200:
            return empty
        text = r.text or ""
    except Exception:
        return empty

    sponsor: str | None = None
    m = re.search(r"Sponsor\s*\n+\s*\[([^\]]+)\]\(", text, flags=re.IGNORECASE)
    if m:
        sponsor = m.group(1).strip()

    cat: str | None = None
    m = re.search(r"Licence category\s*\n+\s*([^\n]+)", text, flags=re.IGNORECASE)
    if m:
        cat = m.group(1).strip()

    stat: str | None = None
    m = re.search(r"Licence status\s*\n+\s*([^\n]+)", text, flags=re.IGNORECASE)
    if m:
        stat = m.group(1).strip()

    sched = _parse_schedule_s2348(text)

    return {
        "tga_sponsor": sponsor,
        "tga_licence_category": cat,
        "tga_licence_status": stat,
        "tga_schedule": sched,
    }


def _parse_first_artg_id(markdown: str) -> str | None:
    """검색 결과 마크다운에서 첫 번째 ARTG ID(숫자)를 추출한다."""
    m = re.search(r"###\s+\[[^\]]*\((\d+)\)\]\(", markdown)
    return m.group(1) if m else None


def _parse_sponsor_filter_first(markdown: str) -> str | None:
    """Sponsor 필터 블록에서 첫 [x] 스폰서명을 추출한다."""
    block = re.search(
        r"Sponsor\s*\n+(.*?)(?=## Published date)",
        markdown,
        flags=re.DOTALL,
    )
    if not block:
        return None
    m = re.search(r"\*\s+-\s+\[x\]\s+(.+?)\(\d+\)\s*\[", block.group(1))
    if not m:
        return None
    return m.group(1).strip()


def fetch_tga_artg(ingredient: str) -> dict[str, Any]:
    """검색 마크다운으로 등록 여부·첫 ARTG ID를 얻고, 상세를 merge 한다."""
    q = (ingredient or "").strip()
    canonical = (
        f"{_TGA_BASE}/resources/artg?keywords={quote(q)}"
        if q
        else f"{_TGA_BASE}/resources/artg"
    )

    not_reg: dict[str, Any] = {
        "artg_number": None,
        "artg_status": "not_registered",
        "tga_sponsor": None,
        "tga_schedule": None,
        "tga_licence_category": None,
        "tga_licence_status": None,
        "artg_source_url": canonical,
    }

    if not q:
        return not_reg

    jina_url = _jina_wrap(canonical)
    try:
        r = httpx.get(jina_url, timeout=_TIMEOUT)
        if r.status_code != 200:
            return not_reg
        text = r.text or ""
    except Exception:
        return not_reg

    has_results = bool(re.search(r"result\s*\(s\)\s*found", text, flags=re.IGNORECASE))
    if not has_results:
        return not_reg

    artg_id = _parse_first_artg_id(text)
    sponsor_list = _parse_sponsor_filter_first(text)

    detail_url = f"{_TGA_BASE}/resources/artg/{artg_id}" if artg_id else canonical

    out: dict[str, Any] = {
        "artg_number": str(artg_id) if artg_id else None,
        "artg_status": "registered" if artg_id else "not_registered",
        "tga_sponsor": sponsor_list,
        "tga_schedule": None,
        "tga_licence_category": None,
        "tga_licence_status": None,
        "artg_source_url": detail_url,
    }

    if artg_id:
        detail = fetch_tga_detail(artg_id)
        out["tga_sponsor"] = detail.get("tga_sponsor") or out["tga_sponsor"]
        out["tga_licence_category"] = detail.get("tga_licence_category")
        out["tga_licence_status"] = detail.get("tga_licence_status")
        out["tga_schedule"] = detail.get("tga_schedule")

    return out


def determine_export_viable(artg_result: dict[str, Any]) -> dict[str, str]:
    sched = (artg_result.get("tga_schedule") or "").upper()
    status = artg_result.get("artg_status")

    if sched == "S8" or "S8" in sched:
        return {"export_viable": "not_viable", "reason_code": "SCHEDULE_8"}

    if status == "registered":
        return {"export_viable": "viable", "reason_code": "ARTG_REGISTERED"}

    return {"export_viable": "not_viable", "reason_code": "TGA_NOT_APPROVED"}
