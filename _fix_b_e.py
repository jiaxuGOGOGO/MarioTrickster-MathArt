path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

# Fix B: pixelize after fit
old_b = '            cell = _fit_sprite_to_cell(img, cell_size)\n        frame_path = frame_dir / f"{action}_{index:03d}.png"\n        cell.save(frame_path)\n        frames.append(cell)\n        frame_paths.append(str(frame_path.resolve()))'
new_b = '            cell = _fit_sprite_to_cell(img, cell_size)\n        # Pixel-art quantization: limit palette, binarize alpha\n        palette_colors = int((asset_spec.get("style_policy") or {}).get("palette_color_count", 16))\n        cell = _pixelize_sprite_frame(cell, palette_colors=palette_colors, cell_size=cell_size)\n        frame_path = frame_dir / f"{action}_{index:03d}.png"\n        cell.save(frame_path)\n        frames.append(cell)\n        frame_paths.append(str(frame_path.resolve()))'

if old_b not in content:
    print("FIX-B NOT FOUND")
    idx = content.rfind('_fit_sprite_to_cell')
    print(repr(content[idx-5:idx+300]))
else:
    content = content.replace(old_b, new_b, 1)
    print("FIX-B OK")

# Fix E: mask routing - find actual text
idx_mask = content.find('use_baked_masks = len(baked_mask_paths) >= len(source_paths)')
print("mask block idx:", idx_mask)
print(repr(content[idx_mask-200:idx_mask+300]))
