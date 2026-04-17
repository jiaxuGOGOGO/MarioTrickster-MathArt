"""Three-layer evolution bridge for Gap D1 state-machine graph coverage.

Layer 1 runs deterministic graph walks plus seeded random walks over the real
``MotionMatchingRuntime`` and measures edge / edge-pair coverage.
Layer 2 distills durable rules into the knowledge base.
Layer 3 persists coverage history so later sessions can keep widening the graph
without losing past evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Optional

from mathart.animation.state_machine_graph import (
    RuntimeStateGraph,
    RuntimeStateMachineHarness,
)


@dataclass
class StateMachineCoverageMetrics:
    cycle_id: int
    states: int = 0
    edges: int = 0
    covered_edges: int = 0
    edge_coverage: float = 0.0
    edge_pairs: int = 0
    covered_edge_pairs: int = 0
    edge_pair_coverage: float = 0.0
    invalid_edges: int = 0
    random_walk_steps: int = 0
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "states": self.states,
            "edges": self.edges,
            "covered_edges": self.covered_edges,
            "edge_coverage": self.edge_coverage,
            "edge_pairs": self.edge_pairs,
            "covered_edge_pairs": self.covered_edge_pairs,
            "edge_pair_coverage": self.edge_pair_coverage,
            "invalid_edges": self.invalid_edges,
            "random_walk_steps": self.random_walk_steps,
            "accepted": self.accepted,
        }


@dataclass
class StateMachineCoverageState:
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_edge_coverage: float = 0.0
    best_edge_pair_coverage: float = 0.0
    largest_graph_edges: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_edge_coverage": self.best_edge_coverage,
            "best_edge_pair_coverage": self.best_edge_pair_coverage,
            "largest_graph_edges": self.largest_graph_edges,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateMachineCoverageState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            best_edge_coverage=float(data.get("best_edge_coverage", 0.0)),
            best_edge_pair_coverage=float(data.get("best_edge_pair_coverage", 0.0)),
            largest_graph_edges=int(data.get("largest_graph_edges", 0)),
            history=list(data.get("history", [])),
        )


class StateMachineCoverageBridge:
    """Evaluate, distill, and persist runtime graph-coverage status."""

    STATE_FILE = ".state_machine_coverage_state.json"
    KNOWLEDGE_FILE = "state_machine_graph_fuzzing.md"

    def __init__(self, project_root: Optional[str | Path] = None) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.state_path = self.root / self.STATE_FILE
        self.knowledge_path = self.root / "knowledge" / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _load_state(self) -> StateMachineCoverageState:
        if not self.state_path.exists():
            return StateMachineCoverageState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return StateMachineCoverageState()
        return StateMachineCoverageState.from_dict(data)

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def evaluate(
        self,
        *,
        random_walk_steps: int = 24,
        seed: int = 51,
    ) -> dict[str, Any]:
        harness = RuntimeStateMachineHarness()
        initial_state = harness.initialize()
        graph = harness.graph
        if graph is None:
            raise RuntimeError("Runtime state graph was not initialized")

        canonical_walk = graph.canonical_edge_walk(start_state=initial_state)
        deterministic_result = harness.execute_walk(canonical_walk, tick_after_transition=False)

        if deterministic_result.failures:
            coverage = deterministic_result.coverage
        else:
            random_walk = graph.random_walk(
                steps=random_walk_steps,
                seed=seed,
                start_state=deterministic_result.final_state,
            )
            random_result = harness.execute_walk(random_walk, tick_after_transition=False)
            merged_edges = deterministic_result.executed_edges + random_result.executed_edges
            coverage = graph.coverage_from_edges(merged_edges)

        metrics = StateMachineCoverageMetrics(
            cycle_id=self.state.total_cycles + 1,
            states=len(graph.nodes()),
            edges=coverage.total_edges,
            covered_edges=coverage.covered_edges,
            edge_coverage=coverage.edge_coverage,
            edge_pairs=coverage.total_edge_pairs,
            covered_edge_pairs=coverage.covered_edge_pairs,
            edge_pair_coverage=coverage.edge_pair_coverage,
            invalid_edges=len(coverage.invalid_edges),
            random_walk_steps=random_walk_steps,
        )
        metrics.accepted = (
            metrics.edges > 0
            and metrics.edge_coverage >= 1.0
            and metrics.invalid_edges == 0
        )
        return {
            "metrics": metrics,
            "graph": graph,
            "coverage": coverage,
            "initial_state": initial_state,
            "canonical_walk": canonical_walk,
        }

    def distill_rules(
        self,
        metrics: StateMachineCoverageMetrics,
        coverage: dict[str, Any] | Any,
    ) -> list[dict[str, str]]:
        missing_edges = getattr(coverage, "missing_edges", [])
        rules = [
            {
                "id": f"STATE-GRAPH-{metrics.cycle_id:03d}-A",
                "rule": "End-to-end animation state testing must operate on an explicit directed graph so the repository can distinguish expected edges, covered edges, and missing edges instead of relying on hand-written example paths only.",
                "parameter": "state_machine.coverage_model",
                "constraint": "runtime states -> directed graph -> edge coverage audit",
            },
            {
                "id": f"STATE-GRAPH-{metrics.cycle_id:03d}-B",
                "rule": "Property-based stateful tests should generate whole transition programs and shrink failures to minimal edge sequences, while the runtime graph remains the single source of truth for legal transitions.",
                "parameter": "state_machine.fuzzing_mode",
                "constraint": "Hypothesis stateful program generation + NetworkX coverage baseline",
            },
        ]
        if metrics.accepted:
            rules.append(
                {
                    "id": f"STATE-GRAPH-{metrics.cycle_id:03d}-PASS",
                    "rule": f"Cycle {metrics.cycle_id} reached full edge coverage ({metrics.covered_edges}/{metrics.edges}) with edge-pair coverage {metrics.edge_pair_coverage:.3f} over the runtime state graph.",
                    "parameter": "state_machine.coverage_status",
                    "constraint": "edge_coverage = 1.0",
                }
            )
        else:
            missing_preview = ", ".join(f"{a}->{b}" for a, b in missing_edges[:6]) or "none"
            rules.append(
                {
                    "id": f"STATE-GRAPH-{metrics.cycle_id:03d}-WARN",
                    "rule": f"Cycle {metrics.cycle_id} did not reach full edge coverage; prioritize missing edges {missing_preview} before claiming runtime state-machine closure.",
                    "parameter": "state_machine.coverage_status",
                    "constraint": "edge_coverage < 1.0",
                }
            )
        return rules

    def write_knowledge_file(
        self,
        metrics: StateMachineCoverageMetrics,
        graph: RuntimeStateGraph,
        coverage: Any,
        rules: list[dict[str, str]],
    ) -> Path:
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# State Machine Graph Fuzzing Rules",
            "",
            "This file captures the durable rules and audit summary for Gap D1 runtime graph coverage.",
            "",
            f"## Cycle {metrics.cycle_id}",
            "",
            f"- States: `{metrics.states}`",
            f"- Expected edges: `{metrics.edges}`",
            f"- Covered edges: `{metrics.covered_edges}`",
            f"- Edge coverage: `{metrics.edge_coverage:.3f}`",
            f"- Expected edge pairs: `{metrics.edge_pairs}`",
            f"- Covered edge pairs: `{metrics.covered_edge_pairs}`",
            f"- Edge-pair coverage: `{metrics.edge_pair_coverage:.3f}`",
            f"- Invalid edges: `{metrics.invalid_edges}`",
            f"- Acceptance: `{metrics.accepted}`",
            "",
            "## Runtime Graph Nodes",
            "",
        ]
        for state in graph.nodes():
            kind = graph.state_kinds.get(state, "unknown")
            successors = ", ".join(graph.successors(state))
            lines.extend([
                f"### {state}",
                "",
                f"- Kind: `{kind}`",
                f"- Successors: `{successors}`",
                "",
            ])
        lines.extend(["## Distilled Rules", ""])
        for rule in rules:
            lines.extend([
                f"### {rule['id']}",
                "",
                f"- Rule: {rule['rule']}",
                f"- Parameter: `{rule['parameter']}`",
                f"- Constraint: `{rule['constraint']}`",
                "",
            ])
        if getattr(coverage, "missing_edges", None):
            lines.extend(["## Missing Edges", ""])
            for source, target in coverage.missing_edges:
                lines.append(f"- `{source} -> {target}`")
            lines.append("")
        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")
        return self.knowledge_path

    def apply_layer3(self, metrics: StateMachineCoverageMetrics) -> float:
        self.state.total_cycles += 1
        if metrics.accepted:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1
        self.state.best_edge_coverage = max(self.state.best_edge_coverage, metrics.edge_coverage)
        self.state.best_edge_pair_coverage = max(
            self.state.best_edge_pair_coverage,
            metrics.edge_pair_coverage,
        )
        self.state.largest_graph_edges = max(self.state.largest_graph_edges, metrics.edges)
        self.state.history.append(metrics.to_dict())
        self._save_state()
        return min(0.12, 0.08 * metrics.edge_coverage + 0.04 * metrics.edge_pair_coverage)

    def run_cycle(self, *, random_walk_steps: int = 24, seed: int = 51) -> dict[str, Any]:
        result = self.evaluate(random_walk_steps=random_walk_steps, seed=seed)
        metrics: StateMachineCoverageMetrics = result["metrics"]
        graph: RuntimeStateGraph = result["graph"]
        coverage = result["coverage"]
        rules = self.distill_rules(metrics, coverage)
        knowledge_path = self.write_knowledge_file(metrics, graph, coverage, rules)
        fitness_bonus = self.apply_layer3(metrics)
        return {
            "metrics": metrics.to_dict(),
            "coverage": coverage.to_dict(),
            "knowledge_path": str(knowledge_path),
            "fitness_bonus": fitness_bonus,
            "accepted": metrics.accepted,
        }


__all__ = [
    "StateMachineCoverageMetrics",
    "StateMachineCoverageState",
    "StateMachineCoverageBridge",
]
