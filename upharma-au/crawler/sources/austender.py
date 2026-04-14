# buy.nsw.gov.au "Notices" 검색 결과 첫 행을 파싱한다.
# (모듈명·함수명·반환 필드는 호환 유지: contract_value_aud, supplier_name,
#  contract_date, austender_source_url)
#
# 호출 URL 패턴
#   https://buy.nsw.gov.au/notices/search?mode=regular&query={검색어}
#   &noticeTypes=can%2Capp     (CAN: Contract Award Notice + APP: Annual Procurement Plan)
#
# 페이지가 SPA 라 raw HTML 로는 결과가 비어 있어, Jina Reader 마크다운으로 우회한다.

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BASE = "https://buy.nsw.gov.au"
_JINA = "https://r.jina.ai/"
_TIMEOUT = 25.0


def _search_url(query: str) -> str:
    return f"{_BASE}/notices/search?mode=regular&query={quote(query)}&noticeTypes=can%2Capp"


def _empty_row(source_url: str) -> dict[str, Any]:
    return {
        "contract_value_aud": None,
        "supplier_name": None,           # buy.nsw 컨텍스트에서는 발주 Agency 명을 매핑
        "contract_date": None,           # Publish date (DD-MMM-YYYY)
        "austender_source_url": source_url,
    }


def _parse_amount(text: str) -> float | None:
    """첫 번째 양수 $ 금액을 float 로 반환 ('$1,690,000.00' → 1690000.0). 0 은 무시."""
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)", text):
        try:
            v = float(m.group(1).replace(",", ""))
            if v > 0:
                return v
        except ValueError:
            continue
    return None


def _parse_first_block(markdown: str) -> dict[str, Any] | None:
    """검색 결과 마크다운에서 첫 노티스 블록을 잘라 dict 로 만든다.

    Jina 출력 패턴 예:
        ### [Medical Services](https://buy.nsw.gov.au/notices/can/CAN-107821)
            Contract award
        CAN ID CAN-107821 Agency Venues NSW Category Other Publish date
        11-Nov-2025 Contract period 8-Sep-2025 to 8-Sep-2028 Estimated
        amount payable to the contractor (including GST)$1,690,000.00
    """
    headers = list(re.finditer(r"^\s*###\s+\[([^\]]+)\]\(([^)]+)\)", markdown, re.MULTILINE))
    if not headers:
        return None

    first = headers[0]
    start = first.end()
    end = headers[1].start() if len(headers) > 1 else len(markdown)
    block_body = markdown[start:end]

    # 결과가 0건이라는 안내 문구가 잡히는 경우 방어
    if re.search(r"No\s+records?\s+matched", markdown, re.IGNORECASE):
        return None

    # 발주처 (Agency 다음 토큰부터 다음 라벨 직전까지)
    agency: str | None = None
    m = re.search(
        r"Agency\s+(.+?)\s+(?:Category|Publish\s+date|Contract\s+period|CAN\s+ID|Estimated)",
        block_body,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        agency = re.sub(r"\s+", " ", m.group(1)).strip() or None

    # Publish date (DD-MMM-YYYY)
    date_s: str | None = None
    m = re.search(
        r"Publish\s+date[\s\n]+(\d{1,2}[-/]\w{3}[-/]\d{2,4})",
        block_body,
        re.IGNORECASE,
    )
    if m:
        date_s = m.group(1).strip()
    else:
        # fallback: 어떤 형태든 첫 날짜
        m = re.search(
            r"\b(\d{1,2}[-/]\w{3}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
            block_body,
        )
        if m:
            date_s = m.group(1)

    value = _parse_amount(block_body)
    if value is None and not agency and not date_s:
        return None

    return {
        "contract_value_aud": value,
        "supplier_name": agency,
        "contract_date": date_s,
    }


def fetch_austender(search_term: str) -> dict[str, Any]:
    """buy.nsw.gov.au 에서 첫 노티스의 발주처·금액·날짜를 추출한다."""
    q = (search_term or "").strip()
    canonical = _search_url(q) if q else f"{_BASE}/notices/search"
    empty = _empty_row(canonical)
    if not q:
        return empty

    jina_url = f"{_JINA}{canonical}"
    try:
        r = httpx.get(
            jina_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/plain"},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return empty
        text = r.text or ""
    except Exception:
        return empty

    parsed = _parse_first_block(text)
    if not parsed:
        return empty

    return {
        "contract_value_aud": parsed["contract_value_aud"],
        "supplier_name": parsed["supplier_name"],
        "contract_date": parsed["contract_date"],
        "austender_source_url": canonical,
    }


if __name__ == "__main__":
    print(fetch_austender("medical"))
