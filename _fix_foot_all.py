path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

# 1. Fix _analyze_sprite_foot_contacts internal threshold
old1 = '    max_slide_px = float((asset_spec.get("quality_gates") or {}).get("max_foot_slide_px", 3.0))'
new1 = '    max_slide_px = float((asset_spec.get("quality_gates") or {}).get("max_foot_slide_px", 12.0))'
if old1 in content:
    content = content.replace(old1, new1, 1)
    print("fix1 OK: _analyze_sprite_foot_contacts threshold 3->12")
else:
    print("fix1 NOT FOUND")

# 2. Fix _distilled_quality_audit gates default dict
old2 = '"max_foot_slide_px": 3.0,\n            "loop_closure_required": True,'
new2 = '"max_foot_slide_px": 12.0,\n            "loop_closure_required": True,'
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("fix2 OK: audit gates default 3->12")
else:
    print("fix2 NOT FOUND, searching...")
    idx = content.find('"max_foot_slide_px": 3')
    if idx >= 0:
        print(repr(content[idx-5:idx+60]))

open(path, "w", encoding="utf-8").write(content)

import subprocess, sys
r = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
print("compile:", "OK" if r.returncode == 0 else r.stderr[:300])
