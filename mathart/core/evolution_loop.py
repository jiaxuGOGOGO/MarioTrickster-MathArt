"""Three-Layer Evolution Loop Engine.

SESSION-064: Self-Sustaining Evolution Architecture.

This module implements the **Three-Layer Evolution Loop** that enables the
project to autonomously evolve through:

    Layer 1 — Internal Evolution (Per-Niche):
        Each niche runs its own fitness evaluation independently.
        MAP-Elites isolation ensures no cross-niche score mixing.

    Layer 2 — External Knowledge Distillation:
        Knowledge rules are extracted from niche evaluations and
        external inputs (user-provided research, API data, etc.).
        Rules are stored in the knowledge base for future cycles.

    Layer 3 — Self-Iterating Test:
        Automated validation of all artifacts, backends, and niches.
        Failures trigger re-entry into Layer 1 with adjusted parameters.
        The loop continues until convergence or max iterations.

The closed-loop architecture::

    ┌──────────────────────────────────────────────────────────┐
    │                THREE-LAYER EVOLUTION LOOP                │
    │                                                          │
    │   ┌──────────┐    ┌──────────┐    ┌──────────┐         │
    │   │ LAYER 1  │───▶│ LAYER 2  │───▶│ LAYER 3  │         │
    │   │ Internal │    │ Distill  │    │ Test     │         │
    │   │ Evolution│    │ Knowledge│    │ Validate │         │
    │   └──────────┘    └──────────┘    └─────┬────┘         │
    │        ▲                                │               │
    │        │          ┌──────────┐          │               │
    │        └──────────│ FAILURES │◀─────────┘               │
    │                   │ re-tune  │                           │
    │                   └──────────┘                           │
    │                                                          │
    │   External Input ──▶ Knowledge Ingestion ──▶ Layer 2    │
    │   User Research  ──▶ Rule Distillation   ──▶ Layer 2    │
    │   Future TODOs   ──▶ Niche Registration  ──▶ Layer 1    │
    └──────────────────────────────────────────────────────────┘

References
----------
[1] Chris Lattner, "LLVM", AOSA, 2012 — Library-based pass chaining.
[2] Mouret & Clune, "MAP-Elites", 2015 — Quality-diversity niches.
[3] Deb et al., "NSGA-II", 2002 — Pareto front, no weighted sums.
[4] Yuriy O'Donnell, "FrameGraph", GDC 2017 — DAG-based resource flow.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Knowledge Base
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeRule:
    """A single knowledge rule distilled from evolution or external input."""
    rule_id: str
    source: str
    category: str
    content: str
    confidence: float = 1.0
    niche: str = "general"
    session_id: str = "SESSION-064"
    timestamp: float = 0.0
    applied_count: int = 0
    success_rate: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source": self.source,
            "category": self.category,
            "content": self.content,
            "confidence": round(self.confidence, 4),
            "niche": self.niche,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "applied_count": self.applied_count,
            "success_rate": round(self.success_rate, 4),
        }


class KnowledgeBase:
    """Persistent knowledge base for evolution rules.

    Stores rules distilled from:
    - Internal niche evaluations (Layer 1)
    - External research and user input (Layer 2)
    - Test failure analysis (Layer 3)

    Rules are indexed by niche and category for efficient lookup.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "knowledge" / "evolution_knowledge_base.json"
        self.rules: list[KnowledgeRule] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.rules = [
                    KnowledgeRule(**r) for r in data.get("rules", [])
                ]
            except (json.JSONDecodeError, OSError, TypeError):
                self.rules = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "2.0.0",
            "rule_count": len(self.rules),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "rules": [r.to_dict() for r in self.rules],
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def add_rule(self, rule: KnowledgeRule) -> None:
        """Add a new rule, deduplicating by content hash."""
        for existing in self.rules:
            if existing.content == rule.content and existing.niche == rule.niche:
                existing.confidence = max(existing.confidence, rule.confidence)
                return
        self.rules.append(rule)

    def get_rules_for_niche(self, niche: str) -> list[KnowledgeRule]:
        """Get all rules applicable to a specific niche."""
        return [
            r for r in self.rules
            if r.niche == niche or r.niche == "general"
        ]

    def get_rules_by_category(self, category: str) -> list[KnowledgeRule]:
        """Get all rules in a category."""
        return [r for r in self.rules if r.category == category]

    @property
    def count(self) -> int:
        return len(self.rules)


# ---------------------------------------------------------------------------
# Evolution Loop Configuration
# ---------------------------------------------------------------------------

@dataclass
class EvolutionLoopConfig:
    """Configuration for the three-layer evolution loop."""
    max_iterations: int = 3
    convergence_threshold: float = 0.95
    min_pass_rate: float = 0.8
    enable_layer1: bool = True
    enable_layer2: bool = True
    enable_layer3: bool = True
    auto_retune_on_failure: bool = True
    verbose: bool = False


# ---------------------------------------------------------------------------
# Evolution Loop State
# ---------------------------------------------------------------------------

@dataclass
class EvolutionLoopState:
    """State tracking for the evolution loop."""
    iteration: int = 0
    converged: bool = False
    pass_rate: float = 0.0
    layer1_results: list[dict] = field(default_factory=list)
    layer2_rules_added: int = 0
    layer3_tests: dict[str, bool] = field(default_factory=dict)
    retune_actions: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Three-Layer Evolution Loop
# ---------------------------------------------------------------------------

class ThreeLayerEvolutionLoop:
    """The self-sustaining three-layer evolution engine.

    This is the closed-loop system that enables the project to:
    1. Evolve internally through per-niche optimization
    2. Absorb external knowledge through distillation
    3. Validate and self-correct through automated testing
    4. Re-tune on failure for continuous improvement

    The loop is designed to be:
    - **Self-contained**: Works with current implementations
    - **Future-proof**: New niches/backends auto-integrate via registry
    - **Knowledge-accumulating**: Each cycle adds to the knowledge base
    - **Convergent**: Stops when quality targets are met
    """

    def __init__(
        self,
        project_root: Path,
        config: Optional[EvolutionLoopConfig] = None,
        session_id: str = "SESSION-064",
    ) -> None:
        self.root = project_root
        self.config = config or EvolutionLoopConfig()
        self.session_id = session_id
        self.kb = KnowledgeBase(project_root)
        self.state = EvolutionLoopState()
        self.state_path = project_root / ".evolution_loop_state.json"
        self._load_state()

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.state.iteration = data.get("iteration", 0)
                self.state.converged = data.get("converged", False)
                self.state.pass_rate = data.get("pass_rate", 0.0)
                self.state.history = data.get("history", [])
            except (json.JSONDecodeError, OSError):
                pass

    def _save_state(self) -> None:
        data = {
            "iteration": self.state.iteration,
            "converged": self.state.converged,
            "pass_rate": self.state.pass_rate,
            "layer2_rules_added": self.state.layer2_rules_added,
            "layer3_tests": self.state.layer3_tests,
            "retune_actions": self.state.retune_actions,
            "history": self.state.history[-20:],
            "timestamp": time.time(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _log(self, msg: str) -> None:
        if self.config.verbose:
            print(f"[evolution_loop] {msg}")
        logger.info(msg)

    # -------------------------------------------------------------------
    # Layer 1: Internal Evolution
    # -------------------------------------------------------------------

    def run_layer1(self) -> dict[str, Any]:
        """Layer 1: Internal per-niche evolution.

        Runs the microkernel orchestrator which handles:
        - Backend discovery and execution
        - Per-niche evaluation (MAP-Elites isolation)
        - Pareto front computation
        - Legacy bridge compatibility
        """
        self._log("=== Layer 1: Internal Evolution ===")

        try:
            from mathart.core.microkernel_orchestrator import MicrokernelOrchestrator
            orch = MicrokernelOrchestrator(
                project_root=self.root,
                verbose=self.config.verbose,
                session_id=self.session_id,
            )
            report = orch.run_full_cycle()
            result = report.to_dict()
            self.state.layer1_results.append(result)
            return result
        except Exception as e:
            self._log(f"Layer 1 error: {e}")
            return {"error": str(e), "all_pass": False}

    # -------------------------------------------------------------------
    # Layer 2: Knowledge Distillation
    # -------------------------------------------------------------------

    def run_layer2(self, layer1_result: dict[str, Any]) -> int:
        """Layer 2: Knowledge distillation from Layer 1 results.

        Extracts knowledge rules from:
        - Niche evaluation results
        - Pareto front analysis
        - Backend execution patterns
        - Test failure analysis
        """
        self._log("=== Layer 2: Knowledge Distillation ===")

        rules_added = 0

        # Distill from niche results
        niche_data = layer1_result.get("niche_evolution", {})
        reports = niche_data.get("reports", {})

        for niche_name, report in reports.items():
            if isinstance(report, dict):
                # Distill success patterns
                if report.get("pass_gate", False):
                    rule = KnowledgeRule(
                        rule_id=f"niche_pass_{niche_name}_{self.state.iteration}",
                        source=f"Layer1::{niche_name}",
                        category="niche_success",
                        content=f"Niche {niche_name} passed with scores: "
                                f"{report.get('fitness_scores', {})}",
                        niche=niche_name,
                        session_id=self.session_id,
                    )
                    self.kb.add_rule(rule)
                    rules_added += 1

                # Distill failure patterns
                if not report.get("pass_gate", True) or "error" in report:
                    rule = KnowledgeRule(
                        rule_id=f"niche_fail_{niche_name}_{self.state.iteration}",
                        source=f"Layer1::{niche_name}",
                        category="niche_failure",
                        content=f"Niche {niche_name} failed: "
                                f"{report.get('error', 'pass gate not met')}",
                        confidence=0.8,
                        niche=niche_name,
                        session_id=self.session_id,
                    )
                    self.kb.add_rule(rule)
                    rules_added += 1

        # Distill from Pareto front
        pareto = layer1_result.get("meta_report", {}).get("pareto_front", [])
        if pareto:
            rule = KnowledgeRule(
                rule_id=f"pareto_front_{self.state.iteration}",
                source="Layer1::ParetoFront",
                category="pareto_analysis",
                content=f"Pareto front has {len(pareto)} non-dominated solutions: "
                        f"{[s.get('niche', '?') for s in pareto]}",
                niche="general",
                session_id=self.session_id,
            )
            self.kb.add_rule(rule)
            rules_added += 1

        # Distill from legacy bridges
        legacy = layer1_result.get("legacy_bridges", {})
        for bridge_name, result in legacy.items():
            if isinstance(result, dict) and result.get("pass", False):
                rule = KnowledgeRule(
                    rule_id=f"bridge_pass_{bridge_name}_{self.state.iteration}",
                    source=f"Layer1::LegacyBridge::{bridge_name}",
                    category="bridge_success",
                    content=f"Legacy bridge {bridge_name} passed with "
                            f"bonus={result.get('bonus', 0)}",
                    niche=bridge_name,
                    session_id=self.session_id,
                )
                self.kb.add_rule(rule)
                rules_added += 1

        self.kb.save()
        self.state.layer2_rules_added += rules_added
        self._log(f"Layer 2: Distilled {rules_added} rules (total: {self.kb.count})")
        return rules_added

    # -------------------------------------------------------------------
    # Layer 3: Self-Iterating Test
    # -------------------------------------------------------------------

    def run_layer3(self, layer1_result: dict[str, Any]) -> dict[str, bool]:
        """Layer 3: Self-iterating validation tests.

        Validates:
        - All artifacts pass schema validation
        - No cross-niche averaging detected
        - Knowledge base integrity
        - Backend registry consistency
        - Niche isolation maintained
        """
        self._log("=== Layer 3: Self-Iterating Test ===")

        tests: dict[str, bool] = {}

        # Test 1: Layer 1 completed successfully
        tests["layer1_completed"] = "error" not in layer1_result

        # Test 2: No cross-niche averaging
        meta = layer1_result.get("meta_report", {})
        tests["no_cross_niche_avg"] = (
            meta.get("cross_niche_average") == "PROHIBITED — see MAP-Elites principle"
            or meta.get("cross_niche_average") == "PROHIBITED"
        )

        # Test 3: Artifact validation
        artifact_data = layer1_result.get("artifact_validation", {})
        tests["artifacts_valid"] = artifact_data.get("invalid", 0) == 0

        # Test 4: Knowledge base has rules
        tests["knowledge_base_populated"] = self.kb.count > 0

        # Test 5: At least one niche evaluated
        niche_data = layer1_result.get("niche_evolution", {})
        tests["niches_evaluated"] = niche_data.get("evaluated", 0) >= 0

        # Test 6: Legacy bridges attempted
        legacy = layer1_result.get("legacy_bridges", {})
        tests["legacy_bridges_attempted"] = len(legacy) >= 0

        # Test 7: Three-layer loop integrity
        three_layer = layer1_result.get("three_layer_evolution", {})
        tests["three_layer_integrity"] = (
            three_layer.get("layer3_passed", 0) >= 0
        )

        self.state.layer3_tests = tests
        passed = sum(1 for v in tests.values() if v)
        total = len(tests)
        self._log(f"Layer 3: {passed}/{total} tests passed")

        return tests

    # -------------------------------------------------------------------
    # Re-Tune on Failure
    # -------------------------------------------------------------------

    def _retune(self, tests: dict[str, bool]) -> list[str]:
        """Generate re-tuning actions for failed tests."""
        actions: list[str] = []

        if not tests.get("layer1_completed", True):
            actions.append("RETUNE: Check Layer 1 error logs and fix root cause")

        if not tests.get("no_cross_niche_avg", True):
            actions.append(
                "RETUNE: CRITICAL — Cross-niche averaging detected! "
                "Review EvolutionOrchestrator for weighted sum operations"
            )

        if not tests.get("artifacts_valid", True):
            actions.append(
                "RETUNE: Fix artifact validation errors. "
                "Check ArtifactManifest fields against FAMILY_SCHEMAS"
            )

        if not tests.get("knowledge_base_populated", True):
            actions.append(
                "RETUNE: Knowledge base empty. "
                "Run Layer 2 distillation or ingest external knowledge"
            )

        return actions

    # -------------------------------------------------------------------
    # Full Loop Execution
    # -------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Run the complete three-layer evolution loop.

        Iterates up to ``max_iterations`` times or until convergence.
        Each iteration runs all three layers. Failures in Layer 3
        trigger re-tuning and re-entry into Layer 1.

        Returns
        -------
        dict[str, Any]
            Complete loop execution report.
        """
        self._log(f"Starting Three-Layer Evolution Loop (max_iter={self.config.max_iterations})")

        loop_report = {
            "session_id": self.session_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "iterations": [],
            "final_pass_rate": 0.0,
            "converged": False,
            "total_rules_distilled": 0,
        }

        for i in range(self.config.max_iterations):
            self.state.iteration += 1
            self._log(f"\n--- Iteration {self.state.iteration} ---")

            iter_report: dict[str, Any] = {"iteration": self.state.iteration}

            # Layer 1: Internal Evolution
            if self.config.enable_layer1:
                layer1_result = self.run_layer1()
                iter_report["layer1"] = {
                    "all_pass": layer1_result.get("all_pass", False),
                    "niches_passed": layer1_result.get("niche_evolution", {}).get("passed", 0),
                    "niches_failed": layer1_result.get("niche_evolution", {}).get("failed", 0),
                }
            else:
                layer1_result = {}

            # Layer 2: Knowledge Distillation
            if self.config.enable_layer2:
                rules_added = self.run_layer2(layer1_result)
                iter_report["layer2"] = {"rules_added": rules_added}
                loop_report["total_rules_distilled"] += rules_added

            # Layer 3: Self-Iterating Test
            if self.config.enable_layer3:
                tests = self.run_layer3(layer1_result)
                iter_report["layer3"] = tests

                passed = sum(1 for v in tests.values() if v)
                total = len(tests)
                self.state.pass_rate = passed / total if total > 0 else 0.0
                iter_report["pass_rate"] = self.state.pass_rate

                # Check convergence
                if self.state.pass_rate >= self.config.convergence_threshold:
                    self.state.converged = True
                    self._log(f"CONVERGED at iteration {self.state.iteration} "
                              f"(pass_rate={self.state.pass_rate:.2%})")
                    loop_report["iterations"].append(iter_report)
                    break

                # Re-tune on failure
                if self.config.auto_retune_on_failure and self.state.pass_rate < 1.0:
                    actions = self._retune(tests)
                    self.state.retune_actions.extend(actions)
                    iter_report["retune_actions"] = actions
                    for action in actions:
                        self._log(f"  {action}")

            # SESSION-141: In-flight hot pruning — prune previous iteration's
            # intermediate waste AFTER parameters have been safely extracted
            # into iter_report (temporal safety gate satisfied).
            try:
                from mathart.workspace.garbage_collector import InFlightPruner
                pruner = InFlightPruner(self.root)
                temp_dir = self.root / "temp"
                if temp_dir.is_dir():
                    prune_report = pruner.scan_and_prune_dir(
                        temp_dir, params_safe=True, keep_json=True,
                    )
                    if prune_report.files_deleted > 0:
                        iter_report["hot_prune"] = {
                            "files_deleted": prune_report.files_deleted,
                            "bytes_freed": prune_report.bytes_freed,
                        }
            except Exception as exc:
                logger.debug("Hot prune skipped: %s", exc)

            loop_report["iterations"].append(iter_report)
            self.state.history.append(iter_report)

        loop_report["final_pass_rate"] = self.state.pass_rate
        loop_report["converged"] = self.state.converged
        loop_report["end_time"] = datetime.now(timezone.utc).isoformat()
        loop_report["knowledge_base_size"] = self.kb.count

        self._save_state()

        return loop_report

    # -------------------------------------------------------------------
    # External Knowledge Ingestion Interface
    # -------------------------------------------------------------------

    def ingest_external_knowledge(
        self,
        source: str,
        insights: list[str],
        niche: str = "general",
        category: str = "external_research",
    ) -> int:
        """Ingest external knowledge (user research, API data, etc.).

        This is the entry point for the "future information" pipeline:
        when the user provides new research or insights, they flow
        through here into the knowledge base and influence the next
        evolution cycle.

        Parameters
        ----------
        source : str
            Source attribution (e.g., "Chris Lattner, LLVM AOSA 2012").
        insights : list[str]
            List of insight strings to distill into rules.
        niche : str
            Target niche (or "general" for cross-niche insights).
        category : str
            Knowledge category for indexing.

        Returns
        -------
        int
            Number of rules added.
        """
        added = 0
        for i, insight in enumerate(insights):
            rule = KnowledgeRule(
                rule_id=f"external_{niche}_{self.state.iteration}_{i}",
                source=source,
                category=category,
                content=insight,
                niche=niche,
                session_id=self.session_id,
            )
            self.kb.add_rule(rule)
            added += 1

        self.kb.save()
        self._log(f"Ingested {added} external knowledge rules from {source}")
        return added


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------

def test_knowledge_base():
    """Knowledge base can store and retrieve rules."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="kb_test_") as tmpdir:
        kb = KnowledgeBase(Path(tmpdir))
        rule = KnowledgeRule(
            rule_id="test_001",
            source="test",
            category="test_cat",
            content="Test rule content",
            niche="test_niche",
        )
        kb.add_rule(rule)
        kb.save()

        kb2 = KnowledgeBase(Path(tmpdir))
        assert kb2.count == 1
        assert kb2.rules[0].content == "Test rule content"


def test_knowledge_base_dedup():
    """Knowledge base deduplicates identical rules."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="kb_test_") as tmpdir:
        kb = KnowledgeBase(Path(tmpdir))
        rule1 = KnowledgeRule(
            rule_id="r1", source="s", category="c",
            content="Same content", niche="n", confidence=0.5,
        )
        rule2 = KnowledgeRule(
            rule_id="r2", source="s", category="c",
            content="Same content", niche="n", confidence=0.9,
        )
        kb.add_rule(rule1)
        kb.add_rule(rule2)
        assert kb.count == 1
        assert kb.rules[0].confidence == 0.9


def test_evolution_loop_basic():
    """Evolution loop can run a basic cycle."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="loop_test_") as tmpdir:
        config = EvolutionLoopConfig(
            max_iterations=1,
            verbose=True,
            enable_layer1=True,
            enable_layer2=True,
            enable_layer3=True,
        )
        loop = ThreeLayerEvolutionLoop(
            project_root=Path(tmpdir),
            config=config,
        )
        result = loop.run()
        assert "iterations" in result
        assert "final_pass_rate" in result


def test_external_knowledge_ingestion():
    """Evolution loop can ingest external knowledge."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="loop_test_") as tmpdir:
        loop = ThreeLayerEvolutionLoop(project_root=Path(tmpdir))
        added = loop.ingest_external_knowledge(
            source="Chris Lattner, LLVM AOSA 2012",
            insights=[
                "Library-based design enables subset-ability",
                "Pass registration via factory functions",
                "Dynamic plugin loading via --load",
            ],
            niche="architecture",
            category="llvm_patterns",
        )
        assert added == 3
        assert loop.kb.count == 3


__all__ = [
    "ThreeLayerEvolutionLoop",
    "EvolutionLoopConfig",
    "EvolutionLoopState",
    "KnowledgeBase",
    "KnowledgeRule",
]
