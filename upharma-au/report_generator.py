#!/usr/bin/env python3
"""호주 수출 시장조사 보고서 PDF 생성기 (reportlab 기반).

1공정 PDF v3 (권장)
    render_pdf(ReportR1Payload | dict, output_path)
    · stage1_schema.ReportR1Payload — HS CODE, [1]~[4] 4블록, 별첨 용어집
    · dict 는 Pydantic 검증 후 실패 시 v2 크롤러 필드로 보정(coerce_dict_to_report_r1)

1공정 PDF v2 레거시 (render_api.py 등 기존 호출)
    render_pdf(row, blocks, refs, meta, output_path)
    · 제품정보 박스, Case·신뢰도 표기 — CC가 v3 페이로드로 전환 시 제거 예정

2공정
    render_p2_pdf(...) — 본 모듈 하단 유지
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from stage1_schema import ReportR1Payload, coerce_dict_to_report_r1

ROOT = Path(__file__).resolve().parent

_FONT_CACHE: str | None = None


def _register_korean_font() -> str:
    """한글 폰트 등록. 시스템 TTF → CID 폴백 → Helvetica."""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("NanumGothic",  str(ROOT / "fonts" / "NanumGothic.ttf")),
        ("AppleGothic",  "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        ("NanumGothic",  "/Library/Fonts/NanumGothic.ttf"),
        ("MalgunGothic", "C:/Windows/Fonts/malgun.ttf"),
    ]
    for name, path in candidates:
        if Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                pdfmetrics.registerFont(TTFont(f"{name}-Bold", path))
                _FONT_CACHE = name
                return name
            except Exception:
                continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        _FONT_CACHE = "HYSMyeongJo-Medium"
        return "HYSMyeongJo-Medium"
    except Exception:
        pass
    _FONT_CACHE = "Helvetica"
    return "Helvetica"


def _verdict_label(export_viable: str | None) -> str:
    ev = (export_viable or "").lower()
    return {"viable": "가능", "conditional": "조건부", "not_viable": "불가"}.get(ev, "분석 중")


def _hs_formatted(hs_code_6: str | None) -> str:
    s = str(hs_code_6 or "").strip()
    if len(s) >= 6:
        return f"{s[:4]}.{s[4:6]}"
    return s or "—"


def _source_label(src: str | None) -> str:
    return {
        "semantic_scholar": "Semantic Scholar",
        "pubmed":           "PubMed",
        "perplexity":       "Perplexity",
    }.get((src or "").lower(), (src or "출처"))


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
        return ((text or "")
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
    """1공정 PDF 생성.

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
    if base_font == "HYSMyeongJo-Medium":
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
        return ((text or "")
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
    hs = _hs_formatted(row.get("hs_code_6"))
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


def _render_pdf_stage1_v3(payload: ReportR1Payload, out_path: Path) -> None:
    """1공정 시장분석 보고서 PDF v3 — HS CODE·4블록·참고자료 표·별첨 용어집."""
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
    if base_font == "HYSMyeongJo-Medium":
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
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

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
        title=f"호주 시장 분석 보고서 — {payload.product_name}",
    )
    story: list = []

    story.append(Paragraph(_rx("호주 시장 분석 보고서"), s_title))
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

    story.extend(_section_line("[1] 진출 적합 판정"))
    story.append(
        _kv(
            [
                ("판정", payload.verdict),
                ("요약", payload.verdict_summary),
            ]
        )
    )
    story.append(Spacer(1, 8))

    story.extend(_section_line("[2] 판정 근거"))
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

    story.extend(_section_line("[3] 시장 진출 전략"))
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

    story.extend(_section_line("[4] 근거 및 출처"))
    story.append(Paragraph(_rx("4-1. Perplexity 추천 논문"), s_hdr))
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
    story.append(Paragraph(_rx("4-2. 사용된 DB / 기관"), s_hdr))
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
                    _rx(
                        "<b>TGA</b> (Therapeutic Goods Administration): "
                        "호주 의약품·의료기기 허가·감독 기관. 수입·유통을 위해 "
                        "<b>ARTG(호주 의약품 등록)</b> 등록이 선행됩니다."
                    ),
                    s_apx,
                )
            ],
            [
                Paragraph(
                    _rx(
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
                    _rx(
                        "<b>PBAC</b> (Pharmaceutical Benefits Advisory Committee, "
                        "약값 심사 위원회): PBS 등재·가격 재조정 안건을 심의·권고합니다."
                    ),
                    s_apx,
                )
            ],
            [
                Paragraph(
                    _rx(
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
# 2공정 — 수출 전략 제안 보고서 PDF
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
) -> None:
    """2공정 수출 전략 제안 보고서 PDF 를 생성하여 out_path 에 저장.

    Args:
        row       : Supabase australia row (품목 메타·TGA·PBS 등)
        seed      : fob_reference_seeds.json 시드 (pricing_case·플래그·참고가)
        dispatch  : fob_calculator.dispatch_by_pricing_case() 결과
        p2_blocks : _haiku_p2_blocks() 8필드 dict
        fx_rates  : {"aud_krw": float, "aud_usd": float}
        out_path  : 저장 경로
    """
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
    if base_font == "HYSMyeongJo-Medium":
        bold_font = base_font

    # 색상 팔레트 (1공정과 동일)
    C_NAVY   = colors.HexColor("#1B2A4A")
    C_BODY   = colors.HexColor("#1A1A1A")
    C_BORDER = colors.HexColor("#D0D7E3")
    C_ALT    = colors.HexColor("#F4F6F9")
    C_BAR    = colors.HexColor("#1E3A5F")
    # 시나리오 강조색
    C_PENE   = colors.HexColor("#2563EB")  # 저가 진입 — 파랑
    C_REF    = colors.HexColor("#059669")  # 기준가   — 초록
    C_PREM   = colors.HexColor("#D97706")  # 프리미엄 — 오렌지

    COL1 = CONTENT_W * 0.26
    COL2 = CONTENT_W * 0.74

    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    s_title = ps("P2Title", fontName=bold_font, fontSize=18, leading=24,
                 alignment=TA_CENTER, textColor=C_NAVY, spaceAfter=4)
    s_date = ps("P2Date", fontName=base_font, fontSize=10, leading=13,
                alignment=TA_CENTER, textColor=colors.HexColor("#6B7280"))
    s_section = ps("P2Section", fontName=bold_font, fontSize=11, textColor=C_NAVY,
                   leading=15, spaceBefore=8, spaceAfter=4)
    s_cell_h = ps("P2CellH", fontName=bold_font, fontSize=9, textColor=C_NAVY,
                  leading=13, wordWrap="CJK")
    s_cell = ps("P2Cell", fontName=base_font, fontSize=9, textColor=C_BODY,
                leading=14, wordWrap="CJK")
    s_bar = ps("P2Bar", fontName=bold_font, fontSize=9, textColor=colors.white,
               leading=13, wordWrap="CJK")
    s_hdr = ps("P2HdrWhite", fontName=bold_font, fontSize=9, textColor=colors.white,
               leading=13, wordWrap="CJK")
    s_cell_sm = ps("P2CellSm", fontName=base_font, fontSize=7,
                   textColor=colors.HexColor("#6B7280"), leading=10, wordWrap="CJK")

    def _rx(text: str) -> str:
        return ((text or "")
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
    hs = _hs_formatted(row.get("hs_code_6"))
    logic = dispatch.get("logic", "?")
    scenarios = dispatch.get("scenarios", {})
    warnings = dispatch.get("warnings", [])
    disclaimer = dispatch.get("disclaimer") or ""
    generated_date = datetime.now().strftime("%Y-%m-%d")

    aud_krw = fx_rates.get("aud_krw")
    aud_usd = fx_rates.get("aud_usd")
    fx_str = f"1 AUD = {aud_krw:.2f} KRW / {aud_usd:.4f} USD" if aud_krw and aud_usd else "환율 미확인"

    # ── Doc ──
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"수출 전략 제안 보고서 — {product_name}",
    )
    story: list = []

    # ── 타이틀 + 날짜 ──
    story.append(Paragraph(_rx("수출 전략 제안 보고서"), s_title))
    story.append(Paragraph(_rx(generated_date), s_date))
    story.append(Spacer(1, 6))

    # ── 제품 바 (1공정과 동일 디자인) ──
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

    # Phase 4.3-v3 — 0. 제품 정보 (자사 vs 호주 PBS 시장) — render_pdf 와 동일
    story.extend(_build_product_info_flowables(
        row,
        content_width=CONTENT_W,
        base_font=base_font,
        bold_font=bold_font,
    ))

    # ── 1. 추출정보 요약 ──
    story.append(Paragraph(_rx("1. 품목 추출정보"), s_section))
    story.append(_kv_table([
        ("분석 경로", f"Logic {logic} ({seed.get('pricing_case', '—')})"),
        ("적용 환율", fx_str),
        ("AI 분석",  p2_blocks.get("block_extract", "—")),
    ]))
    story.append(Spacer(1, 6))

    # ── 2. FOB 3시나리오 역산 ──
    story.append(Paragraph(_rx("2. FOB 3시나리오 역산"), s_section))
    story.append(Paragraph(_rx(p2_blocks.get("block_fob_intro", "")), s_cell))
    story.append(Spacer(1, 4))

    # 시나리오 테이블: 시나리오명 | FOB (AUD) | FOB (KRW) | 마진% | 전략 근거
    w_name = CONTENT_W * 0.18
    w_aud  = CONTENT_W * 0.13
    w_krw  = CONTENT_W * 0.15
    w_mar  = CONTENT_W * 0.10
    w_rea  = CONTENT_W * 0.44

    sce_header = [
        Paragraph("시나리오", s_hdr),
        Paragraph("FOB (AUD)", s_hdr),
        Paragraph("FOB (KRW)", s_hdr),
        Paragraph("마진%", s_hdr),
        Paragraph("전략 근거", s_hdr),
    ]
    sce_rows: list[list] = [sce_header]
    sce_extras: list[tuple] = [("BACKGROUND", (0, 0), (-1, 0), C_NAVY)]
    scenario_colors = [C_PENE, C_REF, C_PREM]

    for idx, (key, label, block_key) in enumerate(_SCENARIO_LABELS, 1):
        sc = scenarios.get(key, {})
        fob_aud = sc.get("fob_aud", 0)
        fob_krw = sc.get("fob_krw", 0)
        margin = sc.get("importer_margin_pct", "—")
        reason = p2_blocks.get(block_key, "—")
        sce_rows.append([
            Paragraph(_rx(label), s_cell_h),
            Paragraph(f"${fob_aud:.2f}" if fob_aud else "—", s_cell),
            Paragraph(f"₩{fob_krw:,.0f}" if fob_krw else "—", s_cell),
            Paragraph(f"{margin}%", s_cell),
            Paragraph(_rx(_trunc(reason, 300)), s_cell),
        ])
        if idx % 2 == 0:
            sce_extras.append(("BACKGROUND", (0, idx), (-1, idx), C_ALT))

    sce_tbl = Table(sce_rows, colWidths=[w_name, w_aud, w_krw, w_mar, w_rea])
    sce_tbl.setStyle(TableStyle(_base_style(sce_extras)))
    story.append(sce_tbl)
    story.append(Spacer(1, 8))

    # ── 3. 권장 진입 전략 ──
    story.append(Paragraph(_rx("3. 권장 진입 전략"), s_section))
    story.append(_kv_table([
        ("전략 요약", p2_blocks.get("block_strategy", "—")),
    ]))

    story.append(PageBreak())

    # ── Page 2 ──

    # ── 4. 리스크 분석 ──
    story.append(Paragraph(_rx("4. 리스크 분석"), s_section))
    story.append(_kv_table([
        ("리스크 요약", p2_blocks.get("block_risks", "—")),
    ]))
    story.append(Spacer(1, 6))

    # ── 5. 경쟁사 포지셔닝 ──
    story.append(Paragraph(_rx("5. 경쟁사 포지셔닝"), s_section))
    competitors = seed.get("competitor_brands_on_pbs") or []
    comp_text = ", ".join(competitors) if competitors else "경쟁 브랜드 미확인"
    story.append(_kv_table([
        ("PBS 등재 경쟁사", comp_text),
        ("포지셔닝 분석", p2_blocks.get("block_positioning", "—")),
    ]))
    story.append(Spacer(1, 6))

    # ── 6. 산출 조건 및 면책 ──
    story.append(Paragraph(_rx("6. 산출 조건 및 면책사항"), s_section))

    # 경고 사항
    warn_text = " / ".join(w for w in warnings if w) if warnings else "없음"
    cond_rows: list[tuple[str, str]] = [
        ("pricing_case", seed.get("pricing_case", "—")),
        ("confidence", str(seed.get("confidence_score", "—"))),
        ("경고 사항", warn_text),
        ("면책 조항", disclaimer),
    ]
    # 규제 플래그
    flags = []
    if seed.get("pbac_superiority_required"):
        flags.append("PBAC 임상우월성(superiority) 입증 필요")
    if seed.get("hospital_channel_only"):
        flags.append("병원조달 전용 (약국 유통 불가)")
    if seed.get("section_19a_flag"):
        flags.append("Section 19A 일시수입 경로")
    if seed.get("commercial_withdrawal_flag"):
        flags.append("Commercial Withdrawal 이력")
    if flags:
        cond_rows.append(("규제 플래그", " / ".join(flags)))

    story.append(_kv_table(cond_rows))

    doc.build(story)
