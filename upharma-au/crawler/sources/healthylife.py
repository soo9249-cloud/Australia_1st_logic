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
from decimal import Decimal
from typing import Any

import httpx

from utils.crawl_time import now_kst_iso

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
        "crawled_at": now_kst_iso(),
        "availability_status": None,   # Phase Omethyl 신규 — in_stock / temporarily_unavailable
        # 하위호환 키
        "slug": slug,
        "is_pbs": False,
        "prescription": None,
        "source": f"Healthylife (실패: {reason})",
        "confidence": 0.0,
        "price_source_url": product_url,
    }


# Phase Omethyl (2026-04-19) — 가격·재고 파싱 헬퍼
# 기존 `\$\s*(\d+...)` 첫 매칭 방식은 페이지 상단의 "Free shipping on orders over $300"
# 같은 프로모션 텍스트를 상품가로 오인식했음 (실제 $48.95 무시, $300 채택). JSON-LD
# schema.org Offer 구조 + data-price/itemprop 속성을 우선하도록 다단계 필터.

_AVAILABILITY_UNAVAILABLE_PATTERNS = (
    r"currently\s+unavailable",
    r"temporarily\s+unavailable",
    r"temporarily\s+out\s+of\s+stock",
    r"out\s+of\s+stock",
    r"sold\s+out",
    r"notify\s+me\s+when\s+back\s+in\s+stock",
    r"notify\s+me\s+when\s+available",
)

# 상품가 추정에서 제외할 노이즈 토큰 — $ 직전 1단어만 검사.
# 단어 직후 바로 \$ 가 오는 프로모션 전형 패턴 ("orders over \$300", "save \$10",
# "spend \$100", "from \$20", "rrp \$45") 포착용. "off" 같이 너무 흔한 토큰은
# 배제 (상품 맥락에서 "\$10 off" 같은 문구가 뒷상품가에 영향 주면 안 됨).
_PRICE_NOISE_LAST_WORDS = frozenset({
    "over",
    "save",
    "spend",
    "rrp",
    "was",
    "reg",
    "from",
    "above",
    "under",
    "minimum",
    "min",
})


def _detect_availability(blob: str) -> str:
    """'in_stock' / 'temporarily_unavailable' 중 하나 반환.

    페이지에 unavailable 관련 문구가 있으면 temporarily_unavailable,
    없으면 in_stock 으로 간주 (보수적).
    """
    low = (blob or "").lower()
    for pat in _AVAILABILITY_UNAVAILABLE_PATTERNS:
        if re.search(pat, low):
            return "temporarily_unavailable"
    return "in_stock"


def _extract_price_from_blob(blob: str) -> float | None:
    """다단계 우선순위로 상품 정가 추출.

    1) JSON-LD schema.org Offer: `"price":"48.95"` / `"price": 48.95`
       또는 `"lowPrice"` / `"highPrice"` (LD Offers).
    2) HTML 속성: `itemprop="price" content="48.95"`, `data-price="48.95"`.
    3) 프로모션 맥락 제외한 첫 $XX.XX (소수점 포함 패턴만 채택 — 소수점 없는
       정수 금액은 대개 "orders over $100" 같은 정책 문구라 2순위).

    실패 시 None. (호출부는 이 경우 None 반환해 다음 fetch 경로로 넘어감.)
    """
    if not blob:
        return None

    # 1) JSON-LD schema.org Offer price
    #    "price":"48.95" / "price":48.95 / "price": "48.95"
    m = re.search(
        r'"price"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
        blob,
    )
    if m:
        try:
            v = float(m.group(1))
            if v > 0:
                return v
        except ValueError:
            pass

    # lowPrice / highPrice — Offers 는 range 도 가짐
    m = re.search(
        r'"lowPrice"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
        blob,
    )
    if m:
        try:
            v = float(m.group(1))
            if v > 0:
                return v
        except ValueError:
            pass

    # 2) HTML itemprop="price" / data-price 속성
    #    <meta itemprop="price" content="48.95">
    #    <span data-price="48.95">...</span>
    for attr_re in (
        r'itemprop=["\']price["\'][^>]*content=["\']?(\d+(?:\.\d{1,2})?)',
        r'data-price=["\']?(\d+(?:\.\d{1,2})?)',
        r'data-product-price=["\']?(\d+(?:\.\d{1,2})?)',
    ):
        m = re.search(attr_re, blob, flags=re.IGNORECASE)
        if m:
            try:
                v = float(m.group(1))
                if v > 0:
                    return v
            except ValueError:
                continue

    # 3) 프로모션 맥락 제외한 $XX.XX — 소수점 포함 패턴만 (정수 단독 금액은 후순위)
    def _is_noise_context(text: str, match_start: int, window: int = 30) -> bool:
        """$ 바로 앞 마지막 1 단어가 프로모션 노이즈 토큰이면 True.

        정확성을 위해 마지막 1 단어만 검사:
          "Orders over $300" → 직전 단어 "over" → 노이즈 ✓
          "Save $10"         → 직전 단어 "save" → 노이즈 ✓
          "Product price: $12.99" → 직전 단어 "price" → 정상 ✓
          "$10 off.\\nProduct price: $12.99" → $12.99 직전 단어는 "price" (clean).
          ($10 off 의 "off" 가 멀리 있어도 직전 1 단어만 검사하므로 무영향.)
        """
        ctx_start = max(0, match_start - window)
        ctx = text[ctx_start:match_start].lower()
        words = re.findall(r"[a-z]+", ctx)
        if not words:
            return False
        return words[-1] in _PRICE_NOISE_LAST_WORDS

    # 3a) 소수점 있는 $XX.XX 우선 — 상품 정가 후보로 적합
    for m in re.finditer(r"\$\s*(\d+\.\d{1,2})", blob):
        if _is_noise_context(blob, m.start()):
            continue
        try:
            v = float(m.group(1))
            if v > 0:
                return v
        except ValueError:
            continue

    # 3b) 마지막 수단 — 소수점 없는 $XX (프로모션 맥락 제외 후)
    for m in re.finditer(r"\$\s*(\d+)(?!\.\d)", blob):
        if _is_noise_context(blob, m.start()):
            continue
        try:
            v = float(m.group(1))
            # 너무 큰 정수 ($300, $500 같은 라운드 넘버) 는 배송 임계값·적립금 가능성 — skip
            # (상품 정가는 통상 소수점 있음. 정수 상품가 있어도 대부분 $1~$99 범위.)
            if 0 < v < 200:
                return v
        except ValueError:
            continue

    return None


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

    # Phase Omethyl — JSON API 응답에서 재고 상태 키 수용 (있을 때).
    availability: str = "in_stock"
    for key in ("availability", "stockStatus", "inStock"):
        v = data.get(key)
        if isinstance(v, str):
            low = v.lower()
            if any(m in low for m in ("unavailable", "out", "sold", "backorder")):
                availability = "temporarily_unavailable"
                break
        elif isinstance(v, bool) and not v:
            availability = "temporarily_unavailable"
            break

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
        "crawled_at": now_kst_iso(),
        "availability_status": availability,
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
    """가격 + 제품명(h1 or 마크다운 제목) + 처방전 여부 + 재고 상태 추출.

    Phase Omethyl (2026-04-19) — 기존 단순 `\\$\\s*(\\d+...)` 첫 매칭 파싱은
    "Free shipping on orders over $300" 같은 프로모션 텍스트를 상품가로 오인식.
    → `_extract_price_from_blob` 다단계 우선순위(JSON-LD → itemprop/data-price
    → 프로모션 맥락 제외한 $XX.XX) 로 교체.

    재고 상태: `_detect_availability` 로 in_stock / temporarily_unavailable 반환.
    품절이어도 정가는 계속 추출해 참고가로 반환 (정가 표시는 유지되는 경우 흔함).
    """
    price = _extract_price_from_blob(blob)
    if price is None:
        return None

    availability = _detect_availability(blob)

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
        "crawled_at": now_kst_iso(),
        "availability_status": availability,
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
