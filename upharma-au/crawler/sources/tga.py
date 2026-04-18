# TGA ARTG: Jina Reader로 검색·상세 마크다운을 받아 ARTG·라이선스·스폰서를 파싱한다.
#
# v2 스키마 준수 — TGAArtgDTO (딕셔너리) 반환.
# 스펙: /AX 호주 final/01_보고서필드스키마_v1.md §13-5-2, §14-3-1(au_products), §14-3-3(au_tga_artg)
#
# Phase 4.3-v3 (2026-04-18) — 4필드 폐기:
#   schedule / route_of_administration / first_registered_date / sponsor_abn 전부 삭제.
#   1/2공정 보고서에 불필요 판정. Supabase au_tga_artg 컬럼도 DROP 완료.
#   au_products.tga_schedule 컬럼도 DROP. schedule_code 는 PBS S85/S100 의미로 유지.
#
# 반환 DTO 는 두 용도 모두 커버:
#   - au_products : tga_found, tga_artg_ids(JSONB array), tga_sponsors(JSONB array)
#   - au_tga_artg : artg_id(단일), sponsor_name(단일) — 1품목 여러 행 가능(1:N)
#
# 하위호환 키 유지 (au_crawler/determine_export_viable 에서 사용 중):
#   artg_number, artg_status, tga_sponsor, tga_licence_category, tga_licence_status,
#   artg_source_url

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


def fetch_tga_detail(artg_id: str) -> dict[str, Any]:
    """ARTG 상세 페이지 마크다운에서 스폰서·라이선스·성분·제형·강도를 추출한다.

    Phase 4.3-v3 유지 필드:
      - tga_sponsor (str|None)              — Sponsor 링크 텍스트
      - tga_licence_category (str|None)     — Licence category
      - tga_licence_status (str|None)       — Licence status
      - active_ingredients (list[str])      — Active Ingredient(s) bullet 목록
      - strength (str|None)                 — TGA 공식 함량 (부분 revert 로 복구)
      - dosage_form (str|None)              — TGA 공식 제형 (부분 revert 로 복구)

    폐기 (2026-04-18 결정): schedule, route_of_administration,
      first_registered_date, sponsor_abn — 보고서에서 쓰이지 않음.

    실패 시 빈 리스트·None 필드.
    """
    empty: dict[str, Any] = {
        "tga_sponsor": None,
        "tga_licence_category": None,
        "tga_licence_status": None,
        "active_ingredients": [],
        "strength": None,
        "dosage_form": None,
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

    # Active ingredients — 실측 Jina Reader 포맷 (2026-04-19 Hydrine 로그 확인):
    #   "Ingredients\n\nhydroxycarbamide\n\nLicence category"
    # bullet 아님. 헤더 한 줄 + 빈줄 + 평문 값. 쉼표·개행 모두 split 허용.
    # 구 포맷(Active Ingredients + bullet) 도 보조로 수용.
    _STOP_HEADERS = (
        "Licence", "Sponsor", "Therapeutic", "Summary", "Strength",
        "Dosage", "Dose", "Download", "Related", "Route",
    )
    _stop_re = r"(?:" + "|".join(_STOP_HEADERS) + r")"

    ingredients: list[str] = []

    # 1차: "Ingredients" 또는 "Active Ingredient(s)" 헤더 → 빈줄 → 값 블록 → 빈줄+stop-header
    m_ing = re.search(
        r"(?:^|\n)\s*(?:Active\s+)?Ingredient(?:s|\(s\))?\s*\n\s*\n\s*"
        r"(.+?)"
        r"(?=\n\s*\n\s*" + _stop_re + r"|\n\s*##\s|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m_ing:
        block = m_ing.group(1)
        for raw_line in block.splitlines():
            s = raw_line.strip().lstrip("*-+•").strip()
            if not s:
                continue
            # 쉼표로 복수 성분 (복합제 대응)
            for piece in s.split(","):
                piece = piece.strip()
                if piece and piece not in ingredients:
                    ingredients.append(piece)

    # 2차 fallback: "Ingredients: value" 또는 "Ingredients | value" (테이블 행) 한 줄
    if not ingredients:
        m_ing2 = re.search(
            r"(?:^|\n)\s*(?:Active\s+)?Ingredient(?:s|\(s\))?\s*[:\|]\s*([^\n\|]+)",
            text,
            flags=re.IGNORECASE,
        )
        if m_ing2:
            for piece in m_ing2.group(1).split(","):
                piece = piece.strip()
                if piece and piece not in ingredients:
                    ingredients.append(piece)

    # Strength / Dosage form —
    # 2026-04-19 실측: ARTG 상세 페이지에 "Strength" / "Dosage form" 필드 없음.
    # 대신 Title 라인 / H1 에 "... 500 mg capsule blister (ARTG_ID)" 형태로 포함.
    #   예) "# HYDROXYCARBAMIDE MEDICIANZ hydroxycarbamide (hydroxyurea) 500 mg capsule blister (313760)"
    strength: str | None = None
    dosage_form: str | None = None

    # 1차: 옛 "Strength\n\n값" 포맷 (다른 페이지·버전 호환)
    m_str = re.search(r"(?:^|\n)\s*Strength\s*\n+\s*([^\n]+)", text, flags=re.IGNORECASE)
    if m_str:
        strength = m_str.group(1).strip()

    m_df = re.search(
        r"(?:^|\n)\s*Dos(?:age|e)\s+form\s*\n+\s*([^\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if m_df:
        dosage_form = m_df.group(1).strip()

    # 2차: Title / H1 에서 추출 — ARTG 상세 기본 포맷
    if strength is None or dosage_form is None:
        title_line: str | None = None
        m_t = re.search(r"(?:^|\n)Title:\s*(.+)", text)
        if m_t:
            title_line = m_t.group(1).strip()
        else:
            m_h = re.search(r"(?:^|\n)#\s+(.+)", text)
            if m_h:
                title_line = m_h.group(1).strip()
                # H1 "Title | Therapeutic Goods Administration (TGA)" 제거
                title_line = re.split(r"\s+\|\s+Therapeutic\s+Goods", title_line)[0].strip()
        if title_line:
            # strength = 숫자+단위 패턴 (슬래시 구분자 흡수)
            m_s = re.search(
                r"(\d[\d.,]*\s*(?:mg|mcg|µg|g|ml|mL|kg|iu|IU|units?|%)"
                r"(?:\s*/\s*[\d.]*\s*(?:mg|mcg|µg|g|ml|mL|kg|iu|IU|units?|%)?)?)",
                title_line,
            )
            if m_s and strength is None:
                strength = m_s.group(1).strip()
                # dosage_form = strength 뒤의 소문자 단어들 (ARTG ID 괄호 / 파이프 직전까지)
                tail = title_line[m_s.end():]
                m_df2 = re.match(
                    r"\s+([A-Za-z][A-Za-z\s\-,]*?)(?=\s*\(\s*\d+\s*\)|\s+\|\s+|$)",
                    tail,
                )
                if m_df2 and dosage_form is None:
                    df = m_df2.group(1).strip().rstrip(",").strip()
                    # 용기 단어 (blister/bottle/ampoule/...) 제거 — 제형 본체만 유지
                    df_clean = re.sub(
                        r"\s+(blister|bottle|pack|ampoule|vial|sachet|tube|carton|pouch)\s*$",
                        "",
                        df,
                        flags=re.IGNORECASE,
                    ).strip()
                    dosage_form = df_clean or df

    return {
        "tga_sponsor": sponsor,
        "tga_licence_category": cat,
        "tga_licence_status": stat,
        "active_ingredients": ingredients,
        "strength": strength,
        "dosage_form": dosage_form,
    }


def _parse_first_artg_id(markdown: str) -> str | None:
    """검색 결과 마크다운에서 첫 번째 ARTG ID(숫자)를 추출한다."""
    m = re.search(r"###\s+\[[^\]]*\((\d+)\)\]\(", markdown)
    return m.group(1) if m else None


def _parse_all_artg_ids(markdown: str) -> list[str]:
    """검색 결과 마크다운에서 모든 ARTG ID 추출. 중복 제거, 등장순 유지.

    Gadvoa 등 다규격 품목(Gadovist 6규격) 대응. `_parse_first_artg_id` 는
    하위호환용 (현행 코드는 미사용).
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
    """TGA 미등재 품목 TGAArtgDTO. tga_found=False.

    Phase 4.3-v3 — 4필드(schedule, route_of_administration,
    first_registered_date, sponsor_abn) 전부 삭제.
    strength / dosage_form 은 부분 revert 로 복구 — PBS 미등재 품목 fallback.
    """
    return {
        # v2 신규 키 (§13-5-2, §14-3-1 JSONB 배열)
        "tga_found": False,
        "tga_artg_ids": [],
        "tga_sponsors": [],
        "raw_html_snippet": None,
        # au_tga_artg 용 단일 필드 (품목당 여러 행 가능 — 현재는 1건만)
        "artg_id": None,
        "sponsor_name": None,
        "active_ingredients": [],
        "strength": None,
        "dosage_form": None,
        "status": "not_registered",
        "artg_url": canonical_url,
        # 하위호환 키 (au_crawler·determine_export_viable 사용 중)
        "artg_number": None,
        "artg_status": "not_registered",
        "tga_sponsor": None,
        "tga_licence_category": None,
        "tga_licence_status": None,
        "artg_source_url": canonical_url,
        # 메타
        "source_name": "tga",
        "crawled_at": now_kst_iso(),
    }


def fetch_tga_artg(ingredient: str) -> dict[str, Any]:
    """검색 마크다운으로 등록 여부·ARTG ID 배열을 얻고, 상세를 merge 한다.

    Phase 4.3-v3 — 폐기된 4필드 코드 제거. 유지 필드:
      tga_sponsor(s), tga_licence_category, tga_licence_status, active_ingredients.
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

    # 다규격 품목 대응 — 모든 ARTG 수집
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

    # 각 ARTG 상세 페이지 수집해 sponsors dedup + 대표 1건 필드 병합
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

        # 대표 상세(첫 ARTG) 에서 라이선스·성분 필드 주입
        sp0 = first_detail.get("tga_sponsor")
        if sp0:
            out["tga_sponsor"] = sp0
            out["sponsor_name"] = sp0
        if all_sponsors:
            out["tga_sponsors"] = all_sponsors

        out["tga_licence_category"] = first_detail.get("tga_licence_category")
        out["tga_licence_status"] = first_detail.get("tga_licence_status")
        out["active_ingredients"] = first_detail.get("active_ingredients") or []
        # Phase 4.3-v3 부분 revert — TGA strength/dosage_form 복구 (PBS 미등재 fallback)
        out["strength"] = first_detail.get("strength")
        out["dosage_form"] = first_detail.get("dosage_form")

    # au_tga_artg.raw_response 저장용 (2KB 컷)
    out["raw_html_snippet"] = (text[:_RAW_SNIPPET_MAX]) if text else None

    return out


def determine_export_viable(artg_result: dict[str, Any]) -> dict[str, str]:
    """TGA ARTG 등록 여부만으로 export_viable 판정.

    Phase 4.3-v3 — tga_schedule 참조 제거 (4필드 폐기). S8 차단 로직은
    TGA 스케줄 정보가 더 이상 크롤러에 없으므로 제거 — ARTG 등록 여부로만 판정.
    향후 Schedule 8 여부 판단이 필요하면 별도 출처 (Poisons Standard SUSMP 등)
    에서 재도입해야 함.
    """
    status = artg_result.get("artg_status")
    if status == "registered":
        return {"export_viable": "viable", "reason_code": "ARTG_REGISTERED"}
    return {"export_viable": "not_viable", "reason_code": "TGA_NOT_APPROVED"}
