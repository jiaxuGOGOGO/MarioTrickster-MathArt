path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
idx = content.find("_fit_sprite_to_cell")
print("_fit_sprite_to_cell idx:", idx)
idx2 = content.rfind("_fit_sprite_to_cell")
print("last idx:", idx2)
print(repr(content[idx2-20:idx2+200]))
