path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

# find the guide baking return block
idx = content.find('"mask_dir": str(mask_dir.resolve()),\n        "source_frames": source_frames,')
if idx == -1:
    print("NOT FOUND, searching...")
    idx2 = content.find('"source_frames": source_frames,')
    print("source_frames at:", idx2)
    print(repr(content[idx2-300:idx2+50]))
else:
    print("FOUND at", idx)
    print(repr(content[idx:idx+250]))
