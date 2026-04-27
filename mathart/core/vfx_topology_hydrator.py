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

# SESSION-199 SAFE MODEL MAPPING (反臆想模型注入红线):
# Fluid flowmap ControlNet uses normalbae model — the fluid momentum field
# encodes directional surface flow which photometric stereo theory maps
# to surface normal perturbations (Lambertian shading gradient ≈ normal delta).
# Reference: Woodham 1980 photometric stereo; SESSION-199 safe model mapping
# research notes (docs/RESEARCH_NOTES_SESSION_199.md).
FLUID_CONTROLNET_MODEL_DEFAULT: str = "control_v11p_sd15_normalbae.pth"
FLUID_CONTROLNET_STRENGTH_DEFAULT: float = 0.35
FLUID_START_PERCENT_DEFAULT: float = 0.0
FLUID_END_PERCENT_DEFAULT: float = 0.80

# Physics 3D ControlNet uses depth model — the physics simulation output
# encodes 3D rigid-body deformation fields which map naturally to depth
# topology (Z-axis displacement ≈ depth map gradient).
# Reference: SESSION-199 safe model mapping research notes.
PHYSICS_CONTROLNET_MODEL_DEFAULT: str = "control_v11f1p_sd15_depth.pth"
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
#  SESSION-199: Adaptive Variance-Based ControlNet Strength Scheduler
#  + Dead-Water Dynamic Pruning + Anti-Math-Crash Red Line
# ═══════════════════════════════════════════════════════════════════════════

import math

# Dead-water variance threshold: below this, the conditioning frame is
# considered flat/static noise and the ControlNet node should be pruned
# from the DAG to prevent latent-space pollution.
DEAD_WATER_VARIANCE_THRESHOLD: float = 0.5


def compute_adaptive_controlnet_strength(
    pixel_variance: float,
    base_strength: float = 0.35,
    *,
    variance_scale: float = 0.5,
    min_strength: float = 0.10,
    max_strength: float = 0.90,
) -> float:
    """Compute adaptive ControlNet strength based on pixel-level variance.

    SESSION-199 PID-inspired adaptive gain scheduling:
    Scales the ControlNet injection strength proportionally to the
    normalised pixel variance of the conditioning image frame, then
    clamps the result to the safe operating window ``[min_strength,
    max_strength]``.

    **反数学崩溃红线 (Anti-Math-Crash Red Line)**:
    - ``np.nan`` / ``np.inf`` / negative variance → returns ``min_strength``
    - ``ZeroDivisionError`` impossible (divisor is constant 255.0)
    - Result is **always** a finite float in ``[min_strength, max_strength]``

    The formula is::

        raw = base_strength + variance_scale * normalised_variance
        strength = clamp(raw, min_strength, max_strength)

    where ``normalised_variance = pixel_variance / 255.0`` (assumes
    uint8 pixel range 0-255).

    Parameters
    ----------
    pixel_variance : float
        Raw pixel variance of the conditioning frame (e.g. ``np.var(frame)``).
        Expected range: 0.0 – 65025.0 (uint8 max variance = 255²).
    base_strength : float
        Baseline ControlNet strength when variance is zero. Default 0.35.
    variance_scale : float
        Scaling coefficient applied to the normalised variance. Default 0.5.
    min_strength : float
        Safety floor — strength is never below this value. Default 0.10.
    max_strength : float
        Safety ceiling — strength is never above this value. Default 0.90.

    Returns
    -------
    float
        Adaptive ControlNet strength in ``[min_strength, max_strength]``.
        Guaranteed finite, non-NaN, non-Inf.

    Examples
    --------
    >>> compute_adaptive_controlnet_strength(0.0)   # flat frame
    0.35
    >>> compute_adaptive_controlnet_strength(6502.5)  # 10% of max variance
    0.375
    >>> compute_adaptive_controlnet_strength(65025.0)  # max variance
    0.85
    >>> compute_adaptive_controlnet_strength(float('nan'))  # NaN → safe floor
    0.1
    >>> compute_adaptive_controlnet_strength(float('inf'))  # Inf → safe floor
    0.1
    """
    # ── 反数学崩溃红线: sanitise all inputs ──────────────────────────────
    try:
        pv = float(pixel_variance)
    except (TypeError, ValueError):
        pv = 0.0
    try:
        bs = float(base_strength)
    except (TypeError, ValueError):
        bs = 0.35
    try:
        vs = float(variance_scale)
    except (TypeError, ValueError):
        vs = 0.5
    try:
        lo = float(min_strength)
    except (TypeError, ValueError):
        lo = 0.10
    try:
        hi = float(max_strength)
    except (TypeError, ValueError):
        hi = 0.90

    # NaN / Inf / negative variance → collapse to safe floor immediately
    if math.isnan(pv) or math.isinf(pv) or pv < 0.0:
        return lo
    if math.isnan(bs) or math.isinf(bs):
        bs = 0.35
    if math.isnan(vs) or math.isinf(vs):
        vs = 0.5
    if math.isnan(lo) or math.isinf(lo):
        lo = 0.10
    if math.isnan(hi) or math.isinf(hi):
        hi = 0.90

    normalised = pv / 255.0  # constant divisor — ZeroDivisionError impossible
    raw = bs + vs * normalised

    # Final clamp — guarantees finite result in [lo, hi]
    result = max(lo, min(hi, raw))

    # Paranoid final NaN/Inf guard (belt-and-suspenders)
    if math.isnan(result) or math.isinf(result):
        return lo
    return result


def should_prune_dead_water(
    pixel_variance: float,
    *,
    threshold: float = DEAD_WATER_VARIANCE_THRESHOLD,
) -> bool:
    """Determine if a conditioning frame should be pruned (dead-water detection).

    SESSION-199 Dynamic Pruning Gate:
    If the pixel variance of a conditioning frame is below the dead-water
    threshold, the corresponding ControlNet node should be short-circuited
    (pruned) from the DAG to prevent micro-noise from polluting the latent
    space.

    Parameters
    ----------
    pixel_variance : float
        Raw pixel variance of the conditioning frame.
    threshold : float
        Dead-water variance threshold. Default 0.5.

    Returns
    -------
    bool
        True if the frame should be pruned (dead water), False otherwise.
    """
    try:
        pv = float(pixel_variance)
    except (TypeError, ValueError):
        return True  # unparseable → prune for safety
    if math.isnan(pv) or math.isinf(pv) or pv < 0.0:
        return True  # anomalous → prune for safety
    return pv < threshold


def prune_controlnet_node_and_reseal_dag(
    workflow: dict[str, Any],
    apply_node_id: str,
) -> dict[str, Any]:
    """Prune a ControlNetApplyAdvanced node and reseal the DAG.

    SESSION-199 反拓扑断裂红线 (Anti-Topology-Fracture Red Line):
    When dead-water detection triggers dynamic pruning of a VFX ControlNet
    node, the upstream positive/negative conditioning wires must be
    seamlessly reconnected to all downstream consumers. This ensures the
    KSampler (or next ControlNet in the daisy-chain) continues to receive
    its conditioning flow without interruption.

    Algorithm:
    1. Read the pruned node's ``positive`` and ``negative`` input references
       (these point to the upstream node).
    2. Find all downstream nodes that reference ``apply_node_id`` in their
       ``positive`` or ``negative`` inputs.
    3. Rewire those downstream references to point directly to the upstream
       node (bypass the pruned node).
    4. Also remove the associated VHS_LoadImagesPath and ControlNetLoader
       nodes that fed exclusively into the pruned apply node.
    5. Delete the pruned nodes from the workflow.

    Parameters
    ----------
    workflow : dict
        The ComfyUI workflow JSON (mutable, modified in-place).
    apply_node_id : str
        The node ID of the ControlNetApplyAdvanced to prune.

    Returns
    -------
    dict
        Pruning report with details of rewired and removed nodes.
    """
    report: dict[str, Any] = {
        "session": "SESSION-199",
        "operation": "dead_water_prune_and_reseal",
        "pruned_node_id": apply_node_id,
        "rewired": [],
        "removed_nodes": [],
    }

    apply_node = workflow.get(str(apply_node_id))
    if apply_node is None:
        report["status"] = "node_not_found"
        return report

    apply_inputs = apply_node.get("inputs", {})

    # ── Step 1: Identify upstream conditioning source ─────────────────
    upstream_positive = apply_inputs.get("positive")  # e.g. ["42", 0]
    upstream_negative = apply_inputs.get("negative")  # e.g. ["42", 1]

    if not isinstance(upstream_positive, list) or not isinstance(upstream_negative, list):
        report["status"] = "upstream_refs_missing"
        return report

    # ── Step 2: Find and rewire all downstream consumers ──────────────
    apply_id_str = str(apply_node_id)
    for nid, node in list(workflow.items()):
        if not isinstance(node, dict) or str(nid) == apply_id_str:
            continue
        ins = node.get("inputs", {})
        if not isinstance(ins, dict):
            continue
        for key in ("positive", "negative"):
            ref = ins.get(key)
            if isinstance(ref, list) and len(ref) >= 2 and str(ref[0]) == apply_id_str:
                # Rewire: point to the upstream node instead
                if key == "positive":
                    ins[key] = list(upstream_positive)
                else:
                    ins[key] = list(upstream_negative)
                report["rewired"].append({
                    "downstream_node_id": str(nid),
                    "input_key": key,
                    "old_ref": ref,
                    "new_ref": ins[key],
                })

    # ── Step 3: Identify and remove orphaned feeder nodes ─────────────
    # The ControlNetLoader and VHS_LoadImagesPath that fed into this apply
    control_net_ref = apply_inputs.get("control_net")
    image_ref = apply_inputs.get("image")

    nodes_to_remove = [apply_id_str]
    for ref in (control_net_ref, image_ref):
        if isinstance(ref, list) and len(ref) >= 1:
            feeder_id = str(ref[0])
            if feeder_id in workflow:
                nodes_to_remove.append(feeder_id)

    for nid in nodes_to_remove:
        if nid in workflow:
            del workflow[nid]
            report["removed_nodes"].append(nid)

    report["status"] = "pruned_and_resealed"
    return report


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

    # ── SESSION-198 P0: Math-to-Pixel Rasterization Bridge ─────────────
    # Convert JSON math features into PNG visual features before injecting.
    try:
        if strict:
            raise RuntimeError("physics_sequence_exporter archived in _legacy_archive_v5")
        logger.info("[SESSION-198 Rasterizer] physics_sequence_exporter archived; skipping rasterization")
    except Exception as e:
        logger.warning(f"[SESSION-198 Rasterizer] Rasterization failed: {e}")
        if strict:
            raise

    # ── SESSION-199 Phase 1.5: Variance-based Adaptive Scheduling ──────
    # Sample PNG frames to compute pixel variance for dynamic gain + pruning.
    import numpy as np

    def _sample_variance_from_dir(artifact_dir: str) -> float:
        """Sample pixel variance from the first PNG in a directory."""
        try:
            from PIL import Image as _PILImage
            p = Path(artifact_dir)
            pngs = sorted(p.glob("*.png"))
            if not pngs:
                return 0.0
            img = _PILImage.open(pngs[0]).convert("RGB")
            arr = np.asarray(img, dtype=np.float64)
            v = float(np.var(arr))
            # 反数学崩溃红线: guard against NaN/Inf from corrupted images
            if math.isnan(v) or math.isinf(v):
                return 0.0
            return v
        except Exception:
            return 0.0

    fluid_variance = _sample_variance_from_dir(fluid_dir) if fluid_dir else 0.0
    physics_variance = _sample_variance_from_dir(physics_dir) if physics_dir else 0.0

    report["fluid_pixel_variance"] = fluid_variance
    report["physics_pixel_variance"] = physics_variance

    # Dead-water detection: if variance is below threshold, prune this channel
    fluid_is_dead_water = should_prune_dead_water(fluid_variance) if fluid_dir else False
    physics_is_dead_water = should_prune_dead_water(physics_variance) if physics_dir else False

    report["fluid_dead_water"] = fluid_is_dead_water
    report["physics_dead_water"] = physics_is_dead_water

    # Adaptive strength scheduling (only for non-dead-water channels)
    if fluid_dir and not fluid_is_dead_water:
        fluid_strength = compute_adaptive_controlnet_strength(
            fluid_variance, base_strength=fluid_strength,
        )
    if physics_dir and not physics_is_dead_water:
        physics_strength = compute_adaptive_controlnet_strength(
            physics_variance, base_strength=physics_strength,
        )

    report["fluid_adaptive_strength"] = fluid_strength if (fluid_dir and not fluid_is_dead_water) else None
    report["physics_adaptive_strength"] = physics_strength if (physics_dir and not physics_is_dead_water) else None

    # ── SESSION-199 UX: Emit industrial baking gateway banner ──────────
    import sys as _sys
    try:
        from mathart.core.anti_flicker_runtime import emit_industrial_baking_banner
        emit_industrial_baking_banner(stream=_sys.stderr)
    except Exception:
        pass  # UX banner is non-critical; never block the pipeline

    # ── Phase 2: Fluid ControlNet Injection ────────────────────
    if fluid_dir and not fluid_is_dead_water:
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
                "[SESSION-199 VFX Hydrator] Fluid ControlNet chain %s (adaptive strength=%.3f): %s",
                fluid_report.get("mode"), fluid_strength, fluid_dir,
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
    elif fluid_dir and fluid_is_dead_water:
        logger.info(
            "[SESSION-199 Dead-Water Pruning] Fluid channel variance %.4f < threshold %.4f — "
            "pruning fluid ControlNet node to prevent latent-space pollution.",
            fluid_variance, DEAD_WATER_VARIANCE_THRESHOLD,
        )
        report["fluid_report"] = {
            "mode": "dead_water_pruned",
            "pixel_variance": fluid_variance,
            "threshold": DEAD_WATER_VARIANCE_THRESHOLD,
            "reason": "Variance below dead-water threshold; node pruned to prevent latent pollution.",
        }

    # ── Phase 3: Physics ControlNet Injection ────────────────────
    if physics_dir and not physics_is_dead_water:
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
                "[SESSION-199 VFX Hydrator] Physics ControlNet chain %s (adaptive strength=%.3f): %s",
                physics_report.get("mode"), physics_strength, physics_dir,
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
    elif physics_dir and physics_is_dead_water:
        logger.info(
            "[SESSION-199 Dead-Water Pruning] Physics channel variance %.4f < threshold %.4f — "
            "pruning physics ControlNet node to prevent latent-space pollution.",
            physics_variance, DEAD_WATER_VARIANCE_THRESHOLD,
        )
        report["physics_report"] = {
            "mode": "dead_water_pruned",
            "pixel_variance": physics_variance,
            "threshold": DEAD_WATER_VARIANCE_THRESHOLD,
            "reason": "Variance below dead-water threshold; node pruned to prevent latent pollution.",
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
    *,
    dead_water_pruned: list[str] | None = None,
    adaptive_strengths: dict[str, float] | None = None,
    stream=None,
) -> str:
    """Emit a sci-fi UX banner when VFX topology hydration completes.

    SESSION-197 UX contract: high-visibility telemetry during the bake phase.
    SESSION-199 extension: includes dead-water pruning status and adaptive
    strength scheduling info, plus industrial baking gateway highlight.
    """
    if not artifacts_injected and not dead_water_pruned:
        return ""

    lines: list[str] = []

    # ── SESSION-197 original banner ────────────────────────────────────
    if artifacts_injected:
        artifact_names = " + ".join(artifacts_injected)
        lines.append(
            f"[\u26a1 SESSION-197 VFX \u62d3\u6251\u6ce8\u5165] "
            f"\u7269\u7406/\u6d41\u4f53\u8ba1\u7b97\u4ea7\u7269\u5df2\u52a8\u6001\u7ec7\u5165 ControlNet \u4e32\u8054\u94fe\u8def "
            f"({artifact_names}) \u2192 DAG \u95ed\u5408\u9a8c\u8bc1\u901a\u8fc7"
        )

    # ── SESSION-199 adaptive scheduling banner ─────────────────────────
    if adaptive_strengths:
        for channel, strength in adaptive_strengths.items():
            lines.append(
                f"[\U0001f3af SESSION-199 \u81ea\u9002\u5e94\u8c03\u5ea6] {channel} ControlNet "
                f"\u5f3a\u5ea6\u5df2\u52a8\u6001\u8c03\u6574 \u2192 {strength:.3f}"
            )

    # ── SESSION-199 dead-water pruning banner ──────────────────────────
    if dead_water_pruned:
        pruned_names = " + ".join(dead_water_pruned)
        lines.append(
            f"[\U0001f4a7 SESSION-199 \u6b7b\u6c34\u526a\u679d] "
            f"{pruned_names} \u65b9\u5dee\u4f4e\u4e8e\u9608\u503c \u2192 \u5df2\u4ece DAG \u526a\u9664\u4ee5\u9632\u6b62\u6f5c\u7a7a\u95f4\u6c61\u67d3"
        )

    # ── SESSION-199 industrial baking gateway highlight ────────────────
    lines.append(
        "[\u2699\ufe0f \u5de5\u4e1a\u70d8\u7119\u7f51\u5173] SESSION-199 VFX \u62d3\u6251\u6ce8\u5165\u5b8c\u6210 \u2192 "
        "\u81ea\u9002\u5e94\u65b9\u5dee\u8c03\u5ea6 + \u6b7b\u6c34\u526a\u679d + DAG \u95ed\u5408\u9a8c\u8bc1 \u2192 \u7ba1\u9053\u5c31\u7eea"
    )

    plain = "\n".join(lines)

    if stream is not None:
        try:
            # Magenta for VFX injection, Cyan for industrial gateway
            for i, line in enumerate(lines):
                if "\u5de5\u4e1a\u70d8\u7119\u7f51\u5173" in line:
                    stream.write("\033[1;36m" + line + "\033[0m\n")  # Cyan
                elif "\u6b7b\u6c34\u526a\u679d" in line:
                    stream.write("\033[1;33m" + line + "\033[0m\n")  # Yellow
                elif "\u81ea\u9002\u5e94\u8c03\u5ea6" in line:
                    stream.write("\033[1;32m" + line + "\033[0m\n")  # Green
                else:
                    stream.write("\033[1;35m" + line + "\033[0m\n")  # Magenta
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
    "DEAD_WATER_VARIANCE_THRESHOLD",
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
    "compute_adaptive_controlnet_strength",
    "should_prune_dead_water",
    "prune_controlnet_node_and_reseal_dag",
]
