"""호주 수출 FOB 역산 계산 모듈 (수출 전략).

**설계 원칙**
- 크롤러와 완전히 분리: `crawler/` 아래 어떤 파일에도 의존하지 않음.
- UI와도 분리: 입력 dict → 출력 dict의 순수 계산 모듈.
- 수수료 상수·역산 공식은 전부 이 파일 안에서 완결.
- 판정 분기는 `fob_reference_seeds.json`의 `pricing_case`로 라우팅.

**pricing_case 라우팅**
- DIRECT                  → Logic A, PBS AEMP 직접 사용
- COMPONENT_SUM           → Logic A, 성분별 AEMP 합산 (PBAC 임상우월성 경고 부착)
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
# 호주 수입 스폰서(수입상) 마진 %.  FOB = AEMP ÷ (1 + 수입상마진%) 이므로
# 수입상 마진이 클수록 수출 FOB는 낮아짐(저가 진입·침투 가격).
# aggressive=저가 진입(수입상 마진↑) → FOB 최저 / conservative=프리미엄(수입상 마진↓) → FOB 최고
DEFAULT_PRESETS_PCT = {"aggressive": 30, "average": 20, "conservative": 10}
# Research doc 권고: importer margin 5~40% 슬라이더, 30/20/10을 3 시나리오로 고정 노출

DEFAULT_FX_AUD_TO_KRW = 900.0  # 참고 환율, UI에서 override 가능

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
    "DEFAULT_PRESETS_PCT", "DEFAULT_FX_AUD_TO_KRW",
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
    """PBS AEMP에서 importer margin만 제거하여 FOB 도출.

    수식: FOB_AUD = AEMP / (1 + importer_margin/100)

    AEMP는 이미 "제조사 출고가"라서 약국단 수수료가 제거된 값.
    importer_margin은 제조사(한국) ↔ 호주 수입상 사이 중간 마진만 남음.
    """
    if not isinstance(aemp_aud, (int, float)) or aemp_aud <= 0:
        raise ValueError(f"aemp_aud must be positive, got {aemp_aud}")
    if not isinstance(importer_margin_pct, (int, float)) or importer_margin_pct < 0:
        raise ValueError(f"importer_margin_pct must be >= 0, got {importer_margin_pct}")

    fob_aud = aemp_aud / (1.0 + importer_margin_pct / 100.0)
    fob_krw = fob_aud * fx_aud_to_krw
    return {
        "aemp_aud": round(float(aemp_aud), 4),
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
) -> dict[str, float]:
    """Private 소매가에서 역순 마진 제거.

    단계:  Retail → (÷1+GST) → (÷1+pharmacy) → (÷1+wholesale) → (÷1+importer) = FOB
    """
    if not isinstance(retail_aud, (int, float)) or retail_aud <= 0:
        raise ValueError(f"retail_aud must be positive, got {retail_aud}")

    pre_gst = retail_aud / (1.0 + gst_pct / 100.0)
    pre_pharmacy = pre_gst / (1.0 + pharmacy_margin_pct / 100.0)
    pre_wholesale = pre_pharmacy / (1.0 + wholesale_margin_pct / 100.0)
    fob_aud = pre_wholesale / (1.0 + importer_margin_pct / 100.0)
    fob_krw = fob_aud * fx_aud_to_krw

    return {
        "retail_aud": round(float(retail_aud), 4),
        "pre_gst_aud": round(pre_gst, 4),
        "pre_pharmacy_aud": round(pre_pharmacy, 4),
        "pre_wholesale_aud": round(pre_wholesale, 4),
        "gst_pct": float(gst_pct),
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
                f"Commercial Withdrawal 이력(연도: {seed.get('commercial_withdrawal_year')}). "
                "재진입 시 TGA에 철수 사유 소명 필요.",
                "PBAC 임상우월성 입증 장벽 동시 존재." if seed.get("pbac_superiority_required") else "",
            ],
            "disclaimer": get_disclaimer_text("blocked"),
            "blocked_reason": "commercial_withdrawal",
        }

    # --- 플래그 수집 (공통) ---
    if seed.get("pbac_superiority_required"):
        warnings.append(
            "복합제가 PBS 신규 등재시 PBAC에 단일성분 대비 임상우월성(예: 심혈관 이벤트 감소) "
            "입증 필요. 등재 지연·거절 리스크 높음."
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
            "PBS Restricted Benefit/Authority — 처방 적응증 제한. 시장 규모 재추정 필요."
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
        if isinstance(aemp_ref, list):
            nums = [float(v) for v in aemp_ref if isinstance(v, (int, float))]
            if not nums:
                return _blocked_no_price(pid, case, "reference_aemp_aud array empty")
            aemp = sum(nums) / len(nums)
            warnings.append(
                f"복수 함량의 평균 AEMP(${aemp:.2f}) 사용. 개별 함량: "
                + ", ".join(f"${v:.2f}" for v in nums)
            )
        elif isinstance(aemp_ref, (int, float)):
            aemp = float(aemp_ref)
        else:
            return _blocked_no_price(pid, case, "reference_aemp_aud missing")

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
                "fx_aud_to_krw": fx_aud_to_krw,
            },
            "warnings": warnings,
            "disclaimer": get_disclaimer_text("A"),
            "blocked_reason": None,
        }

    # --- COMPONENT_SUM: 복합제 성분별 합산 ---
    if case == "COMPONENT_SUM":
        aemp_ref = seed.get("reference_aemp_aud")
        if isinstance(aemp_ref, list):
            nums = [float(v) for v in aemp_ref if isinstance(v, (int, float))]
            if not nums:
                return _blocked_no_price(pid, case, "reference_aemp_aud list empty")
            aemp = max(nums)
            warnings.append(
                f"성분 AEMP range ${min(nums):.2f}~${max(nums):.2f} 중 상한 사용. "
                "오메가3 성분은 PBS 비등재로 별도 가산 필요할 수 있음."
            )
        elif isinstance(aemp_ref, (int, float)):
            aemp = float(aemp_ref)
        else:
            return _blocked_no_price(pid, case, "reference_aemp_aud missing for COMPONENT_SUM")

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
                "fx_aud_to_krw": fx_aud_to_krw,
                "method": "component_sum_max",
            },
            "warnings": warnings,
            "disclaimer": get_disclaimer_text("A"),
            "blocked_reason": None,
        }

    # --- ESTIMATE_substitute: 대체계열 AEMP 참고 ---
    if case == "ESTIMATE_substitute":
        aemp_ref = seed.get("reference_aemp_aud")
        if not isinstance(aemp_ref, (int, float)):
            return _blocked_no_price(pid, case, "substitute reference_aemp_aud missing")
        sub_name = seed.get("substitute_ingredient") or "대체계열"
        warnings.append(
            f"동일성분 부재 → {sub_name} AEMP(${aemp_ref:.2f})를 참고가로 차용. "
            "실제 등재 가능성 및 가격은 PBAC 개별 심의 대상."
        )
        scenarios = calculate_three_scenarios(
            logic="A", aemp_aud=float(aemp_ref), fx_aud_to_krw=fx_aud_to_krw, presets_pct=presets_pct
        )
        return {
            "logic": "A",
            "scenarios": scenarios,
            "inputs": {
                "product_id": pid,
                "pricing_case": case,
                "aemp_aud": float(aemp_ref),
                "substitute_ingredient": sub_name,
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
        # 1순위: seed.reference_retail_aud (수기 검증된 참고가)
        # 2순위: crawler_row.retail_price_aud (크롤러 실시간 시장 추정가)
        # 3순위: blocked
        retail: float | None = None
        retail_source: str | None = None

        seed_retail = seed.get("reference_retail_aud")
        if isinstance(seed_retail, (int, float)) and float(seed_retail) > 0:
            retail = float(seed_retail)
            retail_source = "seed"
            src = seed.get("reference_retail_source")
            if src:
                warnings.append(f"소매 참고가 출처(수기 시드): {src}")
        elif crawler_row is not None:
            cr_retail = crawler_row.get("retail_price_aud")
            if isinstance(cr_retail, (int, float)) and float(cr_retail) > 0:
                retail = float(cr_retail)
                retail_source = "crawler"
                cr_method = crawler_row.get("retail_estimation_method")
                method_label = {
                    "pbs_dpmq": "PBS DPMQ(최대처방량 총약가)",
                    "chemist_markup": "Chemist Warehouse × 1.20 (CHOICE 조사 기준 시장 평균)",
                }.get(cr_method, cr_method or "크롤러 실시간값")
                warnings.append(
                    f"소매 참고가 출처(크롤러 실시간): {method_label}. "
                    "수기 시드(reference_retail_aud) 미확보로 크롤러 추정가 사용 — "
                    "FOB 결과는 시드 확보 후 재검증 권장."
                )

        if retail is None:
            return _blocked_no_price(
                pid, case,
                "reference_retail_aud missing (seed 1순위 / crawler_row 2순위 모두 없음)",
            )

        scenarios = calculate_three_scenarios(
            logic="B",
            retail_aud=retail,
            fx_aud_to_krw=fx_aud_to_krw,
            presets_pct=presets_pct,
            logic_b_kwargs=logic_b_kwargs,
        )
        return {
            "logic": "B",
            "scenarios": scenarios,
            "inputs": {
                "product_id": pid,
                "pricing_case": case,
                "retail_aud": retail,
                "retail_source": retail_source,
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
