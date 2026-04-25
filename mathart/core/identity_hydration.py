"""SESSION-193: IPAdapter Identity Hydration — Zero-Shot Visual Feature Lock.

Industrial References:
  - IP-Adapter (Ye et al., 2023): Image Prompt Adapter for text-to-image
    diffusion models. Extracts CLIP-Vision embeddings from a reference image
    and injects them into the cross-attention layers, enabling zero-shot
    identity preservation without fine-tuning.
  - ComfyUI_IPAdapter_plus (cubiq): The canonical ComfyUI implementation.
    Nodes: ``IPAdapterModelLoader``, ``CLIPVisionLoader``, ``IPAdapterAdvanced``
    (replaces deprecated ``IPAdapterApply``).
  - Golden weight band: 0.80–0.85 empirically balances identity fidelity
    with creative freedom. SESSION-193 locks at **0.85**.

Architecture Discipline:
  This module is a **standalone helper** — it does NOT modify the trunk
  pipeline, the preset manager, or the orchestrator. It exposes pure
  functions that the downstream payload assembly can call to *augment*
  an already-assembled ComfyUI workflow dict with IPAdapter nodes.

  All node addressing uses ``class_type`` / ``_meta.title`` semantic
  selectors — **NEVER** hardcoded numeric node IDs.

Hard Red Lines:
  - NEVER touches proxy environment variables.
  - NEVER modifies SESSION-189 anchors (MAX_FRAMES / LATENT_EDGE / NORMAL_MATTE_RGB).
  - NEVER modifies the anime_rhythmic_subsample or latent_healing logic.
  - NEVER modifies force_decouple_dummy_mesh_payload algorithm.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════

# SESSION-193: IPAdapter identity lock weight — the "golden band" centre.
# Research consensus (IP-Adapter paper + ComfyUI community): 0.80–0.85
# preserves identity while allowing the diffusion model creative latitude.
IPADAPTER_IDENTITY_WEIGHT: float = 0.85

# Default model filenames (user can override via context dict).
DEFAULT_IPADAPTER_MODEL: str = "ip-adapter-plus_sd15.safetensors"
DEFAULT_CLIP_VISION_MODEL: str = "clip-vit-h-14-laion2B-s32B-b79K.safetensors"

# Semantic class_type identifiers for ComfyUI node graph traversal.
IPADAPTER_APPLY_CLASS_TYPES = frozenset({
    "IPAdapterApply",
    "IPAdapterApplyAdvanced",
    "IPAdapter",
    "IPAdapterAdvanced",
})
CLIP_VISION_LOADER_CLASS_TYPE = "CLIPVisionLoader"
IPADAPTER_MODEL_LOADER_CLASS_TYPE = "IPAdapterModelLoader"
LOAD_IMAGE_CLASS_TYPE = "LoadImage"


# ═══════════════════════════════════════════════════════════════════════════
#  Semantic Node Finder (class_type + _meta.title)
# ═══════════════════════════════════════════════════════════════════════════

def _find_node_by_class(
    workflow: dict[str, Any],
    class_type: str | frozenset[str],
    title_hint: str | None = None,
) -> tuple[str, dict] | None:
    """Find a node in the workflow by class_type (and optional title hint).

    Returns ``(node_id, node_dict)`` or ``None``.
    NEVER uses hardcoded numeric IDs — always iterates ``workflow.values()``.
    """
    candidates: list[tuple[str, dict]] = []
    check_set = class_type if isinstance(class_type, frozenset) else frozenset({class_type})
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        if ct not in check_set:
            continue
        if title_hint:
            meta = node.get("_meta", {})
            title = str(meta.get("title", "")).lower()
            if title_hint.lower() in title:
                return (nid, node)
            candidates.append((nid, node))
        else:
            candidates.append((nid, node))
    return candidates[0] if candidates else None


def _next_free_id(workflow: dict[str, Any]) -> str:
    """Return the next unused numeric string ID for a new node."""
    max_id = 0
    for nid in workflow:
        try:
            max_id = max(max_id, int(nid))
        except (ValueError, TypeError):
            pass
    return str(max_id + 1)


# ═══════════════════════════════════════════════════════════════════════════
#  Core: Inject IPAdapter + CLIP Vision into workflow
# ═══════════════════════════════════════════════════════════════════════════

def inject_ipadapter_identity_lock(
    workflow: dict[str, Any],
    reference_image_path: str,
    *,
    weight: float = IPADAPTER_IDENTITY_WEIGHT,
    ipadapter_model: str = DEFAULT_IPADAPTER_MODEL,
    clip_vision_model: str = DEFAULT_CLIP_VISION_MODEL,
) -> dict[str, Any]:
    """Dynamically inject IPAdapter + CLIP Vision nodes into a ComfyUI workflow.

    This function augments an existing workflow dict (the ``prompt`` field
    of a ComfyUI API payload) with three nodes:

    1. **LoadImage** — loads the visual reference from ``reference_image_path``.
    2. **CLIPVisionLoader** — loads the CLIP-ViT-H vision encoder.
    3. **IPAdapterModelLoader** — loads the IP-Adapter model weights.
    4. **IPAdapterAdvanced** — wires reference embedding into the model's
       cross-attention, with ``weight`` set to the golden 0.85.

    If IPAdapter nodes already exist in the workflow (detected by
    ``class_type``), their weight is updated in-place instead of
    duplicating nodes.

    Parameters
    ----------
    workflow : dict
        The ComfyUI workflow API dict (``payload["prompt"]``).
    reference_image_path : str
        Absolute path to the reference image on the ComfyUI server's
        filesystem (or a filename in ComfyUI's ``input/`` directory).
    weight : float
        IPAdapter application weight. Default 0.85.
    ipadapter_model : str
        Filename of the IP-Adapter model checkpoint.
    clip_vision_model : str
        Filename of the CLIP Vision encoder checkpoint.

    Returns
    -------
    dict
        Injection report with keys: ``injected``, ``mode`` (``"new"`` or
        ``"update"``), ``weight``, ``reference_image``, ``touched_nodes``.
    """
    report: dict[str, Any] = {
        "session": "SESSION-193",
        "feature": "ipadapter_identity_lock",
        "reference_image": reference_image_path,
        "weight": weight,
        "touched_nodes": [],
    }

    # ── Case 1: IPAdapter nodes already exist — update weight in-place ──
    existing = _find_node_by_class(workflow, IPADAPTER_APPLY_CLASS_TYPES)
    if existing is not None:
        nid, node = existing
        inputs = node.get("inputs", {})
        prev_weight = inputs.get("weight", None)
        inputs["weight"] = float(weight)
        report["mode"] = "update"
        report["injected"] = True
        report["touched_nodes"].append({
            "node_id": nid,
            "class_type": node.get("class_type"),
            "operation": "weight_update",
            "weight": [prev_weight, float(weight)],
        })
        # Also try to update the reference image on the LoadImage node
        # that feeds into the IPAdapter.
        ref_input = inputs.get("image")
        if isinstance(ref_input, list) and len(ref_input) == 2:
            ref_node_id = str(ref_input[0])
            if ref_node_id in workflow:
                ref_node = workflow[ref_node_id]
                if ref_node.get("class_type") == LOAD_IMAGE_CLASS_TYPE:
                    ref_node.setdefault("inputs", {})["image"] = os.path.basename(reference_image_path)
                    report["touched_nodes"].append({
                        "node_id": ref_node_id,
                        "class_type": LOAD_IMAGE_CLASS_TYPE,
                        "operation": "reference_image_update",
                        "image": reference_image_path,
                    })
        logger.info(
            "[SESSION-193] IPAdapter identity lock UPDATED in-place: "
            "weight %.2f -> %.2f, ref=%s",
            prev_weight or 0.0, weight, reference_image_path,
        )
        return report

    # ── Case 2: No IPAdapter nodes — inject fresh node chain ──
    # Find the model (checkpoint) loader to wire the IPAdapter into.
    model_node = _find_node_by_class(workflow, frozenset({"CheckpointLoaderSimple"}))
    if model_node is None:
        report["mode"] = "skipped"
        report["injected"] = False
        report["reason"] = "No CheckpointLoaderSimple found in workflow"
        logger.warning("[SESSION-193] Cannot inject IPAdapter — no checkpoint loader found")
        return report

    model_node_id, _ = model_node

    # Find the KSampler to wire the conditioned model output into.
    ksampler_node = _find_node_by_class(
        workflow, frozenset({"KSampler", "KSamplerAdvanced"})
    )
    ksampler_id = ksampler_node[0] if ksampler_node else None

    # Allocate new node IDs
    load_image_id = _next_free_id(workflow)
    workflow[load_image_id] = {
        "class_type": "LoadImage",
        "inputs": {
            "image": os.path.basename(reference_image_path),
        },
        "_meta": {"title": "SESSION-193 Identity Reference Image"},
    }

    clip_vision_id = _next_free_id(workflow)
    workflow[clip_vision_id] = {
        "class_type": "CLIPVisionLoader",
        "inputs": {
            "clip_name": clip_vision_model,
        },
        "_meta": {"title": "SESSION-193 CLIP Vision Loader"},
    }

    ipadapter_loader_id = _next_free_id(workflow)
    workflow[ipadapter_loader_id] = {
        "class_type": "IPAdapterModelLoader",
        "inputs": {
            "ipadapter_file": ipadapter_model,
        },
        "_meta": {"title": "SESSION-193 IPAdapter Model Loader"},
    }

    ipadapter_apply_id = _next_free_id(workflow)
    workflow[ipadapter_apply_id] = {
        "class_type": "IPAdapterAdvanced",
        "inputs": {
            "weight": float(weight),
            "weight_type": "linear",
            "combine_embeds": "concat",
            "start_at": 0.0,
            "end_at": 1.0,
            "embeds_scaling": "V only",
            # Wire connections: [source_node_id, output_index]
            "ipadapter": [ipadapter_loader_id, 0],
            "clip_vision": [clip_vision_id, 0],
            "image": [load_image_id, 0],
            "model": [model_node_id, 0],
        },
        "_meta": {"title": "SESSION-193 IPAdapter Identity Lock"},
    }

    # Rewire KSampler to use the IPAdapter-conditioned model output
    if ksampler_id and ksampler_id in workflow:
        ks_inputs = workflow[ksampler_id].get("inputs", {})
        model_input = ks_inputs.get("model")
        if isinstance(model_input, list) and len(model_input) == 2:
            if str(model_input[0]) == model_node_id:
                ks_inputs["model"] = [ipadapter_apply_id, 0]
                report["touched_nodes"].append({
                    "node_id": ksampler_id,
                    "class_type": workflow[ksampler_id].get("class_type"),
                    "operation": "model_rewire_to_ipadapter",
                    "model": [model_node_id, ipadapter_apply_id],
                })

    report["mode"] = "new"
    report["injected"] = True
    report["touched_nodes"].extend([
        {"node_id": load_image_id, "class_type": "LoadImage", "operation": "created"},
        {"node_id": clip_vision_id, "class_type": "CLIPVisionLoader", "operation": "created"},
        {"node_id": ipadapter_loader_id, "class_type": "IPAdapterModelLoader", "operation": "created"},
        {"node_id": ipadapter_apply_id, "class_type": "IPAdapterAdvanced", "operation": "created"},
    ])

    logger.info(
        "[SESSION-193] IPAdapter identity lock INJECTED: "
        "weight=%.2f, ref=%s, nodes=%s",
        weight, reference_image_path,
        [load_image_id, clip_vision_id, ipadapter_loader_id, ipadapter_apply_id],
    )
    return report


# ═══════════════════════════════════════════════════════════════════════════
#  Context-level helper: check and extract visual reference path
# ═══════════════════════════════════════════════════════════════════════════

def extract_visual_reference_path(context: dict[str, Any]) -> str | None:
    """Extract the visual reference image path from the pipeline context.

    Checks multiple possible locations where the CLI wizard or upstream
    stages may have stashed the reference path.

    Returns the absolute path string, or None if no reference is available.
    """
    # Direct field (SESSION-193 canonical location)
    path = context.get("_visual_reference_path")
    if path and Path(path).exists():
        return str(path)

    # Nested in identity_lock config
    identity_cfg = context.get("identity_lock", {})
    if isinstance(identity_cfg, dict):
        path = identity_cfg.get("reference_image_path")
        if path and Path(path).exists():
            return str(path)

    # Nested in director_studio_spec
    spec = context.get("director_studio_spec", {})
    if isinstance(spec, dict):
        path = spec.get("_visual_reference_path")
        if path and Path(path).exists():
            return str(path)

    return None


__all__ = [
    "IPADAPTER_IDENTITY_WEIGHT",
    "DEFAULT_IPADAPTER_MODEL",
    "DEFAULT_CLIP_VISION_MODEL",
    "inject_ipadapter_identity_lock",
    "extract_visual_reference_path",
]
