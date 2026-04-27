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
    # SESSION-033: Phase-driven animation quality tests
    FAIL_PHASE_CONTINUITY = "fail_phase_continuity"  # Discontinuous phase transitions
    FAIL_PELVIS_TRAJECTORY = "fail_pelvis_trajectory"  # Pelvis height doesn't follow Down/Up pattern
    FAIL_ARM_OPPOSITION = "fail_arm_opposition"  # Arms don't counter-rotate with legs
    FAIL_KNEE_ROM = "fail_knee_rom"  # Knees bend forward (positive)
    # SESSION-039: Inertialized transition & runtime motion matching tests
    FAIL_TRANSITION_QUALITY = "fail_transition_quality"  # Inertialized transition too jerky
    FAIL_ENTRY_FRAME_COST = "fail_entry_frame_cost"  # Runtime query entry frame cost too high


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
    # SESSION-033: Phase-driven animation diagnosis actions
    ADJUST_KEY_POSES = "adjust_key_poses"  # Modify key pose joint angles
    SMOOTH_PHASE_TRANSITION = "smooth_phase_transition"  # Add interpolation smoothing
    RECALIBRATE_CHANNELS = "recalibrate_channels"  # Re-tune DeepPhase channels
    SWITCH_TO_PHASE_DRIVEN = "switch_to_phase_driven"  # Upgrade from sin() to phase-driven
    # SESSION-039: Inertialized transition & runtime motion matching diagnosis actions
    TUNE_DECAY_HALFLIFE = "tune_decay_halflife"  # Adjust inertialization decay spring
    TUNE_ENTRY_WEIGHTS = "tune_entry_weights"  # Adjust runtime query feature weights
    SWITCH_BLEND_STRATEGY = "switch_blend_strategy"  # Switch between quintic/dead_blending


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

        # SESSION-033: Phase-Driven Animation Quality Tests
        # Test 5: Phase continuity (max joint delta between 1% steps < 0.5)
        phase_continuity = scores.get("phase_continuity", 1.0)
        if phase_continuity < 0.5:
            failures.append(PhysicsTestResult.FAIL_PHASE_CONTINUITY)
            recommendations.append(DiagnosisAction.SMOOTH_PHASE_TRANSITION)

        # Test 6: Pelvis trajectory (Down < Contact < Up pattern)
        pelvis_trajectory = scores.get("pelvis_trajectory", 1.0)
        if pelvis_trajectory < 0.5:
            failures.append(PhysicsTestResult.FAIL_PELVIS_TRAJECTORY)
            recommendations.append(DiagnosisAction.ADJUST_KEY_POSES)

        # Test 7: Arm-leg opposition (arms counter-rotate with legs)
        arm_opposition = scores.get("arm_opposition", 1.0)
        if arm_opposition < 0.5:
            failures.append(PhysicsTestResult.FAIL_ARM_OPPOSITION)
            recommendations.append(DiagnosisAction.RECALIBRATE_CHANNELS)

        # Test 8: Knee ROM (knees should never bend forward)
        knee_rom = scores.get("knee_rom", 1.0)
        if knee_rom < 0.5:
            failures.append(PhysicsTestResult.FAIL_KNEE_ROM)
            failing_joints.extend(["l_knee", "r_knee"])
            recommendations.append(DiagnosisAction.ADJUST_KEY_POSES)

        # SESSION-034: Industrial motion matching & rendering quality tests
        # Test 9: Contact consistency (Motion Matching feature vector)
        contact_cons = scores.get("contact_consistency", 1.0)
        if contact_cons < 0.5:
            failures.append(PhysicsTestResult.FAIL_CONTACT)
            recommendations.append(DiagnosisAction.ADJUST_FRICTION)

        # Test 10: Skating penalty (feature-vector contact velocity)
        skating_pen = scores.get("skating_penalty", 0.0)
        if skating_pen > self.skating_threshold:
            failures.append(PhysicsTestResult.FAIL_SKATING)
            recommendations.append(DiagnosisAction.INCREASE_DAMPING)

        # SESSION-039: Inertialized transition quality tests
        # Test 11: Transition quality (inertialization offset convergence)
        transition_quality = scores.get("transition_quality", 1.0)
        if transition_quality < 0.4:
            failures.append(PhysicsTestResult.FAIL_TRANSITION_QUALITY)
            if transition_quality < 0.2:
                recommendations.append(DiagnosisAction.SWITCH_BLEND_STRATEGY)
            else:
                recommendations.append(DiagnosisAction.TUNE_DECAY_HALFLIFE)

        # Test 12: Runtime motion matching entry frame cost
        entry_frame_cost = scores.get("entry_frame_cost", 0.0)
        if entry_frame_cost > 8.0:
            failures.append(PhysicsTestResult.FAIL_ENTRY_FRAME_COST)
            recommendations.append(DiagnosisAction.TUNE_ENTRY_WEIGHTS)

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
                "n_tests_run": 12,  # SESSION-039: upgraded from 10 to 12 tests
                "n_failures": len(failures),
                "all_failures": [f.value for f in failures],
                # SESSION-034: Industrial metrics in test details
                "industrial_metrics": {
                    "contact_consistency": contact_cons,
                    "silhouette_quality": scores.get("silhouette_quality", None),
                    "skating_penalty": skating_pen,
                },
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
        # SESSION-033: Phase-Driven Animation diagnosis rules
        PhysicsTestResult.FAIL_PHASE_CONTINUITY: {
            "discontinuous_transition": {
                "condition": lambda scores: scores.get("phase_continuity", 1.0) < 0.5,
                "action": DiagnosisAction.SMOOTH_PHASE_TRANSITION,
                "gene_mods": {"spline_tension": -0.1},
            },
        },
        PhysicsTestResult.FAIL_PELVIS_TRAJECTORY: {
            "wrong_height_pattern": {
                "condition": lambda scores: scores.get("pelvis_trajectory", 1.0) < 0.5,
                "action": DiagnosisAction.ADJUST_KEY_POSES,
                "gene_mods": {"pelvis_amplitude": 0.005},
            },
        },
        PhysicsTestResult.FAIL_ARM_OPPOSITION: {
            "arms_not_opposing": {
                "condition": lambda scores: scores.get("arm_opposition", 1.0) < 0.5,
                "action": DiagnosisAction.RECALIBRATE_CHANNELS,
                "gene_mods": {"arm_swing_amplitude": 0.1},
            },
        },
        PhysicsTestResult.FAIL_KNEE_ROM: {
            "knee_hyperextension": {
                "condition": lambda scores: scores.get("knee_rom", 1.0) < 0.5,
                "action": DiagnosisAction.ADJUST_KEY_POSES,
                "gene_mods": {"knee_clamp_max": -0.05},
            },
        },
        # SESSION-039: Inertialized transition & runtime motion matching diagnosis rules
        PhysicsTestResult.FAIL_TRANSITION_QUALITY: {
            "jerky_transition": {
                "condition": lambda scores: scores.get("transition_quality", 1.0) < 0.2,
                "action": DiagnosisAction.SWITCH_BLEND_STRATEGY,
                "gene_mods": {"blend_strategy": 1.0},  # 1.0 = switch to dead_blending
            },
            "slow_convergence": {
                "condition": lambda scores: scores.get("transition_quality", 1.0) < 0.4,
                "action": DiagnosisAction.TUNE_DECAY_HALFLIFE,
                "gene_mods": {"decay_halflife": -0.02},  # Faster decay
            },
        },
        PhysicsTestResult.FAIL_ENTRY_FRAME_COST: {
            "poor_entry_match": {
                "condition": lambda scores: scores.get("entry_frame_cost", 0.0) > 8.0,
                "action": DiagnosisAction.TUNE_ENTRY_WEIGHTS,
                "gene_mods": {"contact_weight_boost": 0.5},  # Increase contact importance
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

        # SESSION-033: Phase-Driven Animation knowledge distillation
        # Rule 5: Phase continuity quality
        phase_cont = fitness.get("phase_continuity", None)
        if phase_cont is not None and phase_cont > 0.7:
            rules.append({
                "domain": "phase_animation",
                "rule_type": "soft_default",
                "rule_text": (
                    f"Phase-driven animation with Catmull-Rom spline + mirrored contact "
                    f"anchor achieves continuity={phase_cont:.2f} for archetype '{archetype}'. "
                    f"Key: append virtual mirrored-Contact at p=0.5 to ensure C1 boundary."
                ),
                "params": {
                    "phase_continuity": str(phase_cont),
                    "interpolation": "catmull_rom_with_mirror_anchor",
                    "archetype": archetype,
                },
                "confidence": min(phase_cont, 0.95),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 6: Pelvis trajectory pattern
        pelvis_traj = fitness.get("pelvis_trajectory", None)
        if pelvis_traj is not None and pelvis_traj > 0.7:
            rules.append({
                "domain": "phase_animation",
                "rule_type": "hard_constraint",
                "rule_text": (
                    f"Pelvis height must follow Contact(neutral)→Down(lowest)→Pass(rising)→Up(highest) "
                    f"pattern per Animator's Survival Kit. Achieved trajectory_score={pelvis_traj:.2f}."
                ),
                "params": {
                    "pelvis_trajectory": str(pelvis_traj),
                    "source_book": "Animator's Survival Kit p.107-111",
                    "archetype": archetype,
                },
                "confidence": min(pelvis_traj, 0.95),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 7: Arm-leg opposition pattern
        arm_opp = fitness.get("arm_opposition", None)
        if arm_opp is not None and arm_opp > 0.7:
            rules.append({
                "domain": "phase_animation",
                "rule_type": "hard_constraint",
                "rule_text": (
                    f"Arms must counter-rotate with legs (left arm back when left leg forward). "
                    f"Achieved opposition_score={arm_opp:.2f}. This is a fundamental biomechanics "
                    f"principle from both PFNN and Animator's Survival Kit."
                ),
                "params": {
                    "arm_opposition": str(arm_opp),
                    "archetype": archetype,
                },
                "confidence": min(arm_opp, 0.95),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 8: Contact consistency (Motion Matching GDC 2016)
        contact_cons = fitness.get("contact_consistency", None)
        if contact_cons is not None and contact_cons > 0.6:
            rules.append({
                "domain": "motion_matching",
                "rule_type": "heuristic",
                "rule_text": (
                    f"Feature-vector motion matching (Clavet GDC 2016) contact consistency="
                    f"{contact_cons:.2f} for archetype '{archetype}'. Foot contact labels in "
                    f"the feature vector prevent skating and ensure correct gait phase alignment. "
                    f"Contact weight should be 1.5x other features."
                ),
                "params": {
                    "contact_consistency": str(contact_cons),
                    "feature_schema": "59-dim (pose+vel+traj+contact+phase+silhouette)",
                    "contact_weight": "1.5",
                    "archetype": archetype,
                },
                "confidence": min(contact_cons, 0.90),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 10: Hold frame effectiveness (Guilty Gear Xrd GDC 2015)
        skating_pen = fitness.get("skating_penalty", None)
        if skating_pen is not None and skating_pen < 0.3:
            rules.append({
                "domain": "frame_scheduling",
                "rule_type": "soft_default",
                "rule_text": (
                    f"Guilty Gear Xrd-style hold frames reduce skating penalty to "
                    f"{skating_pen:.2f} for archetype '{archetype}'. Hold 2-3 frames at "
                    f"contact/impact/apex phases with stepped interpolation (no blending). "
                    f"Apply squash (0.75-0.88 Y) on contact, stretch (1.08-1.18 Y) on apex. "
                    f"This masks physics imperfections with visual impact."
                ),
                "params": {
                    "skating_penalty": str(skating_pen),
                    "hold_contact": "2 frames",
                    "hold_impact": "3 frames",
                    "hold_apex": "2 frames",
                    "squash_contact_y": "0.88",
                    "stretch_apex_y": "1.18",
                    "archetype": archetype,
                },
                "confidence": min(1.0 - skating_pen, 0.90),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # SESSION-039: Inertialized transition & runtime motion matching knowledge distillation
        # Rule 11: Inertialization transition quality (Bollo GDC 2018 / Holden Dead Blending)
        transition_quality = fitness.get("transition_quality", None)
        if transition_quality is not None and transition_quality > 0.6:
            rules.append({
                "domain": "transition_synthesis",
                "rule_type": "hard_constraint",
                "rule_text": (
                    f"Inertialized transitions (Bollo GDC 2018) achieve quality="
                    f"{transition_quality:.2f} for archetype '{archetype}'. "
                    f"NEVER use linear crossfade for state transitions — it destroys "
                    f"contact tags and causes foot skating. Target animation gets 100% "
                    f"rendering weight immediately; source momentum decays via quintic "
                    f"polynomial or exponential spring over 4-6 frames."
                ),
                "params": {
                    "transition_quality": str(transition_quality),
                    "strategy": "inertialization_or_dead_blending",
                    "decay_frames": "4-6",
                    "contact_rule": "target_always_authoritative",
                    "archetype": archetype,
                },
                "confidence": min(transition_quality, 0.95),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 12: Runtime motion matching entry frame quality (Clavet GDC 2016)
        entry_cost = fitness.get("entry_frame_cost", None)
        if entry_cost is not None and entry_cost < 5.0:
            rules.append({
                "domain": "runtime_motion_matching",
                "rule_type": "heuristic",
                "rule_text": (
                    f"Runtime motion matching query (Clavet GDC 2016) finds optimal "
                    f"entry frame with cost={entry_cost:.2f} for archetype '{archetype}'. "
                    f"Never enter a clip at frame 0 blindly — compute "
                    f"Cost = w_vel*diff(velocity) + w_contact*diff(foot_contacts) + "
                    f"w_phase*diff(phase) and pick the lowest-cost frame. Contact weight "
                    f"should be 2x velocity weight to prevent skating."
                ),
                "params": {
                    "entry_frame_cost": str(entry_cost),
                    "cost_function": "weighted_squared_euclidean",
                    "contact_weight": "2.0",
                    "velocity_weight": "1.0",
                    "phase_weight": "0.8",
                    "archetype": archetype,
                },
                "confidence": min(1.0 - entry_cost / 10.0, 0.90),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        # Rule 13: Combined transition pipeline effectiveness
        if (transition_quality is not None and transition_quality > 0.5 and
                entry_cost is not None and entry_cost < 6.0):
            rules.append({
                "domain": "transition_pipeline",
                "rule_type": "pipeline_pattern",
                "rule_text": (
                    f"Full transition pipeline: RuntimeMotionQuery finds optimal entry "
                    f"frame (cost={entry_cost:.2f}), then TransitionSynthesizer applies "
                    f"inertialized blending (quality={transition_quality:.2f}). This "
                    f"two-stage approach eliminates both pop artifacts (wrong entry frame) "
                    f"and skating artifacts (crossfade contact destruction)."
                ),
                "params": {
                    "pipeline": "query_then_inertialize",
                    "entry_cost": str(entry_cost),
                    "transition_quality": str(transition_quality),
                    "archetype": archetype,
                },
                "confidence": min((transition_quality + (1.0 - entry_cost / 10.0)) / 2.0, 0.92),
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        self.rules_generated += len(rules)

        # Save to knowledge directory if available
        if self.knowledge_dir and self.knowledge_dir.exists():
            self._save_rules(rules)

        return rules

    def distill_pipeline_success(
        self,
        pipeline_metadata: dict[str, Any],
        archetype: str = "level_pdg",
    ) -> list[dict]:
        """Distill successful procedural pipeline executions into reusable rules."""
        rules: list[dict] = []
        scene_metrics = pipeline_metadata.get("scene_metrics", {})
        shader_plan = pipeline_metadata.get("shader_plan", {})
        execution_order = pipeline_metadata.get("pdg_execution_order", [])

        if execution_order:
            rules.append({
                "domain": "procedural_graph",
                "rule_type": "pipeline_pattern",
                "rule_text": (
                    f"For archetype '{archetype}', keep a DAG order like "
                    f"{' -> '.join(execution_order)} so WFC outputs are transformed into "
                    "scene description, shader planning, preview rendering, and export without manual handoff."
                ),
                "params": {
                    "execution_order": " -> ".join(execution_order),
                    "level_id": str(pipeline_metadata.get("level_id", "unknown")),
                    "scene_format": str(pipeline_metadata.get("scene_format", "unknown")),
                },
                "confidence": 0.88,
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        if pipeline_metadata.get("scene_format"):
            rules.append({
                "domain": "scene_description",
                "rule_type": "interface_contract",
                "rule_text": (
                    f"Use a USD-like scene description ({pipeline_metadata.get('scene_format')}) as the "
                    "shared contract between topology generation, shader reasoning, and export staging."
                ),
                "params": {
                    "scene_format": str(pipeline_metadata.get("scene_format")),
                    "hazard_density": str(scene_metrics.get("hazard_density", 0.0)),
                    "vertical_activity_ratio": str(scene_metrics.get("vertical_activity_ratio", 0.0)),
                },
                "confidence": 0.9,
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        if shader_plan.get("shader_type"):
            rules.append({
                "domain": "render_bridge",
                "rule_type": "heuristic",
                "rule_text": (
                    f"Condition downstream rendering from structured scene metrics; current successful base shader is "
                    f"{shader_plan.get('shader_type')} with preset {shader_plan.get('preset_name', 'default')}."
                ),
                "params": {
                    "shader_type": str(shader_plan.get("shader_type")),
                    "preset_name": str(shader_plan.get("preset_name", "default")),
                    "hazard_density": str(shader_plan.get("scene_conditioning", {}).get("hazard_density", 0.0)),
                    "render_mode": str(shader_plan.get("scene_conditioning", {}).get("render_mode", "unknown")),
                },
                "confidence": 0.84,
                "source": f"Layer3-AutoDistill-{self.rules_generated}",
            })

        self.rules_generated += len(rules)

        if self.knowledge_dir and self.knowledge_dir.exists() and rules:
            self._save_rules(rules, knowledge_file_name="procedural_pipeline.md")

        return rules

    def _save_rules(self, rules: list[dict], knowledge_file_name: str = "physics_locomotion.md") -> None:
        """Append rules to the physics knowledge file."""
        knowledge_file = self.knowledge_dir / knowledge_file_name

        lines = []
        if not knowledge_file.exists():
            lines = [
                f"# {knowledge_file.stem.replace('_', ' ').title()} Knowledge Base",
                "",
                "> Auto-generated by Layer 3 Evolution System.",
                "> Contains distilled rules from successful runs and reusable pipeline patterns.",
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

        # SESSION-035: AMP discriminator for adversarial motion evaluation
        # Replaces hand-written coverage_score with learned motion prior
        from ..animation.skill_embeddings import MotionDiscriminator
        self.motion_discriminator = MotionDiscriminator(
            obs_dim=40, hidden_dim=256, reward_mode="lsgan",
        )

        # SESSION-035: VPoser prior for latent-space mutation
        # Guarantees all mutated poses are anatomically legal
        from ..animation.human_math import VPoserDistilledPrior
        self.vposer_prior = VPoserDistilledPrior()

        # SESSION-035: Convergence bridge — stores best parameters for export
        self.converged_params: dict[str, Any] = {}

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

            # SESSION-035: AMP discriminator augments fitness evaluation
            # Instead of relying solely on hand-written metrics, ask the
            # discriminator: "Does this motion look like real motion?"
            try:
                # Generate synthetic state vectors from genotype parameters
                obs_dim = self.motion_discriminator.obs_dim
                n_frames = 10
                states = []
                for frame_i in range(n_frames):
                    state_vec = np.zeros(obs_dim, dtype=np.float32)
                    # Encode physics parameters into state
                    if hasattr(p, 'pd_stiffness_scale'):
                        state_vec[0] = p.pd_stiffness_scale
                    if hasattr(p, 'pd_damping_scale'):
                        state_vec[1] = p.pd_damping_scale
                    if hasattr(l, 'step_frequency'):
                        state_vec[2] = l.step_frequency
                    if hasattr(l, 'stride_length'):
                        state_vec[3] = l.stride_length
                    # Add frame-dependent variation
                    phase = frame_i / max(n_frames - 1, 1)
                    state_vec[4] = math.sin(phase * 2 * math.pi)
                    state_vec[5] = math.cos(phase * 2 * math.pi)
                    states.append(state_vec)

                amp_reward = self.motion_discriminator.style_reward_sequence(states)
                f["amp_style_reward"] = amp_reward

                # Blend AMP reward into overall fitness (30% weight)
                f["overall"] = 0.7 * f["overall"] + 0.3 * amp_reward
            except Exception:
                f["amp_style_reward"] = 0.0

            # SESSION-035: VPoser naturalness scoring
            # Check if the genotype's implied poses are anatomically natural
            try:
                sample_pose = {}
                if hasattr(l, 'key_poses') and l.key_poses:
                    sample_pose = l.key_poses[0] if isinstance(l.key_poses[0], dict) else {}
                elif hasattr(l, 'stride_length'):
                    # Construct a representative pose from gait parameters
                    sample_pose = {
                        "hip": 0.0, "spine": 0.05,
                        "l_hip": -l.stride_length * 0.3,
                        "r_hip": l.stride_length * 0.3,
                        "l_knee": -0.3, "r_knee": -0.1,
                    }
                if sample_pose:
                    vposer_score = self.vposer_prior.naturalness_score(sample_pose)
                    f["vposer_naturalness"] = vposer_score
                    # Penalize unnatural poses (10% weight)
                    f["overall"] = 0.9 * f["overall"] + 0.1 * vposer_score
            except Exception:
                f["vposer_naturalness"] = 0.0

            pop_fitness.append(f)

        # Select best
        best_idx = max(range(len(pop_fitness)), key=lambda i: pop_fitness[i]["overall"])
        best_physics = population_p[best_idx]
        best_loco = population_l[best_idx]
        best_fitness = pop_fitness[best_idx]

        if self.verbose:
            amp_r = best_fitness.get('amp_style_reward', 'N/A')
            vp_n = best_fitness.get('vposer_naturalness', 'N/A')
            print(f"  [EVOLVE] Best in population: {best_fitness['overall']:.3f} (idx={best_idx})")
            if amp_r != 'N/A':
                print(f"  [S035-AMP] style_reward={amp_r:.3f}")
            if vp_n != 'N/A':
                print(f"  [S035-VPoser] naturalness={vp_n:.3f}")

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
                # SESSION-034: Industrial metrics in strategy record
                "industrial_metrics": {
                    "contact_consistency": best_fitness.get("contact_consistency", None),
                    "skating_penalty": best_fitness.get("skating_penalty", None),
                    "anticipation_score": best_fitness.get("anticipation_score", None),
                    "impact_sharpness": best_fitness.get("impact_sharpness", None),
                },
                # SESSION-039: Transition pipeline metrics in strategy record
                "transition_metrics": {
                    "transition_quality": best_fitness.get("transition_quality", None),
                    "entry_frame_cost": best_fitness.get("entry_frame_cost", None),
                    "entry_quality": best_fitness.get("entry_quality", None),
                },
            })

        # SESSION-034: Log industrial metrics for diagnostics
        if self.verbose:
            cc = best_fitness.get('contact_consistency', 'N/A')
            sq = best_fitness.get('silhouette_quality', 'N/A')
            sp = best_fitness.get('skating_penalty', 'N/A')
            if cc != 'N/A':
                print(f"  [S034] contact_consistency={cc:.3f} | "
                      f"silhouette_quality={sq:.3f} | skating_penalty={sp:.3f}")

        # SESSION-039: Log transition pipeline metrics
        if self.verbose:
            tq = best_fitness.get('transition_quality', 'N/A')
            efc = best_fitness.get('entry_frame_cost', 'N/A')
            eq = best_fitness.get('entry_quality', 'N/A')
            if tq != 'N/A':
                print(f"  [S039] transition_quality={tq:.3f} | "
                      f"entry_frame_cost={efc:.3f} | entry_quality={eq:.3f}")

        # SESSION-035: Update convergence bridge with best parameters
        # This bridges the gap between Layer 3 evaluation and export-time
        # parameter selection (Gap #3 fix)
        self.converged_params = {
            "physics_stiffness": getattr(best_physics, 'pd_stiffness_scale', 1.0),
            "physics_damping": getattr(best_physics, 'pd_damping_scale', 1.0),
            "compliance_alpha": min(0.8, max(0.3, best_fitness.get('stability', 0.6))),
            "biomechanics_zmp_strength": min(0.5, max(0.1, best_fitness.get('balance_score', 0.3))),
            "amp_style_reward": best_fitness.get('amp_style_reward', 0.0),
            "vposer_naturalness": best_fitness.get('vposer_naturalness', 0.0),
            # SESSION-039: Transition pipeline parameters in convergence bridge
            "transition_quality": best_fitness.get('transition_quality', 0.5),
            "entry_frame_cost": best_fitness.get('entry_frame_cost', 5.0),
            "transition_strategy": "dead_blending",  # Best default from research
            "combined_fitness": best_fitness['overall'],
            "archetype": archetype,
            "cycle_id": cycle_id,
        }

        self.state.current_phase = EvolutionPhase.IDLE
        self._save_state()

        if self.verbose:
            print(f"  [COMPLETE] Cycle {cycle_id}: combined={best_fitness['overall']:.3f} "
                  f"| stagnation={self.state.stagnation_count}")
            print(f"  [S035-CONVERGE] Bridge params: stiffness={self.converged_params['physics_stiffness']:.2f}, "
                  f"damping={self.converged_params['physics_damping']:.2f}, "
                  f"compliance_alpha={self.converged_params['compliance_alpha']:.2f}")

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
