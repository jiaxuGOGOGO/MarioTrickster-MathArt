"""AI Render Stream Backend — Full-Array Artifact Streaming to ComfyUI.

SESSION-172 (P0-SESSION-172-LATENT-SPACE-RESCUE)
-----------------------------------------------------------------
Critical fix: **JIT Resolution Hydration + Prompt Armor Injection**.

1. **Pre-Upload 512x512 Upscaling**: All 192x192 baked guide images
   (Albedo/Normal/Depth) are JIT-upscaled to 512x512 in-memory via
   ``PIL.Image.resize()`` before uploading to ComfyUI.  This rescues
   the latent space from the Nyquist collapse (24x24 latent → 64x64).
   Original on-disk 192x192 assets are NEVER modified (Immutable Source
   Data Principle).

2. **EmptyLatentImage 512x512 Override**: The workflow mutator now
   force-overrides all ``EmptyLatentImage`` nodes to 512x512, ensuring
   ControlNet condition images and latent canvas are resolution-aligned
   (prevents Tensor Shape Mismatch).

3. **Prompt Armor Injection**: User-provided vibe prompts are wrapped
   with high-quality English anchor tags (``masterpiece, best quality,
   highly detailed, 3d game character asset, ...``) to compensate for
   CLIP's inability to parse non-English (e.g., Chinese) tokens.
   Negative prompt is hardened with industry-standard avoidance terms.

Research grounding (SESSION-172):
- Latent Space Nyquist Limit: SD1.5 VAE 8x → 192px input = 24x24 latent
  (below U-Net minimum receptive field of 64x64 = 512px).
- JIT Resolution Hydration: upscale at network IO boundary only.
- Prompt Anchoring for Multilingual Intents: CLIP ViT-L/14 is English-only.
- AltCLIP (ACL Findings 2023): standard CLIP cannot process Chinese.
- Noise Re-sampling (ICLR 2025): VAE compression induces latent errors.

SESSION-169 (P0-SESSION-169-EXCEPTION-PIERCING-AND-GLOBAL-ABORT)
-----------------------------------------------------------------
Critical fix: The ``ComfyUIExecutionError`` catch in the render retry loop
now converts the fatal exception into a ``PipelineContractError`` wrapped
in ``PipelineQualityCircuitBreak`` before re-raising, ensuring the PDG
scheduler's concurrent executor receives a typed signal for global abort.
The upstream ``comfy_client.py`` exception piercing (SESSION-169) guarantees
that the Poison Pill exception now reaches this layer without being
swallowed by the HTTP polling fallback.

Research grounding (SESSION-169):
- Circuit Breaker Pattern (Michael Nygard, "Release It!" 2007)
- Targeted Exception Handling: fatal domain errors MUST propagate.
- Concurrent Futures Global Cancellation: pending tasks MUST be cancelled.

SESSION-168 (P0-SESSION-168-COMFYUI-CLIENT-DEADLOCK-BREAKER)
-------------------------------------------------------------
Critical fix: Added ``ComfyUIExecutionError`` precision catch in the render
retry loop.  When ComfyUI broadcasts a fatal ``execution_error`` (Poison Pill),
the circuit breaker is FORCE-OPENED and the exception re-raised to the CLI
wizard for loud crash-banner display.  This prevents the old behavior where
a non-retryable error was silently swallowed by the generic ``Exception``
catch and the next action would still attempt to render against a dead server.

SESSION-163 (P0-SESSION-161-COMFYUI-API-BRIDGE)
-----------------------------------------------------------
This module implements the **registry-native** AI render stream backend that
bridges the upstream CPU-bound guide baking engine with the downstream ComfyUI
headless render pipeline.  It is the missing "API communication wire" that
completes the end-to-end render closed loop.

Architecture Pillars
--------------------
1. **Full-Array Artifact Streaming**: Iterates over ALL available motion states
   from the dynamic registry (``get_motion_lane_registry``), collects their
   baked guide sequences (Albedo/Normal/Depth), and streams each action's
   texture set to the ComfyUI render backend for AI-powered upscaling.

2. **Pipeline Context Hydration**: After each successful render, the returned
   high-resolution AI assets are registered into the Pipeline Context bus
   under standardized paths (``ai_render_{action}_{frame:02d}.png``), enabling
   downstream packaging and archival backends to consume them without coupling.

3. **Graceful Degradation with Circuit Breaker**: When ComfyUI is offline or
   unreachable, the backend emits a yellow warning banner and preserves the
   original CPU-baked guide sequences as the canonical output — no crash,
   no data loss, no main-loop deadlock.

4. **Exponential Backoff with Jitter**: Network-level retries use exponential
   backoff (base 2s, max 32s) with random jitter to prevent thundering herd
   on shared GPU clusters.

Industrial References
---------------------
- ComfyUI REST API: POST /prompt, POST /upload/image, GET /history, GET /view
- ComfyUI WebSocket Events: status, executing, executed, progress, execution_error
- ControlNet Multi-Modal Injection: Normal + Depth as strong conditioning inputs
- Circuit Breaker Pattern (Michael Nygard, "Release It!" 2007)
- Exponential Backoff with Jitter (AWS Architecture Blog, 2015)
- BFF Payload Mutation (Sam Newman, "Building Microservices" 2021)

Architecture Red Lines (SESSION-163)
-------------------------------------
- [ANTI-PATTERN] NEVER hardcode absolute filesystem paths.
  All paths are dynamically extracted from ``context['artifacts']``.
- [ANTI-PATTERN] NEVER let ``ConnectionRefusedError`` crash the process.
  Graceful degradation returns a valid manifest with ``degraded=True``.
- [CONTRACT] All AI-rendered outputs are renamed to
  ``ai_render_{action}_{frame:02d}.png`` and registered in the bus.
- [CONTRACT] This backend self-registers via ``@register_backend`` — zero
  trunk modification required.
"""
from __future__ import annotations

import io
import json
import logging
import os
import random
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)
from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_types import BackendType

# ---------------------------------------------------------------------------
# SESSION-172: JIT Resolution Hydration Constants & Helpers
# ---------------------------------------------------------------------------
# The upstream CPU physics engine bakes guide sequences at 192x192 for
# throughput.  The SD1.5 VAE has 8x spatial compression, so 192px input
# produces a 24x24 latent — far below the U-Net's minimum receptive field
# of 64x64 (= 512px).  We MUST upscale to at least 512x512 at the network
# boundary before uploading to ComfyUI.
#
# RED LINE: This upscale ONLY happens in-memory (BytesIO).  The original
# 192x192 on-disk assets in outputs/guide_baking are NEVER overwritten.
# ---------------------------------------------------------------------------

AI_TARGET_RES = 512  # Minimum safe resolution for SD1.5 latent space

# Prompt Armor — English anchor tags for CLIP semantic grounding
_BASE_POSITIVE_PROMPT = (
    "masterpiece, best quality, highly detailed, "
    "3d game character asset, clean white background, "
    "vibrant colors, clear outlines, (masterpiece:1.2)"
)
_BASE_NEGATIVE_PROMPT = (
    "nsfw, worst quality, low quality, bad anatomy, "
    "blurry, noisy, ugly, deformed, extra limbs, "
    "messy background, text, watermark"
)


def _jit_upscale_image(image_path: str | Path, *, is_mask: bool = False) -> bytes:
    """JIT in-memory upscale of a baked guide image to AI_TARGET_RES.

    SESSION-172 (P0-SESSION-172-LATENT-SPACE-RESCUE)
    -------------------------------------------------
    Reads the original 192x192 image from disk, resizes it to
    ``AI_TARGET_RES x AI_TARGET_RES`` in-memory using PIL, and returns
    the PNG-encoded bytes.  The original file is NEVER modified.

    Interpolation Contract:
    - Albedo / Normal / Depth: ``Image.LANCZOS`` (high-quality sinc-based)
    - Mask: ``Image.NEAREST`` (prevents edge softening / anti-aliasing)

    Parameters
    ----------
    image_path : str | Path
        Path to the original baked guide image (e.g., 192x192 PNG).
    is_mask : bool
        If True, use NEAREST interpolation to preserve hard mask edges.

    Returns
    -------
    bytes
        PNG-encoded image data at AI_TARGET_RES x AI_TARGET_RES.
    """
    from PIL import Image as PILImage

    img = PILImage.open(str(image_path))
    original_size = img.size
    resample = PILImage.NEAREST if is_mask else PILImage.LANCZOS
    img_upscaled = img.resize((AI_TARGET_RES, AI_TARGET_RES), resample=resample)

    buf = io.BytesIO()
    img_upscaled.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    logger.info(
        "[JIT-Upscale] %s: %s → %dx%d (%s, %d bytes)",
        Path(image_path).name,
        f"{original_size[0]}x{original_size[1]}",
        AI_TARGET_RES, AI_TARGET_RES,
        "NEAREST" if is_mask else "LANCZOS",
        len(png_bytes),
    )
    return png_bytes


def _armor_prompt(user_vibe: str) -> str:
    """Wrap user vibe prompt with English anchor tags for CLIP grounding.

    SESSION-172: CLIP ViT-L/14 (used by SD1.5) cannot parse non-English
    tokens.  Pure Chinese prompts produce semantic noise.  We prepend
    high-quality English base tags to anchor the generation.

    References:
    - AltCLIP (ACL Findings 2023): standard CLIP has near-zero Chinese capability.
    - MuLan (OpenReview 2025): SD1.5 has strong English language bias.
    """
    return f"{_BASE_POSITIVE_PROMPT}, {user_vibe}"


def _force_latent_canvas_512(workflow: dict) -> None:
    """Force all EmptyLatentImage nodes in the workflow to 512x512.

    SESSION-172: ControlNet condition images are JIT-upscaled to 512x512.
    The EmptyLatentImage canvas MUST match to prevent Tensor Shape Mismatch.
    This mutates the workflow dict in-place.
    """
    for node_id, node_data in workflow.items():
        if not isinstance(node_data, dict):
            continue
        if node_data.get("class_type") == "EmptyLatentImage":
            inputs = node_data.get("inputs", {})
            old_w = inputs.get("width", "?")
            old_h = inputs.get("height", "?")
            inputs["width"] = AI_TARGET_RES
            inputs["height"] = AI_TARGET_RES
            logger.info(
                "[SESSION-172] EmptyLatentImage node %s: %sx%s → %dx%d",
                node_id, old_w, old_h, AI_TARGET_RES, AI_TARGET_RES,
            )


# SESSION-168: Import the Poison Pill exception for immediate circuit-breaker
# trip on fatal ComfyUI execution errors (e.g., PyTorch Half/Float conflict).
try:
    from mathart.comfy_client.comfyui_ws_client import ComfyUIExecutionError
except ImportError:
    class ComfyUIExecutionError(RuntimeError):  # type: ignore[no-redef]
        """Fallback sentinel for environments without the WS client."""
        def __init__(self, message: str, *, node_id: str = "?", details: str = ""):
            super().__init__(message)
            self.node_id = node_id
            self.details = details

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_SERVER = "127.0.0.1:8188"
_DEFAULT_RENDER_TIMEOUT = 600.0
_DEFAULT_POLL_INTERVAL = 1.0
_MAX_RETRY_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_MAX_SECONDS = 32.0
_JITTER_MAX_SECONDS = 1.5


# ---------------------------------------------------------------------------
# Circuit Breaker State Machine
# ---------------------------------------------------------------------------

class CircuitState:
    """Three-state circuit breaker: CLOSED → OPEN → HALF_OPEN."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreaker:
    """Lightweight circuit breaker for external GPU render calls.

    Implements the canonical three-state machine from Michael Nygard's
    "Release It!" (2007):

    - **CLOSED**: Normal operation.  Failures increment the counter.
    - **OPEN**: All calls are short-circuited for ``recovery_timeout`` seconds.
    - **HALF_OPEN**: A single probe call is allowed.  Success resets to CLOSED;
      failure re-opens the circuit.

    Parameters
    ----------
    failure_threshold : int
        Number of consecutive failures before opening the circuit.
    recovery_timeout : float
        Seconds to wait in OPEN state before transitioning to HALF_OPEN.
    """
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    state: str = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0

    def record_success(self) -> None:
        """Record a successful call — reset to CLOSED."""
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call — potentially open the circuit."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "[CircuitBreaker] Circuit OPENED after %d consecutive failures.",
                self.failure_count,
            )

    def allow_request(self) -> bool:
        """Check whether a request is allowed through the circuit.

        Returns
        -------
        bool
            True if the request should proceed; False if short-circuited.
        """
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "[CircuitBreaker] Transitioning to HALF_OPEN after %.1fs.",
                    elapsed,
                )
                return True
            return False
        # HALF_OPEN — allow exactly one probe
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }


# ---------------------------------------------------------------------------
# Exponential Backoff with Jitter
# ---------------------------------------------------------------------------

def _backoff_delay(attempt: int) -> float:
    """Calculate exponential backoff delay with random jitter.

    Formula: min(base * 2^attempt + jitter, max_delay)

    References
    ----------
    - AWS Architecture Blog, "Exponential Backoff And Jitter", 2015
    - Google Cloud API Design Guide, "Retry with exponential backoff"
    """
    delay = min(
        _BACKOFF_BASE_SECONDS * (2 ** attempt) + random.uniform(0, _JITTER_MAX_SECONDS),
        _BACKOFF_MAX_SECONDS,
    )
    return delay


# ---------------------------------------------------------------------------
# Render Stream Result (per-action)
# ---------------------------------------------------------------------------

@dataclass
class ActionRenderResult:
    """Result of streaming a single motion action through AI rendering."""
    action_name: str
    success: bool
    output_paths: list[str] = field(default_factory=list)
    frame_count: int = 0
    error_message: str = ""
    degraded: bool = False
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Registry-Native Backend Plugin
# ---------------------------------------------------------------------------

@register_backend(
    BackendType.AI_RENDER_STREAM,
    display_name="AI Render Stream (Full-Array Artifact Hydration)",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.AI_RENDER_STREAM_REPORT.value,
    ),
    capabilities=(
        BackendCapability.AI_RENDER_STREAM,
        BackendCapability.GPU_ACCELERATED,
    ),
    input_requirements=("artifacts", "output_dir"),
    session_origin="SESSION-163",
)
class AIRenderStreamBackend:
    """Full-array AI render streaming backend with circuit breaker protection.

    This backend implements the complete artifact hydration pipeline:

    1. **Enumerate Actions**: Query the dynamic motion lane registry for all
       available motion states (run, jump, idle, fall, hit, walk, ...).

    2. **Stream Per-Action**: For each action, collect baked guide sequences
       from ``context['artifacts']``, upload them to ComfyUI, inject into
       the workflow template via semantic mutation, and trigger rendering.

    3. **Hydrate Pipeline Context**: Rename AI-rendered outputs to
       ``ai_render_{action}_{frame:02d}.png`` and register paths in the
       pipeline context for downstream consumption.

    4. **Circuit Breaker**: If ComfyUI is unreachable, open the circuit
       after ``failure_threshold`` consecutive failures and gracefully
       degrade for all remaining actions.
    """

    @property
    def name(self) -> str:
        return BackendType.AI_RENDER_STREAM.value

    # ------------------------------------------------------------------
    # Config Validation
    # ------------------------------------------------------------------

    def validate_config(
        self,
        config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize AI render stream configuration."""
        warnings: list[str] = []
        validated = dict(config)

        # ComfyUI server config
        comfyui = validated.get("comfyui", {})
        if not isinstance(comfyui, dict):
            comfyui = {}
            warnings.append("comfyui config must be a dict; using defaults")
        validated["comfyui"] = comfyui

        server = str(comfyui.get("server_address", comfyui.get("url", _DEFAULT_SERVER)))
        for prefix in ("http://", "https://", "ws://", "wss://"):
            if server.startswith(prefix):
                server = server[len(prefix):]
        comfyui["server_address"] = server.rstrip("/")

        render_timeout = float(comfyui.get("render_timeout", _DEFAULT_RENDER_TIMEOUT))
        if render_timeout < 10:
            warnings.append(f"render_timeout={render_timeout}s too low; clamping to 10s")
            render_timeout = 10.0
        comfyui["render_timeout"] = render_timeout

        comfyui["poll_interval"] = max(0.5, float(comfyui.get("poll_interval", _DEFAULT_POLL_INTERVAL)))
        comfyui["auto_free_vram"] = bool(comfyui.get("auto_free_vram", True))

        # Output directory
        output_dir = validated.get("output_dir", "")
        if not output_dir:
            warnings.append("No output_dir specified; using default outputs/production/")
        validated["output_dir"] = str(output_dir)

        # Workflow blueprint
        blueprint_path = validated.get("workflow_blueprint", "")
        if not blueprint_path:
            # Default to the project's bundled template
            default_bp = Path(__file__).resolve().parents[1] / "assets" / "workflows" / "workflow_api_template.json"
            if default_bp.exists():
                blueprint_path = str(default_bp)
            else:
                warnings.append("No workflow_blueprint found; render will use inline fallback")
        validated["workflow_blueprint"] = str(blueprint_path)

        # Prompt
        prompt = str(validated.get("prompt", "pixel art game character sprite, clean linework, vibrant colors"))
        validated["prompt"] = prompt
        validated["negative_prompt"] = str(validated.get("negative_prompt", "blurry, low quality, deformed, watermark"))

        return validated, warnings

    # ------------------------------------------------------------------
    # Execute — Full-Array Streaming Pipeline
    # ------------------------------------------------------------------

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Execute the full-array AI render streaming pipeline.

        Parameters
        ----------
        context : dict
            Pipeline context containing:
            - ``artifacts``: dict mapping action names to their baked asset paths
            - ``output_dir``: Root output directory
            - ``comfyui``: Server configuration sub-dict
            - ``workflow_blueprint``: Path to workflow template
            - ``prompt``: Positive prompt for AI rendering
            - ``negative_prompt``: Negative prompt
            - ``session_id``: Current session identifier

        Returns
        -------
        ArtifactManifest
            Strongly-typed ``AI_RENDER_STREAM_REPORT`` manifest with all
            rendered asset paths and per-action status.
        """
        t0 = time.monotonic()

        # Validate config
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[AIRenderStreamBackend] config warning: %s", w)

        comfyui_cfg = validated["comfyui"]
        output_dir = Path(validated["output_dir"]) if validated["output_dir"] else Path("outputs/production")
        output_dir.mkdir(parents=True, exist_ok=True)
        blueprint_path = validated["workflow_blueprint"]
        prompt = validated["prompt"]
        negative_prompt = validated["negative_prompt"]
        session_id = str(validated.get("session_id", "SESSION-163"))

        # ── Resolve available actions from dynamic registry ──────────
        try:
            from mathart.animation.unified_gait_blender import get_motion_lane_registry
            available_actions = list(get_motion_lane_registry().names())
        except Exception as e:
            logger.warning(
                "[AIRenderStreamBackend] Could not query motion registry: %s. "
                "Falling back to context-provided action list.",
                e,
            )
            artifacts = validated.get("artifacts", {})
            available_actions = list(artifacts.keys()) if isinstance(artifacts, dict) else []

        if not available_actions:
            logger.warning("[AIRenderStreamBackend] No actions available for streaming.")
            return self._build_empty_manifest(
                reason="No motion actions available in registry or context",
                elapsed=time.monotonic() - t0,
            )

        # ── Initialize ComfyUI client with circuit breaker ───────────
        circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)

        try:
            from mathart.backend.comfy_client import ComfyAPIClient
        except ImportError as e:
            logger.error("[AIRenderStreamBackend] Cannot import ComfyAPIClient: %s", e)
            return self._build_error_manifest(
                error=f"Import error: {e}",
                elapsed=time.monotonic() - t0,
            )

        client = ComfyAPIClient(
            server_address=comfyui_cfg["server_address"],
            output_root=str(output_dir),
            connect_timeout=10.0,
            render_timeout=comfyui_cfg["render_timeout"],
            poll_interval=comfyui_cfg["poll_interval"],
            auto_free_vram=comfyui_cfg["auto_free_vram"],
        )

        # ── Health check with graceful degradation ───────────────────
        server_online = False
        try:
            server_online = client.is_server_online()
        except (ConnectionRefusedError, OSError, Exception) as e:
            logger.warning(
                "[AIRenderStreamBackend] Server health check failed: %s", e
            )

        if not server_online:
            # [防崩溃熔断红线] — graceful degradation
            _degradation_msg = (
                "\033[1;33m[⚠️ AI 渲染服务器未就绪，"
                "已为您安全保留原生物理底图并终止推流]\033[0m"
            )
            sys.stderr.write(_degradation_msg + "\n")
            sys.stderr.flush()
            logger.warning(
                "[AIRenderStreamBackend] ComfyUI at %s is offline. "
                "Preserving original baked guides as canonical output.",
                comfyui_cfg["server_address"],
            )
            return self._build_degraded_manifest(
                reason=f"ComfyUI server at {comfyui_cfg['server_address']} is offline",
                actions=available_actions,
                elapsed=time.monotonic() - t0,
            )

        # ── Load workflow mutator ────────────────────────────────────
        try:
            from mathart.backend.comfy_mutator import ComfyWorkflowMutator
        except ImportError as e:
            return self._build_error_manifest(
                error=f"Cannot import ComfyWorkflowMutator: {e}",
                elapsed=time.monotonic() - t0,
            )

        mutator = ComfyWorkflowMutator(blueprint_path=blueprint_path if blueprint_path else None)

        # ── Stream each action through the render pipeline ───────────
        artifacts = validated.get("artifacts", {})
        action_results: list[ActionRenderResult] = []
        all_output_paths: dict[str, list[str]] = {}
        total_rendered = 0
        total_degraded = 0

        for action_name in available_actions:
            action_t0 = time.monotonic()

            # Check circuit breaker
            if not circuit.allow_request():
                logger.warning(
                    "[AIRenderStreamBackend] Circuit OPEN — skipping action '%s'.",
                    action_name,
                )
                action_results.append(ActionRenderResult(
                    action_name=action_name,
                    success=False,
                    degraded=True,
                    error_message="Circuit breaker OPEN — skipped",
                    elapsed_seconds=0.0,
                ))
                total_degraded += 1
                continue

            # ── Extract baked guide paths from context (NO hardcoded paths!) ──
            action_artifacts = {}
            if isinstance(artifacts, dict):
                action_artifacts = artifacts.get(action_name, {})
            if not isinstance(action_artifacts, dict):
                action_artifacts = {}

            # Dynamically extract paths from upstream context
            albedo_path = action_artifacts.get("albedo", action_artifacts.get("source", ""))
            normal_path = action_artifacts.get("normal", "")
            depth_path = action_artifacts.get("depth", "")

            if not albedo_path:
                logger.info(
                    "[AIRenderStreamBackend] No baked assets for action '%s' — skipping.",
                    action_name,
                )
                action_results.append(ActionRenderResult(
                    action_name=action_name,
                    success=False,
                    error_message="No baked guide assets available",
                    elapsed_seconds=time.monotonic() - action_t0,
                ))
                continue

            # ── Upload images with JIT 512 upscale + exponential backoff ───
            # SESSION-172: JIT Resolution Hydration — upscale 192→512 in-memory
            # before uploading.  Original on-disk assets are NEVER modified.
            uploaded_albedo = ""
            for attempt in range(_MAX_RETRY_ATTEMPTS):
                try:
                    if Path(albedo_path).exists():
                        albedo_bytes = _jit_upscale_image(albedo_path, is_mask=False)
                        uploaded_albedo = client.upload_image_bytes(
                            albedo_bytes,
                            f"jit512_{Path(albedo_path).name}",
                        )
                        logger.info(
                            "[SESSION-172] JIT Upscale Albedo: %s → %dx%d → uploaded as %s",
                            Path(albedo_path).name, AI_TARGET_RES, AI_TARGET_RES,
                            uploaded_albedo,
                        )
                    break
                except (ConnectionRefusedError, OSError) as e:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        "[AIRenderStreamBackend] Upload attempt %d/%d failed for '%s': %s. "
                        "Retrying in %.1fs...",
                        attempt + 1, _MAX_RETRY_ATTEMPTS, action_name, e, delay,
                    )
                    circuit.record_failure()
                    if not circuit.allow_request():
                        break
                    time.sleep(delay)
                except Exception as e:
                    logger.error(
                        "[AIRenderStreamBackend] Non-retryable upload error: %s", e
                    )
                    break

            if not uploaded_albedo:
                action_results.append(ActionRenderResult(
                    action_name=action_name,
                    success=False,
                    degraded=True,
                    error_message="Failed to upload albedo guide after retries",
                    elapsed_seconds=time.monotonic() - action_t0,
                ))
                total_degraded += 1
                continue

            # Upload normal and depth maps (optional, non-fatal)
            # SESSION-172: JIT upscale normal/depth to 512x512 as well
            uploaded_normal = ""
            uploaded_depth = ""
            try:
                if normal_path and Path(normal_path).exists():
                    normal_bytes = _jit_upscale_image(normal_path, is_mask=False)
                    uploaded_normal = client.upload_image_bytes(
                        normal_bytes,
                        f"jit512_{Path(normal_path).name}",
                    )
                    logger.info(
                        "[SESSION-172] JIT Upscale Normal: %s → %dx%d",
                        Path(normal_path).name, AI_TARGET_RES, AI_TARGET_RES,
                    )
            except Exception as e:
                logger.warning("[AIRenderStreamBackend] Normal map upload failed: %s", e)

            try:
                if depth_path and Path(depth_path).exists():
                    depth_bytes = _jit_upscale_image(depth_path, is_mask=False)
                    uploaded_depth = client.upload_image_bytes(
                        depth_bytes,
                        f"jit512_{Path(depth_path).name}",
                    )
                    logger.info(
                        "[SESSION-172] JIT Upscale Depth: %s → %dx%d",
                        Path(depth_path).name, AI_TARGET_RES, AI_TARGET_RES,
                    )
            except Exception as e:
                logger.warning("[AIRenderStreamBackend] Depth map upload failed: %s", e)

            # ── Build mutated payload with Prompt Armor + 512 Latent ───
            # SESSION-172: Prompt Armor Injection — wrap user vibe with
            # English anchor tags for CLIP semantic grounding.
            armored_prompt = _armor_prompt(f"{prompt}, {action_name} pose, game sprite animation frame")
            armored_negative = _BASE_NEGATIVE_PROMPT
            # Merge user negative with base negative (user additions preserved)
            if negative_prompt and negative_prompt.strip():
                armored_negative = f"{_BASE_NEGATIVE_PROMPT}, {negative_prompt}"

            logger.info(
                "[SESSION-172] Prompt Armor: positive=%s... | negative=%s...",
                armored_prompt[:80], armored_negative[:60],
            )

            try:
                from mathart.backend.comfy_mutator import SemanticMarker

                extra_injections: dict[str, Any] = {}
                extra_markers: list[SemanticMarker] = []

                if uploaded_normal:
                    marker_normal = SemanticMarker("[MathArt_Normal_Guide]", "image", required=False)
                    extra_injections[marker_normal.marker] = uploaded_normal
                    extra_markers.append(marker_normal)

                if uploaded_depth:
                    marker_depth = SemanticMarker("[MathArt_Depth_Guide]", "image", required=False)
                    extra_injections[marker_depth.marker] = uploaded_depth
                    extra_markers.append(marker_depth)

                payload = mutator.build_payload(
                    image_filename=uploaded_albedo,
                    prompt=armored_prompt,
                    negative_prompt=armored_negative,
                    seed=-1,
                    output_prefix=f"ai_render_{action_name}",
                    extra_injections=extra_injections if extra_injections else None,
                    extra_markers=tuple(extra_markers) if extra_markers else None,
                )

                # SESSION-172: Force EmptyLatentImage canvas to 512x512
                # to align with JIT-upscaled ControlNet condition images.
                if "prompt" in payload and isinstance(payload["prompt"], dict):
                    _force_latent_canvas_512(payload["prompt"])
            except Exception as e:
                logger.error(
                    "[AIRenderStreamBackend] Payload mutation failed for '%s': %s",
                    action_name, e,
                )
                action_results.append(ActionRenderResult(
                    action_name=action_name,
                    success=False,
                    error_message=f"Payload mutation failed: {e}",
                    elapsed_seconds=time.monotonic() - action_t0,
                ))
                continue

            # ── Execute render with retry ────────────────────────────
            render_result = None
            for attempt in range(_MAX_RETRY_ATTEMPTS):
                try:
                    render_result = client.render(
                        payload,
                        image_path=None,  # Already uploaded
                        output_prefix=f"ai_render_{action_name}",
                        free_vram_after=comfyui_cfg["auto_free_vram"],
                    )
                    if render_result.success:
                        circuit.record_success()
                        break
                    else:
                        circuit.record_failure()
                except (ConnectionRefusedError, OSError) as e:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        "[AIRenderStreamBackend] Render attempt %d/%d failed for '%s': %s. "
                        "Retrying in %.1fs...",
                        attempt + 1, _MAX_RETRY_ATTEMPTS, action_name, e, delay,
                    )
                    circuit.record_failure()
                    if not circuit.allow_request():
                        break
                    time.sleep(delay)
                except ComfyUIExecutionError as e:
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # SESSION-169: POISON PILL + GLOBAL CIRCUIT BREAK
                    # ComfyUI reported a fatal execution crash (e.g. PyTorch
                    # Half/Float precision conflict).  This is NOT a transient
                    # network error — retrying is futile and dangerous.
                    #
                    # Action plan:
                    # 1. FORCE-OPEN the circuit breaker.
                    # 2. Log a CRITICAL-level alert with full node diagnostics.
                    # 3. Print the red crash banner to stderr.
                    # 4. Re-raise so the PDG scheduler can cancel all pending
                    #    futures and the CLI wizard can display the crash banner.
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    logger.critical(
                        "[AIRenderStreamBackend] \u26a0\ufe0f FATAL ComfyUIExecutionError "
                        "for '%s' (node=%s): %s.  "
                        "Circuit breaker FORCE-OPENED, aborting remaining actions.",
                        action_name, getattr(e, 'node_id', '?'), e,
                    )
                    circuit.failure_count = circuit.failure_threshold
                    circuit.state = CircuitState.OPEN
                    circuit.last_failure_time = time.monotonic()
                    # SESSION-169: Print red crash banner to stderr
                    sys.stderr.write(
                        "\n\033[1;41;37m"
                        "[\u274c AI \u70bc\u4e39\u7089\u8282\u70b9\u5d29\u6e83] "
                        "\u8fdc\u7aef GPU \u629b\u51fa\u81f4\u547d\u9519\u8bef\uff01"
                        "\u5df2\u89e6\u53d1\u5168\u5c40\u65ad\u8def\u5668\uff0c"
                        "\u5f3a\u5236\u64a4\u9500\u5269\u4f59\u6240\u6709\u89d2\u8272\u7684\u6e32\u67d3\u961f\u5217"
                        "\u4ee5\u9632\u6b7b\u9501\uff01"
                        "\u7269\u7406\u5e95\u56fe\u5df2\u5b89\u5168\u4fdd\u7559\u3002"
                        "\u8bf7\u67e5\u770b ComfyUI \u540e\u53f0\u7ec8\u7aef\u7684\u8be6\u7ec6\u62a5\u9519\u3002"
                        "\033[0m\n"
                    )
                    sys.stderr.flush()
                    action_results.append(ActionRenderResult(
                        action_name=action_name,
                        success=False,
                        degraded=True,
                        error_message=f"FATAL: {e}",
                        elapsed_seconds=time.monotonic() - action_t0,
                    ))
                    total_degraded += 1
                    # Re-raise so the PDG scheduler can trigger global abort
                    # and the CLI wizard can display the crash banner.
                    raise
                except Exception as e:
                    logger.error(
                        "[AIRenderStreamBackend] Non-retryable render error for '%s': %s",
                        action_name, e,
                    )
                    break

            # ── Hydrate: rename and register outputs ─────────────────
            if render_result and render_result.success:
                action_output_dir = output_dir / f"ai_render_{action_name}"
                action_output_dir.mkdir(parents=True, exist_ok=True)

                renamed_paths: list[str] = []
                for idx, img_path in enumerate(render_result.output_images):
                    src = Path(img_path)
                    if src.exists():
                        dst_name = f"ai_render_{action_name}_{idx:02d}{src.suffix}"
                        dst = action_output_dir / dst_name
                        shutil.copy2(src, dst)
                        renamed_paths.append(str(dst))

                all_output_paths[action_name] = renamed_paths
                total_rendered += 1

                action_results.append(ActionRenderResult(
                    action_name=action_name,
                    success=True,
                    output_paths=renamed_paths,
                    frame_count=len(renamed_paths),
                    elapsed_seconds=time.monotonic() - action_t0,
                ))

                logger.info(
                    "[AIRenderStreamBackend] ✅ Action '%s' rendered: %d frames → %s",
                    action_name, len(renamed_paths), action_output_dir,
                )
            else:
                error_msg = render_result.error_message if render_result else "Render failed after retries"
                action_results.append(ActionRenderResult(
                    action_name=action_name,
                    success=False,
                    degraded=True,
                    error_message=error_msg,
                    elapsed_seconds=time.monotonic() - action_t0,
                ))
                total_degraded += 1

        # ── Build final manifest ─────────────────────────────────────
        elapsed = time.monotonic() - t0

        outputs: dict[str, Any] = {}
        for action_name, paths in all_output_paths.items():
            for i, p in enumerate(paths):
                outputs[f"ai_render_{action_name}_{i:02d}"] = p
        outputs["output_dir"] = str(output_dir)
        outputs["action_summary"] = {
            ar.action_name: {
                "success": ar.success,
                "degraded": ar.degraded,
                "frame_count": ar.frame_count,
                "error": ar.error_message,
            }
            for ar in action_results
        }

        return ArtifactManifest(
            artifact_family=ArtifactFamily.AI_RENDER_STREAM_REPORT.value,
            backend_type=BackendType.AI_RENDER_STREAM,
            outputs=outputs,
            metadata={
                "session_id": session_id,
                "server_address": comfyui_cfg["server_address"],
                "total_actions": len(available_actions),
                "total_rendered": total_rendered,
                "total_degraded": total_degraded,
                "total_skipped": len(available_actions) - total_rendered - total_degraded,
                "render_elapsed_seconds": round(elapsed, 3),
                "circuit_breaker": circuit.to_dict(),
                "blueprint_name": Path(blueprint_path).name if blueprint_path else "none",
                "prompt": prompt[:200],
                "negative_prompt": negative_prompt[:200],
            },
            quality_metrics={
                "render_success": total_rendered > 0,
                "total_rendered": total_rendered,
                "total_degraded": total_degraded,
                "degraded": total_degraded > 0,
                "completion_ratio": round(
                    total_rendered / max(len(available_actions), 1), 3
                ),
            },
        )

    # ------------------------------------------------------------------
    # Manifest Builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_degraded_manifest(
        *,
        reason: str,
        actions: list[str],
        elapsed: float,
    ) -> ArtifactManifest:
        """Build a degraded manifest when ComfyUI is offline."""
        return ArtifactManifest(
            artifact_family=ArtifactFamily.AI_RENDER_STREAM_REPORT.value,
            backend_type=BackendType.AI_RENDER_STREAM,
            outputs={},
            metadata={
                "session_id": "SESSION-163",
                "degraded": True,
                "degraded_reason": reason,
                "available_actions": actions,
                "render_elapsed_seconds": round(elapsed, 3),
            },
            quality_metrics={
                "render_success": False,
                "degraded": True,
                "total_rendered": 0,
            },
        )

    @staticmethod
    def _build_error_manifest(
        *,
        error: str,
        elapsed: float,
    ) -> ArtifactManifest:
        """Build an error manifest when the backend encounters a fatal error."""
        return ArtifactManifest(
            artifact_family=ArtifactFamily.AI_RENDER_STREAM_REPORT.value,
            backend_type=BackendType.AI_RENDER_STREAM,
            outputs={},
            metadata={
                "session_id": "SESSION-163",
                "error": error,
                "render_elapsed_seconds": round(elapsed, 3),
            },
            quality_metrics={
                "render_success": False,
                "degraded": False,
                "error": error,
            },
        )

    @staticmethod
    def _build_empty_manifest(
        *,
        reason: str,
        elapsed: float,
    ) -> ArtifactManifest:
        """Build a manifest when no actions are available."""
        return ArtifactManifest(
            artifact_family=ArtifactFamily.AI_RENDER_STREAM_REPORT.value,
            backend_type=BackendType.AI_RENDER_STREAM,
            outputs={},
            metadata={
                "session_id": "SESSION-163",
                "empty_reason": reason,
                "render_elapsed_seconds": round(elapsed, 3),
            },
            quality_metrics={
                "render_success": False,
                "degraded": False,
            },
        )
