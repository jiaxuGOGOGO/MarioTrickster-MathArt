"""SESSION-058 — SDF Sphere Tracing CCD for anti-tunneling motion control.

This module lands the user's requested Phase 3 collision upgrade inspired by
Erwin Coumans and Bullet's continuous collision detection strategy, but adapts
it to this repository's existing SDF-based world representation.

Core idea
---------
Given a moving point/sphere proxy with previous position x0 and candidate new
position x1, we treat the motion segment as a continuous path and trace it
against a target signed distance field Φ(x).  Instead of accepting the full
x1 and then repairing deep penetration afterwards, we compute a conservative
**time of impact (TOI)** by sphere tracing the clearance function:

    clearance(p) = Φ(p) - proxy_radius

Once the clearance falls below a hit tolerance, we clamp the accepted motion to
just before impact and feed the corrected point back into the runtime state.  In
practice this gives the project a repository-native analog to Bullet's motion
clamping / swept-sphere CCD.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Iterable, Optional
import math

import numpy as np

_EPS = 1e-8

SignedDistance2D = Callable[[float, float], float]


@dataclass(frozen=True)
class SDFCCDConfig:
    """Configuration for sphere-tracing CCD queries."""

    max_steps: int = 64
    hit_tolerance: float = 1e-4
    min_advance: float = 1e-4
    safety_backoff: float = 5e-4
    normal_epsilon: float = 1e-4
    binary_refine_steps: int = 10


@dataclass(frozen=True)
class SDFCCDResult:
    """Single continuous collision query result."""

    hit: bool
    toi: float
    traveled: float
    total_distance: float
    hit_point: tuple[float, float]
    safe_point: tuple[float, float]
    normal: tuple[float, float]
    clearance: float
    steps: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SDFCCDBatchDiagnostics:
    """Aggregated results after clamping a batch of moving particles."""

    hits: int
    tested: int
    min_toi: float
    max_correction_distance: float
    mean_correction_distance: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _query_sdf(sdf: Any, x: float, y: float) -> float:
    if hasattr(sdf, "query"):
        return float(sdf.query(x, y))
    return float(sdf(x, y))


def _surface_normal(sdf: Any, x: float, y: float, eps: float) -> tuple[float, float]:
    if hasattr(sdf, "surface_normal"):
        nx, ny = sdf.surface_normal(x, y)
        length = math.hypot(nx, ny)
        if length > _EPS:
            return (float(nx / length), float(ny / length))
    if hasattr(sdf, "gradient"):
        gx, gy = sdf.gradient(x, y, eps=eps)
        length = math.hypot(gx, gy)
        if length > _EPS:
            return (float(gx / length), float(gy / length))
    dx = _query_sdf(sdf, x + eps, y) - _query_sdf(sdf, x - eps, y)
    dy = _query_sdf(sdf, x, y + eps) - _query_sdf(sdf, x, y - eps)
    length = math.hypot(dx, dy)
    if length <= _EPS:
        return (0.0, 1.0)
    return (float(dx / length), float(dy / length))


class SDFSphereTracingCCD:
    """Continuous collision detector based on SDF sphere tracing."""

    def __init__(self, sdf: Any, config: Optional[SDFCCDConfig] = None) -> None:
        self.sdf = sdf
        self.config = config or SDFCCDConfig()

    def _clearance(self, x: float, y: float, radius: float) -> float:
        return _query_sdf(self.sdf, x, y) - float(radius)

    def _point_on_segment(
        self,
        start: tuple[float, float],
        direction: tuple[float, float],
        traveled: float,
    ) -> tuple[float, float]:
        return (
            float(start[0] + direction[0] * traveled),
            float(start[1] + direction[1] * traveled),
        )

    def _refine_hit(
        self,
        start: tuple[float, float],
        direction: tuple[float, float],
        radius: float,
        lo: float,
        hi: float,
    ) -> float:
        for _ in range(self.config.binary_refine_steps):
            mid = 0.5 * (lo + hi)
            px, py = self._point_on_segment(start, direction, mid)
            if self._clearance(px, py, radius) <= self.config.hit_tolerance:
                hi = mid
            else:
                lo = mid
        return hi

    def trace_motion(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        radius: float = 0.0,
    ) -> SDFCCDResult:
        """Trace a swept sphere along the motion segment and compute TOI."""
        sx, sy = float(start[0]), float(start[1])
        ex, ey = float(end[0]), float(end[1])
        dx = ex - sx
        dy = ey - sy
        total_distance = math.hypot(dx, dy)
        radius = float(radius)

        if total_distance <= _EPS:
            clearance = self._clearance(sx, sy, radius)
            hit = clearance <= self.config.hit_tolerance
            normal = _surface_normal(self.sdf, sx, sy, self.config.normal_epsilon)
            point = (sx, sy)
            return SDFCCDResult(
                hit=hit,
                toi=0.0 if hit else 1.0,
                traveled=0.0,
                total_distance=0.0,
                hit_point=point,
                safe_point=point,
                normal=normal,
                clearance=clearance,
                steps=0,
            )

        inv_len = 1.0 / total_distance
        direction = (dx * inv_len, dy * inv_len)
        clearance0 = self._clearance(sx, sy, radius)
        if clearance0 <= self.config.hit_tolerance:
            normal0 = _surface_normal(self.sdf, sx, sy, self.config.normal_epsilon)
            return SDFCCDResult(
                hit=True,
                toi=0.0,
                traveled=0.0,
                total_distance=total_distance,
                hit_point=(sx, sy),
                safe_point=(sx, sy),
                normal=normal0,
                clearance=clearance0,
                steps=0,
            )

        traveled = 0.0
        last_safe = 0.0
        steps = 0
        for step in range(self.config.max_steps):
            steps = step + 1
            px, py = self._point_on_segment((sx, sy), direction, traveled)
            clearance = self._clearance(px, py, radius)
            if clearance <= self.config.hit_tolerance:
                hit_travel = self._refine_hit((sx, sy), direction, radius, last_safe, traveled)
                hit_x, hit_y = self._point_on_segment((sx, sy), direction, hit_travel)
                safe_travel = max(hit_travel - self.config.safety_backoff, 0.0)
                safe_x, safe_y = self._point_on_segment((sx, sy), direction, safe_travel)
                normal = _surface_normal(self.sdf, hit_x, hit_y, self.config.normal_epsilon)
                return SDFCCDResult(
                    hit=True,
                    toi=max(min(hit_travel / total_distance, 1.0), 0.0),
                    traveled=hit_travel,
                    total_distance=total_distance,
                    hit_point=(hit_x, hit_y),
                    safe_point=(safe_x, safe_y),
                    normal=normal,
                    clearance=clearance,
                    steps=steps,
                )

            last_safe = traveled
            traveled += max(clearance, self.config.min_advance)
            if traveled >= total_distance:
                break

        end_clearance = self._clearance(ex, ey, radius)
        if end_clearance <= self.config.hit_tolerance:
            hit_travel = self._refine_hit((sx, sy), direction, radius, last_safe, total_distance)
            hit_x, hit_y = self._point_on_segment((sx, sy), direction, hit_travel)
            safe_travel = max(hit_travel - self.config.safety_backoff, 0.0)
            safe_x, safe_y = self._point_on_segment((sx, sy), direction, safe_travel)
            normal = _surface_normal(self.sdf, hit_x, hit_y, self.config.normal_epsilon)
            return SDFCCDResult(
                hit=True,
                toi=max(min(hit_travel / total_distance, 1.0), 0.0),
                traveled=hit_travel,
                total_distance=total_distance,
                hit_point=(hit_x, hit_y),
                safe_point=(safe_x, safe_y),
                normal=normal,
                clearance=end_clearance,
                steps=steps,
            )

        normal_end = _surface_normal(self.sdf, ex, ey, self.config.normal_epsilon)
        return SDFCCDResult(
            hit=False,
            toi=1.0,
            traveled=total_distance,
            total_distance=total_distance,
            hit_point=(ex, ey),
            safe_point=(ex, ey),
            normal=normal_end,
            clearance=end_clearance,
            steps=steps,
        )


def apply_sdf_ccd_to_particle_batch(
    previous_positions: np.ndarray,
    candidate_positions: np.ndarray,
    radii: np.ndarray,
    sdf: Any,
    *,
    active_mask: Optional[np.ndarray] = None,
    config: Optional[SDFCCDConfig] = None,
) -> tuple[np.ndarray, list[SDFCCDResult], SDFCCDBatchDiagnostics]:
    """Clamp a batch of moving particles against an SDF continuously."""
    detector = SDFSphereTracingCCD(sdf, config=config)
    prev = np.asarray(previous_positions, dtype=np.float64)
    cand = np.asarray(candidate_positions, dtype=np.float64)
    radii_arr = np.asarray(radii, dtype=np.float64)
    corrected = cand.copy()
    results: list[SDFCCDResult] = []
    corrections: list[float] = []
    min_toi = 1.0

    if active_mask is None:
        mask = np.ones(len(prev), dtype=bool)
    else:
        mask = np.asarray(active_mask, dtype=bool)

    for idx in range(len(prev)):
        if not mask[idx]:
            continue
        result = detector.trace_motion(
            (float(prev[idx, 0]), float(prev[idx, 1])),
            (float(cand[idx, 0]), float(cand[idx, 1])),
            radius=float(radii_arr[idx]),
        )
        if result.hit:
            corrected[idx, 0] = result.safe_point[0]
            corrected[idx, 1] = result.safe_point[1]
            min_toi = min(min_toi, result.toi)
            corrections.append(float(np.linalg.norm(cand[idx] - corrected[idx])))
        results.append(result)

    hits = sum(1 for r in results if r.hit)
    diagnostics = SDFCCDBatchDiagnostics(
        hits=hits,
        tested=len(results),
        min_toi=float(min_toi if hits else 1.0),
        max_correction_distance=float(max(corrections) if corrections else 0.0),
        mean_correction_distance=float(sum(corrections) / len(corrections) if corrections else 0.0),
    )
    return corrected, results, diagnostics


def clamp_solver_particle_motion_with_sdf_ccd(
    solver: Any,
    sdf: Any,
    *,
    particle_indices: Optional[Iterable[int]] = None,
    dt: float = 1.0 / 60.0,
    config: Optional[SDFCCDConfig] = None,
) -> SDFCCDBatchDiagnostics:
    """Apply CCD clamping directly to an XPBD solver-like object.

    The solver is expected to expose `_positions`, `_prev_positions`, `_velocities`,
    `_radii`, `_inv_masses`, and `particle_count`, which matches the repository's
    `XPBDSolver` implementation.
    """
    if particle_indices is None:
        indices = list(range(int(solver.particle_count)))
    else:
        indices = [int(i) for i in particle_indices]

    prev = np.asarray(solver._prev_positions[indices], dtype=np.float64)
    cand = np.asarray(solver._positions[indices], dtype=np.float64)
    radii = np.asarray(solver._radii[indices], dtype=np.float64)
    inv_masses = np.asarray(solver._inv_masses[indices], dtype=np.float64)
    active_mask = inv_masses > 0.0

    corrected, results, diagnostics = apply_sdf_ccd_to_particle_batch(
        prev,
        cand,
        radii,
        sdf,
        active_mask=active_mask,
        config=config,
    )

    for local_idx, solver_idx in enumerate(indices):
        result = results[local_idx] if local_idx < len(results) else None
        if result is None or not result.hit:
            continue
        solver._positions[solver_idx] = corrected[local_idx]

        raw_velocity = np.asarray(solver._velocities[solver_idx], dtype=np.float64)
        if np.linalg.norm(raw_velocity) <= _EPS:
            raw_velocity = (cand[local_idx] - prev[local_idx]) / max(float(dt), _EPS)
        normal = np.asarray(result.normal, dtype=np.float64)
        vn = float(np.dot(raw_velocity, normal))
        if vn < 0.0:
            raw_velocity = raw_velocity - vn * normal
        solver._velocities[solver_idx] = raw_velocity

    return diagnostics


__all__ = [
    "SDFCCDConfig",
    "SDFCCDResult",
    "SDFCCDBatchDiagnostics",
    "SDFSphereTracingCCD",
    "apply_sdf_ccd_to_particle_batch",
    "clamp_solver_particle_motion_with_sdf_ccd",
]
