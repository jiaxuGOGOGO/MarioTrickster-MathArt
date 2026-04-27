import sys

path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

ANCHOR = '"contact_ratio", contact_ratio, "ge", max(0.05, desired_contact_ratio * 0.5))\n\n    failed = [check for check in checks if not check["passed"]]'

REPLACEMENT = '"contact_ratio", contact_ratio, "ge", max(0.05, desired_contact_ratio * 0.5))\n\n    # visual semantics gate 1: inter-frame MSE (static frames must fail)\n    frames_raw = list(sprite_motion_quality.get("_frames_ref") or [])\n    interframe_mse = 0.0\n    if len(frames_raw) >= 2:\n        try:\n            import numpy as _np_qa\n            arrs = [_np_qa.array(f.convert("RGB"), dtype=_np_qa.float32) for f in frames_raw]\n            mse_vals = [float(_np_qa.mean((arrs[i + 1] - arrs[i]) ** 2)) for i in range(len(arrs) - 1)]\n            interframe_mse = float(sum(mse_vals) / len(mse_vals)) if mse_vals else 0.0\n        except Exception:\n            interframe_mse = 0.0\n    _add_check("interframe_mse", interframe_mse, "ge", float(gates.get("min_interframe_mse", 10.0)), severity="hard")\n\n    # visual semantics gate 2: pixel-art color complexity (AI noise >> 48 quantized colors)\n    quant_colors_mean = 0.0\n    if frames_raw:\n        try:\n            import numpy as _np_qa\n            qc_vals = []\n            for f in frames_raw:\n                arr = _np_qa.array(f.convert("RGBA"), dtype=_np_qa.uint8)\n                mask = arr[:, :, 3] > 8\n                if not mask.any():\n                    continue\n                rgb = arr[:, :, :3][mask]\n                quant = (rgb.astype(_np_qa.int32) // 16).astype(_np_qa.uint8)\n                uniq = len({(int(r), int(g), int(b)) for r, g, b in quant.reshape(-1, 3)})\n                qc_vals.append(float(uniq))\n            quant_colors_mean = float(sum(qc_vals) / len(qc_vals)) if qc_vals else 0.0\n        except Exception:\n            quant_colors_mean = 0.0\n    _add_check("pixel_art_quant_colors", quant_colors_mean, "le", float(gates.get("pixel_art_max_quant_colors", 48.0)), severity="hard")\n\n    # visual semantics gate 3: inter-frame MSE variance (all-same frames supplemental)\n    interframe_mse_var = 0.0\n    if len(frames_raw) >= 3:\n        try:\n            import numpy as _np_qa\n            arrs = [_np_qa.array(f.convert("RGB"), dtype=_np_qa.float32) for f in frames_raw]\n            mse_vals_v = [float(_np_qa.mean((arrs[i + 1] - arrs[i]) ** 2)) for i in range(len(arrs) - 1)]\n            interframe_mse_var = float(_np_qa.var(_np_qa.array(mse_vals_v))) if mse_vals_v else 0.0\n        except Exception:\n            interframe_mse_var = 0.0\n    _add_check("interframe_mse_variance", interframe_mse_var, "ge", float(gates.get("min_interframe_mse_variance", 0.5)), severity="review")\n\n    failed = [check for check in checks if not check["passed"]]'

if ANCHOR not in content:
    print("ANCHOR NOT FOUND")
    sys.exit(1)

new_content = content.replace(ANCHOR, REPLACEMENT, 1)
open(path, "w", encoding="utf-8").write(new_content)
print("OK patched")
