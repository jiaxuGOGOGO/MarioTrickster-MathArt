from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mathart.pipeline import AssetPipeline
from mathart.evolution.fluid_vfx_bridge import FluidVFXEvolutionBridge, collect_fluid_vfx_status
from mathart.evolution.engine import SelfEvolutionEngine


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "evolution_reports" / "session046_gapc2_audit_assets"
    out_dir.mkdir(parents=True, exist_ok=True)

    pipeline = AssetPipeline(output_dir=str(out_dir))
    produced = {}
    bridge = FluidVFXEvolutionBridge(root, verbose=False)

    for idx, preset in enumerate(["smoke_fluid", "dash_smoke", "slash_smoke"]):
        result = pipeline.produce_vfx(
            name=preset,
            preset=preset,
            canvas_size=48,
            n_frames=8,
            seed=46 + idx,
        )
        produced[preset] = {
            "score": result.score,
            "frame_count": len(result.frames or []),
            "output_paths": result.output_paths,
            "metadata": result.metadata,
        }
        if preset == "dash_smoke":
            diagnostics = result.metadata.get("frames", [])
            metrics = bridge.evaluate_fluid_vfx(
                frames=[np.asarray(frame) for frame in (result.frames or [])],
                diagnostics=diagnostics,
            )
            rules = bridge.distill_fluid_knowledge(metrics)
            produced[preset]["bridge_metrics"] = metrics.to_dict()
            produced[preset]["bridge_rules"] = rules
            produced[preset]["bridge_bonus"] = bridge.compute_fluid_fitness_bonus(metrics)

    status = collect_fluid_vfx_status(root).to_dict()
    engine_status = SelfEvolutionEngine(project_root=root, verbose=False).status()

    payload = {
        "session_id": "SESSION-046",
        "goal": "Gap C2 Stable Fluids VFX audit",
        "produced": produced,
        "fluid_vfx_status": status,
        "engine_status": engine_status,
    }
    audit_path = root / "evolution_reports" / "session046_gapc2_audit.json"
    audit_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(audit_path)


if __name__ == "__main__":
    main()
