import sys
sys.path.insert(0, '.')
import os, httpx, time
from dotenv import load_dotenv
load_dotenv()

headers = {'Subscription-Key': os.environ['PBS_SUBSCRIPTION_KEY']}
time.sleep(21)
r = httpx.get('https://data-api.health.gov.au/pbs/api/v3/schedules', headers=headers, timeout=10)
schedule = str(r.json()['data'][0]['schedule_code'])

time.sleep(21)
r2 = httpx.get('https://data-api.health.gov.au/pbs/api/v3/items', 
    params={'schedule_code': schedule, 'drug_name': 'hydroxycarbamide', 'page': 1, 'limit': 10}, 
    headers=headers, timeout=10)

row = r2.json()['data'][0]
print("=== API에서 제공되는 전체 필드 ===")
for k, v in row.items():
    if v is not None:
        print(f"  {k}: {v}")