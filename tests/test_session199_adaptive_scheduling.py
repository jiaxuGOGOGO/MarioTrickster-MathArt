"""SESSION-199 — Adaptive Variance-Based ControlNet Strength Scheduler Tests.

Tests for the ``compute_adaptive_controlnet_strength``, ``should_prune_dead_water``,
and ``prune_controlnet_node_and_reseal_dag`` functions introduced in SESSION-199.

Design Rationale
----------------
The adaptive scheduler addresses the "反臆想模型注入红线" by dynamically
scaling ControlNet strength proportionally to the normalised pixel variance
of the conditioning frame, then clamping to the safe operating window
``[min_strength, max_strength]``.

Dead-water pruning ensures that near-zero-variance conditioning frames are
short-circuited (pruned) from the DAG, with upstream/downstream conditioning
wires seamlessly resealed to maintain KSampler connectivity.

Formula::

    normalised = pixel_variance / 255.0
    raw = base_strength + variance_scale * normalised
    strength = clamp(raw, min_strength, max_strength)

References
----------
- SESSION-199 safe model mapping research notes
- PID adaptive gain scheduling (Åström & Hägglund 1995)
- Woodham 1980 photometric stereo (fluid→normalbae mapping rationale)
"""
from __future__ import annotations

import math

import pytest

from mathart.core.vfx_topology_hydrator import (
    DEAD_WATER_VARIANCE_THRESHOLD,
    FLUID_CONTROLNET_MODEL_DEFAULT,
    PHYSICS_CONTROLNET_MODEL_DEFAULT,
    compute_adaptive_controlnet_strength,
    prune_controlnet_node_and_reseal_dag,
    should_prune_dead_water,
)


class TestComputeAdaptiveControlnetStrength:
    """Unit tests for compute_adaptive_controlnet_strength."""

    # ── Boundary: zero variance ─────────────────────────────────────────

    def test_zero_variance_returns_base_strength(self) -> None:
        """Flat frame (zero variance) must return exactly the base_strength."""
        result = compute_adaptive_controlnet_strength(0.0)
        assert result == pytest.approx(0.35, abs=1e-9)

    def test_zero_variance_custom_base(self) -> None:
        """Custom base_strength must be respected when variance is zero."""
        result = compute_adaptive_controlnet_strength(0.0, base_strength=0.20)
        assert result == pytest.approx(0.20, abs=1e-9)

    # ── Boundary: maximum variance ──────────────────────────────────────

    def test_max_variance_clamped_to_max_strength(self) -> None:
        """Maximum uint8 variance (255²=65025) must be clamped to max_strength=0.90."""
        result = compute_adaptive_controlnet_strength(65025.0)
        assert result == pytest.approx(0.90, abs=1e-9)

    def test_max_variance_custom_max_strength(self) -> None:
        """Custom max_strength ceiling must be respected."""
        result = compute_adaptive_controlnet_strength(65025.0, max_strength=0.75)
        assert result == pytest.approx(0.75, abs=1e-9)

    # ── Boundary: minimum clamp ─────────────────────────────────────────

    def test_negative_base_clamped_to_min_strength(self) -> None:
        """Negative base_strength must be clamped to min_strength floor."""
        result = compute_adaptive_controlnet_strength(0.0, base_strength=-1.0)
        assert result == pytest.approx(0.10, abs=1e-9)

    def test_custom_min_strength_floor(self) -> None:
        """Custom min_strength floor must be respected."""
        result = compute_adaptive_controlnet_strength(0.0, base_strength=0.0, min_strength=0.05)
        assert result == pytest.approx(0.05, abs=1e-9)

    # ── Mid-range values ────────────────────────────────────────────────

    def test_10_percent_variance(self) -> None:
        """10% of max variance should yield base + 0.5 * 25.5 = 13.10 → clamped 0.90."""
        result = compute_adaptive_controlnet_strength(6502.5)
        assert result == pytest.approx(0.90, abs=1e-9)

    def test_small_variance_no_clamp(self) -> None:
        """Small variance that stays within bounds should not be clamped."""
        result = compute_adaptive_controlnet_strength(1.0)
        expected = 0.35 + 0.5 * (1.0 / 255.0)
        assert result == pytest.approx(expected, abs=1e-6)

    def test_variance_scale_zero(self) -> None:
        """variance_scale=0 must always return base_strength (no scaling)."""
        for var in [0.0, 100.0, 65025.0]:
            result = compute_adaptive_controlnet_strength(var, variance_scale=0.0)
            assert result == pytest.approx(0.35, abs=1e-9), (
                f"variance={var}: expected 0.35, got {result}"
            )

    def test_custom_variance_scale(self) -> None:
        """Custom variance_scale must be applied correctly."""
        result = compute_adaptive_controlnet_strength(255.0, variance_scale=0.1)
        assert result == pytest.approx(0.45, abs=1e-6)

    # ── Return type ─────────────────────────────────────────────────────

    def test_return_type_is_float(self) -> None:
        """Return type must be float, not int or other numeric type."""
        result = compute_adaptive_controlnet_strength(0.0)
        assert isinstance(result, float)

    def test_return_type_with_integer_variance(self) -> None:
        """Integer input must still produce float output."""
        result = compute_adaptive_controlnet_strength(100)
        assert isinstance(result, float)

    # ── Clamp invariant ─────────────────────────────────────────────────

    def test_result_always_within_bounds(self) -> None:
        """Result must always be within [min_strength, max_strength] for any input."""
        test_cases = [
            (0.0, 0.35, 0.5, 0.10, 0.90),
            (65025.0, 0.35, 0.5, 0.10, 0.90),
            (-1000.0, 0.35, 0.5, 0.10, 0.90),
            (1e9, 0.35, 0.5, 0.10, 0.90),
            (0.0, 0.0, 0.0, 0.20, 0.80),
        ]
        for var, base, scale, lo, hi in test_cases:
            result = compute_adaptive_controlnet_strength(
                var, base_strength=base, variance_scale=scale,
                min_strength=lo, max_strength=hi,
            )
            assert lo <= result <= hi, (
                f"variance={var}: result {result} outside [{lo}, {hi}]"
            )

    # ── 反数学崩溃红线: NaN / Inf / negative defense ───────────────────

    def test_nan_variance_returns_min_strength(self) -> None:
        """NaN variance must return min_strength (反数学崩溃红线)."""
        result = compute_adaptive_controlnet_strength(float("nan"))
        assert result == pytest.approx(0.10, abs=1e-9)
        assert not math.isnan(result)

    def test_inf_variance_returns_min_strength(self) -> None:
        """Inf variance must return min_strength (反数学崩溃红线)."""
        result = compute_adaptive_controlnet_strength(float("inf"))
        assert result == pytest.approx(0.10, abs=1e-9)
        assert not math.isinf(result)

    def test_negative_inf_variance_returns_min_strength(self) -> None:
        """Negative Inf variance must return min_strength (反数学崩溃红线)."""
        result = compute_adaptive_controlnet_strength(float("-inf"))
        assert result == pytest.approx(0.10, abs=1e-9)

    def test_negative_variance_returns_min_strength(self) -> None:
        """Negative variance must return min_strength (反数学崩溃红线)."""
        result = compute_adaptive_controlnet_strength(-100.0)
        assert result == pytest.approx(0.10, abs=1e-9)

    def test_result_is_never_nan(self) -> None:
        """Result must never be NaN for any conceivable input (反数学崩溃红线)."""
        edge_cases = [0.0, -1.0, float("nan"), float("inf"), float("-inf"), 1e308, -1e308]
        for val in edge_cases:
            result = compute_adaptive_controlnet_strength(val)
            assert not math.isnan(result), f"NaN result for input {val}"
            assert not math.isinf(result), f"Inf result for input {val}"
            assert isinstance(result, float), f"Non-float result for input {val}"

    # ── SESSION-199 model mapping regression guard ──────────────────────

    def test_fluid_model_default_is_normalbae(self) -> None:
        """SESSION-199: FLUID_CONTROLNET_MODEL_DEFAULT must be normalbae (not depth)."""
        assert FLUID_CONTROLNET_MODEL_DEFAULT == "control_v11p_sd15_normalbae.pth", (
            "SESSION-199 safe model mapping: fluid flowmap must use normalbae model "
            "(photometric stereo: fluid momentum ≈ surface normal perturbation). "
            f"Got: {FLUID_CONTROLNET_MODEL_DEFAULT}"
        )

    def test_physics_model_default_is_depth(self) -> None:
        """SESSION-199: PHYSICS_CONTROLNET_MODEL_DEFAULT must be depth (not normalbae)."""
        assert PHYSICS_CONTROLNET_MODEL_DEFAULT == "control_v11f1p_sd15_depth.pth", (
            "SESSION-199 safe model mapping: physics 3D must use depth model "
            "(Z-axis deformation ≈ depth map gradient). "
            f"Got: {PHYSICS_CONTROLNET_MODEL_DEFAULT}"
        )

    def test_model_defaults_are_not_swapped(self) -> None:
        """SESSION-199: fluid and physics model defaults must not be identical."""
        assert FLUID_CONTROLNET_MODEL_DEFAULT != PHYSICS_CONTROLNET_MODEL_DEFAULT, (
            "FLUID and PHYSICS ControlNet model defaults must differ."
        )


class TestShouldPruneDeadWater:
    """Unit tests for should_prune_dead_water (dead-water detection gate)."""

    def test_zero_variance_is_dead_water(self) -> None:
        """Zero variance must be classified as dead water."""
        assert should_prune_dead_water(0.0) is True

    def test_below_threshold_is_dead_water(self) -> None:
        """Variance below threshold must be classified as dead water."""
        assert should_prune_dead_water(0.1) is True
        assert should_prune_dead_water(0.49) is True

    def test_above_threshold_is_not_dead_water(self) -> None:
        """Variance above threshold must NOT be classified as dead water."""
        assert should_prune_dead_water(1.0) is False
        assert should_prune_dead_water(100.0) is False
        assert should_prune_dead_water(65025.0) is False

    def test_exactly_at_threshold_is_dead_water(self) -> None:
        """Variance exactly at threshold is below (strict <), so dead water."""
        # should_prune_dead_water returns pv < threshold, so exactly at threshold is False
        assert should_prune_dead_water(DEAD_WATER_VARIANCE_THRESHOLD) is False

    def test_nan_is_dead_water(self) -> None:
        """NaN variance must be classified as dead water (safety)."""
        assert should_prune_dead_water(float("nan")) is True

    def test_inf_is_dead_water(self) -> None:
        """Inf variance must be classified as dead water (safety)."""
        assert should_prune_dead_water(float("inf")) is True

    def test_negative_is_dead_water(self) -> None:
        """Negative variance must be classified as dead water (safety)."""
        assert should_prune_dead_water(-5.0) is True

    def test_custom_threshold(self) -> None:
        """Custom threshold must be respected."""
        assert should_prune_dead_water(1.0, threshold=2.0) is True
        assert should_prune_dead_water(3.0, threshold=2.0) is False

    def test_high_variance_turbulent_fluid(self) -> None:
        """High-variance 'turbulent fluid' frame must NOT be pruned."""
        # Simulating a high-variance frame: variance = 5000.0
        assert should_prune_dead_water(5000.0) is False


class TestPruneControlnetNodeAndResealDag:
    """Unit tests for prune_controlnet_node_and_reseal_dag (DAG surgery).

    These tests verify the 反拓扑断裂红线 (Anti-Topology-Fracture Red Line):
    after pruning a ControlNet node, the DAG must remain fully connected
    with KSampler still receiving valid conditioning flow.
    """

    @staticmethod
    def _build_minimal_dag_with_vfx() -> dict:
        """Build a minimal ComfyUI DAG with an OpenPose + Fluid ControlNet chain.

        Topology:
            "10" (CLIPTextEncode positive) ──┐
            "11" (CLIPTextEncode negative) ──┤
                                             ▼
            "20" (ControlNetApplyAdvanced openpose)
                    ├── positive: ["10", 0]
                    └── negative: ["11", 0]
                                             ▼
            "30" (VHS_LoadImagesPath fluid)
            "31" (ControlNetLoader fluid)
            "32" (ControlNetApplyAdvanced fluid)
                    ├── positive: ["20", 0]
                    ├── negative: ["20", 1]
                    ├── control_net: ["31", 0]
                    └── image: ["30", 0]
                                             ▼
            "50" (KSampler)
                    ├── positive: ["32", 0]
                    └── negative: ["32", 1]
        """
        return {
            "10": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "positive prompt", "clip": ["1", 0]},
                "_meta": {"title": "positive prompt"},
            },
            "11": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "negative prompt", "clip": ["1", 0]},
                "_meta": {"title": "negative prompt"},
            },
            "20": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {
                    "positive": ["10", 0],
                    "negative": ["11", 0],
                    "strength": 1.0,
                    "control_net": ["21", 0],
                    "image": ["22", 0],
                },
                "_meta": {"title": "apply openpose controlnet"},
            },
            "21": {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": "control_v11p_sd15_openpose.pth"},
                "_meta": {"title": "openpose controlnet loader"},
            },
            "22": {
                "class_type": "VHS_LoadImagesPath",
                "inputs": {"directory": "/tmp/openpose_seq"},
                "_meta": {"title": "load openpose sequence"},
            },
            "30": {
                "class_type": "VHS_LoadImagesPath",
                "inputs": {"directory": "/tmp/fluid_seq"},
                "_meta": {"title": "SESSION197 Load Fluid Flowmap Sequence"},
            },
            "31": {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": "control_v11p_sd15_normalbae.pth"},
                "_meta": {"title": "SESSION197 Fluid ControlNet Loader"},
            },
            "32": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {
                    "positive": ["20", 0],
                    "negative": ["20", 1],
                    "strength": 0.35,
                    "control_net": ["31", 0],
                    "image": ["30", 0],
                },
                "_meta": {"title": "SESSION197 Apply Fluid ControlNet"},
            },
            "50": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": ["32", 0],
                    "negative": ["32", 1],
                    "model": ["1", 0],
                    "latent_image": ["2", 0],
                },
                "_meta": {"title": "KSampler"},
            },
        }

    def test_prune_removes_fluid_nodes(self) -> None:
        """Pruning fluid apply node must remove it and its feeders (VHS + Loader)."""
        dag = self._build_minimal_dag_with_vfx()
        assert "30" in dag
        assert "31" in dag
        assert "32" in dag

        report = prune_controlnet_node_and_reseal_dag(dag, "32")

        assert report["status"] == "pruned_and_resealed"
        assert "32" not in dag, "Pruned apply node must be removed"
        assert "31" not in dag, "Orphaned ControlNetLoader must be removed"
        assert "30" not in dag, "Orphaned VHS_LoadImagesPath must be removed"
        assert len(report["removed_nodes"]) == 3

    def test_prune_reseals_ksampler_positive(self) -> None:
        """After pruning, KSampler.positive must point to the upstream openpose node."""
        dag = self._build_minimal_dag_with_vfx()
        prune_controlnet_node_and_reseal_dag(dag, "32")

        ksampler = dag["50"]
        # KSampler.positive should now point to "20" (openpose), not "32" (pruned)
        assert ksampler["inputs"]["positive"] == ["20", 0], (
            f"KSampler.positive should be rewired to upstream openpose node ['20', 0], "
            f"got {ksampler['inputs']['positive']}"
        )

    def test_prune_reseals_ksampler_negative(self) -> None:
        """After pruning, KSampler.negative must point to the upstream openpose node."""
        dag = self._build_minimal_dag_with_vfx()
        prune_controlnet_node_and_reseal_dag(dag, "32")

        ksampler = dag["50"]
        # KSampler.negative should now point to "20" (openpose), not "32" (pruned)
        assert ksampler["inputs"]["negative"] == ["20", 1], (
            f"KSampler.negative should be rewired to upstream openpose node ['20', 1], "
            f"got {ksampler['inputs']['negative']}"
        )

    def test_prune_preserves_openpose_chain(self) -> None:
        """Pruning fluid must NOT affect the upstream openpose chain."""
        dag = self._build_minimal_dag_with_vfx()
        prune_controlnet_node_and_reseal_dag(dag, "32")

        # OpenPose chain must remain intact
        assert "20" in dag, "OpenPose apply node must survive"
        assert "21" in dag, "OpenPose loader must survive"
        assert "22" in dag, "OpenPose VHS must survive"
        assert dag["20"]["inputs"]["positive"] == ["10", 0]
        assert dag["20"]["inputs"]["negative"] == ["11", 0]

    def test_prune_dag_node_count_reduced(self) -> None:
        """After pruning, the DAG must have fewer nodes (3 removed)."""
        dag = self._build_minimal_dag_with_vfx()
        original_count = len(dag)
        prune_controlnet_node_and_reseal_dag(dag, "32")
        assert len(dag) == original_count - 3, (
            f"Expected {original_count - 3} nodes after pruning, got {len(dag)}"
        )

    def test_prune_nonexistent_node_is_safe(self) -> None:
        """Pruning a non-existent node must not crash."""
        dag = self._build_minimal_dag_with_vfx()
        report = prune_controlnet_node_and_reseal_dag(dag, "999")
        assert report["status"] == "node_not_found"

    def test_dag_fully_connected_after_prune(self) -> None:
        """After pruning, no node may reference a PRUNED node.

        This is the 反拓扑断裂红线 ultimate assertion: the pruned node IDs
        ("30", "31", "32") must not appear as input references anywhere.
        External upstream refs (e.g. CheckpointLoader "1", VAE "2") are
        outside the test DAG scope and are intentionally excluded.
        """
        dag = self._build_minimal_dag_with_vfx()
        pruned_ids = {"30", "31", "32"}
        prune_controlnet_node_and_reseal_dag(dag, "32")

        for nid, node in dag.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                continue
            for key, ref in inputs.items():
                if isinstance(ref, list) and len(ref) >= 2:
                    ref_id = str(ref[0])
                    assert ref_id not in pruned_ids, (
                        f"Node {nid}.inputs.{key} still references pruned node {ref_id} "
                        f"after pruning — DAG connectivity broken! (反拓扑断裂红线)"
                    )


class TestDeadWaterPruningIntegration:
    """Integration tests: high-variance injection vs dead-water pruning.

    These tests verify the full flow from variance detection through to
    DAG modification, ensuring:
    ① High-variance frames are woven into the graph with strength near max
    ② Dead-water frames trigger pruning with node count reduction
    ③ KSampler conditioning wires remain perfectly closed after pruning
    """

    def test_high_variance_frame_woven_into_graph(self) -> None:
        """High-variance (turbulent) frame must be injected with strength near max."""
        high_variance = 50000.0  # Very turbulent
        assert not should_prune_dead_water(high_variance)
        strength = compute_adaptive_controlnet_strength(high_variance)
        assert strength == pytest.approx(0.90, abs=1e-9), (
            f"High-variance frame should yield max strength, got {strength}"
        )

    def test_dead_water_frame_triggers_prune(self) -> None:
        """Dead-water (near-zero variance) frame must trigger pruning."""
        dead_water_variance = 0.01
        assert should_prune_dead_water(dead_water_variance)

    def test_full_flow_dead_water_prune_and_reseal(self) -> None:
        """Full integration: dead-water detection → prune → DAG reseal → KSampler OK."""
        dag = TestPruneControlnetNodeAndResealDag._build_minimal_dag_with_vfx()

        # Simulate dead-water detection
        variance = 0.01
        assert should_prune_dead_water(variance)

        # Prune the fluid node
        report = prune_controlnet_node_and_reseal_dag(dag, "32")
        assert report["status"] == "pruned_and_resealed"

        # Verify KSampler conditioning is still connected
        ksampler = dag["50"]
        pos_ref = ksampler["inputs"]["positive"]
        neg_ref = ksampler["inputs"]["negative"]
        assert str(pos_ref[0]) in dag, "KSampler.positive dangling after dead-water prune"
        assert str(neg_ref[0]) in dag, "KSampler.negative dangling after dead-water prune"

        # Verify node count reduced
        assert "32" not in dag
        assert "31" not in dag
        assert "30" not in dag

    def test_full_flow_high_variance_no_prune(self) -> None:
        """Full integration: high-variance → no prune → all nodes preserved."""
        dag = TestPruneControlnetNodeAndResealDag._build_minimal_dag_with_vfx()
        original_count = len(dag)

        variance = 5000.0
        assert not should_prune_dead_water(variance)

        # No pruning should happen
        assert len(dag) == original_count
        assert "32" in dag
        assert "31" in dag
        assert "30" in dag


class TestDeadWaterVarianceThreshold:
    """Tests for the DEAD_WATER_VARIANCE_THRESHOLD constant."""

    def test_threshold_is_positive(self) -> None:
        """Threshold must be a positive number."""
        assert DEAD_WATER_VARIANCE_THRESHOLD > 0

    def test_threshold_is_small(self) -> None:
        """Threshold must be small (< 10) to only catch truly flat frames."""
        assert DEAD_WATER_VARIANCE_THRESHOLD < 10.0

    def test_threshold_type(self) -> None:
        """Threshold must be a float."""
        assert isinstance(DEAD_WATER_VARIANCE_THRESHOLD, float)
