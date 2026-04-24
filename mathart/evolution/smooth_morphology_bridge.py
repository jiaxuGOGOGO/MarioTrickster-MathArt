"""
SESSION-057: Smooth Morphology Evolution Bridge — Three-Layer SDF Character Loop.

Integrates the parametric SDF morphology system (smooth_morphology.py) into the
repository's standard three-layer evolution bridge pattern:

1. **Layer 1 — Evaluate**: Run morphology genotypes through fitness evaluation,
   measuring fill ratio, compactness, symmetry, part diversity, and SDF validity.
2. **Layer 2 — Distill**: Extract reusable rules from top performers (optimal
   blend_k ranges, preferred primitives, scale bounds) and persist to knowledge.
3. **Layer 3 — Fitness Bonus + Trend**: Compute evolution fitness bonus for the
   broader orchestrator and persist trend data for cross-session learning.

Research provenance:
  - Inigo Quilez (Shadertoy): Smooth Minimum (smin) for organic SDF blending
  - Constructive Solid Geometry (CSG): boolean operations on implicit surfaces
  - Parametric morphology: genotype → phenotype decoding via SDF composition
"""
from __future__ import annotations
from .state_vault import resolve_state_path

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass
class SmoothMorphologyMetrics:
    """Metrics captured from one morphology evaluation cycle."""

    cycle_id: int = 0
    population_size: int = 0
    best_fitness: float = 0.0
    avg_fitness: float = 0.0
    diversity_score: float = 0.0
    all_valid_sdf: bool = True
    avg_fill_ratio: float = 0.0
    avg_compactness: float = 0.0
    avg_part_count: float = 0.0
    avg_symmetry: float = 0.0
    pass_gate: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "population_size": self.population_size,
            "best_fitness": round(self.best_fitness, 4),
            "avg_fitness": round(self.avg_fitness, 4),
            "diversity_score": round(self.diversity_score, 4),
            "all_valid_sdf": self.all_valid_sdf,
            "avg_fill_ratio": round(self.avg_fill_ratio, 4),
            "avg_compactness": round(self.avg_compactness, 4),
            "avg_part_count": round(self.avg_part_count, 2),
            "avg_symmetry": round(self.avg_symmetry, 4),
            "pass_gate": self.pass_gate,
            "timestamp": self.timestamp,
        }


@dataclass
class SmoothMorphologyState:
    """Persistent state for the morphology evolution bridge."""

    total_cycles: int = 0
    best_fitness_ever: float = 0.0
    fitness_trend: list[float] = field(default_factory=list)
    diversity_trend: list[float] = field(default_factory=list)
    distilled_rules: dict[str, Any] = field(default_factory=dict)
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "best_fitness_ever": round(self.best_fitness_ever, 4),
            "fitness_trend": [round(f, 4) for f in self.fitness_trend[-50:]],
            "diversity_trend": [round(d, 4) for d in self.diversity_trend[-50:]],
            "distilled_rules": self.distilled_rules,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SmoothMorphologyState:
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            best_fitness_ever=float(data.get("best_fitness_ever", 0.0)),
            fitness_trend=list(data.get("fitness_trend", [])),
            diversity_trend=list(data.get("diversity_trend", [])),
            distilled_rules=dict(data.get("distilled_rules", {})),
            last_updated=str(data.get("last_updated", "")),
        )


@dataclass
class SmoothMorphologyStatus:
    """Repository-audit status for the smooth morphology subsystem."""

    module_exists: bool = False
    test_exists: bool = False
    state_file_exists: bool = False
    knowledge_file_exists: bool = False
    tracked_exports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_exists": self.module_exists,
            "test_exists": self.test_exists,
            "state_file_exists": self.state_file_exists,
            "knowledge_file_exists": self.knowledge_file_exists,
            "tracked_exports": self.tracked_exports,
        }


# ── Status Collector ─────────────────────────────────────────────────────────

def collect_smooth_morphology_status(project_root: str | Path) -> SmoothMorphologyStatus:
    """Collect the persisted state of the smooth morphology subsystem."""
    root = Path(project_root)
    module_path = root / "mathart" / "animation" / "smooth_morphology.py"
    test_path = root / "tests" / "test_smooth_morphology.py"
    state_path = resolve_state_path(root, ".smooth_morphology_state.json")
    knowledge_path = root / "knowledge" / "smooth_morphology_rules.md"

    tracked_exports: list[str] = []
    if module_path.exists():
        try:
            text = module_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for name in (
            "MorphologyGenotype",
            "MorphologyFactory",
            "render_morphology_silhouette",
            "evaluate_morphology_diversity",
            "evaluate_morphology_quality",
        ):
            if name in text:
                tracked_exports.append(name)

    return SmoothMorphologyStatus(
        module_exists=module_path.exists(),
        test_exists=test_path.exists(),
        state_file_exists=state_path.exists(),
        knowledge_file_exists=knowledge_path.exists(),
        tracked_exports=tracked_exports,
    )


# ── Evolution Bridge ─────────────────────────────────────────────────────────

class SmoothMorphologyEvolutionBridge:
    """Three-layer evolution bridge for the parametric SDF morphology system.

    Follows the repository's standard bridge pattern (see terrain_sensor_bridge.py).
    """

    STATE_FILE = "smooth_morphology_state.json"
    KNOWLEDGE_FILE = "knowledge/smooth_morphology_rules.md"

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root)
        self.state_path = resolve_state_path(self.root, self.STATE_FILE)
        self.knowledge_path = self.root / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _load_state(self) -> SmoothMorphologyState:
        if not self.state_path.exists():
            return SmoothMorphologyState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return SmoothMorphologyState()
        return SmoothMorphologyState.from_dict(data)

    def _save_state(self) -> None:
        self.state.last_updated = datetime.now(timezone.utc).isoformat()
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ── Layer 1: Evaluate ────────────────────────────────────────────────

    def evaluate(
        self,
        population: Optional[list] = None,
        resolution: int = 64,
    ) -> SmoothMorphologyMetrics:
        """Layer 1: Evaluate morphology population fitness."""
        from mathart.animation.smooth_morphology import (
            MorphologyGenotype,
            MorphologyFactory,
            evaluate_morphology_diversity,
            evaluate_morphology_quality,
        )

        metrics = SmoothMorphologyMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Generate population if not provided
        if population is None:
            factory = MorphologyFactory(seed=42)
            archetypes = ["humanoid", "monster", "mech", "slime", "insectoid"]
            population = [factory.generate_random(a) for a in archetypes * 2]

        metrics.population_size = len(population)

        # Evaluate each genotype
        fitnesses = []
        fill_ratios = []
        compactnesses = []
        part_counts = []
        symmetries = []
        all_valid = True

        for g in population:
            try:
                quality = evaluate_morphology_quality(g, resolution)
                fill_ratios.append(quality["fill_ratio"])
                compactnesses.append(quality["compactness"])
                part_counts.append(quality["part_count"])
                symmetries.append(quality["symmetry_score"])

                # Compute fitness
                import math
                fill = quality["fill_ratio"]
                fill_score = math.exp(-((fill - 0.3) ** 2) / (2 * 0.1 ** 2))
                comp = quality["compactness"]
                comp_score = math.exp(-((comp - 2.5) ** 2) / (2 * 1.5 ** 2))
                part_score = min(math.log(quality["part_count"] + 1) / math.log(12), 1.0)
                sym_score = quality["symmetry_score"] * 0.5 + 0.5
                fitness = 0.30 * fill_score + 0.25 * comp_score + 0.25 * part_score + 0.20 * sym_score
                fitnesses.append(fitness)
            except Exception:
                all_valid = False
                fitnesses.append(0.0)

        metrics.all_valid_sdf = all_valid
        metrics.best_fitness = float(max(fitnesses)) if fitnesses else 0.0
        metrics.avg_fitness = float(np.mean(fitnesses)) if fitnesses else 0.0
        metrics.avg_fill_ratio = float(np.mean(fill_ratios)) if fill_ratios else 0.0
        metrics.avg_compactness = float(np.mean(compactnesses)) if compactnesses else 0.0
        metrics.avg_part_count = float(np.mean(part_counts)) if part_counts else 0.0
        metrics.avg_symmetry = float(np.mean(symmetries)) if symmetries else 0.0

        # Diversity
        if len(population) >= 2:
            metrics.diversity_score = evaluate_morphology_diversity(
                population[:min(10, len(population))], resolution=32
            )

        # Pass gate: fitness > 0.3 and all valid
        metrics.pass_gate = metrics.best_fitness > 0.3 and all_valid

        return metrics

    # ── Layer 2: Distill ─────────────────────────────────────────────────

    def distill(self, metrics: SmoothMorphologyMetrics) -> dict[str, Any]:
        """Layer 2: Distill rules from evaluation results."""
        rules = {}

        # Rule: optimal fitness range
        if metrics.avg_fitness > 0:
            rules["target_fitness_range"] = [
                round(max(0.0, metrics.avg_fitness - 0.1), 4),
                round(min(1.0, metrics.avg_fitness + 0.2), 4),
            ]

        # Rule: fill ratio sweet spot
        if metrics.avg_fill_ratio > 0:
            rules["fill_ratio_sweet_spot"] = [
                round(max(0.05, metrics.avg_fill_ratio - 0.1), 4),
                round(min(0.8, metrics.avg_fill_ratio + 0.1), 4),
            ]

        # Rule: diversity threshold
        rules["min_diversity"] = round(max(0.1, metrics.diversity_score * 0.8), 4)

        # Persist rules
        self.state.distilled_rules.update(rules)
        self._write_knowledge(rules, metrics)

        return rules

    def _write_knowledge(self, rules: dict, metrics: SmoothMorphologyMetrics) -> None:
        """Write distilled knowledge to markdown file."""
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Smooth Morphology — Distilled Knowledge",
            "",
            f"Last updated: {datetime.now(timezone.utc).isoformat()}",
            f"Cycle: {metrics.cycle_id}",
            "",
            "## Distilled Rules",
            "",
        ]
        for key, value in rules.items():
            lines.append(f"- **{key}**: {value}")
        lines.append("")
        lines.append("## Metrics Snapshot")
        lines.append("")
        lines.append(f"- Best fitness: {metrics.best_fitness:.4f}")
        lines.append(f"- Avg fitness: {metrics.avg_fitness:.4f}")
        lines.append(f"- Diversity: {metrics.diversity_score:.4f}")
        lines.append(f"- All valid SDF: {metrics.all_valid_sdf}")
        lines.append("")

        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")

    # ── Layer 3: Fitness Bonus + Trend ───────────────────────────────────

    def compute_fitness_bonus(self, metrics: SmoothMorphologyMetrics) -> float:
        """Layer 3: Compute fitness bonus for the broader orchestrator."""
        bonus = 0.0
        if metrics.pass_gate:
            bonus += 0.1
        if metrics.diversity_score > 0.3:
            bonus += 0.05
        if metrics.all_valid_sdf:
            bonus += 0.05
        return bonus

    def update_trends(self, metrics: SmoothMorphologyMetrics) -> None:
        """Layer 3: Update persistent trend data."""
        self.state.total_cycles += 1
        self.state.fitness_trend.append(metrics.best_fitness)
        self.state.diversity_trend.append(metrics.diversity_score)
        self.state.best_fitness_ever = max(
            self.state.best_fitness_ever, metrics.best_fitness
        )
        self._save_state()

    # ── Full Cycle ───────────────────────────────────────────────────────

    def run_full_cycle(
        self,
        population: Optional[list] = None,
        resolution: int = 64,
    ) -> tuple[SmoothMorphologyMetrics, dict[str, Any], float]:
        """Run a complete three-layer cycle.

        Returns (metrics, distilled_rules, fitness_bonus).
        """
        metrics = self.evaluate(population, resolution)
        rules = self.distill(metrics)
        bonus = self.compute_fitness_bonus(metrics)
        self.update_trends(metrics)
        return metrics, rules, bonus
