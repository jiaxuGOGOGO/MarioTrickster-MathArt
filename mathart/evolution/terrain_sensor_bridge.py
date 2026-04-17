"""Gap B2 bridge — scene-aware distance sensor as a three-layer evolution cycle.

SESSION-048: This module turns SDF terrain sensing + TTC prediction into a
repository-native closed loop:

1. Layer 1 — Evaluate whether terrain sensing is accurate, TTC predictions
   converge, and phase reaches 1.0 at contact across diverse terrain shapes.
2. Layer 2 — Distill reusable rules from measured results (e.g. gravity
   tuning, brace timing, slope compensation).
3. Layer 3 — Persist trends so future sessions can tune toward lower error
   and better landing synchronisation.

Research provenance:
  - Simon Clavet (GDC 2016): trajectory prediction + obstacle reaction
  - UE5 Distance Matching (Laurent Delayen / Paragon): distance-curve playback
  - TTC perceptual science: D/|v| and quadratic free-fall refinement
  - Environment-aware Motion Matching (Pontón et al., SIGGRAPH 2025)
  - Falling and Landing Motion Control (Ha et al., SIGGRAPH Asia 2012)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np


# ── Metrics ───────────────────────────────────────────────────────────────────

@dataclass
class TerrainSensorMetrics:
    """Metrics captured from one Gap B2 evaluation cycle."""

    cycle_id: int = 0
    frame_count: int = 0
    terrain_count: int = 0
    mean_distance_error: float = 0.0
    max_distance_error: float = 0.0
    mean_ttc_error: float = 0.0
    mean_phase_at_contact: float = 0.0
    phase_monotonic_rate: float = 1.0
    ttc_decreasing_rate: float = 1.0
    mean_brace_timing_error: float = 0.0
    pass_gate: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "frame_count": self.frame_count,
            "terrain_count": self.terrain_count,
            "mean_distance_error": self.mean_distance_error,
            "max_distance_error": self.max_distance_error,
            "mean_ttc_error": self.mean_ttc_error,
            "mean_phase_at_contact": self.mean_phase_at_contact,
            "phase_monotonic_rate": self.phase_monotonic_rate,
            "ttc_decreasing_rate": self.ttc_decreasing_rate,
            "mean_brace_timing_error": self.mean_brace_timing_error,
            "pass_gate": self.pass_gate,
            "timestamp": self.timestamp,
        }


# ── Persistent State ──────────────────────────────────────────────────────────

@dataclass
class TerrainSensorState:
    """Persistent state for Gap B2 terrain sensor evolution."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    consecutive_passes: int = 0
    best_mean_distance_error: float = 1.0
    best_mean_phase_at_contact: float = 0.0
    knowledge_rules_total: int = 0
    distance_error_trend: list[float] = field(default_factory=list)
    phase_at_contact_trend: list[float] = field(default_factory=list)
    ttc_error_trend: list[float] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "consecutive_passes": self.consecutive_passes,
            "best_mean_distance_error": self.best_mean_distance_error,
            "best_mean_phase_at_contact": self.best_mean_phase_at_contact,
            "knowledge_rules_total": self.knowledge_rules_total,
            "distance_error_trend": self.distance_error_trend[-50:],
            "phase_at_contact_trend": self.phase_at_contact_trend[-50:],
            "ttc_error_trend": self.ttc_error_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerrainSensorState":
        return cls(
            total_cycles=data.get("total_cycles", 0),
            total_passes=data.get("total_passes", 0),
            total_failures=data.get("total_failures", 0),
            consecutive_passes=data.get("consecutive_passes", 0),
            best_mean_distance_error=data.get("best_mean_distance_error", 1.0),
            best_mean_phase_at_contact=data.get("best_mean_phase_at_contact", 0.0),
            knowledge_rules_total=data.get("knowledge_rules_total", 0),
            distance_error_trend=data.get("distance_error_trend", []),
            phase_at_contact_trend=data.get("phase_at_contact_trend", []),
            ttc_error_trend=data.get("ttc_error_trend", []),
            history=data.get("history", []),
        )


# ── Repository Status ─────────────────────────────────────────────────────────

@dataclass
class TerrainSensorStatus:
    """Repository integration status for Gap B2."""

    module_exists: bool = False
    bridge_exists: bool = False
    public_api_exports_sensor: bool = False
    pipeline_supports_terrain_sensor: bool = False
    test_exists: bool = False
    research_notes_path: str = ""
    tracked_exports: list[str] = field(default_factory=list)
    total_cycles: int = 0
    consecutive_passes: int = 0
    best_mean_distance_error: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def collect_terrain_sensor_status(project_root: str | Path) -> TerrainSensorStatus:
    """Collect repository integration status for Gap B2."""
    root = Path(project_root)
    module_path = root / "mathart/animation/terrain_sensor.py"
    bridge_path = root / "mathart/evolution/terrain_sensor_bridge.py"
    api_module = root / "mathart/animation/__init__.py"
    pipeline_module = root / "mathart/pipeline.py"
    test_path = root / "tests/test_terrain_sensor.py"
    notes_path = root / "docs/research/GAP_B2_TERRAIN_SENSOR_TTC.md"
    state_path = root / ".terrain_sensor_state.json"

    tracked_exports: list[str] = []
    if module_path.exists():
        text = module_path.read_text(encoding="utf-8", errors="replace")
        for name in (
            "TerrainSDF",
            "TerrainRaySensor",
            "TTCPredictor",
            "scene_aware_distance_phase",
            "scene_aware_fall_frame",
            "create_flat_terrain",
            "create_step_terrain",
        ):
            if name in text:
                tracked_exports.append(name)

    api_exports = False
    if api_module.exists():
        api_text = api_module.read_text(encoding="utf-8", errors="replace")
        api_exports = "TerrainSDF" in api_text and "TTCPredictor" in api_text

    pipeline_support = False
    if pipeline_module.exists():
        pipeline_text = pipeline_module.read_text(encoding="utf-8", errors="replace")
        pipeline_support = all(name in pipeline_text for name in (
            "terrain_sensor", "scene_aware_fall_frame", "TerrainSDF",
        ))

    total_cycles = 0
    consecutive_passes = 0
    best_mean_distance_error = 1.0
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            total_cycles = state_data.get("total_cycles", 0)
            consecutive_passes = state_data.get("consecutive_passes", 0)
            best_mean_distance_error = state_data.get("best_mean_distance_error", 1.0)
        except (json.JSONDecodeError, OSError):
            pass

    return TerrainSensorStatus(
        module_exists=module_path.exists(),
        bridge_exists=bridge_path.exists(),
        public_api_exports_sensor=api_exports,
        pipeline_supports_terrain_sensor=pipeline_support,
        test_exists=test_path.exists(),
        research_notes_path=str(notes_path.relative_to(root)) if notes_path.exists() else "",
        tracked_exports=tracked_exports,
        total_cycles=total_cycles,
        consecutive_passes=consecutive_passes,
        best_mean_distance_error=best_mean_distance_error,
    )


# ── Evolution Bridge ──────────────────────────────────────────────────────────

class TerrainSensorEvolutionBridge:
    """Three-layer evolution bridge for Gap B2 terrain sensing + TTC."""

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    def evaluate_terrain_sensor(
        self,
        diagnostics: Optional[list[dict[str, Any]]] = None,
        *,
        max_mean_distance_error: float = 0.05,
        min_phase_at_contact: float = 0.95,
        min_monotonic_rate: float = 0.90,
    ) -> TerrainSensorMetrics:
        """Layer 1: evaluate terrain sensor accuracy and TTC convergence."""
        metrics = TerrainSensorMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        if diagnostics:
            metrics.frame_count = sum(d.get("frame_count", 0) for d in diagnostics)
            metrics.terrain_count = len(diagnostics)

            distance_errors = [d.get("mean_distance_error", 0.0) for d in diagnostics]
            max_errors = [d.get("max_distance_error", 0.0) for d in diagnostics]
            ttc_errors = [d.get("mean_ttc_error", 0.0) for d in diagnostics]
            phases_at_contact = [d.get("phase_at_contact", 0.0) for d in diagnostics]
            monotonic_flags = [d.get("phase_monotonic", True) for d in diagnostics]
            ttc_dec_flags = [d.get("ttc_decreasing", True) for d in diagnostics]

            if distance_errors:
                metrics.mean_distance_error = float(np.mean(distance_errors))
                metrics.max_distance_error = float(np.max(max_errors)) if max_errors else 0.0
            if ttc_errors:
                metrics.mean_ttc_error = float(np.mean(ttc_errors))
            if phases_at_contact:
                metrics.mean_phase_at_contact = float(np.mean(phases_at_contact))
            if monotonic_flags:
                metrics.phase_monotonic_rate = float(np.mean([1.0 if m else 0.0 for m in monotonic_flags]))
            if ttc_dec_flags:
                metrics.ttc_decreasing_rate = float(np.mean([1.0 if d else 0.0 for d in ttc_dec_flags]))

        metrics.pass_gate = (
            metrics.terrain_count > 0
            and metrics.mean_distance_error <= max_mean_distance_error
            and metrics.mean_phase_at_contact >= min_phase_at_contact
            and metrics.phase_monotonic_rate >= min_monotonic_rate
        )
        self._update_state(metrics)
        self._save_state()
        return metrics

    def distill_terrain_sensor_knowledge(self, metrics: TerrainSensorMetrics) -> list[dict[str, str]]:
        """Layer 2: turn metrics into durable repository rules."""
        rules: list[dict[str, str]] = []

        if metrics.mean_distance_error > 0.05:
            rules.append({
                "rule_id": f"terrain_distance_error_{metrics.cycle_id}",
                "rule_text": (
                    "When SDF terrain distance error rises, increase ray marching steps "
                    "or reduce min_distance threshold. For complex terrains, prefer "
                    "sphere tracing over direct SDF query."
                ),
            })

        if metrics.mean_phase_at_contact < 0.95:
            rules.append({
                "rule_id": f"terrain_phase_contact_{metrics.cycle_id}",
                "rule_text": (
                    "If phase does not reach ≥0.95 at contact, the reference TTC is "
                    "miscalibrated. Use the initial TTC at fall-start as reference, "
                    "or switch to quadratic TTC with gravity correction."
                ),
            })

        if metrics.phase_monotonic_rate < 0.90:
            rules.append({
                "rule_id": f"terrain_phase_monotonic_{metrics.cycle_id}",
                "rule_text": (
                    "Non-monotonic phase during fall indicates terrain SDF discontinuity "
                    "or velocity sign flip. Smooth the terrain SDF at step edges and "
                    "clamp phase to never decrease."
                ),
            })

        if metrics.mean_ttc_error > 0.1:
            rules.append({
                "rule_id": f"terrain_ttc_error_{metrics.cycle_id}",
                "rule_text": (
                    "TTC prediction error exceeds threshold. Enable gravity-aware "
                    "quadratic TTC formula and verify that velocity_y is correctly "
                    "signed (negative for falling)."
                ),
            })

        if metrics.pass_gate:
            rules.append({
                "rule_id": f"terrain_pass_{metrics.cycle_id}",
                "rule_text": (
                    "Preferred Gap B2 recipe: TerrainSDF query at foot position → "
                    "sphere-traced distance → quadratic TTC with gravity → "
                    "phase = 1 - (ttc / reference_ttc) → brace signal at TTC < 0.3s → "
                    "landing preparation eased from TTC < 0.5s → "
                    "surface normal slope compensation in pose."
                ),
            })

        if not rules:
            rules.append({
                "rule_id": f"terrain_neutral_{metrics.cycle_id}",
                "rule_text": (
                    "Gap B2 evaluation produced no actionable exception; keep the "
                    "current SDF terrain sensor + TTC recipe and continue collecting "
                    "trend data across diverse terrain shapes."
                ),
            })

        knowledge_dir = self.project_root / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        knowledge_path = knowledge_dir / "terrain_sensor_ttc_rules.md"
        with knowledge_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## Cycle {metrics.cycle_id} — {metrics.timestamp}\n\n")
            for rule in rules:
                f.write(f"- **{rule['rule_id']}**: {rule['rule_text']}\n")
        self.state.knowledge_rules_total += len(rules)
        self._save_state()
        return rules

    def compute_terrain_sensor_fitness_bonus(self, metrics: TerrainSensorMetrics) -> float:
        """Layer 3: convert metrics into a small fitness bonus/penalty."""
        bonus = 0.0
        # Distance accuracy bonus
        bonus += max(-0.12, min(0.12, 0.05 - metrics.mean_distance_error))
        # Phase at contact bonus
        if metrics.mean_phase_at_contact >= 0.98:
            bonus += 0.05
        elif metrics.mean_phase_at_contact >= 0.95:
            bonus += 0.03
        elif metrics.mean_phase_at_contact < 0.80:
            bonus -= 0.06
        # Monotonicity bonus
        if metrics.phase_monotonic_rate >= 0.95:
            bonus += 0.03
        elif metrics.phase_monotonic_rate < 0.80:
            bonus -= 0.04
        # TTC convergence bonus
        if metrics.ttc_decreasing_rate >= 0.95:
            bonus += 0.02
        # Pass gate bonus
        if metrics.pass_gate:
            bonus += 0.04
        return float(np.clip(bonus, -0.20, 0.20))

    def status_report(self) -> str:
        """Human-readable summary for engine status panels."""
        status = collect_terrain_sensor_status(self.project_root)
        lines = [
            "--- Terrain Sensor Evolution Bridge (SESSION-048 / Gap B2) ---",
            f"  Total cycles: {self.state.total_cycles}",
            f"  Passes / failures: {self.state.total_passes} / {self.state.total_failures}",
            f"  Consecutive passes: {self.state.consecutive_passes}",
            f"  Best mean distance error: {self.state.best_mean_distance_error:.4f}",
            f"  Best mean phase at contact: {self.state.best_mean_phase_at_contact:.4f}",
            f"  Module active: {'yes' if status.module_exists else 'no'}",
            f"  Pipeline integration: {'yes' if status.pipeline_supports_terrain_sensor else 'no'}",
            f"  Public API export: {'yes' if status.public_api_exports_sensor else 'no'}",
            f"  Test present: {'yes' if status.test_exists else 'no'}",
        ]
        if status.tracked_exports:
            lines.append(f"  Tracked exports: {', '.join(status.tracked_exports)}")
        return "\n".join(lines)

    # ── Private state management ──

    def _state_path(self) -> Path:
        return self.project_root / ".terrain_sensor_state.json"

    def _load_state(self) -> TerrainSensorState:
        path = self._state_path()
        if not path.exists():
            return TerrainSensorState()
        try:
            return TerrainSensorState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return TerrainSensorState()

    def _save_state(self) -> None:
        self._state_path().write_text(
            json.dumps(self.state.to_dict(), indent=2),
            encoding="utf-8",
        )

    def _update_state(self, metrics: TerrainSensorMetrics) -> None:
        self.state.total_cycles += 1
        if metrics.pass_gate:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
        self.state.best_mean_distance_error = min(
            self.state.best_mean_distance_error,
            metrics.mean_distance_error if metrics.terrain_count > 0 else self.state.best_mean_distance_error,
        )
        self.state.best_mean_phase_at_contact = max(
            self.state.best_mean_phase_at_contact,
            metrics.mean_phase_at_contact,
        )
        self.state.distance_error_trend.append(metrics.mean_distance_error)
        self.state.phase_at_contact_trend.append(metrics.mean_phase_at_contact)
        self.state.ttc_error_trend.append(metrics.mean_ttc_error)
        self.state.history.append(metrics.to_dict())


__all__ = [
    "TerrainSensorMetrics",
    "TerrainSensorState",
    "TerrainSensorStatus",
    "TerrainSensorEvolutionBridge",
    "collect_terrain_sensor_status",
]
