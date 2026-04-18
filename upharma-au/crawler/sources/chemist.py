from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BASE = "https://www.chemistwarehouse.com.au"
_JINA = "https://r.jina.ai/"

# Cloudflare / WAF 차단 시그널 — 상태코드 + 본문 키워드 조합으로 판단
_CF_BLOCK_STATUSES = {403, 503, 520, 521, 522, 523, 524, 525, 526, 527}
_CF_BLOCK_MARKERS = (
    "cloudflare",
    "cf-ray",
    "attention required",
    "challenge-platform",
    "checking your browser",
    "just a moment",
    "please enable javascript",
)


def _is_cloudflare_blocked(resp: httpx.Response | None, text: str) -> bool:
    """Chemist Warehouse의 Cloudflare 차단 여부 — 직접 호출 결과만 평가."""
    if resp is None:
        return True
    if resp.status_code in _CF_BLOCK_STATUSES:
        return True
    low = (text or "").lower()
    if any(m in low for m in _CF_BLOCK_MARKERS) and "chemist" not in low[:200]:
        return True
    # 본문이 비정상적으로 짧으면(수백 바이트) 차단으로 간주
    if resp.status_code == 200 and len(text or "") < 1500:
        if any(m in low for m in _CF_BLOCK_MARKERS):
            return True
    return False


def _extract_first_price(blob: str) -> float | None:
    """$XX.XX 패턴 중 0보다 큰 첫 값을 float 로 반환."""
    for m in re.finditer(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", blob or ""):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if val > 0:
            return val
    return None


def _fetch_direct(target_url: str) -> tuple[str | None, httpx.Response | None]:
    """Chemist Warehouse 직접 호출 — Cloudflare 우회 안 함. 실패/차단 시 (None, resp)."""
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9",
    }
    try:
        r = httpx.get(
            target_url,
            headers=headers,
            timeout=15,
            follow_redirects=True,
        )
    except Exception:
        return None, None
    return r.text if r.status_code == 200 else None, r


def _fetch_jina(target_url: str) -> str | None:
    """Jina AI Reader 폴백 — SPA/Cloudflare 우회용. 본문(마크다운) 반환."""
    jina_url = f"{_JINA}{target_url}"
    headers = {
        "Accept": "text/event-stream",
        "User-Agent": _USER_AGENT,
    }
    try:
        r = httpx.get(
            jina_url,
            headers=headers,
            timeout=30,
            follow_redirects=True,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return r.text


def fetch_chemist_price(search_term: str) -> dict[str, Any] | None:
    """Chemist Warehouse 가격 조회.

    정책: 먼저 직접 호출 → Cloudflare 차단 감지 시 Jina AI Reader(r.jina.ai) 폴백.
    둘 다 실패하면 None.

    반환 dict의 price_source_name 은 경로(direct/Jina) 구분 없이 항상
    "Chemist Warehouse" 로 고정 — app.js 의 정확 매칭(`=== "Chemist Warehouse"`) 호환 유지.
    내부 경로 구분은 print 로그로만 남긴다.
    """
    q = (search_term or "").strip()
    if not q:
        return None

    target_url = f"{_BASE}/search?query={quote(q)}"

    # 1차: 직접 호출
    direct_text, direct_resp = _fetch_direct(target_url)
    blob: str | None = None

    if direct_text and not _is_cloudflare_blocked(direct_resp, direct_text):
        blob = direct_text
        print(f"[chemist] direct OK: {q!r}", flush=True)
    else:
        # 2차: Jina AI Reader 폴백 (Cloudflare 차단·SPA 대응)
        jina_text = _fetch_jina(target_url)
        if jina_text:
            blob = jina_text
            print(f"[chemist] Jina fallback: {q!r}", flush=True)

    if blob is None:
        print(f"[chemist] both direct and Jina failed: {q!r}", flush=True)
        return None

    retail = _extract_first_price(blob)
    if retail is None:
        return None

    # ChemistDTO (§13-5-3) — Decimal 사용, v2 키 + 하위호환 키 둘 다 유지
    price_decimal = Decimal(str(retail))
    return {
        # v2 DTO 키
        "product_url": target_url,
        "brand_name": None,            # 현재 파싱 범위 밖 — 다음 위임
        "price_aud": price_decimal,
        "pack_size": None,             # 현재 파싱 범위 밖
        "in_stock": True,               # 검색 결과에 노출된 첫 양수가 있으면 true 로 간주
        "category": None,
        "source_name": "chemist_warehouse",
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        # 하위호환 키 (au_crawler._estimate_retail_price 등에서 사용)
        "retail_price_aud": price_decimal,
        "price_unit": "per pack",
        "price_source_name": "Chemist Warehouse",
        "price_source_url": target_url,
    }


def build_sites(
    pbs_url: str,
    tga_url: str,
    chemist_url: str,
    nsw_url: str,
    pubmed_url: str | None = None,
) -> dict[str, Any]:
    """출처 URL들을 sites JSON 구조로 묶는다."""
    out: dict[str, Any] = {
        "public_procurement": [
            {"name": "PBS", "url": pbs_url},
            {"name": "NSW Health Procurement", "url": nsw_url},
        ],
        "private_price": [{"name": "Chemist Warehouse", "url": chemist_url}],
        "paper": [],
    }
    if pubmed_url:
        out["paper"] = [{"name": "PubMed", "url": pubmed_url}]
    return out
