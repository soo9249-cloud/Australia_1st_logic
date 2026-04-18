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
from typing import Any
from urllib.parse import quote

import httpx

from utils.crawl_time import now_kst_iso

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
    """ARTG 상세 페이지 마크다운에서 스폰서·라이선스·성분·규제 정보를 추출한다.

    Phase 4.3 추가 필드 (위임지서 §4.3):
      - active_ingredients (list[str])     — bullet 목록 파싱
      - first_registered_date (str|None)   — Start Date 파싱
      - route_of_administration (str|None) — 투여경로
      - sponsor_abn (str|None)             — 호주 사업자번호 (숫자만)
      - strength (str|None)                — TGA 공식 함량 표기

    실패 시 전부 None 필드.
    """
    empty: dict[str, Any] = {
        "tga_sponsor": None,
        "tga_licence_category": None,
        "tga_licence_status": None,
        "tga_schedule": None,
        # Phase 4.3 추가
        "active_ingredients": [],
        "first_registered_date": None,
        "route_of_administration": None,
        "sponsor_abn": None,
        "strength": None,
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

    # ── Phase 4.3: 추가 5필드 파싱 ───────────────────────────────────
    # Active ingredients — "Active Ingredient(s)" 헤더 다음 bullet 목록
    ingredients: list[str] = []
    m_ing = re.search(
        r"Active\s+Ingredient[^\n]*\n+\s*((?:\*\s+[^\n]+\n?)+)",
        text,
        flags=re.IGNORECASE,
    )
    if m_ing:
        for line in m_ing.group(1).splitlines():
            s = line.strip()
            if s.startswith("*"):
                s = s.lstrip("*").strip()
                if s:
                    ingredients.append(s)

    # First registered date — "Start Date" 헤더 다음 "DD Month YYYY"
    first_reg: str | None = None
    m_date = re.search(
        r"Start\s+Date\s*\n+\s*(\d{1,2}\s+\w+\s+\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if m_date:
        first_reg = m_date.group(1).strip()

    # Route of administration — "Route(s) of Administration" 헤더 다음 한 줄
    route: str | None = None
    m_route = re.search(
        r"Route[s]?\s+of\s+Administration\s*\n+\s*([^\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if m_route:
        route = m_route.group(1).strip()

    # Sponsor ABN — "ABN" 다음 9~11자리 숫자 (공백 구분 허용 → 제거)
    abn: str | None = None
    m_abn = re.search(r"ABN\s*[:\s]*(\d[\d\s]{8,})", text)
    if m_abn:
        abn = re.sub(r"\s+", "", m_abn.group(1))

    # Strength — TGA 상세 페이지의 "Strength" 헤더 (있을 때만)
    strength: str | None = None
    m_str = re.search(r"\bStrength\s*\n+\s*([^\n]+)", text, flags=re.IGNORECASE)
    if m_str:
        strength = m_str.group(1).strip()

    return {
        "tga_sponsor": sponsor,
        "tga_licence_category": cat,
        "tga_licence_status": stat,
        "tga_schedule": sched,
        # Phase 4.3 추가
        "active_ingredients": ingredients,
        "first_registered_date": first_reg,
        "route_of_administration": route,
        "sponsor_abn": abn,
        "strength": strength,
    }


def _parse_first_artg_id(markdown: str) -> str | None:
    """검색 결과 마크다운에서 첫 번째 ARTG ID(숫자)를 추출한다."""
    m = re.search(r"###\s+\[[^\]]*\((\d+)\)\]\(", markdown)
    return m.group(1) if m else None


def _parse_all_artg_ids(markdown: str) -> list[str]:
    """검색 결과 마크다운에서 모든 ARTG ID 추출. 중복 제거, 등장순 유지.

    위임지서 §4.2 — Gadvoa 등 다규격 품목(Gadovist 6규격: 2/5/7.5/10/15/65mL)
    대응. `_parse_first_artg_id` 는 하위호환용으로 유지.
    """
    ids = re.findall(r"###\s+\[[^\]]*\((\d+)\)\]\(", markdown)
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


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
        "crawled_at": now_kst_iso(),
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

    # 위임지서 §4.2 — 모든 ARTG 수집 (Gadvoa 6규격 등 다규격 품목 대응)
    artg_id_list = _parse_all_artg_ids(text)
    artg_id = artg_id_list[0] if artg_id_list else None
    sponsor_from_filter = _parse_sponsor_filter_first(text)

    detail_url = f"{_TGA_BASE}/resources/artg/{artg_id}" if artg_id else canonical

    # 기본값 조립
    out = _empty_dto(canonical)
    if artg_id:
        out["tga_found"] = True
        out["tga_artg_ids"] = [str(x) for x in artg_id_list]
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

    # 위임지서 §4.2/4.3 — 각 ARTG 상세 페이지 병렬 수집해 sponsors dedup + 대표 1건 필드 병합
    if artg_id_list:
        all_sponsors: list[str] = []
        first_detail: dict[str, Any] = {}
        for idx, aid in enumerate(artg_id_list):
            detail = fetch_tga_detail(aid)
            if idx == 0:
                first_detail = detail
            sp = detail.get("tga_sponsor")
            if sp and sp not in all_sponsors:
                all_sponsors.append(sp)

        # 대표 상세(첫 ARTG) 에서 기본 라이선스·스케줄·성분·규제 필드 주입
        sp0 = first_detail.get("tga_sponsor")
        if sp0:
            out["tga_sponsor"] = sp0
            out["sponsor_name"] = sp0
        if all_sponsors:
            # filter 스폰서 1건 있어도 상세에서 확보된 실제 스폰서들로 덮어씀
            out["tga_sponsors"] = all_sponsors

        out["tga_licence_category"] = first_detail.get("tga_licence_category")
        out["tga_licence_status"] = first_detail.get("tga_licence_status")
        out["tga_schedule"] = first_detail.get("tga_schedule")
        out["schedule"] = first_detail.get("tga_schedule")
        # 위임지서 §4.3 — schedule_code 는 TGA 값 우선 (PBS 버전번호 "3963" 오염 방지)
        if first_detail.get("tga_schedule"):
            out["schedule_code"] = first_detail.get("tga_schedule")

        # 위임지서 §4.3 — 상세 추가 5필드 반영
        out["active_ingredients"] = first_detail.get("active_ingredients") or []
        out["first_registered_date"] = first_detail.get("first_registered_date")
        out["route_of_administration"] = first_detail.get("route_of_administration")
        out["sponsor_abn"] = first_detail.get("sponsor_abn")
        # strength 는 au_products.json 쪽에 이미 있으면 덮어쓰지 않음 — caller 책임.
        # DTO 에는 TGA 수집값을 그대로 노출.
        out["strength"] = first_detail.get("strength")

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
