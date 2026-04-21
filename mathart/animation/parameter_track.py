"""Tensorized parameter tracks for time-varying SDF animation.

SESSION-122 / P1-2 research-grounded implementation.

This module introduces a *wrapper-layer* dynamic parameter system so that the
existing static SDF morphology trunk can remain backward-compatible.  The core
idea follows three reference pillars:

1. Inigo Quilez smooth SDF modeling:
   animate SDF *parameters* (radius, scale, blend thickness) rather than
   directly editing mesh vertices.
2. Disney squash & stretch:
   provide volume-preserving axis linkage so elastic scaling does not inflate
   volume arbitrarily.
3. Pixar OpenUSD TimeSamples:
   keep a sparse keyframe source representation and a dense sampled value
   representation, both serializable as ``time -> value`` mappings.

Red-line guarantees:
- No Python ``for t in frames`` loops in the hot-path interpolation kernel.
- Entire frame ranges are sampled in one NumPy vectorized pass.
- Dynamic evaluation is intended to be *streamed* frame-by-frame by callers;
  this module never caches a full 4D distance volume.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

import numpy as np


ArrayLike1D = Sequence[float] | np.ndarray
ArrayLikeValue = float | Sequence[float] | np.ndarray


@dataclass(frozen=True)
class ParameterKeyframe:
    """A single sparse keyframe for a parameter track."""

    time: float
    value: ArrayLikeValue


@dataclass(frozen=True)
class SampledParameterMatrix:
    """Dense sampled result over a frame/time tensor.

    Attributes
    ----------
    track_names:
        Flattened parameter-channel names in column order.
    time_codes:
        Sampled time tensor of shape ``(N,)``.
    values:
        Dense sampled matrix of shape ``(N, P)``.
    """

    track_names: tuple[str, ...]
    time_codes: np.ndarray
    values: np.ndarray


@dataclass
class ParameterTrack:
    """A tensorized keyframed parameter track.

    Parameters are authored sparsely as keyframes and evaluated densely over an
    arbitrary time tensor using a fully vectorized interpolation kernel.

    ``interpolation='catmull_rom'`` uses cubic Hermite evaluation with
    Catmull-Rom tangents, which preserves interpolation at knots and C1
    continuity between segments.
    """

    name: str
    keyframes: list[ParameterKeyframe] = field(default_factory=list)
    interpolation: str = "catmull_rom"
    clip_min: Optional[ArrayLikeValue] = None
    clip_max: Optional[ArrayLikeValue] = None

    def __post_init__(self) -> None:
        if not self.keyframes:
            raise ValueError("ParameterTrack requires at least one keyframe")
        if self.interpolation not in {"catmull_rom", "linear", "hold"}:
            raise ValueError(
                "interpolation must be one of 'catmull_rom', 'linear', 'hold'"
            )

    @property
    def dimension(self) -> int:
        """Return parameter dimensionality (1 for scalar tracks)."""
        _, values = self._sorted_arrays()
        return int(values.shape[1])

    def _sorted_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        pairs = sorted(
            ((float(kf.time), np.asarray(kf.value, dtype=np.float64))
             for kf in self.keyframes),
            key=lambda item: item[0],
        )
        times = np.asarray([p[0] for p in pairs], dtype=np.float64)
        if np.any(np.diff(times) < 0.0):
            raise ValueError("keyframe times must be sorted ascending")
        if np.any(np.diff(times) == 0.0):
            raise ValueError("duplicate keyframe times are not allowed")

        values = np.asarray([p[1] for p in pairs], dtype=np.float64)
        if values.ndim == 1:
            values = values[:, None]
        elif values.ndim != 2:
            raise ValueError("keyframe values must be scalar or 1D vectors")
        return times, values

    def sample(self, time_codes: ArrayLike1D) -> np.ndarray:
        """Sample the track on a full time tensor in one vectorized pass."""
        t = np.asarray(time_codes, dtype=np.float64)
        if t.ndim != 1:
            raise ValueError("time_codes must be a 1D tensor")

        kt, kv = self._sorted_arrays()
        n_keys, dim = kv.shape
        if n_keys == 1:
            sampled = np.broadcast_to(kv[[0]], (t.shape[0], dim)).copy()
            return self._apply_clip(sampled, squeeze=(dim == 1))

        idx = np.searchsorted(kt, t, side="right") - 1
        idx = np.clip(idx, 0, n_keys - 2)

        p1 = kv[idx]
        p2 = kv[idx + 1]

        if self.interpolation == "hold":
            return self._apply_clip(p1.copy(), squeeze=(dim == 1))

        t1 = kt[idx]
        t2 = kt[idx + 1]
        dt = np.maximum(t2 - t1, 1e-12)
        u = np.clip((t - t1) / dt, 0.0, 1.0)
        u_col = u[:, None]

        if self.interpolation == "linear":
            sampled = (1.0 - u_col) * p1 + u_col * p2
            return self._apply_clip(sampled, squeeze=(dim == 1))

        tangents = np.zeros_like(kv)
        tangents[0] = (kv[1] - kv[0]) / max(kt[1] - kt[0], 1e-12)
        tangents[-1] = (kv[-1] - kv[-2]) / max(kt[-1] - kt[-2], 1e-12)
        if n_keys > 2:
            denom = np.maximum((kt[2:] - kt[:-2])[:, None], 1e-12)
            tangents[1:-1] = (kv[2:] - kv[:-2]) / denom

        m1 = tangents[idx]
        m2 = tangents[idx + 1]
        dt_col = dt[:, None]

        h00 = 2.0 * u_col**3 - 3.0 * u_col**2 + 1.0
        h10 = u_col**3 - 2.0 * u_col**2 + u_col
        h01 = -2.0 * u_col**3 + 3.0 * u_col**2
        h11 = u_col**3 - u_col**2

        sampled = (
            h00 * p1
            + h10 * dt_col * m1
            + h01 * p2
            + h11 * dt_col * m2
        )
        return self._apply_clip(sampled, squeeze=(dim == 1))

    def to_time_samples(
        self,
        time_codes: ArrayLike1D,
        sampled_values: Optional[np.ndarray] = None,
    ) -> dict[float, Any]:
        """Serialize sampled values as an OpenUSD-style time mapping."""
        times = np.asarray(time_codes, dtype=np.float64)
        values = self.sample(times) if sampled_values is None else np.asarray(sampled_values)
        if values.ndim == 1:
            return {float(t): float(v) for t, v in zip(times.tolist(), values.tolist())}
        return {
            float(t): [float(x) for x in row]
            for t, row in zip(times.tolist(), values.tolist())
        }

    def _apply_clip(self, sampled: np.ndarray, *, squeeze: bool) -> np.ndarray:
        dim = sampled.shape[1]
        if self.clip_min is not None or self.clip_max is not None:
            lo = _prepare_bound(self.clip_min, dim, fill=-np.inf)
            hi = _prepare_bound(self.clip_max, dim, fill=np.inf)
            sampled = np.clip(sampled, lo[None, :], hi[None, :])
        if squeeze:
            return sampled[:, 0]
        return sampled


@dataclass
class ParameterTrackBundle:
    """A registry-like bundle of named parameter tracks."""

    tracks: dict[str, ParameterTrack] = field(default_factory=dict)

    def register(self, track: ParameterTrack) -> None:
        self.tracks[track.name] = track

    def sample_all(self, time_codes: ArrayLike1D) -> dict[str, np.ndarray]:
        t = np.asarray(time_codes, dtype=np.float64)
        return {name: track.sample(t) for name, track in self.tracks.items()}

    def sample_parameter_matrix(
        self,
        time_codes: ArrayLike1D,
        track_order: Optional[Sequence[str]] = None,
    ) -> SampledParameterMatrix:
        """Return a dense ``[frames, params]`` matrix from all tracks."""
        t = np.asarray(time_codes, dtype=np.float64)
        order = tuple(track_order or tuple(self.tracks.keys()))
        if not order:
            return SampledParameterMatrix(track_names=(), time_codes=t, values=np.zeros((t.shape[0], 0), dtype=np.float64))

        sampled_blocks: list[np.ndarray] = []
        names: list[str] = []
        for name in order:
            track = self.tracks[name]
            block = np.asarray(track.sample(t), dtype=np.float64)
            if block.ndim == 1:
                block = block[:, None]
                names.append(name)
            else:
                names.extend([f"{name}[{i}]" for i in range(block.shape[1])])
            sampled_blocks.append(block)

        values = np.concatenate(sampled_blocks, axis=1) if sampled_blocks else np.zeros((t.shape[0], 0), dtype=np.float64)
        return SampledParameterMatrix(
            track_names=tuple(names),
            time_codes=t,
            values=values,
        )

    def resolve_frame_context(
        self,
        sampled_tracks: Mapping[str, np.ndarray],
        frame_index: int,
    ) -> dict[str, Any]:
        """Resolve one frame's scalar/vector values for streaming SDF evaluation."""
        context: dict[str, Any] = {}
        for name, values in sampled_tracks.items():
            arr = np.asarray(values)
            value = arr[frame_index]
            if np.ndim(value) == 0:
                context[name] = float(value)
            else:
                context[name] = np.asarray(value, dtype=np.float64)
        return context

    def to_time_samples(
        self,
        time_codes: ArrayLike1D,
        sampled_tracks: Optional[Mapping[str, np.ndarray]] = None,
    ) -> dict[str, dict[float, Any]]:
        """Serialize all tracks as ``track_name -> {time: value}``."""
        t = np.asarray(time_codes, dtype=np.float64)
        sampled = sampled_tracks or self.sample_all(t)
        return {
            name: self.tracks[name].to_time_samples(t, sampled_values=values)
            for name, values in sampled.items()
        }


@dataclass
class TimeAwareMorphologyEvaluator:
    """Streaming wrapper that adds time-varying parameter contexts to a genotype.

    The wrapper intentionally sits *outside* the static morphology trunk.  It
    samples parameter tracks densely once, then exposes frame-wise context
    dictionaries that callers can stream into ``MorphologyGenotype.decode_to_sdf``
    without caching a 4D field.
    """

    genotype: Any
    track_bundle: ParameterTrackBundle
    time_codes_per_second: float = 24.0

    def make_time_tensor(
        self,
        frame_count: int,
        *,
        start_time: float = 0.0,
        end_time: float = 1.0,
        endpoint: bool = True,
    ) -> np.ndarray:
        if frame_count <= 0:
            raise ValueError("frame_count must be positive")
        return np.linspace(
            float(start_time),
            float(end_time),
            int(frame_count),
            endpoint=endpoint,
            dtype=np.float64,
        )

    def sample_tracks(self, time_codes: ArrayLike1D) -> dict[str, np.ndarray]:
        return self.track_bundle.sample_all(time_codes)

    def sample_parameter_matrix(
        self,
        time_codes: ArrayLike1D,
        track_order: Optional[Sequence[str]] = None,
    ) -> SampledParameterMatrix:
        return self.track_bundle.sample_parameter_matrix(time_codes, track_order=track_order)

    def frame_context(self, sampled_tracks: Mapping[str, np.ndarray], frame_index: int) -> dict[str, Any]:
        return self.track_bundle.resolve_frame_context(sampled_tracks, frame_index)

    def frame_sdf(self, sampled_tracks: Mapping[str, np.ndarray], frame_index: int):
        context = self.frame_context(sampled_tracks, frame_index)
        return self.genotype.decode_to_sdf(parameter_context=context)

    def iter_frame_sdfs(self, time_codes: ArrayLike1D):
        times = np.asarray(time_codes, dtype=np.float64)
        sampled = self.sample_tracks(times)
        for i, t in enumerate(times.tolist()):
            context = self.frame_context(sampled, i)
            yield float(t), context, self.genotype.decode_to_sdf(parameter_context=context)

    def export_time_samples(self, time_codes: ArrayLike1D) -> dict[str, dict[float, Any]]:
        times = np.asarray(time_codes, dtype=np.float64)
        sampled = self.sample_tracks(times)
        return self.track_bundle.to_time_samples(times, sampled_tracks=sampled)


def volume_preserving_axis_link(
    primary_scale: ArrayLikeValue,
    *,
    axis: str = "y",
    min_scale: float = 0.25,
    max_scale: float = 4.0,
) -> np.ndarray:
    """Derive three-axis scales with approximate constant volume.

    For a single driven axis scale ``n``, the two orthogonal axes receive
    ``1 / sqrt(n)`` so that ``sx * sy * sz ≈ 1``.
    """
    axis = axis.lower()
    if axis not in {"x", "y", "z"}:
        raise ValueError("axis must be one of 'x', 'y', or 'z'")

    driven = np.asarray(primary_scale, dtype=np.float64)
    original_shape = driven.shape
    driven = np.clip(driven.reshape(-1), min_scale, max_scale)
    lateral = np.clip(1.0 / np.sqrt(np.maximum(driven, 1e-12)), min_scale, max_scale)

    scales = np.ones((driven.shape[0], 3), dtype=np.float64)
    axis_to_index = {"x": 0, "y": 1, "z": 2}
    idx = axis_to_index[axis]
    scales[:, idx] = driven
    other = [i for i in range(3) if i != idx]
    scales[:, other[0]] = lateral
    scales[:, other[1]] = lateral

    if original_shape == ():
        return scales[0]
    return scales.reshape(*original_shape, 3)


def _prepare_bound(bound: Optional[ArrayLikeValue], dim: int, *, fill: float) -> np.ndarray:
    if bound is None:
        return np.full((dim,), fill, dtype=np.float64)
    arr = np.asarray(bound, dtype=np.float64)
    if arr.ndim == 0:
        return np.full((dim,), float(arr), dtype=np.float64)
    if arr.shape != (dim,):
        raise ValueError(f"bound dimension mismatch: expected {(dim,)}, got {arr.shape}")
    return arr


__all__ = [
    "ParameterKeyframe",
    "ParameterTrack",
    "ParameterTrackBundle",
    "SampledParameterMatrix",
    "TimeAwareMorphologyEvaluator",
    "volume_preserving_axis_link",
]
