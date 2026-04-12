from sources.tga import fetch_tga_artg, determine_export_viable

result = fetch_tga_artg('hydroxycarbamide')
print('TGA:', result)
print('Viable:', determine_export_viable(result))
