"""Tests for SESSION-058 SDF sphere tracing CCD."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.animation.terrain_sensor import create_flat_terrain
from mathart.animation.sdf_ccd import (
    SDFCCDConfig,
    SDFSphereTracingCCD,
    apply_sdf_ccd_to_particle_batch,
    clamp_solver_particle_motion_with_sdf_ccd,
)
from mathart.animation.xpbd_solver import XPBDSolver


def test_trace_motion_hits_flat_ground_and_returns_toi():
    terrain = create_flat_terrain(ground_y=0.0)
    detector = SDFSphereTracingCCD(terrain, SDFCCDConfig(hit_tolerance=1e-5, safety_backoff=1e-3))
    result = detector.trace_motion((0.0, 1.0), (0.0, -1.0), radius=0.1)

    assert result.hit is True
    assert 0.0 < result.toi < 1.0
    assert result.safe_point[1] >= 0.099, result.to_dict()
    assert result.normal[1] > 0.9


def test_particle_batch_is_clamped_before_penetration():
    terrain = create_flat_terrain(ground_y=0.0)
    prev = np.array([[0.0, 1.0], [1.0, 0.5]], dtype=np.float64)
    cand = np.array([[0.0, -0.5], [1.0, 0.3]], dtype=np.float64)
    radii = np.array([0.1, 0.05], dtype=np.float64)

    corrected, results, diag = apply_sdf_ccd_to_particle_batch(prev, cand, radii, terrain)

    assert len(results) == 2
    assert diag.hits == 1
    assert corrected[0, 1] >= 0.099
    np.testing.assert_allclose(corrected[1], cand[1], atol=1e-10)


def test_solver_particle_positions_are_rewritten_after_ccd():
    terrain = create_flat_terrain(ground_y=0.0)
    solver = XPBDSolver()
    idx = solver.add_particle((0.0, 1.0), mass=1.0)
    solver._prev_positions[idx] = np.array([0.0, 1.0], dtype=np.float64)
    solver._positions[idx] = np.array([0.0, -0.5], dtype=np.float64)
    solver._velocities[idx] = np.array([0.0, -20.0], dtype=np.float64)
    solver._radii[idx] = 0.1

    diag = clamp_solver_particle_motion_with_sdf_ccd(solver, terrain, particle_indices=[idx], dt=1.0 / 60.0)

    assert diag.hits == 1
    assert solver._positions[idx][1] >= 0.099
    assert solver._velocities[idx][1] > -20.0
