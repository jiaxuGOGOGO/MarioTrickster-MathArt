from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mathart.evolution.state_machine_coverage_bridge import StateMachineCoverageBridge


def main() -> None:
    project_root = PROJECT_ROOT
    bridge = StateMachineCoverageBridge(project_root=project_root)
    result = bridge.run_cycle(random_walk_steps=24, seed=51)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
