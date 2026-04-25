"""SESSION-194: ComfyUI Preset Topology Hydrator — DAG-Closure Injection.

This module is the single source of truth for **augmenting** an existing
``workflow_api.json`` preset with the SESSION-194 OpenPose + IPAdapter node
chain *without* mutating the JSON file on disk and *without* using string /
regex hacks.

Industrial References (see ``research_notes_session194_pipeline_integration.md``):

* **UE5 ``UAnimGraph`` ↔ ``USkeletalMeshComponent`` decoupling**: the bone
  control flow (OpenPose pose buffer) and the renderer (Depth/Normal apply
  chain) communicate through a *strongly-typed contract* (a node ID + a
  ``ControlNetApplyAdvanced`` chain edge) rather than a hard-coded
  back-reference. The hydrator ensures the contract is honoured node-by-node.
* **Apache Airflow DAG validation**: every newly inserted node has its
  ``inputs`` keys end-to-end resolved against existing node IDs; orphan /
  ghost edges are *fail-fast* (``PipelineIntegrityError``).
* **Spring IoC / DI**: the hydrator never *creates* a ComfyUI client nor
  *invokes* a backend. It only **injects providers** (OpenPose / IPAdapter
  node groups) into the assembled workflow at the explicit call site.

Hard Red Lines:

* No string concatenation or regex on the JSON; every mutation goes through
  ``dict[node_id] = {...}`` AST-style operations.
* No hardcoded numeric IDs from the original preset are referenced; every
  existing node is located via ``class_type`` + ``_meta.title`` selectors
  (the same pattern used by ``ComfyUIPresetManager``).
* Idempotent: invoking ``hydrate_openpose_controlnet_chain()`` twice on the
  same workflow is a no-op the second time (returns ``mode="already_present"``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

from mathart.pipeline_contract import PipelineContractError

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  PipelineIntegrityError — SESSION-194 fail-fast violation surface
# ═══════════════════════════════════════════════════════════════════════════
class PipelineIntegrityError(PipelineContractError):
    """SESSION-194: raised when the preset topology fails closure validation.

    Subclasses :class:`mathart.pipeline_contract.PipelineContractError` so
    upstream try/except blocks that already handle the contract error type
    keep working, while new code can selectively rescue *only* topology
    integrity faults.
    """

    def __init__(self, detail: str) -> None:
        super().__init__("pipeline_integrity_violation", detail)


# ═══════════════════════════════════════════════════════════════════════════
#  Constants — node defaults & wiring sentinels
# ═══════════════════════════════════════════════════════════════════════════
OPENPOSE_CONTROLNET_MODEL_DEFAULT: str = "control_v11p_sd15_openpose.pth"
OPENPOSE_SEQUENCE_DIR_SENTINEL: str = "__OPENPOSE_SEQUENCE_DIR__"

# Node titles used by SESSION-194 — semantic selectors for downstream
# arbitration / preset assembly.
TITLE_OPENPOSE_CONTROLNET_LOADER: str = "openpose controlnet loader"
TITLE_OPENPOSE_VHS_LOADER: str = "load openpose sequence"
TITLE_OPENPOSE_APPLY: str = "apply openpose controlnet"
TITLE_IPADAPTER_LOADER: str = "session194 ip-adapter loader"
TITLE_CLIP_VISION_LOADER: str = "session194 clip vision loader"
TITLE_IPADAPTER_LOAD_IMAGE: str = "session194 ip-adapter reference image"
TITLE_IPADAPTER_APPLY: str = "session194 ip-adapter apply"


# ═══════════════════════════════════════════════════════════════════════════
#  Semantic selector helpers (do not import from comfyui_preset_manager to
#  avoid a circular import; logic is intentionally narrow.)
# ═══════════════════════════════════════════════════════════════════════════
def _find_node(
    workflow: dict[str, Any],
    *,
    class_types: Iterable[str],
    title_contains: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Locate a node by class_type (and optional title substring).

    Returns the *first* match in deterministic ID order, or ``None``.
    """
    needle = title_contains.strip().lower() if title_contains else None
    accepted = {ct for ct in class_types}
    candidates: list[tuple[str, dict[str, Any]]] = []
    for nid in sorted(workflow.keys(), key=lambda x: (len(str(x)), str(x))):
        node = workflow[nid]
        if not isinstance(node, dict):
            continue
        if node.get("class_type") not in accepted:
            continue
        if needle is None:
            candidates.append((str(nid), node))
            continue
        title = str(node.get("_meta", {}).get("title", "")).lower()
        if needle in title:
            return (str(nid), node)
    return candidates[0] if candidates else None


def _next_free_id(workflow: dict[str, Any]) -> str:
    max_id = 0
    for nid in workflow:
        try:
            v = int(nid)
        except (TypeError, ValueError):
            continue
        if v > max_id:
            max_id = v
    return str(max_id + 1)


# ═══════════════════════════════════════════════════════════════════════════
#  OpenPose ControlNet chain injection
# ═══════════════════════════════════════════════════════════════════════════
def hydrate_openpose_controlnet_chain(
    workflow: dict[str, Any],
    *,
    openpose_sequence_directory: str | Path = OPENPOSE_SEQUENCE_DIR_SENTINEL,
    openpose_controlnet_model: str = OPENPOSE_CONTROLNET_MODEL_DEFAULT,
    openpose_strength: float = 1.0,
    openpose_start_percent: float = 0.0,
    openpose_end_percent: float = 1.0,
) -> dict[str, Any]:
    """AST-inject the OpenPose ControlNet chain into the assembled workflow.

    The injection consists of three nodes:

    1. ``VHS_LoadImagesPath`` — points at ``openpose_sequence_directory``
       (replaced post-assembly with the real on-disk pose dir by the data
       bus, see :mod:`mathart.core.openpose_pose_provider`).
    2. ``ControlNetLoader`` — loads ``control_v11p_sd15_openpose.pth``.
    3. ``ControlNetApplyAdvanced`` — splices the loader + image into the
       *positive*/*negative* conditioning chain immediately *after* the
       Depth ControlNet apply and immediately *before* the SparseCtrl-RGB
       apply (or the KSampler if no SparseCtrl is present).

    The function is **idempotent**: a second invocation detects the
    already-present OpenPose apply node and returns ``mode="already_present"``.
    """
    if not isinstance(workflow, dict):
        raise PipelineIntegrityError("workflow must be a dict (workflow_api_json)")

    report: dict[str, Any] = {
        "session": "SESSION-194",
        "feature": "openpose_controlnet_chain_hydration",
        "openpose_sequence_directory": str(openpose_sequence_directory),
        "touched_nodes": [],
    }

    # ── Idempotent guard ────────────────────────────────────────────────
    existing_apply = _find_node(
        workflow,
        class_types={"ControlNetApplyAdvanced", "ControlNetApply"},
        title_contains=TITLE_OPENPOSE_APPLY,
    )
    if existing_apply is not None:
        nid, node = existing_apply
        ins = node.setdefault("inputs", {})
        ins["strength"] = float(openpose_strength)
        ins["start_percent"] = float(openpose_start_percent)
        ins["end_percent"] = float(openpose_end_percent)
        # Refresh image directory on the upstream VHS loader if we can find one
        existing_vhs = _find_node(
            workflow,
            class_types={"VHS_LoadImagesPath"},
            title_contains=TITLE_OPENPOSE_VHS_LOADER,
        )
        if existing_vhs is not None:
            existing_vhs[1].setdefault("inputs", {})["directory"] = str(openpose_sequence_directory)
            report["touched_nodes"].append({
                "node_id": existing_vhs[0],
                "operation": "directory_refresh",
                "directory": str(openpose_sequence_directory),
            })
        report["mode"] = "already_present"
        report["openpose_apply_node_id"] = nid
        report["touched_nodes"].append({
            "node_id": nid,
            "operation": "strength_refresh",
            "strength": float(openpose_strength),
        })
        return report

    # ── Locate the upstream conditioning node we splice after ──────────
    # Prefer the Depth apply (chained directly after Normal); else Normal;
    # else the very first ControlNetApply* we find.
    upstream = (
        _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="apply depth")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced"}, title_contains="apply normal")
        or _find_node(workflow, class_types={"ControlNetApplyAdvanced", "ControlNetApply"})
    )
    if upstream is None:
        raise PipelineIntegrityError(
            "No upstream ControlNetApply node found; preset is missing the "
            "Normal/Depth conditioning chain — cannot splice OpenPose."
        )
    upstream_id, upstream_node = upstream

    # The *downstream* node is the one whose `positive`/`negative` currently
    # references upstream_id (we will rewire it to point at our new apply).
    # Falls back to KSampler if no other ControlNetApply node sits downstream.
    downstream_targets: list[tuple[str, dict[str, Any], str]] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or nid == upstream_id:
            continue
        ins = node.get("inputs", {})
        if not isinstance(ins, dict):
            continue
        for key in ("positive", "negative", "model"):
            ref = ins.get(key)
            if isinstance(ref, list) and len(ref) == 2 and str(ref[0]) == upstream_id:
                downstream_targets.append((str(nid), node, key))

    # ── Allocate IDs and AST-inject the new nodes ──────────────────────
    vhs_id = _next_free_id(workflow)
    workflow[vhs_id] = {
        "class_type": "VHS_LoadImagesPath",
        "inputs": {
            "directory": str(openpose_sequence_directory),
            "image_load_cap": 0,
            "skip_first_images": 0,
            "select_every_nth": 1,
        },
        "_meta": {"title": "Load OpenPose Sequence"},
    }
    report["touched_nodes"].append({"node_id": vhs_id, "operation": "create", "class_type": "VHS_LoadImagesPath"})

    cnet_loader_id = _next_free_id(workflow)
    workflow[cnet_loader_id] = {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": str(openpose_controlnet_model)},
        "_meta": {"title": "OpenPose ControlNet Loader"},
    }
    report["touched_nodes"].append({"node_id": cnet_loader_id, "operation": "create", "class_type": "ControlNetLoader"})

    apply_id = _next_free_id(workflow)
    workflow[apply_id] = {
        "class_type": "ControlNetApplyAdvanced",
        "inputs": {
            "positive": [upstream_id, 0],
            "negative": [upstream_id, 1],
            "control_net": [cnet_loader_id, 0],
            "image": [vhs_id, 0],
            "strength": float(openpose_strength),
            "start_percent": float(openpose_start_percent),
            "end_percent": float(openpose_end_percent),
        },
        "_meta": {"title": "Apply OpenPose ControlNet"},
    }
    report["touched_nodes"].append({"node_id": apply_id, "operation": "create", "class_type": "ControlNetApplyAdvanced"})

    # ── Rewire downstream consumers to point at our new apply node ─────
    for d_id, d_node, d_key in downstream_targets:
        d_ins = d_node.setdefault("inputs", {})
        old_ref = d_ins.get(d_key)
        if d_key == "model":
            # Don't hijack the model wire — only conditioning rewires belong here.
            continue
        # The output index for ControlNetApplyAdvanced mirrors input
        # (positive→0, negative→1), same convention as upstream node.
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
    report["openpose_apply_node_id"] = apply_id
    report["openpose_loader_node_id"] = cnet_loader_id
    report["openpose_vhs_node_id"] = vhs_id
    return report


# ═══════════════════════════════════════════════════════════════════════════
#  IPAdapter quartet injection (delegates to identity_hydration when the
#  caller already has a reference path on disk; this helper just primes the
#  preset with a sentinel-image placeholder so downstream IoC providers can
#  swap it in via the `LoadImage.image` field at payload time).
# ═══════════════════════════════════════════════════════════════════════════
def hydrate_ipadapter_quartet(
    workflow: dict[str, Any],
    *,
    placeholder_image_filename: str = "session194_identity_placeholder.png",
    ipadapter_model: str = "ip-adapter-plus_sd15.safetensors",
    clip_vision_model: str = "clip-vit-h-14-laion2B-s32B-b79K.safetensors",
    weight: float = 0.85,
) -> dict[str, Any]:
    """Inject the four-node IPAdapter chain (idempotent).

    The reference image is set to ``placeholder_image_filename``; the
    SESSION-194 IoC ``identity_lock_provider`` swaps it for the real path
    at payload-assembly time (see ``identity_hydration.inject_ipadapter_identity_lock``).
    """
    report: dict[str, Any] = {
        "session": "SESSION-194",
        "feature": "ipadapter_quartet_hydration",
        "touched_nodes": [],
    }

    existing_apply = _find_node(
        workflow,
        class_types={"IPAdapterApply", "IPAdapterApplyAdvanced", "IPAdapter"},
        title_contains=None,
    )
    if existing_apply is not None:
        nid, node = existing_apply
        node.setdefault("inputs", {})["weight"] = float(weight)
        report["mode"] = "already_present"
        report["ipadapter_apply_node_id"] = nid
        report["touched_nodes"].append({"node_id": nid, "operation": "weight_refresh", "weight": float(weight)})
        return report

    ck = _find_node(workflow, class_types={"CheckpointLoaderSimple"})
    if ck is None:
        raise PipelineIntegrityError(
            "Cannot hydrate IPAdapter quartet — no CheckpointLoaderSimple in workflow."
        )
    ck_id, _ = ck

    ksampler = _find_node(workflow, class_types={"KSampler", "KSamplerAdvanced"})
    if ksampler is None:
        raise PipelineIntegrityError(
            "Cannot hydrate IPAdapter quartet — no KSampler in workflow."
        )
    ks_id, ks_node = ksampler
    ks_inputs = ks_node.setdefault("inputs", {})

    # SESSION-194: discover the *upstream model producer* that the KSampler
    # currently consumes (e.g. AnimateDiffLoader), so the IPAdapter is
    # spliced *between* that producer and the sampler. This preserves the
    # AnimateDiff temporal context while still injecting the identity lock.
    upstream_model_ref = ks_inputs.get("model")
    if isinstance(upstream_model_ref, list) and len(upstream_model_ref) == 2:
        upstream_model_node_id = str(upstream_model_ref[0])
        upstream_model_output_index = int(upstream_model_ref[1])
    else:
        upstream_model_node_id = ck_id
        upstream_model_output_index = 0

    load_image_id = _next_free_id(workflow)
    workflow[load_image_id] = {
        "class_type": "LoadImage",
        "inputs": {"image": placeholder_image_filename},
        "_meta": {"title": "SESSION194 IP-Adapter Reference Image"},
    }
    clip_vision_id = _next_free_id(workflow)
    workflow[clip_vision_id] = {
        "class_type": "CLIPVisionLoader",
        "inputs": {"clip_name": clip_vision_model},
        "_meta": {"title": "SESSION194 CLIP Vision Loader"},
    }
    ipa_loader_id = _next_free_id(workflow)
    workflow[ipa_loader_id] = {
        "class_type": "IPAdapterModelLoader",
        "inputs": {"ipadapter_file": ipadapter_model},
        "_meta": {"title": "SESSION194 IP-Adapter Loader"},
    }
    ipa_apply_id = _next_free_id(workflow)
    workflow[ipa_apply_id] = {
        "class_type": "IPAdapterApply",
        "inputs": {
            "weight": float(weight),
            "noise": 0.0,
            "weight_type": "original",
            "start_at": 0.0,
            "end_at": 1.0,
            "ipadapter": [ipa_loader_id, 0],
            "clip_vision": [clip_vision_id, 0],
            "image": [load_image_id, 0],
            "model": [upstream_model_node_id, upstream_model_output_index],
        },
        "_meta": {"title": "SESSION194 IP-Adapter Apply"},
    }
    # Splice IPAdapter *between* the upstream model producer and the sampler.
    prev_ks_model = ks_inputs.get("model")
    ks_inputs["model"] = [ipa_apply_id, 0]
    report["touched_nodes"].append({
        "node_id": ks_id,
        "operation": "rewire_model_to_ipadapter",
        "from": prev_ks_model,
        "to": [ipa_apply_id, 0],
    })

    for nid, op, ct in [
        (load_image_id, "create", "LoadImage"),
        (clip_vision_id, "create", "CLIPVisionLoader"),
        (ipa_loader_id, "create", "IPAdapterModelLoader"),
        (ipa_apply_id, "create", "IPAdapterApply"),
    ]:
        report["touched_nodes"].append({"node_id": nid, "operation": op, "class_type": ct})

    report["mode"] = "injected"
    report["ipadapter_apply_node_id"] = ipa_apply_id
    return report


# ═══════════════════════════════════════════════════════════════════════════
#  DAG closure validation — Airflow-style strict edge contract
# ═══════════════════════════════════════════════════════════════════════════
def validate_preset_topology_closure(workflow: dict[str, Any]) -> dict[str, Any]:
    """Walk every node's ``inputs`` and verify all node-ref edges resolve.

    Raises :class:`PipelineIntegrityError` if any input references a node
    ID that does not exist (a "ghost edge"), or if the workflow is missing
    a terminal sink (``KSampler*`` / ``SaveImage`` / ``VHS_VideoCombine``).
    Returns a structured report on success.
    """
    if not isinstance(workflow, dict) or not workflow:
        raise PipelineIntegrityError("workflow is empty or not a dict")

    node_ids = {str(nid) for nid in workflow.keys()}
    ghost_edges: list[dict[str, Any]] = []

    for nid, node in workflow.items():
        if not isinstance(node, dict):
            ghost_edges.append({"node_id": str(nid), "issue": "node is not a dict"})
            continue
        ins = node.get("inputs", {})
        if not isinstance(ins, dict):
            continue
        for key, val in ins.items():
            # ComfyUI node refs are 2-element lists [node_id, output_index].
            if isinstance(val, list) and len(val) == 2 and isinstance(val[1], int):
                ref_id = str(val[0])
                if ref_id not in node_ids:
                    ghost_edges.append({
                        "node_id": str(nid),
                        "input_key": key,
                        "ref_id": ref_id,
                        "issue": "ghost_edge_unresolved_node_id",
                    })

    if ghost_edges:
        raise PipelineIntegrityError(
            "DAG closure failure: " + str(ghost_edges)
        )

    has_sampler = any(
        isinstance(n, dict) and str(n.get("class_type", "")).startswith("KSampler")
        for n in workflow.values()
    )
    has_sink = any(
        isinstance(n, dict)
        and str(n.get("class_type", "")) in {"SaveImage", "VHS_VideoCombine", "PreviewImage"}
        for n in workflow.values()
    )
    if not has_sampler:
        raise PipelineIntegrityError("DAG closure failure: no KSampler* terminal node")
    if not has_sink:
        raise PipelineIntegrityError("DAG closure failure: no SaveImage / VHS_VideoCombine sink")

    return {
        "session": "SESSION-194",
        "feature": "validate_preset_topology_closure",
        "node_count": len(workflow),
        "ghost_edges": [],
        "has_sampler": has_sampler,
        "has_sink": has_sink,
        "status": "closed",
    }


__all__ = [
    "PipelineIntegrityError",
    "OPENPOSE_CONTROLNET_MODEL_DEFAULT",
    "OPENPOSE_SEQUENCE_DIR_SENTINEL",
    "TITLE_OPENPOSE_CONTROLNET_LOADER",
    "TITLE_OPENPOSE_VHS_LOADER",
    "TITLE_OPENPOSE_APPLY",
    "TITLE_IPADAPTER_APPLY",
    "hydrate_openpose_controlnet_chain",
    "hydrate_ipadapter_quartet",
    "validate_preset_topology_closure",
]
