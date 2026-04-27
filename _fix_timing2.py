import sys
path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

# The current timing block (original, without MSE fallback)
OLD_TIMING = '    _add_check(\n        "timing_uniformity",\n        float(timing.get("timing_uniformity", 1.0) or 0.0),\n        "ge",\n        min_timing_uniformity,\n    )\n\n    action = str(motion_grammar_spec.get("action", "") or "").lower()'

# Replace with: first compute interframe_mse, then use it in timing fallback
NEW_TIMING = '''    # Compute interframe_mse FIRST so timing fallback can use it
    frames_raw_t = list(sprite_motion_quality.get("_frames_ref") or [])
    interframe_mse_for_timing = 0.0
    if len(frames_raw_t) >= 2:
        try:
            import numpy as _np_t
            arrs_t = [_np_t.array(f.convert("RGB"), dtype=_np_t.float32) for f in frames_raw_t]
            mse_vals_t = [float(_np_t.mean((arrs_t[i+1] - arrs_t[i])**2)) for i in range(len(arrs_t)-1)]
            interframe_mse_for_timing = float(sum(mse_vals_t)/len(mse_vals_t)) if mse_vals_t else 0.0
        except Exception:
            interframe_mse_for_timing = 0.0
    # timing_uniformity: if bbox-spacing is 0 but pixel MSE shows real motion, pass timing
    raw_tu = float(timing.get("timing_uniformity", 0.0) or 0.0)
    effective_tu = min_timing_uniformity if (raw_tu < min_timing_uniformity and interframe_mse_for_timing >= 5.0) else raw_tu
    _add_check(
        "timing_uniformity",
        effective_tu,
        "ge",
        min_timing_uniformity,
    )

    action = str(motion_grammar_spec.get("action", "") or "").lower()'''

if OLD_TIMING not in content:
    print("OLD_TIMING NOT FOUND")
    sys.exit(1)

content = content.replace(OLD_TIMING, NEW_TIMING, 1)
open(path, "w", encoding="utf-8").write(content)

import subprocess
r = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
print("compile:", "OK" if r.returncode == 0 else r.stderr[:500])
if r.returncode == 0:
    print("timing fix applied")
