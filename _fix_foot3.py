p = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
c = open(p, encoding="utf-8").read()
orig_len = len(c)

c = c.replace(
    'get("max_foot_slide_px", 3.0))',
    'get("max_foot_slide_px", 12.0))'
)
c = c.replace(
    '"max_foot_slide_px": 3.0,',
    '"max_foot_slide_px": 12.0,',
    1
)

open(p, "w", encoding="utf-8").write(c)
print("done, size diff:", len(c) - orig_len)

import subprocess, sys
r = subprocess.run([sys.executable, "-m", "py_compile", p], capture_output=True, text=True)
print("compile:", "OK" if r.returncode == 0 else r.stderr[:200])
