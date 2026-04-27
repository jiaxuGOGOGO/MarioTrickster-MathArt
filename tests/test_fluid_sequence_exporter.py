"""Tests for Fluid Sequence Exporter & Unity VFX Graph Bridge (SESSION-062 Phase 4).

Validates:
  - FluidSequenceExporter generates density and velocity atlases
  - FlipbookAtlasBuilder creates correct atlas dimensions
  - VelocityFieldRenderer encodes velocity correctly
  - VFX manifest JSON is valid and complete
  - Unity FluidVFXController C# script is generated
  - Three-layer evolution bridge evaluates correctly
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mathart.animation.fluid_sequence_exporter import (
    FluidSequenceExporter,
    FluidSequenceConfig,
    FlipbookAtlasBuilder,
    VelocityFieldRenderer,
    export_fluid_vfx_bundle,
    generate_fluid_vfx_controller,
)
from PIL import Image


class TestFlipbookAtlasBuilder(unittest.TestCase):
    """Test flipbook atlas construction."""

    def test_basic_atlas(self):
        frames = [Image.new("RGBA", (32, 32), (255, 0, 0, 128)) for _ in range(9)]
        atlas, cols, rows = FlipbookAtlasBuilder.build(frames, columns=3)
        self.assertEqual(cols, 3)
        self.assertEqual(rows, 3)
        self.assertEqual(atlas.size, (96, 96))

    def test_auto_columns(self):
        frames = [Image.new("RGBA", (16, 16), (0, 255, 0, 255)) for _ in range(12)]
        atlas, cols, rows = FlipbookAtlasBuilder.build(frames)
        self.assertEqual(cols, 4)  # ceil(sqrt(12)) = 4
        self.assertEqual(rows, 3)

    def test_empty_frames(self):
        atlas, cols, rows = FlipbookAtlasBuilder.build([])
        self.assertEqual(cols, 0)
        self.assertEqual(rows, 0)


class TestFluidSequenceExporter(unittest.TestCase):
    """Test fluid sequence export pipeline."""

    def test_smoke_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = export_fluid_vfx_bundle(
                driver_mode="smoke",
                frame_count=8,
                canvas_size=32,
                output_dir=tmpdir,
            )
            self.assertTrue(result.success)
            self.assertTrue(os.path.exists(result.density_atlas_path))
            self.assertTrue(os.path.exists(result.velocity_atlas_path))
            self.assertTrue(os.path.exists(result.manifest_path))
            self.assertTrue(os.path.exists(result.unity_controller_path))

    def test_slash_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = export_fluid_vfx_bundle(
                driver_mode="slash",
                frame_count=12,
                canvas_size=48,
                output_dir=tmpdir,
            )
            self.assertTrue(result.success)
            self.assertIsNotNone(result.manifest)
            self.assertEqual(result.manifest.driver_mode, "slash")

    def test_manifest_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = export_fluid_vfx_bundle(
                driver_mode="smoke",
                frame_count=6,
                canvas_size=32,
                output_dir=tmpdir,
            )
            with open(result.manifest_path) as f:
                m = json.load(f)

            required_keys = [
                "generator", "driver_mode", "density_atlas_path",
                "velocity_atlas_path", "atlas_columns", "atlas_rows",
                "frame_count", "frame_width", "frame_height",
                "velocity_scale", "velocity_encoding",
                "suggested_inherit_velocity_multiplier",
            ]
            for key in required_keys:
                self.assertIn(key, m, f"Missing manifest key: {key}")

            self.assertEqual(m["frame_count"], 6)
            self.assertEqual(m["velocity_encoding"], "rg_centered")

    def test_atlas_dimensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = FluidSequenceConfig(
                frame_count=16,
                canvas_size=32,
            )
            exporter = FluidSequenceExporter(config)
            result = exporter.export_all(output_dir=tmpdir)

            with Image.open(result.density_atlas_path) as atlas:
                cols = config.effective_columns()  # ceil(sqrt(16)) = 4
                rows = 16 // cols  # 4
                self.assertEqual(atlas.size, (cols * 32, rows * 32))


class TestUnityVFXController(unittest.TestCase):
    """Test Unity C# script generation."""

    def test_controller_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_fluid_vfx_controller(tmpdir)
            self.assertTrue(os.path.exists(path))
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("FluidVFXController", content)
            self.assertIn("VisualEffect", content)
            self.assertIn("Rigidbody2D", content)
            self.assertIn("VelocityInheritMode", content)
            self.assertIn("inheritVelocityMultiplier", content)
            self.assertIn("DensityAtlas", content)
            self.assertIn("VelocityAtlas", content)


class TestEvolutionBridge(unittest.TestCase):
    """Test three-layer evolution bridge for Fluid Sequence."""

    def test_bridge_cycle(self):
        from mathart.evolution.env_closedloop_bridge import FluidSequenceEvolutionBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = FluidSequenceEvolutionBridge(Path(tmpdir))
            result = bridge.run_full_cycle(driver_mode="smoke")

            self.assertIn("metrics", result)
            self.assertIn("rules", result)
            self.assertIn("fitness_bonus", result)
            self.assertTrue(result["metrics"]["sequence_pass"])
            self.assertGreater(result["fitness_bonus"], 0.0)


if __name__ == "__main__":
    unittest.main()
