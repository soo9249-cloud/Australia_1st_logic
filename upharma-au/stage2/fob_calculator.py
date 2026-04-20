"""호주 수출 FOB 역산 계산 모듈 (수출 전략).

**설계 원칙**
- 크롤러와 완전히 분리: `crawler/` 아래 어떤 파일에도 의존하지 않음.
- UI와도 분리: 입력 dict → 출력 dict의 순수 계산 모듈.
- 수수료 상수·역산 공식은 전부 이 파일 안에서 완결.
- 판정 분기는 `fob_reference_seeds.json`의 `pricing_case`로 라우팅.

**pricing_case 라우팅**
- DIRECT                  → Logic A, PBS AEMP 직접 사용
- COMPONENT_SUM           → Logic A, 성분별 AEMP 합산 (서브케이스 1~3: 양쪽 등재 합산 / 등재+소매역산 / 양쪽 역산)
- ESTIMATE_private        → Logic B, 소매가에서 유통마진 역산
- ESTIMATE_withdrawal     → 계산 거절 (Commercial Withdrawal 플래그만 표시)
- ESTIMATE_substitute     → Logic A 변형, 대체계열 AEMP를 참고가로 차용
- ESTIMATE_hospital       → Logic B 변형, 병원 tender 가격 범위 추정

**반환 dict 스키마**
{
  "logic": "A" | "B" | "blocked",
  "scenarios": {
    "aggressive":  {"importer_margin_pct": 30, "fob_aud": ..., "fob_krw": ..., ...},
    "average":     {"importer_margin_pct": 20, ...},
    "conservative":{"importer_margin_pct": 10, ...},
  },
  "inputs":  {...},           # 계산 입력값 스냅샷 (AEMP, retail, FX 등)
  "warnings": [str, ...],     # PBAC/withdrawal/substitute 등 플래그 메시지
  "disclaimer": str,          # Gross vs Net FOB 주의사항
  "blocked_reason": str|None, # blocked일 때만
}
"""

from __future__ import annotations

from typing import Any


# ============================================================================
# PBS 가격 체계 규제 상수 (AUD, 2026 기준)
# 출처: https://www.pbs.gov.au/pbs/healthpro/explanatory-notes/front/fee
# Hydrine DPMQ $48.11 → AEMP $31.92 실측으로 5-tier 공식 검증 완료 (2026-04-15)
# ============================================================================
# 조제료(Ready-prepared dispensing fee)
DISPENSING_FEE_READY = 8.88

# 도매마진(Wholesale mark-up) — DPMQ→AEMP 역산 3구간
WHOLESALE_TIER1_FLAT = 0.41
WHOLESALE_TIER2_PCT = 0.0752
WHOLESALE_TIER3_FLAT = 54.14

# 약국 인센티브(AHI — Administration, Handling, Infrastructure) 3구간
AHI_TIER1_FLAT = 4.91
AHI_TIER2_PCT = 0.05
AHI_TIER3_FLAT = 99.91

# EFC(Efficient Funding of Chemotherapy) — IV 항암제 전용
# 현재 8개 품목 중 해당 없음. Section 100 확장 대비 보관.
EFC_PREP_FEE = 91.23
EFC_DIST_FEE = 30.71
EFC_DILUENT_FEE = 6.08


# ---- DPMQ → AEMP 역산 5-tier ---------------------------------------------
_REVERSE_THRESHOLDS = (
    (19.70, lambda d: d - 14.20),
    (113.79, lambda d: (d - 13.79) / 1.0752),
    (821.64, lambda d: (d - 8.79) / 1.12896),
    (2108.79, lambda d: (d - 65.64) / 1.05),
)
_REVERSE_FALLBACK = lambda d: d - 162.93  # DPMQ > 2108.79


def calculate_aemp_from_dpmq(dpmq: float | int | None) -> float | None:
    """DPMQ(총약가)에서 AEMP(제조사 출고가)를 역산한다.

    크롤러가 PBS API에서 `determined_price`(= AEMP 공식값)를 가져올 수 없을 때만
    쓰는 **폴백**. 가능하면 크롤러 원본 AEMP를 우선 사용할 것.
    검증: Hydrine DPMQ $48.11 → AEMP $31.92 (오차 < 0.01)
    """
    if not isinstance(dpmq, (int, float)):
        return None
    d = float(dpmq)
    if d <= 0:
        return None
    for threshold, formula in _REVERSE_THRESHOLDS:
        if d <= threshold:
            return round(formula(d), 2)
    return round(_REVERSE_FALLBACK(d), 2)


# ---- 프리셋 / 기본값 -------------------------------------------------------
# 호주 수입 스폰서(수입상) 마진 %.  Logic A: FOB = (공시 AEMP×(1+α)) ÷ (1 + 수입상마진%) 이므로
# 수입상 마진이 클수록 수출 FOB는 낮아짐(저가 진입·침투 가격).
# aggressive=저가 진입(수입상 마진↑) → FOB 최저 / conservative=프리미엄(수입상 마진↓) → FOB 최고
DEFAULT_PRESETS_PCT = {"aggressive": 30, "average": 20, "conservative": 10}
# Research doc 권고: importer margin 5~40% 슬라이더, 30/20/10을 3 시나리오로 고정 노출

DEFAULT_FX_AUD_TO_KRW = 900.0  # 참고 환율, UI에서 override 가능

# α = 공시 AEMP 대비 실거래가 시장 보정 (보고서 v5: 기준 AEMP = 공시 AEMP × (1+α))
# 근거: 이혜재·유수연(2020), Voehler et al.(2023) ERP 연구 등 — Logic A(PBS 경로)에만 적용, Logic B(소매 실거래)에는 미적용
ALPHA_MARKET_UPLIFT_PCT = 20.0

# Logic B (Private) 기본 역산 계수
DEFAULT_GST_PCT = 10.0          # 호주 GST 10%
DEFAULT_PHARMACY_MARGIN_PCT = 30.0   # private 약국 마진 (Research doc 권고)
DEFAULT_WHOLESALE_MARGIN_B_PCT = 10.0  # private 유통 도매 마진


__all__ = [
    # 상수
    "DISPENSING_FEE_READY",
    "WHOLESALE_TIER1_FLAT", "WHOLESALE_TIER2_PCT", "WHOLESALE_TIER3_FLAT",
    "AHI_TIER1_FLAT", "AHI_TIER2_PCT", "AHI_TIER3_FLAT",
    "EFC_PREP_FEE", "EFC_DIST_FEE", "EFC_DILUENT_FEE",
    "DEFAULT_PRESETS_PCT", "DEFAULT_FX_AUD_TO_KRW", "ALPHA_MARKET_UPLIFT_PCT",
    "DEFAULT_GST_PCT", "DEFAULT_PHARMACY_MARGIN_PCT", "DEFAULT_WHOLESALE_MARGIN_B_PCT",
    # 함수
    "calculate_aemp_from_dpmq",
    "calculate_fob_logic_a",
    "calculate_fob_logic_b",
    "calculate_three_scenarios",
    "dispatch_by_pricing_case",
    "get_disclaimer_text",
]


# ---- Logic A: PBS 공공 경로 역산 ------------------------------------------
def calculate_fob_logic_a(
    aemp_aud: float,
    importer_margin_pct: float,
    fx_aud_to_krw: float = DEFAULT_FX_AUD_TO_KRW,
) -> dict[str, float]:
    """PBS 공시 AEMP에 α 시장 보정 후 수입상 마진만 제거하여 FOB 도출.

    기준 AEMP = 공시 AEMP × (1 + α/100),  FOB_AUD = 기준 AEMP ÷ (1 + importer_margin/100)
    α는 ALPHA_MARKET_UPLIFT_PCT(기본 20%). Logic B(소매 실거래)에는 적용하지 않음.
    """
    if not isinstance(aemp_aud, (int, float)) or aemp_aud <= 0:
        raise ValueError(f"aemp_aud must be positive, got {aemp_aud}")
    if not isinstance(importer_margin_pct, (int, float)) or importer_margin_pct < 0:
        raise ValueError(f"importer_margin_pct must be >= 0, got {importer_margin_pct}")

    listed_aemp = float(aemp_aud)
    adjusted_aemp = listed_aemp * (1.0 + ALPHA_MARKET_UPLIFT_PCT / 100.0)
    fob_aud = adjusted_aemp / (1.0 + importer_margin_pct / 100.0)
    fob_krw = fob_aud * fx_aud_to_krw
    return {
        "aemp_aud": round(listed_aemp, 4),
        "adjusted_aemp_aud": round(adjusted_aemp, 4),
        "alpha_market_uplift_pct": float(ALPHA_MARKET_UPLIFT_PCT),
        "importer_margin_pct": float(importer_margin_pct),
        "fob_aud": round(fob_aud, 4),
        "fob_krw": round(fob_krw, 2),
        "fx_aud_to_krw": float(fx_aud_to_krw),
    }


# ---- Logic B: Private 소매가 경로 역산 ------------------------------------
def calculate_fob_logic_b(
    retail_aud: float,
    importer_margin_pct: float,
    gst_pct: float = DEFAULT_GST_PCT,
    pharmacy_margin_pct: float = DEFAULT_PHARMACY_MARGIN_PCT,
    wholesale_margin_pct: float = DEFAULT_WHOLESALE_MARGIN_B_PCT,
    fx_aud_to_krw: float = DEFAULT_FX_AUD_TO_KRW,
    *,
    is_pbs_listed_rx: bool = False,
) -> dict[str, float]:
    """Private 소매가에서 역순 마진 제거.

    단계:  Retail → (÷1+GST) → (÷1+pharmacy) → (÷1+wholesale) → (÷1+importer) = FOB
    PBS 등재 처방의약품(is_pbs_listed_rx=True)은 GST 면제 → gst_pct 를 0으로 강제.
    """
    if not isinstance(retail_aud, (int, float)) or retail_aud <= 0:
        raise ValueError(f"retail_aud must be positive, got {retail_aud}")

    eff_gst = 0.0 if is_pbs_listed_rx else float(gst_pct)

    pre_gst = retail_aud / (1.0 + eff_gst / 100.0)
    pre_pharmacy = pre_gst / (1.0 + pharmacy_margin_pct / 100.0)
    pre_wholesale = pre_pharmacy / (1.0 + wholesale_margin_pct / 100.0)
    fob_aud = pre_wholesale / (1.0 + importer_margin_pct / 100.0)
    fob_krw = fob_aud * fx_aud_to_krw

    return {
        "retail_aud": round(float(retail_aud), 4),
        "pre_gst_aud": round(pre_gst, 4),
        "pre_pharmacy_aud": round(pre_pharmacy, 4),
        "pre_wholesale_aud": round(pre_wholesale, 4),
        "gst_pct": float(eff_gst),
        "is_pbs_listed_rx": bool(is_pbs_listed_rx),
        "pharmacy_margin_pct": float(pharmacy_margin_pct),
        "wholesale_margin_pct": float(wholesale_margin_pct),
        "importer_margin_pct": float(importer_margin_pct),
        "fob_aud": round(fob_aud, 4),
        "fob_krw": round(fob_krw, 2),
        "fx_aud_to_krw": float(fx_aud_to_krw),
    }


# ---- 3 시나리오 일괄 계산 --------------------------------------------------
def calculate_three_scenarios(
    *,
    logic: str,
    aemp_aud: float | None = None,
    retail_aud: float | None = None,
    fx_aud_to_krw: float = DEFAULT_FX_AUD_TO_KRW,
    presets_pct: dict[str, float] | None = None,
    logic_b_kwargs: dict[str, Any] | None = None,
) -> dict[str, dict[str, float]]:
    """30/20/10% (기본) 프리셋 기준 3 시나리오를 한 번에 계산."""
    presets = presets_pct or DEFAULT_PRESETS_PCT
    b_kwargs = logic_b_kwargs or {}
    out: dict[str, dict[str, float]] = {}

    for key, pct in presets.items():
        if logic == "A":
            if aemp_aud is None:
                raise ValueError("Logic A requires aemp_aud")
            out[key] = calculate_fob_logic_a(aemp_aud, pct, fx_aud_to_krw)
        elif logic == "B":
            if retail_aud is None:
                raise ValueError("Logic B requires retail_aud")
            out[key] = calculate_fob_logic_b(
                retail_aud,
                importer_margin_pct=pct,
                fx_aud_to_krw=fx_aud_to_krw,
                **b_kwargs,
            )
        else:
            raise ValueError(f"Unknown logic: {logic}")
    return out


# ---- COMPONENT_SUM 복합제 성분 합산 (서브케이스 3분기) ------------------------
def _retail_to_supply_equiv_aud(
    retail_aud: float,
    *,
    gst_pct: float,
    pharmacy_margin_pct: float = DEFAULT_PHARMACY_MARGIN_PCT,
    wholesale_margin_pct: float = DEFAULT_WHOLESALE_MARGIN_B_PCT,
) -> float:
    """소매가에서 GST·약국·도매만 제거한 금액 (수입상 제외). Logic B의 pre_wholesale 와 동일 단계.

    미등재 성분 유사제품 소매 → AEMP 상당 추정에 사용 (위임식:
    retail / (1+GST) / (1+pharmacy) / (1+wholesale)).
    """
    if retail_aud <= 0:
        raise ValueError("retail_aud must be positive")
    pre_gst = retail_aud / (1.0 + float(gst_pct) / 100.0)
    pre_ph = pre_gst / (1.0 + pharmacy_margin_pct / 100.0)
    pre_wh = pre_ph / (1.0 + wholesale_margin_pct / 100.0)
    return pre_wh


def _similar_ref_effective_retail_aud(ref: dict[str, Any]) -> tuple[float, str]:
    """유사제품 pack 소매가 → 복합제 1정(또는 1참조단위)당 유효 소매가.

    환산식: effective = retail_aud / units_per_pack * fdc_units_per_reference_unit
    (예: 28캡슐 팩 48.95 AUD, 1정=1캡 기준이면 48.95/28).
    """
    retail = float(ref.get("retail_aud") or 0)
    if retail <= 0:
        raise ValueError("similar_product_retail_ref.retail_aud 가 필요합니다.")
    units = ref.get("units_per_pack")
    fdc = float(ref.get("fdc_units_per_reference_unit", 1.0))
    if isinstance(units, (int, float)) and float(units) > 0:
        eff = retail / float(units) * fdc
        note = f"pack {float(units):.0f}단위÷{float(units):.0f}×fdc {fdc:g} → 1정당 {eff:.4f} AUD"
        return eff, note
    return retail, "units_per_pack 없음 — 소매가 전량을 1:1 단위로 사용"


def _listed_component_aemp_aud(
    comp: dict[str, Any],
    idx: int,
    crawler_row: dict[str, Any] | None,
) -> float:
    """등재 성분 AEMP: 시드 reference_aemp_aud_fallback 우선, 없으면 첫 성분만 crawler_row 단일 aemp_aud."""
    fb = comp.get("reference_aemp_aud_fallback")
    if isinstance(fb, (int, float)) and float(fb) > 0:
        return float(fb)
    cr = crawler_row or {}
    cr_aemp = cr.get("pbs_aemp_aud") or cr.get("aemp_aud")
    if idx == 0 and isinstance(cr_aemp, (int, float)) and float(cr_aemp) > 0:
        return float(cr_aemp)
    ing = comp.get("ingredient") or "?"
    raise ValueError(
        f"성분 {ing!r}: PBS AEMP 없음 (reference_aemp_aud_fallback 또는 crawler aemp_aud)"
    )


def _resolve_component_sum_components(
    components: list[dict[str, Any]],
    crawler_row: dict[str, Any] | None,
    warnings: list[str],
) -> tuple[float, dict[str, Any]]:
    """서브케이스 1~3에 따라 기준 AEMP(합산)를 산출. 반환: (total_aemp_aud, inputs 메타)."""
    if len(components) < 2:
        raise ValueError("components 는 최소 2성분 필요")

    listed_flags = [bool(c.get("pbs_listed")) for c in components]
    n_listed = sum(1 for f in listed_flags if f)
    n = len(components)
    breakdown: list[dict[str, Any]] = []

    # --- 서브케이스 1: 양쪽 모두 PBS 등재 ---
    if n_listed == n:
        total = 0.0
        for i, comp in enumerate(components):
            v = _listed_component_aemp_aud(comp, i, crawler_row)
            total += v
            breakdown.append(
                {"ingredient": comp.get("ingredient"), "aemp_aud": round(v, 4), "role": "pbs_listed"}
            )
        warnings.append("복합제 합산 방식 = 성분별 단일제 PBS AEMP 합산")
        meta: dict[str, Any] = {
            "component_sum_subcase": 1,
            "component_sum_breakdown": breakdown,
            "component_sum_method": "sum_listed_pbs_aemp",
        }
        return total, meta

    # --- 서브케이스 3: 양쪽 모두 미등재 ---
    if n_listed == 0:
        total = 0.0
        for comp in components:
            ref = comp.get("similar_product_retail_ref")
            if not isinstance(ref, dict):
                ing = comp.get("ingredient") or "?"
                raise ValueError(f"성분 {ing!r}: similar_product_retail_ref 필요 (서브케이스 3)")
            eff_r, scale_note = _similar_ref_effective_retail_aud(ref)
            gst_o = ref.get("gst_pct_override")
            gst_use = float(gst_o) if isinstance(gst_o, (int, float)) else DEFAULT_GST_PCT
            eq = _retail_to_supply_equiv_aud(eff_r, gst_pct=gst_use)
            total += eq
            breakdown.append({
                "ingredient": comp.get("ingredient"),
                "supply_equiv_aud": round(eq, 4),
                "gst_pct_used": gst_use,
                "retail_scale_note": scale_note,
                "role": "unlisted_retail_reverse",
            })
        warnings.append("복합제 합산 방식 = 양 성분 모두 유사제품 소매가 역산 추정 합산")
        warnings.append("confidence: 서브케이스 3 — 양 성분 역산 추정, 0.3~0.4 대역 권고")
        return total, {
            "component_sum_subcase": 3,
            "component_sum_breakdown": breakdown,
            "component_sum_method": "dual_unlisted_retail_reverse",
        }

    # --- 서브케이스 2: 등재 + 미등재 혼합 (Rosumeg·Atmeg) ---
    total = 0.0
    sim_names: list[str] = []
    for i, comp in enumerate(components):
        if comp.get("pbs_listed"):
            v = _listed_component_aemp_aud(comp, i, crawler_row)
            total += v
            breakdown.append(
                {"ingredient": comp.get("ingredient"), "aemp_aud": round(v, 4), "role": "pbs_listed"}
            )
        else:
            ref = comp.get("similar_product_retail_ref")
            if not isinstance(ref, dict):
                ing = comp.get("ingredient") or "?"
                raise ValueError(f"성분 {ing!r}: 미등재인 경우 similar_product_retail_ref 필요")
            eff_r, scale_note = _similar_ref_effective_retail_aud(ref)
            gst_o = ref.get("gst_pct_override")
            gst_use = float(gst_o) if isinstance(gst_o, (int, float)) else DEFAULT_GST_PCT
            eq = _retail_to_supply_equiv_aud(eff_r, gst_pct=gst_use)
            total += eq
            nm = str(ref.get("name") or "유사제품")
            sim_names.append(nm)
            breakdown.append({
                "ingredient": comp.get("ingredient"),
                "supply_equiv_aud": round(eq, 4),
                "gst_pct_used": gst_use,
                "similar_product": nm,
                "retail_scale_note": scale_note,
                "role": "unlisted_retail_reverse",
            })
    warnings.append(
        "복합제 합산 방식 = 등재성분 AEMP + 미등재성분 소매가 역산 추정 합산"
        + (f" (유사제품: {', '.join(sim_names)})" if sim_names else "")
    )
    warnings.append("confidence: 서브케이스 2 — 등재+미등재 혼합, 0.5~0.6 대역 권고")
    return total, {
        "component_sum_subcase": 2,
        "component_sum_breakdown": breakdown,
        "component_sum_method": "listed_plus_unlisted_retail_reverse",
    }


def _component_sum_legacy_max_or_sum(
    seed: dict[str, Any],
    crawler_row: dict[str, Any] | None,
    warnings: list[str],
) -> tuple[float, dict[str, Any]]:
    """구 스키마(reference_aemp_aud 배열 등) 폴백 — 합산 또는 DB 단일값."""
    cr = crawler_row or {}
    cr_aemp = cr.get("pbs_aemp_aud") or cr.get("aemp_aud")
    db_aemp_ok = isinstance(cr_aemp, (int, float)) and float(cr_aemp) > 0
    aemp_ref = seed.get("reference_aemp_aud")

    if db_aemp_ok:
        aemp = float(cr_aemp)
        warnings.append(
            "COMPONENT_SUM(레거시): DB 단일 aemp_aud 사용 — components[] 스키마로 이관 권장."
        )
        return aemp, {"component_sum_method": "legacy_db_single", "component_sum_subcase": None}

    if isinstance(aemp_ref, list):
        nums = [float(v) for v in aemp_ref if isinstance(v, (int, float))]
        if not nums:
            raise ValueError("reference_aemp_aud list empty")
        aemp = sum(nums)
        warnings.append(
            "COMPONENT_SUM(레거시): reference_aemp_aud 배열 합산 — "
            "components[] + 서브케이스 분기로 이관 권장 (과거 max 상한 사용 아님)."
        )
        if crawler_row is not None and not db_aemp_ok:
            warnings.append("crawler_price_fallback_to_seed")
        return aemp, {"component_sum_method": "legacy_seed_list_sum", "component_sum_subcase": None}

    if isinstance(aemp_ref, (int, float)):
        aemp = float(aemp_ref)
        if crawler_row is not None and not db_aemp_ok:
            warnings.append("crawler_price_fallback_to_seed")
        return aemp, {"component_sum_method": "legacy_seed_scalar", "component_sum_subcase": None}

    raise ValueError("COMPONENT_SUM: components[] 또는 reference_aemp_aud 가 필요합니다.")


# ---- 안내 문구 (Gross vs Net 디스클레이머) ---------------------------------
def get_disclaimer_text(logic: str) -> str:
    base = (
        "본 FOB는 공시가 기준의 Gross 추정치입니다. "
        "호주 PBS는 PBAC 협상 과정에서 기밀 rebate(주로 단일성분 약가협약, 일부 복합제)로 "
        "실제 Net 가격이 공시 AEMP보다 낮을 수 있으니 최종 계약가는 직접 협상 필요."
    )
    if logic == "B":
        return base + " Logic B(Private)는 소매가에서 역산한 값으로 유통사·체인별 편차 ±15% 가능."
    if logic == "hardcoded":
        return (
            "병원 tender 수기 FOB 확정값 (ESTIMATE_hospital). "
            "TradeMap 역산 + AU 프리미엄 + 물류 반영한 오리지널/제네릭 시나리오 직접 입력치. "
            "실제 계약가는 HealthShare NSW 등 state tender 입찰 과정에서 ±20% 변동 가능."
        )
    if logic == "blocked":
        return "해당 품목은 규제 사유로 표준 FOB 역산이 불가합니다. 별도 진입 시나리오 필요."
    return base


# ---- 메인 라우터 -----------------------------------------------------------
def dispatch_by_pricing_case(
    seed: dict[str, Any],
    *,
    fx_aud_to_krw: float = DEFAULT_FX_AUD_TO_KRW,
    presets_pct: dict[str, float] | None = None,
    logic_b_kwargs: dict[str, Any] | None = None,
    crawler_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`fob_reference_seeds.json`의 단일 seed dict를 받아 3 시나리오 FOB 반환.

    Args:
        seed: fob_reference_seeds.json 단일 엔트리 (수기 조사 + 규제 플래그)
        fx_aud_to_krw: AUD → KRW 환율
        presets_pct: 수입 스폰서 마진 프리셋 (기본 aggressive=30/average=20/conservative=10)
        logic_b_kwargs: Logic B 마진 파라미터 (GST, pharmacy, wholesale) 덮어쓰기
        crawler_row: 크롤러 실시간 row (선택). Logic B 에서 seed.reference_retail_aud 가
                     없을 때 crawler_row.retail_price_aud 를 2순위 참고가로 사용.
                     retail_estimation_method 로 출처(PBS DPMQ vs Chemist × 1.20) 구분.

    반환 스키마는 모듈 docstring 참고.
    """
    case = seed.get("pricing_case", "")
    pid = seed.get("product_id", "<unknown>")
    warnings: list[str] = []

    # --- 차단 케이스 ---
    if case == "ESTIMATE_withdrawal":
        return {
            "logic": "blocked",
            "scenarios": {},
            "inputs": {"product_id": pid, "pricing_case": case},
            "warnings": [
                f"Commercial Withdrawal 이력(연도: {seed.get('commercial_withdrawal_year')}) — "
                "데이터에 기록된 사실이며, 재진입·재등재 조건은 건별로 상이함(TGA·PBAC 개별 검토 대상).",
                (
                    "시드 플래그: PBAC 심의에서 비교임상·우월성 논의가 나올 수 있는 품목군으로 표시됨 "
                    "(실제 요구 여부는 개별 심의 대상)."
                    if seed.get("pbac_superiority_required")
                    else ""
                ),
            ],
            "disclaimer": get_disclaimer_text("blocked"),
            "blocked_reason": "commercial_withdrawal",
        }

    # --- 플래그 수집 (공통) ---
    if seed.get("pbac_superiority_required"):
        warnings.append(
            "시드 플래그: PBS 신규 등재 시 PBAC에서 단일성분 대비 비교임상·우월성 자료가 "
            "논의될 수 있는 품목군(실제 요구 범위·일정은 개별 심의 대상)."
        )
    if seed.get("hospital_channel_only"):
        warnings.append(
            "약국 유통 없음, Hospital tender/HealthShare NSW 등 병원조달 루트 전용. "
            "FOB는 tender 가격 밴드 추정치로 ±20% 변동성 존재."
        )
    if seed.get("section_19a_flag"):
        warnings.append(
            "호주 미등재 성분 → Section 19A 일시수입 경로만 가능. 정식 등재 전까지는 비정기 공급."
        )
    if seed.get("restricted_benefit"):
        warnings.append(
            "PBS Restricted Benefit/Authority — 처방 적응증 제한. 적용 환자군·시장 규모는 "
            "품목 고지문·Schedule 기준으로 별도 확인 대상."
        )
    confidence = seed.get("confidence_score")
    if isinstance(confidence, (int, float)) and confidence < 0.7:
        warnings.append(
            f"함량·제형 불일치 또는 데이터 공백으로 confidence score {confidence:.2f}. "
            "FOB 결과는 예비 참고치로만 활용."
        )

    # --- DIRECT: PBS AEMP 직접 ---
    if case == "DIRECT":
        aemp_ref = seed.get("reference_aemp_aud")
        cr = crawler_row or {}
        cr_aemp = cr.get("pbs_aemp_aud") or cr.get("aemp_aud")
        db_aemp_ok = isinstance(cr_aemp, (int, float)) and float(cr_aemp) > 0

        if isinstance(aemp_ref, list):
            # 복수 함량 시드: PBS 항목별 AEMP — 단일 DB 컬럼으로 대체하지 않음
            nums = [float(v) for v in aemp_ref if isinstance(v, (int, float))]
            if not nums:
                return _blocked_no_price(pid, case, "reference_aemp_aud array empty")
            aemp = sum(nums) / len(nums)
            aemp_source = "seed"
            warnings.append(
                f"복수 함량의 평균 AEMP(${aemp:.2f}) 사용. 개별 함량: "
                + ", ".join(f"${v:.2f}" for v in nums)
            )
        elif db_aemp_ok:
            aemp = float(cr_aemp)
            aemp_source = "crawler"
            warnings.append(
                "참고 AEMP: Supabase `au_products.aemp_aud`(또는 pbs_aemp_aud, 크롤러·PBS 동기화) 우선 사용. "
                "시드 `reference_aemp_aud`는 감사·백업용."
            )
        elif isinstance(aemp_ref, (int, float)):
            aemp = float(aemp_ref)
            aemp_source = "seed"
            if crawler_row is not None and not db_aemp_ok:
                warnings.append("crawler_price_fallback_to_seed")
        else:
            return _blocked_no_price(pid, case, "reference_aemp_aud missing 및 DB aemp_aud 없음")

        scenarios = calculate_three_scenarios(
            logic="A", aemp_aud=aemp, fx_aud_to_krw=fx_aud_to_krw, presets_pct=presets_pct
        )
        return {
            "logic": "A",
            "scenarios": scenarios,
            "inputs": {
                "product_id": pid,
                "pricing_case": case,
                "aemp_aud": aemp,
                "aemp_source": aemp_source,
                "alpha_market_uplift_pct": ALPHA_MARKET_UPLIFT_PCT,
                "fx_aud_to_krw": fx_aud_to_krw,
            },
            "warnings": warnings,
            "disclaimer": get_disclaimer_text("A"),
            "blocked_reason": None,
        }

    # --- COMPONENT_SUM: 복합제 성분별 합산 (서브케이스 1~3 + 레거시 폴백) ---
    if case == "COMPONENT_SUM":
        components = seed.get("components")
        try:
            if isinstance(components, list) and len(components) >= 2:
                total_aemp, sum_meta = _resolve_component_sum_components(
                    components, crawler_row, warnings
                )
            else:
                total_aemp, sum_meta = _component_sum_legacy_max_or_sum(
                    seed, crawler_row, warnings
                )
        except ValueError as exc:
            return _blocked_no_price(pid, case, str(exc))

        scenarios = calculate_three_scenarios(
            logic="A",
            aemp_aud=total_aemp,
            fx_aud_to_krw=fx_aud_to_krw,
            presets_pct=presets_pct,
        )
        return {
            "logic": "A",
            "scenarios": scenarios,
            "inputs": {
                "product_id": pid,
                "pricing_case": case,
                "aemp_aud": total_aemp,
                "alpha_market_uplift_pct": ALPHA_MARKET_UPLIFT_PCT,
                "fx_aud_to_krw": fx_aud_to_krw,
                **sum_meta,
            },
            "warnings": warnings,
            "disclaimer": get_disclaimer_text("A"),
            "blocked_reason": None,
        }

    # --- ESTIMATE_substitute: 대체계열 AEMP 참고 ---
    if case == "ESTIMATE_substitute":
        cr = crawler_row or {}
        cr_aemp = cr.get("pbs_aemp_aud") or cr.get("aemp_aud")
        seed_aemp = seed.get("reference_aemp_aud")
        sub_name = seed.get("substitute_ingredient") or "대체계열"
        if seed.get("commercial_withdrawal_flag"):
            warnings.append(
                f"Commercial Withdrawal 이력(연도: {seed.get('commercial_withdrawal_year')}) — "
                "데이터 기록 사실이며, 아래 FOB는 대체계열 참고가 기준(표준 PBS 동일품목 역산 아님)."
            )
        if isinstance(cr_aemp, (int, float)) and float(cr_aemp) > 0:
            aemp_use = float(cr_aemp)
            aemp_source = "crawler"
            warnings.append(
                f"동일성분 부재 → {sub_name}: Supabase `aemp_aud`(또는 pbs_aemp_aud) AUD {aemp_use:.2f} 우선(크롤 동기화). "
                "실제 등재 가능성 및 가격은 PBAC 개별 심의 대상."
            )
        elif isinstance(seed_aemp, (int, float)):
            aemp_use = float(seed_aemp)
            aemp_source = "seed"
            warnings.append(
                f"동일성분 부재 → {sub_name} AEMP(${aemp_use:.2f})를 참고가로 차용(시드). "
                "실제 등재 가능성 및 가격은 PBAC 개별 심의 대상."
            )
            if crawler_row is not None:
                warnings.append("crawler_price_fallback_to_seed")
        else:
            return _blocked_no_price(pid, case, "substitute reference_aemp_aud missing 및 DB aemp_aud 없음")

        scenarios = calculate_three_scenarios(
            logic="A", aemp_aud=aemp_use, fx_aud_to_krw=fx_aud_to_krw, presets_pct=presets_pct
        )
        return {
            "logic": "A",
            "scenarios": scenarios,
            "inputs": {
                "product_id": pid,
                "pricing_case": case,
                "aemp_aud": aemp_use,
                "aemp_source": aemp_source,
                "substitute_ingredient": sub_name,
                "substitute_drug": seed.get("substitute_drug"),
                "alpha_market_uplift_pct": ALPHA_MARKET_UPLIFT_PCT,
                "fx_aud_to_krw": fx_aud_to_krw,
            },
            "warnings": warnings,
            "disclaimer": get_disclaimer_text("A"),
            "blocked_reason": None,
        }

    # --- ESTIMATE_hospital: seed.fob_hardcoded_aud 우선 (병원 tender 수기 확정가) ---
    # 위임지서 Phase 3 — Gadvoa 는 TradeMap NZ 역산 확정값($16.49/병) 하드코딩.
    # 수기 fob_hardcoded_aud 가 있으면 Logic B 역산을 건너뛰고 직접 FOB 시나리오 반환.
    if case == "ESTIMATE_hospital":
        hardcoded = seed.get("fob_hardcoded_aud")
        if isinstance(hardcoded, dict):
            scenarios_out: dict[str, dict[str, float]] = {}
            presets = presets_pct or DEFAULT_PRESETS_PCT
            for key, pct in presets.items():
                fob_v = hardcoded.get(key)
                if not isinstance(fob_v, (int, float)) or float(fob_v) <= 0:
                    continue
                fob_aud = float(fob_v)
                scenarios_out[key] = {
                    "fob_aud": round(fob_aud, 4),
                    "fob_krw": round(fob_aud * fx_aud_to_krw, 2),
                    "importer_margin_pct": float(pct),
                    "fx_aud_to_krw": float(fx_aud_to_krw),
                    "source": "hardcoded_hospital_tender",
                }
            if scenarios_out:
                bayer_ref = hardcoded.get("bayer_reference_aud")
                bayer_src = hardcoded.get("bayer_reference_source")
                if bayer_ref:
                    warnings.append(
                        f"Bayer 오리지널 호주 FOB(원가) 참조값 ${float(bayer_ref):.2f}/병 — "
                        "제네릭 시나리오는 Penetration(저가진입) 40% off / "
                        "Reference(기준가) 25% off / Premium(프리미엄) 15% off 기준."
                    )
                if bayer_src:
                    warnings.append(f"참조값 출처: {bayer_src}")
                return {
                    "logic": "hardcoded",
                    "scenarios": scenarios_out,
                    "inputs": {
                        "product_id": pid,
                        "pricing_case": case,
                        "bayer_reference_aud": bayer_ref,
                        "fx_aud_to_krw": fx_aud_to_krw,
                    },
                    "warnings": warnings,
                    "disclaimer": get_disclaimer_text("hardcoded"),
                    "blocked_reason": None,
                }

    # --- ESTIMATE_private / ESTIMATE_hospital: Logic B ---
    if case in ("ESTIMATE_private", "ESTIMATE_hospital"):
        # 1순위: crawler_row.retail_price_aud (크롤·Healthylife 등 DB 동기화 소매가)
        # 2순위: seed.reference_retail_aud (수기 백업)
        # 3순위: blocked
        retail: float | None = None
        retail_source: str | None = None

        seed_retail = seed.get("reference_retail_aud")
        cr_retail = (crawler_row or {}).get("retail_price_aud")
        cr_ok = isinstance(cr_retail, (int, float)) and float(cr_retail) > 0
        seed_ok = isinstance(seed_retail, (int, float)) and float(seed_retail) > 0

        if cr_ok:
            retail = float(cr_retail)
            retail_source = "crawler"
            cr_method = (crawler_row or {}).get("retail_estimation_method")
            method_label = {
                "pbs_dpmq": "PBS DPMQ(최대처방량 총약가)",
                "aemp_fallback": "PBS AEMP(제조사 출고가) — dispensing rule API 미수집, DPMQ보다 낮음·과소추정 주의",
                "chemist_markup": "Chemist Warehouse × 1.20 (CHOICE 조사 기준 시장 평균)",
                "healthylife_actual": "Healthylife 민간 소매(실측)",
                "healthylife_same_ingredient_diff_form": "Healthylife(동성분·제형 참고)",
            }.get(cr_method, cr_method or "크롤러 실시간값")
            warnings.append(
                f"소매 참고가: 크롤러·DB 동기화 AUD {retail:.2f} ({method_label})."
            )
            if seed_ok:
                warnings.append(
                    f"(시드 소매가 AUD {float(seed_retail):.2f}는 백업·감사용, 역산에는 DB 값 사용.)"
                )
        elif seed_ok:
            retail = float(seed_retail)
            retail_source = "seed"
            src = seed.get("reference_retail_source")
            if src:
                warnings.append(f"소매 참고가 출처(수기 시드, DB 소매가 없을 때): {src}")

        if retail is None:
            return _blocked_no_price(
                pid, case,
                "소매 참고가 없음 (DB retail_price_aud 및 시드 reference_retail_aud 모두 없음)",
            )

        merged_b: dict[str, Any] = dict(logic_b_kwargs or {})
        cr = crawler_row or {}
        if "is_pbs_listed_rx" not in merged_b:
            pbs_ok = cr.get("pbs_found")
            if pbs_ok is None:
                pbs_ok = cr.get("pbs_listed")
            if pbs_ok is True:
                merged_b["is_pbs_listed_rx"] = True

        scenarios = calculate_three_scenarios(
            logic="B",
            retail_aud=retail,
            fx_aud_to_krw=fx_aud_to_krw,
            presets_pct=presets_pct,
            logic_b_kwargs=merged_b,
        )
        return {
            "logic": "B",
            "scenarios": scenarios,
            "inputs": {
                "product_id": pid,
                "pricing_case": case,
                "retail_aud": retail,
                "retail_source": retail_source,
                "is_pbs_listed_rx": bool(merged_b.get("is_pbs_listed_rx")),
                "fx_aud_to_krw": fx_aud_to_krw,
            },
            "warnings": warnings,
            "disclaimer": get_disclaimer_text("B"),
            "blocked_reason": None,
        }

    # --- 미분류 ---
    return _blocked_no_price(pid, case, f"unknown pricing_case: {case}")


def _blocked_no_price(pid: str, case: str, reason: str) -> dict[str, Any]:
    return {
        "logic": "blocked",
        "scenarios": {},
        "inputs": {"product_id": pid, "pricing_case": case},
        "warnings": [f"참고가 데이터 미확보: {reason}"],
        "disclaimer": get_disclaimer_text("blocked"),
        "blocked_reason": "no_reference_price",
    }


# ---- 개발자용 진입점 -------------------------------------------------------
if __name__ == "__main__":
    import json
    from pathlib import Path

    seeds_path = Path(__file__).resolve().parent / "fob_reference_seeds.json"
    with open(seeds_path, encoding="utf-8") as f:
        data = json.load(f)

    print("=" * 70)
    print("UPharma 호주 수출 FOB 역산 — 전 품목 3 시나리오")
    print("=" * 70)
    for seed in data["seeds"]:
        res = dispatch_by_pricing_case(seed)
        print(
            f"\n[{seed['product_id']}] "
            f"({seed['pricing_case']}, logic={res['logic']})"
        )
        if res["blocked_reason"]:
            print(f"  BLOCKED: {res['blocked_reason']}")
        for name, sc in res["scenarios"].items():
            print(
                f"  {name:13s} margin={sc['importer_margin_pct']:>4.0f}%  "
                f"FOB = ${sc['fob_aud']:.2f} / ₩{sc['fob_krw']:,.0f}"
            )
        for w in res["warnings"]:
            if w:
                print(f"  ⚠  {w}")
