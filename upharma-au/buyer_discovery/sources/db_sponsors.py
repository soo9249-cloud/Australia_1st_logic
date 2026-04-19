"""TGA/PBS 스폰서 + 성분 기반 ARTG 스폰서 — DB SELECT 만.

위임지서 §3-1 + §0-1-a 실제 스키마 기준 (2026-04-19 검증 완료):
  - 메인 테이블: `au_products` (구 `australia` 아님). FK 는 `product_id` (TEXT).
  - `au_products.tga_sponsors` : JSONB 배열 (TGA 시장조사에서 수집).
  - `au_products.originator_sponsor` : TEXT (PBS 오리지네이터 1건).
  - `au_pbs_raw.endpoint_organisations` : JSONB. PBS /organisations 엔드포인트 응답
    전체. 구조 다양 (data/organisations/items/results 키 또는 직접 배열) → 관대 파싱.
  - `au_tga_artg.active_ingredients` : JSONB 배열. 문자열 또는 {name/ingredient} 혼용.
    `inn_normalized` 컬럼 **없음** — Python 레벨 필터.

모든 함수는 SELECT 전용 (INSERT/UPDATE 금지). 네트워크 실패 시 빈 리스트 반환.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# crawler.db.supabase_insert.get_supabase_client 재사용 — 신규 클라이언트 만들지 않음
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # upharma-au/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from crawler.db.supabase_insert import get_supabase_client  # noqa: E402

_PRODUCTS_PATH = _PROJECT_ROOT / "crawler" / "au_products.json"


def _load_products() -> list[dict[str, Any]]:
    return json.loads(_PRODUCTS_PATH.read_text(encoding="utf-8"))["products"]


def _walk_jsonb_orgs(orgs: Any) -> list[dict[str, Any]]:
    """au_pbs_raw.endpoint_organisations JSONB 에서 organisation 딕트 리스트 추출.

    PBS API 응답 구조가 버전마다 다양 (data/organisations/items/results 중 하나 또는
    직접 배열). 관대하게 탐색.
    """
    if isinstance(orgs, list):
        return [o for o in orgs if isinstance(o, dict)]
    if not isinstance(orgs, dict):
        return []
    for key in ("data", "organisations", "items", "results"):
        v = orgs.get(key)
        if isinstance(v, list):
            return [o for o in v if isinstance(o, dict)]
    # 단일 org dict 인 경우
    if any(k in orgs for k in ("manufacturer_name", "organisation_name", "name", "sponsor_name")):
        return [orgs]
    return []


def fetch_tga_sponsors_for_product(product_id: str) -> list[dict[str, Any]]:
    """au_products.tga_sponsors(JSONB) 에서 TGA 스폰서 빈도 집계.

    주의: v2 스키마 테이블명은 `au_products` (구 `australia` 아님). FK = `product_id`.
    같은 product_id 가 여러 행 있을 수 있으므로 전부 합산 후 빈도로 artg_count 추정.
    """
    if not product_id:
        return []
    try:
        sb = get_supabase_client()
        rows = (
            sb.table("au_products")
            .select("tga_sponsors")
            .eq("product_id", product_id)
            .execute()
            .data
        ) or []
    except Exception as exc:
        print(f"[db_sponsors] TGA 조회 실패 ({product_id}): {exc}", flush=True)
        return []

    counts: dict[str, int] = {}
    for r in rows:
        for s in (r.get("tga_sponsors") or []):
            if isinstance(s, str) and s.strip():
                counts[s.strip()] = counts.get(s.strip(), 0) + 1
    return [
        {"name": k, "source": "tga", "artg_count": v}
        for k, v in counts.items()
    ]


def fetch_pbs_sponsors_for_product(product_id: str) -> list[dict[str, Any]]:
    """PBS 스폰서 — 2 소스 병합 (v2 스키마 기준):
      (1) au_products.originator_sponsor  — TEXT, 오리지네이터 1건
      (2) au_pbs_raw.endpoint_organisations — JSONB, PBS /organisations 응답 전체

    JSONB 파싱은 _walk_jsonb_orgs + 여러 키 이름 (manufacturer_name /
    organisation_name / name / sponsor_name) 탐색. 회사명 set 으로 dedup.
    """
    if not product_id:
        return []
    sb = get_supabase_client()
    sponsors: set[str] = set()

    # (1) au_products.originator_sponsor
    try:
        op = (
            sb.table("au_products")
            .select("originator_sponsor")
            .eq("product_id", product_id)
            .execute()
            .data
        ) or []
        for r in op:
            s = (r.get("originator_sponsor") or "").strip()
            if s:
                sponsors.add(s)
    except Exception as exc:
        print(f"[db_sponsors] originator_sponsor 조회 실패 ({product_id}): {exc}", flush=True)

    # (2) au_pbs_raw.endpoint_organisations (JSONB)
    try:
        raw_rows = (
            sb.table("au_pbs_raw")
            .select("endpoint_organisations")
            .eq("product_id", product_id)
            .execute()
            .data
        ) or []
        for r in raw_rows:
            orgs_blob = r.get("endpoint_organisations")
            for item in _walk_jsonb_orgs(orgs_blob):
                for key in ("manufacturer_name", "organisation_name", "name", "sponsor_name"):
                    v = item.get(key)
                    if isinstance(v, str) and v.strip():
                        sponsors.add(v.strip())
                        break
    except Exception as exc:
        print(f"[db_sponsors] endpoint_organisations 조회 실패 ({product_id}): {exc}", flush=True)

    return [
        {"name": s, "source": "pbs", "pbs_listed": True}
        for s in sorted(sponsors)
    ]


def _active_ingredient_str(ing: Any) -> str:
    """au_tga_artg.active_ingredients 의 원소(문자열 또는 dict)를 소문자 str 로."""
    if isinstance(ing, dict):
        return str(ing.get("name") or ing.get("ingredient") or "").lower()
    return str(ing or "").lower()


def fetch_sponsors_by_inn(
    inn_components: list[str],
    similar_inns: list[str] | None,
) -> list[dict[str, Any]]:
    """성분 기반 매칭 — au_tga_artg 에서 target_inns 성분 포함 행의 sponsor 전부.

    v2 스키마 주의: `au_tga_artg.inn_normalized` 컬럼 없음. `active_ingredients`
    JSONB 배열(문자열 또는 {name:...} 혼용) → 전체 스캔 후 Python 필터링.

    복합제·대체약 모두 커버 (inn_components + similar_inns 합집합).
    규모: au_tga_artg 행 수만 가정. 문제 시 sponsor 기준 페이지네이션 추가 필요.
    """
    target_inns = {
        (i or "").lower().strip()
        for i in list(inn_components or []) + list(similar_inns or [])
        if i
    }
    if not target_inns:
        return []

    try:
        sb = get_supabase_client()
        rows = (
            sb.table("au_tga_artg")
            .select("sponsor_name, artg_id, active_ingredients")
            .execute()
            .data
        ) or []
    except Exception as exc:
        print(f"[db_sponsors] au_tga_artg 전체 조회 실패: {exc}", flush=True)
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        sponsor_name = (r.get("sponsor_name") or "").strip()
        if not sponsor_name:
            continue
        ings = r.get("active_ingredients") or []
        if not isinstance(ings, list):
            continue
        for ing in ings:
            ing_str = _active_ingredient_str(ing)
            if not ing_str:
                continue
            for inn in target_inns:
                if inn and inn in ing_str:
                    out.append({
                        "name": sponsor_name,
                        "artg_id": r.get("artg_id"),
                        "matched_ingredient": ing_str,
                        "matched_inn": inn,
                        "source": "tga_inn_match",
                    })
                    break  # 같은 ARTG 에서 같은 inn 중복 매칭 방지
    return out


def fetch_artg_matrix_for_buyer(buyer_name: str) -> dict[str, set[str]]:
    """특정 바이어의 ARTG 전체 매트릭스 — Stage 1 4-case 분류용.

    Returns: {artg_id: {active_ingredient_lower, ...}}

    au_tga_artg.sponsor_name ILIKE 로 1차 범위 축소 후 active_ingredients JSONB
    파싱. 빈 buyer_name 은 빈 dict.
    """
    if not buyer_name or not buyer_name.strip():
        return {}

    try:
        sb = get_supabase_client()
        rows = (
            sb.table("au_tga_artg")
            .select("artg_id, active_ingredients")
            .ilike("sponsor_name", f"%{buyer_name.strip()}%")
            .execute()
            .data
        ) or []
    except Exception as exc:
        print(f"[db_sponsors] artg_matrix 조회 실패 ({buyer_name!r}): {exc}", flush=True)
        return {}

    matrix: dict[str, set[str]] = {}
    for r in rows:
        artg = r.get("artg_id") or "_unknown"
        ings = r.get("active_ingredients") or []
        bucket = matrix.setdefault(artg, set())
        if not isinstance(ings, list):
            continue
        for ing in ings:
            s = _active_ingredient_str(ing).strip()
            if s:
                bucket.add(s)
    return matrix
