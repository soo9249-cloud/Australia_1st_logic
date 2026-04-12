# PubChem synonyms로 WHO INN에 가까운 표준명을 추정한다.

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
