path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

# Find exact text around fit_sprite_to_cell call
idx = content.find("cell = _fit_sprite_to_cell(raw_rgba, cell_size)")
print("fit_cell idx:", idx)
print(repr(content[idx:idx+250]))
