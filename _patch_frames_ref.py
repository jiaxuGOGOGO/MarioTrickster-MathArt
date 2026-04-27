path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
old = '"bboxes": [list(bbox) if bbox is not None else None for bbox in bboxes],\n    }\n\n\ndef _stabilize_sprite_pivots('
new = '"bboxes": [list(bbox) if bbox is not None else None for bbox in bboxes],\n        "_frames_ref": frames,\n    }\n\n\ndef _stabilize_sprite_pivots('
if old not in content:
    print("NOT FOUND")
else:
    open(path, "w", encoding="utf-8").write(content.replace(old, new, 1))
    print("OK")
