# PubChem synonyms 로 WHO INN 에 가까운 표준명을 추정 (온라인).
# 추가 (2026-04-19): 오프라인 염·에스터 접미사 제거 헬퍼 — FDC/TGA set 매칭용.

from __future__ import annotations

import re

import httpx


def normalize_inn(drug_name: str) -> str:
    """
    PubChem API로 drug_name의 WHO INN 표준명 반환.
    실패 시 원본 소문자 반환.
    """
    try:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{drug_name}/synonyms/JSON"
        r = httpx.get(url, timeout=10)
        if r.status_code != 200:
            return drug_name.lower()
        synonyms = r.json()["InformationList"]["Information"][0]["Synonym"]
        for s in synonyms:
            if len(s) > 4 and s.isalpha() and s[0].isupper() and s[1:].islower():
                return s.lower()
        return drug_name.lower()
    except Exception:
        return drug_name.lower()


# FDC·TGA set 매칭용 — 흔한 염/에스터/프로드러그 접미사 목록.
# base INN 뒤에 공백·하이픈 으로 붙는 경우 제거한다.
#   "fluticasone propionate" → "fluticasone"
#   "salmeterol xinafoate"   → "salmeterol"
#   "atorvastatin calcium"   → "atorvastatin"
_INN_SALT_SUFFIXES: tuple[str, ...] = (
    "propionate", "furoate", "xinafoate", "fumarate", "maleate",
    "citrate", "tartrate", "sulfate", "sulphate", "phosphate",
    "acetate", "mesylate", "besylate", "besilate", "tosylate",
    "succinate", "gluconate", "lactate", "dipropionate",
    "hydrochloride", "hcl", "dihydrochloride",
    "sodium", "potassium", "calcium", "magnesium",
    "hemihydrate", "monohydrate", "dihydrate", "trihydrate",
    "hydrate", "anhydrous",
    "ethyl", "esters", "ester",    # omega-3-acid ethyl esters → omega-3-acid
)


# 구분자: 공백·쉼표·세미콜론·슬래시·'+'·'&'·'and'·'with'
_INN_SPLIT_RE = re.compile(r"\s*(?:\+|,|;|/|&|\band\b|\bwith\b)\s*", flags=re.IGNORECASE)


def strip_inn_salt(token: str) -> str:
    """단일 INN 토큰에서 꼬리 염/에스터/수화물 수식어 제거.

      "fluticasone propionate" → "fluticasone"
      "salmeterol xinafoate"   → "salmeterol"
      "hydroxycarbamide"       → "hydroxycarbamide"
      ""                       → ""
    """
    t = (token or "").strip().lower()
    if not t:
        return ""
    # 반복 제거 — "sodium phosphate" 같이 2개 겹친 경우 대응
    changed = True
    while changed:
        changed = False
        for suf in _INN_SALT_SUFFIXES:
            if t.endswith(" " + suf) or t.endswith("-" + suf):
                t = t[: -(len(suf) + 1)].strip()
                changed = True
                break
            if t == suf:
                t = ""
                changed = True
                break
    return t


def extract_inn_set(*texts: str | None) -> frozenset[str]:
    """여러 텍스트 필드(drug_name / li_drug_name / schedule_form / active_ingredients …)
    에서 base INN 토큰 set 를 추출.

    규칙:
      1. 각 텍스트를 "+", ",", ";", "/", "&", "and", "with" 로 split
      2. 각 조각 앞뒤 공백·괄호·숫자+단위(200mg, 50mcg 등) 제거
      3. 남은 문자열에 strip_inn_salt 적용
      4. 빈 문자열·숫자만 남은 토큰·불용어 버림

    예) drug_name="fluticasone propionate; salmeterol xinafoate"
        → frozenset({"fluticasone", "salmeterol"})
    """
    result: set[str] = set()
    for raw in texts:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        for piece in _INN_SPLIT_RE.split(text):
            p = piece.strip()
            if not p:
                continue
            # 괄호 안 내용 제거 — "(Eqv ...)", "(anhydrous)" 등
            p = re.sub(r"\([^)]*\)", "", p).strip()
            # 숫자+단위 토큰 제거 — "200 mg", "50mcg" 등
            p = re.sub(
                r"\b\d[\d.,]*\s*(?:mg|mcg|µg|g|ml|mL|iu|IU|units?|%)\b",
                "",
                p,
                flags=re.IGNORECASE,
            ).strip()
            # 여러 공백 정규화
            p = re.sub(r"\s+", " ", p)
            base = strip_inn_salt(p)
            if not base:
                continue
            # 순수 숫자·한 글자 토큰 제외
            if base.isdigit() or len(base) < 3:
                continue
            result.add(base)
    return frozenset(result)
