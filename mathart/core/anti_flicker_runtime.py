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


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION-130: Temporal Variance Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════
#
#  Industrial Reference: AnimateDiff / SparseCtrl temporal conditioning
#  requires genuine per-frame geometric variation in guide sequences.
#  Static or near-static conditioning causes mode collapse in the diffusion
#  model's temporal attention layers.
#
#  This circuit breaker enforces a hard minimum on inter-frame pixel
#  variance BEFORE the sequence reaches the ComfyUI payload assembler.
#  If the guide sequence is effectively static (all frames near-identical),
#  the pipeline is halted immediately with PipelineContractError.
#
#  Reference: Jim Gray, "Why Do Computers Stop and What Can Be Done About
#  It?" (Tandem Computers, 1985) — Fail-Fast principle.
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TemporalVarianceReport:
    """Diagnostic report from temporal variance validation."""

    channel: str
    frame_count: int
    mean_mse: float
    max_mse: float
    min_mse: float
    distinct_pair_count: int
    total_pair_count: int
    passed: bool
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "frame_count": self.frame_count,
            "mean_mse": round(self.mean_mse, 6),
            "max_mse": round(self.max_mse, 6),
            "min_mse": round(self.min_mse, 6),
            "distinct_pair_count": self.distinct_pair_count,
            "total_pair_count": self.total_pair_count,
            "passed": self.passed,
            "threshold": self.threshold,
        }


def validate_temporal_variance(
    frames: Sequence[Image.Image],
    *,
    channel: str = "source",
    mse_threshold: float = 1.0,
    min_distinct_ratio: float = 0.5,
) -> TemporalVarianceReport:
    """Validate that a guide frame sequence has genuine temporal variance.

    This is the **Temporal Variance Circuit Breaker** — a Fail-Fast guard
    that prevents static or near-static guide sequences from reaching the
    AI rendering backend.  AnimateDiff / SparseCtrl temporal attention
    requires real geometric variation between frames; identical conditioning
    frames cause mode collapse or frozen-motion artifacts.

    The validator computes MSE (Mean Squared Error) between consecutive
    frame pairs.  If fewer than ``min_distinct_ratio`` of pairs exceed
    ``mse_threshold``, the sequence is rejected.

    Parameters
    ----------
    frames : Sequence[Image.Image]
        The guide frame sequence to validate.
    channel : str
        Human-readable channel name for diagnostics (e.g., "source", "normal").
    mse_threshold : float
        Minimum MSE between consecutive frames to count as "distinct".
        Default 1.0 (on 0-255 scale) catches sub-pixel jitter forgeries.
    min_distinct_ratio : float
        Minimum fraction of consecutive pairs that must be distinct.
        Default 0.5 means at least half of frame transitions must show
        real motion.

    Returns
    -------
    TemporalVarianceReport
        Diagnostic report including pass/fail status.

    Raises
    ------
    PipelineContractError
        If the sequence fails the temporal variance check.
    """
    from mathart.pipeline_contract import PipelineContractError

    frame_list = list(frames)
    n = len(frame_list)

    if n < 2:
        raise PipelineContractError(
            "temporal_variance_insufficient_frames",
            f"[TemporalVarianceCircuitBreaker] Channel '{channel}': "
            f"guide sequence has {n} frame(s), need >= 2 for temporal validation.",
        )

    mse_values: list[float] = []
    for i in range(n - 1):
        arr_a = np.asarray(frame_list[i].convert("RGB"), dtype=np.float64)
        arr_b = np.asarray(frame_list[i + 1].convert("RGB"), dtype=np.float64)
        mse = float(np.mean((arr_a - arr_b) ** 2))
        mse_values.append(mse)
        # OOM prevention: explicitly delete large arrays after use
        del arr_a, arr_b

    distinct_count = sum(1 for m in mse_values if m > mse_threshold)
    total_pairs = len(mse_values)
    mean_mse = float(np.mean(mse_values)) if mse_values else 0.0
    max_mse = float(np.max(mse_values)) if mse_values else 0.0
    min_mse = float(np.min(mse_values)) if mse_values else 0.0
    passed = (distinct_count / max(1, total_pairs)) >= min_distinct_ratio

    report = TemporalVarianceReport(
        channel=channel,
        frame_count=n,
        mean_mse=mean_mse,
        max_mse=max_mse,
        min_mse=min_mse,
        distinct_pair_count=distinct_count,
        total_pair_count=total_pairs,
        passed=passed,
        threshold=mse_threshold,
    )

    if not passed:
        raise PipelineContractError(
            "temporal_variance_below_threshold",
            f"[TemporalVarianceCircuitBreaker] Channel '{channel}': "
            f"guide sequence FAILED temporal variance check.  "
            f"Only {distinct_count}/{total_pairs} consecutive pairs exceed "
            f"MSE threshold {mse_threshold:.2f} (need ratio >= {min_distinct_ratio:.0%}).  "
            f"Mean MSE = {mean_mse:.4f}, Max MSE = {max_mse:.4f}.  "
            f"This indicates a static or near-static guide sequence that will "
            f"cause mode collapse in AnimateDiff/SparseCtrl temporal attention.  "
            f"The upstream rendering pipeline must produce frames with real "
            f"geometric variation from actual bone-driven animation.",
        )

    return report


def assert_nonzero_temporal_variance(
    frames: Sequence[Image.Image],
    *,
    channel: str = "source",
    mse_floor: float = 0.0001,
) -> None:
    """SESSION-160: Hard per-pair MSE floor assertion (Variance Assert Gate).

    This is the **防静止自爆核弹** — a stricter, non-negotiable assertion that
    fires if ANY consecutive frame pair has MSE below ``mse_floor``.  Unlike
    ``validate_temporal_variance`` which allows a ratio of static pairs, this
    gate enforces that EVERY frame transition shows measurable pixel change.

    Industrial Reference: MSE-based frame differencing is the standard method
    for motion detection in video surveillance and animation QA pipelines.
    A per-pair MSE of 0.0001 on a 0-255 scale corresponds to sub-pixel
    identical frames — a clear sign of rendering forgery or frozen animation.

    Parameters
    ----------
    frames : Sequence[Image.Image]
        The guide frame sequence to validate.
    channel : str
        Human-readable channel name for diagnostics.
    mse_floor : float
        Absolute minimum MSE between any consecutive pair.  If any pair
        falls below this floor, a RuntimeError is raised immediately.

    Raises
    ------
    RuntimeError
        If any consecutive frame pair has MSE < mse_floor.
    """
    frame_list = list(frames)
    n = len(frame_list)
    if n < 2:
        return  # Single-frame sequences cannot be validated

    for i in range(n - 1):
        arr_a = np.asarray(frame_list[i].convert("RGB"), dtype=np.float64)
        arr_b = np.asarray(frame_list[i + 1].convert("RGB"), dtype=np.float64)
        mse = float(np.mean((arr_a - arr_b) ** 2))
        del arr_a, arr_b  # OOM prevention
        if mse < mse_floor:
            raise RuntimeError(
                f"[SESSION-160 VarianceAssertGate] Channel '{channel}': "
                f"frame pair ({i}, {i+1}) has MSE={mse:.8f} < floor={mse_floor:.8f}.  "
                f"This indicates IDENTICAL or near-identical consecutive frames — "
                f"a frozen animation that MUST NOT reach downstream AI rendering.  "
                f"The upstream bone-driven bake pipeline must produce genuine "
                f"per-frame geometric displacement."
            )


def compute_frame_hashes(frames: Sequence[Image.Image]) -> list[str]:
    """Compute SHA-256 hashes of frame pixel data for anti-forgery auditing.

    Used by end-to-end tests to verify that consecutive frames in a guide
    sequence are genuinely distinct (not copies of the same image).

    Parameters
    ----------
    frames : Sequence[Image.Image]
        Frame sequence to hash.

    Returns
    -------
    list[str]
        Per-frame SHA-256 hex digests.
    """
    import hashlib
    hashes: list[str] = []
    for frame in frames:
        pixel_bytes = frame.convert("RGB").tobytes()
        hashes.append(hashlib.sha256(pixel_bytes).hexdigest())
    return hashes


__all__ = [
    "AntiFlickerChunk",
    "RGBSequenceExportResult",
    "MaterializedOutputSequence",
    "TemporalVarianceReport",
    "normalize_server_address",
    "plan_frame_chunks",
    "pil_sequence_to_alpha_masks",
    "pil_sequence_to_normal_arrays",
    "pil_sequence_to_depth_arrays",
    "export_rgb_sequence",
    "materialize_chunk_outputs",
    "validate_temporal_variance",
    "assert_nonzero_temporal_variance",
    "compute_frame_hashes",
]
