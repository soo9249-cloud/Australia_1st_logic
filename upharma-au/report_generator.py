#!/usr/bin/env python3
"""호주 수출 시장조사 보고서 PDF 생성기 (reportlab 기반).

시장조사 PDF (권장)
    render_pdf(ReportR1Payload | dict, output_path)
    · P1: ReportR1Payload (v8 시장보고서 1~5절)
    · dict 는 Pydantic 검증 후 실패 시 v2 크롤러 필드로 보정(coerce_dict_to_report_r1)

시장조사 PDF v2 레거시 (render_api.py 등 기존 호출)
    render_pdf(row, blocks, refs, meta, output_path)
    · 제품정보 박스, Case·신뢰도 표기 — CC가 v3 페이로드로 전환 시 제거 예정

수출 전략 (FOB)
    render_p2_pdf(...) — 본 모듈 하단 유지
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from stage1_schema import (
    MarketAnalysisV8,
    ReportR1Payload,
    coerce_dict_to_report_r1,
)


ROOT = Path(__file__).resolve().parent

_FONT_CACHE: str | None = None


_TERM_ANNOTATIONS_KO: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bLogic\s*A\b(?!\s*\()", re.IGNORECASE), "Logic A (역산 로직 A)"),
    (re.compile(r"\bLogic\s*B\b(?!\s*\()", re.IGNORECASE), "Logic B (역산 로직 B)"),
    (re.compile(r"\bTop\s*10\b(?!\s*\()", re.IGNORECASE), "Top 10 (상위 10개)"),
    (re.compile(r"\bARTG\b(?!\s*\()"), "ARTG (호주 의약품 등록부)"),
    (re.compile(r"\bPBAC\b(?!\s*\()"), "PBAC (약값 심사 위원회)"),
    (re.compile(r"\bAEMP\b(?!\s*\()"), "AEMP (정부 승인 출고가)"),
    (re.compile(r"\bDPMQ\b(?!\s*\()"), "DPMQ (최대 처방량 총약가)"),
    (re.compile(r"\bFOB\b(?!\s*\()"), "FOB (본선인도가격)"),
    (re.compile(r"\bPBS\b(?!\s*\()"), "PBS (호주 의약품 급여 제도)"),
    (re.compile(r"\bTGA\b(?!\s*\()"), "TGA (호주 식약청)"),
    (re.compile(r"\bGST\b(?!\s*\()"), "GST (부가가치세)"),
    (re.compile(r"\bGPCE\b(?!\s*\()"), "GPCE (호주 약사 컨퍼런스)"),
    (re.compile(r"\bCPHI\b(?!\s*\()"), "CPHI (국제 제약 박람회)"),
    (re.compile(r"\bAPI\b(?!\s*\()"), "API (원료의약품)"),
    (re.compile(r"\bUSD\b(?!\s*\()"), "USD (미국 달러)"),
    (re.compile(r"\bAUD\b(?!\s*\()"), "AUD (호주 달러)"),
    (re.compile(r"\bKRW\b(?!\s*\()"), "KRW (원화)"),
]


def _annotate_terms_ko(text: str) -> str:
    """보고서 본문의 핵심 영문 용어에 한국어 괄호 설명을 자동 병기."""
    out = text or ""
    for pattern, repl in _TERM_ANNOTATIONS_KO:
        out = pattern.sub(repl, out)
    return out


def _register_korean_font() -> str:
    """한글 폰트 등록.

    우선순위: 레포 번들 나눔고딕(Regular/Bold) → macOS/Windows 시스템 TTF → CID 명조.
    Helvetica 폴백은 한글 깨짐을 유발하므로 사용하지 않음(Render/Linux 포함).
    """
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    # (등록명, 일반 TTF 경로, 볼드 TTF 경로; 볼드 없으면 일반과 동일 파일)
    bundled_reg = ROOT / "fonts" / "NanumGothic.ttf"
    bundled_bold = ROOT / "fonts" / "NanumGothic-Bold.ttf"
    tt_candidates: list[tuple[str, Path, Path]] = [
        (
            "NanumGothic",
            bundled_reg,
            bundled_bold if bundled_bold.is_file() else bundled_reg,
        ),
        (
            "AppleGothic",
            Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
            Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        ),
        (
            "NanumGothic",
            Path("/Library/Fonts/NanumGothic.ttf"),
            Path("/Library/Fonts/NanumGothic.ttf"),
        ),
        (
            "MalgunGothic",
            Path("C:/Windows/Fonts/malgun.ttf"),
            Path("C:/Windows/Fonts/malgunbd.ttf"),
        ),
    ]
    for family_name, reg_path, bld_path in tt_candidates:
        if not reg_path.is_file():
            continue
        bold_path = bld_path if bld_path.is_file() else reg_path
        try:
            pdfmetrics.registerFont(TTFont(family_name, str(reg_path)))
            pdfmetrics.registerFont(TTFont(f"{family_name}-Bold", str(bold_path)))
            _FONT_CACHE = family_name
            return family_name
        except Exception:
            continue

    # TTF 실패 시 한글 CID (Helvetica 는 한글 미지원이라 사용하지 않음)
    for cid_name in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
            _FONT_CACHE = cid_name
            return cid_name
        except Exception:
            continue
    raise RuntimeError(
        "한글 PDF 폰트 등록 실패. upharma-au/fonts/NanumGothic.ttf·NanumGothic-Bold.ttf 를 확인하세요."
    )


def _verdict_label(export_viable: str | None) -> str:
    ev = (export_viable or "").lower()
    return {"viable": "가능", "conditional": "조건부", "not_viable": "불가"}.get(ev, "분석 중")


def _hs_formatted(hs_code_6: str | None) -> str:
    s = str(hs_code_6 or "").strip()
    if not s:
        return "—"
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 6:
        return f"{digits[:4]}.{digits[4:6]}"
    return s


def _source_label(src: str | None) -> str:
    return {
        "semantic_scholar": "Semantic Scholar",
        "pubmed":           "PubMed",
        "perplexity":       "Perplexity",
    }.get((src or "").lower(), (src or "출처"))


def _hs_from_catalog(product_id: str | None) -> str | None:
    """au_products.json에서 product_id 기준 HS 코드를 조회한다."""
    pid = (product_id or "").strip()
    if not pid:
        return None
    path = ROOT / "crawler" / "au_products.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for p in (data.get("products") or []):
        if str(p.get("product_id") or "").strip() != pid:
            continue
        hs = p.get("hs_code_6") or p.get("hs_code")
        return str(hs).strip() if hs else None
    return None


# 정적 DB/기관 설명 (호주 크롤링 소스)
_DB_SOURCES_STATIC: list[dict[str, str]] = [
    {
        "name": "TGA ARTG",
        "description": "호주 치료제 등록부(ARTG) — 등록번호·스폰서·스케줄 조회",
        "url": "https://www.tga.gov.au/products/australian-register-therapeutic-goods-artg",
    },
    {
        "name": "PBS Schedule",
        "description": "호주 의약품 급여제도 공개 스케줄 — item code·DPMQ·innovator 지위",
        "url": "https://www.pbs.gov.au",
    },
    {
        "name": "Chemist Warehouse",
        "description": "호주 최대 약국 체인 소매가 참조",
        "url": "https://www.chemistwarehouse.com.au",
    },
    {
        "name": "NSW Health Procurement",
        "description": "뉴사우스웨일스주 공공조달 계약 공시",
        "url": "https://buy.nsw.gov.au",
    },
    {
        "name": "KUP_PIPELINE",
        "description": "한국유나이티드제약 내부 파이프라인 DB — 품목 식별자·HS·메타",
        "url": "내부 데이터",
    },
    {
        "name": "하이브리드 학술 API",
        "description": "Semantic Scholar → PubMed → Perplexity 순 폴백 학술 검색",
        "url": "내부 데이터",
    },
]


def _build_product_info_flowables(
    row: dict[str, Any],
    *,
    content_width: float,
    base_font: str,
    bold_font: str,
):
    """'자사 제품 정보' + '호주 PBS 시장 동일 약 정보' 2 박스 + 일치/불일치 배지.

    Phase 4.3-v3 (2026-04-18) — dosage_form 출처 분리:
      · 위 박스: au_products.json 출처 (한국 유나이티드 제품)
      · 아래 박스: au_pbs_raw.market_form / market_strength (호주 PBS 시장 비교 약)

    반환: [Paragraph(섹션 헤더), Table(자사), Paragraph(배지), Table(시장), Spacer].
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    C_NAVY = colors.HexColor("#1B2A4A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT = colors.HexColor("#F4F6F9")
    C_BODY = colors.HexColor("#1A1A1A")
    C_OK = colors.HexColor("#1F7A1F")        # 녹색 — 일치
    C_WARN = colors.HexColor("#B86A00")      # 주황 — 상이
    C_INFO = colors.HexColor("#6B7280")      # 회색 — 정보 없음

    s_section = ParagraphStyle(
        "ProdSection", fontName=bold_font, fontSize=11, textColor=C_NAVY,
        leading=15, spaceBefore=8, spaceAfter=4,
    )
    s_box_title = ParagraphStyle(
        "ProdBoxTitle", fontName=bold_font, fontSize=10, textColor=colors.white,
        leading=13, alignment=TA_CENTER,
    )
    s_cell_h = ParagraphStyle(
        "ProdCellH", fontName=bold_font, fontSize=9, textColor=C_NAVY,
        leading=13, wordWrap="CJK",
    )
    s_cell = ParagraphStyle(
        "ProdCell", fontName=base_font, fontSize=9, textColor=C_BODY,
        leading=14, wordWrap="CJK",
    )
    s_badge = ParagraphStyle(
        "ProdBadge", fontName=bold_font, fontSize=10, textColor=colors.white,
        leading=14, alignment=TA_CENTER,
    )

    def _rx(text: str) -> str:
        return (_annotate_terms_ko(text or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    COL1 = content_width * 0.30
    COL2 = content_width * 0.70

    # ── 데이터 추출 ──
    self_name = str(row.get("product_name_ko") or row.get("trade_name") or "—")
    self_inn = str(row.get("inn_normalized") or "—")
    self_strength = str(row.get("strength") or "—")
    self_form = str(row.get("dosage_form") or "—")

    # Phase 4.3-v3 부분 revert — 호주 시장 비교 데이터 3단 fallback:
    #   1순위: au_pbs_raw.market_form / market_strength (PBS 등재 시)
    #   2순위: au_tga_artg.dosage_form / strength (PBS 미등재, TGA 만 등재 시)
    #   3순위: "호주 시장 데이터 없음" — 회색 배지
    pbs_market_form = row.get("market_form") or None
    pbs_market_strength = row.get("market_strength") or None
    tga_dosage_form = row.get("tga_dosage_form") or None
    tga_strength_val = row.get("tga_strength") or None

    if pbs_market_form or pbs_market_strength:
        market_source = "pbs"              # 1순위
        market_form_val = pbs_market_form or "—"
        market_strength_val = pbs_market_strength or "—"
        market_section_title = "호주 PBS 시장 동일 약 정보 (PBS API 출처)"
        market_header_bg_hex = "#4A5F85"   # 슬레이트 (PBS)
    elif tga_dosage_form or tga_strength_val:
        market_source = "tga"              # 2순위 — PBS 미등재, TGA 만 등재
        market_form_val = tga_dosage_form or "—"
        market_strength_val = tga_strength_val or "—"
        market_section_title = "호주 TGA 등재 약 정보 (ARTG 출처)"
        market_header_bg_hex = "#5F7A4A"   # 올리브 (TGA)
    else:
        market_source = "none"             # 3순위
        market_form_val = "—"
        market_strength_val = "—"
        market_section_title = "호주 시장 동일 약 정보"
        market_header_bg_hex = "#6B7280"   # 회색 (데이터 없음)

    market_form = str(market_form_val)
    market_strength = str(market_strength_val)
    market_brand = str(row.get("pbs_brand_name") or row.get("brand_name") or "—")
    originator_flag = row.get("originator_brand")
    if originator_flag is True:
        brand_kind = "오리지널 (originator brand)"
    elif originator_flag is False:
        brand_kind = "제네릭 (generic brand)"
    else:
        brand_kind = "—"

    def _box_style(header_bg, alt_bg=C_ALT):
        return TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), header_bg),
            ("SPAN",       (0, 0), (-1, 0)),
            ("GRID",       (0, 0), (-1, -1), 0.5, C_BORDER),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("BACKGROUND",    (0, 2), (-1, 2), alt_bg),
        ])

    # 자사 박스 (한국 유나이티드)
    self_rows = [
        [Paragraph(_rx("한국 유나이티드 제품 정보"), s_box_title), ""],
        [Paragraph(_rx("제품명 / INN"), s_cell_h),
         Paragraph(_rx(f"{self_name} — {self_inn}"), s_cell)],
        [Paragraph(_rx("제형 (dosage_form)"), s_cell_h),
         Paragraph(_rx(self_form), s_cell)],
        [Paragraph(_rx("강도 (strength)"), s_cell_h),
         Paragraph(_rx(self_strength), s_cell)],
    ]
    self_tbl = Table(self_rows, colWidths=[COL1, COL2])
    self_tbl.setStyle(_box_style(C_NAVY))

    # 시장 박스 (호주 PBS 등재 또는 TGA 등재 동일 약) — 데이터 출처에 따라 라벨·배경색 전환
    if market_source == "pbs":
        form_label = "호주 PBS 시장 제형 (market_form)"
        strength_label = "호주 PBS 시장 강도 (market_strength)"
    elif market_source == "tga":
        form_label = "호주 TGA 등재 제형 (tga_dosage_form)"
        strength_label = "호주 TGA 등재 강도 (tga_strength)"
    else:
        form_label = "호주 시장 제형"
        strength_label = "호주 시장 강도"

    market_rows = [
        [Paragraph(_rx(market_section_title), s_box_title), ""],
        [Paragraph(_rx("브랜드명 / 구분"), s_cell_h),
         Paragraph(_rx(f"{market_brand} · {brand_kind}"), s_cell)],
        [Paragraph(_rx(form_label), s_cell_h),
         Paragraph(_rx(market_form), s_cell)],
        [Paragraph(_rx(strength_label), s_cell_h),
         Paragraph(_rx(market_strength), s_cell)],
    ]
    market_tbl = Table(market_rows, colWidths=[COL1, COL2])
    market_tbl.setStyle(_box_style(colors.HexColor(market_header_bg_hex)))

    # 일치/불일치 배지
    def _norm(s: str) -> str:
        return (s or "").strip().lower()

    def _forms_match(a: str, b: str) -> bool:
        na, nb = _norm(a), _norm(b)
        if not na or not nb or na == "—" or nb == "—":
            return False
        # 부분 매칭 허용 (Capsule vs "Capsule, hard" 같은 변종 대응)
        return na in nb or nb in na

    def _strengths_match(a: str, b: str) -> bool:
        na, nb = _norm(a), _norm(b)
        if not na or not nb or na == "—" or nb == "—":
            return False
        # 공백 제거 후 비교 (500mg vs "500 mg" 대응)
        return na.replace(" ", "") == nb.replace(" ", "")

    fm = _forms_match(self_form, market_form)
    sm = _strengths_match(self_strength, market_strength)

    if market_source == "none":
        badge_text = (
            "[정보] 호주 시장 비교 약 데이터 없음 (PBS 미등재 + TGA strength/"
            "dosage_form 파싱 실패)"
        )
        badge_bg = C_INFO
    elif fm and sm:
        src_label = "PBS" if market_source == "pbs" else "TGA"
        badge_text = f"[일치] 제형·강도 일치 — 호주 {src_label} 시장 동일 규격 존재"
        badge_bg = C_OK
    else:
        src_label = "PBS" if market_source == "pbs" else "TGA"
        diffs: list[str] = []
        if not sm:
            diffs.append(f"강도 상이: 자사 {self_strength} / 호주{src_label} {market_strength}")
        if not fm:
            diffs.append(f"제형 상이: 자사 {self_form} / 호주{src_label} {market_form}")
        badge_text = "[상이] " + " · ".join(diffs)
        badge_bg = C_WARN

    badge_tbl = Table(
        [[Paragraph(_rx(badge_text), s_badge)]],
        colWidths=[content_width],
    )
    badge_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), badge_bg),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))

    return [
        Paragraph(_rx("0. 제품 정보 (자사 vs 호주 PBS 시장)"), s_section),
        self_tbl,
        Spacer(1, 4),
        badge_tbl,
        Spacer(1, 4),
        market_tbl,
        Spacer(1, 8),
    ]


def render_pdf(*args: Any, **kwargs: Any) -> None:
    """시장조사 PDF 생성.

    v3 (권장):
        render_pdf(payload: ReportR1Payload | dict, output_path: str | Path) -> None

    v2 레거시 (render_api.py 등 기존 호출 — CC가 v3 페이로드로 전환할 때까지 유지):
        render_pdf(row, blocks, refs, meta, out_path) -> None
    """
    if kwargs:
        raise TypeError("render_pdf() does not accept keyword arguments")
    if len(args) == 5:
        row, blocks, refs, meta, out_path = args
        return _render_pdf_legacy_v2(
            row, blocks, refs, meta, Path(out_path),
        )
    if len(args) == 2:
        payload, out_path = args
        pl: ReportR1Payload
        if isinstance(payload, ReportR1Payload):
            pl = payload
        elif isinstance(payload, dict):
            try:
                pl = ReportR1Payload.model_validate(payload)
            except Exception:
                pl = coerce_dict_to_report_r1(payload)
        else:
            raise TypeError(
                "v3 render_pdf(payload, path): payload must be ReportR1Payload or dict",
            )
        return _render_pdf_stage1_v3(pl, Path(out_path))
    raise TypeError(
        "render_pdf() expects (payload, output_path) for v3 or "
        "(row, blocks, refs, meta, output_path) for legacy v2",
    )


def _render_pdf_legacy_v2(
    row: dict[str, Any],
    blocks: dict[str, str],
    refs: list[dict[str, Any]],
    meta: dict[str, Any],
    out_path: Path,
) -> None:
    """v2 레거시 레이아웃 — 제품정보 박스·Case·신뢰도 표기 포함."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    W, _H = A4
    MARGIN = 20 * mm
    CONTENT_W = W - 2 * MARGIN

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold"
    if base_font in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        bold_font = base_font

    # 색상 팔레트
    C_NAVY   = colors.HexColor("#1B2A4A")
    C_BODY   = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT    = colors.HexColor("#F4F6F9")
    C_BAR    = colors.HexColor("#1E3A5F")

    COL1 = CONTENT_W * 0.26
    COL2 = CONTENT_W * 0.74

    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_title = ps("Title", fontName=bold_font, fontSize=18, leading=24,
                 alignment=TA_CENTER, textColor=C_NAVY, spaceAfter=4)
    s_date = ps("Date", fontName=base_font, fontSize=10, leading=13,
                alignment=TA_CENTER, textColor=colors.HexColor("#6B7280"))
    s_section = ps("Section", fontName=bold_font, fontSize=11, textColor=C_NAVY,
                   leading=15, spaceBefore=8, spaceAfter=4)
    s_cell_h = ps("CellH", fontName=bold_font, fontSize=9, textColor=C_NAVY,
                  leading=13, wordWrap="CJK")
    s_cell = ps("Cell", fontName=base_font, fontSize=9, textColor=C_BODY,
                leading=14, wordWrap="CJK")
    s_bar = ps("Bar", fontName=bold_font, fontSize=9, textColor=colors.white,
               leading=13, wordWrap="CJK")
    s_hdr = ps("HdrWhite", fontName=bold_font, fontSize=9, textColor=colors.white,
               leading=13, wordWrap="CJK")
    s_cell_sm = ps("CellSm", fontName=base_font, fontSize=7,
                   textColor=colors.HexColor("#6B7280"), leading=10, wordWrap="CJK")

    def _rx(text: str) -> str:
        return (_annotate_terms_ko(text or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _trunc(text: str, limit: int = 520) -> str:
        s = (text or "").strip()
        return s if len(s) <= limit else s[:limit] + "…"

    def _base_style(extra: list | None = None) -> list:
        cmds = [
            ("GRID",   (0, 0), (-1, -1), 0.5, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ]
        if extra:
            cmds.extend(extra)
        return cmds

    def _kv_table(rows: list[tuple[str, str]]) -> Table:
        pdata = [
            [Paragraph(_rx(k), s_cell_h), Paragraph(_rx(_trunc(v)), s_cell)]
            for k, v in rows
        ]
        extras: list[tuple] = []
        for i in range(len(rows)):
            if i % 2 == 1:
                extras.append(("BACKGROUND", (0, i), (-1, i), C_ALT))
        t = Table(pdata, colWidths=[COL1, COL2])
        t.setStyle(TableStyle(_base_style(extras)))
        return t

    # ── 데이터 추출 ──
    product_name = str(row.get("product_name_ko") or row.get("trade_name") or "—")
    inn = str(row.get("inn_normalized") or "—")
    strength = str(row.get("strength") or "")
    dosage = str(row.get("dosage_form") or "")
    hs_raw = (
        row.get("hs_code_6")
        or row.get("hs_code")
        or _hs_from_catalog(row.get("product_code") or row.get("product_id"))
    )
    hs = _hs_formatted(hs_raw)
    viable_text = _verdict_label(meta.get("export_viable"))
    conf_val = meta.get("confidence")
    conf_pct = (f"{round(float(conf_val) * 100)}%"
                if isinstance(conf_val, (int, float)) else "—")

    ev = (meta.get("export_viable") or "").lower()
    case_grade = "A" if ev == "viable" else "B" if ev == "conditional" else "C"

    generated_date = datetime.now().strftime("%Y-%m-%d")

    # ── DocTemplate ──
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"호주 수출 시장조사 보고서 — {product_name}",
    )
    story: list = []

    # ── 타이틀 + 날짜 ──
    story.append(Paragraph(_rx("호주 수출 시장조사 보고서"), s_title))
    story.append(Paragraph(_rx(generated_date), s_date))
    story.append(Spacer(1, 6))

    # ── 제품 바 ──
    str_form = " ".join(x for x in [strength, dosage] if x).strip()
    bar_txt = f"{product_name} — {inn}"
    if str_form:
        bar_txt += f" · {str_form}"
    bar_txt += f"  |  HS CODE: {hs}"
    bar_tbl = Table([[Paragraph(_rx(bar_txt), s_bar)]], colWidths=[CONTENT_W])
    bar_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_BAR),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(bar_tbl)
    story.append(Spacer(1, 10))

    # Phase 4.3-v3 — 0. 제품 정보 (자사 vs 호주 PBS 시장)
    story.extend(_build_product_info_flowables(
        row,
        content_width=CONTENT_W,
        base_font=base_font,
        bold_font=bold_font,
    ))

    # ── 1. 수출 적합 판정 ──
    story.append(Paragraph(_rx("1. 수출 적합 판정"), s_section))
    story.append(_kv_table([
        ("판정", f"{viable_text} · HS {hs} · Case {case_grade} · 신뢰도 {conf_pct}"),
    ]))
    story.append(Spacer(1, 6))

    # ── 2. 판정 근거 (5축) ──
    story.append(Paragraph(_rx("2. 판정 근거"), s_section))
    story.append(_kv_table([
        ("시장 / 의료", blocks.get("block2_market", "—")),
        ("규제",        blocks.get("block2_regulatory", "—")),
        ("무역",        blocks.get("block2_trade", "—")),
        ("조달",        blocks.get("block2_procurement", "—")),
        ("유통",        blocks.get("block2_channel", "—")),
    ]))
    story.append(Spacer(1, 6))

    # ── 3. 시장 진출 전략 (4축) ──
    story.append(Paragraph(_rx("3. 시장 진출 전략"), s_section))
    story.append(_kv_table([
        ("진입 채널 권고", blocks.get("block3_channel", "—")),
        ("가격 포지셔닝",  blocks.get("block3_pricing", "—")),
        ("파트너 발굴",    blocks.get("block3_partners", "—")),
        ("리스크 + 조건",  blocks.get("block3_risks", "—")),
    ]))

    story.append(PageBreak())

    # ── 4. 근거 및 출처 ──
    story.append(Paragraph(_rx("4. 근거 및 출처"), s_section))

    # 4-1. PERPLEXITY 추천 논문
    story.append(Paragraph(_rx("4-1. PERPLEXITY 추천 논문"), s_section))
    valid_refs = [r for r in (refs or []) if isinstance(r, dict) and (r.get("title") or r.get("url"))]
    if valid_refs:
        w_no    = CONTENT_W * 0.05
        w_title = CONTENT_W * 0.56
        w_sum   = CONTENT_W * 0.39
        paper_tbl: list[list] = [[
            Paragraph("No.", s_hdr),
            Paragraph("논문 제목 / 출처", s_hdr),
            Paragraph("한국어 요약", s_hdr),
        ]]
        extras_p: list[tuple] = [("BACKGROUND", (0, 0), (-1, 0), C_NAVY)]
        for i, r in enumerate(valid_refs, 1):
            title = _trunc(str(r.get("title") or ""), 200)
            url = str(r.get("url") or "")
            source = _source_label(r.get("source"))
            summary = _trunc(
                str(r.get("korean_summary") or r.get("tldr") or r.get("abstract") or "—"),
                400,
            )
            title_lines = _rx(title) if title else _rx("(제목 없음)")
            if source:
                title_lines += f"<br/>[{_rx(source)}]"
            if url:
                short_url = url[:75] + ("…" if len(url) > 75 else "")
                title_lines += f"<br/>{_rx(short_url)}"
            paper_tbl.append([
                Paragraph(str(i), s_cell),
                Paragraph(title_lines, s_cell),
                Paragraph(_rx(summary), s_cell),
            ])
            if i % 2 == 0:
                extras_p.append(("BACKGROUND", (0, i), (-1, i), C_ALT))
        pt = Table(paper_tbl, colWidths=[w_no, w_title, w_sum])
        pt.setStyle(TableStyle(_base_style(extras_p)))
        story.append(pt)
    else:
        story.append(_kv_table([("PERPLEXITY 논문", "사용된 논문 링크 없음")]))

    story.append(Spacer(1, 8))

    # 4-2. 사용된 DB/기관
    story.append(Paragraph(_rx("4-2. 사용된 DB/기관"), s_section))
    w_name = CONTENT_W * 0.25
    w_desc = CONTENT_W * 0.45
    w_link = CONTENT_W * 0.30
    db_tbl: list[list] = [[
        Paragraph("DB/기관명", s_hdr),
        Paragraph("설명", s_hdr),
        Paragraph("링크", s_hdr),
    ]]
    extras_d: list[tuple] = [("BACKGROUND", (0, 0), (-1, 0), C_NAVY)]
    for i, src in enumerate(_DB_SOURCES_STATIC, 1):
        url = src.get("url", "")
        short_url = (url[:55] + "…" if len(url) > 55 else url) if url else "—"
        db_tbl.append([
            Paragraph(_rx(src["name"]),        s_cell),
            Paragraph(_rx(src["description"]), s_cell),
            Paragraph(_rx(short_url),          s_cell_sm),
        ])
        if i % 2 == 0:
            extras_d.append(("BACKGROUND", (0, i), (-1, i), C_ALT))
    dt = Table(db_tbl, colWidths=[w_name, w_desc, w_link])
    dt.setStyle(TableStyle(_base_style(extras_d)))
    story.append(dt)

    doc.build(story)


def _render_pdf_market_v8(payload: ReportR1Payload, out_path: Path) -> None:
    """시장보고서(P1) — 1~5절(근거: 5-1, 5-2)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    raw = payload.v8_market_analysis or {}
    v8 = MarketAnalysisV8.model_validate(raw)

    W, _H = A4
    # 본문 폭 확보·가독성: 여백을 약간 줄임 (시장분석 v8 공통)
    MARGIN_X = 18 * mm
    MARGIN_Y = 16 * mm
    CONTENT_W = W - 2 * MARGIN_X

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold"
    if base_font in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        bold_font = base_font

    C_TITLE = colors.HexColor("#3a4a5e")
    C_BAR = colors.HexColor("#3a4a5e")
    C_BODY = colors.HexColor("#1A1A1A")
    C_MUTED = colors.HexColor("#888888")
    C_BORDER = colors.HexColor("#d0d0d0")
    C_HDR_BG = colors.HexColor("#f0f3f7")
    C_APPENDIX_BG = colors.HexColor("#fafaf7")
    C_APPENDIX_BR = colors.HexColor("#e5e5dd")
    C_FT_BG = colors.HexColor("#fff8e1")

    def ps(name: str, **kw: Any) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_title = ps(
        "V8Title",
        fontName=bold_font,
        fontSize=22,
        leading=26,
        alignment=TA_CENTER,
        textColor=C_TITLE,
        spaceAfter=6,
    )
    s_date = ps(
        "V8Date",
        fontName=base_font,
        fontSize=11,
        leading=14,
        alignment=TA_CENTER,
        textColor=C_MUTED,
        spaceAfter=10,
    )
    s_sec = ps(
        "V8Sec",
        fontName=bold_font,
        fontSize=15,
        textColor=C_TITLE,
        spaceBefore=18,
        spaceAfter=8,
        keepWithNext=True,
    )
    s_sub = ps(
        "V8Sub",
        fontName=bold_font,
        fontSize=13,
        textColor=C_TITLE,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )
    s_cell = ps(
        "V8Cell",
        fontName=base_font,
        fontSize=11,
        textColor=C_BODY,
        leading=17,
        wordWrap="CJK",
    )
    s_bar = ps(
        "V8Bar",
        fontName=bold_font,
        fontSize=13,
        textColor=colors.white,
        leading=16,
        wordWrap="CJK",
    )
    s_cell_h = ps(
        "V8H",
        fontName=bold_font,
        fontSize=9,
        textColor=C_TITLE,
        leading=13,
        wordWrap="CJK",
    )
    s_small = ps(
        "V8Sm",
        fontName=base_font,
        fontSize=10,
        textColor=colors.HexColor("#555555"),
        leading=15,
        wordWrap="CJK",
    )
    # 별첨 A/B 공통 — 상자 안 본문
    s_apx = ps(
        "V8Apx",
        fontName=base_font,
        fontSize=9,
        textColor=colors.HexColor("#444444"),
        leading=13,
        wordWrap="CJK",
    )
    s_apx_head = ps(
        "V8ApxHead",
        fontName=bold_font,
        fontSize=10,
        textColor=C_TITLE,
        leading=14,
        spaceBefore=4,
        spaceAfter=2,
        wordWrap="CJK",
    )

    def _rx(text: str) -> str:
        return (
            _annotate_terms_ko(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _trunc(text: str, limit: int = 1800) -> str:
        s = (text or "").strip()
        return s if len(s) <= limit else s[:limit] + "…"

    def _base_tbl_style(extras: list | None = None) -> list:
        cmds: list = [
            ("GRID", (0, 0), (-1, -1), 1, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (0, -1), C_HDR_BG),
        ]
        if extras:
            cmds.extend(extras)
        return cmds

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_Y,
        bottomMargin=MARGIN_Y + 4 * mm,
        title=f"호주 시장보고서 - {payload.product_name}",
    )
    story: list = []

    # 제목에서 "시장분석"과 "보고서" 사이 불필요한 줄바꿈 완화 (좁은 열에서 단어 단위 줄바꿈 방지)
    story.append(
        Paragraph(_rx(f"호주 시장보고서 - {payload.product_name}"), s_title)
    )
    story.append(Paragraph(_rx(payload.report_date or datetime.now().strftime("%Y-%m-%d")), s_date))

    pn = (payload.product_name or "").strip()
    inn_sf = f"{payload.inn} · {payload.strength_form}".strip(" ·")
    if pn:
        bar_en = f"{pn} — {inn_sf}  |  HS CODE: {payload.hs_code or '—'}"
    else:
        bar_en = f"{inn_sf}  |  HS CODE: {payload.hs_code or '—'}"
    bar_tbl = Table([[Paragraph(_rx(bar_en), s_bar)]], colWidths=[CONTENT_W])
    bar_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_BAR),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(bar_tbl)
    story.append(Spacer(1, 16))

    # 적합/불가 판정 카드 — 사용자 양식에 없음. 내부 스키마(v8.verdict)는 유지하되 PDF에 노출하지 않음.

    story.append(Paragraph(_rx("1. 의료 거시환경 파악"), s_sec))
    story.append(Paragraph(_rx("1-1. 시장 개요"), s_sub))
    story.append(Paragraph(_rx(_trunc(v8.market_overview.paragraph, 900)), s_cell))
    story.append(Spacer(1, 8))
    # P1 본문 1페이지 목표: 질환 블록은 최대 2개만 요약 반영
    for d in (v8.market_overview.disease_block or [])[:2]:
        term_txt = f"<b>{_rx(d.name_ko)}</b> ({_rx(d.short_en)}) — {_rx(d.plain_desc)}"
        term_box = Table([[Paragraph(term_txt, s_small)]], colWidths=[CONTENT_W])
        term_box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f8fa")),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#c0cada")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        story.append(term_box)
        story.append(Spacer(1, 6))

    story.append(Paragraph(_rx("1-2. 경쟁 브랜드 현황"), s_sub))
    cb_rows: list[list] = [
        [Paragraph(_rx("구분"), s_cell_h), Paragraph(_rx("상세"), s_cell_h)]
    ]
    ex_cb: list[tuple] = [("BACKGROUND", (0, 0), (-1, 0), C_HDR_BG)]
    # 경쟁 브랜드는 핵심 3개까지만 반영 (분량 제어)
    for i, c in enumerate((v8.competitor_brands or [])[:3], 1):
        cb_rows.append(
            [
                Paragraph(_rx(c.role), s_cell),
                Paragraph(_rx(_trunc(c.detail, 650)), s_cell),
            ]
        )
        if i % 2 == 0:
            ex_cb.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f7f9fc")))
    if len(cb_rows) == 1:
        cb_rows.append([Paragraph(_rx("—"), s_cell), Paragraph(_rx("해당 데이터 없음"), s_cell)])
    # 구분 열이 너무 좁으면 한글 단어가 어색하게 쪼개짐 — 약간 넓힘
    cb_t = Table(cb_rows, colWidths=[CONTENT_W * 0.35, CONTENT_W * 0.65])
    cb_t.setStyle(TableStyle(_base_tbl_style(ex_cb)))
    story.append(cb_t)

    story.append(Paragraph(_rx("1-3. 시장 구도"), s_sub))
    ms = v8.market_structure
    ms_tag = _rx(ms.tag or "")
    ms_para = _rx(_trunc(ms.paragraph, 700))
    ms_text = f"<b>{ms_tag}</b>. {ms_para}" if ms_tag else ms_para
    story.append(
        Paragraph(ms_text, s_cell)
    )
    story.append(Spacer(1, 8))

    snap_flow: list[Any] = [Paragraph(_rx("1-4. 공시 가격 스냅샷"), s_sub)]
    psn = v8.price_snapshot
    snap_rows: list[list] = [
        [
            Paragraph(_rx("항목"), s_cell_h),
            Paragraph(_rx("공시값"), s_cell_h),
            Paragraph(_rx("출처"), s_cell_h),
        ]
    ]
    snap_ex: list[tuple] = [("BACKGROUND", (0, 0), (-1, 0), C_HDR_BG)]
    snap_data = [
        (
            "AEMP (정부 승인 출고가)",
            f"USD {psn.aemp_usd} / AUD {psn.aemp_aud}",
            f"PBS item {psn.pbs_code}",
        ),
        (
            "DPMQ (최대 처방량 총약가)",
            f"USD {psn.dpmq_usd} / AUD {psn.dpmq_aud}",
            f"PBS item {psn.pbs_code}",
        ),
        ("시장 구분", psn.market_class, "—"),
    ]
    for i, (a, b, c) in enumerate(snap_data, 1):
        snap_rows.append(
            [
                Paragraph(_rx(a), s_cell),
                Paragraph(_rx(b), s_cell),
                Paragraph(_rx(c), s_cell),
            ]
        )
        if i % 2 == 0:
            snap_ex.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f7f9fc")))
    st_snap = Table(snap_rows, colWidths=[CONTENT_W * 0.36, CONTENT_W * 0.28, CONTENT_W * 0.36])
    st_snap.setStyle(TableStyle(_base_tbl_style(snap_ex)))
    snap_flow.append(st_snap)
    snap_flow.append(Spacer(1, 10))
    story.append(KeepTogether(snap_flow))

    story.append(Paragraph(_rx("2. 무역/규제 환경"), s_sec))
    es = v8.entry_strategy
    story.append(Paragraph(_rx("2-1. 진입 채널 및 접근 방식"), s_sub))
    story.append(Paragraph(_rx(_trunc(es.channel, 500)), s_cell))
    story.append(Paragraph(_rx("2-2. 우선 접근 파트너 방향성"), s_sub))
    story.append(Paragraph(_rx(_trunc(es.partner_direction, 420)), s_cell))
    story.append(Paragraph(_rx("2-3. 협력 우선순위 근거"), s_sub))
    story.append(Paragraph(_rx(_trunc(es.rationale, 420)), s_cell))
    story.append(Spacer(1, 18))

    story.append(Paragraph(_rx("3. 참고 가격"), s_sec))
    rr = v8.regulatory_risk
    story.append(Paragraph(_rx("3-1. 규제 리스크 및 가격 영향"), s_sub))
    story.append(
        Paragraph(
            _rx(
                _trunc(
                    "\n\n".join(
                        x
                        for x in (
                            rr.artg_paragraph,
                            rr.pbac_paragraph,
                            rr.prescription_limit_paragraph,
                        )
                        if x
                    ),
                    900,
                )
            ),
            s_cell,
        )
    )
    if v8.fast_track_applies:
        ft_tbl = Table(
            [[Paragraph(_rx("▶ 패스트트랙(COR 등) 경로는 시장분석 본문 기준으로 검토합니다."), s_small)]],
            colWidths=[CONTENT_W],
        )
        ft_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), C_FT_BG),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#e0d4a0")),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#c4a84e")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        story.append(Spacer(1, 8))
        story.append(ft_tbl)

    story.append(Paragraph(_rx("4. 리스크 / 조건"), s_sec))
    story.append(Paragraph(_rx("4-1. 데이터 · 운영 유의사항"), s_sub))
    story.append(Paragraph(_rx(_trunc(v8.operational_risk, 480)), s_cell))
    story.append(Paragraph(_rx("4-2. 본 품목 고유 리스크"), s_sub))
    story.append(Paragraph(_rx(_trunc(v8.product_specific_risk, 420)), s_cell))

    story.append(PageBreak())

    apx_a_rows: list[list] = [
        [Paragraph(_rx("5. 근거 및 출처"), s_apx_head)],
    ]
    # 5-1. 퍼플렉시티(검색) — 최대 3건 (양식 전용, 학술 별첨·용어집은 넣지 않음)
    pplx = list(payload.refs_perplexity or [])[:3]
    apx_a_rows.append(
        [Paragraph(_rx("5-1. 퍼플렉시티 추천 논문"), s_apx_head)]
    )
    if pplx:
        for i, p in enumerate(pplx, 1):
            line = (
                f"[{i}] {p.source or ''} — {_trunc(p.title, 220)} — {_trunc(p.summary_ko, 320)}"
            )
            apx_a_rows.append([Paragraph(_rx(line), s_apx)])
    else:
        apx_a_rows.append(
            [Paragraph(_rx("해당 없음(검색·요약 참고자료가 수집되지 않음)."), s_apx)]
        )
    dbs = list(payload.refs_databases or [])
    apx_a_rows.append(
        [Paragraph(_rx("5-2. 사용된 DB/기관"), s_apx_head)]
    )
    if dbs:
        for d in dbs:
            u = (d.url or "").strip() or "—"
            if len(u) > 100:
                u = u[:100] + "…"
            line = f"{_trunc(d.name, 120)} — {_trunc(d.desc_ko, 300)} — {u}"
            apx_a_rows.append([Paragraph(_rx(line), s_apx)])
    else:
        apx_a_rows.append([Paragraph(_rx("해당 없음"), s_apx)])

    apx_a = Table(apx_a_rows, colWidths=[CONTENT_W])
    apx_a.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_APPENDIX_BG),
                ("BOX", (0, 0), (-1, -1), 1, C_APPENDIX_BR),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(apx_a)
    story.append(Spacer(1, 12))
    disc = ps(
        "V8Disc",
        fontName=base_font,
        fontSize=9,
        textColor=C_MUTED,
        alignment=TA_CENTER,
        leading=12,
    )
    story.append(
        Paragraph(
            _rx(
                "본 보고서는 공개된 데이터 기반 자동 생성본이며, "
                "실제 계약·가격 협상 시 현지 파트너와의 별도 확인이 필요합니다."
            ),
            disc,
        )
    )
    doc.build(story)


def _render_pdf_stage1_v3(payload: ReportR1Payload, out_path: Path) -> None:
    """시장분석 보고서 PDF v3 — HS CODE·4블록·참고자료 표·별첨 용어집."""
    if payload.v8_market_analysis:
        _render_pdf_market_v8(payload, out_path)
        return

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    W, H = A4
    MARGIN_X = 24 * mm
    MARGIN_Y = 20 * mm
    CONTENT_W = W - 2 * MARGIN_X

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold"
    if base_font in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        bold_font = base_font

    C_TITLE = colors.HexColor("#3a4a5e")
    C_BAR = colors.HexColor("#3a4a5e")
    C_BODY = colors.HexColor("#1A1A1A")
    C_MUTED = colors.HexColor("#888888")
    C_BORDER = colors.HexColor("#d0d0d0")
    C_HDR_BG = colors.HexColor("#f0f3f7")
    C_APPENDIX_BG = colors.HexColor("#fafaf7")
    C_APPENDIX_BR = colors.HexColor("#e5e5dd")

    COL_L = CONTENT_W * 0.28
    COL_R = CONTENT_W * 0.72

    def ps(name: str, **kw: Any) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_title = ps(
        "R1V3Title",
        fontName=bold_font,
        fontSize=30,
        leading=36,
        alignment=TA_CENTER,
        textColor=C_TITLE,
        spaceAfter=6,
    )
    s_date = ps(
        "R1V3Date",
        fontName=base_font,
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#6B7280"),
        spaceAfter=10,
    )
    s_sec = ps(
        "R1V3Sec",
        fontName=bold_font,
        fontSize=14,
        leading=18,
        textColor=C_TITLE,
        spaceBefore=10,
        spaceAfter=4,
    )
    s_cell_h = ps(
        "R1V3H",
        fontName=bold_font,
        fontSize=9,
        textColor=C_TITLE,
        leading=13,
        wordWrap="CJK",
    )
    s_cell = ps(
        "R1V3Cell",
        fontName=base_font,
        fontSize=9,
        textColor=C_BODY,
        leading=14,
        wordWrap="CJK",
    )
    s_bar = ps(
        "R1V3Bar",
        fontName=bold_font,
        fontSize=10,
        textColor=colors.white,
        leading=14,
        wordWrap="CJK",
    )
    s_hdr = ps(
        "R1V3TblHdr",
        fontName=bold_font,
        fontSize=9,
        textColor=C_TITLE,
        leading=13,
        wordWrap="CJK",
    )
    s_small = ps(
        "R1V3Small",
        fontName=base_font,
        fontSize=8,
        textColor=colors.HexColor("#555555"),
        leading=11,
        wordWrap="CJK",
    )
    s_apx = ps(
        "R1V3Apx",
        fontName=base_font,
        fontSize=9,
        textColor=C_BODY,
        leading=13,
        wordWrap="CJK",
    )

    def _rx(text: str) -> str:
        return (
            _annotate_terms_ko(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _xml_amp_only(text: str) -> str:
        """reportlab Paragraph XML — 신뢰된 본문만. & 만 이스케이프 (<b> 등 태그 유지)."""
        return (text or "").replace("&", "&amp;")

    def _trunc(text: str, limit: int = 2000) -> str:
        s = (text or "").strip()
        return s if len(s) <= limit else s[:limit] + "…"

    def _cell_body(raw: str) -> Paragraph:
        t = (raw or "").strip()
        if not t or t == "해당없음":
            return Paragraph(
                '<i><font color="#888888">' + _rx("해당없음 — 사유 기재 필요") + "</font></i>",
                s_cell,
            )
        return Paragraph(_rx(_trunc(t)), s_cell)

    def _base_tbl_style(extras: list | None = None) -> list:
        cmds: list = [
            ("GRID", (0, 0), (-1, -1), 1, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (0, -1), C_HDR_BG),
        ]
        if extras:
            cmds.extend(extras)
        return cmds

    def _kv(rows: list[tuple[str, str]]) -> Table:
        data = [[Paragraph(_rx(k), s_cell_h), _cell_body(v)] for k, v in rows]
        t = Table(data, colWidths=[COL_L, COL_R])
        t.setStyle(TableStyle(_base_tbl_style()))
        return t

    def _section_line(title: str) -> list:
        line_tbl = Table(
            [[""]],
            colWidths=[CONTENT_W],
            rowHeights=[2],
        )
        line_tbl.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, -1), 2, C_TITLE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return [Paragraph(_rx(title), s_sec), line_tbl, Spacer(1, 6)]

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_Y,
        bottomMargin=MARGIN_Y,
        title=f"호주 시장보고서 - {payload.product_name}",
    )
    story: list = []

    story.append(Paragraph(_rx(f"호주 시장보고서 - {payload.product_name}"), s_title))
    story.append(Paragraph(_rx(payload.report_date or datetime.now().strftime("%Y-%m-%d")), s_date))

    bar_line = (
        f"{payload.product_name} — {payload.inn} · {payload.strength_form}"
        f"  |  HS CODE: {payload.hs_code or '—'}"
    )
    bar_tbl = Table([[Paragraph(_rx(bar_line), s_bar)]], colWidths=[CONTENT_W])
    bar_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_BAR),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(bar_tbl)
    story.append(Spacer(1, 12))

    # 진출 적합/불가 판정 블록 — 사용자 양식에 없어 PDF에서 제외.

    story.extend(_section_line("[1] 시장·규제·참고가격 요약"))
    story.append(
        _kv(
            [
                ("시장 / 의료", payload.basis_market_medical),
                ("경쟁 브랜드 현황", payload.basis_competitor_brands),
                ("규제", payload.basis_regulatory),
                ("무역", payload.basis_trade),
                ("참고 가격", payload.basis_reference_price),
            ]
        )
    )
    story.append(Spacer(1, 8))

    story.extend(_section_line("[2] 시장 진출 전략"))
    story.append(
        _kv(
            [
                ("진입 채널 권고", payload.strat_entry_channel),
                ("파트너 방향성", payload.strat_partner_direction),
                ("가격 포지셔닝", payload.strat_price_positioning),
                ("리스크 + 조건", payload.strat_risk_conditions),
            ]
        )
    )

    story.append(PageBreak())

    story.extend(_section_line("[3] 근거 및 출처"))
    story.append(Paragraph(_rx("3-1. Perplexity 추천 논문"), s_hdr))
    story.append(Spacer(1, 4))

    papers = payload.refs_perplexity or []
    if papers:
        w_no = CONTENT_W * 0.06
        w_ti = CONTENT_W * 0.44
        w_su = CONTENT_W * 0.50
        ph: list[list] = [
            [
                Paragraph("No.", s_hdr),
                Paragraph("논문 제목 / 출처", s_hdr),
                Paragraph("한국어 요약", s_hdr),
            ]
        ]
        ex_p: list[tuple] = [
            ("BACKGROUND", (0, 0), (-1, 0), C_HDR_BG),
            ("GRID", (0, 0), (-1, -1), 1, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i, p in enumerate(papers, 1):
            title_block = _rx(_trunc(p.title, 300))
            src = _rx(p.source or "")
            url = (p.url or "").strip()
            if src:
                title_block += f"<br/>[{src}]"
            if url:
                u = url[:80] + ("…" if len(url) > 80 else "")
                title_block += f"<br/>{_rx(u)}"
            ph.append(
                [
                    Paragraph(str(i), s_cell),
                    Paragraph(title_block, s_cell),
                    Paragraph(_rx(_trunc(p.summary_ko, 600)), s_cell),
                ]
            )
            if i % 2 == 0:
                ex_p.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f7f9fc")))
        pt = Table(ph, colWidths=[w_no, w_ti, w_su])
        pt.setStyle(TableStyle(ex_p))
        story.append(pt)
    else:
        story.append(_kv([("Perplexity 논문", "해당없음")]))

    story.append(Spacer(1, 10))
    story.append(Paragraph(_rx("3-2. 사용된 DB / 기관"), s_hdr))
    story.append(Spacer(1, 4))

    dbs = payload.refs_databases or []
    if dbs:
        w_n = CONTENT_W * 0.22
        w_d = CONTENT_W * 0.48
        w_u = CONTENT_W * 0.30
        db_rows: list[list] = [
            [
                Paragraph("DB/기관명", s_hdr),
                Paragraph("설명", s_hdr),
                Paragraph("링크", s_hdr),
            ]
        ]
        ex_d: list[tuple] = [
            ("BACKGROUND", (0, 0), (-1, 0), C_HDR_BG),
            ("GRID", (0, 0), (-1, -1), 1, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i, r in enumerate(dbs, 1):
            link = (r.url or "").strip() or "—"
            if len(link) > 70:
                link = link[:70] + "…"
            db_rows.append(
                [
                    Paragraph(_rx(r.name), s_cell),
                    Paragraph(_rx(_trunc(r.desc_ko, 400)), s_cell),
                    Paragraph(_rx(link), s_small),
                ]
            )
            if i % 2 == 0:
                ex_d.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f7f9fc")))
        dt = Table(db_rows, colWidths=[w_n, w_d, w_u])
        dt.setStyle(TableStyle(ex_d))
        story.append(dt)
    else:
        story.append(_kv([("DB/기관", "해당없음")]))

    story.append(Spacer(1, 14))

    s_apx_title = ps(
        "R1ApxTitle",
        fontName=bold_font,
        fontSize=12,
        textColor=C_TITLE,
        leading=16,
    )

    glossary_box = Table(
        [
            [Paragraph(_rx("[별첨] 규제 기관 용어집"), s_apx_title)],
            [
                Paragraph(
                    _xml_amp_only(
                        "<b>TGA</b> (Therapeutic Goods Administration): "
                        "호주 의약품·의료기기 허가·감독 기관. 수입·유통을 위해 "
                        "<b>ARTG(호주 의약품 등록)</b> 등록이 선행됩니다."
                    ),
                    s_apx,
                )
            ],
            [
                Paragraph(
                    _xml_amp_only(
                        "<b>PBS</b> (Pharmaceutical Benefits Scheme, "
                        "호주 의약품급여제도): 공적 급여로 등재 품목은 "
                        "<b>AEMP(정부 승인 출고가)</b>·<b>DPMQ(최대처방량 총약가)</b> "
                        "체계 내에서 공급됩니다."
                    ),
                    s_apx,
                )
            ],
            [
                Paragraph(
                    _xml_amp_only(
                        "<b>PBAC</b> (Pharmaceutical Benefits Advisory Committee, "
                        "약값 심사 위원회): PBS 등재·가격 재조정 안건을 심의·권고합니다."
                    ),
                    s_apx,
                )
            ],
            [
                Paragraph(
                    _xml_amp_only(
                        "<b>ABF</b> (Australian Border Force): "
                        "의약품 수입 통관·국경·세관 관련 절차를 관할합니다."
                    ),
                    s_apx,
                )
            ],
        ],
        colWidths=[CONTENT_W],
    )
    glossary_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_APPENDIX_BG),
                ("BOX", (0, 0), (-1, -1), 1, C_APPENDIX_BR),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(glossary_box)

    story.append(Spacer(1, 16))
    disc = ps(
        "R1Disc",
        fontName=base_font,
        fontSize=9,
        textColor=C_MUTED,
        alignment=TA_CENTER,
        leading=12,
    )
    story.append(
        Paragraph(
            _rx(
                "본 보고서는 공개된 데이터 기반 자동 생성본이며, "
                "실제 계약·가격 협상 시 현지 파트너와의 별도 확인이 필요합니다."
            ),
            disc,
        )
    )

    doc.build(story)


# ═══════════════════════════════════════════════════════════════
# 수출 전략 제안 보고서 PDF
# ═══════════════════════════════════════════════════════════════

_SCENARIO_LABELS = [
    ("aggressive",   "저가 진입 (Penetration Pricing)",    "scenario_penetration"),
    ("average",      "기준가 기반 (Reference Pricing)",    "scenario_reference"),
    ("conservative", "프리미엄 (Premium Pricing)",          "scenario_premium"),
]


def render_p2_pdf(
    row: dict[str, Any],
    seed: dict[str, Any],
    dispatch: dict[str, Any],
    p2_blocks: dict[str, str],
    fx_rates: dict[str, Any],
    out_path: Path,
    *,
    dispatch_public: dict[str, Any] | None = None,
    p2_blocks_public: dict[str, str] | None = None,
    dispatch_private: dict[str, Any] | None = None,
    p2_blocks_private: dict[str, str] | None = None,
) -> None:
    """수출 전략 제안 보고서 PDF 를 생성하여 out_path 에 저장.

    Args:
        row       : Supabase australia row (품목 메타·TGA·PBS 등)
        seed      : fob_reference_seeds.json 시드 (pricing_case·플래그·참고가)
        dispatch  : 본 PDF 세그먼트(공/민 중 파일명에 맞는 쪽) dispatch — 1~3절·기준가에 사용
        p2_blocks : 위 세그먼트에 대응하는 Haiku 8필드
        fx_rates  : {"aud_krw": float, "aud_usd": float}
        out_path  : 저장 경로
        dispatch_public / p2_blocks_public / dispatch_private / p2_blocks_private:
            P2 이중 파이프라인에서 **4-1 공공 / 4-2 민간** 각각 FOB·전략 표를 넣을 때 사용.
            네 인자를 모두 주면 README 양식대로 4-1·4-2 모두에 표가 붙는다.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    W, _H = A4
    MARGIN = 18 * mm
    CONTENT_W = W - 2 * MARGIN

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold"
    if base_font in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        bold_font = base_font

    # 색상 팔레트 (시장조사 PDF와 동일)
    C_NAVY   = colors.HexColor("#1B2A4A")
    C_BODY   = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT    = colors.HexColor("#F4F6F9")
    C_BAR    = colors.HexColor("#3a4a5e")

    COL1 = CONTENT_W * 0.26
    COL2 = CONTENT_W * 0.74

    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_title = ps("P2Title", fontName=bold_font, fontSize=16, leading=22,
                 alignment=TA_CENTER, textColor=C_NAVY, spaceAfter=6)
    s_date = ps("P2Date", fontName=base_font, fontSize=9, leading=12,
                alignment=TA_CENTER, textColor=colors.HexColor("#6B7280"))
    s_section = ps("P2Section", fontName=bold_font, fontSize=10, textColor=C_NAVY,
                   leading=14, spaceBefore=10, spaceAfter=6, keepWithNext=True)
    s_sub = ps("P2SubSection", fontName=bold_font, fontSize=9, textColor=C_NAVY,
               leading=13, spaceBefore=8, spaceAfter=4, keepWithNext=True)
    s_cell_h = ps("P2CellH", fontName=bold_font, fontSize=9, textColor=C_NAVY,
                  leading=13, wordWrap="CJK")
    s_cell = ps("P2Cell", fontName=base_font, fontSize=9, textColor=C_BODY,
                leading=14, wordWrap="CJK")
    s_bar = ps("P2Bar", fontName=bold_font, fontSize=9, textColor=colors.white,
               leading=13, wordWrap="CJK")
    s_hdr = ps("P2HdrWhite", fontName=bold_font, fontSize=8, textColor=colors.white,
               leading=12, wordWrap="CJK")
    s_cell_sm = ps("P2CellSm", fontName=base_font, fontSize=8,
                   textColor=colors.HexColor("#6B7280"), leading=11, wordWrap="CJK")

    def _rx(text: str) -> str:
        return (_annotate_terms_ko(text or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _trunc(text: str, limit: int = 800) -> str:
        s = (text or "").strip()
        return s if len(s) <= limit else s[:limit] + "…"

    def _base_style(extra: list | None = None) -> list:
        cmds = [
            ("GRID",   (0, 0), (-1, -1), 0.5, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING",   (0, 0), (-1, -1), 9),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 9),
        ]
        if extra:
            cmds.extend(extra)
        return cmds

    def _kv_table(rows: list[tuple[str, str]]) -> Table:
        pdata = [
            [Paragraph(_rx(k), s_cell_h), Paragraph(_rx(_trunc(v)), s_cell)]
            for k, v in rows
        ]
        extras: list[tuple] = []
        for i in range(len(rows)):
            if i % 2 == 1:
                extras.append(("BACKGROUND", (0, i), (-1, i), C_ALT))
        t = Table(pdata, colWidths=[COL1, COL2])
        t.setStyle(TableStyle(_base_style(extras)))
        return t

    # ── 데이터 추출 ──
    product_name = str(row.get("product_name_ko") or row.get("trade_name") or "—")
    inn = str(row.get("inn_normalized") or "—")
    strength = str(row.get("strength") or "")
    dosage = str(row.get("dosage_form") or "")
    hs_raw = (
        row.get("hs_code_6")
        or row.get("hs_code")
        or _hs_from_catalog(row.get("product_code") or row.get("product_id"))
    )
    hs = _hs_formatted(hs_raw)
    logic = dispatch.get("logic", "?")
    scenarios = dispatch.get("scenarios", {})
    warnings = dispatch.get("warnings", [])
    disclaimer = dispatch.get("disclaimer") or ""
    generated_date = datetime.now().strftime("%Y-%m-%d")

    aud_krw = fx_rates.get("aud_krw")
    aud_usd = fx_rates.get("aud_usd")
    usd_krw = None
    try:
        if aud_krw and aud_usd:
            usd_krw = float(aud_krw) / float(aud_usd)
    except Exception:
        usd_krw = None
    fx_str = f"1 USD = {usd_krw:,.2f} KRW" if usd_krw else "USD/KRW 환산 미확인"

    # ── Doc ──
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 4 * mm,
        title=f"호주 수출가격 전략 보고서 - {product_name}",
    )
    story: list = []

    # ── 타이틀 + 날짜 (양식 v5) ──
    story.append(Paragraph(_rx(f"호주 수출가격 전략 보고서 - {product_name}"), s_title))
    story.append(Paragraph(_rx(generated_date), s_date))
    story.append(Spacer(1, 10))

    # ── 제품 바 — 영문 성분·제형 중심 (양식 샘플과 동일 계열) ──
    str_form = " ".join(x for x in [strength, dosage] if x).strip()
    bar_txt = f"{inn} · {str_form}" if str_form else str(inn)
    bar_txt += f"  |  HS CODE: {hs}"
    bar_tbl = Table([[Paragraph(_rx(bar_txt), s_bar)]], colWidths=[CONTENT_W])
    bar_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_BAR),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(bar_tbl)
    story.append(Spacer(1, 12))

    # ── 1. 호주 거시 시장 (요약) ──
    story.append(Paragraph(_rx("1. 호주 거시 시장"), s_section))
    story.append(
        Paragraph(
            _rx(_trunc(p2_blocks.get("block_market_macro", "데이터 미확보 — 최신 공신력 출처 확인 필요"), 2000)),
            s_cell,
        )
    )
    story.append(Spacer(1, 8))

    aud_usd_rate = float(aud_usd) if aud_usd else 0.64
    aud_krw_rate = float(aud_krw) if aud_krw else 893.0

    def _fmt_usd(v: Any) -> str:
        try:
            n = float(v)
            if n <= 0:
                return "미확보"
            return f"USD {(n * aud_usd_rate):.2f}"
        except Exception:
            return "미확보"

    avg_sc = scenarios.get("average", {}) if isinstance(scenarios, dict) else {}
    dispatch_inputs = dispatch.get("inputs") or {}
    listed_aemp_aud = avg_sc.get("aemp_aud") or dispatch_inputs.get("aemp_aud")
    adjusted_aemp_aud = avg_sc.get("adjusted_aemp_aud")
    if adjusted_aemp_aud is None and listed_aemp_aud is not None:
        try:
            adjusted_aemp_aud = float(listed_aemp_aud) * 1.2
        except Exception:
            adjusted_aemp_aud = None

    _scen_labels = [
        ("aggressive", "저가 진입", "scenario_penetration"),
        ("average", "기준가", "scenario_reference"),
        ("conservative", "프리미엄", "scenario_premium"),
    ]

    def _scenario_fob_table(disp_in: dict[str, Any], blocks_in: dict[str, str]) -> Any:
        """3시나리오 FOB + Haiku 전략 한 줄 — 4-1(공공) / 4-2(민간) 각각에 사용."""
        scn = (disp_in or {}).get("scenarios", {}) or {}
        sum_rows: list[list] = [
            [
                Paragraph(_rx("시나리오"), s_hdr),
                Paragraph(_rx("수출가 (FOB)"), s_hdr),
                Paragraph(_rx("핵심 전략"), s_hdr),
            ]
        ]
        sum_ex: list[tuple] = [("BACKGROUND", (0, 0), (-1, 0), C_NAVY)]
        for idx, (skey, label_ko, p2k) in enumerate(_scen_labels, 1):
            sc = scn.get(skey, {}) or {}
            fob_aud = float(sc.get("fob_aud") or 0)
            fob_usd = fob_aud * aud_usd_rate
            fob_krw = float(sc.get("fob_krw") or (fob_aud * aud_krw_rate))
            price_line = f"USD {fob_usd:.2f} / KRW {fob_krw:,.0f}원"
            reason = (blocks_in or {}).get(p2k, "—")
            sum_rows.append(
                [
                    Paragraph(_rx(label_ko), s_cell_h),
                    Paragraph(_rx(price_line), s_cell),
                    Paragraph(_rx(_trunc(reason, 4000)), s_cell),
                ]
            )
            if idx % 2 == 0:
                sum_ex.append(("BACKGROUND", (0, idx), (-1, idx), C_ALT))
        tbl = Table(
            sum_rows,
            colWidths=[CONTENT_W * 0.22, CONTENT_W * 0.33, CONTENT_W * 0.45],
            repeatRows=1,
        )
        tbl.setStyle(TableStyle(_base_style(sum_ex)))
        return tbl

    # README 4-1/4-2: 공공+민간 dispatch 를 모두 넣으면 각 절에 표가 붙는다(이전엔 4-1이 비고 표가 4-2에만 갔음).
    _dual = dispatch_public is not None and dispatch_private is not None
    if _dual:
        sum_tbl_public = _scenario_fob_table(dispatch_public, p2_blocks_public or {})
        sum_tbl_private = _scenario_fob_table(dispatch_private, p2_blocks_private or {})
    else:
        sum_tbl = _scenario_fob_table(dispatch, p2_blocks)
    # 2. 단가(시장기준가) — 요청 양식 표 구조 반영
    story.append(Paragraph(_rx(f"2. {product_name} 단가(시장기준가)"), s_section))
    # 상단 2. 단가(시장기준가)에서 계산한 기준값 재사용

    benchmark_usd = 0.0
    row_aemp = row.get("aemp_aud") or row.get("pbs_aemp_aud")
    row_dpmq = row.get("dpmq_aud") or row.get("pbs_dpmq") or row.get("pbs_dpmq_aud")
    row_retail = row.get("retail_price_aud")
    dispatch_source = (dispatch_inputs.get("aemp_source") or dispatch_inputs.get("retail_source") or "미확인")
    source_label_map = {
        "crawler": "실시간 수집 데이터",
        "seed": "기준 시드 데이터",
        "dpmq_reverse": "DPMQ 역산값",
        "hardcoded": "수기 확정값",
    }
    dispatch_source_label = source_label_map.get(str(dispatch_source), str(dispatch_source))
    alpha_pct_val = dispatch_inputs.get("alpha_market_uplift_pct")
    try:
        alpha_pct_num = int(round(float(alpha_pct_val)))
    except Exception:
        alpha_pct_num = 20
    if logic == "A":
        pricing_method_text = (
            f"AEMP (Approved Ex-Manufacturer Price, 정부 승인 출고가) 기반 + 시장 보정(α={alpha_pct_num}%) + FOB 역산"
        )
    elif logic == "B":
        pricing_method_text = (
            "소매가 기반 역산 (GST·약국·도매·수입상 마진 반영, α 미적용)"
        )
    else:
        pricing_method_text = "병원 tender 수기 FOB (α 미적용)"

    try:
        if adjusted_aemp_aud is not None:
            benchmark_usd = float(adjusted_aemp_aud) * aud_usd_rate
        elif listed_aemp_aud is not None:
            benchmark_usd = float(listed_aemp_aud) * aud_usd_rate
        elif row_aemp is not None:
            benchmark_usd = float(row_aemp) * aud_usd_rate
    except Exception:
        benchmark_usd = 0.0
    benchmark_rows = [
        [Paragraph(_rx("기준 가격"), s_cell_h), Paragraph(_rx(f"USD {benchmark_usd:.2f}" if benchmark_usd > 0 else "USD 미확보"), s_cell)],
        [Paragraph(_rx("산정 방식"), s_cell_h), Paragraph(_rx(pricing_method_text), s_cell)],
        [Paragraph(_rx("공식 AEMP (USD 기준)"), s_cell_h), Paragraph(_rx(_fmt_usd(row_aemp)), s_cell)],
        [Paragraph(_rx("공식 DPMQ (USD 기준)"), s_cell_h), Paragraph(_rx(_fmt_usd(row_dpmq)), s_cell)],
        [Paragraph(_rx("시장 참고 소매가 (USD 기준)"), s_cell_h), Paragraph(_rx(_fmt_usd(row_retail)), s_cell)],
        [Paragraph(_rx("가격 근거 출처"), s_cell_h), Paragraph(_rx(dispatch_source_label), s_cell)],
        [Paragraph(_rx("시장 구분"), s_cell_h), Paragraph(_rx("공공 / 민간"), s_cell)],
    ]
    benchmark_tbl = Table(benchmark_rows, colWidths=[CONTENT_W * 0.24, CONTENT_W * 0.76])
    benchmark_tbl.setStyle(TableStyle(_base_style([("BACKGROUND", (0, 0), (0, -1), C_ALT)])))
    story.append(benchmark_tbl)
    story.append(Spacer(1, 8))

    def _resolve_reference_company() -> str:
        """거래처 참고 가격 표의 업체명(우선순위: 현지 스폰서 → 스폰서 목록 → 오리지널 스폰서)."""
        sponsor = str(row.get("tga_sponsor") or "").strip()
        if sponsor:
            return sponsor
        sponsors = row.get("tga_sponsors")
        if isinstance(sponsors, list):
            for s in sponsors:
                s_txt = str(s or "").strip()
                if s_txt:
                    sponsor = s_txt
                    break
        originator = str(row.get("originator_sponsor") or "").strip()
        if sponsor and originator and sponsor != originator:
            return f"{sponsor} (오리지널 스폰서: {originator})"
        if sponsor:
            return sponsor
        if originator:
            return originator
        return "미확보 (현지 스폰서 확인 필요)"

    reference_company = _resolve_reference_company()

    story.append(Paragraph(_rx("3. 거래처 참고 가격"), s_section))
    ref_rows = [
        [
            Paragraph(_rx("업체명"), s_hdr),
            Paragraph(_rx("제품명"), s_hdr),
            Paragraph(_rx("성분함량"), s_hdr),
            Paragraph(_rx("시장가(USD 기준)"), s_hdr),
        ],
        [
            Paragraph(_rx(reference_company), s_cell),
            Paragraph(_rx(product_name), s_cell),
            Paragraph(_rx((f"{inn} {strength} {dosage}").strip() or "미확보"), s_cell),
            Paragraph(_rx(f"USD {benchmark_usd:.2f}" if benchmark_usd > 0 else "USD 미확보"), s_cell),
        ],
    ]
    ref_tbl = Table(
        ref_rows,
        colWidths=[CONTENT_W * 0.16, CONTENT_W * 0.24, CONTENT_W * 0.24, CONTENT_W * 0.36],
        repeatRows=1,
    )
    ref_tbl.setStyle(TableStyle(_base_style([("BACKGROUND", (0, 0), (-1, 0), C_NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)])))
    story.append(ref_tbl)
    story.append(Spacer(1, 6))
    story.append(Paragraph(_rx("시장가 근거 상세"), s_sub))
    story.append(
        Paragraph(
            _rx(_trunc(p2_blocks.get("block_extract", "시장가 데이터 미확보"), 4000)),
            s_cell,
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph(_rx("4. 가격 시나리오"), s_section))
    story.append(Paragraph(_rx("4-1. 공공 시장 (데이터 소스: 정부 입찰가, PBS 공시가 등)"), s_sub))
    story.append(Paragraph(_rx("※ 어려운 영어 용어는 괄호로 한글 설명 병기: PBS (Pharmaceutical Benefits Scheme, 호주 의약품 급여 제도)"), s_cell_sm))
    story.append(Spacer(1, 4))
    if _dual:
        story.append(sum_tbl_public)
    else:
        story.append(
            Paragraph(
                _rx(
                    "· 본 절(공공) FOB 3시나리오: 이 PDF가 단일 세그먼트만 생성된 경우 4-2 민간 절에 표를 배치합니다. "
                    "공공·민간 양쪽 표는 수출가격 P2 이중(공+민) 파이프라인으로 생성하세요."
                ),
                s_cell_sm,
            )
        )
    story.append(Spacer(1, 8))

    story.append(Paragraph(_rx("4-2. 민간 시장 (데이터 소스: 병원, 약국)"), s_sub))
    if _dual:
        story.append(sum_tbl_private)
    else:
        story.append(sum_tbl)
    story.append(Spacer(1, 8))

    # 5. 면책 (README P2 양식)
    story.append(Paragraph(_rx("5. 면책"), s_section))
    # ── 참고(산출 메타) ──
    warn_text = " / ".join(w for w in warnings if w) if warnings else "없음"
    flags = []
    if seed.get("pbac_superiority_required"):
        flags.append(
            "시드 표시: PBS 신규 등재 시 PBAC에서 비교임상·우월성이 논의될 수 있는 품목군(개별 심의 대상)"
        )
    if seed.get("hospital_channel_only"):
        flags.append("시드 표시: 병원조달·입찰 채널 중심(약국 일반 유통 아님)")
    if seed.get("section_19a_flag"):
        flags.append("시드 표시: Section 19A(일시수입) 경로 언급")
    if seed.get("commercial_withdrawal_flag"):
        flags.append(
            "데이터: 상업적 철수(Commercial Withdrawal) 이력 있음 — 재진입·재평가는 건별 검토 대상"
        )
    flag_text = " / ".join(flags) if flags else "없음"
    # NOTE: 긴 면책/경고를 단일 Table 셀에 넣으면 A4 하단에서 잘릴 수 있어 Paragraph 로 분리.
    footer_para = (
        "<b>참고(산출 경로·환율·경고)</b><br/>"
        f"· 분석 경로: Logic {logic} ({_rx(str(seed.get('pricing_case', '—')))} )<br/>"
        f"· 적용 환율: {_rx(fx_str)}<br/>"
        f"· 경고 사항: {_rx(_trunc(warn_text, 4000))}<br/>"
        f"· 시드·데이터 표시: {_rx(_trunc(flag_text, 4000))}<br/>"
        f"· 면책 조항: {_rx(_trunc(disclaimer or '없음', 4000))}"
    )
    story.append(Paragraph(footer_para, s_cell_sm))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            _rx(
                "* 본 산출 결과는 AI 분석에 기반한 추정치이므로, 최종 의사결정 전 반드시 담당자의 검토 및 확인이 필요합니다."
            ),
            s_cell_sm,
        )
    )
    story.append(Spacer(1, 8))
    story.append(Paragraph(_rx("6. 용어 해설 (별첨)"), s_section))
    story.append(
        Paragraph(
            _rx(
                "· AEMP (Approved Ex-Manufacturer Price, 정부 승인 출고가): "
                "호주 PBS 등재 의약품의 정부 공시 기준 출고가입니다."
            ),
            s_cell_sm,
        )
    )
    story.append(
        Paragraph(
            _rx(
                "· DPMQ (Dispensed Price for Maximum Quantity, 최대 처방량 총약가): "
                "최대 처방량 기준으로 환자에게 조제될 때의 총 약가(공시 기준)입니다."
            ),
            s_cell_sm,
        )
    )

    doc.build(story)


# ═══════════════════════════════════════════════════════════════════════
# 바이어 발굴 PDF (Phase 3) — 2026-04-20 신규 추가
# 공통 헬퍼만 재사용.
# ═══════════════════════════════════════════════════════════════════════


def _buyers_tier_from_source_flags(source_flags: Any) -> str:
    """au_buyers.source_flags 에서 tier_A/B/C 추출. 없으면 'A' 반환 (기본)."""
    if isinstance(source_flags, list):
        for f in source_flags:
            if isinstance(f, str) and f.startswith("tier_"):
                return f.split("_", 1)[1]
    return "A"


def _buyers_load_fx() -> dict[str, float]:
    """buyer_discovery.utils.fx_rate 재사용. 실패 시 fallback."""
    try:
        import sys as _sys
        _UP = Path(__file__).resolve().parent
        if str(_UP) not in _sys.path:
            _sys.path.insert(0, str(_UP))
        from buyer_discovery.utils.fx_rate import get_fx_rates
        return get_fx_rates()
    except Exception:
        return {"aud_krw": 900.0, "aud_usd": 0.65}


def _buyers_fetch_rows(product_id: str | None) -> list[dict[str, Any]]:
    """Supabase au_buyers 에서 조회."""
    import sys as _sys
    _UP = Path(__file__).resolve().parent
    if str(_UP / "crawler") not in _sys.path:
        _sys.path.insert(0, str(_UP / "crawler"))
    from db.supabase_insert import get_supabase_client  # type: ignore

    sb = get_supabase_client()
    q = sb.table("au_buyers").select("*")
    if product_id:
        q = q.eq("product_id", product_id)
    q = q.order("product_id").order("rank")
    rows = q.execute().data or []
    return [r for r in rows if not (r.get("product_id") or "").startswith("_")]


def _is_non_buyer_row(row: dict[str, Any]) -> bool:
    """바이어 후보 PDF에서 제외할 비유통성 행 판정(명시적 '바이어 아님'만 제외)."""
    text = " ".join(
        str(row.get(k) or "")
        for k in ("notes", "company_name", "annual_revenue_rank")
    ).lower()
    return "바이어 아님" in text or "buyer 아님" in text


def _buyers_load_product_meta() -> dict[str, dict[str, Any]]:
    """crawler/au_products.json 메타 로드."""
    import json as _json
    path = Path(__file__).resolve().parent / "crawler" / "au_products.json"
    if not path.is_file():
        return {}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {p.get("product_id"): p for p in (data.get("products") or []) if p.get("product_id")}


def render_buyers_pdf(
    product_id: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """바이어 발굴 보고서 PDF 생성.

    Args:
        product_id: 특정 품목만. None 이면 8품목 전체.
        output_dir: 저장 디렉토리. None 이면 upharma-au/reports/.

    Returns:
        저장된 PDF 절대 경로.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    _UP = Path(__file__).resolve().parent
    if output_dir is None:
        output_dir = _UP / "reports"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = _buyers_fetch_rows(product_id)
    if not rows:
        raise ValueError(
            f"au_buyers 데이터 없음 ({'전체' if not product_id else product_id}). "
            f"먼저 stage2_scoring.py 를 실행해서 UPSERT 하세요."
        )
    meta_map = _buyers_load_product_meta()
    fx = _buyers_load_fx()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if product_id:
        out_path = output_dir / f"au_buyers_{product_id}_{ts}.pdf"
    else:
        out_path = output_dir / f"au_buyers_all_{ts}.pdf"

    W, _H = A4
    MARGIN = 18 * mm
    CONTENT_W = W - 2 * MARGIN

    base_font = _register_korean_font()
    bold_font = f"{base_font}-Bold"
    if base_font in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        bold_font = base_font

    C_NAVY = colors.HexColor("#1B2A4A")
    C_BODY = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT = colors.HexColor("#F4F6F9")
    C_MUTED = colors.HexColor("#6B7280")
    C_TIER_A = colors.HexColor("#22c55e")
    C_TIER_B = colors.HexColor("#3b82f6")
    C_TIER_C = colors.HexColor("#9ca3af")

    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_title = ps("BTitle", fontName=bold_font, fontSize=16, leading=22,
                 alignment=TA_CENTER, textColor=C_NAVY, spaceAfter=6)
    s_date = ps("BDate", fontName=base_font, fontSize=9, leading=12,
                alignment=TA_CENTER, textColor=C_MUTED)
    s_sec = ps("BSec", fontName=bold_font, fontSize=11, leading=16,
               textColor=C_NAVY, spaceBefore=10, spaceAfter=6)
    s_sub = ps("BSub", fontName=bold_font, fontSize=10, leading=16,
               textColor=C_NAVY, spaceBefore=10, spaceAfter=6, keepWithNext=True)
    s_body = ps("BBody", fontName=base_font, fontSize=10, leading=17,
                textColor=C_BODY, alignment=TA_LEFT)
    s_small = ps("BSmall", fontName=base_font, fontSize=9, leading=14,
                 textColor=C_MUTED, alignment=TA_LEFT)

    def tier_color(t: str):
        if t == "A":
            return C_TIER_A
        if t == "B":
            return C_TIER_B
        return C_TIER_C

    def safe(v: Any, dash: str = "—") -> str:
        if v is None or v == "":
            return dash
        return str(v)

    def _rx(text: str) -> str:
        return (_annotate_terms_ko(text or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _trunc(text: str, limit: int = 320) -> str:
        s = (text or "").strip()
        return s if len(s) <= limit else s[:limit] + "…"

    rows_by_pid: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        rows_by_pid.setdefault(r["product_id"], []).append(r)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="UPharma 호주 바이어발굴 보고서",
        author="한국유나이티드제약",
    )
    story: list = []

    story.append(Paragraph("호주 바이어 후보 리스트", s_title))
    story.append(Paragraph(
        f"한국유나이티드제약 · 생성 {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"실시간 환율 1 AUD = {fx.get('aud_krw', 0):.1f} KRW · {fx.get('aud_usd', 0) * 100:.2f} USD¢",
        s_date,
    ))
    story.append(Spacer(1, 8))

    for i, (pid, buyers) in enumerate(rows_by_pid.items()):
        if i > 0:
            story.append(PageBreak())

        meta = meta_map.get(pid) or {}
        filtered_buyers = [b for b in buyers if not _is_non_buyer_row(b)]
        if not filtered_buyers:
            filtered_buyers = buyers
        pname = meta.get("product_name_ko") or pid
        pen = meta.get("product_name_en") or ""
        inns = " + ".join(meta.get("inn_components") or [])

        story.append(Paragraph(
            f"【 {safe(pname)} 】 {safe(pen, '')}",
            s_sec,
        ))
        story.append(Paragraph("1. 바이어 후보 리스트 (현지 유통 가능 바이어)", s_sec))
        story.append(Paragraph(
            f"성분: <b>{safe(inns)}</b> · 가격 케이스: {safe(meta.get('pricing_case'))} · 바이어 {len(filtered_buyers)}개",
            s_body,
        ))
        story.append(Paragraph(
            "필터링 조건: 원료의약품(API) 기업·다국적 글로벌 기업·오리지널 제약사 제외 / 완제품(FDF) 판매 기업 우선",
            s_small,
        ))
        story.append(Spacer(1, 6))

        header = ["기업명", "주력상품", "이메일"]
        tbl_data: list[list[Any]] = [header]
        for b in filtered_buyers:
            cats = b.get("therapeutic_categories") or []
            if isinstance(cats, list):
                cats_str = ", ".join(str(c) for c in cats if str(c).strip()) or "완제품 유통/판매"
            else:
                cats_str = str(cats or "완제품 유통/판매")
            company_name = f"{safe(b.get('company_name'))} (#{b.get('rank') or '?'})"
            tbl_data.append([
                Paragraph(company_name, s_body),
                Paragraph(_rx(_trunc(cats_str, 120)), s_body),
                Paragraph(safe(b.get("email"), "비공개"), s_body),
            ])

        col_widths = [
            CONTENT_W * 0.38,
            CONTENT_W * 0.42,
            CONTENT_W * 0.20,
        ]
        tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)
        tstyle = [
            ("FONTNAME", (0, 0), (-1, -1), base_font),
            ("FONTNAME", (0, 0), (-1, 0), bold_font),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_ALT]),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]
        tbl.setStyle(TableStyle(tstyle))
        story.append(tbl)

        story.append(Spacer(1, 12))
        story.append(Paragraph("2. 바이어 정보 상세", s_sec))

        for b in filtered_buyers[:10]:
            cats = b.get("therapeutic_categories") or []
            if isinstance(cats, list):
                cats_str = " · ".join(str(c) for c in cats) or "—"
            else:
                cats_str = str(cats)
            locs = b.get("factory_locations") or []
            if isinstance(locs, list):
                locs_str = ", ".join(str(x) for x in locs if x) or "—"
            else:
                locs_str = str(locs)
            assocs = []
            if b.get("is_ma_member"):    assocs.append("MA")
            if b.get("is_gbma_member"):  assocs.append("GBMA")
            if b.get("is_gpce_exhibitor"): assocs.append("GPCE")
            assoc_str = " · ".join(assocs) or "—"

            company_title = f"{b.get('rank') or '?'} ) {safe(b.get('company_name'))}"
            story.append(Paragraph(company_title, s_sub))
            story.append(Spacer(1, 2))

            notes = safe(b.get("notes"), "공개 자료 요약 기반 기업 프로필")
            story.append(Paragraph(f"- 기업 개요: {_rx(_trunc(notes, 320))}", s_body))
            story.append(Spacer(1, 2))

            story.append(Paragraph("- 추천 이유 (5가지 주요 기준):", s_body))
            story.append(Paragraph(
                f"  ① 매출 규모: {safe(b.get('annual_revenue_rank'))} (점수 {b.get('psi_sales_scale') or 0})",
                s_body,
            ))
            story.append(Paragraph(
                f"  ② 파이프라인(중요): {cats_str} / 점수 {b.get('psi_pipeline') or 0}",
                s_body,
            ))
            story.append(Paragraph(
                f"  ③ 제조소 보유: {('보유 - ' + locs_str) if b.get('has_au_factory') == 'Y' else '미보유'} "
                f"(점수 {b.get('psi_manufacturing') or 0})",
                s_body,
            ))
            story.append(Paragraph(
                f"  ④ 수입 경험: TGA ARTG {b.get('tga_artg_count') or 0}건 (점수 {b.get('psi_import_exp') or 0})",
                s_body,
            ))
            story.append(Paragraph(
                f"  ⑤ 약국 체인/유통 채널: {assoc_str} (점수 {b.get('psi_pharmacy_chain') or 0})",
                s_body,
            ))
            story.append(Spacer(1, 2))

            story.append(Paragraph("- 기본 정보:", s_body))
            story.append(Paragraph(f"  주소: {safe(b.get('state'), '미확보')}", s_body))
            story.append(Paragraph(f"  연락처: {safe(b.get('phone'), '미확보')} / {safe(b.get('email'), '미확보')}", s_body))
            story.append(Paragraph("  설립 연도: 미확보", s_body))
            story.append(Paragraph(f"  홈페이지: {safe(b.get('website'), '미확보')}", s_body))
            story.append(Paragraph(f"  파이프라인: {cats_str}", s_body))
            story.append(Paragraph(f"- 기업 규모: {safe(b.get('annual_revenue_rank'), '매출/직원수 미확보')}", s_body))
            story.append(Paragraph(
                f"- 등록 제품: TGA ARTG 등록 {b.get('tga_artg_count') or 0}건 (CPHI 등 외부 등록 제품은 별도 조사 필요)",
                s_body,
            ))
            story.append(Paragraph("* 출처: Gemini 딥 리서치, Perplexity 분석", s_small))
            sep = Table([[""]], colWidths=[CONTENT_W])
            sep.setStyle(
                TableStyle(
                    [
                        ("LINEABOVE", (0, 0), (-1, 0), 0.6, C_BORDER),
                        ("TOPPADDING", (0, 0), (-1, 0), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                    ]
                )
            )
            story.append(Spacer(1, 8))
            story.append(sep)
            story.append(Spacer(1, 10))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "<b>5지표 가중치:</b> 매출 규모 35% · 성분 경험(파이프라인) 25% · "
        "호주 제조소 20% · 수입 경험 10% · 약국 체인 10%",
        s_small,
    ))
    story.append(Paragraph(
        "<b>티어:</b> A = 해당 품목 INN 직접 등재 · B = 치료영역 매칭 (오리지널/제네릭 파트너) · "
        "C = 기타 (협회·컨퍼런스 기반 잠재)",
        s_small,
    ))
    story.append(Paragraph(
        "본 보고서의 매출 등급은 Perplexity sonar-pro 웹 검색 + Claude Haiku 교차검증 결과이며, "
        "Gemini 딥리서치 수기 데이터 (45개사) 우선 적용. 실제 바이어 접촉 시 현지 상황 별도 확인 권장.",
        s_small,
    ))

    doc.build(story)
    return out_path
