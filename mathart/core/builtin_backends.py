"""Built-in export backends for the Golden Path microkernel.

SESSION-066 hardens the registry inventory around canonical backend types while
keeping historical names compatible through ``BackendType`` alias resolution.
The philosophy is to register stable "slots" first and let old or future
pipelines plug into these slots without trunk edits.
"""
from __future__ import annotations

import logging
from typing import Any

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
        ArtifactFamily.SPRITE_SHEET.value,
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
        return ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SHEET.value,
            backend_type=BackendType.MOTION_2D,
            outputs={
                "spritesheet": context.get("output_path", "output/motion_2d_sprites.png"),
            },
            metadata={
                "frame_count": context.get("frame_count", 8),
                "frame_width": context.get("frame_width", 64),
                "frame_height": context.get("frame_height", 64),
                "lane": "animation_2d",
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
        base_dir = context.get("output_dir", "output")
        return ArtifactManifest(
            artifact_family=ArtifactFamily.ENGINE_PLUGIN.value,
            backend_type=BackendType.URP2D_BUNDLE,
            outputs={
                "plugin_source": context.get("plugin_path", f"{base_dir}/unity_mathart_bundle.cs"),
                "shader_source": context.get("shader_path", f"{base_dir}/MathArtLitSprite.hlsl"),
                "vat_manifest": context.get("vat_manifest_path", f"{base_dir}/vat_bake_manifest.json"),
            },
            metadata={
                "engine": "Unity",
                "plugin_type": "URP_2D_Bundle",
                "supports_vat": True,
                "secondary_textures": ["normal", "mask", "depth"],
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
