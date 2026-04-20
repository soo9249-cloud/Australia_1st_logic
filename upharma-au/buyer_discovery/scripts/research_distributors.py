"""호주 약국 체인·도매 핵심 5개 회사 Perplexity 조사 → hardcode 병합.

대상 (Jisoo 2026-04-20 지시):
  1. Sigma Healthcare Limited (ASX:SIG)         — Chemist Warehouse 모회사 · Amcal · Guardian
  2. EBOS Group Limited (ASX/NZX:EBO)           — Symbion 도매 · TerryWhite Chemmart
  3. Wesfarmers Health (ASX:WES)                 — API · Priceline Pharmacy
  4. Chemist Warehouse Group Holdings (비공개)   — Sigma 와 2024 합병 진행
  5. National Pharmacies Cooperative (SA)        — 협동조합

왜 수기 등록이 필요한가:
  기존 6 크롤 소스 (TGA·PBS·MA·GBMA·GPCE·tga_inn_match) 는 의약품 제조사/스폰서 중심.
  도매·유통 채널은 허가가 다른 범주라 크롤에 안 잡힘. 바이어 발굴 완결을 위해 수기 등록 필수.

결과: seeds/au_buyers_hardcode.json 의 buyers 에 5개 엔트리 추가.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/.env")
_UPHARMA_PATH = Path(r"C:/Users/user/Desktop/Australia_1st_logic/upharma-au")
load_dotenv(_ENV_PATH, override=True)
sys.path.insert(0, str(_UPHARMA_PATH))

from buyer_discovery.sources.perplexity_adapter import _call_perplexity  # type: ignore

_HARDCODE_SEEDS = _UPHARMA_PATH / "buyer_discovery" / "seeds" / "au_buyers_hardcode.json"
_HARDCODE_DOCS = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/au_buyers_hardcode.json"
)

# 조사 대상 — canonical_key, 정식명, 힌트
_TARGETS = [
    ("sigma_healthcare",
     "Sigma Healthcare Limited",
     "ASX:SIG, Chemist Warehouse 모회사 (2024 합병), Amcal · Guardian · PharmaSave 체인 운영"),
    ("ebos_group",
     "EBOS Group Limited",
     "ASX:EBO · NZX:EBO, Symbion 도매, TerryWhite Chemmart 체인, HPS Pharmacies 병원약국"),
    ("wesfarmers_health",
     "Wesfarmers Health",
     "ASX:WES 산하 Health Division, API (Australian Pharmaceutical Industries) 인수, Priceline Pharmacy 체인"),
    ("chemist_warehouse_group",
     "Chemist Warehouse Group Holdings",
     "비공개 Pty Ltd, 호주 최대 단일 약국 체인, 2024 Sigma 합병 진행"),
    ("national_pharmacies",
     "National Pharmacies",
     "SA (남호주) 기반 약국 협동조합, 처방약·헬스케어 유통"),
]


def build_prompt(name: str, hint: str) -> str:
    return (
        f"Company: {name}\n"
        f"Context: {hint}\n\n"
        "Return JSON with EXACTLY these keys:\n"
        "  - 'canonical_name': official legal name\n"
        "  - 'asx_code': stock ticker or null\n"
        "  - 'annual_revenue_rank_ko': Korean string like 'TOP 3 (도매·체인)' · no JSON nesting\n"
        "  - 'annual_revenue_aud_note': '2024 FY AUD X.XB' format with source\n"
        "  - 'factory': {'has': 'Y/N/unknown', 'count': int, 'locations': [str]}\n"
        "  - 'pharmacy_chains': list of chain brand names owned/operated\n"
        "  - 'website': official URL\n"
        "  - 'notes_ko': 1-2 Korean sentences evaluating as a buyer for "
        "Korea United Pharm (한국유나이티드제약) 's Australian export (finished Rx / OTC drugs)\n"
        "  - 'sources': list of at least 2 source URLs\n\n"
        "Rules: valid JSON only. If unknown, use null / [] / 'unknown'. "
        "Revenue figures must cite ASX annual report or reputable news."
    )


def call_once(name: str, hint: str) -> dict:
    """Perplexity 1회 호출 + 결과 반환."""
    system = (
        "You are a pharmaceutical distribution market analyst specializing "
        "in the Australian pharmacy retail and wholesale channels. "
        "Answer ONLY in valid JSON. Cite sources."
    )
    user = build_prompt(name, hint)
    result = _call_perplexity(system, user, max_tokens=900, temperature=0.1)
    return result


def to_hardcode_entry(canon_key: str, px_result: dict) -> dict | None:
    """Perplexity 결과 → hardcode entry 포맷으로 변환."""
    if "error" in px_result:
        print(f"  ❌ Perplexity 오류: {px_result['error']}", flush=True)
        return None
    parsed = px_result.get("parsed")
    if not isinstance(parsed, dict):
        raw = (px_result.get("raw_answer") or "")[:200]
        print(f"  ❌ JSON 파싱 실패. raw={raw}", flush=True)
        return None

    # annual_revenue_rank 필드에 등급 + 금액 통합
    rank_ko = parsed.get("annual_revenue_rank_ko") or ""
    rev_note = parsed.get("annual_revenue_aud_note") or ""
    if rev_note:
        annual = f"{rank_ko} · {rev_note}".strip(" ·")
    else:
        annual = rank_ko or "unknown"

    return {
        "canonical_name": parsed.get("canonical_name") or canon_key,
        "annual_revenue_rank": annual,
        "factory": parsed.get("factory") or {"has": "unknown", "count": 0, "locations": []},
        "notes": parsed.get("notes_ko") or "",
        # 추가 메타 — hardcode 가 doc-level 로 쓰는 정보 (Stage 2 가 읽을 수도)
        "role": "distributor",
        "asx_code": parsed.get("asx_code"),
        "pharmacy_chains": parsed.get("pharmacy_chains") or [],
        "website": parsed.get("website"),
        "evidence_urls": parsed.get("sources") or px_result.get("citations") or [],
    }


def main() -> None:
    # 어느 hardcode 를 업데이트할지: Documents 버전이 진본. seeds/ 는 사본.
    target_path = _HARDCODE_DOCS if _HARDCODE_DOCS.is_file() else _HARDCODE_SEEDS
    print(f"[distributors] hardcode 로드: {target_path}", flush=True)
    doc = json.loads(target_path.read_text(encoding="utf-8"))
    buyers = doc.setdefault("buyers", {})

    added = 0
    skipped: list[str] = []
    for canon_key, name, hint in _TARGETS:
        print(f"\n>>> Perplexity 조사: {name}", flush=True)
        time.sleep(1)  # rate-limit 여유
        res = call_once(name, hint)
        entry = to_hardcode_entry(canon_key, res)
        if entry is None:
            skipped.append(canon_key)
            continue
        buyers[canon_key] = entry
        added += 1
        print(f"  ✅ {entry['canonical_name']}", flush=True)
        print(f"     매출: {entry['annual_revenue_rank']}", flush=True)
        print(f"     공장: {entry['factory']}", flush=True)
        print(f"     체인: {entry.get('pharmacy_chains')}", flush=True)
        print(f"     URL 근거: {len(entry.get('evidence_urls') or [])} 개", flush=True)

    # _meta 업데이트
    doc.setdefault("_meta", {})
    doc["_meta"]["last_updated"] = "2026-04-20"
    doc["_meta"]["total"] = len(buyers)
    doc["_meta"]["distributors_added"] = added

    # 양쪽 저장 (Documents 진본 + seeds 사본)
    payload_text = json.dumps(doc, ensure_ascii=False, indent=2)
    target_path.write_text(payload_text, encoding="utf-8")
    print(f"\n저장: {target_path}", flush=True)

    # seeds/ 에도 복사 (이식성·Git)
    _HARDCODE_SEEDS.parent.mkdir(parents=True, exist_ok=True)
    _HARDCODE_SEEDS.write_text(payload_text, encoding="utf-8")
    print(f"복사: {_HARDCODE_SEEDS}", flush=True)

    print(f"\n총 {len(buyers)} buyers (+{added} distributors 추가)", flush=True)
    if skipped:
        print(f"실패 {len(skipped)}: {skipped}", flush=True)


if __name__ == "__main__":
    main()
