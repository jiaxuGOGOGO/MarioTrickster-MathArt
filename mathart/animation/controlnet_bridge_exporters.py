"""ControlNet bridge exporters for math-native render buffers.

SESSION-107 (P1-AI-1)
--------------------
This module isolates all disk I/O and visual quantization required to bridge
``OrthographicPixelRenderEngine`` float64 buffers into ControlNet-compatible
assets.  The renderer remains pure in-memory math; exporters act as independent
Ports & Adapters that consume typed NumPy buffers and emit typed
``ArtifactManifest`` objects.

Architecture discipline
-----------------------
- ✅ Anti-Pipeline-Bleed: all PNG / JSON writes live here, not in the renderer.
- ✅ Strong-type contract: every export returns a validated ``ArtifactManifest``.
- ✅ 8-pixel alignment: all exported dimensions are padded to Stable Diffusion's
  VAE scale factor (default 8).
- ✅ Explicit color mapping: normals use ``(N*0.5+0.5)*255`` with no lossy
  direct ``astype(np.uint8)`` on the raw float field.

Research alignment
------------------
1. ControlNet 1.1 / NormalBae accepts real rendering-engine normal maps when
   the color protocol is correct: blue=front, red=x, green=y.
2. ControlNet depth accepts real 3D-engine depth when normalized into a stable
   grayscale polarity.
3. Diffusers / Stable Diffusion VAE uses a scale factor of 8, so inputs must be
   divisible by 8 for reliable latent packing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    validate_artifact_strict,
)
from mathart.core.backend_types import BackendType


_SD_ALIGNMENT = 8
_EPS = 1e-12
_NEUTRAL_NORMAL = np.array([0.0, 0.0, 1.0], dtype=np.float64)


@dataclass(frozen=True)
class PaddingInfo:
    """Deterministic right/bottom padding metadata."""

    original_width: int
    original_height: int
    padded_width: int
    padded_height: int
    pad_right: int
    pad_bottom: int
    alignment: int = _SD_ALIGNMENT

    @property
    def applied(self) -> bool:
        return self.pad_right > 0 or self.pad_bottom > 0

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "original_width": self.original_width,
            "original_height": self.original_height,
            "padded_width": self.padded_width,
            "padded_height": self.padded_height,
            "pad_right": self.pad_right,
            "pad_bottom": self.pad_bottom,
            "alignment": self.alignment,
            "applied": self.applied,
        }


@dataclass(frozen=True)
class NormalMapExportConfig:
    """Configuration for exporting ControlNet NormalBae guide images."""

    align_to: int = _SD_ALIGNMENT
    flip_image_y_to_opengl: bool = True
    session_id: str = "SESSION-107"


@dataclass(frozen=True)
class DepthMapExportConfig:
    """Configuration for exporting ControlNet depth guide images."""

    align_to: int = _SD_ALIGNMENT
    invert_polarity: bool = False
    flip_image_y_to_opengl: bool = True
    session_id: str = "SESSION-107"


@dataclass(frozen=True)
class ExportResult:
    """Typed return envelope for exporter calls."""

    image_path: Path
    metadata_path: Path
    manifest_path: Path
    manifest: ArtifactManifest
    padding: PaddingInfo


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _compute_padding(width: int, height: int, alignment: int) -> PaddingInfo:
    if alignment <= 0:
        raise ValueError(f"alignment must be positive, got {alignment}")
    padded_width = ((width + alignment - 1) // alignment) * alignment
    padded_height = ((height + alignment - 1) // alignment) * alignment
    return PaddingInfo(
        original_width=width,
        original_height=height,
        padded_width=padded_width,
        padded_height=padded_height,
        pad_right=padded_width - width,
        pad_bottom=padded_height - height,
        alignment=alignment,
    )


def _normalize_coverage_mask(
    coverage_mask: np.ndarray | None,
    shape: tuple[int, int],
) -> np.ndarray:
    if coverage_mask is None:
        return np.ones(shape, dtype=bool)
    mask = np.asarray(coverage_mask, dtype=bool)
    if mask.shape != shape:
        raise ValueError(
            f"coverage_mask shape {mask.shape!r} does not match image shape {shape!r}"
        )
    return mask


def _flip_image_y_up(
    array: np.ndarray,
    coverage_mask: np.ndarray,
    *,
    enabled: bool,
) -> tuple[np.ndarray, np.ndarray]:
    if not enabled:
        return array, coverage_mask
    return np.flip(array, axis=0), np.flip(coverage_mask, axis=0)


def _pad_image(
    image: np.ndarray,
    padding: PaddingInfo,
    *,
    fill_value: float | int | tuple[float, ...] | tuple[int, ...],
) -> np.ndarray:
    if not padding.applied:
        return image.copy()
    if image.ndim == 2:
        out = np.full(
            (padding.padded_height, padding.padded_width),
            fill_value,
            dtype=image.dtype,
        )
        out[: padding.original_height, : padding.original_width] = image
        return out
    if image.ndim == 3:
        channels = image.shape[2]
        fill = np.asarray(fill_value, dtype=image.dtype)
        if fill.ndim == 0:
            fill = np.full((channels,), fill, dtype=image.dtype)
        if fill.shape != (channels,):
            raise ValueError(
                f"fill_value has shape {fill.shape!r}, expected {(channels,)!r}"
            )
        out = np.zeros(
            (padding.padded_height, padding.padded_width, channels),
            dtype=image.dtype,
        )
        out[:, :] = fill
        out[: padding.original_height, : padding.original_width] = image
        return out
    raise ValueError(f"unsupported image rank {image.ndim}; expected 2 or 3")


def _save_png_rgb(path: Path, rgb: np.ndarray) -> None:
    _ensure_parent_dir(path)
    Image.fromarray(rgb, mode="RGB").save(str(path))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_parent_dir(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_and_save_manifest(manifest: ArtifactManifest, path: Path) -> None:
    errors = validate_artifact_strict(manifest)
    if errors:
        raise ValueError("Artifact manifest validation failed: " + "; ".join(errors))
    _write_json(path, manifest.to_dict())


def _coerce_normals(raw_normals: np.ndarray, coverage_mask: np.ndarray) -> np.ndarray:
    normals = np.asarray(raw_normals, dtype=np.float64)
    if normals.ndim != 3 or normals.shape[2] != 3:
        raise ValueError(
            f"raw_normals must have shape (H, W, 3), got {normals.shape!r}"
        )
    normals = normals.copy()
    invalid = ~np.isfinite(normals).all(axis=2)
    normals[invalid] = _NEUTRAL_NORMAL
    lengths = np.linalg.norm(normals, axis=2, keepdims=True)
    safe_lengths = np.where(lengths > _EPS, lengths, 1.0)
    normals = normals / safe_lengths
    normals = np.clip(normals, -1.0, 1.0)
    normals[~coverage_mask] = _NEUTRAL_NORMAL
    return normals


def encode_controlnet_normal_rgb(normals: np.ndarray) -> np.ndarray:
    """Encode normalized float64 normals to 8-bit RGB for ControlNet.

    The mapping is intentionally explicit and non-rounded so the canonical basis
    vector ``[0, 0, 1]`` maps to ``[127, 127, 255]`` exactly, catching the
    forbidden direct-astype anti-pattern.
    """
    normals = np.asarray(normals, dtype=np.float64)
    if normals.ndim != 3 or normals.shape[2] != 3:
        raise ValueError(f"normals must have shape (H, W, 3), got {normals.shape!r}")
    encoded = np.clip((normals * 0.5 + 0.5) * 255.0, 0.0, 255.0).astype(np.uint8)
    return encoded


class NormalMapExporter:
    """Export float64 normal buffers into ControlNet NormalBae PNG assets."""

    def __init__(self, config: NormalMapExportConfig | None = None) -> None:
        self.config = config or NormalMapExportConfig()

    def export(
        self,
        raw_normals: np.ndarray,
        *,
        output_dir: str | Path,
        stem: str,
        coverage_mask: np.ndarray | None = None,
        source_manifest: ArtifactManifest | None = None,
        lineage: dict[str, Any] | None = None,
    ) -> ExportResult:
        normals = np.asarray(raw_normals, dtype=np.float64)
        if normals.ndim != 3 or normals.shape[2] != 3:
            raise ValueError(
                f"raw_normals must have shape (H, W, 3), got {normals.shape!r}"
            )
        h, w, _ = normals.shape
        coverage = _normalize_coverage_mask(coverage_mask, (h, w))
        normals = _coerce_normals(normals, coverage)
        normals, coverage = _flip_image_y_up(
            normals,
            coverage,
            enabled=self.config.flip_image_y_to_opengl,
        )
        padding = _compute_padding(w, h, self.config.align_to)
        padded_normals = _pad_image(normals, padding, fill_value=tuple(_NEUTRAL_NORMAL))
        padded_rgb = encode_controlnet_normal_rgb(padded_normals)

        bundle_dir = Path(output_dir).resolve()
        image_path = bundle_dir / f"{stem}_normal_controlnet.png"
        metadata_path = bundle_dir / f"{stem}_normal_controlnet_metadata.json"
        manifest_path = bundle_dir / f"{stem}_normal_controlnet_manifest.json"
        _save_png_rgb(image_path, padded_rgb)

        coverage_ratio = float(np.mean(coverage)) if coverage.size else 0.0
        metadata = {
            "width": int(padded_rgb.shape[1]),
            "height": int(padded_rgb.shape[0]),
            "channels": 3,
            "bit_depth": 8,
            "artifact_role": "controlnet_normal_map",
            "color_space": "linear_rgb",
            "normal_mapping": "(N*0.5+0.5)*255",
            "normal_basis": {
                "red": "x",
                "green": "y",
                "blue": "z",
                "reference_phrase": "blue_is_front_red_is_x_green_is_y",
            },
            "source_coordinate_convention": "mathart_z_up_worldspace",
            "target_coordinate_convention": "controlnet_normalbae_opengl_visualization",
            "flip_image_y_to_opengl": bool(self.config.flip_image_y_to_opengl),
            "padding": padding.to_dict(),
            "coverage_ratio": coverage_ratio,
            "lineage": lineage or {},
        }
        sidecar = {
            **metadata,
            "image_path": str(image_path),
            "source_manifest_hash": source_manifest.schema_hash if source_manifest else "",
        }
        _write_json(metadata_path, sidecar)

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SINGLE.value,
            backend_type=BackendType.CONTROLNET_NORMAL_EXPORT,
            version="1.0.0",
            session_id=self.config.session_id,
            outputs={
                "image": str(image_path),
                "metadata_json": str(metadata_path),
                "manifest_json": str(manifest_path),
            },
            metadata=metadata,
            quality_metrics={
                "coverage_ratio": coverage_ratio,
                "aligned_to_8px": 1.0,
                "normal_z_plus_blue": float(padded_rgb[0, 0, 2] == 255),
            },
            references=[source_manifest.schema_hash] if source_manifest else [],
            tags=["controlnet", "normal", "normalbae", "8px_aligned"],
        )
        _validate_and_save_manifest(manifest, manifest_path)
        return ExportResult(
            image_path=image_path,
            metadata_path=metadata_path,
            manifest_path=manifest_path,
            manifest=manifest,
            padding=padding,
        )


def _normalize_depth_field(
    raw_depth: np.ndarray,
    coverage_mask: np.ndarray,
) -> tuple[np.ndarray, float, float]:
    depth = np.asarray(raw_depth, dtype=np.float64)
    if depth.ndim != 2:
        raise ValueError(f"raw_depth must have shape (H, W), got {depth.shape!r}")
    normalized = np.zeros_like(depth, dtype=np.float64)
    finite = np.isfinite(depth) & coverage_mask
    if not np.any(finite):
        return normalized, 0.0, 0.0
    lo = float(np.min(depth[finite]))
    hi = float(np.max(depth[finite]))
    if hi - lo <= _EPS:
        normalized[finite] = 1.0
        return normalized, lo, hi
    normalized[finite] = (depth[finite] - lo) / (hi - lo)
    normalized = np.clip(normalized, 0.0, 1.0)
    return normalized, lo, hi


class DepthMapExporter:
    """Export float64 depth buffers into ControlNet-compatible grayscale PNG."""

    def __init__(self, config: DepthMapExportConfig | None = None) -> None:
        self.config = config or DepthMapExportConfig()

    def export(
        self,
        raw_depth: np.ndarray,
        *,
        output_dir: str | Path,
        stem: str,
        coverage_mask: np.ndarray | None = None,
        source_manifest: ArtifactManifest | None = None,
        lineage: dict[str, Any] | None = None,
    ) -> ExportResult:
        depth = np.asarray(raw_depth, dtype=np.float64)
        if depth.ndim != 2:
            raise ValueError(f"raw_depth must have shape (H, W), got {depth.shape!r}")
        h, w = depth.shape
        coverage = _normalize_coverage_mask(coverage_mask, (h, w))
        depth, coverage = _flip_image_y_up(
            depth,
            coverage,
            enabled=self.config.flip_image_y_to_opengl,
        )
        normalized, src_min, src_max = _normalize_depth_field(depth, coverage)
        far_value = 1.0 if self.config.invert_polarity else 0.0
        gray = 1.0 - normalized if self.config.invert_polarity else normalized
        gray[~coverage] = far_value
        padding = _compute_padding(w, h, self.config.align_to)
        padded_gray = _pad_image(gray, padding, fill_value=far_value)
        padded_rgb = np.repeat(
            np.clip(padded_gray * 255.0, 0.0, 255.0).astype(np.uint8)[..., None],
            3,
            axis=2,
        )

        bundle_dir = Path(output_dir).resolve()
        image_path = bundle_dir / f"{stem}_depth_controlnet.png"
        metadata_path = bundle_dir / f"{stem}_depth_controlnet_metadata.json"
        manifest_path = bundle_dir / f"{stem}_depth_controlnet_manifest.json"
        _save_png_rgb(image_path, padded_rgb)

        coverage_ratio = float(np.mean(coverage)) if coverage.size else 0.0
        metadata = {
            "width": int(padded_rgb.shape[1]),
            "height": int(padded_rgb.shape[0]),
            "channels": 3,
            "bit_depth": 8,
            "artifact_role": "controlnet_depth_map",
            "color_space": "linear_grayscale_rgb",
            "depth_semantics": "linear_depth_normalized",
            "depth_polarity": "near_black_far_white" if self.config.invert_polarity else "near_white_far_black",
            "flip_image_y_to_opengl": bool(self.config.flip_image_y_to_opengl),
            "source_depth_range": {
                "min": src_min,
                "max": src_max,
            },
            "padding": padding.to_dict(),
            "coverage_ratio": coverage_ratio,
            "lineage": lineage or {},
        }
        sidecar = {
            **metadata,
            "image_path": str(image_path),
            "source_manifest_hash": source_manifest.schema_hash if source_manifest else "",
        }
        _write_json(metadata_path, sidecar)

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SINGLE.value,
            backend_type=BackendType.CONTROLNET_DEPTH_EXPORT,
            version="1.0.0",
            session_id=self.config.session_id,
            outputs={
                "image": str(image_path),
                "metadata_json": str(metadata_path),
                "manifest_json": str(manifest_path),
            },
            metadata=metadata,
            quality_metrics={
                "coverage_ratio": coverage_ratio,
                "aligned_to_8px": 1.0,
                "dynamic_range": float(np.max(padded_gray) - np.min(padded_gray)),
            },
            references=[source_manifest.schema_hash] if source_manifest else [],
            tags=["controlnet", "depth", "midas", "zoe", "8px_aligned"],
        )
        _validate_and_save_manifest(manifest, manifest_path)
        return ExportResult(
            image_path=image_path,
            metadata_path=metadata_path,
            manifest_path=manifest_path,
            manifest=manifest,
            padding=padding,
        )


__all__ = [
    "DepthMapExportConfig",
    "DepthMapExporter",
    "ExportResult",
    "NormalMapExportConfig",
    "NormalMapExporter",
    "PaddingInfo",
    "encode_controlnet_normal_rgb",
]
