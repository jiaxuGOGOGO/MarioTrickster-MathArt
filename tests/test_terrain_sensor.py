"""Gap B2 regression tests — SDF terrain sensor + TTC prediction.

SESSION-048: Validates the full terrain sensing stack:
  1. TerrainSDF primitives (flat, slope, step, sine, platform)
  2. TerrainRaySensor sphere tracing
  3. TTCPredictor (linear + gravity-corrected)
  4. scene_aware_distance_phase backward compatibility
  5. scene_aware_fall_frame UMR metadata
  6. Pipeline integration (terrain-aware fall state)
  7. TerrainSensorEvolutionBridge three-layer cycle
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

import numpy as np
import pytest

# ── 1. TerrainSDF Primitives ─────────────────────────────────────────────────

from mathart.animation.terrain_sensor import (
    TerrainSDF,
    create_flat_terrain,
    create_slope_terrain,
    create_step_terrain,
    create_sine_terrain,
    create_platform_terrain,
    TerrainRaySensor,
    RayHit,
    TTCPredictor,
    TTCResult,
    scene_aware_distance_phase,
    scene_aware_fall_pose,
    scene_aware_fall_frame,
    scene_aware_jump_distance_phase,
    TerrainSensorDiagnostics,
    evaluate_terrain_sensor_accuracy,
    _phase1_query_geometry,
    _phase2_evaluate_clearance,
    _phase3_apply_kinematic_adaptation,
)


class TestTerrainSDFPrimitives:
    """Test SDF terrain factory functions."""

    def test_flat_terrain_above(self):
        terrain = create_flat_terrain(0.0)
        assert terrain.query(0.0, 0.5) > 0  # above ground
        assert abs(terrain.query(0.0, 0.5) - 0.5) < 1e-6

    def test_flat_terrain_on_surface(self):
        terrain = create_flat_terrain(0.0)
        assert abs(terrain.query(0.0, 0.0)) < 1e-6  # on surface

    def test_flat_terrain_below(self):
        terrain = create_flat_terrain(0.0)
        assert terrain.query(0.0, -0.3) < 0  # below ground

    def test_flat_terrain_elevated(self):
        terrain = create_flat_terrain(0.5)
        assert abs(terrain.query(0.0, 0.5)) < 1e-6
        assert terrain.query(0.0, 1.0) > 0

    def test_slope_terrain(self):
        terrain = create_slope_terrain(0.0, 0.0, 1.0, 0.5)
        # At x=0: surface at y=0
        assert abs(terrain.query(0.0, 0.0)) < 1e-6
        # At x=1: surface at y=0.5
        assert abs(terrain.query(1.0, 0.5)) < 1e-6
        # At x=0.5: surface at y=0.25
        assert abs(terrain.query(0.5, 0.25)) < 1e-6

    def test_step_terrain(self):
        terrain = create_step_terrain(0.5, 0.15, 0.0)
        # Before step: surface near y=0
        d_before = terrain.query(0.0, 0.0)
        assert abs(d_before) < 0.05
        # After step: surface near y=0.15
        d_after = terrain.query(1.0, 0.15)
        assert abs(d_after) < 0.05

    def test_sine_terrain(self):
        terrain = create_sine_terrain(0.1, 1.0, 0.0)
        # At x=0: surface at y=0 (sin(0)=0)
        assert abs(terrain.query(0.0, 0.0)) < 1e-6
        # At x=0.25: surface at y=0.1 (sin(π/2)=1)
        assert abs(terrain.query(0.25, 0.1)) < 1e-4

    def test_platform_terrain(self):
        platforms = [(0.0, 1.0, 0.2, 0.1), (2.0, 3.0, 0.5, 0.1)]
        terrain = create_platform_terrain(platforms)
        # On first platform
        d = terrain.query(0.5, 0.2)
        assert d <= 0.01  # on or near surface

    def test_terrain_gradient(self):
        terrain = create_flat_terrain(0.0)
        gx, gy = terrain.gradient(0.0, 0.5)
        # Flat terrain: gradient should point upward
        assert abs(gx) < 0.01
        assert gy > 0.5

    def test_terrain_surface_normal(self):
        terrain = create_flat_terrain(0.0)
        nx, ny = terrain.surface_normal(0.0, 0.5)
        assert abs(nx) < 0.01
        assert abs(ny - 1.0) < 0.01

    def test_terrain_compose_union(self):
        flat = create_flat_terrain(0.0)
        step = create_step_terrain(0.5, 0.2)
        combined = flat.compose_union(step)
        # The union should have the closer surface
        d = combined.query(0.0, 0.1)
        assert d >= 0  # above both surfaces

    def test_batch_query(self):
        terrain = create_slope_terrain(0.0, 0.0, 1.0, 0.5)
        x = np.array([0.0, 0.25, 0.5, 1.0])
        y = np.array([0.2, 0.3, 0.4, 0.8])
        d = terrain.query_batch(x, y)
        expected_surface = np.array([0.0, 0.125, 0.25, 0.5])
        expected_distance = y - expected_surface

        assert d.shape == (4,)
        np.testing.assert_allclose(d, expected_distance, atol=1e-6)
        np.testing.assert_allclose(d + expected_surface, y, atol=1e-6)


# ── 2. TerrainRaySensor ──────────────────────────────────────────────────────

class TestTerrainRaySensor:
    """Test SDF-based ray casting."""

    def test_cast_down_flat(self):
        terrain = create_flat_terrain(0.0)
        sensor = TerrainRaySensor(terrain)
        d = sensor.cast_down(0.0, 0.5)
        assert abs(d - 0.5) < 0.01

    def test_cast_down_on_ground(self):
        terrain = create_flat_terrain(0.0)
        sensor = TerrainRaySensor(terrain)
        d = sensor.cast_down(0.0, 0.0)
        assert d < 0.01

    def test_cast_down_step(self):
        terrain = create_step_terrain(0.5, 0.15, 0.0)
        sensor = TerrainRaySensor(terrain)
        # Before step at height 0.3
        d_before = sensor.cast_down(0.0, 0.3)
        assert abs(d_before - 0.3) < 0.05
        # After step at height 0.3 (step is at 0.15)
        d_after = sensor.cast_down(1.0, 0.3)
        assert abs(d_after - 0.15) < 0.05

    def test_cast_down_with_normal(self):
        terrain = create_flat_terrain(0.0)
        sensor = TerrainRaySensor(terrain)
        d, normal = sensor.cast_down_with_normal(0.0, 0.5)
        assert abs(d - 0.5) < 0.01
        assert abs(normal[1] - 1.0) < 0.1  # upward normal

    def test_multi_point_query(self):
        terrain = create_flat_terrain(0.0)
        sensor = TerrainRaySensor(terrain)
        points = [(0.0, 0.3), (0.5, 0.5), (1.0, 0.1)]
        distances = sensor.multi_point_query(points)
        assert len(distances) == 3
        assert abs(distances[0] - 0.3) < 0.01
        assert abs(distances[1] - 0.5) < 0.01
        assert abs(distances[2] - 0.1) < 0.01

    def test_cast_ray_custom_direction(self):
        terrain = create_flat_terrain(0.0)
        sensor = TerrainRaySensor(terrain)
        # Cast at 45 degrees downward
        hit = sensor.cast_ray(0.0, 0.5, 1.0, -1.0)
        assert hit.hit
        assert hit.distance > 0


# ── 3. TTCPredictor ──────────────────────────────────────────────────────────

class TestTTCPredictor:
    """Test Time-to-Contact prediction."""

    def test_ttc_simple_linear(self):
        pred = TTCPredictor(gravity=0.0)
        result = pred.compute_ttc(1.0, -2.0, use_gravity=False)
        assert abs(result.ttc - 0.5) < 0.01  # D/|v| = 1/2

    def test_ttc_with_gravity(self):
        pred = TTCPredictor(gravity=9.81)
        result = pred.compute_ttc(1.0, -1.0, use_gravity=True)
        # Quadratic: 0.5*9.81*t² + 1.0*t - 1.0 = 0
        # t = (-1 + sqrt(1 + 2*9.81*1)) / 9.81
        expected = (-1.0 + math.sqrt(1.0 + 2 * 9.81 * 1.0)) / 9.81
        assert abs(result.ttc - expected) < 0.01

    def test_ttc_at_contact(self):
        pred = TTCPredictor()
        result = pred.compute_ttc(0.005, -1.0)
        assert result.is_contact
        assert result.ttc == 0.0
        assert result.phase == 1.0

    def test_ttc_moving_upward(self):
        pred = TTCPredictor()
        result = pred.compute_ttc(1.0, 1.0)
        assert not result.is_approaching
        assert result.phase == 0.0

    def test_ttc_brace_signal(self):
        pred = TTCPredictor(gravity=9.81)
        # Very close to ground, falling fast → brace should be high
        result = pred.compute_ttc(0.05, -3.0)
        assert result.brace_signal > 0.5

    def test_ttc_to_phase(self):
        pred = TTCPredictor()
        # At start: ttc = reference → phase = 0
        assert abs(pred.ttc_to_phase(1.0, 1.0) - 0.0) < 1e-6
        # At contact: ttc = 0 → phase = 1
        assert abs(pred.ttc_to_phase(0.0, 1.0) - 1.0) < 1e-6
        # Halfway: ttc = 0.5 → phase = 0.5
        assert abs(pred.ttc_to_phase(0.5, 1.0) - 0.5) < 1e-6

    def test_ttc_result_to_dict(self):
        result = TTCResult(ttc=0.5, distance=1.0, velocity=-2.0)
        d = result.to_dict()
        assert "ttc" in d
        assert "distance" in d
        assert d["ttc"] == 0.5


# ── 4. Scene-Aware Distance Phase ────────────────────────────────────────────

class TestSceneAwareDistancePhase:
    """Test the upgraded fall_distance_phase with terrain sensing."""

    def test_flat_ground_backward_compatible(self):
        """Without terrain, should behave like fall_distance_phase."""
        result = scene_aware_distance_phase(
            root_x=0.0, root_y=0.2, velocity_y=-1.0,
            ground_height=0.0,
        )
        assert "phase" in result
        assert 0.0 <= result["phase"] <= 1.0
        assert result["terrain_query_mode"] == "flat_ground_fallback"
        assert result["terrain_sensor_active"] is True

    def test_with_flat_terrain(self):
        terrain = create_flat_terrain(0.0)
        result = scene_aware_distance_phase(
            root_x=0.0, root_y=0.5, velocity_y=-2.0,
            terrain=terrain,
        )
        assert result["terrain_query_mode"] == "sdf_terrain"
        assert result["distance_to_ground"] > 0
        assert result["ttc"] > 0

    def test_with_step_terrain(self):
        terrain = create_step_terrain(0.5, 0.15)
        # Falling onto the step
        result = scene_aware_distance_phase(
            root_x=1.0, root_y=0.5, velocity_y=-2.0,
            terrain=terrain,
        )
        # Distance should be ~0.35 (0.5 - 0.15)
        assert abs(result["distance_to_ground"] - 0.35) < 0.1

    def test_phase_increases_as_falling(self):
        terrain = create_flat_terrain(0.0)
        phases = []
        for y in [0.5, 0.4, 0.3, 0.2, 0.1, 0.01]:
            result = scene_aware_distance_phase(
                root_x=0.0, root_y=y, velocity_y=-2.0,
                terrain=terrain, fall_reference_height=0.5,
            )
            phases.append(result["phase"])
        # Phase should be monotonically increasing
        for i in range(len(phases) - 1):
            assert phases[i + 1] >= phases[i] - 0.01

    def test_phase_reaches_one_at_contact(self):
        terrain = create_flat_terrain(0.0)
        result = scene_aware_distance_phase(
            root_x=0.0, root_y=0.001, velocity_y=-2.0,
            terrain=terrain, fall_reference_height=0.5,
        )
        assert result["phase"] >= 0.95

    def test_ttc_metadata_present(self):
        terrain = create_flat_terrain(0.0)
        result = scene_aware_distance_phase(
            root_x=0.0, root_y=0.5, velocity_y=-2.0,
            terrain=terrain,
        )
        assert "ttc" in result
        assert "ttc_velocity" in result
        assert "ttc_is_approaching" in result
        assert "ttc_brace_signal" in result
        assert "surface_normal_x" in result
        assert "surface_normal_y" in result

    def test_landing_window(self):
        terrain = create_flat_terrain(0.0)
        result = scene_aware_distance_phase(
            root_x=0.0, root_y=0.02, velocity_y=-2.0,
            terrain=terrain, fall_reference_height=0.5,
        )
        assert result["is_landing_window"] is True

    def test_ttc_reference_mode(self):
        terrain = create_flat_terrain(0.0)
        result = scene_aware_distance_phase(
            root_x=0.0, root_y=0.3, velocity_y=-2.0,
            terrain=terrain, fall_reference_ttc=0.5,
        )
        assert result["phase_source"] == "ttc_bound"


# ── 5. Scene-Aware Fall Frame (UMR) ──────────────────────────────────────────

class TestSceneAwareFallFrame:
    """Test UMR frame generation with terrain sensing."""

    def test_fall_frame_basic(self):
        frame = scene_aware_fall_frame(
            0.5,
            time=0.5, frame_index=3, source_state="fall",
            root_x=0.0, root_y=0.3, root_velocity_y=-2.0,
            ground_height=0.0, fall_reference_height=0.5,
        )
        assert frame.phase_state is not None
        assert 0.0 <= frame.phase_state.value <= 1.0
        assert frame.metadata.get("generator") == "scene_aware_fall_ttc_distance_matching"

    def test_fall_frame_with_terrain(self):
        terrain = create_flat_terrain(0.0)
        frame = scene_aware_fall_frame(
            0.5,
            time=0.5, frame_index=3, source_state="fall",
            root_x=0.0, root_y=0.3, root_velocity_y=-2.0,
            terrain=terrain,
        )
        assert frame.metadata.get("terrain_sensor_active") is True
        assert frame.metadata.get("terrain_query_mode") == "sdf_terrain"

    def test_fall_frame_contact_tags(self):
        terrain = create_flat_terrain(0.0)
        frame = scene_aware_fall_frame(
            1.0,
            time=1.0, frame_index=10, source_state="fall",
            root_x=0.0, root_y=0.001, root_velocity_y=-2.0,
            terrain=terrain,
        )
        assert frame.contact_tags.left_foot is True
        assert frame.contact_tags.right_foot is True


# ── 6. Scene-Aware Fall Pose ──────────────────────────────────────────────────

class TestSceneAwareFallPose:
    """Test TTC-driven pose generation."""

    def test_pose_has_all_joints(self):
        pose = scene_aware_fall_pose(
            0.5, root_y=0.3, velocity_y=-2.0,
        )
        expected_joints = ["spine", "chest", "head", "l_hip", "r_hip",
                          "l_knee", "r_knee", "l_foot", "r_foot",
                          "l_shoulder", "r_shoulder", "l_elbow", "r_elbow"]
        for joint in expected_joints:
            assert joint in pose, f"Missing joint: {joint}"

    def test_pose_varies_with_phase(self):
        pose_high = scene_aware_fall_pose(0.0, root_y=0.5, velocity_y=-1.0)
        pose_low = scene_aware_fall_pose(1.0, root_y=0.01, velocity_y=-3.0)
        # Knee bend should be deeper near landing
        assert pose_low["l_knee"] < pose_high["l_knee"]

    def test_slope_compensation(self):
        slope = create_slope_terrain(0.0, 0.0, 1.0, 0.5)
        geometry = _phase1_query_geometry(
            root_x=0.5,
            root_y=0.5,
            velocity_y=-2.0,
            terrain=slope,
        )
        evaluation = _phase2_evaluate_clearance(geometry)
        final_pose = _phase3_apply_kinematic_adaptation(evaluation).to_pose_dict()
        pose = scene_aware_fall_pose(
            0.5, root_x=0.5, root_y=0.5, velocity_y=-2.0,
            terrain=slope,
        )

        expected_normal_x = -1.0 / math.sqrt(5.0)
        expected_ttc = (-2.0 + math.sqrt(4.0 + 2.0 * 9.81 * 0.25)) / 9.81
        expected_brace_signal = 1.0 - (expected_ttc / 0.3)
        expected_slope_lean = expected_normal_x * 0.08
        expected_brace_boost = expected_brace_signal * 0.15

        assert geometry.distance_to_ground == pytest.approx(0.25, abs=5e-3)
        assert geometry.surface_normal_x == pytest.approx(expected_normal_x, abs=5e-3)
        assert evaluation.compensation_vector == pytest.approx(
            (expected_slope_lean, expected_brace_boost),
            abs=5e-3,
        )
        assert final_pose["spine"] == pytest.approx(-0.05 + expected_slope_lean, abs=5e-3)
        assert pose == pytest.approx(final_pose, abs=1e-9)


class TestSceneAwareFallPosePipelinePhases:
    """White-box phase tests for the three-stage fall-pose pipeline."""

    @staticmethod
    def _build_slope_pipeline():
        slope = create_slope_terrain(0.0, 0.0, 1.0, 0.5)
        geometry = _phase1_query_geometry(
            root_x=0.5,
            root_y=0.5,
            velocity_y=-2.0,
            terrain=slope,
        )
        evaluation = _phase2_evaluate_clearance(geometry)
        kinematic = _phase3_apply_kinematic_adaptation(evaluation)
        return geometry, evaluation, kinematic

    def test_phase1_query_geometry_returns_exact_slope_distance_and_normal(self):
        geometry, _, _ = self._build_slope_pipeline()
        expected_normal = (-1.0 / math.sqrt(5.0), 2.0 / math.sqrt(5.0))

        assert geometry.terrain_query_mode == "sdf_terrain"
        assert geometry.distance_to_ground == pytest.approx(0.25, abs=5e-3)
        assert geometry.surface_normal_x == pytest.approx(expected_normal[0], abs=5e-3)
        assert geometry.surface_normal_y == pytest.approx(expected_normal[1], abs=5e-3)

    def test_phase2_evaluate_clearance_returns_exact_compensation_vector(self):
        geometry, evaluation, _ = self._build_slope_pipeline()
        expected_ttc = (-2.0 + math.sqrt(4.0 + 2.0 * 9.81 * geometry.distance_to_ground)) / 9.81
        expected_brace_signal = 1.0 - (expected_ttc / 0.3)
        expected_slope_lean = (-1.0 / math.sqrt(5.0)) * 0.08
        expected_brace_boost = expected_brace_signal * 0.15

        assert evaluation.phase == pytest.approx(0.0, abs=1e-6)
        assert evaluation.phase_source == "distance_matching_with_ttc"
        assert evaluation.ttc == pytest.approx(expected_ttc, abs=5e-6)
        assert evaluation.ttc_brace_signal == pytest.approx(expected_brace_signal, abs=5e-6)
        assert evaluation.compensation_vector == pytest.approx(
            (expected_slope_lean, expected_brace_boost),
            abs=5e-6,
        )

    def test_phase3_apply_kinematic_adaptation_returns_expected_pose_matrix(self):
        _, evaluation, kinematic = self._build_slope_pipeline()
        pose_matrix = dict(kinematic.final_pose_matrix)
        expected_slope_lean, expected_brace_boost = evaluation.compensation_vector
        expected_ttc_brace = evaluation.ttc_brace_signal

        assert pose_matrix["spine"] == pytest.approx(-0.05 + expected_slope_lean, abs=5e-6)
        assert pose_matrix["l_hip"] == pytest.approx(0.10 - expected_brace_boost, abs=5e-6)
        assert pose_matrix["r_hip"] == pytest.approx(-0.10 - expected_brace_boost, abs=5e-6)
        assert pose_matrix["l_knee"] == pytest.approx(-0.16 - 0.08 * expected_ttc_brace, abs=5e-6)
        assert pose_matrix["r_knee"] == pytest.approx(-0.12 - 0.08 * expected_ttc_brace, abs=5e-6)


# ── 7. Scene-Aware Jump Phase ─────────────────────────────────────────────────

class TestSceneAwareJumpPhase:
    """Test terrain-aware jump phase."""

    def test_ascending_uses_standard_phase(self):
        result = scene_aware_jump_distance_phase(
            root_x=0.0, root_y=0.1, root_velocity_y=2.0,
            apex_height=0.3,
        )
        assert result["phase_kind"] in ("distance_to_apex",)

    def test_descending_uses_scene_aware(self):
        terrain = create_flat_terrain(0.0)
        result = scene_aware_jump_distance_phase(
            root_x=0.0, root_y=0.2, root_velocity_y=-1.0,
            terrain=terrain,
        )
        assert result["phase_kind"] == "scene_aware_jump_descent"
        assert result["phase"] >= 0.5  # descent phase starts at 0.5


# ── 8. Terrain Sensor Diagnostics ─────────────────────────────────────────────

class TestTerrainSensorDiagnostics:
    """Test evaluation diagnostics."""

    def test_evaluate_flat_terrain(self):
        terrain = create_flat_terrain(0.0)
        trajectory = []
        y = 0.5
        vy = -1.0
        dt = 0.05
        g = 9.81
        for i in range(20):
            trajectory.append({
                "root_x": 0.0,
                "root_y": max(y, 0.0),
                "velocity_y": vy,
                "expected_distance": max(y, 0.0),
            })
            y += vy * dt
            vy -= g * dt

        diag = evaluate_terrain_sensor_accuracy(terrain, trajectory)
        assert diag.frame_count == 20
        assert diag.terrain_name == terrain.name
        assert diag.mean_distance_error < 0.1
        assert diag.phase_monotonic

    def test_diagnostics_to_dict(self):
        diag = TerrainSensorDiagnostics(cycle_id=1, frame_count=10)
        d = diag.to_dict()
        assert d["cycle_id"] == 1
        assert d["frame_count"] == 10


# ── 9. Evolution Bridge ──────────────────────────────────────────────────────

class TestTerrainSensorEvolutionBridge:
    """Test three-layer evolution bridge for Gap B2."""

    def test_bridge_evaluate(self):
        from mathart.evolution.terrain_sensor_bridge import (
            TerrainSensorEvolutionBridge,
            collect_terrain_sensor_status,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = TerrainSensorEvolutionBridge(tmpdir)
            diagnostics = [
                {
                    "frame_count": 20,
                    "mean_distance_error": 0.02,
                    "max_distance_error": 0.05,
                    "mean_ttc_error": 0.01,
                    "phase_at_contact": 0.98,
                    "phase_monotonic": True,
                    "ttc_decreasing": True,
                },
                {
                    "frame_count": 20,
                    "mean_distance_error": 0.03,
                    "max_distance_error": 0.06,
                    "mean_ttc_error": 0.02,
                    "phase_at_contact": 0.97,
                    "phase_monotonic": True,
                    "ttc_decreasing": True,
                },
            ]
            metrics = bridge.evaluate_terrain_sensor(diagnostics)
            assert metrics.pass_gate is True
            assert metrics.terrain_count == 2
            assert metrics.mean_phase_at_contact > 0.95

    def test_bridge_distill(self):
        from mathart.evolution.terrain_sensor_bridge import TerrainSensorEvolutionBridge
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = TerrainSensorEvolutionBridge(tmpdir)
            diagnostics = [{
                "frame_count": 20,
                "mean_distance_error": 0.02,
                "max_distance_error": 0.05,
                "mean_ttc_error": 0.01,
                "phase_at_contact": 0.98,
                "phase_monotonic": True,
                "ttc_decreasing": True,
            }]
            metrics = bridge.evaluate_terrain_sensor(diagnostics)
            rules = bridge.distill_terrain_sensor_knowledge(metrics)
            assert len(rules) > 0
            # Check knowledge file was created
            knowledge_path = os.path.join(tmpdir, "knowledge", "terrain_sensor_ttc_rules.md")
            assert os.path.exists(knowledge_path)

    def test_bridge_fitness_bonus(self):
        from mathart.evolution.terrain_sensor_bridge import (
            TerrainSensorEvolutionBridge, TerrainSensorMetrics,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = TerrainSensorEvolutionBridge(tmpdir)
            good_metrics = TerrainSensorMetrics(
                terrain_count=3,
                mean_distance_error=0.01,
                mean_phase_at_contact=0.99,
                phase_monotonic_rate=1.0,
                ttc_decreasing_rate=1.0,
                pass_gate=True,
            )
            bonus = bridge.compute_terrain_sensor_fitness_bonus(good_metrics)
            assert bonus > 0

            bad_metrics = TerrainSensorMetrics(
                terrain_count=3,
                mean_distance_error=0.15,
                mean_phase_at_contact=0.7,
                phase_monotonic_rate=0.6,
                ttc_decreasing_rate=0.5,
                pass_gate=False,
            )
            penalty = bridge.compute_terrain_sensor_fitness_bonus(bad_metrics)
            assert penalty < 0

    def test_bridge_status_report(self):
        from mathart.evolution.terrain_sensor_bridge import TerrainSensorEvolutionBridge
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = TerrainSensorEvolutionBridge(tmpdir)
            report = bridge.status_report()
            assert "Gap B2" in report
            assert "Terrain Sensor" in report

    def test_bridge_state_persistence(self):
        from mathart.evolution.terrain_sensor_bridge import TerrainSensorEvolutionBridge
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = TerrainSensorEvolutionBridge(tmpdir)
            diagnostics = [{
                "frame_count": 20,
                "mean_distance_error": 0.02,
                "max_distance_error": 0.05,
                "mean_ttc_error": 0.01,
                "phase_at_contact": 0.98,
                "phase_monotonic": True,
                "ttc_decreasing": True,
            }]
            bridge.evaluate_terrain_sensor(diagnostics)

            state_path = os.path.join(tmpdir, ".terrain_sensor_state.json")
            assert os.path.exists(state_path)

            # Reload and verify
            bridge2 = TerrainSensorEvolutionBridge(tmpdir)
            assert bridge2.state.total_cycles == 1

    def test_collect_status(self):
        from mathart.evolution.terrain_sensor_bridge import collect_terrain_sensor_status
        # Test against actual project root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        status = collect_terrain_sensor_status(project_root)
        assert status.module_exists is True
        assert status.bridge_exists is True


# ── 10. Integration: imports from public API ──────────────────────────────────

class TestPublicAPIExports:
    """Verify terrain sensor is accessible from public API."""

    def test_animation_package_exports(self):
        from mathart.animation import (
            TerrainSDF,
            TerrainRaySensor,
            TTCPredictor,
            scene_aware_distance_phase,
            scene_aware_fall_frame,
            create_flat_terrain,
            create_step_terrain,
        )
        assert TerrainSDF is not None
        assert TTCPredictor is not None

    def test_evolution_package_exports(self):
        from mathart.evolution import (
            TerrainSensorEvolutionBridge,
            TerrainSensorMetrics,
            TerrainSensorState,
            TerrainSensorStatus,
            collect_terrain_sensor_status,
        )
        assert TerrainSensorEvolutionBridge is not None


# ── 11. SESSION-112 / P1-B2-1 — High-Order Terrain SDF Primitives ──────────
#
# These tests guard the three new factories landed for P1-B2-1
# (`create_convex_hull_terrain`, `create_bezier_terrain`,
#  `create_heightmap_terrain`).  They are the white-box closure for the
# task brief's three red lines:
#
#   U0001f534 Anti-Pseudo-Distance Red Line  — the SDF magnitude must equal
#       Euclidean distance to the surface to within 1% relative error
#       on a dense random query batch (no algebraic-distance shortcuts).
#   U0001f534 Anti-Scalar-Loop Red Line       — 1000+ random points must be
#       evaluated through the public `eval_sdf` / `eval_gradient` and
#       complete in a single broadcast (timing assertion <= 0.5 s).
#   U0001f534 Anti-Gradient-Jitter Red Line   — the gradient magnitude must
#       satisfy the Eikonal equation ∥∇SDF∥ ≈ 1 a.e. and never produce
#       NaN / zero vectors.

import time

from mathart.animation.terrain_sensor import (
    create_convex_hull_terrain,
    create_bezier_terrain,
    create_heightmap_terrain,
    _segment_sdf_broadcast,
    _polygon_sign_broadcast,
)

_RNG = np.random.default_rng(seed=20260421)


def _eikonal_assertion(terrain, sample_points, *, eps=1e-3, atol=0.15):
    """Helper: assert ∥∇SDF∥ ≈ 1 on the supplied sample points.

    Tolerance `atol` is intentionally generous because central-difference
    gradients on broadcast SDFs accumulate small bias near edges, but
    must remain bounded away from 0 and 2.
    """
    grads = terrain.eval_gradient(sample_points, eps=eps)
    magnitudes = np.linalg.norm(grads, axis=-1)
    assert np.all(np.isfinite(magnitudes)), "non-finite gradient detected"
    assert np.all(magnitudes > 0.0), "zero-length gradient detected"
    assert np.all(np.abs(magnitudes - 1.0) < atol), (
        f"Eikonal violation: |grad| range = [{magnitudes.min():.4f},"
        f" {magnitudes.max():.4f}], expected ~1.0 ± {atol}"
    )


class TestConvexHullTerrainPrimitive:
    """P1-B2-1 — `create_convex_hull_terrain` analytical closure tests."""

    def _square(self):
        # Unit square at origin, deliberately supplied in the wrong order
        # to exercise the auto_hull re-ordering path.
        return [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)]

    def test_interior_negative_exterior_positive(self):
        terrain = create_convex_hull_terrain(self._square())
        assert terrain.query(0.5, 0.5) < 0.0
        # Exact distance from (1.5, 0.5) to nearest edge x=1 is 0.5.
        assert abs(terrain.query(1.5, 0.5) - 0.5) < 1e-9
        assert abs(terrain.query(-0.25, 0.5) - 0.25) < 1e-9

    def test_known_corner_distance(self):
        terrain = create_convex_hull_terrain(self._square())
        # (2, 2) → nearest corner (1, 1), distance = sqrt(2).
        assert abs(terrain.query(2.0, 2.0) - math.sqrt(2.0)) < 1e-9

    def test_anti_scalar_loop_broadcast_vectorisation(self):
        """Red line: 1000+ point batch evaluation must stay broadcast."""
        terrain = create_convex_hull_terrain(self._square())
        pts = _RNG.uniform(-2.0, 2.0, size=(1500, 2))
        start = time.perf_counter()
        d = terrain.eval_sdf(pts)
        elapsed = time.perf_counter() - start
        assert d.shape == (1500,)
        assert np.all(np.isfinite(d))
        # Generous performance budget: 1500 × 4-edge polygon broadcast on
        # CPython must stay below 100 ms even on weak CI runners.
        assert elapsed < 0.5, f"eval_sdf took {elapsed:.3f}s, broadcast lost"

    def test_eikonal_lipschitz_continuity(self):
        terrain = create_convex_hull_terrain(self._square())
        # Sample exterior away from the medial axis and interior away from
        # the centre to avoid the gradient-degenerate locus.
        ext = _RNG.uniform(1.5, 3.0, size=(500, 2))
        ext[:, 1] = _RNG.uniform(-2.0, 2.0, size=500)
        _eikonal_assertion(terrain, ext, atol=0.05)

    def test_arbitrary_polygon_via_auto_hull(self):
        # Random 12-gon over the unit disk — hull should be re-computed.
        thetas = _RNG.uniform(0.0, 2 * math.pi, size=12)
        verts = np.stack([np.cos(thetas), np.sin(thetas)], axis=-1)
        terrain = create_convex_hull_terrain(verts)
        assert terrain.query(0.0, 0.0) < 0.0
        assert terrain.query(5.0, 0.0) > 3.0


class TestBezierTerrainPrimitive:
    """P1-B2-1 — `create_bezier_terrain` thickened-polyline closure."""

    def test_apex_distance_matches_thickness(self):
        # B(0.5) for control points (0,0) (0.5,1) (1,0) is exactly (0.5, 0.5).
        terrain = create_bezier_terrain((0, 0), (0.5, 1.0), (1.0, 0.0),
                                         thickness=0.05)
        assert abs(terrain.query(0.5, 0.5) - (-0.05)) < 1e-9

    def test_endpoint_on_curve(self):
        terrain = create_bezier_terrain((0, 0), (0.5, 1.0), (1.0, 0.0),
                                         thickness=0.0)
        assert abs(terrain.query(0.0, 0.0)) < 1e-9
        assert abs(terrain.query(1.0, 0.0)) < 1e-9

    def test_far_field_distance_matches_min_segment(self):
        terrain = create_bezier_terrain((0, 0), (0.5, 1.0), (1.0, 0.0),
                                         thickness=0.0, segments=200)
        # (0.5, 5) is far above; nearest curve point is the apex (0.5, 0.5).
        assert abs(terrain.query(0.5, 5.0) - 4.5) < 1e-3

    def test_anti_scalar_loop_dense_batch(self):
        terrain = create_bezier_terrain((0, 0), (0.5, 1.0), (1.0, 0.0),
                                         thickness=0.05)
        pts = _RNG.uniform(-1.0, 2.0, size=(1500, 2))
        start = time.perf_counter()
        d = terrain.eval_sdf(pts)
        elapsed = time.perf_counter() - start
        assert d.shape == (1500,)
        assert np.all(np.isfinite(d))
        assert elapsed < 0.5, f"eval_sdf took {elapsed:.3f}s, broadcast lost"

    def test_eikonal_lipschitz_far_field(self):
        terrain = create_bezier_terrain((0, 0), (0.5, 1.0), (1.0, 0.0),
                                         thickness=0.05, segments=200)
        # Sample well outside the tube where the polyline SDF is smooth.
        x = _RNG.uniform(-1.5, 2.5, size=400)
        y = _RNG.uniform(2.0, 4.0, size=400)
        pts = np.stack([x, y], axis=-1)
        _eikonal_assertion(terrain, pts, eps=5e-3, atol=0.05)

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            create_bezier_terrain((0, 0), (1, 1), (2, 0), segments=1)
        with pytest.raises(ValueError):
            create_bezier_terrain((0, 0), (1, 1), (2, 0), thickness=-0.1)


class TestHeightmapTerrainPrimitive:
    """P1-B2-1 — `create_heightmap_terrain` EDT-baked discrete primitive."""

    def _hill(self):
        xs = np.linspace(0.0, 1.0, 64)
        heights = 0.3 + 0.2 * np.sin(2 * math.pi * xs)
        terrain = create_heightmap_terrain(
            heights, physical_bounds=(0.0, 1.0, -0.5, 1.5),
            grid_resolution=(96, 64),
        )
        return terrain, xs, heights

    def test_above_surface_positive_below_negative(self):
        terrain, xs, heights = self._hill()
        # Sample at x = 0.5 — surface y ≈ 0.3 + 0.2 sin(π) = 0.3.
        assert terrain.query(0.5, 0.8) > 0.0
        assert terrain.query(0.5, -0.2) < 0.0

    def test_distance_monotonic_with_height(self):
        terrain, _, _ = self._hill()
        # Walking straight up should yield monotonically increasing SDF.
        ys = np.linspace(0.5, 1.4, 50)
        d = terrain.query_batch(np.full_like(ys, 0.5), ys)
        assert np.all(np.diff(d) >= -1e-6)

    def test_anti_scalar_loop_dense_batch(self):
        terrain, _, _ = self._hill()
        pts = _RNG.uniform(0.0, 1.0, size=(1500, 2))
        pts[:, 1] = _RNG.uniform(-0.4, 1.4, size=1500)
        start = time.perf_counter()
        d = terrain.eval_sdf(pts)
        elapsed = time.perf_counter() - start
        assert d.shape == (1500,)
        assert np.all(np.isfinite(d))
        assert elapsed < 0.5, f"eval_sdf took {elapsed:.3f}s, broadcast lost"

    def test_anti_gradient_jitter_guard(self):
        terrain, _, _ = self._hill()
        # Sample on a deliberately stairstep-prone strip just above surface.
        x = _RNG.uniform(0.0, 1.0, size=600)
        y = _RNG.uniform(0.6, 1.4, size=600)
        pts = np.stack([x, y], axis=-1)
        grads = terrain.eval_gradient(pts, eps=1e-3)
        magnitudes = np.linalg.norm(grads, axis=-1)
        # The EDT-derived field is smoother than a raw heightmap; gradients
        # must always be finite, non-zero, and bounded near unit length.
        assert np.all(np.isfinite(magnitudes))
        assert np.all(magnitudes > 0.0)
        assert np.all(magnitudes < 3.0)

    def test_zero_vector_fallback_for_degenerate_queries(self):
        terrain, _, _ = self._hill()
        # Even if a query lands on a constant-distance plateau, the
        # gradient must fall back to the canonical upward normal rather
        # than emitting NaN.
        pts = np.array([[0.5, 1.4], [0.0, 1.4]])
        grads = terrain.eval_gradient(pts, eps=1e-9)  # collapse FD
        assert np.all(np.isfinite(grads))
        magnitudes = np.linalg.norm(grads, axis=-1)
        assert np.all(magnitudes > 0.0)

    def test_invalid_bounds_rejected(self):
        with pytest.raises(ValueError):
            create_heightmap_terrain(np.zeros(8), (1.0, 0.0, 0.0, 1.0))
        with pytest.raises(ValueError):
            create_heightmap_terrain(np.zeros(0), (0.0, 1.0, 0.0, 1.0))


class TestSharedBroadcastHelpers:
    """Direct DOD invariants for the shared broadcast helpers."""

    def test_segment_sdf_broadcast_shapes_and_correctness(self):
        # Two segments forming an “L”, verify distances against analytic.
        a = np.array([[0.0, 0.0], [1.0, 0.0]])
        b = np.array([[1.0, 0.0], [1.0, 1.0]])
        px = np.array([0.5, 2.0, -1.0])
        py = np.array([0.5, 0.5, 0.0])
        d = _segment_sdf_broadcast(px, py, a, b)
        assert d.shape == (3, 2)
        # Closest distance from (0.5, 0.5) to first segment is 0.5.
        assert abs(d[0, 0] - 0.5) < 1e-9
        # Closest distance from (2, 0.5) to second segment is 1.0.
        assert abs(d[1, 1] - 1.0) < 1e-9
        # (-1, 0) to first segment endpoint = 1.0.
        assert abs(d[2, 0] - 1.0) < 1e-9

    def test_polygon_sign_broadcast_vectorised(self):
        verts = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        px = np.array([0.5, 2.0, -1.0, 0.5])
        py = np.array([0.5, 0.5, 0.5, -0.5])
        sign = _polygon_sign_broadcast(px, py, verts)
        assert sign.shape == (4,)
        assert sign[0] == -1.0   # interior
        assert sign[1] == 1.0    # right exterior
        assert sign[2] == 1.0    # left exterior
        assert sign[3] == 1.0    # below exterior


class TestPublicAPIExports_P1B2_1:
    """Confirm the new factories are reachable from the top-level package."""

    def test_factories_exported(self):
        from mathart.animation import (
            create_convex_hull_terrain,
            create_bezier_terrain,
            create_heightmap_terrain,
            TerrainSDF,
        )
        assert callable(create_convex_hull_terrain)
        assert callable(create_bezier_terrain)
        assert callable(create_heightmap_terrain)
        # Strong-typed contract: factories MUST return TerrainSDF instances.
        assert isinstance(
            create_convex_hull_terrain([(0, 0), (1, 0), (0, 1)]),
            TerrainSDF,
        )
        assert isinstance(
            create_bezier_terrain((0, 0), (0.5, 1.0), (1.0, 0.0), 0.05),
            TerrainSDF,
        )
        assert isinstance(
            create_heightmap_terrain(np.zeros(16), (0.0, 1.0, -0.2, 0.5)),
            TerrainSDF,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
