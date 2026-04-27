path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

# Find mask section
idx = content.find("use_baked_masks")
print("use_baked_masks idx:", idx)
if idx >= 0:
    print(repr(content[idx-300:idx+300]))
else:
    # search alternate
    idx2 = content.find("baked_mask_paths")
    print("baked_mask_paths idx:", idx2)
    print(repr(content[idx2-50:idx2+300]))
