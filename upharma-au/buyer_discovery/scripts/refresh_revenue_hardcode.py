"""69개 hardcode 바이어 매출 재조사 — 기준 통일 + 정확 금액·연도·출처.

목적 (2026-04-20 Jisoo 지시 "A+B · 정확하게 계속 쓸 수 있게"):
  · 45 기존 + 19 신규 + 5 distributor = 69개 전부 Perplexity 재조사
  · 기준: 호주 처방약 매출 · 2024 우선 · ASX annual report/IBISWorld 출처
  · 결과 포맷 통일: "TOP N (카테고리) · 2024 AUD XXXM · 출처"

hardcode annual_revenue_rank 필드를 통합 문자열로 재작성. ALTER TABLE 불필요.

실행:
  python -m buyer_discovery.scripts.refresh_revenue_hardcode
  (또는 절대경로로 python <path>)

결과:
  · seeds/au_buyers_hardcode.json  덮어쓰기
  · Documents/.../au_buyers_hardcode.json 동시 백업
  · seeds/refresh_revenue_report.json 에 불일치 리포트 (기존 값 vs 새 값)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_BUYER_DIR = Path(__file__).resolve().parents[1]
_UPHARMA = _BUYER_DIR.parent
_PROJECT_ROOT = _UPHARMA.parent

load_dotenv(_PROJECT_ROOT / ".env", override=True)
sys.path.insert(0, str(_UPHARMA))

from buyer_discovery.sources.perplexity_adapter import query_revenue  # type: ignore
from buyer_discovery.validators.haiku_cross_check import validate_revenue  # type: ignore

_SEEDS = _BUYER_DIR / "seeds"
_HARDCODE_SEEDS = _SEEDS / "au_buyers_hardcode.json"
_HARDCODE_DOCS = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/au_buyers_hardcode.json"
)
_REPORT = _SEEDS / "refresh_revenue_report.json"


def build_annual_revenue_text(
    parsed: dict,
    old_rank: str,
    canonical_name: str,
) -> str:
    """Perplexity parsed → annual_revenue_rank 문자열 조립.

    예시:
      · 공개사·금액 있음: "TOP 10 (오리지널) · 2024 AUD 637M · Roche Products Pty"
      · 비공개·추정: "TOP 20 (제네릭) 추정 · 비공개 (Pty Ltd)"
      · unknown: "unknown"
    """
    rank = (parsed.get("rank") or "unknown").strip()
    amt = parsed.get("revenue_aud_millions")
    year = parsed.get("revenue_year")
    is_public = parsed.get("is_public_disclosed")

    parts: list[str] = []
    # 기존 rank 의 카테고리 주석 유지 (예: "TOP 10 (오리지널)")
    if rank != "unknown" and "(" in old_rank:
        # 기존 문자열의 괄호 부분 유지
        rank_with_cat = old_rank.split("·")[0].strip()
        parts.append(rank_with_cat if rank_with_cat else rank)
    else:
        parts.append(rank)

    if amt is not None and isinstance(amt, (int, float)):
        yr = year or "?"
        # AUD 포맷
        if amt >= 1000:
            amt_str = f"{amt / 1000:.1f}B"
        else:
            amt_str = f"{int(amt)}M"
        parts.append(f"{yr} AUD {amt_str}")
    elif is_public is False:
        parts.append("비공개 (Pty Ltd)")

    return " · ".join(p for p in parts if p and p != "unknown") or "unknown"


def compare_ranks(old: str, new: str) -> str:
    """기존 값 vs 새 값 비교. 일치/경미 불일치/중대 불일치 판정."""
    old_l = (old or "").lower()
    new_l = (new or "").lower()
    if old_l == new_l:
        return "identical"

    # 주요 등급만 추출
    def extract_rank(s: str) -> str:
        s = s.lower()
        for key in ("top 5", "top 10", "top 20", "top 50", "niche", "순위 밖"):
            if key in s:
                return key
        return "unknown"

    old_rank = extract_rank(old_l)
    new_rank = extract_rank(new_l)
    if old_rank == new_rank:
        return "same_rank"  # 등급 같지만 포맷/금액 다름
    return "different_rank"  # 등급 불일치


def main() -> None:
    if not _HARDCODE_SEEDS.is_file():
        print(f"[X] hardcode seeds 없음: {_HARDCODE_SEEDS}", flush=True)
        sys.exit(1)

    hc = json.loads(_HARDCODE_SEEDS.read_text(encoding="utf-8"))
    buyers = hc.setdefault("buyers", {})
    non_template = {k: v for k, v in buyers.items() if not k.startswith("_")}
    print(f"[refresh] hardcode 총 {len(non_template)} 바이어 재조사 시작\n", flush=True)

    identical = 0
    same_rank = 0
    different_rank = 0
    failed = 0
    report_entries: list[dict] = []

    for i, (canon_key, entry) in enumerate(non_template.items(), 1):
        if not isinstance(entry, dict):
            continue
        name = entry.get("canonical_name") or canon_key
        old_rank = entry.get("annual_revenue_rank") or ""
        print(f"[{i:2d}/{len(non_template)}] {name}", flush=True)
        time.sleep(1.2)  # rate-limit 여유

        px = query_revenue(name)
        if "error" in px or not px.get("parsed"):
            print(f"  ❌ Perplexity 실패: {px.get('error') or 'parsed 없음'}", flush=True)
            failed += 1
            report_entries.append({
                "canonical_key": canon_key,
                "canonical_name": name,
                "old": old_rank,
                "new": None,
                "status": "failed",
            })
            continue

        parsed = px["parsed"]
        new_rank = build_annual_revenue_text(parsed, old_rank, name)

        # 비교
        status = compare_ranks(old_rank, new_rank)
        if status == "identical":
            identical += 1
        elif status == "same_rank":
            same_rank += 1
        else:
            different_rank += 1

        # 업데이트 (distributor 는 기존 값 보존 — 이미 정확)
        if entry.get("role") != "distributor":
            entry["annual_revenue_rank"] = new_rank
            # 추가 메타 (optional)
            entry["revenue_aud_millions"] = parsed.get("revenue_aud_millions")
            entry["revenue_year"] = parsed.get("revenue_year")
            entry["revenue_sources"] = (parsed.get("sources") or px.get("citations") or [])[:3]

        print(f"  OLD: {old_rank}", flush=True)
        print(f"  NEW: {new_rank}", flush=True)
        print(f"  status: {status}", flush=True)

        report_entries.append({
            "canonical_key": canon_key,
            "canonical_name": name,
            "old": old_rank,
            "new": new_rank,
            "status": status,
            "sources_count": len(parsed.get("sources") or []),
            "is_public_disclosed": parsed.get("is_public_disclosed"),
        })

    # _meta 업데이트
    hc.setdefault("_meta", {})
    hc["_meta"]["last_revenue_refresh"] = "2026-04-20"
    hc["_meta"]["total"] = len(buyers)

    # 양쪽 저장
    payload = json.dumps(hc, ensure_ascii=False, indent=2)
    _HARDCODE_SEEDS.write_text(payload, encoding="utf-8")
    try:
        _HARDCODE_DOCS.parent.mkdir(parents=True, exist_ok=True)
        _HARDCODE_DOCS.write_text(payload, encoding="utf-8")
    except Exception as exc:
        print(f"[refresh] Documents 백업 실패 (무시): {exc}", flush=True)

    # 리포트 저장
    report = {
        "_meta": {
            "generated_at": "2026-04-20",
            "total": len(non_template),
            "identical": identical,
            "same_rank_different_format": same_rank,
            "different_rank_mismatch": different_rank,
            "failed": failed,
        },
        "entries": report_entries,
    }
    _REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 72, flush=True)
    print(f"재조사 완료 — 총 {len(non_template)} 바이어", flush=True)
    print(f"  · 일치 (identical)           : {identical}", flush=True)
    print(f"  · 등급 같음·포맷 차이 (same_rank) : {same_rank}", flush=True)
    print(f"  · ⚠️  등급 불일치 (different_rank) : {different_rank}", flush=True)
    print(f"  · 실패                         : {failed}", flush=True)
    print(f"\n리포트: {_REPORT}", flush=True)
    print(f"hardcode 저장: {_HARDCODE_SEEDS}", flush=True)


if __name__ == "__main__":
    main()
