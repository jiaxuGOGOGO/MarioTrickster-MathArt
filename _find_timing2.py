path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
idx = content.find("timing_uniformity")
print("first timing_uniformity at:", idx)
print(repr(content[idx-50:idx+400]))
