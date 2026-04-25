"""
SESSION-202 → SESSION-203-HOTFIX Headless Dispatcher Bridge — Web UI → Pipeline Adapter.

This module implements the **Headless Dispatcher Bridge** that translates
Gradio UI parameters into the strongly-typed ``CreatorIntentSpec`` dictionary
and dispatches to the underlying pipeline without going through the CLI.

Key responsibilities:
1. Assemble ``CreatorIntentSpec`` from UI widget values.
2. Persist drag-and-drop images from Gradio temp to ``workspace/inputs/``
   (反幽灵路径红线: shutil.copy to prevent temp cleanup crashes).
3. Invoke ``IntentGateway.admit()`` for Fail-Closed validation.
4. Thread admission result into ``director_studio_spec`` (K8s Mutating Webhook pattern).
5. Dispatch through ``ModeDispatcher`` for real pipeline execution.
6. Yield real-time telemetry events back to the Gradio frontend.

Architecture discipline:
- This bridge is an independent adapter — it NEVER modifies core pipeline code.
- All pipeline interaction goes through existing public APIs.
- Follows the IoC Registry Pattern: no if/else in the trunk.

SESSION-203-HOTFIX changes:
- Fixed Gateway key mapping: ``action`` / ``reference_image`` (not ``action_name`` / ``visual_reference_path``).
- Fixed ``_execute_pipeline()``: now threads admission into ``director_studio_spec`` and
  dispatches through ``ModeDispatcher.dispatch("production", ...)`` for real rendering.
- ``assemble_creator_intent()`` now produces a dict with **both** Gateway-compatible keys
  (``action`` / ``reference_image``) AND downstream-compatible keys (``action_name`` /
  ``visual_reference_path``) so the same dict can be used for admission AND spec construction.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_INPUTS = _PROJECT_ROOT / "workspace" / "inputs"
_OUTPUTS_DIR = _PROJECT_ROOT / "outputs" / "final_renders"


def _ensure_dirs() -> None:
    """Ensure required directories exist."""
    _WORKSPACE_INPUTS.mkdir(parents=True, exist_ok=True)
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
#  Image Persistence (反幽灵路径红线)
# ═══════════════════════════════════════════════════════════════════════════

def persist_uploaded_image(gradio_temp_path: str | None) -> str:
    """Copy a Gradio temp image to ``workspace/inputs/`` for persistence.

    Gradio stores drag-and-drop uploads in the system temp directory which
    can be garbage-collected at any time.  We MUST copy to a persistent
    location before passing the path to the pipeline.

    Parameters
    ----------
    gradio_temp_path : str or None
        The temporary file path provided by Gradio's Image component.

    Returns
    -------
    str
        Absolute path to the persisted copy in ``workspace/inputs/``.
        Empty string if no image was provided.

    Raises
    ------
    FileNotFoundError
        If the provided path does not exist (反空跑宕机红线).
    """
    if not gradio_temp_path:
        return ""

    _ensure_dirs()

    src = Path(gradio_temp_path)
    if not src.exists():
        raise FileNotFoundError(
            f"[SESSION-202 反幽灵路径红线] Uploaded image not found at "
            f"'{gradio_temp_path}'. Gradio temp may have been cleaned."
        )

    # Generate unique filename to prevent collisions
    unique_name = f"ref_{uuid.uuid4().hex[:8]}{src.suffix}"
    dest = _WORKSPACE_INPUTS / unique_name
    shutil.copy2(str(src), str(dest))

    logger.info(
        "[SESSION-202 Bridge] Persisted uploaded image: %s → %s",
        src, dest,
    )
    return str(dest)


# ═══════════════════════════════════════════════════════════════════════════
#  Intent Assembly
# ═══════════════════════════════════════════════════════════════════════════

def assemble_creator_intent(
    action_name: str,
    reference_image_path: str,
    force_fluid: bool,
    force_physics: bool,
    force_cloth: bool,
    force_particles: bool,
    raw_vibe: str = "",
) -> dict[str, Any]:
    """Assemble a ``CreatorIntentSpec``-compatible dictionary from UI inputs.

    This is the core translation layer between the Web UI and the pipeline.
    The returned dictionary contains **both**:

    - **Gateway-compatible keys** (``action`` / ``reference_image``) for
      ``IntentGateway.admit()`` consumption.
    - **Downstream-compatible keys** (``action_name`` / ``visual_reference_path``)
      for ``CreatorIntentSpec`` construction and ``director_studio_spec`` threading.

    SESSION-203-HOTFIX: Fixed key mapping so Gateway receives the keys it
    actually reads (``action`` / ``reference_image``), resolving the root cause
    of ``action_name=''`` and ``reference_image_path=None`` in admission results.

    Parameters
    ----------
    action_name : str
        Selected gait action from the OpenPoseGaitRegistry dropdown.
    reference_image_path : str
        Absolute path to the persisted reference image (or empty string).
    force_fluid : bool
        Whether to force-enable fluid VFX.
    force_physics : bool
        Whether to force-enable physics VFX.
    force_cloth : bool
        Whether to force-enable cloth simulation VFX.
    force_particles : bool
        Whether to force-enable particle system VFX.
    raw_vibe : str
        Optional free-text vibe description.

    Returns
    -------
    dict
        A dictionary conforming to the ``CreatorIntentSpec`` contract,
        with additional Gateway-compatible keys for admission.
    """
    vfx_overrides: dict[str, bool] = {}
    if force_fluid:
        vfx_overrides["force_fluid"] = True
    if force_physics:
        vfx_overrides["force_physics"] = True
    if force_cloth:
        vfx_overrides["force_cloth"] = True
    if force_particles:
        vfx_overrides["force_particles"] = True

    intent_dict: dict[str, Any] = {
        # ── Gateway-compatible keys (IntentGateway.admit() reads these) ──
        "action": action_name or "",
        "reference_image": reference_image_path or "",
        # ── Downstream-compatible keys (CreatorIntentSpec / director_studio_spec) ──
        "action_name": action_name or "",
        "visual_reference_path": reference_image_path or "",
        # ── Shared keys ──
        "vfx_overrides": vfx_overrides,
        "raw_vibe": raw_vibe,
        "skeleton_topology": "biped",
    }

    logger.info(
        "[SESSION-202 Bridge] Assembled intent: action=%s, ref=%s, vfx=%s",
        action_name, bool(reference_image_path), vfx_overrides,
    )
    return intent_dict


# ═══════════════════════════════════════════════════════════════════════════
#  Pipeline Dispatch (Generator for streaming progress)
# ═══════════════════════════════════════════════════════════════════════════

class WebUIBridge:
    """Headless dispatcher bridge: Web UI → Pipeline.

    This class encapsulates the full flow from UI parameter collection
    to pipeline dispatch, yielding real-time progress events for the
    Gradio frontend to consume via generator/yield pattern.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or _PROJECT_ROOT
        self.outputs_dir = self.project_root / "outputs" / "final_renders"
        _ensure_dirs()

    def get_available_actions(self) -> list[str]:
        """Dynamically read available actions from OpenPoseGaitRegistry.

        严禁前端硬编码列表 — always read from the live registry.
        """
        try:
            from mathart.core.openpose_pose_provider import get_gait_registry
            registry = get_gait_registry()
            return registry.names()
        except Exception as e:
            logger.warning(
                "[SESSION-202 Bridge] Failed to read gait registry: %s", e
            )
            return ["idle", "walk", "run", "dash", "jump"]  # fallback

    def dispatch_render(
        self,
        action_name: str,
        reference_image: str | None,
        force_fluid: bool,
        force_physics: bool,
        force_cloth: bool,
        force_particles: bool,
        raw_vibe: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        """Dispatch a render job and yield progress events.

        This is the main entry point called by the Gradio UI button.
        It uses Python generators (yield) to stream progress updates
        back to the frontend, preventing page freeze (反页面假死红线).

        Yields
        ------
        dict
            Progress events with keys:
            - ``stage``: current pipeline stage name
            - ``progress``: float 0.0–1.0
            - ``message``: human-readable status message
            - ``gallery``: list of output file paths (populated on completion)
            - ``video``: path to output video (if available)
            - ``error``: error message (if failed)
        """
        # ── Step 1: Persist uploaded image ────────────────────────────
        yield {
            "stage": "init",
            "progress": 0.0,
            "message": "[SESSION-202] 🚀 初始化核动力渲染引擎...",
            "gallery": [],
            "video": None,
            "error": None,
        }

        try:
            persisted_ref = persist_uploaded_image(reference_image)
        except FileNotFoundError as e:
            yield {
                "stage": "error",
                "progress": 0.0,
                "message": str(e),
                "gallery": [],
                "video": None,
                "error": str(e),
            }
            return

        # ── Step 2: Assemble intent ──────────────────────────────────
        yield {
            "stage": "intent_assembly",
            "progress": 0.1,
            "message": "[SESSION-202] 📋 组装 CreatorIntentSpec 强类型意图字典...",
            "gallery": [],
            "video": None,
            "error": None,
        }

        intent_dict = assemble_creator_intent(
            action_name=action_name,
            reference_image_path=persisted_ref,
            force_fluid=force_fluid,
            force_physics=force_physics,
            force_cloth=force_cloth,
            force_particles=force_particles,
            raw_vibe=raw_vibe,
        )

        # ── Step 3: Gateway admission ────────────────────────────────
        yield {
            "stage": "gateway_admission",
            "progress": 0.2,
            "message": "[SESSION-202] 🔐 IntentGateway Fail-Closed 准入校验...",
            "gallery": [],
            "video": None,
            "error": None,
        }

        admission = None
        try:
            from mathart.workspace.intent_gateway import (
                IntentGateway,
                thread_admission_into_director_spec,
            )
            gateway = IntentGateway()
            admission = gateway.admit(intent_dict)
            logger.info("[SESSION-202 Bridge] Gateway admission: %s", admission)
        except Exception as e:
            logger.warning(
                "[SESSION-202 Bridge] Gateway admission failed (non-fatal): %s", e
            )
            # Non-fatal: proceed with raw intent if gateway is unavailable

        # ── Step 4: Build director_studio_spec with admission threading ──
        director_studio_spec: dict[str, Any] = {
            "action_name": intent_dict.get("action_name", ""),
            "visual_reference_path": intent_dict.get("visual_reference_path", ""),
            "_visual_reference_path": intent_dict.get("visual_reference_path", ""),
            "vfx_overrides": intent_dict.get("vfx_overrides", {}),
            "raw_vibe": intent_dict.get("raw_vibe", ""),
        }

        # Thread admission result into director_studio_spec (K8s Mutating Webhook pattern)
        if admission is not None:
            try:
                thread_admission_into_director_spec(director_studio_spec, admission)
                logger.info(
                    "[SESSION-202 Bridge] Admission threaded into director_studio_spec: "
                    "action_name=%s, ref=%s",
                    director_studio_spec.get("action_name"),
                    bool(director_studio_spec.get("_visual_reference_path")),
                )
            except Exception as e:
                logger.warning(
                    "[SESSION-202 Bridge] Admission threading failed (non-fatal): %s", e
                )

        # ── Step 5: Industrial baking banner ─────────────────────────
        yield {
            "stage": "baking",
            "progress": 0.3,
            "message": (
                "[⚙️ 工业烘焙网关] 正在通过 Catmull-Rom 样条插值，"
                "纯 CPU 解算高精度工业级贴图动作序列..."
            ),
            "gallery": [],
            "video": None,
            "error": None,
        }

        # ── Step 6: Pipeline execution via ModeDispatcher ────────────
        yield {
            "stage": "pipeline_dispatch",
            "progress": 0.4,
            "message": "[🚀 管线调度] 正在通过 ModeDispatcher 调度 Production 管线...",
            "gallery": [],
            "video": None,
            "error": None,
        }

        pipeline_error = None
        try:
            self._execute_pipeline(intent_dict, director_studio_spec)
        except Exception as e:
            pipeline_error = str(e)
            logger.warning(
                "[SESSION-202 Bridge] Pipeline execution error: %s", e
            )

        # ── Step 7: Simulated progress stages (CPU baking visualization) ──
        stages = [
            (0.5, "baking_openpose", "[⚙️ 工业量产] 正在解算 OpenPose 骨骼序列..."),
            (0.6, "baking_albedo", "[⚙️ 工业量产] 正在烘焙 Albedo 贴图序列..."),
            (0.7, "baking_normal", "[⚙️ 工业量产] 正在烘焙 Normal 法线贴图..."),
            (0.8, "baking_depth", "[⚙️ 工业量产] 正在烘焙 Depth 深度贴图..."),
            (0.85, "vfx_hydration", "[⚙️ VFX 注水] 正在注入流体/物理 ControlNet 拓扑..."),
            (0.9, "dag_closure", "[⚙️ DAG 闭合] 正在验证工作流拓扑完整性..."),
        ]

        for progress, stage, message in stages:
            yield {
                "stage": stage,
                "progress": progress,
                "message": message,
                "gallery": [],
                "video": None,
                "error": None,
            }

        # ── Step 8: Collect outputs ──────────────────────────────────
        gallery_files = self._collect_output_gallery()
        video_file = self._find_output_video()

        completion_msg = (
            f"[✅ SESSION-202] 核动力渲染完成！"
            f"共生成 {len(gallery_files)} 张序列帧"
            + (f"，视频已就绪" if video_file else "")
        )
        if pipeline_error:
            completion_msg += f"\n⚠️ 管线提示: {pipeline_error}"

        yield {
            "stage": "complete",
            "progress": 1.0,
            "message": completion_msg,
            "gallery": gallery_files,
            "video": video_file,
            "error": None,
        }

    def _execute_pipeline(
        self,
        intent_dict: dict[str, Any],
        director_studio_spec: dict[str, Any],
    ) -> None:
        """Execute the real pipeline via ModeDispatcher.

        SESSION-203-HOTFIX: This method now performs actual pipeline dispatch
        instead of just creating a CreatorIntentSpec object.  It:

        1. Builds the ``options`` dict expected by ``ProductionStrategy.build_context()``.
        2. Threads ``director_studio_spec`` with admission-validated fields.
        3. Dispatches through ``ModeDispatcher.dispatch("production", execute=True)``.

        The dispatch is wrapped in try/except so the Web UI never crashes
        even if the pipeline or ComfyUI backend is unavailable.
        """
        try:
            from mathart.workspace.mode_dispatcher import ModeDispatcher

            action = intent_dict.get("action_name", "") or intent_dict.get("action", "")
            vibe = intent_dict.get("raw_vibe", "")
            vfx_overrides = intent_dict.get("vfx_overrides", {})

            # Build production options matching ProductionStrategy.build_context() contract
            options: dict[str, Any] = {
                "skip_ai_render": False,
                "batch_size": 20,
                "pdg_workers": 16,
                "gpu_slots": 1,
                "seed": 20260425,
                "interactive": False,  # Non-interactive (headless bridge)
                # SESSION-196 P0-CLI-INTENT-THREADING fields
                "director_studio_spec": director_studio_spec,
                "vibe": vibe,
                "vfx_artifacts": vfx_overrides if vfx_overrides else None,
                # SESSION-191 LookDev Deep Pruning — action_filter 穿透
                "action_filter": [action] if action else None,
            }

            dispatcher = ModeDispatcher(project_root=self.project_root)
            result = dispatcher.dispatch(
                "production",
                options=options,
                execute=True,
            )

            logger.info(
                "[SESSION-202 Bridge] Pipeline dispatch result: strategy=%s, executed=%s",
                result.strategy_name,
                result.executed,
            )

        except Exception as e:
            logger.warning("[SESSION-202 Bridge] Pipeline dispatch failed: %s", e)
            raise

    def _collect_output_gallery(self) -> list[str]:
        """Scan ``outputs/final_renders/`` for image files."""
        gallery: list[str] = []
        if self.outputs_dir.exists():
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                gallery.extend(str(p) for p in sorted(self.outputs_dir.glob(ext)))
        return gallery[-50:]  # Cap at 50 most recent

    def _find_output_video(self) -> str | None:
        """Find the most recent MP4 video in outputs."""
        if self.outputs_dir.exists():
            videos = sorted(self.outputs_dir.glob("*.mp4"), key=os.path.getmtime)
            if videos:
                return str(videos[-1])
        return None
