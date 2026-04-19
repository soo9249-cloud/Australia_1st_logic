"""TGA Manufacturer Licence (Australian_Manufacturers.xls — 실제론 XML) 파서 + 매칭.

주요 함수
---------
- parse_manufacturers_xml(xml_path) -> list[dict]
    XML 파일을 읽어 432개 entry 를 dict 리스트로 반환.
- normalize_company_name(name) -> str
    꼬리 제거 + 특수문자 제거 후 소문자 키 반환.
- build_manufacturer_index(entries) -> dict[str, dict]
    정규화 키 → entry dict 매핑.
- match_company(company_name, index, aliases) -> dict | None
    1차 정확 일치 → 2차 alias 적용 재시도 → 3차 substring(>=5자, 2단어 겹침) fuzzy.

호주 비개발자(Jisoo)용: 본 모듈은 TGA Manufacturer Licence (호주 제조업 허가) 엑셀을
읽어서 survivors(생존 바이어) 목록과 공장 보유 여부를 연결하는 용도.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# 정규화
# ---------------------------------------------------------------------------

# 회사명 꼬리 패턴. 긴 것부터 매칭해야 "pty limited" 가 "pty" 보다 먼저 제거됨.
_TAIL_PATTERNS: tuple[str, ...] = (
    "pty limited",
    "pty. ltd.",
    "pty ltd.",
    "pty. ltd",
    "pty ltd",
    "pty.",
    "pty",
    "pharmaceuticals australia",
    "pharmaceuticals",
    "pharmaceutical",
    "pharma",
    "healthcare",
    "consumer healthcare",
    "(australia)",
    "australia",
    "limited",
    "ltd.",
    "ltd",
    "inc.",
    "inc",
    "corporation",
    "corp.",
    "corp",
    "co.",
    "company",
    "group",
    "holdings",
    "operations",
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_company_name(name: str) -> str:
    """회사명을 정규화 키로 변환.

    1) 소문자
    2) 꼬리 제거 (pty ltd / pharmaceuticals / (australia) 등)
    3) 특수문자·공백 제거 → 순수 알파뉴머릭
    """
    if not name:
        return ""
    s = name.strip().lower()
    # 괄호·콤마 안쪽 정보 제거 후 꼬리 매칭 정확도↑
    s = s.replace("&", " and ")
    # 꼬리 패턴 반복 제거 (예: "X Pty Ltd Australia" → 여러 번 돌아야 둘 다 떨어짐)
    for _ in range(4):
        changed = False
        for tail in _TAIL_PATTERNS:
            if s.endswith(tail):
                s = s[: -len(tail)].strip(" .,-")
                changed = True
                break
            # 중간에 낀 경우도 제거 (예: "X Pharmaceuticals Group")
            token = " " + tail + " "
            if token in (" " + s + " "):
                s = (" " + s + " ").replace(token, " ").strip()
                changed = True
                break
        if not changed:
            break
    # 특수문자 제거
    s = _NON_ALNUM_RE.sub("", s)
    return s


# ---------------------------------------------------------------------------
# XML 파서
# ---------------------------------------------------------------------------

_ENTRY_FIELDS: tuple[str, ...] = (
    "issue_date",
    "licence_number",
    "manufacturer_id",
    "address",
    "state",
    "manufacturer",
)


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def parse_manufacturers_xml(xml_path: str | Path) -> list[dict]:
    """XML (실제론 .xls 확장자) 파일을 파싱해 entry dict 리스트 반환.

    각 dict 키: issue_date, licence_number, manufacturer_id, address, state,
    manufacturer, normalized(추가 필드).
    """
    path = Path(xml_path)
    if not path.exists():
        raise FileNotFoundError(f"Manufacturer XML 파일 없음: {path}")

    # 일부 파일이 루트 없이 <viewentries> 하나로 시작하므로 그냥 파싱 시도.
    tree = ET.parse(str(path))
    root = tree.getroot()
    entries: list[dict] = []
    for ve in root.findall(".//viewentry"):
        rec: dict = {}
        for fld in _ENTRY_FIELDS:
            rec[fld] = _text(ve.find(fld))
        rec["normalized"] = normalize_company_name(rec.get("manufacturer", ""))
        entries.append(rec)
    return entries


# ---------------------------------------------------------------------------
# 인덱스 빌드 및 매칭
# ---------------------------------------------------------------------------

def build_manufacturer_index(entries: Iterable[dict]) -> dict[str, dict]:
    """정규화 키 → entry dict 매핑.

    같은 정규화 키가 여러 번 나오면 먼저 등장한 것이 유지되고, 나머지는
    `_duplicates` 리스트에 누적.
    """
    index: dict[str, dict] = {}
    for rec in entries:
        key = rec.get("normalized") or normalize_company_name(rec.get("manufacturer", ""))
        if not key:
            continue
        if key in index:
            index[key].setdefault("_duplicates", []).append(rec)
        else:
            # 원본 rec 오염 방지: 얕은 복사
            index[key] = dict(rec)
    return index


def load_aliases(alias_json_path: str | Path) -> dict[str, str]:
    """manufacturer_aliases.json 읽어서 {정규화 키 → survivors canonical} 맵 반환."""
    path = Path(alias_json_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        doc = json.load(fp)
    raw = doc.get("aliases", {}) or {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        out[normalize_company_name(k)] = v
    return out


def _token_set(s: str) -> set[str]:
    """공백 분리 → 5자 이상 토큰만 set."""
    return {t for t in re.split(r"[^a-zA-Z0-9]+", s.lower()) if len(t) >= 5}


def match_company(
    company_name: str,
    index: dict[str, dict],
    aliases: dict[str, str] | None = None,
) -> dict | None:
    """회사명을 manufacturer index 에 매칭.

    반환: 매칭된 entry dict + {"match_type": "exact|alias|substring", "match_key": ...}
          매칭 실패 시 None.
    """
    if not company_name:
        return None
    key = normalize_company_name(company_name)
    if not key:
        return None

    # 1차: 정확 일치
    if key in index:
        out = dict(index[key])
        out["match_type"] = "exact"
        out["match_key"] = key
        return out

    # 2차: alias 통과 후 재시도.
    # aliases: normalized_tga_key → survivors_canonical 이므로, 역방향으로
    # "survivors canonical == company_name(normalized)" 인 alias 키 전체를
    # 인덱스에서 찾아본다.
    aliases = aliases or {}
    if aliases:
        # company_name 이 survivors canonical 라면: alias value 가 key 와 같은
        # 모든 normalized_tga_key 를 index 에서 찾기
        canon_candidates = [tga_key for tga_key, canon in aliases.items() if canon == company_name or canon == key]
        for tga_key in canon_candidates:
            if tga_key in index:
                out = dict(index[tga_key])
                out["match_type"] = "alias"
                out["match_key"] = tga_key
                return out
        # 반대 방향: company_name(normalized) 자체가 alias 키인 경우
        if key in aliases:
            canon = aliases[key]
            if canon in index:
                out = dict(index[canon])
                out["match_type"] = "alias"
                out["match_key"] = canon
                return out

    # 3차: substring + 토큰 겹침.
    # 조건: key 길이 >= 5, 인덱스 키와 substring (한쪽이 다른 쪽 포함) 관계,
    # 그리고 원문 기준 토큰 2개 이상 겹침 (false positive 방지).
    if len(key) < 5:
        return None
    src_tokens = _token_set(company_name)
    best: dict | None = None
    for idx_key, rec in index.items():
        if len(idx_key) < 5:
            continue
        if not (key in idx_key or idx_key in key):
            continue
        overlap = src_tokens & _token_set(rec.get("manufacturer", ""))
        if len(overlap) < 2:
            continue
        out = dict(rec)
        out["match_type"] = "substring"
        out["match_key"] = idx_key
        out["_overlap"] = sorted(overlap)
        # 더 긴 substring 우선 (더 구체적)
        if best is None or len(idx_key) > len(best.get("match_key", "")):
            best = out
    return best


__all__ = [
    "parse_manufacturers_xml",
    "normalize_company_name",
    "build_manufacturer_index",
    "load_aliases",
    "match_company",
]
