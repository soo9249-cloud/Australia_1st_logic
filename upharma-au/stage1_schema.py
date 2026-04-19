"""1공정 시장분석 보고서 PDF v3 — 공통 Pydantic 스키마 (8품목 + 신약 공용).

프로덕션: 크롤러/Haiku JSON → ReportR1Payload 검증 → report_generator.render_pdf(payload, path)
"""

from __future__ import annotations

import re
import warnings
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 금지 표현 (v2 잔재)
_FORBIDDEN_PHRASES = re.compile(
    r"가능\s*\(\s*High\s+Confidence\s*\)|Case\s+[A-F]|Case\s+[1-6]|신뢰도\s*[0-9]",
    re.IGNORECASE,
)
# 약품명·성분명 뒤 한글 괄호 병기 (규제 용어 병기는 제외)
_INN_KOREAN_SUFFIX = re.compile(r"[A-Za-z][A-Za-z0-9\-]*\s*\([가-힣]")


class PerplexityPaper(BaseModel):
    title: str = ""
    source: str = ""
    url: str = ""
    summary_ko: str = ""


class DbReference(BaseModel):
    name: str = ""
    desc_ko: str = ""
    url: str | None = None


class ReportR1Payload(BaseModel):
    # 헤더
    product_name: str = ""
    inn: str = ""
    strength_form: str = ""
    hs_code: str = ""
    report_date: str = ""

    # [1] 판정
    verdict: Literal["가능", "조건부", "불가"] = "조건부"
    verdict_summary: str = Field(default="", max_length=200)

    # [2] 판정 근거
    basis_market_medical: str = ""
    basis_competitor_brands: str = ""
    basis_regulatory: str = ""
    basis_trade: str = ""
    basis_reference_price: str = ""

    # [3] 시장 진출 전략
    strat_entry_channel: str = ""
    strat_partner_direction: str = ""
    strat_price_positioning: str = ""
    strat_risk_conditions: str = ""

    # [4] 근거 및 출처
    refs_perplexity: list[PerplexityPaper] = Field(default_factory=list)
    refs_databases: list[DbReference] = Field(default_factory=list)

    @field_validator("verdict_summary", mode="before")
    @classmethod
    def _trim_summary(cls, v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        return s[:200]

    @model_validator(mode="after")
    def _validate_forbidden_and_inn(self) -> ReportR1Payload:
        parts: list[str] = []
        for k, v in self.model_dump().items():
            if k in ("refs_perplexity", "refs_databases"):
                continue
            parts.append(str(v))
        blob = " ".join(parts)
        for p in self.refs_perplexity:
            blob += f" {p.title} {p.source} {p.url} {p.summary_ko}"
        for d in self.refs_databases:
            blob += f" {d.name} {d.desc_ko} {d.url or ''}"

        if _FORBIDDEN_PHRASES.search(blob):
            raise ValueError(
                "금지 표현이 포함되었습니다: "
                "'가능 (High Confidence)', Case A~F/1~6, '신뢰도 숫자' 등 v2 잔재 문구를 제거하세요."
            )

        inn = self.inn or ""
        if _INN_KOREAN_SUFFIX.search(inn):
            warnings.warn(
                "성분명(inn)에 한글 병기 패턴이 감지되었습니다. "
                "약품명·성분명은 영문 단독, 제도 용어만 한글 병기 규칙을 확인하세요.",
                UserWarning,
                stacklevel=2,
            )
        return self


def coerce_dict_to_report_r1(raw: dict[str, Any]) -> ReportR1Payload:
    """CC v2 페이로드 등 느슨한 dict → v3 필드로 보정 (누락 시 빈 문자열).

    render_api.py 가 아직 v2 필드를 넘길 때 render_pdf 가 깨지지 않도록 방어용.
    """
    d = dict(raw)

    # v2 row / blocks / meta 흔적 매핑
    if "product_name" not in d and d.get("product_name_ko"):
        d["product_name"] = str(d.get("product_name_ko") or "")
    if "inn" not in d and d.get("inn_normalized"):
        d["inn"] = str(d.get("inn_normalized") or "")
    if "strength_form" not in d:
        st = str(d.get("strength") or "").strip()
        form = str(d.get("dosage_form") or "").strip()
        d["strength_form"] = " ".join(x for x in (st, form) if x).strip() or ""
    if "hs_code" not in d and d.get("hs_code_6") is not None:
        s = str(d.get("hs_code_6") or "").strip()
        d["hs_code"] = f"{s[:4]}.{s[4:6]}" if len(s) >= 6 else s
    if "report_date" not in d:
        from datetime import datetime

        d["report_date"] = datetime.now().strftime("%Y-%m-%d")

    blocks = d.get("blocks") if isinstance(d.get("blocks"), dict) else {}
    meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}

    ev = str(meta.get("export_viable") or "").lower()
    if "verdict" not in d:
        d["verdict"] = (
            "가능" if ev == "viable" else "불가" if ev == "not_viable" else "조건부"
        )
    if "verdict_summary" not in d:
        d["verdict_summary"] = str(meta.get("reason_code") or meta.get("situation_summary") or "")[
            :200
        ]

    if "basis_market_medical" not in d:
        d["basis_market_medical"] = str(blocks.get("block2_market") or "")
    if "basis_competitor_brands" not in d:
        d["basis_competitor_brands"] = str(blocks.get("block2_channel") or "")
    if "basis_regulatory" not in d:
        d["basis_regulatory"] = str(blocks.get("block2_regulatory") or "")
    if "basis_trade" not in d:
        proc = str(blocks.get("block2_procurement") or "")
        trade = str(blocks.get("block2_trade") or "")
        d["basis_trade"] = "\n".join(x for x in (trade, proc) if x).strip()
    if "basis_reference_price" not in d:
        d["basis_reference_price"] = ""

    if "strat_entry_channel" not in d:
        d["strat_entry_channel"] = str(blocks.get("block3_channel") or "")
    if "strat_partner_direction" not in d:
        d["strat_partner_direction"] = str(blocks.get("block3_partners") or "")
    if "strat_price_positioning" not in d:
        d["strat_price_positioning"] = str(blocks.get("block3_pricing") or "")
    if "strat_risk_conditions" not in d:
        d["strat_risk_conditions"] = str(blocks.get("block3_risks") or "")

    if "refs_perplexity" not in d:
        refs_in = d.get("refs")
        out: list[dict[str, Any]] = []
        if isinstance(refs_in, list):
            for r in refs_in:
                if not isinstance(r, dict):
                    continue
                out.append(
                    {
                        "title": str(r.get("title") or ""),
                        "source": str(r.get("source") or ""),
                        "url": str(r.get("url") or ""),
                        "summary_ko": str(
                            r.get("korean_summary")
                            or r.get("summary_ko")
                            or r.get("tldr")
                            or r.get("abstract")
                            or ""
                        ),
                    }
                )
        d["refs_perplexity"] = out

    if "refs_databases" not in d:
        d["refs_databases"] = []

    # 최종: 알 수 없는 키는 무시하기 위해 ReportR1Payload 허용 키만 통과
    allowed = set(ReportR1Payload.model_fields.keys())
    clean = {k: v for k, v in d.items() if k in allowed}
    return ReportR1Payload.model_validate(clean)
