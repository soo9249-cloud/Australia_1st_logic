"""hardcode 64개 회사의 factory 정보를 TGA Manufacturer Licence (432건) 로 검증.

비교 결과 3 카테고리:
  ✅ 일치: hardcode.factory.has=Y 이고 TGA 에도 매칭
  ⚠️ 의심 Y: hardcode.factory.has=Y 인데 TGA 에 없음 (공장 없거나 이름 매칭 실패)
  ⚠️ 의심 N: hardcode.factory.has=N 인데 TGA 에 있음 (공장 놓쳤거나 관계사)

출력: 콘솔 리포트 + seeds/verify_factory_report.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT.parent))  # upharma-au

from buyer_discovery.utils.manufacturer_match import (  # type: ignore
    build_manufacturer_index,
    load_aliases,
    match_company,
    parse_manufacturers_xml,
)

_HARDCODE = _ROOT / "seeds" / "au_buyers_hardcode.json"  # seeds/ 진본 사용
_XLS = Path(r"C:/Users/user/Downloads/Australian_Manufacturers.xls")
_SEEDS = _ROOT / "seeds"
_OUT = _SEEDS / "verify_factory_report.json"


def main() -> None:
    if not _HARDCODE.is_file():
        print(f"[X] hardcode 없음: {_HARDCODE}", flush=True)
        sys.exit(1)
    if not _XLS.is_file():
        print(f"[X] Manufacturer 엑셀 없음: {_XLS}", flush=True)
        sys.exit(1)

    print("[verify] TGA Manufacturer XML 파싱…", flush=True)
    entries = parse_manufacturers_xml(_XLS)
    index = build_manufacturer_index(entries)
    aliases = load_aliases(_SEEDS / "manufacturer_aliases.json")
    print(f"[verify] 엑셀 entries={len(entries)} / unique_keys={len(index)} / aliases={len(aliases)}", flush=True)

    hc = json.loads(_HARDCODE.read_text(encoding="utf-8"))
    buyers = hc.get("buyers") or {}
    print(f"[verify] hardcode buyers={len(buyers)}\n", flush=True)

    matched_y: list[dict] = []        # hardcode Y + TGA 매칭 OK
    mismatch_y_no_tga: list[dict] = [] # hardcode Y 인데 TGA 없음 (의심)
    matched_n: list[dict] = []         # hardcode N + TGA 에도 없음 (일관)
    mismatch_n_tga_yes: list[dict] = [] # hardcode N 인데 TGA 있음 (놓쳤을 가능성)
    skipped: list[dict] = []            # has=unknown 또는 파싱 실패

    for key, entry in buyers.items():
        if key.startswith("_"):
            continue
        name = entry.get("canonical_name") or key
        factory = entry.get("factory") or {}
        has = (factory.get("has") or "").upper()
        mfr_match = match_company(name, index, aliases)

        row = {
            "canonical_key": key,
            "canonical_name": name,
            "hardcode_has": has,
            "hardcode_locations": factory.get("locations") or [],
            "tga_matched": bool(mfr_match),
            "tga_manufacturer": (mfr_match or {}).get("manufacturer"),
            "tga_address": (mfr_match or {}).get("address"),
            "tga_state": (mfr_match or {}).get("state"),
            "tga_match_type": (mfr_match or {}).get("match_type"),
        }

        if has == "Y" and mfr_match:
            matched_y.append(row)
        elif has == "Y" and not mfr_match:
            mismatch_y_no_tga.append(row)
        elif has == "N" and not mfr_match:
            matched_n.append(row)
        elif has == "N" and mfr_match:
            mismatch_n_tga_yes.append(row)
        else:
            skipped.append(row)

    # ───── 리포트 출력 ─────
    print("=" * 72)
    print(f"✅ 일치 Y ({len(matched_y)}명): hardcode Y + TGA 매칭")
    print("=" * 72)
    for r in matched_y:
        print(f"  {r['canonical_name']:45s} → {r['tga_manufacturer']} ({r['tga_state']}) [{r['tga_match_type']}]")

    print()
    print("=" * 72)
    print(f"⚠️  의심 Y ({len(mismatch_y_no_tga)}명): hardcode Y 인데 TGA 에 없음")
    print("=" * 72)
    for r in mismatch_y_no_tga:
        print(f"  {r['canonical_name']:45s} hardcode locations={r['hardcode_locations']}")

    print()
    print("=" * 72)
    print(f"⚠️  의심 N ({len(mismatch_n_tga_yes)}명): hardcode N 인데 TGA 에 있음")
    print("=" * 72)
    for r in mismatch_n_tga_yes:
        print(f"  {r['canonical_name']:45s} → TGA: {r['tga_manufacturer']} ({r['tga_state']}) [{r['tga_match_type']}]")

    print()
    print("=" * 72)
    print(f"ℹ️  일치 N ({len(matched_n)}명): hardcode N + TGA 에도 없음 (정상)")
    print(f"ℹ️  스킵 ({len(skipped)}명): has=unknown 등")
    print("=" * 72)

    total = len(matched_y) + len(mismatch_y_no_tga) + len(mismatch_n_tga_yes) + len(matched_n) + len(skipped)
    agree = len(matched_y) + len(matched_n)
    print()
    print(f"총 {total}명 중 일치 {agree}명 ({agree * 100 // max(1, total)}%)")
    print(f"수기 검토 필요: {len(mismatch_y_no_tga) + len(mismatch_n_tga_yes)}명")

    # JSON 저장
    payload = {
        "_meta": {
            "total_hardcode": total,
            "matched_y": len(matched_y),
            "mismatch_y_no_tga": len(mismatch_y_no_tga),
            "matched_n": len(matched_n),
            "mismatch_n_tga_yes": len(mismatch_n_tga_yes),
            "skipped": len(skipped),
        },
        "matched_y": matched_y,
        "mismatch_y_no_tga": mismatch_y_no_tga,
        "mismatch_n_tga_yes": mismatch_n_tga_yes,
        "matched_n": matched_n,
        "skipped": skipped,
    }
    _OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {_OUT}")


if __name__ == "__main__":
    main()
