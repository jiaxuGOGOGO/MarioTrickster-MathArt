path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
target = "max_foot_slide_px"
start = 0
while True:
    idx = content.find(target, start)
    if idx == -1:
        break
    print(idx, repr(content[idx:idx+80]))
    start = idx + 1
