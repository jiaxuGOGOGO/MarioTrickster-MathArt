path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
# Find all occurrences
for keyword in ["_add_check(\n        \"timing_uniformity\"", "timing_uniformity\",\n        float"]:
    idx = content.find(keyword)
    print(f"'{keyword[:30]}' at:", idx)
    if idx >= 0:
        print(repr(content[idx-20:idx+300]))
        print("---")
