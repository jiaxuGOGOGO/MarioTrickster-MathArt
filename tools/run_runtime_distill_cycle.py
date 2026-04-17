from __future__ import annotations

import json
from pathlib import Path

from mathart.evolution.runtime_distill_bridge import RuntimeDistillBridge


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    bridge = RuntimeDistillBridge(project_root=root, verbose=True)
    result = bridge.run_cycle()
    print(json.dumps(result, ensure_ascii=False, indent=2))
