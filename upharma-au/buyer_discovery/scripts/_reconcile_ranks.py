"""15건 등급 불일치 조정:
   OLD (Gemini 딥리서치) = "TOP 10 (오리지널)"
   NEW (Perplexity)      = "비공개 (Pty Ltd)" 만 있음
→ 결합: "TOP 10 (오리지널) · 비공개 (Pty Ltd)"

NEW 에 구체 금액 (AUD) 있으면 NEW 그대로.
"""
import json
import re
from pathlib import Path

_SEEDS = Path(__file__).resolve().parents[1] / "seeds"
_HC = _SEEDS / "au_buyers_hardcode.json"
_REPORT = _SEEDS / "refresh_revenue_report.json"

report = json.loads(_REPORT.read_text(encoding="utf-8"))
hc = json.loads(_HC.read_text(encoding="utf-8"))
buyers = hc.get("buyers") or {}

fixed = 0
kept_new = 0
for entry in report.get("entries") or []:
    if entry.get("status") != "different_rank":
        continue
    key = entry["canonical_key"]
    old = entry.get("old") or ""
    new = entry.get("new") or ""
    if key not in buyers:
        continue

    # NEW 에 구체 금액 (AUD) 가 있으면 NEW 우선
    has_amount = bool(re.search(r"AUD\s*\d+[A-Z]?", new))
    if has_amount:
        # OLD 의 등급 괄호 (예: "(오리지널)") + NEW 금액 병합
        m_old_rank = re.search(r"TOP\s*\d+\s*\([^)]+\)", old)
        if m_old_rank:
            merged = f"{m_old_rank.group()} · {new}"
        else:
            merged = new
        buyers[key]["annual_revenue_rank"] = merged
        print(f"✔️ {entry['canonical_name']:40s} → {merged}")
        kept_new += 1
        continue

    # NEW 가 "비공개" 위주 → OLD 등급 복원 + 비공개 표기 병합
    # 단 OLD 가 "unknown" 또는 "순위 밖" 이면 NEW 수용 (새 정보)
    old_l = old.lower()
    if "unknown" in old_l or "순위 밖" in old:
        buyers[key]["annual_revenue_rank"] = new
        print(f"→ {entry['canonical_name']:40s} (OLD unknown) → NEW: {new}")
        continue

    # OLD 에 TOP/niche 등급이 있으면 복원
    merged = f"{old.strip(' ·')} · 비공개 (Pty Ltd)"
    buyers[key]["annual_revenue_rank"] = merged
    print(f"복원 {entry['canonical_name']:40s} → {merged}")
    fixed += 1

# 저장
payload = json.dumps(hc, ensure_ascii=False, indent=2)
_HC.write_text(payload, encoding="utf-8")
print(f"\n복원: {fixed} · NEW 금액 병합: {kept_new} · 총 수정: {fixed + kept_new}")
print(f"저장: {_HC}")

# Documents 백업
try:
    docs = Path(r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/au_buyers_hardcode.json")
    docs.write_text(payload, encoding="utf-8")
    print(f"백업: {docs}")
except Exception as exc:
    print(f"Documents 백업 실패 (무시): {exc}")
