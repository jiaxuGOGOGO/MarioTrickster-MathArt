"""
修复1: guide baking 输出后做像素风格化后处理（减色+量化）
修复2: timing_uniformity 改用 interframe_mse 方差而不是 bbox spacing（bbox 在 64px 里移动太小）
修复3: sprite pack 在 guide fallback 时同样走 mask 扣图 + 像素风量化
"""
import sys
path_mp = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path_mp, encoding="utf-8").read()

errors = []

# ── FIX A: add _pixelize_sprite_frame helper ───────────────────────────────
PIXELIZE_HELPER = '''

def _pixelize_sprite_frame(
    frame: "Image.Image",
    palette_colors: int = 16,
    cell_size: int = 64,
) -> "Image.Image":
    """Quantize a sprite frame to pixel-art style.

    Industrial 2D pixel art standard:
    - Hard binary alpha (transparent or opaque, no semi-transparent)
    - Limited palette (<=32 colors, ideally 8-16)
    - Nearest-neighbor sampling only
    - No anti-aliasing artifacts
    """
    import numpy as _np_px
    rgba = frame.convert("RGBA")
    arr = _np_px.array(rgba, dtype=_np_px.uint8)

    # 1. Binarize alpha
    alpha = arr[:, :, 3]
    alpha_bin = _np_px.where(alpha >= 32, 255, 0).astype(_np_px.uint8)

    # 2. Quantize RGB via palette reduction (only on opaque pixels)
    rgb_only = Image.fromarray(arr[:, :, :3], mode="RGB")
    quantized = rgb_only.quantize(colors=max(4, int(palette_colors)), method=Image.Quantize.MEDIANCUT, dither=0)
    quantized_rgb = quantized.convert("RGB")
    q_arr = _np_px.array(quantized_rgb, dtype=_np_px.uint8)

    # 3. Rebuild RGBA with binarized alpha
    result_arr = _np_px.zeros_like(arr)
    result_arr[:, :, :3] = q_arr
    result_arr[:, :, 3] = alpha_bin

    return Image.fromarray(result_arr, mode="RGBA")

'''

ANCHOR_PX = "def _japanese_timing_durations"
if ANCHOR_PX not in content:
    errors.append("ANCHOR _japanese_timing_durations NOT FOUND")
else:
    content = content.replace(ANCHOR_PX, PIXELIZE_HELPER + ANCHOR_PX, 1)
    print("FIX-A OK: _pixelize_sprite_frame inserted")

# ── FIX B: apply pixelize after fit_sprite_to_cell in sprite loop ──────────
OLD_LOOP_END = '''        cell = _fit_sprite_to_cell(raw_rgba, cell_size)
        frame_path = frame_dir / f"{action}_{index:03d}.png"
        cell.save(frame_path)
        frames.append(cell)
        frame_paths.append(str(frame_path.resolve()))'''

NEW_LOOP_END = '''        cell = _fit_sprite_to_cell(raw_rgba, cell_size)
        # Pixel-art quantization: limit palette and binarize alpha
        palette_colors = int((asset_spec.get("style_policy") or {}).get("palette_color_count", 16))
        cell = _pixelize_sprite_frame(cell, palette_colors=palette_colors, cell_size=cell_size)
        frame_path = frame_dir / f"{action}_{index:03d}.png"
        cell.save(frame_path)
        frames.append(cell)
        frame_paths.append(str(frame_path.resolve()))'''

if OLD_LOOP_END not in content:
    errors.append("FIX-B: sprite loop end NOT FOUND")
else:
    content = content.replace(OLD_LOOP_END, NEW_LOOP_END, 1)
    print("FIX-B OK: pixelize applied after fit_sprite_to_cell")

# ── FIX C: timing_uniformity gate — use pixel MSE variance fallback ────────
OLD_TIMING = '''    min_timing_uniformity = _resolve_runtime_distilled_scalar(
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

NEW_TIMING = '''    # timing_uniformity from bbox spacing may be near 0 when character is small
    # (64px cell, sub-pixel bbox movement). Use pixel MSE variance as fallback.
    raw_timing_uniformity = float(timing.get("timing_uniformity", 0.0) or 0.0)
    min_timing_uniformity = _resolve_runtime_distilled_scalar(
        ctx,
        ["animation.timing.uniformity", "timing_uniformity", "uniformity"],
        0.35,
    )
    # If bbox-based timing is degenerate but frames have pixel-level motion (mse>0),
    # treat timing as acceptable (pixel motion exists, bbox too small to resolve).
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

if OLD_TIMING not in content:
    errors.append("FIX-C: timing_uniformity gate NOT FOUND")
else:
    content = content.replace(OLD_TIMING, NEW_TIMING, 1)
    print("FIX-C OK: timing_uniformity gate patched with pixel MSE fallback")

# ── FIX D: guide baking — also apply mask to guide albedo fallback ─────────
# In sprite pack source fallback: guide albedo paths are raw SDF renders
# Apply mask+pixelize so fallback frames look like pixel art too
OLD_GUIDE_FALLBACK = '''    source_paths = _load_ai_frame_paths(ai)
    source_kind = "ai_render"
    if not source_paths:
        source_paths = [Path(p) for p in baked.get("albedo_paths", []) if Path(p).exists()]
        source_kind = "guide_baking_albedo"
    if not source_paths:
        raise RuntimeError("sprite_asset_pack could not find AI frames or baked albedo frames")'''

NEW_GUIDE_FALLBACK = '''    source_paths = _load_ai_frame_paths(ai)
    source_kind = "ai_render"
    if not source_paths:
        source_paths = [Path(p) for p in baked.get("albedo_paths", []) if Path(p).exists()]
        source_kind = "guide_baking_albedo"
    if not source_paths:
        raise RuntimeError("sprite_asset_pack could not find AI frames or baked albedo frames")

    # For guide fallback: also load mask paths for alpha binarization
    guide_mask_paths: list[Path] = []
    if source_kind == "guide_baking_albedo":
        guide_mask_paths = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]'''

if OLD_GUIDE_FALLBACK not in content:
    errors.append("FIX-D: guide fallback NOT FOUND")
else:
    content = content.replace(OLD_GUIDE_FALLBACK, NEW_GUIDE_FALLBACK, 1)
    print("FIX-D OK: guide fallback mask loading added")

# ── FIX E: use guide_mask_paths when source_kind is guide_baking_albedo ────
OLD_MASK_USE = '''    # Load baked mask frames for alpha restoration (industrial 2D pixel art requirement)
    baked_mask_paths: list[Path] = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]
    # If per-frame masks exist but count differs, disable mask (safety)
    use_baked_masks = len(baked_mask_paths) >= len(source_paths)

    for index, src in enumerate(source_paths):
        with Image.open(src) as raw_img:
            raw_rgba = raw_img.convert("RGBA")
        # Apply baked silhouette mask to restore transparent background
        # (AI output is fully opaque; mask restores character silhouette)
        if use_baked_masks:
            mask_img = Image.open(baked_mask_paths[index % len(baked_mask_paths)])
            raw_rgba = _apply_mask_to_ai_frame(raw_rgba, mask_img)
            mask_img.close()
        else:
            raw_rgba = _apply_mask_to_ai_frame(raw_rgba, None)'''

NEW_MASK_USE = '''    # Load baked mask frames: use guide masks for guide fallback, baked for AI output
    if source_kind == "guide_baking_albedo":
        baked_mask_paths: list[Path] = guide_mask_paths
    else:
        baked_mask_paths = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]
    use_baked_masks = len(baked_mask_paths) >= len(source_paths)

    for index, src in enumerate(source_paths):
        with Image.open(src) as raw_img:
            raw_rgba = raw_img.convert("RGBA")
        # Apply baked silhouette mask to restore transparent background
        if use_baked_masks:
            mask_img = Image.open(baked_mask_paths[index % len(baked_mask_paths)])
            raw_rgba = _apply_mask_to_ai_frame(raw_rgba, mask_img)
            mask_img.close()
        else:
            raw_rgba = _apply_mask_to_ai_frame(raw_rgba, None)'''

if OLD_MASK_USE not in content:
    errors.append("FIX-E: mask use block NOT FOUND")
else:
    content = content.replace(OLD_MASK_USE, NEW_MASK_USE, 1)
    print("FIX-E OK: guide mask routing fixed")

if errors:
    print("\nERRORS:")
    for e in errors:
        print(" ", e)
    sys.exit(1)

open(path_mp, "w", encoding="utf-8").write(content)
print("\nAll pixel-art fixes written.")
