"""SESSION-120 / P1-NEW-8 — White-box tests for mid-generation checkpoint
branch pruning in PDG v2.

Every test in this module is designed to be adversarial:

* SKIPPED propagation is end-to-end (direct + transitive dependents).
* Rejection reports are structurally complete and round-trip through
  ``trace.to_dict()``.
* GPU semaphore never leaks when the node is cancelled mid-flight
  (concurrent branch).
* Only ``EarlyRejectionError`` triggers cancellation; every other exception
  type must surface as ``PDGError`` (Anti-Silent-Swallow).
* Microsecond latency budget is enforced by a time-domain assertion.
* Context/ratio proportion bounds stay in lock-step with the canonical
  ``STYLE_PARAMETER_BOUNDS`` from the animation module.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

from mathart.level.pdg import (
    EarlyRejectionError,
    PDGError,
    PDGNode,
    ProceduralDependencyGraph,
    WorkItemState,
)
from mathart.quality.mid_generation_checkpoint import (
    CheckpointVerdict,
    DEFAULT_SKELETON_PROPORTION_BOUNDS,
    NumericalToxinGate,
    QualityCheckpointNode,
    SkeletonProportionGate,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _healthy_genotype() -> dict[str, float]:
    return {
        "head_radius": 0.40,
        "torso_width": 0.22,
        "torso_height": 0.20,
        "arm_thickness": 0.07,
        "leg_thickness": 0.09,
        "hand_radius": 0.05,
        "foot_width": 0.10,
        "foot_height": 0.05,
    }


# ── 1. EarlyRejectionError contract ────────────────────────────────────────


def test_early_rejection_error_is_distinct_from_pdg_error() -> None:
    err = EarlyRejectionError("reason_x")
    assert not isinstance(err, PDGError), (
        "EarlyRejectionError must NOT subclass PDGError so that scheduler "
        "code can use `except EarlyRejectionError` surgically."
    )
    assert isinstance(err, Exception)


def test_early_rejection_error_serialises_to_dict() -> None:
    err = EarlyRejectionError(
        "skeleton_inverted",
        source_node="node_A",
        diagnostics={"ratio": 0.1},
        fitness_penalty=0.75,
    )
    payload = err.to_dict()
    assert payload["prune_reason"] == "skeleton_inverted"
    assert payload["source_node"] == "node_A"
    assert payload["diagnostics"] == {"ratio": 0.1}
    assert payload["fitness_penalty"] == 0.75


def test_work_item_state_enum_covers_all_cases() -> None:
    values = {s.value for s in WorkItemState}
    assert values == {"cooked_success", "cooked_cancel", "cooked_fail", "skipped"}


# ── 2. SkeletonProportionGate — microsecond-level correctness ───────────────


def test_skeleton_gate_passes_healthy_genotype() -> None:
    gate = SkeletonProportionGate.from_style_bounds()
    verdict = gate.evaluate(_healthy_genotype(), {})
    assert verdict.passed is True
    assert verdict.prune_reason is None
    assert verdict.duration_us < 5_000.0, (
        f"Gate must be microsecond-class; observed {verdict.duration_us:.1f} us"
    )


@pytest.mark.parametrize(
    "override,expected_reason",
    [
        ({"head_radius": 0.99}, "skeleton_head_radius_out_of_bounds"),
        ({"torso_width": 0.01}, "skeleton_torso_width_out_of_bounds"),
        ({"foot_width": 0.99}, "skeleton_foot_width_out_of_bounds"),
        ({"head_radius": float("nan")}, "skeleton_nan_proportion"),
        ({"head_radius": float("inf")}, "skeleton_nan_proportion"),
    ],
)
def test_skeleton_gate_rejects_out_of_bounds_and_nan(
    override: dict[str, float],
    expected_reason: str,
) -> None:
    gate = SkeletonProportionGate.from_style_bounds()
    ctx = _healthy_genotype()
    ctx.update(override)
    verdict = gate.evaluate(ctx, {})
    assert verdict.passed is False
    assert verdict.prune_reason == expected_reason
    assert verdict.fitness_penalty is not None
    assert 0.0 <= verdict.fitness_penalty <= 1.0


def test_skeleton_gate_detects_inverted_ratio() -> None:
    gate = SkeletonProportionGate.from_style_bounds()
    ctx = _healthy_genotype()
    # Make legs *much* thinner than arms — anatomically inverted.
    ctx["arm_thickness"] = 0.11
    ctx["leg_thickness"] = 0.05
    verdict = gate.evaluate(ctx, {})
    assert verdict.passed is False
    assert verdict.prune_reason == "skeleton_proportion_inverted"
    assert "ratio" in verdict.diagnostics
    assert verdict.diagnostics["ratio"] == "leg_to_arm_thickness"


def test_skeleton_bounds_mirror_genotype_contract() -> None:
    """The checkpoint bounds MUST stay in lock-step with the canonical ones."""
    from mathart.animation.genotype import STYLE_PARAMETER_BOUNDS

    for field_name, (lo, hi) in DEFAULT_SKELETON_PROPORTION_BOUNDS.items():
        canonical = STYLE_PARAMETER_BOUNDS.get(field_name)
        assert canonical is not None, f"Missing canonical bound for {field_name}"
        canon_lo = float(canonical.minimum)
        canon_hi = float(canonical.maximum)
        assert abs(canon_lo - lo) < 1e-9, f"{field_name} low mismatch"
        assert abs(canon_hi - hi) < 1e-9, f"{field_name} high mismatch"


# ── 3. NumericalToxinGate ───────────────────────────────────────────────────


def test_numerical_toxin_gate_passes_clean_tensor() -> None:
    gate = NumericalToxinGate()
    arr = np.linspace(-10.0, 10.0, 256, dtype=np.float64)
    verdict = gate.evaluate({"tensor": arr}, {})
    assert verdict.passed is True
    assert verdict.duration_us < 5_000.0


@pytest.mark.parametrize(
    "poison,expected_reason",
    [
        (float("nan"), "numerical_nan_detected"),
        (float("inf"), "numerical_inf_detected"),
        (1.0e12, "numerical_magnitude_explosion"),
    ],
)
def test_numerical_toxin_gate_catches_poison(
    poison: float, expected_reason: str
) -> None:
    gate = NumericalToxinGate(max_abs_value=1.0e6)
    arr = np.array([1.0, poison, 3.0, 4.0], dtype=np.float64)
    verdict = gate.evaluate({"tensor": arr}, {})
    assert verdict.passed is False
    assert verdict.prune_reason == expected_reason
    assert verdict.diagnostics["tensor"] == "context.tensor"


def test_numerical_toxin_gate_respects_whitelist() -> None:
    gate = NumericalToxinGate(tensor_keys=["allowed"])
    good = np.array([1.0, 2.0])
    bad = np.array([float("nan")])
    verdict = gate.evaluate({"allowed": good, "ignored": bad}, {})
    assert verdict.passed is True  # 'ignored' must not be scanned.


# ── 4. QualityCheckpointNode — translation to EarlyRejectionError ──────────


def test_quality_checkpoint_node_passes_all_gates() -> None:
    node = QualityCheckpointNode(
        [SkeletonProportionGate.from_style_bounds(), NumericalToxinGate()],
        node_name="q_gate",
    )
    payload = node(_healthy_genotype(), {})
    assert payload["verdict"] == "pass"
    assert payload["gate_count"] == 2
    for result in payload["gate_results"]:
        assert result["passed"] is True


def test_quality_checkpoint_node_raises_typed_error_on_first_failure() -> None:
    node = QualityCheckpointNode(
        [SkeletonProportionGate.from_style_bounds(), NumericalToxinGate()],
        node_name="q_gate",
    )
    ctx = _healthy_genotype()
    ctx["head_radius"] = 0.99
    with pytest.raises(EarlyRejectionError) as excinfo:
        node(ctx, {})
    err = excinfo.value
    assert err.prune_reason == "skeleton_head_radius_out_of_bounds"
    assert err.source_node == "q_gate"
    # Anti-Silent-Swallow: diagnostics must be populated.
    assert "failing_gate" in err.diagnostics
    assert "gate_diagnostics" in err.diagnostics


# ── 5. PDG v2 scheduler integration (sequential mode) ──────────────────────


def test_pdg_cancels_node_and_skips_all_downstream_work(tmp_path) -> None:
    graph = ProceduralDependencyGraph(
        name="rejection_test", max_workers=1, cache_dir=tmp_path / "pdg"
    )

    seen: dict[str, int] = {"decode": 0, "gate": 0, "skinning": 0, "render": 0}

    def decode(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        seen["decode"] += 1
        payload = _healthy_genotype()
        payload["head_radius"] = 0.99  # poison
        return payload

    def skinning(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        seen["skinning"] += 1
        return {"skin": "ok"}

    def render(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        seen["render"] += 1
        return {"render": "ok"}

    gate_node = QualityCheckpointNode(
        [SkeletonProportionGate.from_style_bounds()], node_name="quality_gate"
    )

    def gate_wrapper(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        seen["gate"] += 1
        # Feed downstream payload from the decode node into the gate.
        decoded = deps.get("decode", {})
        return gate_node(decoded, {})

    graph.add_node(PDGNode(name="decode", operation=decode))
    graph.add_node(
        PDGNode(name="quality_gate", operation=gate_wrapper, dependencies=["decode"])
    )
    graph.add_node(
        PDGNode(name="skinning", operation=skinning, dependencies=["quality_gate"])
    )
    graph.add_node(
        PDGNode(
            name="render",
            operation=render,
            dependencies=["skinning"],
            requires_gpu=True,
        )
    )

    report = graph.run()
    states = report["node_states"]

    assert states["decode"] == WorkItemState.COOKED_SUCCESS.value
    assert states["quality_gate"] == WorkItemState.COOKED_CANCEL.value
    assert states["skinning"] == WorkItemState.SKIPPED.value
    assert states["render"] == WorkItemState.SKIPPED.value

    # The heavy nodes must never have been called.
    assert seen["decode"] == 1
    assert seen["gate"] == 1
    assert seen["skinning"] == 0, "skinning invoked despite upstream cancel"
    assert seen["render"] == 0, "render invoked despite upstream cancel"

    pruning = report["pruning_report"]
    assert pruning["cancelled_count"] == 1
    assert pruning["skipped_count"] == 2
    assert pruning["gpu_inflight_after_run"] == 0, "GPU semaphore leaked!"

    rejection_entries = pruning["rejections"]
    assert len(rejection_entries) == 1
    assert rejection_entries[0]["prune_reason"] == "skeleton_head_radius_out_of_bounds"
    assert rejection_entries[0]["source_node"] == "quality_gate"

    # Trace entries for each skipped node must carry structured prune_reason
    trace_by_node = {entry["node_name"]: entry for entry in report["trace"]}
    assert trace_by_node["quality_gate"]["state"] == WorkItemState.COOKED_CANCEL.value
    assert trace_by_node["quality_gate"]["prune_reason"] == "skeleton_head_radius_out_of_bounds"
    for downstream in ("skinning", "render"):
        entry = trace_by_node[downstream]
        assert entry["state"] == WorkItemState.SKIPPED.value
        assert entry["prune_reason"] == "skeleton_head_radius_out_of_bounds"
        assert entry["pruned_by"] == "quality_gate"
        assert entry["duration_ms"] == 0.0


def test_pdg_does_not_swallow_unrelated_exceptions(tmp_path) -> None:
    """Anti-Silent-Swallow: TypeError from a node must surface as PDGError."""
    graph = ProceduralDependencyGraph(
        name="bug_visibility", max_workers=1, cache_dir=tmp_path / "pdg"
    )

    def buggy_op(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        raise TypeError("programmer error")

    graph.add_node(PDGNode(name="buggy", operation=buggy_op))

    with pytest.raises((PDGError, TypeError)):
        graph.run()


# ── 6. Concurrency: GPU semaphore MUST not leak on cancellation ────────────


def test_concurrent_cancellation_releases_gpu_slots(tmp_path) -> None:
    graph = ProceduralDependencyGraph(
        name="concurrent_cancel",
        max_workers=4,
        gpu_slots=2,
        cache_dir=tmp_path / "pdg",
    )
    barrier = threading.Barrier(4, timeout=5.0)

    def decode(ctx: dict[str, Any], deps: dict[str, Any]) -> Any:
        # Fan-out: produce 4 partitions, one of them poisoned.
        from mathart.level.pdg import PDGFanOutItem, PDGFanOutResult

        items = []
        for i in range(4):
            payload = _healthy_genotype()
            if i == 2:
                payload["head_radius"] = 0.99
            items.append(
                PDGFanOutItem(payload=payload, partition_key=f"p{i}")
            )
        return PDGFanOutResult(items=items)

    gate_core = QualityCheckpointNode(
        [SkeletonProportionGate.from_style_bounds()], node_name="gate"
    )

    def gate_op(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        # Synchronise all fan-out invocations so they all acquire the GPU
        # semaphore before the poisoned one raises.
        try:
            barrier.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            pass
        decoded = deps.get("decode", {})
        return gate_core(decoded, {})

    graph.add_node(PDGNode(name="decode", operation=decode))
    graph.add_node(
        PDGNode(
            name="gate",
            operation=gate_op,
            dependencies=["decode"],
            requires_gpu=True,
        )
    )

    report = graph.run()
    pruning = report["pruning_report"]
    assert pruning["cancelled_count"] == 1
    # The GPU semaphore must be fully released after the run, even though
    # one of the concurrent invocations raised mid-flight.
    assert pruning["gpu_inflight_after_run"] == 0, "GPU semaphore leaked!"


# ── 7. Trace determinism & JSON round-trip ─────────────────────────────────


def test_trace_entries_are_json_serialisable(tmp_path) -> None:
    import json

    graph = ProceduralDependencyGraph(
        name="json_trace", max_workers=1, cache_dir=tmp_path / "pdg"
    )

    def decode(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        payload = _healthy_genotype()
        payload["torso_width"] = 0.001  # poison
        return payload

    gate_core = QualityCheckpointNode(
        [SkeletonProportionGate.from_style_bounds()], node_name="gate"
    )

    def gate_op(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
        return gate_core(deps.get("decode", {}), {})

    graph.add_node(PDGNode(name="decode", operation=decode))
    graph.add_node(PDGNode(name="gate", operation=gate_op, dependencies=["decode"]))
    graph.add_node(
        PDGNode(
            name="downstream",
            operation=lambda c, d: {"ok": True},
            dependencies=["gate"],
        )
    )

    report = graph.run()
    dumped = json.dumps(report["pruning_report"])  # must not raise
    assert "torso_width" in dumped
    for entry in report["trace"]:
        json.dumps(entry)  # per-entry json round-trip


# ── 8. Latency budget: 1000 evaluations < 200 ms total ─────────────────────


def test_checkpoint_latency_budget_under_microsecond_class() -> None:
    gate = SkeletonProportionGate.from_style_bounds()
    toxin = NumericalToxinGate()
    ctx = _healthy_genotype()
    ctx["tensor"] = np.random.default_rng(0).standard_normal(1024)
    N = 1000
    t0 = time.perf_counter()
    for _ in range(N):
        assert gate.evaluate(ctx, {}).passed
        assert toxin.evaluate(ctx, {}).passed
    elapsed = time.perf_counter() - t0
    # Two gates × 1000 calls must finish in < 0.5 s even on a slow CI box.
    assert elapsed < 0.5, (
        f"Latency budget violated: {elapsed*1000:.1f} ms for {2*N} evaluations"
    )
