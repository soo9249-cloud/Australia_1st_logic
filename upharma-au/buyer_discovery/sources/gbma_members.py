"""GBMA(호주 제네릭·바이오시밀러 협회) 회원사 — 실시간 크롤.

수집 필드 (2026-04-20 확장):
  · name       — 회사명 (h6 헤딩 anchor)
  · website    — 공식 URL (anchor href)
  · address    — 주소 (본문 p 태그, 주·도시 포함)
  · state      — 주 (VIC / NSW / QLD / WA / SA / TAS / ACT / NT)
  · phone      — 전화번호 ("Telephone: ..." 패턴)
  · is_gbma_member: True

페이지 구조 (2026-04-20 실측):
  <h6 class="wp-block-heading">
    <a href="https://...">Company Name Pty Ltd</a>
  </h6>
  <p>Level 24, 570 Bourke Street, Melbourne VIC 3000 Telephone: 1800 222 673</p>
  ...

한국어 주석 병기: GBMA(호주 제네릭·바이오시밀러 협회).
"""
from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .ma_members import _accept_company_name  # 공통 whitelist/블랙리스트

_GBMA_URL = "https://gbma.com.au/gbma-members/"
_TIMEOUT = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# 전화번호 — "Telephone:" / "Tel:" / "Phone:" 접두사 뒤 숫자·괄호·공백·하이픈
_PHONE_RE = re.compile(
    r"(?:Telephone|Tel|Phone)[:\s]*([\d()\s\+\-]{7,})",
    flags=re.IGNORECASE,
)

# 호주 주 약어 추출 (주소 뒷부분에 "VIC 3000" / "NSW 2000" 등장)
_STATE_RE = re.compile(
    r"\b(VIC|NSW|QLD|WA|SA|TAS|ACT|NT)\b\s*\d{4}",
    flags=re.IGNORECASE,
)


def _extract_contact_from_text(body: str) -> dict[str, Any]:
    """p 태그 병합 텍스트에서 address/state/phone 추출.

    예: "Level 24, 570 Bourke Street, Melbourne VIC 3000 Telephone: 1800 222 673"
       → address="Level 24, 570 Bourke Street, Melbourne VIC 3000"
         state="VIC"
         phone="1800 222 673"
    """
    out: dict[str, Any] = {"address": None, "state": None, "phone": None}
    if not body:
        return out

    # 1. 전화번호 매칭
    m_phone = _PHONE_RE.search(body)
    phone_raw = m_phone.group(1).strip() if m_phone else None
    if phone_raw:
        out["phone"] = re.sub(r"\s+", " ", phone_raw).strip(" .,-")

    # 2. 주소 = 전화번호 전까지의 부분
    if m_phone:
        address = body[: m_phone.start()].strip(" .,|-")
    else:
        address = body.strip(" .,|-")
    # 회사명이 주소 앞에 붙어있으면 ("Accord Australia | Level ...") 분리자 이후만
    if "|" in address:
        parts = [p.strip() for p in address.split("|")]
        address = parts[-1] if parts else address
    if address:
        out["address"] = re.sub(r"\s+", " ", address).strip()

    # 3. 주 약어 추출
    m_state = _STATE_RE.search(body)
    if m_state:
        out["state"] = m_state.group(1).upper()

    return out


def fetch_gbma_members() -> list[dict[str, Any]]:
    """GBMA 회원사 실시간 크롤 — 회사명·URL·주소·전화·주 전부.

    반환: list[dict] — 각 회원사 메타 정보 (위 docstring 참고).
    실패 시 빈 리스트.
    """
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

    # h6.wp-block-heading 순회. 각 헤딩의 다음 형제 p 태그를 본문으로 수집.
    for h in soup.select("h6.wp-block-heading"):
        a = h.find("a")
        if a is not None:
            name = a.get_text(strip=True)
            href = a.get("href")
        else:
            name = h.get_text(strip=True)
            href = None
        if not _accept_company_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        # 다음 형제 p 태그들 수집 (다음 h6 까지)
        body_parts: list[str] = []
        nxt = h.find_next_sibling()
        while nxt is not None and getattr(nxt, "name", None) not in (
            "h1", "h2", "h3", "h4", "h5", "h6"
        ):
            if getattr(nxt, "name", None) == "p":
                t = nxt.get_text(" ", strip=True)
                if t:
                    body_parts.append(t)
                if len(body_parts) >= 3:
                    break
            nxt = nxt.find_next_sibling()
        body = " | ".join(body_parts)

        contact = _extract_contact_from_text(body)

        members.append({
            "name": name,
            "source": "gbma",
            "is_gbma_member": True,
            "website": href if isinstance(href, str) and href.startswith("http") else None,
            "address": contact["address"],
            "state": contact["state"],
            "phone": contact["phone"],
        })

    # 2차 fallback — h6 매칭 실패 시 anchor 전체에서 (연락처 없이)
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
                "address": None,
                "state": None,
                "phone": None,
            })

    if not members:
        print("[gbma_members] HTML 구조 변경 감지 — 셀렉터 조정 필요", flush=True)
    return members
