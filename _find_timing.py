path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
idx = content.find("raw_timing_uniformity")
print("raw_timing_uniformity at:", idx)
if idx >= 0:
    print(repr(content[idx:idx+600]))
