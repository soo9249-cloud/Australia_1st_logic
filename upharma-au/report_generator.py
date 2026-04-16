#!/usr/bin/env python3
"""호주 수출 시장조사 보고서 PDF 생성기 (reportlab 기반).

input 구조:
    row    : supabase australia 테이블 row (품목 메타·TGA·PBS·NSW·Chemist 컬럼)
    blocks : Claude Haiku가 생성한 block2_* / block3_* / block4_regulatory 10개 필드
    refs   : 하이브리드 학술 검색 결과(Semantic Scholar · PubMed · Perplexity)
    meta   : export_viable / confidence / confidence_breakdown 등 판정 메타

output:
    upharma-au/reports/au_report_{product_key}_{YYYYMMDD_HHMMSS}.pdf

PDF 구조 (품목 1건당 2페이지):
    p1: 타이틀 + 제품바 + 1.판정 + 2.판정근거(5축) + 3.시장진출전략(4축)
    p2: 4. 근거 및 출처 — 4-1 PERPLEXITY 추천 논문 / 4-2 사용된 DB·기관
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

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


def render_pdf(
    row: dict[str, Any],
    blocks: dict[str, str],
    refs: list[dict[str, Any]],
    meta: dict[str, Any],
    out_path: Path,
) -> None:
    """보고서 PDF를 생성하여 out_path 에 저장."""
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
