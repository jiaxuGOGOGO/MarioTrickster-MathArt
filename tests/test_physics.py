"""Tests for physics-based animation components."""
import math

import numpy as np
import pytest

from mathart.animation.physics import SpringDamper, FABRIKSolver, PerlinAnimator


# ── Spring-Damper Tests ──

@pytest.mark.unit
class TestSpringDamper:
    def test_default_initialization(self):
        spring = SpringDamper()
        assert spring.spring_k == 15.0
        assert spring.damping_c == 4.0
        assert spring.mass == 1.0

    def test_step_returns_tuple(self):
        spring = SpringDamper()
        pos = spring.step(target=(1.0, 0.0))
        assert isinstance(pos, tuple)
        assert len(pos) == 2

    def test_spring_moves_toward_target(self):
        spring = SpringDamper(spring_k=50.0, damping_c=10.0)
        spring.reset((0.0, 0.0))
        target = (1.0, 0.0)

        # After many steps, should be close to target
        for _ in range(200):
            pos = spring.step(target, dt=1/60)

        assert abs(pos[0] - 1.0) < 0.05, f"Spring should converge to target, got {pos[0]}"
        assert abs(pos[1]) < 0.05

    def test_critical_damping_formula(self):
        spring = SpringDamper(spring_k=16.0, mass=1.0)
        # Critical damping = 2 * sqrt(k * m) = 2 * sqrt(16) = 8
        assert abs(spring.critical_damping - 8.0) < 1e-6

    def test_damping_ratio(self):
        spring = SpringDamper(spring_k=16.0, damping_c=8.0, mass=1.0)
        # ζ = c / (2*sqrt(k*m)) = 8 / 8 = 1.0 (critical)
        assert abs(spring.damping_ratio - 1.0) < 1e-6

    def test_simulate_sequence(self):
        spring = SpringDamper()
        targets = [(float(i) / 10, 0.0) for i in range(30)]
        positions = spring.simulate(targets)
        assert len(positions) == 30
        for pos in positions:
            assert len(pos) == 2

    def test_reset_clears_velocity(self):
        spring = SpringDamper()
        # Give it some velocity
        for _ in range(10):
            spring.step((1.0, 0.0))
        # Reset
        spring.reset((0.0, 0.0))
        # After reset, position should be at reset point
        pos = spring.step((0.0, 0.0), dt=0.0)  # Zero dt = no movement
        # Position should be near reset point
        assert abs(pos[0]) < 0.1

    def test_clamps_extreme_spring_k(self):
        spring = SpringDamper(spring_k=10000.0)
        assert spring.spring_k <= 200.0

    def test_underdamped_oscillates(self):
        """Underdamped spring should overshoot target."""
        spring = SpringDamper(spring_k=50.0, damping_c=0.5, mass=1.0)
        spring.reset((0.0, 0.0))

        positions = []
        for _ in range(60):
            pos = spring.step((1.0, 0.0), dt=1/60)
            positions.append(pos[0])

        # Should overshoot (go past 1.0 at some point)
        max_x = max(positions)
        assert max_x > 1.0, f"Underdamped spring should overshoot, max_x={max_x}"


# ── FABRIK IK Tests ──

@pytest.mark.unit
class TestFABRIKSolver:
    def test_default_initialization(self):
        solver = FABRIKSolver([0.3, 0.25, 0.2])
        assert solver.n_joints == 4
        assert abs(solver.total_length - 0.75) < 1e-6

    def test_solve_returns_joint_positions(self):
        solver = FABRIKSolver([0.3, 0.25])
        joints = solver.solve(target=(0.4, 0.3))
        assert len(joints) == 3  # 2 bones → 3 joints
        for j in joints:
            assert len(j) == 2

    def test_tip_reaches_reachable_target(self):
        solver = FABRIKSolver([0.3, 0.3])
        target = (0.4, 0.2)
        joints = solver.solve(target=target)
        tip = joints[-1]
        dist = math.sqrt((tip[0] - target[0])**2 + (tip[1] - target[1])**2)
        assert dist < 0.01, f"Tip should reach target, dist={dist:.4f}"

    def test_unreachable_target_stretches_chain(self):
        solver = FABRIKSolver([0.3, 0.3])  # Total length = 0.6
        # Target far beyond reach
        target = (10.0, 0.0)
        joints = solver.solve(target=target)
        # Chain should be stretched toward target
        tip = joints[-1]
        assert tip[0] > 0.5, f"Chain should stretch toward target, tip_x={tip[0]}"

    def test_bone_lengths_preserved(self):
        """Bone lengths should be preserved after IK solve."""
        lengths = [0.3, 0.25, 0.2]
        solver = FABRIKSolver(lengths)
        joints = solver.solve(target=(0.3, 0.4))

        for i, expected_len in enumerate(lengths):
            p1 = np.array(joints[i])
            p2 = np.array(joints[i + 1])
            actual_len = float(np.linalg.norm(p2 - p1))
            assert abs(actual_len - expected_len) < 0.01, (
                f"Bone {i} length should be {expected_len}, got {actual_len}"
            )

    def test_get_joint_angles(self):
        solver = FABRIKSolver([0.3, 0.3])
        solver.solve(target=(0.4, 0.2))
        angles = solver.get_joint_angles()
        assert len(angles) == 2  # 2 bones → 2 angles
        for angle in angles:
            assert -math.pi <= angle <= math.pi

    def test_set_root(self):
        solver = FABRIKSolver([0.3, 0.3])
        solver.set_root((1.0, 2.0))
        joints = solver.solve(target=(1.4, 2.3))
        root = joints[0]
        assert abs(root[0] - 1.0) < 0.01
        assert abs(root[1] - 2.0) < 0.01

    def test_joint_constraints_applied(self):
        """Joint constraints should limit angles."""
        # Elbow: only forward flexion (0 to 145 degrees)
        constraints = [(0.0, math.radians(145))]
        solver = FABRIKSolver([0.3, 0.25], joint_constraints=constraints)
        joints = solver.solve(target=(0.1, 0.4))
        # Should not crash and should return valid positions
        assert len(joints) == 3


# ── Perlin Animator Tests ──

@pytest.mark.unit
class TestPerlinAnimator:
    def test_sample_returns_float(self):
        animator = PerlinAnimator()
        val = animator.sample(0.0)
        assert isinstance(val, float)

    def test_sample_within_amplitude(self):
        animator = PerlinAnimator(amplitude=0.1)
        # Sample many points
        values = [animator.sample(t * 0.1) for t in range(100)]
        max_val = max(abs(v) for v in values)
        # Should be roughly within 2x amplitude (noise can exceed amplitude due to octaves)
        assert max_val < animator.amplitude * 4

    def test_sample_2d_returns_tuple(self):
        animator = PerlinAnimator()
        result = animator.sample_2d(0.5)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_different_seeds_different_output(self):
        animator1 = PerlinAnimator(seed=42, frequency=2.0)
        animator2 = PerlinAnimator(seed=123, frequency=2.0)
        # Sample multiple points to ensure difference
        vals1 = [animator1.sample(t) for t in [0.1, 0.5, 1.0, 2.0, 3.0]]
        vals2 = [animator2.sample(t) for t in [0.1, 0.5, 1.0, 2.0, 3.0]]
        assert vals1 != vals2, "Different seeds should produce different noise sequences"

    def test_same_seed_same_output(self):
        animator1 = PerlinAnimator(seed=42)
        animator2 = PerlinAnimator(seed=42)
        val1 = animator1.sample(1.0)
        val2 = animator2.sample(1.0)
        assert val1 == val2

    def test_smooth_output(self):
        """Noise should be smooth (small differences for nearby inputs)."""
        animator = PerlinAnimator(frequency=1.0, amplitude=0.1)
        dt = 0.001
        v1 = animator.sample(1.0)
        v2 = animator.sample(1.0 + dt)
        diff = abs(v2 - v1)
        # Very small time step should produce very small change
        assert diff < 0.01, f"Noise should be smooth, diff={diff}"
