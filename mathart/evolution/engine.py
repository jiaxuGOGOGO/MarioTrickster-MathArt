"""Self-Evolution Engine — top-level orchestrator.

Coordinates the three layers of the self-evolution system:
  1. Inner Loop: quality-driven parameter optimization (with QC)
  2. Outer Loop: external knowledge distillation
  3. Math Registry: model catalog and capability tracking
  4. Quality Controller: art + math knowledge throughout the pipeline
  5. Sprite Library: reference sprite management
  6. Project Brain: cross-session persistent memory

**v0.6 upgrade**: unified ``run`` command that chains all subsystems.

Design philosophy:
  - The engine is stateless between sessions (state lives in files)
  - Every action is logged and reversible (via git)
  - The engine exposes its limitations honestly (capability gaps)
  - Cross-session continuity: new sessions pick up from PROJECT_BRAIN.json
  - **AUTONOMOUS mode**: never blocks on AI, always iterates
  - **ASSISTED mode**: uses AI when available, falls back gracefully
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from ..distill.compiler import ParameterSpace
from .inner_loop import InnerLoopRunner, InnerLoopResult, RunMode
from .math_registry import MathModelRegistry, ModelCapability
from .outer_loop import OuterLoopDistiller


class SelfEvolutionEngine:
    """Top-level coordinator for the self-evolution system.

    Parameters
    ----------
    project_root : str or Path
        Root directory of the MarioTrickster-MathArt project.
    mode : RunMode
        AUTONOMOUS (default) or ASSISTED.
    verbose : bool
        Print progress to stdout.
    """

    def __init__(
        self,
        project_root: str | Path = ".",
        mode: RunMode = RunMode.AUTONOMOUS,
        verbose: bool = True,
    ):
        self.project_root = Path(project_root)
        self.mode = mode
        self.verbose = verbose

        # Initialize subsystems
        self.inner_loop = InnerLoopRunner(
            verbose=verbose,
            mode=mode,
            project_root=self.project_root,
        )
        self.outer_loop = OuterLoopDistiller(
            project_root=project_root,
            verbose=verbose,
        )
        self.math_registry = MathModelRegistry()

    # ── Unified run command ────────────────────────────────────────────────

    def run(
        self,
        generator: Callable[[dict], Image.Image],
        space: ParameterSpace,
        reference: Optional[Image.Image] = None,
        palette: Optional[list] = None,
        max_iterations: int = 50,
        population_size: int = 20,
        seed: Optional[int] = None,
    ) -> InnerLoopResult:
        """Run the full self-evolution pipeline.

        This is the unified entry point that:
        1. Loads knowledge + math + sprite constraints
        2. Runs the inner loop with full quality control
        3. Updates the project brain with results
        4. Returns the optimization result

        Parameters
        ----------
        generator : callable
            Function that takes a parameter dict and returns a PIL.Image.
        space : ParameterSpace
            The parameter space to search.
        reference : PIL.Image, optional
            Style reference image.
        palette : list of (R, G, B), optional
            Target palette.
        max_iterations : int
            Maximum generations.
        population_size : int
            Population size per generation.
        seed : int, optional
            Random seed.

        Returns
        -------
        InnerLoopResult
        """
        # Configure inner loop
        self.inner_loop.max_iterations = max_iterations
        self.inner_loop.population_size = population_size

        if self.verbose:
            mode_str = "AUTONOMOUS" if self.mode == RunMode.AUTONOMOUS else "ASSISTED"
            print(f"\n{'='*60}")
            print(f"MarioTrickster-MathArt — Self-Evolution Run ({mode_str})")
            print(f"{'='*60}")
            print(f"  Max iterations: {max_iterations}")
            print(f"  Population: {population_size}")
            print(f"  Quality threshold: {self.inner_loop.quality_threshold}")
            print()

        # Run inner loop (QC is integrated inside)
        result = self.inner_loop.run(
            generator=generator,
            space=space,
            reference=reference,
            palette=palette,
            seed=seed,
        )

        if self.verbose:
            print()
            print(result.summary())
            print(f"{'='*60}\n")

        # Update project brain if available
        self._update_brain(result)

        return result

    def _update_brain(self, result: InnerLoopResult) -> None:
        """Update PROJECT_BRAIN.json with the latest run results."""
        try:
            from ..brain.memory import ProjectMemory
            mem = ProjectMemory(project_root=self.project_root)
            mem.update_counters(
                total_iterations=result.iterations,
            )
            if result.best_score > 0:
                mem.state.best_quality_score = max(
                    mem.state.best_quality_score,
                    result.best_score,
                )
            mem.generate_handoff()
        except Exception:
            pass  # Brain update is optional

    # ── Status and reporting ───────────────────────────────────────────────

    def status(self) -> str:
        """Return a comprehensive status report of the evolution system."""
        lines = [
            "=" * 60,
            "MarioTrickster-MathArt — Self-Evolution Engine Status",
            f"  Mode: {self.mode.value.upper()}",
            "=" * 60,
            "",
        ]

        # Knowledge base stats
        knowledge_dir = self.project_root / "knowledge"
        if knowledge_dir.exists():
            md_files = list(knowledge_dir.glob("*.md"))
            total_lines = sum(
                len(f.read_text(encoding="utf-8").splitlines()) for f in md_files
            )
            lines.extend([
                f"Knowledge Base: {len(md_files)} files, ~{total_lines} lines",
                f"   Files: {', '.join(f.stem for f in sorted(md_files))}",
                "",
            ])

        # Sprite library stats
        sprite_json = self.project_root / "knowledge" / "sprite_library.json"
        if sprite_json.exists():
            try:
                data = json.loads(sprite_json.read_text())
                lines.append(f"Sprite Library: {len(data)} reference sprites")
                lines.append("")
            except Exception:
                pass

        # Math model registry
        all_models = self.math_registry.list_all()
        stable = [m for m in all_models if m.status == "stable"]
        experimental = [m for m in all_models if m.status == "experimental"]
        lines.extend([
            f"Math Model Registry: {len(all_models)} models",
            f"   Stable: {len(stable)} | Experimental: {len(experimental)}",
            "",
        ])

        # Capability coverage
        all_caps = set(ModelCapability)
        covered_caps = set()
        for model in stable:
            covered_caps.update(model.capabilities)
        missing_caps = all_caps - covered_caps

        lines.append("Covered Capabilities:")
        for cap in sorted(covered_caps, key=lambda c: c.value):
            lines.append(f"   + {cap.value}")

        if missing_caps:
            lines.append("")
            lines.append("Capability Gaps (experimental or missing):")
            for cap in sorted(missing_caps, key=lambda c: c.value):
                exp_models = [m for m in experimental if cap in m.capabilities]
                if exp_models:
                    lines.append(f"   ~ {cap.value} (experimental: {exp_models[0].name})")
                else:
                    lines.append(f"   x {cap.value} (not implemented)")

        # Project brain summary
        brain_path = self.project_root / "PROJECT_BRAIN.json"
        if brain_path.exists():
            try:
                brain = json.loads(brain_path.read_text())
                lines.extend([
                    "",
                    f"Project Brain: v{brain.get('version', '?')}",
                    f"   Best score: {brain.get('best_quality_score', 0):.3f}",
                    f"   Total iterations: {brain.get('total_iterations', 0)}",
                    f"   Pending tasks: {len(brain.get('pending_tasks', []))}",
                    f"   Capability gaps: {len(brain.get('capability_gaps', []))}",
                ])
            except Exception:
                pass

        # Distill log summary
        log_path = self.project_root / "DISTILL_LOG.md"
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
            sessions = re.findall(r'DISTILL-(\d+)', content)
            if sessions:
                lines.extend([
                    "",
                    f"Distillation Sessions: {len(set(sessions))} completed",
                    f"   Latest: DISTILL-{max(sessions)}",
                ])

        # Mode-specific guidance
        lines.extend([
            "",
            "Run Mode Guidance:",
        ])
        if self.mode == RunMode.AUTONOMOUS:
            lines.extend([
                "   Currently in AUTONOMOUS mode:",
                "   - All iterations run locally without AI dependency",
                "   - Quality control active but AI arbitration skipped",
                "   - Stagnation triggers auto-recovery (space widening)",
                "   - To enable AI: engine = SelfEvolutionEngine(mode=RunMode.ASSISTED)",
            ])
        else:
            lines.extend([
                "   Currently in ASSISTED mode:",
                "   - AI arbitration enabled for stagnation resolution",
                "   - Falls back to autonomous if AI unavailable",
                "   - LLM API required: set OPENAI_API_KEY",
            ])

        lines.extend(["", "=" * 60])

        report = "\n".join(lines)
        if self.verbose:
            print(report)
        return report

    def capability_gap_report(self) -> dict:
        """Return a structured report of capability gaps."""
        all_caps = set(ModelCapability)
        all_models = self.math_registry.list_all()

        covered = set()
        experimental_caps = set()
        for model in all_models:
            if model.status == "stable":
                covered.update(model.capabilities)
            elif model.status == "experimental":
                experimental_caps.update(model.capabilities)

        missing = all_caps - covered - experimental_caps

        recommendations = []
        if ModelCapability.SHADER_PARAMS in missing or ModelCapability.SHADER_PARAMS in experimental_caps:
            recommendations.append(
                "SHADER_PARAMS: Consider integrating Godot/Unity shader compiler "
                "for real-time PBR shader parameter optimization."
            )
        if ModelCapability.TEXTURE in missing:
            recommendations.append(
                "TEXTURE: Perlin/Simplex noise texture generator not yet implemented. "
                "Add mathart/sdf/noise.py with octave noise functions."
            )

        return {
            "covered": [c.value for c in sorted(covered, key=lambda x: x.value)],
            "experimental": [c.value for c in sorted(experimental_caps, key=lambda x: x.value)],
            "missing": [c.value for c in sorted(missing, key=lambda x: x.value)],
            "recommendations": recommendations,
        }

    def save_registry(self, filepath: Optional[str] = None) -> Path:
        """Save the math model registry to a JSON file."""
        if filepath is None:
            filepath = self.project_root / "math_models.json"
        else:
            filepath = Path(filepath)

        self.math_registry.save(filepath)
        if self.verbose:
            print(f"[Engine] Math registry saved to {filepath}")
        return filepath
