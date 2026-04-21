"""
SESSION-124 (P2-UNITY-2DANIM-1): Unity 2D Native Animation Backend Plugin.

This module is the **registry-native backend wrapper** around the pure
algorithm module ``mathart.animation.unity_2d_anim``.  It follows the
exact same Ports-and-Adapters discipline as ``LevelTopologyBackend``
(SESSION-109) and ``ReactionDiffusionBackend`` (SESSION-119):

1. Self-registers via ``@register_backend`` at import time.
2. Owns ``validate_config()`` — all parameter parsing and contract
   validation is sunk into the backend Adapter.
3. ``execute()`` returns a strongly-typed ``ArtifactManifest`` with
   ``artifact_family = UNITY_NATIVE_ANIM``.

Input Contract
--------------
* ``clip_2d``: A ``Clip2D`` object from ``mathart.animation.orthographic_projector``.
  When absent or a placeholder string (CI fixture), the backend synthesises
  a minimal 3-bone, 10-frame demo clip for hermetic validation.
* ``output_dir``: Directory to write output files (default: ``"output"``).
* ``clip_name``: Override for the animation clip name.
* ``controller_name``: Override for the animator controller name.
* ``loop``: Whether the animation should loop (default: ``True``).
* ``fps``: Frames per second (default: ``30.0``).

Output Contract (family ``UNITY_NATIVE_ANIM``)
-----------------------------------------------
* ``anim_file``: Path to the generated ``.anim`` file.
* ``controller_file``: Path to the generated ``.controller`` file.
* ``anim_meta_file``: Path to the ``.anim.meta`` file.
* ``controller_meta_file``: Path to the ``.controller.meta`` file.

Red-Line Guards
---------------
🔴 Anti-PyYAML-Overhead: The underlying emitter NEVER uses ``import yaml``.
🔴 Anti-Euler-Flip: ``np.unwrap`` is mandatory before tangent baking.
🔴 Anti-GUID-Collision: ``hashlib.md5(name.encode()).hexdigest()`` only.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

import numpy as np

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)
from mathart.core.backend_types import BackendType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthetic demo clip for CI / hermetic validation
# ---------------------------------------------------------------------------

def _build_synthetic_clip_2d():
    """Build a minimal 3-bone, 10-frame Clip2D for CI fixture compatibility.

    This allows the backend to pass registry smoke tests and CI guards
    without requiring a fully formed upstream pipeline.
    """
    from mathart.animation.orthographic_projector import (
        Bone2D, Clip2D, Pose2D,
    )

    bones = [
        Bone2D(name="root", parent=None, x=0.0, y=0.0, rotation=0.0,
               length=0.0, scale_x=1.0, scale_y=1.0, sorting_order=0),
        Bone2D(name="spine", parent="root", x=0.0, y=0.5, rotation=0.0,
               length=0.5, scale_x=1.0, scale_y=1.0, sorting_order=0),
        Bone2D(name="head", parent="spine", x=0.0, y=1.0, rotation=0.0,
               length=0.3, scale_x=1.0, scale_y=1.0, sorting_order=0),
    ]

    frames = []
    for i in range(10):
        t = i / 9.0
        angle = math.sin(t * 2 * math.pi) * 15.0
        pose = Pose2D(
            bone_transforms={
                "root": {"x": 0.0, "y": 0.0, "rotation": 0.0,
                         "scale_x": 1.0, "scale_y": 1.0},
                "spine": {"x": 0.0, "y": 0.5, "rotation": angle * 0.5,
                           "scale_x": 1.0, "scale_y": 1.0},
                "head": {"x": 0.0, "y": 1.0, "rotation": angle,
                          "scale_x": 1.0, "scale_y": 1.0},
            },
            root_x=0.0,
            root_y=0.0,
            root_rotation=0.0,
            sorting_orders={"root": 0, "spine": 0, "head": 0},
        )
        frames.append(pose)

    return Clip2D(
        name="synthetic_demo",
        fps=30.0,
        frames=frames,
        skeleton_bones=bones,
    )


# ---------------------------------------------------------------------------
# The backend plugin
# ---------------------------------------------------------------------------


@register_backend(
    BackendType.UNITY_2D_ANIM,
    display_name="Unity 2D Native Animation Exporter",
    version="1.0.0",
    artifact_families=(ArtifactFamily.UNITY_NATIVE_ANIM.value,),
    capabilities=(BackendCapability.ANIMATION_EXPORT,),
    input_requirements=("clip_2d",),
    dependencies=(),
    session_origin="SESSION-124",
    schema_version="1.0.0",
)
class Unity2DAnimBackend:
    """Microkernel plugin that exports Clip2D to Unity native .anim files.

    This backend wraps ``mathart.animation.unity_2d_anim.Unity2DAnimExporter``
    and produces a strongly-typed ``UNITY_NATIVE_ANIM`` manifest.
    """

    @property
    def name(self) -> str:
        return BackendType.UNITY_2D_ANIM.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta  # injected by @register_backend

    # ------------------------------------------------------------------ config

    def validate_config(
        self, context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize the execution context.

        Handles CI fixture placeholders by synthesising a demo clip.
        """
        warnings: list[str] = []
        ctx = dict(context)
        ctx.setdefault("output_dir", "output")
        ctx.setdefault("clip_name", None)
        ctx.setdefault("controller_name", None)
        ctx.setdefault("loop", True)
        ctx.setdefault("fps", 30.0)

        # CI Minimal Context Fixture compatibility: the CI guard injects a
        # placeholder string for every declared input_requirements key.
        clip = ctx.get("clip_2d")
        if clip is None or isinstance(clip, str):
            ctx["clip_2d"] = _build_synthetic_clip_2d()
            if isinstance(clip, str):
                warnings.append(
                    "clip_2d received placeholder string from CI fixture; "
                    "synthesised a 3-bone, 10-frame demo clip for hermetic validation."
                )
            else:
                warnings.append(
                    "clip_2d not provided; synthesised a 3-bone, 10-frame demo clip."
                )

        return ctx, warnings

    # ------------------------------------------------------------------ execute

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Execute the Unity 2D animation export pipeline.

        Returns a strongly-typed ``UNITY_NATIVE_ANIM`` manifest with
        all required metadata keys.
        """
        # Self-heal: if caller invokes execute() directly without validate_config
        ctx = context
        if "output_dir" not in ctx:
            ctx, _warnings = self.validate_config(ctx)

        # Ensure clip_2d is valid
        clip = ctx.get("clip_2d")
        if clip is None or isinstance(clip, str):
            ctx, _warnings = self.validate_config(ctx)
            clip = ctx["clip_2d"]

        from mathart.animation.unity_2d_anim import Unity2DAnimExporter

        fps = float(ctx.get("fps", 30.0))
        exporter = Unity2DAnimExporter(fps=fps)

        result = exporter.export(
            clip_2d=clip,
            output_dir=ctx["output_dir"],
            clip_name=ctx.get("clip_name"),
            controller_name=ctx.get("controller_name"),
            loop=ctx.get("loop", True),
        )

        # Build manifest
        outputs = {
            "anim_file": result.anim_paths[0] if result.anim_paths else "",
            "controller_file": result.controller_path,
        }
        if result.meta_paths:
            outputs["anim_meta_file"] = result.meta_paths[0]
            if len(result.meta_paths) > 1:
                outputs["controller_meta_file"] = result.meta_paths[1]

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.UNITY_NATIVE_ANIM.value,
            backend_type=BackendType.UNITY_2D_ANIM,
            version="1.0.0",
            session_id=str(ctx.get("session_id", "SESSION-124")),
            outputs=outputs,
            metadata={
                "bone_count": result.bone_count,
                "frame_count": result.frame_count,
                "total_keyframes": result.total_keyframes,
                "fps": fps,
                "export_time_ms": result.export_time_ms,
                "anim_guids": result.guids,
                "loop": ctx.get("loop", True),
                "clip_name": ctx.get("clip_name") or getattr(clip, "name", "untitled"),
            },
            quality_metrics={
                "bone_count": float(result.bone_count),
                "frame_count": float(result.frame_count),
                "total_keyframes": float(result.total_keyframes),
                "export_time_ms": result.export_time_ms,
            },
        )

        logger.info(
            "Unity2DAnimBackend: exported %d bones, %d frames → %s",
            result.bone_count, result.frame_count, result.anim_paths,
        )

        return manifest


__all__ = ["Unity2DAnimBackend"]
