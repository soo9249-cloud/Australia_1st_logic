"""FOB 수출가 역산 전체 시뮬레이션 — 8개 품목 + 신약 케이스별 검증.

실행:  python -m stage2.simulate_fob_all
또는:  python stage2/simulate_fob_all.py

■ 구조
  - 케이스별(DIRECT / COMPONENT_SUM / ESTIMATE_*) 로 구성
  - 8개 기존 품목 전부 + 케이스별 신약 예시 포함
  - PBS API 실시간 크롤링 시도 → 실패 시 현실적 mock 값으로 폴백
  - FOB 3시나리오(저가진입/기준가/프리미엄) + 이상치 검사 출력

■ 이상치 기준
  - FOB AUD < 0.10  → 지나치게 낮음 (역산 오류 가능)
  - FOB AUD > 500   → 지나치게 높음 (단위 오류 가능)
  - blocked         → 데이터 미수집 or 규제 차단
  - warnings 3개↑   → 신뢰도 검토 필요
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from stage2.fob_calculator import dispatch_by_pricing_case  # noqa: E402

# ── 환율 고정값 (시뮬레이션용) ──────────────────────────────────────────────
FX_AUD_TO_KRW = 910.0
FX_AUD_TO_USD = 0.716

# ── 이상치 임계값 ──────────────────────────────────────────────────────────
FOB_LOW_WARN  = 0.10   # AUD 이하 → 지나치게 낮음
FOB_HIGH_WARN = 500.0  # AUD 이상 → 지나치게 높음
MAX_WARNINGS  = 3      # 경고 3개 이상 → 신뢰도 검토

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: seeds.json 로드
# ══════════════════════════════════════════════════════════════════════════════
def _load_seeds() -> dict[str, dict]:
    path = _HERE / "fob_reference_seeds.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {s["product_id"]: s for s in data.get("seeds", [])}


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: PBS 실시간 크롤링 시도 (실패 시 mock 폴백)
# ══════════════════════════════════════════════════════════════════════════════
def _try_live_pbs(ingredient: str) -> dict | None:
    """PBS API 실시간 조회. 실패 시 None 반환."""
    try:
        sys.path.insert(0, str(_ROOT / "crawler"))
        from sources.pbs import fetch_pbs_by_ingredient  # type: ignore
        rows = fetch_pbs_by_ingredient(ingredient)
        if rows:
            return rows[0]
    except Exception:
        pass
    return None


def _try_live_healthylife(slug: str) -> float | None:
    """Healthylife 실시간 소매가 조회. 실패 시 None 반환."""
    try:
        sys.path.insert(0, str(_ROOT / "crawler"))
        from sources.healthylife import fetch_healthylife_price  # type: ignore
        r = fetch_healthylife_price(slug)
        if r:
            price = r.get("price_aud") or r.get("retail_price_aud")
            if price:
                return float(price)
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: 케이스별 crawler_row 구성
# ══════════════════════════════════════════════════════════════════════════════

def _build_crawler_row_DIRECT(
    ingredient: str,
    mock_aemp: float,
    mock_dpmq: float | None = None,
) -> dict:
    """DIRECT 케이스: PBS API → aemp_aud + dpmq_aud."""
    live = _try_live_pbs(ingredient)
    if live and (live.get("aemp_aud") or live.get("pbs_price_aud")):
        aemp = float(live.get("aemp_aud") or live.get("pbs_price_aud"))
        dpmq = float(live.get("dpmq_aud") or live.get("pbs_dpmq") or mock_dpmq or 0)
        source = "live_pbs"
    else:
        aemp = mock_aemp
        dpmq = mock_dpmq or 0.0
        source = "mock"
    return {
        "aemp_aud": aemp,
        "dpmq_aud": dpmq,
        "_price_source": source,
        "_ingredient": ingredient,
    }


def _build_crawler_row_COMPONENT_SUM(
    listed_ingredient: str,
    mock_listed_aemp: float,
    healthylife_slug: str,
    mock_retail: float,
    unlisted_ingredient: str,
    all_components: list[str],
) -> dict:
    """COMPONENT_SUM 케이스: 등재 성분 PBS + 미등재 성분 Healthylife 소매가.

    ingredients_split.pbs_prices  에 등재 성분 개별 AEMP 저장.
    ingredients_split.retail_prices 에 미등재 성분 소매가 원본 저장.
    """
    # 등재 성분 PBS 조회
    live_pbs = _try_live_pbs(listed_ingredient)
    if live_pbs and (live_pbs.get("aemp_aud") or live_pbs.get("pbs_price_aud")):
        listed_aemp = float(live_pbs.get("aemp_aud") or live_pbs.get("pbs_price_aud"))
        pbs_source = "live_pbs"
    else:
        listed_aemp = mock_listed_aemp
        pbs_source = "mock"

    # 미등재 성분 Healthylife 소매가
    live_hl = _try_live_healthylife(healthylife_slug)
    retail = live_hl if live_hl else mock_retail
    hl_source = "live_healthylife" if live_hl else "mock"

    return {
        "healthylife_price_aud": retail,
        "ingredients_split": {
            "components": all_components,
            "pbs_prices":    {listed_ingredient.lower(): listed_aemp},
            "retail_prices": {unlisted_ingredient.lower(): retail},
        },
        "_price_source": f"pbs={pbs_source}, hl={hl_source}",
    }


def _build_crawler_row_ESTIMATE_private(
    healthylife_slug: str,
    mock_retail: float,
) -> dict:
    """ESTIMATE_private: Healthylife 소매가 → Logic B 역산."""
    live = _try_live_healthylife(healthylife_slug)
    retail = live if live else mock_retail
    return {
        "retail_price_aud": retail,
        "retail_estimation_method": "healthylife_actual" if live else "mock",
        "_price_source": "live_healthylife" if live else "mock",
    }


def _build_crawler_row_ESTIMATE_substitute(
    similar_inn: str,
    mock_aemp: float,
) -> dict:
    """ESTIMATE_substitute: 대체계열 PBS AEMP 조회."""
    live = _try_live_pbs(similar_inn)
    if live and (live.get("aemp_aud") or live.get("pbs_price_aud")):
        aemp = float(live.get("aemp_aud") or live.get("pbs_price_aud"))
        source = "live_pbs"
    else:
        aemp = mock_aemp
        source = "mock"
    return {
        "pbs_aemp_aud": aemp,
        "_price_source": source,
        "_similar_inn": similar_inn,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: FOB 결과 출력 + 이상치 검사
# ══════════════════════════════════════════════════════════════════════════════

_anomalies: list[str] = []


def _print_fob_result(
    label: str,
    result: dict,
    crawler_row: dict | None = None,
) -> None:
    logic = result.get("logic", "?")
    blocked = result.get("blocked_reason")
    scenarios = result.get("scenarios", {})
    warnings = result.get("warnings", [])
    inputs = result.get("inputs", {})

    price_src = (crawler_row or {}).get("_price_source", "—")
    print(f"\n  {'─'*60}")
    print(f"  {label}")
    print(f"  Logic: {logic}  |  가격출처: {price_src}")

    if blocked:
        msg = f"  ⛔ BLOCKED: {blocked}"
        print(msg)
        _anomalies.append(f"[BLOCKED] {label}: {blocked}")
        return

    # AEMP / 기준가 표시
    aemp = inputs.get("aemp_aud")
    if aemp:
        print(f"  기준 AEMP: AUD {float(aemp):.2f}  "
              f"(출처: {inputs.get('aemp_source', inputs.get('component_sum_method', '—'))})")

    # 3시나리오
    scenario_map = {
        "aggressive":   "저가 진입 (Penetration)",
        "average":      "기준가   (Reference)  ",
        "conservative": "프리미엄  (Premium)    ",
    }
    for key, label_sc in scenario_map.items():
        sc = scenarios.get(key)
        if not sc:
            continue
        fob_aud = float(sc.get("fob_aud", 0))
        fob_usd = fob_aud * FX_AUD_TO_USD
        fob_krw = float(sc.get("fob_krw", fob_aud * FX_AUD_TO_KRW))
        margin   = sc.get("importer_margin_pct", "?")
        print(f"    {label_sc}  FOB AUD {fob_aud:7.2f}  "
              f"USD {fob_usd:6.2f}  KRW {fob_krw:>10,.0f}  (마진 {margin}%)")

        # 이상치 검사
        if fob_aud < FOB_LOW_WARN:
            _anomalies.append(f"[LOW]  {label} {key}: FOB AUD {fob_aud:.4f} < {FOB_LOW_WARN}")
        if fob_aud > FOB_HIGH_WARN:
            _anomalies.append(f"[HIGH] {label} {key}: FOB AUD {fob_aud:.2f} > {FOB_HIGH_WARN}")

    # 경고
    if len(warnings) >= MAX_WARNINGS:
        _anomalies.append(f"[WARN] {label}: 경고 {len(warnings)}개 — 신뢰도 검토 필요")
    for w in warnings:
        print(f"    ⚠ {w[:90]}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: 케이스별 실행
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    seeds = _load_seeds()
    print("=" * 70)
    print("UPharma 호주 FOB 수출가 역산 전체 시뮬레이션")
    print(f"환율: 1 AUD = {FX_AUD_TO_KRW:,.0f} KRW = {FX_AUD_TO_USD} USD")
    print("=" * 70)

    # ──────────────────────────────────────────────────────────────────────────
    # CASE 1: DIRECT — PBS 공시 AEMP 직접 사용
    # 해당 품목: Hydrine, Sereterol
    # 신약 적용: PBS에 단일성분 또는 복합제로 등재된 경우 동일 케이스 사용
    # ──────────────────────────────────────────────────────────────────────────
    print("\n\n[CASE 1] DIRECT — PBS 공시 AEMP 직접 사용")
    print("  적용 대상: PBS 등재 처방약 (단일성분 또는 복합제)")

    # 1-A: Hydrine (hydroxycarbamide 500mg)
    seed = seeds["au-hydrine-004"]
    cr = _build_crawler_row_DIRECT("hydroxycarbamide", mock_aemp=31.92, mock_dpmq=48.11)
    r = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr)
    _print_fob_result("Hydrine (Hydroxyurea 500mg Cap.)", r, cr)

    # 1-B: Sereterol 250/50 (fluticasone+salmeterol — 함량별 시드 평균)
    seed = seeds["au-sereterol-003"]
    cr_ser = {"aemp_aud": None, "_price_source": "seed_list_average"}  # 리스트 시드 → 평균
    r = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr_ser)
    _print_fob_result("Sereterol Activair (Fluticasone/Salmeterol 250/50·500/50 평균)", r, cr_ser)

    # ──────────────────────────────────────────────────────────────────────────
    # CASE 2: COMPONENT_SUM — 복합제 미등재, 단일성분 합산
    # 해당 품목: Rosumeg (5/1000, 10/1000), Atmeg (10/1000)
    # 신약 적용: 복합제가 PBS 미등재지만 각 단일성분은 등재된 경우
    # ──────────────────────────────────────────────────────────────────────────
    print("\n\n[CASE 2] COMPONENT_SUM — 복합제 미등재, 단일성분 PBS AEMP + OTC 소매가 합산")
    print("  등재 성분: PBS API 실시간 AEMP  |  미등재 성분: Healthylife 소매가 → 역산")

    # 2-A: Rosumeg 5/1000 (rosuvastatin 5mg + omega-3 EE90 1g)
    seed = seeds["au-rosumeg-005"]
    cr = _build_crawler_row_COMPONENT_SUM(
        listed_ingredient="rosuvastatin",
        mock_listed_aemp=2.00,          # PBS rosuvastatin 5mg 제네릭 추정
        healthylife_slug="omacor-1000mg-cap-28",
        mock_retail=48.95,
        unlisted_ingredient="omega-3-acid ethyl esters",
        all_components=["rosuvastatin", "omega-3-acid ethyl esters"],
    )
    r = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr)
    _print_fob_result("Rosumeg 5/1000 (Rosuvastatin 5mg + Omega-3 EE90 1g)", r, cr)

    # 2-B: Rosumeg 10/1000 (rosuvastatin 10mg + omega-3 EE90 1g)
    cr2 = _build_crawler_row_COMPONENT_SUM(
        listed_ingredient="rosuvastatin",
        mock_listed_aemp=2.50,          # PBS rosuvastatin 10mg 제네릭
        healthylife_slug="omacor-1000mg-cap-28",
        mock_retail=48.95,
        unlisted_ingredient="omega-3-acid ethyl esters",
        all_components=["rosuvastatin", "omega-3-acid ethyl esters"],
    )
    r2 = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr2)
    _print_fob_result("Rosumeg 10/1000 (Rosuvastatin 10mg + Omega-3 EE90 1g)", r2, cr2)

    # 2-C: Atmeg 10/1000 (atorvastatin 10mg + omega-3 EE90 1g)
    seed = seeds["au-atmeg-006"]
    cr = _build_crawler_row_COMPONENT_SUM(
        listed_ingredient="atorvastatin",
        mock_listed_aemp=2.50,          # PBS atorvastatin 10mg 제네릭
        healthylife_slug="omacor-1000mg-cap-28",
        mock_retail=48.95,
        unlisted_ingredient="omega-3-acid ethyl esters",
        all_components=["atorvastatin", "omega-3-acid ethyl esters"],
    )
    r = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr)
    _print_fob_result("Atmeg 10/1000 (Atorvastatin 10mg + Omega-3 EE90 1g)", r, cr)

    # ──────────────────────────────────────────────────────────────────────────
    # CASE 3: ESTIMATE_private — PBS 미등재, 민간 소매가 역산
    # 해당 품목: Omethyl (omega-3 EE90 2g pouch)
    # 신약 적용: PBS·TGA 미등재이나 유사 OTC 참고가로 추정 가능한 경우
    # ──────────────────────────────────────────────────────────────────────────
    print("\n\n[CASE 3] ESTIMATE_private — PBS 미등재, Healthylife 소매가 → Logic B 역산")
    print("  GST(10%) + 약국마진(30%) + 도매마진(10%) 순차 차감")

    seed = seeds["au-omethyl-001"]
    cr = _build_crawler_row_ESTIMATE_private(
        healthylife_slug="omacor-1000mg-cap-28",
        mock_retail=48.95,
    )
    r = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr)
    _print_fob_result("Omethyl Cutielet (Omega-3 EE90 2g Pouch)", r, cr)

    # ──────────────────────────────────────────────────────────────────────────
    # CASE 4: ESTIMATE_hospital — 병원 조달 전용, TradeMap 수기 확정가
    # 해당 품목: Gadvoa (gadobutrol MRI 조영제)
    # 신약 적용: PBS 미등재 + 병원 독점 공급 품목 (HealthShare NSW 등)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n\n[CASE 4] ESTIMATE_hospital — 병원 조달, TradeMap 수기 역산 확정가")
    print("  PBS 등재 불가. HealthShare NSW / State Hospital Tender 경로.")

    seed = seeds["au-gadvoa-002"]
    # Gadvoa: fob_hardcoded_aud 직접 사용 — crawler_row 불필요
    r5mL  = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW)
    # 7.5mL 기본값 동일 시드 사용 (by_size 는 별도 표시)
    _print_fob_result("Gadvoa Inj. 7.5mL PFS (Gadobutrol — 기본 7.5mL 기준)", r5mL)
    # 5mL 별도 계산 (by_size 값 직접 주입)
    seed_5ml = dict(seed)
    hc_5ml = dict(seed.get("fob_hardcoded_aud", {}))
    hc_5ml.update(seed["fob_hardcoded_aud"]["by_size"]["5mL"])
    seed_5ml["fob_hardcoded_aud"] = hc_5ml
    r_5ml = dispatch_by_pricing_case(seed_5ml, fx_aud_to_krw=FX_AUD_TO_KRW)
    _print_fob_result("Gadvoa Inj. 5mL PFS (Gadobutrol — 5mL 기준)", r_5ml)

    # ──────────────────────────────────────────────────────────────────────────
    # CASE 5: ESTIMATE_substitute — PBS 미등재, 대체계열 AEMP 참고
    # 해당 품목: Ciloduo (cilostazol 2021 철수), Gastiin CR (mosapride 미등재)
    # 신약 적용: 동일성분 미등재지만 유사 ATC 계열이 PBS 등재된 경우
    # ──────────────────────────────────────────────────────────────────────────
    print("\n\n[CASE 5] ESTIMATE_substitute — 동일성분 미등재, 대체계열 PBS AEMP 참고")
    print("  실시간: similar_inns 성분 PBS 조회 → AEMP 차용. 정밀도 낮음(confidence ≤0.4)")

    # 5-A: Ciloduo 200/10 (cilostazol 철수 → clopidogrel 대체 참고)
    seed = seeds["au-ciloduo-007"]
    cr = _build_crawler_row_ESTIMATE_substitute(
        similar_inn="clopidogrel",
        mock_aemp=14.37,   # cilostazol 11.87 + rosuvastatin 10mg 2.50
    )
    r = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr)
    _print_fob_result("Ciloduo 200/10mg (Cilostazol 200 + Rosuvastatin 10 — 대체계열)", r, cr)

    # 5-B: Ciloduo 200/20 (rosuvastatin 20mg → AEMP 다름)
    seed_c20 = dict(seed)
    seed_c20["reference_aemp_aud"] = 15.37   # per_strength.200/20 값 사용
    cr_c20 = _build_crawler_row_ESTIMATE_substitute("clopidogrel", mock_aemp=15.37)
    r_c20 = dispatch_by_pricing_case(seed_c20, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr_c20)
    _print_fob_result("Ciloduo 200/20mg (Cilostazol 200 + Rosuvastatin 20 — 대체계열)", r_c20, cr_c20)

    # 5-C: Gastiin CR (mosapride → domperidone 대체 참고)
    seed = seeds["au-gastiin-008"]
    cr = _build_crawler_row_ESTIMATE_substitute(
        similar_inn="domperidone",
        mock_aemp=1.85,   # domperidone 10mg PBS AEMP 추정
    )
    r = dispatch_by_pricing_case(seed, fx_aud_to_krw=FX_AUD_TO_KRW, crawler_row=cr)
    _print_fob_result("Gastiin CR 15mg Tab. (Mosapride — domperidone 대체 참고)", r, cr)

    # ──────────────────────────────────────────────────────────────────────────
    # CASE 6: ESTIMATE_withdrawal — TGA 상업적 철수 (계산 차단)
    # 신약 적용: 동일성분이 과거 철수 이력이 있는 경우 자동 차단
    # ──────────────────────────────────────────────────────────────────────────
    print("\n\n[CASE 6] ESTIMATE_withdrawal — TGA Commercial Withdrawal (계산 차단)")
    print("  철수 소명 + PBAC 재심의 필요. FOB 역산 불가.")

    withdrawal_example = {
        "product_id": "신약-withdrawal-예시",
        "pricing_case": "ESTIMATE_withdrawal",
        "commercial_withdrawal_flag": True,
        "commercial_withdrawal_year": 2021,
        "pbac_superiority_required": True,
    }
    r = dispatch_by_pricing_case(withdrawal_example, fx_aud_to_krw=FX_AUD_TO_KRW)
    _print_fob_result("신약 예시 — Commercial Withdrawal 케이스", r)

    # ══════════════════════════════════════════════════════════════════════════
    # 이상치 요약
    # ══════════════════════════════════════════════════════════════════════════
    print("\n\n" + "=" * 70)
    print("이상치 검사 결과")
    print("=" * 70)
    if _anomalies:
        print(f"⚠ 이상치 {len(_anomalies)}건 발견:")
        for a in _anomalies:
            print(f"  {a}")
    else:
        print("✓ 이상치 없음 — 전 품목 정상 범위")

    print("\n" + "=" * 70)
    print("시뮬레이션 완료")
    print(f"  총 케이스: 6  |  총 품목: 8개 + Ciloduo 2함량 + 신약예시")
    print("  ※ 마진값(약국30%·도매10%)은 추정값 — 지수님 딥리서치 결과 반영 후 업데이트 예정")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
