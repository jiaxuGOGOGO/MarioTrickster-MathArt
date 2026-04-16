"""Gap C2 bridge: grid-based fluid VFX into the three-layer evolution loop.

This module formalizes the repository's Stable Fluids upgrade as a repeatable
three-layer cycle:

1. Layer 1 — Internal Evolution Gate
   Render fluid VFX, measure flow energy / obstacle leakage / particle support,
   and reject dead or leaky effects.
2. Layer 2 — External Knowledge Distillation
   Persist reusable rules extracted from successful or failed runs.
3. Layer 3 — Self-Iterative Testing
   Track trends over time and convert them into a fitness bonus / penalty.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FluidVFXMetrics:
    """Metrics captured from one fluid VFX evaluation cycle."""

    cycle_id: int = 0
    frame_count: int = 0
    mean_flow_energy: float = 0.0
    max_flow_speed: float = 0.0
    mean_density_mass: float = 0.0
    mean_obstacle_leak_ratio: float = 0.0
    mean_active_particles: float = 0.0
    obstacle_coverage: float = 0.0
    visual_alpha_coverage: float = 0.0
    fluid_pass: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "frame_count": self.frame_count,
            "mean_flow_energy": self.mean_flow_energy,
            "max_flow_speed": self.max_flow_speed,
            "mean_density_mass": self.mean_density_mass,
            "mean_obstacle_leak_ratio": self.mean_obstacle_leak_ratio,
            "mean_active_particles": self.mean_active_particles,
            "obstacle_coverage": self.obstacle_coverage,
            "visual_alpha_coverage": self.visual_alpha_coverage,
            "fluid_pass": self.fluid_pass,
            "timestamp": self.timestamp,
        }


@dataclass
class FluidVFXState:
    """Persistent state for Gap C2 VFX evolution tracking."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_flow_energy: float = 0.0
    lowest_obstacle_leak_ratio: float = 1.0
    best_visual_alpha_coverage: float = 0.0
    consecutive_passes: int = 0
    knowledge_rules_total: int = 0
    flow_energy_trend: list[float] = field(default_factory=list)
    obstacle_leak_trend: list[float] = field(default_factory=list)
    particle_trend: list[float] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_flow_energy": self.best_flow_energy,
            "lowest_obstacle_leak_ratio": self.lowest_obstacle_leak_ratio,
            "best_visual_alpha_coverage": self.best_visual_alpha_coverage,
            "consecutive_passes": self.consecutive_passes,
            "knowledge_rules_total": self.knowledge_rules_total,
            "flow_energy_trend": self.flow_energy_trend[-50:],
            "obstacle_leak_trend": self.obstacle_leak_trend[-50:],
            "particle_trend": self.particle_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FluidVFXState":
        return cls(
            total_cycles=d.get("total_cycles", 0),
            total_passes=d.get("total_passes", 0),
            total_failures=d.get("total_failures", 0),
            best_flow_energy=d.get("best_flow_energy", 0.0),
            lowest_obstacle_leak_ratio=d.get("lowest_obstacle_leak_ratio", 1.0),
            best_visual_alpha_coverage=d.get("best_visual_alpha_coverage", 0.0),
            consecutive_passes=d.get("consecutive_passes", 0),
            knowledge_rules_total=d.get("knowledge_rules_total", 0),
            flow_energy_trend=d.get("flow_energy_trend", []),
            obstacle_leak_trend=d.get("obstacle_leak_trend", []),
            particle_trend=d.get("particle_trend", []),
            history=d.get("history", []),
        )


@dataclass
class FluidVFXStatus:
    """Snapshot of Gap C2 integration coverage for report generation."""

    module_exists: bool = False
    bridge_exists: bool = False
    public_api_exports_fluid_vfx: bool = False
    pipeline_supports_fluid_presets: bool = False
    test_exists: bool = False
    research_notes_path: str = ""
    tracked_exports: list[str] = field(default_factory=list)
    total_cycles: int = 0
    consecutive_passes: int = 0
    best_flow_energy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def collect_fluid_vfx_status(project_root: str | Path) -> FluidVFXStatus:
    """Collect repository integration status for Stable Fluids VFX."""
    root = Path(project_root)
    fluid_module = root / "mathart/animation/fluid_vfx.py"
    bridge_module = root / "mathart/evolution/fluid_vfx_bridge.py"
    api_module = root / "mathart/animation/__init__.py"
    pipeline_module = root / "mathart/pipeline.py"
    test_path = root / "tests/test_fluid_vfx.py"
    notes_path = root / "docs/research/GAP_C2_STABLE_FLUIDS_VFX.md"
    state_path = root / ".fluid_vfx_state.json"

    tracked_exports: list[str] = []
    if fluid_module.exists():
        try:
            text = fluid_module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for name in (
            "FluidGrid2D",
            "FluidDrivenVFXSystem",
            "FluidVFXConfig",
            "default_character_obstacle_mask",
        ):
            if name in text:
                tracked_exports.append(name)

    api_exports = False
    if api_module.exists():
        try:
            api_text = api_module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            api_text = ""
        api_exports = "FluidDrivenVFXSystem" in api_text and "FluidGrid2D" in api_text

    pipeline_support = False
    if pipeline_module.exists():
        try:
            pipeline_text = pipeline_module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pipeline_text = ""
        pipeline_support = all(name in pipeline_text for name in ("smoke_fluid", "dash_smoke", "slash_smoke"))

    total_cycles = 0
    consecutive_passes = 0
    best_flow_energy = 0.0
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            total_cycles = state_data.get("total_cycles", 0)
            consecutive_passes = state_data.get("consecutive_passes", 0)
            best_flow_energy = state_data.get("best_flow_energy", 0.0)
        except (json.JSONDecodeError, OSError):
            pass

    return FluidVFXStatus(
        module_exists=fluid_module.exists(),
        bridge_exists=bridge_module.exists(),
        public_api_exports_fluid_vfx=api_exports,
        pipeline_supports_fluid_presets=pipeline_support,
        test_exists=test_path.exists(),
        research_notes_path=str(notes_path.relative_to(root)) if notes_path.exists() else "",
        tracked_exports=tracked_exports,
        total_cycles=total_cycles,
        consecutive_passes=consecutive_passes,
        best_flow_energy=best_flow_energy,
    )


class FluidVFXEvolutionBridge:
    """Three-layer bridge for the Stable Fluids VFX stack."""

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    def evaluate_fluid_vfx(
        self,
        frames: Optional[list[np.ndarray]] = None,
        diagnostics: Optional[list[dict[str, Any]]] = None,
        min_flow_energy: float = 0.0005,
        max_obstacle_leak_ratio: float = 0.05,
        min_alpha_coverage: float = 0.01,
    ) -> FluidVFXMetrics:
        """Layer 1: evaluate whether the generated fluid VFX is alive and sane."""
        metrics = FluidVFXMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if diagnostics:
            metrics.frame_count = len(diagnostics)
            metrics.mean_flow_energy = float(np.mean([d.get("mean_flow_energy", 0.0) for d in diagnostics]))
            metrics.max_flow_speed = float(np.max([d.get("max_flow_speed", 0.0) for d in diagnostics]))
            metrics.mean_density_mass = float(np.mean([d.get("density_mass", 0.0) for d in diagnostics]))
            metrics.mean_obstacle_leak_ratio = float(np.mean([d.get("obstacle_leak_ratio", 0.0) for d in diagnostics]))
            metrics.mean_active_particles = float(np.mean([d.get("active_particles", 0.0) for d in diagnostics]))
            metrics.obstacle_coverage = float(np.mean([d.get("obstacle_coverage", 0.0) for d in diagnostics]))

        if frames:
            alpha_coverages = []
            for frame in frames:
                arr = np.asarray(frame)
                if arr.ndim == 3 and arr.shape[-1] >= 4:
                    alpha_coverages.append(float((arr[..., 3] > 0).mean()))
            if alpha_coverages:
                metrics.visual_alpha_coverage = float(np.mean(alpha_coverages))
                if metrics.frame_count == 0:
                    metrics.frame_count = len(alpha_coverages)

        metrics.fluid_pass = (
            metrics.frame_count > 0
            and metrics.mean_flow_energy >= min_flow_energy
            and metrics.mean_obstacle_leak_ratio <= max_obstacle_leak_ratio
            and metrics.visual_alpha_coverage >= min_alpha_coverage
        )

        self._update_state(metrics)
        self._save_state()
        return metrics

    def distill_fluid_knowledge(self, metrics: FluidVFXMetrics) -> list[dict[str, str]]:
        """Layer 2: turn evaluation results into durable project rules."""
        rules: list[dict[str, str]] = []

        if metrics.mean_flow_energy < 0.0005:
            rules.append({
                "rule_id": f"fluid_vfx_flow_{metrics.cycle_id}",
                "rule_text": (
                    "Stable Fluids VFX should inject action velocity into the grid, not only emit particles. "
                    "If flow energy collapses, increase driver velocity scale or reduce viscosity."
                ),
            })
        if metrics.mean_obstacle_leak_ratio > 0.05:
            rules.append({
                "rule_id": f"fluid_vfx_obstacle_{metrics.cycle_id}",
                "rule_text": (
                    "When obstacle leakage rises, treat the character silhouette as an internal boundary and zero density/velocity inside occupied cells."
                ),
            })
        if metrics.fluid_pass:
            rules.append({
                "rule_id": f"fluid_vfx_pass_{metrics.cycle_id}",
                "rule_text": (
                    "Preferred Gap C2 recipe: colocated 2D grid + semi-Lagrangian advection + Gauss-Seidel diffusion/projection + obstacle mask + particle advection overlay."
                ),
            })

        if not rules:
            rules.append({
                "rule_id": f"fluid_vfx_default_{metrics.cycle_id}",
                "rule_text": (
                    "Gap C2 requires both grid density rendering and flow-guided particles; keep both paths active during iteration so stylized readability is preserved."
                ),
            })

        kb_path = self.project_root / "knowledge" / "fluid_vfx_rules.md"
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        if kb_path.exists():
            existing = kb_path.read_text(encoding="utf-8")
        else:
            existing = "# Fluid VFX Knowledge Base\n\n> Auto-generated by Gap C2 evolution bridge.\n\n"
        new_lines = [existing.rstrip(), "", f"## Cycle {metrics.cycle_id}", ""]
        for rule in rules:
            new_lines.append(f"- **{rule['rule_id']}**: {rule['rule_text']}")
        kb_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        self.state.knowledge_rules_total += len(rules)
        self._save_state()
        return rules

    def compute_fluid_fitness_bonus(self, metrics: FluidVFXMetrics) -> float:
        """Layer 3: convert fluid quality into a bounded bonus / penalty."""
        bonus = 0.0
        bonus += min(metrics.mean_flow_energy * 30.0, 0.12)
        bonus += min(metrics.visual_alpha_coverage * 0.5, 0.08)
        bonus -= min(metrics.mean_obstacle_leak_ratio * 1.5, 0.18)
        if metrics.fluid_pass:
            bonus += 0.05
        else:
            bonus -= 0.05
        return float(max(-0.20, min(0.20, bonus)))

    def status_report(self) -> str:
        s = self.state
        return "\n".join([
            "--- Fluid VFX Evolution Bridge (SESSION-046 / Gap C2) ---",
            f"   Total cycles: {s.total_cycles}",
            f"   Pass rate: {s.total_passes}/{s.total_cycles}" if s.total_cycles > 0 else "   Pass rate: N/A",
            f"   Consecutive passes: {s.consecutive_passes}",
            f"   Best flow energy: {s.best_flow_energy:.6f}",
            f"   Lowest obstacle leak ratio: {s.lowest_obstacle_leak_ratio:.6f}",
            f"   Knowledge rules distilled: {s.knowledge_rules_total}",
        ])

    def _update_state(self, metrics: FluidVFXMetrics) -> None:
        self.state.total_cycles += 1
        if metrics.fluid_pass:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
        self.state.best_flow_energy = max(self.state.best_flow_energy, metrics.mean_flow_energy)
        self.state.lowest_obstacle_leak_ratio = min(
            self.state.lowest_obstacle_leak_ratio,
            metrics.mean_obstacle_leak_ratio,
        )
        self.state.best_visual_alpha_coverage = max(
            self.state.best_visual_alpha_coverage,
            metrics.visual_alpha_coverage,
        )
        self.state.flow_energy_trend.append(metrics.mean_flow_energy)
        self.state.obstacle_leak_trend.append(metrics.mean_obstacle_leak_ratio)
        self.state.particle_trend.append(metrics.mean_active_particles)
        self.state.history.append(metrics.to_dict())

    def _load_state(self) -> FluidVFXState:
        state_path = self.project_root / ".fluid_vfx_state.json"
        if state_path.exists():
            try:
                return FluidVFXState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as exc:
                if self.verbose:
                    logger.warning("Failed to load fluid VFX state: %s", exc)
        return FluidVFXState()

    def _save_state(self) -> None:
        state_path = self.project_root / ".fluid_vfx_state.json"
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


__all__ = [
    "FluidVFXMetrics",
    "FluidVFXState",
    "FluidVFXStatus",
    "collect_fluid_vfx_status",
    "FluidVFXEvolutionBridge",
]
