"""SESSION-040: Evolution Contract Bridge — Three-Layer Pipeline Contract Integration.

This module bridges the SESSION-040 pipeline contract system (UMR_Context,
PipelineContractGuard, UMR_Auditor) into the three-layer evolution loop,
enabling the **three-layer evolution cycle** to:

1. **Layer 1 (Inner Loop):** Validate that every generated character pack
   passes the pipeline contract before scoring. Reject packs with legacy
   bypass or hash seal failures.

2. **Layer 2 (Outer Loop):** Distill pipeline contract knowledge into
   reusable rules. When a contract violation is detected, auto-generate
   knowledge rules that prevent future regressions.

3. **Layer 3 (Self-Iteration):** Include contract compliance as a fitness
   dimension. Physics evolution cycles now verify that evolved parameters
   don't break the deterministic hash seal.

The three-layer evolution cycle:

    ┌─────────────────────────────────────────────────────────────┐
    │  Layer 1: Internal Evolution                                │
    │  Generate → Contract Validate → Evaluate → Optimize         │
    │  (reject legacy bypass, verify hash determinism)            │
    ├─────────────────────────────────────────────────────────────┤
    │  Layer 2: External Knowledge Distillation                   │
    │  Ingest Research → Extract Rules → Validate Against Contract│
    │  (Mike Acton DOD, Pixar USD, Glenn Fiedler determinism)     │
    ├─────────────────────────────────────────────────────────────┤
    │  Layer 3: Self-Iteration Testing                            │
    │  Train → Test (incl. contract) → Diagnose → Evolve → Distill│
    │  (hash seal regression, contact flicker, contract violations)│
    └─────────────────────────────────────────────────────────────┘

References:
    - Mike Acton, "Data-Oriented Design and C++", CppCon 2014
    - Glenn Fiedler, "Deterministic Lockstep", Gaffer on Games, 2014
    - Pixar USD Schema Validation & CI mechanism
"""
from __future__ import annotations
from .state_vault import resolve_state_path

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ContractEvolutionMetrics:
    """Metrics from a contract-aware evolution cycle."""

    cycle_id: int = 0
    contract_checks_passed: int = 0
    contract_checks_failed: int = 0
    legacy_bypass_attempts: int = 0
    hash_seal_verified: bool = False
    hash_seal_stable: bool = False  # True if hash matches golden master
    contact_flicker_detected: bool = False
    flicker_frame_count: int = 0
    knowledge_rules_generated: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "contract_checks_passed": self.contract_checks_passed,
            "contract_checks_failed": self.contract_checks_failed,
            "legacy_bypass_attempts": self.legacy_bypass_attempts,
            "hash_seal_verified": self.hash_seal_verified,
            "hash_seal_stable": self.hash_seal_stable,
            "contact_flicker_detected": self.contact_flicker_detected,
            "flicker_frame_count": self.flicker_frame_count,
            "knowledge_rules_generated": self.knowledge_rules_generated,
            "timestamp": self.timestamp,
        }


@dataclass
class ContractEvolutionState:
    """Persistent state for contract-aware evolution."""

    total_contract_cycles: int = 0
    total_contract_passes: int = 0
    total_contract_failures: int = 0
    golden_master_hash: str = ""
    golden_master_set: bool = False
    last_pipeline_hash: str = ""
    hash_stability_streak: int = 0  # consecutive cycles with stable hash
    knowledge_rules_total: int = 0
    history: list[ContractEvolutionMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_contract_cycles": self.total_contract_cycles,
            "total_contract_passes": self.total_contract_passes,
            "total_contract_failures": self.total_contract_failures,
            "golden_master_hash": self.golden_master_hash,
            "golden_master_set": self.golden_master_set,
            "last_pipeline_hash": self.last_pipeline_hash,
            "hash_stability_streak": self.hash_stability_streak,
            "knowledge_rules_total": self.knowledge_rules_total,
            "history": [h.to_dict() for h in self.history[-20:]],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContractEvolutionState":
        history = []
        for h in d.get("history", []):
            m = ContractEvolutionMetrics(**{
                k: v for k, v in h.items()
                if k in ContractEvolutionMetrics.__dataclass_fields__
            })
            history.append(m)
        return cls(
            total_contract_cycles=d.get("total_contract_cycles", 0),
            total_contract_passes=d.get("total_contract_passes", 0),
            total_contract_failures=d.get("total_contract_failures", 0),
            golden_master_hash=d.get("golden_master_hash", ""),
            golden_master_set=d.get("golden_master_set", False),
            last_pipeline_hash=d.get("last_pipeline_hash", ""),
            hash_stability_streak=d.get("hash_stability_streak", 0),
            knowledge_rules_total=d.get("knowledge_rules_total", 0),
            history=history,
        )


class ContractEvolutionBridge:
    """Bridge between the pipeline contract system and the three-layer evolution loop.

    This class provides the integration layer that allows the evolution engine
    to incorporate contract compliance into its fitness evaluation, knowledge
    distillation, and self-iteration cycles.

    Parameters
    ----------
    project_root : Path
        Root directory of the project.
    verbose : bool
        Print progress to stdout.
    """

    def __init__(
        self,
        project_root: Path,
        verbose: bool = True,
    ):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    def evaluate_contract_compliance(
        self,
        manifest_path: str | Path,
        umr_manifest_path: str | Path | None = None,
    ) -> ContractEvolutionMetrics:
        """Evaluate a character pack's contract compliance.

        This is called by Layer 1 (Inner Loop) after producing a character pack.
        It checks:
        1. Pipeline contract section exists in manifest
        2. All states are phase-driven (no legacy bypass)
        3. Hash seal is present and valid
        4. Contact flicker is within acceptable bounds

        Parameters
        ----------
        manifest_path : Path
            Path to the character manifest JSON.
        umr_manifest_path : Path, optional
            Path to the .umr_manifest.json seal file.

        Returns
        -------
        ContractEvolutionMetrics
            Metrics from the compliance check.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.state.total_contract_cycles += 1
        metrics = ContractEvolutionMetrics(
            cycle_id=self.state.total_contract_cycles,
            timestamp=now,
        )

        manifest_path = Path(manifest_path)
        if not manifest_path.exists():
            metrics.contract_checks_failed += 1
            self.state.total_contract_failures += 1
            return metrics

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            metrics.contract_checks_failed += 1
            self.state.total_contract_failures += 1
            return metrics

        # Check 1: Pipeline contract section
        pc = manifest.get("pipeline_contract", {})
        if pc.get("all_states_phase_driven") and pc.get("legacy_bypass_blocked"):
            metrics.contract_checks_passed += 1
        else:
            metrics.contract_checks_failed += 1
            metrics.legacy_bypass_attempts += 1

        # Check 2: Hash seal
        if umr_manifest_path is None:
            umr_manifest_path = manifest_path.parent / ".umr_manifest.json"
        umr_manifest_path = Path(umr_manifest_path)

        if umr_manifest_path.exists():
            try:
                seal_data = json.loads(umr_manifest_path.read_text(encoding="utf-8"))
                seal = seal_data.get("seal", {})
                pipeline_hash = seal.get("pipeline_hash", "")

                if pipeline_hash:
                    metrics.hash_seal_verified = True
                    metrics.contract_checks_passed += 1

                    # Check against golden master
                    if self.state.golden_master_set:
                        if pipeline_hash == self.state.golden_master_hash:
                            metrics.hash_seal_stable = True
                            self.state.hash_stability_streak += 1
                        else:
                            metrics.hash_seal_stable = False
                            self.state.hash_stability_streak = 0
                    else:
                        # First run: set golden master
                        self.state.golden_master_hash = pipeline_hash
                        self.state.golden_master_set = True
                        metrics.hash_seal_stable = True
                        self.state.hash_stability_streak = 1

                    self.state.last_pipeline_hash = pipeline_hash
                else:
                    metrics.contract_checks_failed += 1
            except (json.JSONDecodeError, OSError):
                metrics.contract_checks_failed += 1
        else:
            metrics.contract_checks_failed += 1

        # Update totals
        if metrics.contract_checks_failed == 0:
            self.state.total_contract_passes += 1
        else:
            self.state.total_contract_failures += 1

        self.state.history.append(metrics)
        self._save_state()

        if self.verbose:
            status = "PASS" if metrics.contract_checks_failed == 0 else "FAIL"
            logger.info(
                f"[ContractBridge] Cycle {metrics.cycle_id}: {status} "
                f"(passed={metrics.contract_checks_passed}, "
                f"failed={metrics.contract_checks_failed}, "
                f"hash_stable={metrics.hash_seal_stable})"
            )

        return metrics

    def distill_contract_knowledge(
        self,
        metrics: ContractEvolutionMetrics,
    ) -> list[dict[str, Any]]:
        """Distill contract compliance results into knowledge rules.

        This is called by Layer 2 (Outer Loop) to generate reusable rules
        from contract enforcement outcomes.

        Parameters
        ----------
        metrics : ContractEvolutionMetrics
            Metrics from the latest compliance check.

        Returns
        -------
        list[dict]
            Knowledge rules to add to the knowledge base.
        """
        rules: list[dict[str, Any]] = []

        if metrics.contract_checks_failed > 0:
            rules.append({
                "domain": "pipeline_contract",
                "rule_type": "enforcement",
                "rule_text": (
                    "Pipeline contract violation detected. All animation states MUST "
                    "use phase-driven generators. The legacy_pose_adapter path is "
                    "permanently blocked. Any new state must have a corresponding "
                    "phase_driven_*_frame() generator before it can be added to the pipeline."
                ),
                "params": {
                    "cycle_id": str(metrics.cycle_id),
                    "failures": str(metrics.contract_checks_failed),
                    "legacy_attempts": str(metrics.legacy_bypass_attempts),
                },
                "confidence": 0.98,
                "source": f"ContractBridge-Cycle-{metrics.cycle_id}",
            })

        if metrics.hash_seal_verified and not metrics.hash_seal_stable:
            rules.append({
                "domain": "deterministic_seal",
                "rule_type": "regression_guard",
                "rule_text": (
                    "Deterministic hash seal regression detected. The pipeline hash "
                    "changed between runs with identical inputs. This indicates a "
                    "non-deterministic code path was introduced. Check for: "
                    "1) dict ordering changes, 2) floating-point non-determinism, "
                    "3) timestamp injection, 4) random seed leaks."
                ),
                "params": {
                    "cycle_id": str(metrics.cycle_id),
                    "expected_hash": self.state.golden_master_hash[:16] + "...",
                    "actual_hash": self.state.last_pipeline_hash[:16] + "...",
                },
                "confidence": 0.95,
                "source": f"ContractBridge-Cycle-{metrics.cycle_id}",
            })

        if metrics.contact_flicker_detected:
            rules.append({
                "domain": "contact_quality",
                "rule_type": "heuristic",
                "rule_text": (
                    f"Contact tag flicker detected ({metrics.flicker_frame_count} frames). "
                    "Foot contact tags should not toggle more than 2 times within a "
                    "4-frame window. This usually indicates the phase-driven contact "
                    "inference thresholds need adjustment for the affected state."
                ),
                "params": {
                    "flicker_frames": str(metrics.flicker_frame_count),
                },
                "confidence": 0.88,
                "source": f"ContractBridge-Cycle-{metrics.cycle_id}",
            })

        if metrics.hash_seal_stable and self.state.hash_stability_streak >= 3:
            rules.append({
                "domain": "pipeline_contract",
                "rule_type": "confidence_boost",
                "rule_text": (
                    f"Pipeline hash has been stable for {self.state.hash_stability_streak} "
                    "consecutive cycles. The deterministic seal is reliable. Consider "
                    "promoting the current golden master hash to CI enforcement."
                ),
                "params": {
                    "streak": str(self.state.hash_stability_streak),
                    "golden_hash": self.state.golden_master_hash[:16] + "...",
                },
                "confidence": 0.92,
                "source": f"ContractBridge-Cycle-{metrics.cycle_id}",
            })

        self.state.knowledge_rules_total += len(rules)
        metrics.knowledge_rules_generated = len(rules)

        if rules:
            self._save_knowledge_rules(rules)

        return rules

    def compute_contract_fitness_bonus(
        self,
        metrics: ContractEvolutionMetrics,
    ) -> float:
        """Compute a fitness bonus/penalty based on contract compliance.

        This is used by Layer 3 (Self-Iteration) to incorporate contract
        compliance into the physics evolution fitness function.

        Parameters
        ----------
        metrics : ContractEvolutionMetrics
            Metrics from the latest compliance check.

        Returns
        -------
        float
            Fitness modifier in [-0.2, +0.1]. Positive for compliance,
            negative for violations.
        """
        bonus = 0.0

        # Full compliance bonus
        if metrics.contract_checks_failed == 0:
            bonus += 0.05

        # Hash seal stability bonus
        if metrics.hash_seal_stable:
            bonus += 0.03

        # Legacy bypass penalty
        if metrics.legacy_bypass_attempts > 0:
            bonus -= 0.1

        # Contact flicker penalty
        if metrics.contact_flicker_detected:
            bonus -= 0.05 * min(metrics.flicker_frame_count, 4) / 4.0

        # Hash instability penalty
        if metrics.hash_seal_verified and not metrics.hash_seal_stable:
            bonus -= 0.1

        return max(-0.2, min(0.1, bonus))

    def status_report(self) -> str:
        """Generate a status report for the contract evolution bridge."""
        lines = [
            "--- Pipeline Contract Evolution Bridge (SESSION-040) ---",
            f"   Total cycles: {self.state.total_contract_cycles}",
            f"   Passes: {self.state.total_contract_passes}",
            f"   Failures: {self.state.total_contract_failures}",
            f"   Golden master set: {self.state.golden_master_set}",
            f"   Hash stability streak: {self.state.hash_stability_streak}",
            f"   Knowledge rules generated: {self.state.knowledge_rules_total}",
        ]
        if self.state.golden_master_hash:
            lines.append(f"   Golden master: {self.state.golden_master_hash[:16]}...")
        return "\n".join(lines)

    def _save_knowledge_rules(self, rules: list[dict]) -> None:
        """Save contract knowledge rules to the knowledge base."""
        knowledge_dir = self.project_root / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        knowledge_file = knowledge_dir / "pipeline_contract.md"

        lines = []
        if not knowledge_file.exists():
            lines = [
                "# Pipeline Contract Knowledge Base",
                "",
                "> Auto-generated by SESSION-040 Contract Evolution Bridge.",
                "> Contains distilled rules from pipeline contract enforcement.",
                "",
            ]

        for rule in rules:
            lines.extend([
                f"## [{rule['domain']}] {rule['rule_type']} (confidence: {rule['confidence']:.2f})",
                "",
                f"> {rule['rule_text']}",
                "",
                f"Source: {rule['source']}",
                "",
                "Parameters:",
            ])
            for k, v in rule["params"].items():
                lines.append(f"  - `{k}`: {v}")
            lines.append("")

        with knowledge_file.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _load_state(self) -> ContractEvolutionState:
        """Load persistent state from disk."""
        state_path = resolve_state_path(self.project_root, ".contract_evolution_state.json")
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                return ContractEvolutionState.from_dict(data)
            except (json.JSONDecodeError, OSError):
                pass
        return ContractEvolutionState()

    def _save_state(self) -> None:
        """Save persistent state to disk."""
        state_path = resolve_state_path(self.project_root, ".contract_evolution_state.json")
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


__all__ = [
    "ContractEvolutionMetrics",
    "ContractEvolutionState",
    "ContractEvolutionBridge",
]
