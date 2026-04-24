"""
SESSION-057: Constraint-Aware WFC Evolution Bridge — Three-Layer Level Loop.

Integrates the constraint-driven WFC system (constraint_wfc.py) with TTC
reachability validation into the repository's standard three-layer evolution
bridge pattern:

1. **Layer 1 — Evaluate**: Generate levels with physics constraints, measure
   playability rate, difficulty distribution, platform variety, and tile diversity.
2. **Layer 2 — Distill**: Extract reusable rules from successful levels (optimal
   gap sizes, platform density, difficulty curves) and persist to knowledge.
3. **Layer 3 — Fitness Bonus + Trend**: Compute level quality bonus for the
   broader orchestrator and persist trend data for cross-session learning.

Research provenance:
  - Maxim Gumin (2016): Wave Function Collapse — constraint-aware tile generation
  - Oskar Stålberg (Townscaper): WFC with domain constraints for organic structures
  - SESSION-048 TTC: Time-to-Contact predictor for reachability validation
  - Lee et al. (2020): Precomputing player movement for reachability guarantees
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
class ConstraintWFCMetrics:
    """Metrics captured from one WFC evaluation cycle."""

    cycle_id: int = 0
    levels_generated: int = 0
    playability_rate: float = 0.0
    avg_difficulty: float = 0.0
    difficulty_variance: float = 0.0
    avg_platform_count: float = 0.0
    avg_tile_types: float = 0.0
    veto_rate: float = 0.0
    avg_generation_attempts: float = 0.0
    pass_gate: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "levels_generated": self.levels_generated,
            "playability_rate": round(self.playability_rate, 4),
            "avg_difficulty": round(self.avg_difficulty, 4),
            "difficulty_variance": round(self.difficulty_variance, 4),
            "avg_platform_count": round(self.avg_platform_count, 2),
            "avg_tile_types": round(self.avg_tile_types, 2),
            "veto_rate": round(self.veto_rate, 4),
            "avg_generation_attempts": round(self.avg_generation_attempts, 2),
            "pass_gate": self.pass_gate,
            "timestamp": self.timestamp,
        }


@dataclass
class ConstraintWFCState:
    """Persistent state for the constraint WFC evolution bridge."""

    total_cycles: int = 0
    best_playability_ever: float = 0.0
    playability_trend: list[float] = field(default_factory=list)
    difficulty_trend: list[float] = field(default_factory=list)
    distilled_rules: dict[str, Any] = field(default_factory=dict)
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "best_playability_ever": round(self.best_playability_ever, 4),
            "playability_trend": [round(p, 4) for p in self.playability_trend[-50:]],
            "difficulty_trend": [round(d, 4) for d in self.difficulty_trend[-50:]],
            "distilled_rules": self.distilled_rules,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConstraintWFCState:
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            best_playability_ever=float(data.get("best_playability_ever", 0.0)),
            playability_trend=list(data.get("playability_trend", [])),
            difficulty_trend=list(data.get("difficulty_trend", [])),
            distilled_rules=dict(data.get("distilled_rules", {})),
            last_updated=str(data.get("last_updated", "")),
        )


@dataclass
class ConstraintWFCStatus:
    """Repository-audit status for the constraint WFC subsystem."""

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

def collect_constraint_wfc_status(project_root: str | Path) -> ConstraintWFCStatus:
    """Collect the persisted state of the constraint WFC subsystem."""
    root = Path(project_root)
    module_path = root / "mathart" / "level" / "constraint_wfc.py"
    test_path = root / "tests" / "test_constraint_wfc.py"
    state_path = resolve_state_path(root, ".constraint_wfc_state.json")
    knowledge_path = root / "knowledge" / "constraint_wfc_rules.md"

    tracked_exports: list[str] = []
    if module_path.exists():
        try:
            text = module_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for name in (
            "ConstraintAwareWFC",
            "PhysicsConstraint",
            "ReachabilityValidator",
            "TilePlatformExtractor",
        ):
            if name in text:
                tracked_exports.append(name)

    return ConstraintWFCStatus(
        module_exists=module_path.exists(),
        test_exists=test_path.exists(),
        state_file_exists=state_path.exists(),
        knowledge_file_exists=knowledge_path.exists(),
        tracked_exports=tracked_exports,
    )


# ── Evolution Bridge ─────────────────────────────────────────────────────────

class ConstraintWFCEvolutionBridge:
    """Three-layer evolution bridge for the constraint-aware WFC system.

    Follows the repository's standard bridge pattern (see terrain_sensor_bridge.py).
    """

    STATE_FILE = "constraint_wfc_state.json"
    KNOWLEDGE_FILE = "knowledge/constraint_wfc_rules.md"

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root)
        self.state_path = resolve_state_path(self.root, self.STATE_FILE)
        self.knowledge_path = self.root / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _load_state(self) -> ConstraintWFCState:
        if not self.state_path.exists():
            return ConstraintWFCState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ConstraintWFCState()
        return ConstraintWFCState.from_dict(data)

    def _save_state(self) -> None:
        self.state.last_updated = datetime.now(timezone.utc).isoformat()
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ── Layer 1: Evaluate ────────────────────────────────────────────────

    def evaluate(
        self,
        n_levels: int = 10,
        width: int = 22,
        height: int = 7,
        seed: int = 42,
    ) -> ConstraintWFCMetrics:
        """Layer 1: Generate and evaluate constraint-aware levels."""
        from mathart.level.constraint_wfc import (
            ConstraintAwareWFC,
            PhysicsConstraint,
            ReachabilityValidator,
            TilePlatformExtractor,
        )
        from mathart.level.templates import parse_fragment

        metrics = ConstraintWFCMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        physics = PhysicsConstraint.mario_default()
        validator = ReachabilityValidator(physics)

        playable_count = 0
        difficulties = []
        platform_counts = []
        tile_type_counts = []
        total_vetos = 0
        total_attempts = 0

        for i in range(n_levels):
            try:
                gen = ConstraintAwareWFC(
                    physics=physics,
                    seed=seed + i,
                )
                gen.learn()
                level = gen.generate(
                    width, height,
                    validate_playability=True,
                    max_retries=20,
                )
                metrics.levels_generated += 1

                # Check playability
                grid = parse_fragment(level)
                is_playable, _ = validator.validate_level(grid)
                if is_playable:
                    playable_count += 1

                # Difficulty
                cols = max(len(row) for row in grid) if grid else 0
                diffs = [validator.difficulty_at_column(grid, c) for c in range(cols)]
                if diffs:
                    difficulties.append(float(np.mean(diffs)))

                # Platform count
                platforms = TilePlatformExtractor.extract_platforms(grid)
                platform_counts.append(len(platforms))

                # Tile types
                from collections import Counter
                all_tiles = [t for row in grid for t in row]
                tile_type_counts.append(len(Counter(all_tiles)))

                # Stats
                stats = gen.generation_stats
                total_vetos += stats.get("veto_count", 0)
                total_attempts += stats.get("attempts", 1)

            except RuntimeError:
                pass

        total = max(metrics.levels_generated, 1)
        metrics.playability_rate = playable_count / total
        metrics.avg_difficulty = float(np.mean(difficulties)) if difficulties else 0.0
        metrics.difficulty_variance = float(np.std(difficulties)) if difficulties else 0.0
        metrics.avg_platform_count = float(np.mean(platform_counts)) if platform_counts else 0.0
        metrics.avg_tile_types = float(np.mean(tile_type_counts)) if tile_type_counts else 0.0
        metrics.veto_rate = total_vetos / max(total_attempts, 1)
        metrics.avg_generation_attempts = total_attempts / total

        # Pass gate: >70% playable and reasonable difficulty
        metrics.pass_gate = (
            metrics.playability_rate >= 0.7
            and metrics.avg_difficulty > 0.05
        )

        return metrics

    # ── Layer 2: Distill ─────────────────────────────────────────────────

    def distill(self, metrics: ConstraintWFCMetrics) -> dict[str, Any]:
        """Layer 2: Distill rules from evaluation results."""
        rules = {}

        # Rule: target playability
        rules["target_playability"] = round(
            max(0.7, metrics.playability_rate * 0.95), 4
        )

        # Rule: difficulty sweet spot
        if metrics.avg_difficulty > 0:
            rules["difficulty_sweet_spot"] = [
                round(max(0.0, metrics.avg_difficulty - 0.15), 4),
                round(min(1.0, metrics.avg_difficulty + 0.15), 4),
            ]

        # Rule: optimal platform density
        if metrics.avg_platform_count > 0:
            rules["platform_density_target"] = round(metrics.avg_platform_count, 1)

        # Rule: veto effectiveness
        rules["veto_effectiveness"] = round(1.0 - metrics.veto_rate, 4)

        # Persist rules
        self.state.distilled_rules.update(rules)
        self._write_knowledge(rules, metrics)

        return rules

    def _write_knowledge(self, rules: dict, metrics: ConstraintWFCMetrics) -> None:
        """Write distilled knowledge to markdown file."""
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Constraint-Aware WFC — Distilled Knowledge",
            "",
            f"Last updated: {datetime.now(timezone.utc).isoformat()}",
            f"Cycle: {metrics.cycle_id}",
            "",
            "## Research Foundation",
            "",
            "- **Maxim Gumin (2016)**: WFC as constraint solver with stationary distribution",
            "- **Oskar Stålberg (Townscaper)**: Domain constraints in WFC for organic generation",
            "- **SESSION-048 TTC**: Physics-based reachability validation during collapse phase",
            "",
            "## Distilled Rules",
            "",
        ]
        for key, value in rules.items():
            lines.append(f"- **{key}**: {value}")
        lines.append("")
        lines.append("## Metrics Snapshot")
        lines.append("")
        lines.append(f"- Levels generated: {metrics.levels_generated}")
        lines.append(f"- Playability rate: {metrics.playability_rate:.1%}")
        lines.append(f"- Avg difficulty: {metrics.avg_difficulty:.4f}")
        lines.append(f"- Avg platform count: {metrics.avg_platform_count:.1f}")
        lines.append(f"- Veto rate: {metrics.veto_rate:.4f}")
        lines.append("")

        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")

    # ── Layer 3: Fitness Bonus + Trend ───────────────────────────────────

    def compute_fitness_bonus(self, metrics: ConstraintWFCMetrics) -> float:
        """Layer 3: Compute fitness bonus for the broader orchestrator."""
        bonus = 0.0
        if metrics.pass_gate:
            bonus += 0.1
        if metrics.playability_rate >= 0.9:
            bonus += 0.1  # Extra bonus for high playability
        if metrics.difficulty_variance > 0.05:
            bonus += 0.05  # Reward difficulty variation
        return bonus

    def update_trends(self, metrics: ConstraintWFCMetrics) -> None:
        """Layer 3: Update persistent trend data."""
        self.state.total_cycles += 1
        self.state.playability_trend.append(metrics.playability_rate)
        self.state.difficulty_trend.append(metrics.avg_difficulty)
        self.state.best_playability_ever = max(
            self.state.best_playability_ever, metrics.playability_rate
        )
        self._save_state()

    # ── Full Cycle ───────────────────────────────────────────────────────

    def run_full_cycle(
        self,
        n_levels: int = 10,
        width: int = 22,
        height: int = 7,
        seed: int = 42,
    ) -> tuple[ConstraintWFCMetrics, dict[str, Any], float]:
        """Run a complete three-layer cycle.

        Returns (metrics, distilled_rules, fitness_bonus).
        """
        metrics = self.evaluate(n_levels, width, height, seed)
        rules = self.distill(metrics)
        bonus = self.compute_fitness_bonus(metrics)
        self.update_trends(metrics)
        return metrics, rules, bonus
