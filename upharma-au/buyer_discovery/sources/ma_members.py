"""MA(Medicines Australia · 호주 의약품 협회) 회원사 목록.

회원 페이지 HTML 크롤. 구조 변경 시 빈 배열 반환해 파이프라인 전파 방지.
"""
from __future__ import annotations

from typing import Any

import httpx
from bs4 import BeautifulSoup

_MA_URL = "https://www.medicinesaustralia.com.au/about-us/our-members/"
_TIMEOUT = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


def fetch_ma_members() -> list[dict[str, Any]]:
    """MA 회원사 이름·웹사이트 리스트. 실패·구조변경 시 빈 리스트."""
    try:
        r = httpx.get(
            _MA_URL,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"[ma_members] fetch 실패: {exc}", flush=True)
        return []

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except Exception:
        soup = BeautifulSoup(r.text, "html.parser")

    members: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 다양한 셀렉터 시도 (HTML 구조 변경 대비) — 매칭되는 건 전부 수집
    selectors = [
        "a[title]",
        "div.member-name",
        "li.member",
        "div.member a",
        "h3 a, h4 a",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            name = (
                (el.get("title") if hasattr(el, "get") else None)
                or el.get_text(strip=True)
            )
            name = (name or "").strip()
            if not name or len(name) < 2:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            href = el.get("href") if hasattr(el, "get") else None
            members.append({
                "name": name,
                "source": "ma",
                "is_ma_member": True,
                "website": href if isinstance(href, str) and href.startswith("http") else None,
            })

    if not members:
        print("[ma_members] HTML 구조 변경 감지 — 셀렉터 조정 필요", flush=True)
    return members
