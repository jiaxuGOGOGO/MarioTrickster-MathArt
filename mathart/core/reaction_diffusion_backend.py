"""ReactionDiffusionBackend — Registry-native Gray-Scott texture plugin.

SESSION-119 (P1-NEW-2) — first member of the ``texture`` lane, plugged into
the project's Registry Pattern with the same discipline used by
``IndustrialSpriteBackend``:

* self-registers via ``@register_backend`` — zero trunk edits,
* owns its own ``validate_config()`` (Hexagonal Architecture Adapter),
* returns a strongly-typed ``ArtifactManifest`` of family
  ``MATERIAL_BUNDLE`` with the canonical ``texture_channels`` payload so the
  output can feed directly into any Unity/Godot PBR material slot.

The heavy maths lives in :mod:`mathart.texture.reaction_diffusion`; this
module is purely the plugin shell + packaging.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)
from mathart.core.backend_types import BackendType
from mathart.texture.reaction_diffusion import (
    GRAY_SCOTT_PRESETS,
    GrayScottSolver,
    GrayScottSolverConfig,
    derive_pbr_from_concentration,
    encode_albedo_map,
    encode_height_map,
    encode_mask_map,
    encode_normal_map,
    get_preset,
    list_preset_names,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Channel semantics (MaterialX / glTF PBR-inspired)
# ---------------------------------------------------------------------------

_RD_CHANNEL_SEMANTICS: dict[str, dict[str, str]] = {
    "albedo": {
        "color_space": "sRGB",
        "engine_slot_unity": "_BaseMap",
        "engine_slot_godot": "albedo_texture",
        "description": "Palette-interpolated base colour derived from V concentration.",
    },
    "normal": {
        "color_space": "linear",
        "engine_slot_unity": "_BumpMap",
        "engine_slot_godot": "normal_texture",
        "description": "Tangent-space normal map, each pixel unit-length (RGB=(N+1)/2).",
    },
    "height": {
        "color_space": "linear",
        "engine_slot_unity": "_ParallaxMap",
        "engine_slot_godot": "heightmap_texture",
        "description": "V concentration interpreted as height; drives parallax / POM.",
    },
    "mask": {
        "color_space": "linear",
        "engine_slot_unity": "_DetailMask",
        "engine_slot_godot": "detail_mask",
        "description": "Smoothstepped occupancy of the reaction-product species V.",
    },
}

_RD_DEFAULT_CHANNELS: tuple[str, ...] = ("albedo", "normal", "height", "mask")


def _save_rgb_png(path: Path, array: np.ndarray) -> None:
    from PIL import Image
    Image.fromarray(array, mode="RGB").save(path)


def _save_gray_png(path: Path, array: np.ndarray) -> None:
    from PIL import Image
    Image.fromarray(array, mode="L").save(path)


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

@register_backend(
    BackendType.REACTION_DIFFUSION,
    display_name="Reaction-Diffusion Organic Texture Backend",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.MATERIAL_BUNDLE.value,
        ArtifactFamily.DISPLACEMENT_MAP.value,
    ),
    capabilities=(
        BackendCapability.SPRITE_EXPORT,
        BackendCapability.SHADER_EXPORT,
    ),
    input_requirements=(),
    session_origin="SESSION-119",
)
class ReactionDiffusionBackend:
    """Gray-Scott organic texture generator packaged as a MATERIAL_BUNDLE.

    Context keys consumed by ``execute()``:

    ``preset`` (str)
        One of :func:`mathart.texture.reaction_diffusion.list_preset_names`
        — default ``"CORAL"``.  Overrides any individual ``feed``/``kill``.
    ``feed`` (float), ``kill`` (float)
        Explicit Gray-Scott parameters, overriding the preset defaults.
    ``width``, ``height`` (int)
        Grid resolution; both default to 256.
    ``steps`` (int)
        Number of iterations to evolve.  Default 2500.
    ``dt`` (float)
        Euler step; automatically clamped to the CFL limit.
    ``diffusion_u``, ``diffusion_v`` (float)
        Diffusion coefficients.  Defaults from preset/Karl Sims.
    ``seed`` (int)
        Deterministic RNG seed.  Default 2026.
    ``channels`` (list[str] | str)
        Subset of ``{"albedo", "normal", "height", "mask"}``.
    ``output_dir`` (str)
        Destination directory for the bundle.  Default ``output``.
    ``name`` (str)
        Bundle stem; default ``reaction_diffusion``.
    ``normal_strength`` (float)
        Emboss strength of the Sobel gradient.  Default 6.0.
    ``mask_threshold`` (float)
        V threshold for the soft mask.  Default 0.25.
    """

    @property
    def name(self) -> str:
        return BackendType.REACTION_DIFFUSION.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta  # type: ignore[attr-defined]

    # ---------- Config validation ----------
    def validate_config(
        self,
        config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        validated: dict[str, Any] = dict(config)

        preset_name = str(validated.get("preset", "CORAL")).upper()
        if preset_name not in GRAY_SCOTT_PRESETS:
            warnings.append(
                f"Unknown preset {preset_name!r}; falling back to CORAL."
            )
            preset_name = "CORAL"
        preset = GRAY_SCOTT_PRESETS[preset_name]
        validated["preset"] = preset_name

        feed = float(validated.get("feed", preset.feed))
        kill = float(validated.get("kill", preset.kill))
        if not (0.0 <= feed <= 0.15):
            warnings.append(f"feed={feed} outside [0, 0.15]; clamping.")
            feed = float(np.clip(feed, 0.0, 0.15))
        if not (0.0 <= kill <= 0.10):
            warnings.append(f"kill={kill} outside [0, 0.10]; clamping.")
            kill = float(np.clip(kill, 0.0, 0.10))
        validated["feed"] = feed
        validated["kill"] = kill

        width = int(validated.get("width", 256))
        height = int(validated.get("height", 256))
        if width < 16:
            warnings.append(f"width={width} too small; clamping to 16")
            width = 16
        if height < 16:
            warnings.append(f"height={height} too small; clamping to 16")
            height = 16
        validated["width"] = width
        validated["height"] = height

        steps = int(validated.get("steps", 2500))
        if steps < 1:
            warnings.append(f"steps={steps} invalid; using 1")
            steps = 1
        if steps > 50000:
            warnings.append(f"steps={steps} unusually large; proceeding but slow")
        validated["steps"] = steps

        diffusion_u = float(validated.get("diffusion_u", preset.diffusion_u))
        diffusion_v = float(validated.get("diffusion_v", preset.diffusion_v))
        dt = float(validated.get("dt", 1.0))
        cfl = 1.0 / (2.0 * max(diffusion_u, diffusion_v, 1e-9))
        if dt > cfl:
            warnings.append(
                f"dt={dt} exceeds CFL limit {cfl:.4f}; will be clamped by solver."
            )
        validated["diffusion_u"] = diffusion_u
        validated["diffusion_v"] = diffusion_v
        validated["dt"] = dt
        validated["seed"] = int(validated.get("seed", 2026))
        validated["normal_strength"] = float(validated.get("normal_strength", 6.0))
        validated["mask_threshold"] = float(validated.get("mask_threshold", 0.25))
        validated["seed_patch_fraction"] = float(
            validated.get("seed_patch_fraction", 0.08)
        )
        validated["seed_noise_amplitude"] = float(
            validated.get("seed_noise_amplitude", 0.05)
        )

        channels = validated.get("channels", list(_RD_DEFAULT_CHANNELS))
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",")]
        filtered = [c for c in channels if c in _RD_CHANNEL_SEMANTICS]
        if not filtered:
            warnings.append("No valid channels requested; using all defaults.")
            filtered = list(_RD_DEFAULT_CHANNELS)
        validated["channels"] = filtered

        validated["output_dir"] = str(validated.get("output_dir", "output"))
        validated["name"] = str(validated.get("name", "reaction_diffusion"))

        # Advection hook — accept but do not execute yet.
        advection = validated.get("advection_field")
        if advection is not None:
            arr = np.asarray(advection, dtype=np.float64)
            exp = (2, height, width)
            if tuple(arr.shape) != exp:
                warnings.append(
                    f"advection_field shape {tuple(arr.shape)} != {exp}; ignored."
                )
                arr = None
            validated["advection_field"] = arr
        validated["advection_scheme"] = str(validated.get("advection_scheme", "none"))

        return validated, warnings

    # ---------- Execution ----------
    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[reaction_diffusion] %s", w)

        preset = get_preset(validated["preset"])
        cfg = GrayScottSolverConfig(
            width=validated["width"],
            height=validated["height"],
            feed=validated["feed"],
            kill=validated["kill"],
            diffusion_u=validated["diffusion_u"],
            diffusion_v=validated["diffusion_v"],
            dt=validated["dt"],
            steps=validated["steps"],
            seed=validated["seed"],
            seed_patch_fraction=validated["seed_patch_fraction"],
            seed_noise_amplitude=validated["seed_noise_amplitude"],
            advection_field=validated.get("advection_field"),
            advection_scheme=validated.get("advection_scheme", "none"),
        )

        t0 = time.time()
        solver = GrayScottSolver(cfg)
        state = solver.run()
        wall_time = time.time() - t0

        if not np.all(np.isfinite(state.u)) or not np.all(np.isfinite(state.v)):
            raise RuntimeError(
                "Gray-Scott integrator produced non-finite values "
                "(NaN/inf) — divergence trap tripped. Reduce dt or shrink "
                "diffusion coefficients."
            )

        pbr = derive_pbr_from_concentration(
            state.v,
            preset=preset,
            normal_strength=validated["normal_strength"],
            mask_threshold=validated["mask_threshold"],
        )

        # Package outputs.
        output_dir = Path(validated["output_dir"]).resolve()
        stem = validated["name"]
        bundle_dir = output_dir / f"{stem}_bundle"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        channel_files: dict[str, Path] = {}
        if "albedo" in validated["channels"]:
            p = bundle_dir / "albedo.png"
            _save_rgb_png(p, encode_albedo_map(pbr.albedo_rgb))
            channel_files["albedo"] = p
        if "normal" in validated["channels"]:
            p = bundle_dir / "normal.png"
            _save_rgb_png(p, encode_normal_map(pbr.normal_rgb))
            channel_files["normal"] = p
        if "height" in validated["channels"]:
            p = bundle_dir / "height.png"
            _save_gray_png(p, encode_height_map(pbr.height))
            channel_files["height"] = p
        if "mask" in validated["channels"]:
            p = bundle_dir / "mask.png"
            _save_gray_png(p, encode_mask_map(pbr.mask))
            channel_files["mask"] = p

        # Sidecar .npz with raw float fields (loss-less provenance).
        npz_path = bundle_dir / "fields.npz"
        np.savez_compressed(
            npz_path,
            u=state.u.astype(np.float32),
            v=state.v.astype(np.float32),
            height=pbr.height,
            normal_vec=pbr.normal_vec,
            albedo=pbr.albedo_rgb,
            mask=pbr.mask,
        )

        # Manifest payload.
        texture_channels: dict[str, dict[str, Any]] = {}
        for ch_name, ch_path in channel_files.items():
            semantics = _RD_CHANNEL_SEMANTICS[ch_name]
            bit_depth = 8
            texture_channels[ch_name] = {
                "path": str(ch_path.resolve()),
                "dimensions": {
                    "width": validated["width"],
                    "height": validated["height"],
                },
                "bit_depth": bit_depth,
                "color_space": semantics["color_space"],
                "engine_slot": {
                    "unity": semantics["engine_slot_unity"],
                    "godot": semantics["engine_slot_godot"],
                },
                "description": semantics["description"],
            }

        # Build flat outputs dict — MATERIAL_BUNDLE requires at least 'albedo'.
        outputs: dict[str, str] = {
            ch_name: info["path"] for ch_name, info in texture_channels.items()
        }
        if "albedo" not in outputs:
            # Fallback — always emit albedo to satisfy the schema.
            albedo_path = bundle_dir / "albedo.png"
            _save_rgb_png(albedo_path, encode_albedo_map(pbr.albedo_rgb))
            outputs["albedo"] = str(albedo_path.resolve())
            texture_channels["albedo"] = {
                "path": str(albedo_path.resolve()),
                "dimensions": {
                    "width": validated["width"],
                    "height": validated["height"],
                },
                "bit_depth": 8,
                "color_space": _RD_CHANNEL_SEMANTICS["albedo"]["color_space"],
                "engine_slot": {
                    "unity": _RD_CHANNEL_SEMANTICS["albedo"]["engine_slot_unity"],
                    "godot": _RD_CHANNEL_SEMANTICS["albedo"]["engine_slot_godot"],
                },
                "description": _RD_CHANNEL_SEMANTICS["albedo"]["description"],
            }
        outputs["fields_npz"] = str(npz_path.resolve())

        manifest_path = bundle_dir / "manifest.json"
        manifest_payload = {
            "preset": preset.name,
            "pearson_class": preset.pearson_class,
            "feed": cfg.feed,
            "kill": cfg.kill,
            "diffusion_u": cfg.diffusion_u,
            "diffusion_v": cfg.diffusion_v,
            "dt_effective": cfg.effective_dt(),
            "dt_requested": cfg.dt,
            "steps": cfg.steps,
            "width": cfg.width,
            "height": cfg.height,
            "wall_time_seconds": wall_time,
            "u_mean": float(state.u.mean()),
            "v_mean": float(state.v.mean()),
            "u_range": [float(state.u.min()), float(state.u.max())],
            "v_range": [float(state.v.min()), float(state.v.max())],
            "advection_scheme": cfg.advection_scheme,
            "advection_attached": cfg.advection_field is not None,
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        outputs["bundle_manifest"] = str(manifest_path.resolve())

        metadata = {
            "channels": validated["channels"],
            "bundle_kind": "reaction_diffusion",
            "lane": "texture_procedural",
            "renderer": "gray_scott_tensor",
            "bundle_format": "mathart",
            "target_engine": "generic",
            "material_model": "pbr_linear",
            "dimensions": {
                "width": validated["width"],
                "height": validated["height"],
            },
            "preset": preset.name,
            "pearson_class": preset.pearson_class,
            "feed": cfg.feed,
            "kill": cfg.kill,
            "diffusion_u": cfg.diffusion_u,
            "diffusion_v": cfg.diffusion_v,
            "dt_effective": cfg.effective_dt(),
            "steps": cfg.steps,
            "wall_time_seconds": wall_time,
            "normal_strength": validated["normal_strength"],
            "mask_threshold": validated["mask_threshold"],
            "advection_scheme": cfg.advection_scheme,
            "advection_attached": cfg.advection_field is not None,
            "payload": {
                "texture_channels": texture_channels,
                "bundle_path": str(bundle_dir.resolve()),
                "raw_field_archive": str(npz_path.resolve()),
            },
        }

        quality_metrics = {
            "channel_count": float(len(texture_channels)),
            "v_coverage": float((state.v > validated["mask_threshold"]).mean()),
            "u_mean": float(state.u.mean()),
            "v_mean": float(state.v.mean()),
            "wall_time_seconds": float(wall_time),
            "normal_unit_length_error": float(
                np.abs(np.linalg.norm(pbr.normal_vec, axis=-1) - 1.0).max()
            ),
        }

        return ArtifactManifest(
            artifact_family=ArtifactFamily.MATERIAL_BUNDLE.value,
            backend_type=BackendType.REACTION_DIFFUSION,
            version="1.0.0",
            session_id=validated.get("session_id", "SESSION-119"),
            outputs=outputs,
            metadata=metadata,
            quality_metrics=quality_metrics,
            tags=[
                "reaction_diffusion",
                "gray_scott",
                "material_bundle",
                "pbr",
                "session-119",
                f"preset:{preset.name.lower()}",
            ],
        )


__all__ = [
    "ReactionDiffusionBackend",
    "list_preset_names",
]
