"""SESSION-053 — Three-layer evolution bridge for locomotion CNS rollout.

Layer 1 evaluates a repository-standard batch of hard locomotion transitions.
Layer 2 distills operational rules from those measurements.
Layer 3 persists trend data so later sessions can continue widening rollout.
"""
from __future__ import annotations
from .state_vault import resolve_state_path

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from mathart.animation.locomotion_cns import (
    default_cns_transition_requests,
    evaluate_transition_batch,
)
from mathart.distill.runtime_bus import RuntimeDistillationBus, load_runtime_distillation_bus


@dataclass
class LocomotionCNSMetrics:
    cycle_id: int
    case_count: int = 0
    accepted_ratio: float = 0.0
    mean_runtime_score: float = 0.0
    mean_sliding_error: float = 0.0
    worst_phase_jump: float = 0.0
    mean_contact_mismatch: float = 0.0
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "case_count": self.case_count,
            "accepted_ratio": self.accepted_ratio,
            "mean_runtime_score": self.mean_runtime_score,
            "mean_sliding_error": self.mean_sliding_error,
            "worst_phase_jump": self.worst_phase_jump,
            "mean_contact_mismatch": self.mean_contact_mismatch,
            "accepted": self.accepted,
        }


@dataclass
class LocomotionCNSState:
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_accepted_ratio: float = 0.0
    best_mean_sliding_error: float = 1.0
    best_runtime_score: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_accepted_ratio": self.best_accepted_ratio,
            "best_mean_sliding_error": self.best_mean_sliding_error,
            "best_runtime_score": self.best_runtime_score,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LocomotionCNSState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            best_accepted_ratio=float(data.get("best_accepted_ratio", 0.0)),
            best_mean_sliding_error=float(data.get("best_mean_sliding_error", 1.0)),
            best_runtime_score=float(data.get("best_runtime_score", 0.0)),
            history=list(data.get("history", [])),
        )


class LocomotionCNSBridge:
    STATE_FILE = "locomotion_cns_state.json"
    KNOWLEDGE_FILE = "locomotion_cns.md"

    def __init__(self, project_root: Optional[str | Path] = None, *, verbose: bool = False) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.verbose = verbose
        self.state_path = resolve_state_path(self.root, self.STATE_FILE)
        self.knowledge_path = self.root / "knowledge" / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _load_state(self) -> LocomotionCNSState:
        if not self.state_path.exists():
            return LocomotionCNSState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return LocomotionCNSState()
        return LocomotionCNSState.from_dict(data)

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def evaluate(self, bus: Optional[RuntimeDistillationBus] = None) -> LocomotionCNSMetrics:
        runtime_bus = bus or load_runtime_distillation_bus(self.root)
        batch = evaluate_transition_batch(default_cns_transition_requests(), runtime_bus=runtime_bus)
        metrics = LocomotionCNSMetrics(
            cycle_id=self.state.total_cycles + 1,
            case_count=len(batch.metrics),
            accepted_ratio=float(batch.accepted_ratio),
            mean_runtime_score=float(batch.mean_runtime_score),
            mean_sliding_error=float(batch.mean_sliding_error),
            worst_phase_jump=float(batch.worst_phase_jump),
            mean_contact_mismatch=float(batch.mean_contact_mismatch),
        )
        metrics.accepted = bool(
            metrics.case_count > 0
            and metrics.accepted_ratio >= 0.80
            and metrics.mean_runtime_score >= 0.75
            and metrics.mean_sliding_error <= 0.08
            and metrics.worst_phase_jump <= 0.10
            and metrics.mean_contact_mismatch <= 0.25
        )
        return metrics

    def distill_rules(self, metrics: LocomotionCNSMetrics) -> list[dict[str, str]]:
        rules = [
            {
                "id": f"CNS-{metrics.cycle_id:03d}-A",
                "rule": "Before switching locomotion states, align the target gait to the source support phase; phase correspondence is established first, interpolation second.",
                "parameter": "locomotion.phase_alignment",
                "constraint": "target_phase = phase_warp(source_phase, source_markers, target_markers)",
            },
            {
                "id": f"CNS-{metrics.cycle_id:03d}-B",
                "rule": "At transition time, render the target gait immediately and decay only the residual source offset; target contacts remain authoritative.",
                "parameter": "locomotion.transition_mode",
                "constraint": "pose = target_pose + inertialized_residual(source_minus_target)",
            },
            {
                "id": f"CNS-{metrics.cycle_id:03d}-C",
                "rule": "Locomotion quality gates must be compiled into dense feature evaluators so batch audits can score phase jump, sliding, contact mismatch and foot lock in one hot path.",
                "parameter": "locomotion.runtime_gate",
                "constraint": "features -> dense array -> compiled runtime mask",
            },
        ]
        outcome = "pass" if metrics.accepted else "warn"
        rules.append(
            {
                "id": f"CNS-{metrics.cycle_id:03d}-{outcome.upper()}",
                "rule": f"Cycle {metrics.cycle_id} produced accepted_ratio={metrics.accepted_ratio:.2f}, mean_sliding_error={metrics.mean_sliding_error:.4f}, mean_runtime_score={metrics.mean_runtime_score:.2f}.",
                "parameter": "locomotion.acceptance",
                "constraint": f"state = {outcome}",
            }
        )
        return rules

    def write_knowledge_file(self, metrics: LocomotionCNSMetrics, rules: list[dict[str, str]]) -> Path:
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Locomotion CNS Rules",
            "",
            "Durable rules for the repository's phase-aligned, inertialized locomotion transition stack.",
            "",
            f"## Cycle {metrics.cycle_id}",
            "",
            f"- Case count: `{metrics.case_count}`",
            f"- Accepted ratio: `{metrics.accepted_ratio:.2f}`",
            f"- Mean runtime score: `{metrics.mean_runtime_score:.2f}`",
            f"- Mean sliding error: `{metrics.mean_sliding_error:.4f}`",
            f"- Worst phase jump: `{metrics.worst_phase_jump:.4f}`",
            f"- Mean contact mismatch: `{metrics.mean_contact_mismatch:.4f}`",
            f"- Acceptance: `{metrics.accepted}`",
            "",
            "## Distilled Rules",
            "",
        ]
        for rule in rules:
            lines.extend([
                f"### {rule['id']}",
                "",
                f"- Rule: {rule['rule']}",
                f"- Parameter: `{rule['parameter']}`",
                f"- Constraint: `{rule['constraint']}`",
                "",
            ])
        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")
        return self.knowledge_path

    def apply_layer3(self, metrics: LocomotionCNSMetrics) -> float:
        self.state.total_cycles += 1
        if metrics.accepted:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1
        self.state.best_accepted_ratio = max(self.state.best_accepted_ratio, metrics.accepted_ratio)
        self.state.best_mean_sliding_error = min(self.state.best_mean_sliding_error, metrics.mean_sliding_error)
        self.state.best_runtime_score = max(self.state.best_runtime_score, metrics.mean_runtime_score)
        self.state.history.append(metrics.to_dict())
        self._save_state()
        stability_bonus = min(0.10, metrics.accepted_ratio * 0.10)
        slide_bonus = min(0.10, max(0.0, 0.08 - metrics.mean_sliding_error) / 0.08 * 0.10)
        return stability_bonus + slide_bonus

    def run_cycle(self, bus: Optional[RuntimeDistillationBus] = None) -> dict[str, Any]:
        metrics = self.evaluate(bus=bus)
        rules = self.distill_rules(metrics)
        knowledge_path = self.write_knowledge_file(metrics, rules)
        fitness_bonus = self.apply_layer3(metrics)
        return {
            "metrics": metrics.to_dict(),
            "knowledge_path": str(knowledge_path),
            "fitness_bonus": float(fitness_bonus),
            "accepted": bool(metrics.accepted),
        }


__all__ = [
    "LocomotionCNSMetrics",
    "LocomotionCNSState",
    "LocomotionCNSBridge",
]
