"""Haiku 어댑터 실측 검증 — 일회성 테스트. 삭제해도 무관."""
import os, sys
from pathlib import Path
from dotenv import load_dotenv

# 메인 프로젝트 경로 고정 (worktree 에서 실행 시 대비)
_ENV_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/.env")
_UPHARMA_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/upharma-au")
load_dotenv(_ENV_PATH, override=True)  # Windows 환경변수 빈값 덮어쓰기
sys.path.insert(0, str(_UPHARMA_PATH))
print(f"[dotenv] loaded from {_ENV_PATH}: key_present={bool(os.environ.get('ANTHROPIC_API_KEY'))}")

from buyer_discovery.validators.haiku_cross_check import (
    extract_therapy_from_description,
    validate_revenue,
)

print("=" * 70)
print("TEST 1: Astellas description → 치료영역 추출")
print("=" * 70)
astellas_desc = (
    "Astellas is a global pharmaceutical company, working at the forefront "
    "of healthcare change to turn innovative science into value for patients. "
    "Making a positive impact on patients' lives is the purpose that drives "
    "us. Astellas in Australia focuses on women's health (Veoza for "
    "vasomotor symptoms), overactive bladder (Betmiga), oncology, "
    "transplantation, and immunology. With a global footprint in more than "
    "70 countries, Astellas is committed to delivering innovative medicines "
    "that address unmet medical needs."
)
result = extract_therapy_from_description(
    "Astellas Pharma Australia Pty Ltd",
    astellas_desc,
    represented_brands=["Veoza (Fezolinetant)", "Betmiga (Mirabegron)"],
)
print(f"  areas_en: {result['areas_en']}")
print(f"  areas_kr: {result['areas_kr']}")
print(f"  confidence: {result['confidence']}")
print(f"  reasoning: {result['reasoning']}")

print()
print("=" * 70)
print("TEST 2: 짧은 description → 빈 결과 기대")
print("=" * 70)
short = extract_therapy_from_description(
    "Xyz Co",
    "We sell medicines.",
)
print(f"  areas_en: {short['areas_en']}")
print(f"  reasoning: {short['reasoning']}")

print()
print("=" * 70)
print("TEST 3: Perplexity 매출 응답 검증 — 대기업 케이스")
print("=" * 70)
mock_pplx_roche = {
    "parsed": {
        "rank": "TOP 10",
        "reasoning": "Roche is one of largest pharmaceutical companies in Australia",
        "sources": [
            "https://www.roche.com.au/about-roche-australia.html",
            "https://www.iqvia.com/insights/australia-pharma-top-20-2024",
        ],
    },
    "citations": [
        "https://www.roche.com.au/about-roche-australia.html",
        "https://www.iqvia.com/insights/australia-pharma-top-20-2024",
    ],
}
local_roche = {
    "sources": ["ma"],
    "tga_artg_count": 0,  # Roche 는 survivors 에서 tga_artg_count=0 (MA 기반)
    "is_ma": True,
    "is_gbma": False,
    "is_gpce": False,
}
mfr_roche = {"has_factory": True, "address": "Sydney NSW"}
result = validate_revenue("Roche", mock_pplx_roche, local_roche, mfr_roche)
print(f"  rank: {result['rank']}")
print(f"  score: {result['score']}")
print(f"  confidence: {result['confidence']}")
print(f"  reasoning: {result['reasoning']}")

print()
print("=" * 70)
print("TEST 4: Perplexity 결과 없음 (에러) → fallback")
print("=" * 70)
bad = validate_revenue("NoData", {"error": "timeout"}, local_roche, None)
print(f"  rank: {bad['rank']}, score: {bad['score']}, conf: {bad['confidence']}")
print(f"  reasoning: {bad['reasoning']}")
