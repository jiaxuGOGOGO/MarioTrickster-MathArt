"""SESSION-194 P0-PIPELINE-INTEGRATION-CLOSURE regression suite.

These tests act as the "拦截测试" mandated by the SESSION-194 directive:
they exercise the **real** ``ComfyUIPresetManager.assemble_sequence_payload``
plus the trunk-level ``openpose_pose_provider`` and assert that:

1. The assembled workflow contains the SESSION-194 OpenPose ControlNet
   chain (``VHS_LoadImagesPath`` + ``ControlNetLoader`` +
   ``ControlNetApplyAdvanced``) wired with the correct semantic titles
   and ``strength == 1.0``.
2. The assembled workflow contains the SESSION-194 IPAdapter quartet
   wired through the ``KSampler.model`` input.
3. After the trunk's OpenPose pose-provider bake, the
   ``VHS_LoadImagesPath.directory`` no longer contains the sentinel
   placeholder and instead points at a directory that physically exists
   on disk and contains at least ``frame_count`` PNG frames.
4. ``validate_preset_topology_closure`` reports zero ghost edges (Airflow
   DAG closure invariant).
5. The arbitrator (``arbitrate_controlnet_strengths``) lifts the OpenPose
   strength to ``1.0`` and softens Depth/Normal to ``0.45`` only when
   ``is_dummy_mesh=True``, and is a no-op otherwise.
6. ``PipelineIntegrityError`` is raised on intentionally corrupted
   workflow input (ghost edge, missing sampler / sink).

Red-line compliance:
  * No live ComfyUI HTTP server is contacted; the test is fully offline.
  * No SESSION-189 / SESSION-190 anchors are touched; the new code only
    adds nodes and rewires conditioning edges.
  * No proxy environment variables are referenced.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import unittest


_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from mathart.animation.comfyui_preset_manager import (  # noqa: E402
    ComfyUIPresetManager,
    _SPARSECTRL_PRESET_NAME,
)
from mathart.core.preset_topology_hydrator import (  # noqa: E402
    OPENPOSE_SEQUENCE_DIR_SENTINEL,
    PipelineIntegrityError,
    hydrate_openpose_controlnet_chain,
    hydrate_ipadapter_quartet,
    validate_preset_topology_closure,
)
from mathart.core.openpose_pose_provider import (  # noqa: E402
    bake_openpose_pose_sequence,
    derive_industrial_walk_cycle,
)
from mathart.core.openpose_skeleton_renderer import (  # noqa: E402
    OPENPOSE_CONTROLNET_STRENGTH,
    arbitrate_controlnet_strengths,
)


def _assemble_default_payload(frame_count: int = 8) -> dict:
    """Build a real assembled payload for the SparseCtrl preset."""
    manager = ComfyUIPresetManager()
    return manager.assemble_sequence_payload(
        preset_name=_SPARSECTRL_PRESET_NAME,
        normal_sequence_dir="/tmp/session194_normal",
        depth_sequence_dir="/tmp/session194_depth",
        rgb_sequence_dir="/tmp/session194_rgb",
        prompt="a high quality 3a character, cinematic lighting, masterpiece",
        negative_prompt="blurry, low quality, deformed",
        frame_count=frame_count,
    )


def _find_node_by_class_and_title(workflow: dict, class_type: str, title_needle: str):
    needle = title_needle.lower()
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != class_type:
            continue
        title = str(node.get("_meta", {}).get("title", "")).lower()
        if needle in title:
            return nid, node
    return None


class OpenPoseChainAssemblyTests(unittest.TestCase):
    """Assert that the OpenPose ControlNet chain is wired into the payload."""

    def test_openpose_apply_node_present_with_strength_1(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        match = _find_node_by_class_and_title(
            workflow, "ControlNetApplyAdvanced", "apply openpose controlnet"
        )
        self.assertIsNotNone(match, "OpenPose ControlNetApplyAdvanced node missing")
        _nid, node = match
        self.assertEqual(node["inputs"]["strength"], 1.0)
        self.assertIn("control_net", node["inputs"])
        self.assertIn("image", node["inputs"])
        self.assertIn("positive", node["inputs"])
        self.assertIn("negative", node["inputs"])

    def test_openpose_loader_and_vhs_present(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        loader = _find_node_by_class_and_title(
            workflow, "ControlNetLoader", "openpose controlnet loader"
        )
        vhs = _find_node_by_class_and_title(
            workflow, "VHS_LoadImagesPath", "load openpose sequence"
        )
        self.assertIsNotNone(loader, "OpenPose ControlNetLoader missing")
        self.assertIsNotNone(vhs, "OpenPose VHS_LoadImagesPath missing")
        self.assertEqual(
            vhs[1]["inputs"]["directory"], OPENPOSE_SEQUENCE_DIR_SENTINEL
        )

    def test_lock_manifest_records_session194_closure(self):
        payload = _assemble_default_payload()
        manifest = payload["mathart_lock_manifest"]
        self.assertTrue(manifest.get("session194_pipeline_integration_closure"))
        self.assertEqual(
            manifest["session194_openpose_chain"]["mode"], "injected"
        )
        self.assertEqual(
            manifest["session194_ipadapter_chain"]["mode"], "injected"
        )
        self.assertEqual(
            manifest["session194_dag_closure"]["status"], "closed"
        )

    def test_idempotent_double_hydration_does_not_duplicate_nodes(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        node_count_before = len(workflow)
        report = hydrate_openpose_controlnet_chain(workflow)
        self.assertEqual(report["mode"], "already_present")
        self.assertEqual(len(workflow), node_count_before)

    def test_ipadapter_apply_present_and_wired_into_ksampler(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        # Find the IPAdapterAdvanced node we injected
        ipa = None
        for nid, node in workflow.items():
            if isinstance(node, dict) and node.get("class_type") == "IPAdapterAdvanced":
                ipa = (nid, node)
                break
        self.assertIsNotNone(ipa, "SESSION-194 IPAdapterAdvanced node missing")
        # KSampler.model must be wired through the IPAdapterAdvanced output
        ks = None
        for nid, node in workflow.items():
            if isinstance(node, dict) and str(node.get("class_type", "")).startswith("KSampler"):
                ks = (nid, node)
                break
        self.assertIsNotNone(ks, "KSampler missing")
        model_ref = ks[1]["inputs"].get("model")
        self.assertIsInstance(model_ref, list)
        self.assertEqual(str(model_ref[0]), str(ipa[0]))


class OpenPoseBakeProviderTests(unittest.TestCase):
    """Assert the IoC payload provider physically lands PNGs on disk."""

    def test_bake_produces_frame_count_png_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = bake_openpose_pose_sequence(
                output_dir=tmp,
                frame_count=8,
                width=256,
                height=256,
                emit_banner=False,
            )
            self.assertEqual(artifact.frame_count, 8)
            self.assertEqual(artifact.artifact_family, "openpose_pose_sequence")
            self.assertEqual(artifact.backend_type, "openpose_skeleton_render")
            png_paths = sorted(pathlib.Path(tmp).glob("*.png"))
            self.assertEqual(len(png_paths), 8)
            # Each PNG must be a non-empty file
            for p in png_paths:
                self.assertGreater(p.stat().st_size, 100)

    def test_walk_cycle_is_temporally_varying(self):
        frames = derive_industrial_walk_cycle(8)
        # The left-hand y position should differ between frames 0 and 4
        # (heel-strike vs mid-stance) by a measurable amount
        diff = abs(frames[0]["l_hand"][1] - frames[4]["l_hand"][1])
        self.assertGreater(diff, 1e-6)

    def test_bake_then_rebind_directory_simulates_trunk_handoff(self):
        """Mimic the SESSION-194 trunk handoff: assemble payload, bake
        OpenPose to disk, rebind the sentinel directory, ensure it points
        at a real folder with PNG files."""
        with tempfile.TemporaryDirectory() as tmp:
            payload = _assemble_default_payload()
            workflow = payload["prompt"]
            artifact = bake_openpose_pose_sequence(
                output_dir=pathlib.Path(tmp) / "openpose_pose",
                frame_count=8,
                width=256,
                height=256,
                emit_banner=False,
            )
            # Trunk-style rebind
            for node in workflow.values():
                if isinstance(node, dict) and node.get("class_type") == "VHS_LoadImagesPath":
                    ins = node.setdefault("inputs", {})
                    if ins.get("directory") == OPENPOSE_SEQUENCE_DIR_SENTINEL:
                        ins["directory"] = artifact.sequence_directory
            # Verify
            vhs = _find_node_by_class_and_title(
                workflow, "VHS_LoadImagesPath", "load openpose sequence"
            )
            self.assertIsNotNone(vhs)
            resolved_dir = pathlib.Path(vhs[1]["inputs"]["directory"])
            self.assertTrue(resolved_dir.exists())
            png_count = len(list(resolved_dir.glob("*.png")))
            self.assertGreaterEqual(png_count, 8)


class ArbitratorTriggerTests(unittest.TestCase):
    """Assert the SESSION-193 arbitrator runs cleanly on the SESSION-194 graph."""

    def test_arbitrator_no_change_when_not_dummy(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        snapshot = json.dumps(workflow, sort_keys=True)
        report = arbitrate_controlnet_strengths(workflow, is_dummy_mesh=False)
        self.assertEqual(report["action"], "no_change")
        self.assertEqual(json.dumps(workflow, sort_keys=True), snapshot)

    def test_arbitrator_lifts_openpose_to_1_when_dummy(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        # Pre-set OpenPose strength to a low value to verify arbitration lifts it
        match = _find_node_by_class_and_title(
            workflow, "ControlNetApplyAdvanced", "apply openpose controlnet"
        )
        match[1]["inputs"]["strength"] = 0.30
        report = arbitrate_controlnet_strengths(workflow, is_dummy_mesh=True)
        self.assertEqual(report["action"], "arbitrated")
        # Verify the OpenPose strength was lifted back to 1.0
        new_strength = match[1]["inputs"]["strength"]
        self.assertEqual(new_strength, OPENPOSE_CONTROLNET_STRENGTH)

    def test_arbitrator_softens_depth_normal_to_0_45_when_dummy(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        depth_match = _find_node_by_class_and_title(
            workflow, "ControlNetApplyAdvanced", "apply depth controlnet"
        )
        normal_match = _find_node_by_class_and_title(
            workflow, "ControlNetApplyAdvanced", "apply normal controlnet"
        )
        self.assertIsNotNone(depth_match)
        self.assertIsNotNone(normal_match)
        # Pre-set strengths to arbitrary values to ensure arbitration overwrites
        depth_match[1]["inputs"]["strength"] = 1.0
        normal_match[1]["inputs"]["strength"] = 1.0
        arbitrate_controlnet_strengths(workflow, is_dummy_mesh=True)
        self.assertAlmostEqual(depth_match[1]["inputs"]["strength"], 0.45)
        self.assertAlmostEqual(normal_match[1]["inputs"]["strength"], 0.45)


class DAGClosureTests(unittest.TestCase):
    """Assert validate_preset_topology_closure rejects malformed graphs."""

    def test_closure_passes_on_assembled_payload(self):
        payload = _assemble_default_payload()
        report = validate_preset_topology_closure(payload["prompt"])
        self.assertEqual(report["status"], "closed")
        self.assertEqual(report["ghost_edges"], [])

    def test_closure_fails_on_ghost_edge(self):
        payload = _assemble_default_payload()
        workflow = copy.deepcopy(payload["prompt"])
        # Inject a ghost edge: point a real node's input at an ID that
        # does not exist.
        ksampler_id = next(
            nid for nid, node in workflow.items()
            if isinstance(node, dict) and str(node.get("class_type", "")).startswith("KSampler")
        )
        workflow[ksampler_id]["inputs"]["positive"] = ["99999_phantom", 0]
        with self.assertRaises(PipelineIntegrityError):
            validate_preset_topology_closure(workflow)

    def test_closure_fails_when_sampler_missing(self):
        payload = _assemble_default_payload()
        workflow = copy.deepcopy(payload["prompt"])
        for nid in list(workflow.keys()):
            if isinstance(workflow[nid], dict) and str(workflow[nid].get("class_type", "")).startswith("KSampler"):
                del workflow[nid]
        with self.assertRaises(PipelineIntegrityError):
            validate_preset_topology_closure(workflow)


class IPAdapterIdempotencyTests(unittest.TestCase):
    """Assert the IPAdapter quartet hydrator is idempotent."""

    def test_ipadapter_double_hydration_only_updates_weight(self):
        payload = _assemble_default_payload()
        workflow = payload["prompt"]
        nodes_before = len(workflow)
        report = hydrate_ipadapter_quartet(workflow, weight=0.5)
        self.assertEqual(report["mode"], "already_present")
        self.assertEqual(len(workflow), nodes_before)
        # Verify weight was refreshed
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") in {
                "IPAdapterAdvanced", "IPAdapterApply", "IPAdapter",
            }:
                self.assertEqual(node["inputs"]["weight"], 0.5)
                break


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
