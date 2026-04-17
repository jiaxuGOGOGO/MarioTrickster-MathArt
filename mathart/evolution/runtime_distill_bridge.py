"""SESSION-050 — three-layer evolution bridge for the runtime distillation bus.

This bridge mirrors the repository's existing "evaluate → distill → persist" pattern,
but targets Gap A2 specifically:

Layer 1 validates that runtime distillation is actually connected to executable
code paths.
Layer 2 turns those findings into durable knowledge rules.
Layer 3 persists trend data so future sessions can keep improving the bus.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from mathart.distill.runtime_bus import RuntimeDistillationBus, load_runtime_distillation_bus


@dataclass
class RuntimeDistillMetrics:
    cycle_id: int
    module_count: int = 0
    constraint_count: int = 0
    program_count: int = 0
    backend: str = "python"
    sample_count: int = 0
    expected_matches: int = 0
    mean_contact_score: float = 0.0
    throughput_per_s: float = 0.0
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "module_count": self.module_count,
            "constraint_count": self.constraint_count,
            "program_count": self.program_count,
            "backend": self.backend,
            "sample_count": self.sample_count,
            "expected_matches": self.expected_matches,
            "mean_contact_score": self.mean_contact_score,
            "throughput_per_s": self.throughput_per_s,
            "accepted": self.accepted,
        }


@dataclass
class RuntimeDistillState:
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_throughput_per_s: float = 0.0
    best_constraint_count: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_throughput_per_s": self.best_throughput_per_s,
            "best_constraint_count": self.best_constraint_count,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeDistillState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            best_throughput_per_s=float(data.get("best_throughput_per_s", 0.0)),
            best_constraint_count=int(data.get("best_constraint_count", 0)),
            history=list(data.get("history", [])),
        )


class RuntimeDistillBridge:
    """Evaluate, distill, and persist the runtime distillation bus state."""

    STATE_FILE = ".runtime_distill_state.json"
    KNOWLEDGE_FILE = "runtime_distill_bus.md"

    def __init__(self, project_root: Optional[str | Path] = None, *, verbose: bool = False) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.verbose = verbose
        self.state_path = self.root / self.STATE_FILE
        self.knowledge_path = self.root / "knowledge" / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _load_state(self) -> RuntimeDistillState:
        if not self.state_path.exists():
            return RuntimeDistillState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return RuntimeDistillState()
        return RuntimeDistillState.from_dict(data)

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def evaluate_runtime_bus(
        self,
        bus: Optional[RuntimeDistillationBus] = None,
    ) -> RuntimeDistillMetrics:
        bus = bus or load_runtime_distillation_bus(self.root)
        if bus.compiled_spaces:
            summary = {
                "module_count": len(bus.compiled_spaces),
                "constraint_count": int(sum(space.dimensions for space in bus.compiled_spaces.values())),
                "backend": bus._pick_backend(),
            }
        else:
            summary = bus.summary()
        cycle_id = self.state.total_cycles + 1
        program = bus.build_foot_contact_program()

        samples = np.array(
            [
                [0.00, 0.00],
                [0.03, 0.04],
                [0.12, 0.02],
                [0.02, 0.40],
                [0.08, -0.30],
                [0.01, 0.01],
            ],
            dtype=np.float64,
        )
        expected = [True, True, False, False, False, True]
        matches = 0
        score_total = 0.0
        for row, exp in zip(samples, expected):
            evaluation = program.evaluate_array(row)
            score_total += evaluation.score
            matches += int(evaluation.accepted == exp)

        bench = program.benchmark(sample_count=3000)
        metrics = RuntimeDistillMetrics(
            cycle_id=cycle_id,
            module_count=int(summary.get("module_count", 0)),
            constraint_count=int(summary.get("constraint_count", 0)),
            program_count=len(bus.runtime_programs),
            backend=str(summary.get("backend", "python")),
            sample_count=len(samples),
            expected_matches=matches,
            mean_contact_score=score_total / max(len(samples), 1),
            throughput_per_s=float(bench.get("throughput_per_s", 0.0)),
        )
        metrics.accepted = (
            metrics.module_count > 0
            and metrics.constraint_count > 0
            and metrics.expected_matches == metrics.sample_count
            and metrics.throughput_per_s > 0.0
        )
        return metrics

    def distill_rules(self, metrics: RuntimeDistillMetrics) -> list[dict[str, str]]:
        rules = [
            {
                "id": f"RUNTIME-BUS-{metrics.cycle_id:03d}-A",
                "rule": "Repository knowledge must be lowered into dense runtime arrays before entering frame-critical loops; do not interpret nested dictionaries inside the 60fps contact path.",
                "parameter": "distill_bus.execution_model",
                "constraint": "knowledge -> ParameterSpace -> dense arrays -> compiled closure",
            },
            {
                "id": f"RUNTIME-BUS-{metrics.cycle_id:03d}-B",
                "rule": "Foot contact detection should be compiled as a two-clause gate over foot height and vertical velocity, enabling direct machine-code execution and bitmask diagnostics.",
                "parameter": "physics.contact.runtime_kernel",
                "constraint": "contact = (foot_height <= threshold) and (abs(foot_vertical_velocity) <= threshold)",
            },
        ]
        if metrics.accepted:
            rules.append(
                {
                    "id": f"RUNTIME-BUS-{metrics.cycle_id:03d}-PASS",
                    "rule": f"Cycle {metrics.cycle_id} validated runtime bus execution with {metrics.constraint_count} compiled constraints and throughput {metrics.throughput_per_s:.1f}/s.",
                    "parameter": "runtime_distill.acceptance",
                    "constraint": "compiled_runtime_bus = enabled",
                }
            )
        else:
            rules.append(
                {
                    "id": f"RUNTIME-BUS-{metrics.cycle_id:03d}-WARN",
                    "rule": f"Cycle {metrics.cycle_id} did not meet all acceptance gates; re-check knowledge coverage, compiled module count, or runtime correctness before widening rollout.",
                    "parameter": "runtime_distill.acceptance",
                    "constraint": "compiled_runtime_bus = partial",
                }
            )
        return rules

    def write_knowledge_file(self, metrics: RuntimeDistillMetrics, rules: list[dict[str, str]]) -> Path:
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Runtime Distill Bus Rules",
            "",
            "This file captures the repository's durable rules for the Gap A2 runtime distillation bus.",
            "",
            f"## Cycle {metrics.cycle_id}",
            "",
            f"- Backend: `{metrics.backend}`",
            f"- Compiled module count: `{metrics.module_count}`",
            f"- Constraint count: `{metrics.constraint_count}`",
            f"- Contact-rule benchmark throughput: `{metrics.throughput_per_s:.1f}` eval/s",
            f"- Acceptance: `{metrics.accepted}`",
            "",
            "## Distilled Rules",
            "",
        ]
        for rule in rules:
            lines.extend(
                [
                    f"### {rule['id']}",
                    "",
                    f"- Rule: {rule['rule']}",
                    f"- Parameter: `{rule['parameter']}`",
                    f"- Constraint: `{rule['constraint']}`",
                    "",
                ]
            )
        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")
        return self.knowledge_path

    def apply_layer3(self, metrics: RuntimeDistillMetrics) -> float:
        self.state.total_cycles += 1
        if metrics.accepted:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1
        self.state.best_throughput_per_s = max(self.state.best_throughput_per_s, metrics.throughput_per_s)
        self.state.best_constraint_count = max(self.state.best_constraint_count, metrics.constraint_count)
        self.state.history.append(metrics.to_dict())
        self._save_state()
        throughput_bonus = min(0.10, metrics.throughput_per_s / 100000.0)
        coverage_bonus = min(0.10, metrics.constraint_count / 1000.0)
        return throughput_bonus + coverage_bonus

    def run_cycle(self) -> dict[str, Any]:
        metrics = self.evaluate_runtime_bus()
        rules = self.distill_rules(metrics)
        knowledge_path = self.write_knowledge_file(metrics, rules)
        fitness_bonus = self.apply_layer3(metrics)
        return {
            "metrics": metrics.to_dict(),
            "knowledge_path": str(knowledge_path),
            "fitness_bonus": fitness_bonus,
            "accepted": metrics.accepted,
        }


__all__ = [
    "RuntimeDistillMetrics",
    "RuntimeDistillState",
    "RuntimeDistillBridge",
]
