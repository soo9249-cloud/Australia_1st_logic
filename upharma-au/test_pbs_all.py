import sys
sys.path.insert(0, '.')
from crawler.sources.pbs import fetch_pbs_by_ingredient

products = [
    ("Omethyl Cutielet",   "omega-3-acid ethyl esters"),
    ("Rosumeg Combigel",   "rosuvastatin"),        
    ("Atmeg Combigel",     "atorvastatin"),
    ("Ciloduo",            "cilostazol"),
    ("Gastiin CR",         "mosapride"),
    ("Sereterol Activair", "fluticasone"),
    ("Gadvoa Inj.",        "gadobutrol"),
    ("Hydrine",            "hydroxycarbamide"),
]

for name, inn in products:
    print(f"\n{'='*60}")
    print(f"[{name}] 검색어: {inn}")
    print(f"{'='*60}")
    results = fetch_pbs_by_ingredient(inn)
    
    if not results or not results[0]['pbs_listed']:
        print(f"  ❌ PBS 미등재")
        continue
    
    print(f"  총 {len(results)}개 제품 발견")
    for i, r in enumerate(results, 1):
        print(f"\n  [{i}] {r['pbs_brand_name']}")
        print(f"      PBS 코드       : {r['pbs_item_code']}")
        print(f"      가격(determined): {r['pbs_price_aud']} AUD")
        print(f"      DPMQ          : {r['pbs_dpmq']} AUD")
        print(f"      팩 사이즈      : {r['pbs_pack_size']}정")
        print(f"      급여 유형      : {r['pbs_benefit_type']}")
        print(f"      오리지널 여부  : {r['pbs_innovator']}")
        print(f"      제한급여 여부  : {r['pbs_restriction']}")
        print(f"      처방 반복      : {r['pbs_repeats']}회")
        print(f"      처방집 분류    : {r['pbs_formulary']}")
        print(f"      최초 등재일    : {r['pbs_first_listed_date']}")
        print(f"      소스 URL       : {r['pbs_source_url']}")