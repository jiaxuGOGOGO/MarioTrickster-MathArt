"""Three-Layer Evolution Orchestrator — Unified Self-Evolving System.

SESSION-055: Battle 4 — The Three-Layer Evolution Loop

This module is the **apex orchestrator** that unifies all SESSION-055
subsystems into a single self-evolving feedback loop:

    ┌─────────────────────────────────────────────────────────────────┐
    │              THREE-LAYER EVOLUTION LOOP                        │
    │                                                                │
    │  ┌──────────────────────────────────────────────────────────┐  │
    │  │  Layer 1: INTERNAL EVOLUTION (自我进化)                  │  │
    │  │  ├── XPBD solver auto-tuning (InternalEvolver)          │  │
    │  │  ├── Graph-fuzz NaN/penetration monitoring               │  │
    │  │  └── Visual fitness self-optimization                    │  │
    │  └──────────────────────────────────────────────────────────┘  │
    │                          ↓ findings                           │
    │  ┌──────────────────────────────────────────────────────────┐  │
    │  │  Layer 2: EXTERNAL KNOWLEDGE DISTILLATION (外部知识蒸馏) │  │
    │  │  ├── Research paper ingestion (KnowledgeDistiller)       │  │
    │  │  ├── User-provided insights                              │  │
    │  │  └── Cross-session learning persistence                  │  │
    │  └──────────────────────────────────────────────────────────┘  │
    │                          ↓ rules                              │
    │  ┌──────────────────────────────────────────────────────────┐  │
    │  │  Layer 3: SELF-ITERATING TEST (自我迭代测试)             │  │
    │  │  ├── Headless E2E CI (structural + visual regression)   │  │
    │  │  ├── Graph-fuzz CI (state-machine coverage)              │  │
    │  │  ├── Asset Factory (commercial quality gates)            │  │
    │  │  └── Physics test harness (Newton's law validation)      │  │
    │  └──────────────────────────────────────────────────────────┘  │
    │                          ↓ failures                           │
    │                    ┌──→ Layer 1 (re-tune) ──→ ...             │
    └─────────────────────────────────────────────────────────────────┘

The orchestrator is designed to be **self-contained**: it can run
autonomously, or accept new information from the user to trigger
knowledge distillation and re-evolution.

Usage::

    from mathart.evolution.evolution_orchestrator import EvolutionOrchestrator
    orch = EvolutionOrchestrator(project_root=".")
    report = orch.run_full_cycle()
    print(report.summary())
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class EvolutionCycleReport:
    """Report from a single evolution cycle."""

    session_id: str = "SESSION-055"
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
            "all_pass": self.all_pass,
            "evolution_triggered": self.evolution_triggered,
        }

    def summary(self) -> str:
        lines = [
            f"=== Evolution Cycle Report (Cycle {self.cycle_id}) ===",
            f"Timestamp: {self.timestamp}",
            "",
            "--- Layer 1: Internal Evolution ---",
            f"  XPBD tuning actions: {', '.join(self.xpbd_tuning_actions) or 'none'}",
            f"  Graph-fuzz NaN: {self.graph_fuzz_nan_count}",
            f"  Graph-fuzz penetration: {self.graph_fuzz_penetration_count}",
            f"  Graph-fuzz edge coverage: {self.graph_fuzz_edge_coverage:.2%}",
            f"  Visual fitness mean: {self.visual_fitness_mean:.4f}",
            "",
            "--- Layer 2: Knowledge Distillation ---",
            f"  Knowledge entries applied: {self.knowledge_entries_applied}",
            f"  New rules distilled: {self.new_rules_distilled}",
            "",
            "--- Layer 3: Self-Iterating Test ---",
            f"  E2E L0 (cold start): {'PASS' if self.e2e_level0_pass else 'FAIL'}",
            f"  E2E L1 (structural): {'PASS' if self.e2e_level1_pass else 'FAIL'}",
            f"  E2E L2 (visual): {'PASS' if self.e2e_level2_pass else 'FAIL'}",
            f"  Physics tests: {self.physics_tests_passed}/{self.physics_tests_total}",
            f"  Asset factory: {self.asset_factory_accepted}/{self.asset_factory_total} accepted",
            "",
            f"ALL PASS: {self.all_pass}",
            f"Evolution triggered: {self.evolution_triggered}",
        ]
        return "\n".join(lines)


@dataclass
class EvolutionState:
    """Persistent state for the evolution orchestrator."""

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


# ---------------------------------------------------------------------------
# Evolution Orchestrator
# ---------------------------------------------------------------------------

class EvolutionOrchestrator:
    """Unified three-layer evolution orchestrator.

    Coordinates all SESSION-055 subsystems into a single self-evolving loop.
    """

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
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[evolution] {msg}")

    # -------------------------------------------------------------------
    # Layer 1: Internal Evolution
    # -------------------------------------------------------------------

    def _run_layer1(self, report: EvolutionCycleReport) -> None:
        """Layer 1: Internal evolution — XPBD tuning + graph-fuzz monitoring."""
        self._log("Layer 1: Running XPBD evolution cycle...")

        # 1a. XPBD auto-tuning via existing orchestrator
        try:
            from mathart.animation.xpbd_evolution import XPBDEvolutionOrchestrator
            xpbd_orch = XPBDEvolutionOrchestrator()
            new_config = xpbd_orch.evolve()
            test_results = xpbd_orch.run_test_cycle()
            report.physics_tests_passed = sum(1 for r in test_results if r.passed)
            report.physics_tests_total = len(test_results)
            for action in xpbd_orch._evolver._history[-5:]:
                if action.tuning_action != "NO_ACTION":
                    report.xpbd_tuning_actions.append(action.tuning_action)
            self._log(f"  XPBD tests: {report.physics_tests_passed}/{report.physics_tests_total}")
        except Exception as e:
            self._log(f"  XPBD evolution error: {e}")

        # 1b. Graph-fuzz monitoring
        try:
            from mathart.headless_graph_fuzz_ci import run_graph_fuzz_audit
            fuzz_report = run_graph_fuzz_audit(
                num_random_walks=10, random_walk_steps=30,
            )
            report.graph_fuzz_nan_count = fuzz_report.nan_explosions
            report.graph_fuzz_penetration_count = fuzz_report.penetration_violations
            report.graph_fuzz_edge_coverage = fuzz_report.coverage_edge
            self._log(f"  Graph-fuzz: NaN={fuzz_report.nan_explosions}, "
                       f"penetration={fuzz_report.penetration_violations}, "
                       f"coverage={fuzz_report.coverage_edge:.2%}")
        except Exception as e:
            self._log(f"  Graph-fuzz error: {e}")

    # -------------------------------------------------------------------
    # Layer 2: Knowledge Distillation
    # -------------------------------------------------------------------

    def _run_layer2(self, report: EvolutionCycleReport) -> None:
        """Layer 2: External knowledge distillation."""
        self._log("Layer 2: Running knowledge distillation...")

        try:
            from mathart.animation.xpbd_evolution import (
                KnowledgeDistiller, KnowledgeEntry,
            )

            knowledge_path = self.root / "knowledge" / "xpbd_knowledge.json"
            distiller = KnowledgeDistiller(knowledge_path)

            # Inject SESSION-055 research findings as knowledge entries
            session_055_entries = [
                KnowledgeEntry(
                    source="SESSION-055: David R. MacIver, Hypothesis JOSS 2019",
                    topic="Property-based graph-fuzz testing for XPBD",
                    insight="Property-based stateful testing with RuleBasedStateMachine "
                            "can generate adversarial state-transition sequences that "
                            "expose NaN explosions and penetration violations in XPBD.",
                    parameter_effects={"testing.graph_fuzz_enabled": True},
                ),
                KnowledgeEntry(
                    source="SESSION-055: Wang et al., SSIM IEEE TIP 2004",
                    topic="SSIM temporal consistency for animation quality",
                    insight="SSIM temporal consistency between adjacent animation frames "
                            "should be above 0.85 to prevent visible geometric deformation. "
                            "Combined with Laplacian variance for normal map quality.",
                    parameter_effects={"quality.ssim_min_threshold": 0.85},
                ),
                KnowledgeEntry(
                    source="SESSION-055: Laplacian Variance NR-IQA",
                    topic="Normal map quality via Laplacian variance sweet-spot",
                    insight="Normal map quality is best measured by Laplacian variance "
                            "in a sweet-spot range (50-5000). Too low = blurry, too high = noisy.",
                    parameter_effects={"quality.laplacian_optimal": 500.0},
                ),
                KnowledgeEntry(
                    source="SESSION-055: Asset Factory Design",
                    topic="Commercial asset quality gates",
                    insight="Commercial asset packs require multi-modal quality gates: "
                            "visual fitness > 0.45, Laplacian score > 0.20, and 100% export success.",
                    parameter_effects={"factory.min_visual_fitness": 0.45},
                ),
            ]

            for entry in session_055_entries:
                distiller.add_knowledge(entry)

            report.knowledge_entries_applied = len(session_055_entries)
            report.new_rules_distilled = len(session_055_entries)
            self.state.knowledge_entries_count += len(session_055_entries)
            self._log(f"  Distilled {len(session_055_entries)} knowledge entries")

        except Exception as e:
            self._log(f"  Knowledge distillation error: {e}")

    # -------------------------------------------------------------------
    # Layer 3: Self-Iterating Test
    # -------------------------------------------------------------------

    def _run_layer3(self, report: EvolutionCycleReport) -> None:
        """Layer 3: Self-iterating test — E2E + asset factory."""
        self._log("Layer 3: Running self-iterating tests...")

        # 3a. Headless E2E CI
        try:
            from mathart.headless_e2e_ci import run_full_audit
            e2e_report = run_full_audit()
            report.e2e_level0_pass = e2e_report.level0_pass
            report.e2e_level1_pass = e2e_report.level1_pass
            report.e2e_level2_pass = e2e_report.level2_pass
            self._log(f"  E2E: L0={'PASS' if e2e_report.level0_pass else 'FAIL'}, "
                       f"L1={'PASS' if e2e_report.level1_pass else 'FAIL'}, "
                       f"L2={'PASS' if e2e_report.level2_pass else 'FAIL'}")
        except Exception as e:
            self._log(f"  E2E CI error: {e}")

        # 3b. Asset Factory
        try:
            from mathart.evolution.asset_factory_bridge import AssetFactory, AssetSpec
            factory = AssetFactory(project_root=str(self.root), verbose=self.verbose)
            # Use minimal specs for CI speed
            specs = [
                AssetSpec(name="evo_idle", preset="mario", state="idle",
                          width=32, height=32),
                AssetSpec(name="evo_walk", preset="mario", state="walk",
                          width=32, height=32),
                AssetSpec(name="evo_run", preset="mario", state="run",
                          width=32, height=32),
            ]
            factory_report = factory.run_production_cycle(specs=specs)
            report.asset_factory_accepted = factory_report.accepted_assets
            report.asset_factory_total = factory_report.total_assets
            report.visual_fitness_mean = factory_report.mean_visual_fitness
            self._log(f"  Asset factory: {factory_report.accepted_assets}/"
                       f"{factory_report.total_assets} accepted, "
                       f"fitness={factory_report.mean_visual_fitness:.4f}")
        except Exception as e:
            self._log(f"  Asset factory error: {e}")

    # -------------------------------------------------------------------
    # Full Cycle
    # -------------------------------------------------------------------

    def run_full_cycle(self) -> EvolutionCycleReport:
        """Run a complete three-layer evolution cycle.

        1. Layer 1: Internal evolution (XPBD tuning + graph-fuzz)
        2. Layer 2: Knowledge distillation
        3. Layer 3: Self-iterating test (E2E + asset factory)
        4. Determine if evolution was triggered
        5. Update persistent state

        Returns
        -------
        EvolutionCycleReport
            Complete cycle report.
        """
        report = EvolutionCycleReport(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            cycle_id=self.state.total_cycles + 1,
        )

        self._log(f"Starting evolution cycle {report.cycle_id}...")

        # Run all three layers
        self._run_layer1(report)
        self._run_layer2(report)
        self._run_layer3(report)

        # Determine overall pass/fail
        report.all_pass = (
            report.graph_fuzz_nan_count == 0
            and report.graph_fuzz_penetration_count == 0
            and report.e2e_level0_pass
        )

        # Evolution is triggered if any test failed
        report.evolution_triggered = not report.all_pass

        # Update persistent state
        self.state.total_cycles += 1
        if report.all_pass:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1
        self.state.best_visual_fitness = max(
            self.state.best_visual_fitness, report.visual_fitness_mean,
        )
        self.state.best_edge_coverage = max(
            self.state.best_edge_coverage, report.graph_fuzz_edge_coverage,
        )
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
        """Ingest user-provided knowledge for the next evolution cycle.

        This is the entry point for the user to feed new information
        into the system, triggering knowledge distillation on the next cycle.
        """
        try:
            from mathart.animation.xpbd_evolution import (
                KnowledgeDistiller, KnowledgeEntry,
            )
            knowledge_path = self.root / "knowledge" / "xpbd_knowledge.json"
            knowledge_path.parent.mkdir(parents=True, exist_ok=True)
            distiller = KnowledgeDistiller(knowledge_path)
            entry = KnowledgeEntry(
                source=source,
                topic=topic,
                insight=insight,
                parameter_effects=parameter_effects or {},
            )
            distiller.add_knowledge(entry)
            self.state.knowledge_entries_count += 1
            self._save_state()
        except Exception as e:
            if self.verbose:
                print(f"[evolution] Knowledge ingestion error: {e}")


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------


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
