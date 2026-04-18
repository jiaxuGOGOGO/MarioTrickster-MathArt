"""Tests for WFC Tilemap Exporter (SESSION-062 Phase 4).

Validates:
  - WFCTilemapExporter generates valid JSON tilemap data
  - DualGridMapper produces correct Marching Squares indices
  - Unity C# WFCTilemapLoader script is generated
  - Full generate_and_export_tilemap pipeline works end-to-end
  - Three-layer evolution bridge evaluates correctly
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mathart.level.wfc_tilemap_exporter import (
    WFCTilemapExporter,
    DualGridMapper,
    TilemapExportResult,
    TilemapMetadata,
    generate_and_export_tilemap,
    generate_wfc_tilemap_loader,
)
from mathart.level.constraint_wfc import ConstraintAwareWFC


class TestWFCTilemapExporter(unittest.TestCase):
    """Test WFC Tilemap JSON export."""

    def test_basic_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_and_export_tilemap(
                width=12, height=5, seed=42,
                output_dir=tmpdir, include_unity_loader=False,
            )
            self.assertTrue(result.success)
            self.assertEqual(result.logical_width, 12)
            self.assertEqual(result.logical_height, 5)
            self.assertTrue(os.path.exists(result.json_path))

            with open(result.json_path) as f:
                data = json.load(f)
            self.assertIn("logical_grid", data)
            self.assertIn("metadata", data)
            self.assertEqual(len(data["logical_grid"]), 5)
            self.assertEqual(len(data["logical_grid"][0]), 12)

    def test_metadata_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_and_export_tilemap(
                width=15, height=6, seed=123,
                output_dir=tmpdir, include_unity_loader=False,
            )
            meta = result.metadata
            self.assertIsNotNone(meta)
            self.assertIsInstance(meta.is_playable, bool)
            self.assertIsInstance(meta.tile_diversity, float)
            self.assertIsInstance(meta.platform_count, int)
            self.assertIsInstance(meta.gap_count, int)
            self.assertIsInstance(meta.reachability_path_length, int)

    def test_dual_grid_in_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_and_export_tilemap(
                width=18, height=6, seed=42,
                output_dir=tmpdir, include_unity_loader=False,
            )
            with open(result.json_path) as f:
                data = json.load(f)
            self.assertIn("dual_grid", data)
            if data["dual_grid"]:
                # Dual grid should be (height-1) x (width-1)
                self.assertEqual(len(data["dual_grid"]), 5)
                self.assertEqual(len(data["dual_grid"][0]), 17)


class TestDualGridMapper(unittest.TestCase):
    """Test Dual Grid WFC (Oskar Stålberg / Marching Squares)."""

    def test_dual_grid_dimensions(self):
        # 4x3 logical → 2x3 dual grid (rows-1 x cols-1)
        tile_ids = [
            [1, 1, 0, 0],
            [1, 0, 0, 1],
            [0, 0, 1, 1],
        ]
        dual_cells = DualGridMapper.compute(tile_ids)
        self.assertEqual(len(dual_cells), 2)
        self.assertEqual(len(dual_cells[0]), 3)

    def test_marching_indices_range(self):
        tile_ids = [
            [1, 1, 1, 1, 1],
            [1, 0, 0, 0, 1],
            [1, 0, 1, 0, 1],
            [1, 1, 1, 1, 1],
        ]
        dual_cells = DualGridMapper.compute(tile_ids)
        for row in dual_cells:
            for cell in row:
                self.assertGreaterEqual(cell.marching_index, 0)
                self.assertLessEqual(cell.marching_index, 15)

    def test_all_solid_gives_15(self):
        tile_ids = [[1, 1], [1, 1]]
        dual_cells = DualGridMapper.compute(tile_ids)
        self.assertEqual(dual_cells[0][0].marching_index, 15)

    def test_all_empty_gives_0(self):
        tile_ids = [[0, 0], [0, 0]]
        dual_cells = DualGridMapper.compute(tile_ids)
        self.assertEqual(dual_cells[0][0].marching_index, 0)


class TestUnityTilemapLoader(unittest.TestCase):
    """Test Unity C# script generation."""

    def test_script_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_wfc_tilemap_loader(tmpdir)
            self.assertTrue(os.path.exists(path))
            content = Path(path).read_text()
            self.assertIn("WFCTilemapLoader", content)
            self.assertIn("CompositeCollider2D", content)
            self.assertIn("TileBase", content)


class TestGenerateAndExportPipeline(unittest.TestCase):
    """Test the full one-shot pipeline."""

    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_and_export_tilemap(
                width=18, height=6, seed=42,
                output_dir=tmpdir, include_unity_loader=True,
            )
            self.assertTrue(result.success)
            self.assertTrue(os.path.exists(result.json_path))

            # Verify dual grid was computed
            with open(result.json_path) as f:
                data = json.load(f)
            self.assertIn("dual_grid", data)


class TestEvolutionBridge(unittest.TestCase):
    """Test three-layer evolution bridge for WFC Tilemap."""

    def test_bridge_cycle(self):
        from mathart.evolution.env_closedloop_bridge import WFCTilemapEvolutionBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = WFCTilemapEvolutionBridge(Path(tmpdir))
            result = bridge.run_full_cycle()

            self.assertIn("metrics", result)
            self.assertIn("rules", result)
            self.assertIn("fitness_bonus", result)
            self.assertIsInstance(result["fitness_bonus"], float)
            self.assertGreaterEqual(result["fitness_bonus"], -0.20)
            self.assertLessEqual(result["fitness_bonus"], 0.20)


if __name__ == "__main__":
    unittest.main()
