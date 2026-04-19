"""GBMA(호주 제네릭·바이오시밀러 협회, Generic and Biosimilar Medicines Australia)
회원사 목록. 회원 페이지 HTML 크롤.
"""
from __future__ import annotations

from typing import Any

import httpx
from bs4 import BeautifulSoup

_GBMA_URL = "https://gbma.com.au/gbma-members/"
_TIMEOUT = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


def fetch_gbma_members() -> list[dict[str, Any]]:
    """GBMA 회원사 리스트. 실패·구조변경 시 빈 리스트."""
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

    for sel in ("a[title]", ".member-name", "h3", "h4", "div.member a"):
        for el in soup.select(sel):
            name = (
                (el.get("title") if hasattr(el, "get") else None)
                or el.get_text(strip=True)
            )
            name = (name or "").strip()
            if not name or len(name) < 3:
                continue
            # 제목류 헤더 제외 (너무 포괄적인 단어)
            if name.lower() in ("gbma members", "members", "our members", "menu", "home"):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            href = el.get("href") if hasattr(el, "get") else None
            members.append({
                "name": name,
                "source": "gbma",
                "is_gbma_member": True,
                "website": href if isinstance(href, str) and href.startswith("http") else None,
            })

    if not members:
        print("[gbma_members] HTML 구조 변경 감지 — 셀렉터 조정 필요", flush=True)
    return members
