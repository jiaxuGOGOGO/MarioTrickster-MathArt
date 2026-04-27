"""Rewrite the complete sprite pack loop with mask + pixelize support."""
import sys
path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

OLD_LOOP_BLOCK = '''    action = str(prepared.get("motion_state", "motion"))
    frames: list[Image.Image] = []
    frame_paths: list[str] = []
    for index, src in enumerate(source_paths):
        with Image.open(src) as img:
            cell = _fit_sprite_to_cell(img, cell_size)
        frame_path = frame_dir / f"{action}_{index:03d}.png"
        cell.save(frame_path)
        frames.append(cell)
        frame_paths.append(str(frame_path.resolve()))'''

NEW_LOOP_BLOCK = '''    action = str(prepared.get("motion_state", "motion"))
    frames: list[Image.Image] = []
    frame_paths: list[str] = []

    # Load per-frame masks: guide masks for guide fallback, baked masks for AI output
    guide_mask_paths: list[Path] = []
    if source_kind == "guide_baking_albedo":
        guide_mask_paths = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]
    baked_mask_paths: list[Path] = guide_mask_paths if source_kind == "guide_baking_albedo" else [
        Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()
    ]
    use_baked_masks = len(baked_mask_paths) >= len(source_paths)
    palette_colors = int((asset_spec.get("style_policy") or {}).get("palette_color_count", 16))

    for index, src in enumerate(source_paths):
        with Image.open(src) as raw_img:
            raw_rgba = raw_img.convert("RGBA")
        # Apply baked silhouette mask to restore transparent background
        if use_baked_masks:
            with Image.open(baked_mask_paths[index % len(baked_mask_paths)]) as mask_img:
                raw_rgba = _apply_mask_to_ai_frame(raw_rgba, mask_img)
        else:
            raw_rgba = _apply_mask_to_ai_frame(raw_rgba, None)
        # Fit to sprite cell and quantize to pixel-art palette
        cell = _fit_sprite_to_cell(raw_rgba, cell_size)
        cell = _pixelize_sprite_frame(cell, palette_colors=palette_colors, cell_size=cell_size)
        frame_path = frame_dir / f"{action}_{index:03d}.png"
        cell.save(frame_path)
        frames.append(cell)
        frame_paths.append(str(frame_path.resolve()))'''

if OLD_LOOP_BLOCK not in content:
    print("OLD_LOOP_BLOCK NOT FOUND")
    # show what's around action line
    idx = content.find('    action = str(prepared.get("motion_state", "motion"))')
    print("action line at:", idx)
    if idx >= 0:
        print(repr(content[idx:idx+400]))
    sys.exit(1)

content = content.replace(OLD_LOOP_BLOCK, NEW_LOOP_BLOCK, 1)

# Also ensure guide_mask_paths doesn't double-appear from FIX-D
# FIX-D added: guide_mask_paths after the "not source_paths" block
# Remove that duplicate since we now handle it in the loop section
dup = '\n\n    guide_mask_paths: list[Path] = []\n    if source_kind == "guide_baking_albedo":\n        guide_mask_paths = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]'
if dup in content:
    content = content.replace(dup, "", 1)
    print("Removed duplicate guide_mask_paths block")

open(path, "w", encoding="utf-8").write(content)
print("Loop rewritten.")

import subprocess
r = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
if r.returncode == 0:
    print("COMPILE OK")
else:
    print("COMPILE ERROR:", r.stderr[:500])
