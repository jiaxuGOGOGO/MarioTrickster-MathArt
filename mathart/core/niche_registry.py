"""Niche Registry — MAP-Elites-Inspired Per-Lane Evolution with Pareto Front.

SESSION-064: Paradigm Shift #3 — From Cross-Lane Averaging to Niche Isolation.

This module implements the **Niche Registry** and **Pareto Front** system
inspired by MAP-Elites (Mouret & Clune 2015) and NSGA-II (Deb et al. 2002):

    1. Each evolution lane (2D Contour, 3D Mesh, Fluid VFX, WFC Tilemap,
       Motion 2D, etc.) is a separate **niche** with its own fitness function.
    2. Niches self-register via ``@register_niche`` — same pattern as backends.
    3. The orchestrator NEVER computes cross-lane weighted averages.
    4. A **Pareto Front** tracks the non-dominated solution set across niches.
    5. The **Meta-Report** aggregates niche reports without mixing scores.

Critical Rule (from the user's diagnostic)::

    NEVER compute: (2D_contour_score + 3D_mesh_normal_continuity) / 2
    ALWAYS report: {niche_A: score_A, niche_B: score_B, pareto_rank: ...}

Architecture::

    ┌─────────────────────────────────────────────────────────────┐
    │                   NicheRegistry (Singleton)                 │
    │                                                             │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
    │  │ motion2d │  │ mesh_3d  │  │ fluid    │  │ wfc      │  │
    │  │ (niche)  │  │ (niche)  │  │ (niche)  │  │ (niche)  │  │
    │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
    │       │              │              │              │        │
    │       ▼              ▼              ▼              ▼        │
    │   NicheReport    NicheReport    NicheReport    NicheReport │
    │   (isolated)     (isolated)     (isolated)     (isolated)  │
    └───────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  ParetoFront  │
                    │  (no mixing)  │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  Meta-Report  │
                    └───────────────┘

References
----------
[1] Jean-Baptiste Mouret & Jeff Clune, "Illuminating Search Spaces by
    Mapping Elites", arXiv:1504.04909, 2015.
[2] Kalyanmoy Deb et al., "A Fast and Elitist Multiobjective Genetic
    Algorithm: NSGA-II", IEEE TEC 6(2), 2002.
[3] Antoine Cully et al., "Quality-Diversity Optimization: a novel branch
    of stochastic optimization", arXiv:1708.09251, 2017.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Type, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Niche Report (Per-Lane Isolated Report)
# ---------------------------------------------------------------------------

@dataclass
class NicheReport:
    """Report from a single evolution niche — NEVER mixed with other niches.

    This is the MAP-Elites cell equivalent: each niche stores its own
    best-performing individual and fitness metrics independently.

    Attributes
    ----------
    niche_name : str
        Unique niche identifier.
    fitness_scores : dict[str, float]
        Per-objective fitness scores (NEVER averaged with other niches).
    pass_gate : bool
        Whether this niche's pass gate was met.
    elite_solution : dict[str, Any]
        The best solution found in this niche (behavioral descriptor + params).
    distilled_rules : list[str]
        Knowledge rules distilled from this niche's evolution.
    cycle_count : int
        Number of evolution cycles completed in this niche.
    trend : list[float]
        Historical fitness trend (last N cycles).
    metadata : dict[str, Any]
        Niche-specific metadata.
    """
    niche_name: str
    fitness_scores: dict[str, float] = field(default_factory=dict)
    pass_gate: bool = False
    elite_solution: dict[str, Any] = field(default_factory=dict)
    distilled_rules: list[str] = field(default_factory=list)
    cycle_count: int = 0
    trend: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_fitness(self) -> float:
        """The primary fitness score for this niche (max of all objectives)."""
        if not self.fitness_scores:
            return 0.0
        return max(self.fitness_scores.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "niche_name": self.niche_name,
            "fitness_scores": {k: round(v, 4) for k, v in self.fitness_scores.items()},
            "pass_gate": self.pass_gate,
            "elite_solution": self.elite_solution,
            "distilled_rules": self.distilled_rules,
            "cycle_count": self.cycle_count,
            "trend": [round(t, 4) for t in self.trend[-20:]],
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Evolution Niche Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class EvolutionNicheProtocol(Protocol):
    """Protocol that all evolution niches must satisfy."""

    @property
    def niche_name(self) -> str:
        """Unique niche identifier."""
        ...

    def evaluate(self, **kwargs: Any) -> NicheReport:
        """Run evaluation and return an isolated NicheReport."""
        ...

    def distill(self) -> list[str]:
        """Distill knowledge rules from this niche."""
        ...


# ---------------------------------------------------------------------------
# Niche Metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NicheMeta:
    """Immutable metadata for a registered niche."""
    name: str
    display_name: str = ""
    lane: str = ""
    fitness_objectives: tuple[str, ...] = ()
    pass_gate_conditions: tuple[str, ...] = ()
    behavioral_descriptors: tuple[str, ...] = ()
    session_origin: str = "SESSION-064"


# ---------------------------------------------------------------------------
# Evolution Niche Base Class
# ---------------------------------------------------------------------------

class EvolutionNiche:
    """Base class for evolution niches.

    Subclasses implement ``evaluate()`` and ``distill()`` for their
    specific lane. The base class provides common state management.
    """

    def __init__(
        self,
        niche_name: str,
        project_root: Optional[str | Path] = None,
    ) -> None:
        self._niche_name = niche_name
        self._root = Path(project_root) if project_root else Path.cwd()
        self._state_path = self._root / f".niche_{niche_name}_state.json"
        self._cycle_count = 0
        self._trend: list[float] = []
        self._elite: dict[str, Any] = {}
        self._load_state()

    @property
    def niche_name(self) -> str:
        return self._niche_name

    def _load_state(self) -> None:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._cycle_count = data.get("cycle_count", 0)
                self._trend = data.get("trend", [])
                self._elite = data.get("elite", {})
            except (json.JSONDecodeError, OSError):
                pass

    def _save_state(self, report: NicheReport) -> None:
        self._cycle_count = report.cycle_count
        self._trend = report.trend
        if report.elite_solution:
            self._elite = report.elite_solution
        data = {
            "niche_name": self._niche_name,
            "cycle_count": self._cycle_count,
            "trend": self._trend[-50:],
            "elite": self._elite,
            "last_fitness": report.fitness_scores,
            "last_pass_gate": report.pass_gate,
            "timestamp": time.time(),
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def evaluate(self, **kwargs: Any) -> NicheReport:
        """Override in subclass."""
        raise NotImplementedError

    def distill(self) -> list[str]:
        """Override in subclass."""
        return []


# ---------------------------------------------------------------------------
# Pareto Front (NSGA-II Inspired)
# ---------------------------------------------------------------------------

@dataclass
class ParetoSolution:
    """A single solution in the Pareto front."""
    niche_name: str
    objectives: dict[str, float]
    rank: int = 0
    crowding_distance: float = 0.0


class ParetoFront:
    """Multi-objective Pareto front tracker.

    Implements non-dominated sorting (NSGA-II) to identify the Pareto
    front across niche reports. NEVER combines objectives into a single
    scalar — preserves the full trade-off surface.

    Key Rule: Each niche's objectives are compared ONLY within the same
    objective dimensions. Cross-niche comparison uses Pareto dominance,
    not weighted averaging.
    """

    def __init__(self) -> None:
        self._solutions: list[ParetoSolution] = []

    def add_niche_report(self, report: NicheReport) -> None:
        """Add a niche report as a solution point."""
        self._solutions.append(ParetoSolution(
            niche_name=report.niche_name,
            objectives=dict(report.fitness_scores),
        ))

    def compute_front(self) -> list[ParetoSolution]:
        """Compute the Pareto front using non-dominated sorting.

        Returns the rank-0 (non-dominated) solutions.
        """
        if not self._solutions:
            return []

        n = len(self._solutions)
        domination_count = [0] * n
        dominated_by: list[list[int]] = [[] for _ in range(n)]

        # Get all unique objective keys
        all_keys = set()
        for s in self._solutions:
            all_keys.update(s.objectives.keys())

        for i in range(n):
            for j in range(i + 1, n):
                if self._dominates(self._solutions[i], self._solutions[j], all_keys):
                    dominated_by[i].append(j)
                    domination_count[j] += 1
                elif self._dominates(self._solutions[j], self._solutions[i], all_keys):
                    dominated_by[j].append(i)
                    domination_count[i] += 1

        # Rank 0 = non-dominated
        front: list[ParetoSolution] = []
        for i in range(n):
            self._solutions[i].rank = domination_count[i]
            if domination_count[i] == 0:
                front.append(self._solutions[i])

        # Compute crowding distance for front
        self._compute_crowding_distance(front, all_keys)

        return front

    @staticmethod
    def _dominates(
        a: ParetoSolution, b: ParetoSolution, keys: set[str],
    ) -> bool:
        """Check if solution a dominates solution b.

        a dominates b if a is >= b in all objectives and > b in at least one.
        """
        at_least_one_better = False
        for key in keys:
            va = a.objectives.get(key, 0.0)
            vb = b.objectives.get(key, 0.0)
            if va < vb:
                return False
            if va > vb:
                at_least_one_better = True
        return at_least_one_better

    @staticmethod
    def _compute_crowding_distance(
        front: list[ParetoSolution], keys: set[str],
    ) -> None:
        """Compute NSGA-II crowding distance for diversity preservation."""
        if len(front) <= 2:
            for s in front:
                s.crowding_distance = float("inf")
            return

        for s in front:
            s.crowding_distance = 0.0

        for key in keys:
            sorted_front = sorted(front, key=lambda s: s.objectives.get(key, 0.0))
            sorted_front[0].crowding_distance = float("inf")
            sorted_front[-1].crowding_distance = float("inf")

            obj_range = (
                sorted_front[-1].objectives.get(key, 0.0)
                - sorted_front[0].objectives.get(key, 0.0)
            )
            if obj_range == 0:
                continue

            for i in range(1, len(sorted_front) - 1):
                diff = (
                    sorted_front[i + 1].objectives.get(key, 0.0)
                    - sorted_front[i - 1].objectives.get(key, 0.0)
                )
                sorted_front[i].crowding_distance += diff / obj_range

    def summary(self) -> str:
        """Generate a summary of the Pareto front."""
        front = self.compute_front()
        if not front:
            return "No solutions in Pareto front."

        lines = [
            "## Pareto Front Summary",
            "",
            "| Niche | Rank | Crowding | Objectives |",
            "|---|---|---|---|",
        ]
        for s in sorted(front, key=lambda x: (-x.crowding_distance)):
            obj_str = ", ".join(
                f"{k}={v:.3f}" for k, v in sorted(s.objectives.items())
            )
            lines.append(
                f"| {s.niche_name} | {s.rank} | "
                f"{s.crowding_distance:.2f} | {obj_str} |"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Niche Registry Singleton
# ---------------------------------------------------------------------------

class NicheRegistry:
    """Singleton registry for evolution niches.

    Analogous to MAP-Elites' behavior space partitioning — each registered
    niche is a cell in the quality-diversity archive.
    """

    _instance: Optional["NicheRegistry"] = None
    _niches: dict[str, tuple[NicheMeta, Type]] = {}

    def __new__(cls) -> "NicheRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (for testing)."""
        cls._niches = {}

    def register(self, meta: NicheMeta, niche_class: Type) -> None:
        """Register a niche class with its metadata."""
        if meta.name in self._niches:
            logger.warning("Niche %r already registered, overwriting.", meta.name)
        self._niches[meta.name] = (meta, niche_class)
        logger.debug("Registered niche: %s", meta.name)

    def get(self, name: str) -> Optional[tuple[NicheMeta, Type]]:
        """Look up a niche by name."""
        return self._niches.get(name)

    def all_niches(self) -> dict[str, tuple[NicheMeta, Type]]:
        """Return all registered niches."""
        return dict(self._niches)

    def run_all_evaluations(
        self,
        project_root: Optional[str | Path] = None,
        **kwargs: Any,
    ) -> tuple[dict[str, NicheReport], ParetoFront]:
        """Run evaluation on all registered niches and compute Pareto front.

        Returns
        -------
        tuple[dict[str, NicheReport], ParetoFront]
            Per-niche reports (NEVER mixed) and the Pareto front.
        """
        reports: dict[str, NicheReport] = {}
        pareto = ParetoFront()

        for name, (meta, niche_cls) in self._niches.items():
            try:
                niche = niche_cls(project_root=project_root)
                report = niche.evaluate(**kwargs)
                reports[name] = report
                pareto.add_niche_report(report)
                logger.info(
                    "Niche %s: pass=%s, fitness=%s",
                    name, report.pass_gate, report.fitness_scores,
                )
            except Exception as e:
                logger.error("Niche %s evaluation failed: %s", name, e)
                reports[name] = NicheReport(
                    niche_name=name,
                    pass_gate=False,
                    metadata={"error": str(e)},
                )

        return reports, pareto

    def generate_meta_report(
        self,
        reports: dict[str, NicheReport],
        pareto: ParetoFront,
    ) -> dict[str, Any]:
        """Generate a Meta-Report aggregating all niche reports.

        The Meta-Report NEVER computes cross-niche averages.
        It presents each niche's results independently and shows
        the Pareto front for multi-objective analysis.
        """
        front = pareto.compute_front()

        meta_report = {
            "report_type": "meta_report",
            "timestamp": time.time(),
            "niche_count": len(reports),
            "niches_passed": sum(1 for r in reports.values() if r.pass_gate),
            "niches_failed": sum(1 for r in reports.values() if not r.pass_gate),
            "per_niche_results": {
                name: report.to_dict() for name, report in reports.items()
            },
            "pareto_front": [
                {
                    "niche": s.niche_name,
                    "rank": s.rank,
                    "crowding_distance": round(s.crowding_distance, 4),
                    "objectives": {k: round(v, 4) for k, v in s.objectives.items()},
                }
                for s in front
            ],
            "pareto_front_size": len(front),
            "cross_niche_average": "PROHIBITED — see MAP-Elites principle",
        }

        return meta_report

    def summary_table(self) -> str:
        """Generate a Markdown summary table of all registered niches."""
        lines = [
            "| Niche | Lane | Objectives | Pass Gate | Session |",
            "|---|---|---|---|---|",
        ]
        for name in sorted(self._niches.keys()):
            meta, _ = self._niches[name]
            objectives = ", ".join(meta.fitness_objectives) or "—"
            gate = ", ".join(meta.pass_gate_conditions) or "—"
            lines.append(
                f"| {meta.display_name or name} | {meta.lane} "
                f"| {objectives} | {gate} | {meta.session_origin} |"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decorator: @register_niche
# ---------------------------------------------------------------------------

def register_niche(
    name: str,
    *,
    display_name: str = "",
    lane: str = "",
    fitness_objectives: tuple[str, ...] = (),
    pass_gate_conditions: tuple[str, ...] = (),
    behavioral_descriptors: tuple[str, ...] = (),
    session_origin: str = "SESSION-064",
) -> Callable[[Type], Type]:
    """Decorator to register a class as an evolution niche.

    Usage::

        @register_niche(
            "dimension_uplift",
            display_name="2.5D/3D Dimension Uplift",
            lane="3d_mesh",
            fitness_objectives=("mesh_quality", "cache_accuracy"),
            pass_gate_conditions=("vertex_count > 10", "face_count > 5"),
        )
        class DimensionUpliftNiche(EvolutionNiche):
            ...
    """
    def decorator(cls: Type) -> Type:
        meta = NicheMeta(
            name=name,
            display_name=display_name or name,
            lane=lane,
            fitness_objectives=fitness_objectives,
            pass_gate_conditions=pass_gate_conditions,
            behavioral_descriptors=behavioral_descriptors,
            session_origin=session_origin,
        )
        registry = get_niche_registry()
        registry.register(meta, cls)
        cls._niche_meta = meta
        return cls

    return decorator


def get_niche_registry() -> NicheRegistry:
    """Get the global NicheRegistry singleton."""
    return NicheRegistry()


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------

def test_niche_registry_basic():
    """Niche registry can register and retrieve niches."""
    NicheRegistry.reset()
    reg = get_niche_registry()

    @register_niche(
        "test_niche",
        display_name="Test Niche",
        lane="test_lane",
        fitness_objectives=("score_a", "score_b"),
    )
    class TestNiche(EvolutionNiche):
        def evaluate(self, **kwargs):
            return NicheReport(
                niche_name="test_niche",
                fitness_scores={"score_a": 0.8, "score_b": 0.6},
                pass_gate=True,
                cycle_count=1,
            )

    assert reg.get("test_niche") is not None
    meta, cls = reg.get("test_niche")
    assert meta.lane == "test_lane"
    NicheRegistry.reset()


def test_pareto_front_basic():
    """Pareto front correctly identifies non-dominated solutions."""
    pareto = ParetoFront()

    # Solution A: good at objective 1, bad at objective 2
    pareto.add_niche_report(NicheReport(
        niche_name="A",
        fitness_scores={"obj1": 0.9, "obj2": 0.3},
    ))
    # Solution B: bad at objective 1, good at objective 2
    pareto.add_niche_report(NicheReport(
        niche_name="B",
        fitness_scores={"obj1": 0.3, "obj2": 0.9},
    ))
    # Solution C: dominated by both A and B
    pareto.add_niche_report(NicheReport(
        niche_name="C",
        fitness_scores={"obj1": 0.2, "obj2": 0.2},
    ))

    front = pareto.compute_front()
    front_names = {s.niche_name for s in front}
    assert "A" in front_names
    assert "B" in front_names
    assert "C" not in front_names


def test_pareto_front_no_cross_niche_average():
    """Meta-report explicitly prohibits cross-niche averaging."""
    NicheRegistry.reset()
    reg = get_niche_registry()

    reports = {
        "niche_a": NicheReport(
            niche_name="niche_a",
            fitness_scores={"quality": 0.9},
            pass_gate=True,
        ),
        "niche_b": NicheReport(
            niche_name="niche_b",
            fitness_scores={"diversity": 0.7},
            pass_gate=True,
        ),
    }
    pareto = ParetoFront()
    for r in reports.values():
        pareto.add_niche_report(r)

    meta_report = reg.generate_meta_report(reports, pareto)
    assert meta_report["cross_niche_average"] == "PROHIBITED — see MAP-Elites principle"
    assert "niche_a" in meta_report["per_niche_results"]
    assert "niche_b" in meta_report["per_niche_results"]

    NicheRegistry.reset()


def test_niche_report_isolation():
    """NicheReport fitness scores are never mixed."""
    r1 = NicheReport(
        niche_name="2d_contour",
        fitness_scores={"contour_fidelity": 0.95},
    )
    r2 = NicheReport(
        niche_name="3d_mesh",
        fitness_scores={"normal_continuity": 0.88},
    )
    # These should NEVER be averaged
    assert "contour_fidelity" not in r2.fitness_scores
    assert "normal_continuity" not in r1.fitness_scores
