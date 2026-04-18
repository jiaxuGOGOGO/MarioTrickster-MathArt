"""Federated evolution orchestrator facade.

SESSION-066 repositions the legacy ``EvolutionOrchestrator`` as a compatibility
facade over the Golden Path architecture:

1. **Layer 1** delegates to ``MicrokernelOrchestrator`` so lanes evolve in
   parallel and only produce meta-reports.
2. **Layer 2** delegates to ``ThreeLayerEvolutionLoop`` knowledge distillation.
3. **Layer 3** delegates to ``ThreeLayerEvolutionLoop`` self-iterating tests.

The public class and report fields remain stable so older callers continue to
work, but the implementation no longer centralizes hard-coded business logic.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mathart.core.evolution_loop import (
    EvolutionLoopConfig,
    KnowledgeRule,
    ThreeLayerEvolutionLoop,
)
from mathart.core.microkernel_orchestrator import MicrokernelOrchestrator


@dataclass
class EvolutionCycleReport:
    """Backward-compatible report from a single federated evolution cycle."""

    session_id: str = "SESSION-066"
    timestamp: str = ""
    cycle_id: int = 0

    # Layer 1: Internal Evolution
    xpbd_tuning_actions: list[str] = field(default_factory=list)
    graph_fuzz_nan_count: int = 0
    graph_fuzz_penetration_count: int = 0
    graph_fuzz_edge_coverage: float = 0.0
    visual_fitness_mean: float = 0.0

    # Layer 2: Knowledge Distillation
    knowledge_entries_applied: int = 0
    new_rules_distilled: int = 0

    # Layer 3: Self-Iterating Test
    e2e_level0_pass: bool = False
    e2e_level1_pass: bool = False
    e2e_level2_pass: bool = False
    physics_tests_passed: int = 0
    physics_tests_total: int = 0
    asset_factory_accepted: int = 0
    asset_factory_total: int = 0

    # Unified bridge suite / federated status
    unified_bridges_passed: int = 0
    unified_bridges_total: int = 0
    unified_bridge_bonus: float = 0.0
    unified_bridge_status: dict[str, Any] = field(default_factory=dict)

    # Overall
    all_pass: bool = False
    evolution_triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "cycle_id": self.cycle_id,
            "layer1_internal_evolution": {
                "xpbd_tuning_actions": self.xpbd_tuning_actions,
                "graph_fuzz_nan_count": self.graph_fuzz_nan_count,
                "graph_fuzz_penetration_count": self.graph_fuzz_penetration_count,
                "graph_fuzz_edge_coverage": round(self.graph_fuzz_edge_coverage, 4),
                "visual_fitness_mean": round(self.visual_fitness_mean, 4),
            },
            "layer2_knowledge_distillation": {
                "knowledge_entries_applied": self.knowledge_entries_applied,
                "new_rules_distilled": self.new_rules_distilled,
            },
            "layer3_self_iterating_test": {
                "e2e_level0_pass": self.e2e_level0_pass,
                "e2e_level1_pass": self.e2e_level1_pass,
                "e2e_level2_pass": self.e2e_level2_pass,
                "physics_tests_passed": self.physics_tests_passed,
                "physics_tests_total": self.physics_tests_total,
                "asset_factory_accepted": self.asset_factory_accepted,
                "asset_factory_total": self.asset_factory_total,
            },
            "unified_bridge_suite": {
                "passed": self.unified_bridges_passed,
                "total": self.unified_bridges_total,
                "fitness_bonus": round(self.unified_bridge_bonus, 4),
                "status": self.unified_bridge_status,
            },
            "all_pass": self.all_pass,
            "evolution_triggered": self.evolution_triggered,
        }

    def summary(self) -> str:
        lines = [
            f"=== Evolution Cycle Report (Cycle {self.cycle_id}) ===",
            f"Timestamp: {self.timestamp}",
            "",
            "--- Layer 1: Internal Evolution ---",
            f"  Federated actions: {', '.join(self.xpbd_tuning_actions) or 'none'}",
            f"  Lane pass ratio proxy: {self.graph_fuzz_edge_coverage:.2%}",
            f"  Validation pass ratio proxy: {self.visual_fitness_mean:.4f}",
            "",
            "--- Layer 2: Knowledge Distillation ---",
            f"  Knowledge entries applied: {self.knowledge_entries_applied}",
            f"  New rules distilled: {self.new_rules_distilled}",
            "",
            "--- Layer 3: Self-Iterating Test ---",
            f"  E2E L0 (loop started): {'PASS' if self.e2e_level0_pass else 'FAIL'}",
            f"  E2E L1 (artifacts valid): {'PASS' if self.e2e_level1_pass else 'FAIL'}",
            f"  E2E L2 (no cross-lane averaging): {'PASS' if self.e2e_level2_pass else 'FAIL'}",
            f"  Federated tests: {self.physics_tests_passed}/{self.physics_tests_total}",
            f"  Lane acceptance: {self.asset_factory_accepted}/{self.asset_factory_total}",
            "",
            "--- Unified Bridge Suite ---",
            f"  Bridge pass count: {self.unified_bridges_passed}/{self.unified_bridges_total}",
            f"  Bridge fitness bonus: {self.unified_bridge_bonus:.4f}",
            f"  Bridge status keys: {', '.join(sorted(self.unified_bridge_status.keys())) or 'none'}",
            "",
            f"ALL PASS: {self.all_pass}",
            f"Evolution triggered: {self.evolution_triggered}",
        ]
        return "\n".join(lines)


@dataclass
class EvolutionState:
    """Persistent state for the federated evolution facade."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_visual_fitness: float = 0.0
    best_edge_coverage: float = 0.0
    knowledge_entries_count: int = 0
    quality_trend: list[float] = field(default_factory=list)
    cycle_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_visual_fitness": round(self.best_visual_fitness, 4),
            "best_edge_coverage": round(self.best_edge_coverage, 4),
            "knowledge_entries_count": self.knowledge_entries_count,
            "quality_trend": [round(q, 4) for q in self.quality_trend[-50:]],
            "cycle_history": self.cycle_history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvolutionState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            best_visual_fitness=float(data.get("best_visual_fitness", 0.0)),
            best_edge_coverage=float(data.get("best_edge_coverage", 0.0)),
            knowledge_entries_count=int(data.get("knowledge_entries_count", 0)),
            quality_trend=list(data.get("quality_trend", [])),
            cycle_history=list(data.get("cycle_history", [])),
        )


class EvolutionOrchestrator:
    """Compatibility facade for the Golden Path federated evolution stack."""

    STATE_FILE = ".evolution_orchestrator_state.json"

    def __init__(
        self,
        project_root: Optional[str | Path] = None,
        *,
        verbose: bool = False,
    ) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.verbose = verbose
        self.state_path = self.root / self.STATE_FILE
        self.state = self._load_state()

    def _load_state(self) -> EvolutionState:
        if not self.state_path.exists():
            return EvolutionState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return EvolutionState()
        return EvolutionState.from_dict(data)

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[evolution] {msg}")

    def run_full_cycle(self) -> EvolutionCycleReport:
        """Run a complete federated Golden Path evolution cycle."""
        report = EvolutionCycleReport(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            cycle_id=self.state.total_cycles + 1,
        )
        self._log(f"Starting federated evolution cycle {report.cycle_id}...")

        microkernel = MicrokernelOrchestrator(
            project_root=self.root,
            verbose=self.verbose,
            session_id="SESSION-066",
        )
        micro_report = microkernel.run_full_cycle()
        micro_dict = micro_report.to_dict()

        loop = ThreeLayerEvolutionLoop(
            project_root=self.root,
            config=EvolutionLoopConfig(max_iterations=1, verbose=self.verbose),
            session_id="SESSION-066",
        )
        rules_added = loop.run_layer2(micro_dict)
        tests = loop.run_layer3(micro_dict)

        lane_ratio = (
            micro_report.niches_passed / micro_report.niches_evaluated
            if micro_report.niches_evaluated > 0 else 0.0
        )
        test_ratio = (
            micro_report.layer3_tests_passed / micro_report.layer3_tests_total
            if micro_report.layer3_tests_total > 0 else 0.0
        )
        legacy_results = dict(micro_report.legacy_bridge_results)
        legacy_bonus = sum(
            float(payload.get("bonus", 0.0))
            for payload in legacy_results.values()
            if isinstance(payload, dict)
        )
        legacy_passed = sum(
            1 for payload in legacy_results.values()
            if isinstance(payload, dict) and payload.get("pass", False)
        )

        report.xpbd_tuning_actions = list(micro_report.layer1_internal_actions[-12:])
        report.graph_fuzz_edge_coverage = lane_ratio
        report.visual_fitness_mean = test_ratio
        report.knowledge_entries_applied = rules_added
        report.new_rules_distilled = rules_added
        report.e2e_level0_pass = tests.get("layer1_completed", False)
        report.e2e_level1_pass = tests.get("artifacts_valid", False)
        report.e2e_level2_pass = tests.get("no_cross_niche_avg", False)
        report.physics_tests_passed = micro_report.layer3_tests_passed
        report.physics_tests_total = micro_report.layer3_tests_total
        report.asset_factory_accepted = micro_report.niches_passed
        report.asset_factory_total = micro_report.niches_evaluated
        report.unified_bridges_passed = legacy_passed
        report.unified_bridges_total = len(legacy_results)
        report.unified_bridge_bonus = legacy_bonus
        report.unified_bridge_status = {
            "federated_meta_report": micro_report.meta_report,
            "lane_reports": micro_report.niche_reports,
            **legacy_results,
        }
        report.all_pass = bool(micro_report.all_pass and all(tests.values()))
        report.evolution_triggered = not report.all_pass

        self.state.total_cycles += 1
        if report.all_pass:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1
        self.state.best_visual_fitness = max(self.state.best_visual_fitness, report.visual_fitness_mean)
        self.state.best_edge_coverage = max(self.state.best_edge_coverage, report.graph_fuzz_edge_coverage)
        self.state.knowledge_entries_count += rules_added
        self.state.quality_trend.append(report.visual_fitness_mean)
        self.state.cycle_history.append(report.to_dict())
        self._save_state()

        self._log(f"Cycle {report.cycle_id} complete. ALL_PASS={report.all_pass}")
        return report

    def ingest_user_knowledge(
        self,
        source: str,
        insight: str,
        topic: str = "general",
        parameter_effects: Optional[dict[str, Any]] = None,
    ) -> None:
        """Ingest user-provided knowledge into the persistent evolution KB."""
        loop = ThreeLayerEvolutionLoop(
            project_root=self.root,
            config=EvolutionLoopConfig(max_iterations=1, verbose=self.verbose),
            session_id="SESSION-066",
        )
        payload = insight.strip()
        if parameter_effects:
            payload += f" | parameter_effects={json.dumps(parameter_effects, ensure_ascii=False, sort_keys=True)}"
        loop.kb.add_rule(
            KnowledgeRule(
                rule_id=f"user_{int(time.time() * 1000)}",
                source=source,
                category=topic,
                content=payload,
                niche="general",
                session_id="SESSION-066",
            )
        )
        loop.kb.save()
        self.state.knowledge_entries_count += 1
        self._save_state()


def test_evolution_orchestrator_full_cycle():
    """Evolution orchestrator can run a complete cycle."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="evo_test_") as tmpdir:
        orch = EvolutionOrchestrator(project_root=tmpdir, verbose=True)
        report = orch.run_full_cycle()
        assert report.cycle_id == 1
        assert report.timestamp != ""
        assert isinstance(report.all_pass, bool)


def test_evolution_orchestrator_state_persistence():
    """Evolution orchestrator state persists across instances."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="evo_test_") as tmpdir:
        orch = EvolutionOrchestrator(project_root=tmpdir)
        orch.run_full_cycle()
        assert orch.state.total_cycles == 1

        orch2 = EvolutionOrchestrator(project_root=tmpdir)
        assert orch2.state.total_cycles == 1


def test_evolution_orchestrator_knowledge_ingestion():
    """Evolution orchestrator can ingest user knowledge."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="evo_test_") as tmpdir:
        orch = EvolutionOrchestrator(project_root=tmpdir)
        orch.ingest_user_knowledge(
            source="test",
            insight="Test insight",
            topic="test_topic",
            parameter_effects={"test_param": 42},
        )
        assert orch.state.knowledge_entries_count == 1


def test_evolution_orchestrator_quality_trend():
    """Evolution orchestrator tracks quality trend."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="evo_test_") as tmpdir:
        orch = EvolutionOrchestrator(project_root=tmpdir)
        orch.run_full_cycle()
        orch.run_full_cycle()
        assert len(orch.state.quality_trend) == 2


__all__ = [
    "EvolutionCycleReport",
    "EvolutionState",
    "EvolutionOrchestrator",
]
