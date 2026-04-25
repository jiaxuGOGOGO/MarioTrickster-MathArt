"""SESSION-205: ComfyUI Runtime Model Resolver — Dynamic Asset Discovery.

This module queries a live ComfyUI server's ``GET /object_info`` endpoint
to discover which models are *actually* installed on the user's machine,
then resolves the project's requested model filenames against the real
inventory.

Root-cause context (SESSION-205):
  The production pipeline hardcodes model filenames such as
  ``clip-vit-h-14-laion2B-s32B-b79K.safetensors`` and
  ``ip-adapter-plus_sd15.safetensors``.  If the user's ComfyUI has the
  same model under a *different* filename (e.g. ``clip_vision_h.safetensors``)
  or has not yet downloaded the model at all, the ``POST /prompt`` payload
  validation fails with ``value_not_in_list``.

Fix strategy:
  1. Query ``/object_info/{node_class}`` to get the real enum list for each
     model-loading node (``CLIPVisionLoader``, ``IPAdapterModelLoader``,
     ``ControlNetLoader``).
  2. Fuzzy-match the requested filename against the real list.
  3. If a match is found, return the server-side filename.
  4. If no match is found, return ``None`` so the caller can gracefully
     degrade (skip the node chain) instead of crashing.

Architecture discipline:
  - This module is a **pure query helper** — it NEVER mutates workflows,
    never uploads files, never modifies the pipeline.
  - All HTTP calls are wrapped in try/except with graceful fallback.
  - Follows the same ``host:port`` address convention as ``ComfyUIClient``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _get_object_info(
    server_address: str,
    node_class: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Fetch ``/object_info/{node_class}`` from a live ComfyUI server.

    Returns the parsed JSON dict for the node class, or ``None`` on any error.
    """
    try:
        url = f"http://{server_address}/object_info/{node_class}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get(node_class, data)
    except Exception as exc:
        logger.debug(
            "[model_resolver] Failed to query /object_info/%s: %s",
            node_class, exc,
        )
        return None


def _extract_enum_list(
    object_info: dict[str, Any] | None,
    input_key: str,
) -> list[str]:
    """Extract the enum value list for a given input key from object_info.

    ComfyUI object_info structure:
    ``{ "input": { "required": { "<key>": [ [list_of_values], {} ] } } }``
    """
    if object_info is None:
        return []
    try:
        required = object_info.get("input", {}).get("required", {})
        field = required.get(input_key)
        if isinstance(field, (list, tuple)) and len(field) >= 1:
            candidates = field[0]
            if isinstance(candidates, list):
                return candidates
    except Exception:
        pass
    return []


# SESSION-206: ControlNet model-type identifiers used by the type-safe
# fuzzy matcher to prevent cross-type misresolution (e.g. openpose → normalbae).
_CONTROLNET_TYPE_KEYWORDS: dict[str, set[str]] = {
    "openpose": {"openpose"},
    "normalbae": {"normalbae", "normal_bae"},
    "depth": {"depth"},
    "canny": {"canny"},
    "scribble": {"scribble"},
    "lineart": {"lineart"},
    "softedge": {"softedge", "hed"},
    "seg": {"seg"},
    "shuffle": {"shuffle"},
    "tile": {"tile"},
    "inpaint": {"inpaint"},
    "ip2p": {"ip2p", "instruct"},
    "mlsd": {"mlsd"},
}


def _detect_controlnet_type(filename: str) -> str | None:
    """Detect the ControlNet model type from a filename.

    SESSION-206: This is the type-safety guard that prevents the fuzzy
    matcher from resolving ``openpose`` to ``normalbae`` just because
    they share tokens like ``control``, ``v11p``, ``sd15``.

    Returns the detected type string (e.g. ``"openpose"``) or ``None``
    if the filename does not match any known ControlNet type.
    """
    lower = filename.lower()
    for type_name, keywords in _CONTROLNET_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return type_name
    return None


def _fuzzy_match(
    requested: str,
    available: list[str],
    *,
    require_same_controlnet_type: bool = False,
) -> str | None:
    """Fuzzy-match a requested model filename against available filenames.

    Match strategy (in priority order):
    1. Exact match (case-insensitive).
    2. Substring containment: requested stem is contained in an available name.
    3. Keyword overlap: match by shared significant tokens.

    SESSION-206 Type-Safety Guard:
    When ``require_same_controlnet_type=True``, the matcher will REJECT any
    candidate whose ControlNet type differs from the requested filename's
    type.  This prevents the catastrophic ``openpose → normalbae`` cross-type
    misresolution that was the root cause of the abstract symmetric output.

    Returns the best matching filename, or ``None`` if no match is found.
    """
    if not available:
        return None

    req_lower = requested.lower()
    _req_cnet_type = _detect_controlnet_type(req_lower) if require_same_controlnet_type else None

    def _type_compatible(candidate: str) -> bool:
        """Return True if the candidate is type-compatible with the request."""
        if _req_cnet_type is None:
            return True
        cand_type = _detect_controlnet_type(candidate)
        if cand_type is None:
            return True  # Unknown type — allow (conservative)
        return cand_type == _req_cnet_type

    # 1. Exact match (case-insensitive)
    for name in available:
        if name.lower() == req_lower:
            return name

    # 2. Stem containment
    import os
    req_stem = os.path.splitext(req_lower)[0]
    # Normalize common separators
    req_tokens = set(req_stem.replace("-", "_").replace(".", "_").split("_"))
    req_tokens.discard("")

    best_match = None
    best_score = 0

    for name in available:
        name_lower = name.lower()
        name_stem = os.path.splitext(name_lower)[0]
        name_tokens = set(name_stem.replace("-", "_").replace(".", "_").split("_"))
        name_tokens.discard("")

        # Check if the requested stem is a substring of the available name
        if req_stem in name_lower or name_stem in req_lower:
            if _type_compatible(name):
                return name
            else:
                logger.warning(
                    "[model_resolver] SESSION-206 Type-safety REJECT: "
                    "'%s' substring-matched '%s' but ControlNet type differs "
                    "(requested=%s, candidate=%s)",
                    requested, name, _req_cnet_type,
                    _detect_controlnet_type(name),
                )
                continue

        # 3. Token overlap scoring
        overlap = req_tokens & name_tokens
        score = len(overlap)
        if score > best_score and _type_compatible(name):
            best_score = score
            best_match = name

    # Require at least 2 overlapping tokens for a fuzzy match
    if best_score >= 2:
        return best_match

    return None


def resolve_clip_vision_model(
    server_address: str,
    requested: str = "clip-vit-h-14-laion2B-s32B-b79K.safetensors",
    *,
    timeout: float = 5.0,
) -> str | None:
    """Resolve the CLIP Vision model filename against the live ComfyUI server.

    Returns the server-side filename if found, or ``None`` if unavailable.
    """
    info = _get_object_info(server_address, "CLIPVisionLoader", timeout=timeout)
    available = _extract_enum_list(info, "clip_name")
    if not available:
        logger.warning(
            "[model_resolver] CLIPVisionLoader: no models available on server"
        )
        return None

    match = _fuzzy_match(requested, available)
    if match:
        logger.info(
            "[model_resolver] CLIPVisionLoader: resolved '%s' → '%s' (available: %s)",
            requested, match, available,
        )
    else:
        logger.warning(
            "[model_resolver] CLIPVisionLoader: '%s' not found in %s",
            requested, available,
        )
    return match


def resolve_ipadapter_model(
    server_address: str,
    requested: str = "ip-adapter-plus_sd15.safetensors",
    *,
    timeout: float = 5.0,
) -> str | None:
    """Resolve the IPAdapter model filename against the live ComfyUI server.

    Returns the server-side filename if found, or ``None`` if unavailable.
    """
    info = _get_object_info(server_address, "IPAdapterModelLoader", timeout=timeout)
    available = _extract_enum_list(info, "ipadapter_file")
    if not available:
        logger.warning(
            "[model_resolver] IPAdapterModelLoader: no models available on server"
        )
        return None

    match = _fuzzy_match(requested, available)
    if match:
        logger.info(
            "[model_resolver] IPAdapterModelLoader: resolved '%s' → '%s' (available: %s)",
            requested, match, available,
        )
    else:
        logger.warning(
            "[model_resolver] IPAdapterModelLoader: '%s' not found in %s",
            requested, available,
        )
    return match


def resolve_controlnet_model(
    server_address: str,
    requested: str = "control_v11p_sd15_openpose.pth",
    *,
    timeout: float = 5.0,
) -> str | None:
    """Resolve a ControlNet model filename against the live ComfyUI server.

    SESSION-206: Uses ``require_same_controlnet_type=True`` to prevent
    cross-type misresolution (e.g. openpose → normalbae).

    Returns the server-side filename if found, or ``None`` if unavailable.
    """
    info = _get_object_info(server_address, "ControlNetLoader", timeout=timeout)
    available = _extract_enum_list(info, "control_net_name")
    if not available:
        logger.warning(
            "[model_resolver] ControlNetLoader: no models available on server"
        )
        return None

    # SESSION-206: Enable type-safe matching for ControlNet models
    match = _fuzzy_match(requested, available, require_same_controlnet_type=True)
    if match:
        logger.info(
            "[model_resolver] ControlNetLoader: resolved '%s' → '%s' (available: %s)",
            requested, match, available,
        )
    else:
        logger.warning(
            "[model_resolver] ControlNetLoader: '%s' not found in %s "
            "(type-safe matching enabled, cross-type candidates rejected)",
            requested, available,
        )
    return match


def resolve_all_ipadapter_models(
    server_address: str,
    *,
    clip_vision_requested: str = "clip-vit-h-14-laion2B-s32B-b79K.safetensors",
    ipadapter_requested: str = "ip-adapter-plus_sd15.safetensors",
    timeout: float = 5.0,
) -> dict[str, str | None]:
    """Resolve all IPAdapter-related models in one call.

    Returns a dict with keys ``clip_vision``, ``ipadapter``, each mapping
    to the resolved server-side filename or ``None``.
    """
    return {
        "clip_vision": resolve_clip_vision_model(
            server_address, clip_vision_requested, timeout=timeout,
        ),
        "ipadapter": resolve_ipadapter_model(
            server_address, ipadapter_requested, timeout=timeout,
        ),
    }


def resolve_openpose_controlnet(
    server_address: str,
    requested: str = "control_v11p_sd15_openpose.pth",
    *,
    timeout: float = 5.0,
) -> str | None:
    """Resolve the OpenPose ControlNet model filename.

    Returns the server-side filename if found, or ``None`` if unavailable.
    """
    return resolve_controlnet_model(server_address, requested, timeout=timeout)


def upload_reference_image(
    server_address: str,
    image_path: str,
    *,
    timeout: float = 10.0,
) -> str | None:
    """Upload a reference image to ComfyUI via POST /upload/image.

    This is the **only** sanctioned way to make images available to
    ComfyUI's ``LoadImage`` node. NEVER reference local filesystem paths
    directly — they are invisible to the ComfyUI server process.

    Returns the server-side filename on success, or ``None`` on failure.
    """
    import io
    import os
    import uuid
    from pathlib import Path

    path = Path(image_path)
    if not path.exists():
        logger.warning(
            "[model_resolver] upload_reference_image: file not found: %s",
            image_path,
        )
        return None

    image_data = path.read_bytes()
    filename = path.name

    boundary = f"----MathArtBoundary{uuid.uuid4().hex[:16]}"
    body = io.BytesIO()

    # File part
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="image"; '
        f'filename="{filename}"\r\n'.encode()
    )
    body.write(b"Content-Type: image/png\r\n\r\n")
    body.write(image_data)
    body.write(b"\r\n")

    # Overwrite flag
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="overwrite"\r\n\r\n')
    body.write(b"true")
    body.write(b"\r\n")

    # Type field
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="type"\r\n\r\n')
    body.write(b"input")
    body.write(b"\r\n")

    # End boundary
    body.write(f"--{boundary}--\r\n".encode())
    body_bytes = body.getvalue()

    try:
        url = f"http://{server_address}/upload/image"
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            server_filename = result.get("name", filename)
            logger.info(
                "[model_resolver] Uploaded reference image %s → server filename: %s",
                path.name, server_filename,
            )
            return server_filename
    except Exception as exc:
        logger.warning(
            "[model_resolver] upload_reference_image failed: %s", exc,
        )
        return None


__all__ = [
    "resolve_clip_vision_model",
    "resolve_ipadapter_model",
    "resolve_controlnet_model",
    "resolve_all_ipadapter_models",
    "resolve_openpose_controlnet",
    "upload_reference_image",
]
