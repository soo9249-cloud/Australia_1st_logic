"""Stage 1 결정적 필터 — AI 없음, 알고리즘 투명.

4-Pass 구조 (위임지서 §4):
  Pass 1: 정규화 + 중복 제거 (회사명 통합)
  Pass 2: 블랙리스트 hard-kill (대마·기기·원료상·약국·CDD)
  Pass 3: 4-case 성분 보유 분류 (A/B/C/D) — au_tga_artg 매트릭스
  Pass 4: 티어 스코어링 + 전부 출력 (top-N 컷 없음)

출력: 생존자 리스트 (Stage 1 내부 점수 내림차순, 컷 없음 — 사용자가 하드코딩
전에 전부 확인).

Stage 1 내부 점수는 정렬용. 실제 최종 Top 10 점수는 Stage 2 Haiku 가 매김.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .sources.db_sponsors import fetch_artg_matrix_for_buyer

# INN 정규화 — hydroxyurea ↔ hydroxycarbamide 같은 동의어·염 꼬리 제거.
# crawler/utils/inn_normalize.py 의 _INN_ALIASES 테이블을 통해
# TGA(WHO INN) ↔ au_products.json(USAN) 간 표기 차이를 흡수한다.
import sys as _sys
from pathlib import Path as _Path
_CRAWLER_ROOT = _Path(__file__).resolve().parent.parent
if str(_CRAWLER_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_CRAWLER_ROOT))
from crawler.utils.inn_normalize import extract_inn_set, strip_inn_salt  # noqa: E402

_SEEDS = Path(__file__).resolve().parent / "seeds"


def _load_seed(name: str) -> dict[str, Any]:
    """시드 JSON 로드. 파일 없으면 빈 dict (방어적)."""
    p = _SEEDS / name
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[stage1_filter] seed 로드 실패 {name}: {exc}", flush=True)
        return {}


_ALIASES_DATA = _load_seed("company_aliases.json")
# _meta 키 제외한 실제 alias 매핑만
_ALIASES: dict[str, str] = {
    k: v for k, v in _ALIASES_DATA.items()
    if not k.startswith("_") and isinstance(v, str)
}
_BLACKLIST = _load_seed("blacklist.json")
_INN_THERAPY = _load_seed("inn_to_therapy.json")


# ─────────────────────────────────────────────────────────────────────
# Pass 1 · 정규화 + 중복 제거
# ─────────────────────────────────────────────────────────────────────

def normalize_name(raw: str) -> str:
    """회사명 표준화 → canonical_key (소문자 snake_case).

    절차:
      1. 소문자 + trim
      2. 여러 공백 → 단일
      3. 괄호 안 내용 제거
      4. 기업 접미사 (Pty Ltd, Ltd, Inc, GmbH, ...) 제거
      5. aliases 매칭 시 canonical_key 반환
      6. 공백·마침표·쉼표 제거
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)  # 괄호 내용 제거
    s = re.sub(
        r"\s*(pty\s*ltd|limited|ltd|inc|gmbh|plc|corp|co\.|s\.a\.)\.?\s*$",
        "",
        s,
    )
    s = s.strip()
    if s in _ALIASES:
        return _ALIASES[s]
    return s.replace(" ", "_").replace(".", "").replace(",", "")


def _new_merged_entry(row: dict[str, Any], key: str) -> dict[str, Any]:
    return {
        "canonical_key": key,
        "canonical_name": row.get("name"),
        "sources": [],
        "raw_data": {
            "website": None,
            "email": None,
            "phone": None,
            "address": None,          # 2026-04-20 추가 — GBMA 페이지 본문에서 파싱
            "state": None,            # 2026-04-20 추가 — VIC/NSW/QLD 등
            "description": None,
            "represented_brands": [],
        },
        "tga_artg_count": 0,
        "pbs_listed_count": 0,
        "is_ma_member": False,
        "is_gbma_member": False,
        "is_gpce_exhibitor": False,
        "inn_match_artgs": [],
    }


def dedupe_and_merge(all_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """전 소스 머지 (canonical_key 기준 통합)."""
    merged: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        key = normalize_name(row.get("name", ""))
        if not key or len(key) < 2:
            continue
        if key not in merged:
            merged[key] = _new_merged_entry(row, key)
        m = merged[key]

        src = row.get("source")
        if src and src not in m["sources"]:
            m["sources"].append(src)

        if src == "tga":
            m["tga_artg_count"] += int(row.get("artg_count") or 1)
        elif src == "pbs":
            m["pbs_listed_count"] += 1
        elif src == "ma":
            m["is_ma_member"] = True
            w = row.get("website")
            if isinstance(w, str) and w.startswith("http") and not m["raw_data"]["website"]:
                m["raw_data"]["website"] = w
        elif src == "gbma":
            m["is_gbma_member"] = True
            w = row.get("website")
            if isinstance(w, str) and w.startswith("http") and not m["raw_data"]["website"]:
                m["raw_data"]["website"] = w
            # 2026-04-20 추가 — GBMA 본문 파싱 결과 병합
            for fld in ("address", "state", "phone"):
                v = row.get(fld)
                if v and not m["raw_data"].get(fld):
                    m["raw_data"][fld] = v
        elif src == "gpce":
            m["is_gpce_exhibitor"] = True
            for k in ("website", "email", "phone", "description", "address", "state"):
                v = row.get(k)
                if v and not m["raw_data"].get(k):
                    m["raw_data"][k] = v
            brands = row.get("represented_brands")
            if brands and not m["raw_data"]["represented_brands"]:
                m["raw_data"]["represented_brands"] = brands
        elif src == "tga_inn_match":
            m["inn_match_artgs"].append({
                "artg_id": row.get("artg_id"),
                "matched_ingredient": row.get("matched_ingredient"),
                "matched_inn": row.get("matched_inn"),
            })
    return merged


# ─────────────────────────────────────────────────────────────────────
# Pass 2 · 블랙리스트 hard-kill
# ─────────────────────────────────────────────────────────────────────

def apply_blacklist(merged: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """blacklist.json.hard_kill 패턴에 회사명 부분일치 시 제외."""
    hard_kill = _BLACKLIST.get("hard_kill", []) or []
    out: dict[str, dict[str, Any]] = {}
    for key, row in merged.items():
        name_lower = (row.get("canonical_name") or "").lower()
        killed = False
        for b in hard_kill:
            pat = (b.get("pattern") or "").lower()
            if pat and pat in name_lower:
                killed = True
                break
        if not killed:
            out[key] = row
    return out


# ─────────────────────────────────────────────────────────────────────
# Pass 3 · ★ 4-case 성분 보유 분류
# ─────────────────────────────────────────────────────────────────────

# 위임지서 §1-3 — UI 표시용 파이프라인 base score (Stage 2 에서 Haiku 가 ±3 조정)
CASE_PIPELINE_SCORE: dict[str, int] = {
    "B_ideal_buyer": 25,   # 각 성분 별개 보유 — 이상적 바이어 ⭐
    "A_competitor":  15,   # 복합제 자체 보유 (경쟁)
    "C_partial":     12,   # 일부 성분만 보유
    "D_none":         0,   # 성분 없음
}


def classify_ingredient_coverage(
    buyer_name: str,
    target_inns: list[str],
) -> tuple[str, str]:
    """바이어의 ARTG 매트릭스를 target_inns 와 대조해 4-case 분류.

    Args:
      buyer_name: au_tga_artg.sponsor_name 조회 키
      target_inns: au_products.inn_components + similar_inns (소문자 필터 포함)

    Returns:
      (case_code, label)
        case_code ∈ {"A_competitor", "B_ideal_buyer", "C_partial", "D_none"}
        label: UI 표시용 한국어 문구

    2026-04-19 수정:
      · 성분 비교 시 양쪽 모두 `strip_inn_salt`·`extract_inn_set` 로 정규화.
        예) target "hydroxyurea" ↔ TGA "hydroxycarbamide" 는 _INN_ALIASES
        를 통해 canonical "hydroxycarbamide" 로 통일 → set-equality 성립.
      · FDC 복합제 sponsor (ARTG 한 행 안에 모든 inn_components 를 포함) 는
        경쟁자(A_competitor) 가 아니라 **B_ideal_buyer** 로 재분류. 동일
        복합제 취급 경험 있는 회사가 한국유나이티드 품목의 현실적 바이어
        1순위이므로.
    """
    if not target_inns:
        return ("D_none", "미보유")

    # target_set: INN 을 canonical (염·수화물 제거 + USAN→WHO INN 매핑) 으로 정규화
    target_set: set[str] = set()
    for t in target_inns:
        base = strip_inn_salt(t or "")
        if base:
            target_set.add(base)
    if not target_set:
        return ("D_none", "미보유")

    raw_matrix = fetch_artg_matrix_for_buyer(buyer_name)  # {artg_id: {원본문자열}}

    # 각 ARTG 의 active_ingredients 문자열들을 canonical INN set 으로 변환
    matrix: dict[str, frozenset[str]] = {
        artg_id: extract_inn_set(*ings)
        for artg_id, ings in raw_matrix.items()
    }

    # Case A/B 공통 — 같은 ARTG 하나에 target_inns 전부 포함
    # (사용자 지시 2026-04-19): FDC 복합제 sponsor 는 경쟁이 아니라
    # 이상적 바이어로 분류. 동일 복합제 취급 경험 = 최우선 바이어.
    for artg_id, artg_inns in matrix.items():
        if target_set.issubset(artg_inns):
            return (
                "B_ideal_buyer",
                f"복합제 보유 (FDC 취급 경험) — ARTG {artg_id}",
            )

    # 전체 합집합 (여러 ARTG 에 걸쳐 각 성분 별개 보유 여부 판정용)
    all_covered: set[str] = set()
    for inns in matrix.values():
        all_covered |= set(inns)

    matched = target_set & all_covered

    # Case B: 모든 성분이 별개 ARTG 로 커버 (각 성분 개별 보유 = 이상적 바이어)
    if matched == target_set:
        return ("B_ideal_buyer", f"별개 보유 ({', '.join(sorted(matched))})")

    # Case C: 일부 보유
    if matched:
        return ("C_partial", f"일부 보유 ({', '.join(sorted(matched))})")

    # Case D: 성분 매칭 0
    return ("D_none", "미보유")


def apply_ingredient_classification(
    survivors: dict[str, dict[str, Any]],
    product: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """전 생존자에 4-case 분류 + pipeline_base_score 주입."""
    inns = list(product.get("inn_components") or [])
    similar = list(product.get("similar_inns") or [])
    target = inns + similar

    for key, row in survivors.items():
        case, label = classify_ingredient_coverage(row.get("canonical_name") or key, target)
        row["ingredient_case"] = case
        row["ingredient_label"] = label
        row["pipeline_base_score"] = CASE_PIPELINE_SCORE.get(case, 0)
    return survivors


# ─────────────────────────────────────────────────────────────────────
# Pass 4 · 티어 스코어링 (정렬용만, 컷 없음)
# ─────────────────────────────────────────────────────────────────────

def score_for_sorting(row: dict[str, Any]) -> int:
    """Stage 1 내부 점수 — 사용자에게 정렬 순서를 보여주기 위한 휴리스틱.

    실제 최종 점수(100점)는 Stage 2 Haiku 가 매김. 여기는 어디까지나 정렬용.
    """
    s = 0
    sources = row.get("sources") or []
    if "tga" in sources:           s += 30
    if "pbs" in sources:           s += 25
    if "ma" in sources:            s += 15
    if "gbma" in sources:          s += 15
    if "gpce" in sources:          s += 10
    if "tga_inn_match" in sources: s += 20
    s += min(int(row.get("tga_artg_count") or 0) * 2, 20)
    s += min(int(row.get("pbs_listed_count") or 0) * 3, 20)
    s += int(row.get("pipeline_base_score") or 0)
    return s


def run_stage1(
    collected: dict[str, list[dict[str, Any]]],
    product: dict[str, Any],
) -> list[dict[str, Any]]:
    """Stage 1 전체 실행 — 컷 없이 전부 반환, sort_score 내림차순.

    Args:
      collected: pipeline_collect.collect_all_sources() 반환 dict
      product:   au_products.json 의 한 엔트리

    Returns:
      list[dict] — 각 엔트리에 canonical_key, ingredient_case, pipeline_base_score,
      stage1_sort_score, sources 등 포함
    """
    all_rows: list[dict[str, Any]] = []
    for src_key in (
        "tga_sponsors",
        "pbs_sponsors",
        "ma_members",
        "gbma_members",
        "gpce_exhibitors",
        "inn_match_sponsors",
    ):
        all_rows.extend(collected.get(src_key) or [])

    merged = dedupe_and_merge(all_rows)
    merged = apply_blacklist(merged)
    merged = apply_ingredient_classification(merged, product)

    sorted_list = sorted(
        merged.values(),
        key=score_for_sorting,
        reverse=True,
    )
    for row in sorted_list:
        row["stage1_sort_score"] = score_for_sorting(row)
    return sorted_list
