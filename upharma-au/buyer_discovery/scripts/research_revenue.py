"""매출 규모 조사 — 65개 survivors 전부.

파이프라인:
  1. Perplexity query_revenue(company_name)  → {rank, reasoning, sources}
  2. Haiku validate_revenue(perplexity, local_evidence, manufacturer_info)
     → {rank, score, confidence, reasoning, evidence_urls}

로컬 증거 3-소스:
  · survivors_expanded_v3.json 의 sources / tga_artg_count / is_ma / is_gbma / is_gpce
  · survivors_manufacturer_match.json 의 has_factory / state / address
  · (암묵) au_tga_artg 의 ARTG 실제 등록 수

결과: seeds/company_revenue.json
  - 주 1회 수동 재실행 (Jisoo 요청)
  - 비용: 65회 × $0.005 (Perplexity) + 65회 × $0.001 (Haiku) ≈ $0.40

실행:
  python C:/Users/user/Desktop/Australia_1st_logic/upharma-au/buyer_discovery/scripts/research_revenue.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/.env")
_UPHARMA_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/upharma-au")

load_dotenv(_ENV_PATH, override=True)
sys.path.insert(0, str(_UPHARMA_PATH))

from buyer_discovery.sources.perplexity_adapter import query_revenue  # noqa: E402
from buyer_discovery.validators.haiku_cross_check import validate_revenue  # noqa: E402

_SEEDS = _UPHARMA_PATH / "buyer_discovery" / "seeds"
_SURVIVORS_JSON = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/survivors_expanded_v3.json"
)
_MFR_MATCH_JSON = _SEEDS / "survivors_manufacturer_match.json"
_OUT_JSON = _SEEDS / "company_revenue.json"


def _build_local_evidence(row: dict) -> dict:
    """survivors_expanded_v3.json 의 buyer row 에서 로컬 증거 추출."""
    ev = row.get("evidence") or {}
    return {
        "sources": row.get("sources") or [],
        "tga_artg_count": int(ev.get("tga_artg_count") or 0),
        "pbs_listed_count": int(ev.get("pbs_listed_count") or 0),
        "is_ma": bool(ev.get("is_ma_member")),
        "is_gbma": bool(ev.get("is_gbma_member")),
        "is_gpce": bool(ev.get("is_gpce_exhibitor")),
    }


def _load_manufacturer_match() -> dict:
    """survivors_manufacturer_match.json 의 matches 로드. 없으면 빈 dict."""
    if not _MFR_MATCH_JSON.exists():
        return {}
    data = json.loads(_MFR_MATCH_JSON.read_text(encoding="utf-8"))
    return data.get("matches") or {}


def main() -> None:
    print("[revenue] 입력 로드", flush=True)
    survivors = json.loads(_SURVIVORS_JSON.read_text(encoding="utf-8"))
    mfr_matches = _load_manufacturer_match()

    buyers = survivors.get("buyers") or {}
    total = len(buyers)
    results: dict[str, dict] = {}
    rank_counts: Counter = Counter()

    for i, (canon_key, row) in enumerate(buyers.items(), 1):
        name = row.get("canonical_name") or canon_key
        print(f"[revenue] ({i}/{total}) {name}", flush=True)

        # 1) Perplexity 매출 조사
        px = query_revenue(name)
        if "error" in px:
            print(f"  └ [Perplexity] 실패: {px.get('error')}", flush=True)

        # 2) Haiku 교차검증
        local_ev = _build_local_evidence(row)
        mfr = mfr_matches.get(canon_key)
        validated = validate_revenue(name, px, local_ev, mfr)

        rank = validated["rank"]
        score = validated["score"]
        rank_counts[rank] += 1

        results[canon_key] = {
            "canonical_name": name,
            "rank": rank,
            "score": score,
            "confidence": validated["confidence"],
            "reasoning": validated["reasoning"],
            "evidence_urls": validated["evidence_urls"],
            "perplexity_raw": px.get("raw_answer"),
            "local_evidence": local_ev,
            "has_factory": bool(mfr and mfr.get("has_factory")),
        }
        print(
            f"  └ rank={rank} score={score} conf={validated['confidence']:.2f}",
            flush=True,
        )

        # rate limit 방지 (Perplexity + Haiku 번갈아)
        time.sleep(1.5)

    payload = {
        "_meta": {
            "description": (
                "바이어 발굴 매출 규모 조사. Perplexity sonar-pro + Haiku 교차검증. "
                "주 1회 수동 재실행. 등급: TOP 5 (100점) → TOP 50 (50점) → niche (30) → unknown (0)."
            ),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "rank_distribution": dict(rank_counts),
        },
        "revenue": results,
    }
    _OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(flush=True)
    print(f"[revenue] 완료: {_OUT_JSON}", flush=True)
    print(f"[revenue] 등급 분포: {dict(rank_counts)}", flush=True)


if __name__ == "__main__":
    main()
