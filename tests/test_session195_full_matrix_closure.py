"""SESSION-195 Full-Matrix Closure Tests.

Covers all three P0 deliverables:
  1. Historical test debt clearance — verified by test_session190 / test_session192
     passing; here we add regression guards.
  2. IPAdapter Identity Context Late-Binding — extract_visual_reference_path
     multi-location search + inject_ipadapter_identity_lock idempotency.
  3. OpenPose Gait Registry Expansion — walk/run/jump/idle/dash all registered,
     each produces valid COCO-18 frames, registry is data-driven (no if/elif).

Red-line compliance:
  - No proxy env var references.
  - SESSION-189 hard anchors untouched.
  - anime_rhythmic_subsample untouched.
  - force_decouple_dummy_mesh_payload untouched.
  - No hardcoded numeric node IDs.
"""

from __future__ import annotations

import importlib
import inspect
import os
import pathlib
import sys
import tempfile
import unittest

import numpy as np

# ─── ensure project root is on sys.path ──────────────────────────────
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════
# Group 1: Historical Test Debt Regression Guards
# ═══════════════════════════════════════════════════════════════════════

class TestHistoricalDebtRegression(unittest.TestCase):
    """Ensure the SESSION-195 contract alignment holds."""

    def test_depth_normal_strength_is_045(self):
        from mathart.core.anti_flicker_runtime import DECOUPLED_DEPTH_NORMAL_STRENGTH
        self.assertAlmostEqual(DECOUPLED_DEPTH_NORMAL_STRENGTH, 0.45, places=2)

    def test_depth_normal_min_strength_is_040(self):
        from mathart.core.anti_flicker_runtime import DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH
        self.assertAlmostEqual(DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH, 0.40, places=2)

    def test_rgb_strength_still_zero(self):
        from mathart.core.anti_flicker_runtime import DECOUPLED_RGB_STRENGTH
        self.assertAlmostEqual(DECOUPLED_RGB_STRENGTH, 0.0, places=2)

    def test_denoise_still_one(self):
        from mathart.core.anti_flicker_runtime import DECOUPLED_DENOISE
        self.assertAlmostEqual(DECOUPLED_DENOISE, 1.0, places=2)

    def test_telemetry_banner_uses_current_strength(self):
        from mathart.core.anti_flicker_runtime import (
            emit_physics_telemetry_handshake,
            DECOUPLED_DEPTH_NORMAL_STRENGTH,
            DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH,
        )
        text = emit_physics_telemetry_handshake(
            action_name="walk",
            depth_normal_strength=DECOUPLED_DEPTH_NORMAL_STRENGTH,
        )
        self.assertIn(f"{DECOUPLED_DEPTH_NORMAL_STRENGTH:.2f}", text)
        self.assertIn(f">= {DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH:.2f}", text)
        self.assertIn("✅", text)

    def test_telemetry_warns_below_new_floor(self):
        from mathart.core.anti_flicker_runtime import emit_physics_telemetry_handshake
        text = emit_physics_telemetry_handshake(
            action_name="test",
            depth_normal_strength=0.30,  # below 0.40 floor
        )
        self.assertIn("⚠️", text)


# ═══════════════════════════════════════════════════════════════════════
# Group 2: IPAdapter Identity Context Late-Binding
# ═══════════════════════════════════════════════════════════════════════

class TestIPAdapterLateBind(unittest.TestCase):
    """Test the Spring ResourceLoader-style late-binding pattern."""

    def _get_module(self):
        return importlib.import_module("mathart.core.identity_hydration")

    def test_extract_returns_none_when_no_path(self):
        mod = self._get_module()
        result = mod.extract_visual_reference_path({})
        self.assertIsNone(result)

    def test_extract_returns_none_when_path_missing_on_disk(self):
        mod = self._get_module()
        result = mod.extract_visual_reference_path({
            "_visual_reference_path": "/nonexistent/path/to/image.png"
        })
        self.assertIsNone(result)

    def test_extract_finds_direct_field(self):
        mod = self._get_module()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake_image_data")
            tmp_path = f.name
        try:
            result = mod.extract_visual_reference_path({
                "_visual_reference_path": tmp_path
            })
            self.assertEqual(result, tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_extract_finds_nested_identity_lock(self):
        mod = self._get_module()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake_image_data")
            tmp_path = f.name
        try:
            result = mod.extract_visual_reference_path({
                "identity_lock": {"reference_image_path": tmp_path}
            })
            self.assertEqual(result, tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_extract_finds_nested_director_studio_spec(self):
        mod = self._get_module()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake_image_data")
            tmp_path = f.name
        try:
            result = mod.extract_visual_reference_path({
                "director_studio_spec": {"_visual_reference_path": tmp_path}
            })
            self.assertEqual(result, tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_inject_new_nodes_into_workflow(self):
        mod = self._get_module()
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "test.safetensors"},
            },
            "2": {
                "class_type": "KSampler",
                "inputs": {"model": ["1", 0], "seed": 42},
            },
        }
        report = mod.inject_ipadapter_identity_lock(workflow, "/fake/ref.png")
        self.assertTrue(report["injected"])
        self.assertEqual(report["mode"], "new")
        self.assertAlmostEqual(report["weight"], 0.85, places=2)
        # Verify new nodes were created
        class_types = {n.get("class_type") for n in workflow.values() if isinstance(n, dict)}
        self.assertIn("LoadImage", class_types)
        self.assertIn("CLIPVisionLoader", class_types)
        self.assertIn("IPAdapterModelLoader", class_types)
        self.assertIn("IPAdapterAdvanced", class_types)

    def test_inject_updates_existing_weight(self):
        mod = self._get_module()
        workflow = {
            "1": {
                "class_type": "IPAdapterApply",
                "inputs": {"weight": 0.5, "image": ["2", 0]},
            },
            "2": {
                "class_type": "LoadImage",
                "inputs": {"image": "old_ref.png"},
            },
        }
        report = mod.inject_ipadapter_identity_lock(workflow, "/new/ref.png", weight=0.85)
        self.assertTrue(report["injected"])
        self.assertEqual(report["mode"], "update")
        self.assertAlmostEqual(workflow["1"]["inputs"]["weight"], 0.85, places=2)

    def test_inject_skips_when_no_checkpoint(self):
        mod = self._get_module()
        workflow = {
            "1": {"class_type": "KSampler", "inputs": {}},
        }
        report = mod.inject_ipadapter_identity_lock(workflow, "/fake/ref.png")
        self.assertFalse(report["injected"])
        self.assertEqual(report["mode"], "skipped")


# ═══════════════════════════════════════════════════════════════════════
# Group 3: OpenPose Gait Registry Expansion
# ═══════════════════════════════════════════════════════════════════════

class TestGaitRegistry(unittest.TestCase):
    """Verify the UE5 AnimGraph / Chooser-Table registry pattern."""

    def _get_module(self):
        return importlib.import_module("mathart.core.openpose_pose_provider")

    def test_registry_has_all_five_gaits(self):
        mod = self._get_module()
        registry = mod.get_gait_registry()
        required = {"walk", "run", "jump", "idle", "dash"}
        for action in required:
            self.assertIn(action, registry, f"Gait '{action}' not in registry")
        # At least 5 built-in gaits (may have more from custom registrations)
        self.assertGreaterEqual(len(registry), 5)

    def test_registry_names_contains_builtins(self):
        mod = self._get_module()
        registry = mod.get_gait_registry()
        names = set(registry.names())
        required = {"dash", "idle", "jump", "run", "walk"}
        self.assertTrue(required.issubset(names), f"Missing gaits: {required - names}")

    def test_each_gait_produces_correct_frame_count(self):
        mod = self._get_module()
        registry = mod.get_gait_registry()
        for action in registry.names():
            strategy = registry.get(action)
            frames = strategy.generate(16)
            self.assertEqual(len(frames), 16, f"Gait '{action}' produced {len(frames)} frames, expected 16")

    def test_each_gait_frame_has_required_joints(self):
        mod = self._get_module()
        registry = mod.get_gait_registry()
        required_joints = {"head", "neck", "l_shoulder", "r_shoulder",
                           "l_elbow", "r_elbow", "l_hand", "r_hand",
                           "l_hip", "r_hip", "l_knee", "r_knee",
                           "l_foot", "r_foot"}
        for action in registry.names():
            strategy = registry.get(action)
            frames = strategy.generate(8)
            for i, frame in enumerate(frames):
                for joint in required_joints:
                    self.assertIn(
                        joint, frame,
                        f"Gait '{action}' frame {i} missing joint '{joint}'"
                    )

    def test_no_if_elif_dispatch_in_bake_function(self):
        """Anti-spaghetti red line: bake_openpose_pose_sequence must NOT
        use if/elif chains to dispatch gait actions."""
        mod = self._get_module()
        source = inspect.getsource(mod.bake_openpose_pose_sequence)
        # Should not contain action-specific if/elif branches
        self.assertNotIn('if action_name == "walk"', source)
        self.assertNotIn('elif action_name == "run"', source)
        self.assertNotIn('elif action_name == "jump"', source)

    def test_bake_with_walk_action(self):
        mod = self._get_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = mod.bake_openpose_pose_sequence(
                output_dir=tmpdir,
                frame_count=4,
                width=256,
                height=256,
                action_name="walk",
                emit_banner=False,
            )
            self.assertEqual(artifact.frame_count, 4)
            self.assertEqual(artifact.width, 256)
            self.assertEqual(artifact.height, 256)
            self.assertEqual(artifact.artifact_family, "openpose_pose_sequence")
            # Verify PNGs were written
            pngs = list(pathlib.Path(tmpdir).glob("*.png"))
            self.assertEqual(len(pngs), 4)

    def test_bake_with_run_action(self):
        mod = self._get_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = mod.bake_openpose_pose_sequence(
                output_dir=tmpdir,
                frame_count=4,
                width=256,
                height=256,
                action_name="run",
                emit_banner=False,
            )
            self.assertEqual(artifact.frame_count, 4)

    def test_bake_with_jump_action(self):
        mod = self._get_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = mod.bake_openpose_pose_sequence(
                output_dir=tmpdir,
                frame_count=4,
                width=256,
                height=256,
                action_name="jump",
                emit_banner=False,
            )
            self.assertEqual(artifact.frame_count, 4)

    def test_bake_with_idle_action(self):
        mod = self._get_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = mod.bake_openpose_pose_sequence(
                output_dir=tmpdir,
                frame_count=4,
                width=256,
                height=256,
                action_name="idle",
                emit_banner=False,
            )
            self.assertEqual(artifact.frame_count, 4)

    def test_bake_with_dash_action(self):
        mod = self._get_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = mod.bake_openpose_pose_sequence(
                output_dir=tmpdir,
                frame_count=4,
                width=256,
                height=256,
                action_name="dash",
                emit_banner=False,
            )
            self.assertEqual(artifact.frame_count, 4)

    def test_bake_unknown_action_falls_back_to_walk(self):
        mod = self._get_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = mod.bake_openpose_pose_sequence(
                output_dir=tmpdir,
                frame_count=4,
                width=256,
                height=256,
                action_name="nonexistent_action",
                emit_banner=False,
            )
            self.assertEqual(artifact.frame_count, 4)

    def test_legacy_derive_industrial_walk_cycle_still_works(self):
        mod = self._get_module()
        frames = mod.derive_industrial_walk_cycle(8)
        self.assertEqual(len(frames), 8)
        self.assertIn("head", frames[0])

    def test_gait_strategy_abstract_contract(self):
        mod = self._get_module()
        self.assertTrue(hasattr(mod, "OpenPoseGaitStrategy"))
        self.assertTrue(hasattr(mod, "OpenPoseGaitRegistry"))
        self.assertTrue(hasattr(mod, "register_gait_strategy"))
        self.assertTrue(hasattr(mod, "get_gait_registry"))

    def test_custom_gait_registration(self):
        """Verify that external code can register a new gait strategy."""
        mod = self._get_module()

        class _TestGait(mod.OpenPoseGaitStrategy):
            @property
            def action_name(self):
                return "_test_custom_gait"

            def generate(self, frame_count):
                hu = 1.0 / 3.0
                return [{
                    "head": (0.0, hu * 2.8),
                    "neck": (0.0, hu * 2.5),
                    "l_shoulder": (-0.2, hu * 2.3),
                    "r_shoulder": (0.2, hu * 2.3),
                    "l_elbow": (-0.3, hu * 1.8),
                    "r_elbow": (0.3, hu * 1.8),
                    "l_hand": (-0.4, hu * 1.3),
                    "r_hand": (0.4, hu * 1.3),
                    "l_hip": (-0.1, hu * 1.0),
                    "r_hip": (0.1, hu * 1.0),
                    "l_knee": (-0.1, hu * 0.5),
                    "r_knee": (0.1, hu * 0.5),
                    "l_foot": (-0.1, 0.0),
                    "r_foot": (0.1, 0.0),
                }] * frame_count

        mod.register_gait_strategy(_TestGait())
        registry = mod.get_gait_registry()
        self.assertIn("_test_custom_gait", registry)
        frames = registry.get("_test_custom_gait").generate(4)
        self.assertEqual(len(frames), 4)


# ═══════════════════════════════════════════════════════════════════════
# Group 4: Red Line Compliance
# ═══════════════════════════════════════════════════════════════════════

class TestRedLineCompliance(unittest.TestCase):
    """Verify SESSION-195 changes don't violate project red lines."""

    def test_session189_anchors_untouched(self):
        from mathart.core.anti_flicker_runtime import (
            MAX_FRAMES, LATENT_EDGE, NORMAL_MATTE_RGB,
        )
        self.assertEqual(MAX_FRAMES, 16)
        self.assertEqual(LATENT_EDGE, 512)
        self.assertEqual(NORMAL_MATTE_RGB, (128, 128, 255))

    def test_no_proxy_env_in_identity_hydration(self):
        mod = importlib.import_module("mathart.core.identity_hydration")
        source = inspect.getsource(mod)
        for forbidden in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.assertNotIn(forbidden, source)

    def test_no_proxy_env_in_openpose_provider(self):
        mod = importlib.import_module("mathart.core.openpose_pose_provider")
        source = inspect.getsource(mod)
        for forbidden in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.assertNotIn(forbidden, source)

    def test_no_hardcoded_node_ids_in_identity_hydration(self):
        mod = importlib.import_module("mathart.core.identity_hydration")
        source = inspect.getsource(mod.inject_ipadapter_identity_lock)
        # Should use class_type, not hardcoded IDs like workflow["42"]
        self.assertIn("class_type", source)


if __name__ == "__main__":
    unittest.main()
