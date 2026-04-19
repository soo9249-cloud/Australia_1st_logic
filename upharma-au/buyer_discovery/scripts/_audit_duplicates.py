"""65개 survivors 내 잠재 중복 회사 전수 감사."""
import json, re
from pathlib import Path

v3 = json.loads(Path(r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/survivors_expanded_v3.json").read_text(encoding="utf-8"))
buyers = v3.get("buyers") or {}

# 각 회사명을 토큰화 (공백 분리)
def tokens(name: str) -> set[str]:
    """소문자, 기업접미사 제거 후 남은 토큰 set."""
    s = (name or "").lower()
    s = re.sub(r"\b(pty|ltd|pty\.|ltd\.|limited|inc|inc\.|corporation|corp|co\.|gmbh|plc|australia|au|anz|pharmaceuticals|pharmaceutical|pharma|healthcare|pacific)\b", " ", s)
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) >= 3}

# canonical_key, canonical_name, tokens 리스트
entries = []
for k, b in buyers.items():
    nm = b.get("canonical_name") or k
    entries.append({
        "key": k,
        "name": nm,
        "tokens": tokens(nm),
    })

# 각 쌍에 대해 토큰 교집합이 1개 이상이면 후보
print("토큰 겹침 있는 회사 쌍 (잠재 중복):")
print("-" * 70)
candidates = []
for i in range(len(entries)):
    for j in range(i+1, len(entries)):
        a = entries[i]; b = entries[j]
        common = a["tokens"] & b["tokens"]
        if not common: continue
        # 너무 일반적인 단어 제외
        common -= {"the", "and", "for", "group", "international", "holdings", "science"}
        if not common: continue
        candidates.append((a, b, common))

# 결과 출력 (공통 토큰 수 내림차순)
candidates.sort(key=lambda x: -len(x[2]))
for a, b, common in candidates:
    print(f"  공통 {sorted(common)}:")
    print(f"    · {a['key']:40s} = {a['name']}")
    print(f"    · {b['key']:40s} = {b['name']}")
    print()
