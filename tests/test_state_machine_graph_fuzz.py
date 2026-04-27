from __future__ import annotations

from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from mathart.animation.state_machine_graph import (
    RuntimeStateGraph,
    RuntimeStateMachineHarness,
)
from mathart.evolution.state_machine_coverage_bridge import StateMachineCoverageBridge


class RuntimeGraphMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.harness = RuntimeStateMachineHarness()
        initial_state = self.harness.initialize()
        self.graph = self.harness.graph
        assert self.graph is not None
        self.model_state = initial_state

    @initialize()
    def sync_initial_state(self) -> None:
        assert self.harness.current_state == self.model_state
        assert self.model_state in self.graph.nodes()

    @rule(data=st.data())
    def transition(self, data) -> None:
        successors = self.graph.successors(self.model_state)
        assert successors, f"No successors available for {self.model_state!r}"
        target = data.draw(st.sampled_from(sorted(successors)), label="target_state")
        frame = self.harness.transition_to(target)
        assert frame is not None
        assert frame.source_state == target
        assert self.harness.current_state == target
        self.model_state = target

    @rule()
    def advance_playback(self) -> None:
        frame = self.harness.tick()
        assert frame is not None
        assert self.harness.current_state == self.model_state
        assert frame.source_state == self.model_state

    @invariant()
    def runtime_state_is_known(self) -> None:
        assert self.harness.current_state in self.graph.nodes()

    @invariant()
    def transition_log_edges_are_legal(self) -> None:
        for source, target in self.harness.covered_edges():
            assert self.graph.edge_exists(source, target), (source, target)


TestRuntimeGraphMachine = RuntimeGraphMachine.TestCase
TestRuntimeGraphMachine.settings = settings(
    max_examples=40,
    stateful_step_count=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    phases=(Phase.generate, Phase.shrink),
)


def test_runtime_state_graph_derives_expected_core_states():
    harness = RuntimeStateMachineHarness()
    initial_state = harness.initialize()
    graph = harness.graph
    assert graph is not None

    nodes = set(graph.nodes())
    assert {"idle", "walk", "run", "jump"}.issubset(nodes)
    assert initial_state in nodes
    assert graph.edge_exists("run", "jump")
    assert graph.edge_exists("jump", "run")
    assert graph.edge_exists("idle", "idle")


def test_canonical_edge_walk_reaches_full_edge_coverage():
    harness = RuntimeStateMachineHarness()
    initial_state = harness.initialize()
    graph = harness.graph
    assert graph is not None

    walk = graph.canonical_edge_walk(start_state=initial_state)
    result = harness.execute_walk(walk)

    assert not result.failures
    assert result.coverage.edge_coverage == 1.0
    assert result.coverage.covered_edges == result.coverage.total_edges
    assert result.accepted


def test_state_machine_coverage_bridge_persists_cycle(tmp_path: Path):
    bridge = StateMachineCoverageBridge(project_root=tmp_path)
    result = bridge.run_cycle(random_walk_steps=18, seed=51)

    assert result["accepted"] is True
    assert result["metrics"]["edge_coverage"] == 1.0
    assert result["metrics"]["edges"] >= 4
    assert (tmp_path / "workspace" / "evolution_states" / "state_machine_coverage_state.json").exists()
    assert (tmp_path / "knowledge" / "state_machine_graph_fuzzing.md").exists()


def test_edge_pair_coverage_accumulates_from_walks():
    graph = RuntimeStateGraph.from_clip_names(["idle", "walk", "run", "jump"])
    walk = [
        ("idle", "walk"),
        ("walk", "run"),
        ("run", "jump"),
        ("jump", "run"),
    ]
    coverage = graph.coverage_from_edges(walk)

    assert coverage.total_edge_pairs > 0
    assert coverage.covered_edge_pairs >= 3
    assert coverage.invalid_edges == []
