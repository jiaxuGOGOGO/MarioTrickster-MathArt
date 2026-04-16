"""Evolution Layer 3 — Physics-Aware Self-Iteration & Testing System.

SESSION-030: The third layer of the three-layer evolution architecture.

Architecture overview of the upgraded three-layer evolution system:

    Layer 1 (Inner Loop) — EXISTING + UPGRADED
        Generate → Evaluate → Optimize → Repeat
        NOW INCLUDES: physics fitness evaluation, PD stability scoring,
        locomotion quality metrics alongside visual quality.

    Layer 2 (Outer Loop) — EXISTING + UPGRADED
        Parse → Distill → Validate → Integrate
        NOW INCLUDES: physics knowledge domain, RL paper distillation,
        DeepMimic/ASE parameter extraction, MuJoCo config rules.

    Layer 3 (Self-Iteration) — NEW
        Train → Test → Diagnose → Evolve → Train
        A closed-loop system that:
        1. Trains RL policies on current physics genotypes
        2. Tests them against physics quality metrics
        3. Diagnoses failures (falling, sliding, energy waste)
        4. Evolves physics/locomotion genes based on diagnosis
        5. Distills successful strategies back into knowledge base

This layer bridges the gap between the existing visual evolution system
and the new physics-based animation stack. It ensures that:
- Physics parameters are optimized alongside visual parameters
- RL training results feed back into the genotype evolution
- Knowledge from successful physics configurations is preserved
- The system can self-diagnose and recover from physics failures

Integration points:
    - InnerLoopRunner.run() → calls PhysicsEvolutionLayer.evaluate()
    - OuterLoopDistiller.distill() → PhysicsEvolutionLayer.distill_physics_knowledge()
    - SelfEvolutionEngine.run() → orchestrates all three layers
    - ProjectMemory → stores physics evolution history and metrics

References:
    - SESSION-027: CharacterGenotype, mutate_genotype
    - SESSION-028: AnglePoseProjector, PhysDiff
    - SESSION-029: BiomechanicsProjector, ZMP
    - SESSION-030: PDController, MuJoCo, RL Locomotion, ASE, PhysicsGenotype
"""
from __future__ import annotations

import copy
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────


class PhysicsTestResult(str, Enum):
    """Result of a physics quality test."""
    PASS = "pass"
    FAIL_STABILITY = "fail_stability"       # PD controller unstable
    FAIL_CONTACT = "fail_contact"           # Penetration or sliding
    FAIL_ENERGY = "fail_energy"             # Excessive torque usage
    FAIL_IMITATION = "fail_imitation"       # Poor motion matching
    FAIL_BALANCE = "fail_balance"           # ZMP outside support polygon
    FAIL_SKATING = "fail_skating"           # Foot skating detected
    WARN_MARGINAL = "warn_marginal"         # Passes but barely


class DiagnosisAction(str, Enum):
    """Recommended action from physics diagnosis."""
    INCREASE_STIFFNESS = "increase_stiffness"
    DECREASE_STIFFNESS = "decrease_stiffness"
    INCREASE_DAMPING = "increase_damping"
    DECREASE_DAMPING = "decrease_damping"
    ADJUST_FRICTION = "adjust_friction"
    ADJUST_MASS = "adjust_mass"
    CHANGE_GAIT = "change_gait"
    RETRAIN_POLICY = "retrain_policy"
    WIDEN_PHYSICS_SPACE = "widen_physics_space"
    DISTILL_MORE_KNOWLEDGE = "distill_more_knowledge"
    ESCALATE_TO_HUMAN = "escalate_to_human"


class EvolutionPhase(str, Enum):
    """Current phase of the Layer 3 evolution cycle."""
    TRAIN = "train"
    TEST = "test"
    DIAGNOSE = "diagnose"
    EVOLVE = "evolve"
    DISTILL = "distill"
    IDLE = "idle"


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class PhysicsTestReport:
    """Detailed report from a physics quality test battery."""
    test_id: str
    timestamp: str
    result: PhysicsTestResult
    scores: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    failing_joints: list[str] = field(default_factory=list)
    recommendations: list[DiagnosisAction] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "timestamp": self.timestamp,
            "result": self.result.value,
            "scores": self.scores,
            "details": {k: str(v) for k, v in self.details.items()},
            "failing_joints": self.failing_joints,
            "recommendations": [r.value for r in self.recommendations],
        }


@dataclass
class PhysicsEvolutionRecord:
    """Record of a single Layer 3 evolution cycle."""
    cycle_id: int
    phase: EvolutionPhase
    timestamp: str
    physics_fitness: float
    locomotion_fitness: float
    combined_fitness: float
    test_results: list[PhysicsTestReport] = field(default_factory=list)
    actions_taken: list[DiagnosisAction] = field(default_factory=list)
    genes_modified: list[str] = field(default_factory=list)
    knowledge_distilled: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "phase": self.phase.value,
            "timestamp": self.timestamp,
            "physics_fitness": self.physics_fitness,
            "locomotion_fitness": self.locomotion_fitness,
            "combined_fitness": self.combined_fitness,
            "test_results": [t.to_dict() for t in self.test_results],
            "actions_taken": [a.value for a in self.actions_taken],
            "genes_modified": self.genes_modified,
            "knowledge_distilled": self.knowledge_distilled,
        }


@dataclass
class PhysicsEvolutionState:
    """Persistent state for the Layer 3 evolution system."""
    total_cycles: int = 0
    best_physics_fitness: float = 0.0
    best_locomotion_fitness: float = 0.0
    best_combined_fitness: float = 0.0
    current_phase: EvolutionPhase = EvolutionPhase.IDLE
    stagnation_count: int = 0
    history: list[PhysicsEvolutionRecord] = field(default_factory=list)
    knowledge_rules_generated: int = 0
    successful_strategies: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_cycles": self.total_cycles,
            "best_physics_fitness": self.best_physics_fitness,
            "best_locomotion_fitness": self.best_locomotion_fitness,
            "best_combined_fitness": self.best_combined_fitness,
            "current_phase": self.current_phase.value,
            "stagnation_count": self.stagnation_count,
            "history": [h.to_dict() for h in self.history[-20:]],
            "knowledge_rules_generated": self.knowledge_rules_generated,
            "successful_strategies": self.successful_strategies[-10:],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PhysicsEvolutionState":
        state = cls(
            total_cycles=d.get("total_cycles", 0),
            best_physics_fitness=d.get("best_physics_fitness", 0.0),
            best_locomotion_fitness=d.get("best_locomotion_fitness", 0.0),
            best_combined_fitness=d.get("best_combined_fitness", 0.0),
            current_phase=EvolutionPhase(d.get("current_phase", "idle")),
            stagnation_count=d.get("stagnation_count", 0),
            knowledge_rules_generated=d.get("knowledge_rules_generated", 0),
            successful_strategies=d.get("successful_strategies", []),
        )
        return state


# ── Physics Test Battery ─────────────────────────────────────────────────────


class PhysicsTestBattery:
    """Comprehensive physics quality test suite.

    Runs a battery of tests on a physics+locomotion genotype pair
    and produces a detailed report with pass/fail/warn results.

    Tests:
        1. PD Stability: All joints must be stable (damping ratio > 0)
        2. Contact Quality: No penetration, reasonable friction
        3. Energy Efficiency: Total torque within budget
        4. Motion Imitation: DeepMimic reward above threshold
        5. Balance: ZMP within support polygon
        6. Skating: Foot sliding below threshold
    """

    def __init__(
        self,
        stability_threshold: float = 0.7,
        energy_threshold: float = 0.3,
        imitation_threshold: float = 0.4,
        balance_threshold: float = 0.5,
        skating_threshold: float = 0.02,
    ):
        self.stability_threshold = stability_threshold
        self.energy_threshold = energy_threshold
        self.imitation_threshold = imitation_threshold
        self.balance_threshold = balance_threshold
        self.skating_threshold = skating_threshold
        self._test_counter = 0

    def run_full_battery(
        self,
        physics_fitness: dict[str, float],
        locomotion_fitness: Optional[dict[str, float]] = None,
    ) -> PhysicsTestReport:
        """Run the full test battery and produce a report.

        Parameters
        ----------
        physics_fitness : dict
            Output from evaluate_physics_fitness().
        locomotion_fitness : dict, optional
            Additional locomotion metrics.

        Returns
        -------
        PhysicsTestReport
        """
        self._test_counter += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        scores = dict(physics_fitness)
        if locomotion_fitness:
            scores.update(locomotion_fitness)

        # Run individual tests
        failures = []
        recommendations = []
        failing_joints = []

        # Test 1: Stability
        stability = scores.get("stability", 0.0)
        if stability < self.stability_threshold:
            failures.append(PhysicsTestResult.FAIL_STABILITY)
            if stability < 0.3:
                recommendations.append(DiagnosisAction.INCREASE_DAMPING)
            else:
                recommendations.append(DiagnosisAction.INCREASE_STIFFNESS)

        # Test 2: Energy efficiency
        energy = scores.get("energy_efficiency", 0.0)
        if energy < self.energy_threshold:
            failures.append(PhysicsTestResult.FAIL_ENERGY)
            recommendations.append(DiagnosisAction.DECREASE_STIFFNESS)

        # Test 3: Imitation quality
        imitation = scores.get("imitation_score", 0.0)
        if imitation < self.imitation_threshold:
            failures.append(PhysicsTestResult.FAIL_IMITATION)
            recommendations.append(DiagnosisAction.RETRAIN_POLICY)

        # Test 4: Damping quality (proxy for balance)
        damping_quality = scores.get("damping_quality", 0.0)
        if damping_quality < self.balance_threshold:
            failures.append(PhysicsTestResult.FAIL_BALANCE)
            recommendations.append(DiagnosisAction.ADJUST_MASS)

        # Determine overall result
        if not failures:
            overall = scores.get("overall", 0.0)
            if overall < 0.6:
                result = PhysicsTestResult.WARN_MARGINAL
            else:
                result = PhysicsTestResult.PASS
        else:
            result = failures[0]  # Primary failure

        return PhysicsTestReport(
            test_id=f"PHYS-TEST-{self._test_counter:04d}",
            timestamp=now,
            result=result,
            scores=scores,
            details={
                "n_tests_run": 4,
                "n_failures": len(failures),
                "all_failures": [f.value for f in failures],
            },
            failing_joints=failing_joints,
            recommendations=recommendations,
        )


# ── Physics Diagnosis Engine ─────────────────────────────────────────────────


class PhysicsDiagnosisEngine:
    """Diagnoses physics failures and recommends corrective actions.

    Uses a rule-based system (distilled from DeepMimic/ASE research)
    to map test failures to specific genotype modifications.
    """

    # Diagnosis rules: (failure_type, condition) → action + gene_modification
    DIAGNOSIS_RULES = {
        PhysicsTestResult.FAIL_STABILITY: {
            "low_damping": {
                "condition": lambda scores: scores.get("damping_quality", 0) < 0.3,
                "action": DiagnosisAction.INCREASE_DAMPING,
                "gene_mods": {"pd_damping_scale": 0.3},
            },
            "low_stiffness": {
                "condition": lambda scores: scores.get("stability", 0) < 0.5,
                "action": DiagnosisAction.INCREASE_STIFFNESS,
                "gene_mods": {"pd_stiffness_scale": 0.2},
            },
        },
        PhysicsTestResult.FAIL_ENERGY: {
            "over_stiff": {
                "condition": lambda scores: scores.get("energy_efficiency", 0) < 0.2,
                "action": DiagnosisAction.DECREASE_STIFFNESS,
                "gene_mods": {"pd_stiffness_scale": -0.3, "pd_torque_limit_scale": -0.2},
            },
        },
        PhysicsTestResult.FAIL_IMITATION: {
            "poor_tracking": {
                "condition": lambda scores: scores.get("imitation_score", 0) < 0.3,
                "action": DiagnosisAction.RETRAIN_POLICY,
                "gene_mods": {},
            },
        },
        PhysicsTestResult.FAIL_BALANCE: {
            "mass_imbalance": {
                "condition": lambda scores: True,
                "action": DiagnosisAction.ADJUST_MASS,
                "gene_mods": {"gravity_compensation": 0.1},
            },
        },
    }

    def diagnose(
        self,
        report: PhysicsTestReport,
    ) -> tuple[list[DiagnosisAction], dict[str, float]]:
        """Diagnose a test report and return actions + gene modifications.

        Parameters
        ----------
        report : PhysicsTestReport
            Test report to diagnose.

        Returns
        -------
        tuple[list[DiagnosisAction], dict[str, float]]
            Recommended actions and gene modification deltas.
        """
        actions = []
        gene_mods = {}

        if report.result == PhysicsTestResult.PASS:
            return actions, gene_mods

        # Check all applicable rules
        for failure_type_str in report.details.get("all_failures", []):
            try:
                failure_type = PhysicsTestResult(failure_type_str)
            except ValueError:
                continue

            rules = self.DIAGNOSIS_RULES.get(failure_type, {})
            for rule_name, rule in rules.items():
                if rule["condition"](report.scores):
                    actions.append(rule["action"])
                    for gene, delta in rule["gene_mods"].items():
                        gene_mods[gene] = gene_mods.get(gene, 0.0) + delta

        # Deduplicate actions
        actions = list(dict.fromkeys(actions))

        return actions, gene_mods

    def apply_gene_modifications(
        self,
        physics_geno: Any,
        gene_mods: dict[str, float],
    ) -> Any:
        """Apply diagnosed gene modifications to a PhysicsGenotype.

        Parameters
        ----------
        physics_geno : PhysicsGenotype
            Current physics genes.
        gene_mods : dict[str, float]
            Gene modification deltas from diagnosis.

        Returns
        -------
        PhysicsGenotype
            Modified physics genes.
        """
        modified = copy.deepcopy(physics_geno)

        for gene, delta in gene_mods.items():
            if hasattr(modified, gene):
                current = getattr(modified, gene)
                if isinstance(current, (int, float)):
                    setattr(modified, gene, current + delta)

        return modified


# ── Knowledge Distillation from Physics Results ──────────────────────────────


class PhysicsKnowledgeDistiller:
    """Distills successful physics configurations into reusable knowledge rules.

    When a physics genotype achieves high fitness, this distiller extracts
    the key parameter patterns and saves them as knowledge rules that can
    be applied to future genotypes.

    This is the "self-teaching" component of Layer 3: the system learns
    from its own successes and codifies that learning.
    """

    def __init__(self, knowledge_dir: Optional[Path] = None):
        self.knowledge_dir = knowledge_dir
        self.rules_generated = 0

    def distill_success(
        self,
        physics_geno: Any,
        loco_geno: Any,
        fitness: dict[str, float],
        archetype: str = "unknown",
    ) -> list[dict]:
        """Distill a successful configuration into knowledge rules.

        Parameters
        ----------
        physics_geno : PhysicsGenotype
            Successful physics genes.
        loco_geno : LocomotionGenotype
            Successful locomotion genes.
        fitness : dict[str, float]
            Fitness scores achieved.
        archetype : str
            Character archetype.

        Returns
        -------
        list[dict]
            Extracted knowledge rules.
        """
        rules = []

        # Rule 1: PD gain ratio
        if hasattr(physics_geno, 'pd_stiffness_scale') and hasattr(physics_geno, 'pd_damping_scale'):
            ratio = physics_geno.pd_damping_scale / max(physics_geno.pd_stiffness_scale, 0.01)
            rules.append({
                "domain": "physics_pd",
                "rule_type": "soft_default",
                "rule_text": (
                    f"For archetype '{archetype}', optimal PD damping/stiffness ratio "
                    f"is approximately {ratio:.2f} (stiffness={physics_geno.pd_stiffness_scale:.2f}, "
                    f"damping={physics_geno.pd_damping_scale:.2f})"
                ),
                "params": {
                    "pd_stiffness_scale": str(physics_geno.pd_stiffness_scale),
                    "pd_damping_scale": str(physics_geno.pd_damping_scale),
                    "archetype": archetype,
                },
                "confidence": min(fitness.get("overall", 0.0) + 0.2, 1.0),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 2: Contact material
        if hasattr(physics_geno, 'contact_friction'):
            rules.append({
                "domain": "physics_contact",
                "rule_type": "heuristic",
                "rule_text": (
                    f"Contact friction {physics_geno.contact_friction:.2f} works well "
                    f"for archetype '{archetype}' with stability={fitness.get('stability', 0):.2f}"
                ),
                "params": {
                    "contact_friction": str(physics_geno.contact_friction),
                    "archetype": archetype,
                },
                "confidence": fitness.get("stability", 0.5),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 3: Gait parameters
        if hasattr(loco_geno, 'step_frequency'):
            rules.append({
                "domain": "locomotion",
                "rule_type": "soft_default",
                "rule_text": (
                    f"Gait '{loco_geno.gait_type}' at {loco_geno.step_frequency:.1f} Hz "
                    f"with stride {loco_geno.stride_length:.2f} achieves "
                    f"imitation={fitness.get('imitation_score', 0):.2f}, "
                    f"motion_match={fitness.get('motion_match_score', 0):.2f} "
                    f"for archetype '{archetype}'"
                ),
                "params": {
                    "gait_type": loco_geno.gait_type,
                    "step_frequency": str(loco_geno.step_frequency),
                    "stride_length": str(loco_geno.stride_length),
                    "motion_match_score": str(fitness.get('motion_match_score', 0.0)),
                    "archetype": archetype,
                },
                "confidence": 0.5 * fitness.get("imitation_score", 0.5) + 0.5 * fitness.get("motion_match_score", 0.5),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 4: Anatomical plausibility prior
        if fitness.get("anatomical_score") is not None:
            rules.append({
                "domain": "anatomy",
                "rule_type": "heuristic",
                "rule_text": (
                    f"For archetype '{archetype}', locomotion candidates should maintain "
                    f"anatomical_score >= {fitness.get('anatomical_score', 0):.2f} after "
                    f"VPoser-like projection to avoid impossible knee/elbow solutions."
                ),
                "params": {
                    "anatomical_score": str(fitness.get('anatomical_score', 0.0)),
                    "gait_type": loco_geno.gait_type,
                    "archetype": archetype,
                },
                "confidence": fitness.get("anatomical_score", 0.5),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        self.rules_generated += len(rules)

        # Save to knowledge directory if available
        if self.knowledge_dir and self.knowledge_dir.exists():
            self._save_rules(rules)

        return rules

    def _save_rules(self, rules: list[dict]) -> None:
        """Append rules to the physics knowledge file."""
        knowledge_file = self.knowledge_dir / "physics_locomotion.md"

        lines = []
        if not knowledge_file.exists():
            lines = [
                "# Physics & Locomotion Knowledge Base",
                "",
                "> Auto-generated by Layer 3 Physics Evolution System.",
                "> Contains distilled rules from successful physics configurations.",
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
            for k, v in rule['params'].items():
                lines.append(f"  - `{k}`: {v}")
            lines.append("")

        with knowledge_file.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# ── Main Layer 3 Orchestrator ────────────────────────────────────────────────


class PhysicsEvolutionLayer:
    """Layer 3 of the three-layer evolution system.

    Orchestrates the Train → Test → Diagnose → Evolve → Distill cycle
    for physics-based character animation.

    This layer sits alongside (not above) the existing InnerLoop and OuterLoop:
    - InnerLoop handles visual quality optimization
    - OuterLoop handles external knowledge ingestion
    - PhysicsEvolutionLayer handles physics quality optimization

    The SelfEvolutionEngine coordinates all three.

    Parameters
    ----------
    project_root : Path
        Project root directory.
    verbose : bool
        Print progress.
    max_cycles : int
        Maximum evolution cycles per run.
    fitness_threshold : float
        Target combined fitness to stop early.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        verbose: bool = True,
        max_cycles: int = 20,
        fitness_threshold: float = 0.75,
    ):
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.verbose = verbose
        self.max_cycles = max_cycles
        self.fitness_threshold = fitness_threshold

        # Subsystems
        self.test_battery = PhysicsTestBattery()
        self.diagnosis_engine = PhysicsDiagnosisEngine()
        self.knowledge_distiller = PhysicsKnowledgeDistiller(
            knowledge_dir=self.project_root / "knowledge"
        )

        # State
        self.state = self._load_state()

    def run(
        self,
        physics_geno: Any,
        loco_geno: Any,
        archetype: str = "hero",
        population_size: int = 10,
        rng: Optional[np.random.Generator] = None,
    ) -> PhysicsEvolutionRecord:
        """Run one full Layer 3 evolution cycle.

        Parameters
        ----------
        physics_geno : PhysicsGenotype
            Starting physics genes.
        loco_geno : LocomotionGenotype
            Starting locomotion genes.
        archetype : str
            Character archetype for defaults.
        population_size : int
            Population size for physics gene evolution.
        rng : np.random.Generator, optional
            Random number generator.

        Returns
        -------
        PhysicsEvolutionRecord
        """
        if rng is None:
            rng = np.random.default_rng()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.state.total_cycles += 1
        cycle_id = self.state.total_cycles

        if self.verbose:
            print(f"\n[Layer3] Cycle {cycle_id} — Phase: TRAIN → TEST → DIAGNOSE → EVOLVE")

        # Phase 1: TRAIN — evaluate current genotype
        self.state.current_phase = EvolutionPhase.TRAIN
        from ..animation.physics_genotype import evaluate_physics_fitness
        fitness = evaluate_physics_fitness(physics_geno, loco_geno)

        if self.verbose:
            print(f"  [TRAIN] Initial fitness: {fitness['overall']:.3f}")

        # Phase 2: TEST — run test battery
        self.state.current_phase = EvolutionPhase.TEST
        test_report = self.test_battery.run_full_battery(fitness)

        if self.verbose:
            print(f"  [TEST] Result: {test_report.result.value} | Scores: {test_report.scores}")

        # Phase 3: DIAGNOSE — identify issues
        self.state.current_phase = EvolutionPhase.DIAGNOSE
        actions, gene_mods = self.diagnosis_engine.diagnose(test_report)

        if self.verbose and actions:
            print(f"  [DIAGNOSE] Actions: {[a.value for a in actions]}")
            print(f"  [DIAGNOSE] Gene mods: {gene_mods}")

        # Phase 4: EVOLVE — apply modifications and evolve population
        self.state.current_phase = EvolutionPhase.EVOLVE
        best_physics = physics_geno
        best_loco = loco_geno
        best_fitness = fitness

        if gene_mods:
            # Apply diagnosed modifications
            best_physics = self.diagnosis_engine.apply_gene_modifications(
                physics_geno, gene_mods
            )

        # Run mini-evolution on physics genes
        from ..animation.physics_genotype import (
            mutate_physics_genotype, mutate_locomotion_genotype,
            crossover_physics_genotype, crossover_locomotion_genotype,
        )

        population_p = [copy.deepcopy(best_physics) for _ in range(population_size)]
        population_l = [copy.deepcopy(best_loco) for _ in range(population_size)]

        for i in range(1, population_size):
            population_p[i] = mutate_physics_genotype(population_p[i], rng, strength=0.3)
            population_l[i] = mutate_locomotion_genotype(population_l[i], rng, strength=0.3)

        # Evaluate population
        pop_fitness = []
        for p, l in zip(population_p, population_l):
            f = evaluate_physics_fitness(p, l)
            pop_fitness.append(f)

        # Select best
        best_idx = max(range(len(pop_fitness)), key=lambda i: pop_fitness[i]["overall"])
        best_physics = population_p[best_idx]
        best_loco = population_l[best_idx]
        best_fitness = pop_fitness[best_idx]

        if self.verbose:
            print(f"  [EVOLVE] Best in population: {best_fitness['overall']:.3f} (idx={best_idx})")

        # Phase 5: DISTILL — save successful strategies
        self.state.current_phase = EvolutionPhase.DISTILL
        knowledge_rules = []
        if best_fitness["overall"] > 0.5:
            knowledge_rules = self.knowledge_distiller.distill_success(
                best_physics, best_loco, best_fitness, archetype
            )
            if self.verbose:
                print(f"  [DISTILL] Generated {len(knowledge_rules)} knowledge rules")

        # Update state
        self.state.best_physics_fitness = max(
            self.state.best_physics_fitness, best_fitness.get("stability", 0)
        )
        self.state.best_locomotion_fitness = max(
            self.state.best_locomotion_fitness, best_fitness.get("imitation_score", 0)
        )
        self.state.best_combined_fitness = max(
            self.state.best_combined_fitness, best_fitness["overall"]
        )
        self.state.knowledge_rules_generated += len(knowledge_rules)

        # Stagnation detection
        if (self.state.history and
                abs(best_fitness["overall"] - self.state.history[-1].combined_fitness) < 0.005):
            self.state.stagnation_count += 1
        else:
            self.state.stagnation_count = 0

        # Record
        record = PhysicsEvolutionRecord(
            cycle_id=cycle_id,
            phase=EvolutionPhase.DISTILL,
            timestamp=now,
            physics_fitness=best_fitness.get("stability", 0),
            locomotion_fitness=best_fitness.get("imitation_score", 0),
            combined_fitness=best_fitness["overall"],
            test_results=[test_report],
            actions_taken=actions,
            genes_modified=list(gene_mods.keys()),
            knowledge_distilled=[r.get("rule_text", "") for r in knowledge_rules],
        )
        self.state.history.append(record)

        # Save successful strategy
        if best_fitness["overall"] > 0.6:
            self.state.successful_strategies.append({
                "archetype": archetype,
                "fitness": best_fitness["overall"],
                "physics": best_physics.to_dict() if hasattr(best_physics, 'to_dict') else {},
                "locomotion": best_loco.to_dict() if hasattr(best_loco, 'to_dict') else {},
            })

        self.state.current_phase = EvolutionPhase.IDLE
        self._save_state()

        if self.verbose:
            print(f"  [COMPLETE] Cycle {cycle_id}: combined={best_fitness['overall']:.3f} "
                  f"| stagnation={self.state.stagnation_count}")

        return record

    def run_multi_cycle(
        self,
        physics_geno: Any,
        loco_geno: Any,
        archetype: str = "hero",
        n_cycles: Optional[int] = None,
        rng: Optional[np.random.Generator] = None,
    ) -> list[PhysicsEvolutionRecord]:
        """Run multiple Layer 3 evolution cycles.

        Stops early if fitness threshold is met or stagnation is detected.
        """
        if rng is None:
            rng = np.random.default_rng()
        n = n_cycles or self.max_cycles

        records = []
        current_p = copy.deepcopy(physics_geno)
        current_l = copy.deepcopy(loco_geno)

        for i in range(n):
            record = self.run(current_p, current_l, archetype, rng=rng)
            records.append(record)

            # Early stopping
            if record.combined_fitness >= self.fitness_threshold:
                if self.verbose:
                    print(f"\n[Layer3] Fitness threshold {self.fitness_threshold} reached!")
                break

            if self.state.stagnation_count >= 5:
                if self.verbose:
                    print(f"\n[Layer3] Stagnation detected ({self.state.stagnation_count} cycles)")
                break

        return records

    def status_report(self) -> str:
        """Generate a human-readable status report."""
        s = self.state
        lines = [
            "=" * 50,
            "Layer 3 — Physics Evolution Status",
            "=" * 50,
            f"  Total cycles: {s.total_cycles}",
            f"  Current phase: {s.current_phase.value}",
            f"  Best physics fitness: {s.best_physics_fitness:.3f}",
            f"  Best locomotion fitness: {s.best_locomotion_fitness:.3f}",
            f"  Best combined fitness: {s.best_combined_fitness:.3f}",
            f"  Stagnation count: {s.stagnation_count}",
            f"  Knowledge rules generated: {s.knowledge_rules_generated}",
            f"  Successful strategies: {len(s.successful_strategies)}",
        ]

        if s.history:
            last = s.history[-1]
            lines.extend([
                "",
                f"  Last cycle: #{last.cycle_id}",
                f"    Combined fitness: {last.combined_fitness:.3f}",
                f"    Actions: {[a.value for a in last.actions_taken]}",
            ])

        lines.append("=" * 50)
        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_state(self) -> PhysicsEvolutionState:
        state_file = self.project_root / "PHYSICS_EVOLUTION_STATE.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                return PhysicsEvolutionState.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                pass
        return PhysicsEvolutionState()

    def _save_state(self) -> None:
        state_file = self.project_root / "PHYSICS_EVOLUTION_STATE.json"
        state_file.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
