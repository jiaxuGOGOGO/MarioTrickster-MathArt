"""ComfyUI Headless Render Backend — Registry-Native Production Plugin.

SESSION-173 (P0-SESSION-173-OFFLINE-SEMANTIC-TRANSLATOR)
-----------------------------------------------------------------
Inherits SESSION-172 JIT upscale + Prompt Armor.  _armor_prompt() now
internally translates Chinese vibe tokens to English via the hardcoded
VIBE_TRANSLATION_MAP before wrapping with base prompt anchors.

SESSION-172 (P0-SESSION-172-LATENT-SPACE-RESCUE)
-----------------------------------------------------------------
Integrated JIT Resolution Hydration and Prompt Armor Injection into the
registry-native render backend.  Image uploads are now JIT-upscaled to
512x512 in-memory before upload.  Prompts are wrapped with English anchor
tags for CLIP semantic grounding.  EmptyLatentImage nodes are forced to
512x512 to align with ControlNet condition images.

SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH)
----------------------------------------------------------
This module implements the **@register_backend** plugin for the ComfyUI
end-to-end headless render lane.  It integrates:

1. ``ComfyWorkflowMutator`` — BFF dynamic payload injection via semantic
   ``_meta.title`` markers (NEVER hardcoded node IDs).
2. ``ComfyAPIClient`` — Ephemeral upload, WebSocket telemetry, timeout
   circuit breaker, and VRAM garbage collection.
3. ``ArtifactManifest`` — Strongly-typed ``COMFYUI_RENDER_REPORT`` output
   with full provenance metadata for downstream quality gates and GA
   fitness evaluation.

Architecture Discipline
-----------------------
- This backend is a **pure plugin** — it self-registers via ``@register_backend``
  at import time.  No trunk orchestrator code is modified.
- The CLI bus passes opaque config dicts; all parameter validation is owned
  by ``validate_config()`` inside this Adapter (Hexagonal Architecture).
- Graceful degradation: if ComfyUI is offline, the backend returns a valid
  ``ArtifactManifest`` with ``degraded=True`` metadata — no crash.

Red Lines (SESSION-151 Anti-Pattern Guards)
-------------------------------------------
- [ANTI-PATTERN] NEVER hardcode ComfyUI node IDs.
- [ANTI-PATTERN] NEVER reference local filesystem paths in workflow nodes.
- [ANTI-PATTERN] NEVER block the main thread without timeout.
- [CONTRACT] All outputs are repatriated to ``outputs/production/``.
- [CONTRACT] VRAM is freed after every render batch.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)
from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_types import BackendType

# SESSION-172/173: Import JIT upscale + Prompt Armor (with built-in vibe
# translation) from ai_render_stream_backend.  _armor_prompt() now
# internally calls _translate_vibe() — no additional import needed.
from mathart.backend.ai_render_stream_backend import (
    _jit_upscale_image,
    _armor_prompt,
    _BASE_NEGATIVE_PROMPT,
    _force_latent_canvas_512,
    AI_TARGET_RES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default Configuration
# ---------------------------------------------------------------------------

_DEFAULT_SERVER = "127.0.0.1:8188"
_DEFAULT_RENDER_TIMEOUT = 600.0
_DEFAULT_POLL_INTERVAL = 1.0
_DEFAULT_OUTPUT_PREFIX = "final_render"


# ---------------------------------------------------------------------------
# Registry-Native Backend Plugin
# ---------------------------------------------------------------------------

@register_backend(
    BackendType.COMFYUI_RENDER,
    display_name="ComfyUI Static Asset Initializer (Vibe → Still Asset)",
    version="2.0.0",
    artifact_families=(
        ArtifactFamily.COMFYUI_STATIC_ASSET.value,
    ),
    capabilities=(
        BackendCapability.COMFYUI_RENDER,
    ),
    input_requirements=("vibe",),
    session_origin="V6-PHASE-1",
)
class ComfyUIRenderBackend:
    """ComfyUI headless render backend with BFF payload mutation.

    This backend implements the complete end-to-end render pipeline:

    1. **validate_config()** — Parse and normalize all render parameters.
       The CLI bus passes opaque config; this method is the single authority.

    2. **execute()** — Run the full pipeline:
       a. Load workflow blueprint
       b. Upload proxy image via ``/upload/image``
       c. Mutate workflow with semantic markers
       d. Submit to ComfyUI via ``POST /prompt``
       e. Wait for completion (WebSocket → HTTP poll fallback)
       f. Download outputs to ``outputs/production/``
       g. Free VRAM via ``POST /free``
       h. Return strongly-typed ``ArtifactManifest``
    """

    @property
    def name(self) -> str:
        return BackendType.COMFYUI_RENDER.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    # ------------------------------------------------------------------
    # Config Validation (Backend-Owned, Not CLI)
    # ------------------------------------------------------------------

    def validate_config(
        self,
        config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize ComfyUI render configuration.

        Parameters
        ----------
        config : dict
            Raw config dict from the CLI bus or PDG context.

        Returns
        -------
        tuple[dict, list[str]]
            (validated_config, warnings)
        """
        warnings: list[str] = []
        validated = dict(config)

        # --- ComfyUI server ---
        comfyui = validated.get("comfyui", {})
        if not isinstance(comfyui, dict):
            comfyui = {}
            warnings.append("comfyui config must be a dict; using defaults")
        validated["comfyui"] = comfyui

        server = str(comfyui.get("server_address", comfyui.get("url", _DEFAULT_SERVER)))
        # Strip protocol prefix if present
        for prefix in ("http://", "https://", "ws://", "wss://"):
            if server.startswith(prefix):
                server = server[len(prefix):]
        comfyui["server_address"] = server.rstrip("/")

        render_timeout = float(comfyui.get("render_timeout", _DEFAULT_RENDER_TIMEOUT))
        if render_timeout < 10:
            warnings.append(f"render_timeout={render_timeout}s too low; clamping to 10s")
            render_timeout = 10.0
        comfyui["render_timeout"] = render_timeout

        poll_interval = float(comfyui.get("poll_interval", _DEFAULT_POLL_INTERVAL))
        comfyui["poll_interval"] = max(0.5, poll_interval)

        auto_free = bool(comfyui.get("auto_free_vram", True))
        comfyui["auto_free_vram"] = auto_free

        # --- Workflow blueprint ---
        blueprint_path = validated.get("workflow_blueprint", validated.get("blueprint_path", ""))
        if blueprint_path:
            bp = Path(blueprint_path)
            if not bp.exists():
                warnings.append(f"workflow_blueprint {bp} not found")
        validated["workflow_blueprint"] = str(blueprint_path)

        # --- Image path ---
        image_path = validated.get("image_path", "")
        if image_path:
            ip = Path(image_path)
            if not ip.exists():
                warnings.append(f"image_path {ip} not found")
        validated["image_path"] = str(image_path)

        # --- V6 Static Asset Vibe ---
        forbidden_temporal_keys = (
            "frames", "frame_count", "fps", "duration", "video", "sequence",
            "actions", "motion", "temporal", "animation", "output_videos",
        )
        for key in forbidden_temporal_keys:
            value = validated.get(key)
            if value not in (None, "", [], {}, False):
                warnings.append(f"V6 ComfyUI static contract ignores temporal key '{key}'")
                validated.pop(key, None)

        vibe = str(validated.get("vibe", validated.get("prompt", ""))).strip()
        if not vibe:
            warnings.append("No vibe provided; using neutral static asset vibe")
            vibe = "neutral game character material reference, clean single image"
        validated["vibe"] = vibe
        validated["prompt"] = vibe

        negative_prompt = str(validated.get("negative_prompt", ""))
        validated["negative_prompt"] = negative_prompt

        # --- Seed ---
        seed = int(validated.get("seed", -1))
        validated["seed"] = seed

        # --- Output ---
        output_dir = validated.get("output_dir", "")
        validated["output_dir"] = str(output_dir) if output_dir else ""

        output_prefix = str(validated.get("output_prefix", _DEFAULT_OUTPUT_PREFIX))
        validated["output_prefix"] = output_prefix

        return validated, warnings

    # ------------------------------------------------------------------
    # Execute — End-to-End Render Pipeline
    # ------------------------------------------------------------------

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Execute the complete ComfyUI headless render pipeline.

        Parameters
        ----------
        context : dict
            The validated config dict containing:
            - ``workflow_blueprint``: Path to workflow_api.json
            - ``image_path``: Local path to the proxy image
            - ``prompt``: Positive vibe description
            - ``negative_prompt``: Negative prompt (optional)
            - ``seed``: Random seed (-1 = auto)
            - ``comfyui``: Server config sub-dict
            - ``output_dir``: Override output directory (optional)
            - ``output_prefix``: Filename prefix for outputs

        Returns
        -------
        ArtifactManifest
            Strongly-typed ``COMFYUI_RENDER_REPORT`` manifest.
        """
        from mathart.backend.comfy_mutator import ComfyWorkflowMutator
        from mathart.backend.comfy_client import ComfyAPIClient, RenderTimeoutError

        t0 = time.monotonic()

        # --- Validate config ---
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[ComfyUIRenderBackend] config warning: %s", w)

        comfyui_cfg = validated["comfyui"]
        output_dir = Path(validated["output_dir"]) if validated["output_dir"] else None
        blueprint_path = validated["workflow_blueprint"]
        image_path = validated["image_path"]
        vibe = validated["vibe"]
        negative_prompt = validated["negative_prompt"]
        seed = validated["seed"]
        output_prefix = validated["output_prefix"]
        session_id = str(validated.get("session_id", "SESSION-151"))

        # --- Create API client ---
        client = ComfyAPIClient(
            server_address=comfyui_cfg["server_address"],
            output_root=str(output_dir) if output_dir else None,
            connect_timeout=10.0,
            render_timeout=comfyui_cfg["render_timeout"],
            poll_interval=comfyui_cfg["poll_interval"],
            auto_free_vram=comfyui_cfg["auto_free_vram"],
        )

        # --- Health check (graceful degradation) ---
        if not client.is_server_online():
            logger.warning(
                "[ComfyUIRenderBackend] ComfyUI server at %s is offline. "
                "Returning degraded manifest.",
                comfyui_cfg["server_address"],
            )
            return self._build_degraded_manifest(
                reason=f"ComfyUI server at {comfyui_cfg['server_address']} is offline",
                blueprint_name=Path(blueprint_path).name if blueprint_path else "none",
                server_address=comfyui_cfg["server_address"],
                elapsed=time.monotonic() - t0,
            )

        # --- Upload proxy image (SESSION-172: JIT 512 upscale) ---
        uploaded_filename = ""
        if image_path:
            try:
                img_bytes = _jit_upscale_image(image_path, is_mask=False)
                uploaded_filename = client.upload_image_bytes(
                    img_bytes,
                    f"jit512_{Path(image_path).name}",
                )
                logger.info(
                    "[SESSION-172] JIT Upscale: %s → %dx%d → %s",
                    Path(image_path).name, AI_TARGET_RES, AI_TARGET_RES,
                    uploaded_filename,
                )
            except Exception as e:
                logger.error(
                    "[ComfyUIRenderBackend] Image upload failed: %s", e
                )
                return self._build_error_manifest(
                    error=f"Image upload failed: {e}",
                    blueprint_name=Path(blueprint_path).name if blueprint_path else "none",
                    server_address=comfyui_cfg["server_address"],
                    elapsed=time.monotonic() - t0,
                )

        # --- Build mutated payload (SESSION-172: Prompt Armor + 512 Latent) ---
        # Prompt Armor: wrap user prompt with English anchor tags
        armored_prompt = _armor_prompt(vibe)
        armored_negative = _BASE_NEGATIVE_PROMPT
        if negative_prompt and negative_prompt.strip():
            armored_negative = f"{_BASE_NEGATIVE_PROMPT}, {negative_prompt}"

        mutator = ComfyWorkflowMutator(blueprint_path=blueprint_path if blueprint_path else None)

        try:
            payload = mutator.build_payload(
                image_filename=uploaded_filename,
                prompt=armored_prompt,
                negative_prompt=armored_negative,
                seed=seed,
                output_prefix=output_prefix,
            )
            # SESSION-172: Force EmptyLatentImage to 512x512
            if "prompt" in payload and isinstance(payload["prompt"], dict):
                _force_latent_canvas_512(payload["prompt"])
        except Exception as e:
            logger.error(
                "[ComfyUIRenderBackend] Payload mutation failed: %s", e
            )
            return self._build_error_manifest(
                error=f"Payload mutation failed: {e}",
                blueprint_name=Path(blueprint_path).name if blueprint_path else "none",
                server_address=comfyui_cfg["server_address"],
                elapsed=time.monotonic() - t0,
            )

        mutation_ledger = payload.get("mathart_mutation_ledger", {})
        mutation_count = mutation_ledger.get("mutations_applied", 0)

        # --- Execute render ---
        result = client.render(
            payload,
            image_path=None,  # Already uploaded above
            output_prefix=output_prefix,
            free_vram_after=comfyui_cfg["auto_free_vram"],
        )

        elapsed = time.monotonic() - t0
        bp_name = Path(blueprint_path).name if blueprint_path else "none"

        if not result.success:
            if result.degraded:
                return self._build_degraded_manifest(
                    reason=result.degraded_reason,
                    blueprint_name=bp_name,
                    server_address=comfyui_cfg["server_address"],
                    elapsed=elapsed,
                )
            return self._build_error_manifest(
                error=result.error_message,
                blueprint_name=bp_name,
                server_address=comfyui_cfg["server_address"],
                elapsed=elapsed,
            )

        # --- Build success manifest ---
        outputs: dict[str, Any] = {}
        for i, img_path in enumerate(result.output_images[:1]):
            outputs[f"static_asset_{i}"] = img_path
        # V6 forbids ComfyUI video outputs; ignore any legacy video results.
        if result.output_dir:
            outputs["output_dir"] = result.output_dir

        return ArtifactManifest(
            artifact_family=ArtifactFamily.COMFYUI_STATIC_ASSET.value,
            backend_type=BackendType.COMFYUI_RENDER,
            outputs=outputs,
            metadata={
                "prompt_id": result.prompt_id,
                "server_address": comfyui_cfg["server_address"],
                "render_elapsed_seconds": round(elapsed, 3),
                "images_downloaded": len(result.output_images),
                "vram_freed": result.vram_freed,
                "mutation_count": mutation_count,
                "blueprint_name": bp_name,
                "uploaded_filename": uploaded_filename,
                "seed": seed,
                "vibe": vibe[:200],
                "static_asset_only": True,
                "negative_prompt": negative_prompt[:200],
                "output_prefix": output_prefix,
                "session_id": session_id,
                "mutation_ledger": mutation_ledger,
            },
            quality_metrics={
                "render_success": True,
                "degraded": False,
                "static_assets_count": len(result.output_images[:1]),
                "videos_ignored": len(result.output_videos),
            },
        )

    # ------------------------------------------------------------------
    # Manifest Builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_degraded_manifest(
        *,
        reason: str,
        blueprint_name: str,
        server_address: str,
        elapsed: float,
    ) -> ArtifactManifest:
        """Build a degraded manifest when ComfyUI is offline."""
        return ArtifactManifest(
            artifact_family=ArtifactFamily.COMFYUI_STATIC_ASSET.value,
            backend_type=BackendType.COMFYUI_RENDER,
            outputs={},
            metadata={
                "prompt_id": "",
                "server_address": server_address,
                "render_elapsed_seconds": round(elapsed, 3),
                "images_downloaded": 0,
                "vram_freed": False,
                "mutation_count": 0,
                "blueprint_name": blueprint_name,
                "vibe": "",
                "static_asset_only": True,
                "degraded": True,
                "degraded_reason": reason,
            },
            quality_metrics={
                "render_success": False,
                "degraded": True,
            },
        )

    @staticmethod
    def _build_error_manifest(
        *,
        error: str,
        blueprint_name: str,
        server_address: str,
        elapsed: float,
    ) -> ArtifactManifest:
        """Build an error manifest when rendering fails."""
        return ArtifactManifest(
            artifact_family=ArtifactFamily.COMFYUI_STATIC_ASSET.value,
            backend_type=BackendType.COMFYUI_RENDER,
            outputs={},
            metadata={
                "prompt_id": "",
                "server_address": server_address,
                "render_elapsed_seconds": round(elapsed, 3),
                "images_downloaded": 0,
                "vram_freed": False,
                "mutation_count": 0,
                "blueprint_name": blueprint_name,
                "vibe": "",
                "static_asset_only": True,
                "error": error,
            },
            quality_metrics={
                "render_success": False,
                "degraded": False,
                "error": error,
            },
        )
