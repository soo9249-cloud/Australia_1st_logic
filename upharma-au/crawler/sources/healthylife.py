# Healthylife 소매가 크롤러 — PBS 미등재 Private 처방약(예: OMACOR) 참고가 수집.
#
# 호출 순서:
#   1) Next.js JSON API: {BASE}/api/products/{slug}
#   2) 실패 시 공개 HTML 페이지: {BASE}/products/{slug}
#   3) Cloudflare/SPA 방어벽에 막히면 Jina AI Reader(r.jina.ai) 폴백
#
# 반환 dict 형식은 au_price_crawler.fetch_healthylife_price 와 동일.

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

_BASE = "https://www.healthylife.com.au"
_JINA = "https://r.jina.ai/"
_REQUEST_DELAY = 1.5
_TIMEOUT = 12.0

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_HEADERS_JSON = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json",
}
_HEADERS_HTML = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

_CF_BLOCK_STATUSES = {403, 503, 520, 521, 522, 523, 524, 525, 526, 527}
_CF_BLOCK_MARKERS = (
    "cloudflare",
    "cf-ray",
    "attention required",
    "challenge-platform",
    "checking your browser",
    "just a moment",
)


def _error_result(slug: str, reason: str) -> dict[str, Any]:
    # HealthylifeDTO (§13-5-5) + 하위호환 키
    product_url = f"{_BASE}/products/{slug}"
    return {
        # v2 DTO 키
        "product_url": product_url,
        "brand_name": slug,
        "price_aud": None,
        "pack_size": None,
        "category": None,
        "source_name": "healthylife",
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        # 하위호환 키
        "slug": slug,
        "is_pbs": False,
        "prescription": None,
        "source": f"Healthylife (실패: {reason})",
        "confidence": 0.0,
        "price_source_url": product_url,
    }


def _is_blocked(status: int, body: str) -> bool:
    if status in _CF_BLOCK_STATUSES:
        return True
    low = (body or "").lower()
    if any(m in low for m in _CF_BLOCK_MARKERS):
        return True
    return False


def _fetch_json(slug: str) -> dict[str, Any] | None:
    """Next.js JSON API 시도. 404/차단 시 None."""
    url = f"{_BASE}/api/products/{slug}"
    try:
        r = httpx.get(
            url,
            headers=_HEADERS_JSON,
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    # 다양한 키 형태 대응
    price: float | None = None
    for key in ("price", "salePrice", "currentPrice"):
        v = data.get(key)
        if isinstance(v, (int, float)) and v > 0:
            price = float(v)
            break
    if price is None:
        cents = data.get("priceInCents")
        if isinstance(cents, (int, float)) and cents > 0:
            price = float(cents) / 100.0

    # HealthylifeDTO (§13-5-5) + 하위호환
    product_url = f"{_BASE}/products/{slug}"
    price_decimal = Decimal(str(price)) if price is not None else None
    return {
        # v2 DTO 키
        "product_url": product_url,
        "brand_name": data.get("name") or slug,
        "price_aud": price_decimal,
        "pack_size": None,                 # 파싱 범위 밖 (다음 위임)
        "category": data.get("category"),
        "source_name": "healthylife",
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        # 하위호환
        "slug": slug,
        "is_pbs": False,
        "prescription": bool(data.get("prescriptionOnly")) if "prescriptionOnly" in data else True,
        "source": "Healthylife JSON API",
        "confidence": 0.85 if price else 0.40,
        "price_source_url": product_url,
    }


def _fetch_html(slug: str) -> dict[str, Any] | None:
    """공개 HTML 페이지 파싱. 차단 시 None (Jina 폴백 유도)."""
    url = f"{_BASE}/products/{slug}"
    try:
        r = httpx.get(
            url,
            headers=_HEADERS_HTML,
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
    except Exception:
        return None
    body = r.text or ""
    if r.status_code != 200 or _is_blocked(r.status_code, body):
        return None

    return _parse_price_block(slug, body, source="Healthylife HTML 파싱", confidence=0.75)


def _fetch_jina(slug: str) -> dict[str, Any] | None:
    """Jina AI Reader 폴백 — Cloudflare·SPA 대응."""
    url = f"{_BASE}/products/{slug}"
    jina_url = f"{_JINA}{url}"
    try:
        r = httpx.get(
            jina_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/event-stream"},
            timeout=30,
            follow_redirects=True,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return _parse_price_block(
        slug, r.text or "", source="Healthylife (via Jina AI Reader)", confidence=0.70
    )


def _parse_price_block(
    slug: str, blob: str, *, source: str, confidence: float
) -> dict[str, Any] | None:
    """$XX.XX 첫 값 + 제품명(h1 or 마크다운 제목) + 처방전 여부를 추출."""
    price = None
    for m in re.finditer(r"\$\s*(\d+(?:\.\d{1,2})?)", blob):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if v > 0:
            price = v
            break
    if price is None:
        return None

    name: str | None = None
    m = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", blob, flags=re.IGNORECASE)
    if m:
        name = m.group(1).strip()
    if not name:
        m = re.search(r"^\s*#\s+(.+?)\s*$", blob, flags=re.MULTILINE)
        if m:
            name = m.group(1).strip()

    prescription = bool(
        re.search(r"\b(prescription|pharmacist\s+only|S4|schedule\s*4)\b", blob, flags=re.IGNORECASE)
    )

    # HealthylifeDTO (§13-5-5) + 하위호환
    product_url = f"{_BASE}/products/{slug}"
    price_decimal = Decimal(str(price)) if price is not None else None
    return {
        # v2 DTO 키
        "product_url": product_url,
        "brand_name": name or slug,
        "price_aud": price_decimal,
        "pack_size": None,
        "category": None,
        "source_name": "healthylife",
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        # 하위호환
        "slug": slug,
        "is_pbs": False,
        "prescription": prescription,
        "source": source,
        "confidence": confidence,
        "price_source_url": product_url,
    }


def fetch_healthylife_price(slug: str) -> dict[str, Any]:
    """Healthylife 공개 소매가 조회.

    순서: JSON API → HTML → Jina AI Reader. 셋 다 실패 시 에러 dict.
    """
    s = (slug or "").strip()
    if not s:
        return _error_result("", "빈 slug")

    result = _fetch_json(s)
    if result and result.get("price_aud"):
        time.sleep(_REQUEST_DELAY)
        return result

    result = _fetch_html(s)
    if result and result.get("price_aud"):
        time.sleep(_REQUEST_DELAY)
        return result

    result = _fetch_jina(s)
    if result and result.get("price_aud"):
        time.sleep(_REQUEST_DELAY)
        return result

    return _error_result(s, "JSON/HTML/Jina 모두 실패")


if __name__ == "__main__":
    import json

    r = fetch_healthylife_price("omacor-1000mg-cap-28")
    print(json.dumps(r, ensure_ascii=False, indent=2))
