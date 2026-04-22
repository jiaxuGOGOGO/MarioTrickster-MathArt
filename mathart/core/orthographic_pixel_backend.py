"""SESSION-128 (P0-SESSION-127-CORE-CONSTRAINTS): OrthographicPixelRenderBackend —
Registry-Native Dead Cells 3D→2D Pixel Render Plugin with Fail-Fast Mesh Contract.

This module implements the ``@register_backend`` plugin for the Dead Cells-
style orthographic 3D→2D pixel art rendering pipeline.  It follows the
project's IoC Registry Pattern (SESSION-064) and Strong-Type Artifact
Contract (SESSION-064/073) without modifying any trunk code.

SESSION-128 CRITICAL CHANGE — Fail-Fast Mesh3D Consumption Contract
--------------------------------------------------------------------
The orthographic render backend now **strictly enforces** that a real
``Mesh3D`` object is provided in the execution context.  If the mesh is
absent, ``None``, or has zero geometry (0 vertices / 0 triangles), the
backend raises ``PipelineContractError`` immediately, halting the pipeline.

This change is grounded in four industrial/academic references:

1. **Pixar USD Composition Semantics**: A typed reference to a ``UsdGeomMesh``
   must resolve to real geometry.  If the referenced layer is missing, USD
   raises a composition error — it does NOT silently substitute a sphere.

2. **Bazel / Buck Action Cache & Determinism**: A build action's output
   depends only on its declared inputs.  If the input mesh is a demo sphere
   instead of the real composed character mesh, the output hash is
   deterministically *wrong* — producing 22,422 identical ``generator_invariant``
   iterations.

3. **Data Mesh Delivery Contract**: The ``/archive`` directory is the final
   delivery contract.  Delivering renders of a demo sphere instead of the
   real character mesh violates the delivery SLA.

4. **Jim Gray Fail-Fast (Tandem Computers, 1985)**: "Each module is
   self-checking.  When it detects a fault, it stops."  Silent fallback to
   a demo sphere is the textbook "fail-soft" anti-pattern that propagates
   corrupted state downstream.

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
- ✅ Fail-Fast Mesh Contract: raises ``PipelineContractError`` on missing mesh

Anti-Pattern Guards
-------------------
🚫 Perspective Distortion Trap: Enforces pure orthographic matrix.
🚫 Bilinear Blur Trap: Forces nearest-neighbor downscale only.
🚫 GUI Window Crash Trap: Pure NumPy — zero windowed context.
🚫 Fallback Sphere Trap (SESSION-128): ZERO fallback mesh generation.
   Missing Mesh3D → PipelineContractError, not silent degradation.
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
from mathart.pipeline_contract import PipelineContractError

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Channel semantics for the orthographic pixel render output
# ═══════════════════════════════════════════════════════════════════════════

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
    version="2.0.0",
    artifact_families=(
        ArtifactFamily.SPRITE_SHEET.value,
        ArtifactFamily.MATERIAL_BUNDLE.value,
    ),
    capabilities=(
        BackendCapability.SPRITE_EXPORT,
        BackendCapability.ATLAS_EXPORT,
    ),
    input_requirements=("mesh",),
    session_origin="SESSION-128",
    schema_version="2.0.0",
)
class OrthographicPixelRenderBackend:
    """Dead Cells-style 3D→2D orthographic pixel render backend.

    SESSION-128 enforces a **Fail-Fast Mesh3D Consumption Contract**:
    the backend MUST receive a real ``Mesh3D`` object from upstream
    (typically composed by ``Pseudo3DShellBackend`` + ``PhysicalRibbonBackend``
    + genotype attachment assembly).  If the mesh is missing, None, or
    geometrically empty, the backend raises ``PipelineContractError``
    immediately — no fallback sphere, no silent degradation.

    This backend implements the full pipeline:
    1. **Validate** real Mesh3D from context (Fail-Fast).
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

        SESSION-128 Fail-Fast Contract
        ------------------------------
        This method enforces the **Mesh3D Consumption Contract**:

        1. The context MUST contain a ``"mesh"`` key with a ``Mesh3D`` instance.
        2. The ``Mesh3D`` MUST have ``vertex_count > 0`` and ``triangle_count > 0``.
        3. If either condition fails, ``PipelineContractError`` is raised
           immediately — the pipeline is halted, not silently degraded.

        This eliminates the root cause of the ``generator_invariant``
        stagnation disaster: downstream renders of a fallback demo sphere
        produce identical outputs regardless of genotype variation.

        Parameters
        ----------
        context : dict
            Execution context.  **MUST** contain ``"mesh"`` (Mesh3D).

        Returns
        -------
        ArtifactManifest
            Strongly-typed manifest with Albedo/Normal/Depth outputs.

        Raises
        ------
        PipelineContractError
            If ``"mesh"`` is missing, not a ``Mesh3D``, or geometrically empty.
        """
        from mathart.animation.orthographic_pixel_render import (
            OrthographicRenderConfig,
            Mesh3D,
            render_orthographic_sprite,
            save_sprite_result,
            validate_hard_edges,
        )

        # ══════════════════════════════════════════════════════════════════
        #  SESSION-128: FAIL-FAST MESH3D CONSUMPTION CONTRACT
        #  Grounded in: Jim Gray Fail-Fast (1985), Pixar USD Schema
        #  Validation, Bazel Hermetic Action Determinism.
        #
        #  🚫 ZERO fallback mesh generation.
        #  🚫 ZERO silent degradation.
        #  🚫 ZERO demo sphere substitution.
        # ══════════════════════════════════════════════════════════════════
        mesh = context.get("mesh")

        if mesh is None:
            raise PipelineContractError(
                violation_type="missing_mesh3d",
                detail=(
                    "[OrthographicPixelRenderBackend] Fail-Fast: context does "
                    "not contain a 'mesh' key.  The orthographic render backend "
                    "requires a real Mesh3D object assembled by upstream stages "
                    "(Pseudo3DShellBackend + PhysicalRibbonBackend + genotype "
                    "attachment composition).  Fallback sphere generation has "
                    "been permanently removed per SESSION-128 to eliminate the "
                    "generator_invariant stagnation disaster.  "
                    "Ref: Jim Gray Fail-Fast (Tandem Computers, 1985)."
                ),
            )

        if not isinstance(mesh, Mesh3D):
            raise PipelineContractError(
                violation_type="invalid_mesh3d_type",
                detail=(
                    f"[OrthographicPixelRenderBackend] Fail-Fast: context['mesh'] "
                    f"is {type(mesh).__name__}, not Mesh3D.  The orthographic "
                    f"render backend requires a strongly-typed Mesh3D object.  "
                    f"Raw mesh dicts must be converted to Mesh3D before passing "
                    f"to this backend.  "
                    f"Ref: Pixar USD Schema Validation — type ambiguity is the "
                    f"root of all pipeline bugs."
                ),
            )

        if mesh.vertex_count == 0 or mesh.triangle_count == 0:
            raise PipelineContractError(
                violation_type="empty_mesh3d",
                detail=(
                    f"[OrthographicPixelRenderBackend] Fail-Fast: Mesh3D is "
                    f"geometrically empty (vertices={mesh.vertex_count}, "
                    f"triangles={mesh.triangle_count}).  An empty mesh cannot "
                    f"produce meaningful orthographic renders.  This likely "
                    f"indicates an upstream assembly failure in the composed "
                    f"mesh stage.  "
                    f"Ref: Bazel Hermetic Determinism — an action with empty "
                    f"inputs must fail, not produce a vacuous output."
                ),
            )

        logger.info(
            "[orthographic_pixel_render] Mesh3D contract satisfied: "
            "%d vertices, %d triangles (real composed mesh, NOT fallback)",
            mesh.vertex_count, mesh.triangle_count,
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

        # Execute the full pipeline with the REAL composed mesh
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

        # SESSION-098 (HIGH-2.6): FAMILY_SCHEMAS requires a "spritesheet"
        # output key for ArtifactFamily.SPRITE_SHEET.  Use the albedo channel
        # (the primary visual output) as the canonical spritesheet path.
        if "albedo" in outputs and "spritesheet" not in outputs:
            outputs["spritesheet"] = outputs["albedo"]

        # Save render report — SESSION-128: includes mesh_source provenance
        report = {
            "pipeline": "dead_cells_orthographic_pixel_render",
            "session": "SESSION-128",
            "mesh_contract": {
                "fail_fast_enforced": True,
                "fallback_sphere_removed": True,
                "mesh_source": "upstream_composed_mesh3d",
                "reference": "Jim Gray Fail-Fast (Tandem Computers, 1985)",
            },
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
            version="2.0.0",
            session_id=validated.get("session_id", "SESSION-128"),
            outputs=outputs,
            metadata={
                # SESSION-098 (HIGH-2.6): FAMILY_SCHEMAS for sprite_sheet
                # requires frame_count, frame_width, frame_height at the
                # top level of metadata.
                "frame_count": 1,
                "frame_width": config.output_width,
                "frame_height": config.output_height,
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
                "mesh_contract": {
                    "fail_fast_enforced": True,
                    "fallback_sphere_removed": True,
                    "session": "SESSION-128",
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
                "fail_fast_mesh_contract",
                "session-128",
            ],
        )
