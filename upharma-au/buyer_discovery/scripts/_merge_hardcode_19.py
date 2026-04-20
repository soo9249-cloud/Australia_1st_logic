"""Jisoo Gemini 딥리서치 19개 결과를 au_buyers_hardcode.json 에 병합."""
import json
from pathlib import Path

# Jisoo 가 준 결과 (locations 빈 항목은 [] 로 보정)
new_entries = [
    {"canonical_name":"Avallon Pharmaceuticals Pty Ltd","annual_revenue_rank":"순위 밖","factory":{"has":"N","count":0,"locations":[]},"notes":"자체 공장이 없는 소형 의약품 도매/유통사. Ciloduo 완제 턴키 독점 공급 제안 방식의 수출 타겟으로 고려 가능."},
    {"canonical_name":"Baxter Healthcare Pty Ltd","annual_revenue_rank":"TOP 20 (특수/수액)","factory":{"has":"Y","count":1,"locations":[]},"notes":"호주 병원 수액·주사제 시장의 약 95% 점유 핵심 제조사. Gastiin 주사제 등 병원용 주사제 라인의 강력한 상업화 파트너로 매우 적합."},
    {"canonical_name":"Bayer Australia Ltd","annual_revenue_rank":"TOP 20 (오리지널)","factory":{"has":"N","count":0,"locations":[]},"notes":"Gadvoa의 오리지널 제조사이자 다국적 빅파마의 호주 마케팅 법인. 자사 글로벌 파이프라인 집중으로 한국산 제네릭 완제의 직접 바이어로는 적합도 낮음."},
    {"canonical_name":"Boucher & Muir Pty Ltd","annual_revenue_rank":"TOP 50 (제네릭/특수)","factory":{"has":"N","count":0,"locations":[]},"notes":"ADVANZ PHARMA 산하로 호주·뉴질랜드 틈새 특수의약품 수입 유통에 특화. Sereterol 등 특수 제형(흡입기) 호주 진출 파트너로 유망."},
    {"canonical_name":"Cipla Australia Pty Ltd","annual_revenue_rank":"TOP 50 (제네릭)","factory":{"has":"N","count":0,"locations":[]},"notes":"인도계 글로벌 제네릭사로 탄탄한 자체 글로벌 제조망 보유. Sereterol 등 주요 호흡기 품목이 겹칠 가능성 커 파트너십 매력도 낮음."},
    {"canonical_name":"Demogen Australia Pty Ltd trading as DEMOGEN Pharmaceuticals","annual_revenue_rank":"순위 밖","factory":{"has":"N","count":0,"locations":[]},"notes":"특수 수술용 및 병원용 주사제 제네릭 수입 유통에 집중하는 소형 기업. Gastiin 주사제 등의 B2B 완제 수출 타겟으로 접근 적합."},
    {"canonical_name":"Generic Health Pty Ltd","annual_revenue_rank":"TOP 20 (제네릭)","factory":{"has":"N","count":0,"locations":[]},"notes":"글로벌 제약사 Lupin의 자회사로 호주 내 제네릭 4위권 고속 성장. 인도 모회사 파이프라인 의존도 높아 신규 도입 유인 제한적."},
    {"canonical_name":"GM Pharma International Pty Ltd","annual_revenue_rank":"순위 밖","factory":{"has":"N","count":0,"locations":[]},"notes":"호주 대형 약국 체인(Sigma/Chemist Warehouse) 연관 소형 제네릭 유통망 추정. Atmeg·Gastiin 등 대중적 제네릭의 약국 유통 PB 채널로 고려 가능."},
    {"canonical_name":"Helix Pharmaceuticals Pty Ltd","annual_revenue_rank":"unknown","factory":{"has":"unknown","count":0,"locations":[]},"notes":"과거 TGA 허가 이력은 있으나 실체·규모 파악 어려운 소형 페이퍼 컴퍼니 추정. 핵심 품목 Sereterol 수출 파트너로는 리스크 큼."},
    {"canonical_name":"Ipca Pharma (Australia) Pty Ltd","annual_revenue_rank":"TOP 50 (제네릭)","factory":{"has":"N","count":0,"locations":[]},"notes":"인도 Ipca Labs의 호주 유통 법인으로 모든 의약품을 모회사 제조시설에서 전량 수입. 자사 소화기 포트폴리오 있어 Gastiin 완제 외부 조달 동기 약함."},
    {"canonical_name":"Johnson & Johnson Pacific Pty Ltd","annual_revenue_rank":"TOP 10 (오리지널/OTC)","factory":{"has":"N","count":0,"locations":[]},"notes":"다국적 빅파마 법인으로 자사 블록버스터 오리지널·OTC 마케팅 위주. Gastiin 제네릭 등의 수출 바이어로는 전략적 부합도 떨어짐."},
    {"canonical_name":"LumaCina Australia Pty Ltd","annual_revenue_rank":"TOP 50 (특수/주사제)","factory":{"has":"Y","count":1,"locations":["Perth, WA"]},"notes":"화이자 퍼스 공장 인수해 무균 주사제 CDMO·상업화 병행. Gastiin 등 주사제 완제 수입보다 자사 공장 수주 영업 집중 가능성 높음."},
    {"canonical_name":"Medreich Australia Pty Ltd","annual_revenue_rank":"순위 밖","factory":{"has":"N","count":0,"locations":[]},"notes":"일본 Meiji 그룹 소속으로 아시아·유럽 거점 공장 활용 위탁생산·공급 주력. Rosumeg 등 한국산 제네릭 인라이선싱 유인 약함."},
    {"canonical_name":"Micro Labs Pty Ltd","annual_revenue_rank":"순위 밖","factory":{"has":"N","count":0,"locations":[]},"notes":"글로벌 13개 제조 시설 보유 인도계 다국적 제네릭사의 호주 마케팅 법인. Atmeg 관련 심혈관계 파이프라인 자사 공장 직접 조달 가능해 외부 도입 필요성 낮음."},
    {"canonical_name":"Orion Pharma (AUS) Pty Limited","annual_revenue_rank":"순위 밖","factory":{"has":"N","count":0,"locations":[]},"notes":"호주 내 공장 없음, 핀란드 본사로부터 자체 호흡기 포트폴리오(Easyhaler 등) 조달. Sereterol(흡입기) 분야 직접 경쟁사라 바이어로 매우 부적합."},
    {"canonical_name":"Pharmacor Pty Ltd","annual_revenue_rank":"TOP 20 (제네릭)","factory":{"has":"Y","count":1,"locations":["Melbourne, VIC"]},"notes":"인도 Alkem 산하이나 호주 멜버른 자체 R&D·제조 시설 가동, CDMO 사업 영위. 모회사 수입·자체 조달 기반 강해 Rosumeg/Ciloduo 도입 장벽 높을 수 있음."},
    {"canonical_name":"Southern Cross Pharma Pty Ltd","annual_revenue_rank":"TOP 50 (제네릭)","factory":{"has":"N","count":0,"locations":[]},"notes":"2021년 다국적사 Lupin 피인수되어 현재 Generic Health 와 통합 시너지. 인도 본사 풍부한 파이프라인으로 Ciloduo 등 외부 완제 수입 매력도 낮음."},
    {"canonical_name":"Strides Pharma Science Pty Ltd","annual_revenue_rank":"순위 밖","factory":{"has":"N","count":0,"locations":[]},"notes":"호주 점유율 1위 Arrotex 에 인도 제조공장 대규모 물량 납품하는 핵심 B2B 공급사. 호주 내 자체 상업화보다 타사 제품 생산 목적이라 바이어 타겟으로 부적합."},
    {"canonical_name":"Sun Pharma ANZ Pty Ltd","annual_revenue_rank":"TOP 20 (제네릭/특수)","factory":{"has":"N","count":0,"locations":[]},"notes":"안과·종양·피부과 강점 인도 글로벌 빅파마의 호주 법인, 자체 제조 기반 압도적. Atmeg·Ciloduo 등 자사 주력 경쟁 영역에서 타사 제네릭 완제 우선 도입 확률 매우 낮음."},
]

# survivors_expanded_v4.json 에서 canonical_key ↔ canonical_name 매핑 구성
v4_path = Path(r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/survivors_expanded_v4.json")
v4 = json.loads(v4_path.read_text(encoding="utf-8"))
name_to_key: dict[str, str] = {}
for k, b in (v4.get("buyers") or {}).items():
    nm = (b.get("canonical_name") or "").strip()
    if nm:
        name_to_key[nm] = k

# hardcode 완성본 로드
hc_path = Path(r"C:/Users/user/Documents/Claude/Projects/AX 호주 final/au_buyers_hardcode.json")
hc = json.loads(hc_path.read_text(encoding="utf-8"))
buyers = hc.setdefault("buyers", {})

added = 0
skipped = []
for entry in new_entries:
    nm = entry["canonical_name"]
    key = name_to_key.get(nm)
    if not key:
        # fallback: 이름 유사 매칭
        low_nm = nm.lower()
        for cand_nm, cand_key in name_to_key.items():
            if cand_nm.lower() == low_nm:
                key = cand_key
                break
    if not key:
        skipped.append(nm)
        continue
    if key in buyers:
        print(f"  [이미 있음, 덮어쓰기] {key} ← {nm}", flush=True)
    else:
        print(f"  [추가] {key} ← {nm}", flush=True)
    buyers[key] = {
        "canonical_name": entry["canonical_name"],
        "annual_revenue_rank": entry["annual_revenue_rank"],
        "factory": entry["factory"],
        "notes": entry["notes"],
    }
    added += 1

# _meta 업데이트
hc.setdefault("_meta", {})
hc["_meta"]["last_updated"] = "2026-04-20"
hc["_meta"]["total"] = len(buyers)
hc["_meta"]["gemini_deep_research_rounds"] = (
    (hc["_meta"].get("gemini_deep_research_rounds") or 0) + 1
)

hc_path.write_text(
    json.dumps(hc, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(flush=True)
print(f"총 bubyers: {len(buyers)}", flush=True)
print(f"이번 병합: {added}개", flush=True)
if skipped:
    print(f"매칭 실패 {len(skipped)}: {skipped}", flush=True)
else:
    print(f"매칭 실패 0", flush=True)
