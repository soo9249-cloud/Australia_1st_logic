# PBS 공개 API v3: schedule_code 확보 후 성분 기준 filter 시도, 실패 시 페이지 순회로 매칭한다.

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
import httpx

# 프로젝트 루트 .env 로드(cwd와 무관하게 상위 경로 탐색)
_env_dir = Path(__file__).resolve().parent
for _ in range(8):
    _env_file = _env_dir / ".env"
    if _env_file.is_file():
        load_dotenv(_env_file)
        break
    if _env_dir.parent == _env_dir:
        load_dotenv()
        break
    _env_dir = _env_dir.parent
else:
    load_dotenv()

_BASE = "https://data-api.health.gov.au/pbs/api/v3"
_MAX_FALLBACK_PAGES = 10


def _headers() -> dict[str, str]:
    return {"Subscription-Key": os.environ["PBS_SUBSCRIPTION_KEY"]}


def _pbs_public_url(pbs_code: str | None) -> str:
    if pbs_code:
        return f"https://www.pbs.gov.au/browse/medicine?search={quote(str(pbs_code))}"
    return "https://www.pbs.gov.au/browse/medicine"


def _empty_dict() -> dict[str, Any]:
    return {
        "pbs_item_code": None,
        "pbs_listed": False,
        "pbs_price_aud": None,
        "pbs_source_url": _pbs_public_url(None),
        "restriction_text": None,
    }


def _price_from_row(row: dict[str, Any]) -> float | None:
    for key in ("determined_price", "claimed_price"):
        v = row.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _restriction_from_row(row: dict[str, Any]) -> str | None:
    for key in ("restriction_text", "note_text", "caution_text"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _row_matches_ingredient(row: dict[str, Any], needle_lower: str) -> bool:
    if not needle_lower:
        return False
    parts: list[str] = []
    for key in ("drug_name", "li_drug_name"):
        v = row.get(key)
        if isinstance(v, str):
            parts.append(v.lower())
    blob = " ".join(parts)
    return needle_lower in blob


def _row_to_result(row: dict[str, Any]) -> dict[str, Any]:
    raw_code = row.get("pbs_code")
    pbs_item = str(raw_code) if raw_code is not None else None
    return {
        "pbs_item_code": pbs_item,
        "pbs_listed": True,
        "pbs_price_aud": _price_from_row(row),
        "pbs_source_url": _pbs_public_url(pbs_item),
        "restriction_text": _restriction_from_row(row),
        "pbs_dpmq": float(row["claimed_price"]) if row.get("claimed_price") else None,
        "pbs_determined_price": float(row["determined_price"]) if row.get("determined_price") else None,
        "pbs_pack_size": row.get("pack_size"),
        "pbs_pricing_quantity": row.get("pricing_quantity"),
        "pbs_benefit_type": row.get("benefit_type_code"),
        "pbs_program_code": row.get("program_code"),
        "pbs_brand_name": row.get("brand_name"),
        "pbs_innovator": row.get("innovator_indicator"),
        "pbs_first_listed_date": row.get("first_listed_date"),
        "pbs_repeats": row.get("number_of_repeats"),
        "pbs_formulary": row.get("formulary"),
        "pbs_restriction": row.get("benefit_type_code") == "S",
    }


def fetch_latest_schedule_code() -> str | None:
    """schedules 응답 data[0].schedule_code를 문자열로 반환한다."""
    try:
        time.sleep(21)
        r = httpx.get(f"{_BASE}/schedules", headers=_headers(), timeout=10)
        if r.status_code != 200:
            return None
        payload = r.json()
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        code = data[0].get("schedule_code")
        return str(code) if code is not None else None
    except Exception:
        return None


def _filter_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows or not rows[0].get("pbs_listed"):
        return rows
    result: list[dict[str, Any]] = []

    # 오리지널
    originals = [r for r in rows if r.get("pbs_innovator") == "Y"]
    if originals:
        result.append(originals[0])

    # 제네릭 최저가
    generics = [r for r in rows if r.get("pbs_innovator") == "N"]
    if generics:
        cheapest = min(generics, key=lambda x: x.get("pbs_price_aud") or 999)
        result.append(cheapest)

    # 최신 등재
    latest = max(rows, key=lambda x: x.get("pbs_first_listed_date") or "")
    if latest not in result:
        result.append(latest)

    out = result if result else rows
    pbs_total_brands = len(set(r.get("pbs_brand_name") for r in rows))
    for d in out:
        d["pbs_total_brands"] = pbs_total_brands
    return out


def fetch_pbs_by_ingredient(ingredient: str) -> list[dict[str, Any]]:
    """ingredient를 반영해 PBS 품목을 찾는다. 매칭 행마다 dict 하나, 없으면 [_empty_dict()]."""
    from utils.inn_normalize import normalize_inn

    ing_raw = (ingredient or "").strip()
    if not ing_raw:
        return [_empty_dict()]
    ing = normalize_inn(ing_raw)  # PubChem으로 WHO INN 자동 정규화
    needle = ing.lower()
    out_empty = _empty_dict()

    try:
        schedule = fetch_latest_schedule_code()
        if not schedule:
            return [out_empty]

        # 1차: drug_name 파라미터 (빈 결과·비-200이면 fallback)
        params_primary: dict[str, Any] = {
            "schedule_code": schedule,
            "drug_name": ing,
            "page": 1,
            "limit": 10,
        }
        try:
            time.sleep(21)
            r1 = httpx.get(
                f"{_BASE}/items",
                params=params_primary,
                headers=_headers(),
                timeout=10,
            )
        except Exception:
            r1 = None

        primary_matched: list[dict[str, Any]] = []
        if r1 is not None and r1.status_code == 200:
            try:
                payload = r1.json()
            except Exception:
                payload = {}
            rows = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and _row_matches_ingredient(row, needle):
                        primary_matched.append(row)

        if primary_matched:
            return _filter_results([_row_to_result(r) for r in primary_matched])

        # fallback: filter 없이 page 순차, drug_name / li_drug_name 부분일치(소문자)
        fallback_matched: list[dict[str, Any]] = []
        for page in range(1, _MAX_FALLBACK_PAGES + 1):
            try:
                time.sleep(21)
                r2 = httpx.get(
                    f"{_BASE}/items",
                    params={
                        "schedule_code": schedule,
                        "page": page,
                        "limit": 100,
                    },
                    headers=_headers(),
                    timeout=10,
                )
            except Exception:
                break
            if r2.status_code != 200:
                break
            payload = r2.json()
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if isinstance(row, dict) and _row_matches_ingredient(row, needle):
                    fallback_matched.append(row)
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            total = meta.get("total_records")
            if isinstance(total, int) and page * 100 >= total:
                break

        if fallback_matched:
            return _filter_results([_row_to_result(r) for r in fallback_matched])

        return [out_empty]
    except Exception:
        return [out_empty]


def fetch_pbs_multi(ingredients: list[str]) -> list[dict[str, Any]]:
    """여러 성분에 대해 fetch_pbs_by_ingredient 결과를 이어 붙인다."""
    if not ingredients:
        return [_empty_dict()]
    acc: list[dict[str, Any]] = []
    for raw in ingredients:
        acc.extend(fetch_pbs_by_ingredient(raw))
    return acc if acc else [_empty_dict()]
