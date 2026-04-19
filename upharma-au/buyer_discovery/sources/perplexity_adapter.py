"""Perplexity API 어댑터 — Stage 2 바이어 정보 조사 전용.

용도 (4가지):
  1. query_revenue            — 매출 규모 등급 (TOP 5/10/20/50/niche/unknown)
  2. query_therapeutic_areas  — 주력 치료영역 (Oncology, Respiratory, ...)
  3. query_pharmacy_chain     — 호주 약국 체인 운영 여부
  4. query_import_experience  — 수입·유통 업력

프롬프트 원칙:
  · 모두 JSON 응답 강제 (`response_format`)
  · 최소 2개 근거 URL 강제 (citations)
  · 호주 특화 쿼리 (회사명 뒤에 "in Australia" 명시)

Perplexity 모델: `sonar-pro`  (온라인 검색 특화, Anthropic Haiku 와 다른 역할)
Anthropic Claude Haiku 는 검증 단계에서만 사용 — 본 모듈은 순수 Perplexity.

CLAUDE.md 규칙: AI 모델 하드코딩 = Perplexity `sonar-pro` 고정.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar-pro"
_TIMEOUT = 60.0
_MAX_RETRIES = 3


def _get_api_key() -> str | None:
    """`.env` 에서 PERPLEXITY_API_KEY 읽기. 없으면 None."""
    return os.environ.get("PERPLEXITY_API_KEY") or None


def _call_perplexity(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 600,
    response_format_json: bool = True,
) -> dict[str, Any]:
    """Perplexity API 공통 호출. 실패 시 {'error': str} 반환.

    성공 시 반환:
      {
        'raw_answer': str,          # JSON 문자열 또는 평문
        'parsed': dict | None,      # JSON 파싱 성공 시
        'citations': list[str],     # Perplexity 가 인용한 URL 들
        'model': str,
        'usage': dict,              # tokens prompt/completion
      }
    """
    key = _get_api_key()
    if not key:
        return {"error": "PERPLEXITY_API_KEY 환경변수 없음"}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # NOTE: Perplexity `response_format: json_schema` 를 빈 스키마로 쓰면
    # 실제 응답이 `{}` 만 반환되는 문제 확인 (2026-04-20 실측).
    # 해결: response_format 파라미터 제거 + 프롬프트로 JSON 강제 (sonar-pro 는
    # 시스템 프롬프트 지시만으로도 JSON 형식 잘 반환).
    # response_format_json 인자는 호환성 위해 유지하되 실제론 사용 안 함.

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            r = httpx.post(_PERPLEXITY_URL, headers=headers, json=payload, timeout=_TIMEOUT)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"[perplexity] 429 rate-limit, {wait}s 대기", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"] if data.get("choices") else ""
            parsed: dict[str, Any] | None = None
            # 1차: 순수 JSON 파싱
            try:
                parsed = json.loads(content) if content else None
            except json.JSONDecodeError:
                parsed = None
            # 2차: markdown 코드블록 (```json ... ```) 감싼 케이스 대응
            if parsed is None and content:
                stripped = content.strip()
                if stripped.startswith("```"):
                    inner = stripped.strip("`")
                    # "json\n..." 형태 앞 접두어 제거
                    if inner.lower().startswith("json"):
                        inner = inner[4:]
                    inner = inner.strip()
                    try:
                        parsed = json.loads(inner)
                    except json.JSONDecodeError:
                        pass
            # 3차: 응답 안에 JSON 객체 substring 추출 (본문 앞뒤 설명문 케이스)
            if parsed is None and content:
                import re as _re
                m = _re.search(r"\{.*\}", content, _re.DOTALL)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        pass
            return {
                "raw_answer": content,
                "parsed": parsed,
                "citations": data.get("citations") or [],
                "model": data.get("model"),
                "usage": data.get("usage") or {},
            }
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(3 * (attempt + 1))
    return {"error": f"Perplexity 호출 실패: {last_exc!r}"}


# ═══════════════════════════════════════════════════════════════════════
# 4가지 조사 쿼리
# ═══════════════════════════════════════════════════════════════════════

def query_revenue(company_name: str) -> dict[str, Any]:
    """호주 내 매출 규모 등급 조사.

    반환 `parsed` 기대 키: rank, reasoning, sources
      · rank ∈ {'TOP 5','TOP 10','TOP 20','TOP 50','niche','unknown'}
    """
    system = (
        "You are a pharmaceutical market research analyst specializing in "
        "the Australian market. Answer ONLY in valid JSON. "
        "Include at least 2 source URLs in the 'sources' array. "
        "If data is insufficient, use 'unknown'."
    )
    user = (
        f"Company: {company_name} (Australia)\n\n"
        "Question: What is this company's annual revenue ranking within "
        "the Australian pharmaceutical market (2023-2024)?\n\n"
        "Return JSON with keys:\n"
        "  - 'rank': one of 'TOP 5', 'TOP 10', 'TOP 20', 'TOP 50', 'niche', 'unknown'\n"
        "  - 'reasoning': 1-2 sentence justification citing sources\n"
        "  - 'sources': array of 2+ URLs\n"
    )
    return _call_perplexity(system, user, max_tokens=500)


def query_therapeutic_areas(company_name: str) -> dict[str, Any]:
    """주력 치료영역 (ATC 대분류) 조사.

    반환 `parsed` 기대 키: areas, reasoning, sources
      · areas: list[str] — 예: ['Oncology', 'Respiratory', 'Cardiovascular']
    """
    system = (
        "You are a pharmaceutical market research analyst. "
        "Answer ONLY in valid JSON. "
        "Include at least 2 source URLs. "
        "Use standard ATC-like therapeutic area names."
    )
    user = (
        f"Company: {company_name} (Australia or global, but focus on Australian portfolio)\n\n"
        "Question: What are this company's primary therapeutic areas "
        "(drug classes / treatment domains)?\n\n"
        "Common categories include: Oncology, Cardiovascular, Respiratory, "
        "CNS/Neurology, Immunology, Infectious Disease, Diabetes/Endocrine, "
        "Gastrointestinal, Dermatology, Ophthalmology, Rare Disease, Vaccine, "
        "Imaging/Contrast, Pain/Anesthesia, Hematology, Women's Health, "
        "OTC/Consumer Health, Nutrition\n\n"
        "Return JSON with keys:\n"
        "  - 'areas': array of 1-5 therapeutic area names\n"
        "  - 'reasoning': 1-2 sentences\n"
        "  - 'sources': array of 2+ URLs\n"
    )
    return _call_perplexity(system, user, max_tokens=500)


def query_pharmacy_chain(company_name: str) -> dict[str, Any]:
    """호주 약국 체인 운영 여부.

    반환 `parsed` 기대 키: has_chain, chain_names, reasoning, sources
    """
    system = (
        "You are a pharmaceutical market research analyst. "
        "Answer ONLY in valid JSON."
    )
    user = (
        f"Company: {company_name} (Australia)\n\n"
        "Question: Does this company own or operate pharmacy chains in Australia? "
        "(e.g., Chemist Warehouse, Priceline, TerryWhite Chemmart)\n\n"
        "Return JSON with keys:\n"
        "  - 'has_chain': 'Y', 'N', or 'unknown'\n"
        "  - 'chain_names': array of chain names (empty if N)\n"
        "  - 'reasoning': 1 sentence\n"
        "  - 'sources': array of 2+ URLs\n"
    )
    return _call_perplexity(system, user, max_tokens=400)


def query_import_experience(company_name: str) -> dict[str, Any]:
    """수입·유통 업력 조사.

    반환 `parsed` 기대 키: years_of_operation, import_categories, reasoning, sources
    """
    system = (
        "You are a pharmaceutical market research analyst. "
        "Answer ONLY in valid JSON."
    )
    user = (
        f"Company: {company_name} (Australia)\n\n"
        "Question: How many years has this company been operating as a "
        "pharmaceutical importer/distributor in Australia? "
        "What categories of medicines do they typically import?\n\n"
        "Return JSON with keys:\n"
        "  - 'years_of_operation': integer or 'unknown'\n"
        "  - 'import_categories': array of drug categories\n"
        "  - 'reasoning': 1-2 sentences\n"
        "  - 'sources': array of 2+ URLs\n"
    )
    return _call_perplexity(system, user, max_tokens=500)


__all__ = [
    "query_revenue",
    "query_therapeutic_areas",
    "query_pharmacy_chain",
    "query_import_experience",
]
