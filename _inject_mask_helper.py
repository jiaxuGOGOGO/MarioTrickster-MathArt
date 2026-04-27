path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

MASK_HELPER = '''

def _apply_mask_to_ai_frame(
    ai_frame: "Image.Image",
    mask_frame: "Image.Image | None",
    *,
    threshold: int = 16,
) -> "Image.Image":
    """Apply baked silhouette mask to AI-rendered frame to restore transparent background.

    Industrial 2D pixel art requires clean transparent background.
    ComfyUI AnimateDiff outputs full-opaque RGBA (alpha=255 everywhere).
    We use the upstream CPU-baked mask to restore the character silhouette.
    """
    import numpy as _np_m
    ai_rgba = ai_frame.convert("RGBA")
    w, h = ai_rgba.size
    if mask_frame is not None:
        mask_resized = mask_frame.convert("L").resize((w, h), Image.Resampling.NEAREST)
        mask_arr = _np_m.array(mask_resized, dtype=_np_m.uint8)
        alpha_arr = _np_m.where(mask_arr >= threshold, 255, 0).astype(_np_m.uint8)
    else:
        arr = _np_m.array(ai_rgba, dtype=_np_m.uint8)
        rgb_sum = arr[:, :, :3].astype(_np_m.int32).sum(axis=2)
        alpha_arr = _np_m.where(rgb_sum > threshold * 3, 255, 0).astype(_np_m.uint8)
    ai_arr = _np_m.array(ai_rgba, dtype=_np_m.uint8).copy()
    ai_arr[:, :, 3] = alpha_arr
    return Image.fromarray(ai_arr, mode="RGBA")

'''

ANCHOR = "def _japanese_timing_durations"
if ANCHOR not in content:
    print("ANCHOR NOT FOUND")
else:
    content = content.replace(ANCHOR, MASK_HELPER + ANCHOR, 1)
    open(path, "w", encoding="utf-8").write(content)
    # verify
    if "def _apply_mask_to_ai_frame" in open(path, encoding="utf-8").read():
        print("OK: _apply_mask_to_ai_frame injected")
    else:
        print("FAIL: still not found")
