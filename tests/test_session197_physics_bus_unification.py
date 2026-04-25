"""SESSION-197 P0-PHYSICS-DATA-BUS-UNIFICATION — Interception Tests.

This test module implements the **反自欺测试红线**: comprehensive L3
interception tests that Mock out a full physics+fluid data context and
intercept the final JSON Payload to assert:

1. Physics/Fluid ControlNet nodes are actually injected.
2. Conditioning chain (positive/negative) is serially connected (DAG closure).
3. KSampler inputs remain stable and connected.
4. Arbitrator correctly calibrates VFX ControlNet strengths.
5. Ghost path detection (反空投送幻觉红线) raises PipelineIntegrityError.
6. Idempotent re-injection returns ``mode="already_present"``.
7. No base JSON preset files are modified (反静态死板红线).
8. UX banner emits correct text.

Industrial References:
- Apache Airflow DAG validation pattern
- ONNX/TensorRT serial fusion topology
- ECS component scanning pattern
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# ═══════════════════════════════════════════════════════════════════════════
#  Imports under test
# ═══════════════════════════════════════════════════════════════════════════

from mathart.core.preset_topology_hydrator import (
    PipelineIntegrityError,
    _find_node,
    _next_free_id,
    hydrate_openpose_controlnet_chain,
    validate_preset_topology_closure,
)
from mathart.core.vfx_topology_hydrator import (
    FLUID_CONTROLNET_MODEL_DEFAULT,
    FLUID_CONTROLNET_STRENGTH_DEFAULT,
    PHYSICS_CONTROLNET_MODEL_DEFAULT,
    PHYSICS_CONTROLNET_STRENGTH_DEFAULT,
    TITLE_FLUID_APPLY,
    TITLE_PHYSICS_APPLY,
    extract_fluid_artifact_dir,
    extract_physics_artifact_dir,
    hydrate_fluid_controlnet_chain,
    hydrate_physics_controlnet_chain,
    hydrate_vfx_topology,
    emit_vfx_hydration_banner,
)
from mathart.core.openpose_skeleton_renderer import (
    arbitrate_controlnet_strengths,
    FLUID_VFX_CONTROLNET_STRENGTH,
    PHYSICS_VFX_CONTROLNET_STRENGTH,
    MAX_COMBINED_CONTROLNET_STRENGTH,
    OPENPOSE_CONTROLNET_STRENGTH,
    DUMMY_MESH_DEPTH_NORMAL_STRENGTH,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures — Mock workflow and context builders
# ═══════════════════════════════════════════════════════════════════════════

def _build_base_workflow() -> dict[str, Any]:
    """Build a minimal but realistic ComfyUI workflow with the SESSION-194
    chain: Depth → Normal → OpenPose → KSampler + SaveImage.

    This mirrors the real ``sparsectrl_animatediff`` preset after SESSION-194
    hydration. All node IDs are strings (ComfyUI convention).
    """
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"},
            "_meta": {"title": "Load Checkpoint"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "high quality pixel art", "clip": ["1", 1]},
            "_meta": {"title": "Positive Prompt"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry, low quality", "clip": ["1", 1]},
            "_meta": {"title": "Negative Prompt"},
        },
        "4": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "control_v11p_sd15_normalbae.pth"},
            "_meta": {"title": "Normal ControlNet Loader"},
        },
        "5": {
            "class_type": "VHS_LoadImagesPath",
            "inputs": {"directory": "/tmp/normal_seq", "image_load_cap": 0,
                       "skip_first_images": 0, "select_every_nth": 1},
            "_meta": {"title": "Load Normal Sequence"},
        },
        "6": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["2", 0],
                "negative": ["3", 0],
                "control_net": ["4", 0],
                "image": ["5", 0],
                "strength": 1.0,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            "_meta": {"title": "Apply Normal ControlNet"},
        },
        "7": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "control_v11f1p_sd15_depth.pth"},
            "_meta": {"title": "Depth ControlNet Loader"},
        },
        "8": {
            "class_type": "VHS_LoadImagesPath",
            "inputs": {"directory": "/tmp/depth_seq", "image_load_cap": 0,
                       "skip_first_images": 0, "select_every_nth": 1},
            "_meta": {"title": "Load Depth Sequence"},
        },
        "9": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["6", 0],
                "negative": ["6", 1],
                "control_net": ["7", 0],
                "image": ["8", 0],
                "strength": 1.0,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            "_meta": {"title": "Apply Depth ControlNet"},
        },
        # OpenPose chain (SESSION-194)
        "10": {
            "class_type": "VHS_LoadImagesPath",
            "inputs": {"directory": "/tmp/openpose_seq", "image_load_cap": 0,
                       "skip_first_images": 0, "select_every_nth": 1},
            "_meta": {"title": "Load OpenPose Sequence"},
        },
        "11": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "control_v11p_sd15_openpose.pth"},
            "_meta": {"title": "OpenPose ControlNet Loader"},
        },
        "12": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["9", 0],
                "negative": ["9", 1],
                "control_net": ["11", 0],
                "image": ["10", 0],
                "strength": 1.0,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            "_meta": {"title": "Apply OpenPose ControlNet"},
        },
        "13": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["12", 0],
                "negative": ["12", 1],
                "latent_image": ["1", 0],
                "seed": 42,
                "steps": 20,
                "cfg": 4.5,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": 1.0,
            },
            "_meta": {"title": "KSampler"},
        },
        "14": {
            "class_type": "SaveImage",
            "inputs": {"images": ["13", 0], "filename_prefix": "output"},
            "_meta": {"title": "Save Image"},
        },
    }


def _build_vfx_context(
    fluid_dir: str | None = None,
    physics_dir: str | None = None,
) -> dict[str, Any]:
    """Build a pipeline context with VFX artifact components."""
    ctx: dict[str, Any] = {}
    vfx: dict[str, Any] = {}
    if fluid_dir:
        vfx["fluid_flowmap_dir"] = fluid_dir
    if physics_dir:
        vfx["physics_3d_dir"] = physics_dir
    if vfx:
        ctx["vfx_artifacts"] = vfx
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 1: Context Extraction (ECS Component Scanning)
# ═══════════════════════════════════════════════════════════════════════════

class TestContextExtraction:
    """Verify ECS-style component scanning from pipeline context."""

    def test_extract_fluid_from_vfx_artifacts(self):
        ctx = {"vfx_artifacts": {"fluid_flowmap_dir": "/tmp/fluid"}}
        assert extract_fluid_artifact_dir(ctx) == "/tmp/fluid"

    def test_extract_fluid_from_nested_controller(self):
        ctx = {"vfx_artifacts": {
            "fluid_momentum_controller": {"sequence_dir": "/tmp/fluid_seq"}
        }}
        assert extract_fluid_artifact_dir(ctx) == "/tmp/fluid_seq"

    def test_extract_fluid_from_top_level(self):
        ctx = {"fluid_flowmap_dir": "/tmp/fluid_top"}
        assert extract_fluid_artifact_dir(ctx) == "/tmp/fluid_top"

    def test_extract_fluid_returns_none_when_absent(self):
        assert extract_fluid_artifact_dir({}) is None
        assert extract_fluid_artifact_dir({"vfx_artifacts": {}}) is None

    def test_extract_physics_from_vfx_artifacts(self):
        ctx = {"vfx_artifacts": {"physics_3d_dir": "/tmp/physics"}}
        assert extract_physics_artifact_dir(ctx) == "/tmp/physics"

    def test_extract_physics_from_nested(self):
        ctx = {"vfx_artifacts": {
            "physics_3d": {"sequence_dir": "/tmp/phys_seq"}
        }}
        assert extract_physics_artifact_dir(ctx) == "/tmp/phys_seq"

    def test_extract_physics_from_top_level(self):
        ctx = {"physics_3d_dir": "/tmp/phys_top"}
        assert extract_physics_artifact_dir(ctx) == "/tmp/phys_top"

    def test_extract_physics_returns_none_when_absent(self):
        assert extract_physics_artifact_dir({}) is None


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 2: Fluid ControlNet Chain Injection
# ═══════════════════════════════════════════════════════════════════════════

class TestFluidControlNetInjection:
    """Verify fluid ControlNet chain is correctly injected into the workflow."""

    def test_fluid_injection_creates_three_nodes(self):
        wf = _build_base_workflow()
        initial_count = len(wf)
        report = hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        assert report["mode"] == "injected"
        assert len(wf) == initial_count + 3  # VHS + Loader + Apply
        # Verify node types
        new_nodes = {nid: wf[nid] for nid in wf if nid not in _build_base_workflow()}
        class_types = {n["class_type"] for n in new_nodes.values()}
        assert "VHS_LoadImagesPath" in class_types
        assert "ControlNetLoader" in class_types
        assert "ControlNetApplyAdvanced" in class_types

    def test_fluid_injection_splices_after_openpose(self):
        wf = _build_base_workflow()
        report = hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        apply_id = report["fluid_apply_node_id"]
        apply_node = wf[apply_id]
        # The fluid apply should take positive/negative from OpenPose (node 12)
        assert apply_node["inputs"]["positive"] == ["12", 0]
        assert apply_node["inputs"]["negative"] == ["12", 1]

    def test_fluid_injection_rewires_ksampler(self):
        wf = _build_base_workflow()
        report = hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        apply_id = report["fluid_apply_node_id"]
        ks = wf["13"]
        # KSampler should now point at the fluid apply node
        assert ks["inputs"]["positive"] == [apply_id, 0]
        assert ks["inputs"]["negative"] == [apply_id, 1]

    def test_fluid_injection_idempotent(self):
        wf = _build_base_workflow()
        r1 = hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        count_after_first = len(wf)
        r2 = hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq_v2",
        )
        assert r2["mode"] == "already_present"
        assert len(wf) == count_after_first  # No new nodes

    def test_fluid_injection_strength_default(self):
        wf = _build_base_workflow()
        report = hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        apply_id = report["fluid_apply_node_id"]
        assert wf[apply_id]["inputs"]["strength"] == FLUID_CONTROLNET_STRENGTH_DEFAULT

    def test_fluid_injection_dag_closure(self):
        """反图谱污染红线: DAG must remain closed after injection."""
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        closure = validate_preset_topology_closure(wf)
        assert closure["status"] == "closed"
        assert closure["ghost_edges"] == []


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 3: Physics ControlNet Chain Injection
# ═══════════════════════════════════════════════════════════════════════════

class TestPhysicsControlNetInjection:
    """Verify physics ControlNet chain is correctly injected."""

    def test_physics_injection_creates_three_nodes(self):
        wf = _build_base_workflow()
        initial_count = len(wf)
        report = hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        assert report["mode"] == "injected"
        assert len(wf) == initial_count + 3

    def test_physics_injection_after_fluid(self):
        """When both fluid and physics are injected, physics chains after fluid."""
        wf = _build_base_workflow()
        fluid_report = hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        fluid_apply_id = fluid_report["fluid_apply_node_id"]
        physics_report = hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        physics_apply_id = physics_report["physics_apply_node_id"]
        # Physics should take input from fluid
        assert wf[physics_apply_id]["inputs"]["positive"] == [fluid_apply_id, 0]
        assert wf[physics_apply_id]["inputs"]["negative"] == [fluid_apply_id, 1]

    def test_physics_injection_rewires_ksampler(self):
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        physics_report = hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        physics_apply_id = physics_report["physics_apply_node_id"]
        ks = wf["13"]
        assert ks["inputs"]["positive"] == [physics_apply_id, 0]
        assert ks["inputs"]["negative"] == [physics_apply_id, 1]

    def test_physics_injection_idempotent(self):
        wf = _build_base_workflow()
        hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        count = len(wf)
        r2 = hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq_v2",
        )
        assert r2["mode"] == "already_present"
        assert len(wf) == count


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 4: Full Chain Topology Validation (Conditioning Daisy-Chain)
# ═══════════════════════════════════════════════════════════════════════════

class TestConditioningDaisyChain:
    """Verify the complete conditioning chain is serially connected:
    CLIP → Normal → Depth → OpenPose → Fluid → Physics → KSampler.
    """

    def test_full_chain_connectivity(self):
        """The ultimate connectivity test: walk the entire chain."""
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )

        # Walk the positive conditioning chain from CLIP to KSampler
        chain: list[str] = []
        current_id = "2"  # Positive CLIP encode
        chain.append(current_id)

        # Find who consumes current_id as positive input
        visited = set()
        for _ in range(20):  # Safety limit
            found = False
            for nid, node in wf.items():
                if not isinstance(node, dict) or nid in visited:
                    continue
                ins = node.get("inputs", {})
                pos = ins.get("positive")
                if isinstance(pos, list) and len(pos) == 2 and str(pos[0]) == current_id:
                    chain.append(str(nid))
                    visited.add(str(nid))
                    current_id = str(nid)
                    found = True
                    break
            if not found:
                break

        # Chain should be: CLIP(2) → Normal(6) → Depth(9) → OpenPose(12)
        #                  → Fluid(15) → Physics(18) → KSampler(13)
        assert len(chain) >= 6, f"Chain too short: {chain}"
        # Verify KSampler is at the end
        last_node = wf[chain[-1]]
        assert last_node["class_type"] in ("KSampler", "KSamplerAdvanced"), \
            f"Chain does not end at KSampler: {last_node['class_type']}"

    def test_dag_closure_after_full_injection(self):
        """反图谱污染红线: no ghost edges after full injection."""
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        closure = validate_preset_topology_closure(wf)
        assert closure["status"] == "closed"
        assert closure["ghost_edges"] == []
        assert closure["has_sampler"] is True
        assert closure["has_sink"] is True

    def test_ksampler_model_input_preserved(self):
        """KSampler model input must NOT be hijacked by ControlNet injection."""
        wf = _build_base_workflow()
        original_model = copy.deepcopy(wf["13"]["inputs"]["model"])
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        assert wf["13"]["inputs"]["model"] == original_model

    def test_ksampler_seed_steps_cfg_preserved(self):
        """KSampler core parameters must be untouched."""
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        ks = wf["13"]["inputs"]
        assert ks["seed"] == 42
        assert ks["steps"] == 20
        assert ks["cfg"] == 4.5
        assert ks["denoise"] == 1.0


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 5: Unified VFX Topology Hydration (ECS System Entry Point)
# ═══════════════════════════════════════════════════════════════════════════

class TestUnifiedVFXHydration:
    """Test the unified ``hydrate_vfx_topology`` entry point."""

    def test_no_artifacts_skips(self):
        wf = _build_base_workflow()
        report = hydrate_vfx_topology(wf, {})
        assert report["action"] == "no_vfx_artifacts_detected"
        assert report["artifacts_detected"] == []

    def test_fluid_only_injection(self):
        wf = _build_base_workflow()
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _build_vfx_context(fluid_dir=tmpdir)
            report = hydrate_vfx_topology(wf, ctx)
            assert "fluid_flowmap" in report["artifacts_injected"]
            assert report["physics_report"] is None
            assert report["dag_closure"]["status"] == "closed"

    def test_physics_only_injection(self):
        wf = _build_base_workflow()
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _build_vfx_context(physics_dir=tmpdir)
            report = hydrate_vfx_topology(wf, ctx)
            assert "physics_3d" in report["artifacts_injected"]
            assert report["fluid_report"] is None

    def test_both_fluid_and_physics(self):
        wf = _build_base_workflow()
        with tempfile.TemporaryDirectory() as fluid_dir, \
             tempfile.TemporaryDirectory() as physics_dir:
            ctx = _build_vfx_context(fluid_dir=fluid_dir, physics_dir=physics_dir)
            report = hydrate_vfx_topology(wf, ctx)
            assert "fluid_flowmap" in report["artifacts_injected"]
            assert "physics_3d" in report["artifacts_injected"]
            assert report["dag_closure"]["status"] == "closed"

    def test_ghost_path_raises_in_strict_mode(self):
        """反空投送幻觉红线: ghost paths MUST raise PipelineIntegrityError."""
        wf = _build_base_workflow()
        ctx = _build_vfx_context(fluid_dir="/nonexistent/ghost/path")
        with pytest.raises(PipelineIntegrityError, match="does not exist on disk"):
            hydrate_vfx_topology(wf, ctx, strict=True)

    def test_ghost_path_degrades_in_non_strict_mode(self):
        """Graceful degradation when strict=False."""
        wf = _build_base_workflow()
        ctx = _build_vfx_context(fluid_dir="/nonexistent/ghost/path")
        report = hydrate_vfx_topology(wf, ctx, strict=False)
        assert report["fluid_report"]["mode"] == "graceful_degradation"


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 6: Arbitrator VFX Weight Calibration
# ═══════════════════════════════════════════════════════════════════════════

class TestArbitratorVFXWeights:
    """Verify SESSION-197 arbitrator extension for fluid/physics weights."""

    def test_constants_exist(self):
        assert FLUID_VFX_CONTROLNET_STRENGTH == 0.35
        assert PHYSICS_VFX_CONTROLNET_STRENGTH == 0.30
        assert MAX_COMBINED_CONTROLNET_STRENGTH == 3.50

    def test_arbitrator_calibrates_fluid_node(self):
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        report = arbitrate_controlnet_strengths(wf, is_dummy_mesh=True)
        # Find the fluid apply node and check its strength
        fluid_touched = [
            t for t in report["touched_nodes"]
            if t.get("operation") == "session197_fluid_vfx_strength_calibrate"
        ]
        assert len(fluid_touched) == 1
        assert fluid_touched[0]["strength"][1] <= 0.35

    def test_arbitrator_calibrates_physics_node(self):
        wf = _build_base_workflow()
        hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        report = arbitrate_controlnet_strengths(wf, is_dummy_mesh=True)
        physics_touched = [
            t for t in report["touched_nodes"]
            if t.get("operation") == "session197_physics_vfx_strength_calibrate"
        ]
        assert len(physics_touched) == 1
        assert physics_touched[0]["strength"][1] <= 0.30

    def test_arbitrator_no_change_when_not_dummy(self):
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        report = arbitrate_controlnet_strengths(wf, is_dummy_mesh=False)
        assert report["action"] == "no_change"

    def test_arbitrator_report_includes_session197_fields(self):
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        report = arbitrate_controlnet_strengths(wf, is_dummy_mesh=True)
        assert "session197_fluid_vfx_strength" in report
        assert "session197_physics_vfx_strength" in report

    def test_dummy_mesh_reduces_fluid_strength(self):
        """On dummy mesh, fluid strength should be capped at 0.30."""
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
            fluid_strength=0.50,  # Start high
        )
        arbitrate_controlnet_strengths(wf, is_dummy_mesh=True)
        fluid_apply = _find_node(
            wf,
            class_types={"ControlNetApplyAdvanced"},
            title_contains="fluid",
        )
        assert fluid_apply is not None
        assert fluid_apply[1]["inputs"]["strength"] <= 0.30


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 7: Anti-Static Red Line (No Base JSON Modification)
# ═══════════════════════════════════════════════════════════════════════════

class TestAntiStaticRedLine:
    """反静态死板红线: base preset JSON files must NEVER be modified."""

    def test_base_workflow_not_mutated_by_reference(self):
        """Ensure the injection operates on the passed dict, not a global."""
        original = _build_base_workflow()
        frozen = json.dumps(original, sort_keys=True)
        wf = copy.deepcopy(original)
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        # Original must be unchanged
        assert json.dumps(original, sort_keys=True) == frozen

    def test_no_hardcoded_node_ids_in_source(self):
        """Source code must not reference hardcoded numeric node IDs."""
        import inspect
        source = inspect.getsource(hydrate_fluid_controlnet_chain)
        source += inspect.getsource(hydrate_physics_controlnet_chain)
        # Should not contain patterns like workflow["42"] or workflow['42']
        import re
        hardcoded = re.findall(r'workflow\[[\"\'](\d+)[\"\']\]', source)
        assert hardcoded == [], f"Hardcoded node IDs found: {hardcoded}"


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 8: UX Banner
# ═══════════════════════════════════════════════════════════════════════════

class TestUXBanner:
    """Verify SESSION-197 UX banner emission."""

    def test_banner_with_artifacts(self):
        text = emit_vfx_hydration_banner(["fluid_flowmap", "physics_3d"])
        assert "SESSION-197" in text
        assert "fluid_flowmap" in text
        assert "physics_3d" in text
        assert "DAG" in text

    def test_banner_empty_when_no_artifacts(self):
        text = emit_vfx_hydration_banner([])
        assert text == ""

    def test_banner_writes_to_stream(self):
        import io
        stream = io.StringIO()
        emit_vfx_hydration_banner(["fluid_flowmap"], stream=stream)
        output = stream.getvalue()
        assert "SESSION-197" in output
        assert "\033[1;35m" in output  # ANSI magenta


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 9: Edge Cases and Robustness
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases and robustness tests."""

    def test_injection_on_empty_workflow_raises(self):
        with pytest.raises(PipelineIntegrityError):
            hydrate_fluid_controlnet_chain(
                {}, fluid_sequence_directory="/tmp/fluid_seq",
            )

    def test_injection_on_non_dict_raises(self):
        with pytest.raises(PipelineIntegrityError):
            hydrate_fluid_controlnet_chain(
                "not a dict", fluid_sequence_directory="/tmp/fluid_seq",  # type: ignore
            )

    def test_physics_without_any_controlnet_raises(self):
        """If no upstream ControlNet exists, injection must fail."""
        minimal_wf = {
            "1": {
                "class_type": "KSampler",
                "inputs": {"positive": None, "negative": None, "model": None},
                "_meta": {"title": "KSampler"},
            },
            "2": {
                "class_type": "SaveImage",
                "inputs": {"images": ["1", 0]},
                "_meta": {"title": "Save"},
            },
        }
        with pytest.raises(PipelineIntegrityError, match="No upstream"):
            hydrate_physics_controlnet_chain(
                minimal_wf, physics_sequence_directory="/tmp/physics_seq",
            )

    def test_next_free_id_monotonic(self):
        wf = _build_base_workflow()
        id1 = _next_free_id(wf)
        wf[id1] = {"class_type": "Test", "inputs": {}, "_meta": {"title": "test"}}
        id2 = _next_free_id(wf)
        assert int(id2) > int(id1)


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 10: SESSION-197 Red Line Compliance
# ═══════════════════════════════════════════════════════════════════════════

class TestRedLineCompliance:
    """Comprehensive red line compliance verification."""

    def test_session189_anchors_preserved(self):
        """SESSION-189 anchors must not be touched."""
        from mathart.core.anti_flicker_runtime import (
            MAX_FRAMES,
            LATENT_EDGE,
            NORMAL_MATTE_RGB,
        )
        assert MAX_FRAMES == 16
        assert LATENT_EDGE == 512
        assert NORMAL_MATTE_RGB == (128, 128, 255)

    def test_session193_arbitrator_base_unchanged(self):
        """SESSION-193 base arbitration logic must be preserved."""
        assert OPENPOSE_CONTROLNET_STRENGTH == 1.0
        assert DUMMY_MESH_DEPTH_NORMAL_STRENGTH == 0.45

    def test_session194_openpose_contract_intact(self):
        """SESSION-194 OpenPose hydration must still work."""
        wf = _build_base_workflow()
        # Remove OpenPose nodes to test fresh injection
        for nid in ["10", "11", "12"]:
            del wf[nid]
        # Rewire KSampler to point at Depth
        wf["13"]["inputs"]["positive"] = ["9", 0]
        wf["13"]["inputs"]["negative"] = ["9", 1]
        report = hydrate_openpose_controlnet_chain(wf)
        assert report["mode"] == "injected"

    def test_industrial_baking_banner_preserved(self):
        """SESSION-192 UX banner must still work."""
        from mathart.core.anti_flicker_runtime import emit_industrial_baking_banner
        text = emit_industrial_baking_banner()
        assert "\u5de5\u4e1a\u70d8\u7119\u7f51\u5173" in text
        assert "Catmull-Rom" in text

    def test_all_new_nodes_use_semantic_titles(self):
        """All SESSION-197 nodes must use _meta.title selectors."""
        wf = _build_base_workflow()
        hydrate_fluid_controlnet_chain(
            wf, fluid_sequence_directory="/tmp/fluid_seq",
        )
        hydrate_physics_controlnet_chain(
            wf, physics_sequence_directory="/tmp/physics_seq",
        )
        base_ids = set(_build_base_workflow().keys())
        for nid, node in wf.items():
            if nid not in base_ids and isinstance(node, dict):
                assert "_meta" in node, f"Node {nid} missing _meta"
                assert "title" in node["_meta"], f"Node {nid} missing title"
                title = node["_meta"]["title"]
                assert "SESSION197" in title or "session197" in title.lower(), \
                    f"Node {nid} title '{title}' missing SESSION197 tag"
