# TGA ARTG: Jina Reader로 검색·상세 마크다운을 받아 ARTG·라이선스·스폰서를 파싱한다.
#
# v2 스키마 준수 — TGAArtgDTO (딕셔너리) 반환.
# 스펙: /AX 호주 final/01_보고서필드스키마_v1.md §13-5-2, §14-3-1(au_products), §14-3-3(au_tga_artg)
#
# 반환 DTO 는 두 용도 모두 커버:
#   - au_products : tga_found, tga_artg_ids(JSONB array), tga_sponsors(JSONB array)
#   - au_tga_artg : artg_id(단일), sponsor_name(단일) — 1품목 여러 행 가능(1:N)
#
# 하위호환 키 유지 (au_crawler/determine_export_viable 에서 사용 중):
#   artg_number, artg_status, tga_sponsor, tga_schedule, tga_licence_category, tga_licence_status, artg_source_url

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

_JINA = "https://r.jina.ai/"
_TGA_BASE = "https://www.tga.gov.au"
_TIMEOUT = 20.0
_RAW_SNIPPET_MAX = 2048  # au_tga_artg.raw_response 저장용 (2KB 컷)


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


def _empty_dto(canonical_url: str) -> dict[str, Any]:
    """TGA 미등재 품목 TGAArtgDTO. tga_found=False."""
    return {
        # v2 신규 키 (§13-5-2, §14-3-1 JSONB 배열)
        "tga_found": False,
        "tga_artg_ids": [],
        "tga_sponsors": [],
        "schedule_code": None,          # S2/S3/S4/S8 (TGA 스케줄)
        "raw_html_snippet": None,
        # au_tga_artg 용 단일 필드 (품목당 여러 행 가능 — 현재는 1건만)
        "artg_id": None,
        "sponsor_name": None,
        "sponsor_abn": None,
        "active_ingredients": [],
        "strength": None,
        "dosage_form": None,
        "route_of_administration": None,
        "schedule": None,
        "first_registered_date": None,
        "status": "not_registered",
        "artg_url": canonical_url,
        # 하위호환 키 (au_crawler·determine_export_viable 사용 중)
        "artg_number": None,
        "artg_status": "not_registered",
        "tga_sponsor": None,
        "tga_schedule": None,
        "tga_licence_category": None,
        "tga_licence_status": None,
        "artg_source_url": canonical_url,
        # 메타
        "source_name": "tga",
        "crawled_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_tga_artg(ingredient: str) -> dict[str, Any]:
    """검색 마크다운으로 등록 여부·첫 ARTG ID를 얻고, 상세를 merge 한다.

    v2 TGAArtgDTO 형식 반환 (§13-5-2). au_products.tga_artg_ids/tga_sponsors(배열)
    + au_tga_artg 개별 행 필드 + 하위호환 키 (artg_number/artg_status 등) 전부 포함.

    현재는 첫 ARTG 1 건만 파싱 → tga_artg_ids = [artg_id] 로 배열화.
    다중 ARTG 파싱은 별도 위임 예정.
    """
    q = (ingredient or "").strip()
    canonical = (
        f"{_TGA_BASE}/resources/artg?keywords={quote(q)}"
        if q
        else f"{_TGA_BASE}/resources/artg"
    )

    if not q:
        return _empty_dto(canonical)

    jina_url = _jina_wrap(canonical)
    try:
        r = httpx.get(jina_url, timeout=_TIMEOUT)
        if r.status_code != 200:
            return _empty_dto(canonical)
        text = r.text or ""
    except Exception:
        return _empty_dto(canonical)

    has_results = bool(re.search(r"result\s*\(s\)\s*found", text, flags=re.IGNORECASE))
    if not has_results:
        return _empty_dto(canonical)

    artg_id = _parse_first_artg_id(text)
    sponsor_from_filter = _parse_sponsor_filter_first(text)

    detail_url = f"{_TGA_BASE}/resources/artg/{artg_id}" if artg_id else canonical

    # 기본값 조립
    out = _empty_dto(canonical)
    if artg_id:
        out["tga_found"] = True
        out["tga_artg_ids"] = [str(artg_id)]
        out["artg_id"] = str(artg_id)
        out["artg_number"] = str(artg_id)
        out["artg_status"] = "registered"
        out["status"] = "registered"
        out["artg_url"] = detail_url
        out["artg_source_url"] = detail_url

    if sponsor_from_filter:
        out["tga_sponsor"] = sponsor_from_filter
        out["sponsor_name"] = sponsor_from_filter
        out["tga_sponsors"] = [sponsor_from_filter]

    if artg_id:
        detail = fetch_tga_detail(artg_id)
        sp = detail.get("tga_sponsor")
        if sp:
            out["tga_sponsor"] = sp
            out["sponsor_name"] = sp
            # 스폰서 배열 업데이트 (filter 값과 다를 수 있으므로 덮어쓰기)
            out["tga_sponsors"] = [sp]
        out["tga_licence_category"] = detail.get("tga_licence_category")
        out["tga_licence_status"] = detail.get("tga_licence_status")
        out["tga_schedule"] = detail.get("tga_schedule")
        out["schedule"] = detail.get("tga_schedule")
        out["schedule_code"] = detail.get("tga_schedule")

    # au_tga_artg.raw_response 저장용 (2KB 컷)
    out["raw_html_snippet"] = (text[:_RAW_SNIPPET_MAX]) if text else None

    return out


def determine_export_viable(artg_result: dict[str, Any]) -> dict[str, str]:
    sched = (artg_result.get("tga_schedule") or "").upper()
    status = artg_result.get("artg_status")

    if sched == "S8" or "S8" in sched:
        return {"export_viable": "not_viable", "reason_code": "SCHEDULE_8"}

    if status == "registered":
        return {"export_viable": "viable", "reason_code": "ARTG_REGISTERED"}

    return {"export_viable": "not_viable", "reason_code": "TGA_NOT_APPROVED"}
