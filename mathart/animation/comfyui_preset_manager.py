"""ComfyUI workflow_api preset manager for data-driven anti-flicker rendering.

SESSION-084 (P1-AI-2D)
----------------------
This module enforces the uploaded architectural red lines:

1. Workflow topology MUST live in external ``workflow_api.json`` assets.
2. Runtime code MUST inject only values, never rebuild node graphs in Python.
3. Node binding MUST use semantic selectors (``class_type`` + ``_meta.title``),
   never hardcoded numeric node IDs.
4. Tests can validate preset loading and payload assembly offline without any
   live ComfyUI HTTP server.

SESSION-086 (P1-AI-2D-SPARSECTRL)
----------------------------------
Extends the preset manager with **sequence-aware injection** for temporal
consistency workflows based on SparseCtrl + AnimateDiff + VHS:

1. New ``_SPARSECTRL_SELECTORS`` define semantic bindings for VHS directory-
   based frame loaders, AnimateDiff context options, SparseCtrl model, and
   VHS_VideoCombine output nodes.
2. ``assemble_sequence_payload()`` accepts frame sequence directories instead
   of single image paths, and injects ``batch_size``, ``context_length``,
   ``frame_rate``, and directory paths into the correct VHS/AnimateDiff nodes.
3. The preset validation is **preset-specific**: ``validate_preset_structure``
   now accepts an optional ``selectors`` argument so the SparseCtrl preset
   is validated against its own selector set, not the single-image selectors.

Anti-pattern guards (SESSION-086 red lines):
- 🚫 Single-Frame Fallacy: sequence presets use VHS_LoadImagesPath directories,
  NEVER single-image LoadImage nodes for guide channels.
- 🚫 Python Topology Trap: all node wiring lives in the JSON asset; this code
  only locates nodes by (class_type, _meta.title) and injects scalar values
  or directory path strings.
- 🚫 CI HTTP Blocking Trap: no HTTP calls; payload assembly is 100% offline.
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_DEFAULT_PRESET_NAME = "dual_controlnet_ipadapter"
_SPARSECTRL_PRESET_NAME = "sparsectrl_animatediff"


@dataclass(frozen=True)
class NodeSelector:
    """Semantic node selector used for safe workflow injection."""

    role: str
    class_type: str
    title_contains: str
    input_key: str
    required: bool = True


# ---------------------------------------------------------------------------
# Selectors for the original dual_controlnet_ipadapter preset (SESSION-084)
# ---------------------------------------------------------------------------
_PRESET_SELECTORS: tuple[NodeSelector, ...] = (
    NodeSelector("checkpoint", "CheckpointLoaderSimple", "load checkpoint", "ckpt_name"),
    NodeSelector("positive_prompt", "CLIPTextEncode", "positive prompt", "text"),
    NodeSelector("negative_prompt", "CLIPTextEncode", "negative prompt", "text"),
    NodeSelector("source_image", "LoadImage", "source image", "image"),
    NodeSelector("normal_image", "LoadImage", "normal guide", "image"),
    NodeSelector("depth_image", "LoadImage", "depth guide", "image"),
    NodeSelector("identity_image", "LoadImage", "identity reference", "image"),
    NodeSelector("normal_controlnet", "ControlNetLoader", "normal controlnet", "control_net_name"),
    NodeSelector("depth_controlnet", "ControlNetLoader", "depth controlnet", "control_net_name"),
    NodeSelector("normal_apply", "ControlNetApply", "normal controlnet", "strength"),
    NodeSelector("depth_apply", "ControlNetApply", "depth controlnet", "strength"),
    NodeSelector("clip_vision", "CLIPVisionLoader", "clip vision", "clip_name"),
    NodeSelector("ip_adapter_loader", "IPAdapterModelLoader", "ip-adapter", "ipadapter_file"),
    NodeSelector("ip_adapter_apply", "IPAdapterApply", "ip-adapter", "weight"),
    NodeSelector("ksampler_seed", "KSampler", "ksampler", "seed"),
    NodeSelector("ksampler_steps", "KSampler", "ksampler", "steps"),
    NodeSelector("ksampler_cfg", "KSampler", "ksampler", "cfg"),
    NodeSelector("ksampler_denoise", "KSampler", "ksampler", "denoise"),
    NodeSelector("save_output", "SaveImage", "save output", "filename_prefix"),
)


# ---------------------------------------------------------------------------
# Selectors for the sparsectrl_animatediff preset (SESSION-086)
# ---------------------------------------------------------------------------
_SPARSECTRL_SELECTORS: tuple[NodeSelector, ...] = (
    # --- Model loading ---
    NodeSelector("checkpoint", "CheckpointLoaderSimple", "load checkpoint", "ckpt_name"),
    # --- Text conditioning ---
    NodeSelector("positive_prompt", "CLIPTextEncode", "positive prompt", "text"),
    NodeSelector("negative_prompt", "CLIPTextEncode", "negative prompt", "text"),
    # --- AnimateDiff temporal backbone ---
    NodeSelector("animatediff_model", "ADE_AnimateDiffLoaderWithContext", "animatediff loader", "model_name"),
    NodeSelector("animatediff_beta", "ADE_AnimateDiffLoaderWithContext", "animatediff loader", "beta_schedule"),
    # --- AnimateDiff context options ---
    NodeSelector("context_length", "ADE_AnimateDiffUniformContextOptions", "context options", "context_length"),
    NodeSelector("context_overlap", "ADE_AnimateDiffUniformContextOptions", "context options", "context_overlap"),
    # --- VHS sequence frame loaders (directory-based, NOT single-image) ---
    NodeSelector("normal_sequence_dir", "VHS_LoadImagesPath", "load normal sequence", "directory"),
    NodeSelector("depth_sequence_dir", "VHS_LoadImagesPath", "load depth sequence", "directory"),
    NodeSelector("rgb_sequence_dir", "VHS_LoadImagesPath", "load rgb sequence", "directory"),
    # --- ControlNet models ---
    NodeSelector("normal_controlnet", "ControlNetLoader", "normal controlnet", "control_net_name"),
    NodeSelector("depth_controlnet", "ControlNetLoader", "depth controlnet", "control_net_name"),
    # --- ControlNet application strengths ---
    NodeSelector("normal_apply", "ControlNetApplyAdvanced", "apply normal controlnet", "strength"),
    NodeSelector("depth_apply", "ControlNetApplyAdvanced", "apply depth controlnet", "strength"),
    NodeSelector("sparsectrl_apply", "ControlNetApplyAdvanced", "apply sparsectrl rgb", "strength"),
    NodeSelector("sparsectrl_end_percent", "ControlNetApplyAdvanced", "apply sparsectrl rgb", "end_percent"),
    # --- SparseCtrl RGB preprocessor (required by SparseCtrl, no injectable value) ---
    NodeSelector("sparsectrl_rgb_preprocess", "ACN_SparseCtrlRGBPreprocessor", "sparsectrl rgb preprocessor", "image", required=False),
    # --- SparseCtrl model ---
    NodeSelector("sparsectrl_model", "ACN_SparseCtrlLoaderAdvanced", "load sparsectrl model", "sparsectrl_name"),
    NodeSelector("sparsectrl_strength", "ACN_SparseCtrlLoaderAdvanced", "load sparsectrl model", "motion_strength"),
    # --- Latent batch (frame count) ---
    NodeSelector("latent_batch_size", "EmptyLatentImage", "empty latent batch", "batch_size"),
    NodeSelector("latent_width", "EmptyLatentImage", "empty latent batch", "width"),
    NodeSelector("latent_height", "EmptyLatentImage", "empty latent batch", "height"),
    # --- KSampler ---
    NodeSelector("ksampler_seed", "KSampler", "ksampler", "seed"),
    NodeSelector("ksampler_steps", "KSampler", "ksampler", "steps"),
    NodeSelector("ksampler_cfg", "KSampler", "ksampler", "cfg"),
    NodeSelector("ksampler_denoise", "KSampler", "ksampler", "denoise"),
    # --- Video output ---
    NodeSelector("video_frame_rate", "VHS_VideoCombine", "video combine output", "frame_rate"),
    NodeSelector("video_filename", "VHS_VideoCombine", "video combine output", "filename_prefix"),
    # --- Frame save output ---
    NodeSelector("save_frame_output", "SaveImage", "save frame output", "filename_prefix"),
)


class PresetBindingError(ValueError):
    """Raised when a preset cannot be injected safely."""


class ComfyUIPresetManager:
    """Load and inject ComfyUI API presets using semantic selectors only."""

    def __init__(self, preset_root: str | Path | None = None) -> None:
        self.preset_root = Path(preset_root) if preset_root else self.default_preset_root()

    @staticmethod
    def default_preset_root() -> Path:
        return Path(__file__).resolve().parents[1] / "assets" / "comfyui_presets"

    def available_presets(self) -> list[str]:
        if not self.preset_root.exists():
            return []
        return sorted(path.stem for path in self.preset_root.glob("*.json"))

    def resolve_preset_path(self, preset_name: str = _DEFAULT_PRESET_NAME) -> Path:
        candidate = self.preset_root / f"{preset_name}.json"
        if not candidate.exists():
            raise FileNotFoundError(
                f"ComfyUI preset {preset_name!r} not found under {self.preset_root}"
            )
        return candidate.resolve()

    def load_preset(
        self,
        preset_name: str = _DEFAULT_PRESET_NAME,
        *,
        selectors: tuple[NodeSelector, ...] | None = None,
    ) -> dict[str, Any]:
        """Load and validate a preset JSON asset.

        Parameters
        ----------
        preset_name : str
            Stem of the JSON file under the preset root.
        selectors : tuple[NodeSelector, ...] | None
            If provided, validate against these selectors instead of the
            default ``_PRESET_SELECTORS``.  This allows the SparseCtrl
            preset to be validated against its own selector set.
        """
        path = self.resolve_preset_path(preset_name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload:
            raise PresetBindingError(f"Preset {preset_name!r} is not a non-empty workflow dict")
        self.validate_preset_structure(payload, selectors=selectors)
        return payload

    def validate_preset_structure(
        self,
        workflow: dict[str, Any],
        *,
        selectors: tuple[NodeSelector, ...] | None = None,
    ) -> None:
        """Validate that all required semantic nodes exist in the workflow.

        Parameters
        ----------
        selectors : tuple[NodeSelector, ...] | None
            Selector set to validate against.  Defaults to
            ``_PRESET_SELECTORS`` for backward compatibility.
        """
        check_selectors = selectors if selectors is not None else _PRESET_SELECTORS
        missing: list[str] = []
        for selector in check_selectors:
            try:
                self.find_node(workflow, selector.class_type, selector.title_contains)
            except PresetBindingError:
                if selector.required:
                    missing.append(f"{selector.class_type}:{selector.title_contains}")
        if missing:
            raise PresetBindingError(
                "Preset is missing required semantic nodes: " + ", ".join(sorted(missing))
            )

    def find_node(
        self,
        workflow: dict[str, Any],
        class_type: str,
        title_contains: str,
    ) -> tuple[str, dict[str, Any]]:
        matches: list[tuple[str, dict[str, Any]]] = []
        needle = title_contains.strip().lower()
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") != class_type:
                continue
            title = str(node.get("_meta", {}).get("title", "")).strip().lower()
            if needle in title:
                matches.append((str(node_id), node))
        if not matches:
            raise PresetBindingError(
                f"No node found for class_type={class_type!r}, title_contains={title_contains!r}"
            )
        if len(matches) > 1:
            ids = ", ".join(node_id for node_id, _ in matches)
            raise PresetBindingError(
                f"Ambiguous semantic selector class_type={class_type!r}, "
                f"title_contains={title_contains!r}; matched node ids: {ids}"
            )
        return matches[0]

    def _set_input(
        self,
        workflow: dict[str, Any],
        *,
        selector: NodeSelector,
        value: Any,
        bindings: dict[str, dict[str, Any]],
    ) -> None:
        node_id, node = self.find_node(workflow, selector.class_type, selector.title_contains)
        inputs = node.setdefault("inputs", {})
        inputs[selector.input_key] = value
        bindings[selector.role] = {
            "node_id": node_id,
            "class_type": selector.class_type,
            "title": node.get("_meta", {}).get("title", ""),
            "input_key": selector.input_key,
            "value": value,
        }

    def _build_bindings_table(self, bindings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for role in sorted(bindings):
            row = dict(bindings[role])
            row["role"] = role
            rows.append(row)
        return rows

    # ===================================================================
    # SESSION-084: Single-image preset assembly (dual_controlnet_ipadapter)
    # ===================================================================

    def assemble_payload(
        self,
        *,
        preset_name: str = _DEFAULT_PRESET_NAME,
        source_image_path: str | Path,
        normal_map_path: str | Path,
        depth_map_path: str | Path,
        prompt: str,
        negative_prompt: str = "",
        identity_reference_path: str | Path | None = None,
        use_ip_adapter: bool = True,
        ip_adapter_weight: float = 0.85,
        ip_adapter_model_name: str = "ip-adapter-plus_sdxl_vit-h.safetensors",
        ip_adapter_clip_vision_name: str = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
        normal_controlnet_name: str = "control_v11p_sd15_normalbae.pth",
        depth_controlnet_name: str = "control_v11f1p_sd15_depth.pth",
        model_checkpoint: str = "sd_xl_base_1.0.safetensors",
        steps: int = 20,
        cfg_scale: float = 7.5,
        denoising_strength: float = 0.65,
        normal_weight: float = 1.0,
        depth_weight: float = 1.0,
        seed: int = -1,
        client_id: str | None = None,
        filename_prefix: str = "mathart_neural",
    ) -> dict[str, Any]:
        workflow = copy.deepcopy(self.load_preset(preset_name))
        bindings: dict[str, dict[str, Any]] = {}

        if seed < 0:
            seed = int(time.time_ns() % (2**31))

        source_path = str(Path(source_image_path).resolve())
        normal_path = str(Path(normal_map_path).resolve())
        depth_path = str(Path(depth_map_path).resolve())
        identity_path = str(Path(identity_reference_path).resolve()) if identity_reference_path else source_path

        selector_map = {selector.role: selector for selector in _PRESET_SELECTORS}
        injections = {
            "checkpoint": model_checkpoint,
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt,
            "source_image": source_path,
            "normal_image": normal_path,
            "depth_image": depth_path,
            "identity_image": identity_path,
            "normal_controlnet": normal_controlnet_name,
            "depth_controlnet": depth_controlnet_name,
            "normal_apply": float(normal_weight),
            "depth_apply": float(depth_weight),
            "clip_vision": ip_adapter_clip_vision_name,
            "ip_adapter_loader": ip_adapter_model_name,
            "ip_adapter_apply": float(ip_adapter_weight if use_ip_adapter else 0.0),
            "ksampler_seed": int(seed),
            "ksampler_steps": int(steps),
            "ksampler_cfg": float(cfg_scale),
            "ksampler_denoise": float(denoising_strength),
            "save_output": filename_prefix,
        }

        for role, value in injections.items():
            self._set_input(
                workflow,
                selector=selector_map[role],
                value=value,
                bindings=bindings,
            )

        guides_locked = ["normal", "depth"]
        guides_requested = list(guides_locked)
        if use_ip_adapter:
            guides_requested.append("ip_adapter_identity")

        preset_path = self.resolve_preset_path(preset_name)
        lock_manifest = {
            "preset_name": preset_name,
            "preset_path": str(preset_path),
            "controlnet_guides": guides_locked,
            "guides_requested": guides_requested,
            "identity_reference_present": identity_reference_path is not None,
            "identity_lock_requested": bool(use_ip_adapter and identity_reference_path is not None),
            "identity_lock_active": bool(use_ip_adapter),
            "seed": int(seed),
            "node_count": len(workflow),
            "semantic_bindings": self._build_bindings_table(bindings),
            "workflow_contract": {
                "format": "workflow_api_json",
                "injection_mode": "semantic_selector",
                "selector_fields": ["class_type", "_meta.title"],
            },
        }

        return {
            "client_id": client_id or str(uuid.uuid4()),
            "prompt": workflow,
            "mathart_lock_manifest": lock_manifest,
        }

    # ===================================================================
    # SESSION-086: Sequence-aware preset assembly (sparsectrl_animatediff)
    # ===================================================================

    def assemble_sequence_payload(
        self,
        *,
        preset_name: str = _SPARSECTRL_PRESET_NAME,
        normal_sequence_dir: str | Path,
        depth_sequence_dir: str | Path,
        rgb_sequence_dir: str | Path,
        prompt: str,
        negative_prompt: str = "",
        normal_controlnet_name: str = "control_v11p_sd15_normalbae.pth",
        depth_controlnet_name: str = "control_v11f1p_sd15_depth.pth",
        sparsectrl_model_name: str = "v3_sd15_sparsectrl_rgb.ckpt",
        sparsectrl_strength: float = 1.0,
        sparsectrl_end_percent: float = 0.5,
        animatediff_model_name: str = "v3_sd15_mm.ckpt",
        animatediff_beta_schedule: str = "autoselect",
        model_checkpoint: str = "v1-5-pruned-emaonly.safetensors",
        frame_count: int = 16,
        context_length: int = 16,
        context_overlap: int = 4,
        frame_rate: int = 12,
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        cfg_scale: float = 7.5,
        denoising_strength: float = 1.0,
        normal_weight: float = 1.0,
        depth_weight: float = 1.0,
        seed: int = -1,
        client_id: str | None = None,
        filename_prefix: str = "mathart_sparsectrl",
    ) -> dict[str, Any]:
        """Assemble a sequence-aware ComfyUI payload for SparseCtrl + AnimateDiff.

        Unlike ``assemble_payload()`` which injects single image paths,
        this method injects **directory paths** for VHS_LoadImagesPath nodes,
        synchronizes the ``batch_size`` with ``frame_count``, and configures
        AnimateDiff context options and SparseCtrl model parameters.

        All node wiring is defined in the external JSON preset asset.
        This method ONLY injects scalar values and path strings.

        Parameters
        ----------
        normal_sequence_dir : str | Path
            Directory containing the normal map frame sequence.
        depth_sequence_dir : str | Path
            Directory containing the depth map frame sequence.
        rgb_sequence_dir : str | Path
            Directory containing the RGB reference frame sequence
            (sparse keyframes for SparseCtrl conditioning).
        frame_count : int
            Total number of frames to generate.  Injected into
            ``EmptyLatentImage.batch_size`` to synchronize with AnimateDiff.
        context_length : int
            AnimateDiff temporal attention window size (default 16).
        frame_rate : int
            Output video frame rate for VHS_VideoCombine.
        """
        workflow = copy.deepcopy(
            self.load_preset(preset_name, selectors=_SPARSECTRL_SELECTORS)
        )
        bindings: dict[str, dict[str, Any]] = {}

        if seed < 0:
            seed = int(time.time_ns() % (2**31))

        normal_dir = str(Path(normal_sequence_dir).resolve())
        depth_dir = str(Path(depth_sequence_dir).resolve())
        rgb_dir = str(Path(rgb_sequence_dir).resolve())
        selector_map = {s.role: s for s in _SPARSECTRL_SELECTORS}

        # --- Core injections (all scalar values or path strings) ---
        injections: dict[str, Any] = {
            # Model
            "checkpoint": model_checkpoint,
            # Text
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt,
            # AnimateDiff
            "animatediff_model": animatediff_model_name,
            "animatediff_beta": animatediff_beta_schedule,
            # Context options
            "context_length": int(context_length),
            "context_overlap": int(context_overlap),
            # VHS directory paths (SEQUENCE, not single image!)
            "normal_sequence_dir": normal_dir,
            "depth_sequence_dir": depth_dir,
            "rgb_sequence_dir": rgb_dir,
            # ControlNet models
            "normal_controlnet": normal_controlnet_name,
            "depth_controlnet": depth_controlnet_name,
            # ControlNet strengths
            "normal_apply": float(normal_weight),
            "depth_apply": float(depth_weight),
            # SparseCtrl
            "sparsectrl_model": sparsectrl_model_name,
            "sparsectrl_strength": float(sparsectrl_strength),
            "sparsectrl_apply": float(sparsectrl_strength),
            "sparsectrl_end_percent": float(sparsectrl_end_percent),
            # Latent batch (frame_count → batch_size synchronization)
            "latent_batch_size": int(frame_count),
            "latent_width": int(width),
            "latent_height": int(height),
            # KSampler
            "ksampler_seed": int(seed),
            "ksampler_steps": int(steps),
            "ksampler_cfg": float(cfg_scale),
            "ksampler_denoise": float(denoising_strength),
            # Video output
            "video_frame_rate": int(frame_rate),
            "video_filename": filename_prefix,
            # Frame save
            "save_frame_output": f"{filename_prefix}_frames",
        }

        for role, value in injections.items():
            if role in selector_map:
                self._set_input(
                    workflow,
                    selector=selector_map[role],
                    value=value,
                    bindings=bindings,
                )

        guides_locked = ["normal", "depth", "sparsectrl_rgb"]
        guides_requested = list(guides_locked)

        preset_path = self.resolve_preset_path(preset_name)
        lock_manifest = {
            "preset_name": preset_name,
            "preset_path": str(preset_path),
            "controlnet_guides": guides_locked,
            "guides_requested": guides_requested,
            "identity_reference_present": False,
            "identity_lock_requested": False,
            "identity_lock_active": False,
            "seed": int(seed),
            "node_count": len(workflow),
            "semantic_bindings": self._build_bindings_table(bindings),
            "temporal_config": {
                "frame_count": int(frame_count),
                "context_length": int(context_length),
                "context_overlap": int(context_overlap),
                "frame_rate": int(frame_rate),
                "animatediff_model": animatediff_model_name,
                "animatediff_beta_schedule": animatediff_beta_schedule,
                "sparsectrl_model": sparsectrl_model_name,
                "sparsectrl_strength": float(sparsectrl_strength),
                "sparsectrl_end_percent": float(sparsectrl_end_percent),
                "batch_size_synced": True,
            },
            "sequence_directories": {
                "normal": normal_dir,
                "depth": depth_dir,
                "rgb": rgb_dir,
            },
            "workflow_contract": {
                "format": "workflow_api_json",
                "injection_mode": "semantic_selector",
                "selector_fields": ["class_type", "_meta.title"],
                "sequence_aware": True,
                "vhs_directory_injection": True,
            },
        }

        return {
            "client_id": client_id or str(uuid.uuid4()),
            "prompt": workflow,
            "mathart_lock_manifest": lock_manifest,
        }


__all__ = [
    "ComfyUIPresetManager",
    "NodeSelector",
    "PresetBindingError",
    "_PRESET_SELECTORS",
    "_SPARSECTRL_SELECTORS",
    "_SPARSECTRL_PRESET_NAME",
]
