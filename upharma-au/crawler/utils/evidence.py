# 근거 문구(evidence_text) 생성 — OpenAI gpt-4o-mini (환경변수 OPENAI_API_KEY).

from __future__ import annotations

import os

from openai import OpenAI


def translate_to_korean(text: str) -> str:
    """영어 등 원문을 한국어로 번역한다. 실패 시 원문 그대로."""
    raw = text or ""
    if not raw.strip():
        return raw
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return raw
    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "아래 텍스트를 한국어로 번역해라. 원문에 없는 내용 추가 금지."
                    ),
                },
                {"role": "user", "content": raw},
            ],
            temperature=0.2,
        )
        choice = resp.choices[0].message.content
        out = (choice or "").strip()
        return out if out else raw
    except Exception:
        return raw


def _summarize_estimate_evidence_ko(inn: str, raw_text: str) -> str:
    """ESTIMATE: TGA 미승인·조건부 근거를 한국어 300자 이내로 요약(1회 호출). 실패 시 긴 번역 폴백."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return translate_to_korean(raw_text[:1500])
    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{inn}의 호주 TGA 미승인 또는 조건부 수출 근거를 아래 텍스트에서 찾아 "
                        "한국어 300자 이내로 요약해라. 텍스트에 없는 내용 추가 금지.\n\n"
                        f"{raw_text[:4000]}"
                    ),
                },
            ],
            temperature=0.2,
        )
        choice = resp.choices[0].message.content
        out = (choice or "").strip()
        return out if out else translate_to_korean(raw_text[:1500])
    except Exception:
        return translate_to_korean(raw_text[:1500])


def build_evidence_text(pricing_case: str, raw_text: str, inn: str) -> dict[str, str]:
    """영어 발췌와 한국어 근거/번역을 담은 dict를 반환한다."""
    raw = raw_text or ""

    if pricing_case in ("DIRECT", "COMPONENT_SUM"):
        en_text = raw[:300]
        ko_text = translate_to_korean(en_text)
        return {"evidence_text": en_text, "evidence_text_ko": ko_text}

    # ESTIMATE: 프롬프트 요약 1회 호출, 실패 시 긴 텍스트 번역 폴백
    en_text = raw[:300]
    ko_text = _summarize_estimate_evidence_ko(inn, raw)
    return {"evidence_text": en_text, "evidence_text_ko": ko_text}
