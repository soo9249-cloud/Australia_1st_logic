"""TGA Manufacturer Licence XML → seeds/au_manufacturers.json 동기화.

용도:
  1. `Australian_Manufacturers.xls` (실은 XML) 432건 파싱
  2. `seeds/au_manufacturers.json` 저장 (정규화된 entry 배열)
  3. survivors_expanded_v3.json 과 fuzzy match → 매칭 리포트 출력

실행:
  cd upharma-au/
  python -m buyer_discovery.scripts.sync_manufacturers [XLS_PATH]

인자:
  XLS_PATH (선택) — 기본값 C:/Users/user/Downloads/Australian_Manufacturers.xls
  (Jisoo 가 TGA 사이트에서 월 1회 재다운로드해서 덮어쓰는 경로)

출력:
  · seeds/au_manufacturers.json     — 432건 entry
  · seeds/survivors_manufacturer_match.json — survivors × manufacturer 매칭 결과
  · 콘솔: 매칭률 리포트
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from buyer_discovery.utils.manufacturer_match import (
    build_manufacturer_index,
    load_aliases,
    match_company,
    parse_manufacturers_xml,
)

_ROOT = Path(__file__).resolve().parent.parent
_SEEDS = _ROOT / "seeds"
_DEFAULT_XLS = Path(r"C:/Users/user/Downloads/Australian_Manufacturers.xls")

# Stage 1 결과 JSON — survivors 65개 읽기용
_SURVIVORS_JSON = Path(
    r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/survivors_expanded_v3.json"
)


def _write_manufacturers_json(entries: list[dict]) -> Path:
    out_path = _SEEDS / "au_manufacturers.json"
    payload = {
        "_meta": {
            "description": (
                "TGA Manufacturer Licence Register (호주 의약품 제조허가) 432건. "
                "Australian_Manufacturers.xls (XML 포맷) 에서 파싱. "
                "월 1회 TGA 사이트에서 재다운로드 → 본 스크립트 재실행."
            ),
            "source_file": "Australian_Manufacturers.xls",
            "last_synced": datetime.now(timezone.utc).isoformat(),
            "total": len(entries),
        },
        "entries": entries,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def _load_survivors(path: Path) -> dict:
    if not path.exists():
        print(f"[sync] survivors JSON 없음, 매칭 생략: {path}", flush=True)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _match_all_survivors(
    survivors_doc: dict,
    index: dict[str, dict],
    aliases: dict[str, str],
) -> dict:
    """survivors 65개 × manufacturer 매칭.

    반환 구조:
      {
        "_meta": {...},
        "matches": {
          "canonical_key": {
            "canonical_name": "...",
            "match_type": "exact|alias|substring",
            "tga_manufacturer": "...",
            "address": "...", "state": "...",
            "licence_number": "...",
            "has_factory": True,
          },
          ...
        },
        "unmatched": ["key1", "key2", ...]
      }
    """
    buyers = (survivors_doc.get("buyers") or {}) if survivors_doc else {}
    matches: dict[str, dict] = {}
    unmatched: list[str] = []
    for key, row in buyers.items():
        name = row.get("canonical_name") or key
        m = match_company(name, index, aliases)
        if m:
            matches[key] = {
                "canonical_name": name,
                "match_type": m.get("match_type"),
                "match_key": m.get("match_key"),
                "tga_manufacturer": m.get("manufacturer"),
                "address": m.get("address"),
                "state": m.get("state"),
                "licence_number": m.get("licence_number"),
                "issue_date": m.get("issue_date"),
                "has_factory": True,
            }
        else:
            unmatched.append(key)
            matches[key] = {
                "canonical_name": name,
                "has_factory": False,
            }

    return {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_survivors": len(buyers),
            "matched": len(matches) - len(unmatched),
            "unmatched": len(unmatched),
        },
        "matches": matches,
        "unmatched_keys": unmatched,
    }


def main(xls_path: Path = _DEFAULT_XLS) -> None:
    print(f"[sync] TGA Manufacturer XML 파싱 시작: {xls_path}", flush=True)
    if not xls_path.exists():
        print(f"[sync] 파일 없음 — Jisoo 가 TGA 에서 다운받아야 함: {xls_path}", flush=True)
        sys.exit(1)

    entries = parse_manufacturers_xml(xls_path)
    print(f"[sync] 파싱 완료: {len(entries)} entries", flush=True)

    index = build_manufacturer_index(entries)
    print(f"[sync] index: {len(index)} unique normalized keys", flush=True)

    # JSON 저장
    out_path = _write_manufacturers_json(entries)
    print(f"[sync] 저장: {out_path}", flush=True)

    # survivors 매칭
    aliases = load_aliases(_SEEDS / "manufacturer_aliases.json")
    print(f"[sync] alias 매핑: {len(aliases)} 개 로드", flush=True)

    survivors = _load_survivors(_SURVIVORS_JSON)
    if survivors:
        report = _match_all_survivors(survivors, index, aliases)
        match_path = _SEEDS / "survivors_manufacturer_match.json"
        match_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        meta = report["_meta"]
        print(f"[sync] 매칭 결과 저장: {match_path}", flush=True)
        print(
            f"[sync] 매칭률: {meta['matched']}/{meta['total_survivors']} "
            f"(미매칭 {meta['unmatched']})",
            flush=True,
        )

        # 매칭된 회사 샘플
        print("\n=== 매칭된 survivors (공장 보유) 샘플 ===", flush=True)
        shown = 0
        for key, m in report["matches"].items():
            if m.get("has_factory"):
                print(
                    f"  {m['canonical_name']:45s} → {m['tga_manufacturer']} "
                    f"({m['state']}) [{m['match_type']}]",
                    flush=True,
                )
                shown += 1
                if shown >= 20:
                    break

        print("\n=== 미매칭 (공장 없음 추정) 샘플 ===", flush=True)
        for k in report["unmatched_keys"][:10]:
            name = (survivors.get("buyers", {}).get(k) or {}).get("canonical_name") or k
            print(f"  {name}", flush=True)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_XLS
    main(path)
