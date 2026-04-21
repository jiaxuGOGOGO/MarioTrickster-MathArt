"""
SESSION-125 (P2-SPINE-PREVIEW-1): Spine preview backend plugin.

This module is the registry adapter around
``mathart.animation.spine_preview``. It keeps all JSON parsing, parameter
normalization, demo self-healing, and typed manifest construction inside the
backend layer so the trunk pipeline remains untouched.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import BackendCapability, BackendMeta, register_backend
from mathart.core.backend_types import BackendType

logger = logging.getLogger(__name__)


@register_backend(
    BackendType.SPINE_PREVIEW,
    display_name="Spine JSON Headless Preview",
    version="1.0.0",
    artifact_families=(ArtifactFamily.ANIMATION_PREVIEW.value,),
    capabilities=(BackendCapability.ANIMATION_EXPORT,),
    input_requirements=("spine_json_path",),
    dependencies=(),
    session_origin="SESSION-125",
    schema_version="1.0.0",
)
class SpinePreviewBackend:
    """Microkernel backend for Spine JSON tensor FK preview rendering."""

    @property
    def name(self) -> str:
        return BackendType.SPINE_PREVIEW.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(self, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        ctx = dict(context)
        output_dir = Path(ctx.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        ctx["output_dir"] = str(output_dir)
        ctx.setdefault("fps", None)
        ctx.setdefault("animation_name", None)
        ctx.setdefault("canvas_size", (512, 512))
        ctx.setdefault("margin", 32)
        ctx.setdefault("render_gif", True)
        stem = str(ctx.get("name", "spine_preview"))
        spine_json_path = ctx.get("spine_json_path")

        from mathart.animation.spine_preview import create_demo_spine_json

        resolved_path: Path | None = None
        if isinstance(spine_json_path, (str, Path)):
            candidate = Path(spine_json_path)
            if candidate.exists() and candidate.is_file():
                resolved_path = candidate.resolve()

        if resolved_path is None:
            resolved_path = output_dir / f"{stem}_synthetic_spine.json"
            create_demo_spine_json(resolved_path)
            if spine_json_path is None:
                warnings.append(
                    "spine_json_path 未提供；已生成合成 Spine JSON 用于无头预览验证。"
                )
            else:
                warnings.append(
                    "spine_json_path 不可读或为 CI 占位值；已回退为合成 Spine JSON。"
                )

        canvas_size = ctx.get("canvas_size", (512, 512))
        if isinstance(canvas_size, int):
            canvas_size = (int(canvas_size), int(canvas_size))
        elif isinstance(canvas_size, (list, tuple)) and len(canvas_size) == 2:
            canvas_size = (int(canvas_size[0]), int(canvas_size[1]))
        else:
            canvas_size = (512, 512)
            warnings.append("canvas_size 非法，已回退为 (512, 512)。")

        ctx["spine_json_path"] = str(resolved_path)
        ctx["canvas_size"] = canvas_size
        ctx["margin"] = int(ctx.get("margin", 32))
        ctx["render_gif"] = bool(ctx.get("render_gif", True))
        return ctx, warnings

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        ctx, warnings = self.validate_config(context)
        output_dir = Path(ctx["output_dir"])
        stem = str(ctx.get("name", "spine_preview"))
        session_id = str(ctx.get("session_id", "SESSION-125"))

        from mathart.animation.spine_preview import HeadlessSpineRenderer, SpineJSONTensorSolver

        solver = SpineJSONTensorSolver(fps=ctx.get("fps"))
        solve_start = time.perf_counter()
        clip = solver.solve(
            ctx["spine_json_path"],
            animation_name=ctx.get("animation_name"),
            fps=ctx.get("fps"),
        )
        solve_time_ms = (time.perf_counter() - solve_start) * 1000.0

        renderer = HeadlessSpineRenderer(
            canvas_size=ctx["canvas_size"],
            margin=ctx["margin"],
        )
        mp4_path = output_dir / f"{stem}_{clip.animation_name}_preview.mp4"
        gif_path = output_dir / f"{stem}_{clip.animation_name}_preview.gif"
        diagnostics_path = output_dir / f"{stem}_{clip.animation_name}_preview_diagnostics.json"
        render_result = renderer.render(
            clip,
            output_mp4_path=mp4_path,
            output_gif_path=(gif_path if ctx.get("render_gif", True) else mp4_path.with_suffix(".gif")),
            diagnostics_path=diagnostics_path,
        )

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.ANIMATION_PREVIEW.value,
            backend_type=BackendType.SPINE_PREVIEW,
            version="1.0.0",
            session_id=session_id,
            outputs={
                "preview_mp4": render_result.mp4_path,
                "preview_gif": render_result.gif_path,
                "diagnostics_json": render_result.diagnostics_path,
                "spine_json": str(ctx["spine_json_path"]),
            },
            metadata={
                "bone_count": clip.bone_count,
                "frame_count": clip.frame_count,
                "fps": clip.fps,
                "canvas_size": list(render_result.canvas_size),
                "render_time_ms": render_result.render_time_ms,
                "animation_name": clip.animation_name,
                "solver_time_ms": solve_time_ms,
                "topology_depth": len(clip.depth_levels),
                "warnings": warnings,
            },
            quality_metrics={
                "bone_count": float(clip.bone_count),
                "frame_count": float(clip.frame_count),
                "render_time_ms": float(render_result.render_time_ms),
                "solver_time_ms": float(solve_time_ms),
                "frames_per_second_effective": float(
                    clip.frame_count / max(render_result.render_time_ms / 1000.0, 1e-6)
                ),
            },
        )
        logger.info(
            "SpinePreviewBackend: solved %d bones / %d frames from %s",
            clip.bone_count,
            clip.frame_count,
            ctx["spine_json_path"],
        )
        return manifest


__all__ = ["SpinePreviewBackend"]
