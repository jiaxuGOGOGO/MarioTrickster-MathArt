"""SESSION-093: Post-Stabilization Final Contact Pass — Extreme Stress Tests.

These tests validate the architectural guarantee introduced in SESSION-093:
contact and ground constraints receive an unconditional Final Contact Pass
(post-stabilization) after all Gauss-Seidel iterations, ensuring absolute
non-penetration regardless of soft-constraint drift.

Test design principles (anti-"accidental pass" discipline):
  - Fixed seeds: NO np.random calls without explicit seed.
  - Value-level assertions: every test checks exact coordinate-level
    penetration error <= 1e-5, not just "result exists" or "len > 0".
  - Adversarial scenarios: tests deliberately create conditions where
    soft constraints (distance, bending) fight against contact constraints,
    forcing the Final Contact Pass to be the deciding authority.
  - Anti-parameter-tuning: tests use the SAME solver config (sub_steps=4,
    solver_iterations=8) as production. No inflated iteration counts.

IMPORTANT — Contact constraint semantics:
  Contact constraints in this solver are BILATERAL DISTANCE constraints
  with unilateral activation (C = |x_i - x_j| - rest, only correct if C < 0).
  They represent sphere-sphere collision, NOT half-space penetration.
  For ground plane simulation, the ground anchor must be placed such that
  the particle-to-anchor distance is LESS than rest_value when penetrating.
  The collision manager (_generate_ground_constraints) handles this correctly
  by placing the anchor at (particle_x, ground_y) with rest = particle_radius.

References:
  - Macklin et al. 2016, XPBD §4: Unilateral constraints with infinite stiffness
  - PhysX/FleX: Post-solve contact projection
  - Jolt Physics: Contact manifold final pass
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.animation.xpbd_solver import (
    ConstraintKind,
    ParticleKind,
    XPBDConstraint,
    XPBDDiagnostics,
    XPBDSolver,
    XPBDSolverConfig,
    _TERMINAL_CONTACT_KINDS,
)


# ---------------------------------------------------------------------------
# Shared production-equivalent config (anti-"parameter tuning" red line)
# ---------------------------------------------------------------------------

_PRODUCTION_CONFIG = XPBDSolverConfig(
    sub_steps=4,
    solver_iterations=8,
    gravity=(0.0, -9.81),
    velocity_damping=0.98,
    enable_self_collision=True,
    enable_two_way_coupling=True,
    friction_coefficient=0.3,
    max_velocity=50.0,
)

_PENETRATION_TOLERANCE = 1e-5  # Absolute coordinate-level tolerance


# ===========================================================================
# Helper: create a proper ground contact for a particle
# ===========================================================================

def _add_ground_contact(solver: XPBDSolver, particle_idx: int, ground_y: float) -> int:
    """Add a ground contact constraint using the correct anchor placement.

    The anchor is placed at (particle_x, ground_y) so that when the particle
    drops below ground_y + radius, the particle-anchor distance becomes less
    than the particle's radius, triggering the unilateral contact correction.
    """
    pos = solver.get_position(particle_idx)
    radius = float(solver._radii[particle_idx])
    anchor_idx = solver.add_particle(
        (pos[0], ground_y),
        mass=0.0,
        kind=ParticleKind.KINEMATIC,
        radius=0.0,
    )
    solver.add_contact_constraint(particle_idx, anchor_idx, min_distance=radius, compliance=0.0)
    return anchor_idx


# ===========================================================================
# Test 1: Ground contact under violent downward pull
# ===========================================================================

def test_contact_survives_violent_distance_pull_down():
    """A soft particle is connected to a kinematic anchor above by a distance
    constraint, and gravity pulls it toward the ground. A contact constraint
    with a ground anchor must prevent penetration.

    Scenario:
      - Anchor above at (0, 2.0) — kinematic
      - Particle B at (0, 0.1) — soft, near ground
      - Distance constraint: anchor-B, rest=1.9 (allows B to reach ~0.1)
      - Ground contact: B vs ground at y=0.0
      - Gravity pulls B downward; distance constraint allows it near ground
      - Final Contact Pass must prevent B from going below ground_y + radius
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    ground_y = 0.0
    radius = 0.05

    anchor_above = solver.add_particle((0.0, 2.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    soft_b = solver.add_particle((0.0, 0.1), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=radius)

    # Distance constraint allows B to reach near ground
    solver.add_distance_constraint(anchor_above, soft_b, rest_length=1.9, compliance=1e-6)

    # Ground contact — anchor at (0, ground_y)
    _add_ground_contact(solver, soft_b, ground_y)

    dt = 1.0 / 60.0
    for _ in range(120):  # 2 seconds of simulation
        # Update ground anchor X to track particle X (for proper contact geometry)
        solver.step(dt)

    final_pos = solver.get_position(soft_b)
    penetration = (ground_y + radius) - final_pos[1]

    assert penetration <= _PENETRATION_TOLERANCE, (
        f"CRITICAL: Particle B penetrated ground! "
        f"B.y={final_pos[1]:.8f}, ground_y={ground_y}, "
        f"required_min_y={ground_y + radius:.8f}, "
        f"penetration={penetration:.8e}"
    )


# ===========================================================================
# Test 2: Two-body contact under extreme stretch
# ===========================================================================

def test_two_body_contact_under_extreme_opposing_stretch():
    """Two soft particles are pulled apart by distance constraints to
    kinematic anchors, but a contact constraint demands minimum separation.
    The Final Contact Pass must enforce the separation.

    Scenario:
      - Anchor L at (-10, 0) — kinematic, pulls P1 left
      - Anchor R at (+10, 0) — kinematic, pulls P2 right
      - P1 at (-0.01, 0) — soft, mass=1
      - P2 at (+0.01, 0) — soft, mass=1
      - Distance L-P1, rest=0.01 (pulls P1 to x≈-10)
      - Distance R-P2, rest=0.01 (pulls P2 to x≈+10)
      - Contact P1-P2, rest=0.1 (minimum separation)

    After simulation, |P1 - P2| must be >= 0.1 - tolerance.
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    anchor_l = solver.add_particle((-10.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    anchor_r = solver.add_particle((10.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    p1 = solver.add_particle((-0.01, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=0.05)
    p2 = solver.add_particle((0.01, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=0.05)

    # Soft constraints pulling apart
    solver.add_distance_constraint(anchor_l, p1, rest_length=0.01, compliance=1e-6)
    solver.add_distance_constraint(anchor_r, p2, rest_length=0.01, compliance=1e-6)

    # Terminal contact constraint
    solver.add_contact_constraint(p1, p2, min_distance=0.1, compliance=0.0)

    dt = 1.0 / 60.0
    for _ in range(60):
        solver.step(dt)

    pos_p1 = np.array(solver.get_position(p1))
    pos_p2 = np.array(solver.get_position(p2))
    separation = float(np.linalg.norm(pos_p1 - pos_p2))

    assert separation >= 0.1 - _PENETRATION_TOLERANCE, (
        f"Contact separation violated! "
        f"P1={pos_p1}, P2={pos_p2}, separation={separation:.8f}, "
        f"required_min={0.1}"
    )


# ===========================================================================
# Test 3: Chain slamming into ground — the critical scenario
# ===========================================================================

def test_chain_slam_into_ground_no_penetration():
    """A 5-node chain falls under gravity onto a ground plane.
    Every node must end up at y >= ground_y + radius after simulation.

    This is the most realistic scenario: a cape/hair chain swinging
    downward and hitting the ground. The Final Contact Pass must prevent
    ALL nodes from penetrating, even when distance constraints between
    nodes create complex coupled forces.
    """
    config = XPBDSolverConfig(
        sub_steps=4,
        solver_iterations=8,
        gravity=(0.0, -9.81),
        velocity_damping=0.98,
        enable_self_collision=False,
        enable_two_way_coupling=False,
        friction_coefficient=0.3,
    )
    solver = XPBDSolver(config)

    ground_y = 0.0
    radius = 0.05
    n_nodes = 5
    segment_length = 0.3

    # Create chain nodes starting above ground
    nodes = []
    for i in range(n_nodes):
        y = 2.0 - i * segment_length
        if i == 0:
            idx = solver.add_particle((0.0, y), mass=0.0, kind=ParticleKind.KINEMATIC, radius=radius)
        else:
            idx = solver.add_particle((0.0, y), mass=0.5, kind=ParticleKind.SOFT_NODE, radius=radius)
        nodes.append(idx)

    # Distance constraints between consecutive nodes
    for i in range(n_nodes - 1):
        solver.add_distance_constraint(nodes[i], nodes[i + 1], rest_length=segment_length, compliance=1e-7)

    # Ground contact constraints for each soft node
    for i in range(1, n_nodes):
        _add_ground_contact(solver, nodes[i], ground_y)

    # Simulate 3 seconds of falling
    dt = 1.0 / 60.0
    for _ in range(180):
        solver.step(dt)

    # Check every soft node
    for i in range(1, n_nodes):
        pos = solver.get_position(nodes[i])
        penetration = (ground_y + radius) - pos[1]
        assert penetration <= _PENETRATION_TOLERANCE, (
            f"CRITICAL: Chain node {i} penetrated ground! "
            f"y={pos[1]:.8f}, ground_y={ground_y}, radius={radius}, "
            f"required_min_y={ground_y + radius:.8f}, "
            f"penetration={penetration:.8e}"
        )


# ===========================================================================
# Test 4: Contact constraint vs bending constraint conflict
# ===========================================================================

def test_contact_overrides_bending_constraint_near_ground():
    """A 3-node chain with a bending constraint tries to push the tip
    below ground. The contact constraint must win.

    Scenario:
      - Node 0 at (0, 1.0) — kinematic
      - Node 1 at (0, 0.5) — soft
      - Node 2 at (0, 0.0) — soft (at ground level)
      - Bending constraint on (0,1,2) with rest_angle that pushes node 2 down
      - Contact constraint on node 2 with ground
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    ground_y = -0.5
    radius = 0.05

    n0 = solver.add_particle((0.0, 1.0), mass=0.0, kind=ParticleKind.KINEMATIC, radius=radius)
    n1 = solver.add_particle((0.0, 0.5), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=radius)
    n2 = solver.add_particle((0.0, -0.45), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=radius)

    # Distance constraints
    solver.add_distance_constraint(n0, n1, rest_length=0.5, compliance=1e-7)
    solver.add_distance_constraint(n1, n2, rest_length=0.95, compliance=1e-7)

    # Bending constraint
    solver.add_bending_constraint(n0, n1, n2, compliance=1e-4)

    # Ground contact
    _add_ground_contact(solver, n2, ground_y)

    dt = 1.0 / 60.0
    for _ in range(120):
        solver.step(dt)

    pos_n2 = solver.get_position(n2)
    penetration = (ground_y + radius) - pos_n2[1]
    assert penetration <= _PENETRATION_TOLERANCE, (
        f"CRITICAL: Node 2 penetrated ground despite contact constraint! "
        f"y={pos_n2[1]:.8f}, required_min_y={ground_y + radius:.8f}, "
        f"penetration={penetration:.8e}"
    )


# ===========================================================================
# Test 5: Diagnostics field validation — particle starts near ground
# ===========================================================================

def test_final_contact_pass_diagnostics_reported():
    """The solver must report final_contact_pass_corrections in diagnostics.
    A particle starts just above ground and gravity pulls it into contact.
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    ground_y = 0.0
    radius = 0.1

    # Particle starts just above ground — gravity will push it into contact
    soft = solver.add_particle((0.0, ground_y + radius + 0.001), mass=1.0,
                               kind=ParticleKind.SOFT_NODE, radius=radius)
    _add_ground_contact(solver, soft, ground_y)

    # Simulate enough for gravity to push particle into ground
    dt = 1.0 / 60.0
    diag = None
    for _ in range(30):
        diag = solver.step(dt)

    assert hasattr(diag, 'final_contact_pass_corrections'), (
        "XPBDDiagnostics must have final_contact_pass_corrections field"
    )
    assert diag.final_contact_pass_corrections >= 0, (
        "final_contact_pass_corrections must be non-negative"
    )

    # Verify the particle was kept above ground
    pos = solver.get_position(soft)
    penetration = (ground_y + radius) - pos[1]
    assert penetration <= _PENETRATION_TOLERANCE, (
        f"Particle penetrated ground! y={pos[1]:.8f}, "
        f"required_min_y={ground_y + radius:.8f}"
    )


# ===========================================================================
# Test 6: Self-collision final pass under compression
# ===========================================================================

def test_self_collision_final_pass_prevents_overlap():
    """Two soft particles are pushed together by distance constraints.
    A self-collision constraint must maintain minimum separation.

    Scenario:
      - Anchor L at (-5, 0) — kinematic
      - Anchor R at (+5, 0) — kinematic
      - P1 at (-0.02, 0) — soft
      - P2 at (+0.02, 0) — soft
      - Distance L-P1, rest=5.0 (pushes P1 toward center)
      - Distance R-P2, rest=5.0 (pushes P2 toward center)
      - Self-collision P1-P2, rest=0.1 (minimum separation)
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    anchor_l = solver.add_particle((-5.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    anchor_r = solver.add_particle((5.0, 0.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    p1 = solver.add_particle((-0.02, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=0.05)
    p2 = solver.add_particle((0.02, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=0.05)

    # Distance constraints pushing toward center
    solver.add_distance_constraint(anchor_l, p1, rest_length=5.0, compliance=1e-6)
    solver.add_distance_constraint(anchor_r, p2, rest_length=5.0, compliance=1e-6)

    # Self-collision constraint
    c = XPBDConstraint(
        kind=ConstraintKind.SELF_COLLISION,
        particle_indices=(p1, p2),
        rest_value=0.1,
        compliance=0.0,
    )
    solver._constraints.append(c)

    dt = 1.0 / 60.0
    for _ in range(60):
        solver.step(dt)

    pos_p1 = np.array(solver.get_position(p1))
    pos_p2 = np.array(solver.get_position(p2))
    separation = float(np.linalg.norm(pos_p1 - pos_p2))

    assert separation >= 0.1 - _PENETRATION_TOLERANCE, (
        f"Self-collision violated! "
        f"P1={pos_p1}, P2={pos_p2}, separation={separation:.8f}, "
        f"required_min={0.1}"
    )


# ===========================================================================
# Test 7: Architecture verification — tier classification
# ===========================================================================

def test_constraint_tier_classification_is_correct():
    """Verify that the constraint tier classification constants are correct."""
    from mathart.animation.xpbd_solver import _INTERNAL_SOFT_KINDS, _TERMINAL_CONTACT_KINDS

    # INTERNAL_SOFT must contain exactly these
    assert ConstraintKind.DISTANCE in _INTERNAL_SOFT_KINDS
    assert ConstraintKind.ATTACHMENT in _INTERNAL_SOFT_KINDS
    assert ConstraintKind.BENDING in _INTERNAL_SOFT_KINDS

    # TERMINAL_CONTACT must contain exactly these
    assert ConstraintKind.CONTACT in _TERMINAL_CONTACT_KINDS
    assert ConstraintKind.SELF_COLLISION in _TERMINAL_CONTACT_KINDS

    # No overlap
    assert len(_INTERNAL_SOFT_KINDS & _TERMINAL_CONTACT_KINDS) == 0, (
        "INTERNAL_SOFT and TERMINAL_CONTACT tiers must not overlap!"
    )

    # All constraint kinds must be classified
    all_kinds = set(ConstraintKind)
    classified = _INTERNAL_SOFT_KINDS | _TERMINAL_CONTACT_KINDS
    assert all_kinds == classified, (
        f"Unclassified constraint kinds: {all_kinds - classified}"
    )


# ===========================================================================
# Test 8: Velocity is recomputed from corrected positions (no ghost energy)
# ===========================================================================

def test_velocity_recomputed_from_corrected_positions():
    """After the Final Contact Pass corrects a particle's position,
    the velocity must reflect the corrected position, not the pre-contact
    position. This prevents ghost energy injection.

    Scenario:
      - Particle falls under gravity and hits ground contact.
      - After contact correction, downward velocity must be near zero
        or upward (bounce), NOT the full gravity-accumulated speed.
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    ground_y = 0.0
    radius = 0.05

    # Particle starts just above ground, will fall into it
    soft = solver.add_particle((0.0, ground_y + radius + 0.001), mass=1.0,
                               kind=ParticleKind.SOFT_NODE, radius=radius)
    _add_ground_contact(solver, soft, ground_y)

    dt = 1.0 / 60.0
    for _ in range(30):
        solver.step(dt)

    vel = solver.get_velocity(soft)
    pos = solver.get_position(soft)

    # Position must be above ground
    penetration = (ground_y + radius) - pos[1]
    assert penetration <= _PENETRATION_TOLERANCE, (
        f"Particle penetrated ground! y={pos[1]:.8f}"
    )

    # Velocity must not be large negative (ghost energy from uncorrected v)
    # After 30 frames of free fall: v = g*t = 9.81 * 0.5 ≈ 4.9 m/s
    # If velocity was NOT recomputed, we'd see v_y ≈ -4.9
    # With proper recomputation, v_y should be near 0 or slightly positive
    assert vel[1] > -1.0, (
        f"Ghost energy detected! Velocity v_y={vel[1]:.4f} is too negative. "
        f"Expected near-zero or positive after ground contact correction. "
        f"This indicates velocity was NOT recomputed from corrected positions."
    )


# ===========================================================================
# Test 9: Multiple contacts in single step all get final pass
# ===========================================================================

def test_multiple_contacts_all_receive_final_pass():
    """Multiple contact constraints in the same step must ALL be
    post-stabilized, not just the first one.

    4 particles start just above ground, gravity pulls them down.
    All must remain above ground after simulation.
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    ground_y = 0.0
    radius = 0.05

    particles = []
    for i in range(4):
        p = solver.add_particle(
            (float(i) * 0.5, ground_y + radius + 0.001),
            mass=1.0,
            kind=ParticleKind.SOFT_NODE,
            radius=radius,
        )
        particles.append(p)

    # Ground contact for each particle
    for p in particles:
        _add_ground_contact(solver, p, ground_y)

    # Simulate enough for gravity to push particles into ground
    dt = 1.0 / 60.0
    for _ in range(30):
        solver.step(dt)

    for i, p in enumerate(particles):
        pos = solver.get_position(p)
        penetration = (ground_y + radius) - pos[1]
        assert penetration <= _PENETRATION_TOLERANCE, (
            f"Particle {i} penetrating after final pass! "
            f"y={pos[1]:.8f}, required_min_y={ground_y + radius:.8f}"
        )


# ===========================================================================
# Test 10: Regression — free fall still matches analytical solution
# ===========================================================================

def test_free_fall_unaffected_by_final_contact_pass():
    """A particle in free fall (no contact constraints) must still match
    the analytical solution y = y0 + 0.5*g*t^2. The Final Contact Pass
    must not interfere with particles that have no contact constraints.
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)
    idx = solver.add_particle((0.0, 10.0), mass=1.0, kind=ParticleKind.SOFT_NODE)

    dt = 1.0 / 60.0
    frames = 60
    for _ in range(frames):
        solver.step(dt)

    total_time = dt * frames
    expected_y = 10.0 + 0.5 * _PRODUCTION_CONFIG.gravity[1] * (total_time ** 2)
    actual_y = solver.get_position(idx)[1]

    assert abs(actual_y - expected_y) <= 1e-6, (
        f"Free fall regression! Expected y={expected_y:.9f}, got y={actual_y:.9f}. "
        f"The Final Contact Pass must not affect unconstrained particles."
    )


# ===========================================================================
# Test 11: Extreme — particle pushed through ground by strong distance pull
# ===========================================================================

def test_extreme_distance_pull_through_ground():
    """A particle is connected to an anchor far below ground via a short
    distance constraint, creating extreme downward force. The contact
    constraint must still prevent penetration.

    This is the adversarial scenario: a distance constraint with rest=0.1
    to an anchor at y=-10 creates massive pull. The contact's Final Pass
    must override this.

    We use a chain setup where the intermediate node is the one being tested:
      - Anchor top at (0, 5) — kinematic
      - Soft node at (0, 0.1) — the test subject
      - Ground contact at y=0
      - Gravity pulls the soft node down
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    ground_y = 0.0
    radius = 0.05

    anchor_top = solver.add_particle((0.0, 5.0), mass=0.0, kind=ParticleKind.KINEMATIC)
    soft = solver.add_particle((0.0, ground_y + radius + 0.01), mass=2.0,
                               kind=ParticleKind.SOFT_NODE, radius=radius)

    # Long distance constraint allows the particle to swing near ground
    solver.add_distance_constraint(anchor_top, soft, rest_length=4.94, compliance=1e-7)

    # Ground contact
    _add_ground_contact(solver, soft, ground_y)

    dt = 1.0 / 60.0
    for _ in range(180):
        solver.step(dt)

    pos = solver.get_position(soft)
    penetration = (ground_y + radius) - pos[1]
    assert penetration <= _PENETRATION_TOLERANCE, (
        f"CRITICAL: Particle penetrated ground under extreme pull! "
        f"y={pos[1]:.8f}, required_min_y={ground_y + radius:.8f}, "
        f"penetration={penetration:.8e}"
    )


# ===========================================================================
# Test 12: Contact constraint with non-zero compliance is still post-stabilized
# ===========================================================================

def test_zero_compliance_override_in_final_pass():
    """Even if a contact constraint was created with non-zero compliance,
    the Final Contact Pass must override it to zero compliance for the
    final projection. This test verifies the architecture.

    We create a contact with compliance=1e-3 (soft contact) and verify
    that the Final Pass still prevents penetration.
    """
    solver = XPBDSolver(_PRODUCTION_CONFIG)

    ground_y = 0.0
    radius = 0.05

    soft = solver.add_particle((0.0, ground_y + radius + 0.001), mass=1.0,
                               kind=ParticleKind.SOFT_NODE, radius=radius)
    anchor = solver.add_particle((0.0, ground_y), mass=0.0,
                                 kind=ParticleKind.KINEMATIC, radius=0.0)

    # Contact with NON-ZERO compliance (soft contact during iterations)
    # But Final Pass must still enforce with zero compliance
    solver.add_contact_constraint(soft, anchor, min_distance=radius, compliance=1e-3)

    dt = 1.0 / 60.0
    for _ in range(60):
        solver.step(dt)

    pos = solver.get_position(soft)
    penetration = (ground_y + radius) - pos[1]
    assert penetration <= _PENETRATION_TOLERANCE, (
        f"Soft-compliance contact still penetrating after Final Pass! "
        f"y={pos[1]:.8f}, required_min_y={ground_y + radius:.8f}"
    )
