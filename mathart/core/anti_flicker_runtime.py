"""Runtime helpers for production anti-flicker ComfyUI execution.

This module keeps chunk planning, sequence export, and output materialization
outside the CLI and away from the central pipeline/orchestrator.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class AntiFlickerChunk:
    """A contiguous frame span scheduled as one GPU-safe execution unit."""

    chunk_index: int
    start_frame: int
    end_frame: int

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame + 1

    def to_dict(self) -> dict[str, int]:
        return {
            "chunk_index": self.chunk_index,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "frame_count": self.frame_count,
        }


@dataclass(frozen=True)
class RGBSequenceExportResult:
    """Export result for an aligned RGB frame sequence."""

    sequence_dir: Path
    metadata_path: Path
    frame_paths: tuple[Path, ...]
    padded_width: int
    padded_height: int
    source_width: int
    source_height: int


@dataclass(frozen=True)
class MaterializedOutputSequence:
    """Unified output directory after merging chunk-level ComfyUI results."""

    sequence_dir: Path
    frame_paths: tuple[Path, ...]
    video_paths: tuple[Path, ...]


def normalize_server_address(url_or_address: str) -> str:
    """Normalize ``http://host:port`` or ``host:port`` into ``host:port``."""
    text = str(url_or_address or "127.0.0.1:8188").strip()
    if "://" not in text:
        return text.rstrip("/")
    parsed = urlparse(text)
    return parsed.netloc or parsed.path.rstrip("/")


def plan_frame_chunks(frame_count: int, chunk_size: int) -> list[AntiFlickerChunk]:
    """Plan contiguous non-overlapping frame chunks.

    The user requested either a 16-frame context window or explicit batch
    chunking. This helper implements the latter deterministically.
    """
    if frame_count < 1:
        raise ValueError("frame_count must be >= 1")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    chunks: list[AntiFlickerChunk] = []
    start = 0
    chunk_index = 0
    while start < frame_count:
        end = min(frame_count - 1, start + chunk_size - 1)
        chunks.append(
            AntiFlickerChunk(
                chunk_index=chunk_index,
                start_frame=start,
                end_frame=end,
            )
        )
        chunk_index += 1
        start = end + 1
    return chunks


def pil_sequence_to_alpha_masks(frames: Sequence[Image.Image] | Iterable[Image.Image]) -> list[np.ndarray]:
    masks: list[np.ndarray] = []
    for frame in frames:
        rgba = frame.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
        if int(alpha.max()) == 0:
            rgb = np.asarray(rgba.convert("RGB"), dtype=np.uint8)
            alpha = np.where(rgb.mean(axis=2) > 0, 255, 0).astype(np.uint8)
        masks.append(alpha > 0)
    return masks


def pil_sequence_to_normal_arrays(frames: Sequence[Image.Image] | Iterable[Image.Image]) -> list[np.ndarray]:
    arrays: list[np.ndarray] = []
    for frame in frames:
        rgb = np.asarray(frame.convert("RGB"), dtype=np.float64)
        normals = rgb / 255.0 * 2.0 - 1.0
        arrays.append(normals)
    return arrays


def pil_sequence_to_depth_arrays(frames: Sequence[Image.Image] | Iterable[Image.Image]) -> list[np.ndarray]:
    arrays: list[np.ndarray] = []
    for frame in frames:
        gray = np.asarray(frame.convert("L"), dtype=np.float64) / 255.0
        arrays.append(gray)
    return arrays


def _compute_padded_size(width: int, height: int, align_to: int) -> tuple[int, int]:
    padded_width = ((width + align_to - 1) // align_to) * align_to
    padded_height = ((height + align_to - 1) // align_to) * align_to
    return padded_width, padded_height


def export_rgb_sequence(
    frames: Sequence[Image.Image] | Iterable[Image.Image],
    *,
    output_dir: str | Path,
    sequence_name: str,
    fps: int = 12,
    align_to: int = 8,
    session_id: str = "SESSION-108",
) -> RGBSequenceExportResult:
    frame_list = list(frames)
    if not frame_list:
        raise ValueError("frames must not be empty")

    source_width, source_height = frame_list[0].size
    for index, frame in enumerate(frame_list[1:], start=1):
        if frame.size != (source_width, source_height):
            raise ValueError(
                "All RGB frames in a chunk must share one source resolution; "
                f"frame 0 is {(source_width, source_height)!r}, frame {index} is {frame.size!r}"
            )

    padded_width, padded_height = _compute_padded_size(source_width, source_height, align_to)
    bundle_root = Path(output_dir).resolve() / f"{sequence_name}_rgb_sequence"
    sequence_dir = bundle_root / "frames"
    sequence_dir.mkdir(parents=True, exist_ok=True)

    frame_paths: list[Path] = []
    for index, frame in enumerate(frame_list):
        canvas = Image.new("RGBA", (padded_width, padded_height), (0, 0, 0, 0))
        canvas.paste(frame.convert("RGBA"), (0, 0))
        frame_path = sequence_dir / f"frame_{index:05d}.png"
        canvas.save(frame_path)
        frame_paths.append(frame_path)

    metadata = {
        "sequence_name": sequence_name,
        "frame_kind": "rgb",
        "frame_count": len(frame_paths),
        "fps": int(fps),
        "frame_width": padded_width,
        "frame_height": padded_height,
        "source_width": source_width,
        "source_height": source_height,
        "frame_naming": "frame_%05d.png",
        "sequence_dir": str(sequence_dir),
        "alignment": align_to,
        "session_id": session_id,
    }
    metadata_path = bundle_root / "sequence_metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return RGBSequenceExportResult(
        sequence_dir=sequence_dir,
        metadata_path=metadata_path,
        frame_paths=tuple(frame_paths),
        padded_width=padded_width,
        padded_height=padded_height,
        source_width=source_width,
        source_height=source_height,
    )


def materialize_chunk_outputs(
    *,
    image_paths: Sequence[str] | Iterable[str],
    video_paths: Sequence[str] | Iterable[str] = (),
    output_dir: str | Path,
    start_index: int = 0,
) -> MaterializedOutputSequence:
    sequence_dir = Path(output_dir).resolve()
    sequence_dir.mkdir(parents=True, exist_ok=True)

    frame_paths: list[Path] = []
    for offset, source in enumerate(image_paths):
        src = Path(source)
        target = sequence_dir / f"frame_{start_index + offset:05d}{src.suffix.lower() or '.png'}"
        shutil.copy2(src, target)
        frame_paths.append(target)

    materialized_videos: list[Path] = []
    for video in video_paths:
        src = Path(video)
        target = sequence_dir / src.name
        shutil.copy2(src, target)
        materialized_videos.append(target)

    return MaterializedOutputSequence(
        sequence_dir=sequence_dir,
        frame_paths=tuple(frame_paths),
        video_paths=tuple(materialized_videos),
    )


__all__ = [
    "AntiFlickerChunk",
    "RGBSequenceExportResult",
    "MaterializedOutputSequence",
    "normalize_server_address",
    "plan_frame_chunks",
    "pil_sequence_to_alpha_masks",
    "pil_sequence_to_normal_arrays",
    "pil_sequence_to_depth_arrays",
    "export_rgb_sequence",
    "materialize_chunk_outputs",
]
