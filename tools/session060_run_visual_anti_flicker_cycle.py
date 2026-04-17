from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mathart.animation.character_presets import get_preset
from mathart.animation.presets import idle_animation
from mathart.animation.skeleton import Skeleton
from mathart.evolution.breakwall_evolution_bridge import BreakwallEvolutionBridge


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    style, _palette = get_preset("mario")
    skeleton = Skeleton.create_humanoid()
    bridge = BreakwallEvolutionBridge(project_root=repo_root, verbose=False)

    metrics = bridge.evaluate_full(
        skeleton=skeleton,
        animation_func=idle_animation,
        style=style,
        pose=idle_animation(0.0),
        frames=4,
        width=64,
        height=64,
        warp_error_threshold=0.15,
    )
    rules = bridge.distill_knowledge(metrics)
    fitness_bonus = bridge.compute_fitness_bonus(metrics)
    auto_tune = bridge.auto_tune_parameters()

    payload = {
        "session_id": "SESSION-060",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bridge": "BreakwallEvolutionBridge",
        "phase": "Phase 2 industrial anti-flicker",
        "metrics": metrics.to_dict(),
        "rules_count": len(rules),
        "rules": rules,
        "fitness_bonus": fitness_bonus,
        "auto_tune": auto_tune,
        "status_report": bridge.status_report(),
    }

    out_dir = repo_root / "evolution_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "session060_visual_anti_flicker_cycle.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
