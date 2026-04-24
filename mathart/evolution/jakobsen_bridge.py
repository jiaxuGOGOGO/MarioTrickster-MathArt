"""Gap B1 bridge — lightweight rigid-soft coupling as a three-layer cycle.

This module turns Jakobsen-style secondary chains into a repository-native
closed loop:

1. Layer 1 — Evaluate whether cape/hair chains are stable, elastic, and alive.
2. Layer 2 — Distill reusable rules from the measured results.
3. Layer 3 — Persist trends so future sessions can tune toward lower error and
   better follow-through.
"""
from __future__ import annotations
from .state_vault import resolve_state_path

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np


@dataclass
class JakobsenChainMetrics:
    """Metrics captured from one Gap B1 evaluation cycle."""

    cycle_id: int = 0
    frame_count: int = 0
    chain_count: int = 0
    mean_constraint_error: float = 0.0
    max_constraint_error: float = 0.0
    mean_tip_lag: float = 0.0
    mean_collision_count: float = 0.0
    max_stretch_ratio: float = 1.0
    mean_anchor_speed: float = 0.0
    pass_gate: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "frame_count": self.frame_count,
            "chain_count": self.chain_count,
            "mean_constraint_error": self.mean_constraint_error,
            "max_constraint_error": self.max_constraint_error,
            "mean_tip_lag": self.mean_tip_lag,
            "mean_collision_count": self.mean_collision_count,
            "max_stretch_ratio": self.max_stretch_ratio,
            "mean_anchor_speed": self.mean_anchor_speed,
            "pass_gate": self.pass_gate,
            "timestamp": self.timestamp,
        }


@dataclass
class JakobsenChainState:
    """Persistent state for Gap B1 secondary-chain evolution."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    consecutive_passes: int = 0
    best_mean_constraint_error: float = 1.0
    best_mean_tip_lag: float = 0.0
    knowledge_rules_total: int = 0
    constraint_error_trend: list[float] = field(default_factory=list)
    tip_lag_trend: list[float] = field(default_factory=list)
    stretch_ratio_trend: list[float] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "consecutive_passes": self.consecutive_passes,
            "best_mean_constraint_error": self.best_mean_constraint_error,
            "best_mean_tip_lag": self.best_mean_tip_lag,
            "knowledge_rules_total": self.knowledge_rules_total,
            "constraint_error_trend": self.constraint_error_trend[-50:],
            "tip_lag_trend": self.tip_lag_trend[-50:],
            "stretch_ratio_trend": self.stretch_ratio_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JakobsenChainState":
        return cls(
            total_cycles=data.get("total_cycles", 0),
            total_passes=data.get("total_passes", 0),
            total_failures=data.get("total_failures", 0),
            consecutive_passes=data.get("consecutive_passes", 0),
            best_mean_constraint_error=data.get("best_mean_constraint_error", 1.0),
            best_mean_tip_lag=data.get("best_mean_tip_lag", 0.0),
            knowledge_rules_total=data.get("knowledge_rules_total", 0),
            constraint_error_trend=data.get("constraint_error_trend", []),
            tip_lag_trend=data.get("tip_lag_trend", []),
            stretch_ratio_trend=data.get("stretch_ratio_trend", []),
            history=data.get("history", []),
        )


@dataclass
class JakobsenChainStatus:
    """Repository integration status for Gap B1."""

    module_exists: bool = False
    bridge_exists: bool = False
    public_api_exports_chain: bool = False
    pipeline_supports_secondary_chains: bool = False
    test_exists: bool = False
    research_notes_path: str = ""
    tracked_exports: list[str] = field(default_factory=list)
    total_cycles: int = 0
    consecutive_passes: int = 0
    best_mean_constraint_error: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def collect_jakobsen_chain_status(project_root: str | Path) -> JakobsenChainStatus:
    """Collect repository integration status for Gap B1."""
    root = Path(project_root)
    module_path = root / "mathart/animation/jakobsen_chain.py"
    bridge_path = root / "mathart/evolution/jakobsen_bridge.py"
    api_module = root / "mathart/animation/__init__.py"
    pipeline_module = root / "mathart/pipeline.py"
    test_path = root / "tests/test_jakobsen_chain.py"
    notes_path = root / "docs/research/GAP_B1_JAKOBSEN_SECONDARY_CHAINS.md"
    state_path = resolve_state_path(root, ".jakobsen_chain_state.json")

    tracked_exports: list[str] = []
    if module_path.exists():
        text = module_path.read_text(encoding="utf-8", errors="replace")
        for name in (
            "JakobsenSecondaryChain",
            "SecondaryChainProjector",
            "SecondaryChainConfig",
            "create_default_secondary_chain_configs",
        ):
            if name in text:
                tracked_exports.append(name)

    api_exports = False
    if api_module.exists():
        api_text = api_module.read_text(encoding="utf-8", errors="replace")
        api_exports = "JakobsenSecondaryChain" in api_text and "SecondaryChainProjector" in api_text

    pipeline_support = False
    if pipeline_module.exists():
        pipeline_text = pipeline_module.read_text(encoding="utf-8", errors="replace")
        pipeline_support = all(name in pipeline_text for name in ("enable_secondary_chains", "secondary_chain_projection", "secondary_chain_config"))

    total_cycles = 0
    consecutive_passes = 0
    best_mean_constraint_error = 1.0
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            total_cycles = state_data.get("total_cycles", 0)
            consecutive_passes = state_data.get("consecutive_passes", 0)
            best_mean_constraint_error = state_data.get("best_mean_constraint_error", 1.0)
        except (json.JSONDecodeError, OSError):
            pass

    return JakobsenChainStatus(
        module_exists=module_path.exists(),
        bridge_exists=bridge_path.exists(),
        public_api_exports_chain=api_exports,
        pipeline_supports_secondary_chains=pipeline_support,
        test_exists=test_path.exists(),
        research_notes_path=str(notes_path.relative_to(root)) if notes_path.exists() else "",
        tracked_exports=tracked_exports,
        total_cycles=total_cycles,
        consecutive_passes=consecutive_passes,
        best_mean_constraint_error=best_mean_constraint_error,
    )


class JakobsenEvolutionBridge:
    """Three-layer evolution bridge for Gap B1 secondary chains."""

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    def evaluate_secondary_chains(
        self,
        diagnostics: Optional[list[dict[str, Any]]] = None,
        *,
        max_mean_constraint_error: float = 0.035,
        min_mean_tip_lag: float = 0.05,
        max_mean_tip_lag: float = 0.80,
        max_stretch_ratio: float = 1.30,
    ) -> JakobsenChainMetrics:
        """Layer 1: evaluate chain stability and follow-through quality."""
        metrics = JakobsenChainMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        if diagnostics:
            metrics.frame_count = len(diagnostics)
            chain_names = set()
            flat_entries: list[dict[str, Any]] = []
            for frame_entry in diagnostics:
                for chain_name, chain_metrics in frame_entry.items():
                    chain_names.add(chain_name)
                    flat_entries.append(chain_metrics)
            metrics.chain_count = len(chain_names)
            if flat_entries:
                metrics.mean_constraint_error = float(np.mean([m.get("mean_constraint_error", 0.0) for m in flat_entries]))
                metrics.max_constraint_error = float(np.max([m.get("max_constraint_error", 0.0) for m in flat_entries]))
                metrics.mean_tip_lag = float(np.mean([m.get("tip_lag", 0.0) for m in flat_entries]))
                metrics.mean_collision_count = float(np.mean([m.get("collision_count", 0.0) for m in flat_entries]))
                metrics.max_stretch_ratio = float(np.max([m.get("stretch_ratio", 1.0) for m in flat_entries]))
                metrics.mean_anchor_speed = float(np.mean([m.get("anchor_speed", 0.0) for m in flat_entries]))

        metrics.pass_gate = (
            metrics.frame_count > 0
            and metrics.chain_count > 0
            and metrics.mean_constraint_error <= max_mean_constraint_error
            and min_mean_tip_lag <= metrics.mean_tip_lag <= max_mean_tip_lag
            and metrics.max_stretch_ratio <= max_stretch_ratio
        )
        self._update_state(metrics)
        self._save_state()
        return metrics

    def distill_secondary_chain_knowledge(self, metrics: JakobsenChainMetrics) -> list[dict[str, str]]:
        """Layer 2: turn metrics into durable repository rules."""
        rules: list[dict[str, str]] = []
        if metrics.mean_constraint_error > 0.035:
            rules.append(
                {
                    "rule_id": f"jakobsen_error_{metrics.cycle_id}",
                    "rule_text": (
                        "When Jakobsen chain error rises, increase relaxation iterations or reduce segment length before reaching for XPBD."
                    ),
                }
            )
        if metrics.mean_tip_lag < 0.05:
            rules.append(
                {
                    "rule_id": f"jakobsen_lag_low_{metrics.cycle_id}",
                    "rule_text": (
                        "If secondary motion feels dead, inject more root acceleration inertia or slightly reduce damping so the tip preserves follow-through."
                    ),
                }
            )
        if metrics.max_stretch_ratio > 1.30:
            rules.append(
                {
                    "rule_id": f"jakobsen_stretch_{metrics.cycle_id}",
                    "rule_text": (
                        "Excessive stretch indicates too few constraint iterations or too much retained velocity; clamp velocity_retention and reinforce support constraints."
                    ),
                }
            )
        if metrics.pass_gate:
            rules.append(
                {
                    "rule_id": f"jakobsen_pass_{metrics.cycle_id}",
                    "rule_text": (
                        "Preferred Gap B1 recipe: kinematic root pin + Verlet position update + repeated distance relaxation + simple body proxies + per-frame diagnostics written into UMR metadata."
                    ),
                }
            )
        if not rules:
            rules.append(
                {
                    "rule_id": f"jakobsen_neutral_{metrics.cycle_id}",
                    "rule_text": "Gap B1 evaluation produced no actionable exception; keep the current lightweight chain recipe and continue collecting trend data.",
                }
            )

        knowledge_dir = self.project_root / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        knowledge_path = knowledge_dir / "jakobsen_secondary_chain_rules.md"
        with knowledge_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## Cycle {metrics.cycle_id} — {metrics.timestamp}\n\n")
            for rule in rules:
                f.write(f"- **{rule['rule_id']}**: {rule['rule_text']}\n")
        self.state.knowledge_rules_total += len(rules)
        self._save_state()
        return rules

    def compute_secondary_chain_fitness_bonus(self, metrics: JakobsenChainMetrics) -> float:
        """Layer 3: convert metrics into a small fitness bonus/penalty."""
        bonus = 0.0
        bonus += max(-0.12, min(0.12, 0.035 - metrics.mean_constraint_error))
        if 0.05 <= metrics.mean_tip_lag <= 0.40:
            bonus += 0.03
        elif metrics.mean_tip_lag > 0.80:
            bonus -= 0.04
        if metrics.max_stretch_ratio > 1.30:
            bonus -= 0.05
        if metrics.pass_gate:
            bonus += 0.04
        return float(np.clip(bonus, -0.20, 0.20))

    def status_report(self) -> str:
        """Human-readable summary for engine status panels."""
        status = collect_jakobsen_chain_status(self.project_root)
        lines = [
            "--- Jakobsen Secondary Chain Evolution Bridge (SESSION-047 / Gap B1) ---",
            f"  Total cycles: {self.state.total_cycles}",
            f"  Passes / failures: {self.state.total_passes} / {self.state.total_failures}",
            f"  Consecutive passes: {self.state.consecutive_passes}",
            f"  Best mean constraint error: {self.state.best_mean_constraint_error:.4f}",
            f"  Best mean tip lag: {self.state.best_mean_tip_lag:.4f}",
            f"  Module active: {'yes' if status.module_exists else 'no'}",
            f"  Pipeline integration: {'yes' if status.pipeline_supports_secondary_chains else 'no'}",
            f"  Public API export: {'yes' if status.public_api_exports_chain else 'no'}",
            f"  Test present: {'yes' if status.test_exists else 'no'}",
        ]
        if status.tracked_exports:
            lines.append(f"  Tracked exports: {', '.join(status.tracked_exports)}")
        return "\n".join(lines)

    def _state_path(self) -> Path:
        return resolve_state_path(self.project_root, ".jakobsen_chain_state.json")

    def _load_state(self) -> JakobsenChainState:
        path = self._state_path()
        if not path.exists():
            return JakobsenChainState()
        try:
            return JakobsenChainState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return JakobsenChainState()

    def _save_state(self) -> None:
        self._state_path().write_text(
            json.dumps(self.state.to_dict(), indent=2),
            encoding="utf-8",
        )

    def _update_state(self, metrics: JakobsenChainMetrics) -> None:
        self.state.total_cycles += 1
        if metrics.pass_gate:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
        self.state.best_mean_constraint_error = min(
            self.state.best_mean_constraint_error,
            metrics.mean_constraint_error if metrics.frame_count > 0 else self.state.best_mean_constraint_error,
        )
        self.state.best_mean_tip_lag = max(self.state.best_mean_tip_lag, metrics.mean_tip_lag)
        self.state.constraint_error_trend.append(metrics.mean_constraint_error)
        self.state.tip_lag_trend.append(metrics.mean_tip_lag)
        self.state.stretch_ratio_trend.append(metrics.max_stretch_ratio)
        self.state.history.append(metrics.to_dict())


__all__ = [
    "JakobsenChainMetrics",
    "JakobsenChainState",
    "JakobsenChainStatus",
    "JakobsenEvolutionBridge",
    "collect_jakobsen_chain_status",
]
