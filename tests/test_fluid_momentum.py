"""P1-VFX-1B White-Box Regression Tests — SESSION-115.

Strict regression suite for the UMR Kinematic Impulse Adapter, Tensor-Based
Line-Segment Splatter, CFL Guard, and Fluid Momentum Controller.

Test groups:
1. Anti-Dotted-Line Guard — verify continuous band injection, no discrete dots.
2. Anti-Scalar-Grid-Loop Guard — verify zero Python ``for`` loops in hot paths.
3. Anti-Energy-Explosion / CFL Guard — verify NaN-free fields after extreme injection.
4. UMR Kinematic Extraction — verify correct root/effector velocity extraction.
5. Line-Segment Splatter Geometry — verify distance field correctness.
6. CFL Soft Tanh Clamping — verify velocity limiting behavior.
7. Closed-Loop Momentum Controller — end-to-end UMR-to-fluid pipeline.
8. Evolution Bridge Metrics — verify three-layer evaluation.
9. Backward Compatibility — existing fluid VFX tests still pass.
"""
from __future__ import annotations

import inspect
import math
import re
from pathlib import Path

import numpy as np
import pytest

from mathart.animation.umr_kinematic_impulse import (
    KinematicFrame,
    LineSegmentSplatter,
    UMRExtractionConfig,
    UMRKinematicImpulseAdapter,
    VectorFieldImpulse,
    compute_cfl_safe_velocity_limit,
    soft_tanh_clamp,
    vector_field_impulse_to_fluid_impulse,
)
from mathart.animation.fluid_momentum_controller import (
    FluidMomentumController,
    MomentumInjectionConfig,
    MomentumSimulationResult,
    MomentumVFXMetrics,
    evaluate_momentum_simulation,
)
from mathart.animation.fluid_vfx import (
    FluidGrid2D,
    FluidGridConfig,
    FluidDrivenVFXSystem,
    FluidVFXConfig,
    FluidImpulse,
)
from mathart.animation.unified_motion import (
    MotionRootTransform,
    MotionContactState,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers: Fabricate UMR clips for testing
# ═══════════════════════════════════════════════════════════════════════════


def _make_dash_clip(n_frames: int = 16, fps: int = 12) -> UnifiedMotionClip:
    """Create a synthetic UMR clip simulating a horizontal dash."""
    frames = []
    dt = 1.0 / fps
    dash_speed = 5.0  # world units per second
    for i in range(n_frames):
        t = i * dt
        x = 0.2 + dash_speed * t  # moving right
        y = 0.7
        frames.append(UnifiedMotionFrame(
            time=t,
            phase=float(i) / max(n_frames - 1, 1),
            root_transform=MotionRootTransform(
                x=x, y=y,
                velocity_x=dash_speed,
                velocity_y=0.0,
            ),
            joint_local_rotations={"hip": 0.0},
            contact_tags=MotionContactState(),
            frame_index=i,
            source_state="dash",
            metadata={},
        ))
    return UnifiedMotionClip(
        clip_id="test_dash",
        state="dash",
        fps=fps,
        frames=frames,
    )


def _make_slash_clip(n_frames: int = 16, fps: int = 12) -> UnifiedMotionClip:
    """Create a synthetic UMR clip simulating a weapon slash arc."""
    frames = []
    dt = 1.0 / fps
    cx, cy = 0.5, 0.6  # pivot centre
    arc_radius = 0.15
    arc_speed = 8.0  # radians per second
    for i in range(n_frames):
        t = i * dt
        angle = math.pi * 0.8 - arc_speed * t
        tip_x = cx + math.cos(angle) * arc_radius
        tip_y = cy + math.sin(angle) * arc_radius
        frames.append(UnifiedMotionFrame(
            time=t,
            phase=float(i) / max(n_frames - 1, 1),
            root_transform=MotionRootTransform(
                x=cx, y=cy,
                velocity_x=0.0,
                velocity_y=0.0,
            ),
            joint_local_rotations={"hip": 0.0},
            contact_tags=MotionContactState(),
            frame_index=i,
            source_state="slash",
            metadata={
                "weapon_tip_x": tip_x,
                "weapon_tip_y": tip_y,
            },
        ))
    return UnifiedMotionClip(
        clip_id="test_slash",
        state="slash",
        fps=fps,
        frames=frames,
    )


def _make_high_speed_clip(
    n_frames: int = 10,
    fps: int = 12,
    speed: float = 500.0,
) -> UnifiedMotionClip:
    """Create a clip with extremely high velocity for CFL stress testing."""
    frames = []
    dt = 1.0 / fps
    for i in range(n_frames):
        t = i * dt
        frames.append(UnifiedMotionFrame(
            time=t,
            phase=float(i) / max(n_frames - 1, 1),
            root_transform=MotionRootTransform(
                x=0.2 + speed * t,
                y=0.5,
                velocity_x=speed,
                velocity_y=speed * 0.3,
            ),
            joint_local_rotations={"hip": 0.0},
            contact_tags=MotionContactState(),
            frame_index=i,
            source_state="dash",
            metadata={
                "weapon_tip_x": 0.3 + speed * 1.2 * t,
                "weapon_tip_y": 0.4 + speed * 0.8 * t,
            },
        ))
    return UnifiedMotionClip(
        clip_id="test_extreme",
        state="dash",
        fps=fps,
        frames=frames,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 1: Anti-Dotted-Line Guard
# ═══════════════════════════════════════════════════════════════════════════


class TestAntiDottedLineGuard:
    """Verify that high-speed trajectories produce continuous bands, not dots."""

    def test_line_segment_produces_continuous_band(self):
        """A diagonal line segment must produce a smooth Gaussian ridge,
        not isolated point peaks."""
        splatter = LineSegmentSplatter(64)
        impulse = VectorFieldImpulse(
            start=(0.1, 0.1),
            end=(0.9, 0.9),
            velocity_vector=(1.0, 1.0),
            radius=0.08,
            density_amount=1.0,
        )
        fu, fv, fd = splatter.splat_impulse(impulse)

        # The density field along the diagonal should be continuous
        # Sample along the diagonal
        diag_values = np.array([fd[i, i] for i in range(64)])
        # All diagonal cells within the segment should have non-zero density
        interior = diag_values[4:60]  # exclude border cells
        assert np.all(interior > 0.01), (
            "Diagonal injection has zero-density gaps — dotted-line artifact!"
        )

    def test_fast_horizontal_sweep_no_gaps(self):
        """A fast horizontal sweep must not produce gaps in the velocity field."""
        splatter = LineSegmentSplatter(128)
        impulse = VectorFieldImpulse(
            start=(0.05, 0.5),
            end=(0.95, 0.5),
            velocity_vector=(2.0, 0.0),
            radius=0.06,
            density_amount=1.0,
        )
        fu, fv, fd = splatter.splat_impulse(impulse)

        # Check the row closest to y=0.5 (row 64)
        row = fu[64, :]
        # All cells along the sweep should have positive velocity
        interior = row[6:122]
        assert np.all(interior > 0.01), (
            "Horizontal sweep has velocity gaps — dotted-line artifact!"
        )

    def test_arc_trajectory_produces_crescent(self):
        """A weapon arc should produce a crescent-shaped momentum field."""
        splatter = LineSegmentSplatter(64)
        # Simulate an arc as multiple short segments
        n_segments = 8
        total_fu = np.zeros((64, 64))
        total_fv = np.zeros((64, 64))
        for i in range(n_segments):
            t0 = i / n_segments
            t1 = (i + 1) / n_segments
            angle0 = math.pi * (0.8 - 1.0 * t0)
            angle1 = math.pi * (0.8 - 1.0 * t1)
            cx, cy = 0.5, 0.6
            r = 0.18
            p0 = (cx + math.cos(angle0) * r, cy + math.sin(angle0) * r)
            p1 = (cx + math.cos(angle1) * r, cy + math.sin(angle1) * r)
            tangent = ((p1[0] - p0[0]) * 10, (p1[1] - p0[1]) * 10)
            imp = VectorFieldImpulse(
                start=p0, end=p1,
                velocity_vector=tangent,
                radius=0.06,
                density_amount=1.0,
            )
            fu, fv, _ = splatter.splat_impulse(imp)
            total_fu += fu
            total_fv += fv

        # The combined field should have energy spread across an arc region
        speed = np.sqrt(total_fu ** 2 + total_fv ** 2)
        # More than 5% of cells should have significant speed
        active_ratio = float(np.mean(speed > 0.01))
        assert active_ratio > 0.05, (
            f"Arc injection too sparse ({active_ratio:.3f}) — possible dotted-line!"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 2: Anti-Scalar-Grid-Loop Guard
# ═══════════════════════════════════════════════════════════════════════════


class TestAntiScalarGridLoop:
    """Verify zero Python for-loops in hot-path source code."""

    def test_no_for_loop_in_splatter_hot_path(self):
        """LineSegmentSplatter.compute_segment_distance_field and
        splat_impulse must not contain Python for-loops."""
        src = inspect.getsource(LineSegmentSplatter.compute_segment_distance_field)
        src += inspect.getsource(LineSegmentSplatter.splat_impulse)
        # Match "for ... in ..." but not in comments or docstrings
        lines = src.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            assert not re.match(r"^\s*for\s+\w+.*\s+in\s+", line), (
                f"Found Python for-loop in hot path: {line.strip()}"
            )

    def test_splatter_performance_large_grid(self):
        """128x128 grid splatting must complete in reasonable time."""
        import time
        splatter = LineSegmentSplatter(128)
        impulse = VectorFieldImpulse(
            start=(0.1, 0.2),
            end=(0.8, 0.9),
            velocity_vector=(1.0, -0.5),
            radius=0.1,
            density_amount=1.0,
        )
        start = time.perf_counter()
        for _ in range(100):
            splatter.splat_impulse(impulse)
        elapsed = time.perf_counter() - start
        # 100 splats on 128x128 should take < 2 seconds
        assert elapsed < 2.0, f"Splatter too slow: {elapsed:.2f}s for 100 iterations"


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 3: Anti-Energy-Explosion / CFL Guard
# ═══════════════════════════════════════════════════════════════════════════


class TestCFLGuard:
    """Verify CFL-safe velocity clamping prevents NaN and energy explosion."""

    def test_soft_tanh_clamp_limits_magnitude(self):
        """Soft tanh clamp must limit velocity to approximately v_max."""
        v_max = 1.0
        # Extreme input
        clamped = soft_tanh_clamp((1000.0, 500.0), v_max)
        mag = math.hypot(*clamped)
        assert mag < v_max * 1.01, f"Clamped magnitude {mag} exceeds v_max {v_max}"

    def test_soft_tanh_clamp_preserves_direction(self):
        """Clamped velocity must preserve the original direction."""
        vx, vy = 100.0, 200.0
        clamped = soft_tanh_clamp((vx, vy), 1.0)
        original_angle = math.atan2(vy, vx)
        clamped_angle = math.atan2(clamped[1], clamped[0])
        assert abs(original_angle - clamped_angle) < 1e-6

    def test_soft_tanh_clamp_zero_input(self):
        """Zero velocity must remain zero."""
        clamped = soft_tanh_clamp((0.0, 0.0), 1.0)
        assert clamped == (0.0, 0.0)

    def test_soft_tanh_clamp_small_input_passes_through(self):
        """Small velocities should pass through nearly unchanged."""
        v_max = 10.0
        clamped = soft_tanh_clamp((0.01, 0.02), v_max)
        # tanh(x) ≈ x for small x
        assert abs(clamped[0] - 0.01) < 0.001
        assert abs(clamped[1] - 0.02) < 0.001

    def test_cfl_safe_velocity_limit_formula(self):
        """Verify CFL limit formula: v_max = cfl_factor * dx / dt."""
        v_max = compute_cfl_safe_velocity_limit(32, 1.0 / 12.0, 0.5)
        dx = 1.0 / 32
        expected = 0.5 * dx / (1.0 / 12.0)
        assert abs(v_max - expected) < 1e-9

    def test_extreme_velocity_injection_no_nan(self):
        """Injecting extreme velocities via CFL-clamped adapter must not
        produce NaN in the fluid field."""
        clip = _make_high_speed_clip(n_frames=10, speed=5000.0)
        controller = FluidMomentumController(grid_size=32)
        result = controller.simulate_with_umr(clip, n_frames=10)

        assert not result.has_nan(), "NaN detected after extreme velocity injection!"
        for diag in result.fluid_diagnostics:
            assert np.isfinite(diag.mean_flow_energy)
            assert np.isfinite(diag.max_flow_speed)
            assert np.isfinite(diag.density_mass)

    def test_rapid_slash_no_nan(self):
        """Rapid weapon slash must not produce NaN."""
        clip = _make_slash_clip(n_frames=20, fps=24)
        controller = FluidMomentumController(grid_size=32)
        result = controller.simulate_with_umr(clip, n_frames=20)
        assert not result.has_nan(), "NaN detected during rapid slash!"


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 4: UMR Kinematic Extraction
# ═══════════════════════════════════════════════════════════════════════════


class TestUMRKinematicExtraction:
    """Verify correct extraction of root velocity and effector trajectories."""

    def test_dash_clip_extracts_horizontal_velocity(self):
        """Dash clip should produce positive horizontal root velocity."""
        clip = _make_dash_clip(n_frames=8)
        adapter = UMRKinematicImpulseAdapter(grid_size=32)
        kframes = adapter.extract_kinematic_frames(clip)

        assert len(kframes) == 8
        for kf in kframes:
            assert kf.source_state == "dash"
            # Root should have positive x velocity
            assert kf.root_velocity[0] > 0.0

    def test_slash_clip_extracts_weapon_tip(self):
        """Slash clip should extract weapon_tip positions and velocities."""
        clip = _make_slash_clip(n_frames=8)
        adapter = UMRKinematicImpulseAdapter(grid_size=32)
        kframes = adapter.extract_kinematic_frames(clip, effector_key="weapon_tip")

        for kf in kframes:
            assert "weapon_tip" in kf.effector_positions
        # From frame 1 onward, should have effector velocities
        for kf in kframes[1:]:
            assert "weapon_tip" in kf.effector_velocities

    def test_central_finite_difference_velocity(self):
        """Central finite difference should produce smooth velocity estimates."""
        clip = _make_dash_clip(n_frames=10, fps=12)
        config = UMRExtractionConfig(use_central_difference=True)
        adapter = UMRKinematicImpulseAdapter(grid_size=32, config=config)
        kframes = adapter.extract_kinematic_frames(clip)

        # Interior frames should have smooth velocity
        for kf in kframes[1:-1]:
            assert kf.root_velocity[0] > 0.0

    def test_extract_impulses_produces_vector_field_impulses(self):
        """extract_impulses should produce VectorFieldImpulse objects."""
        clip = _make_dash_clip(n_frames=8)
        adapter = UMRKinematicImpulseAdapter(grid_size=32)
        impulse_seq = adapter.extract_impulses(clip)

        assert len(impulse_seq) == 8
        # Frame 0 may have no impulse (no previous position)
        # Frames 1+ should have root impulses
        for frame_imps in impulse_seq[1:]:
            assert len(frame_imps) >= 1
            for imp in frame_imps:
                assert isinstance(imp, VectorFieldImpulse)
                assert imp.radius > 0


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 5: Line-Segment Splatter Geometry
# ═══════════════════════════════════════════════════════════════════════════


class TestLineSegmentSplatterGeometry:
    """Verify distance field and splatting geometry correctness."""

    def test_point_distance_at_segment_midpoint_is_zero(self):
        """Distance at the midpoint of a segment should be approximately zero."""
        splatter = LineSegmentSplatter(64)
        dist = splatter.compute_segment_distance_field((0.3, 0.5), (0.7, 0.5))
        # Cell closest to y=0.5, x=0.5 (midpoint)
        mid_row = 31  # (31+0.5)/64 ≈ 0.492
        mid_col = 31
        assert dist[mid_row, mid_col] < 0.02

    def test_distance_increases_away_from_segment(self):
        """Distance should increase monotonically away from the segment."""
        splatter = LineSegmentSplatter(64)
        dist = splatter.compute_segment_distance_field((0.5, 0.2), (0.5, 0.8))
        # At x=0.5 (col 31), distance should be near zero
        # At x=0.1 (col 6), distance should be larger
        assert dist[31, 6] > dist[31, 31]

    def test_degenerate_segment_is_point(self):
        """A zero-length segment should produce a radial distance field."""
        splatter = LineSegmentSplatter(32)
        dist = splatter.compute_segment_distance_field((0.5, 0.5), (0.5, 0.5))
        # Should be a radial field centred at (0.5, 0.5)
        center_dist = dist[15, 15]  # (15+0.5)/32 ≈ 0.484
        corner_dist = dist[0, 0]
        assert corner_dist > center_dist

    def test_splat_impulse_returns_correct_shapes(self):
        """Splat output shapes must match grid size."""
        splatter = LineSegmentSplatter(48)
        imp = VectorFieldImpulse(
            start=(0.2, 0.3), end=(0.8, 0.7),
            velocity_vector=(1.0, -0.5),
            radius=0.1, density_amount=1.0,
        )
        fu, fv, fd = splatter.splat_impulse(imp)
        assert fu.shape == (48, 48)
        assert fv.shape == (48, 48)
        assert fd.shape == (48, 48)

    def test_splat_multiple_accumulates(self):
        """Multiple impulses should accumulate additively."""
        splatter = LineSegmentSplatter(32)
        imp1 = VectorFieldImpulse(
            start=(0.2, 0.5), end=(0.4, 0.5),
            velocity_vector=(1.0, 0.0),
            radius=0.1, density_amount=1.0,
        )
        imp2 = VectorFieldImpulse(
            start=(0.6, 0.5), end=(0.8, 0.5),
            velocity_vector=(1.0, 0.0),
            radius=0.1, density_amount=1.0,
        )
        fu_single1, _, _ = splatter.splat_impulse(imp1)
        fu_single2, _, _ = splatter.splat_impulse(imp2)
        fu_multi, _, _ = splatter.splat_multiple([imp1, imp2])

        np.testing.assert_allclose(fu_multi, fu_single1 + fu_single2, atol=1e-12)


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 6: Closed-Loop Momentum Controller
# ═══════════════════════════════════════════════════════════════════════════


class TestFluidMomentumController:
    """End-to-end tests for the UMR-to-fluid momentum pipeline."""

    def test_dash_simulation_produces_frames(self):
        """Dash UMR clip should produce valid rendered frames."""
        clip = _make_dash_clip(n_frames=8)
        controller = FluidMomentumController(grid_size=32)
        result = controller.simulate_with_umr(clip, n_frames=8)

        assert len(result.frames) == 8
        assert not result.has_nan()
        assert result.total_impulse_count() > 0

    def test_slash_simulation_produces_frames(self):
        """Slash UMR clip should produce valid rendered frames."""
        clip = _make_slash_clip(n_frames=12)
        controller = FluidMomentumController(grid_size=32)
        result = controller.simulate_with_umr(clip, n_frames=12)

        assert len(result.frames) == 12
        assert not result.has_nan()

    def test_injection_diagnostics_populated(self):
        """Injection diagnostics should be populated for each frame."""
        clip = _make_dash_clip(n_frames=6)
        controller = FluidMomentumController(grid_size=32)
        result = controller.simulate_with_umr(clip, n_frames=6)

        assert len(result.injection_diagnostics) == 6
        # At least some frames should have non-zero injected energy
        energies = [d["total_injected_energy"] for d in result.injection_diagnostics]
        assert any(e > 0 for e in energies)

    def test_inject_impulses_into_grid_directly(self):
        """Direct grid injection should modify the source fields."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=32))
        controller = FluidMomentumController(grid_size=32)

        impulses = [VectorFieldImpulse(
            start=(0.3, 0.5), end=(0.7, 0.5),
            velocity_vector=(0.5, 0.0),
            radius=0.1, density_amount=1.0,
        )]

        diag = controller.inject_impulses_into_grid(grid, impulses)
        assert diag["impulse_count"] == 1
        assert diag["total_injected_energy"] > 0

        # Source fields should be non-zero
        assert np.any(grid.u_prev != 0)
        assert np.any(grid.density_prev != 0)

    def test_auto_config_selection(self):
        """Controller should auto-select VFX config based on clip state."""
        dash_clip = _make_dash_clip(n_frames=4)
        slash_clip = _make_slash_clip(n_frames=4)

        controller = FluidMomentumController(grid_size=32)

        dash_result = controller.simulate_with_umr(dash_clip, n_frames=4)
        slash_result = controller.simulate_with_umr(slash_clip, n_frames=4)

        # Both should produce valid results
        assert len(dash_result.frames) == 4
        assert len(slash_result.frames) == 4
        assert not dash_result.has_nan()
        assert not slash_result.has_nan()

    def test_with_dynamic_obstacle_masks(self):
        """Momentum controller should work with dynamic obstacle masks."""
        clip = _make_dash_clip(n_frames=6)
        controller = FluidMomentumController(grid_size=32)

        # Create simple moving obstacle masks
        masks = []
        for i in range(6):
            mask = np.zeros((32, 32), dtype=bool)
            col_start = 10 + i
            mask[12:20, col_start:col_start+4] = True
            masks.append(mask)

        result = controller.simulate_with_umr(
            clip, n_frames=6, dynamic_obstacle_masks=masks
        )
        assert len(result.frames) == 6
        assert not result.has_nan()


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 7: Evolution Bridge Metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestEvolutionBridgeMetrics:
    """Verify three-layer evolution evaluation."""

    def test_evaluate_passing_simulation(self):
        """A normal simulation should pass the evolution gate."""
        clip = _make_dash_clip(n_frames=8)
        controller = FluidMomentumController(grid_size=32)
        result = controller.simulate_with_umr(clip, n_frames=8)

        metrics = evaluate_momentum_simulation(result)
        assert metrics.momentum_pass is True
        assert metrics.has_nan is False
        assert metrics.total_impulses > 0
        assert metrics.mean_injected_energy > 0

    def test_metrics_to_dict(self):
        """Metrics should serialize to dict."""
        metrics = MomentumVFXMetrics(
            total_frames=8,
            total_impulses=16,
            mean_injected_energy=0.5,
            max_injected_velocity=1.0,
            has_nan=False,
            field_continuity_score=0.8,
            momentum_pass=True,
        )
        d = metrics.to_dict()
        assert d["total_frames"] == 8
        assert d["momentum_pass"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 8: Backward Compatibility
# ═══════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Ensure existing FluidDrivenVFXSystem contract is preserved."""

    def test_legacy_driver_impulses_still_work(self):
        """The old driver_impulses parameter should still function."""
        cfg = FluidVFXConfig.dash_smoke(canvas_size=48)
        system = FluidDrivenVFXSystem(cfg)
        impulses = [
            FluidImpulse(
                center_x=0.3 + i * 0.05,
                center_y=0.7,
                velocity_x=20.0,
                velocity_y=-2.0,
                density=1.0,
                radius=0.12,
            )
            for i in range(6)
        ]
        frames = system.simulate_and_render(n_frames=6, driver_impulses=impulses)
        assert len(frames) == 6

    def test_vector_field_impulse_to_fluid_impulse_bridge(self):
        """VectorFieldImpulse should convert to legacy FluidImpulse."""
        vfi = VectorFieldImpulse(
            start=(0.2, 0.3),
            end=(0.8, 0.7),
            velocity_vector=(0.5, -0.3),
            radius=0.1,
            density_amount=1.5,
            label="test",
        )
        fi = vector_field_impulse_to_fluid_impulse(vfi, 32)
        assert abs(fi.center_x - 0.5) < 1e-6
        assert abs(fi.center_y - 0.5) < 1e-6
        assert abs(fi.velocity_x - 0.5 * 32) < 1e-6
        assert fi.label == "test"


# ═══════════════════════════════════════════════════════════════════════════
# Test Group 9: Stress Tests — Extreme Conditions
# ═══════════════════════════════════════════════════════════════════════════


class TestStressConditions:
    """Extreme condition tests to verify robustness."""

    def test_empty_clip_no_crash(self):
        """An empty UMR clip should not crash the adapter."""
        clip = UnifiedMotionClip(
            clip_id="empty", state="idle", fps=12, frames=[]
        )
        adapter = UMRKinematicImpulseAdapter(grid_size=32)
        impulses = adapter.extract_impulses(clip)
        assert impulses == []

    def test_single_frame_clip(self):
        """A single-frame clip should produce one frame of impulses."""
        clip = _make_dash_clip(n_frames=1)
        adapter = UMRKinematicImpulseAdapter(grid_size=32)
        impulses = adapter.extract_impulses(clip)
        assert len(impulses) == 1

    def test_large_grid_no_nan(self):
        """64x64 grid with extreme injection should remain NaN-free."""
        clip = _make_high_speed_clip(n_frames=8, speed=2000.0)
        controller = FluidMomentumController(grid_size=64)
        result = controller.simulate_with_umr(clip, n_frames=8)
        assert not result.has_nan()

    def test_many_simultaneous_impulses(self):
        """Multiple simultaneous impulses should not cause overflow."""
        grid = FluidGrid2D(FluidGridConfig(grid_size=32))
        controller = FluidMomentumController(grid_size=32)

        impulses = [
            VectorFieldImpulse(
                start=(0.1 + i * 0.08, 0.3),
                end=(0.15 + i * 0.08, 0.7),
                velocity_vector=(0.3, -0.2),
                radius=0.05,
                density_amount=0.5,
            )
            for i in range(10)
        ]

        diag = controller.inject_impulses_into_grid(grid, impulses)
        assert diag["impulse_count"] == 10
        assert np.isfinite(grid.u_prev).all()
        assert np.isfinite(grid.v_prev).all()
        assert np.isfinite(grid.density_prev).all()
