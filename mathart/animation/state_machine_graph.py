"""Runtime state-machine graph model and coverage utilities for Gap D1.

This module gives the repository an explicit, inspectable state-graph boundary for
runtime animation transitions. It does **not** replace ``MotionMatchingRuntime`` as
an execution engine. Instead, it provides:

1. A directed graph model derived from the runtime's available clips.
2. Deterministic edge and edge-pair coverage accounting.
3. A small harness that executes graph walks through ``MotionMatchingRuntime``.

The design follows the SESSION-051 research direction:
- Hypothesis generates long state-transition programs.
- NetworkX owns the graph model and coverage baseline.
- Runtime execution still happens through the real motion system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Any, Iterable, Optional, Sequence

import networkx as nx

from .runtime_motion_query import MotionMatchingRuntime


_CYCLIC_STATE_HINTS = {
    "idle",
    "walk",
    "run",
    "sneak",
    "crouch",
    "strafe",
    "turn",
}
_TRANSIENT_STATE_HINTS = {
    "jump",
    "fall",
    "hit",
    "attack",
    "land",
    "recover",
    "fall_recover",
    "dash",
}
_PREFERRED_START_STATES = ("idle", "walk", "run")


@dataclass(frozen=True)
class RuntimeTransitionEdge:
    """A directed state transition in the explicit runtime graph."""

    source: str
    target: str
    source_kind: str
    target_kind: str
    rationale: str = ""

    def to_tuple(self) -> tuple[str, str]:
        return (self.source, self.target)

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "source_kind": self.source_kind,
            "target_kind": self.target_kind,
            "rationale": self.rationale,
        }


@dataclass
class GraphCoverageSnapshot:
    """Coverage snapshot over state-machine edges and edge pairs."""

    total_edges: int
    covered_edges: int
    total_edge_pairs: int
    covered_edge_pairs: int
    missing_edges: list[tuple[str, str]] = field(default_factory=list)
    missing_edge_pairs: list[tuple[tuple[str, str], tuple[str, str]]] = field(default_factory=list)
    invalid_edges: list[tuple[str, str]] = field(default_factory=list)

    @property
    def edge_coverage(self) -> float:
        return self.covered_edges / self.total_edges if self.total_edges else 1.0

    @property
    def edge_pair_coverage(self) -> float:
        return self.covered_edge_pairs / self.total_edge_pairs if self.total_edge_pairs else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_edges": self.total_edges,
            "covered_edges": self.covered_edges,
            "edge_coverage": self.edge_coverage,
            "total_edge_pairs": self.total_edge_pairs,
            "covered_edge_pairs": self.covered_edge_pairs,
            "edge_pair_coverage": self.edge_pair_coverage,
            "missing_edges": [list(edge) for edge in self.missing_edges],
            "missing_edge_pairs": [
                [list(first), list(second)] for first, second in self.missing_edge_pairs
            ],
            "invalid_edges": [list(edge) for edge in self.invalid_edges],
        }


@dataclass
class RuntimeGraphExecutionResult:
    """Result of executing a graph walk through the real runtime."""

    executed_edges: list[tuple[str, str]]
    coverage: GraphCoverageSnapshot
    final_state: str
    failures: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.failures and self.coverage.edge_coverage >= 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed_edges": [list(edge) for edge in self.executed_edges],
            "coverage": self.coverage.to_dict(),
            "final_state": self.final_state,
            "failures": list(self.failures),
            "accepted": self.accepted,
        }


class RuntimeStateGraph:
    """Explicit directed state graph for the runtime motion system."""

    def __init__(
        self,
        graph: nx.DiGraph,
        *,
        start_state: str,
        state_kinds: Optional[dict[str, str]] = None,
    ) -> None:
        self.graph = graph
        self.start_state = start_state
        self.state_kinds = state_kinds or {
            str(node): str(graph.nodes[node].get("kind", "unknown")) for node in graph.nodes
        }

    @staticmethod
    def classify_state(state: str) -> str:
        normalized = str(state).strip().lower()
        if normalized in _CYCLIC_STATE_HINTS:
            return "cyclic"
        if normalized in _TRANSIENT_STATE_HINTS or any(
            hint in normalized for hint in ("jump", "fall", "hit", "attack", "recover", "dash")
        ):
            return "transient"
        return "unknown"

    @classmethod
    def from_clip_names(cls, clip_names: Sequence[str]) -> "RuntimeStateGraph":
        states = sorted({str(name) for name in clip_names if str(name).strip()})
        if not states:
            raise ValueError("Cannot build RuntimeStateGraph without clip names")

        kinds = {state: cls.classify_state(state) for state in states}
        cyclic_states = [state for state in states if kinds[state] == "cyclic"]
        transient_states = [state for state in states if kinds[state] == "transient"]
        unknown_states = [state for state in states if kinds[state] == "unknown"]

        graph = nx.DiGraph()
        for state in states:
            graph.add_node(state, kind=kinds[state])

        for state in states:
            source_kind = kinds[state]
            successors: set[str] = {state}
            if source_kind == "cyclic":
                successors.update(cyclic_states)
                successors.update(transient_states)
                successors.update(unknown_states)
            elif source_kind == "transient":
                successors.update(transient_states)
                successors.update(cyclic_states)
                if not cyclic_states:
                    successors.update(unknown_states)
            else:
                successors.update(states)

            for target in sorted(successors):
                target_kind = kinds[target]
                if state == target:
                    rationale = "self_loop"
                elif source_kind == "cyclic" and target_kind == "cyclic":
                    rationale = "cyclic_to_cyclic"
                elif source_kind == "cyclic" and target_kind == "transient":
                    rationale = "cyclic_to_transient"
                elif source_kind == "transient" and target_kind == "cyclic":
                    rationale = "transient_to_cyclic"
                elif source_kind == "transient" and target_kind == "transient":
                    rationale = "transient_chain"
                else:
                    rationale = "runtime_available"
                graph.add_edge(state, target, rationale=rationale)

        start_state = next((state for state in _PREFERRED_START_STATES if state in states), states[0])
        return cls(graph, start_state=start_state, state_kinds=kinds)

    @classmethod
    def from_runtime(cls, runtime: MotionMatchingRuntime) -> "RuntimeStateGraph":
        if not getattr(runtime, "_initialized", False):
            runtime.initialize()
        return cls.from_clip_names(runtime.database.get_clip_names())

    def nodes(self) -> list[str]:
        return list(self.graph.nodes)

    def successors(self, state: str) -> list[str]:
        if state not in self.graph:
            return []
        return list(self.graph.successors(state))

    def edge_exists(self, source: str, target: str) -> bool:
        return self.graph.has_edge(source, target)

    def expected_edges(self) -> set[tuple[str, str]]:
        return {(str(source), str(target)) for source, target in self.graph.edges()}

    def expected_edge_pairs(self) -> set[tuple[tuple[str, str], tuple[str, str]]]:
        pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        for source, target in self.expected_edges():
            for next_target in self.successors(target):
                pairs.add(((source, target), (target, next_target)))
        return pairs

    def shortest_path(self, source: str, target: str) -> list[str]:
        if source == target:
            return [source]
        return list(nx.shortest_path(self.graph, source, target))

    def canonical_edge_walk(self, start_state: Optional[str] = None) -> list[tuple[str, str]]:
        """Return a deterministic walk that attempts to cover every edge once."""
        uncovered = set(self.expected_edges())
        current = start_state or self.start_state
        walk: list[tuple[str, str]] = []

        while uncovered:
            best_edge: Optional[tuple[str, str]] = None
            best_path: Optional[list[str]] = None
            best_cost: Optional[tuple[int, tuple[str, str]]] = None

            for edge in sorted(uncovered):
                path = self.shortest_path(current, edge[0])
                cost = (max(len(path) - 1, 0), edge)
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_edge = edge
                    best_path = path

            if best_edge is None or best_path is None:
                break

            for next_state in best_path[1:]:
                transitional_edge = (current, next_state)
                walk.append(transitional_edge)
                uncovered.discard(transitional_edge)
                current = next_state

            final_edge = (current, best_edge[1])
            walk.append(final_edge)
            uncovered.discard(final_edge)
            current = best_edge[1]

        return walk

    def random_walk(
        self,
        *,
        steps: int,
        seed: int = 0,
        start_state: Optional[str] = None,
    ) -> list[tuple[str, str]]:
        rng = random.Random(seed)
        current = start_state or self.start_state
        walk: list[tuple[str, str]] = []
        for _ in range(max(int(steps), 0)):
            successors = self.successors(current)
            if not successors:
                break
            target = rng.choice(sorted(successors))
            walk.append((current, target))
            current = target
        return walk

    def coverage_from_edges(self, executed_edges: Iterable[tuple[str, str]]) -> GraphCoverageSnapshot:
        expected_edges = self.expected_edges()
        expected_pairs = self.expected_edge_pairs()
        covered_edges: set[tuple[str, str]] = set()
        invalid_edges: list[tuple[str, str]] = []
        executed_list = [(str(source), str(target)) for source, target in executed_edges]

        previous: Optional[tuple[str, str]] = None
        covered_pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        for edge in executed_list:
            if edge in expected_edges:
                covered_edges.add(edge)
                if previous is not None:
                    pair = (previous, edge)
                    if pair in expected_pairs:
                        covered_pairs.add(pair)
            else:
                invalid_edges.append(edge)
            previous = edge

        missing_edges = sorted(expected_edges - covered_edges)
        missing_pairs = sorted(expected_pairs - covered_pairs)
        return GraphCoverageSnapshot(
            total_edges=len(expected_edges),
            covered_edges=len(covered_edges),
            total_edge_pairs=len(expected_pairs),
            covered_edge_pairs=len(covered_pairs),
            missing_edges=missing_edges,
            missing_edge_pairs=missing_pairs,
            invalid_edges=invalid_edges,
        )


class RuntimeStateMachineHarness:
    """Execute graph walks through the real MotionMatchingRuntime."""

    def __init__(
        self,
        runtime: Optional[MotionMatchingRuntime] = None,
        *,
        transition_strategy: str = "dead_blending",
        blend_time: float = 0.2,
        decay_halflife: float = 0.05,
        transition_cost_threshold: float = 5.0,
    ) -> None:
        self.runtime = runtime or MotionMatchingRuntime(
            transition_strategy=transition_strategy,
            blend_time=blend_time,
            decay_halflife=decay_halflife,
            transition_cost_threshold=transition_cost_threshold,
        )
        self.graph: Optional[RuntimeStateGraph] = None
        self._bootstrapped = False

    def initialize(self) -> str:
        if not getattr(self.runtime, "_initialized", False):
            self.runtime.initialize()
        self.graph = RuntimeStateGraph.from_runtime(self.runtime)
        initial_state = self.runtime.get_state().state
        if not initial_state:
            self.runtime.tick(self.graph.start_state)
            initial_state = self.runtime.get_state().state or self.graph.start_state
        self._bootstrapped = True
        return initial_state

    @property
    def current_state(self) -> str:
        state = self.runtime.get_state().state
        if state:
            return state
        if self.graph is not None:
            return self.graph.start_state
        return ""

    def _ensure_ready(self) -> None:
        if not self._bootstrapped or self.graph is None:
            self.initialize()

    def transition_to(self, target_state: str, *, dt: float = 1.0 / 24.0) -> Any:
        self._ensure_ready()
        assert self.graph is not None
        if not self.graph.edge_exists(self.current_state, target_state):
            raise ValueError(f"Illegal state transition {self.current_state!r} -> {target_state!r}")
        return self.runtime.force_transition(target_state, dt=dt)

    def tick(self, *, dt: float = 1.0 / 24.0) -> Any:
        self._ensure_ready()
        return self.runtime.tick(self.current_state, dt=dt)

    def covered_edges(self) -> list[tuple[str, str]]:
        log = self.runtime.get_transition_log()
        return [
            (str(entry.get("from_state", "")), str(entry.get("to_state", "")))
            for entry in log
            if str(entry.get("from_state", "")) and str(entry.get("from_state", "")) != "none"
        ]

    def execute_walk(
        self,
        edge_walk: Sequence[tuple[str, str]],
        *,
        tick_after_transition: bool = False,
        dt: float = 1.0 / 24.0,
    ) -> RuntimeGraphExecutionResult:
        self._ensure_ready()
        assert self.graph is not None
        executed_edges: list[tuple[str, str]] = []
        failures: list[str] = []

        for expected_source, target in edge_walk:
            current = self.current_state
            if current != expected_source:
                failures.append(
                    f"Expected source state {expected_source!r} but runtime is at {current!r}"
                )
                break
            frame = self.transition_to(target, dt=dt)
            if frame is None:
                failures.append(f"Runtime returned no frame for {expected_source!r} -> {target!r}")
                break
            if self.current_state != target:
                failures.append(
                    f"Runtime ended in {self.current_state!r} after requesting {target!r}"
                )
                break
            executed_edges.append((expected_source, target))
            if tick_after_transition:
                self.tick(dt=dt)

        coverage = self.graph.coverage_from_edges(executed_edges)
        return RuntimeGraphExecutionResult(
            executed_edges=executed_edges,
            coverage=coverage,
            final_state=self.current_state,
            failures=failures,
        )


__all__ = [
    "RuntimeTransitionEdge",
    "GraphCoverageSnapshot",
    "RuntimeGraphExecutionResult",
    "RuntimeStateGraph",
    "RuntimeStateMachineHarness",
]
