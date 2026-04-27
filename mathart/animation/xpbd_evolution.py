"""XPBD Three-Layer Evolution Loop: internal evolution, external knowledge
distillation, and self-iterating test harness.

This module implements the **three-layer evolution cycle** that allows the
physics engine to continuously improve itself:

  Layer 1 — **Internal Evolution (自我进化)**
    The solver monitors its own diagnostics (constraint errors, energy drift,
    collision counts) and auto-tunes parameters (sub-steps, iterations,
    compliance values) to converge toward physically plausible behaviour.

  Layer 2 — **External Knowledge Distillation (外部知识蒸馏)**
    New research findings (papers, code references, user-provided insights)
    are ingested as ``KnowledgeEntry`` records.  The distiller maps each entry
    to concrete parameter adjustments or architectural flags, then persists
    the mapping in a JSON knowledge base so future sessions can replay it.

  Layer 3 — **Self-Iterating Test (自我迭代测试)**
    A battery of physics scenario tests (pendulum, heavy-weapon swing, cape
    flutter, collision stress) is run automatically.  Results are compared
    against acceptance thresholds derived from Newton's laws.  Failures
    trigger Layer 1 auto-tuning or flag Layer 2 knowledge gaps.

The three layers form a closed feedback loop:
  Test → Diagnose → Tune/Distill → Re-test → …

All state is serialisable to JSON for persistence across sessions.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .xpbd_solver import (
    XPBDSolver,
    XPBDSolverConfig,
    XPBDChainPreset,
    XPBDDiagnostics,
    ParticleKind,
    build_xpbd_chain,
    create_default_xpbd_presets,
)
from .xpbd_collision import XPBDCollisionManager

_EPS = 1e-8


# ===========================================================================
# Layer 1: Internal Evolution (Auto-Tuning)
# ===========================================================================

class TuningAction(Enum):
    INCREASE_SUBSTEPS = auto()
    DECREASE_SUBSTEPS = auto()
    INCREASE_ITERATIONS = auto()
    DECREASE_ITERATIONS = auto()
    TIGHTEN_COMPLIANCE = auto()
    RELAX_COMPLIANCE = auto()
    INCREASE_DAMPING = auto()
    DECREASE_DAMPING = auto()
    LOWER_MAX_VELOCITY = auto()
    RAISE_MAX_VELOCITY = auto()
    NO_ACTION = auto()


@dataclass
class EvolutionDiagnosticSnapshot:
    """A single diagnostic measurement for the evolution loop."""
    timestamp: float = 0.0
    mean_constraint_error: float = 0.0
    max_constraint_error: float = 0.0
    energy_estimate: float = 0.0
    energy_drift: float = 0.0
    rigid_com_displacement: float = 0.0
    reaction_impulse: float = 0.0
    self_collision_count: int = 0
    max_velocity: float = 0.0
    sub_steps: int = 0
    iterations: int = 0
    tuning_action: str = "NO_ACTION"

    def to_dict(self) -> dict:
        return asdict(self)


class InternalEvolver:
    """Layer 1: monitors solver diagnostics and auto-tunes parameters.

    Thresholds:
      - constraint_error_target: mean error should stay below this
      - energy_drift_target: frame-to-frame energy change should be small
      - max_velocity_threshold: if exceeded, increase sub-steps
      - collision_spike_threshold: sudden collision count increase → investigate
    """

    def __init__(
        self,
        config: XPBDSolverConfig,
        *,
        constraint_error_target: float = 1e-4,
        energy_drift_target: float = 0.1,
        max_velocity_threshold: float = 30.0,
        collision_spike_threshold: int = 10,
        max_substeps: int = 16,
        max_iterations: int = 32,
        min_substeps: int = 1,
        min_iterations: int = 2,
    ):
        self._config = config
        self._constraint_error_target = constraint_error_target
        self._energy_drift_target = energy_drift_target
        self._max_velocity_threshold = max_velocity_threshold
        self._collision_spike_threshold = collision_spike_threshold
        self._max_substeps = max_substeps
        self._max_iterations = max_iterations
        self._min_substeps = min_substeps
        self._min_iterations = min_iterations
        self._history: list[EvolutionDiagnosticSnapshot] = []
        self._prev_energy: float = 0.0
        self._tuning_cooldown: int = 0

    def observe(self, diag: XPBDDiagnostics) -> TuningAction:
        """Observe solver diagnostics and decide on a tuning action."""
        energy_drift = abs(diag.energy_estimate - self._prev_energy)
        self._prev_energy = diag.energy_estimate

        snapshot = EvolutionDiagnosticSnapshot(
            timestamp=time.time(),
            mean_constraint_error=diag.mean_constraint_error,
            max_constraint_error=diag.max_constraint_error,
            energy_estimate=diag.energy_estimate,
            energy_drift=energy_drift,
            rigid_com_displacement=diag.rigid_com_displacement,
            reaction_impulse=diag.reaction_impulse_magnitude,
            self_collision_count=diag.self_collision_count,
            max_velocity=diag.max_velocity_observed,
            sub_steps=diag.sub_steps_used,
            iterations=diag.iterations_per_substep,
        )

        action = TuningAction.NO_ACTION

        if self._tuning_cooldown > 0:
            self._tuning_cooldown -= 1
        else:
            # Priority 1: velocity too high → increase sub-steps
            if diag.max_velocity_observed > self._max_velocity_threshold:
                if self._config.sub_steps < self._max_substeps:
                    action = TuningAction.INCREASE_SUBSTEPS
                    self._tuning_cooldown = 5

            # Priority 2: constraint error too high → increase iterations
            elif diag.mean_constraint_error > self._constraint_error_target:
                if self._config.solver_iterations < self._max_iterations:
                    action = TuningAction.INCREASE_ITERATIONS
                    self._tuning_cooldown = 3

            # Priority 3: energy drift too high → increase damping
            elif energy_drift > self._energy_drift_target:
                action = TuningAction.INCREASE_DAMPING
                self._tuning_cooldown = 3

            # Priority 4: collision spike → tighten compliance
            elif diag.self_collision_count > self._collision_spike_threshold:
                action = TuningAction.TIGHTEN_COMPLIANCE
                self._tuning_cooldown = 5

            # Optimization: if everything is good and we have headroom, reduce cost
            elif (diag.mean_constraint_error < self._constraint_error_target * 0.1
                  and diag.max_velocity_observed < self._max_velocity_threshold * 0.5):
                if self._config.sub_steps > self._min_substeps:
                    action = TuningAction.DECREASE_SUBSTEPS
                    self._tuning_cooldown = 10
                elif self._config.solver_iterations > self._min_iterations:
                    action = TuningAction.DECREASE_ITERATIONS
                    self._tuning_cooldown = 10

        snapshot.tuning_action = action.name
        self._history.append(snapshot)
        return action

    def apply_action(self, action: TuningAction) -> XPBDSolverConfig:
        """Apply a tuning action and return the updated config."""
        cfg = self._config
        if action == TuningAction.INCREASE_SUBSTEPS:
            cfg = XPBDSolverConfig(
                sub_steps=min(cfg.sub_steps + 1, self._max_substeps),
                solver_iterations=cfg.solver_iterations,
                gravity=cfg.gravity,
                default_compliance=cfg.default_compliance,
                default_damping=cfg.default_damping,
                velocity_damping=cfg.velocity_damping,
                max_velocity=cfg.max_velocity,
                enable_self_collision=cfg.enable_self_collision,
                self_collision_radius=cfg.self_collision_radius,
                friction_coefficient=cfg.friction_coefficient,
                enable_two_way_coupling=cfg.enable_two_way_coupling,
            )
        elif action == TuningAction.DECREASE_SUBSTEPS:
            cfg = XPBDSolverConfig(
                sub_steps=max(cfg.sub_steps - 1, self._min_substeps),
                solver_iterations=cfg.solver_iterations,
                gravity=cfg.gravity,
                default_compliance=cfg.default_compliance,
                default_damping=cfg.default_damping,
                velocity_damping=cfg.velocity_damping,
                max_velocity=cfg.max_velocity,
                enable_self_collision=cfg.enable_self_collision,
                self_collision_radius=cfg.self_collision_radius,
                friction_coefficient=cfg.friction_coefficient,
                enable_two_way_coupling=cfg.enable_two_way_coupling,
            )
        elif action == TuningAction.INCREASE_ITERATIONS:
            cfg = XPBDSolverConfig(
                sub_steps=cfg.sub_steps,
                solver_iterations=min(cfg.solver_iterations + 2, self._max_iterations),
                gravity=cfg.gravity,
                default_compliance=cfg.default_compliance,
                default_damping=cfg.default_damping,
                velocity_damping=cfg.velocity_damping,
                max_velocity=cfg.max_velocity,
                enable_self_collision=cfg.enable_self_collision,
                self_collision_radius=cfg.self_collision_radius,
                friction_coefficient=cfg.friction_coefficient,
                enable_two_way_coupling=cfg.enable_two_way_coupling,
            )
        elif action == TuningAction.DECREASE_ITERATIONS:
            cfg = XPBDSolverConfig(
                sub_steps=cfg.sub_steps,
                solver_iterations=max(cfg.solver_iterations - 1, self._min_iterations),
                gravity=cfg.gravity,
                default_compliance=cfg.default_compliance,
                default_damping=cfg.default_damping,
                velocity_damping=cfg.velocity_damping,
                max_velocity=cfg.max_velocity,
                enable_self_collision=cfg.enable_self_collision,
                self_collision_radius=cfg.self_collision_radius,
                friction_coefficient=cfg.friction_coefficient,
                enable_two_way_coupling=cfg.enable_two_way_coupling,
            )
        elif action == TuningAction.INCREASE_DAMPING:
            cfg = XPBDSolverConfig(
                sub_steps=cfg.sub_steps,
                solver_iterations=cfg.solver_iterations,
                gravity=cfg.gravity,
                default_compliance=cfg.default_compliance,
                default_damping=cfg.default_damping,
                velocity_damping=max(cfg.velocity_damping * 0.98, 0.9),
                max_velocity=cfg.max_velocity,
                enable_self_collision=cfg.enable_self_collision,
                self_collision_radius=cfg.self_collision_radius,
                friction_coefficient=cfg.friction_coefficient,
                enable_two_way_coupling=cfg.enable_two_way_coupling,
            )
        elif action == TuningAction.TIGHTEN_COMPLIANCE:
            cfg = XPBDSolverConfig(
                sub_steps=cfg.sub_steps,
                solver_iterations=cfg.solver_iterations,
                gravity=cfg.gravity,
                default_compliance=cfg.default_compliance * 0.5,
                default_damping=cfg.default_damping,
                velocity_damping=cfg.velocity_damping,
                max_velocity=cfg.max_velocity,
                enable_self_collision=cfg.enable_self_collision,
                self_collision_radius=cfg.self_collision_radius,
                friction_coefficient=cfg.friction_coefficient,
                enable_two_way_coupling=cfg.enable_two_way_coupling,
            )
        self._config = cfg
        return cfg

    @property
    def history(self) -> list[EvolutionDiagnosticSnapshot]:
        return self._history

    def export_history(self) -> list[dict]:
        return [s.to_dict() for s in self._history]


# ===========================================================================
# Layer 2: External Knowledge Distillation
# ===========================================================================

@dataclass
class KnowledgeEntry:
    """A distilled piece of external knowledge."""
    source: str                          # Paper / tutorial / user input
    topic: str                           # e.g. "XPBD compliance decoupling"
    insight: str                         # Human-readable insight
    parameter_effects: dict[str, Any] = field(default_factory=dict)
    # e.g. {"compliance": 1e-7, "sub_steps": 4}
    applied: bool = False
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "topic": self.topic,
            "insight": self.insight,
            "parameter_effects": self.parameter_effects,
            "applied": self.applied,
            "timestamp": self.timestamp,
        }


class KnowledgeDistiller:
    """Layer 2: ingests external knowledge and maps it to solver parameters.

    Knowledge entries are persisted to a JSON file so that future sessions
    can replay the distillation without re-reading the original sources.
    """

    def __init__(self, knowledge_path: Optional[Path] = None):
        self._entries: list[KnowledgeEntry] = []
        self._path = knowledge_path

        # Pre-load foundational knowledge
        self._seed_foundational_knowledge()

        # Load persisted knowledge
        if self._path and self._path.exists():
            self._load()

    def _seed_foundational_knowledge(self) -> None:
        """Seed the knowledge base with research findings from this session."""
        foundational = [
            KnowledgeEntry(
                source="Macklin & Müller, XPBD (SIGGRAPH 2016)",
                topic="Compliance decouples stiffness from iteration count and time step",
                insight="Replace stiffness k with compliance α = 1/k. "
                        "Normalise as α̃ = α/Δt² to make behaviour independent of "
                        "time step and iteration count.",
                parameter_effects={"compliance_mode": "alpha_tilde"},
                applied=True,
                timestamp=time.time(),
            ),
            KnowledgeEntry(
                source="Macklin & Müller, XPBD (SIGGRAPH 2016)",
                topic="Lagrange multiplier accumulation for force estimation",
                insight="Accumulate Δλ across iterations. The constraint force is "
                        "estimated as f ≈ λ/Δt, enabling reaction impulse measurement.",
                parameter_effects={"lambda_accumulation": True},
                applied=True,
                timestamp=time.time(),
            ),
            KnowledgeEntry(
                source="Müller et al., Detailed Rigid Body Simulation (SCA 2020)",
                topic="Two-way rigid-soft coupling via inverse mass weighting",
                insight="Place rigid-body CoM as a particle with w = 1/m_body. "
                        "Constraint corrections distribute proportionally to inverse "
                        "masses, automatically producing Newton's Third Law reactions.",
                parameter_effects={"enable_two_way_coupling": True, "rigid_body_mass": 70.0},
                applied=True,
                timestamp=time.time(),
            ),
            KnowledgeEntry(
                source="Müller et al., Detailed Rigid Body Simulation (SCA 2020)",
                topic="Non-linear Projected Gauss-Seidel (NPGS)",
                insight="Update positions immediately after each constraint solve "
                        "(not after full iteration). This makes the solver non-linear "
                        "and avoids freezing constraint directions.",
                parameter_effects={"solver_mode": "npgs"},
                applied=True,
                timestamp=time.time(),
            ),
            KnowledgeEntry(
                source="Matthias Müller, Ten Minute Physics Tutorial 15",
                topic="Self-collision 5-trick recipe",
                insight="1) Spatial hash table for O(1) queries. "
                        "2) rest_length >= 2*radius to avoid constraint fighting. "
                        "3) Sub-steps for stability. "
                        "4) v_max = 0.2*radius/Δt_sub for tunnelling guard. "
                        "5) Friction damping after collision response.",
                parameter_effects={
                    "enable_self_collision": True,
                    "self_collision_radius": 0.015,
                    "friction_coefficient": 0.3,
                },
                applied=True,
                timestamp=time.time(),
            ),
            KnowledgeEntry(
                source="Macklin & Müller, XPBD (SIGGRAPH 2016), Eq 26",
                topic="Rayleigh damping via compliance-like parameter β",
                insight="Damping is modelled as γ = α̃·β/Δt. The modified update "
                        "Δλ = (-C - α̃·λ - γ·Ċ·Δt) / ((1+γ)·∇C·M⁻¹·∇Cᵀ + α̃) "
                        "provides stable energy dissipation without ad-hoc velocity scaling.",
                parameter_effects={"damping_compliance": 1e-4},
                applied=True,
                timestamp=time.time(),
            ),
        ]
        for entry in foundational:
            if not any(e.topic == entry.topic for e in self._entries):
                self._entries.append(entry)

    def add_knowledge(self, entry: KnowledgeEntry) -> None:
        """Add a new knowledge entry."""
        entry.timestamp = time.time()
        self._entries.append(entry)

    def apply_to_config(self, config: XPBDSolverConfig) -> XPBDSolverConfig:
        """Apply all unapplied knowledge entries to the solver config."""
        updates: dict[str, Any] = {}
        for entry in self._entries:
            if not entry.applied:
                updates.update(entry.parameter_effects)
                entry.applied = True

        if not updates:
            return config

        return XPBDSolverConfig(
            sub_steps=updates.get("sub_steps", config.sub_steps),
            solver_iterations=updates.get("solver_iterations", config.solver_iterations),
            gravity=updates.get("gravity", config.gravity),
            default_compliance=updates.get("default_compliance", config.default_compliance),
            default_damping=updates.get("default_damping", config.default_damping),
            velocity_damping=updates.get("velocity_damping", config.velocity_damping),
            max_velocity=updates.get("max_velocity", config.max_velocity),
            enable_self_collision=updates.get("enable_self_collision", config.enable_self_collision),
            self_collision_radius=updates.get("self_collision_radius", config.self_collision_radius),
            friction_coefficient=updates.get("friction_coefficient", config.friction_coefficient),
            enable_two_way_coupling=updates.get("enable_two_way_coupling", config.enable_two_way_coupling),
        )

    def save(self, path: Optional[Path] = None) -> None:
        """Persist knowledge base to JSON."""
        target = path or self._path
        if target is None:
            return
        data = [e.to_dict() for e in self._entries]
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> None:
        """Load knowledge base from JSON."""
        if self._path is None or not self._path.exists():
            return
        text = self._path.read_text(encoding="utf-8").strip()
        if not text:
            return
        data = json.loads(text)
        for item in data:
            entry = KnowledgeEntry(**item)
            if not any(e.topic == entry.topic for e in self._entries):
                self._entries.append(entry)

    @property
    def entries(self) -> list[KnowledgeEntry]:
        return self._entries

    def export(self) -> list[dict]:
        return [e.to_dict() for e in self._entries]


# ===========================================================================
# Layer 3: Self-Iterating Test Harness
# ===========================================================================

@dataclass
class TestResult:
    """Result of a single physics scenario test."""
    name: str
    passed: bool
    metric_name: str
    expected_range: tuple[float, float]
    actual_value: float
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "metric_name": self.metric_name,
            "expected_range": list(self.expected_range),
            "actual_value": self.actual_value,
            "details": self.details,
        }


class PhysicsTestHarness:
    """Layer 3: automated physics scenario tests.

    Each test creates a minimal XPBD scene, runs it for a fixed number of
    frames, and checks that the result falls within physically plausible
    bounds.
    """

    def __init__(self, config: Optional[XPBDSolverConfig] = None):
        self._config = config or XPBDSolverConfig()
        self._results: list[TestResult] = []

    def run_all(self) -> list[TestResult]:
        """Run the full test battery."""
        self._results.clear()
        self._results.append(self._test_free_fall())
        self._results.append(self._test_pendulum_conservation())
        self._results.append(self._test_two_way_coupling_reaction())
        self._results.append(self._test_distance_constraint_stability())
        self._results.append(self._test_self_collision_separation())
        self._results.append(self._test_heavy_weapon_stagger())
        self._results.append(self._test_velocity_clamping())
        return self._results

    def _test_free_fall(self) -> TestResult:
        """Test: free fall must match the analytical baseline s = 0.5*g*t²."""
        cfg = XPBDSolverConfig(
            sub_steps=self._config.sub_steps,
            solver_iterations=self._config.solver_iterations,
            gravity=self._config.gravity,
            default_compliance=self._config.default_compliance,
            default_damping=self._config.default_damping,
            velocity_damping=self._config.velocity_damping,
            max_velocity=self._config.max_velocity,
            enable_self_collision=False,
            self_collision_radius=self._config.self_collision_radius,
            friction_coefficient=self._config.friction_coefficient,
            enable_two_way_coupling=False,
        )
        solver = XPBDSolver(cfg)
        idx = solver.add_particle((0.0, 10.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
        dt = 1.0 / 60.0
        frames = 60  # 1 second
        total_time = dt * frames
        gravity_y = float(cfg.gravity[1])
        for _ in range(frames):
            solver.step(dt)
        pos = solver.get_position(idx)
        expected_y = 10.0 + 0.5 * gravity_y * (total_time ** 2)
        actual_y = pos[1]
        error = abs(actual_y - expected_y)
        tolerance = 1e-6
        return TestResult(
            name="free_fall_gravity",
            passed=error <= tolerance,
            metric_name="y_position_error",
            expected_range=(0.0, tolerance),
            actual_value=error,
            details=(
                f"Expected analytical y={expected_y:.9f}, got y={actual_y:.9f}, "
                f"|error|={error:.3e}"
            ),
        )

    def _test_pendulum_conservation(self) -> TestResult:
        """Test: a pendulum should roughly conserve energy over 2 seconds."""
        solver = XPBDSolver(XPBDSolverConfig(
            sub_steps=4, solver_iterations=8, velocity_damping=1.0,
            gravity=(0.0, -9.81), enable_self_collision=False,
            enable_two_way_coupling=False,
        ))
        anchor = solver.add_particle((0.0, 5.0), mass=0.0, kind=ParticleKind.KINEMATIC)
        bob = solver.add_particle((1.0, 5.0), mass=1.0, kind=ParticleKind.SOFT_NODE)
        solver.add_distance_constraint(anchor, bob, rest_length=1.0, compliance=0.0)

        dt = 1.0 / 60.0
        energies = []
        for _ in range(120):
            diag = solver.step(dt)
            pos = solver.get_position(bob)
            vel = solver.get_velocity(bob)
            ke = 0.5 * (vel[0]**2 + vel[1]**2)
            pe = 9.81 * pos[1]
            energies.append(ke + pe)

        if len(energies) < 2:
            return TestResult("pendulum_energy", False, "energy_drift", (0.0, 5.0), 999.0)

        drift = abs(energies[-1] - energies[0])
        return TestResult(
            name="pendulum_energy_conservation",
            passed=drift < 5.0,
            metric_name="total_energy_drift",
            expected_range=(0.0, 5.0),
            actual_value=drift,
            details=f"Initial E={energies[0]:.3f}, Final E={energies[-1]:.3f}",
        )

    def _test_two_way_coupling_reaction(self) -> TestResult:
        """Test: pulling a soft chain should move the rigid CoM."""
        solver = XPBDSolver(XPBDSolverConfig(
            sub_steps=4, solver_iterations=8, gravity=(0.0, -9.81),
            enable_two_way_coupling=True, enable_self_collision=False,
        ))
        com = solver.add_particle((0.0, 5.0), mass=10.0, kind=ParticleKind.RIGID_COM)
        node1 = solver.add_particle((0.5, 5.0), mass=0.5, kind=ParticleKind.SOFT_NODE)
        node2 = solver.add_particle((1.0, 5.0), mass=0.5, kind=ParticleKind.SOFT_NODE)
        solver.add_distance_constraint(com, node1, rest_length=0.5, compliance=1e-7)
        solver.add_distance_constraint(node1, node2, rest_length=0.5, compliance=1e-7)

        initial_com = solver.get_position(com)
        dt = 1.0 / 60.0
        for _ in range(60):
            solver.step(dt)

        final_com = solver.get_position(com)
        displacement = np.linalg.norm(np.array(final_com) - np.array(initial_com))

        return TestResult(
            name="two_way_coupling_reaction",
            passed=displacement > 0.001,  # CoM must move
            metric_name="com_displacement",
            expected_range=(0.001, 10.0),
            actual_value=float(displacement),
            details=f"CoM moved {displacement:.6f} units (should be > 0.001)",
        )

    def _test_distance_constraint_stability(self) -> TestResult:
        """Test: distance constraints should maintain rest length under stress."""
        solver = XPBDSolver(XPBDSolverConfig(
            sub_steps=4, solver_iterations=8, gravity=(0.0, -9.81),
            enable_self_collision=False, enable_two_way_coupling=False,
        ))
        anchor = solver.add_particle((0.0, 10.0), mass=0.0, kind=ParticleKind.KINEMATIC)
        rest_len = 0.5
        prev_idx = anchor
        for i in range(5):
            idx = solver.add_particle((0.0, 10.0 - rest_len * (i + 1)), mass=0.2, kind=ParticleKind.SOFT_NODE)
            solver.add_distance_constraint(prev_idx, idx, rest_length=rest_len, compliance=1e-7)
            prev_idx = idx

        dt = 1.0 / 60.0
        max_stretch = 0.0
        for _ in range(120):
            diag = solver.step(dt)
            max_stretch = max(max_stretch, diag.max_constraint_error)

        return TestResult(
            name="distance_constraint_stability",
            passed=max_stretch < rest_len * 0.1,  # Less than 10% stretch
            metric_name="max_stretch_error",
            expected_range=(0.0, rest_len * 0.1),
            actual_value=max_stretch,
            details=f"Max stretch error: {max_stretch:.6f} (threshold: {rest_len * 0.1:.6f})",
        )

    def _test_self_collision_separation(self) -> TestResult:
        """Test: two particles should not overlap after collision resolution."""
        solver = XPBDSolver(XPBDSolverConfig(
            sub_steps=4, solver_iterations=8, gravity=(0.0, 0.0),
            enable_self_collision=True, self_collision_radius=0.1,
            enable_two_way_coupling=False,
        ))
        # Two particles heading toward each other
        p1 = solver.add_particle((-0.05, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=0.1)
        p2 = solver.add_particle((0.05, 0.0), mass=1.0, kind=ParticleKind.SOFT_NODE, radius=0.1)
        solver._velocities[p1] = [1.0, 0.0]
        solver._velocities[p2] = [-1.0, 0.0]

        # Add self-collision constraint manually
        from .xpbd_solver import XPBDConstraint, ConstraintKind
        solver._constraints.append(XPBDConstraint(
            kind=ConstraintKind.SELF_COLLISION,
            particle_indices=(p1, p2),
            rest_value=0.2,  # 2 * radius
            compliance=0.0,
        ))

        dt = 1.0 / 60.0
        for _ in range(30):
            solver.step(dt)

        pos1 = np.array(solver.get_position(p1))
        pos2 = np.array(solver.get_position(p2))
        separation = float(np.linalg.norm(pos1 - pos2))

        return TestResult(
            name="self_collision_separation",
            passed=separation >= 0.18,  # Should maintain ~0.2 separation
            metric_name="particle_separation",
            expected_range=(0.18, 10.0),
            actual_value=separation,
            details=f"Separation: {separation:.4f} (minimum: 0.18)",
        )

    def _test_heavy_weapon_stagger(self) -> TestResult:
        """Test: heavy weapon at chain tip should pull CoM significantly."""
        solver = XPBDSolver(XPBDSolverConfig(
            sub_steps=4, solver_iterations=8, gravity=(0.0, -9.81),
            enable_two_way_coupling=True, enable_self_collision=False,
        ))
        # Character CoM (70 kg)
        com = solver.add_particle((0.0, 5.0), mass=70.0, kind=ParticleKind.RIGID_COM)
        # Arm chain
        arm1 = solver.add_particle((0.3, 5.0), mass=2.0, kind=ParticleKind.SOFT_NODE)
        arm2 = solver.add_particle((0.6, 5.0), mass=2.0, kind=ParticleKind.SOFT_NODE)
        # Heavy weapon (15 kg greatsword)
        weapon = solver.add_particle((0.9, 5.0), mass=15.0, kind=ParticleKind.SOFT_NODE)

        solver.add_distance_constraint(com, arm1, rest_length=0.3, compliance=1e-8)
        solver.add_distance_constraint(arm1, arm2, rest_length=0.3, compliance=1e-8)
        solver.add_distance_constraint(arm2, weapon, rest_length=0.3, compliance=1e-8)

        initial_com = np.array(solver.get_position(com))
        dt = 1.0 / 60.0
        max_reaction = 0.0
        for _ in range(120):
            diag = solver.step(dt)
            max_reaction = max(max_reaction, diag.reaction_impulse_magnitude)

        final_com = np.array(solver.get_position(com))
        displacement = float(np.linalg.norm(final_com - initial_com))

        return TestResult(
            name="heavy_weapon_stagger",
            passed=displacement > 0.0001 and max_reaction > 0.0,
            metric_name="com_displacement_and_reaction",
            expected_range=(0.0001, 100.0),
            actual_value=displacement,
            details=f"CoM displaced {displacement:.6f}, max reaction impulse {max_reaction:.4f}",
        )

    def _test_velocity_clamping(self) -> TestResult:
        """Test: velocity should be clamped to max_velocity."""
        max_v = 20.0
        solver = XPBDSolver(XPBDSolverConfig(
            sub_steps=1, solver_iterations=1, gravity=(0.0, -1000.0),
            max_velocity=max_v, enable_self_collision=False,
            enable_two_way_coupling=False,
        ))
        idx = solver.add_particle((0.0, 100.0), mass=1.0, kind=ParticleKind.SOFT_NODE)

        dt = 1.0 / 60.0
        for _ in range(60):
            solver.step(dt)

        vel = solver.get_velocity(idx)
        speed = float(np.linalg.norm(vel))

        return TestResult(
            name="velocity_clamping",
            passed=speed <= max_v * 1.1,  # 10% tolerance
            metric_name="max_speed",
            expected_range=(0.0, max_v * 1.1),
            actual_value=speed,
            details=f"Speed: {speed:.2f} (limit: {max_v})",
        )

    @property
    def results(self) -> list[TestResult]:
        return self._results

    def export_results(self) -> list[dict]:
        return [r.to_dict() for r in self._results]

    def summary(self) -> dict:
        total = len(self._results)
        passed = sum(1 for r in self._results if r.passed)
        return {
            "total_tests": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / max(total, 1),
            "tests": self.export_results(),
        }


# ===========================================================================
# Orchestrator: ties the three layers together
# ===========================================================================

class XPBDEvolutionOrchestrator:
    """Orchestrates the three-layer evolution loop.

    Usage:
        orchestrator = XPBDEvolutionOrchestrator()
        # Run tests
        test_results = orchestrator.run_test_cycle()
        # Auto-tune based on results
        new_config = orchestrator.evolve()
        # Save state
        orchestrator.save_state(Path("evolution_state.json"))
    """

    def __init__(
        self,
        config: Optional[XPBDSolverConfig] = None,
        knowledge_path: Optional[Path] = None,
    ):
        self._config = config or XPBDSolverConfig()
        self._evolver = InternalEvolver(self._config)
        self._distiller = KnowledgeDistiller(knowledge_path)
        self._test_harness = PhysicsTestHarness(self._config)
        self._evolution_cycles: int = 0

    def run_test_cycle(self) -> list[TestResult]:
        """Run the full test battery with current config."""
        self._test_harness = PhysicsTestHarness(self._config)
        return self._test_harness.run_all()

    def evolve(self, solver_diagnostics: Optional[XPBDDiagnostics] = None) -> XPBDSolverConfig:
        """Run one evolution cycle: test → diagnose → tune → distill.

        Returns the updated solver config.
        """
        self._evolution_cycles += 1

        # Layer 2: Apply any new knowledge
        self._config = self._distiller.apply_to_config(self._config)

        # Layer 3: Run tests
        results = self.run_test_cycle()

        # Layer 1: If we have live diagnostics, observe and tune
        if solver_diagnostics is not None:
            action = self._evolver.observe(solver_diagnostics)
            if action != TuningAction.NO_ACTION:
                self._config = self._evolver.apply_action(action)

        # Layer 1: Also tune based on test failures
        failed = [r for r in results if not r.passed]
        for failure in failed:
            if "velocity" in failure.name.lower():
                self._config = self._evolver.apply_action(TuningAction.INCREASE_SUBSTEPS)
            elif "constraint" in failure.name.lower():
                self._config = self._evolver.apply_action(TuningAction.INCREASE_ITERATIONS)
            elif "energy" in failure.name.lower():
                self._config = self._evolver.apply_action(TuningAction.INCREASE_DAMPING)

        return self._config

    def add_external_knowledge(self, entry: KnowledgeEntry) -> None:
        """Inject new external knowledge into the distiller."""
        self._distiller.add_knowledge(entry)

    def save_state(self, path: Path) -> None:
        """Save the complete evolution state to JSON."""
        state = {
            "evolution_cycles": self._evolution_cycles,
            "config": {
                "sub_steps": int(self._config.sub_steps),
                "solver_iterations": int(self._config.solver_iterations),
                "gravity": [float(g) for g in self._config.gravity],
                "default_compliance": float(self._config.default_compliance),
                "default_damping": float(self._config.default_damping),
                "velocity_damping": float(self._config.velocity_damping),
                "max_velocity": float(self._config.max_velocity),
                "enable_self_collision": bool(self._config.enable_self_collision),
                "self_collision_radius": float(self._config.self_collision_radius),
                "friction_coefficient": float(self._config.friction_coefficient),
                "enable_two_way_coupling": bool(self._config.enable_two_way_coupling),
            },
            "knowledge_base": self._distiller.export(),
            "evolution_history": self._evolver.export_history(),
            "test_results": self._test_harness.export_results(),
        }
        # Use a custom encoder to handle numpy types
        class _NumpyEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, (np.integer,)):
                    return int(obj)
                if isinstance(obj, (np.floating,)):
                    return float(obj)
                if isinstance(obj, (np.bool_,)):
                    return bool(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                return super().default(obj)
        path.write_text(
            json.dumps(state, indent=2, ensure_ascii=True, cls=_NumpyEncoder),
            encoding="utf-8",
        )

    def load_state(self, path: Path) -> None:
        """Load evolution state from JSON."""
        if not path.exists():
            return
        state = json.loads(path.read_text(encoding="utf-8"))
        self._evolution_cycles = state.get("evolution_cycles", 0)
        cfg_data = state.get("config", {})
        if cfg_data:
            self._config = XPBDSolverConfig(
                sub_steps=cfg_data.get("sub_steps", 4),
                solver_iterations=cfg_data.get("solver_iterations", 8),
                gravity=tuple(cfg_data.get("gravity", [0.0, -9.81])),
                default_compliance=cfg_data.get("default_compliance", 0.0),
                default_damping=cfg_data.get("default_damping", 0.0),
                velocity_damping=cfg_data.get("velocity_damping", 0.98),
                max_velocity=cfg_data.get("max_velocity", 50.0),
                enable_self_collision=cfg_data.get("enable_self_collision", True),
                self_collision_radius=cfg_data.get("self_collision_radius", 0.015),
                friction_coefficient=cfg_data.get("friction_coefficient", 0.3),
                enable_two_way_coupling=cfg_data.get("enable_two_way_coupling", True),
            )

    @property
    def config(self) -> XPBDSolverConfig:
        return self._config

    @property
    def test_summary(self) -> dict:
        return self._test_harness.summary()

    @property
    def knowledge_entries(self) -> list[KnowledgeEntry]:
        return self._distiller.entries

    @property
    def evolution_cycles(self) -> int:
        return self._evolution_cycles


__all__ = [
    "TuningAction",
    "EvolutionDiagnosticSnapshot",
    "InternalEvolver",
    "KnowledgeEntry",
    "KnowledgeDistiller",
    "TestResult",
    "PhysicsTestHarness",
    "XPBDEvolutionOrchestrator",
]
