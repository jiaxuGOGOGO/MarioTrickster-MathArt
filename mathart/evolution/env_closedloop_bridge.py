"""
Phase 4 Environment Closed-Loop Three-Layer Evolution Bridge.

SESSION-062: Integrates WFC Tilemap Export (Dual Grid) and Fluid Sequence
Export (VFX Graph Velocity Inheritance) into the project's three-layer
evolution architecture.

Three-Layer Architecture:
  1. **Internal Evolution Gate** — Generate assets, evaluate against quality
     thresholds, reject failures.
  2. **External Knowledge Distillation** — Persist reusable rules from
     successful/failed runs into Markdown knowledge files.
  3. **Self-Iterative Testing** — Track trends over time, compute fitness
     bonus/penalty, enable cross-session improvement.

Research provenance:
  - Maxim Gumin: WFC constraint solver (2016)
  - Oskar Stålberg: Dual Grid WFC / Townscaper (2017-2021)
  - Jos Stam: Stable Fluids (SIGGRAPH 1999)
  - Unity VFX Graph: Flipbook Player + Velocity Inheritance
"""
from __future__ import annotations
from .state_vault import resolve_state_path

import json
import logging
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ── WFC Tilemap Evolution Metrics ────────────────────────────────────────────


@dataclass
class WFCTilemapMetrics:
    """Metrics captured from one WFC Tilemap evaluation cycle."""

    cycle_id: int = 0
    width: int = 0
    height: int = 0
    is_playable: bool = False
    path_length: int = 0
    tile_diversity: float = 0.0
    platform_count: int = 0
    gap_count: int = 0
    dual_grid_coverage: float = 0.0
    marching_index_diversity: float = 0.0
    generation_attempts: int = 0
    veto_count: int = 0
    tilemap_pass: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WFCTilemapState:
    """Persistent state for WFC Tilemap evolution tracking."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_tile_diversity: float = 0.0
    best_path_length: int = 0
    best_marching_diversity: float = 0.0
    consecutive_passes: int = 0
    knowledge_rules_total: int = 0
    diversity_trend: list[float] = field(default_factory=list)
    playability_trend: list[bool] = field(default_factory=list)
    marching_diversity_trend: list[float] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_tile_diversity": self.best_tile_diversity,
            "best_path_length": self.best_path_length,
            "best_marching_diversity": self.best_marching_diversity,
            "consecutive_passes": self.consecutive_passes,
            "knowledge_rules_total": self.knowledge_rules_total,
            "diversity_trend": self.diversity_trend[-50:],
            "playability_trend": [int(b) for b in self.playability_trend[-50:]],
            "marching_diversity_trend": self.marching_diversity_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WFCTilemapState":
        return cls(
            total_cycles=d.get("total_cycles", 0),
            total_passes=d.get("total_passes", 0),
            total_failures=d.get("total_failures", 0),
            best_tile_diversity=d.get("best_tile_diversity", 0.0),
            best_path_length=d.get("best_path_length", 0),
            best_marching_diversity=d.get("best_marching_diversity", 0.0),
            consecutive_passes=d.get("consecutive_passes", 0),
            knowledge_rules_total=d.get("knowledge_rules_total", 0),
            diversity_trend=d.get("diversity_trend", []),
            playability_trend=[bool(b) for b in d.get("playability_trend", [])],
            marching_diversity_trend=d.get("marching_diversity_trend", []),
            history=d.get("history", []),
        )


# ── Fluid Sequence Evolution Metrics ────────────────────────────────────────


@dataclass
class FluidSequenceMetrics:
    """Metrics captured from one Fluid Sequence export evaluation cycle."""

    cycle_id: int = 0
    driver_mode: str = "smoke"
    frame_count: int = 0
    atlas_width: int = 0
    atlas_height: int = 0
    mean_flow_energy: float = 0.0
    max_flow_speed: float = 0.0
    total_density_mass: float = 0.0
    velocity_field_coverage: float = 0.0
    density_atlas_exists: bool = False
    velocity_atlas_exists: bool = False
    manifest_valid: bool = False
    unity_controller_exists: bool = False
    sequence_pass: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FluidSequenceState:
    """Persistent state for Fluid Sequence evolution tracking."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_flow_energy: float = 0.0
    best_velocity_coverage: float = 0.0
    consecutive_passes: int = 0
    knowledge_rules_total: int = 0
    flow_energy_trend: list[float] = field(default_factory=list)
    velocity_coverage_trend: list[float] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_flow_energy": self.best_flow_energy,
            "best_velocity_coverage": self.best_velocity_coverage,
            "consecutive_passes": self.consecutive_passes,
            "knowledge_rules_total": self.knowledge_rules_total,
            "flow_energy_trend": self.flow_energy_trend[-50:],
            "velocity_coverage_trend": self.velocity_coverage_trend[-50:],
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FluidSequenceState":
        return cls(
            total_cycles=d.get("total_cycles", 0),
            total_passes=d.get("total_passes", 0),
            total_failures=d.get("total_failures", 0),
            best_flow_energy=d.get("best_flow_energy", 0.0),
            best_velocity_coverage=d.get("best_velocity_coverage", 0.0),
            consecutive_passes=d.get("consecutive_passes", 0),
            knowledge_rules_total=d.get("knowledge_rules_total", 0),
            flow_energy_trend=d.get("flow_energy_trend", []),
            velocity_coverage_trend=d.get("velocity_coverage_trend", []),
            history=d.get("history", []),
        )


# ── Combined Status ──────────────────────────────────────────────────────────


@dataclass
class EnvClosedLoopStatus:
    """Snapshot of Phase 4 integration coverage for report generation."""

    # WFC Tilemap
    wfc_tilemap_exporter_exists: bool = False
    dual_grid_mapper_exists: bool = False
    unity_tilemap_loader_exists: bool = False
    wfc_public_api_exports: bool = False
    wfc_total_cycles: int = 0
    wfc_consecutive_passes: int = 0

    # Fluid Sequence
    fluid_sequence_exporter_exists: bool = False
    velocity_field_renderer_exists: bool = False
    unity_vfx_controller_exists: bool = False
    fluid_seq_public_api_exports: bool = False
    fluid_seq_total_cycles: int = 0
    fluid_seq_consecutive_passes: int = 0

    # Tests
    test_wfc_tilemap_exists: bool = False
    test_fluid_sequence_exists: bool = False

    # Knowledge
    wfc_knowledge_path: str = ""
    fluid_seq_knowledge_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_env_closedloop_status(project_root: str | Path) -> EnvClosedLoopStatus:
    """Collect repository integration status for Phase 4 subsystems."""
    root = Path(project_root)
    status = EnvClosedLoopStatus()

    # WFC Tilemap
    wfc_exporter = root / "mathart/level/wfc_tilemap_exporter.py"
    status.wfc_tilemap_exporter_exists = wfc_exporter.exists()
    if wfc_exporter.exists():
        text = wfc_exporter.read_text(encoding="utf-8", errors="replace")
        status.dual_grid_mapper_exists = "DualGridMapper" in text
        status.unity_tilemap_loader_exists = "WFCTilemapLoader" in text

    level_init = root / "mathart/level/__init__.py"
    if level_init.exists():
        text = level_init.read_text(encoding="utf-8", errors="replace")
        status.wfc_public_api_exports = "WFCTilemapExporter" in text

    wfc_state_path = resolve_state_path(root, ".wfc_tilemap_state.json")
    if wfc_state_path.exists():
        try:
            d = json.loads(wfc_state_path.read_text(encoding="utf-8"))
            status.wfc_total_cycles = d.get("total_cycles", 0)
            status.wfc_consecutive_passes = d.get("consecutive_passes", 0)
        except (json.JSONDecodeError, OSError):
            pass

    # Fluid Sequence
    fluid_exporter = root / "mathart/animation/fluid_sequence_exporter.py"
    status.fluid_sequence_exporter_exists = fluid_exporter.exists()
    if fluid_exporter.exists():
        text = fluid_exporter.read_text(encoding="utf-8", errors="replace")
        status.velocity_field_renderer_exists = "VelocityFieldRenderer" in text
        status.unity_vfx_controller_exists = "FluidVFXController" in text

    anim_init = root / "mathart/animation/__init__.py"
    if anim_init.exists():
        text = anim_init.read_text(encoding="utf-8", errors="replace")
        status.fluid_seq_public_api_exports = "FluidSequenceExporter" in text

    fluid_state_path = resolve_state_path(root, ".fluid_sequence_state.json")
    if fluid_state_path.exists():
        try:
            d = json.loads(fluid_state_path.read_text(encoding="utf-8"))
            status.fluid_seq_total_cycles = d.get("total_cycles", 0)
            status.fluid_seq_consecutive_passes = d.get("consecutive_passes", 0)
        except (json.JSONDecodeError, OSError):
            pass

    # Tests
    status.test_wfc_tilemap_exists = (root / "tests/test_wfc_tilemap_exporter.py").exists()
    status.test_fluid_sequence_exists = (root / "tests/test_fluid_sequence_exporter.py").exists()

    # Knowledge
    wfc_kb = root / "knowledge/wfc_tilemap_rules.md"
    fluid_kb = root / "knowledge/fluid_sequence_rules.md"
    if wfc_kb.exists():
        status.wfc_knowledge_path = str(wfc_kb.relative_to(root))
    if fluid_kb.exists():
        status.fluid_seq_knowledge_path = str(fluid_kb.relative_to(root))

    return status


# ── WFC Tilemap Evolution Bridge ─────────────────────────────────────────────


class WFCTilemapEvolutionBridge:
    """Three-layer bridge for WFC Tilemap + Dual Grid subsystem."""

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    def evaluate_tilemap(
        self,
        export_result: Optional[dict[str, Any]] = None,
        min_diversity: float = 0.15,
        min_path_length: int = 3,
        min_marching_diversity: float = 0.2,
    ) -> WFCTilemapMetrics:
        """Layer 1: evaluate whether the generated tilemap is viable."""
        metrics = WFCTilemapMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if export_result:
            meta = export_result.get("metadata", {})
            metrics.width = export_result.get("logical_width", 0)
            metrics.height = export_result.get("logical_height", 0)
            metrics.is_playable = meta.get("is_playable", False)
            metrics.path_length = meta.get("reachability_path_length", 0)
            metrics.tile_diversity = meta.get("tile_diversity", 0.0)
            metrics.platform_count = meta.get("platform_count", 0)
            metrics.gap_count = meta.get("gap_count", 0)
            metrics.dual_grid_coverage = (
                export_result.get("dual_width", 0) * export_result.get("dual_height", 0)
            ) / max(metrics.width * metrics.height, 1)

            # Compute marching index diversity from dual grid
            dual_w = export_result.get("dual_width", 0)
            dual_h = export_result.get("dual_height", 0)
            if dual_w > 0 and dual_h > 0:
                unique_indices = export_result.get("unique_tiles", 1)
                metrics.marching_index_diversity = unique_indices / 16.0

        metrics.tilemap_pass = (
            metrics.is_playable
            and metrics.tile_diversity >= min_diversity
            and metrics.path_length >= min_path_length
        )

        self._update_state(metrics)
        self._save_state()
        return metrics

    def distill_tilemap_knowledge(self, metrics: WFCTilemapMetrics) -> list[dict[str, str]]:
        """Layer 2: turn evaluation results into durable project rules."""
        rules: list[dict[str, str]] = []

        if not metrics.is_playable:
            rules.append({
                "rule_id": f"wfc_tilemap_playability_{metrics.cycle_id}",
                "rule_text": (
                    "WFC Tilemap generation must ensure reachability from spawn to goal. "
                    "If playability fails, increase max_retries or widen veto_threshold."
                ),
            })
        if metrics.tile_diversity < 0.15:
            rules.append({
                "rule_id": f"wfc_tilemap_diversity_{metrics.cycle_id}",
                "rule_text": (
                    "Low tile diversity indicates overly homogeneous levels. "
                    "Increase hazard/enemy probability or add more tile types to training data."
                ),
            })
        if metrics.dual_grid_coverage < 0.5:
            rules.append({
                "rule_id": f"wfc_tilemap_dualgrid_{metrics.cycle_id}",
                "rule_text": (
                    "Dual grid coverage below 50% suggests the level is too small for effective "
                    "Marching Squares autotiling. Ensure minimum grid size of 4x4."
                ),
            })
        if metrics.tilemap_pass:
            rules.append({
                "rule_id": f"wfc_tilemap_pass_{metrics.cycle_id}",
                "rule_text": (
                    "Preferred Phase 4 WFC recipe: ConstraintAwareWFC with physics veto → "
                    "WFCTilemapExporter with DualGridMapper → JSON → Unity WFCTilemapLoader "
                    "with CompositeCollider2D. Oskar Stålberg dual-grid technique confirmed effective."
                ),
            })

        if not rules:
            rules.append({
                "rule_id": f"wfc_tilemap_default_{metrics.cycle_id}",
                "rule_text": "No specific issues detected. Continue monitoring tilemap quality metrics.",
            })

        kb_path = self.project_root / "knowledge" / "wfc_tilemap_rules.md"
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        if kb_path.exists():
            existing = kb_path.read_text(encoding="utf-8")
        else:
            existing = (
                "# WFC Tilemap Knowledge Base\n\n"
                "> Auto-generated by Phase 4 Environment Closed-Loop evolution bridge.\n"
                "> Research: Maxim Gumin (WFC), Oskar Stålberg (Dual Grid), Boris the Brave (Quarter-Tile).\n\n"
            )
        new_lines = [existing.rstrip(), "", f"## Cycle {metrics.cycle_id}", ""]
        for rule in rules:
            new_lines.append(f"- **{rule['rule_id']}**: {rule['rule_text']}")
        kb_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        self.state.knowledge_rules_total += len(rules)
        self._save_state()
        return rules

    def compute_tilemap_fitness_bonus(self, metrics: WFCTilemapMetrics) -> float:
        """Layer 3: convert tilemap quality into a bounded bonus / penalty."""
        bonus = 0.0
        bonus += min(metrics.tile_diversity * 0.5, 0.10)
        bonus += min(metrics.path_length * 0.01, 0.08)
        bonus += min(metrics.dual_grid_coverage * 0.1, 0.05)
        bonus += min(metrics.marching_index_diversity * 0.15, 0.07)
        if metrics.tilemap_pass:
            bonus += 0.05
        else:
            bonus -= 0.10
        return float(max(-0.20, min(0.20, bonus)))

    def run_full_cycle(self) -> dict[str, Any]:
        """Execute a complete three-layer evolution cycle."""
        from mathart.level.wfc_tilemap_exporter import generate_and_export_tilemap

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_and_export_tilemap(
                width=22, height=7, seed=self.state.total_cycles + 1,
                output_dir=tmpdir, include_unity_loader=True,
            )
            metrics = self.evaluate_tilemap(result.to_dict())
            rules = self.distill_tilemap_knowledge(metrics)
            bonus = self.compute_tilemap_fitness_bonus(metrics)

        return {
            "metrics": metrics.to_dict(),
            "rules": rules,
            "fitness_bonus": bonus,
            "status_report": self.status_report(),
        }

    def status_report(self) -> str:
        s = self.state
        return "\n".join([
            "--- WFC Tilemap Evolution Bridge (SESSION-062 / Phase 4) ---",
            f"   Total cycles: {s.total_cycles}",
            f"   Pass rate: {s.total_passes}/{s.total_cycles}" if s.total_cycles > 0 else "   Pass rate: N/A",
            f"   Consecutive passes: {s.consecutive_passes}",
            f"   Best tile diversity: {s.best_tile_diversity:.4f}",
            f"   Best path length: {s.best_path_length}",
            f"   Knowledge rules distilled: {s.knowledge_rules_total}",
        ])

    def _update_state(self, metrics: WFCTilemapMetrics) -> None:
        self.state.total_cycles += 1
        if metrics.tilemap_pass:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
        self.state.best_tile_diversity = max(self.state.best_tile_diversity, metrics.tile_diversity)
        self.state.best_path_length = max(self.state.best_path_length, metrics.path_length)
        self.state.best_marching_diversity = max(
            self.state.best_marching_diversity, metrics.marching_index_diversity
        )
        self.state.diversity_trend.append(metrics.tile_diversity)
        self.state.playability_trend.append(metrics.is_playable)
        self.state.marching_diversity_trend.append(metrics.marching_index_diversity)
        self.state.history.append(metrics.to_dict())

    def _load_state(self) -> WFCTilemapState:
        state_path = resolve_state_path(self.project_root, ".wfc_tilemap_state.json")
        if state_path.exists():
            try:
                return WFCTilemapState.from_dict(
                    json.loads(state_path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, OSError) as exc:
                if self.verbose:
                    logger.warning("Failed to load WFC tilemap state: %s", exc)
        return WFCTilemapState()

    def _save_state(self) -> None:
        state_path = resolve_state_path(self.project_root, ".wfc_tilemap_state.json")
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# ── Fluid Sequence Evolution Bridge ──────────────────────────────────────────


class FluidSequenceEvolutionBridge:
    """Three-layer bridge for Fluid Sequence Export + VFX Graph subsystem."""

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.verbose = verbose
        self.state = self._load_state()

    def evaluate_sequence(
        self,
        export_result: Optional[dict[str, Any]] = None,
        min_flow_energy: float = 0.0003,
        min_velocity_coverage: float = 0.01,
    ) -> FluidSequenceMetrics:
        """Layer 1: evaluate whether the exported sequence bundle is viable."""
        metrics = FluidSequenceMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if export_result:
            manifest = export_result.get("manifest", {})
            if isinstance(manifest, dict):
                metrics.driver_mode = manifest.get("driver_mode", "smoke")
                metrics.frame_count = manifest.get("frame_count", 0)
                metrics.atlas_width = manifest.get("atlas_width", 0)
                metrics.atlas_height = manifest.get("atlas_height", 0)
                metrics.mean_flow_energy = manifest.get("mean_flow_energy", 0.0)
                metrics.max_flow_speed = manifest.get("max_flow_speed", 0.0)
                metrics.total_density_mass = manifest.get("total_density_mass", 0.0)

            metrics.density_atlas_exists = bool(export_result.get("density_atlas_path"))
            metrics.velocity_atlas_exists = bool(export_result.get("velocity_atlas_path"))
            metrics.manifest_valid = bool(export_result.get("manifest_path"))
            metrics.unity_controller_exists = bool(export_result.get("unity_controller_path"))

            # Estimate velocity field coverage from atlas dimensions
            if metrics.atlas_width > 0 and metrics.atlas_height > 0:
                metrics.velocity_field_coverage = min(1.0, metrics.atlas_width * metrics.atlas_height / 65536)

        metrics.sequence_pass = (
            metrics.frame_count > 0
            and metrics.density_atlas_exists
            and metrics.velocity_atlas_exists
            and metrics.manifest_valid
            and metrics.mean_flow_energy >= min_flow_energy
        )

        self._update_state(metrics)
        self._save_state()
        return metrics

    def distill_sequence_knowledge(self, metrics: FluidSequenceMetrics) -> list[dict[str, str]]:
        """Layer 2: turn evaluation results into durable project rules."""
        rules: list[dict[str, str]] = []

        if metrics.mean_flow_energy < 0.0003:
            rules.append({
                "rule_id": f"fluid_seq_energy_{metrics.cycle_id}",
                "rule_text": (
                    "Fluid sequence export shows near-zero flow energy. "
                    "Increase source_velocity_scale or reduce viscosity for visible flow patterns."
                ),
            })
        if not metrics.velocity_atlas_exists:
            rules.append({
                "rule_id": f"fluid_seq_velocity_{metrics.cycle_id}",
                "rule_text": (
                    "Velocity atlas missing. Ensure export_velocity_atlas=True in FluidSequenceConfig. "
                    "Unity VFX Graph velocity inheritance requires the flow-map atlas."
                ),
            })
        if metrics.sequence_pass:
            rules.append({
                "rule_id": f"fluid_seq_pass_{metrics.cycle_id}",
                "rule_text": (
                    "Preferred Phase 4 fluid sequence recipe: FluidDrivenVFXSystem → "
                    "FluidSequenceExporter with density + velocity atlases → "
                    "Unity FluidVFXController with Rigidbody2D velocity inheritance. "
                    "RG-centered encoding (0.5=zero) confirmed compatible with VFX Graph."
                ),
            })

        if not rules:
            rules.append({
                "rule_id": f"fluid_seq_default_{metrics.cycle_id}",
                "rule_text": "No specific issues detected. Continue monitoring sequence export quality.",
            })

        kb_path = self.project_root / "knowledge" / "fluid_sequence_rules.md"
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        if kb_path.exists():
            existing = kb_path.read_text(encoding="utf-8")
        else:
            existing = (
                "# Fluid Sequence Export Knowledge Base\n\n"
                "> Auto-generated by Phase 4 Environment Closed-Loop evolution bridge.\n"
                "> Research: Jos Stam (Stable Fluids), Unity VFX Graph (Velocity Inheritance).\n\n"
            )
        new_lines = [existing.rstrip(), "", f"## Cycle {metrics.cycle_id}", ""]
        for rule in rules:
            new_lines.append(f"- **{rule['rule_id']}**: {rule['rule_text']}")
        kb_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        self.state.knowledge_rules_total += len(rules)
        self._save_state()
        return rules

    def compute_sequence_fitness_bonus(self, metrics: FluidSequenceMetrics) -> float:
        """Layer 3: convert sequence quality into a bounded bonus / penalty."""
        bonus = 0.0
        bonus += min(metrics.mean_flow_energy * 20.0, 0.10)
        bonus += min(metrics.velocity_field_coverage * 0.3, 0.05)
        if metrics.density_atlas_exists:
            bonus += 0.03
        if metrics.velocity_atlas_exists:
            bonus += 0.03
        if metrics.unity_controller_exists:
            bonus += 0.02
        if metrics.sequence_pass:
            bonus += 0.05
        else:
            bonus -= 0.08
        return float(max(-0.20, min(0.20, bonus)))

    def run_full_cycle(self, driver_mode: str = "smoke") -> dict[str, Any]:
        return {
            "metrics": {
                "archived": True,
                "module": "mathart.animation.fluid_sequence_exporter",
                "archive": "_legacy_archive_v5",
            },
            "rules": [],
            "fitness_bonus": 0.0,
            "status_report": self.status_report(),
        }

    def status_report(self) -> str:
        s = self.state
        return "\n".join([
            "--- Fluid Sequence Evolution Bridge (SESSION-062 / Phase 4) ---",
            f"   Total cycles: {s.total_cycles}",
            f"   Pass rate: {s.total_passes}/{s.total_cycles}" if s.total_cycles > 0 else "   Pass rate: N/A",
            f"   Consecutive passes: {s.consecutive_passes}",
            f"   Best flow energy: {s.best_flow_energy:.6f}",
            f"   Best velocity coverage: {s.best_velocity_coverage:.4f}",
            f"   Knowledge rules distilled: {s.knowledge_rules_total}",
        ])

    def _update_state(self, metrics: FluidSequenceMetrics) -> None:
        self.state.total_cycles += 1
        if metrics.sequence_pass:
            self.state.total_passes += 1
            self.state.consecutive_passes += 1
        else:
            self.state.total_failures += 1
            self.state.consecutive_passes = 0
        self.state.best_flow_energy = max(self.state.best_flow_energy, metrics.mean_flow_energy)
        self.state.best_velocity_coverage = max(
            self.state.best_velocity_coverage, metrics.velocity_field_coverage
        )
        self.state.flow_energy_trend.append(metrics.mean_flow_energy)
        self.state.velocity_coverage_trend.append(metrics.velocity_field_coverage)
        self.state.history.append(metrics.to_dict())

    def _load_state(self) -> FluidSequenceState:
        state_path = resolve_state_path(self.project_root, ".fluid_sequence_state.json")
        if state_path.exists():
            try:
                return FluidSequenceState.from_dict(
                    json.loads(state_path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, OSError) as exc:
                if self.verbose:
                    logger.warning("Failed to load fluid sequence state: %s", exc)
        return FluidSequenceState()

    def _save_state(self) -> None:
        state_path = resolve_state_path(self.project_root, ".fluid_sequence_state.json")
        state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# ── Combined Orchestrator ────────────────────────────────────────────────────


class EnvClosedLoopOrchestrator:
    """Orchestrate both WFC Tilemap and Fluid Sequence evolution cycles.

    This is the top-level entry point for Phase 4 three-layer evolution.
    It runs both subsystem bridges and aggregates results.
    """

    def __init__(self, project_root: Path, verbose: bool = True):
        self.project_root = Path(project_root)
        self.wfc_bridge = WFCTilemapEvolutionBridge(project_root, verbose)
        self.fluid_bridge = FluidSequenceEvolutionBridge(project_root, verbose)

    def run_full_cycle(self) -> dict[str, Any]:
        """Run both subsystem evolution cycles and aggregate results."""
        wfc_result = self.wfc_bridge.run_full_cycle()
        fluid_results = {}
        for mode in ("smoke", "slash", "dash"):
            fluid_results[mode] = self.fluid_bridge.run_full_cycle(driver_mode=mode)

        combined_bonus = wfc_result["fitness_bonus"]
        for mode, result in fluid_results.items():
            combined_bonus += result["fitness_bonus"]
        combined_bonus = max(-0.40, min(0.40, combined_bonus))

        return {
            "wfc_tilemap": wfc_result,
            "fluid_sequence": fluid_results,
            "combined_fitness_bonus": combined_bonus,
            "status": collect_env_closedloop_status(self.project_root).to_dict(),
        }


__all__ = [
    "WFCTilemapMetrics",
    "WFCTilemapState",
    "FluidSequenceMetrics",
    "FluidSequenceState",
    "EnvClosedLoopStatus",
    "collect_env_closedloop_status",
    "WFCTilemapEvolutionBridge",
    "FluidSequenceEvolutionBridge",
    "EnvClosedLoopOrchestrator",
]
