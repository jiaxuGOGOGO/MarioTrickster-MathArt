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
]
