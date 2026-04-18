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
import time
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
    version="3.0.0",
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
    session_origin="SESSION-068",
)
class AntiFlickerRenderBackend:
    """Real anti-flicker temporal rendering backend.

    SESSION-068 upgrades from placeholder to real execution:
    1. Invokes ``HeadlessNeuralRenderPipeline.run()`` for bake→stylize→propagate.
    2. Implements ``validate_config()`` for ComfyUI presets and temporal params.
    3. Emits a ``frame_sequence`` time-series manifest payload inspired by
       OpenTimelineIO (OTIO) / VFX Reference Platform conventions.
    """

    @property
    def name(self) -> str:
        return BackendType.ANTI_FLICKER_RENDER.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(self, config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize anti-flicker rendering configuration.

        Parses ComfyUI presets, temporal parameters, and identity-lock settings.
        The CLI bus passes the raw config dict without any inspection.

        Returns
        -------
        tuple[dict, list[str]]
            (validated_config, warnings)
        """
        warnings: list[str] = []
        validated = dict(config)

        # --- Temporal namespace ---
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
        validated["temporal"] = temporal

        # --- Guides namespace ---
        guides = validated.get("guides", {})
        if not isinstance(guides, dict):
            guides = {}
            warnings.append("guides config must be a dict; using defaults")

        for guide_key in ("normal", "depth", "mask", "motion_vector"):
            guides.setdefault(guide_key, True)
        validated["guides"] = guides

        # --- Identity lock namespace ---
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

        # --- ComfyUI namespace ---
        comfyui = validated.get("comfyui", {})
        if not isinstance(comfyui, dict):
            comfyui = {}
            warnings.append("comfyui config must be a dict; using defaults")

        comfyui.setdefault("url", "http://localhost:8188")
        comfyui.setdefault("style_prompt", "high quality pixel art, detailed shading, game sprite")
        comfyui.setdefault("negative_prompt", "blurry, low quality, distorted, deformed")
        comfyui.setdefault("controlnet_normal_weight", 1.0)
        comfyui.setdefault("controlnet_depth_weight", 1.0)

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

        comfyui.setdefault("denoising_strength", 0.65)
        comfyui.setdefault("steps", 20)
        comfyui.setdefault("cfg_scale", 7.5)

        keyframe_interval = int(comfyui.get("keyframe_interval", 4))
        if keyframe_interval < 1:
            warnings.append("comfyui.keyframe_interval must be >= 1; setting to 1")
            keyframe_interval = 1
        comfyui["keyframe_interval"] = keyframe_interval
        validated["comfyui"] = comfyui

        # --- EbSynth namespace ---
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

        # --- Render dimensions ---
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

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        from mathart.animation.skeleton import Skeleton
        from mathart.animation.parts import CharacterStyle
        from mathart.animation.presets import idle_animation
        from mathart.animation.headless_comfy_ebsynth import (
            HeadlessNeuralRenderPipeline,
            NeuralRenderConfig,
        )

        # --- Validate config (backend-owned, not CLI) ---
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[anti_flicker_render] config warning: %s", w)

        output_dir = Path(validated.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = validated.get("name", "anti_flicker")

        temporal = validated.get("temporal", {})
        frame_count = int(temporal.get("frame_count", 8))
        fps = int(temporal.get("fps", 12))
        comfyui_cfg = validated.get("comfyui", {})
        ebsynth_cfg = validated.get("ebsynth", {})
        identity_cfg = validated.get("identity_lock", {})
        render_width = int(validated.get("width", 64))
        render_height = int(validated.get("height", 64))

        # Build NeuralRenderConfig from validated namespaces
        neural_config = NeuralRenderConfig(
            comfyui_url=comfyui_cfg.get("url", "http://localhost:8188"),
            style_prompt=comfyui_cfg.get("style_prompt", "high quality pixel art, detailed shading, game sprite"),
            negative_prompt=comfyui_cfg.get("negative_prompt", "blurry, low quality, distorted, deformed"),
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
            output_dir=str(output_dir / stem),
        )

        # Build skeleton + style (lightweight defaults)
        skeleton = Skeleton.create_humanoid()
        style = CharacterStyle()

        # --- Real pipeline execution ---
        pipeline = HeadlessNeuralRenderPipeline(neural_config)
        result = pipeline.run(
            skeleton=skeleton,
            animation_func=idle_animation,
            style=style,
            frames=frame_count,
            width=render_width,
            height=render_height,
        )

        # --- Build frame_sequence payload (OTIO-inspired time-series) ---
        frame_output_dir = output_dir / f"{stem}_frames"
        frame_output_dir.mkdir(parents=True, exist_ok=True)

        frame_sequence: list[dict[str, Any]] = []
        keyframe_set = set(result.keyframe_indices)

        for idx, frame_img in enumerate(result.stylized_frames):
            frame_path = frame_output_dir / f"frame_{idx:04d}.png"
            frame_img.save(str(frame_path))

            role = "keyframe" if idx in keyframe_set else "propagated"
            frame_entry = {
                "frame_index": idx,
                "path": str(frame_path.resolve()),
                "role": role,
                "temporal_coherence_score": float(
                    result.temporal_metrics.get("temporal_stability_score", 0.0)
                ),
            }
            frame_sequence.append(frame_entry)

        # Write ancillary artifacts
        workflow_path = output_dir / f"{stem}_workflow_manifest.json"
        workflow_path.write_text(
            json.dumps(result.workflow_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        keyframe_plan_path = output_dir / f"{stem}_keyframe_plan.json"
        kp_data = result.keyframe_plan.to_dict() if result.keyframe_plan else {}
        keyframe_plan_path.write_text(
            json.dumps(kp_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        temporal_report_path = output_dir / f"{stem}_temporal_report.json"
        temporal_report = {
            "temporal_metrics": result.temporal_metrics,
            "frame_count": result.frame_count,
            "keyframe_indices": result.keyframe_indices,
            "elapsed_seconds": result.elapsed_seconds,
            "pipeline_metadata": result.to_metadata(),
        }
        temporal_report_path.write_text(
            json.dumps(temporal_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # --- Build outputs dict ---
        outputs: dict[str, str] = {
            "workflow": str(workflow_path.resolve()),
            "keyframe_plan": str(keyframe_plan_path.resolve()),
            "temporal_report": str(temporal_report_path.resolve()),
            "frame_directory": str(frame_output_dir.resolve()),
        }
        # Add individual frame paths
        for entry in frame_sequence:
            outputs[f"frame_{entry['frame_index']:04d}"] = entry["path"]

        time_range = {
            "start_frame": 0,
            "end_frame": len(frame_sequence) - 1,
            "fps": fps,
            "total_frames": len(frame_sequence),
        }

        return ArtifactManifest(
            artifact_family=ArtifactFamily.COMPOSITE.value,
            backend_type=BackendType.ANTI_FLICKER_RENDER,
            version="3.0.0",
            session_id=validated.get("session_id", "SESSION-068"),
            outputs=outputs,
            metadata={
                "strategy": "sparse_ctrl_plus_ebsynth",
                "guide_channels": [
                    ch for ch, enabled in validated.get("guides", {}).items()
                    if enabled
                ],
                "keyframe_interval": int(comfyui_cfg.get("keyframe_interval", 4)),
                "lane": "temporal_consistency",
                "temporal_metrics": result.temporal_metrics,
                "keyframe_indices": result.keyframe_indices,
                "identity_lock": {
                    "enabled": bool(identity_cfg.get("enabled", True)),
                    "weight": float(identity_cfg.get("weight", 0.85)),
                },
                "time_range": time_range,
                # --- Polymorphic payload: frame_sequence (OTIO-inspired) ---
                "payload": {
                    "frame_sequence": frame_sequence,
                    "time_range": time_range,
                    "keyframe_plan": kp_data,
                    "workflow_manifest_path": str(workflow_path.resolve()),
                },
            },
            quality_metrics={
                "temporal_stability_score": float(
                    result.temporal_metrics.get("temporal_stability_score", 0.0)
                ),
                "mean_warp_error": float(
                    result.temporal_metrics.get("mean_warp_error", 0.0)
                ),
                "frame_count": float(len(frame_sequence)),
                "keyframe_count": float(len(result.keyframe_indices)),
            },
            tags=["anti_flicker", "temporal", "comfyui", "ebsynth", "session-068"],
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Remaining backends (unchanged from SESSION-066/067)
# ═══════════════════════════════════════════════════════════════════════════


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
