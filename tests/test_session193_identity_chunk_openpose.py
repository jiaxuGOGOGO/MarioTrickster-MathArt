"""SESSION-193 regression tests.

Tests cover three core tasks:
  1. Identity Hydration (IPAdapter identity lock)
  2. Chunk Math Repair (frame_count rebinding + array co-origin assertion)
  3. OpenPose Skeleton Renderer + ControlNet Arbitration

Red-line compliance:
  - No proxy env var references
  - SESSION-189 hard anchors untouched
  - anime_rhythmic_subsample untouched
  - force_decouple_dummy_mesh_payload untouched
  - IoC registry architecture: new modules are standalone helpers
"""

from __future__ import annotations

import copy
import importlib
import json
import math
import os
import pathlib
import sys
import textwrap
import types
import unittest

# ─── ensure project root is on sys.path ──────────────────────────────
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════
# Group 1: Identity Hydration Tests
# ═══════════════════════════════════════════════════════════════════════

class TestIdentityHydration(unittest.TestCase):
    """Tests for mathart.core.identity_hydration module."""

    def _get_module(self):
        return importlib.import_module("mathart.core.identity_hydration")

    # ── Module existence & public API ──────────────────────────────
    def test_module_importable(self):
        mod = self._get_module()
        self.assertTrue(hasattr(mod, "inject_ipadapter_identity_lock"))
        self.assertTrue(hasattr(mod, "extract_visual_reference_path"))

    def test_default_weight_is_085(self):
        mod = self._get_module()
        self.assertAlmostEqual(mod.IPADAPTER_IDENTITY_WEIGHT, 0.85, places=2)

    # ── inject_ipadapter_identity_lock on empty workflow ───────────
    def test_inject_into_empty_workflow_returns_report(self):
        """inject returns a report dict; on empty workflow it should be skipped."""
        mod = self._get_module()
        workflow = {}
        report = mod.inject_ipadapter_identity_lock(workflow, "/tmp/ref.png")
        self.assertIsInstance(report, dict)
        # Empty workflow has no CheckpointLoaderSimple, so injection is skipped
        self.assertEqual(report.get("mode"), "skipped")
        self.assertFalse(report.get("injected", True))

    def test_inject_into_workflow_with_checkpoint(self):
        """inject should add IPAdapter nodes when CheckpointLoaderSimple exists."""
        mod = self._get_module()
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "sd15.safetensors"},
                "_meta": {"title": "Load Checkpoint"},
            },
            "2": {
                "class_type": "KSampler",
                "inputs": {"model": ["1", 0], "seed": 42},
                "_meta": {"title": "KSampler"},
            },
        }
        report = mod.inject_ipadapter_identity_lock(workflow, "/tmp/ref.png")
        self.assertTrue(report.get("injected"))
        self.assertEqual(report.get("mode"), "new")
        # Check that LoadImage and CLIPVisionLoader were added to workflow
        class_types = {v.get("class_type") for v in workflow.values() if isinstance(v, dict)}
        self.assertIn("LoadImage", class_types)
        self.assertIn("CLIPVisionLoader", class_types)
        self.assertIn("IPAdapterModelLoader", class_types)
        self.assertIn("IPAdapterApply", class_types)

    def test_inject_preserves_existing_nodes(self):
        mod = self._get_module()
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "sd15.safetensors"},
                "_meta": {"title": "Load Checkpoint"},
            },
            "2": {
                "class_type": "KSampler",
                "inputs": {"model": ["1", 0]},
                "_meta": {"title": "KSampler"},
            },
        }
        original_keys = set(workflow.keys())
        report = mod.inject_ipadapter_identity_lock(workflow, "/tmp/ref.png")
        # Original nodes must still exist
        for k in original_keys:
            self.assertIn(k, workflow)

    def test_inject_idempotent(self):
        """Injecting twice should update weight in-place, not duplicate nodes."""
        mod = self._get_module()
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "sd15.safetensors"},
                "_meta": {"title": "Load Checkpoint"},
            },
        }
        report1 = mod.inject_ipadapter_identity_lock(workflow, "/tmp/ref.png")
        count1 = len(workflow)
        report2 = mod.inject_ipadapter_identity_lock(workflow, "/tmp/ref2.png")
        count2 = len(workflow)
        # Second injection should update in-place, not add more nodes
        self.assertEqual(count1, count2)
        self.assertEqual(report2.get("mode"), "update")

    def test_custom_weight(self):
        mod = self._get_module()
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "sd15.safetensors"},
                "_meta": {"title": "Load Checkpoint"},
            },
        }
        report = mod.inject_ipadapter_identity_lock(workflow, "/tmp/ref.png", weight=0.70)
        # Find IPAdapterApply node and check weight
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") == "IPAdapterApply":
                self.assertAlmostEqual(node["inputs"]["weight"], 0.70, places=2)
                break

    # ── extract_visual_reference_path ──────────────────────────────
    def test_extract_from_visual_reference_path_key(self):
        """extract should find path from _visual_reference_path key (with real file)."""
        mod = self._get_module()
        # Create a temp file so Path.exists() returns True
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        try:
            ctx = {"_visual_reference_path": tmp_path}
            self.assertEqual(mod.extract_visual_reference_path(ctx), tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_extract_from_nested_extra(self):
        """extract should find path from nested director_studio_spec."""
        mod = self._get_module()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        try:
            ctx = {"director_studio_spec": {"_visual_reference_path": tmp_path}}
            self.assertEqual(mod.extract_visual_reference_path(ctx), tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_extract_returns_none_when_missing(self):
        mod = self._get_module()
        ctx = {"some_other_key": "value"}
        self.assertIsNone(mod.extract_visual_reference_path(ctx))

    # ── No numeric node IDs ────────────────────────────────────────
    def test_no_hardcoded_numeric_node_ids_in_source(self):
        """Source code must not contain hardcoded numeric node ID references."""
        mod = self._get_module()
        src = pathlib.Path(mod.__file__).read_text("utf-8")
        import re
        matches = re.findall(r'workflow\[[\"\'](\d+)[\"\']\]', src)
        self.assertEqual(len(matches), 0,
                         f"Found hardcoded numeric node IDs: {matches}")


# ═══════════════════════════════════════════════════════════════════════
# Group 2: Chunk Math Repair Tests
# ═══════════════════════════════════════════════════════════════════════

class TestChunkMathRepair(unittest.TestCase):
    """Tests for the frame_count rebinding fix in builtin_backends.py."""

    def test_rebinding_code_exists_in_source(self):
        src_path = _PROJECT_ROOT / "mathart" / "core" / "builtin_backends.py"
        src = src_path.read_text("utf-8")
        self.assertIn("_actual_frame_count", src)
        self.assertIn("frame_count = _actual_frame_count", src)

    def test_array_co_origin_assertion_exists(self):
        src_path = _PROJECT_ROOT / "mathart" / "core" / "builtin_backends.py"
        src = src_path.read_text("utf-8")
        self.assertIn("Array Co-Origin", src)
        self.assertIn("len(normal_arrays) == len(depth_arrays)", src)

    def test_chunk_size_clamped_after_rebinding(self):
        src_path = _PROJECT_ROOT / "mathart" / "core" / "builtin_backends.py"
        src = src_path.read_text("utf-8")
        self.assertIn("chunk_size = min(chunk_size, frame_count)", src)


# ═══════════════════════════════════════════════════════════════════════
# Group 3: OpenPose Skeleton Renderer Tests
# ═══════════════════════════════════════════════════════════════════════

class TestOpenPoseSkeletonRenderer(unittest.TestCase):
    """Tests for mathart.core.openpose_skeleton_renderer module."""

    def _get_module(self):
        return importlib.import_module("mathart.core.openpose_skeleton_renderer")

    def test_module_importable(self):
        mod = self._get_module()
        self.assertTrue(hasattr(mod, "render_openpose_sequence"))
        self.assertTrue(hasattr(mod, "arbitrate_controlnet_strengths"))

    def test_coco18_skeleton_definition(self):
        mod = self._get_module()
        self.assertEqual(len(mod.COCO_18_KEYPOINT_NAMES), 18)

    def test_render_single_frame(self):
        mod = self._get_module()
        import numpy as np
        skeleton = np.random.rand(18, 2).astype(np.float32)
        result = mod.render_openpose_sequence([skeleton])
        self.assertEqual(len(result), 1)
        from PIL import Image
        self.assertIsInstance(result[0], Image.Image)

    def test_render_multi_frame(self):
        mod = self._get_module()
        import numpy as np
        frames = [np.random.rand(18, 2).astype(np.float32) for _ in range(5)]
        result = mod.render_openpose_sequence(frames)
        self.assertEqual(len(result), 5)

    def test_render_canvas_size(self):
        """render_openpose_sequence uses width/height params, not canvas_size tuple."""
        mod = self._get_module()
        import numpy as np
        skeleton = np.random.rand(18, 2).astype(np.float32)
        result = mod.render_openpose_sequence([skeleton], width=256, height=256)
        self.assertEqual(result[0].size, (256, 256))

    def test_zero_cv2_dependency(self):
        mod = self._get_module()
        src = pathlib.Path(mod.__file__).read_text("utf-8")
        self.assertNotIn("import cv2", src)
        self.assertNotIn("from cv2", src)

    # ── ControlNet Arbitration ─────────────────────────────────────
    def test_arbitrate_dummy_mesh_mode(self):
        """In dummy mesh mode, depth/normal ControlNet should be softened."""
        mod = self._get_module()
        workflow = {
            "10": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 0.90, "control_net": ["cn_loader", 0]},
                "_meta": {"title": "Apply ControlNet - Depth"}
            },
            "11": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 0.90, "control_net": ["cn_loader2", 0]},
                "_meta": {"title": "Apply ControlNet - Normal"}
            },
        }
        # arbitrate_controlnet_strengths returns a report, modifies workflow in-place
        report = mod.arbitrate_controlnet_strengths(workflow, is_dummy_mesh=True)
        self.assertEqual(report.get("action"), "arbitrated")
        # Check that Depth/Normal were softened
        for nid, node in workflow.items():
            if node.get("class_type") == "ControlNetApplyAdvanced":
                title = node.get("_meta", {}).get("title", "")
                if "Depth" in title or "Normal" in title:
                    self.assertAlmostEqual(node["inputs"]["strength"], 0.45, places=2)

    def test_arbitrate_normal_mesh_no_change(self):
        """In normal mesh mode, no changes should be made."""
        mod = self._get_module()
        workflow = {
            "10": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"strength": 0.90},
                "_meta": {"title": "Apply ControlNet - Depth"}
            },
        }
        report = mod.arbitrate_controlnet_strengths(workflow, is_dummy_mesh=False)
        self.assertEqual(report.get("action"), "no_change")
        # Strength should remain unchanged
        self.assertEqual(workflow["10"]["inputs"]["strength"], 0.90)


# ═══════════════════════════════════════════════════════════════════════
# Group 4: Anti-Flicker Runtime Updates
# ═══════════════════════════════════════════════════════════════════════

class TestAntiFlickerRuntimeUpdates(unittest.TestCase):
    """Tests for SESSION-193 updates to anti_flicker_runtime.py."""

    def _get_module(self):
        return importlib.import_module("mathart.core.anti_flicker_runtime")

    def test_depth_normal_strength_is_045(self):
        mod = self._get_module()
        self.assertAlmostEqual(mod.DECOUPLED_DEPTH_NORMAL_STRENGTH, 0.45, places=2)

    def test_depth_normal_min_strength_is_040(self):
        mod = self._get_module()
        self.assertAlmostEqual(mod.DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH, 0.40, places=2)

    def test_session189_anchors_untouched(self):
        mod = self._get_module()
        self.assertEqual(mod.MAX_FRAMES, 16)
        self.assertEqual(mod.LATENT_EDGE, 512)
        self.assertEqual(tuple(mod.NORMAL_MATTE_RGB), (128, 128, 255))

    def test_telemetry_includes_openpose(self):
        mod = self._get_module()
        import io
        buf = io.StringIO()
        result = mod.emit_physics_telemetry_handshake(
            action_name="jump",
            skeleton_tensor_shape=(16, 24, 3),
            stream=buf,
        )
        output = buf.getvalue() + (result or "")
        self.assertIn("OpenPose", output)

    def test_rgb_strength_still_zero(self):
        mod = self._get_module()
        self.assertAlmostEqual(mod.DECOUPLED_RGB_STRENGTH, 0.0, places=2)

    def test_denoise_still_one(self):
        mod = self._get_module()
        self.assertAlmostEqual(mod.DECOUPLED_DENOISE, 1.0, places=2)

    def test_industrial_baking_banner_exists(self):
        mod = self._get_module()
        self.assertTrue(hasattr(mod, "emit_industrial_baking_banner"))


# ═══════════════════════════════════════════════════════════════════════
# Group 5: CLI Wizard Visual Reference Path
# ═══════════════════════════════════════════════════════════════════════

class TestCliWizardVisualReferencePath(unittest.TestCase):

    def test_visual_reference_path_in_source(self):
        src_path = _PROJECT_ROOT / "mathart" / "cli_wizard.py"
        src = src_path.read_text("utf-8")
        self.assertIn("_visual_reference_path", src)


# ═══════════════════════════════════════════════════════════════════════
# Group 6: Red-Line Compliance
# ═══════════════════════════════════════════════════════════════════════

class TestRedLineCompliance(unittest.TestCase):

    def test_no_proxy_env_vars_in_new_modules(self):
        """New modules must not reference proxy environment variable names
        in a way that reads or writes them. Docstring mentions of the
        red-line policy are acceptable only if they use the word 'proxy'
        generically, not the actual variable names."""
        new_files = [
            _PROJECT_ROOT / "mathart" / "core" / "identity_hydration.py",
            _PROJECT_ROOT / "mathart" / "core" / "openpose_skeleton_renderer.py",
        ]
        forbidden = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]
        for fpath in new_files:
            src = fpath.read_text("utf-8")
            for var in forbidden:
                self.assertNotIn(var, src,
                                 f"{fpath.name} references {var}")

    def test_project_brain_version(self):
        brain_path = _PROJECT_ROOT / "PROJECT_BRAIN.json"
        brain = json.loads(brain_path.read_text("utf-8"))
        self.assertEqual(brain["version"], "v1.0.4")
        self.assertEqual(brain["last_session_id"], "SESSION-193")

    def test_project_brain_has_new_contracts(self):
        brain_path = _PROJECT_ROOT / "PROJECT_BRAIN.json"
        brain = json.loads(brain_path.read_text("utf-8"))
        self.assertIn("identity_hydration_contract", brain)
        self.assertIn("openpose_arbitration_contract", brain)
        self.assertIn("chunk_math_repair_contract", brain)

    def test_session_handoff_is_193(self):
        handoff_path = _PROJECT_ROOT / "SESSION_HANDOFF.md"
        src = handoff_path.read_text("utf-8")
        self.assertIn("SESSION-193", src)

    def test_user_guide_has_section_23(self):
        guide_path = _PROJECT_ROOT / "docs" / "USER_GUIDE.md"
        src = guide_path.read_text("utf-8")
        self.assertIn("## 23.", src)


# ═══════════════════════════════════════════════════════════════════════
# Group 7: Document Completeness
# ═══════════════════════════════════════════════════════════════════════

class TestDocumentCompleteness(unittest.TestCase):

    def test_all_new_files_exist(self):
        expected_new = [
            "mathart/core/identity_hydration.py",
            "mathart/core/openpose_skeleton_renderer.py",
            "tests/test_session193_identity_chunk_openpose.py",
        ]
        for rel in expected_new:
            fpath = _PROJECT_ROOT / rel
            self.assertTrue(fpath.exists(), f"Missing: {rel}")

    def test_all_modified_files_exist(self):
        expected_modified = [
            "mathart/core/builtin_backends.py",
            "mathart/core/anti_flicker_runtime.py",
            "mathart/cli_wizard.py",
            "docs/USER_GUIDE.md",
            "SESSION_HANDOFF.md",
            "PROJECT_BRAIN.json",
        ]
        for rel in expected_modified:
            fpath = _PROJECT_ROOT / rel
            self.assertTrue(fpath.exists(), f"Missing: {rel}")


if __name__ == "__main__":
    unittest.main()
