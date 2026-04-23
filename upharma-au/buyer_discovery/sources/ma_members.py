"""MA(Medicines Australia · 호주 의약품 협회) 회원사 목록.

회원 페이지 HTML 크롤. 실제 DOM 구조 (2026-04-19 확인):
  <figure class="aligncenter size-full|size-medium|size-large"
          class="wp-block-image ...">
    <a href="https://company-url"><img alt="..." title="회사명"/></a>
  </figure>

→ 셀렉터: `figure a img[title]` — `img.title` 속성을 회사명으로, 부모 `a.href` 를
웹사이트로 채택. 기업명 whitelist (Pty Ltd / Pharma / Pharmaceuticals / Limited /
Australia 중 하나 포함, 최소 4글자) 적용해 네비·링크 텍스트 제거.
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

# 회사명 검증용 — 이 중 하나 포함해야 제약 회사로 인정 (대소문자 무시)
_COMPANY_KEYWORDS: tuple[str, ...] = (
    "pty ltd",
    "pharma",
    "pharmaceutical",
    "limited",
    "australia",
    "au pty",
    "biosciences",
    "therapeutics",
    "laboratories",
    "healthcare",
    "medsurge",
    "corporation",
    "inc.",
    "inc ",
    "gmbh",
    "s.a.",
    "nv",
    "plc",
    " ag ",
    "sankyo",
    "kirin",
    "lilly",
    "pfizer",
    "bayer",
    "novartis",
    "roche",
    "sanofi",
    "merck",
    "abbvie",
    "amgen",
    "takeda",
    "astellas",
    "gsk",
    "msd",
    "argenx",
    "ucb",
    "bms",
    "norgine",
    "viatris",
    "chiesi",
    "menarini",
    "aspen",
    "mayne",
    "inova",
    "theramex",
    "stallergenes",
    "organon",
    "lundbeck",
    "boehringer",
    "daiichi",
    "ptc",
    "iqvia",
    "prospection",
    "kyowa",
)

# 노이즈 블랙리스트 — 회사명처럼 보여도 페이지 네비·헤더로 확인된 것
_NAME_BLACKLIST: tuple[str, ...] = (
    "linkedin",
    "twitter",
    "facebook",
    "instagram",
    "youtube",
    "medicines australia on linkedin",
    "member login",
    "our members",
    "explore",
    "resources",
    "site information",
    "home",
    "menu",
    "search",
    "contact us",
    "about us",
    "advisory council",
    "board members",
    "legal statements",
    "media events",
    "privacy policy",
    "subscribe",
)


def _accept_company_name(name: str) -> bool:
    """회사명으로 인정할지 — whitelist + 블랙리스트 + 길이 필터."""
    s = (name or "").strip()
    if not s or len(s) < 4:
        return False
    low = s.lower()
    if low in _NAME_BLACKLIST:
        return False
    # 블랙리스트 substring 매칭 (더 엄격)
    for b in _NAME_BLACKLIST:
        if len(b) >= 6 and b in low:
            return False
    # whitelist: 제약 회사 키워드 중 하나 포함
    if not any(kw in low for kw in _COMPANY_KEYWORDS):
        return False
    return True


def fetch_ma_members() -> list[dict[str, Any]]:
    """MA 회원사 figure→img[title] 추출 + whitelist 필터.

    실패·파싱 에러 시 빈 리스트.
    """
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

    # 1차 셀렉터: figure 내부 a > img[title] — 가장 신뢰 (MA 회원 로고 그리드 구조)
    for fig in soup.select("figure"):
        a = fig.find("a")
        img = fig.find("img")
        if not img or not hasattr(img, "get"):
            continue
        # title 우선, 없으면 alt
        name = (img.get("title") or img.get("alt") or "").strip()
        if not _accept_company_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        href = a.get("href") if a and hasattr(a, "get") else None
        members.append({
            "name": name,
            "source": "ma",
            "is_ma_member": True,
            "website": href if isinstance(href, str) and href.startswith("http") else None,
        })

    # 2차 fallback: <img title="..."> 전체 스캔 (figure 구조 안에 없을 수도)
    if len(members) < 5:
        for img in soup.select("img[title]"):
            name = (img.get("title") or "").strip()
            if not _accept_company_name(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            # 가장 가까운 a 부모
            href = None
            parent = img.parent
            for _ in range(3):
                if parent is None:
                    break
                if getattr(parent, "name", None) == "a" and parent.get("href"):
                    href = parent.get("href")
                    break
                parent = parent.parent
            members.append({
                "name": name,
                "source": "ma",
                "is_ma_member": True,
                "website": href if isinstance(href, str) and href.startswith("http") else None,
            })

    if not members:
        print("[ma_members] HTML 구조 변경 감지 — 셀렉터 조정 필요", flush=True)
    return members
