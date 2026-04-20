"""Microkernel Orchestrator — Unified Contract-Based Evolution Engine.

SESSION-064: The Apex Integration — Three Paradigm Shifts United.

This module is the **new apex orchestrator** that replaces the centralized
routing model with a contract-based microkernel architecture. It integrates:

    1. **Backend Registry** (LLVM): Discovers and chains backends dynamically.
    2. **Artifact Schema** (USD): Validates all outputs against typed schemas.
    3. **Niche Registry** (MAP-Elites): Runs per-lane evolution with Pareto front.

The orchestrator implements the **Three-Layer Evolution Loop** with strict
niche isolation:

    Layer 1: Internal Evolution (per-niche, isolated fitness)
    Layer 2: External Knowledge Distillation (per-niche + cross-niche insights)
    Layer 3: Self-Iterating Test (per-niche validation, Meta-Report aggregation)

Architecture::

    ┌─────────────────────────────────────────────────────────────────┐
    │              MICROKERNEL ORCHESTRATOR                           │
    │                                                                 │
    │  ┌───────────────────────────────────────────────────────────┐  │
    │  │  Backend Registry (LLVM)                                  │  │
    │  │  @register_backend → auto-discovery → dependency chain    │  │
    │  └───────────────────────────────────────────────────────────┘  │
    │                          ↓ artifacts                            │
    │  ┌───────────────────────────────────────────────────────────┐  │
    │  │  Artifact Schema (USD)                                    │  │
    │  │  validate_artifact → schema_hash → composition arcs       │  │
    │  └───────────────────────────────────────────────────────────┘  │
    │                          ↓ validated outputs                    │
    │  ┌───────────────────────────────────────────────────────────┐  │
    │  │  Niche Registry (MAP-Elites)                              │  │
    │  │  per-lane evaluation → Pareto front → Meta-Report         │  │
    │  └───────────────────────────────────────────────────────────┘  │
    │                          ↓                                      │
    │  ┌───────────────────────────────────────────────────────────┐  │
    │  │  Three-Layer Evolution Loop                               │  │
    │  │  L1: Internal (per-niche) → L2: Distill → L3: Test       │  │
    │  │  ↓ failures → L1 re-tune (closed loop)                   │  │
    │  └───────────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────────┘

References
----------
[1] Chris Lattner, "LLVM", AOSA, 2012.
[2] Pixar, "OpenUSD", openusd.org, 2024.
[3] Yuriy O'Donnell, "FrameGraph", GDC 2017.
[4] Mouret & Clune, "MAP-Elites", arXiv:1504.04909, 2015.
[5] Deb et al., "NSGA-II", IEEE TEC, 2002.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mathart.core.backend_registry import (
    BackendRegistry,
    BackendCapability,
    BackendMeta,
    get_registry,
)
from mathart.core.safe_point_execution import (
    SafePointExecutionLock,
    get_safe_point_lock,
)
from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    ArtifactValidationError,
    CompositeManifestBuilder,
    validate_artifact,
)
from mathart.core.niche_registry import (
    EvolutionNiche,
    NicheRegistry,
    NicheReport,
    ParetoFront,
    get_niche_registry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Microkernel Cycle Report
# ---------------------------------------------------------------------------

@dataclass
class MicrokernelCycleReport:
    """Report from a complete microkernel evolution cycle."""

    session_id: str = "SESSION-064"
    cycle_id: int = 0
    timestamp: str = ""

    # Backend discovery
    backends_discovered: int = 0
    backends_executed: int = 0
    backend_errors: list[str] = field(default_factory=list)

    # Artifact validation
    artifacts_produced: int = 0
    artifacts_valid: int = 0
    artifacts_invalid: int = 0
    validation_errors: list[str] = field(default_factory=list)

    # Niche evolution (MAP-Elites)
    niches_evaluated: int = 0
    niches_passed: int = 0
    niches_failed: int = 0
    niche_reports: dict[str, dict] = field(default_factory=dict)
    pareto_front_size: int = 0

    # Three-layer evolution
    layer1_internal_actions: list[str] = field(default_factory=list)
    layer2_knowledge_distilled: int = 0
    layer3_tests_passed: int = 0
    layer3_tests_total: int = 0

    # Legacy bridge compatibility
    legacy_bridge_results: dict[str, Any] = field(default_factory=dict)

    # Overall
    all_pass: bool = False
    meta_report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cycle_id": self.cycle_id,
            "timestamp": self.timestamp,
            "backend_discovery": {
                "discovered": self.backends_discovered,
                "executed": self.backends_executed,
                "errors": self.backend_errors,
            },
            "artifact_validation": {
                "produced": self.artifacts_produced,
                "valid": self.artifacts_valid,
                "invalid": self.artifacts_invalid,
                "errors": self.validation_errors[:20],
            },
            "niche_evolution": {
                "evaluated": self.niches_evaluated,
                "passed": self.niches_passed,
                "failed": self.niches_failed,
                "reports": self.niche_reports,
                "pareto_front_size": self.pareto_front_size,
                "cross_niche_average": "PROHIBITED",
            },
            "three_layer_evolution": {
                "layer1_actions": self.layer1_internal_actions,
                "layer2_distilled": self.layer2_knowledge_distilled,
                "layer3_passed": self.layer3_tests_passed,
                "layer3_total": self.layer3_tests_total,
            },
            "legacy_bridges": self.legacy_bridge_results,
            "all_pass": self.all_pass,
            "meta_report": self.meta_report,
        }

    def summary(self) -> str:
        lines = [
            f"=== Microkernel Evolution Cycle {self.cycle_id} ===",
            f"Timestamp: {self.timestamp}",
            "",
            "--- Backend Discovery ---",
            f"  Discovered: {self.backends_discovered}",
            f"  Executed: {self.backends_executed}",
            f"  Errors: {len(self.backend_errors)}",
            "",
            "--- Artifact Validation ---",
            f"  Produced: {self.artifacts_produced}",
            f"  Valid: {self.artifacts_valid}",
            f"  Invalid: {self.artifacts_invalid}",
            "",
            "--- Niche Evolution (MAP-Elites) ---",
            f"  Evaluated: {self.niches_evaluated}",
            f"  Passed: {self.niches_passed}",
            f"  Failed: {self.niches_failed}",
            f"  Pareto front size: {self.pareto_front_size}",
            f"  Cross-niche average: PROHIBITED",
            "",
            "--- Three-Layer Evolution ---",
            f"  L1 actions: {len(self.layer1_internal_actions)}",
            f"  L2 distilled: {self.layer2_knowledge_distilled}",
            f"  L3 tests: {self.layer3_tests_passed}/{self.layer3_tests_total}",
            "",
            f"ALL PASS: {self.all_pass}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Microkernel State
# ---------------------------------------------------------------------------

@dataclass
class MicrokernelState:
    """Persistent state for the microkernel orchestrator."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_pareto_front_size: int = 0
    niche_trends: dict[str, list[float]] = field(default_factory=dict)
    knowledge_rule_count: int = 0
    artifact_count: int = 0
    cycle_history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_pareto_front_size": self.best_pareto_front_size,
            "niche_trends": {
                k: [round(v, 4) for v in vals[-20:]]
                for k, vals in self.niche_trends.items()
            },
            "knowledge_rule_count": self.knowledge_rule_count,
            "artifact_count": self.artifact_count,
            "cycle_history": self.cycle_history[-10:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MicrokernelState":
        return cls(
            total_cycles=data.get("total_cycles", 0),
            total_passes=data.get("total_passes", 0),
            total_failures=data.get("total_failures", 0),
            best_pareto_front_size=data.get("best_pareto_front_size", 0),
            niche_trends=data.get("niche_trends", {}),
            knowledge_rule_count=data.get("knowledge_rule_count", 0),
            artifact_count=data.get("artifact_count", 0),
            cycle_history=data.get("cycle_history", []),
        )


# ---------------------------------------------------------------------------
# Microkernel Orchestrator
# ---------------------------------------------------------------------------

class MicrokernelOrchestrator:
    """Contract-based microkernel orchestrator.

    This is the new apex orchestrator that replaces centralized routing
    with plugin-based discovery and per-niche evolution.

    The orchestrator:
    1. Discovers all registered backends and niches at startup.
    2. Runs the three-layer evolution loop with strict niche isolation.
    3. Validates all artifacts against typed schemas.
    4. Produces a Meta-Report with Pareto front analysis.
    5. Maintains backward compatibility with legacy bridges.
    """

    STATE_FILE = ".microkernel_state.json"

    def __init__(
        self,
        project_root: Optional[str | Path] = None,
        *,
        verbose: bool = False,
        session_id: str = "SESSION-064",
    ) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.verbose = verbose
        self.session_id = session_id
        self.state_path = self.root / self.STATE_FILE
        self.state = self._load_state()
        self.backend_registry = get_registry()
        self.niche_registry = get_niche_registry()

        # SESSION-091 (P1-AI-2E): Hot-reload coordination state.
        # Result cache and iteration counters are invalidated on reload
        # to prevent the Stale Cache Leak anti-pattern.
        self._result_cache: dict[str, Any] = {}
        self._iteration_counters: dict[str, int] = {}
        self._safe_point_lock = get_safe_point_lock()
        self._reload_callbacks: list[tuple[str, Any]] = []

    def _load_state(self) -> MicrokernelState:
        if not self.state_path.exists():
            return MicrokernelState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return MicrokernelState.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return MicrokernelState()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[microkernel] {msg}")
        logger.info(msg)

    # -------------------------------------------------------------------
    # Layer 1: Internal Evolution (Per-Niche)
    # -------------------------------------------------------------------

    def _run_layer1(self, report: MicrokernelCycleReport) -> dict[str, NicheReport]:
        """Layer 1: Run per-niche internal evaluation.

        Each niche evaluates independently — NO cross-niche mixing.
        """
        self._log("Layer 1: Running per-niche internal evolution...")

        niche_reports: dict[str, NicheReport] = {}
        pareto = ParetoFront()

        # Run registered niches
        all_niches = self.niche_registry.all_niches()
        for name, (meta, niche_cls) in all_niches.items():
            try:
                niche = niche_cls(project_root=self.root)
                niche_report = niche.evaluate()
                niche_reports[name] = niche_report
                pareto.add_niche_report(niche_report)
                report.niches_evaluated += 1
                if niche_report.pass_gate:
                    report.niches_passed += 1
                else:
                    report.niches_failed += 1
                report.niche_reports[name] = niche_report.to_dict()
                self._log(
                    f"  Niche {name}: pass={niche_report.pass_gate}, "
                    f"fitness={niche_report.fitness_scores}"
                )
            except Exception as e:
                report.niches_failed += 1
                report.niche_reports[name] = {"error": str(e)}
                self._log(f"  Niche {name} error: {e}")

        # Run legacy bridges for backward compatibility
        self._run_legacy_bridges(report)

        # Compute Pareto front
        front = pareto.compute_front()
        report.pareto_front_size = len(front)

        # Generate Meta-Report (no cross-niche averaging!)
        report.meta_report = self.niche_registry.generate_meta_report(
            niche_reports, pareto,
        )

        return niche_reports

    # -------------------------------------------------------------------
    # Layer 2: Knowledge Distillation
    # -------------------------------------------------------------------

    def _run_layer2(
        self,
        report: MicrokernelCycleReport,
        niche_reports: dict[str, NicheReport],
    ) -> None:
        """Layer 2: Distill knowledge from niche evaluations."""
        self._log("Layer 2: Running knowledge distillation...")

        total_rules = 0
        all_niches = self.niche_registry.all_niches()

        for name, (meta, niche_cls) in all_niches.items():
            try:
                niche = niche_cls(project_root=self.root)
                rules = niche.distill()
                total_rules += len(rules)
                self._log(f"  Niche {name}: distilled {len(rules)} rules")
            except Exception as e:
                self._log(f"  Niche {name} distill error: {e}")

        # Distill cross-niche architectural insights
        arch_rules = self._distill_architectural_insights(niche_reports)
        total_rules += len(arch_rules)

        report.layer2_knowledge_distilled = total_rules
        self.state.knowledge_rule_count += total_rules

    def _distill_architectural_insights(
        self, niche_reports: dict[str, NicheReport],
    ) -> list[str]:
        """Distill cross-niche architectural insights.

        These are NOT fitness averages — they are structural observations
        about which niches are strong/weak and what patterns emerge.
        """
        insights: list[str] = []

        passed = [n for n, r in niche_reports.items() if r.pass_gate]
        failed = [n for n, r in niche_reports.items() if not r.pass_gate]

        if failed:
            insights.append(
                f"ARCH_INSIGHT: Niches [{', '.join(failed)}] failed pass gate. "
                f"Investigate root cause before cross-niche integration."
            )

        if len(passed) == len(niche_reports) and len(passed) > 0:
            insights.append(
                "ARCH_INSIGHT: All niches passed. System is ready for "
                "cross-niche composition (USD Composition Arcs)."
            )

        return insights

    # -------------------------------------------------------------------
    # Layer 3: Self-Iterating Test
    # -------------------------------------------------------------------

    def _run_layer3(self, report: MicrokernelCycleReport) -> None:
        """Layer 3: Run self-iterating validation tests."""
        self._log("Layer 3: Running self-iterating tests...")

        tests_passed = 0
        tests_total = 0

        # Test 1: All backends discoverable
        tests_total += 1
        backends = self.backend_registry.all_backends()
        report.backends_discovered = len(backends)
        if len(backends) > 0:
            tests_passed += 1
            self._log(f"  Backend discovery: {len(backends)} backends found")
        else:
            self._log("  Backend discovery: WARNING — no backends registered")

        # Test 2: Artifact schema validation
        tests_total += 1
        test_manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.META_REPORT.value,
            backend_type="microkernel",
            outputs={"report_file": "meta_report.json"},
            metadata={"niche_count": report.niches_evaluated},
        )
        errors = validate_artifact(test_manifest)
        if not errors:
            tests_passed += 1
            report.artifacts_valid += 1
        else:
            report.artifacts_invalid += 1
            report.validation_errors.extend(errors)
        report.artifacts_produced += 1

        # Test 3: Niche isolation (no cross-niche averaging)
        tests_total += 1
        if report.meta_report.get("cross_niche_average") == "PROHIBITED — see MAP-Elites principle":
            tests_passed += 1
        else:
            report.validation_errors.append(
                "CRITICAL: Cross-niche averaging detected!"
            )

        # Test 4: Legacy bridge compatibility
        tests_total += 1
        if report.legacy_bridge_results:
            legacy_passed = sum(
                1 for v in report.legacy_bridge_results.values()
                if isinstance(v, dict) and v.get("pass", False)
            )
            if legacy_passed > 0:
                tests_passed += 1

        report.layer3_tests_passed = tests_passed
        report.layer3_tests_total = tests_total

    # -------------------------------------------------------------------
    # Legacy Bridge Compatibility
    # -------------------------------------------------------------------

    def _run_legacy_bridges(self, report: MicrokernelCycleReport) -> None:
        """Run all evolution-domain backends via registry reflection.

        SESSION-074 (P1-MIGRATE-2): Strangler Fig completion.

        The hardcoded ``bridge_specs`` list is **eliminated**.  Instead,
        the orchestrator discovers every backend that declares
        ``BackendCapability.EVOLUTION_DOMAIN`` through the registry's
        ``find_by_capability()`` API.  This makes the orchestrator
        completely blind to individual bridge identities — it only
        recognises the abstract EVOLUTION_DOMAIN contract.

        Design references:
            - Eclipse OSGi dynamic service discovery
            - Martin Fowler, Strangler Fig Application (2004)
        """
        from mathart.core.pipeline_bridge import MicrokernelPipelineBridge

        bridge = MicrokernelPipelineBridge(
            project_root=self.root, session_id=self.session_id,
        )

        evo_backends = self.backend_registry.find_by_capability(
            BackendCapability.EVOLUTION_DOMAIN,
        )

        for meta, _cls in sorted(evo_backends, key=lambda x: x[0].name):
            backend_name = meta.name
            try:
                ctx = {
                    "output_dir": str(self.root),
                    "name": f"evo_{backend_name}",
                    "verbose": self.verbose,
                    "evolution_state": {"cycle": 1, "population": []},
                }
                manifest = bridge.run_backend(backend_name, ctx)

                bonus = manifest.quality_metrics.get("fitness_bonus", 0.0)
                best_fitness = manifest.metadata.get("best_fitness", 0.0)
                bridge_pass = best_fitness > 0.0 or bonus > 0.0

                report.legacy_bridge_results[backend_name] = {
                    "pass": bridge_pass,
                    "bonus": round(float(bonus), 4),
                    "knowledge_ref": manifest.metadata.get("knowledge_path"),
                    "artifact_family": manifest.artifact_family,
                    "schema_hash": manifest.schema_hash,
                }
                report.layer1_internal_actions.append(
                    f"evo_backend:{backend_name}={'PASS' if bridge_pass else 'FAIL'}"
                )
                self._log(
                    f"  Evolution backend {backend_name}: "
                    f"{'PASS' if bridge_pass else 'FAIL'} (bonus={float(bonus):.4f})"
                )
            except Exception as e:
                report.legacy_bridge_results[backend_name] = {
                    "pass": False,
                    "error": str(e),
                }
                self._log(f"  Evolution backend {backend_name} error: {e}")

    # -------------------------------------------------------------------
    # Full Cycle
    # -------------------------------------------------------------------

    def run_full_cycle(self) -> MicrokernelCycleReport:
        """Run a complete microkernel evolution cycle.

        1. Layer 1: Per-niche internal evolution (MAP-Elites isolation)
        2. Layer 2: Knowledge distillation (per-niche + architectural)
        3. Layer 3: Self-iterating test (validation + Pareto front)
        4. Update persistent state
        5. Save Meta-Report artifact

        Returns
        -------
        MicrokernelCycleReport
            Complete cycle report with Pareto front analysis.
        """
        report = MicrokernelCycleReport(
            session_id=self.session_id,
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        self._log(f"Starting microkernel cycle {report.cycle_id}...")

        # Three-layer evolution loop
        niche_reports = self._run_layer1(report)
        self._run_layer2(report, niche_reports)
        self._run_layer3(report)

        # Determine overall pass
        report.all_pass = (
            report.niches_failed == 0
            and report.artifacts_invalid == 0
            and report.layer3_tests_passed == report.layer3_tests_total
        )

        # Update state
        self.state.total_cycles += 1
        if report.all_pass:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1
        self.state.best_pareto_front_size = max(
            self.state.best_pareto_front_size, report.pareto_front_size,
        )
        self.state.artifact_count += report.artifacts_produced
        self.state.cycle_history.append(report.to_dict())

        # Update niche trends
        for name, niche_report_dict in report.niche_reports.items():
            if isinstance(niche_report_dict, dict) and "fitness_scores" in niche_report_dict:
                scores = niche_report_dict["fitness_scores"]
                if scores:
                    primary = max(scores.values()) if scores else 0.0
                    self.state.niche_trends.setdefault(name, []).append(primary)

        self._save_state()

        # Save Meta-Report as artifact
        self._save_meta_report(report)

        self._log(f"Cycle {report.cycle_id} complete. ALL_PASS={report.all_pass}")
        return report

    def _save_meta_report(self, report: MicrokernelCycleReport) -> None:
        """Save the Meta-Report as a validated artifact."""
        report_path = (
            self.root / "evolution_reports"
            / f"microkernel_cycle_{report.cycle_id:04d}.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # -------------------------------------------------------------------
    # SESSION-091 (P1-AI-2E): Hot-Reload Coordination
    # -------------------------------------------------------------------

    def on_backend_reload(self, backend_name: str) -> None:
        """Callback invoked when a backend is hot-reloaded.

        SESSION-091 (P1-AI-2E): Implements the Stale Cache Invalidation
        protocol.  When a backend is reloaded:

        1. Purge its entry from ``_result_cache`` so stale keyframe plans
           or render strategies are never reused.
        2. Reset its ``_iteration_counters`` entry so the evolution loop
           re-evaluates from scratch with the new code.
        3. Notify all registered reload callbacks.

        Anti-Pattern Guard:
        - 🚫 **Stale Cache Leak Trap**: After reload, old KEYFRAME_PLAN
          or render strategy MUST be discarded.  This method enforces that.
        """
        # Targeted invalidation — NOT a global clear()
        if backend_name in self._result_cache:
            self._log(
                f"on_backend_reload: purging result cache for '{backend_name}'"
            )
            del self._result_cache[backend_name]

        if backend_name in self._iteration_counters:
            self._log(
                f"on_backend_reload: resetting iteration counter for '{backend_name}'"
            )
            del self._iteration_counters[backend_name]

        # Notify registered callbacks
        for cb_name, cb_fn in self._reload_callbacks:
            try:
                cb_fn(backend_name)
            except Exception as e:
                self._log(f"Reload callback '{cb_name}' error: {e}")

        self._log(f"on_backend_reload: '{backend_name}' cache invalidated")

    def register_reload_callback(
        self, name: str, callback: Any,
    ) -> None:
        """Register a callback to be invoked on backend reload.

        Callbacks receive the backend_name as their sole argument.
        Used by ComfyUI Client and other downstream consumers to
        invalidate their own caches.
        """
        self._reload_callbacks.append((name, callback))

    def cache_result(self, backend_name: str, result: Any) -> None:
        """Cache a backend execution result for reuse."""
        self._result_cache[backend_name] = result

    def get_cached_result(self, backend_name: str) -> Any | None:
        """Retrieve a cached result, or None if invalidated/missing."""
        return self._result_cache.get(backend_name)

    def increment_iteration(self, backend_name: str) -> int:
        """Increment and return the iteration counter for a backend."""
        count = self._iteration_counters.get(backend_name, 0) + 1
        self._iteration_counters[backend_name] = count
        return count

    def get_iteration_count(self, backend_name: str) -> int:
        """Get current iteration count for a backend."""
        return self._iteration_counters.get(backend_name, 0)

    @property
    def safe_point_lock(self) -> SafePointExecutionLock:
        """Access the SafePointExecutionLock for frame-boundary gating."""
        return self._safe_point_lock

    def ingest_knowledge(
        self,
        source: str,
        insight: str,
        niche: str = "general",
    ) -> None:
        """Ingest external knowledge for the next evolution cycle.

        This is the entry point for user-provided information and
        external research findings.
        """
        knowledge_path = self.root / "knowledge" / "microkernel_knowledge.json"
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)

        entries: list[dict] = []
        if knowledge_path.exists():
            try:
                entries = json.loads(knowledge_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                entries = []

        entries.append({
            "source": source,
            "insight": insight,
            "niche": niche,
            "timestamp": time.time(),
            "session_id": self.session_id,
        })

        knowledge_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.state.knowledge_rule_count += 1
        self._save_state()


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------

def test_microkernel_orchestrator_creation():
    """Microkernel orchestrator can be created."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mk_test_") as tmpdir:
        orch = MicrokernelOrchestrator(project_root=tmpdir, verbose=True)
        assert orch.state.total_cycles == 0


def test_microkernel_orchestrator_full_cycle():
    """Microkernel orchestrator can run a complete cycle."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mk_test_") as tmpdir:
        orch = MicrokernelOrchestrator(project_root=tmpdir, verbose=True)
        report = orch.run_full_cycle()
        assert report.cycle_id == 1
        assert report.timestamp != ""
        assert isinstance(report.all_pass, bool)
        # Verify no cross-niche averaging
        assert report.meta_report.get("cross_niche_average") == "PROHIBITED — see MAP-Elites principle"


def test_microkernel_orchestrator_state_persistence():
    """Microkernel state persists across instances."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mk_test_") as tmpdir:
        orch = MicrokernelOrchestrator(project_root=tmpdir)
        orch.run_full_cycle()
        assert orch.state.total_cycles == 1

        orch2 = MicrokernelOrchestrator(project_root=tmpdir)
        assert orch2.state.total_cycles == 1


def test_microkernel_knowledge_ingestion():
    """Microkernel orchestrator can ingest external knowledge."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mk_test_") as tmpdir:
        orch = MicrokernelOrchestrator(project_root=tmpdir)
        orch.ingest_knowledge(
            source="Chris Lattner, LLVM AOSA 2012",
            insight="Library-based design enables subset-ability",
            niche="architecture",
        )
        assert orch.state.knowledge_rule_count == 1


__all__ = [
    "MicrokernelOrchestrator",
    "MicrokernelCycleReport",
    "MicrokernelState",
]
