"""Built-in export backends for the Golden Path microkernel.

SESSION-066 hardens the registry inventory around canonical backend types while
keeping historical names compatible through ``BackendType`` alias resolution.

SESSION-068 upgrades ``IndustrialSpriteBackend`` and ``AntiFlickerRenderBackend``
from placeholder stubs to **real execution backends** with:

1. **Backend-owned ``validate_config()``** — all parameter parsing and contract
   validation is physically sunk into the backend Adapter, keeping the CLI Port
   absolutely ignorant of business logic (Hexagonal Architecture / Ports &
   Adapters, Alistair Cockburn 2005).

2. **Polymorphic Manifest payloads** — the anti-flicker backend emits a
   ``frame_sequence`` time-series contract (inspired by OpenTimelineIO / VFX
   Reference Platform), while the industrial backend emits a ``texture_channels``
   material-bundle contract (inspired by MaterialX / glTF PBR).

3. **Real execution wiring** — ``IndustrialSpriteBackend`` invokes
   ``render_character_maps_industrial`` + ``generate_mathart_bundle``;
   ``AntiFlickerRenderBackend`` invokes ``HeadlessNeuralRenderPipeline.run()``.

Architecture Discipline
-----------------------
- The CLI (``cli.py``) NEVER inspects backend-specific parameters.
- Config dicts are passed through as opaque ``kwargs`` and consumed only by
  ``validate_config()`` inside each backend.
- Manifest ``metadata["payload"]`` carries the polymorphic structured data:
  ``frame_sequence`` for temporal assets, ``texture_channels`` for material bundles.
"""
from __future__ import annotations

import json
import logging
import math
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
#  Motion 2D Backend (unchanged from SESSION-067)
# ═══════════════════════════════════════════════════════════════════════════


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
        output_dir = Path(context.get("output_dir", ".")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = context.get("name", "motion_2d")
        gait = str(context.get("gait", "biped_walk"))
        frame_count = int(context.get("frame_count", context.get("n_frames", 24)))
        speed = float(context.get("speed", 1.0))

        from mathart.animation.motion_2d_pipeline import Motion2DPipeline

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


# ═══════════════════════════════════════════════════════════════════════════
#  Industrial Sprite Backend — SESSION-068 REAL EXECUTION WIRING
# ═══════════════════════════════════════════════════════════════════════════


# Default channel configuration for the industrial material bundle.
_INDUSTRIAL_DEFAULT_CHANNELS = (
    "albedo", "normal", "depth", "thickness", "roughness", "mask",
)

# Channel semantics following MaterialX / glTF PBR conventions.
_CHANNEL_SEMANTICS: dict[str, dict[str, str]] = {
    "albedo": {
        "description": "Base color (sRGB, premultiplied alpha)",
        "color_space": "sRGB",
        "engine_slot_unity": "_MainTex",
        "engine_slot_godot": "texture_albedo",
    },
    "normal": {
        "description": "Tangent-space normal map (R=X, G=Y, B=Z, [0,255]→[-1,1])",
        "color_space": "linear",
        "engine_slot_unity": "_NormalMap",
        "engine_slot_godot": "texture_normal",
    },
    "depth": {
        "description": "Pseudo-3D depth (grayscale, 0=far, 255=near)",
        "color_space": "linear",
        "engine_slot_unity": "_DepthMap",
        "engine_slot_godot": "texture_depth",
    },
    "thickness": {
        "description": "Material thickness for SSS (0=thin/translucent, 255=thick/opaque)",
        "color_space": "linear",
        "engine_slot_unity": "_ThicknessMap",
        "engine_slot_godot": "texture_thickness",
    },
    "roughness": {
        "description": "Surface roughness (0=smooth/specular, 255=rough/matte)",
        "color_space": "linear",
        "engine_slot_unity": "_RoughnessMap",
        "engine_slot_godot": "texture_roughness",
    },
    "mask": {
        "description": "Alpha mask (255=inside, 0=outside)",
        "color_space": "linear",
        "engine_slot_unity": "_MaskTex",
        "engine_slot_godot": "texture_mask",
    },
}


@register_backend(
    BackendType.INDUSTRIAL_SPRITE,
    display_name="Industrial Sprite Bundle",
    version="3.0.0",
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
    session_origin="SESSION-068",
)
class IndustrialSpriteBackend:
    """Real industrial sprite + multi-channel material bundle backend.

    SESSION-068 upgrades from placeholder to real execution:
    1. Invokes ``render_character_maps_industrial`` for analytical PBR maps.
    2. Packages output via ``MathArtBundle.save()`` for engine-ready delivery.
    3. Emits a ``texture_channels`` manifest payload following MaterialX /
       glTF PBR asset structure conventions.
    4. Implements ``validate_config()`` so all parameter validation is
       physically owned by this Adapter, not the CLI Port.
    """

    @property
    def name(self) -> str:
        return BackendType.INDUSTRIAL_SPRITE.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(self, config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize industrial rendering configuration.

        The CLI bus passes the raw config dict without inspection. This method
        is the single authority for parameter parsing and contract enforcement.

        Returns
        -------
        tuple[dict, list[str]]
            (validated_config, warnings)
        """
        warnings: list[str] = []
        validated = dict(config)

        # --- Material namespace ---
        material = validated.get("material", {})
        if not isinstance(material, dict):
            material = {}
            warnings.append("material config must be a dict; using defaults")
        validated["material"] = material

        # --- Render namespace ---
        render = validated.get("render", {})
        if not isinstance(render, dict):
            render = {}
            warnings.append("render config must be a dict; using defaults")

        width = int(render.get("width", validated.get("width", 64)))
        height = int(render.get("height", validated.get("height", 64)))
        if width < 8:
            warnings.append(f"render.width={width} too small; clamping to 8")
            width = 8
        if height < 8:
            warnings.append(f"render.height={height} too small; clamping to 8")
            height = 8
        render["width"] = width
        render["height"] = height
        validated["render"] = render

        # --- Export namespace ---
        export = validated.get("export", {})
        if not isinstance(export, dict):
            export = {}
        bundle_format = str(export.get("bundle_format", "mathart"))
        if bundle_format not in ("mathart", "gltf_pbr", "materialx"):
            warnings.append(
                f"export.bundle_format={bundle_format!r} unknown; defaulting to 'mathart'"
            )
            bundle_format = "mathart"
        export["bundle_format"] = bundle_format

        target_engine = str(export.get("target_engine", "generic"))
        if target_engine not in ("unity_urp_2d", "godot_4", "generic"):
            warnings.append(
                f"export.target_engine={target_engine!r} unknown; defaulting to 'generic'"
            )
            target_engine = "generic"
        export["target_engine"] = target_engine

        material_model = str(export.get("material_model", "toon_lit"))
        export["material_model"] = material_model
        validated["export"] = export

        # --- Channel selection ---
        channels = validated.get("channels", list(_INDUSTRIAL_DEFAULT_CHANNELS))
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",")]
        validated["channels"] = [
            ch for ch in channels if ch in _CHANNEL_SEMANTICS
        ]
        if not validated["channels"]:
            validated["channels"] = list(_INDUSTRIAL_DEFAULT_CHANNELS)
            warnings.append("No valid channels specified; using all defaults")

        return validated, warnings

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        from mathart.animation.skeleton import Skeleton
        from mathart.animation.parts import CharacterStyle
        from mathart.animation.presets import idle_animation
        from mathart.animation.industrial_renderer import render_character_maps_industrial
        from mathart.animation.engine_import_plugin import (
            MathArtBundle,
            extract_sdf_contour,
        )

        # --- Validate config (backend-owned, not CLI) ---
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[industrial_sprite] config warning: %s", w)

        output_dir = Path(validated.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = validated.get("name", "industrial_sprite")
        bundle_dir = output_dir / f"{stem}_bundle"

        render_cfg = validated.get("render", {})
        width = int(render_cfg.get("width", 64))
        height = int(render_cfg.get("height", 64))

        # Build skeleton + style + pose (use defaults for lightweight execution)
        skeleton = Skeleton.create_humanoid()
        style = CharacterStyle()
        pose = idle_animation(0.0)

        # --- Real industrial rendering ---
        aux_result = render_character_maps_industrial(
            skeleton=skeleton,
            pose=pose,
            style=style,
            width=width,
            height=height,
        )

        # --- Extract SDF contour for collider ---
        contour = extract_sdf_contour(
            skeleton=skeleton,
            pose=pose,
            style=style,
            width=width,
            height=height,
        )

        # --- Package as MathArtBundle ---
        bundle = MathArtBundle(
            name=stem,
            albedo=aux_result.albedo_image,
            normal_map=aux_result.normal_map_image,
            depth_map=aux_result.depth_map_image,
            thickness_map=aux_result.thickness_map_image,
            roughness_map=aux_result.roughness_map_image,
            mask=aux_result.mask_image,
            contour_points=contour,
            metadata={
                "width": width,
                "height": height,
                "source": "MarioTrickster-MathArt IndustrialSpriteBackend",
                "session": "SESSION-068",
            },
        )
        bundle_path = bundle.save(bundle_dir)

        # --- Build texture_channels payload (MaterialX / glTF PBR inspired) ---
        requested_channels = validated.get("channels", list(_INDUSTRIAL_DEFAULT_CHANNELS))
        texture_channels: dict[str, dict[str, Any]] = {}
        channel_file_map = {
            "albedo": "albedo.png",
            "normal": "normal.png",
            "depth": "depth.png",
            "thickness": "thickness.png",
            "roughness": "roughness.png",
            "mask": "mask.png",
        }
        for ch_name in requested_channels:
            ch_file = channel_file_map.get(ch_name)
            if ch_file and (bundle_path / ch_file).exists():
                semantics = _CHANNEL_SEMANTICS.get(ch_name, {})
                texture_channels[ch_name] = {
                    "path": str((bundle_path / ch_file).resolve()),
                    "dimensions": {"width": width, "height": height},
                    "bit_depth": 8,
                    "color_space": semantics.get("color_space", "linear"),
                    "engine_slot": {
                        "unity": semantics.get("engine_slot_unity", ""),
                        "godot": semantics.get("engine_slot_godot", ""),
                    },
                    "description": semantics.get("description", ""),
                }

        # Build flat outputs dict for manifest (required by schema validation)
        outputs: dict[str, str] = {}
        for ch_name, ch_info in texture_channels.items():
            outputs[ch_name] = ch_info["path"]
        manifest_json_path = bundle_path / "manifest.json"
        if manifest_json_path.exists():
            outputs["bundle_manifest"] = str(manifest_json_path.resolve())
        contour_json_path = bundle_path / "contour.json"
        if contour_json_path.exists():
            outputs["contour"] = str(contour_json_path.resolve())

        export_cfg = validated.get("export", {})

        return ArtifactManifest(
            artifact_family=ArtifactFamily.MATERIAL_BUNDLE.value,
            backend_type=BackendType.INDUSTRIAL_SPRITE,
            version="3.0.0",
            session_id=validated.get("session_id", "SESSION-068"),
            outputs=outputs,
            metadata={
                "channels": requested_channels,
                "bundle_kind": "industrial_sprite",
                "lane": "rendering_2d_aux",
                "renderer": "industrial_renderer",
                "bundle_format": export_cfg.get("bundle_format", "mathart"),
                "target_engine": export_cfg.get("target_engine", "generic"),
                "material_model": export_cfg.get("material_model", "toon_lit"),
                "dimensions": {"width": width, "height": height},
                "contour_point_count": len(contour),
                "render_metadata": aux_result.metadata,
                # --- Polymorphic payload: texture_channels (MaterialX/glTF PBR) ---
                "payload": {
                    "texture_channels": texture_channels,
                    "bundle_path": str(bundle_path.resolve()),
                    "contour_available": len(contour) > 0,
                },
            },
            quality_metrics={
                "channel_count": float(len(texture_channels)),
                "contour_point_count": float(len(contour)),
                "inside_pixel_coverage": float(
                    aux_result.metadata.get("inside_pixel_count", 0)
                ) / max(width * height, 1),
            },
            tags=["industrial", "material_bundle", "pbr", "session-068"],
        )


# ═══════════════════════════════════════════════════════════════════════════
#  URP2D Bundle Backend (unchanged from SESSION-067)
# ═══════════════════════════════════════════════════════════════════════════


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
        from mathart.animation.unity_urp_native import (
            UnityURP2DNativePipelineGenerator,
            XPBDVATBakeConfig,
            bake_cloth_vat,
        )

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


# ═══════════════════════════════════════════════════════════════════════════
#  Dimension Uplift Mesh Backend (unchanged)
# ═══════════════════════════════════════════════════════════════════════════


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
                "material": context.get("material_path", f"{base_dir}/dimension_uplift.mtl"),
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


# ═══════════════════════════════════════════════════════════════════════════
#  Anti-Flicker Render Backend — SESSION-068 REAL EXECUTION WIRING
# ═══════════════════════════════════════════════════════════════════════════


@register_backend(
    BackendType.ANTI_FLICKER_RENDER,
    display_name="Anti-Flicker Render",
    version="5.0.0",
    artifact_families=(
        ArtifactFamily.ANTI_FLICKER_REPORT.value,
        ArtifactFamily.VFX_FLIPBOOK.value,
    ),
    capabilities=(
        BackendCapability.VFX_EXPORT,
        BackendCapability.ANIMATION_EXPORT,
    ),
    input_requirements=("source_frames", "guide_channels"),
    dependencies=(BackendType.INDUSTRIAL_SPRITE,),
    session_origin="SESSION-108",
)
class AntiFlickerRenderBackend:
    """Production anti-flicker backend with optional live ComfyUI execution.

    SESSION-108 upgrades the backend so that the anti-flicker lane can:

    1. keep the historical offline-safe pipeline for tests and fallback work;
    2. execute real ComfyUI sequence payloads through ``ComfyUIClient``;
    3. protect 12GB VRAM machines with explicit chunking when frame counts exceed 16;
    4. surface progress events upward without polluting stdout.
    """

    MAX_SAFE_CONTEXT_WINDOW = 16
    DEFAULT_LIVE_PRESET = "sparsectrl_animatediff"
    SUPPORTED_LIVE_SEQUENCE_PRESETS = {
        "sparsectrl_animatediff",
        "normal_depth_dual_controlnet",
    }

    @property
    def name(self) -> str:
        return BackendType.ANTI_FLICKER_RENDER.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(self, config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize anti-flicker rendering configuration."""
        warnings: list[str] = []
        validated = dict(config)

        temporal = validated.get("temporal", {})
        if not isinstance(temporal, dict):
            temporal = {}
            warnings.append("temporal config must be a dict; using defaults")

        frame_count = int(temporal.get("frame_count", validated.get("frame_count", 8)))
        if frame_count < 2:
            warnings.append(f"temporal.frame_count={frame_count} too small; clamping to 2")
            frame_count = 2
        temporal["frame_count"] = frame_count

        fps = int(temporal.get("fps", validated.get("fps", 12)))
        temporal["fps"] = max(1, fps)

        window = int(temporal.get("window", 0))
        temporal["window"] = max(0, window)

        requested_chunk_size = int(temporal.get("chunk_size", self.MAX_SAFE_CONTEXT_WINDOW))
        if requested_chunk_size < 1:
            warnings.append("temporal.chunk_size must be >= 1; setting to 1")
            requested_chunk_size = 1
        if requested_chunk_size > self.MAX_SAFE_CONTEXT_WINDOW:
            warnings.append(
                f"temporal.chunk_size={requested_chunk_size} exceeds 12GB safe limit; clamping to {self.MAX_SAFE_CONTEXT_WINDOW}"
            )
            requested_chunk_size = self.MAX_SAFE_CONTEXT_WINDOW
        temporal["chunk_size"] = requested_chunk_size
        temporal["chunking_enabled"] = bool(frame_count > requested_chunk_size)
        validated["temporal"] = temporal

        guides = validated.get("guides", {})
        if not isinstance(guides, dict):
            guides = {}
            warnings.append("guides config must be a dict; using defaults")
        for guide_key in ("normal", "depth", "mask", "motion_vector"):
            guides.setdefault(guide_key, True)
        validated["guides"] = guides

        identity_lock = validated.get("identity_lock", {})
        if not isinstance(identity_lock, dict):
            identity_lock = {}
        weight = float(identity_lock.get("weight", 0.85))
        if weight < 0.0 or weight > 1.5:
            warnings.append(
                f"identity_lock.weight={weight} out of range [0, 1.5]; clamping"
            )
            weight = max(0.0, min(1.5, weight))
        identity_lock["weight"] = weight
        identity_lock.setdefault("enabled", True)
        validated["identity_lock"] = identity_lock

        comfyui = validated.get("comfyui", {})
        if not isinstance(comfyui, dict):
            comfyui = {}
            warnings.append("comfyui config must be a dict; using defaults")

        comfyui.setdefault("url", "http://localhost:8188")
        comfyui.setdefault("style_prompt", "high quality pixel art, detailed shading, game sprite")
        comfyui.setdefault("negative_prompt", "blurry, low quality, distorted, deformed")
        comfyui.setdefault("controlnet_normal_weight", 1.0)
        comfyui.setdefault("controlnet_depth_weight", 1.0)
        comfyui.setdefault("denoising_strength", 0.65)
        comfyui.setdefault("steps", 20)
        comfyui.setdefault("cfg_scale", 7.5)
        comfyui.setdefault("keyframe_interval", 4)
        comfyui.setdefault("normal_controlnet_model", "control_v11p_sd15_normalbae.pth")
        comfyui.setdefault("depth_controlnet_model", "control_v11f1p_sd15_depth.pth")
        comfyui.setdefault("sparsectrl_model", "v3_sd15_sparsectrl_rgb.ckpt")
        comfyui.setdefault("animatediff_model", "v3_sd15_mm.ckpt")
        comfyui.setdefault("animatediff_beta_schedule", "autoselect")
        comfyui.setdefault("context_window", min(frame_count, self.MAX_SAFE_CONTEXT_WINDOW))
        comfyui.setdefault("context_overlap", 4)
        comfyui.setdefault("sparsectrl_strength", 1.0)
        comfyui.setdefault("sparsectrl_end_percent", 0.5)
        comfyui.setdefault("model_checkpoint", "v1-5-pruned-emaonly.safetensors")
        comfyui.setdefault("live_execution", False)
        comfyui.setdefault("fail_fast_on_offline", bool(comfyui.get("live_execution", False)))
        comfyui.setdefault("connect_timeout", 5.0)
        comfyui.setdefault("ws_timeout", 600.0)
        comfyui.setdefault("max_execution_time", 1800.0)

        cn_normal_w = float(comfyui["controlnet_normal_weight"])
        if cn_normal_w < 0.5:
            warnings.append(
                f"comfyui.controlnet_normal_weight={cn_normal_w} < 0.5: geometry may not be fully locked"
            )
        cn_depth_w = float(comfyui["controlnet_depth_weight"])
        if cn_depth_w < 0.5:
            warnings.append(
                f"comfyui.controlnet_depth_weight={cn_depth_w} < 0.5: silhouette may drift"
            )

        keyframe_interval = int(comfyui.get("keyframe_interval", 4))
        if keyframe_interval < 1:
            warnings.append("comfyui.keyframe_interval must be >= 1; setting to 1")
            keyframe_interval = 1
        comfyui["keyframe_interval"] = keyframe_interval

        context_window = int(comfyui.get("context_window", self.MAX_SAFE_CONTEXT_WINDOW))
        if context_window < 1:
            warnings.append("comfyui.context_window must be >= 1; setting to 1")
            context_window = 1
        if context_window > self.MAX_SAFE_CONTEXT_WINDOW:
            warnings.append(
                f"comfyui.context_window={context_window} exceeds 12GB safe limit; clamping to {self.MAX_SAFE_CONTEXT_WINDOW}"
            )
            context_window = self.MAX_SAFE_CONTEXT_WINDOW
        comfyui["context_window"] = min(context_window, requested_chunk_size)

        overlap = int(comfyui.get("context_overlap", 4))
        comfyui["context_overlap"] = max(0, min(overlap, max(0, comfyui["context_window"] - 1)))
        validated["comfyui"] = comfyui

        preset = validated.get("preset", {})
        if not isinstance(preset, dict):
            preset = {}
            warnings.append("preset config must be a dict; using defaults")
        preset.setdefault(
            "name",
            comfyui.get(
                "workflow_preset_name",
                self.DEFAULT_LIVE_PRESET if comfyui.get("live_execution", False) else "dual_controlnet_ipadapter",
            ),
        )
        preset.setdefault("root", None)
        comfyui["workflow_preset_name"] = preset["name"]
        validated["preset"] = preset

        ebsynth = validated.get("ebsynth", {})
        if not isinstance(ebsynth, dict):
            ebsynth = {}
        ebsynth.setdefault("uniformity", 4000.0)
        patch_size = int(ebsynth.get("patch_size", 7))
        if patch_size % 2 == 0:
            warnings.append("ebsynth.patch_size must be odd; auto-correcting")
            patch_size += 1
        ebsynth["patch_size"] = patch_size
        validated["ebsynth"] = ebsynth

        render_width = int(validated.get("width", 64))
        render_height = int(validated.get("height", 64))
        if render_width < 8:
            warnings.append(f"width={render_width} too small; clamping to 8")
            render_width = 8
        if render_height < 8:
            warnings.append(f"height={render_height} too small; clamping to 8")
            render_height = 8
        validated["width"] = render_width
        validated["height"] = render_height

        return validated, warnings

    def _build_neural_config(self, validated: dict[str, Any], output_dir: Path, stem: str) -> Any:
        from mathart.animation.headless_comfy_ebsynth import NeuralRenderConfig

        comfyui_cfg = validated.get("comfyui", {})
        ebsynth_cfg = validated.get("ebsynth", {})
        identity_cfg = validated.get("identity_lock", {})
        preset_cfg = validated.get("preset", {})
        return NeuralRenderConfig(
            comfyui_url=str(comfyui_cfg.get("url", "http://localhost:8188")),
            style_prompt=str(comfyui_cfg.get("style_prompt", "high quality pixel art, detailed shading, game sprite")),
            negative_prompt=str(comfyui_cfg.get("negative_prompt", "blurry, low quality, distorted, deformed")),
            controlnet_normal_weight=float(comfyui_cfg.get("controlnet_normal_weight", 1.0)),
            controlnet_depth_weight=float(comfyui_cfg.get("controlnet_depth_weight", 1.0)),
            use_ip_adapter_identity=bool(identity_cfg.get("enabled", True)),
            ip_adapter_weight=float(identity_cfg.get("weight", 0.85)),
            ebsynth_uniformity=float(ebsynth_cfg.get("uniformity", 4000.0)),
            ebsynth_patch_size=int(ebsynth_cfg.get("patch_size", 7)),
            keyframe_interval=int(comfyui_cfg.get("keyframe_interval", 4)),
            sd_denoising_strength=float(comfyui_cfg.get("denoising_strength", 0.65)),
            sd_steps=int(comfyui_cfg.get("steps", 20)),
            sd_cfg_scale=float(comfyui_cfg.get("cfg_scale", 7.5)),
            workflow_preset_name=str(preset_cfg.get("name", self.DEFAULT_LIVE_PRESET)),
            output_dir=str(output_dir / stem),
        )

    def _execute_offline_pipeline(self, validated: dict[str, Any]) -> ArtifactManifest:
        from mathart.animation.skeleton import Skeleton
        from mathart.animation.parts import CharacterStyle
        from mathart.animation.presets import idle_animation
        from mathart.animation.comfyui_preset_manager import ComfyUIPresetManager
        from mathart.animation.headless_comfy_ebsynth import HeadlessNeuralRenderPipeline

        output_dir = Path(validated.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = validated.get("name", "anti_flicker")

        temporal = validated.get("temporal", {})
        frame_count = int(temporal.get("frame_count", 8))
        fps = int(temporal.get("fps", 12))
        comfyui_cfg = validated.get("comfyui", {})
        identity_cfg = validated.get("identity_lock", {})
        preset_cfg = validated.get("preset", {})
        render_width = int(validated.get("width", 64))
        render_height = int(validated.get("height", 64))

        preset_manager = ComfyUIPresetManager(preset_cfg.get("root"))
        preset_name = str(preset_cfg.get("name", "dual_controlnet_ipadapter"))
        preset_path = preset_manager.resolve_preset_path(preset_name)

        neural_config = self._build_neural_config(validated, output_dir, stem)
        skeleton = Skeleton.create_humanoid()
        style = CharacterStyle()
        pipeline = HeadlessNeuralRenderPipeline(neural_config)
        result = pipeline.run(
            skeleton=skeleton,
            animation_func=idle_animation,
            style=style,
            frames=frame_count,
            width=render_width,
            height=render_height,
        )

        frame_output_dir = output_dir / f"{stem}_frames"
        frame_output_dir.mkdir(parents=True, exist_ok=True)
        frame_sequence: list[dict[str, Any]] = []
        keyframe_set = set(result.keyframe_indices)
        for idx, frame_img in enumerate(result.stylized_frames):
            frame_path = frame_output_dir / f"frame_{idx:04d}.png"
            frame_img.save(str(frame_path))
            frame_sequence.append({
                "frame_index": idx,
                "path": str(frame_path.resolve()),
                "role": "keyframe" if idx in keyframe_set else "propagated",
                "temporal_coherence_score": float(
                    result.temporal_metrics.get("temporal_stability_score", 0.0)
                ),
            })

        guide_output_dir = output_dir / f"{stem}_guides"
        guide_output_dir.mkdir(parents=True, exist_ok=True)
        identity_index = 0
        if result.keyframe_plan is not None:
            identity_index = int(result.keyframe_plan.identity_reference_index)
        identity_index = max(0, min(identity_index, max(0, len(result.source_frames) - 1)))

        source_img = result.source_frames[0] if result.source_frames else result.stylized_frames[0]
        normal_img = result.normal_maps[0] if result.normal_maps else source_img
        depth_img = result.depth_maps[0] if result.depth_maps else source_img
        identity_img = result.source_frames[identity_index] if result.source_frames else source_img

        source_path = guide_output_dir / "source_frame_0000.png"
        normal_path = guide_output_dir / "normal_frame_0000.png"
        depth_path = guide_output_dir / "depth_frame_0000.png"
        identity_path = guide_output_dir / f"identity_frame_{identity_index:04d}.png"
        source_img.save(source_path)
        normal_img.save(normal_path)
        depth_img.save(depth_path)
        identity_img.save(identity_path)

        assembled_payload = preset_manager.assemble_payload(
            preset_name=preset_name,
            source_image_path=source_path,
            normal_map_path=normal_path,
            depth_map_path=depth_path,
            identity_reference_path=identity_path,
            use_ip_adapter=bool(identity_cfg.get("enabled", True)),
            ip_adapter_weight=float(identity_cfg.get("weight", 0.85)),
            ip_adapter_model_name=neural_config.ip_adapter_model_name,
            ip_adapter_clip_vision_name=neural_config.ip_adapter_clip_vision_name,
            normal_controlnet_name=str(comfyui_cfg.get("normal_controlnet_model", "control_v11p_sd15_normalbae.pth")),
            depth_controlnet_name=str(comfyui_cfg.get("depth_controlnet_model", "control_v11f1p_sd15_depth.pth")),
            prompt=neural_config.style_prompt,
            negative_prompt=neural_config.negative_prompt,
            normal_weight=neural_config.controlnet_normal_weight,
            depth_weight=neural_config.controlnet_depth_weight,
            model_checkpoint=neural_config.sd_model_checkpoint,
            steps=neural_config.sd_steps,
            cfg_scale=neural_config.sd_cfg_scale,
            denoising_strength=neural_config.sd_denoising_strength,
            seed=int(result.workflow_manifest.get("seed", -1)),
            filename_prefix=f"{stem}_stylized",
        )

        workflow_path = output_dir / f"{stem}_workflow_manifest.json"
        workflow_path.write_text(
            json.dumps(result.workflow_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        workflow_payload_path = output_dir / f"{stem}_workflow_payload.json"
        workflow_payload_path.write_text(
            json.dumps(assembled_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        preset_asset_path = output_dir / f"{stem}_preset.workflow_api.json"
        preset_asset_path.write_text(preset_path.read_text(encoding="utf-8"), encoding="utf-8")

        keyframe_plan_path = output_dir / f"{stem}_keyframe_plan.json"
        kp_data = result.keyframe_plan.to_dict() if result.keyframe_plan else {}
        keyframe_plan_path.write_text(
            json.dumps(kp_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        time_range = {
            "start_frame": 0,
            "end_frame": len(frame_sequence) - 1,
            "fps": fps,
            "total_frames": len(frame_sequence),
        }
        guides_locked = list(assembled_payload["mathart_lock_manifest"].get("controlnet_guides", []))
        if assembled_payload["mathart_lock_manifest"].get("identity_lock_active"):
            guides_locked.append("ip_adapter_identity")

        temporal_report_path = output_dir / f"{stem}_temporal_report.json"
        temporal_report = {
            "preset_name": preset_name,
            "preset_asset_path": str(preset_asset_path.resolve()),
            "workflow_payload_path": str(workflow_payload_path.resolve()),
            "lock_manifest": assembled_payload.get("mathart_lock_manifest", {}),
            "temporal_metrics": result.temporal_metrics,
            "frame_count": result.frame_count,
            "fps": fps,
            "keyframe_indices": result.keyframe_indices,
            "elapsed_seconds": result.elapsed_seconds,
            "pipeline_metadata": result.to_metadata(),
            "execution_mode": "offline_pipeline",
        }
        temporal_report_path.write_text(
            json.dumps(temporal_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        outputs: dict[str, str] = {
            "workflow": str(workflow_path.resolve()),
            "workflow_payload": str(workflow_payload_path.resolve()),
            "preset_asset": str(preset_asset_path.resolve()),
            "keyframe_plan": str(keyframe_plan_path.resolve()),
            "temporal_report": str(temporal_report_path.resolve()),
            "frame_directory": str(frame_output_dir.resolve()),
            "guide_directory": str(guide_output_dir.resolve()),
        }
        for entry in frame_sequence:
            outputs[f"frame_{entry['frame_index']:04d}"] = entry["path"]

        return ArtifactManifest(
            artifact_family=ArtifactFamily.ANTI_FLICKER_REPORT.value,
            backend_type=BackendType.ANTI_FLICKER_RENDER,
            version="5.0.0",
            session_id=validated.get("session_id", "SESSION-108"),
            outputs=outputs,
            metadata={
                "strategy": "data_driven_comfyui_preset_injection",
                "execution_mode": "offline_pipeline",
                "preset_name": preset_name,
                "frame_count": len(frame_sequence),
                "fps": fps,
                "keyframe_count": len(result.keyframe_indices),
                "guides_locked": guides_locked,
                "identity_lock_enabled": bool(identity_cfg.get("enabled", True)),
                "guide_channels": [
                    ch for ch, enabled in validated.get("guides", {}).items() if enabled
                ],
                "keyframe_interval": int(comfyui_cfg.get("keyframe_interval", 4)),
                "lane": "temporal_consistency",
                "preset_asset_path": str(preset_asset_path.resolve()),
                "assembled_workflow_node_count": len(assembled_payload.get("prompt", {})),
                "temporal_metrics": result.temporal_metrics,
                "keyframe_indices": result.keyframe_indices,
                "identity_lock": {
                    "enabled": bool(identity_cfg.get("enabled", True)),
                    "weight": float(identity_cfg.get("weight", 0.85)),
                },
                "time_range": time_range,
                "payload": {
                    "frame_sequence": frame_sequence,
                    "time_range": time_range,
                    "keyframe_plan": kp_data,
                    "workflow_manifest_path": str(workflow_path.resolve()),
                    "workflow_payload_path": str(workflow_payload_path.resolve()),
                    "preset_asset_path": str(preset_asset_path.resolve()),
                    "lock_manifest": assembled_payload.get("mathart_lock_manifest", {}),
                },
            },
            quality_metrics={
                "temporal_stability_score": float(result.temporal_metrics.get("temporal_stability_score", 0.0)),
                "mean_warp_error": float(result.temporal_metrics.get("mean_warp_error", 0.0)),
                "frame_count": float(len(frame_sequence)),
            },
            references=[
                str(workflow_path.resolve()),
                str(workflow_payload_path.resolve()),
                str(keyframe_plan_path.resolve()),
                str(temporal_report_path.resolve()),
            ],
            tags=["anti_flicker", "comfyui", "ebsynth", preset_name, "offline"],
        )

    def _execute_live_pipeline(self, validated: dict[str, Any]) -> ArtifactManifest:
        from mathart.animation.frame_sequence_exporter import (
            FrameSequenceExportConfig,
            FrameSequenceExporter,
        )
        from mathart.animation.headless_comfy_ebsynth import HeadlessNeuralRenderPipeline
        from mathart.animation.parts import CharacterStyle
        from mathart.animation.presets import idle_animation
        from mathart.animation.comfyui_preset_manager import ComfyUIPresetManager
        from mathart.animation.skeleton import Skeleton
        from mathart.comfy_client.comfyui_ws_client import ComfyUIClient
        from mathart.core.anti_flicker_runtime import (
            export_rgb_sequence,
            materialize_chunk_outputs,
            normalize_server_address,
            pil_sequence_to_alpha_masks,
            pil_sequence_to_depth_arrays,
            pil_sequence_to_normal_arrays,
            plan_frame_chunks,
        )

        output_dir = Path(validated.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = str(validated.get("name", "anti_flicker"))
        temporal = validated.get("temporal", {})
        comfyui_cfg = validated.get("comfyui", {})
        preset_cfg = validated.get("preset", {})
        identity_cfg = validated.get("identity_lock", {})

        frame_count = int(temporal.get("frame_count", 8))
        fps = int(temporal.get("fps", 12))
        chunk_size = int(temporal.get("chunk_size", self.MAX_SAFE_CONTEXT_WINDOW))
        render_width = int(validated.get("width", 64))
        render_height = int(validated.get("height", 64))
        progress_callback = validated.get("_progress_callback")

        def emit_backend_progress(event: dict[str, Any]) -> None:
            if callable(progress_callback):
                try:
                    progress_callback(dict(event))
                except Exception as exc:
                    logger.warning("[anti_flicker_render] progress callback failed: %s", exc)

        preset_name = str(preset_cfg.get("name", self.DEFAULT_LIVE_PRESET))
        if preset_name not in self.SUPPORTED_LIVE_SEQUENCE_PRESETS:
            logger.warning(
                "[anti_flicker_render] preset '%s' is not sequence-aware; switching to '%s'",
                preset_name,
                self.DEFAULT_LIVE_PRESET,
            )
            preset_name = self.DEFAULT_LIVE_PRESET

        preset_manager = ComfyUIPresetManager(preset_cfg.get("root"))
        preset_path = preset_manager.resolve_preset_path(preset_name)
        server_address = normalize_server_address(str(comfyui_cfg.get("url", "http://localhost:8188")))
        client = ComfyUIClient(
            server_address=server_address,
            output_root=output_dir / f"{stem}_comfyui_downloads",
            connect_timeout=float(comfyui_cfg.get("connect_timeout", 5.0)),
            ws_timeout=float(comfyui_cfg.get("ws_timeout", 600.0)),
            max_execution_time=float(comfyui_cfg.get("max_execution_time", 1800.0)),
        )

        if bool(comfyui_cfg.get("fail_fast_on_offline", True)) and not client.is_server_online():
            raise RuntimeError(
                f"ComfyUI server at {server_address} is offline; live anti-flicker execution aborted before rendering"
            )

        emit_backend_progress({
            "event_type": "prepare",
            "message": "Baking source, normal, depth, mask, and motion guides",
            "server_address": server_address,
        })

        neural_config = self._build_neural_config(validated, output_dir, stem)
        skeleton = Skeleton.create_humanoid()
        style = CharacterStyle()
        pipeline = HeadlessNeuralRenderPipeline(neural_config)
        source_frames, normal_maps, depth_maps, mask_maps, mv_sequence = pipeline.bake_auxiliary_maps(
            skeleton=skeleton,
            animation_func=idle_animation,
            style=style,
            frames=frame_count,
            width=render_width,
            height=render_height,
        )
        keyframe_plan = pipeline.plan_keyframes(source_frames, mask_maps, mv_sequence)
        keyframe_indices = list(getattr(keyframe_plan, "indices", []))

        guide_output_dir = output_dir / f"{stem}_guides"
        guide_output_dir.mkdir(parents=True, exist_ok=True)
        frame_output_dir = output_dir / f"{stem}_frames"
        runtime_dir = output_dir / f"{stem}_runtime"
        payload_dir = runtime_dir / "payloads"
        report_dir = runtime_dir / "chunk_reports"
        payload_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        # ComfyUI's EmptyLatentImage rejects either dimension below 16px.
        # The normal/depth/RGB guide sequences therefore must be padded to at
        # least a 16px lattice before the workflow payload is assembled.
        sequence_alignment = 16
        sequence_exporter = FrameSequenceExporter(
            FrameSequenceExportConfig(
                align_to=sequence_alignment,
                fps=fps,
                session_id=str(validated.get("session_id", "SESSION-108")),
            )
        )

        coverage_masks = pil_sequence_to_alpha_masks(source_frames)
        normal_arrays = pil_sequence_to_normal_arrays(normal_maps)
        depth_arrays = pil_sequence_to_depth_arrays(depth_maps)
        chunk_plan = plan_frame_chunks(frame_count, chunk_size)
        chunk_payload_paths: list[str] = []
        chunk_report_paths: list[str] = []
        final_frame_paths: list[str] = []
        final_video_paths: list[str] = []
        total_elapsed = 0.0

        for chunk in chunk_plan:
            emit_backend_progress({
                "event_type": "chunk_start",
                **chunk.to_dict(),
                "chunk_count": len(chunk_plan),
            })
            chunk_label = f"chunk_{chunk.chunk_index:04d}"
            chunk_root = guide_output_dir / chunk_label
            chunk_root.mkdir(parents=True, exist_ok=True)
            subset_slice = slice(chunk.start_frame, chunk.end_frame + 1)

            normal_result = sequence_exporter.export_normal_sequence(
                normal_arrays[subset_slice],
                output_dir=chunk_root,
                sequence_name=f"{stem}_{chunk_label}",
                coverage_masks=coverage_masks[subset_slice],
                lineage={
                    "backend": self.name,
                    "chunk": chunk.to_dict(),
                },
            )
            depth_result = sequence_exporter.export_depth_sequence(
                depth_arrays[subset_slice],
                output_dir=chunk_root,
                sequence_name=f"{stem}_{chunk_label}",
                coverage_masks=coverage_masks[subset_slice],
                invert_polarity=True,
                lineage={
                    "backend": self.name,
                    "chunk": chunk.to_dict(),
                },
            )
            rgb_result = export_rgb_sequence(
                source_frames[subset_slice],
                output_dir=chunk_root,
                sequence_name=f"{stem}_{chunk_label}",
                fps=fps,
                align_to=sequence_alignment,
                session_id=str(validated.get("session_id", "SESSION-108")),
            )

            payload = preset_manager.assemble_sequence_payload(
                preset_name=preset_name,
                normal_sequence_dir=normal_result.sequence_dir,
                depth_sequence_dir=depth_result.sequence_dir,
                rgb_sequence_dir=rgb_result.sequence_dir if preset_name == "sparsectrl_animatediff" else None,
                prompt=str(comfyui_cfg.get("style_prompt", "high quality pixel art, detailed shading, game sprite")),
                negative_prompt=str(comfyui_cfg.get("negative_prompt", "blurry, low quality, distorted, deformed")),
                normal_controlnet_name=str(comfyui_cfg.get("normal_controlnet_model", "control_v11p_sd15_normalbae.pth")),
                depth_controlnet_name=str(comfyui_cfg.get("depth_controlnet_model", "control_v11f1p_sd15_depth.pth")),
                sparsectrl_model_name=str(comfyui_cfg.get("sparsectrl_model", "v3_sd15_sparsectrl_rgb.ckpt")),
                sparsectrl_strength=float(comfyui_cfg.get("sparsectrl_strength", 1.0)),
                sparsectrl_end_percent=float(comfyui_cfg.get("sparsectrl_end_percent", 0.5)),
                animatediff_model_name=str(comfyui_cfg.get("animatediff_model", "v3_sd15_mm.ckpt")),
                animatediff_beta_schedule=str(comfyui_cfg.get("animatediff_beta_schedule", "autoselect")),
                model_checkpoint=str(comfyui_cfg.get("model_checkpoint", "v1-5-pruned-emaonly.safetensors")),
                frame_count=chunk.frame_count,
                context_length=min(int(comfyui_cfg.get("context_window", self.MAX_SAFE_CONTEXT_WINDOW)), chunk.frame_count),
                context_overlap=min(int(comfyui_cfg.get("context_overlap", 4)), max(0, chunk.frame_count - 1)),
                frame_rate=fps,
                width=rgb_result.padded_width,
                height=rgb_result.padded_height,
                steps=int(comfyui_cfg.get("steps", 20)),
                cfg_scale=float(comfyui_cfg.get("cfg_scale", 7.5)),
                denoising_strength=float(comfyui_cfg.get("denoising_strength", 1.0)),
                normal_weight=float(comfyui_cfg.get("controlnet_normal_weight", 1.0)),
                depth_weight=float(comfyui_cfg.get("controlnet_depth_weight", 1.0)),
                filename_prefix=f"{stem}_{chunk_label}",
            )
            payload_path = payload_dir / f"{chunk_label}_workflow_payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            chunk_payload_paths.append(str(payload_path.resolve()))

            def chunk_progress(event: dict[str, Any], *, _chunk=chunk) -> None:
                emit_backend_progress({
                    **event,
                    **_chunk.to_dict(),
                    "chunk_count": len(chunk_plan),
                })

            execution_result = client.execute_workflow(
                payload,
                run_label=f"{stem}_{chunk_label}",
                progress_callback=chunk_progress,
            )
            total_elapsed += float(execution_result.elapsed_seconds)
            if not execution_result.success:
                message = execution_result.error_message or execution_result.degraded_reason or "Unknown ComfyUI execution failure"
                raise RuntimeError(f"Chunk {chunk.chunk_index} failed: {message}")

            materialized = materialize_chunk_outputs(
                image_paths=execution_result.output_images,
                video_paths=execution_result.output_videos,
                output_dir=frame_output_dir,
                start_index=len(final_frame_paths),
            )
            final_frame_paths.extend(str(path.resolve()) for path in materialized.frame_paths)
            final_video_paths.extend(str(path.resolve()) for path in materialized.video_paths)

            chunk_report = {
                **chunk.to_dict(),
                "chunk_count": len(chunk_plan),
                "payload_path": str(payload_path.resolve()),
                "preset_name": preset_name,
                "sequence_directories": {
                    "normal": str(normal_result.sequence_dir),
                    "depth": str(depth_result.sequence_dir),
                    **({"rgb": str(rgb_result.sequence_dir)} if preset_name == "sparsectrl_animatediff" else {}),
                },
                "execution_result": execution_result.to_dict(),
                "node_progress": execution_result.node_progress,
                "materialized_frame_paths": [str(path.resolve()) for path in materialized.frame_paths],
                "materialized_video_paths": [str(path.resolve()) for path in materialized.video_paths],
            }
            chunk_report_path = report_dir / f"{chunk_label}_report.json"
            chunk_report_path.write_text(json.dumps(chunk_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            chunk_report_paths.append(str(chunk_report_path.resolve()))
            emit_backend_progress({
                "event_type": "chunk_complete",
                **chunk.to_dict(),
                "chunk_count": len(chunk_plan),
                "downloaded_frame_count": len(materialized.frame_paths),
            })

        if not final_frame_paths and not final_video_paths:
            raise RuntimeError("Live anti-flicker execution completed but produced no downloadable outputs")

        preset_asset_path = output_dir / f"{stem}_preset.workflow_api.json"
        preset_asset_path.write_text(preset_path.read_text(encoding="utf-8"), encoding="utf-8")
        workflow_path = output_dir / f"{stem}_workflow_manifest.json"
        workflow_payload_path = output_dir / f"{stem}_workflow_payload.json"
        keyframe_plan_path = output_dir / f"{stem}_keyframe_plan.json"
        temporal_report_path = output_dir / f"{stem}_temporal_report.json"

        workflow_manifest = {
            "execution_mode": "live_comfyui_chunked",
            "server_address": server_address,
            "preset_name": preset_name,
            "chunk_plan": [chunk.to_dict() for chunk in chunk_plan],
            "chunk_reports": chunk_report_paths,
            "payload_paths": chunk_payload_paths,
            "frame_count_requested": frame_count,
            "frame_count_materialized": len(final_frame_paths),
            "video_count_materialized": len(final_video_paths),
            "fail_fast_on_offline": bool(comfyui_cfg.get("fail_fast_on_offline", True)),
        }
        workflow_path.write_text(json.dumps(workflow_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        workflow_payload_summary = {
            "preset_name": preset_name,
            "payload_paths": chunk_payload_paths,
            "chunk_plan": [chunk.to_dict() for chunk in chunk_plan],
        }
        workflow_payload_path.write_text(json.dumps(workflow_payload_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        kp_data = keyframe_plan.to_dict() if keyframe_plan is not None else {}
        keyframe_plan_path.write_text(json.dumps(kp_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        keyframe_set = set(keyframe_indices)
        frame_sequence = [
            {
                "frame_index": index,
                "path": path,
                "role": "keyframe" if index in keyframe_set else "propagated",
                "temporal_coherence_score": 1.0,
            }
            for index, path in enumerate(final_frame_paths)
        ]
        time_range = {
            "start_frame": 0,
            "end_frame": len(frame_sequence) - 1,
            "fps": fps,
            "total_frames": len(frame_sequence),
        }
        guides_locked = ["normal", "depth"]
        if preset_name == "sparsectrl_animatediff":
            guides_locked.append("sparsectrl_rgb")

        temporal_report = {
            "preset_name": preset_name,
            "preset_asset_path": str(preset_asset_path.resolve()),
            "workflow_payload_path": str(workflow_payload_path.resolve()),
            "workflow_manifest_path": str(workflow_path.resolve()),
            "frame_count_requested": frame_count,
            "frame_count_materialized": len(frame_sequence),
            "fps": fps,
            "keyframe_indices": keyframe_indices,
            "elapsed_seconds": total_elapsed,
            "chunk_count": len(chunk_plan),
            "chunk_size": chunk_size,
            "chunk_reports": chunk_report_paths,
            "execution_mode": "live_comfyui_chunked",
        }
        temporal_report_path.write_text(json.dumps(temporal_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        outputs: dict[str, str] = {
            "workflow": str(workflow_path.resolve()),
            "workflow_payload": str(workflow_payload_path.resolve()),
            "preset_asset": str(preset_asset_path.resolve()),
            "keyframe_plan": str(keyframe_plan_path.resolve()),
            "temporal_report": str(temporal_report_path.resolve()),
            "frame_directory": str(frame_output_dir.resolve()),
            "guide_directory": str(guide_output_dir.resolve()),
            "chunk_reports_directory": str(report_dir.resolve()),
        }
        for entry in frame_sequence:
            outputs[f"frame_{entry['frame_index']:04d}"] = entry["path"]
        for index, video_path in enumerate(final_video_paths):
            outputs[f"video_{index:04d}"] = video_path

        return ArtifactManifest(
            artifact_family=ArtifactFamily.ANTI_FLICKER_REPORT.value,
            backend_type=BackendType.ANTI_FLICKER_RENDER,
            version="5.0.0",
            session_id=validated.get("session_id", "SESSION-108"),
            outputs=outputs,
            metadata={
                "strategy": "comfyui_client_chunked_sequence_runtime",
                "execution_mode": "live_comfyui_chunked",
                "preset_name": preset_name,
                "frame_count": len(frame_sequence),
                "requested_frame_count": frame_count,
                "fps": fps,
                "keyframe_count": len(keyframe_indices),
                "guides_locked": guides_locked,
                "identity_lock_enabled": bool(identity_cfg.get("enabled", True)),
                "guide_channels": [
                    ch for ch, enabled in validated.get("guides", {}).items() if enabled
                ],
                "keyframe_interval": int(comfyui_cfg.get("keyframe_interval", 4)),
                "lane": "temporal_consistency",
                "preset_asset_path": str(preset_asset_path.resolve()),
                "assembled_workflow_node_count": 0,
                "temporal_metrics": {
                    "chunk_count": len(chunk_plan),
                    "aggregate_elapsed_seconds": total_elapsed,
                    "downloaded_video_count": len(final_video_paths),
                },
                "keyframe_indices": keyframe_indices,
                "identity_lock": {
                    "enabled": bool(identity_cfg.get("enabled", True)),
                    "weight": float(identity_cfg.get("weight", 0.85)),
                },
                "time_range": time_range,
                "comfyui_server_address": server_address,
                "chunking": {
                    "enabled": len(chunk_plan) > 1,
                    "chunk_size": chunk_size,
                    "context_window": int(comfyui_cfg.get("context_window", self.MAX_SAFE_CONTEXT_WINDOW)),
                    "context_overlap": int(comfyui_cfg.get("context_overlap", 4)),
                    "chunk_plan": [chunk.to_dict() for chunk in chunk_plan],
                },
                "payload": {
                    "frame_sequence": frame_sequence,
                    "time_range": time_range,
                    "keyframe_plan": kp_data,
                    "workflow_manifest_path": str(workflow_path.resolve()),
                    "workflow_payload_path": str(workflow_payload_path.resolve()),
                    "preset_asset_path": str(preset_asset_path.resolve()),
                    "lock_manifest": {
                        "controlnet_guides": guides_locked,
                        "chunk_plan": [chunk.to_dict() for chunk in chunk_plan],
                        "payload_paths": chunk_payload_paths,
                    },
                },
            },
            quality_metrics={
                "frame_count": float(len(frame_sequence)),
                "chunk_count": float(len(chunk_plan)),
                "chunk_size": float(chunk_size),
            },
            references=chunk_report_paths + chunk_payload_paths,
            tags=["anti_flicker", "comfyui", "chunked", preset_name, "live"],
        )

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[anti_flicker_render] config warning: %s", w)

        if bool(validated.get("comfyui", {}).get("live_execution", False)):
            return self._execute_live_pipeline(validated)
        return self._execute_offline_pipeline(validated)
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


# ═══════════════════════════════════════════════════════════════════════════
#  Unified Motion Backend (SESSION-070 — P1-MIGRATE-1)
# ═══════════════════════════════════════════════════════════════════════════
#
#  Promotes the MotionStateLaneRegistry from a domain-internal side registry
#  into a first-class BackendMeta-compliant plugin discovered by the
#  MicrokernelOrchestrator.
#
#  Architecture alignment:
#    - EA Frostbite FrameGraph (GDC 2017): data-driven scheduling via
#      context dict → manifest output.
#    - Mach/QNX Microkernel: motion trunk is a Backend, not kernel code.
#    - Clean Architecture: Context-in / Manifest-out boundary.
#    - Pixar OpenUSD Schema: backward-compatible 3D extension via
#      joint_channel_schema metadata.
# ═══════════════════════════════════════════════════════════════════════════


def _wrap_scalar_delta(curr: float, prev: float, *, cyclic: bool = False) -> float:
    delta = float(curr) - float(prev)
    if not cyclic:
        return delta
    while delta > 0.5:
        delta -= 1.0
    while delta < -0.5:
        delta += 1.0
    return delta


def _build_cognitive_telemetry_sidecar(
    frames: list[Any],
    *,
    state: str,
    fps: int,
) -> dict[str, Any]:
    """Build a cognition-oriented trace sidecar at the backend boundary.

    This function intentionally runs *after* the UMR frames have already been
    produced. It never touches ``UnifiedGaitBlender`` inner loops, preserving
    the repository's O(1) hot-path discipline.
    """
    traces: list[dict[str, Any]] = []
    dt = 1.0 / max(int(fps), 1)
    prev_vx = 0.0
    prev_vy = 0.0
    prev_ax = 0.0
    prev_ay = 0.0
    total_contact_transitions = 0
    phase_kinds: set[str] = set()

    prev_frame = None
    for idx, frame in enumerate(frames):
        root = frame.root_transform
        phase_kind = str(frame.metadata.get("phase_kind", "cyclic"))
        phase_kinds.add(phase_kind)
        cyclic = phase_kind not in {"distance_to_apex", "distance_to_ground", "hit_recovery", "transient"}

        if prev_frame is None:
            vx = float(getattr(root, "velocity_x", 0.0))
            vy = float(getattr(root, "velocity_y", 0.0))
        else:
            prev_root = prev_frame.root_transform
            vx = (float(root.x) - float(prev_root.x)) / dt
            vy = (float(root.y) - float(prev_root.y)) / dt

        ax = (vx - prev_vx) / dt if idx > 0 else 0.0
        ay = (vy - prev_vy) / dt if idx > 0 else 0.0
        jx = (ax - prev_ax) / dt if idx > 1 else 0.0
        jy = (ay - prev_ay) / dt if idx > 1 else 0.0
        speed = math.sqrt(vx * vx + vy * vy)
        acceleration = math.sqrt(ax * ax + ay * ay)
        jerk = math.sqrt(jx * jx + jy * jy)

        joint_angular_velocity: dict[str, float] = {}
        extremity_motion_energy = 0.0
        extremity_count = 0
        if prev_frame is not None:
            prev_pose = getattr(prev_frame, "joint_local_rotations", {})
            for joint_name, angle in frame.joint_local_rotations.items():
                if joint_name not in prev_pose:
                    continue
                vel = _wrap_scalar_delta(
                    float(angle),
                    float(prev_pose[joint_name]),
                    cyclic=True,
                ) / dt
                joint_angular_velocity[str(joint_name)] = float(vel)
                lname = str(joint_name).lower()
                if any(token in lname for token in ("hand", "foot", "arm", "leg", "head", "tail")):
                    extremity_motion_energy += abs(float(vel))
                    extremity_count += 1

        if extremity_count > 0:
            extremity_motion_energy /= float(extremity_count)

        contacts = frame.contact_tags
        desired_contact_state = str(frame.metadata.get("desired_contact_state", ""))
        contact_expectation = {
            "left_foot": bool(getattr(contacts, "left_foot", False)),
            "right_foot": bool(getattr(contacts, "right_foot", False)),
            "desired_contact_state": desired_contact_state,
            "contact_active_count": int(bool(getattr(contacts, "left_foot", False)))
            + int(bool(getattr(contacts, "right_foot", False))),
        }
        if "contact_expectation" in frame.metadata:
            contact_expectation["semantic_expectation"] = str(frame.metadata.get("contact_expectation", ""))

        if prev_frame is not None:
            prev_contacts = prev_frame.contact_tags
            total_contact_transitions += int(
                bool(prev_contacts.left_foot) != bool(contacts.left_foot)
            )
            total_contact_transitions += int(
                bool(prev_contacts.right_foot) != bool(contacts.right_foot)
            )

        phase_velocity = 0.0
        if prev_frame is not None:
            prev_phase_kind = str(prev_frame.metadata.get("phase_kind", phase_kind))
            prev_cyclic = prev_phase_kind not in {"distance_to_apex", "distance_to_ground", "hit_recovery", "transient"}
            phase_velocity = _wrap_scalar_delta(
                float(frame.phase),
                float(prev_frame.phase),
                cyclic=(cyclic and prev_cyclic),
            ) / dt

        traces.append({
            "frame_index": int(frame.frame_index),
            "time": float(frame.time),
            "phase": float(frame.phase),
            "phase_kind": phase_kind,
            "phase_velocity": float(phase_velocity),
            "phase_vector": {
                "sin": float(math.sin(2.0 * math.pi * float(frame.phase))),
                "cos": float(math.cos(2.0 * math.pi * float(frame.phase))),
            },
            "root_position": {
                "x": float(root.x),
                "y": float(root.y),
                "z": float(root.z) if getattr(root, "z", None) is not None else None,
            },
            "root_velocity": {
                "x": float(vx),
                "y": float(vy),
                "z": float(getattr(root, "velocity_z", 0.0) or 0.0),
            },
            "root_speed": float(speed),
            "root_acceleration": float(acceleration),
            "root_jerk": float(jerk),
            "extremity_motion_energy": float(extremity_motion_energy),
            "joint_angular_velocity": joint_angular_velocity,
            "contact_expectation": contact_expectation,
        })

        prev_frame = frame
        prev_vx = vx
        prev_vy = vy
        prev_ax = ax
        prev_ay = ay

    mean_speed = sum(float(t["root_speed"]) for t in traces) / max(len(traces), 1)
    mean_jerk = sum(float(t["root_jerk"]) for t in traces) / max(len(traces), 1)
    peak_jerk = max((float(t["root_jerk"]) for t in traces), default=0.0)
    mean_extremity = sum(float(t["extremity_motion_energy"]) for t in traces) / max(len(traces), 1)

    return {
        "schema_version": "1.0.0",
        "state": str(state),
        "frame_count": int(len(frames)),
        "fps": int(fps),
        "trace_fields": [
            "frame_index",
            "time",
            "phase",
            "phase_kind",
            "phase_velocity",
            "root_position",
            "root_velocity",
            "root_speed",
            "root_acceleration",
            "root_jerk",
            "extremity_motion_energy",
            "joint_angular_velocity",
            "contact_expectation",
        ],
        "summary": {
            "phase_kinds": sorted(phase_kinds),
            "mean_root_speed": float(mean_speed),
            "mean_root_jerk": float(mean_jerk),
            "peak_root_jerk": float(peak_jerk),
            "mean_extremity_motion_energy": float(mean_extremity),
            "contact_transition_count": int(total_contact_transitions),
        },
        "traces": traces,
    }


@register_backend(
    BackendType.UNIFIED_MOTION,
    display_name="Unified Motion Trunk Backend",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.MOTION_UMR.value,
    ),
    capabilities=(
        BackendCapability.ANIMATION_EXPORT,
    ),
    input_requirements=("state", "frame_count"),
    session_origin="SESSION-070",
    schema_version="1.0.0",
)
class UnifiedMotionBackend:
    """First-class motion backend wrapping the MotionStateLaneRegistry.

    This backend replaces direct lane-registry calls in ``pipeline.py`` with
    a proper Context-in / Manifest-out boundary. The lane registry remains
    the authoritative motion generation engine; this backend simply provides
    the microkernel-compliant interface.

    Context Keys
    ------------
    state : str
        Motion state name (e.g., "run", "walk", "jump").
    frame_count : int
        Number of frames to generate.
    fps : int, optional
        Frames per second (default 12).
    output_dir : str | Path, optional
        Directory for clip JSON output.
    name : str, optional
        Stem name for output files.
    speed : float, optional
        Motion speed multiplier.
    joint_channel_schema : str, optional
        Rotation encoding declaration (default "2d_scalar").
    """

    @property
    def name(self) -> str:
        return BackendType.UNIFIED_MOTION.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(
        self, context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Backend-owned config validation (Hexagonal Architecture).

        Normalizes motion context parameters and returns warnings for
        any defaulted or corrected values.
        """
        warnings: list[str] = []
        ctx = dict(context)

        # --- state ---
        state = str(ctx.get("state", "")).strip().lower()
        if not state:
            state = "idle"
            warnings.append("state not provided, defaulting to 'idle'")
        ctx["state"] = state

        # --- frame_count ---
        try:
            fc = int(ctx.get("frame_count", 12))
        except (ValueError, TypeError):
            fc = 12
            warnings.append("frame_count invalid, defaulting to 12")
        ctx["frame_count"] = max(1, fc)

        # --- fps ---
        try:
            fps = int(ctx.get("fps", 12))
        except (ValueError, TypeError):
            fps = 12
            warnings.append("fps invalid, defaulting to 12")
        ctx["fps"] = max(1, fps)

        # --- joint_channel_schema ---
        from mathart.animation.unified_motion import (
            JOINT_CHANNEL_2D_SCALAR,
            VALID_JOINT_CHANNEL_SCHEMAS,
        )
        jcs = str(ctx.get("joint_channel_schema", JOINT_CHANNEL_2D_SCALAR))
        if jcs not in VALID_JOINT_CHANNEL_SCHEMAS:
            warnings.append(
                f"joint_channel_schema '{jcs}' unknown, defaulting to '{JOINT_CHANNEL_2D_SCALAR}'"
            )
            jcs = JOINT_CHANNEL_2D_SCALAR
        ctx["joint_channel_schema"] = jcs

        # --- output_dir ---
        ctx.setdefault("output_dir", "output")
        ctx.setdefault("name", "motion")

        return ctx, warnings

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Generate a UMR motion clip via the lane registry and return a manifest.

        The lane registry is the authoritative motion generation engine.
        This method wraps it in the Context-in / Manifest-out discipline.
        """
        from mathart.animation.unified_gait_blender import (
            MotionStateRequest,
            get_motion_lane_registry,
            resolve_unified_gait_runtime_config,
            resolve_unified_transition_runtime_config,
        )
        from mathart.animation.unified_motion import (
            JOINT_CHANNEL_2D_SCALAR,
            UnifiedMotionClip,
            infer_contact_tags,
        )

        state = str(context.get("state", "idle"))
        frame_count = int(context.get("frame_count", 12))
        fps = int(context.get("fps", 12))
        output_dir = Path(context.get("output_dir", "output")).resolve()
        stem = str(context.get("name", "motion"))
        jcs = str(context.get("joint_channel_schema", JOINT_CHANNEL_2D_SCALAR))

        output_dir.mkdir(parents=True, exist_ok=True)

        registry = get_motion_lane_registry()
        lane = registry.get(state)
        gait_runtime_config = resolve_unified_gait_runtime_config(
            context.get("runtime_distillation_bus"),
            blend_time=float(context.get("blend_time", 0.2)),
            phase_weight=float(context.get("phase_weight", 1.0)),
        )
        transition_runtime_config = resolve_unified_transition_runtime_config(
            context.get("runtime_distillation_bus"),
            recovery_half_life=float(context.get("recovery_half_life", 0.12)),
            impact_damping_weight=float(context.get("impact_damping_weight", 1.0)),
            landing_anticipation_window=float(context.get("landing_anticipation_window", 0.18)),
        )
        lane = lane.begin_clip(
            gait_runtime_config=gait_runtime_config,
            transition_runtime_config=transition_runtime_config,
        )

        frames = []
        for i in range(frame_count):
            progress = float(i) / max(frame_count - 1, 1)
            phase = progress
            t = float(i) / max(fps, 1)

            root = lane.infer_root_transform(
                progress=progress,
                frame_index=i,
                frame_count=frame_count,
                fps=fps,
            )

            request = MotionStateRequest(
                state=state,
                phase=phase,
                time=t,
                frame_index=i,
                frame_count=frame_count,
                fps=fps,
                metadata={
                    "generator": "motion_lane_registry",
                    "motion_lane": state,
                    "pipeline_source": "unified_motion_backend",
                    "joint_channel_schema": jcs,
                    "gait_blend_time": gait_runtime_config.blend_time,
                    "gait_phase_weight": gait_runtime_config.phase_weight,
                    "gait_param_source": gait_runtime_config.parameter_source,
                    "transient_recovery_half_life": transition_runtime_config.recovery_half_life,
                    "transient_impact_damping_weight": transition_runtime_config.impact_damping_weight,
                    "transient_landing_anticipation_window": transition_runtime_config.landing_anticipation_window,
                    "transient_param_source": transition_runtime_config.parameter_source,
                },
                root_x=root.x,
            )
            frame = lane.build_frame(request)
            frames.append(frame)

        cognitive_telemetry = _build_cognitive_telemetry_sidecar(
            frames,
            state=state,
            fps=fps,
        )

        clip = UnifiedMotionClip(
            clip_id=f"{stem}_{state}_umr",
            state=state,
            fps=fps,
            frames=frames,
            metadata={
                "generator": "unified_motion_backend",
                "motion_lane": state,
                "joint_channel_schema": jcs,
                "backend_type": BackendType.UNIFIED_MOTION.value,
                "session_origin": "SESSION-070",
                "gait_blend_time": gait_runtime_config.blend_time,
                "gait_phase_weight": gait_runtime_config.phase_weight,
                "gait_param_source": gait_runtime_config.parameter_source,
                "transient_recovery_half_life": transition_runtime_config.recovery_half_life,
                "transient_impact_damping_weight": transition_runtime_config.impact_damping_weight,
                "transient_landing_anticipation_window": transition_runtime_config.landing_anticipation_window,
                "transient_param_source": transition_runtime_config.parameter_source,
                "cognitive_telemetry": cognitive_telemetry,
            },
        )

        clip_path = output_dir / f"{stem}_{state}.umr.json"
        clip.save(clip_path)
        cognitive_telemetry_path = output_dir / f"{stem}_{state}.cognitive_telemetry.json"
        cognitive_telemetry_path.write_text(
            json.dumps(cognitive_telemetry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.MOTION_UMR.value,
            backend_type=BackendType.UNIFIED_MOTION,
            version="1.0.0",
            session_id="SESSION-070",
            outputs={
                "motion_clip_json": str(clip_path),
                "cognitive_telemetry_json": str(cognitive_telemetry_path),
            },
            metadata={
                "state": state,
                "frame_count": frame_count,
                "fps": fps,
                "joint_channel_schema": jcs,
                "clip_id": clip.clip_id,
                "motion_lane": state,
                "generator": "unified_motion_backend",
                "gait_blend_time": gait_runtime_config.blend_time,
                "gait_phase_weight": gait_runtime_config.phase_weight,
                "gait_param_source": gait_runtime_config.parameter_source,
                "transient_recovery_half_life": transition_runtime_config.recovery_half_life,
                "transient_impact_damping_weight": transition_runtime_config.impact_damping_weight,
                "transient_landing_anticipation_window": transition_runtime_config.landing_anticipation_window,
                "transient_param_source": transition_runtime_config.parameter_source,
                "cognitive_telemetry": cognitive_telemetry,
                "payload": {
                    "type": "motion_umr_clip",
                    "state": state,
                    "frame_count": frame_count,
                    "fps": fps,
                    "clip_path": str(clip_path),
                    "joint_channel_schema": jcs,
                    "gait_blend_time": gait_runtime_config.blend_time,
                    "gait_phase_weight": gait_runtime_config.phase_weight,
                    "gait_param_source": gait_runtime_config.parameter_source,
                    "transient_recovery_half_life": transition_runtime_config.recovery_half_life,
                    "transient_impact_damping_weight": transition_runtime_config.impact_damping_weight,
                    "transient_landing_anticipation_window": transition_runtime_config.landing_anticipation_window,
                    "transient_param_source": transition_runtime_config.parameter_source,
                    "cognitive_telemetry_path": str(cognitive_telemetry_path),
                    "cognitive_telemetry_summary": cognitive_telemetry.get("summary", {}),
                },
            },
            quality_metrics={},
            tags=["motion", "umr", state, "session-070"],
        )

        manifest_path = output_dir / f"{stem}_{state}.umr_manifest.json"
        manifest.save(manifest_path)

        logger.info(
            "UnifiedMotionBackend: generated %d frames for state=%s → %s",
            frame_count, state, clip_path,
        )
        return manifest


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
    "UnifiedMotionBackend",
]
