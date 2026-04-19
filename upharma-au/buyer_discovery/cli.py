"""Phase 3.5 dry-run CLI — Stage 1 까지만 돌려서 사용자 하드코딩용 JSON 출력.

위임지서 §0-6: 이 CLI 실행은 Jisoo 가 본인 터미널에서 직접 함
(Render 환경변수 세팅된 로컬에서 실행해야 Supabase SELECT 가능).

사용법:
  # 8 품목 전부 합집합 (권장)
  python -m buyer_discovery.cli --stage1-only --all-products \\
      --output "/path/to/AX 호주 final/survivors_for_hardcode.json"

  # 특정 품목만
  python -m buyer_discovery.cli --stage1-only --product au-rosumeg-005 \\
      --output /tmp/survivors_rosumeg.json

출력 JSON 구조:
  {
    "_meta": {generated_at, instruction, total_unique},
    "buyers": {
      "<canonical_key>": {
        "canonical_name", "website", "sources", "stage1_sort_score",
        "evidence": {...},
        "products_relevant": [pid, ...],
        "hardcode_needed": {  ← 사용자가 Gemini 딥리서치로 채울 두 필드
          "annual_revenue_rank": "??? (예: TOP 5 (제네릭 1위))",
          "factory": {"has": "???", "count": 0, "locations": []},
          "notes": ""
        }
      },
      ...
    }
  }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pipeline_collect import _get_product, collect_all_sources
from .stage1_filter import run_stage1

_PRODUCTS_PATH = Path(__file__).resolve().parent.parent / "crawler" / "au_products.json"


def _all_product_codes() -> list[str]:
    data = json.loads(_PRODUCTS_PATH.read_text(encoding="utf-8"))
    return [p["product_id"] for p in data.get("products", []) if p.get("product_id")]


def _build_hardcode_template(
    survivors: list[dict[str, Any]],
    ingredient_per_product: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """딥리서치 인계용 JSON 빌드.

    각 회사별로 어느 품목(product_id) 에 관련 있는지 (`products_relevant`),
    품목별 4-case 결과 (`ingredient_case_per_product`) 를 수록.
    사용자는 `hardcode_needed` 두 필드만 채워서 au_buyers_hardcode.json 에 저장.
    """
    buyers: dict[str, dict[str, Any]] = {}
    for row in survivors:
        key = row["canonical_key"]
        relevant = [
            pid
            for pid, case_map in ingredient_per_product.items()
            if case_map.get(key) and case_map[key] != "D_none"
        ]
        buyers[key] = {
            "canonical_name": row.get("canonical_name"),
            "website": (row.get("raw_data") or {}).get("website"),
            "sources": row.get("sources", []),
            "stage1_sort_score": row.get("stage1_sort_score", 0),
            "evidence": {
                "tga_artg_count":    row.get("tga_artg_count", 0),
                "pbs_listed_count":  row.get("pbs_listed_count", 0),
                "is_ma_member":      row.get("is_ma_member", False),
                "is_gbma_member":    row.get("is_gbma_member", False),
                "is_gpce_exhibitor": row.get("is_gpce_exhibitor", False),
                "ingredient_case_per_product": {
                    pid: case_map.get(key, "D_none")
                    for pid, case_map in ingredient_per_product.items()
                },
            },
            "products_relevant": relevant,
            "hardcode_needed": {
                "annual_revenue_rank": "??? (예: TOP 5 (제네릭 1위))",
                "factory": {
                    "has": "??? (Y/N/unknown)",
                    "count": 0,
                    "locations": [],
                },
                "notes": "",
            },
        }

    return {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "instruction": (
                "각 회사의 'hardcode_needed' 두 필드(annual_revenue_rank + factory) "
                "와 notes 만 채워서 buyer_discovery/seeds/au_buyers_hardcode.json "
                "으로 저장하세요. 다른 필드는 건드리지 마세요. Gemini 딥리서치 권장."
            ),
            "total_unique": len(buyers),
        },
        "buyers": buyers,
    }


async def main_async(args: argparse.Namespace) -> None:
    products = _all_product_codes() if args.all_products else [args.product]

    # 품목별 Stage 1 → 합집합
    union_survivors: dict[str, dict[str, Any]] = {}
    ingredient_per_product: dict[str, dict[str, str]] = {}

    for pid in products:
        product = _get_product(pid)
        collected = await collect_all_sources(pid)
        survivors = run_stage1(collected, product)

        # 품목별 case 맵
        ingredient_per_product[pid] = {
            row["canonical_key"]: row.get("ingredient_case", "D_none")
            for row in survivors
        }

        # 합집합 — 동일 key 는 더 높은 stage1_sort_score 유지
        for row in survivors:
            key = row["canonical_key"]
            cur = union_survivors.get(key)
            if cur is None or row.get("stage1_sort_score", 0) > cur.get("stage1_sort_score", 0):
                union_survivors[key] = row

    sorted_union = sorted(
        union_survivors.values(),
        key=lambda r: r.get("stage1_sort_score", 0),
        reverse=True,
    )

    out = _build_hardcode_template(sorted_union, ingredient_per_product)
    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print(f"✓ 생성 완료: {args.output}")
    print(f"  - 생존자 총 {out['_meta']['total_unique']}개 회사")
    print(f"  - 품목 수: {len(products)}")
    print(f"  - 다음 단계: 이 파일을 Gemini 딥리서치에 던져서 hardcode_needed 채우세요.")
    print(f"  - 완성본을 buyer_discovery/seeds/au_buyers_hardcode.json 에 저장 후 git commit.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="바이어발굴 Stage 1 dry-run — 사용자 하드코딩 템플릿 생성",
    )
    parser.add_argument(
        "--stage1-only",
        action="store_true",
        required=True,
        help="Stage 1 까지만 돌리고 멈춤 (Phase 3.5 전용 플래그)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all-products",
        action="store_true",
        help="au_products.json 의 전 품목 합집합 (권장)",
    )
    group.add_argument(
        "--product",
        type=str,
        help="특정 product_id 만 (예: au-rosumeg-005)",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="출력 JSON 경로",
    )

    args = parser.parse_args()
    try:
        asyncio.run(main_async(args))
    except Exception as exc:
        print(f"[CLI 오류] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
