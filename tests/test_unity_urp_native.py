from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np


def test_unity_urp_pipeline_generator_emits_native_files():
    from mathart.animation.unity_urp_native import UnityURP2DNativePipelineGenerator

    generator = UnityURP2DNativePipelineGenerator()
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir)
        generator.generate(out)
        audit = generator.audit(out)

        assert audit.importer_exists
        assert audit.postprocessor_exists
        assert audit.vat_player_exists
        assert audit.vat_shader_exists
        assert audit.README_exists
        assert audit.all_pass

        postprocessor = (out / "Editor" / "MathArtSecondaryTexturePostprocessor.cs").read_text(encoding="utf-8")
        assert "ISecondaryTextureDataProvider" in postprocessor
        assert "SecondarySpriteTexture" in postprocessor
        assert "_NormalMap" in postprocessor
        assert "_MaskTex" in postprocessor

        vat_shader = (out / "Shaders" / "MathArtVATLit.shader").read_text(encoding="utf-8")
        assert "_VATPositionTex" in vat_shader
        assert "MathArt/VATSpriteLit" in vat_shader


def test_bake_cloth_vat_manifest_is_consistent():
    from mathart.animation.unity_urp_native import XPBDVATBakeConfig, bake_cloth_vat

    positions = np.array(
        [
            [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            [[0.1, 0.0], [1.1, 0.0], [0.0, 1.1], [1.0, 1.1]],
            [[0.2, 0.0], [1.2, 0.0], [0.0, 1.2], [1.0, 1.2]],
        ],
        dtype=np.float32,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        result = bake_cloth_vat(
            tmpdir,
            config=XPBDVATBakeConfig(asset_name="unit_test_cloth", include_preview=True),
            positions=positions,
        )
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        assert result.texture_path.exists()
        assert result.preview_path is not None and result.preview_path.exists()
        assert manifest["name"] == "unit_test_cloth"
        assert manifest["frame_count"] == 3
        assert manifest["vertex_count"] == 4
        assert manifest["texture_width"] == 4
        assert manifest["texture_height"] == 3
        assert manifest["channels"]["position"] == "vat_position.png"


def test_unity_urp_bridge_full_cycle_persists_state_and_knowledge():
    from mathart.evolution.unity_urp_2d_bridge import (
        UnityURP2DEvolutionBridge,
        collect_unity_urp_2d_status,
    )

    with tempfile.TemporaryDirectory(prefix="unity_bridge_") as tmpdir:
        root = Path(tmpdir)
        bridge = UnityURP2DEvolutionBridge(project_root=root, verbose=False)
        metrics, knowledge_path, bonus = bridge.run_full_cycle()
        status = collect_unity_urp_2d_status(root)

        assert metrics.vat_manifest_valid
        assert metrics.vat_frame_count >= 8
        assert metrics.vat_vertex_count >= 16
        assert knowledge_path.exists()
        assert bonus > 0.0
        assert status.total_cycles == 1
        assert status.consecutive_passes in (0, 1)


def test_evolution_orchestrator_runs_unified_bridge_suite():
    from mathart.evolution.evolution_orchestrator import EvolutionOrchestrator

    with tempfile.TemporaryDirectory(prefix="evo_unified_") as tmpdir:
        orch = EvolutionOrchestrator(project_root=tmpdir, verbose=False)
        report = orch.run_full_cycle()

        assert report.unified_bridges_total >= 4
        # SESSION-098 (HIGH-2.6): Since SESSION-074 (P1-MIGRATE-2), legacy
        # bridges are registered under their canonical evolution_* names.
        # The old bare names (smooth_morphology, constraint_wfc, etc.) are
        # no longer present as top-level keys in unified_bridge_status.
        assert {
            "evolution_morphology",
            "evolution_wfc",
            "evolution_phase3_physics",
            "evolution_urp2d",
        }.issubset(set(report.unified_bridge_status.keys()))
