from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mathart.evolution.evolution_loop import generate_evolution_report, save_evolution_report


def main() -> None:
    report = generate_evolution_report(ROOT, session_id="SESSION-044", cycle_id="CYCLE-SESSION044")
    out_path = ROOT / "evolution_reports" / "CYCLE-SESSION044.json"
    save_evolution_report(report, out_path)


if __name__ == "__main__":
    main()
