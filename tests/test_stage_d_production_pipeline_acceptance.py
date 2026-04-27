"""Stage D — End-to-end production pipeline acceptance.

This guard verifies that the production intent path can resolve real plugins,
execute a deterministic offline asset chain, pass real physics data across a
backend boundary, validate every manifest, and finish with provenance audit.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mathart.core.artifact_schema import validate_artifact
from mathart.core.backend_registry import get_registry
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge
from mathart.workspace.semantic_orchestrator import SemanticOrchestrator


def _assert_manifest_valid(manifest, backend_name: str) -> None:
    errors = validate_artifact(manifest)
    assert not errors, f"{backend_name} produced invalid manifest: {errors}"


def _assert_declared_outputs_exist(manifest, backend_name: str) -> None:
    missing: list[str] = []
    for key, raw_path in manifest.outputs.items():
        if not raw_path:
            missing.append(f"{key}=<empty>")
            continue
        path = Path(raw_path)
        if not path.exists():
            missing.append(f"{key}={raw_path}")
        elif path.is_file() and path.stat().st_size <= 0:
            missing.append(f"{key}={raw_path} (empty file)")
    assert not missing, f"{backend_name} has missing/empty declared outputs: {missing}"


def test_stage_d_offline_production_pipeline_acceptance(tmp_path: Path) -> None:
    registry = get_registry()
    orchestrator = SemanticOrchestrator()
    intent = orchestrator.resolve_full_intent(
        raw_intent={},
        vibe="四足机械狗 高精度导出 赛博纹理",
        registry=registry,
    )

    active_plugins = intent["active_vfx_plugins"]
    assert intent["skeleton_topology"] == "quadruped"
    for required in (
        "quadruped_physics",
        "high_precision_vat",
        "cppn_texture_evolution",
    ):
        assert required in active_plugins

    bridge = MicrokernelPipelineBridge(
        project_root=tmp_path,
        session_id="SESSION-STAGE-D",
    )

    manifests = {}

    quadruped_manifest = bridge.run_backend(
        "quadruped_physics",
        {
            "output_dir": str(tmp_path / "01_quadruped_physics"),
            "num_frames": 8,
            "num_vertices": 16,
            "channels": 3,
            "fps": 12,
            "skeleton_topology": intent["skeleton_topology"],
            "vibe": "四足机械狗",
        },
    )
    manifests["quadruped_physics"] = quadruped_manifest

    positions_path = Path(quadruped_manifest.outputs["positions_npy"])
    positions = np.load(positions_path)
    assert positions.shape == (8, 16, 3)

    vat_manifest = bridge.run_backend(
        "high_precision_vat",
        {
            "output_dir": str(tmp_path / "02_high_precision_vat"),
            "positions": positions,
            "num_vertices": 16,
            "channels": 3,
            "fps": 12,
            "asset_name": "stage_d_quadruped_vat",
            "skeleton_topology": intent["skeleton_topology"],
        },
    )
    manifests["high_precision_vat"] = vat_manifest
    assert vat_manifest.metadata["data_source"] == "real_physics"
    assert vat_manifest.metadata["skeleton_topology"] == "quadruped"

    texture_manifest = bridge.run_backend(
        "cppn_texture_evolution",
        {
            "output_dir": str(tmp_path / "03_cppn_texture"),
            "num_textures": 1,
            "resolution": 32,

            "seed": 7,
            "vibe": "赛博纹理",
        },
    )
    manifests["cppn_texture_evolution"] = texture_manifest

    backend_consumed_params = {
        "active_vfx_plugins": active_plugins,
        "skeleton_topology": intent["skeleton_topology"],
        "quadruped_positions": str(positions_path),
        "vat_position_tex": vat_manifest.outputs["position_tex"],
        "texture_albedo": texture_manifest.outputs["albedo"],
    }
    provenance_manifest = bridge.run_backend(
        "provenance_audit",
        {
            "output_dir": str(tmp_path / "04_provenance_audit"),
            "session_id": "SESSION-STAGE-D",
            "raw_vibe": "四足机械狗 高精度导出 赛博纹理",
            "genotype_flat": {
                "speed": 1.0,
                "fps": 12,
                "num_vertices": 16,
            },
            "backend_name": "stage_d_acceptance_chain",
            "backend_consumed_params": backend_consumed_params,
        },
    )
    manifests["provenance_audit"] = provenance_manifest

    for backend_name, manifest in manifests.items():
        _assert_manifest_valid(manifest, backend_name)
        _assert_declared_outputs_exist(manifest, backend_name)

    acceptance_report = {
        "session_id": "SESSION-STAGE-D",
        "active_vfx_plugins": active_plugins,
        "skeleton_topology": intent["skeleton_topology"],
        "manifest_families": {
            name: manifest.artifact_family for name, manifest in manifests.items()
        },
        "handoffs": {
            "quadruped_positions_to_vat": str(positions_path),
            "vat_data_source": vat_manifest.metadata["data_source"],
            "texture_albedo": texture_manifest.outputs["albedo"],
            "provenance_report": provenance_manifest.outputs["report_file"],
        },
    }
    report_path = tmp_path / "stage_d_acceptance_report.json"
    report_path.write_text(
        json.dumps(acceptance_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert report_path.exists()
