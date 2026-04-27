path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

PIXELIZE_DEF = '''

def _pixelize_sprite_frame(frame, palette_colors=16, cell_size=64):
    """Quantize sprite frame to pixel-art style: limited palette, binary alpha."""
    import numpy as _np_px
    rgba = frame.convert("RGBA")
    arr = _np_px.array(rgba, dtype=_np_px.uint8)
    alpha_bin = _np_px.where(arr[:,:,3] >= 32, 255, 0).astype(_np_px.uint8)
    rgb_img = Image.fromarray(arr[:,:,:3], mode="RGB")
    quantized = rgb_img.quantize(colors=max(4, int(palette_colors)), method=Image.Quantize.MEDIANCUT, dither=0)
    q_arr = _np_px.array(quantized.convert("RGB"), dtype=_np_px.uint8)
    result = _np_px.zeros_like(arr)
    result[:,:,:3] = q_arr
    result[:,:,3] = alpha_bin
    return Image.fromarray(result, mode="RGBA")

'''

# Check if the def already exists
if "def _pixelize_sprite_frame" in content:
    print("already defined")
else:
    anchor = "def _japanese_timing_durations"
    if anchor in content:
        content = content.replace(anchor, PIXELIZE_DEF + anchor, 1)
        open(path, "w", encoding="utf-8").write(content)
        print("inserted _pixelize_sprite_frame")
    else:
        print("anchor not found")

import subprocess, sys
r = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
print("compile:", "OK" if r.returncode == 0 else r.stderr[:300])
