"""치료영역 자동 태깅 — 3단계 폴백 로직.

대상: survivors_expanded_v3.json 의 65 canonical_key.
출력: buyer_discovery/seeds/company_categories.json

우선순위 (비용·신뢰도 순):
  1. INN 매핑 (무료, 가장 정확)
     · au_tga_artg 에서 해당 sponsor 의 모든 ARTG active_ingredients 수집
     · 각 INN 을 inn_to_therapy.json 로 치료영역 변환
     · 가장 많이 등장한 영역들 집계
     · 사용 조건: TGA ARTG ≥ 1건 있는 회사

  2. GPCE description + brands → Haiku 요약 (~$0.002/회)
     · 전시사 풀에서 description 가져와 Haiku 로 치료영역 추출
     · 사용 조건: INN 매핑 실패 AND GPCE description 존재

  3. Perplexity query_therapeutic_areas (~$0.005/회, 최후 수단)
     · 위 둘 다 실패한 회사 (주로 MA 회원 중 GPCE 비참가)

결과: seeds/company_categories.json (월 1회 수동 갱신용 시드)

실행:
  python C:/Users/user/Desktop/Australia_1st_logic/upharma-au/buyer_discovery/scripts/tag_therapeutic_areas.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────
# 경로·환경 고정
# ──────────────────────────────────────────────────────────────────────
_ENV_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/.env")
_UPHARMA_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/upharma-au")

load_dotenv(_ENV_PATH, override=True)
sys.path.insert(0, str(_UPHARMA_PATH))

# 임포트는 path 세팅 후
from buyer_discovery.sources.db_sponsors import fetch_artg_matrix_for_buyer  # noqa: E402
from buyer_discovery.sources.perplexity_adapter import query_therapeutic_areas  # noqa: E402
from buyer_discovery.validators.haiku_cross_check import (  # noqa: E402
    _KO_MAP,
    _STANDARD_THERAPY_AREAS_EN,
    extract_therapy_from_description,
)
from buyer_discovery.sources.gpce_exhibitors import fetch_gpce_exhibitors  # noqa: E402
from crawler.utils.inn_normalize import extract_inn_set  # noqa: E402

_SEEDS = _UPHARMA_PATH / "buyer_discovery" / "seeds"
_SURVIVORS_JSON = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/survivors_expanded_v3.json"
)
_OUT_JSON = _SEEDS / "company_categories.json"


# ──────────────────────────────────────────────────────────────────────
# Utility: inn_to_therapy 로드 + INN → 치료영역 변환
# ──────────────────────────────────────────────────────────────────────

def _load_inn_to_therapy() -> dict[str, dict]:
    with open(_SEEDS / "inn_to_therapy.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


# inn_to_therapy 의 'cardiovascular' 등 소문자 영역명 → 표준 영문 (Haiku 어휘) 로 매핑
_INN_THERAPY_TO_STANDARD: dict[str, str] = {
    "cardiovascular": "Cardiovascular",
    "cardio": "Cardiovascular",
    "heart": "Cardiovascular",
    "respiratory": "Respiratory",
    "pulmonary": "Respiratory",
    "asthma": "Respiratory",
    "oncology": "Oncology",
    "cancer": "Oncology",
    "tumor": "Oncology",
    "hematology": "Hematology",
    "haematology": "Hematology",
    "blood": "Hematology",
    "gastrointestinal": "Gastrointestinal",
    "gastric": "Gastrointestinal",
    "digestive": "Gastrointestinal",
    "imaging": "Imaging_Contrast",
    "contrast": "Imaging_Contrast",
    "radiology": "Imaging_Contrast",
    "cns": "CNS_Neurology",
    "neurology": "CNS_Neurology",
    "neuroscience": "CNS_Neurology",
    "neurological": "CNS_Neurology",
    "brain": "CNS_Neurology",
    "alzheimer": "CNS_Neurology",
    "parkinson": "CNS_Neurology",
    "immunology": "Immunology",
    "immune": "Immunology",
    "autoimmune": "Immunology",
    "rheumatology": "Rheumatology",
    "arthritis": "Rheumatology",
    "infectious": "Infectious_Disease",
    "antimicrobial": "Infectious_Disease",
    "antiviral": "Infectious_Disease",
    "virology": "Infectious_Disease",
    "hiv": "Infectious_Disease",
    "vaccine": "Vaccine",
    "vaccination": "Vaccine",
    "immunization": "Vaccine",
    "diabetes": "Diabetes_Endocrine",
    "endocrine": "Diabetes_Endocrine",
    "metabolic": "Diabetes_Endocrine",
    "dermatology": "Dermatology",
    "skin": "Dermatology",
    "ophthalmology": "Ophthalmology",
    "eye": "Ophthalmology",
    "ocular": "Ophthalmology",
    "urology": "Urology",
    "urological": "Urology",
    "bladder": "Urology",
    "women": "Womens_Health",
    "womens": "Womens_Health",
    "gynecology": "Womens_Health",
    "gynaecology": "Womens_Health",
    "menopause": "Womens_Health",
    "contraception": "Womens_Health",
    "men": "Mens_Health",
    "mens": "Mens_Health",
    "andrology": "Mens_Health",
    "rare": "Rare_Disease",
    "orphan": "Rare_Disease",
    "pain": "Pain_Anesthesia",
    "analgesic": "Pain_Anesthesia",
    "anesthesia": "Pain_Anesthesia",
    "anaesthesia": "Pain_Anesthesia",
    "allergy": "Allergy",
    "allergic": "Allergy",
    "otc": "OTC_Consumer_Health",
    "consumer": "OTC_Consumer_Health",
    "non-prescription": "OTC_Consumer_Health",
    "nutrition": "Nutrition",
    "nutritional": "Nutrition",
    "dietary": "Nutrition",
    "device": "Medical_Device",
    "medical device": "Medical_Device",
    "psychiatry": "Psychiatry",
    "mental health": "Psychiatry",
    "depression": "Psychiatry",
}


def _standardize_en(label: str) -> str | None:
    """inn_to_therapy 소문자 영문 → 표준 영문 매핑. 매칭 실패 시 None."""
    key = (label or "").lower().strip()
    if key in _INN_THERAPY_TO_STANDARD:
        return _INN_THERAPY_TO_STANDARD[key]
    # 부분 매칭
    for needle, std in _INN_THERAPY_TO_STANDARD.items():
        if needle in key:
            return std
    return None


# ──────────────────────────────────────────────────────────────────────
# 방법 1: INN 매핑
# ──────────────────────────────────────────────────────────────────────

def tag_via_inn(
    buyer_name: str,
    inn_to_therapy: dict[str, dict],
) -> dict:
    """au_tga_artg 의 sponsor ARTG 들 → INN → 치료영역 집계.

    반환 빈 areas 면 "매핑 실패" 로 간주 (다음 단계 폴백).
    """
    matrix = fetch_artg_matrix_for_buyer(buyer_name)
    if not matrix:
        return {"areas_en": [], "areas_kr": [], "method": None, "source_inns": []}

    # 모든 ARTG 의 원본 INN 문자열 합집합
    # 주의: fetch_artg_matrix_for_buyer 는 active_ingredients 를 소문자 strip 만 함.
    #       "fluticasone propionate" 같은 salt 붙은 원본 문자열이 반환됨.
    #       → extract_inn_set 으로 salt·수화물 제거 + WHO INN alias 변환 필수.
    raw_strings: list[str] = []
    for ings in matrix.values():
        raw_strings.extend(s for s in ings if s)
    all_inns: set[str] = set(extract_inn_set(*raw_strings))

    # INN → 치료영역 집계 (Counter)
    area_counts: Counter = Counter()
    matched_inns: list[str] = []
    for inn in all_inns:
        therapy = inn_to_therapy.get(inn)
        if not therapy:
            continue
        matched_inns.append(inn)
        for en_label in therapy.get("en") or []:
            std = _standardize_en(en_label)
            if std:
                area_counts[std] += 1

    if not area_counts:
        return {"areas_en": [], "areas_kr": [], "method": None, "source_inns": []}

    # 상위 4개만
    areas_en = [a for a, _ in area_counts.most_common(4)]
    areas_kr = [_KO_MAP.get(a, a) for a in areas_en]

    return {
        "areas_en": areas_en,
        "areas_kr": areas_kr,
        "method": "inn_mapping",
        "confidence": 0.95,
        "source_inns": sorted(matched_inns),
        "reasoning": f"au_tga_artg ARTG {len(matrix)}건의 성분 {sorted(all_inns)}",
    }


# ──────────────────────────────────────────────────────────────────────
# 방법 2: GPCE description → Haiku
# ──────────────────────────────────────────────────────────────────────

def tag_via_gpce_description(
    buyer_name: str,
    gpce_index: dict[str, dict],
) -> dict:
    """GPCE 전시사 풀에서 이름 매칭 → description/brands → Haiku 로 영역 추출."""
    # canonical_name 소문자 substring 매칭
    low = (buyer_name or "").lower()
    hit = None
    for key, row in gpce_index.items():
        if key in low or low in key:
            hit = row
            break
    if not hit:
        return {"areas_en": [], "areas_kr": [], "method": None}

    desc = hit.get("description") or ""
    brands = hit.get("represented_brands") or []
    if len(desc) < 20:
        return {"areas_en": [], "areas_kr": [], "method": None}

    result = extract_therapy_from_description(buyer_name, desc, brands)
    if not result.get("areas_en"):
        return {"areas_en": [], "areas_kr": [], "method": None}
    return {
        "areas_en": result["areas_en"],
        "areas_kr": result["areas_kr"],
        "method": "haiku_description",
        "confidence": result["confidence"],
        "reasoning": result.get("reasoning"),
    }


# ──────────────────────────────────────────────────────────────────────
# 방법 3: Perplexity (최후 수단)
# ──────────────────────────────────────────────────────────────────────

def tag_via_perplexity(buyer_name: str) -> dict:
    """Perplexity query_therapeutic_areas 최후 수단."""
    result = query_therapeutic_areas(buyer_name)
    if "error" in result or not result.get("parsed"):
        return {"areas_en": [], "areas_kr": [], "method": None}

    parsed = result["parsed"]
    raw_areas = parsed.get("areas") or []

    # 표준 어휘로 변환
    std_areas: list[str] = []
    for a in raw_areas:
        if a in _STANDARD_THERAPY_AREAS_EN:
            std_areas.append(a)
        else:
            mapped = _standardize_en(a)
            if mapped and mapped not in std_areas:
                std_areas.append(mapped)
    if not std_areas:
        return {"areas_en": [], "areas_kr": [], "method": None}

    return {
        "areas_en": std_areas,
        "areas_kr": [_KO_MAP.get(a, a) for a in std_areas],
        "method": "perplexity",
        "confidence": 0.7,
        "reasoning": str(parsed.get("reasoning") or "")[:300],
        "evidence_urls": (result.get("citations") or [])[:5],
    }


# ──────────────────────────────────────────────────────────────────────
# 메인 파이프라인
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. 입력 로드
    print("[tag] survivors + GPCE + inn_to_therapy 로드", flush=True)
    survivors = json.loads(_SURVIVORS_JSON.read_text(encoding="utf-8"))
    inn_map = _load_inn_to_therapy()

    # GPCE 전시사 인덱스 (소문자 이름 → row)
    print("[tag] GPCE 크롤 실행 (수 초)", flush=True)
    gpce_rows = fetch_gpce_exhibitors()
    gpce_index: dict[str, dict] = {}
    for row in gpce_rows:
        nm = (row.get("name") or "").strip().lower()
        if nm and nm not in gpce_index:
            gpce_index[nm] = row
    print(f"[tag] GPCE 인덱스: {len(gpce_index)} unique", flush=True)

    # 2. 각 survivor 에 대해 3단계 폴백
    results: dict[str, dict] = {}
    method_counts: Counter = Counter()

    buyers = survivors.get("buyers") or {}
    total = len(buyers)
    for i, (canon_key, row) in enumerate(buyers.items(), 1):
        name = row.get("canonical_name") or canon_key
        print(f"[tag] ({i}/{total}) {name}", flush=True)

        # 방법 1: INN 매핑
        inn_result = tag_via_inn(name, inn_map)
        if inn_result.get("areas_en"):
            results[canon_key] = {
                "canonical_name": name,
                **inn_result,
            }
            method_counts["inn_mapping"] += 1
            print(
                f"  └ [INN] {inn_result['areas_kr']} (from {len(inn_result['source_inns'])} INN)",
                flush=True,
            )
            continue

        # 방법 2: GPCE description → Haiku
        haiku_result = tag_via_gpce_description(name, gpce_index)
        if haiku_result.get("areas_en"):
            results[canon_key] = {
                "canonical_name": name,
                **haiku_result,
            }
            method_counts["haiku_description"] += 1
            print(f"  └ [Haiku/GPCE] {haiku_result['areas_kr']}", flush=True)
            continue

        # 방법 3: Perplexity 최후
        print(f"  └ Perplexity 호출 (최후 수단)", flush=True)
        ppx_result = tag_via_perplexity(name)
        if ppx_result.get("areas_en"):
            results[canon_key] = {
                "canonical_name": name,
                **ppx_result,
            }
            method_counts["perplexity"] += 1
            print(f"     → {ppx_result['areas_kr']}", flush=True)
        else:
            results[canon_key] = {
                "canonical_name": name,
                "areas_en": [],
                "areas_kr": [],
                "method": "unknown",
                "confidence": 0.0,
                "reasoning": "3단계 모두 실패",
            }
            method_counts["unknown"] += 1
            print(f"     → unknown", flush=True)

    # 3. 저장
    payload = {
        "_meta": {
            "description": (
                "바이어 발굴 Stage 2 치료영역 태깅 결과. "
                "3단계 폴백 (INN 매핑 / Haiku description / Perplexity). "
                "주 1회 수동 재실행 권장 — tag_therapeutic_areas.py."
            ),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "method_breakdown": dict(method_counts),
        },
        "categories": results,
    }
    _OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(flush=True)
    print(f"[tag] 완료: {_OUT_JSON}", flush=True)
    print(f"[tag] 방법별 분포: {dict(method_counts)}", flush=True)


if __name__ == "__main__":
    main()
