path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()
old = '    foot_slide_threshold = float(gates.get("max_foot_slide_px", 8.0) or 8.0)'
new = '    foot_slide_threshold = float(gates.get("max_foot_slide_px", 12.0) or 12.0)'
if old in content:
    content = content.replace(old, new, 1)
    open(path, "w", encoding="utf-8").write(content)
    print("foot threshold 8->12 OK")
else:
    print("NOT FOUND:", repr(old[:60]))

import subprocess, sys
r = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True, text=True)
print("compile:", "OK" if r.returncode == 0 else r.stderr[:300])
