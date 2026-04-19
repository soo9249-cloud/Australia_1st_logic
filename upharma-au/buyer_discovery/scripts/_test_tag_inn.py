"""tag_via_inn fix 검증 — 5개 회사 샘플."""
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(r"C:/Users/user/Desktop/Australia_1st_logic/.env", override=True)
sys.path.insert(0, r"C:/Users/user/Desktop/Australia_1st_logic/upharma-au")

from buyer_discovery.scripts.tag_therapeutic_areas import (
    _load_inn_to_therapy, tag_via_inn
)

inn_map = _load_inn_to_therapy()
for name in [
    "Cipla Australia Pty Ltd",
    "Medsurge Pharma Pty Ltd",
    "Pharmacor Pty Ltd",
    "GlaxoSmithKline Australia Pty Ltd",
    "Bayer Australia Ltd",
]:
    r = tag_via_inn(name, inn_map)
    print(f"{name:45s} → en={r.get('areas_en')} kr={r.get('areas_kr')} inns={r.get('source_inns')}")
