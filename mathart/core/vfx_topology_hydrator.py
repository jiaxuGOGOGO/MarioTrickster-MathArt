"""SESSION-197 VFX Topology Hydrator — Physics & Fluid ControlNet Dynamic Injection.

This module implements the **ECS-style system** that scans the payload assembly
context for physics/fluid artifact components and dynamically injects the
corresponding ``ControlNetApplyAdvanced`` + ``VHS_LoadImagesPath`` /
``ControlNetLoader`` node chains into the ComfyUI workflow JSON **at runtime**.

Industrial References (see ``docs/RESEARCH_NOTES_SESSION_197.md``):

* **Houdini PDG (Procedural Dependency Graph)**: physics/fluid computation
  outputs are treated as *work items* flowing through a dependency graph.
  The hydrator transforms these work items into conditioning streams
  (ControlNet inputs) at the assembly site — a layer-based injection where
  physics artifacts become independent control flow layers woven into the
  render pipeline.

* **Entity Component System (ECS) — Data-Oriented Design**: ``VFX_FLOWMAP``
  and ``PHYSICS_3D`` outputs are *components* attached to the render entity.
  The hydrator acts as a *system*: it scans the payload context for these
  component signatures. When detected, it triggers the corresponding AST
  node graph hydration logic, achieving **absolute decoupling**.

* **ONNX/TensorRT Computation Graph Multi-Path Fusion Topology**: multiple
  ControlNets MUST be chained in **serial topology** (daisy-chain). Each
  ``ControlNetApplyAdvanced`` node takes ``positive``/``negative`` from the
  **previous** node's output. Parallel/disconnected paths cause "断头覆盖"
  (decapitation override).

Hard Red Lines (SESSION-197):

* **反空投送幻觉红线**: Every artifact path is validated with ``os.path.exists()``
  before injection. Missing files trigger ``PipelineIntegrityError`` or
  graceful degradation — NEVER a ghost path to ComfyUI.

* **反图谱污染红线**: New nodes splice strictly AFTER the last existing
  ControlNet apply node (OpenPose). All wiring uses the previous node's
  output as input, forming a perfect DAG chain. A single broken wire raises
  ``PipelineIntegrityError``.

* **反静态死板红线**: Zero modification to base JSON preset files. All node
  injection happens in-memory at runtime via AST-style dict operations.

* No string concatenation or regex on the JSON.
* No hardcoded numeric IDs — all nodes located via ``class_type`` +
  ``_meta.title`` selectors.
* Idempotent: invoking twice on the same workflow is a no-op the second time.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mathart.core.preset_topology_hydrator import (
    PipelineIntegrityError,
    _find_node,
    _next_free_id,
    validate_preset_topology_closure,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Constants — ControlNet models & node titles for physics/fluid chains
# ═══════════════════════════════════════════════════════════════════════════

# Fluid flowmap ControlNet (uses depth-style model as the flowmap is a
# 2D displacement field similar to depth topology).
FLUID_CONTROLNET_MODEL_DEFAULT: str = "control_v11f1p_sd15_depth.pth"
FLUID_CONTROLNET_STRENGTH_DEFAULT: float = 0.35
FLUID_START_PERCENT_DEFAULT: float = 0.0
FLUID_END_PERCENT_DEFAULT: float = 0.80

# Physics 3D ControlNet (uses normal-bae model as the physics output
# encodes surface deformation similar to normal maps).
PHYSICS_CONTROLNET_MODEL_DEFAULT: str = "control_v11p_sd15_normalbae.pth"
PHYSICS_CONTROLNET_STRENGTH_DEFAULT: float = 0.30
PHYSICS_START_PERCENT_DEFAULT: float = 0.0
PHYSICS_END_PERCENT_DEFAULT: float = 0.75

# Node titles — semantic selectors (never hardcoded numeric IDs)
TITLE_FLUID_VHS_LOADER: str = "session197 load fluid flowmap sequence"
TITLE_FLUID_CONTROLNET_LOADER: str = "session197 fluid controlnet loader"
TITLE_FLUID_APPLY: str = "session197 apply fluid controlnet"

TITLE_PHYSICS_VHS_LOADER: str = "session197 load physics sequence"
TITLE_PHYSICS_CONTROLNET_LOADER: str = "session197 physics controlnet loader"
TITLE_PHYSICS_APPLY: str = "session197 apply physics controlnet"


# ═══════════════════════════════════════════════════════════════════════════
#  Context extraction helpers (ECS component scanning)
# ═══════════════════════════════════════════════════════════════════════════

def extract_fluid_artifact_dir(context: dict[str, Any]) -> str | None:
    """Extract the fluid flowmap sequence directory from the pipeline context.

    Searches in order:
    1. ``context["vfx_artifacts"]["fluid_flowmap_dir"]``
    2. ``context["vfx_artifacts"]["fluid_momentum_controller"]["sequence_dir"]``
    3. ``context["fluid_flowmap_dir"]``

    Returns ``None`` if no fluid artifact is found.
    """
    vfx = context.get("vfx_artifacts") or {}
    if isinstance(vfx, dict):
        # Direct path
        d = vfx.get("fluid_flowmap_dir")
        if d and isinstance(d, str):
            return d
        # Nested under fluid_momentum_controller
        fmc = vfx.get("fluid_momentum_controller") or {}
        if isinstance(fmc, dict):
            d = fmc.get("sequence_dir") or fmc.get("output_dir")
            if d and isinstance(d, str):
                return d
    # Top-level fallback
    d = context.get("fluid_flowmap_dir")
    if d and isinstance(d, str):
        return d
    return None


def extract_physics_artifact_dir(context: dict[str, Any]) -> str | None:
    """Extract the physics 3D artifact sequence directory from the pipeline context.

    Searches in order:
    1. ``context["vfx_artifacts"]["physics_3d_dir"]``
    2. ``context["vfx_artifacts"]["physics_3d"]["sequence_dir"]``
    3. ``context["physics_3d_dir"]``

    Returns ``None`` if no physics artifact is found.
    """
    vfx = context.get("vfx_artifacts") or {}
    if isinstance(vfx, dict):
        d = vfx.get("physics_3d_dir")
        if d and isinstance(d, str):
            return d
        p3d = vfx.get("physics_3d") or {}
        if isinstance(p3d, dict):
            d = p3d.get("sequence_dir") or p3d.get("output_dir")
            if d and isinstance(d, str):
                return d
    d = context.get("physics_3d_dir")
    if d and isinstance(d, str):
        return d
    return None


def _validate_artifact_dir(artifact_dir: str, artifact_type: str) -> bool:
    """Validate that the artifact directory exists on disk.

    Implements the **反空投送幻觉红线**: if the directory is declared but
    does not exist, we MUST NOT feed a ghost path to ComfyUI.

    Returns True if valid, raises PipelineIntegrityError if strict mode.
    """
    if not os.path.exists(artifact_dir):
        raise PipelineIntegrityError(
            f"SESSION-197 {artifact_type} artifact directory "
            f"'{artifact_dir}' does not exist on disk. "
            f"Refusing to inject a ghost path into ComfyUI workflow. "
            f"(反空投送幻觉红线)"
        )
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  Fluid ControlNet chain injection
# ═══════════════════════════════════════════════════════════════════════════

def hydrate_fluid_controlnet_chain(
    workflow: dict[str, Any],
    *,
    fluid_sequence_directory: str,
    fluid_controlnet_model: str = FLUID_CONTROLNET_MODEL_DEFAULT,
    fluid_strength: float = FLUID_CONTROLNET_STRENGTH_DEFAULT,
    fluid_start_percent: float = FLUID_START_PERCENT_DEFAULT,
    fluid_end_percent: float = FLUID_END_PERCENT_DEFAULT,
) -> dict[str, Any]:
    """AST-inject the Fluid Flowmap ControlNet chain into the assembled workflow.

    The injection consists of three nodes:

    1. ``VHS_LoadImagesPath`` — points at ``fluid_sequence_directory``.
    2. ``ControlNetLoader`` — loads the fluid ControlNet model.
    3. ``ControlNetApplyAdvanced`` — splices the loader + image into the
       conditioning chain immediately AFTER the last existing ControlNet
       apply node (typically OpenPose), maintaining the daisy-chain topology.

    Idempotent: a second invocation detects the already-present fluid apply
    node and returns ``mode="already_present"``.
    """
    if not isinstance(workflow, dict):
        raise PipelineIntegrityError("workflow must be a dict (workflow_api_json)")

    report: dict[str, Any] = {
        "session": "SESSION-197",
        "feature": "fluid_controlnet_chain_hydration",
        "fluid_sequence_directory": str(fluid_sequence_directory),
        "touched_nodes": [],
    }

    # ── Idempotent guard ────────────────────────────────────────────────
    existing_apply = _find_node(
        workflow,
        class_types={"ControlNetApplyAdvanced", "ControlNetApply"},
        title_contains="fluid",
    )
    if existing_apply is not None:
        nid, node = existing_apply
        ins = node.setdefault("inputs", {})
        ins["strength"] = float(fluid_strength)
        ins["start_percent"] = float(fluid_start_percent)
        ins["end_percent"] = float(fluid_end_percent)
        # Refresh image directory on the upstream VHS loader
        existing_vhs = _find_node(
            workflow,
            class_types={"VHS_LoadImagesPath"},
            title_contains="fluid",
        )
        if existing_vhs is not None:
            existing_vhs[1].setdefault("inputs", {})["directory"] = str(fluid_sequence_directory)
            report["touched_nodes"].append({
                "node_id": existing_vhs[0],
                "operation": "directory_refresh",
                "directory": str(fluid_sequence_directory),
            })
        report["mode"] = "already_present"
        report["fluid_apply_node_id"] = nid
        report["touched_nodes"].append({
            "node_id": nid,
            "operation": "strength_refresh",
            "strength": float(fluid_strength),
        })
        return report

    # ── Locate the upstream conditioning node we splice after ──────────
    # Prefer OpenPose apply (last in SESSION-194 chain), then Depth, then
    # any ControlNetApply*.
    upstream = (
        _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="openpose")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="apply depth")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="apply normal")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced", "ControlNetApply"})
    )
    if upstream is None:
        raise PipelineIntegrityError(
            "SESSION-197: No upstream ControlNetApply node found; cannot splice "
            "fluid ControlNet into the conditioning chain."
        )
    upstream_id, _upstream_node = upstream

    # ── Find downstream consumers of the upstream node ─────────────────
    downstream_targets: list[tuple[str, dict[str, Any], str]] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or str(nid) == upstream_id:
            continue
        ins = node.get("inputs", {})
        if not isinstance(ins, dict):
            continue
        for key in ("positive", "negative"):
            ref = ins.get(key)
            if isinstance(ref, list) and len(ref) == 2 and str(ref[0]) == upstream_id:
                downstream_targets.append((str(nid), node, key))

    # ── Allocate IDs and AST-inject the new nodes ──────────────────────
    vhs_id = _next_free_id(workflow)
    workflow[vhs_id] = {
        "class_type": "VHS_LoadImagesPath",
        "inputs": {
            "directory": str(fluid_sequence_directory),
            "image_load_cap": 0,
            "skip_first_images": 0,
            "select_every_nth": 1,
        },
        "_meta": {"title": "SESSION197 Load Fluid Flowmap Sequence"},
    }
    report["touched_nodes"].append({
        "node_id": vhs_id, "operation": "create",
        "class_type": "VHS_LoadImagesPath",
    })

    cnet_loader_id = _next_free_id(workflow)
    workflow[cnet_loader_id] = {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": str(fluid_controlnet_model)},
        "_meta": {"title": "SESSION197 Fluid ControlNet Loader"},
    }
    report["touched_nodes"].append({
        "node_id": cnet_loader_id, "operation": "create",
        "class_type": "ControlNetLoader",
    })

    apply_id = _next_free_id(workflow)
    workflow[apply_id] = {
        "class_type": "ControlNetApplyAdvanced",
        "inputs": {
            "positive": [upstream_id, 0],
            "negative": [upstream_id, 1],
            "control_net": [cnet_loader_id, 0],
            "image": [vhs_id, 0],
            "strength": float(fluid_strength),
            "start_percent": float(fluid_start_percent),
            "end_percent": float(fluid_end_percent),
        },
        "_meta": {"title": "SESSION197 Apply Fluid ControlNet"},
    }
    report["touched_nodes"].append({
        "node_id": apply_id, "operation": "create",
        "class_type": "ControlNetApplyAdvanced",
    })

    # ── Rewire downstream consumers ───────────────────────────────────
    for d_id, d_node, d_key in downstream_targets:
        d_ins = d_node.setdefault("inputs", {})
        old_ref = d_ins.get(d_key)
        new_index = 0 if d_key == "positive" else 1
        d_ins[d_key] = [apply_id, new_index]
        report["touched_nodes"].append({
            "node_id": d_id,
            "operation": "rewire_conditioning",
            "input_key": d_key,
            "from": old_ref,
            "to": [apply_id, new_index],
        })

    report["mode"] = "injected"
    report["fluid_apply_node_id"] = apply_id
    report["fluid_loader_node_id"] = cnet_loader_id
    report["fluid_vhs_node_id"] = vhs_id
    return report


# ═══════════════════════════════════════════════════════════════════════════
#  Physics 3D ControlNet chain injection
# ═══════════════════════════════════════════════════════════════════════════

def hydrate_physics_controlnet_chain(
    workflow: dict[str, Any],
    *,
    physics_sequence_directory: str,
    physics_controlnet_model: str = PHYSICS_CONTROLNET_MODEL_DEFAULT,
    physics_strength: float = PHYSICS_CONTROLNET_STRENGTH_DEFAULT,
    physics_start_percent: float = PHYSICS_START_PERCENT_DEFAULT,
    physics_end_percent: float = PHYSICS_END_PERCENT_DEFAULT,
) -> dict[str, Any]:
    """AST-inject the Physics 3D ControlNet chain into the assembled workflow.

    Identical pattern to ``hydrate_fluid_controlnet_chain`` but for physics
    artifacts. Splices AFTER the fluid apply node (if present) or after the
    last existing ControlNet apply node.

    Idempotent: a second invocation returns ``mode="already_present"``.
    """
    if not isinstance(workflow, dict):
        raise PipelineIntegrityError("workflow must be a dict (workflow_api_json)")

    report: dict[str, Any] = {
        "session": "SESSION-197",
        "feature": "physics_controlnet_chain_hydration",
        "physics_sequence_directory": str(physics_sequence_directory),
        "touched_nodes": [],
    }

    # ── Idempotent guard ────────────────────────────────────────────────
    existing_apply = _find_node(
        workflow,
        class_types={"ControlNetApplyAdvanced", "ControlNetApply"},
        title_contains="physics",
    )
    if existing_apply is not None:
        nid, node = existing_apply
        ins = node.setdefault("inputs", {})
        ins["strength"] = float(physics_strength)
        ins["start_percent"] = float(physics_start_percent)
        ins["end_percent"] = float(physics_end_percent)
        existing_vhs = _find_node(
            workflow,
            class_types={"VHS_LoadImagesPath"},
            title_contains="physics",
        )
        if existing_vhs is not None:
            existing_vhs[1].setdefault("inputs", {})["directory"] = str(physics_sequence_directory)
            report["touched_nodes"].append({
                "node_id": existing_vhs[0],
                "operation": "directory_refresh",
                "directory": str(physics_sequence_directory),
            })
        report["mode"] = "already_present"
        report["physics_apply_node_id"] = nid
        report["touched_nodes"].append({
            "node_id": nid,
            "operation": "strength_refresh",
            "strength": float(physics_strength),
        })
        return report

    # ── Locate the upstream conditioning node we splice after ──────────
    # Prefer fluid apply (SESSION-197 chain order), then OpenPose, then
    # Depth, then any ControlNetApply*.
    upstream = (
        _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="fluid")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="openpose")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="apply depth")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="apply normal")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced", "ControlNetApply"})
    )
    if upstream is None:
        raise PipelineIntegrityError(
            "SESSION-197: No upstream ControlNetApply node found; cannot splice "
            "physics ControlNet into the conditioning chain."
        )
    upstream_id, _upstream_node = upstream

    # ── Find downstream consumers ─────────────────────────────────────
    downstream_targets: list[tuple[str, dict[str, Any], str]] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or str(nid) == upstream_id:
            continue
        ins = node.get("inputs", {})
        if not isinstance(ins, dict):
            continue
        for key in ("positive", "negative"):
            ref = ins.get(key)
            if isinstance(ref, list) and len(ref) == 2 and str(ref[0]) == upstream_id:
                downstream_targets.append((str(nid), node, key))

    # ── Allocate IDs and AST-inject ───────────────────────────────────
    vhs_id = _next_free_id(workflow)
    workflow[vhs_id] = {
        "class_type": "VHS_LoadImagesPath",
        "inputs": {
            "directory": str(physics_sequence_directory),
            "image_load_cap": 0,
            "skip_first_images": 0,
            "select_every_nth": 1,
        },
        "_meta": {"title": "SESSION197 Load Physics Sequence"},
    }
    report["touched_nodes"].append({
        "node_id": vhs_id, "operation": "create",
        "class_type": "VHS_LoadImagesPath",
    })

    cnet_loader_id = _next_free_id(workflow)
    workflow[cnet_loader_id] = {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": str(physics_controlnet_model)},
        "_meta": {"title": "SESSION197 Physics ControlNet Loader"},
    }
    report["touched_nodes"].append({
        "node_id": cnet_loader_id, "operation": "create",
        "class_type": "ControlNetLoader",
    })

    apply_id = _next_free_id(workflow)
    workflow[apply_id] = {
        "class_type": "ControlNetApplyAdvanced",
        "inputs": {
            "positive": [upstream_id, 0],
            "negative": [upstream_id, 1],
            "control_net": [cnet_loader_id, 0],
            "image": [vhs_id, 0],
            "strength": float(physics_strength),
            "start_percent": float(physics_start_percent),
            "end_percent": float(physics_end_percent),
        },
        "_meta": {"title": "SESSION197 Apply Physics ControlNet"},
    }
    report["touched_nodes"].append({
        "node_id": apply_id, "operation": "create",
        "class_type": "ControlNetApplyAdvanced",
    })

    # ── Rewire downstream consumers ───────────────────────────────────
    for d_id, d_node, d_key in downstream_targets:
        d_ins = d_node.setdefault("inputs", {})
        old_ref = d_ins.get(d_key)
        new_index = 0 if d_key == "positive" else 1
        d_ins[d_key] = [apply_id, new_index]
        report["touched_nodes"].append({
            "node_id": d_id,
            "operation": "rewire_conditioning",
            "input_key": d_key,
            "from": old_ref,
            "to": [apply_id, new_index],
        })

    report["mode"] = "injected"
    report["physics_apply_node_id"] = apply_id
    report["physics_loader_node_id"] = cnet_loader_id
    report["physics_vhs_node_id"] = vhs_id
    return report


# ═══════════════════════════════════════════════════════════════════════════
#  Unified VFX Topology Hydration — the ECS "System" entry point
# ═══════════════════════════════════════════════════════════════════════════

def hydrate_vfx_topology(
    workflow: dict[str, Any],
    context: dict[str, Any],
    *,
    strict: bool = True,
    fluid_strength: float = FLUID_CONTROLNET_STRENGTH_DEFAULT,
    physics_strength: float = PHYSICS_CONTROLNET_STRENGTH_DEFAULT,
) -> dict[str, Any]:
    """ECS-style system: scan context for physics/fluid components, inject ControlNet chains.

    This is the **single entry point** called from the payload assembly site
    (``builtin_backends._execute_live_pipeline``). It:

    1. Scans the context for ``fluid_flowmap_dir`` and ``physics_3d_dir``
       artifact components (ECS component detection).
    2. Validates each artifact directory exists on disk (反空投送幻觉红线).
    3. Injects the corresponding ControlNet chains in daisy-chain topology
       (ONNX/TensorRT serial fusion pattern).
    4. Runs DAG closure validation to ensure no ghost edges (反图谱污染红线).

    Parameters
    ----------
    workflow : dict
        The ComfyUI workflow JSON (mutable, will be modified in-place).
    context : dict
        The pipeline context containing ``vfx_artifacts`` and other metadata.
    strict : bool
        If True, missing artifact directories raise PipelineIntegrityError.
        If False, missing directories are silently skipped (graceful degradation).
    fluid_strength : float
        ControlNet strength for the fluid flowmap chain.
    physics_strength : float
        ControlNet strength for the physics 3D chain.

    Returns
    -------
    dict
        Hydration report with details of all injected nodes and wiring.
    """
    report: dict[str, Any] = {
        "session": "SESSION-197",
        "feature": "vfx_topology_hydration",
        "fluid_report": None,
        "physics_report": None,
        "artifacts_detected": [],
        "artifacts_injected": [],
        "dag_closure": None,
    }

    # ── Phase 1: ECS Component Scanning ────────────────────────────────
    fluid_dir = extract_fluid_artifact_dir(context)
    physics_dir = extract_physics_artifact_dir(context)

    if fluid_dir:
        report["artifacts_detected"].append("fluid_flowmap")
    if physics_dir:
        report["artifacts_detected"].append("physics_3d")

    if not fluid_dir and not physics_dir:
        report["action"] = "no_vfx_artifacts_detected"
        logger.info(
            "[SESSION-197 VFX Hydrator] No physics/fluid artifacts in context — skipping."
        )
        return report

    # ── Phase 2: Fluid ControlNet Injection ────────────────────────────
    if fluid_dir:
        try:
            _validate_artifact_dir(fluid_dir, "fluid_flowmap")
            fluid_report = hydrate_fluid_controlnet_chain(
                workflow,
                fluid_sequence_directory=fluid_dir,
                fluid_strength=fluid_strength,
            )
            report["fluid_report"] = fluid_report
            report["artifacts_injected"].append("fluid_flowmap")
            logger.info(
                "[SESSION-197 VFX Hydrator] Fluid ControlNet chain %s: %s",
                fluid_report.get("mode"), fluid_dir,
            )
        except PipelineIntegrityError:
            if strict:
                raise
            logger.warning(
                "[SESSION-197 VFX Hydrator] Fluid artifact dir '%s' not found — "
                "graceful degradation (strict=False).", fluid_dir,
            )
            report["fluid_report"] = {
                "mode": "graceful_degradation",
                "reason": f"Directory not found: {fluid_dir}",
            }

    # ── Phase 3: Physics ControlNet Injection ──────────────────────────
    if physics_dir:
        try:
            _validate_artifact_dir(physics_dir, "physics_3d")
            physics_report = hydrate_physics_controlnet_chain(
                workflow,
                physics_sequence_directory=physics_dir,
                physics_strength=physics_strength,
            )
            report["physics_report"] = physics_report
            report["artifacts_injected"].append("physics_3d")
            logger.info(
                "[SESSION-197 VFX Hydrator] Physics ControlNet chain %s: %s",
                physics_report.get("mode"), physics_dir,
            )
        except PipelineIntegrityError:
            if strict:
                raise
            logger.warning(
                "[SESSION-197 VFX Hydrator] Physics artifact dir '%s' not found — "
                "graceful degradation (strict=False).", physics_dir,
            )
            report["physics_report"] = {
                "mode": "graceful_degradation",
                "reason": f"Directory not found: {physics_dir}",
            }

    # ── Phase 4: DAG Closure Validation (反图谱污染红线) ─────────────
    try:
        closure_report = validate_preset_topology_closure(workflow)
        report["dag_closure"] = closure_report
    except PipelineIntegrityError as e:
        raise PipelineIntegrityError(
            f"SESSION-197 VFX Hydrator DAG closure failure after injection: {e}"
        ) from e

    report["action"] = "hydrated"
    return report


# ═══════════════════════════════════════════════════════════════════════════
#  UX Banner — SESSION-197 physics bus telemetry
# ═══════════════════════════════════════════════════════════════════════════

def emit_vfx_hydration_banner(
    artifacts_injected: list[str],
    stream=None,
) -> str:
    """Emit a sci-fi UX banner when VFX topology hydration completes.

    SESSION-197 UX contract: high-visibility telemetry during the bake phase.
    """
    if not artifacts_injected:
        return ""

    artifact_names = " + ".join(artifacts_injected)
    plain = (
        f"[\u26a1 SESSION-197 VFX \u62d3\u6251\u6ce8\u5165] "
        f"\u7269\u7406/\u6d41\u4f53\u8ba1\u7b97\u4ea7\u7269\u5df2\u52a8\u6001\u7ec7\u5165 ControlNet \u4e32\u8054\u94fe\u8def "
        f"({artifact_names}) \u2192 DAG \u95ed\u5408\u9a8c\u8bc1\u901a\u8fc7"
    )
    if stream is not None:
        try:
            stream.write("\033[1;35m" + plain + "\033[0m\n")
            stream.flush()
        except Exception:
            pass
    return plain


__all__ = [
    "PipelineIntegrityError",
    "FLUID_CONTROLNET_MODEL_DEFAULT",
    "FLUID_CONTROLNET_STRENGTH_DEFAULT",
    "PHYSICS_CONTROLNET_MODEL_DEFAULT",
    "PHYSICS_CONTROLNET_STRENGTH_DEFAULT",
    "TITLE_FLUID_VHS_LOADER",
    "TITLE_FLUID_CONTROLNET_LOADER",
    "TITLE_FLUID_APPLY",
    "TITLE_PHYSICS_VHS_LOADER",
    "TITLE_PHYSICS_CONTROLNET_LOADER",
    "TITLE_PHYSICS_APPLY",
    "extract_fluid_artifact_dir",
    "extract_physics_artifact_dir",
    "hydrate_fluid_controlnet_chain",
    "hydrate_physics_controlnet_chain",
    "hydrate_vfx_topology",
    "emit_vfx_hydration_banner",
]
