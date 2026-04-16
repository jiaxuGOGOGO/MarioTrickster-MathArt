"""Self-Evolution Engine — top-level orchestrator.

Coordinates the **three layers** of the self-evolution system:

  **Layer 1 — Inner Loop** (InnerLoopRunner):
    Generate → Evaluate → Optimize → Repeat
    Quality-driven parameter optimization with QC, stagnation detection,
    and now physics fitness scoring.

  **Layer 2 — Outer Loop** (OuterLoopDistiller):
    Parse → Distill → Validate → Integrate
    External knowledge ingestion from PDFs, papers, and research.
    Now includes physics/locomotion knowledge domains.

  **Layer 3 — Physics Evolution** (PhysicsEvolutionLayer):  [SESSION-030 NEW]
    Train → Test → Diagnose → Evolve → Distill
    Physics-aware self-iteration: PD controller optimization,
    RL locomotion training, DeepMimic reward evaluation,
    and auto-distillation of successful strategies.

  Supporting systems:
    - Math Registry: model catalog and capability tracking
    - Quality Controller: art + math knowledge throughout the pipeline
    - Sprite Library: reference sprite management
    - Project Brain: cross-session persistent memory

**v0.6 upgrade**: unified ``run`` command that chains all subsystems.
**v0.7 upgrade (SESSION-030)**: Layer 3 physics evolution integration.

Design philosophy:
  - The engine is stateless between sessions (state lives in files)
  - Every action is logged and reversible (via git)
  - The engine exposes its limitations honestly (capability gaps)
  - Cross-session continuity: new sessions pick up from PROJECT_BRAIN.json
  - **AUTONOMOUS mode**: never blocks on AI, always iterates
  - **ASSISTED mode**: uses AI when available, falls back gracefully
  - **Three-layer synergy**: visual quality (L1) + knowledge (L2) + physics (L3)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image

from ..distill.compiler import ParameterSpace
from .inner_loop import InnerLoopRunner, InnerLoopResult, RunMode
from .math_registry import MathModelRegistry, ModelCapability
from .outer_loop import OuterLoopDistiller
from .evolution_layer3 import PhysicsEvolutionLayer, PhysicsEvolutionRecord


class SelfEvolutionEngine:
    """Top-level coordinator for the three-layer self-evolution system.

    Parameters
    ----------
    project_root : str or Path
        Root directory of the MarioTrickster-MathArt project.
    mode : RunMode
        AUTONOMOUS (default) or ASSISTED.
    verbose : bool
        Print progress to stdout.
    enable_physics : bool
        Enable Layer 3 physics evolution (default True).
    """

    def __init__(
        self,
        project_root: str | Path = ".",
        mode: RunMode = RunMode.AUTONOMOUS,
        verbose: bool = True,
        enable_physics: bool = True,
    ):
        self.project_root = Path(project_root)
        self.mode = mode
        self.verbose = verbose
        self.enable_physics = enable_physics

        # Layer 1: Inner Loop — visual quality optimization
        self.inner_loop = InnerLoopRunner(
            verbose=verbose,
            mode=mode,
            project_root=self.project_root,
        )

        # Layer 2: Outer Loop — external knowledge distillation
        self.outer_loop = OuterLoopDistiller(
            project_root=project_root,
            verbose=verbose,
        )

        # Layer 3: Physics Evolution — physics-aware self-iteration
        self.physics_layer = PhysicsEvolutionLayer(
            project_root=self.project_root,
            verbose=verbose,
        ) if enable_physics else None

        # Supporting: Math model registry
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
        """Run the full self-evolution pipeline (Layer 1 + Layer 3).

        This is the unified entry point that:
        1. Loads knowledge + math + sprite constraints
        2. Runs the inner loop with full quality control (Layer 1)
        3. Optionally runs physics evolution (Layer 3)
        4. Updates the project brain with results
        5. Returns the optimization result

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
            physics_str = "ENABLED" if self.enable_physics else "DISABLED"
            print(f"\n{'='*60}")
            print(f"MarioTrickster-MathArt — Self-Evolution Run ({mode_str})")
            print(f"{'='*60}")
            print(f"  Max iterations: {max_iterations}")
            print(f"  Population: {population_size}")
            print(f"  Quality threshold: {self.inner_loop.quality_threshold}")
            print(f"  Physics Layer 3: {physics_str}")
            print()

        # ── Layer 1: Inner Loop — visual quality optimization ──
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

        # ── Layer 3: Physics Evolution (if enabled) ──
        physics_record = None
        if self.enable_physics and self.physics_layer is not None:
            physics_record = self._run_physics_evolution(result)

        if self.verbose:
            print(f"{'='*60}\n")

        # Update project brain if available
        self._update_brain(result, physics_record)

        return result

    def run_physics_only(
        self,
        physics_geno: Any = None,
        loco_geno: Any = None,
        archetype: str = "hero",
        n_cycles: int = 5,
        seed: Optional[int] = None,
    ) -> list[PhysicsEvolutionRecord]:
        """Run Layer 3 physics evolution independently.

        Useful for optimizing physics parameters without visual generation.

        Parameters
        ----------
        physics_geno : PhysicsGenotype, optional
            Starting physics genes. Uses archetype defaults if None.
        loco_geno : LocomotionGenotype, optional
            Starting locomotion genes. Uses archetype defaults if None.
        archetype : str
            Character archetype.
        n_cycles : int
            Number of evolution cycles.
        seed : int, optional
            Random seed.

        Returns
        -------
        list[PhysicsEvolutionRecord]
        """
        if self.physics_layer is None:
            raise RuntimeError("Physics layer not enabled. Set enable_physics=True.")

        import numpy as np
        from ..animation.physics_genotype import (
            create_physics_genotype, create_locomotion_genotype,
        )

        rng = np.random.default_rng(seed)

        if physics_geno is None:
            physics_geno = create_physics_genotype(archetype)
        if loco_geno is None:
            loco_geno = create_locomotion_genotype(archetype)

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"MarioTrickster-MathArt — Physics Evolution (Layer 3 Only)")
            print(f"{'='*60}")
            print(f"  Archetype: {archetype}")
            print(f"  Cycles: {n_cycles}")
            print()

        records = self.physics_layer.run_multi_cycle(
            physics_geno, loco_geno, archetype, n_cycles, rng
        )

        if self.verbose:
            print(f"\n{'='*60}")
            print(self.physics_layer.status_report())
            print(f"{'='*60}\n")

        return records

    def _run_physics_evolution(
        self,
        inner_result: InnerLoopResult,
    ) -> Optional[PhysicsEvolutionRecord]:
        """Run Layer 3 physics evolution based on inner loop results.

        Automatically creates physics genotypes from the best parameters
        found by the inner loop, then runs one physics evolution cycle.
        """
        try:
            import numpy as np
            from ..animation.physics_genotype import (
                create_physics_genotype, create_locomotion_genotype,
            )

            # Determine archetype from best params
            archetype = "hero"
            if hasattr(inner_result, 'best_params') and inner_result.best_params:
                archetype = inner_result.best_params.get("archetype", "hero")

            physics_geno = create_physics_genotype(archetype)
            loco_geno = create_locomotion_genotype(archetype)

            rng = np.random.default_rng()

            if self.verbose:
                print(f"\n[Engine] Running Layer 3 physics evolution for '{archetype}'...")

            record = self.physics_layer.run(
                physics_geno, loco_geno, archetype, rng=rng
            )

            return record

        except Exception as e:
            if self.verbose:
                print(f"[Engine] Layer 3 physics evolution skipped: {e}")
            return None

    def _update_brain(
        self,
        result: InnerLoopResult,
        physics_record: Optional[PhysicsEvolutionRecord] = None,
    ) -> None:
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

            # Store Layer 3 results in custom notes
            if physics_record is not None:
                mem.set_note(
                    "layer3_last_cycle",
                    f"Cycle {physics_record.cycle_id}: "
                    f"combined={physics_record.combined_fitness:.3f}"
                )
                mem.set_note(
                    "layer3_best_combined",
                    f"{self.physics_layer.state.best_combined_fitness:.3f}"
                    if self.physics_layer else "N/A"
                )

            mem.generate_handoff()
        except Exception:
            pass  # Brain update is optional

    # ── Three-Layer Status Report ─────────────────────────────────────────

    def status(self) -> str:
        """Return a comprehensive status report of the three-layer evolution system."""
        lines = [
            "=" * 60,
            "MarioTrickster-MathArt — Three-Layer Self-Evolution Engine",
            f"  Mode: {self.mode.value.upper()}",
            f"  Physics Layer: {'ENABLED' if self.enable_physics else 'DISABLED'}",
            "=" * 60,
            "",
        ]

        # ── Layer 1: Inner Loop Status ──
        lines.extend([
            "--- Layer 1: Inner Loop (Visual Quality) ---",
            f"   Quality threshold: {self.inner_loop.quality_threshold}",
            f"   Max iterations: {self.inner_loop.max_iterations}",
            f"   Population: {self.inner_loop.population_size}",
            "",
        ])

        # ── Layer 2: Knowledge Base Status ──
        lines.append("--- Layer 2: Outer Loop (Knowledge Distillation) ---")
        knowledge_dir = self.project_root / "knowledge"
        if knowledge_dir.exists():
            md_files = list(knowledge_dir.glob("*.md"))
            total_lines = sum(
                len(f.read_text(encoding="utf-8").splitlines()) for f in md_files
            )
            lines.extend([
                f"   Knowledge Base: {len(md_files)} files, ~{total_lines} lines",
                f"   Files: {', '.join(f.stem for f in sorted(md_files))}",
            ])
            # Check for physics knowledge
            physics_kb = knowledge_dir / "physics_locomotion.md"
            if physics_kb.exists():
                pk_lines = len(physics_kb.read_text(encoding="utf-8").splitlines())
                lines.append(f"   Physics knowledge: {pk_lines} lines")
        lines.append("")

        # ── Layer 3: Physics Evolution Status ──
        lines.append("--- Layer 3: Physics Evolution (Self-Iteration) ---")
        if self.physics_layer:
            ps = self.physics_layer.state
            lines.extend([
                f"   Total cycles: {ps.total_cycles}",
                f"   Best physics fitness: {ps.best_physics_fitness:.3f}",
                f"   Best locomotion fitness: {ps.best_locomotion_fitness:.3f}",
                f"   Best combined fitness: {ps.best_combined_fitness:.3f}",
                f"   Knowledge rules generated: {ps.knowledge_rules_generated}",
                f"   Successful strategies: {len(ps.successful_strategies)}",
                f"   Stagnation count: {ps.stagnation_count}",
            ])
        else:
            lines.append("   [DISABLED]")
        lines.append("")

        # ── Math Model Registry ──
        all_models = self.math_registry.list_all()
        stable = [m for m in all_models if m.status == "stable"]
        experimental = [m for m in all_models if m.status == "experimental"]
        lines.extend([
            "--- Math Model Registry ---",
            f"   Total: {len(all_models)} | Stable: {len(stable)} | Experimental: {len(experimental)}",
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
            lines.append("Capability Gaps:")
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
                "   - All three layers run locally without AI dependency",
                "   - Layer 1: visual quality optimization with QC",
                "   - Layer 2: knowledge distillation (requires source documents)",
                "   - Layer 3: physics evolution with auto-diagnosis",
                "   - Stagnation: auto-recover → widen space → escalate",
            ])
        else:
            lines.extend([
                "   Currently in ASSISTED mode:",
                "   - AI arbitration enabled for stagnation resolution",
                "   - Layer 3 can use LLM for physics diagnosis",
                "   - Falls back to autonomous if AI unavailable",
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

        # SESSION-030: Physics-specific recommendations
        if self.physics_layer:
            ps = self.physics_layer.state
            if ps.best_combined_fitness < 0.5:
                recommendations.append(
                    "PHYSICS: Layer 3 combined fitness below 0.5. Consider running "
                    "more physics evolution cycles or distilling additional DeepMimic/ASE papers."
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
