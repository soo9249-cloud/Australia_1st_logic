"""GBMA(호주 제네릭·바이오시밀러 협회) 회원사.

회원 페이지 HTML 크롤. 실제 DOM 구조 (2026-04-19 확인):
  <a href="https://company-url" target="_blank">
    <img alt="..." src="..."/>
  </a>
  <h6 class="wp-block-heading">
    <a href="https://company-url" target="_blank" rel="noopener">Company Name Pty Ltd</a>
  </h6>

→ 셀렉터: `h6.wp-block-heading a` — 텍스트를 회사명으로, href 를 웹사이트로. 헤더·
네비 단어는 blacklist 로 제거하고 whitelist (Pty Ltd / Pharma 등) 통과한 것만.
"""
from __future__ import annotations

from typing import Any

import httpx
from bs4 import BeautifulSoup

from .ma_members import _accept_company_name  # 동일 whitelist/블랙리스트 재사용

_GBMA_URL = "https://gbma.com.au/gbma-members/"
_TIMEOUT = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


def fetch_gbma_members() -> list[dict[str, Any]]:
    """GBMA 회원사 `h6.wp-block-heading a` 텍스트 추출 + whitelist 필터."""
    try:
        r = httpx.get(
            _GBMA_URL,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"[gbma_members] fetch 실패: {exc}", flush=True)
        return []

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except Exception:
        soup = BeautifulSoup(r.text, "html.parser")

    members: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1차: h6.wp-block-heading 내부 anchor 텍스트
    for h in soup.select("h6.wp-block-heading a, h6.wp-block-heading"):
        name = h.get_text(strip=True)
        if not _accept_company_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        href = h.get("href") if hasattr(h, "get") else None
        members.append({
            "name": name,
            "source": "gbma",
            "is_gbma_member": True,
            "website": href if isinstance(href, str) and href.startswith("http") else None,
        })

    # 2차 fallback: target="_blank" 링크 중 whitelist 통과하는 텍스트
    if len(members) < 5:
        for a in soup.select('a[target="_blank"]'):
            name = a.get_text(strip=True)
            if not _accept_company_name(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            href = a.get("href")
            members.append({
                "name": name,
                "source": "gbma",
                "is_gbma_member": True,
                "website": href if isinstance(href, str) and href.startswith("http") else None,
            })

    if not members:
        print("[gbma_members] HTML 구조 변경 감지 — 셀렉터 조정 필요", flush=True)
    return members
