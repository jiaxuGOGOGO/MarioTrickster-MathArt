import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from mathart.webui.bridge import WebUIBridge

log_path = PROJECT_ROOT / 'artifacts' / 'formal_webui_run_smoke' / 'events.jsonl'
log_path.parent.mkdir(parents=True, exist_ok=True)
b = WebUIBridge(project_root=PROJECT_ROOT)
b.PRODUCTION_TIMEOUT_SECONDS = 300.0
with log_path.open('w', encoding='utf-8') as f:
    for e in b.dispatch_render(
        action_name='run',
        reference_image=None,
        force_fluid=False,
        force_physics=False,
        force_cloth=False,
        force_particles=False,
        raw_vibe='pixel art humanoid runner, side view, clear run cycle',
    ):
        f.write(json.dumps(e, ensure_ascii=False) + '\n')
        f.flush()
print(str(log_path))
