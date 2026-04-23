from __future__ import annotations

import json
from pathlib import Path

from mathart.cli import main as cli_main
from mathart.core.artifact_schema import ArtifactManifest
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
    assert topology["guide_baking_stage"]["work_items"] == 2
    assert topology["ai_render_stage"]["work_items"] == 2

    # SESSION-158: guide_baking_stage is CPU-only (requires_gpu=False)
    baking_trace = [
        entry for entry in runtime["trace"]
        if entry["node_name"] == "guide_baking_stage"
    ]
    assert baking_trace
    assert all(entry["requires_gpu"] is False for entry in baking_trace)

    gpu_trace = [
        entry for entry in runtime["trace"]
        if entry["node_name"] in {"orthographic_render_stage", "ai_render_stage"}
    ]
    assert gpu_trace
    assert all(entry["requires_gpu"] is True for entry in gpu_trace)

    observed_seed_digests = set()
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

        # SESSION-158: guide_baking assets MUST exist even with skip_ai_render
        guide_baking = record["final_outputs"]["guide_baking"]
        assert guide_baking["frame_count"] > 0
        assert Path(guide_baking["report_path"]).exists()
        assert Path(guide_baking["albedo_dir"]).exists()
        assert Path(guide_baking["normal_dir"]).exists()
        assert Path(guide_baking["depth_dir"]).exists()
        assert Path(guide_baking["mask_dir"]).exists()
        assert record["manifests"]["guide_baking"] is not None
        assert Path(record["manifests"]["guide_baking"]).exists()

        stage_rng = record["stage_rng_spawn_digests"]
        assert set(stage_rng) == {
            "prepare_character",
            "unified_motion_stage",
            "pseudo3d_shell_stage",
            "physical_ribbon_stage",
            "orthographic_render_stage",
            "motion2d_export_stage",
            "final_delivery_stage",
            "guide_baking_stage",
            "ai_render_stage",
        }
        assert all(stage_rng[key] for key in stage_rng)
        assert len(set(stage_rng.values())) == len(stage_rng)
        observed_seed_digests.add(record["seed_spawn_digest"])

        orthographic_manifest = ArtifactManifest.load(record["manifests"]["orthographic_pixel_render"])
        render_report = json.loads(Path(orthographic_manifest.outputs["render_report"]).read_text(encoding="utf-8"))
        composition_report = json.loads(
            next((character_dir / "composed_mesh").glob("*_composition_report.json")).read_text(encoding="utf-8")
        )
        assert render_report["mesh_stats"]["vertex_count"] == composition_report["vertex_count"]
        assert render_report["mesh_stats"]["triangle_count"] == composition_report["triangle_count"]

        delivery_archive = record["final_outputs"]["delivery_archive"]
        orthographic_archive = record["final_outputs"]["orthographic_archive"]
        assert any(path.endswith(".anim") for path in delivery_archive["unity_2d_anim"].values())
        assert any(path.endswith(".mp4") for path in delivery_archive["spine_preview"].values())
        assert any(name.endswith("_render_report.json") for name in orthographic_archive)
        assert any(path.endswith(".png") for path in orthographic_archive.values())

        ai_render = record["final_outputs"]["ai_render"]
        assert ai_render["skipped"] is True
        assert Path(ai_render["report_path"]).exists()
        assert any(path.endswith("anti_flicker_render_skipped.json") for path in ai_render["archived"].values())

    assert len(observed_seed_digests) == 2


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
