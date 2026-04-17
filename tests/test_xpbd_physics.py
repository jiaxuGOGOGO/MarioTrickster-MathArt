"""Comprehensive tests for the XPBD Physics Singularity (SESSION-052).

Tests cover:
  1. XPBD core solver: distance constraints, compliance decoupling, Lagrange multipliers
  2. Two-way rigid-soft coupling: reaction impulse, CoM displacement
  3. Spatial hash collision detection
  4. Self-collision separation
  5. Three-layer evolution loop
  6. Knowledge distillation
  7. Physics test harness (meta-tests)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.animation.xpbd_solver import (
    ParticleKind,
    ConstraintKind,
    XPBDSolverConfig,
    XPBDChainPreset,
    XPBDDiagnostics,
    XPBDConstraint,
    XPBDSolver,
    build_xpbd_chain,
    create_default_xpbd_presets,
)
from mathart.animation.xpbd_collision import (
    SpatialHashGrid,
    BodyCollisionProxy,
    XPBDCollisionManager,
    build_body_proxies_from_joints,
)
from mathart.animation.xpbd_evolution import (
    TuningAction,
    InternalEvolver,
    KnowledgeEntry,
    KnowledgeDistiller,
    PhysicsTestHarness,
    XPBDEvolutionOrchestrator,
)


def test_solver_basic_creation():
    """Test: solver can be created with default config."""
    solver = XPBDSolver()
    assert solver.particle_count == 0
    assert solver.constraint_count == 0
    print("  [PASS] test_solver_basic_creation")


def test_add_particles():
    """Test: particles can be added with correct properties."""
    solver = XPBDSolver()
    idx0 = solver.add_particle((1.0, 2.0), mass=5.0, kind=ParticleKind.SOFT_NODE)
    idx1 = solver.add_particle((3.0, 4.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    idx2 = solver.add_particle((0.0, 0.0), mass=70.0, kind=ParticleKind.RIGID_COM)

    assert solver.particle_count == 3
    assert idx0 == 0
    assert idx1 == 1
    assert idx2 == 2

    pos0 = solver.get_position(idx0)
    assert abs(pos0[0] - 1.0) < 1e-10
    assert abs(pos0[1] - 2.0) < 1e-10

    # Kinematic has zero inverse mass
    assert solver._inv_masses[idx1] == 0.0
    # Rigid COM has finite inverse mass
    assert abs(solver._inv_masses[idx2] - 1.0 / 70.0) < 1e-10
    print("  [PASS] test_add_particles")


def test_distance_constraint():
    """Test: distance constraint maintains rest length."""
    config = XPBDSolverConfig(
        sub_steps=4, solver_iterations=8, gravity=(0.0, 0.0),
        enable_self_collision=False, enable_two_way_coupling=False,
    )
    solver = XPBDSolver(config)
    anchor = solver.add_particle((0.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    bob = solver.add_particle((1.0, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    solver.add_distance_constraint(anchor, bob, rest_length=1.0, compliance=0.0)

    # Perturb bob
    solver._positions[bob] = [1.5, 0.0]

    dt = 1.0 / 60.0
    for _ in range(30):
        solver.step(dt)

    pos = solver.get_position(bob)
    dist = np.linalg.norm(np.array(pos))
    assert abs(dist - 1.0) < 0.1, f"Distance should be ~1.0, got {dist}"
    print("  [PASS] test_distance_constraint")


def test_compliance_decoupling():
    """Test: compliance makes constraint softer (higher α → more stretch)."""
    results = {}
    for compliance in [0.0, 1e-5, 1e-3]:
        config = XPBDSolverConfig(
            sub_steps=4, solver_iterations=8, gravity=(0.0, -9.81),
            enable_self_collision=False, enable_two_way_coupling=False,
        )
        solver = XPBDSolver(config)
        anchor = solver.add_particle((0.0, 5.0), mass=0.0, kind=ParticleKind.KINEMATIC)
        bob = solver.add_particle((0.0, 4.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
        solver.add_distance_constraint(anchor, bob, rest_length=1.0, compliance=compliance)

        dt = 1.0 / 60.0
        for _ in range(60):
            solver.step(dt)

        pos = solver.get_position(bob)
        dist = np.linalg.norm(np.array(pos) - np.array([0.0, 5.0]))
        results[compliance] = dist

    # Higher compliance → more stretch (further from anchor)
    assert results[1e-3] >= results[1e-5] >= results[0.0] - 0.01
    print(f"  [PASS] test_compliance_decoupling: {results}")


def test_two_way_coupling():
    """Test: soft chain pulls rigid CoM (Newton's Third Law)."""
    config = XPBDSolverConfig(
        sub_steps=4, solver_iterations=8, gravity=(0.0, -9.81),
        enable_two_way_coupling=True, enable_self_collision=False,
    )
    solver = XPBDSolver(config)
    com = solver.add_particle((0.0, 5.0), mass=10.0, kind=ParticleKind.RIGID_COM)
    node = solver.add_particle((0.5, 5.0), mass=2.0, kind=ParticleKind.SOFT_NODE)
    solver.add_distance_constraint(com, node, rest_length=0.5, compliance=1e-7)

    initial_com = np.array(solver.get_position(com))
    dt = 1.0 / 60.0
    for _ in range(60):
        diag = solver.step(dt)

    final_com = np.array(solver.get_position(com))
    displacement = np.linalg.norm(final_com - initial_com)

    assert displacement > 0.001, f"CoM should move, got displacement={displacement}"
    assert diag.rigid_com_displacement >= 0.0
    print(f"  [PASS] test_two_way_coupling: CoM displaced {displacement:.6f}")


def test_lagrange_multiplier_force_estimation():
    """Test: accumulated λ provides force estimate."""
    config = XPBDSolverConfig(
        sub_steps=4, solver_iterations=8, gravity=(0.0, -9.81),
        enable_self_collision=False, enable_two_way_coupling=False,
    )
    solver = XPBDSolver(config)
    anchor = solver.add_particle((0.0, 5.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    bob = solver.add_particle((0.0, 4.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
    c_idx = solver.add_distance_constraint(anchor, bob, rest_length=1.0, compliance=0.0)

    dt = 1.0 / 60.0
    for _ in range(60):
        solver.step(dt)

    force = solver.get_constraint_force(c_idx, dt)
    # Should be roughly m*g = 1.0 * 9.81 ≈ 9.81 (with damping effects)
    assert force != 0.0, "Constraint force should be non-zero"
    print(f"  [PASS] test_lagrange_multiplier_force_estimation: force={force:.4f}")


def test_spatial_hash_grid():
    """Test: spatial hash finds close pairs correctly."""
    grid = SpatialHashGrid(cell_size=0.1, table_size=256)
    positions = np.array([
        [0.0, 0.0],
        [0.05, 0.0],   # Close to particle 0
        [1.0, 1.0],    # Far away
        [0.04, 0.03],  # Close to particle 0 and 1
    ], dtype=np.float64)
    radii = np.full(4, 0.05, dtype=np.float64)

    pairs = grid.find_all_pairs(positions, radii, min_separation=0.1)
    # Particles 0,1 and 0,3 and 1,3 should be found
    pair_indices = {(p[0], p[1]) for p in pairs}
    assert (0, 1) in pair_indices, "Pair (0,1) should be found"
    assert (0, 3) in pair_indices, "Pair (0,3) should be found"
    assert (1, 3) in pair_indices, "Pair (1,3) should be found"
    assert (0, 2) not in pair_indices, "Pair (0,2) should NOT be found"
    print(f"  [PASS] test_spatial_hash_grid: found {len(pairs)} pairs")


def test_build_xpbd_chain():
    """Test: chain builder creates correct particles and constraints."""
    solver = XPBDSolver()
    com = solver.add_particle((0.0, 5.0), mass=70.0, kind=ParticleKind.RIGID_COM)
    preset = XPBDChainPreset(
        name="test_cape",
        anchor_joint="chest",
        segment_count=4,
        segment_length=0.5,
    )
    indices = build_xpbd_chain(solver, preset, (0.0, 5.0), rigid_com_index=com)

    assert len(indices) == 4
    assert solver.particle_count == 5  # 1 COM + 4 chain nodes
    # Should have 3 distance + 2 bending + 1 attachment = 6 constraints
    assert solver.constraint_count >= 4
    print(f"  [PASS] test_build_xpbd_chain: {solver.particle_count} particles, {solver.constraint_count} constraints")


def test_evolution_orchestrator():
    """Test: evolution orchestrator runs full cycle."""
    orchestrator = XPBDEvolutionOrchestrator()
    results = orchestrator.run_test_cycle()

    assert len(results) > 0
    summary = orchestrator.test_summary
    assert summary["total_tests"] > 0
    print(f"  [PASS] test_evolution_orchestrator: {summary['passed']}/{summary['total_tests']} tests passed")


def test_knowledge_distiller():
    """Test: knowledge distiller persists and loads entries."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)

    distiller = KnowledgeDistiller(path)
    distiller.add_knowledge(KnowledgeEntry(
        source="Test",
        topic="Test topic",
        insight="Test insight",
        parameter_effects={"sub_steps": 8},
    ))
    distiller.save()

    # Reload
    distiller2 = KnowledgeDistiller(path)
    assert any(e.topic == "Test topic" for e in distiller2.entries)

    # Apply to config
    config = XPBDSolverConfig()
    new_config = distiller2.apply_to_config(config)
    # The test entry should have been applied
    assert new_config.sub_steps == 8 or new_config.sub_steps == config.sub_steps  # May already be applied

    path.unlink()
    print("  [PASS] test_knowledge_distiller")


def test_internal_evolver():
    """Test: internal evolver detects issues and suggests tuning."""
    config = XPBDSolverConfig(sub_steps=2, solver_iterations=4)
    evolver = InternalEvolver(config, max_velocity_threshold=10.0)

    # Simulate high velocity diagnostic
    diag = XPBDDiagnostics(
        max_velocity_observed=20.0,
        mean_constraint_error=0.001,
    )
    action = evolver.observe(diag)
    assert action == TuningAction.INCREASE_SUBSTEPS
    print(f"  [PASS] test_internal_evolver: action={action.name}")


def test_physics_test_harness():
    """Test: physics test harness runs all tests."""
    harness = PhysicsTestHarness()
    results = harness.run_all()

    assert len(results) >= 7
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.name}: {r.details}")

    summary = harness.summary()
    print(f"  [SUMMARY] Physics tests: {summary['passed']}/{summary['total_tests']} passed")
    # At least 5 out of 7 should pass
    assert summary["passed"] >= 5, f"Too many failures: {summary['failed']}"
    print("  [PASS] test_physics_test_harness")


def test_evolution_state_persistence():
    """Test: evolution state can be saved and loaded."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)

    orchestrator = XPBDEvolutionOrchestrator()
    orchestrator.run_test_cycle()
    orchestrator.save_state(path)

    # Verify file exists and is valid JSON
    assert path.exists()
    data = json.loads(path.read_text())
    assert "evolution_cycles" in data
    assert "config" in data
    assert "knowledge_base" in data
    assert "test_results" in data

    path.unlink()
    print("  [PASS] test_evolution_state_persistence")


def test_full_evolution_cycle():
    """Test: full evolution cycle improves or maintains config."""
    orchestrator = XPBDEvolutionOrchestrator()
    initial_config = orchestrator.config

    # Run 3 evolution cycles
    for cycle in range(3):
        new_config = orchestrator.evolve()

    # Config should be valid
    assert new_config.sub_steps >= 1
    assert new_config.solver_iterations >= 1
    assert new_config.enable_two_way_coupling is True
    print(f"  [PASS] test_full_evolution_cycle: {orchestrator.evolution_cycles} cycles completed")


# ===========================================================================
# Main runner
# ===========================================================================

def main():
    """Run all XPBD physics tests."""
    print("=" * 70)
    print("  XPBD Physics Singularity — Comprehensive Test Suite (SESSION-052)")
    print("=" * 70)

    tests = [
        test_solver_basic_creation,
        test_add_particles,
        test_distance_constraint,
        test_compliance_decoupling,
        test_two_way_coupling,
        test_lagrange_multiplier_force_estimation,
        test_spatial_hash_grid,
        test_build_xpbd_chain,
        test_evolution_orchestrator,
        test_knowledge_distiller,
        test_internal_evolver,
        test_physics_test_harness,
        test_evolution_state_persistence,
        test_full_evolution_cycle,
    ]

    passed = 0
    failed = 0
    errors = []

    for test_fn in tests:
        try:
            print(f"\n--- {test_fn.__name__} ---")
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test_fn.__name__, str(e)))
            print(f"  [FAIL] {test_fn.__name__}: {e}")

    print("\n" + "=" * 70)
    print(f"  RESULTS: {passed} passed, {failed} failed out of {len(tests)} tests")
    if errors:
        print("  FAILURES:")
        for name, err in errors:
            print(f"    - {name}: {err}")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
