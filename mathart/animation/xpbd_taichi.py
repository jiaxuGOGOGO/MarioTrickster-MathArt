"""SESSION-058 — Taichi GPU JIT XPBD backend for large cloth meshes.

This module lands the user's Phase 3 requirement inspired by Yuanming Hu's
Taichi language work: keep the authoring experience in Python, but JIT compile
hot simulation loops into CPU/GPU kernels instead of hand-writing CUDA.

Design goals
------------
1. Preserve the existing NumPy XPBD solver as the project's correctness oracle.
2. Add a **dense Taichi cloth backend** that scales the simulation target from
   1D secondary chains to 2D cloth meshes with tens of thousands of particles.
3. Fit the repository's three-layer evolution architecture by exposing stable
   diagnostics and runtime capability reporting.

Research grounding
------------------
- Yuanming Hu et al., "Taichi: A Language for High-Performance Computation on
  Spatially Sparse Data Structures," SIGGRAPH Asia 2019.
- Taichi official cloth simulation tutorial.
- Taichi official sparse data-structure documentation.

The implementation intentionally starts with a dense grid layout because it is
safer and easier to validate against the repository's current XPBD semantics.
Sparse SNode layouts remain a future optimization path once the dense backend is
fully integrated into all runtime and evolution-loop hooks.
"""
from dataclasses import dataclass, asdict
from time import perf_counter
from typing import Any, Optional
import math

import numpy as np

try:  # pragma: no cover - import availability is environment-dependent
    import taichi as ti
    _TAICHI_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - handled at runtime
    ti = None  # type: ignore[assignment]
    _TAICHI_IMPORT_ERROR = exc


_TAICHI_READY = False
_TAICHI_ARCH = "uninitialized"
_EPS = 1e-6


@dataclass(frozen=True)
class TaichiXPBDBackendStatus:
    """Runtime availability and selected backend information."""

    available: bool
    initialized: bool
    active_arch: str
    import_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaichiXPBDClothConfig:
    """Configuration for the dense Taichi XPBD cloth system."""

    width: int = 64
    height: int = 64
    spacing: float = 0.02
    origin_x: float = -0.64
    origin_y: float = 0.85
    particle_mass: float = 0.04
    gravity: tuple[float, float] = (0.0, -9.81)
    sub_steps: int = 4
    solver_iterations: int = 8
    structural_compliance: float = 1e-6
    shear_compliance: float = 5e-6
    bending_compliance: float = 2e-4
    velocity_damping: float = 0.995
    max_velocity: float = 25.0
    pin_top_row: bool = False
    pin_corners: bool = True
    enable_ground_collision: bool = True
    ground_y: float = -0.45
    enable_circle_collision: bool = True
    circle_center: tuple[float, float] = (0.0, 0.15)
    circle_radius: float = 0.18
    prefer_gpu: bool = True

    @property
    def particle_count(self) -> int:
        return int(self.width * self.height)

    @property
    def structural_constraint_count(self) -> int:
        return int((self.width - 1) * self.height + self.width * (self.height - 1))

    @property
    def shear_constraint_count(self) -> int:
        return int(2 * (self.width - 1) * (self.height - 1))

    @property
    def bending_constraint_count(self) -> int:
        return int(max(self.width - 2, 0) * self.height + self.width * max(self.height - 2, 0))

    @property
    def total_constraint_count(self) -> int:
        return (
            self.structural_constraint_count
            + self.shear_constraint_count
            + self.bending_constraint_count
        )


@dataclass(frozen=True)
class TaichiXPBDClothDiagnostics:
    """Step-level diagnostics for the Taichi cloth backend."""

    active_arch: str
    particle_count: int
    total_constraints: int
    sub_steps_used: int
    iterations_per_substep: int
    mean_constraint_error: float
    max_constraint_error: float
    max_velocity_observed: float
    mean_height: float
    min_height: float
    max_height: float
    max_frame_displacement: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaichiXPBDBenchmarkResult:
    """Benchmark result for a Taichi cloth run."""

    active_arch: str
    frames: int
    seconds: float
    particle_count: int
    constraints: int
    simulated_particle_steps: int
    fps: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_taichi_initialized(prefer_gpu: bool = True) -> str:
    """Initialize Taichi once and return the active backend name."""
    global _TAICHI_READY, _TAICHI_ARCH

    if ti is None:
        raise RuntimeError(
            "Taichi is not available in this environment: "
            f"{_TAICHI_IMPORT_ERROR!r}"
        )
    if _TAICHI_READY:
        return _TAICHI_ARCH

    candidates: list[tuple[str, Any]] = []
    if prefer_gpu:
        for name in ("cuda", "vulkan", "gpu"):
            arch = getattr(ti, name, None)
            if arch is not None:
                candidates.append((name, arch))
    candidates.append(("cpu", ti.cpu))

    last_error: Optional[Exception] = None
    for name, arch in candidates:
        try:
            ti.init(arch=arch, default_fp=ti.f32, offline_cache=True)
            _TAICHI_READY = True
            _TAICHI_ARCH = name
            return name
        except Exception as exc:  # pragma: no cover - backend availability varies
            last_error = exc
            try:
                ti.reset()
            except Exception:
                pass

    raise RuntimeError(f"Failed to initialize Taichi backend: {last_error}")


def get_taichi_xpbd_backend_status(prefer_gpu: bool = True) -> TaichiXPBDBackendStatus:
    """Return runtime status without forcing callers to know Taichi internals."""
    if ti is None:
        return TaichiXPBDBackendStatus(
            available=False,
            initialized=False,
            active_arch="unavailable",
            import_error=str(_TAICHI_IMPORT_ERROR),
        )
    try:
        arch = _ensure_taichi_initialized(prefer_gpu=prefer_gpu)
        return TaichiXPBDBackendStatus(
            available=True,
            initialized=True,
            active_arch=arch,
        )
    except Exception as exc:
        return TaichiXPBDBackendStatus(
            available=True,
            initialized=False,
            active_arch="failed",
            import_error=str(exc),
        )


if ti is not None:
    _data_oriented = ti.data_oriented
    _kernel = ti.kernel
    _func = ti.func
else:
    _data_oriented = lambda cls: cls
    _kernel = lambda fn: fn
    _func = lambda fn: fn


if ti is not None:
    @_data_oriented  # type: ignore[misc]
    class TaichiXPBDClothSystem:
        """Dense 2D XPBD cloth mesh compiled with Taichi kernels.

        The solver uses regular-grid cloth constraints instead of the repository's
        generic list-of-constraints representation. This is deliberate: it lets
        Taichi compile tight kernels with predictable memory access patterns while
        keeping the mathematical behavior aligned with XPBD.
        """

        def __init__(self, config: Optional[TaichiXPBDClothConfig] = None) -> None:
            self.config = config or TaichiXPBDClothConfig()
            self.active_arch = _ensure_taichi_initialized(self.config.prefer_gpu)
            self.width = int(self.config.width)
            self.height = int(self.config.height)
            self.shape = (self.width, self.height)

            self.positions = ti.Vector.field(2, dtype=ti.f32, shape=self.shape)
            self.prev_positions = ti.Vector.field(2, dtype=ti.f32, shape=self.shape)
            self.predicted = ti.Vector.field(2, dtype=ti.f32, shape=self.shape)
            self.velocities = ti.Vector.field(2, dtype=ti.f32, shape=self.shape)
            self.inv_masses = ti.field(dtype=ti.f32, shape=self.shape)

            self.lambda_h = ti.field(dtype=ti.f32, shape=(max(self.width - 1, 1), self.height))
            self.lambda_v = ti.field(dtype=ti.f32, shape=(self.width, max(self.height - 1, 1)))
            self.lambda_d1 = ti.field(dtype=ti.f32, shape=(max(self.width - 1, 1), max(self.height - 1, 1)))
            self.lambda_d2 = ti.field(dtype=ti.f32, shape=(max(self.width - 1, 1), max(self.height - 1, 1)))
            self.lambda_bh = ti.field(dtype=ti.f32, shape=(max(self.width - 2, 1), self.height))
            self.lambda_bv = ti.field(dtype=ti.f32, shape=(self.width, max(self.height - 2, 1)))

            self.constraint_error_sum = ti.field(dtype=ti.f32, shape=())
            self.constraint_error_count = ti.field(dtype=ti.i32, shape=())
            self.constraint_error_max = ti.field(dtype=ti.f32, shape=())
            self.max_speed = ti.field(dtype=ti.f32, shape=())

            self._initialize(
                self.config.origin_x,
                self.config.origin_y,
                self.config.spacing,
                int(self.config.pin_top_row),
                int(self.config.pin_corners),
            )

        @_kernel
        def _initialize(
            self,
            origin_x: ti.f32,
            origin_y: ti.f32,
            spacing: ti.f32,
            pin_top_row: ti.i32,
            pin_corners: ti.i32,
        ):
            for i, j in self.positions:
                p = ti.Vector([origin_x + i * spacing, origin_y - j * spacing])
                self.positions[i, j] = p
                self.prev_positions[i, j] = p
                self.predicted[i, j] = p
                self.velocities[i, j] = ti.Vector([0.0, 0.0])

                pinned = 0
                if pin_top_row == 1 and j == 0:
                    pinned = 1
                elif pin_corners == 1 and j == 0 and (i == 0 or i == self.width - 1):
                    pinned = 1
                self.inv_masses[i, j] = 0.0 if pinned == 1 else 1.0 / max(self.config.particle_mass, _EPS)

        @_kernel
        def _reset_lambdas(self):
            for i, j in self.lambda_h:
                if i < self.width - 1:
                    self.lambda_h[i, j] = 0.0
            for i, j in self.lambda_v:
                if j < self.height - 1:
                    self.lambda_v[i, j] = 0.0
            for i, j in self.lambda_d1:
                if i < self.width - 1 and j < self.height - 1:
                    self.lambda_d1[i, j] = 0.0
                    self.lambda_d2[i, j] = 0.0
            for i, j in self.lambda_bh:
                if i < self.width - 2:
                    self.lambda_bh[i, j] = 0.0
            for i, j in self.lambda_bv:
                if j < self.height - 2:
                    self.lambda_bv[i, j] = 0.0

        @_kernel
        def _reset_step_diagnostics(self):
            self.constraint_error_sum[None] = 0.0
            self.constraint_error_count[None] = 0
            self.constraint_error_max[None] = 0.0
            self.max_speed[None] = 0.0

        @_kernel
        def _predict(self, dt: ti.f32, gx: ti.f32, gy: ti.f32):
            gravity = ti.Vector([gx, gy])
            for i, j in self.positions:
                self.prev_positions[i, j] = self.positions[i, j]
                if self.inv_masses[i, j] > 0.0:
                    self.velocities[i, j] += gravity * dt
                    self.predicted[i, j] = self.positions[i, j] + self.velocities[i, j] * dt
                else:
                    self.predicted[i, j] = self.positions[i, j]
                    self.velocities[i, j] = ti.Vector([0.0, 0.0])

        @_func
        def _accumulate_error(self, err: ti.f32):
            abs_err = ti.abs(err)
            ti.atomic_add(self.constraint_error_sum[None], abs_err)
            ti.atomic_add(self.constraint_error_count[None], 1)
            ti.atomic_max(self.constraint_error_max[None], abs_err)

        @_func
        def _solve_pair(
            self,
            ax: ti.i32,
            ay: ti.i32,
            bx: ti.i32,
            by: ti.i32,
            rest_value: ti.f32,
            compliance: ti.f32,
            dt: ti.f32,
            lambda_ref: ti.template(),
        ):
            w_a = self.inv_masses[ax, ay]
            w_b = self.inv_masses[bx, by]
            w_sum = w_a + w_b
            if w_sum > 0.0:
                delta = self.predicted[ax, ay] - self.predicted[bx, by]
                dist = delta.norm() + 1e-8
                C = dist - rest_value
                alpha_tilde = compliance / (dt * dt + 1e-8)
                denom = w_sum + alpha_tilde
                if ti.abs(denom) > 1e-8:
                    delta_lambda = (-C - alpha_tilde * lambda_ref) / denom
                    lambda_ref += delta_lambda
                    correction = delta / dist * delta_lambda
                    self.predicted[ax, ay] += w_a * correction
                    self.predicted[bx, by] -= w_b * correction
                self._accumulate_error(C)

        @_kernel
        def _solve_horizontal(self, parity: ti.i32, dt: ti.f32, rest_value: ti.f32, compliance: ti.f32):
            for i, j in self.lambda_h:
                if i < self.width - 1 and i % 2 == parity:
                    lam = self.lambda_h[i, j]
                    self._solve_pair(i, j, i + 1, j, rest_value, compliance, dt, lam)
                    self.lambda_h[i, j] = lam

        @_kernel
        def _solve_vertical(self, parity: ti.i32, dt: ti.f32, rest_value: ti.f32, compliance: ti.f32):
            for i, j in self.lambda_v:
                if j < self.height - 1 and j % 2 == parity:
                    lam = self.lambda_v[i, j]
                    self._solve_pair(i, j, i, j + 1, rest_value, compliance, dt, lam)
                    self.lambda_v[i, j] = lam

        @_kernel
        def _solve_diag_main(self, parity: ti.i32, dt: ti.f32, rest_value: ti.f32, compliance: ti.f32):
            for i, j in self.lambda_d1:
                if i < self.width - 1 and j < self.height - 1 and i % 2 == parity:
                    lam = self.lambda_d1[i, j]
                    self._solve_pair(i, j, i + 1, j + 1, rest_value, compliance, dt, lam)
                    self.lambda_d1[i, j] = lam

        @_kernel
        def _solve_diag_anti(self, parity: ti.i32, dt: ti.f32, rest_value: ti.f32, compliance: ti.f32):
            for i, j in self.lambda_d2:
                if i < self.width - 1 and j < self.height - 1 and i % 2 == parity:
                    lam = self.lambda_d2[i, j]
                    self._solve_pair(i + 1, j, i, j + 1, rest_value, compliance, dt, lam)
                    self.lambda_d2[i, j] = lam

        @_kernel
        def _solve_bending_h(self, parity: ti.i32, dt: ti.f32, rest_value: ti.f32, compliance: ti.f32):
            for i, j in self.lambda_bh:
                if i < self.width - 2 and i % 2 == parity:
                    lam = self.lambda_bh[i, j]
                    self._solve_pair(i, j, i + 2, j, rest_value, compliance, dt, lam)
                    self.lambda_bh[i, j] = lam

        @_kernel
        def _solve_bending_v(self, parity: ti.i32, dt: ti.f32, rest_value: ti.f32, compliance: ti.f32):
            for i, j in self.lambda_bv:
                if j < self.height - 2 and j % 2 == parity:
                    lam = self.lambda_bv[i, j]
                    self._solve_pair(i, j, i, j + 2, rest_value, compliance, dt, lam)
                    self.lambda_bv[i, j] = lam

        @_kernel
        def _apply_collisions(self, center_x: ti.f32, center_y: ti.f32, radius: ti.f32, ground_y: ti.f32, use_circle: ti.i32, use_ground: ti.i32):
            center = ti.Vector([center_x, center_y])
            for i, j in self.predicted:
                if self.inv_masses[i, j] <= 0.0:
                    continue
                p = self.predicted[i, j]
                if use_circle == 1:
                    delta = p - center
                    dist = delta.norm()
                    if dist < radius and dist > 1e-8:
                        self.predicted[i, j] = center + delta / dist * radius
                    elif dist <= 1e-8:
                        self.predicted[i, j] = center + ti.Vector([0.0, radius])
                if use_ground == 1 and self.predicted[i, j].y < ground_y:
                    self.predicted[i, j].y = ground_y

        @_kernel
        def _finalize(self, dt: ti.f32, velocity_damping: ti.f32, max_velocity: ti.f32):
            for i, j in self.positions:
                if self.inv_masses[i, j] > 0.0:
                    new_vel = (self.predicted[i, j] - self.positions[i, j]) / dt
                    new_vel *= velocity_damping
                    speed = new_vel.norm()
                    if speed > max_velocity and speed > 1e-8:
                        new_vel = new_vel / speed * max_velocity
                        speed = max_velocity
                    self.velocities[i, j] = new_vel
                    self.positions[i, j] = self.predicted[i, j]
                    ti.atomic_max(self.max_speed[None], speed)
                else:
                    self.positions[i, j] = self.predicted[i, j]
                    self.velocities[i, j] = ti.Vector([0.0, 0.0])

        def step(self, dt: float = 1.0 / 60.0) -> TaichiXPBDClothDiagnostics:
            """Advance the cloth by one frame and return diagnostics."""
            sub_dt = float(dt) / max(int(self.config.sub_steps), 1)
            gx, gy = self.config.gravity
            rest = float(self.config.spacing)
            diag_rest = float(math.sqrt(2.0) * self.config.spacing)
            bend_rest = float(2.0 * self.config.spacing)

            self._reset_step_diagnostics()
            for _ in range(int(self.config.sub_steps)):
                self._predict(sub_dt, float(gx), float(gy))
                self._reset_lambdas()
                for _ in range(int(self.config.solver_iterations)):
                    self._solve_horizontal(0, sub_dt, rest, float(self.config.structural_compliance))
                    self._solve_horizontal(1, sub_dt, rest, float(self.config.structural_compliance))
                    self._solve_vertical(0, sub_dt, rest, float(self.config.structural_compliance))
                    self._solve_vertical(1, sub_dt, rest, float(self.config.structural_compliance))
                    self._solve_diag_main(0, sub_dt, diag_rest, float(self.config.shear_compliance))
                    self._solve_diag_main(1, sub_dt, diag_rest, float(self.config.shear_compliance))
                    self._solve_diag_anti(0, sub_dt, diag_rest, float(self.config.shear_compliance))
                    self._solve_diag_anti(1, sub_dt, diag_rest, float(self.config.shear_compliance))
                    self._solve_bending_h(0, sub_dt, bend_rest, float(self.config.bending_compliance))
                    self._solve_bending_h(1, sub_dt, bend_rest, float(self.config.bending_compliance))
                    self._solve_bending_v(0, sub_dt, bend_rest, float(self.config.bending_compliance))
                    self._solve_bending_v(1, sub_dt, bend_rest, float(self.config.bending_compliance))
                    self._apply_collisions(
                        float(self.config.circle_center[0]),
                        float(self.config.circle_center[1]),
                        float(self.config.circle_radius),
                        float(self.config.ground_y),
                        int(self.config.enable_circle_collision),
                        int(self.config.enable_ground_collision),
                    )
                self._finalize(
                    sub_dt,
                    float(self.config.velocity_damping),
                    float(self.config.max_velocity),
                )
            return self._collect_diagnostics()

        def _collect_diagnostics(self) -> TaichiXPBDClothDiagnostics:
            positions = self.positions.to_numpy()
            prev_positions = self.prev_positions.to_numpy()
            error_count = int(self.constraint_error_count[None])
            mean_error = float(self.constraint_error_sum[None]) / max(error_count, 1)
            heights = positions[:, :, 1]
            displacements = np.linalg.norm(positions - prev_positions, axis=2)
            return TaichiXPBDClothDiagnostics(
                active_arch=self.active_arch,
                particle_count=self.config.particle_count,
                total_constraints=self.config.total_constraint_count,
                sub_steps_used=int(self.config.sub_steps),
                iterations_per_substep=int(self.config.solver_iterations),
                mean_constraint_error=float(mean_error),
                max_constraint_error=float(self.constraint_error_max[None]),
                max_velocity_observed=float(self.max_speed[None]),
                mean_height=float(np.mean(heights)),
                min_height=float(np.min(heights)),
                max_height=float(np.max(heights)),
                max_frame_displacement=float(np.max(displacements)),
            )

        def positions_numpy(self) -> np.ndarray:
            """Export current positions for analysis or rendering."""
            return self.positions.to_numpy()

        def run(self, frames: int = 60, dt: float = 1.0 / 60.0) -> TaichiXPBDBenchmarkResult:
            """Run a short benchmark / warm simulation sequence."""
            start = perf_counter()
            for _ in range(int(frames)):
                self.step(dt)
            seconds = perf_counter() - start
            fps = float(frames) / max(seconds, 1e-8)
            return TaichiXPBDBenchmarkResult(
                active_arch=self.active_arch,
                frames=int(frames),
                seconds=float(seconds),
                particle_count=self.config.particle_count,
                constraints=self.config.total_constraint_count,
                simulated_particle_steps=int(frames) * self.config.particle_count,
                fps=fps,
            )

else:
    class TaichiXPBDClothSystem:  # type: ignore[no-redef]
        """Stub when Taichi is not available."""
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Taichi is not available in this environment")


def create_default_taichi_cloth_config(particle_budget: int = 4096) -> TaichiXPBDClothConfig:
    """Create a near-square cloth grid sized from a particle budget."""
    side = max(int(round(math.sqrt(max(particle_budget, 16)))), 4)
    return TaichiXPBDClothConfig(width=side, height=side)


__all__ = [
    "TaichiXPBDBackendStatus",
    "TaichiXPBDClothConfig",
    "TaichiXPBDClothDiagnostics",
    "TaichiXPBDBenchmarkResult",
    "TaichiXPBDClothSystem",
    "get_taichi_xpbd_backend_status",
    "create_default_taichi_cloth_config",
]
