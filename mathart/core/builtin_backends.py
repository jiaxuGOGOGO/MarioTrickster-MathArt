"""Built-in export backends for the Golden Path microkernel.

SESSION-066 hardens the registry inventory around canonical backend types while
keeping historical names compatible through ``BackendType`` alias resolution.
The philosophy is to register stable "slots" first and let old or future
pipelines plug into these slots without trunk edits.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mathart.animation.motion_2d_pipeline import Motion2DPipeline
from mathart.animation.unity_urp_native import (
    UnityURP2DNativePipelineGenerator,
    XPBDVATBakeConfig,
    bake_cloth_vat,
)
from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)
from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_types import BackendType

logger = logging.getLogger(__name__)


@register_backend(
    BackendType.MOTION_2D,
    display_name="Motion 2D Sprite Pipeline",
    version="2.0.0",
    artifact_families=(
        ArtifactFamily.ANIMATION_SPINE.value,
        ArtifactFamily.ANIMATION_SPRITESHEET.value,
    ),
    capabilities=(
        BackendCapability.SPRITE_EXPORT,
        BackendCapability.ANIMATION_EXPORT,
    ),
    input_requirements=("sdf_field", "motion_params"),
    session_origin="SESSION-066",
)
class Motion2DBackend:
    """Canonical 2D motion backend."""

    @property
    def name(self) -> str:
        return BackendType.MOTION_2D.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        output_dir = Path(context.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = context.get("name", "motion_2d")
        gait = str(context.get("gait", "biped_walk"))
        frame_count = int(context.get("frame_count", context.get("n_frames", 24)))
        speed = float(context.get("speed", 1.0))

        pipeline = Motion2DPipeline()
        if gait == "quadruped_trot":
            result = pipeline.run_quadruped_trot(n_frames=frame_count, speed=speed)
        else:
            gait = "biped_walk"
            result = pipeline.run_biped_walk(n_frames=frame_count, speed=speed)

        spine_json_path = output_dir / f"{stem}_spine.json"
        report_path = output_dir / f"{stem}_motion_report.json"
        pipeline.export_spine_json(result, spine_json_path)
        report_payload = {
            "gait": gait,
            "total_frames": int(result.total_frames),
            "pipeline_pass": bool(result.pipeline_pass),
            "projection_quality": (
                result.projection_quality.to_dict()
                if hasattr(result.projection_quality, "to_dict")
                else {}
            ),
            "ik_quality": (
                result.ik_quality.to_dict()
                if getattr(result, "ik_quality", None) is not None and hasattr(result.ik_quality, "to_dict")
                else None
            ),
        }
        report_path.write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        bone_count = len(getattr(result.clip_2d, "skeleton_bones", []) or [])
        return ArtifactManifest(
            artifact_family=ArtifactFamily.ANIMATION_SPINE.value,
            backend_type=BackendType.MOTION_2D,
            outputs={
                "spine_json": str(spine_json_path),
                "report": str(report_path),
            },
            metadata={
                "bone_count": bone_count,
                "animation_count": 1,
                "frame_count": int(result.total_frames),
                "gait": gait,
                "fps": int(getattr(getattr(result, "clip_2d", None), "fps", 0) or 0),
                "lane": "animation_2d",
                "pipeline_pass": bool(result.pipeline_pass),
            },
        )


@register_backend(
    BackendType.INDUSTRIAL_SPRITE,
    display_name="Industrial Sprite Bundle",
    version="2.0.0",
    artifact_families=(
        ArtifactFamily.MATERIAL_BUNDLE.value,
        ArtifactFamily.SPRITE_SHEET.value,
    ),
    capabilities=(
        BackendCapability.SPRITE_EXPORT,
        BackendCapability.SHADER_EXPORT,
    ),
    input_requirements=("skeleton", "pose", "style"),
    dependencies=(BackendType.MOTION_2D,),
    session_origin="SESSION-066",
)
class IndustrialSpriteBackend:
    """Encapsulates the industrial sprite + auxiliary-map production lane."""

    @property
    def name(self) -> str:
        return BackendType.INDUSTRIAL_SPRITE.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        base_dir = context.get("output_dir", "output")
        stem = context.get("name", "industrial_sprite")
        return ArtifactManifest(
            artifact_family=ArtifactFamily.MATERIAL_BUNDLE.value,
            backend_type=BackendType.INDUSTRIAL_SPRITE,
            outputs={
                "albedo": context.get("albedo_path", f"{base_dir}/{stem}_albedo.png"),
                "normal": context.get("normal_path", f"{base_dir}/{stem}_normal.png"),
                "depth": context.get("depth_path", f"{base_dir}/{stem}_depth.png"),
                "mask": context.get("mask_path", f"{base_dir}/{stem}_mask.png"),
            },
            metadata={
                "channels": ["albedo", "normal", "depth", "mask"],
                "bundle_kind": "industrial_sprite",
                "lane": "rendering_2d_aux",
                "renderer": "industrial_renderer",
            },
        )


@register_backend(
    BackendType.URP2D_BUNDLE,
    display_name="Unity URP 2D Bundle",
    version="2.0.0",
    artifact_families=(
        ArtifactFamily.ENGINE_PLUGIN.value,
        ArtifactFamily.VAT_BUNDLE.value,
        ArtifactFamily.SHADER_HLSL.value,
    ),
    capabilities=(
        BackendCapability.SPRITE_EXPORT,
        BackendCapability.SHADER_EXPORT,
        BackendCapability.VAT_EXPORT,
    ),
    input_requirements=("sprite_sheet", "shader_params"),
    dependencies=(BackendType.INDUSTRIAL_SPRITE,),
    session_origin="SESSION-066",
)
class URP2DBundleBackend:
    """Canonical Unity URP 2D bundle backend with VAT export metadata."""

    @property
    def name(self) -> str:
        return BackendType.URP2D_BUNDLE.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        output_dir = Path(context.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = context.get("name", "urp2d_bundle")
        bundle_root = output_dir / stem
        bundle_root.mkdir(parents=True, exist_ok=True)

        generator = UnityURP2DNativePipelineGenerator()
        generator.generate(bundle_root)
        audit = generator.audit(bundle_root)

        vat_result = bake_cloth_vat(
            bundle_root / "VAT",
            config=XPBDVATBakeConfig(
                asset_name=stem,
                frame_count=int(context.get("frame_count", 24)),
                fps=int(context.get("fps", 24)),
                particle_budget=int(context.get("particle_budget", 256)),
                displacement_scale=float(context.get("displacement_scale", 1.0)),
                include_preview=bool(context.get("include_preview", True)),
            ),
        )

        plugin_source = bundle_root / "Editor" / "MathArtImporter.cs"
        shader_source = bundle_root / "Shaders" / "MathArtVATLit.shader"
        secondary_postprocessor = bundle_root / "Editor" / "MathArtSecondaryTexturePostprocessor.cs"
        vat_player = bundle_root / "Runtime" / "MathArtVATPlayer.cs"
        readme = bundle_root / "Docs" / "MATHART_UNITY_URP2D_README.md"

        return ArtifactManifest(
            artifact_family=ArtifactFamily.ENGINE_PLUGIN.value,
            backend_type=BackendType.URP2D_BUNDLE,
            outputs={
                "plugin_source": str(plugin_source),
                "shader_source": str(shader_source),
                "secondary_texture_postprocessor": str(secondary_postprocessor),
                "vat_player": str(vat_player),
                "vat_manifest": str(vat_result.manifest_path),
                "vat_position_tex": str(vat_result.texture_path),
                "vat_preview": str(vat_result.preview_path) if vat_result.preview_path else "",
                "docs": str(readme),
            },
            metadata={
                "engine": "Unity",
                "plugin_type": "URP_2D_Bundle",
                "supports_vat": True,
                "secondary_textures": ["normal", "mask", "depth"],
                "frame_count": int(vat_result.manifest.frame_count),
                "vertex_count": int(vat_result.manifest.vertex_count),
                "vat_backend": vat_result.manifest.source_backend,
                "unity_native_audit": audit.to_dict(),
                "lane": "engine_export",
            },
        )


@register_backend(
    BackendType.DIMENSION_UPLIFT_MESH,
    display_name="Dimension Uplift Mesh",
    version="2.0.0",
    artifact_families=(
        ArtifactFamily.MESH_OBJ.value,
        ArtifactFamily.MATERIAL_BUNDLE.value,
        ArtifactFamily.CEL_SHADING.value,
    ),
    capabilities=(
        BackendCapability.MESH_EXPORT,
        BackendCapability.SHADER_EXPORT,
    ),
    input_requirements=("sdf_field", "depth_params"),
    dependencies=(BackendType.MOTION_2D,),
    session_origin="SESSION-066",
)
class DimensionUpliftMeshBackend:
    """Canonical dimension uplift slot for 2.5D/3D evolution."""

    @property
    def name(self) -> str:
        return BackendType.DIMENSION_UPLIFT_MESH.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        base_dir = context.get("output_dir", "output")
        return ArtifactManifest(
            artifact_family=ArtifactFamily.MESH_OBJ.value,
            backend_type=BackendType.DIMENSION_UPLIFT_MESH,
            outputs={
                "mesh": context.get("mesh_path", f"{base_dir}/dimension_uplift.obj"),
                "material": context.get("material_path", f"{base_dir}/dimension_uplift_material.json"),
                "cel_shader": context.get("shader_path", f"{base_dir}/dimension_uplift_cel.hlsl"),
            },
            metadata={
                "vertex_count": context.get("vertex_count", 0),
                "face_count": context.get("face_count", 0),
                "mesh_strategy": context.get("mesh_strategy", "dual_contouring"),
                "lod_strategy": context.get("lod_strategy", "octree"),
                "lane": "dimension_uplift",
            },
        )


@register_backend(
    BackendType.ANTI_FLICKER_RENDER,
    display_name="Anti-Flicker Render",
    version="2.0.0",
    artifact_families=(
        ArtifactFamily.COMPOSITE.value,
        ArtifactFamily.VFX_FLIPBOOK.value,
    ),
    capabilities=(
        BackendCapability.VFX_EXPORT,
        BackendCapability.ANIMATION_EXPORT,
    ),
    input_requirements=("source_frames", "guide_channels"),
    dependencies=(BackendType.INDUSTRIAL_SPRITE,),
    session_origin="SESSION-066",
)
class AntiFlickerRenderBackend:
    """Encapsulates the repository anti-flicker production recipe."""

    @property
    def name(self) -> str:
        return BackendType.ANTI_FLICKER_RENDER.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        base_dir = context.get("output_dir", "output")
        stem = context.get("name", "anti_flicker")
        return ArtifactManifest(
            artifact_family=ArtifactFamily.COMPOSITE.value,
            backend_type=BackendType.ANTI_FLICKER_RENDER,
            outputs={
                "workflow": context.get("workflow_path", f"{base_dir}/{stem}_workflow.json"),
                "preview": context.get("preview_path", f"{base_dir}/{stem}_preview.gif"),
                "report": context.get("report_path", f"{base_dir}/{stem}_temporal_report.json"),
            },
            metadata={
                "strategy": "sparse_ctrl_plus_ebsynth",
                "guide_channels": context.get(
                    "guide_channels",
                    ["normal", "depth", "mask", "motion_vector", "identity_ref"],
                ),
                "keyframe_interval": context.get("keyframe_interval", 4),
                "lane": "temporal_consistency",
            },
        )


@register_backend(
    BackendType.WFC_TILEMAP,
    display_name="WFC Tilemap Generator",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.LEVEL_TILEMAP.value,
        ArtifactFamily.LEVEL_WFC.value,
    ),
    capabilities=(BackendCapability.LEVEL_EXPORT,),
    input_requirements=("tile_rules", "level_params"),
    session_origin="SESSION-064",
)
class WFCTilemapBackend:
    @property
    def name(self) -> str:
        return BackendType.WFC_TILEMAP.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        return ArtifactManifest(
            artifact_family=ArtifactFamily.LEVEL_TILEMAP.value,
            backend_type=BackendType.WFC_TILEMAP,
            outputs={
                "tilemap_json": context.get("output_path", "output/tilemap.json"),
            },
            metadata={
                "width": context.get("width", 18),
                "height": context.get("height", 7),
                "tile_count": context.get("tile_count", 0),
            },
        )


@register_backend(
    BackendType.PHYSICS_VFX,
    display_name="Physics VFX Pipeline",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.VFX_FLIPBOOK.value,
        ArtifactFamily.VFX_FLOWMAP.value,
        ArtifactFamily.VAT_BUNDLE.value,
    ),
    capabilities=(
        BackendCapability.VFX_EXPORT,
        BackendCapability.VAT_EXPORT,
    ),
    input_requirements=("physics_sim", "vfx_params"),
    session_origin="SESSION-064",
)
class PhysicsVFXBackend:
    @property
    def name(self) -> str:
        return BackendType.PHYSICS_VFX.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        return ArtifactManifest(
            artifact_family=ArtifactFamily.VFX_FLIPBOOK.value,
            backend_type=BackendType.PHYSICS_VFX,
            outputs={
                "atlas": context.get("output_path", "output/vfx_atlas.png"),
            },
            metadata={
                "frame_count": context.get("frame_count", 16),
                "atlas_width": context.get("atlas_width", 512),
                "atlas_height": context.get("atlas_height", 512),
            },
        )


@register_backend(
    BackendType.CEL_SHADING,
    display_name="Cel Shading Pipeline",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.CEL_SHADING.value,
        ArtifactFamily.SHADER_HLSL.value,
    ),
    capabilities=(BackendCapability.SHADER_EXPORT,),
    input_requirements=("mesh", "cel_params"),
    dependencies=(BackendType.DIMENSION_UPLIFT_MESH,),
    session_origin="SESSION-064",
)
class CelShadingBackend:
    @property
    def name(self) -> str:
        return BackendType.CEL_SHADING.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        return ArtifactManifest(
            artifact_family=ArtifactFamily.CEL_SHADING.value,
            backend_type=BackendType.CEL_SHADING,
            outputs={
                "shader_source": context.get("output_path", "output/cel.hlsl"),
            },
            metadata={
                "shader_type": "cel",
                "outline_method": context.get("outline_method", "sobel_depth"),
            },
        )


@register_backend(
    BackendType.KNOWLEDGE_DISTILL,
    display_name="Knowledge Distillation Engine",
    version="1.0.0",
    artifact_families=(ArtifactFamily.KNOWLEDGE_RULES.value,),
    capabilities=(BackendCapability.KNOWLEDGE_DISTILL,),
    input_requirements=("evolution_state",),
    session_origin="SESSION-064",
)
class KnowledgeDistillBackend:
    @property
    def name(self) -> str:
        return BackendType.KNOWLEDGE_DISTILL.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        return ArtifactManifest(
            artifact_family=ArtifactFamily.KNOWLEDGE_RULES.value,
            backend_type=BackendType.KNOWLEDGE_DISTILL,
            outputs={
                "rules_file": context.get("output_path", "knowledge/rules.json"),
            },
            metadata={
                "rule_count": context.get("rule_count", 0),
            },
        )


__all__ = [
    "Motion2DBackend",
    "IndustrialSpriteBackend",
    "URP2DBundleBackend",
    "DimensionUpliftMeshBackend",
    "AntiFlickerRenderBackend",
    "WFCTilemapBackend",
    "PhysicsVFXBackend",
    "CelShadingBackend",
    "KnowledgeDistillBackend",
]
