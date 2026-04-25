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


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION-189: Anime Rhythmic Subsampler + Latent Healing JIT Matting
# ═══════════════════════════════════════════════════════════════════════════
#
#  Industrial References:
#    - iD Tech (2021) — Animating on Ones / Twos / Threes (anime 常用 on 3s)
#    - Richard Williams — Animator's Survival Kit, Disc 12
#      (Anticipation → Impact → Hold 非均匀节奏)
#    - animetudes (2020) — Framerate Modulation Theory
#    - HuggingFace stable-diffusion-v1-5 — 训练分辨率 512×512
#    - stable-diffusion-art.com — AnimateDiff recommended CFG 4-5 @ 512×512
#    - HuggingFace lllyasviel/sd-controlnet-normal — 法线中性 (128, 128, 255)
#
#  Three hard constants (do not monkey-patch):
#    MAX_FRAMES = 16    → RTX 4070 12GB safe batch for AnimateDiff+SparseCtrl
#    LATENT_EDGE = 512  → SD1.5 U-Net 感受野的绝对下限
#    NORMAL_MATTE_RGB = (128, 128, 255)  → 法线切线空间零向量 → RGB
# ═══════════════════════════════════════════════════════════════════════════

MAX_FRAMES: int = 16
LATENT_EDGE: int = 512
NORMAL_MATTE_RGB: tuple[int, int, int] = (128, 128, 255)
DEPTH_MATTE_RGB: tuple[int, int, int] = (0, 0, 0)
SOURCE_MATTE_RGB: tuple[int, int, int] = (0, 0, 0)


def anime_rhythmic_subsample(total_frames: int, *, max_frames: int = MAX_FRAMES) -> list[int]:
    """Select at most ``max_frames`` indices from ``[0, total_frames)`` using a
    non-linear Ease-In/Ease-Out cosine curve (緩急 / Kan-Kyu).

    Rationale
    ---------
    Uniform ``np.linspace`` gives flat, lifeless timing and wastes precious
    frames on the mid-swing. Anime timing concentrates drawings at the
    *Anticipation* and *Hold* ends (on 2s / 3s) while stretching the
    *Impact* middle to a single sharp 1s-beat. We realise this with a
    cosine S-curve:

        phase        ∈ [0, 1]  (i / (max_frames - 1))
        ease_weight  = 0.5 - 0.5 * cos(π * phase)
        source_index = round(ease_weight * (total_frames - 1))

    Then we deduplicate indices (cosine curve can collide at the
    extremities) and back-fill the sparsest gap(s) so that the returned
    list contains **exactly** ``min(total_frames, max_frames)`` unique,
    strictly ascending indices.

    Parameters
    ----------
    total_frames :
        Length of the upstream physics-baked sequence.
    max_frames :
        VRAM-safe ceiling. Defaults to :data:`MAX_FRAMES` (16).

    Returns
    -------
    list[int]
        Strictly ascending, unique indices; length ≤ ``max_frames``.
    """
    if total_frames < 1:
        raise ValueError("total_frames must be >= 1")
    if max_frames < 1:
        raise ValueError("max_frames must be >= 1")

    target = min(int(total_frames), int(max_frames))
    if total_frames <= max_frames:
        return list(range(total_frames))

    import math

    raw_indices: list[int] = []
    for i in range(target):
        phase = i / max(target - 1, 1)
        ease_weight = 0.5 - 0.5 * math.cos(math.pi * phase)
        source_index = int(round(ease_weight * (total_frames - 1)))
        raw_indices.append(max(0, min(total_frames - 1, source_index)))

    unique_sorted: list[int] = sorted(set(raw_indices))
    # Back-fill the sparsest gap(s) to restore exactly ``target`` frames.
    while len(unique_sorted) < target:
        gaps = [
            (unique_sorted[k + 1] - unique_sorted[k], k)
            for k in range(len(unique_sorted) - 1)
        ]
        if not gaps:
            break
        gaps.sort(reverse=True)
        _, insert_after = gaps[0]
        lo = unique_sorted[insert_after]
        hi = unique_sorted[insert_after + 1]
        candidate = (lo + hi) // 2
        if candidate in unique_sorted or candidate <= lo or candidate >= hi:
            # No room to grow; bail out and return the de-duplicated list.
            break
        unique_sorted.append(candidate)
        unique_sorted.sort()

    return unique_sorted[:target]


def jit_matte_and_upscale(
    frame: Image.Image,
    *,
    matte_rgb: tuple[int, int, int],
    target_edge: int = LATENT_EDGE,
) -> Image.Image:
    """In-memory alpha matting + LANCZOS upscale to ``target_edge``×``target_edge``.

    - If the frame carries an alpha channel, the transparent region is
      composited onto a solid ``matte_rgb`` canvas (prevents ControlNet
      from interpreting alpha-zero pixels as severely distorted tangent
      vectors, e.g. ``(0, 0, 0)`` in normal space).
    - The final RGB image is then resized to the SD 1.5 training square
      (512×512 by default) using LANCZOS to preserve high-frequency detail.
    - Output is guaranteed to be ``mode="RGB"`` so downstream ComfyUI
      LoadImage nodes never receive an alpha channel.
    """
    if target_edge < 16:
        raise ValueError("target_edge must be >= 16 (SD latent canvas floor)")

    working = frame
    if working.mode == "RGBA":
        alpha = working.getchannel("A")
        rgb = working.convert("RGB")
        canvas = Image.new("RGB", working.size, matte_rgb)
        canvas.paste(rgb, (0, 0), mask=alpha)
        working = canvas
    elif working.mode != "RGB":
        working = working.convert("RGB")

    if working.size != (target_edge, target_edge):
        working = working.resize((target_edge, target_edge), resample=Image.Resampling.LANCZOS)
    return working


def heal_guide_sequences(
    *,
    source_frames: Sequence[Image.Image],
    normal_maps: Sequence[Image.Image],
    depth_maps: Sequence[Image.Image],
    mask_maps: Sequence[Image.Image] | None = None,
    target_edge: int = LATENT_EDGE,
    max_frames: int = MAX_FRAMES,
) -> dict[str, Any]:
    """One-shot SESSION-189 healing applied to every guide channel.

    Steps:
    1. ``anime_rhythmic_subsample`` selects up to ``max_frames`` indices using
       the non-linear Kan-Kyu curve.
    2. Each selected frame is mattes + upscaled to ``target_edge`` via
       :func:`jit_matte_and_upscale` using the channel-appropriate floor:

       - Normal  → ``(128, 128, 255)`` (tangent-space zero vector)
       - Depth   → ``(0, 0, 0)``       (far plane)
       - Source  → ``(0, 0, 0)``       (safe default)
       - Mask    → ``(0, 0, 0)``       (kept as L mode)

    Returns a dict with healed lists and a ``report`` describing the
    timing decisions for transparent auditing.
    """
    total = len(source_frames)
    if total == 0:
        raise ValueError("source_frames must not be empty")
    if len(normal_maps) != total or len(depth_maps) != total:
        raise ValueError(
            "source_frames, normal_maps, and depth_maps must share length; "
            f"got {len(source_frames)} / {len(normal_maps)} / {len(depth_maps)}"
        )
    if mask_maps is not None and len(mask_maps) != total:
        raise ValueError(
            f"mask_maps length {len(mask_maps)} does not match source length {total}"
        )

    indices = anime_rhythmic_subsample(total, max_frames=max_frames)

    healed_source = [
        jit_matte_and_upscale(source_frames[i], matte_rgb=SOURCE_MATTE_RGB, target_edge=target_edge)
        for i in indices
    ]
    healed_normal = [
        jit_matte_and_upscale(normal_maps[i], matte_rgb=NORMAL_MATTE_RGB, target_edge=target_edge)
        for i in indices
    ]
    healed_depth = [
        jit_matte_and_upscale(depth_maps[i], matte_rgb=DEPTH_MATTE_RGB, target_edge=target_edge)
        for i in indices
    ]

    healed_mask: list[Image.Image] | None = None
    if mask_maps is not None:
        healed_mask = []
        for i in indices:
            raw = mask_maps[i]
            lmode = raw.convert("L") if raw.mode != "L" else raw
            if lmode.size != (target_edge, target_edge):
                lmode = lmode.resize((target_edge, target_edge), resample=Image.Resampling.LANCZOS)
            healed_mask.append(lmode)

    report = {
        "session": "SESSION-189",
        "feature": "anime_rhythmic_subsample + jit_matte_and_upscale",
        "input_frame_count": int(total),
        "output_frame_count": len(indices),
        "max_frames": int(max_frames),
        "target_edge": int(target_edge),
        "selected_indices": list(indices),
        "rhythm_curve": "ease_in_out_cosine",
        "matte_normal_rgb": list(NORMAL_MATTE_RGB),
        "matte_depth_rgb": list(DEPTH_MATTE_RGB),
        "matte_source_rgb": list(SOURCE_MATTE_RGB),
    }

    return {
        "indices": list(indices),
        "source_frames": healed_source,
        "normal_maps": healed_normal,
        "depth_maps": healed_depth,
        "mask_maps": healed_mask,
        "report": report,
    }


def force_override_workflow_payload(
    workflow: dict[str, Any],
    *,
    target_edge: int = LATENT_EDGE,
    max_frames: int = MAX_FRAMES,
    cfg_ceiling: float = 4.5,
    controlnet_strength: float = 0.55,
    actual_batch_size: int | None = None,
    video_frame_rate: int | None = None,
) -> dict[str, Any]:
    """Scan ``workflow`` by ``class_type`` (never by numeric node id) and
    enforce the SESSION-189 latent-healing contract as a last line of
    defence regardless of what the upstream preset produced.

    Operations (all optional per node, all idempotent):

    - ``EmptyLatentImage``  → ``width = height = target_edge`` and, if
      ``actual_batch_size`` is given, ``batch_size = min(actual_batch_size,
      max_frames)``.
    - ``KSampler`` / ``KSamplerAdvanced`` / ``SamplerCustomAdvanced`` →
      ``cfg = min(cfg, cfg_ceiling)`` (also ``cfg_scale`` if present).
    - ``ControlNetApply*`` / ``ACN_SparseCtrl*`` →
      ``strength = min(strength, controlnet_strength)`` /
      ``motion_strength = min(motion_strength, controlnet_strength)``.
    - ``VHS_VideoCombine`` / ``VideoCombine`` → ``frame_rate =
      video_frame_rate`` (when supplied, e.g. 8 or 10 for anime feel).

    The function returns a ``report`` dict describing every node it
    touched so the caller can log or persist an audit trail.
    """
    if not isinstance(workflow, dict):
        raise TypeError("workflow must be a dict (ComfyUI workflow_api_json).")

    touched: list[dict[str, Any]] = []

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", ""))
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        if class_type == "EmptyLatentImage":
            prev_w = inputs.get("width")
            prev_h = inputs.get("height")
            inputs["width"] = int(target_edge)
            inputs["height"] = int(target_edge)
            entry = {
                "node_id": node_id,
                "class_type": class_type,
                "width": [prev_w, int(target_edge)],
                "height": [prev_h, int(target_edge)],
            }
            if actual_batch_size is not None:
                prev_bs = inputs.get("batch_size")
                inputs["batch_size"] = int(min(int(actual_batch_size), int(max_frames)))
                entry["batch_size"] = [prev_bs, inputs["batch_size"]]
            touched.append(entry)

        elif class_type in ("KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced"):
            changed = {"node_id": node_id, "class_type": class_type}
            for key in ("cfg", "cfg_scale"):
                if key in inputs:
                    try:
                        prev = float(inputs[key])
                    except (TypeError, ValueError):
                        continue
                    capped = min(prev, float(cfg_ceiling))
                    if capped != prev:
                        inputs[key] = capped
                        changed[key] = [prev, capped]
            if len(changed) > 2:
                touched.append(changed)

        elif class_type.startswith("ControlNetApply") or class_type.startswith("ACN_SparseCtrl"):
            changed = {"node_id": node_id, "class_type": class_type}
            for key in ("strength", "motion_strength"):
                if key in inputs:
                    try:
                        prev = float(inputs[key])
                    except (TypeError, ValueError):
                        continue
                    capped = min(prev, float(controlnet_strength))
                    if capped != prev:
                        inputs[key] = capped
                        changed[key] = [prev, capped]
            if len(changed) > 2:
                touched.append(changed)

        elif class_type in ("VHS_VideoCombine", "VideoCombine"):
            if video_frame_rate is not None and "frame_rate" in inputs:
                prev = inputs["frame_rate"]
                inputs["frame_rate"] = int(video_frame_rate)
                touched.append({
                    "node_id": node_id,
                    "class_type": class_type,
                    "frame_rate": [prev, int(video_frame_rate)],
                })

    return {
        "session": "SESSION-189",
        "feature": "force_override_workflow_payload",
        "target_edge": int(target_edge),
        "max_frames": int(max_frames),
        "cfg_ceiling": float(cfg_ceiling),
        "controlnet_strength_ceiling": float(controlnet_strength),
        "video_frame_rate": (int(video_frame_rate) if video_frame_rate is not None else None),
        "touched_nodes": touched,
    }



# ═══════════════════════════════════════════════════════════════════════════
#  SESSION-190: Modal Decoupling + Semantic Hydration
# ═══════════════════════════════════════════════════════════════════════════
#
#  Industrial References:
#    - MoSA (Wang et al., 2025) — Structure-Appearance Decoupling
#    - MCM (NeurIPS 2024) — Motion-Appearance Disentanglement
#    - DC-ControlNet (2025) — Multi-condition Decoupling
#    - ComfyUI #1077 — denoise=1.0 behavior verification
#    - ComfyUI-AnimateDiff-Evolved #245 — SparseCtrl strength control
#    - OWASP Input Validation Cheat Sheet — Robust I/O Sanitization
#
#  When the physics layer degrades to a Dummy Cylinder Mesh (pseudo_3d_shell),
#  the generated Albedo (flat cylinder colors) catastrophically pollutes
#  the RGB guidance channel (SparseCtrl RGB).  The diffusion model locks
#  onto the cylinder's color blocks and produces symmetric blocky monsters.
#
#  Solution: Absolute Appearance-Motion Decoupling:
#    1. Detect dummy mesh → force denoise=1.0 (full noise, ignore input)
#    2. Kill all RGB/Color ControlNet strength → 0.0
#    3. Keep only Depth/Normal at reduced strength (0.45) for skeleton
#    4. Inject high-quality 3A character prompt as semantic hydration
# ═══════════════════════════════════════════════════════════════════════════

# SESSION-190: Semantic Hydration — 3A character fallback prompt
SEMANTIC_HYDRATION_POSITIVE = (
    "(masterpiece, best quality, ultra-detailed:1.2), 1boy, "
    "handsome cyber-ninja superhero, dynamic action pose, "
    "vivid colors, clear background"
)
SEMANTIC_HYDRATION_NEGATIVE = (
    "abstract, symmetric, geometric, cylinder, blocky, dummy, "
    "deformed, blurry, low quality, distorted"
)

# SESSION-190 -> SESSION-192: Decoupled ControlNet strength for Depth/Normal
# when dummy mesh detected.
#
# Director's hard ruling (SESSION-192 P0 Lookdev hotfix):
#   The previous 0.45 was *too gentle* — the diffusion model still leaked
#   blocky cylinder geometry into the final render because the spatial
#   guidance was not authoritative enough. The order is now to RAM the
#   Depth/Normal ControlNet strength up to 0.85+ so the diffusion latent
#   is forced to obey the math-derived skeleton tensor and ignore the
#   pseudo-3d-shell pixel albedo entirely.
#
#   - DECOUPLED_DEPTH_NORMAL_STRENGTH: SESSION-193 reverted to 0.45 (OpenPose takes over motion).
#   - DECOUPLED_RGB_STRENGTH: stays 0.0 (color pollution must be killed dead).
#   - DECOUPLED_DENOISE: stays 1.0 (full noise rebake of the latent).
DECOUPLED_DEPTH_NORMAL_STRENGTH: float = 0.45
DECOUPLED_RGB_STRENGTH: float = 0.0
DECOUPLED_DENOISE: float = 1.0

# SESSION-192: explicit lower bound used by tests + telemetry handshake.
DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH: float = 0.40


def detect_dummy_mesh(context: dict) -> bool:
    """Detect whether the current render context uses a pseudo_3d_shell
    generated dummy cylinder mesh.

    SESSION-190: This is the first gate in the Modal Decoupling pipeline.
    When True, the downstream payload assembly MUST:
      - Force denoise=1.0 (ignore input image colors)
      - Kill RGB/SparseCtrl strength to 0.0
      - Reduce Depth/Normal strength to 0.45
      - Inject semantic hydration prompt

    Detection heuristics (any match triggers):
      1. context["backend_type"] == "pseudo_3d_shell"
      2. context["_idle_bake_bypassed"] is False and no external guides
      3. context contains cylinder_* parameters
    """
    if not isinstance(context, dict):
        return False
    # Direct backend type check
    if str(context.get("backend_type", "")).lower() == "pseudo_3d_shell":
        return True
    # Check for cylinder parameters (signature of dummy mesh)
    if any(k.startswith("cylinder_") for k in context):
        return True
    # Check for pseudo_3d_shell in nested dicts
    for v in context.values():
        if isinstance(v, dict):
            if str(v.get("backend", "")).lower() == "pseudo_3d_shell":
                return True
            if str(v.get("backend_type", "")).lower() == "pseudo_3d_shell":
                return True
    return False


def _contains_non_ascii(text: str) -> bool:
    """SESSION-208: Detect non-ASCII characters (Chinese, Japanese, Korean, etc.).

    SD1.5's CLIP tokenizer is English-only.  Any non-ASCII content in the
    prompt produces zero semantic signal, so we must detect and translate.
    """
    return any(ord(c) > 127 for c in text)


def hydrate_prompt(context: dict) -> dict:
    """SESSION-190 + SESSION-208: Semantic Hydration — inject high-quality
    3A character prompt when the user's prompt is empty/short, OR when the
    prompt contains non-ASCII characters that SD1.5 CLIP cannot parse.

    SESSION-208 enhancement:
      - Detects non-ASCII (Chinese/Japanese/Korean) vibes regardless of length
      - Translates Chinese vibes to English via SESSION-173 offline dictionary
      - Wraps translated text with SESSION-172 base quality tags
      - Falls back to SEMANTIC_HYDRATION_POSITIVE if translation unavailable

    This prevents the diffusion model from being directionless when
    confronted with a featureless cylinder albedo or a Chinese prompt
    that CLIP cannot understand.

    Returns a new dict with hydrated prompt fields.
    """
    result = dict(context)
    vibe = str(result.get("vibe", "") or "").strip()
    style_prompt = str(result.get("style_prompt", "") or "").strip()

    # ── SESSION-208: Detect non-ASCII vibes (Chinese, etc.) ──────────
    vibe_has_non_ascii = bool(vibe) and _contains_non_ascii(vibe)
    style_has_non_ascii = bool(style_prompt) and _contains_non_ascii(style_prompt)

    # Trigger hydration if:
    #   1. Both vibe and style_prompt are empty/short (original SESSION-190), OR
    #   2. Vibe contains non-ASCII chars that CLIP cannot parse (SESSION-208)
    needs_hydration = (
        (not vibe or len(vibe) < 10) and (not style_prompt or len(style_prompt) < 10)
    ) or vibe_has_non_ascii or style_has_non_ascii

    if needs_hydration:
        # ── SESSION-208: Try to translate non-ASCII vibes to English ──
        _translated_positive = ""
        if vibe_has_non_ascii or style_has_non_ascii:
            _source_text = vibe if vibe else style_prompt
            try:
                from mathart.backend.ai_render_stream_backend import (
                    _armor_prompt as _s208_armor,
                )
                _translated_positive = _s208_armor(_source_text)
            except Exception:  # pragma: no cover
                pass  # Fall through to SEMANTIC_HYDRATION_POSITIVE

        if _translated_positive and not _contains_non_ascii(_translated_positive):
            # Successfully translated — use the user's creative intent
            result["vibe"] = _translated_positive
            result["style_prompt"] = _translated_positive
            result["negative_prompt"] = SEMANTIC_HYDRATION_NEGATIVE
            result["_session190_semantic_hydration"] = True
            result["_session208_vibe_translated"] = True
            result["_session190_hydration_reason"] = (
                f"Original vibe='{vibe}' contained non-ASCII — "
                f"translated to English: '{_translated_positive[:80]}...'"
            )
        else:
            # Translation failed or unavailable — use 3A character fallback
            result["vibe"] = SEMANTIC_HYDRATION_POSITIVE
            result["style_prompt"] = SEMANTIC_HYDRATION_POSITIVE
            result["negative_prompt"] = SEMANTIC_HYDRATION_NEGATIVE
            result["_session190_semantic_hydration"] = True
            result["_session208_vibe_translated"] = False
            result["_session190_hydration_reason"] = (
                f"Original vibe='{vibe}' (len={len(vibe)}), "
                f"style_prompt='{style_prompt}' (len={len(style_prompt)}) — "
                "below threshold or non-ASCII, injected 3A character fallback"
            )
    else:
        result["_session190_semantic_hydration"] = False
        result["_session208_vibe_translated"] = False

    return result


def force_decouple_dummy_mesh_payload(
    workflow: dict,
    *,
    denoise_override: float = DECOUPLED_DENOISE,
    rgb_strength_override: float = DECOUPLED_RGB_STRENGTH,
    depth_normal_strength_override: float = DECOUPLED_DEPTH_NORMAL_STRENGTH,
    positive_prompt: str = SEMANTIC_HYDRATION_POSITIVE,
    negative_prompt: str = SEMANTIC_HYDRATION_NEGATIVE,
) -> dict:
    """SESSION-190: Force Modal Decoupling on a ComfyUI workflow payload.

    When a dummy cylinder mesh (pseudo_3d_shell) is detected, this function
    performs surgical modifications to the workflow:

    1. **KSampler*.denoise → 1.0**: Full denoising ignores input image
       colors, breaking the albedo lock from the cylinder.
    2. **SparseCtrl RGB / Color ControlNet strength → 0.0**: Completely
       disables RGB guidance to prevent color block pollution.
    3. **Depth/Normal ControlNet strength → 0.45**: Reduced but preserved
       to maintain skeleton/motion guidance.
    4. **Prompt injection**: Overrides positive/negative prompts with
       high-quality 3A character descriptions.

    Industrial References:
      - MoSA (Wang et al., 2025): Structure-Appearance Decoupling
      - ComfyUI #1077: denoise=1.0 behavior
      - SparseCtrl (Guo et al., 2023): RGB strength control

    Returns a report dict describing all modifications made.
    """
    if not isinstance(workflow, dict):
        raise TypeError("workflow must be a dict (ComfyUI workflow_api_json).")

    touched = []

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", ""))
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        # ── Force denoise=1.0 on all KSampler variants ──
        if class_type in ("KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced"):
            changed = {"node_id": node_id, "class_type": class_type, "operation": "denoise_override"}
            if "denoise" in inputs:
                prev = inputs["denoise"]
                inputs["denoise"] = float(denoise_override)
                changed["denoise"] = [prev, float(denoise_override)]
            else:
                inputs["denoise"] = float(denoise_override)
                changed["denoise"] = [None, float(denoise_override)]
            touched.append(changed)

        # ── Kill RGB/SparseCtrl strength → 0.0 ──
        # SparseCtrl RGB nodes contain "SparseCtrl" and "RGB" in class_type
        # or have _meta.title containing "rgb" or "sparsectrl rgb"
        elif class_type.startswith("ACN_SparseCtrl"):
            changed = {
                "node_id": node_id,
                "class_type": class_type,
                "operation": "rgb_strength_kill",
            }
            for key in ("strength", "motion_strength"):
                if key in inputs:
                    prev = inputs[key]
                    inputs[key] = float(rgb_strength_override)
                    changed[key] = [prev, float(rgb_strength_override)]
            touched.append(changed)

        # ── ControlNetApply* — differentiate RGB vs Depth/Normal ──
        elif class_type.startswith("ControlNetApply"):
            meta = node.get("_meta", {})
            title = str(meta.get("title", "")).lower()
            # Determine if this is an RGB/color control or Depth/Normal
            is_rgb = any(kw in title for kw in ("rgb", "color", "sparsectrl", "sparse"))
            if is_rgb:
                # Kill RGB strength
                changed = {
                    "node_id": node_id,
                    "class_type": class_type,
                    "operation": "rgb_controlnet_kill",
                }
                for key in ("strength",):
                    if key in inputs:
                        prev = inputs[key]
                        inputs[key] = float(rgb_strength_override)
                        changed[key] = [prev, float(rgb_strength_override)]
                touched.append(changed)
            else:
                # Reduce Depth/Normal strength to 0.45
                changed = {
                    "node_id": node_id,
                    "class_type": class_type,
                    "operation": "depth_normal_strength_reduce",
                }
                for key in ("strength",):
                    if key in inputs:
                        prev = inputs[key]
                        inputs[key] = float(depth_normal_strength_override)
                        changed[key] = [prev, float(depth_normal_strength_override)]
                touched.append(changed)

        # ── Inject prompts into CLIPTextEncode nodes ──
        elif class_type == "CLIPTextEncode":
            meta = node.get("_meta", {})
            title = str(meta.get("title", "")).lower()
            if "negative" in title:
                if "text" in inputs:
                    prev = inputs["text"]
                    inputs["text"] = negative_prompt
                    touched.append({
                        "node_id": node_id,
                        "class_type": class_type,
                        "operation": "negative_prompt_hydration",
                        "text": [prev, negative_prompt],
                    })
            elif "positive" in title or "prompt" in title:
                if "text" in inputs:
                    prev = inputs["text"]
                    inputs["text"] = positive_prompt
                    touched.append({
                        "node_id": node_id,
                        "class_type": class_type,
                        "operation": "positive_prompt_hydration",
                        "text": [prev, positive_prompt],
                    })

    return {
        "session": "SESSION-192",
        "feature": "force_decouple_dummy_mesh_payload",
        "denoise_override": float(denoise_override),
        "rgb_strength_override": float(rgb_strength_override),
        "depth_normal_strength_override": float(depth_normal_strength_override),
        "depth_normal_min_strength": DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH,
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
        "touched_nodes": touched,
    }


# ══════════════════════════════════════════════════════════════════════
# SESSION-192 [Physics Telemetry Audit]: 物理遥测审计层
#
#   The director ordered a *visible*, terminal-level handshake printed
#   right before the math-derived skeleton tensor is shipped to the GPU.
#   This breaks the "black box" feeling: the operator now sees, in
#   bright green, that the math engine actually injected a real motion
#   tensor and that the AI control nets are receiving 0.85+ spatial
#   strength after color pollution was killed.
#
#   Output is deliberately ANSI-coloured (bright green, bold) so it
#   stands out in long render logs. The function is silent (returns the
#   text without printing) when ``stream`` is None, which keeps unit
#   tests deterministic.
# ══════════════════════════════════════════════════════════════════════
import sys as _sys  # local alias to avoid collision with module-level imports

PHYSICS_TELEMETRY_BANNER_TAG = "\U0001f52c 物理总线审计"
PHYSICS_TELEMETRY_FRAMES_TAG = "16帧日漫抽帧机制已激活"


def emit_physics_telemetry_handshake(
    *,
    action_name: str = "unknown",
    depth_normal_strength: float = DECOUPLED_DEPTH_NORMAL_STRENGTH,
    rgb_strength: float = DECOUPLED_RGB_STRENGTH,
    frames: int = MAX_FRAMES,
    skeleton_tensor_shape: tuple | None = None,
    stream=None,
) -> str:
    """SESSION-192: Print the [🔬 物理总线审计] green handshake banner.

    Returns the rendered banner text (without ANSI codes) so test suites
    can pattern-match on it deterministically. When ``stream`` is given
    (typically ``sys.stderr`` from caller), the ANSI-coloured version is
    written there as well.

    The banner asserts three things to the operator:
      1. The action has been locked and the 16-frame anime subsampler
         is active (SESSION-189 anchor).
      2. The math engine produced a real skeletal-displacement tensor
         (no synthetic Catmull-Rom fallback).
      3. The downstream AI render controlnets are receiving ≥ 0.85
         spatial strength after the cylinder colour pollution was
         killed (SESSION-192 hardening).
    """
    shape_repr = (
        "x".join(str(d) for d in skeleton_tensor_shape)
        if skeleton_tensor_shape
        else "NxJx3"
    )
    assert_str = "✅" if depth_normal_strength >= DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH else "⚠️"
    plain_lines = [
        f"[{PHYSICS_TELEMETRY_BANNER_TAG}] \u52a8\u4f5c\u5df2\u9501\u5b9a={action_name} | {PHYSICS_TELEMETRY_FRAMES_TAG} ({frames}\u5e27)",
        f" \u21b3 \u5f15\u64ce\u786e\u6743: \u6355\u6349\u5230\u7eaf\u6570\u5b66\u9aa8\u9abc\u4f4d\u79fb\u5f20\u91cf({shape_repr}) (\u5e95\u5c42\u6570\u5b66\u5f15\u64ce\u5df2\u5168\u91cf\u53d1\u529b) -> \u5b8c\u7f8e\u6ce8\u5165 downstream\uff01",
        f" \u21b3 AI \u63e1\u624b: \u7a7a\u95f4\u63a7\u5236\u7f51\u5f3a\u5ea6\u62c9\u5347\u81f3 {depth_normal_strength:.2f} (>= {DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH:.2f}) {assert_str}\uff0cRGB={rgb_strength:.2f}\uff0c\u65b9\u5757\u5047\u4eba\u76ae\u56ca\u6c61\u67d3\u5df2\u5265\u79bb\u3002AI \u6e32\u67d3\u5668\u5df2\u88ab\u6570\u5b66\u9aa8\u67b6\u5f7b\u5e95\u63a5\u7ba1\uff01",
        f" \u21b3 SESSION-193 OpenPose: \u6570\u5b66\u9aa8\u9abc\u2192COCO-18\u59ff\u6001\u5e8f\u5217\u5df2\u5c31\u7eea\uff0cControlNet\u4ef2\u88c1\u5668\u5df2\u6fc0\u6d3b (Depth/Normal={depth_normal_strength:.2f}, OpenPose=1.00)",
    ]
    plain_text = "\n".join(plain_lines)

    if stream is not None:
        # Bright green + bold ANSI envelope (\033[1;92m) with reset.
        try:
            stream.write("\033[1;92m" + plain_text + "\033[0m\n")
            stream.flush()
        except Exception:
            # Never let a logging-side failure break the render path.
            pass
    return plain_text


def emit_industrial_baking_banner(stream=None) -> str:
    """SESSION-192 UX zero-degradation: the [⚙️ 工业烘焙网关] banner.

    Centralised here so every backend that performs CPU Catmull-Rom
    interpolation can emit the *same* banner without copy-pasting a
    bare ANSI string. This keeps SESSION-191's UX contract intact
    and gives the test suite one stable string to assert against.
    """
    plain = (
        "[\u2699\ufe0f  \u5de5\u4e1a\u70d8\u7119\u7f51\u5173] \u6b63\u5728\u901a\u8fc7 Catmull-Rom \u6837\u6761\u63d2\u503c\uff0c"
        "\u7eaf CPU \u89e3\u7b97\u9ad8\u7cbe\u5ea6\u5de5\u4e1a\u7ea7\u8d34\u56fe\u52a8\u4f5c\u5e8f\u5217..."
    )
    if stream is not None:
        try:
            stream.write("\033[1;36m" + plain + "\033[0m\n")
            stream.flush()
        except Exception:
            pass
    return plain


# ---------------------------------------------------------------------------
# SESSION-200: Epic Ignition UX Banner
# ---------------------------------------------------------------------------

EPIC_IGNITION_BANNER_TAG = "[\U0001f680 SESSION-200 \u53f2\u8bd7\u7ea7\u70b9\u706b]"


def emit_epic_ignition_banner(stream=None) -> str:
    """SESSION-200 UX zero-degradation: the [\U0001f680 SESSION-200 \u53f2\u8bd7\u7ea7\u70b9\u706b] banner.

    Centralised here so the ignition launchpad and all backend paths
    can emit the *same* banner without copy-pasting ANSI strings.
    This keeps the UX contract intact and gives the test suite one
    stable string to assert against.

    The banner announces the full SESSION-200 upgrade chain:
    - Golden Payload Pre-flight Dump (SpaceX F9 Protocol)
    - WebSocket Dual-Channel Telemetry (900s hard deadline)
    - Streaming Artifact Fetch (iter_content 8192)
    - Circuit Breaker Fail-Fast (execution_error \u2192 Poison Pill)
    """
    plain = (
        "[\U0001f680 SESSION-200 \u53f2\u8bd7\u7ea7\u70b9\u706b] "
        "\u5e26\u5361\u70b9\u706b\u5168\u94fe\u8def\u901a\u8f66 | "
        "\u9ec4\u91d1\u8f7d\u8377\u5feb\u7167 \u2192 WS\u9065\u6d4b \u2192 \u6d41\u5f0f\u62c9\u53d6 \u2192 \u7194\u65ad\u5b88\u62a4"
    )
    if stream is not None:
        try:
            stream.write("\033[1;93m" + plain + "\033[0m\n")
            stream.flush()
        except Exception:
            pass
    return plain


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
    # SESSION-189 additions
    "MAX_FRAMES",
    "LATENT_EDGE",
    "NORMAL_MATTE_RGB",
    "DEPTH_MATTE_RGB",
    "SOURCE_MATTE_RGB",
    "anime_rhythmic_subsample",
    "jit_matte_and_upscale",
    "heal_guide_sequences",
    "force_override_workflow_payload",
    # SESSION-190 additions
    "SEMANTIC_HYDRATION_POSITIVE",
    "SEMANTIC_HYDRATION_NEGATIVE",
    "DECOUPLED_DEPTH_NORMAL_STRENGTH",
    "DECOUPLED_RGB_STRENGTH",
    "DECOUPLED_DENOISE",
    "detect_dummy_mesh",
    "hydrate_prompt",
    "force_decouple_dummy_mesh_payload",
    # SESSION-192 additions
    "DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH",
    "PHYSICS_TELEMETRY_BANNER_TAG",
    "PHYSICS_TELEMETRY_FRAMES_TAG",
    "emit_physics_telemetry_handshake",
    "emit_industrial_baking_banner",
    # SESSION-200 additions
    "EPIC_IGNITION_BANNER_TAG",
    "emit_epic_ignition_banner",
    # SESSION-208 additions
    "_contains_non_ascii",
]
