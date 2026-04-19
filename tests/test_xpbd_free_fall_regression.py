"""Regression tests for Galilean-invariant XPBD gravity integration.

These tests lock in the architectural rule established in SESSION-081:
external gravity integration must remain analytically correct even when the
solver keeps non-zero velocity damping enabled for internal XPBD stability.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.animation.xpbd_solver import ParticleKind, XPBDSolver, XPBDSolverConfig
from mathart.animation.xpbd_solver_3d import XPBDSolver3D, XPBDSolver3DConfig


def _run_free_fall_2d(*, velocity_damping: float = 0.98) -> tuple[float, float]:
    cfg = XPBDSolverConfig(
        sub_steps=4,
        solver_iterations=8,
        gravity=(0.0, -9.81),
        velocity_damping=velocity_damping,
        enable_self_collision=False,
        enable_two_way_coupling=False,
    )
    solver = XPBDSolver(cfg)
    idx = solver.add_particle((0.0, 10.0), mass=1.0, kind=ParticleKind.SOFT_NODE)

    dt = 1.0 / 60.0
    frames = 60
    for _ in range(frames):
        solver.step(dt)

    total_time = dt * frames
    expected_y = 10.0 + 0.5 * cfg.gravity[1] * (total_time ** 2)
    actual_y = float(solver.get_position(idx)[1])
    return actual_y, expected_y


def _run_free_fall_3d(*, velocity_damping: float = 0.98) -> tuple[float, float]:
    cfg = XPBDSolver3DConfig(
        sub_steps=4,
        solver_iterations=8,
        gravity=(0.0, -9.81, 0.0),
        velocity_damping=velocity_damping,
        enable_self_collision=False,
        enable_two_way_coupling=False,
    )
    solver = XPBDSolver3D(cfg)
    idx = solver.add_particle((0.0, 10.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE)

    dt = 1.0 / 60.0
    frames = 60
    for _ in range(frames):
        solver.step(dt)

    total_time = dt * frames
    expected_y = 10.0 + 0.5 * cfg.gravity[1] * (total_time ** 2)
    actual_y = float(solver.get_position(idx)[1])
    return actual_y, expected_y


def test_free_fall_2d_matches_analytical_solution_with_damping_enabled():
    actual_y, expected_y = _run_free_fall_2d(velocity_damping=0.98)
    assert abs(actual_y - expected_y) <= 1e-6, (
        "2D free fall must match y = y0 + 0.5*g*t^2 even when velocity_damping "
        f"remains enabled; expected {expected_y:.9f}, got {actual_y:.9f}."
    )


def test_free_fall_3d_matches_analytical_solution_with_damping_enabled():
    actual_y, expected_y = _run_free_fall_3d(velocity_damping=0.98)
    assert abs(actual_y - expected_y) <= 1e-6, (
        "3D free fall must match y = y0 + 0.5*g*t^2 even when velocity_damping "
        f"remains enabled; expected {expected_y:.9f}, got {actual_y:.9f}."
    )


def test_distance_connected_pair_preserves_gravity_translation_2d():
    cfg = XPBDSolverConfig(
        sub_steps=4,
        solver_iterations=8,
        gravity=(0.0, -9.81),
        velocity_damping=0.98,
        enable_self_collision=False,
        enable_two_way_coupling=False,
    )
    solver = XPBDSolver(cfg)
    a = solver.add_particle((0.0, 10.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    b = solver.add_particle((0.0, 9.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    solver.add_distance_constraint(a, b, rest_length=1.0, compliance=0.0)

    dt = 1.0 / 60.0
    frames = 60
    for _ in range(frames):
        solver.step(dt)

    total_time = dt * frames
    expected_shift = 0.5 * cfg.gravity[1] * (total_time ** 2)
    pos_a = np.array(solver.get_position(a), dtype=np.float64)
    pos_b = np.array(solver.get_position(b), dtype=np.float64)
    center_y = float(0.5 * (pos_a[1] + pos_b[1]))
    expected_center_y = 9.5 + expected_shift

    assert abs(center_y - expected_center_y) <= 1e-6, (
        "A constrained 2D component must preserve rigid-body gravity translation; "
        f"expected center y {expected_center_y:.9f}, got {center_y:.9f}."
    )
    assert abs(np.linalg.norm(pos_a - pos_b) - 1.0) <= 1e-6


def test_distance_connected_pair_preserves_gravity_translation_3d():
    cfg = XPBDSolver3DConfig(
        sub_steps=4,
        solver_iterations=8,
        gravity=(0.0, -9.81, 0.0),
        velocity_damping=0.98,
        enable_self_collision=False,
        enable_two_way_coupling=False,
    )
    solver = XPBDSolver3D(cfg)
    a = solver.add_particle((0.0, 10.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    b = solver.add_particle((0.0, 9.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    solver.add_distance_constraint(a, b, rest_length=1.0, compliance=0.0)

    dt = 1.0 / 60.0
    frames = 60
    for _ in range(frames):
        solver.step(dt)

    total_time = dt * frames
    expected_shift = 0.5 * cfg.gravity[1] * (total_time ** 2)
    pos_a = np.array(solver.get_position(a), dtype=np.float64)
    pos_b = np.array(solver.get_position(b), dtype=np.float64)
    center_y = float(0.5 * (pos_a[1] + pos_b[1]))
    expected_center_y = 9.5 + expected_shift

    assert abs(center_y - expected_center_y) <= 1e-6, (
        "A constrained 3D component must preserve rigid-body gravity translation; "
        f"expected center y {expected_center_y:.9f}, got {center_y:.9f}."
    )
    assert abs(np.linalg.norm(pos_a - pos_b) - 1.0) <= 1e-6
