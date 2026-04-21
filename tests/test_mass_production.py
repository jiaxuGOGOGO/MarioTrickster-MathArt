from __future__ import annotations

import json
from pathlib import Path

from mathart.cli import main as cli_main
from tools.run_mass_production_factory import run_mass_production_factory


def test_mass_production_factory_dry_run_skip_ai_render(tmp_path: Path) -> None:
    payload = run_mass_production_factory(
        output_root=tmp_path,
        batch_size=2,
        pdg_workers=4,
        gpu_slots=1,
        seed=20260421,
        skip_ai_render=True,
    )

    assert payload["status"] == "ok"
    assert payload["character_count"] == 2
    assert payload["skip_ai_render"] is True

    batch_dir = Path(payload["batch_dir"])
    summary_path = Path(payload["summary_path"])
    trace_path = Path(payload["pdg_trace_path"])
    assert batch_dir.exists()
    assert summary_path.exists()
    assert trace_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    runtime = json.loads(trace_path.read_text(encoding="utf-8"))

    assert summary["character_count"] == 2
    assert len(summary["records"]) == 2
    assert runtime["scheduler"]["max_workers"] == 4
    assert runtime["scheduler"]["gpu_slots"] == 1
    assert runtime["scheduler"]["gpu_max_inflight_observed"] <= 1

    topology = runtime["topology_summary"]
    assert topology["fan_out_orders"]["work_items"] == 2
    assert topology["prepare_character"]["work_items"] == 2
    assert topology["orthographic_render_stage"]["work_items"] == 2
    assert topology["ai_render_stage"]["work_items"] == 2

    gpu_trace = [
        entry for entry in runtime["trace"]
        if entry["node_name"] in {"orthographic_render_stage", "ai_render_stage"}
    ]
    assert gpu_trace
    assert all(entry["requires_gpu"] is True for entry in gpu_trace)

    for record in summary["records"]:
        character_dir = Path(record["character_dir"])
        assert character_dir.exists()
        assert Path(record["manifests"]["unified_motion"]).exists()
        assert Path(record["manifests"]["pseudo_3d_shell"]).exists()
        assert Path(record["manifests"]["physical_ribbon"]).exists()
        assert Path(record["manifests"]["orthographic_pixel_render"]).exists()
        assert Path(record["manifests"]["unity_2d_anim"]).exists()
        assert Path(record["manifests"]["spine_preview"]).exists()
        assert record["manifests"]["anti_flicker_render"] is None
        assert Path(record["final_outputs"]["spine_json"]).exists()
        ai_render = record["final_outputs"]["ai_render"]
        assert ai_render["skipped"] is True
        assert Path(ai_render["report_path"]).exists()


def test_cli_mass_produce_dry_run_skip_ai_render(tmp_path: Path) -> None:
    exit_code = cli_main(
        [
            "mass-produce",
            "--output-dir",
            str(tmp_path),
            "--batch-size",
            "1",
            "--pdg-workers",
            "2",
            "--gpu-slots",
            "1",
            "--skip-ai-render",
            "--seed",
            "20260421",
        ]
    )
    assert exit_code == 0
