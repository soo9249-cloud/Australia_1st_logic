"""Stage 2 — 5지표 가중치 점수화 + 품목별 1~10 순위 + au_buyers UPSERT.

5지표 (Jisoo 확정 가중치):
  ① psi_sales_scale      ×35%  매출 규모        (company_revenue.json)
  ② psi_pipeline         ×25%  동일 성분 경험   (survivors ingredient_case + tga_artg_count)
  ③ psi_manufacturing    ×20%  호주 공장 보유   (survivors_manufacturer_match.json)
  ④ psi_import_exp       ×10%  수입 경험        (tga_artg_count + GPCE 참가)
  ⑤ psi_pharmacy_chain   ×10%  약국 체인 운영   (기본 0 — 추후 보강)

psi_total = Σ(지표 × 가중치), 0~100 스케일

품목별 TOP 10 구성 로직 (A/B/C 티어 계단식):
  A티어: 해당 품목 INN 직접 매칭 (ingredient_case ∈ {A_competitor, B_ideal_buyer, C_partial})
  B티어: 품목 치료영역 ∈ company_categories.areas_en 매칭
  C티어: 나머지 (점수 최상위만 보충)

실행:
  python C:/Users/user/Desktop/Australia_1st_logic/upharma-au/buyer_discovery/stage2_scoring.py

전제: ALTER TABLE au_buyers 실행 완료 (alter_au_buyers.sql).
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/.env")
_UPHARMA_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/upharma-au")

load_dotenv(_ENV_PATH, override=True)
sys.path.insert(0, str(_UPHARMA_PATH))

from crawler.db.supabase_insert import get_supabase_client  # noqa: E402

_SEEDS = _UPHARMA_PATH / "buyer_discovery" / "seeds"
_AU_PRODUCTS_JSON = _UPHARMA_PATH / "crawler" / "au_products.json"
_SURVIVORS_JSON = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/survivors_expanded_v4.json"
)
_CATEGORIES_JSON = _SEEDS / "company_categories.json"
_REVENUE_JSON = _SEEDS / "company_revenue.json"
_MFR_MATCH_JSON = _SEEDS / "survivors_manufacturer_match.json"
# 45개 딥리서치 수동 정정본 — Documents 쪽이 진짜 완성본 (seeds/ 는 스텁만 있음)
_HARDCODE_JSON = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/au_buyers_hardcode.json"
)
_OUT_REPORT_JSON = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/stage2_scoring_report.json"
)


# ──────────────────────────────────────────────────────────────────────
# 지표 점수 산출
# ──────────────────────────────────────────────────────────────────────

_INGREDIENT_CASE_SCORE: dict[str, int] = {
    "B_ideal_buyer": 100,
    "A_competitor":   70,
    "C_partial":      50,
    "D_none":          0,
}


def _score_pipeline(
    product_id: str,
    survivor_row: dict,
) -> int:
    """psi_pipeline — 해당 품목의 성분 경험 점수.

    ingredient_case (B/A/C/D) 기본값 + tga_artg_count 가중(최대 +20).
    """
    ev = survivor_row.get("evidence") or {}
    case_map = ev.get("ingredient_case_per_product") or {}
    case = case_map.get(product_id, "D_none")
    base = _INGREDIENT_CASE_SCORE.get(case, 0)
    artg_count = int(ev.get("tga_artg_count") or 0)
    bonus = min(artg_count * 2, 20)
    return min(base + bonus, 100)


def _score_manufacturing(canon_key: str, mfr_matches: dict) -> int:
    """psi_manufacturing — 호주 공장 보유 = 100, 없음 = 0."""
    m = mfr_matches.get(canon_key)
    if m and m.get("has_factory"):
        return 100
    return 0


def _hardcode_rank_to_score(rank_text: str) -> int:
    """hardcode 의 annual_revenue_rank 문자열 → 0~100 점수.

    매핑 규칙 (au_buyers_hardcode.json 수기 포맷 기준):
      "TOP 5 (...)"    → 100
      "TOP 10 (...)"   →  85
      "TOP 20 (...)"   →  70
      "TOP 50 (...)"   →  50
      "niche" / "순위 밖" / "mid-tier" → 30
      그 외 → 30 (보수적 기본값)
    """
    text = (rank_text or "").upper().strip()
    if "???" in text or not text:
        return 0
    # 구체적 숫자 매칭 (예: "TOP 5", "TOP 10", "TOP 20", "TOP 50")
    import re as _re
    m = _re.search(r"TOP\s*(\d+)", text)
    if m:
        n = int(m.group(1))
        if n <= 5:   return 100
        if n <= 10:  return 85
        if n <= 20:  return 70
        if n <= 50:  return 50
        return 30
    if any(kw in text for kw in ("NICHE", "SMALL", "MID-TIER", "MID TIER", "순위 밖", "SPECIALTY")):
        return 30
    return 30  # 알 수 없으면 중간값


def _score_sales(
    canon_key: str,
    revenue_doc: dict,
    hardcoded_buyers: dict,
) -> tuple[int, str, float, str]:
    """psi_sales_scale — hardcode 우선 3단계 폴백.

    우선순위:
      1. au_buyers_hardcode.json (Jisoo 수기 검증 45개) — confidence 1.0
      2. company_revenue.json (Perplexity + Haiku 교차검증) — Haiku 반환 confidence
      3. unknown (0점)

    반환: (score, rank_text, confidence, source)
      source ∈ {"hardcode", "perplexity_haiku", "none"}
    """
    # 1. hardcode 우선 (Jisoo 수기 45개)
    hc = (hardcoded_buyers or {}).get(canon_key) or {}
    hc_rank = hc.get("annual_revenue_rank") or ""
    if hc_rank and "???" not in hc_rank:
        return _hardcode_rank_to_score(hc_rank), hc_rank, 1.0, "hardcode"

    # 2. Perplexity + Haiku 교차검증 결과
    rev = (revenue_doc.get("revenue") or {}).get(canon_key)
    if rev and rev.get("rank") and rev.get("rank") != "unknown":
        return (
            int(rev.get("score") or 0),
            rev.get("rank") or "unknown",
            float(rev.get("confidence") or 0.0),
            "perplexity_haiku",
        )

    # 3. 데이터 없음
    return 0, "unknown", 0.0, "none"


def _score_import_exp(survivor_row: dict) -> int:
    """psi_import_exp — ARTG 카운트 + GPCE 참가 조합.

    TGA ARTG ≥10 → 70점, ≥5 → 50, ≥1 → 30, 0 → 0
    + GPCE 참가시 +15
    + PBS 등재시 +15
    최대 100.
    """
    ev = survivor_row.get("evidence") or {}
    artg = int(ev.get("tga_artg_count") or 0)
    if artg >= 10:
        base = 70
    elif artg >= 5:
        base = 50
    elif artg >= 1:
        base = 30
    else:
        base = 0
    if ev.get("is_gpce_exhibitor"):
        base += 15
    if (ev.get("pbs_listed_count") or 0) > 0:
        base += 15
    return min(base, 100)


def _score_pharmacy_chain(canon_key: str) -> int:
    """psi_pharmacy_chain — 미구현 (추후 Perplexity 조사 결과 연동).

    현 구현: 전부 0. Chemist Warehouse, Priceline 등 체인 운영 회사만 예외.
    """
    known_chain_owners = {
        "sigma_healthcare": 100,  # Chemist Warehouse 모회사
        "chemist_warehouse": 100,
        "ramsay_pharmacy": 80,
        "epharmacy": 60,
    }
    return known_chain_owners.get(canon_key, 0)


# ──────────────────────────────────────────────────────────────────────
# 가중치 합산
# ──────────────────────────────────────────────────────────────────────

_WEIGHTS = {
    "sales":        0.35,
    "pipeline":     0.25,
    "manufacturing": 0.20,
    "import_exp":   0.10,
    "pharmacy":     0.10,
}


def compute_psi_total(scores: dict[str, int]) -> int:
    total = (
        scores["sales"] * _WEIGHTS["sales"]
        + scores["pipeline"] * _WEIGHTS["pipeline"]
        + scores["manufacturing"] * _WEIGHTS["manufacturing"]
        + scores["import_exp"] * _WEIGHTS["import_exp"]
        + scores["pharmacy"] * _WEIGHTS["pharmacy"]
    )
    return int(round(total))


# ──────────────────────────────────────────────────────────────────────
# A/B/C 티어 분류
# ──────────────────────────────────────────────────────────────────────

def classify_tier(
    product_id: str,
    product_therapy_en: set[str],
    canon_key: str,
    survivor_row: dict,
    categories_doc: dict,
) -> str:
    """A/B/C 티어 라벨.

    A: ingredient_case ∈ {B_ideal_buyer, A_competitor, C_partial}
    B: 위 D_none 이지만 therapeutic_categories ∩ product_therapy_en ≠ ∅
    C: 나머지 (survivors 에 포함되었으나 품목·카테고리 매칭 모두 없음)

    주의: survivors_expanded_v3.json 의 buyer row 에는 canonical_key 필드가 없음
          (바깥 dict 키로만 존재) → 호출측에서 명시적으로 canon_key 전달 필수.
    """
    ev = survivor_row.get("evidence") or {}
    case_map = ev.get("ingredient_case_per_product") or {}
    case = case_map.get(product_id, "D_none")
    if case != "D_none":
        return "A"

    cat_entry = (categories_doc.get("categories") or {}).get(canon_key) or {}
    buyer_areas = set(cat_entry.get("areas_en") or [])
    if buyer_areas & product_therapy_en:
        return "B"
    return "C"


# ──────────────────────────────────────────────────────────────────────
# 로드 헬퍼
# ──────────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    if not path.exists():
        print(f"[stage2] 경고: 파일 없음 {path}", flush=True)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _inn_to_therapy_en(inn: str, inn_map: dict) -> set[str]:
    """단일 INN → 표준 영문 치료영역 set.

    inn_to_therapy.json 의 소문자 영문을 Haiku 표준 어휘 (CamelCase) 로 매핑.
    """
    from buyer_discovery.scripts.tag_therapeutic_areas import _standardize_en

    entry = inn_map.get(inn)
    if not entry:
        return set()
    out: set[str] = set()
    for en in entry.get("en") or []:
        std = _standardize_en(en)
        if std:
            out.add(std)
    return out


# ──────────────────────────────────────────────────────────────────────
# 메인 파이프라인
# ──────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    print(f"[stage2] dry_run={dry_run} — 입력 로드", flush=True)

    survivors = _load(_SURVIVORS_JSON)
    categories = _load(_CATEGORIES_JSON)
    revenue = _load(_REVENUE_JSON)
    mfr_data = _load(_MFR_MATCH_JSON)
    mfr_matches = mfr_data.get("matches") or {}
    hardcoded = _load(_HARDCODE_JSON)
    hardcoded_buyers = (hardcoded.get("buyers") or {}) if hardcoded else {}

    products_doc = json.loads(_AU_PRODUCTS_JSON.read_text(encoding="utf-8"))
    products = products_doc.get("products") or []

    inn_map_doc = _load(_SEEDS / "inn_to_therapy.json")
    inn_map = {k: v for k, v in inn_map_doc.items() if not k.startswith("_") and isinstance(v, dict)}

    buyers = (survivors.get("buyers") or {})

    # ========================================================================
    # 1. 각 바이어에 대해 5지표 점수 전체 산출 (품목 무관)
    # ========================================================================
    #    단, psi_pipeline 는 품목마다 다름 → 품목별 재계산
    #    sales/manufacturing/import/pharmacy 는 품목 무관 (회사 고유)
    buyer_static_scores: dict[str, dict] = {}
    for canon_key, row in buyers.items():
        sales_score, sales_rank, sales_conf, sales_source = _score_sales(
            canon_key, revenue, hardcoded_buyers
        )
        mfg_score = _score_manufacturing(canon_key, mfr_matches)
        imp_score = _score_import_exp(row)
        ph_score = _score_pharmacy_chain(canon_key)
        buyer_static_scores[canon_key] = {
            "sales": sales_score,
            "sales_rank": sales_rank,
            "sales_confidence": sales_conf,
            "sales_source": sales_source,  # 'hardcode' / 'perplexity_haiku' / 'none'
            "manufacturing": mfg_score,
            "import_exp": imp_score,
            "pharmacy": ph_score,
        }

    # ========================================================================
    # 2. 품목별 TOP 10 선별 + A/B/C 티어 + psi_total
    # ========================================================================
    product_rankings: dict[str, list] = {}
    total_upserts = 0
    tier_dist: Counter = Counter()

    for p in products:
        pid = p["product_id"]
        inns = (p.get("inn_components") or []) + (p.get("similar_inns") or [])
        # 품목 치료영역 (표준 영문 set)
        product_therapy_en: set[str] = set()
        for inn in inns:
            # strip_inn_salt → inn_map 에서 조회 위해 다양한 표기 시도
            for key in (inn, inn.lower().strip()):
                product_therapy_en |= _inn_to_therapy_en(key, inn_map)
        # alias 보정: hydroxyurea → hydroxycarbamide 경우 대응
        from crawler.utils.inn_normalize import strip_inn_salt
        for inn in inns:
            k = strip_inn_salt(inn)
            product_therapy_en |= _inn_to_therapy_en(k, inn_map)

        # 각 바이어에 대해 티어 + psi_total
        scored: list[dict] = []
        for canon_key, row in buyers.items():
            tier = classify_tier(pid, product_therapy_en, canon_key, row, categories)
            pipeline_score = _score_pipeline(pid, row)

            static = buyer_static_scores[canon_key]
            scores = {
                "sales": static["sales"],
                "pipeline": pipeline_score,
                "manufacturing": static["manufacturing"],
                "import_exp": static["import_exp"],
                "pharmacy": static["pharmacy"],
            }
            psi_total = compute_psi_total(scores)

            # 바이어 치료영역
            cat_entry = (categories.get("categories") or {}).get(canon_key) or {}
            # 하드코딩 시트 (딥리서치 45 완성본)
            hc = hardcoded_buyers.get(canon_key) or {}
            factory_hc = hc.get("factory") or {}

            # annual_revenue_rank 는 이미 _score_sales 에서 hardcode 우선 결정됨
            # static["sales_rank"] 가 곧 최종 표시용 문자열.

            scored.append({
                "canonical_key": canon_key,
                "canonical_name": row.get("canonical_name"),
                "tier": tier,
                "psi_total": psi_total,
                "scores": scores,
                "sales_rank_text": static["sales_rank"],
                "sales_source": static["sales_source"],
                "annual_revenue_rank": static["sales_rank"],
                "therapeutic_categories_en": cat_entry.get("areas_en") or [],
                "therapeutic_categories_kr": cat_entry.get("areas_kr") or [],
                "has_au_factory": (
                    factory_hc.get("has") == "Y"
                    if factory_hc
                    else bool(mfr_matches.get(canon_key, {}).get("has_factory"))
                ),
                "factory_locations": (
                    factory_hc.get("locations")
                    if factory_hc.get("locations")
                    else (
                        [mfr_matches.get(canon_key, {}).get("state")]
                        if mfr_matches.get(canon_key, {}).get("has_factory")
                        else []
                    )
                ),
                "notes": hc.get("notes"),
                "evidence_urls": (
                    (revenue.get("revenue") or {}).get(canon_key, {}).get("evidence_urls") or []
                ),
                "is_ma": bool((row.get("evidence") or {}).get("is_ma_member")),
                "is_gbma": bool((row.get("evidence") or {}).get("is_gbma_member")),
                "is_gpce": bool((row.get("evidence") or {}).get("is_gpce_exhibitor")),
                "tga_artg_count": int((row.get("evidence") or {}).get("tga_artg_count") or 0),
            })

        # A티어 우선 → B티어 → C티어 순 안에서 psi_total 내림차순
        tier_order = {"A": 0, "B": 1, "C": 2}
        scored.sort(key=lambda x: (tier_order[x["tier"]], -x["psi_total"]))
        top10 = scored[:10]

        # 순위 부여 (1~10)
        for rnk, entry in enumerate(top10, 1):
            entry["rank"] = rnk
            tier_dist[entry["tier"]] += 1

        product_rankings[pid] = top10

        print(
            f"[stage2] {pid} | "
            f"A={sum(1 for x in top10 if x['tier']=='A')} "
            f"B={sum(1 for x in top10 if x['tier']=='B')} "
            f"C={sum(1 for x in top10 if x['tier']=='C')}",
            flush=True,
        )

    # ========================================================================
    # 3. 리포트 저장
    # ========================================================================
    report = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_products": len(products),
            "weights": _WEIGHTS,
            "tier_distribution": dict(tier_dist),
        },
        "rankings": product_rankings,
    }
    _OUT_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    _OUT_REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[stage2] 리포트 저장: {_OUT_REPORT_JSON}", flush=True)

    # ========================================================================
    # 4. au_buyers UPSERT (DB)
    # ========================================================================
    if dry_run:
        print("[stage2] DRY-RUN — au_buyers 저장 생략", flush=True)
        return

    sb = get_supabase_client()
    for pid, top10 in product_rankings.items():
        # 기존 (product_id, rank) 행 삭제 후 재삽입 — 이전 TOP10 쓰레기 제거 효과
        try:
            sb.table("au_buyers").delete().eq("product_id", pid).execute()
        except Exception as exc:
            print(f"[stage2] {pid} 기존 삭제 실패: {exc}", flush=True)

        rows_to_insert: list[dict] = []
        for entry in top10:
            scores = entry["scores"]
            rows_to_insert.append({
                "product_id": pid,
                "rank": entry["rank"],
                "company_name": entry["canonical_name"],
                "company_key": entry["canonical_key"],
                "psi_sales_scale":   scores["sales"],
                "psi_pipeline":      scores["pipeline"],
                "psi_manufacturing": scores["manufacturing"],
                "psi_import_exp":    scores["import_exp"],
                "psi_pharmacy_chain": scores["pharmacy"],
                "psi_total":         entry["psi_total"],
                "annual_revenue_rank": entry["annual_revenue_rank"],
                "has_au_factory":    "Y" if entry["has_au_factory"] else "N",
                "factory_locations": entry["factory_locations"],
                "therapeutic_categories": entry["therapeutic_categories_en"],
                "is_ma_member":      entry["is_ma"],
                "is_gbma_member":    entry["is_gbma"],
                "is_gpce_exhibitor": entry["is_gpce"],
                "tga_artg_count":    entry["tga_artg_count"],
                "source_flags": [
                    f for f in [
                        "tier_" + entry["tier"],
                        "ma" if entry["is_ma"] else None,
                        "gbma" if entry["is_gbma"] else None,
                        "gpce" if entry["is_gpce"] else None,
                    ] if f
                ],
                "evidence_urls":     entry["evidence_urls"],
                "reasoning":         entry.get("notes") or f"tier={entry['tier']}",
                "notes":             entry.get("notes"),
                "last_researched_at": datetime.now(timezone.utc).isoformat(),
            })
        try:
            r = sb.table("au_buyers").insert(rows_to_insert).execute()
            inserted = len(r.data or [])
            total_upserts += inserted
            print(f"[stage2] {pid}: {inserted} rows inserted", flush=True)
        except Exception as exc:
            print(f"[stage2] {pid} INSERT 실패: {exc}", flush=True)

    print(flush=True)
    print(f"[stage2] 총 {total_upserts} rows UPSERT", flush=True)
    print(f"[stage2] 티어 분포: {dict(tier_dist)}", flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 저장 없이 리포트만")
    args = ap.parse_args()
    main(dry_run=args.dry_run)
