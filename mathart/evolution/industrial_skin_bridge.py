"""SESSION-054 — Three-layer evolution bridge for industrial skin delivery.

Layer 1 evaluates a standard industrial-material benchmark by rendering a small
set of representative poses and verifying that the repository can emit a stable
commercial sprite bundle: albedo, normal, depth, thickness, roughness, and mask.
Layer 2 distills durable operational rules from those measurements. Layer 3
persists trend data so future sessions can continue optimizing analytic gradient
coverage, material contrast, and engine-ready export quality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Optional

import numpy as np

from mathart.animation import Skeleton, render_character_maps_industrial
from mathart.animation.character_presets import get_preset
from mathart.animation.presets import hit_animation, idle_animation, run_animation, walk_animation
from mathart.export.bridge import AssetExporter, ExportConfig


@dataclass
class IndustrialSkinMetrics:
    cycle_id: int
    case_count: int = 0
    mean_inside_analytic_coverage: float = 0.0
    mean_depth_range: float = 0.0
    mean_thickness_range: float = 0.0
    mean_roughness_range: float = 0.0
    export_success_ratio: float = 0.0
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "case_count": self.case_count,
            "mean_inside_analytic_coverage": self.mean_inside_analytic_coverage,
            "mean_depth_range": self.mean_depth_range,
            "mean_thickness_range": self.mean_thickness_range,
            "mean_roughness_range": self.mean_roughness_range,
            "export_success_ratio": self.export_success_ratio,
            "accepted": self.accepted,
        }


@dataclass
class IndustrialSkinState:
    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_inside_analytic_coverage: float = 0.0
    best_export_success_ratio: float = 0.0
    best_depth_range: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_inside_analytic_coverage": self.best_inside_analytic_coverage,
            "best_export_success_ratio": self.best_export_success_ratio,
            "best_depth_range": self.best_depth_range,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndustrialSkinState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            best_inside_analytic_coverage=float(data.get("best_inside_analytic_coverage", 0.0)),
            best_export_success_ratio=float(data.get("best_export_success_ratio", 0.0)),
            best_depth_range=float(data.get("best_depth_range", 0.0)),
            history=list(data.get("history", [])),
        )


class IndustrialSkinBridge:
    STATE_FILE = ".industrial_skin_state.json"
    KNOWLEDGE_FILE = "industrial_skin.md"

    def __init__(self, project_root: Optional[str | Path] = None, *, verbose: bool = False) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.verbose = verbose
        self.state_path = self.root / self.STATE_FILE
        self.knowledge_path = self.root / "knowledge" / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _load_state(self) -> IndustrialSkinState:
        if not self.state_path.exists():
            return IndustrialSkinState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return IndustrialSkinState()
        return IndustrialSkinState.from_dict(data)

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _benchmark_cases(self) -> list[tuple[str, dict[str, float]]]:
        return [
            ("idle_00", idle_animation(0.0)),
            ("walk_00", walk_animation(0.0)),
            ("walk_05", walk_animation(0.5)),
            ("run_00", run_animation(0.0)),
            ("hit_00", hit_animation(0.0)),
        ]

    def evaluate(self) -> IndustrialSkinMetrics:
        skeleton = Skeleton.create_humanoid()
        style, palette = get_preset("mario")
        cases = self._benchmark_cases()
        analytic_coverages: list[float] = []
        depth_ranges: list[float] = []
        thickness_ranges: list[float] = []
        roughness_ranges: list[float] = []
        export_successes = 0

        with tempfile.TemporaryDirectory(prefix="industrial_skin_eval_") as tmp_dir:
            exporter = AssetExporter(ExportConfig(output_dir=tmp_dir, version=max(1, self.state.total_cycles + 1)))
            for case_name, pose in cases:
                bundle = render_character_maps_industrial(
                    skeleton,
                    pose,
                    style,
                    width=32,
                    height=32,
                    palette=palette,
                )
                inside_pixels = max(1, int(bundle.metadata.get("inside_pixel_count", 0)))
                analytic_pixels = int(bundle.metadata.get("analytic_inside_coverage_pixels", bundle.metadata.get("analytic_coverage_pixels", 0)))
                analytic_coverages.append(min(1.0, float(analytic_pixels) / float(inside_pixels)))

                depth_rgba = np.asarray(bundle.depth_map_image, dtype=np.uint8)[..., 0]
                thickness_rgba = np.asarray(bundle.thickness_map_image, dtype=np.uint8)[..., 0]
                roughness_rgba = np.asarray(bundle.roughness_map_image, dtype=np.uint8)[..., 0]
                depth_ranges.append(float(depth_rgba.max() - depth_rgba.min()) / 255.0)
                thickness_ranges.append(float(thickness_rgba.max() - thickness_rgba.min()) / 255.0)
                roughness_ranges.append(float(roughness_rgba.max() - roughness_rgba.min()) / 255.0)

                try:
                    out_path = exporter.export_industrial_bundle(bundle, f"mario_{case_name}", "Characters")
                    export_successes += int(out_path.exists())
                except Exception:
                    if self.verbose:
                        print(f"[industrial-skin] export failed for {case_name}")

        metrics = IndustrialSkinMetrics(
            cycle_id=self.state.total_cycles + 1,
            case_count=len(cases),
            mean_inside_analytic_coverage=float(np.mean(analytic_coverages)) if analytic_coverages else 0.0,
            mean_depth_range=float(np.mean(depth_ranges)) if depth_ranges else 0.0,
            mean_thickness_range=float(np.mean(thickness_ranges)) if thickness_ranges else 0.0,
            mean_roughness_range=float(np.mean(roughness_ranges)) if roughness_ranges else 0.0,
            export_success_ratio=float(export_successes) / float(max(1, len(cases))),
        )
        metrics.accepted = bool(
            metrics.case_count > 0
            and metrics.mean_inside_analytic_coverage >= 0.85
            and metrics.mean_depth_range >= 0.10
            and metrics.mean_thickness_range >= 0.10
            and metrics.mean_roughness_range >= 0.05
            and metrics.export_success_ratio >= 1.0
        )
        return metrics

    def distill_rules(self, metrics: IndustrialSkinMetrics) -> list[dict[str, str]]:
        rules = [
            {
                "id": f"SKIN-{metrics.cycle_id:03d}-A",
                "rule": "For canonical 2D body primitives, gradients must come from analytic distance-plus-gradient contracts; sampled differences are fallback only for unsupported composites.",
                "parameter": "industrial.gradient_policy",
                "constraint": "gradient_source = analytic || hybrid_fallback",
            },
            {
                "id": f"SKIN-{metrics.cycle_id:03d}-B",
                "rule": "Every industrial sprite frame must ship as a material bundle containing albedo, normal, depth, thickness, roughness, and mask so downstream 2D engines can light it immediately.",
                "parameter": "industrial.bundle_channels",
                "constraint": "required = [albedo, normal, depth, thickness, roughness, mask]",
            },
            {
                "id": f"SKIN-{metrics.cycle_id:03d}-C",
                "rule": "Thickness is derived from interior negative distance, while roughness is derived from inverse curvature magnitude, and both must remain non-flat across accepted benchmark cases.",
                "parameter": "industrial.material_proxy",
                "constraint": "depth_range>0 && thickness_range>0 && roughness_range>0",
            },
        ]
        outcome = "pass" if metrics.accepted else "warn"
        rules.append(
            {
                "id": f"SKIN-{metrics.cycle_id:03d}-{outcome.upper()}",
                "rule": f"Cycle {metrics.cycle_id} produced coverage={metrics.mean_inside_analytic_coverage:.2f}, export_success_ratio={metrics.export_success_ratio:.2f}, depth_range={metrics.mean_depth_range:.2f}, thickness_range={metrics.mean_thickness_range:.2f}, roughness_range={metrics.mean_roughness_range:.2f}.",
                "parameter": "industrial.acceptance",
                "constraint": f"state = {outcome}",
            }
        )
        return rules

    def write_knowledge_file(self, metrics: IndustrialSkinMetrics, rules: list[dict[str, str]]) -> Path:
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Industrial Skin Rules",
            "",
            "Durable rules for the repository's industrial 2.5D material-delivery stack.",
            "",
            f"## Cycle {metrics.cycle_id}",
            "",
            f"- Case count: `{metrics.case_count}`",
            f"- Mean inside analytic coverage: `{metrics.mean_inside_analytic_coverage:.2f}`",
            f"- Mean depth range: `{metrics.mean_depth_range:.2f}`",
            f"- Mean thickness range: `{metrics.mean_thickness_range:.2f}`",
            f"- Mean roughness range: `{metrics.mean_roughness_range:.2f}`",
            f"- Export success ratio: `{metrics.export_success_ratio:.2f}`",
            f"- Acceptance: `{metrics.accepted}`",
            "",
            "## Distilled Rules",
            "",
        ]
        for rule in rules:
            lines.extend([
                f"### {rule['id']}",
                "",
                f"- Rule: {rule['rule']}",
                f"- Parameter: `{rule['parameter']}`",
                f"- Constraint: `{rule['constraint']}`",
                "",
            ])
        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")
        return self.knowledge_path

    def apply_layer3(self, metrics: IndustrialSkinMetrics) -> float:
        self.state.total_cycles += 1
        if metrics.accepted:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1
        self.state.best_inside_analytic_coverage = max(
            self.state.best_inside_analytic_coverage,
            metrics.mean_inside_analytic_coverage,
        )
        self.state.best_export_success_ratio = max(
            self.state.best_export_success_ratio,
            metrics.export_success_ratio,
        )
        self.state.best_depth_range = max(self.state.best_depth_range, metrics.mean_depth_range)
        self.state.history.append(metrics.to_dict())
        self._save_state()
        coverage_bonus = min(0.10, metrics.mean_inside_analytic_coverage * 0.10)
        export_bonus = min(0.10, metrics.export_success_ratio * 0.10)
        return coverage_bonus + export_bonus

    def run_cycle(self) -> dict[str, Any]:
        metrics = self.evaluate()
        rules = self.distill_rules(metrics)
        knowledge_path = self.write_knowledge_file(metrics, rules)
        fitness_bonus = self.apply_layer3(metrics)
        return {
            "metrics": metrics.to_dict(),
            "knowledge_path": str(knowledge_path),
            "fitness_bonus": float(fitness_bonus),
            "accepted": bool(metrics.accepted),
        }


__all__ = [
    "IndustrialSkinMetrics",
    "IndustrialSkinState",
    "IndustrialSkinBridge",
]
