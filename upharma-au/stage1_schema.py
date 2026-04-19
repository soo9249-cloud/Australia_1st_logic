"""시장분석 보고서 PDF v3 — 공통 Pydantic 스키마 (8품목 + 신약 공용).

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


def _format_hs_code(row_hs: Any) -> str:
    """hs_code_6 숫자/문자열 → 3004.90 형식."""
    s = str(row_hs or "").strip()
    if len(s) >= 6:
        return f"{s[:4]}.{s[4:6]}"
    return s


def _sanitize_legacy_jargon(text: str) -> str:
    """Haiku/레거시에 섞인 v2 금지 표현 제거 (검증 통과용)."""
    if not text:
        return ""
    s = text
    s = re.sub(r"가능\s*\(\s*High\s+Confidence\s*\)", "가능", s, flags=re.IGNORECASE)
    s = re.sub(r"Case\s+[A-F]\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"Case\s+[1-6]\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"신뢰도\s*[0-9]+%?", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _format_reference_prices(row: dict[str, Any]) -> str:
    """au_products 행의 AEMP·DPMQ → 참고 가격 문장."""
    parts: list[str] = []
    for key, label in (
        ("aemp_aud", "AEMP(정부 승인 출고가)"),
        ("dpmq_aud", "DPMQ(최대처방량 총약가)"),
    ):
        v = row.get(key)
        if v is None or str(v).strip() == "":
            continue
        try:
            parts.append(f"{label} {float(v):.2f} AUD")
        except (TypeError, ValueError):
            parts.append(f"{label} {v} AUD")
    return " · ".join(parts) if parts else ""


def _verdict_from_export_viable(ev_raw: Any) -> Literal["가능", "조건부", "불가"]:
    ev = str(ev_raw or "").lower().strip()
    if ev == "viable":
        return "가능"
    if ev == "not_viable":
        return "불가"
    return "조건부"


def default_stage1_db_references() -> list[DbReference]:
    """시장조사 PDF 4-2 기본 행 (크롤·검색 소스 설명)."""
    return [
        DbReference(
            name="TGA ARTG",
            desc_ko="호주 치료제 등록부(ARTG) — 등록번호·스폰서·스케줄 조회",
            url="https://www.tga.gov.au/products/australian-register-therapeutic-goods-artg",
        ),
        DbReference(
            name="PBS Schedule",
            desc_ko="호주 의약품 급여제도 공개 스케줄 — item code·DPMQ·innovator 지위",
            url="https://www.pbs.gov.au",
        ),
        DbReference(
            name="Chemist Warehouse",
            desc_ko="호주 최대 약국 체인 소매가 참조",
            url="https://www.chemistwarehouse.com.au",
        ),
        DbReference(
            name="NSW Health Procurement",
            desc_ko="뉴사우스웨일스주 공공조달 계약 공시",
            url="https://buy.nsw.gov.au",
        ),
    ]


def _deep_scrub_forbidden_phrases(obj: Any) -> Any:
    """LLM 산출물에 남은 v2 금지 표현을 제거해 PDF 검증(model_validate)이 실패하지 않게 한다."""
    def _scrub_str(s: str) -> str:
        t = _sanitize_legacy_jargon(s)
        for _ in range(8):
            if not _FORBIDDEN_PHRASES.search(t):
                break
            t = _FORBIDDEN_PHRASES.sub("", t)
        return re.sub(r"\s{2,}", " ", t).strip()

    if isinstance(obj, str):
        return _scrub_str(obj)
    if isinstance(obj, dict):
        return {k: _deep_scrub_forbidden_phrases(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_scrub_forbidden_phrases(v) for v in obj]
    if isinstance(obj, BaseModel):
        return _deep_scrub_forbidden_phrases(obj.model_dump())
    return obj


def build_report_r1_payload_from_pipeline(
    row: dict[str, Any],
    blocks: dict[str, Any],
    refs: list[dict[str, Any]],
    meta: dict[str, Any],
) -> ReportR1Payload:
    """`/api/report/generate` 파이프라인 산출물 → ReportR1Payload (PDF v3).

    - block2_channel → 경쟁 브랜드·유통(호주 프롬프트와 정합)
    - block2_regulatory + block2_procurement → 규제·조달 통합
    - block2_trade → 무역 단독
    """
    from datetime import datetime

    b = {k: str(v or "") for k, v in (blocks or {}).items()}

    ev = meta.get("export_viable", row.get("export_viable"))
    verdict = _verdict_from_export_viable(ev)

    reason = str(meta.get("reason_code") or row.get("reason_code") or "").strip()
    if not reason:
        reason = str(row.get("situation_summary") or "").strip()
    verdict_summary = _sanitize_legacy_jargon(reason)[:200]

    st = str(row.get("strength") or "").strip()
    form = str(row.get("dosage_form") or "").strip()
    strength_form = " ".join(x for x in (st, form) if x).strip() or "—"

    reg = b.get("block2_regulatory", "").strip()
    proc = b.get("block2_procurement", "").strip()
    basis_regulatory = _sanitize_legacy_jargon("\n\n".join(x for x in (reg, proc) if x))

    paper_models: list[PerplexityPaper] = []
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        paper_models.append(
            PerplexityPaper(
                title=_sanitize_legacy_jargon(str(r.get("title") or "")),
                source=_sanitize_legacy_jargon(str(r.get("source") or "")),
                url=str(r.get("url") or "").strip(),
                summary_ko=_sanitize_legacy_jargon(
                    str(
                        r.get("korean_summary")
                        or r.get("summary_ko")
                        or r.get("tldr")
                        or r.get("abstract")
                        or ""
                    )
                ),
            )
        )

    data: dict[str, Any] = {
        "product_name": _sanitize_legacy_jargon(
            str(row.get("product_name_ko") or row.get("product_name") or "—")
        ),
        "inn": _sanitize_legacy_jargon(str(row.get("inn_normalized") or "—")),
        "strength_form": _sanitize_legacy_jargon(strength_form),
        "hs_code": _format_hs_code(row.get("hs_code_6")),
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "verdict": verdict,
        "verdict_summary": verdict_summary,
        "basis_market_medical": _sanitize_legacy_jargon(b.get("block2_market", "")),
        "basis_competitor_brands": _sanitize_legacy_jargon(b.get("block2_channel", "")),
        "basis_regulatory": basis_regulatory,
        "basis_trade": _sanitize_legacy_jargon(b.get("block2_trade", "")),
        "basis_reference_price": _sanitize_legacy_jargon(_format_reference_prices(row)),
        "strat_entry_channel": _sanitize_legacy_jargon(b.get("block3_channel", "")),
        "strat_partner_direction": _sanitize_legacy_jargon(b.get("block3_partners", "")),
        "strat_price_positioning": _sanitize_legacy_jargon(b.get("block3_pricing", "")),
        "strat_risk_conditions": _sanitize_legacy_jargon(b.get("block3_risks", "")),
        "refs_perplexity": [p.model_dump() for p in paper_models],
        "refs_databases": [d.model_dump() for d in default_stage1_db_references()],
    }

    data = _deep_scrub_forbidden_phrases(data)
    if isinstance(data, dict):
        vs = data.get("verdict_summary")
        if isinstance(vs, str) and len(vs) > 200:
            data["verdict_summary"] = vs[:200]

    return ReportR1Payload.model_validate(data)


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
        reg = str(blocks.get("block2_regulatory") or "").strip()
        proc = str(blocks.get("block2_procurement") or "").strip()
        d["basis_regulatory"] = "\n\n".join(x for x in (reg, proc) if x)
    if "basis_trade" not in d:
        d["basis_trade"] = str(blocks.get("block2_trade") or "")
    if "basis_reference_price" not in d:
        d["basis_reference_price"] = _format_reference_prices(d)

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

    if "refs_databases" not in d or not d.get("refs_databases"):
        d["refs_databases"] = [m.model_dump() for m in default_stage1_db_references()]

    # 최종: 알 수 없는 키는 무시하기 위해 ReportR1Payload 허용 키만 통과
    allowed = set(ReportR1Payload.model_fields.keys())
    clean = {k: v for k, v in d.items() if k in allowed}
    return ReportR1Payload.model_validate(clean)
