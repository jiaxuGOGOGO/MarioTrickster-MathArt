from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mathart.evolution.evolution_orchestrator import EvolutionOrchestrator
from mathart.evolution.unity_urp_2d_bridge import UnityURP2DEvolutionBridge


def main() -> None:
    root = ROOT
    out_dir = root / "evolution_reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    unity_bridge = UnityURP2DEvolutionBridge(project_root=root, verbose=False)
    metrics, knowledge_path, bonus = unity_bridge.run_full_cycle()

    orchestrator = EvolutionOrchestrator(project_root=root, verbose=False)
    report = orchestrator.run_full_cycle()

    payload = {
        "unity_bridge": {
            "metrics": metrics.to_dict(),
            "knowledge_path": str(knowledge_path.relative_to(root)),
            "bonus": bonus,
        },
        "orchestrator": report.to_dict(),
    }
    (out_dir / "session059_runtime_cycle.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
