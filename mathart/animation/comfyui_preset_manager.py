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


@dataclass(frozen=True)
class NodeSelector:
    """Semantic node selector used for safe workflow injection."""

    role: str
    class_type: str
    title_contains: str
    input_key: str
    required: bool = True


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

    def load_preset(self, preset_name: str = _DEFAULT_PRESET_NAME) -> dict[str, Any]:
        path = self.resolve_preset_path(preset_name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload:
            raise PresetBindingError(f"Preset {preset_name!r} is not a non-empty workflow dict")
        self.validate_preset_structure(payload)
        return payload

    def validate_preset_structure(self, workflow: dict[str, Any]) -> None:
        missing: list[str] = []
        for selector in _PRESET_SELECTORS:
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


__all__ = [
    "ComfyUIPresetManager",
    "NodeSelector",
    "PresetBindingError",
]
