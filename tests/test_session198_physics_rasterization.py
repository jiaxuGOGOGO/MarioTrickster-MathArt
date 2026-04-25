"""
Tests for SESSION-198: Physics & Fluid Rasterization Bridge
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from mathart.animation.physics_sequence_exporter import PhysicsRasterizerAdapter

class TestPhysicsRasterization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)
        self.adapter = PhysicsRasterizerAdapter(self.output_dir, resolution=64)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_physics_rasterization_variance_red_line(self):
        # 1. Mock JSON data
        json_path = self.output_dir / "mock_physics.json"
        mock_data = {
            "frame_count": 4,
            "frames": [
                {"joints": {"root": {"z": 0.1}}},
                {"joints": {"root": {"z": 0.5}}},
                {"joints": {"root": {"z": 0.9}}},
                {"joints": {"root": {"z": 1.2}}},
            ]
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(mock_data, f)

        # 2. Rasterize
        output_paths = self.adapter.rasterize_physics_clip(json_path)

        # 3. Assertions
        self.assertEqual(len(output_paths), 4)
        for path in output_paths:
            self.assertTrue(path.exists())
            
            # 4. Anti-Fake-Image Red Line: Read and check variance
            img = Image.open(path)
            img_array = np.array(img)
            variance = np.var(img_array)
            
            # Must not be a solid color
            self.assertGreater(variance, 0.0, "Rasterized physics image has zero variance (solid color). Fails anti-fake-image red line.")

    def test_fluid_rasterization_variance_red_line(self):
        # 1. Mock JSON data
        json_path = self.output_dir / "mock_fluid.json"
        mock_data = {
            "frame_count": 3,
            "frames": [
                {"velocity": [0.1, -0.2]},
                {"velocity": [0.5, 0.0]},
                {"velocity": [-0.8, 0.9]},
            ]
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(mock_data, f)

        # 2. Rasterize
        output_paths = self.adapter.rasterize_fluid_momentum(json_path)

        # 3. Assertions
        self.assertEqual(len(output_paths), 3)
        for path in output_paths:
            self.assertTrue(path.exists())
            
            # 4. Anti-Fake-Image Red Line: Read and check variance
            img = Image.open(path)
            img_array = np.array(img)
            variance = np.var(img_array)
            
            # Must not be a solid color
            self.assertGreater(variance, 0.0, "Rasterized fluid image has zero variance (solid color). Fails anti-fake-image red line.")

if __name__ == "__main__":
    unittest.main()
