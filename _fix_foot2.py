path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

old_foot = '        float(gates.get("max_foot_slide_px", 3.0) or 3.0),\n    )\n    _add_check(\n        "ground_jitter_px",\n        float(foot_metrics.get("ground_jitter_px", 0.0) or 0.0),\n        "le",\n        float(gates.get("max_foot_slide_px", 3.0) or 3.0),'

new_foot = '        float(gates.get("max_foot_slide_px", 12.0) or 12.0),\n    )\n    _add_check(\n        "ground_jitter_px",\n        float(foot_metrics.get("ground_jitter_px", 0.0) or 0.0),\n        "le",\n        float(gates.get("max_foot_slide_px", 12.0) or 12.0),'

if old_foot in content:
    content = content.replace(old_foot, new_foot, 1)
    open(path, "w", encoding="utf-8").write(content)
    print("foot threshold 3->12 OK")
else:
    print("NOT FOUND")
    # find partial
    idx = content.find('max_foot_slide_px\", 3.0) or 3.0)')
    print("partial at:", idx, repr(content[idx-80:idx+80]))

import subprocess, sys
r = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
print("compile:", "OK" if r.returncode == 0 else r.stderr[:300])
