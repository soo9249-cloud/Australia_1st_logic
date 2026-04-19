"""stage2.fob_calculator 역산 공식 단위 테스트 (self-contained).

실행:  python -m stage2.test_fob_calculator
또는:  python stage2/test_fob_calculator.py

architecture note:
    calculate_aemp_from_dpmq 포함 모든 역산 공식은 stage2/fob_calculator.py에
    자체 보관되며, crawler 모듈에는 의존하지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from stage2.fob_calculator import (  # noqa: E402
    ALPHA_MARKET_UPLIFT_PCT,
    calculate_aemp_from_dpmq,
    calculate_fob_logic_a,
    calculate_fob_logic_b,
    calculate_three_scenarios,
    dispatch_by_pricing_case,
)


_fails: list[str] = []


def _assert_close(actual: float, expected: float, tol: float, label: str) -> None:
    diff = abs(actual - expected)
    if diff > tol:
        _fails.append(
            f"✗ {label}: got {actual:.4f}, expected {expected:.4f} (|Δ|={diff:.4f} > tol={tol})"
        )
        print(f"  ✗ {label}: {actual:.4f} vs {expected:.4f}  Δ={diff:.4f}")
    else:
        print(f"  ✓ {label}: {actual:.4f} ≈ {expected:.4f}  Δ={diff:.4f}")


def _assert_true(cond: bool, label: str) -> None:
    if cond:
        print(f"  ✓ {label}")
    else:
        _fails.append(f"✗ {label}")
        print(f"  ✗ {label}")


# --------------------------------------------------------------------------
# TEST 1: Hydrine 실측 검증 — DPMQ $48.11 → AEMP $31.92 (Research doc 기준값)
# --------------------------------------------------------------------------
def test_hydrine_reverse() -> None:
    print("\n[T1] Hydrine DPMQ→AEMP 5-tier 역산 실측 검증")
    result = calculate_aemp_from_dpmq(48.11)
    _assert_close(result, 31.92, tol=0.01, label="Hydrine AEMP")


# --------------------------------------------------------------------------
# TEST 2: 5-tier 경계값 — 각 임계 구간에서 공식이 올바른 쪽으로 분기하는지
# --------------------------------------------------------------------------
def test_tier_boundaries() -> None:
    print("\n[T2] 5-tier 임계값 경계 분기 확인")
    # Tier 1: DPMQ <= 19.70 → AEMP = DPMQ - 14.20
    _assert_close(calculate_aemp_from_dpmq(19.70), 5.50, tol=0.01, label="Tier1 boundary 19.70")
    _assert_close(calculate_aemp_from_dpmq(15.00), 0.80, tol=0.01, label="Tier1 mid 15.00")
    # Tier 2: 19.70 < DPMQ <= 113.79
    _assert_close(calculate_aemp_from_dpmq(113.79), (113.79 - 13.79) / 1.0752, tol=0.01, label="Tier2 boundary 113.79")
    # Tier 3: 113.79 < DPMQ <= 821.64
    _assert_close(calculate_aemp_from_dpmq(821.64), (821.64 - 8.79) / 1.12896, tol=0.01, label="Tier3 boundary 821.64")
    # Tier 4: 821.64 < DPMQ <= 2108.79
    _assert_close(calculate_aemp_from_dpmq(2108.79), (2108.79 - 65.64) / 1.05, tol=0.01, label="Tier4 boundary 2108.79")
    # Tier 5: DPMQ > 2108.79 → AEMP = DPMQ - 162.93
    _assert_close(calculate_aemp_from_dpmq(3000.00), 3000.00 - 162.93, tol=0.01, label="Tier5 3000.00")


# --------------------------------------------------------------------------
# TEST 3: Real Cost of Medicines 5건 — AEMP 역산이 항상 양수이고 DPMQ보다 작은지
# (PBS 공시 DPMQ 값: Research doc "Real Cost of Medicines" 표)
# --------------------------------------------------------------------------
def test_real_cost_samples() -> None:
    print("\n[T3] PBS 실제 품목 DPMQ → AEMP 폴백 sanity check")
    samples = [
        ("Fluticasone+salmeterol 250/25 pMDI", 56.54),
        ("Dabigatran 150mg",                   81.69),
        ("Goserelin 3.6mg + Bicalutamide 50mg", 412.39),
        ("Fingolimod 500mcg",                  1062.59),
        ("Imatinib 400mg",                      662.67),
    ]
    for name, dpmq in samples:
        aemp = calculate_aemp_from_dpmq(dpmq)
        _assert_true(
            aemp is not None and 0 < aemp < dpmq,
            f"{name}: DPMQ=${dpmq} → AEMP=${aemp} (positive & < DPMQ)",
        )


# --------------------------------------------------------------------------
# TEST 4: Logic A Hydrine 10% 프리셋 — (31.92×(1+α)) / 1.10
# --------------------------------------------------------------------------
def test_logic_a_hydrine_10pct() -> None:
    print("\n[T4] Logic A Hydrine 10% importer margin (α 시장 보정 포함)")
    r = calculate_fob_logic_a(31.92, 10.0, fx_aud_to_krw=900.0)
    expected = 31.92 * (1.0 + ALPHA_MARKET_UPLIFT_PCT / 100.0) / 1.10
    _assert_close(r["fob_aud"], expected, tol=0.001, label="Hydrine FOB AUD (10%)")
    _assert_close(r["fob_krw"], expected * 900.0, tol=0.5, label="Hydrine FOB KRW (10%)")


# --------------------------------------------------------------------------
# TEST 5: Logic B Omethyl 소매역산 — retail $48.95 단계별 확인
# --------------------------------------------------------------------------
def test_logic_b_omethyl() -> None:
    print("\n[T5] Logic B Omethyl retail→FOB 역산 단계별")
    retail = 48.95
    r = calculate_fob_logic_b(retail, importer_margin_pct=20.0)
    # pre_gst = 48.95 / 1.10 = 44.50
    _assert_close(r["pre_gst_aud"], retail / 1.10, tol=0.01, label="pre_gst Omethyl")
    # pre_pharmacy = 44.50 / 1.30 = 34.23
    _assert_close(r["pre_pharmacy_aud"], (retail / 1.10) / 1.30, tol=0.01, label="pre_pharmacy Omethyl")
    # FOB는 순차 체인 나눗셈
    expected_fob = retail / 1.10 / 1.30 / 1.10 / 1.20
    _assert_close(r["fob_aud"], expected_fob, tol=0.01, label="Omethyl FOB 20% margin")


# --------------------------------------------------------------------------
# TEST 6: dispatch_by_pricing_case — ESTIMATE_withdrawal 는 blocked
# --------------------------------------------------------------------------
def test_dispatch_withdrawal_blocked() -> None:
    print("\n[T6] ESTIMATE_withdrawal → blocked 반환")
    ciloduo = {
        "product_id": "au-ciloduo-007",
        "pricing_case": "ESTIMATE_withdrawal",
        "commercial_withdrawal_year": 2021,
        "pbac_superiority_required": True,
    }
    r = dispatch_by_pricing_case(ciloduo)
    _assert_true(r["logic"] == "blocked", "logic == 'blocked'")
    _assert_true(r["blocked_reason"] == "commercial_withdrawal", "blocked_reason == 'commercial_withdrawal'")
    _assert_true(len(r["scenarios"]) == 0, "scenarios empty")
    _assert_true(any("2021" in w for w in r["warnings"]), "warning mentions 2021")


# --------------------------------------------------------------------------
# TEST 6b: ESTIMATE_substitute + withdrawal 플래그 — 차단 아님, 경고만
# --------------------------------------------------------------------------
def test_dispatch_substitute_withdrawal_warning() -> None:
    print("\n[T6b] Ciloduo형 substitute + withdrawal → logic A, 경고 포함")
    sub_seed = {
        "product_id": "au-ciloduo-007",
        "pricing_case": "ESTIMATE_substitute",
        "reference_aemp_aud": 11.87,
        "substitute_ingredient": "cilostazol",
        "commercial_withdrawal_flag": True,
        "commercial_withdrawal_year": 2021,
    }
    r = dispatch_by_pricing_case(sub_seed)
    _assert_true(r["logic"] == "A", "logic == A (substitute)")
    _assert_true(r.get("blocked_reason") is None, "not blocked")
    _assert_true(any("Withdrawal" in w for w in r["warnings"]), "withdrawal warning present")


# --------------------------------------------------------------------------
# TEST 7: 3 시나리오 순서 — 수입 스폰서 마진이 클수록 FOB는 작아져야 함
# --------------------------------------------------------------------------
def test_scenario_monotonicity() -> None:
    print("\n[T7] 시나리오 단조성: 수입상 마진↑ → FOB↓ (저가진입 < 기준 < 프리미엄 FOB)")
    sc = calculate_three_scenarios(logic="A", aemp_aud=31.92)
    _assert_true(
        sc["aggressive"]["fob_aud"] < sc["average"]["fob_aud"] < sc["conservative"]["fob_aud"],
        "aggressive(30%) < average(20%) < conservative(10%) [FOB]",
    )


# --------------------------------------------------------------------------
# TEST 7b: Logic A 평균 시나리오(20% 마진) — α와 마진이 상쇄되어 FOB_AUD ≈ 공시 AEMP
# --------------------------------------------------------------------------
def test_logic_a_average_fob_usd_band() -> None:
    print("\n[T7b] Logic A average(20%): FOB_AUD ≈ 공시 AEMP, USD 밴드 검증")
    r = calculate_fob_logic_a(31.92, 20.0, fx_aud_to_krw=900.0)
    _assert_close(r["fob_aud"], 31.92, tol=0.01, label="FOB AUD equals listed AEMP at 20% margin + α=20%")
    aud_usd = 0.716  # 검증용 고정 환율 (보고서 시나리오)
    usd = r["fob_aud"] * aud_usd
    _assert_close(usd, 22.88, tol=0.10, label="FOB USD ≈ 22.88 ±0.10 (Hydrine average)")


# --------------------------------------------------------------------------
# TEST 7c: Logic B PBS 등재 처방 — GST 면제 분기
# --------------------------------------------------------------------------
def test_logic_b_pbs_listed_gst_free() -> None:
    print("\n[T7c] Logic B is_pbs_listed_rx=True → pre_gst = retail (GST 0)")
    retail = 48.95
    r_gst = calculate_fob_logic_b(retail, 20.0, is_pbs_listed_rx=False)
    r_free = calculate_fob_logic_b(retail, 20.0, is_pbs_listed_rx=True)
    _assert_close(r_gst["pre_gst_aud"], retail / 1.10, tol=0.01, label="OTC/비등재: GST 10%")
    _assert_close(r_free["pre_gst_aud"], retail, tol=0.01, label="PBS 등재 RX: GST 면제")


# --------------------------------------------------------------------------
# TEST 9: COMPONENT_SUM Rosumeg — 서브케이스2 (등재+소매역산), FOB USD 밴드
# --------------------------------------------------------------------------
def _seed_by_product_id(product_id: str) -> dict:
    path = _HERE / "fob_reference_seeds.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for s in data.get("seeds", []):
        if s.get("product_id") == product_id:
            return s
    raise RuntimeError(f"seed not found: {product_id}")


def test_component_sum_rosumeg_subcase2() -> None:
    print("\n[T9] COMPONENT_SUM Rosumeg — 서브케이스2, 평균 FOB USD")
    seed = _seed_by_product_id("au-rosumeg-005")
    r = dispatch_by_pricing_case(seed)
    _assert_true(r["logic"] == "A", "logic == A")
    _assert_true(r["inputs"].get("component_sum_subcase") == 2, "subcase == 2")
    avg = r["scenarios"]["average"]
    aud_usd = 0.716
    usd = float(avg["fob_aud"]) * aud_usd
    # 합산 AUD ≈ 2.5 + (48.95/28)/1.1/1.3/1.1 → 평균 시나리오 FOB USD ≈ 2.59 전후
    _assert_close(usd, 2.586, tol=0.03, label="Rosumeg average FOB USD (subcase 2)")


# --------------------------------------------------------------------------
# TEST 8: 입력 검증 — 음수/0은 ValueError
# --------------------------------------------------------------------------
def test_input_validation() -> None:
    print("\n[T8] 음수/0 입력 예외 처리")
    try:
        calculate_fob_logic_a(-10.0, 20.0)
        _fails.append("✗ negative AEMP should raise")
        print("  ✗ negative AEMP should raise")
    except ValueError:
        print("  ✓ negative AEMP raises ValueError")
    try:
        calculate_fob_logic_a(10.0, -5.0)
        _fails.append("✗ negative margin should raise")
        print("  ✗ negative margin should raise")
    except ValueError:
        print("  ✓ negative margin raises ValueError")
    # DPMQ=0 → None
    _assert_true(calculate_aemp_from_dpmq(0) is None, "AEMP(DPMQ=0) returns None")
    _assert_true(calculate_aemp_from_dpmq(None) is None, "AEMP(None) returns None")


# --------------------------------------------------------------------------
def main() -> int:
    print("=" * 70)
    print("UPharma 호주 FOB 계산기 단위 테스트")
    print("=" * 70)

    test_hydrine_reverse()
    test_tier_boundaries()
    test_real_cost_samples()
    test_logic_a_hydrine_10pct()
    test_logic_b_omethyl()
    test_dispatch_withdrawal_blocked()
    test_dispatch_substitute_withdrawal_warning()
    test_scenario_monotonicity()
    test_logic_a_average_fob_usd_band()
    test_logic_b_pbs_listed_gst_free()
    test_component_sum_rosumeg_subcase2()
    test_input_validation()

    print("\n" + "=" * 70)
    if _fails:
        print(f"FAILED: {len(_fails)} 개")
        for f in _fails:
            print(f"  {f}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
