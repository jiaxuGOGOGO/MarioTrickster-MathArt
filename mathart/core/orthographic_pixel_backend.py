"""SESSION-089 (P1-INDUSTRIAL-34C): OrthographicPixelRenderBackend —
Registry-Native Dead Cells 3D→2D Pixel Render Plugin.

This module implements the ``@register_backend`` plugin for the Dead Cells-
style orthographic 3D→2D pixel art rendering pipeline.  It follows the
project's IoC Registry Pattern (SESSION-064) and Strong-Type Artifact
Contract (SESSION-064/073) without modifying any trunk code.

Research Foundations
--------------------
1. **Motion Twin (Dead Cells) GDC 2018** — Thomas Vasseur:
   3D skeletal animation → orthographic camera → no-AA nearest-neighbor
   downsample → synchronous Albedo/Normal/Depth export.

2. **Arc System Works (Guilty Gear Xrd) GDC 2015** — Junya C. Motomura:
   Hard stepped cel-shading thresholds, frame decimation to 12fps.

3. **Headless EGL / Software Rasterizer**:
   Pure NumPy software rasterizer — zero GLFW/X11 dependency.

Architecture Discipline
-----------------------
- ✅ Independent plugin: self-registers via ``@register_backend``
- ✅ No trunk modification: no if/else in AssetPipeline/Orchestrator
- ✅ Strong-type contract: returns ``ArtifactManifest`` with explicit
  ``artifact_family`` and ``backend_type``
- ✅ Backend-owned ``validate_config()``: all parameter validation is
  physically sunk into this Adapter (Hexagonal Architecture)

Anti-Pattern Guards
-------------------
🚫 Perspective Distortion Trap: Enforces pure orthographic matrix.
🚫 Bilinear Blur Trap: Forces nearest-neighbor downscale only.
🚫 GUI Window Crash Trap: Pure NumPy — zero windowed context.
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

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  New BackendType and ArtifactFamily for Orthographic Pixel Render
# ═══════════════════════════════════════════════════════════════════════════

# We use the existing INDUSTRIAL_SPRITE backend type and artifact family
# since this backend is the industrial-grade 3D→2D pixel render pipeline.
# The artifact family INDUSTRIAL_SPRITE is already defined in the schema.

# Channel semantics for the orthographic pixel render output
_ORTHO_CHANNEL_SEMANTICS: dict[str, dict[str, str]] = {
    "albedo": {
        "description": "Cel-shaded base color (sRGB, hard-stepped lighting)",
        "color_space": "sRGB",
        "engine_slot_unity": "_MainTex",
        "engine_slot_godot": "texture_albedo",
    },
    "normal": {
        "description": "World-space normal map (R=X, G=Y, B=Z, [0,255]→[-1,1])",
        "color_space": "linear",
        "engine_slot_unity": "_NormalMap",
        "engine_slot_godot": "texture_normal",
    },
    "depth": {
        "description": "Linear depth (grayscale, 0=far, 255=near, orthographic)",
        "color_space": "linear",
        "engine_slot_unity": "_DepthMap",
        "engine_slot_godot": "texture_depth",
    },
}


@register_backend(
    "orthographic_pixel_render",
    display_name="Orthographic Pixel Render (Dead Cells 3D→2D)",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.SPRITE_SHEET.value,
        ArtifactFamily.MATERIAL_BUNDLE.value,
    ),
    capabilities=(
        BackendCapability.SPRITE_EXPORT,
        BackendCapability.ATLAS_EXPORT,
    ),
    input_requirements=("mesh_data",),
    session_origin="SESSION-089",
    schema_version="1.0.0",
)
class OrthographicPixelRenderBackend:
    """Dead Cells-style 3D→2D orthographic pixel render backend.

    This backend implements the full pipeline:
    1. Accept 3D mesh/skeleton data from upstream.
    2. Render via pure NumPy software rasterizer (headless).
    3. Apply hard-stepped cel-shading (Guilty Gear Xrd style).
    4. Downscale via nearest-neighbor (Dead Cells: no AA).
    5. Export spatially-aligned Albedo/Normal/Depth channels.
    6. Return a strongly-typed ``ArtifactManifest``.

    The backend self-registers via ``@register_backend`` and requires
    ZERO modification to any trunk code (AssetPipeline, Orchestrator).
    """

    @property
    def name(self) -> str:
        return "orthographic_pixel_render"

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(
        self, config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize orthographic render configuration.

        Backend-owned validation (Hexagonal Architecture: Adapter owns
        all parameter parsing, CLI Port is ignorant of business logic).

        Parameters
        ----------
        config : dict
            Raw configuration from CLI or orchestrator.

        Returns
        -------
        tuple[dict, list[str]]
            (validated_config, warnings)
        """
        warnings: list[str] = []
        validated = dict(config)

        # --- Render namespace ---
        render = validated.get("render", {})
        if not isinstance(render, dict):
            render = {}
            warnings.append("render config must be a dict; using defaults")

        render_width = int(render.get("render_width", 256))
        render_height = int(render.get("render_height", 256))
        output_width = int(render.get("output_width",
                           validated.get("width", 64)))
        output_height = int(render.get("output_height",
                            validated.get("height", 64)))

        if render_width < 16:
            warnings.append(
                f"render_width={render_width} too small; clamping to 16"
            )
            render_width = 16
        if render_height < 16:
            warnings.append(
                f"render_height={render_height} too small; clamping to 16"
            )
            render_height = 16
        if output_width < 8:
            warnings.append(
                f"output_width={output_width} too small; clamping to 8"
            )
            output_width = 8
        if output_height < 8:
            warnings.append(
                f"output_height={output_height} too small; clamping to 8"
            )
            output_height = 8

        render["render_width"] = render_width
        render["render_height"] = render_height
        render["output_width"] = output_width
        render["output_height"] = output_height
        validated["render"] = render

        # --- Lighting namespace ---
        lighting = validated.get("lighting", {})
        if not isinstance(lighting, dict):
            lighting = {}
        light_dir = lighting.get("direction", (-0.577, 0.577, 0.577))
        if isinstance(light_dir, (list, tuple)) and len(light_dir) == 3:
            lighting["direction"] = tuple(float(x) for x in light_dir)
        else:
            lighting["direction"] = (-0.577, 0.577, 0.577)
            warnings.append("Invalid light direction; using default")

        cel_thresholds = lighting.get("cel_thresholds", (0.15, 0.55))
        if isinstance(cel_thresholds, (list, tuple)):
            cel_thresholds = tuple(float(x) for x in cel_thresholds)
        else:
            cel_thresholds = (0.15, 0.55)
        lighting["cel_thresholds"] = cel_thresholds
        validated["lighting"] = lighting

        # --- Channel selection ---
        channels = validated.get("channels", ["albedo", "normal", "depth"])
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",")]
        validated["channels"] = [
            ch for ch in channels if ch in _ORTHO_CHANNEL_SEMANTICS
        ]
        if not validated["channels"]:
            validated["channels"] = ["albedo", "normal", "depth"]
            warnings.append("No valid channels; using all defaults")

        # --- FPS ---
        validated["fps"] = int(validated.get("fps", 12))

        return validated, warnings

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Execute the orthographic pixel render pipeline.

        Parameters
        ----------
        context : dict
            Execution context with optional mesh data and configuration.

        Returns
        -------
        ArtifactManifest
            Strongly-typed manifest with Albedo/Normal/Depth outputs.
        """
        from mathart.animation.orthographic_pixel_render import (
            OrthographicRenderConfig,
            Mesh3D,
            render_orthographic_sprite,
            save_sprite_result,
            create_sphere_mesh,
            validate_hard_edges,
        )

        # Validate config (backend-owned)
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[orthographic_pixel_render] %s", w)

        output_dir = Path(validated.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = validated.get("name", "ortho_pixel_render")
        bundle_dir = output_dir / f"{stem}_bundle"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        render_cfg = validated.get("render", {})
        lighting_cfg = validated.get("lighting", {})

        # Build render config
        config = OrthographicRenderConfig(
            render_width=int(render_cfg.get("render_width", 256)),
            render_height=int(render_cfg.get("render_height", 256)),
            output_width=int(render_cfg.get("output_width", 64)),
            output_height=int(render_cfg.get("output_height", 64)),
            light_direction=tuple(lighting_cfg.get(
                "direction", (-0.577, 0.577, 0.577)
            )),
            cel_thresholds=tuple(lighting_cfg.get(
                "cel_thresholds", (0.15, 0.55)
            )),
            fps=int(validated.get("fps", 12)),
        )

        # Get or create mesh
        mesh = context.get("mesh")
        if mesh is None or not isinstance(mesh, Mesh3D):
            # Default: create a demo sphere for testing
            mesh = create_sphere_mesh(
                radius=0.5, rings=16, sectors=24,
                color=(200, 120, 80),
            )
            logger.info(
                "[orthographic_pixel_render] No mesh provided; "
                "using default sphere (%d verts, %d tris)",
                mesh.vertex_count, mesh.triangle_count,
            )

        # Execute the full pipeline
        t0 = time.monotonic()
        result = render_orthographic_sprite(mesh, config)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        # Save outputs
        paths = save_sprite_result(result, bundle_dir, stem)

        # Validate hard edges (Dead Cells requirement)
        albedo_valid, albedo_diag = validate_hard_edges(result.albedo_image)
        normal_valid, normal_diag = validate_hard_edges(result.normal_image)
        depth_valid, depth_diag = validate_hard_edges(result.depth_image)

        # Build texture_channels payload
        requested_channels = validated.get(
            "channels", ["albedo", "normal", "depth"]
        )
        texture_channels: dict[str, dict[str, Any]] = {}
        for ch_name in requested_channels:
            ch_path = paths.get(ch_name)
            if ch_path:
                semantics = _ORTHO_CHANNEL_SEMANTICS.get(ch_name, {})
                texture_channels[ch_name] = {
                    "path": ch_path,
                    "dimensions": {
                        "width": config.output_width,
                        "height": config.output_height,
                    },
                    "bit_depth": 8,
                    "color_space": semantics.get("color_space", "linear"),
                    "engine_slot": {
                        "unity": semantics.get("engine_slot_unity", ""),
                        "godot": semantics.get("engine_slot_godot", ""),
                    },
                    "description": semantics.get("description", ""),
                }

        # Build outputs dict for manifest
        outputs: dict[str, str] = {}
        for ch_name, ch_info in texture_channels.items():
            outputs[ch_name] = ch_info["path"]

        # Save render report
        report = {
            "pipeline": "dead_cells_orthographic_pixel_render",
            "session": "SESSION-089",
            "render_config": {
                "render_resolution": f"{config.render_width}x{config.render_height}",
                "output_resolution": f"{config.output_width}x{config.output_height}",
                "projection": "orthographic",
                "cel_thresholds": list(config.cel_thresholds),
                "light_direction": list(config.light_direction),
                "fps": config.fps,
                "downscale_method": "nearest_neighbor",
                "renderer": "numpy_software_rasterizer",
            },
            "mesh_stats": {
                "vertex_count": mesh.vertex_count,
                "triangle_count": mesh.triangle_count,
            },
            "render_stats": result.metadata,
            "hard_edge_validation": {
                "albedo": albedo_diag,
                "normal": normal_diag,
                "depth": depth_diag,
                "all_valid": albedo_valid and normal_valid and depth_valid,
            },
            "elapsed_ms": elapsed_ms,
        }
        report_path = bundle_dir / f"{stem}_render_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        outputs["render_report"] = str(report_path)

        return ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SHEET.value,
            backend_type="orthographic_pixel_render",
            version="1.0.0",
            session_id=validated.get("session_id", "SESSION-089"),
            outputs=outputs,
            metadata={
                "channels": requested_channels,
                "bundle_kind": "orthographic_pixel_sprite",
                "lane": "rendering_3d_to_2d",
                "renderer": "numpy_software_rasterizer",
                "projection_type": "orthographic",
                "dimensions": {
                    "render": f"{config.render_width}x{config.render_height}",
                    "output": f"{config.output_width}x{config.output_height}",
                },
                "mesh_stats": {
                    "vertex_count": mesh.vertex_count,
                    "triangle_count": mesh.triangle_count,
                },
                "cel_shading": {
                    "thresholds": list(config.cel_thresholds),
                    "band_count": len(config.cel_thresholds) + 1,
                    "light_direction": list(config.light_direction),
                },
                "hard_edge_validation": {
                    "albedo_valid": albedo_valid,
                    "normal_valid": normal_valid,
                    "depth_valid": depth_valid,
                    "all_valid": albedo_valid and normal_valid and depth_valid,
                },
                "elapsed_ms": elapsed_ms,
                "fps": config.fps,
                "headless": True,
                "payload": {
                    "texture_channels": texture_channels,
                    "bundle_path": str(bundle_dir.resolve()),
                    "render_report_path": str(report_path.resolve()),
                },
            },
            quality_metrics={
                "channel_count": float(len(texture_channels)),
                "coverage_ratio": float(
                    result.metadata.get("coverage_ratio", 0.0)
                ),
                "hard_edge_score": 1.0 if (
                    albedo_valid and normal_valid and depth_valid
                ) else 0.0,
                "triangles_rendered_ratio": float(
                    result.metadata.get("triangles_rendered", 0)
                ) / max(1, mesh.triangle_count),
                "render_time_ms": elapsed_ms,
            },
            tags=[
                "dead_cells",
                "orthographic",
                "pixel_art",
                "cel_shading",
                "headless",
                "session-089",
            ],
        )
