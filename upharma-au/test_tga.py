import sys
sys.path.insert(0, '.')
from crawler.sources.tga import fetch_tga_artg, determine_export_viable

print("=== Hydrine (hydroxycarbamide) ===")
result = fetch_tga_artg('hydroxycarbamide')
print(result)
viable = determine_export_viable(result)
print(viable)