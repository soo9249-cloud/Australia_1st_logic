from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BASE = "https://www.chemistwarehouse.com.au"

def fetch_chemist_price(search_term: str) -> dict[str, Any] | None:
    """Jina AI (r.jina.ai)를 활용해 Chemist Warehouse의 가격을 추출한다."""
    try:
        q = (search_term or "").strip()
        if not q:
            return None
            
        # 1. 원본 Chemist URL
        target_url = f"{_BASE}/search?query={quote(q)}"
        
        # 🚀 2. Jina AI 렌더링 API를 앞에 붙여서 우회 호출!
        jina_url = f"https://r.jina.ai/{target_url}"
        
        # Jina AI 전용 헤더
        headers = {
            "Accept": "text/event-stream",
            "User-Agent": _USER_AGENT
        }
        
        r = httpx.get(
            jina_url,
            headers=headers,
            timeout=30, # Jina가 렌더링할 시간을 충분히 줍니다 (30초)
            follow_redirects=True,
        )
        
        if r.status_code != 200:
            return None
            
        retail: float | None = None
        
        # Jina AI는 불필요한 HTML을 다 지우고 깔끔한 텍스트(마크다운)만 줍니다.
        # 따라서 복잡한 파싱 없이, 텍스트에서 정규식으로 $XX.XX만 찾으면 끝!
        blob = r.text
        for m in re.finditer(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", blob):
            val = float(m.group(1).replace(",", ""))
            # 장바구니 0.0 무시하고 실제 가격만 낚아챔
            if val > 0:
                retail = val
                break
                
        if retail is None:
            return None

        return {
            "retail_price_aud": retail,
            "price_unit": "per pack",
            "price_source_name": "Chemist Warehouse",
            "price_source_url": target_url, # 사용자에게는 원본 URL을 돌려줍니다.
        }
        
    except Exception as e:
        print(f"Jina AI 파싱 에러: {e}")
        return None

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