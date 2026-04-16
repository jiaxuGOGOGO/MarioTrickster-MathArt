from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mathart.evolution.engine import SelfEvolutionEngine


def main() -> None:
    project_root = PROJECT_ROOT
    engine = SelfEvolutionEngine(project_root=project_root, verbose=True)
    result = engine.run_transition_closed_loop(
        source_state="run",
        target_state="jump",
        source_phase=0.8,
        n_trials=24,
    )
    print("\n[SESSION-043] Closed loop result")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
