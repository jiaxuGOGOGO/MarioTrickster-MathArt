from __future__ import annotations

import json
from pathlib import Path

from mathart.evolution.jakobsen_bridge import (
    JakobsenEvolutionBridge,
    collect_jakobsen_chain_status,
)
from mathart.pipeline import AssetPipeline, CharacterSpec


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "evolution_reports" / "session047_gapb1_output"
AUDIT_JSON = PROJECT_ROOT / "evolution_reports" / "session047_gapb1_audit.json"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pipeline = AssetPipeline(output_dir=str(OUTPUT_DIR))
    spec = CharacterSpec(
        name="session047_gapb1_character",
        preset="mario",
        states=["idle", "run"],
        frames_per_state=6,
        fps=10,
        enable_secondary_chains=True,
        secondary_chain_presets=["cape", "hair"],
    )
    result = pipeline.produce_character_pack(spec)

    asset_dir = OUTPUT_DIR / spec.name
    state_meta = result.metadata["states"]["run"]
    umr_path = asset_dir / state_meta["motion_bus"]["file"]
    umr_payload = json.loads(umr_path.read_text(encoding="utf-8"))

    diagnostics = [
        frame.get("metadata", {}).get("secondary_chain_debug", {})
        for frame in umr_payload.get("frames", [])
        if frame.get("metadata", {}).get("secondary_chain_debug")
    ]

    bridge = JakobsenEvolutionBridge(PROJECT_ROOT, verbose=False)
    metrics = bridge.evaluate_secondary_chains(diagnostics)
    rules = bridge.distill_secondary_chain_knowledge(metrics)
    fitness_bonus = bridge.compute_secondary_chain_fitness_bonus(metrics)
    status = collect_jakobsen_chain_status(PROJECT_ROOT)

    payload = {
        "session": "SESSION-047",
        "gap": "B1",
        "character_asset_dir": str(asset_dir.relative_to(PROJECT_ROOT)),
        "umr_path": str(umr_path.relative_to(PROJECT_ROOT)),
        "diagnostic_frames": len(diagnostics),
        "metrics": metrics.to_dict(),
        "rules": rules,
        "fitness_bonus": fitness_bonus,
        "status": status.to_dict(),
        "result_metadata_excerpt": {
            "secondary_chain_config": result.metadata["character"].get("secondary_chain_config", {}),
            "states": list(result.metadata.get("states", {}).keys()),
        },
    }
    AUDIT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(AUDIT_JSON)


if __name__ == "__main__":
    main()
