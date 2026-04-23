"""Backend type system for Golden Path registry hardening.

SESSION-066 introduces a strong ``BackendType`` enum so artifact manifests,
registries, and orchestration reports stop drifting across historical naming
variants. The design deliberately keeps a compatibility alias layer so older
strings such as ``dimension_uplift`` or ``unity_urp_2d`` still resolve to the
new canonical backend types.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class BackendType(str, Enum):
    """Canonical backend types used by the registry and manifest contract."""

    MOTION_2D = "motion_2d"
    URP2D_BUNDLE = "urp2d_bundle"
    INDUSTRIAL_SPRITE = "industrial_sprite"
    DIMENSION_UPLIFT_MESH = "dimension_uplift_mesh"
    ANTI_FLICKER_RENDER = "anti_flicker_render"
    WFC_TILEMAP = "wfc_tilemap"
    PHYSICS_VFX = "physics_vfx"
    CEL_SHADING = "cel_shading"
    KNOWLEDGE_DISTILL = "knowledge_distill"
    COMPOSITE = "composite"
    LEGACY = "legacy"
    UNIFIED_MOTION = "unified_motion"
    MICROKERNEL = "microkernel"
    PHYSICS_3D = "physics_3d"
    # SESSION-075 (P1-DISTILL-1B): Taichi XPBD benchmark backend.
    TAICHI_XPBD = "taichi_xpbd"
    # SESSION-083 (P1-B4-1): registry-native RL rollout / training backend.
    RL_TRAINING = "rl_training"
    # SESSION-074 (P1-MIGRATE-2): Evolution bridge backend types.
    # Each legacy EvolutionBridge is promoted to a first-class backend
    # identity so the registry can discover, route, and CI-guard them
    # without any hardcoded bridge lists in the orchestrator.
    EVOLUTION_XPBD = "evolution_xpbd"
    EVOLUTION_FLUID_VFX = "evolution_fluid_vfx"
    EVOLUTION_BREAKWALL = "evolution_breakwall"
    EVOLUTION_MORPHOLOGY = "evolution_morphology"
    EVOLUTION_WFC = "evolution_wfc"
    EVOLUTION_MOTION_2D = "evolution_motion_2d"
    EVOLUTION_DIM_UPLIFT = "evolution_dim_uplift"
    EVOLUTION_GAIT_BLEND = "evolution_gait_blend"
    EVOLUTION_JAKOBSEN = "evolution_jakobsen"
    EVOLUTION_TERRAIN = "evolution_terrain"
    EVOLUTION_NEURAL_RENDER = "evolution_neural_render"
    EVOLUTION_VISUAL_REGRESSION = "evolution_visual_regression"
    EVOLUTION_URP2D = "evolution_urp2d"
    EVOLUTION_PHASE3_PHYSICS = "evolution_phase3_physics"
    EVOLUTION_INDUSTRIAL_SKIN = "evolution_industrial_skin"
    EVOLUTION_LOCOMOTION_CNS = "evolution_locomotion_cns"
    EVOLUTION_STATE_MACHINE = "evolution_state_machine"
    EVOLUTION_RUNTIME_DISTILL = "evolution_runtime_distill"
    EVOLUTION_CONTRACT = "evolution_contract"
    EVOLUTION_ENV_CLOSEDLOOP = "evolution_env_closedloop"
    EVOLUTION_RESEARCH = "evolution_research"
    # SESSION-076 (P1-DISTILL-3): Physics-gait distillation backend.
    EVOLUTION_PHYSICS_GAIT_DISTILL = "evolution_physics_gait_distill"
    # SESSION-078 (P1-DISTILL-4): Cognitive science / biological motion distillation backend.
    EVOLUTION_COGNITIVE_DISTILL = "evolution_cognitive_distill"
    # SESSION-089 (P1-INDUSTRIAL-34C): Dead Cells-style orthographic pixel
    # render backend for 3D→2D dimension-reduction rendering.
    ORTHOGRAPHIC_PIXEL_RENDER = "orthographic_pixel_render"
    # SESSION-091 (P1-AI-2E): Motion-adaptive keyframe planning backend.
    # Computes per-frame nonlinearity scores from UMR clips and outputs
    # adaptive keyframe plans with SparseCtrl end_percent mapping.
    MOTION_ADAPTIVE_KEYFRAME = "motion_adaptive_keyframe"
    # SESSION-106 (P1-B1-1): Physical ribbon mesh extractor backend.
    # Produces 3D ribbon meshes from secondary animation chain snapshots
    # (cape, hair, cloth) for orthographic pixel rendering.
    PHYSICAL_RIBBON = "physical_ribbon"
    # SESSION-107 (P1-AI-1): Math-to-AI bridge exporters.
    CONTROLNET_NORMAL_EXPORT = "controlnet_normal_export"
    CONTROLNET_DEPTH_EXPORT = "controlnet_depth_export"
    FRAME_SEQUENCE_EXPORT = "frame_sequence_export"
    # SESSION-109 (P1-ARCH-6): Tensor-based level topology extractor backend.
    # Consumes a logical tile-id grid (typically produced upstream by
    # WFC_TILEMAP) and emits a strongly-typed LEVEL_TOPOLOGY artifact
    # carrying SemanticAnchors, TraversalLanes and TopologyTensors.
    LEVEL_TOPOLOGY = "level_topology"
    # SESSION-118 (P1-HUMAN-31C): Pseudo-3D paper-doll / mesh-shell
    # deformation backend using tensorized dual quaternion skinning.
    PSEUDO_3D_SHELL = "pseudo_3d_shell"
    # SESSION-119 (P1-NEW-2): Tensorized Gray-Scott reaction-diffusion
    # organic texture backend (Karl Sims / Pearson xmorphia).  Produces
    # MATERIAL_BUNDLE artifacts with albedo/normal/height/mask channels.
    REACTION_DIFFUSION = "reaction_diffusion"
    # SESSION-124 (P2-UNITY-2DANIM-1): Unity 2D native animation format
    # zero-dependency direct export backend.  Produces .anim, .controller,
    # and .meta files from projected Clip2D data using tensorized coordinate
    # transformation, Euler angle unwrapping, and high-throughput string
    # template assembly (no PyYAML dependency).
    UNITY_2D_ANIM = "unity_2d_anim"
    # SESSION-125 (P2-SPINE-PREVIEW-1): Spine JSON tensorized FK preview
    # backend. Reads exported Spine JSON, solves world transforms in batch,
    # and emits headless MP4 / GIF diagnostics without any GUI dependency.
    SPINE_PREVIEW = "spine_preview"
    # SESSION-128 (P0-SESSION-127-CORE-CONSTRAINTS): Centralized archive
    # delivery backend.  Collects artifacts from all upstream stages into
    # a unified archive/ directory with batch_summary.json index.
    ARCHIVE_DELIVERY = "archive_delivery"
    # SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH): ComfyUI
    # dynamic payload injection and headless render backend.  Provides
    # end-to-end BFF mutation, ephemeral upload, WebSocket telemetry,
    # and VRAM garbage collection for production AI rendering.
    COMFYUI_RENDER = "comfyui_render"

    # SESSION-152 (P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT): Non-intrusive
    # sidecar audit backend.  Reads the knowledge bus state and pipeline
    # parameter flow to produce a full-chain provenance audit trail.
    # Design: OpenLineage-aligned lineage events + XAI audit trail.
    PROVENANCE_AUDIT = "provenance_audit"


_BACKEND_ALIASES: dict[str, str] = {
    # Canonical values
    BackendType.MOTION_2D.value: BackendType.MOTION_2D.value,
    BackendType.URP2D_BUNDLE.value: BackendType.URP2D_BUNDLE.value,
    BackendType.INDUSTRIAL_SPRITE.value: BackendType.INDUSTRIAL_SPRITE.value,
    BackendType.DIMENSION_UPLIFT_MESH.value: BackendType.DIMENSION_UPLIFT_MESH.value,
    BackendType.ANTI_FLICKER_RENDER.value: BackendType.ANTI_FLICKER_RENDER.value,
    BackendType.WFC_TILEMAP.value: BackendType.WFC_TILEMAP.value,
    BackendType.PHYSICS_VFX.value: BackendType.PHYSICS_VFX.value,
    BackendType.CEL_SHADING.value: BackendType.CEL_SHADING.value,
    BackendType.KNOWLEDGE_DISTILL.value: BackendType.KNOWLEDGE_DISTILL.value,
    BackendType.COMPOSITE.value: BackendType.COMPOSITE.value,
    BackendType.LEGACY.value: BackendType.LEGACY.value,
    BackendType.UNIFIED_MOTION.value: BackendType.UNIFIED_MOTION.value,
    BackendType.MICROKERNEL.value: BackendType.MICROKERNEL.value,
    BackendType.PHYSICS_3D.value: BackendType.PHYSICS_3D.value,
    BackendType.TAICHI_XPBD.value: BackendType.TAICHI_XPBD.value,
    BackendType.RL_TRAINING.value: BackendType.RL_TRAINING.value,
    BackendType.EVOLUTION_PHYSICS_GAIT_DISTILL.value: BackendType.EVOLUTION_PHYSICS_GAIT_DISTILL.value,
    BackendType.EVOLUTION_COGNITIVE_DISTILL.value: BackendType.EVOLUTION_COGNITIVE_DISTILL.value,
    # Historical / user-requested variants
    "dimension_uplift": BackendType.DIMENSION_UPLIFT_MESH.value,
    "dimension_uplift_bundle": BackendType.DIMENSION_UPLIFT_MESH.value,
    "unity_urp_2d": BackendType.URP2D_BUNDLE.value,
    "unity_urp_2d_bundle": BackendType.URP2D_BUNDLE.value,
    "unity_urp2d_bundle": BackendType.URP2D_BUNDLE.value,
    "unity_urp_native": BackendType.URP2D_BUNDLE.value,
    "industrial_sprite_bundle": BackendType.INDUSTRIAL_SPRITE.value,
    "industrial_renderer": BackendType.INDUSTRIAL_SPRITE.value,
    "breakwall": BackendType.ANTI_FLICKER_RENDER.value,
    "sparse_ctrl": BackendType.ANTI_FLICKER_RENDER.value,
    "anti_flicker": BackendType.ANTI_FLICKER_RENDER.value,
    "motion_trunk": BackendType.UNIFIED_MOTION.value,
    "unified_motion_trunk": BackendType.UNIFIED_MOTION.value,
    "motion_lane_registry": BackendType.UNIFIED_MOTION.value,
    # SESSION-071: P1-XPBD-3 — 3D XPBD physics backend aliases
    "xpbd_3d": BackendType.PHYSICS_3D.value,
    "physics3d": BackendType.PHYSICS_3D.value,
    "physics_xpbd_3d": BackendType.PHYSICS_3D.value,
    # SESSION-075 (P1-DISTILL-1B): Taichi XPBD benchmark backend aliases
    "taichi_benchmark": BackendType.TAICHI_XPBD.value,
    "taichi_cloth": BackendType.TAICHI_XPBD.value,
    # SESSION-083 (P1-B4-1): RL training backend aliases
    "imitation_rl": BackendType.RL_TRAINING.value,
    "locomotion_rl": BackendType.RL_TRAINING.value,
    # SESSION-074 (P1-MIGRATE-2): Evolution bridge backend aliases
    "xpbd_evolution": BackendType.EVOLUTION_XPBD.value,
    "fluid_vfx_evolution": BackendType.EVOLUTION_FLUID_VFX.value,
    "breakwall_evolution": BackendType.EVOLUTION_BREAKWALL.value,
    "smooth_morphology_evolution": BackendType.EVOLUTION_MORPHOLOGY.value,
    "constraint_wfc_evolution": BackendType.EVOLUTION_WFC.value,
    "motion_2d_evolution": BackendType.EVOLUTION_MOTION_2D.value,
    "dimension_uplift_evolution": BackendType.EVOLUTION_DIM_UPLIFT.value,
    "gait_blend_evolution": BackendType.EVOLUTION_GAIT_BLEND.value,
    "jakobsen_evolution": BackendType.EVOLUTION_JAKOBSEN.value,
    "terrain_sensor_evolution": BackendType.EVOLUTION_TERRAIN.value,
    "neural_rendering_evolution": BackendType.EVOLUTION_NEURAL_RENDER.value,
    "visual_regression_evolution": BackendType.EVOLUTION_VISUAL_REGRESSION.value,
    "unity_urp_2d_evolution": BackendType.EVOLUTION_URP2D.value,
    "phase3_physics_evolution": BackendType.EVOLUTION_PHASE3_PHYSICS.value,
    "industrial_skin_evolution": BackendType.EVOLUTION_INDUSTRIAL_SKIN.value,
    "locomotion_cns_evolution": BackendType.EVOLUTION_LOCOMOTION_CNS.value,
    "state_machine_evolution": BackendType.EVOLUTION_STATE_MACHINE.value,
    "runtime_distill_evolution": BackendType.EVOLUTION_RUNTIME_DISTILL.value,
    "contract_evolution": BackendType.EVOLUTION_CONTRACT.value,
    "env_closedloop_evolution": BackendType.EVOLUTION_ENV_CLOSEDLOOP.value,
    "research_evolution": BackendType.EVOLUTION_RESEARCH.value,
    # SESSION-076 (P1-DISTILL-3): Physics-gait distillation backend aliases
    "physics_gait_distill": BackendType.EVOLUTION_PHYSICS_GAIT_DISTILL.value,
    "physics_gait_evolution": BackendType.EVOLUTION_PHYSICS_GAIT_DISTILL.value,
    "gait_distill": BackendType.EVOLUTION_PHYSICS_GAIT_DISTILL.value,
    # SESSION-078 (P1-DISTILL-4): Cognitive distillation backend aliases
    "cognitive_distill": BackendType.EVOLUTION_COGNITIVE_DISTILL.value,
    "cognitive_science_distill": BackendType.EVOLUTION_COGNITIVE_DISTILL.value,
    "biomotion_distill": BackendType.EVOLUTION_COGNITIVE_DISTILL.value,
    # SESSION-089 (P1-INDUSTRIAL-34C): Orthographic pixel render backend aliases
    BackendType.ORTHOGRAPHIC_PIXEL_RENDER.value: BackendType.ORTHOGRAPHIC_PIXEL_RENDER.value,
    "ortho_pixel": BackendType.ORTHOGRAPHIC_PIXEL_RENDER.value,
    "dead_cells_render": BackendType.ORTHOGRAPHIC_PIXEL_RENDER.value,
    "orthographic_render": BackendType.ORTHOGRAPHIC_PIXEL_RENDER.value,
    "3d_to_2d_pixel": BackendType.ORTHOGRAPHIC_PIXEL_RENDER.value,
    # SESSION-091 (P1-AI-2E): Motion-adaptive keyframe planning backend aliases
    BackendType.MOTION_ADAPTIVE_KEYFRAME.value: BackendType.MOTION_ADAPTIVE_KEYFRAME.value,
    "adaptive_keyframe": BackendType.MOTION_ADAPTIVE_KEYFRAME.value,
    "keyframe_planner": BackendType.MOTION_ADAPTIVE_KEYFRAME.value,
    "motion_keyframe": BackendType.MOTION_ADAPTIVE_KEYFRAME.value,
    # SESSION-106 (P1-B1-1): Physical ribbon mesh extractor backend aliases
    BackendType.PHYSICAL_RIBBON.value: BackendType.PHYSICAL_RIBBON.value,
    "ribbon_mesh": BackendType.PHYSICAL_RIBBON.value,
    "cape_mesh": BackendType.PHYSICAL_RIBBON.value,
    "hair_mesh": BackendType.PHYSICAL_RIBBON.value,
    "secondary_chain_mesh": BackendType.PHYSICAL_RIBBON.value,
    # SESSION-107 (P1-AI-1): Math-to-AI bridge exporter aliases
    BackendType.CONTROLNET_NORMAL_EXPORT.value: BackendType.CONTROLNET_NORMAL_EXPORT.value,
    BackendType.CONTROLNET_DEPTH_EXPORT.value: BackendType.CONTROLNET_DEPTH_EXPORT.value,
    BackendType.FRAME_SEQUENCE_EXPORT.value: BackendType.FRAME_SEQUENCE_EXPORT.value,
    "normal_map_exporter": BackendType.CONTROLNET_NORMAL_EXPORT.value,
    "depth_map_exporter": BackendType.CONTROLNET_DEPTH_EXPORT.value,
    "controlnet_normal": BackendType.CONTROLNET_NORMAL_EXPORT.value,
    "controlnet_depth": BackendType.CONTROLNET_DEPTH_EXPORT.value,
    "vhs_sequence_exporter": BackendType.FRAME_SEQUENCE_EXPORT.value,
    # SESSION-109 (P1-ARCH-6): Level topology extractor backend aliases
    BackendType.LEVEL_TOPOLOGY.value: BackendType.LEVEL_TOPOLOGY.value,
    "topology_extractor": BackendType.LEVEL_TOPOLOGY.value,
    "level_topology_extractor": BackendType.LEVEL_TOPOLOGY.value,
    "recast_topology": BackendType.LEVEL_TOPOLOGY.value,
    "dual_grid_topology": BackendType.LEVEL_TOPOLOGY.value,
    # SESSION-118 (P1-HUMAN-31C): Pseudo-3D shell backend aliases
    BackendType.PSEUDO_3D_SHELL.value: BackendType.PSEUDO_3D_SHELL.value,
    "pseudo3d_shell": BackendType.PSEUDO_3D_SHELL.value,
    "paper_doll_shell": BackendType.PSEUDO_3D_SHELL.value,
    "dqs_mesh_shell": BackendType.PSEUDO_3D_SHELL.value,
    "mesh_shell_dqs": BackendType.PSEUDO_3D_SHELL.value,
    # SESSION-119 (P1-NEW-2): Reaction-diffusion texture backend aliases
    BackendType.REACTION_DIFFUSION.value: BackendType.REACTION_DIFFUSION.value,
    "gray_scott": BackendType.REACTION_DIFFUSION.value,
    "reaction_diffusion_texture": BackendType.REACTION_DIFFUSION.value,
    "organic_texture": BackendType.REACTION_DIFFUSION.value,
    "turing_pattern": BackendType.REACTION_DIFFUSION.value,
    # SESSION-124 (P2-UNITY-2DANIM-1): Unity 2D native animation backend aliases
    BackendType.UNITY_2D_ANIM.value: BackendType.UNITY_2D_ANIM.value,
    "unity_native_anim": BackendType.UNITY_2D_ANIM.value,
    "unity_2d_animation": BackendType.UNITY_2D_ANIM.value,
    "unity_anim_export": BackendType.UNITY_2D_ANIM.value,
    "anim_exporter": BackendType.UNITY_2D_ANIM.value,
    # SESSION-125 (P2-SPINE-PREVIEW-1): Spine preview backend aliases
    BackendType.SPINE_PREVIEW.value: BackendType.SPINE_PREVIEW.value,
    "animation_preview": BackendType.SPINE_PREVIEW.value,
    "spine_json_preview": BackendType.SPINE_PREVIEW.value,
    "spine_preview_backend": BackendType.SPINE_PREVIEW.value,
    "spine_headless_preview": BackendType.SPINE_PREVIEW.value,
    # SESSION-128 (P0-SESSION-127-CORE-CONSTRAINTS): Archive delivery backend aliases
    BackendType.ARCHIVE_DELIVERY.value: BackendType.ARCHIVE_DELIVERY.value,
    "archive_backend": BackendType.ARCHIVE_DELIVERY.value,
    "centralized_archive": BackendType.ARCHIVE_DELIVERY.value,
    "data_mesh_delivery": BackendType.ARCHIVE_DELIVERY.value,
    # SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH): ComfyUI render backend aliases
    BackendType.COMFYUI_RENDER.value: BackendType.COMFYUI_RENDER.value,
    "comfyui_api_render": BackendType.COMFYUI_RENDER.value,
    "comfy_render": BackendType.COMFYUI_RENDER.value,
    "comfyui_headless": BackendType.COMFYUI_RENDER.value,
    "bff_render": BackendType.COMFYUI_RENDER.value,
    # SESSION-152 (P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT): Provenance audit backend aliases
    BackendType.PROVENANCE_AUDIT.value: BackendType.PROVENANCE_AUDIT.value,
    "knowledge_audit": BackendType.PROVENANCE_AUDIT.value,
    "provenance_tracker": BackendType.PROVENANCE_AUDIT.value,
    "lineage_audit": BackendType.PROVENANCE_AUDIT.value,
}


def backend_type_value(
    value: str | BackendType | None,
    *,
    allow_unknown: bool = True,
) -> str:
    """Return a canonical backend type string.

    Known aliases are normalized to the canonical ``BackendType`` value. Unknown
    strings are preserved by default for backward compatibility with ad-hoc test
    backends and experimental local plugins.
    """
    if value is None:
        return ""
    if isinstance(value, BackendType):
        return value.value
    text = str(value).strip()
    if not text:
        return ""
    normalized = text.lower().replace("-", "_")
    resolved = _BACKEND_ALIASES.get(normalized)
    if resolved is not None:
        return resolved
    if allow_unknown:
        return normalized
    raise ValueError(f"Unknown backend type: {value!r}")


def parse_backend_type(
    value: str | BackendType | None,
    *,
    allow_unknown: bool = True,
) -> BackendType | str:
    """Parse a backend value into ``BackendType`` when possible."""
    canonical = backend_type_value(value, allow_unknown=allow_unknown)
    if not canonical:
        return canonical
    try:
        return BackendType(canonical)
    except ValueError:
        if allow_unknown:
            return canonical
        raise


def known_backend_types() -> tuple[str, ...]:
    """Return all canonical backend type values."""
    return tuple(member.value for member in BackendType)


def backend_alias_map() -> dict[str, str]:
    """Return a copy of the alias map for reporting and audits."""
    return dict(_BACKEND_ALIASES)


def is_known_backend_type(value: str | BackendType | None) -> bool:
    """Return whether the value resolves to a canonical backend enum."""
    if value is None:
        return False
    canonical = backend_type_value(value, allow_unknown=True)
    return canonical in known_backend_types()


__all__ = [
    "BackendType",
    "backend_alias_map",
    "backend_type_value",
    "is_known_backend_type",
    "known_backend_types",
    "parse_backend_type",
]
