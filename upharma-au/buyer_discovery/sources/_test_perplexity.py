"""Perplexity 응답 실측 — AbbVie, Roche 두 회사 원본 응답 확인."""
import sys, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(r"C:/Users/user/Desktop/Australia_1st_logic/.env", override=True)
sys.path.insert(0, r"C:/Users/user/Desktop/Australia_1st_logic/upharma-au")

from buyer_discovery.sources.perplexity_adapter import query_therapeutic_areas, query_revenue

for company in ["AbbVie Australia", "Roche Australia"]:
    print("=" * 70)
    print(f"[{company}] query_therapeutic_areas")
    print("=" * 70)
    r = query_therapeutic_areas(company)
    if "error" in r:
        print(f"  ERROR: {r['error']}")
        continue
    print(f"  model: {r.get('model')}")
    print(f"  raw_answer (first 500 chars):")
    print(f"    {(r.get('raw_answer') or '')[:500]}")
    print(f"  parsed: {r.get('parsed')}")
    print(f"  citations ({len(r.get('citations') or [])}):")
    for c in (r.get('citations') or [])[:3]:
        print(f"    · {c}")
    print()

    print("=" * 70)
    print(f"[{company}] query_revenue")
    print("=" * 70)
    r2 = query_revenue(company)
    if "error" in r2:
        print(f"  ERROR: {r2['error']}")
        continue
    print(f"  raw_answer (first 400 chars): {(r2.get('raw_answer') or '')[:400]}")
    print(f"  parsed: {r2.get('parsed')}")
    print()
