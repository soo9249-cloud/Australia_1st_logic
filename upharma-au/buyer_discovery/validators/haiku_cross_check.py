"""Claude Haiku 교차검증기 — 바이어 발굴 Stage 2 전용.

용도 (2가지):
  1. extract_therapy_from_description(description, brands)
     - GPCE exhibitorDescription + representedBrands 를 읽고
       표준 치료영역 (ATC 대분류) 목록으로 요약.
     - Perplexity 대체 (비용 1/5).

  2. validate_revenue(perplexity_result, local_evidence, manufacturer_info)
     - Perplexity 매출 응답을 로컬 3-소스와 대조해서
       환각 여부 판정 + 최종 등급 + confidence 반환.

모델: `claude-haiku-4-5-20251001` 고정 (CLAUDE.md 절대 규칙 — Sonnet/Opus 금지).

환각 방지 설계:
  · 응답은 항상 JSON 스키마 강제.
  · 로컬 증거와 2개 이상 일치하면 high confidence.
  · 근거 부족 시 "unknown" / 낮은 confidence.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

try:
    import anthropic  # type: ignore
except Exception:  # pragma: no cover
    anthropic = None  # type: ignore

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 60.0
_MAX_RETRIES = 3


# ═══════════════════════════════════════════════════════════════════════
# 공통 래퍼
# ═══════════════════════════════════════════════════════════════════════

_CLIENT: Any = None


def _get_client() -> Any:
    """Anthropic 클라이언트 lazy 초기화. 실패 시 None."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if anthropic is None:
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        _CLIENT = anthropic.Anthropic(api_key=key, timeout=_TIMEOUT)
        return _CLIENT
    except Exception as exc:
        print(f"[haiku] 클라이언트 초기화 실패: {exc}", flush=True)
        return None


def _call_haiku(
    system: str,
    user: str,
    *,
    max_tokens: int = 600,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Haiku 단일 호출. JSON 응답 기대.

    반환:
      · 성공: {'parsed': dict, 'raw': str, 'usage': dict}
      · 실패: {'error': str}
    """
    client = _get_client()
    if client is None:
        return {"error": "Anthropic 클라이언트 없음 (ANTHROPIC_API_KEY 확인)"}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            # Haiku 응답에서 text 추출
            text = "".join(
                blk.text for blk in resp.content
                if getattr(blk, "type", None) == "text"
            )
            parsed: dict[str, Any] | None = None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # 마크다운 코드블록 감싼 JSON 시도
                stripped = text.strip()
                if stripped.startswith("```"):
                    # ```json\n...\n``` 형태 제거
                    inner = stripped.strip("`").lstrip("json").strip()
                    try:
                        parsed = json.loads(inner)
                    except json.JSONDecodeError:
                        parsed = None
            usage = getattr(resp, "usage", None)
            usage_dict = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            } if usage else {}
            return {"parsed": parsed, "raw": text, "usage": usage_dict}
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
    return {"error": f"Haiku 호출 실패: {last_exc!r}"}


# ═══════════════════════════════════════════════════════════════════════
# 기능 1: GPCE description → 치료영역
# ═══════════════════════════════════════════════════════════════════════

# 표준 치료영역 어휘 (Haiku 출력 제약용)
_STANDARD_THERAPY_AREAS_EN: tuple[str, ...] = (
    "Oncology", "Hematology", "Cardiovascular", "Respiratory",
    "CNS_Neurology", "Psychiatry", "Immunology", "Rheumatology",
    "Infectious_Disease", "Vaccine", "Diabetes_Endocrine",
    "Gastrointestinal", "Dermatology", "Ophthalmology", "Urology",
    "Womens_Health", "Mens_Health", "Rare_Disease", "Imaging_Contrast",
    "Pain_Anesthesia", "Allergy", "OTC_Consumer_Health", "Nutrition",
    "Medical_Device",
)

_KO_MAP: dict[str, str] = {
    "Oncology": "항암", "Hematology": "혈액", "Cardiovascular": "심혈관",
    "Respiratory": "호흡기", "CNS_Neurology": "중추신경",
    "Psychiatry": "정신과", "Immunology": "면역", "Rheumatology": "류마티스",
    "Infectious_Disease": "감염", "Vaccine": "백신",
    "Diabetes_Endocrine": "당뇨·내분비", "Gastrointestinal": "소화기",
    "Dermatology": "피부", "Ophthalmology": "안과", "Urology": "비뇨기",
    "Womens_Health": "여성건강", "Mens_Health": "남성건강",
    "Rare_Disease": "희귀질환", "Imaging_Contrast": "조영·영상진단",
    "Pain_Anesthesia": "진통·마취", "Allergy": "알레르기",
    "OTC_Consumer_Health": "일반의약품", "Nutrition": "영양제",
    "Medical_Device": "의료기기",
}


def extract_therapy_from_description(
    company_name: str,
    description: str | None,
    represented_brands: list[str] | None = None,
) -> dict[str, Any]:
    """GPCE description + brands 에서 치료영역 추출.

    반환:
      {
        'areas_en': list[str],          # 표준 영문 영역 (위 24개 중)
        'areas_kr': list[str],          # 한국어 대응
        'confidence': float,            # 0.0~1.0
        'reasoning': str,               # 1문장
      }
    description 비어있으면 unknown 반환.
    """
    empty = {
        "areas_en": [],
        "areas_kr": [],
        "confidence": 0.0,
        "reasoning": "description 없음",
    }
    if not description or len(description.strip()) < 20:
        return empty

    brands_str = ", ".join(represented_brands or []) or "(없음)"
    allowed = ", ".join(_STANDARD_THERAPY_AREAS_EN)

    system = (
        "You are a pharmaceutical analyst. "
        "Given a company description and brand list, identify the primary "
        "therapeutic areas (drug classes) the company focuses on in Australia.\n\n"
        "CONSTRAINTS:\n"
        "- Pick ONLY from the allowed list below (copy exact spelling).\n"
        "- 1-5 areas max.\n"
        "- If description is too generic, return empty array.\n"
        "- Respond with valid JSON only (no markdown, no prose outside JSON).\n\n"
        f"Allowed areas: {allowed}"
    )
    user = (
        f"Company: {company_name}\n"
        f"Brands: {brands_str}\n\n"
        f"Description:\n{description[:2000]}\n\n"
        "Output JSON:\n"
        "{\n"
        '  "areas": ["Area1", "Area2"],\n'
        '  "confidence": 0.0-1.0,\n'
        '  "reasoning": "1 sentence"\n'
        "}"
    )
    result = _call_haiku(system, user, max_tokens=400)
    if "error" in result:
        return {**empty, "reasoning": result["error"]}

    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        return {**empty, "reasoning": "Haiku JSON 파싱 실패"}

    raw_areas = parsed.get("areas") or []
    # 표준 어휘 외 제거 + dedupe
    valid_en = [a for a in raw_areas if a in _STANDARD_THERAPY_AREAS_EN]
    # 순서 유지 dedupe
    seen: set[str] = set()
    areas_en: list[str] = []
    for a in valid_en:
        if a not in seen:
            seen.add(a)
            areas_en.append(a)
    areas_kr = [_KO_MAP.get(a, a) for a in areas_en]
    conf = float(parsed.get("confidence") or 0.0)

    return {
        "areas_en": areas_en,
        "areas_kr": areas_kr,
        "confidence": max(0.0, min(1.0, conf)),
        "reasoning": str(parsed.get("reasoning") or "")[:300],
    }


# ═══════════════════════════════════════════════════════════════════════
# 기능 2: Perplexity 매출 응답 3-소스 교차검증
# ═══════════════════════════════════════════════════════════════════════

_REVENUE_RANK_SCORE: dict[str, int] = {
    "TOP 5": 100,
    "TOP 10": 85,
    "TOP 20": 70,
    "TOP 50": 50,
    "niche": 30,
    "unknown": 0,
}


def validate_revenue(
    company_name: str,
    perplexity_result: dict[str, Any],
    local_evidence: dict[str, Any],
    manufacturer_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perplexity 매출 응답 + 로컬 증거 교차검증 → 최종 등급·점수 판정.

    Args:
      company_name: 회사명 (로그·프롬프트용)
      perplexity_result: sources.perplexity_adapter.query_revenue() 반환
      local_evidence: dict with keys:
        - 'sources' (list[str]): ['tga','pbs','ma','gbma','gpce','tga_inn_match']
        - 'tga_artg_count' (int)
        - 'is_ma' / 'is_gbma' / 'is_gpce' (bool)
      manufacturer_info: dict with 'has_factory' (bool), 'address' etc. 또는 None

    반환:
      {
        'rank': str,                    # 최종 등급
        'score': int,                   # 0~100
        'confidence': float,            # 0.0~1.0
        'reasoning': str,
        'evidence_urls': list[str],     # Perplexity citations 중 2개+
      }
    """
    # Perplexity 에러 체크
    if "error" in perplexity_result or not perplexity_result.get("parsed"):
        return {
            "rank": "unknown",
            "score": 0,
            "confidence": 0.0,
            "reasoning": "Perplexity 결과 없음",
            "evidence_urls": [],
        }

    px_parsed = perplexity_result["parsed"]
    px_rank = (px_parsed.get("rank") or "unknown").strip()
    px_reasoning = str(px_parsed.get("reasoning") or "")
    citations = perplexity_result.get("citations") or []
    # Perplexity JSON 응답 내부에 sources 도 있음
    px_sources = px_parsed.get("sources") or []
    if isinstance(px_sources, list):
        for s in px_sources:
            if isinstance(s, str) and s.startswith("http") and s not in citations:
                citations.append(s)

    # 로컬 증거 요약
    has_tga = "tga" in (local_evidence.get("sources") or [])
    has_pbs = "pbs" in (local_evidence.get("sources") or [])
    is_ma = bool(local_evidence.get("is_ma"))
    is_gbma = bool(local_evidence.get("is_gbma"))
    is_gpce = bool(local_evidence.get("is_gpce"))
    tga_count = int(local_evidence.get("tga_artg_count") or 0)
    has_factory = bool(manufacturer_info and manufacturer_info.get("has_factory"))

    system = (
        "You are a fact-checker validating pharmaceutical market revenue claims. "
        "Cross-reference the Perplexity claim against local evidence from 3 sources:\n"
        "  (1) Local buyer database (source flags, TGA ARTG count)\n"
        "  (2) TGA Manufacturer Licence registry (factory presence)\n"
        "  (3) Association memberships (MA = Big Pharma, GBMA = Generics)\n\n"
        "RULES:\n"
        "- Require at least 2 citation URLs from Perplexity.\n"
        "- If claim says 'TOP 5/10' but local evidence shows only small ARTG count "
        "  and no factory and not MA/GBMA member → downgrade confidence.\n"
        "- If claim says 'niche' but company is MA member with 10+ ARTGs → upgrade.\n"
        "- Final rank must be from: TOP 5, TOP 10, TOP 20, TOP 50, niche, unknown.\n"
        "- Respond with valid JSON only."
    )
    user = (
        f"Company: {company_name}\n\n"
        f"Perplexity claim:\n"
        f"  rank: {px_rank}\n"
        f"  reasoning: {px_reasoning}\n"
        f"  citations ({len(citations)}): {citations[:4]}\n\n"
        f"Local evidence:\n"
        f"  - TGA sponsor: {has_tga}\n"
        f"  - PBS listed: {has_pbs}\n"
        f"  - MA member (Big Pharma assoc): {is_ma}\n"
        f"  - GBMA member (Generics assoc): {is_gbma}\n"
        f"  - GPCE conference attendee: {is_gpce}\n"
        f"  - Australian TGA ARTG count: {tga_count}\n"
        f"  - Has Australian manufacturing facility: {has_factory}\n\n"
        "Output JSON:\n"
        "{\n"
        '  "rank": "TOP 5|TOP 10|TOP 20|TOP 50|niche|unknown",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "reasoning": "1-2 sentences on agreement/disagreement with local evidence"\n'
        "}"
    )
    result = _call_haiku(system, user, max_tokens=400)
    if "error" in result:
        return {
            "rank": px_rank if px_rank in _REVENUE_RANK_SCORE else "unknown",
            "score": _REVENUE_RANK_SCORE.get(px_rank, 0),
            "confidence": 0.3,  # Haiku 실패 시 Perplexity 만 신뢰
            "reasoning": f"Haiku 검증 실패, Perplexity 원본 사용: {result['error']}",
            "evidence_urls": citations[:5],
        }

    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        return {
            "rank": px_rank if px_rank in _REVENUE_RANK_SCORE else "unknown",
            "score": _REVENUE_RANK_SCORE.get(px_rank, 0),
            "confidence": 0.3,
            "reasoning": "Haiku JSON 파싱 실패",
            "evidence_urls": citations[:5],
        }

    final_rank = (parsed.get("rank") or "unknown").strip()
    if final_rank not in _REVENUE_RANK_SCORE:
        final_rank = "unknown"
    confidence = float(parsed.get("confidence") or 0.0)
    score = _REVENUE_RANK_SCORE[final_rank]
    # 근거 URL 2개 미만 시 confidence 하향
    if len(citations) < 2:
        confidence = min(confidence, 0.4)

    return {
        "rank": final_rank,
        "score": int(score * max(0.0, min(1.0, confidence))),
        "confidence": max(0.0, min(1.0, confidence)),
        "reasoning": str(parsed.get("reasoning") or "")[:400],
        "evidence_urls": citations[:5],
    }


__all__ = [
    "extract_therapy_from_description",
    "validate_revenue",
]
