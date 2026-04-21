"""VHS-compliant frame sequence exporter for ControlNet guide channels.

SESSION-107 (P1-AI-1)
--------------------
This adapter builds industrial-grade frame sequence directories on top of the
single-frame ControlNet exporters.  The output layout follows ComfyUI
VideoHelperSuite expectations:

- ``frame_00000.png`` ... ``frame_NNNNN.png`` continuous numbering
- one homogeneous directory per sequence
- sidecar ``sequence_metadata.json`` carrying lineage, fps, and resolution

The exporter deliberately keeps file I/O here instead of leaking it back into
``OrthographicPixelRenderEngine``.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from mathart.animation.controlnet_bridge_exporters import (
    DepthMapExportConfig,
    DepthMapExporter,
    ExportResult,
    NormalMapExportConfig,
    NormalMapExporter,
)
from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    validate_artifact_strict,
)
from mathart.core.backend_types import BackendType


@dataclass(frozen=True)
class FrameSequenceExportConfig:
    """Shared sequence export configuration."""

    align_to: int = 8
    fps: int = 12
    flip_image_y_to_opengl: bool = True
    session_id: str = "SESSION-107"


@dataclass(frozen=True)
class FrameSequenceExportResult:
    """Typed result envelope for a frame sequence export."""

    sequence_dir: Path
    metadata_path: Path
    manifest_path: Path
    manifest: ArtifactManifest
    frame_paths: tuple[Path, ...]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_and_save_manifest(manifest: ArtifactManifest, path: Path) -> None:
    errors = validate_artifact_strict(manifest)
    if errors:
        raise ValueError("Artifact manifest validation failed: " + "; ".join(errors))
    _write_json(path, manifest.to_dict())


def _as_list(values: Sequence[np.ndarray] | Iterable[np.ndarray]) -> list[np.ndarray]:
    if isinstance(values, list):
        return values
    return list(values)


class FrameSequenceExporter:
    """Export normal/depth frame batches into VHS-compatible sequence folders."""

    def __init__(self, config: FrameSequenceExportConfig | None = None) -> None:
        self.config = config or FrameSequenceExportConfig()

    def export_normal_sequence(
        self,
        frames: Sequence[np.ndarray] | Iterable[np.ndarray],
        *,
        output_dir: str | Path,
        sequence_name: str,
        coverage_masks: Sequence[np.ndarray | None] | None = None,
        source_manifest: ArtifactManifest | None = None,
        lineage: dict[str, Any] | None = None,
    ) -> FrameSequenceExportResult:
        exporter = NormalMapExporter(
            NormalMapExportConfig(
                align_to=self.config.align_to,
                flip_image_y_to_opengl=self.config.flip_image_y_to_opengl,
                session_id=self.config.session_id,
            )
        )
        return self._export_sequence(
            frames=_as_list(frames),
            output_dir=output_dir,
            sequence_name=sequence_name,
            frame_kind="normal",
            exporter=exporter,
            coverage_masks=coverage_masks,
            source_manifest=source_manifest,
            lineage=lineage,
        )

    def export_depth_sequence(
        self,
        frames: Sequence[np.ndarray] | Iterable[np.ndarray],
        *,
        output_dir: str | Path,
        sequence_name: str,
        coverage_masks: Sequence[np.ndarray | None] | None = None,
        invert_polarity: bool = False,
        source_manifest: ArtifactManifest | None = None,
        lineage: dict[str, Any] | None = None,
    ) -> FrameSequenceExportResult:
        exporter = DepthMapExporter(
            DepthMapExportConfig(
                align_to=self.config.align_to,
                invert_polarity=invert_polarity,
                flip_image_y_to_opengl=self.config.flip_image_y_to_opengl,
                session_id=self.config.session_id,
            )
        )
        return self._export_sequence(
            frames=_as_list(frames),
            output_dir=output_dir,
            sequence_name=sequence_name,
            frame_kind="depth",
            exporter=exporter,
            coverage_masks=coverage_masks,
            source_manifest=source_manifest,
            lineage=lineage,
        )

    def _export_sequence(
        self,
        *,
        frames: list[np.ndarray],
        output_dir: str | Path,
        sequence_name: str,
        frame_kind: str,
        exporter: NormalMapExporter | DepthMapExporter,
        coverage_masks: Sequence[np.ndarray | None] | None,
        source_manifest: ArtifactManifest | None,
        lineage: dict[str, Any] | None,
    ) -> FrameSequenceExportResult:
        if not frames:
            raise ValueError("frames must not be empty")

        first_shape = tuple(np.asarray(frames[0]).shape[:2])
        for index, frame in enumerate(frames[1:], start=1):
            shape = tuple(np.asarray(frame).shape[:2])
            if shape != first_shape:
                raise ValueError(
                    "All frames in a VHS sequence must have identical source resolution; "
                    f"frame 0 has {first_shape!r}, frame {index} has {shape!r}"
                )

        if coverage_masks is None:
            coverage_list: list[np.ndarray | None] = [None] * len(frames)
        else:
            coverage_list = list(coverage_masks)
            if len(coverage_list) != len(frames):
                raise ValueError(
                    f"coverage_masks length {len(coverage_list)} does not match frame count {len(frames)}"
                )

        bundle_root = Path(output_dir).resolve() / f"{sequence_name}_{frame_kind}_sequence"
        sequence_dir = bundle_root / "frames"
        artifacts_dir = bundle_root / "artifacts"
        sequence_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        frame_paths: list[Path] = []
        frame_manifest_hashes: list[str] = []
        frame_sidecars: list[dict[str, Any]] = []
        padded_width = 0
        padded_height = 0

        for index, (frame, coverage_mask) in enumerate(zip(frames, coverage_list)):
            frame_id = f"frame_{index:05d}"
            export_result: ExportResult = exporter.export(
                frame,
                output_dir=artifacts_dir,
                stem=frame_id,
                coverage_mask=coverage_mask,
                source_manifest=source_manifest,
                lineage={
                    **(lineage or {}),
                    "sequence_name": sequence_name,
                    "frame_index": index,
                    "frame_kind": frame_kind,
                },
            )
            target_frame_path = sequence_dir / f"{frame_id}.png"
            shutil.copy2(export_result.image_path, target_frame_path)
            frame_paths.append(target_frame_path)
            frame_manifest_hashes.append(export_result.manifest.schema_hash)
            frame_sidecars.append(
                {
                    "frame_index": index,
                    "frame_path": str(target_frame_path),
                    "artifact_manifest_path": str(export_result.manifest_path),
                    "padding": export_result.padding.to_dict(),
                    "schema_hash": export_result.manifest.schema_hash,
                }
            )
            padded_width = int(export_result.padding.padded_width)
            padded_height = int(export_result.padding.padded_height)

        metadata_path = bundle_root / "sequence_metadata.json"
        manifest_path = bundle_root / f"{sequence_name}_{frame_kind}_sequence_manifest.json"
        metadata = {
            "sequence_name": sequence_name,
            "frame_kind": frame_kind,
            "frame_count": len(frame_paths),
            "fps": int(self.config.fps),
            "frame_width": padded_width,
            "frame_height": padded_height,
            "source_width": int(first_shape[1]),
            "source_height": int(first_shape[0]),
            "frame_naming": "frame_%05d.png",
            "sequence_dir": str(sequence_dir),
            "sequence_metadata_json": str(metadata_path),
            "artifact_family": ArtifactFamily.IMAGE_SEQUENCE.value,
            "backend_type": BackendType.FRAME_SEQUENCE_EXPORT.value,
            "lineage": lineage or {},
            "source_manifest_hash": source_manifest.schema_hash if source_manifest else "",
            "frame_artifacts": frame_sidecars,
        }
        _write_json(metadata_path, metadata)

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.IMAGE_SEQUENCE.value,
            backend_type=BackendType.FRAME_SEQUENCE_EXPORT,
            version="1.0.0",
            session_id=self.config.session_id,
            outputs={
                "sequence_dir": str(sequence_dir),
                "metadata_json": str(metadata_path),
                "first_frame": str(frame_paths[0]),
                "last_frame": str(frame_paths[-1]),
                "manifest_json": str(manifest_path),
            },
            metadata={
                "frame_count": len(frame_paths),
                "frame_width": padded_width,
                "frame_height": padded_height,
                "fps": int(self.config.fps),
                "frame_kind": frame_kind,
                "frame_naming": "frame_%05d.png",
                "source_resolution": {
                    "width": int(first_shape[1]),
                    "height": int(first_shape[0]),
                },
                "padded_resolution": {
                    "width": padded_width,
                    "height": padded_height,
                },
                "lineage": lineage or {},
            },
            quality_metrics={
                "aligned_to_8px": 1.0,
                "frame_count": float(len(frame_paths)),
            },
            references=(
                ([source_manifest.schema_hash] if source_manifest else [])
                + frame_manifest_hashes
            ),
            tags=["vhs", "sequence", frame_kind, "8px_aligned"],
        )
        _validate_and_save_manifest(manifest, manifest_path)
        return FrameSequenceExportResult(
            sequence_dir=sequence_dir,
            metadata_path=metadata_path,
            manifest_path=manifest_path,
            manifest=manifest,
            frame_paths=tuple(frame_paths),
        )


__all__ = [
    "FrameSequenceExportConfig",
    "FrameSequenceExporter",
    "FrameSequenceExportResult",
]
