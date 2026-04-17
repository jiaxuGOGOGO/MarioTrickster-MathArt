"""
SESSION-056 — Breakwall Phase 1 Tests.

Regression tests for the three new subsystems:
1. HeadlessNeuralRenderPipeline (EbSynth + ControlNet)
2. EngineImportPlugin (Godot 4 + Unity URP)
3. BreakwallEvolutionBridge (Three-Layer Evolution Loop)

Research provenance:
    - Jamriška et al., "Stylizing Video by Example", SIGGRAPH 2019
    - Zhang et al., "Adding Conditional Control to Text-to-Image Diffusion Models", ICCV 2023
    - Bénard, "Dead Cells: 2D Deferred Lighting", GDC 2019
"""
import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mathart.animation.skeleton import Skeleton
from mathart.animation.parts import CharacterStyle
from mathart.animation.character_presets import get_preset
from mathart.animation.presets import idle_animation, walk_animation, run_animation


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def skeleton():
    return Skeleton.create_humanoid()

@pytest.fixture
def style():
    return CharacterStyle()

@pytest.fixture
def idle_pose():
    return idle_animation(0.0)


# ═══════════════════════════════════════════════════════════════════════════
#  1. HeadlessNeuralRenderPipeline Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestNeuralRenderConfig:
    """Test NeuralRenderConfig validation."""

    def test_default_config_validates(self):
        from mathart.animation.headless_comfy_ebsynth import NeuralRenderConfig
        config = NeuralRenderConfig()
        warnings = config.validate()
        assert isinstance(warnings, list)
        assert len(warnings) == 0

    def test_low_controlnet_weight_warns(self):
        from mathart.animation.headless_comfy_ebsynth import NeuralRenderConfig
        config = NeuralRenderConfig(controlnet_normal_weight=0.3)
        warnings = config.validate()
        assert any("normal_weight" in w for w in warnings)

    def test_even_patch_size_corrected(self):
        from mathart.animation.headless_comfy_ebsynth import NeuralRenderConfig
        config = NeuralRenderConfig(ebsynth_patch_size=6)
        warnings = config.validate()
        assert config.ebsynth_patch_size == 7
        assert any("patch_size" in w for w in warnings)


class TestComfyUIHeadlessClient:
    """Test ComfyUI headless client workflow building."""

    def test_build_workflow_returns_dict(self):
        from mathart.animation.headless_comfy_ebsynth import ComfyUIHeadlessClient
        client = ComfyUIHeadlessClient()
        source = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        normal = Image.new("RGBA", (64, 64), (128, 128, 255, 255))
        depth = Image.new("RGBA", (64, 64), (128, 128, 128, 255))
        workflow = client.build_controlnet_workflow(
            source_image=source,
            normal_map=normal,
            depth_map=depth,
            prompt="pixel art",
        )
        assert isinstance(workflow, dict)
        assert "prompt" in workflow
        assert "client_id" in workflow
        # Should have 14 nodes
        prompt_nodes = workflow["prompt"]
        assert len(prompt_nodes) == 14

    def test_workflow_has_dual_controlnet(self):
        from mathart.animation.headless_comfy_ebsynth import ComfyUIHeadlessClient
        client = ComfyUIHeadlessClient()
        source = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        normal = Image.new("RGBA", (64, 64), (128, 128, 255, 255))
        depth = Image.new("RGBA", (64, 64), (128, 128, 128, 255))
        workflow = client.build_controlnet_workflow(
            source_image=source,
            normal_map=normal,
            depth_map=depth,
            prompt="test",
        )
        nodes = workflow["prompt"]
        # Node 8: ControlNet Normal loader
        assert nodes["8"]["class_type"] == "ControlNetLoader"
        assert "normalbae" in nodes["8"]["inputs"]["control_net_name"]
        # Node 9: ControlNet Depth loader
        assert nodes["9"]["class_type"] == "ControlNetLoader"
        assert "depth" in nodes["9"]["inputs"]["control_net_name"]


class TestEbSynthPropagationEngine:
    """Test EbSynth-style temporal propagation."""

    def test_propagate_all_keyframes(self):
        from mathart.animation.headless_comfy_ebsynth import (
            EbSynthPropagationEngine, NeuralRenderConfig,
        )
        from mathart.animation.motion_vector_baker import MotionVectorSequence, MotionVectorField

        config = NeuralRenderConfig()
        engine = EbSynthPropagationEngine(config)

        # Create 4 frames, all as keyframes
        frames = [Image.new("RGBA", (32, 32), (i * 60, 0, 0, 255)) for i in range(4)]
        keyframes = {i: frames[i] for i in range(4)}
        mv_seq = MotionVectorSequence(
            fields=[
                MotionVectorField(
                    dx=np.zeros((32, 32)),
                    dy=np.zeros((32, 32)),
                    magnitude=np.zeros((32, 32)),
                    mask=np.ones((32, 32), dtype=bool),
                    width=32,
                    height=32,
                )
                for _ in range(3)
            ],
            frame_count=4,
            width=32,
            height=32,
        )

        result = engine.propagate_style(frames, keyframes, mv_seq)
        assert len(result) == 4
        for r in result:
            assert isinstance(r, Image.Image)

    def test_propagate_with_interpolation(self):
        from mathart.animation.headless_comfy_ebsynth import (
            EbSynthPropagationEngine, NeuralRenderConfig,
        )
        from mathart.animation.motion_vector_baker import MotionVectorSequence, MotionVectorField

        config = NeuralRenderConfig(keyframe_interval=4)
        engine = EbSynthPropagationEngine(config)

        # 8 frames, keyframes at 0 and 7
        frames = [Image.new("RGBA", (32, 32), (100, 100, 100, 255)) for _ in range(8)]
        keyframes = {
            0: Image.new("RGBA", (32, 32), (255, 0, 0, 255)),
            7: Image.new("RGBA", (32, 32), (0, 0, 255, 255)),
        }
        mv_seq = MotionVectorSequence(
            fields=[
                MotionVectorField(
                    dx=np.zeros((32, 32)),
                    dy=np.zeros((32, 32)),
                    magnitude=np.zeros((32, 32)),
                    mask=np.ones((32, 32), dtype=bool),
                    width=32,
                    height=32,
                )
                for _ in range(7)
            ],
            frame_count=8,
            width=32,
            height=32,
        )

        result = engine.propagate_style(frames, keyframes, mv_seq)
        assert len(result) == 8
        # Frame 0 should be red keyframe
        arr0 = np.array(result[0])
        assert arr0[16, 16, 0] == 255  # Red
        # Frame 7 should be blue keyframe
        arr7 = np.array(result[7])
        assert arr7[16, 16, 2] == 255  # Blue


class TestHeadlessNeuralRenderPipeline:
    """Test the full headless neural rendering pipeline."""

    def test_pipeline_bake_auxiliary_maps(self, skeleton, style):
        from mathart.animation.headless_comfy_ebsynth import (
            HeadlessNeuralRenderPipeline, NeuralRenderConfig,
        )
        config = NeuralRenderConfig()
        pipeline = HeadlessNeuralRenderPipeline(config)

        source, normals, depths, mv_seq = pipeline.bake_auxiliary_maps(
            skeleton=skeleton,
            animation_func=idle_animation,
            style=style,
            frames=4,
            width=64,
            height=64,
        )

        assert len(source) == 4
        assert len(normals) == 4
        assert len(depths) == 4
        assert mv_seq is not None
        assert len(mv_seq.fields) == 3  # N-1 fields for N frames

    def test_pipeline_fallback_style_transfer(self, skeleton, style):
        from mathart.animation.headless_comfy_ebsynth import (
            HeadlessNeuralRenderPipeline, NeuralRenderConfig,
        )
        config = NeuralRenderConfig()
        pipeline = HeadlessNeuralRenderPipeline(config)

        source = Image.new("RGBA", (64, 64), (200, 100, 50, 255))
        normal = Image.new("RGBA", (64, 64), (128, 128, 255, 255))

        styled = pipeline._fallback_style_transfer(source, normal)
        assert isinstance(styled, Image.Image)
        assert styled.size == (64, 64)

    def test_full_pipeline_run(self, skeleton, style):
        from mathart.animation.headless_comfy_ebsynth import (
            HeadlessNeuralRenderPipeline, NeuralRenderConfig,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            config = NeuralRenderConfig(
                output_dir=tmpdir,
                keyframe_interval=2,
            )
            pipeline = HeadlessNeuralRenderPipeline(config)
            result = pipeline.run(
                skeleton=skeleton,
                animation_func=idle_animation,
                style=style,
                frames=4,
                width=64,
                height=64,
                export=True,
            )

            assert result.frame_count == 4
            assert len(result.keyframe_indices) > 0
            assert len(result.source_frames) == 4
            assert len(result.normal_maps) == 4
            assert result.mv_sequence is not None
            assert "mean_warp_error" in result.temporal_metrics
            assert result.elapsed_seconds > 0

            # Check export
            meta_path = Path(tmpdir) / "pipeline.json"
            assert meta_path.exists()
            meta = json.loads(meta_path.read_text())
            assert meta["format"] == "headless_neural_render"

    def test_result_metadata(self, skeleton, style):
        from mathart.animation.headless_comfy_ebsynth import (
            HeadlessNeuralRenderPipeline, NeuralRenderConfig, NeuralRenderResult,
        )
        config = NeuralRenderConfig(style_prompt="test style")
        result = NeuralRenderResult(
            config=config,
            keyframe_indices=[0, 4],
            temporal_metrics={"temporal_pass": True, "mean_warp_error": 0.05},
        )
        meta = result.to_metadata()
        assert meta["format"] == "headless_neural_render"
        assert "Jamriška" in str(meta["research_provenance"])
        assert "ControlNet" in str(meta["research_provenance"])


# ═══════════════════════════════════════════════════════════════════════════
#  2. Engine Import Plugin Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMathArtBundle:
    """Test .mathart bundle creation, saving, and loading."""

    def test_bundle_save_and_load(self):
        from mathart.animation.engine_import_plugin import MathArtBundle

        bundle = MathArtBundle(
            name="test_hero",
            albedo=Image.new("RGBA", (64, 64), (255, 0, 0, 255)),
            normal_map=Image.new("RGBA", (64, 64), (128, 128, 255, 255)),
            depth_map=Image.new("L", (64, 64), 128),
            thickness_map=Image.new("L", (64, 64), 200),
            roughness_map=Image.new("L", (64, 64), 100),
            mask=Image.new("L", (64, 64), 255),
            contour_points=[(10, 10), (50, 10), (50, 50), (10, 50)],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = bundle.save(tmpdir)
            assert (bundle_path / "manifest.json").exists()
            assert (bundle_path / "albedo.png").exists()
            assert (bundle_path / "normal.png").exists()
            assert (bundle_path / "contour.json").exists()

            # Load back
            loaded = MathArtBundle.load(tmpdir)
            assert loaded.name == "test_hero"
            assert loaded.albedo is not None
            assert len(loaded.contour_points) == 4

    def test_bundle_manifest_structure(self):
        from mathart.animation.engine_import_plugin import MathArtBundle

        bundle = MathArtBundle(
            name="test",
            albedo=Image.new("RGBA", (32, 32), (255, 0, 0, 255)),
            normal_map=Image.new("RGBA", (32, 32), (128, 128, 255, 255)),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bundle.save(tmpdir)
            manifest = json.loads((Path(tmpdir) / "manifest.json").read_text())

            assert manifest["format"] == "mathart_bundle"
            assert manifest["version"] == "1.0"
            assert "godot_4" in manifest["engine_targets"]
            assert "unity_urp_2d" in manifest["engine_targets"]
            assert "albedo" in manifest["channel_semantics"]
            assert "thickness" in manifest["channel_semantics"]
            assert "Bénard" in str(manifest["research_provenance"])


class TestSdfContourExtraction:
    """Test SDF contour extraction for PolygonCollider2D."""

    def test_contour_extraction_returns_points(self, skeleton, style):
        from mathart.animation.engine_import_plugin import extract_sdf_contour

        contour = extract_sdf_contour(
            skeleton=skeleton,
            pose=idle_animation(0.0),
            style=style,
            width=64,
            height=64,
        )
        assert isinstance(contour, list)
        assert len(contour) > 0
        for pt in contour:
            assert len(pt) == 2
            assert isinstance(pt[0], float)
            assert isinstance(pt[1], float)


class TestEngineImportPluginGenerator:
    """Test Godot 4 and Unity plugin generation."""

    def test_generate_godot_plugin(self):
        from mathart.animation.engine_import_plugin import EngineImportPluginGenerator

        generator = EngineImportPluginGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            addon_dir = generator.generate_godot_plugin(tmpdir)
            assert (addon_dir / "plugin.cfg").exists()
            assert (addon_dir / "plugin.gd").exists()
            assert (addon_dir / "mathart_material.gdshader").exists()

            # Check shader content
            shader = (addon_dir / "mathart_material.gdshader").read_text()
            assert "sss_strength" in shader
            assert "rim_light_power" in shader
            assert "thickness_map" in shader
            assert "normal_map" in shader

    def test_generate_unity_plugin(self):
        from mathart.animation.engine_import_plugin import EngineImportPluginGenerator

        generator = EngineImportPluginGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            editor_dir = generator.generate_unity_plugin(tmpdir)
            assert (editor_dir / "MathArtImporter.cs").exists()
            shader_dir = Path(tmpdir) / "Shaders"
            assert (shader_dir / "MathArtLitShader.shader").exists()

            # Check importer content
            cs = (editor_dir / "MathArtImporter.cs").read_text()
            assert "ScriptedImporter" in cs
            assert "PolygonCollider2D" in cs
            assert "_NormalMap" in cs
            assert "_ThicknessMap" in cs

    def test_generate_all(self):
        from mathart.animation.engine_import_plugin import EngineImportPluginGenerator

        generator = EngineImportPluginGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generator.generate_all(tmpdir)
            assert "godot_4" in result
            assert "unity_urp" in result


class TestBundleValidation:
    """Test .mathart bundle validation."""

    def test_validate_complete_bundle(self, skeleton, style):
        from mathart.animation.engine_import_plugin import (
            generate_mathart_bundle, validate_mathart_bundle,
        )

        bundle = generate_mathart_bundle(
            skeleton=skeleton,
            pose=idle_animation(0.0),
            style=style,
            width=64,
            height=64,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bundle.save(tmpdir)
            result = validate_mathart_bundle(tmpdir)
            assert result["valid"] is True
            assert len(result["issues"]) == 0
            assert "albedo" in result["channels_found"]
            assert "normal" in result["channels_found"]

    def test_validate_missing_manifest(self):
        from mathart.animation.engine_import_plugin import validate_mathart_bundle

        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_mathart_bundle(tmpdir)
            assert result["valid"] is False


class TestGenerateMathArtBundle:
    """Test full bundle generation from math engine."""

    def test_generate_bundle(self, skeleton, style):
        from mathart.animation.engine_import_plugin import generate_mathart_bundle

        bundle = generate_mathart_bundle(
            skeleton=skeleton,
            pose=idle_animation(0.0),
            style=style,
            width=64,
            height=64,
            name="hero_idle",
        )

        assert bundle.name == "hero_idle"
        assert bundle.albedo is not None
        assert bundle.normal_map is not None
        assert bundle.depth_map is not None
        assert bundle.thickness_map is not None
        assert bundle.roughness_map is not None
        assert bundle.mask is not None
        assert len(bundle.contour_points) > 0


# ═══════════════════════════════════════════════════════════════════════════
#  3. Breakwall Evolution Bridge Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBreakwallEvolutionBridge:
    """Test the three-layer evolution bridge."""

    def test_evaluate_engine_bundle(self, skeleton, style):
        from mathart.evolution.breakwall_evolution_bridge import BreakwallEvolutionBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = BreakwallEvolutionBridge(
                project_root=Path(tmpdir),
                verbose=True,
            )
            metrics = bridge.evaluate_engine_bundle(
                skeleton=skeleton,
                pose=idle_animation(0.0),
                style=style,
                width=64,
                height=64,
            )
            assert metrics.bundle_valid is True
            assert metrics.bundle_channels_found >= 5

    def test_distill_knowledge_on_failure(self):
        from mathart.evolution.breakwall_evolution_bridge import (
            BreakwallEvolutionBridge, BreakwallMetrics,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = BreakwallEvolutionBridge(project_root=Path(tmpdir))
            metrics = BreakwallMetrics(
                cycle_id=1,
                neural_render_pass=False,
                mean_warp_error=0.25,
                flicker_score=0.08,
                bundle_valid=False,
                bundle_channels_found=3,
                bundle_issues=["Missing channel: depth"],
            )
            rules = bridge.distill_knowledge(metrics)
            assert len(rules) >= 2  # At least neural + bundle rules
            assert any("enforcement" in r["rule_type"] for r in rules)
            assert any("bundle_warning" in r["rule_type"] for r in rules)

    def test_compute_fitness_bonus_pass(self):
        from mathart.evolution.breakwall_evolution_bridge import (
            BreakwallEvolutionBridge, BreakwallMetrics,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = BreakwallEvolutionBridge(project_root=Path(tmpdir))
            metrics = BreakwallMetrics(
                neural_render_pass=True,
                mean_warp_error=0.05,
                flicker_score=0.01,
                bundle_valid=True,
                bundle_channels_found=6,
                bundle_contour_points=20,
            )
            bonus = bridge.compute_fitness_bonus(metrics)
            assert bonus > 0
            assert bonus <= 0.2

    def test_compute_fitness_bonus_fail(self):
        from mathart.evolution.breakwall_evolution_bridge import (
            BreakwallEvolutionBridge, BreakwallMetrics,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = BreakwallEvolutionBridge(project_root=Path(tmpdir))
            metrics = BreakwallMetrics(
                neural_render_pass=False,
                mean_warp_error=0.3,
                flicker_score=0.15,
                bundle_valid=False,
                bundle_channels_found=2,
            )
            bonus = bridge.compute_fitness_bonus(metrics)
            assert bonus < 0
            assert bonus >= -0.3

    def test_status_report(self):
        from mathart.evolution.breakwall_evolution_bridge import BreakwallEvolutionBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = BreakwallEvolutionBridge(project_root=Path(tmpdir))
            report = bridge.status_report()
            assert "Breakwall" in report
            assert "SESSION-056" in report

    def test_collect_status(self):
        from mathart.evolution.breakwall_evolution_bridge import collect_breakwall_status

        with tempfile.TemporaryDirectory() as tmpdir:
            status = collect_breakwall_status(Path(tmpdir))
            assert status.neural_render_available is True
            assert status.engine_import_available is True

    def test_state_persistence(self):
        from mathart.evolution.breakwall_evolution_bridge import BreakwallEvolutionBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = BreakwallEvolutionBridge(project_root=Path(tmpdir))
            bridge.state.total_cycles = 5
            bridge.state.total_passes = 3
            bridge.state.warp_error_trend = [0.1, 0.08, 0.06]
            bridge._save_state()

            # Reload
            bridge2 = BreakwallEvolutionBridge(project_root=Path(tmpdir))
            assert bridge2.state.total_cycles == 5
            assert bridge2.state.total_passes == 3
            assert len(bridge2.state.warp_error_trend) == 3

    def test_auto_tune_reduces_interval_on_high_error(self):
        from mathart.evolution.breakwall_evolution_bridge import BreakwallEvolutionBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = BreakwallEvolutionBridge(project_root=Path(tmpdir))
            bridge.state.optimal_keyframe_interval = 4
            bridge.state.warp_error_trend = [0.15, 0.16, 0.18]
            changes = bridge.auto_tune_parameters()
            assert bridge.state.optimal_keyframe_interval < 4
