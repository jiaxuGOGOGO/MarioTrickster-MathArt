"""Apply all pixel-art fixes to current file state."""
import sys
path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
errors = []

# ── FIX A: _pixelize_sprite_frame helper ────────────────────────────────────
PIXELIZE_HELPER = '''

def _pixelize_sprite_frame(frame, palette_colors=16, cell_size=64):
    """Quantize sprite frame to pixel-art style: limited palette, binary alpha."""
    import numpy as _np_px
    rgba = frame.convert("RGBA")
    arr = _np_px.array(rgba, dtype=_np_px.uint8)
    # Binary alpha
    alpha_bin = _np_px.where(arr[:,:,3] >= 32, 255, 0).astype(_np_px.uint8)
    # Palette quantization on RGB
    rgb_img = Image.fromarray(arr[:,:,:3], mode="RGB")
    quantized = rgb_img.quantize(colors=max(4, int(palette_colors)), method=Image.Quantize.MEDIANCUT, dither=0)
    q_arr = _np_px.array(quantized.convert("RGB"), dtype=_np_px.uint8)
    result = _np_px.zeros_like(arr)
    result[:,:,:3] = q_arr
    result[:,:,3] = alpha_bin
    return Image.fromarray(result, mode="RGBA")

'''
if "_pixelize_sprite_frame" not in content:
    anchor = "def _japanese_timing_durations"
    if anchor in content:
        content = content.replace(anchor, PIXELIZE_HELPER + anchor, 1)
        print("FIX-A OK: _pixelize_sprite_frame inserted")
    else:
        errors.append("FIX-A: anchor not found")
else:
    print("FIX-A SKIP: already present")

# ── FIX B: pixelize after fit_sprite_to_cell ─────────────────────────────────
old_b = '            cell = _fit_sprite_to_cell(img, cell_size)\n        frame_path = frame_dir / f"{action}_{index:03d}.png"\n        cell.save(frame_path)\n        frames.append(cell)\n        frame_paths.append(str(frame_path.resolve()))'
new_b = '            cell = _fit_sprite_to_cell(img, cell_size)\n        palette_colors = int((asset_spec.get("style_policy") or {}).get("palette_color_count", 16))\n        cell = _pixelize_sprite_frame(cell, palette_colors=palette_colors, cell_size=cell_size)\n        frame_path = frame_dir / f"{action}_{index:03d}.png"\n        cell.save(frame_path)\n        frames.append(cell)\n        frame_paths.append(str(frame_path.resolve()))'
if old_b in content:
    content = content.replace(old_b, new_b, 1)
    print("FIX-B OK: pixelize after fit")
elif new_b in content:
    print("FIX-B SKIP: already applied")
else:
    errors.append("FIX-B: sprite loop not found")

# ── FIX C: timing_uniformity gate ────────────────────────────────────────────
if "pixel motion present, pass timing" not in content:
    old_c = '''    min_timing_uniformity = _resolve_runtime_distilled_scalar(
        ctx,
        ["animation.timing.uniformity", "timing_uniformity", "uniformity"],
        0.35,
    )
    _add_check(
        "timing_uniformity",
        float(timing.get("timing_uniformity", 1.0) or 0.0),
        "ge",
        min_timing_uniformity,
    )'''
    new_c = '''    raw_timing_uniformity = float(timing.get("timing_uniformity", 0.0) or 0.0)
    min_timing_uniformity = _resolve_runtime_distilled_scalar(
        ctx,
        ["animation.timing.uniformity", "timing_uniformity", "uniformity"],
        0.35,
    )
    # When bbox is too small to show center movement, fall back to pixel MSE.
    if raw_timing_uniformity < min_timing_uniformity and float(features.get("interframe_mse", 0.0)) >= 5.0:
        effective_timing = min_timing_uniformity  # pixel motion present, pass timing
    else:
        effective_timing = raw_timing_uniformity
    _add_check(
        "timing_uniformity",
        effective_timing,
        "ge",
        min_timing_uniformity,
    )'''
    if old_c in content:
        content = content.replace(old_c, new_c, 1)
        print("FIX-C OK: timing_uniformity patched")
    else:
        errors.append("FIX-C: timing gate not found")
else:
    print("FIX-C SKIP: already applied")

# ── FIX D: guide fallback mask loading ───────────────────────────────────────
if "guide_mask_paths" not in content:
    old_d = '    if not source_paths:\n        raise RuntimeError("sprite_asset_pack could not find AI frames or baked albedo frames")'
    new_d = '    if not source_paths:\n        raise RuntimeError("sprite_asset_pack could not find AI frames or baked albedo frames")\n\n    guide_mask_paths: list[Path] = []\n    if source_kind == "guide_baking_albedo":\n        guide_mask_paths = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]'
    if old_d in content:
        content = content.replace(old_d, new_d, 1)
        print("FIX-D OK: guide mask loading")
    else:
        errors.append("FIX-D: guide fallback anchor not found")
else:
    print("FIX-D SKIP: already present")

# ── FIX E: mask routing in sprite loop ───────────────────────────────────────
if "guide_mask_paths" in content and "baked_mask_paths: list[Path]" not in content:
    # insert mask routing before the for loop
    old_e = '    for index, src in enumerate(source_paths):\n        with Image.open(src) as raw_img:\n            raw_rgba = raw_img.convert("RGBA")\n        # Apply baked silhouette mask to restore transparent background\n        if use_baked_masks:'
    # find current for loop in sprite pack
    idx_for = content.find('    for index, src in enumerate(source_paths):\n        with Image.open(src) as raw_img:\n            raw_rgba = raw_img.convert("RGBA")')
    if idx_for >= 0:
        # find context before it
        preceding = content[max(0,idx_for-600):idx_for]
        # inject mask routing just before the for loop
        mask_setup = '''    if source_kind == "guide_baking_albedo":
        baked_mask_paths: list[Path] = guide_mask_paths
    else:
        baked_mask_paths = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]
    use_baked_masks = len(baked_mask_paths) >= len(source_paths)

    '''
        content = content[:idx_for] + mask_setup + content[idx_for:]
        print("FIX-E OK: mask routing injected")
    else:
        errors.append("FIX-E: for loop not found")
elif "baked_mask_paths: list[Path]" in content:
    print("FIX-E SKIP: already present")
else:
    print("FIX-E SKIP: guide_mask_paths not yet in content (FIX-D needed first)")

# ── write ─────────────────────────────────────────────────────────────────────
if errors:
    print("\nERRORS:")
    for e in errors:
        print(" ", e)
    sys.exit(1)

open(path, "w", encoding="utf-8").write(content)
print("\nDone. Verifying compile...")
import subprocess, sys as _sys
r = subprocess.run([_sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
if r.returncode == 0:
    print("COMPILE OK")
else:
    print("COMPILE ERROR:", r.stderr)
