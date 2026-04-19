"""GPCE(호주 일반의 컨퍼런스, General Practitioner Conference & Exhibition)
전시사 목록 — Algolia API 로 멜버른 + 시드니 두 이벤트 조회.

엔드포인트 (2026-04-19 F12 분석):
  App ID:  XD0U5M6Y4R
  API Key: d5cd7d4ec26134ff4a34d736a7f9ad47   (공개 클라이언트 키)
  Melbourne index:          evt-7e0b79b1-704d-4c8b-9e4f-9d47f5b98102-index
  Sydney    index:          evt-d5dae678-a0eb-40bc-8d0f-a454652582a7-index
  Melbourne eventEditionId: eve-f5c3fc57-57f0-4e7d-a9c9-0b0409d048e6
  Sydney    eventEditionId: eve-a991e762-714f-42bb-9cb3-008511e7eb13

처방의약품(`Pharmaceutical Prescription`) 카테고리 전시사만 수집.
"""
from __future__ import annotations

from typing import Any

import httpx

_APP_ID = "XD0U5M6Y4R"
_API_KEY = "d5cd7d4ec26134ff4a34d736a7f9ad47"
_TIMEOUT = 30.0

_EVENTS = [
    {
        "label": "melbourne",
        "index": "evt-7e0b79b1-704d-4c8b-9e4f-9d47f5b98102-index",
        "event_id": "eve-f5c3fc57-57f0-4e7d-a9c9-0b0409d048e6",
    },
    {
        "label": "sydney",
        "index": "evt-d5dae678-a0eb-40bc-8d0f-a454652582a7-index",
        "event_id": "eve-a991e762-714f-42bb-9cb3-008511e7eb13",
    },
]


def fetch_gpce_exhibitors() -> list[dict[str, Any]]:
    """GPCE 전시사 리스트 (처방의약품 카테고리만). 이벤트별 실패 시 해당 이벤트만 skip."""
    out: list[dict[str, Any]] = []
    for ev in _EVENTS:
        url = f"https://{_APP_ID.lower()}-dsn.algolia.net/1/indexes/{ev['index']}/query"
        headers = {
            "x-algolia-application-id": _APP_ID,
            "x-algolia-api-key": _API_KEY,
            "content-type": "application/json",
        }
        filters = (
            f"recordType:exhibitor AND locale:en-gb "
            f"AND eventEditionId:{ev['event_id']}"
        )
        payload = {"params": f"query=&page=0&filters={filters}&hitsPerPage=200"}
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f"[gpce] {ev['label']} 실패: {exc}", flush=True)
            continue

        for hit in data.get("hits", []):
            cats = hit.get("ppsAnswers") or []
            # 처방의약품 (Pharmaceutical Prescription) 카테고리 전시사만
            if not any("Pharmaceutical Prescription" in c for c in cats):
                continue

            name = hit.get("companyName") or hit.get("exhibitorName") or ""
            name = str(name).strip()
            if not name:
                continue

            out.append({
                "name": name,
                "exhibitor_name": hit.get("exhibitorName"),
                "website": hit.get("website"),
                "email": hit.get("email") or None,
                "phone": hit.get("phone"),
                "description": hit.get("exhibitorDescription"),
                "represented_brands": hit.get("representedBrands") or [],
                "products": [p.get("name") for p in (hit.get("products") or []) if isinstance(p, dict)],
                "categories": cats,
                "event": ev["label"],
                "source": "gpce",
            })
    return out
