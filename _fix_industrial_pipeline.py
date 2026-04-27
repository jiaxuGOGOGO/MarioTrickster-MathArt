"""
工业级2D像素资产生产链路修复脚本
修复点：
1. _load_ai_frame_paths: 支持 character_000_frames/frame_*.png 格式
2. _apply_mask_to_ai_frames: 新函数，用 baked mask 扣 AI 输出的 alpha
3. _node_sprite_asset_pack: 在 fit_sprite_to_cell 前先用 mask 扣图
"""
import sys
path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
errors = []

# ── FIX 1: _load_ai_frame_paths ── 支持 character_000_frames/frame_*.png ──
OLD_LOAD_AI = '''def _load_ai_frame_paths(ai_item: dict[str, Any]) -> list[Path]:
    manifest_path = ai_item.get("manifest_path")
    if manifest_path and Path(manifest_path).exists():
        manifest = _load_manifest(manifest_path)
        paths: list[Path] = []
        for key, value in sorted(manifest.outputs.items()):
            if key.startswith("frame_") and isinstance(value, str):
                p = Path(value)
                if p.is_file():
                    paths.append(p.resolve())
        if paths:
            return paths
    return []'''

NEW_LOAD_AI = '''def _load_ai_frame_paths(ai_item: dict[str, Any]) -> list[Path]:
    manifest_path = ai_item.get("manifest_path")
    if manifest_path and Path(manifest_path).exists():
        manifest = _load_manifest(manifest_path)
        # Priority 1: manifest outputs keyed frame_NNNN
        paths: list[Path] = []
        for key, value in sorted(manifest.outputs.items()):
            if key.startswith("frame_") and isinstance(value, str):
                p = Path(value)
                if p.is_file():
                    paths.append(p.resolve())
        if paths:
            return paths
        # Priority 2: sibling frames directory (character_000_frames/frame_*.png)
        mdir = Path(manifest_path).parent
        candidates = [
            mdir / "character_000_frames",
            mdir / "frames",
        ]
        # also scan subdirs that contain frame_*.png
        for sub in sorted(mdir.iterdir()):
            if sub.is_dir() and "frame" in sub.name.lower():
                candidates.append(sub)
        for cdir in candidates:
            if cdir.is_dir():
                frame_files = sorted(cdir.glob("frame_*.png"))
                if frame_files:
                    return [f.resolve() for f in frame_files]
        # Priority 3: any outputs that are png paths
        for key, value in sorted(manifest.outputs.items()):
            if isinstance(value, str) and value.endswith(".png"):
                p = Path(value)
                if p.is_file():
                    paths.append(p.resolve())
        if paths:
            return paths
    return []'''

if OLD_LOAD_AI not in content:
    errors.append("FIX1: _load_ai_frame_paths old_string NOT FOUND")
else:
    content = content.replace(OLD_LOAD_AI, NEW_LOAD_AI, 1)
    print("FIX1 OK: _load_ai_frame_paths patched")

# ── FIX 2: Add _apply_mask_to_ai_frame helper after _load_ai_frame_paths ──
MASK_HELPER = '''

def _apply_mask_to_ai_frame(
    ai_frame: "Image.Image",
    mask_frame: "Image.Image | None",
    *,
    threshold: int = 16,
) -> "Image.Image":
    """Apply baked silhouette mask to an AI-rendered frame.

    Industrial 2D pixel art requires a clean transparent background.
    ComfyUI AnimateDiff outputs full-opaque RGBA (alpha=255 everywhere).
    We must use the upstream CPU-baked mask (binary alpha) to restore
    the character silhouette and zero out background pixels.

    Parameters
    ----------
    ai_frame : PIL.Image
        The AI-rendered frame (likely 512x512 fully opaque).
    mask_frame : PIL.Image or None
        The baked mask from guide_baking_stage (grayscale or RGBA).
        If None, falls back to non-black pixel detection.
    threshold : int
        Pixels with mask value below this are treated as background.
    """
    ai_rgba = ai_frame.convert("RGBA")
    w, h = ai_rgba.size

    if mask_frame is not None:
        mask_resized = mask_frame.convert("L").resize((w, h), Image.Resampling.NEAREST)
        import numpy as _np_mask
        mask_arr = _np_mask.array(mask_resized, dtype=_np_mask.uint8)
        alpha_arr = _np_mask.where(mask_arr >= threshold, 255, 0).astype(_np_mask.uint8)
    else:
        import numpy as _np_mask
        arr = _np_mask.array(ai_rgba, dtype=_np_mask.uint8)
        rgb_sum = arr[:, :, :3].astype(_np_mask.int32).sum(axis=2)
        alpha_arr = _np_mask.where(rgb_sum > threshold * 3, 255, 0).astype(_np_mask.uint8)

    import numpy as _np_mask
    ai_arr = _np_mask.array(ai_rgba, dtype=_np_mask.uint8).copy()
    ai_arr[:, :, 3] = alpha_arr
    return Image.fromarray(ai_arr, mode="RGBA")

'''

ANCHOR_AFTER_LOAD_AI = "def _japanese_timing_durations"
if ANCHOR_AFTER_LOAD_AI not in content:
    errors.append("FIX2: anchor _japanese_timing_durations NOT FOUND")
else:
    content = content.replace(
        "def _japanese_timing_durations",
        MASK_HELPER + "def _japanese_timing_durations",
        1,
    )
    print("FIX2 OK: _apply_mask_to_ai_frame inserted")

# ── FIX 3: In _node_sprite_asset_pack, apply mask before fit_sprite_to_cell ──
OLD_SPRITE_LOOP = '''    for index, src in enumerate(source_paths):
        with Image.open(src) as img:
            cell = _fit_sprite_to_cell(img, cell_size)
        frame_path = frame_dir / f"{action}_{index:03d}.png"
        cell.save(frame_path)
        frames.append(cell)
        frame_paths.append(str(frame_path.resolve()))'''

NEW_SPRITE_LOOP = '''    # Load baked mask frames for alpha restoration (industrial 2D pixel art requirement)
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
            raw_rgba = _apply_mask_to_ai_frame(raw_rgba, None)
        cell = _fit_sprite_to_cell(raw_rgba, cell_size)
        frame_path = frame_dir / f"{action}_{index:03d}.png"
        cell.save(frame_path)
        frames.append(cell)
        frame_paths.append(str(frame_path.resolve()))'''

if OLD_SPRITE_LOOP not in content:
    errors.append("FIX3: sprite loop old_string NOT FOUND")
else:
    content = content.replace(OLD_SPRITE_LOOP, NEW_SPRITE_LOOP, 1)
    print("FIX3 OK: sprite loop patched with mask application")

# ── FIX 4: guide baking report must include mask_paths list ──
OLD_BAKING_REPORT_SECTION = '''    baking_report = {
        "character_id": prepared["character_id"],
        "session_id": _SESSION_ID,
        "stage": "guide_baking",
        "frame_count": len(source_frames),
        "render_width": render_width,
        "render_height": render_height,
        "guide_width": guide_width,
        "guide_height": guide_height,
        "channels": ["albedo", "normal", "depth", "mask"],
        "albedo_dir": str(albedo_dir.resolve()),
        "normal_dir": str(normal_dir.resolve()),
        "depth_dir": str(depth_dir.resolve()),
        "mask_dir": str(mask_dir.resolve()),
        "motion_state": prepared["motion_state"],  # SESSION-160: RenderContext
        "fps": int(prepared["fps"]),  # SESSION-160: RenderContext
        "cpu_only": True,
        "gpu_required": False,
        "temporal_variance_passed": temporal_variance_passed,
        "renderer": "render_character_maps_industrial (Catmull-Rom interpolated)",
    }'''

NEW_BAKING_REPORT_SECTION = '''    baking_report = {
        "character_id": prepared["character_id"],
        "session_id": _SESSION_ID,
        "stage": "guide_baking",
        "frame_count": len(source_frames),
        "render_width": render_width,
        "render_height": render_height,
        "guide_width": guide_width,
        "guide_height": guide_height,
        "channels": ["albedo", "normal", "depth", "mask"],
        "albedo_dir": str(albedo_dir.resolve()),
        "normal_dir": str(normal_dir.resolve()),
        "depth_dir": str(depth_dir.resolve()),
        "mask_dir": str(mask_dir.resolve()),
        "albedo_paths": albedo_paths,
        "normal_paths": normal_paths,
        "depth_paths": depth_paths,
        "mask_paths": mask_paths,
        "motion_state": prepared["motion_state"],  # SESSION-160: RenderContext
        "fps": int(prepared["fps"]),  # SESSION-160: RenderContext
        "cpu_only": True,
        "gpu_required": False,
        "temporal_variance_passed": temporal_variance_passed,
        "renderer": "render_character_maps_industrial (Catmull-Rom interpolated)",
    }'''

if OLD_BAKING_REPORT_SECTION not in content:
    errors.append("FIX4: baking_report old_string NOT FOUND")
else:
    content = content.replace(OLD_BAKING_REPORT_SECTION, NEW_BAKING_REPORT_SECTION, 1)
    print("FIX4 OK: guide baking report now includes mask_paths/albedo_paths")

# ── Also update the returned dict from guide_baking_stage to expose mask_paths ──
OLD_BAKE_RETURN = '''    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "report_path": str(report_path.resolve()),
        "frame_count": len(source_frames),
        "guide_width": guide_width,
        "guide_height": guide_height,
        "albedo_dir": str(albedo_dir.resolve()),
        "normal_dir": str(normal_dir.resolve()),
        "depth_dir": str(depth_dir.resolve()),
        "mask_dir": str(mask_dir.resolve()),
        "source_frames": source_frames,
        "normal_maps": normal_maps,
        "depth_maps": depth_maps,
        "mask_maps": mask_maps,'''

NEW_BAKE_RETURN = '''    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "report_path": str(report_path.resolve()),
        "frame_count": len(source_frames),
        "guide_width": guide_width,
        "guide_height": guide_height,
        "albedo_dir": str(albedo_dir.resolve()),
        "normal_dir": str(normal_dir.resolve()),
        "depth_dir": str(depth_dir.resolve()),
        "mask_dir": str(mask_dir.resolve()),
        "albedo_paths": albedo_paths,
        "normal_paths": normal_paths,
        "depth_paths": depth_paths,
        "mask_paths": mask_paths,
        "source_frames": source_frames,
        "normal_maps": normal_maps,
        "depth_maps": depth_maps,
        "mask_maps": mask_maps,'''

if OLD_BAKE_RETURN not in content:
    errors.append("FIX5: guide baking return old_string NOT FOUND")
else:
    content = content.replace(OLD_BAKE_RETURN, NEW_BAKE_RETURN, 1)
    print("FIX5 OK: guide_baking_stage return now includes mask_paths")

if errors:
    print("\nERRORS:")
    for e in errors:
        print(" ", e)
    sys.exit(1)

open(path, "w", encoding="utf-8").write(content)
print("\nAll fixes applied and written.")
