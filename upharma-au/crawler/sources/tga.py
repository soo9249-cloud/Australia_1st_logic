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
    Phase Omethyl — match_type / tga_artg_details 배열 키 추가.
    """
    return {
        # v2 신규 키 (§13-5-2, §14-3-1 JSONB 배열)
        "tga_found": False,
        "tga_artg_ids": [],
        "tga_sponsors": [],
        "raw_html_snippet": None,
        # au_tga_artg 용 단일 필드 (품목당 여러 행 가능)
        "artg_id": None,
        "sponsor_name": None,
        "active_ingredients": [],
        "strength": None,
        "dosage_form": None,
        "status": "not_registered",
        "artg_url": canonical_url,
        # Phase Omethyl — 대표 ARTG 의 match_type + 전체 후보 배열
        "match_type": None,
        "tga_artg_details": [],
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


# Phase Sereterol (2026-04-19) — TGA 검색 결과 정확 매칭용 헬퍼.
# 오리지널 브랜드 보유 빅파마 스폰서 키워드. 매칭되면 첫 후보로 우선.
_ORIGINATOR_SPONSOR_KEYWORDS: tuple[str, ...] = (
    "glaxosmithkline", "gsk",
    "novartis",
    "pfizer",
    "bayer",
    "astrazeneca",
    "merck",
    "merck sharp",   # MSD 호주 법인
    "msd",
    "roche",
    "sanofi",
    "eli lilly", "lilly",
    "boehringer",
    "abbvie", "abbott",
    "janssen",
    "bristol-myers", "bristol myers", "bms",
    "amgen",
    "takeda",
    "astellas",
)


def _sponsor_is_originator_candidate(sponsor: str | None) -> bool:
    """스폰서명에 originator 빅파마 키워드가 포함돼 있는지 (대소문자 무시)."""
    if not sponsor:
        return False
    low = str(sponsor).lower()
    return any(kw in low for kw in _ORIGINATOR_SPONSOR_KEYWORDS)


def _normalize_strength_cmp(s: str | None) -> str:
    """strength 비교용 정규화 — 공백 제거·소문자·통상 단위 표기 흡수.

    "2 g" / "2g" / "2000 mg" / "2000mg" 는 의미상 같지만 단순 문자열 비교는 실패.
    이 함수는 단순 case-insensitive + 공백 제거만 수행 (심볼 단위 변환까진 안 함).
    strength exact match 는 문자열 ``==`` 또는 ``in`` 로 판정하되, 변환 필요한 경우는
    caller 레벨에서 보강할 것.
    """
    t = (s or "").strip().lower().replace(" ", "")
    return t


def _strength_match(a: str | None, b: str | None) -> bool:
    """양쪽 모두 비어있지 않고, 정규화 후 하나가 다른 하나의 substring 이면 True."""
    na, nb = _normalize_strength_cmp(a), _normalize_strength_cmp(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _dosage_form_match(a: str | None, b: str | None) -> bool:
    """제형 비교 — 공백·하이픈·쉼표 제거 후 substring 비교."""
    def _norm(s: str | None) -> str:
        t = (s or "").strip().lower()
        t = re.sub(r"[\s\-,]+", "", t)
        return t
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def fetch_tga_artg(
    ingredient: str,
    *,
    expected_inns: list[str] | None = None,
    match_mode: str = "strict",
    expected_strength: str | None = None,
    expected_dosage_form: str | None = None,
) -> dict[str, Any]:
    """검색 마크다운으로 등록 여부·ARTG ID 배열을 얻고, 상세를 merge 한다.

    Phase 4.3-v3 — 폐기된 4필드 코드 제거. 유지 필드:
      tga_sponsor(s), tga_licence_category, tga_licence_status, active_ingredients.

    Phase Sereterol 수정 (2026-04-19):
      `expected_inns` 가 제공되면 각 ARTG 상세에서 active_ingredients 를 가져와
      base INN set-equality 로 필터. 후보 정렬:
        (a) sponsor 가 originator 빅파마 키워드 포함 → 우선
        (b) 동순위 시 ARTG ID 오름차순 (등재일 대체 프록시 — 작은 값이 오래됨)
      필터 결과 0건이면 기존 동작(전체 검색 결과) 으로 fallback + warning.

    Phase Omethyl 수정 (2026-04-19):
      `match_mode` 파라미터 추가 — Sereterol 의 set-equality 엄격 필터는
      ESTIMATE_private 같은 함량·제형 상이 케이스에서 0건 매칭을 유발했음.

      - `match_mode='strict'` (기본, DIRECT/FDC 용): 기존 set-equality 그대로
        (활성성분 완전 일치만 통과)
      - `match_mode='ingredient_only'` (ESTIMATE_* 용): 활성성분 set 이
        `expected_inns` 를 **포함(subset)** 하면 통과. 함량·제형 다른 ARTG 도 수용.

      각 ARTG 에 `match_type` 필드 부착:
        - `exact`: inn_set == expected AND strength match AND dosage_form match
        - `same_ingredient_diff_form`: 성분은 맞지만 strength 또는 form 다름
        - (향후 `similar_inn` 은 유사계열 분기에서)

      정렬:
        (1) match_type='exact' 먼저
        (2) originator 빅파마 sponsor 우선
        (3) ARTG ID 오름차순

      반환 DTO 에 `tga_artg_details: list[dict]` 추가 — 각 매칭 ARTG 의
      (artg_id, sponsor_name, active_ingredients, strength, dosage_form, match_type)
      전부 저장. au_crawler 가 이 배열을 iterate 해서 au_tga_artg 다행 INSERT.
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

    # Phase Sereterol — expected_inns 매칭용 base INN set 계산 (제공됐을 때)
    expected_set: frozenset[str] | None = None
    if expected_inns:
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from utils.inn_normalize import strip_inn_salt

        expected_set = frozenset(
            s for s in (strip_inn_salt(c) for c in expected_inns) if s
        )
        if not expected_set:
            expected_set = None

    # 각 ARTG 상세 페이지 수집 — (ARTG ID, detail) 튜플 리스트
    detail_pairs: list[tuple[str, dict[str, Any]]] = []
    all_sponsors: list[str] = []
    if artg_id_list:
        for aid in artg_id_list:
            detail = fetch_tga_detail(aid)
            detail_pairs.append((aid, detail))
            sp = detail.get("tga_sponsor")
            if sp and sp not in all_sponsors:
                all_sponsors.append(sp)

    # Phase Omethyl — match_mode 기반 필터 + match_type 계산
    filtered_pairs: list[tuple[str, dict[str, Any]]] = list(detail_pairs)
    if expected_set is not None and detail_pairs:
        import sys as _sys2
        import os as _os2
        _sys2.path.insert(0, _os2.path.dirname(_os2.path.dirname(_os2.path.abspath(__file__))))
        from utils.inn_normalize import extract_inn_set

        eligible: list[tuple[str, dict[str, Any]]] = []
        for aid, det in detail_pairs:
            ingredients_list = det.get("active_ingredients") or []
            inn_set = extract_inn_set(*ingredients_list)

            # 통과 판정
            passes = False
            if match_mode == "strict":
                passes = (inn_set == expected_set)
            elif match_mode == "ingredient_only":
                # expected_inns ⊆ ARTG inn_set (자사 성분 전부 포함하면 통과,
                # ARTG 가 추가 성분 가졌어도 OK).
                passes = expected_set.issubset(inn_set) and bool(expected_set)
            else:
                passes = (inn_set == expected_set)  # fallback = strict

            if not passes:
                continue

            # match_type 계산
            inn_exact = (inn_set == expected_set)
            str_ok = _strength_match(expected_strength, det.get("strength"))
            form_ok = _dosage_form_match(expected_dosage_form, det.get("dosage_form"))
            # expected_strength / expected_dosage_form 이 아예 없으면 exact 판정 불가
            has_self_meta = bool(expected_strength) and bool(expected_dosage_form)
            if inn_exact and has_self_meta and str_ok and form_ok:
                det["match_type"] = "exact"
            else:
                det["match_type"] = "same_ingredient_diff_form"
            eligible.append((aid, det))

        if eligible:
            filtered_pairs = eligible
            out["tga_filter_applied"] = f"inn_{match_mode}"
            out["tga_filter_count_before"] = len(detail_pairs)
            out["tga_filter_count_after"] = len(eligible)
        else:
            # 필터 결과 0건 → fallback. warning 만 표시, 원본 리스트 유지 (match_type=None).
            out["tga_filter_applied"] = f"inn_{match_mode}_failed_fallback_all"
            out["tga_filter_count_before"] = len(detail_pairs)
            out["tga_filter_count_after"] = 0
            for _aid, det in filtered_pairs:
                det.setdefault("match_type", None)

    # Phase Omethyl — 정렬:
    #   (1) match_type='exact' 먼저 (같은 성분 + 함량 + 제형 일치)
    #   (2) originator 빅파마 sponsor 우선
    #   (3) ARTG ID 오름차순
    def _sort_key(pair: tuple[str, dict[str, Any]]) -> tuple[int, int, int]:
        aid_str, det = pair
        is_exact = det.get("match_type") == "exact"
        is_originator = _sponsor_is_originator_candidate(det.get("tga_sponsor"))
        try:
            aid_int = int(aid_str)
        except ValueError:
            aid_int = 10**12
        return (
            0 if is_exact else 1,
            0 if is_originator else 1,
            aid_int,
        )

    filtered_pairs = sorted(filtered_pairs, key=_sort_key)

    # 대표 상세 = 정렬된 첫 번째 후보 + 전체 상세 배열 보존
    if filtered_pairs:
        first_aid, first_detail = filtered_pairs[0]
        out["tga_artg_ids"] = [aid for aid, _ in filtered_pairs]
        out["artg_id"] = str(first_aid)
        out["artg_number"] = str(first_aid)
        out["artg_url"] = f"{_TGA_BASE}/resources/artg/{first_aid}"
        out["artg_source_url"] = out["artg_url"]

        sp0 = first_detail.get("tga_sponsor")
        if sp0:
            out["tga_sponsor"] = sp0
            out["sponsor_name"] = sp0
        filtered_sponsors: list[str] = []
        for _aid, det in filtered_pairs:
            sp = det.get("tga_sponsor")
            if sp and sp not in filtered_sponsors:
                filtered_sponsors.append(sp)
        if filtered_sponsors:
            out["tga_sponsors"] = filtered_sponsors
        elif all_sponsors:
            out["tga_sponsors"] = all_sponsors

        out["tga_licence_category"] = first_detail.get("tga_licence_category")
        out["tga_licence_status"] = first_detail.get("tga_licence_status")
        out["active_ingredients"] = first_detail.get("active_ingredients") or []
        out["strength"] = first_detail.get("strength")
        out["dosage_form"] = first_detail.get("dosage_form")
        # 대표 ARTG 의 match_type (au_products level 플래그)
        out["match_type"] = first_detail.get("match_type")

        # Phase Omethyl — 매칭된 ARTG 전부의 상세 배열. au_crawler 가 iterate 해서
        # au_tga_artg 다행 INSERT. 각 엔트리는 match_type 로 라벨링.
        out["tga_artg_details"] = [
            {
                "artg_id": aid,
                "sponsor_name": det.get("tga_sponsor"),
                "active_ingredients": det.get("active_ingredients") or [],
                "strength": det.get("strength"),
                "dosage_form": det.get("dosage_form"),
                "match_type": det.get("match_type"),
            }
            for aid, det in filtered_pairs
        ]

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
