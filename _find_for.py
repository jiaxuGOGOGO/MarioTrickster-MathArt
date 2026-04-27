path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
# Find the actual for loop text in sprite pack
idx = content.find("for index, src in enumerate(source_paths):")
print("for loop idx:", idx)
print(repr(content[idx-50:idx+300]))
