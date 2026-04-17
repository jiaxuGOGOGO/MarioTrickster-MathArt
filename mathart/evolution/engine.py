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
# SESSION-040: Pipeline Contract Evolution Bridge
from .evolution_contract_bridge import ContractEvolutionBridge
# SESSION-041: Visual Regression Evolution Bridge
from .visual_regression_bridge import VisualRegressionEvolutionBridge
from .layer3_closed_loop import Layer3ClosedLoopDistiller, TransitionTuningTarget
from .evolution_loop import collect_closed_loop_status, collect_analytical_rendering_status
# SESSION-045: Neural Rendering Evolution Bridge (Gap C3)
from .neural_rendering_bridge import NeuralRenderingEvolutionBridge, collect_neural_rendering_status
# SESSION-046: Stable Fluids VFX Bridge (Gap C2)
from .fluid_vfx_bridge import FluidVFXEvolutionBridge, collect_fluid_vfx_status
# SESSION-047: Jakobsen Secondary Chain Bridge (Gap B1)
from .jakobsen_bridge import JakobsenEvolutionBridge, collect_jakobsen_chain_status
# SESSION-048: Terrain Sensor Bridge (Gap B2)
from .terrain_sensor_bridge import TerrainSensorEvolutionBridge, collect_terrain_sensor_status
# SESSION-049: Gait Blend Bridge (Gap B3)
from .gait_blend_bridge import GaitBlendEvolutionBridge, collect_gait_blend_status


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

        # SESSION-043: Active Layer 3 closed loop — runtime transition tuning
        self.closed_loop_layer = Layer3ClosedLoopDistiller(
            project_root=self.project_root,
            session_id="SESSION-043",
            random_seed=42,
            verbose=verbose,
        )

        # Supporting: Math model registry
        self.math_registry = MathModelRegistry()

        # SESSION-040: Pipeline Contract Evolution Bridge
        self.contract_bridge = ContractEvolutionBridge(
            project_root=self.project_root,
            verbose=verbose,
        )

        # SESSION-041: Visual Regression Evolution Bridge
        self.visual_regression_bridge = VisualRegressionEvolutionBridge(
            project_root=self.project_root,
            verbose=verbose,
        )

        # SESSION-045: Neural Rendering Evolution Bridge (Gap C3)
        self.neural_rendering_bridge = NeuralRenderingEvolutionBridge(
            project_root=self.project_root,
            verbose=verbose,
        )

        # SESSION-046: Stable Fluids VFX Bridge (Gap C2)
        self.fluid_vfx_bridge = FluidVFXEvolutionBridge(
            project_root=self.project_root,
            verbose=verbose,
        )

        # SESSION-047: Jakobsen Secondary Chain Bridge (Gap B1)
        self.jakobsen_bridge = JakobsenEvolutionBridge(
            project_root=self.project_root,
            verbose=verbose,
        )

        # SESSION-048: Terrain Sensor Bridge (Gap B2)
        self.terrain_sensor_bridge = TerrainSensorEvolutionBridge(
            project_root=self.project_root,
            verbose=verbose,
        )

        # SESSION-049: Gait Blend Bridge (Gap B3)
        self.gait_blend_bridge = GaitBlendEvolutionBridge(
            project_root=self.project_root,
            verbose=verbose,
        )

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

    # ── SESSION-040: Contract-Aware Evolution ─────────────────────────────

    def evaluate_contract(
        self,
        manifest_path: str | Path,
        umr_manifest_path: str | Path | None = None,
    ) -> dict:
        """Evaluate pipeline contract compliance and distill knowledge.

        This is the unified entry point for contract-aware evolution.
        It runs all three layers of the contract evolution cycle:
        1. Layer 1: Validate contract compliance
        2. Layer 2: Distill knowledge from results
        3. Layer 3: Compute fitness bonus

        Returns
        -------
        dict
            Contract evaluation results including metrics, rules, and fitness.
        """
        metrics = self.contract_bridge.evaluate_contract_compliance(
            manifest_path, umr_manifest_path
        )
        rules = self.contract_bridge.distill_contract_knowledge(metrics)
        fitness_bonus = self.contract_bridge.compute_contract_fitness_bonus(metrics)

        if self.verbose:
            status = "PASS" if metrics.contract_checks_failed == 0 else "FAIL"
            print(f"\n[CONTRACT] Evaluation: {status}")
            print(f"  Checks: {metrics.contract_checks_passed} passed, "
                  f"{metrics.contract_checks_failed} failed")
            print(f"  Hash seal: {'verified' if metrics.hash_seal_verified else 'missing'} "
                  f"({'stable' if metrics.hash_seal_stable else 'unstable'})")
            print(f"  Knowledge rules: {len(rules)} generated")
            print(f"  Fitness bonus: {fitness_bonus:+.3f}")

        return {
            "metrics": metrics.to_dict(),
            "rules": rules,
            "fitness_bonus": fitness_bonus,
            "contract_status": "PASS" if metrics.contract_checks_failed == 0 else "FAIL",
        }

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

    def run_transition_closed_loop(
        self,
        source_state: str = "run",
        target_state: str = "jump",
        source_phase: float = 0.8,
        n_trials: int = 24,
    ) -> dict[str, Any]:
        """Run the active Layer 3 runtime transition tuning loop.

        This is the formal engine-level entry point for Gap 4. It performs the
        runtime query → synthesize → score → Optuna search → write-back cycle
        and returns the distilled rule plus bridge payload.
        """
        result = self.closed_loop_layer.optimize_transition(
            target=TransitionTuningTarget(
                source_state=source_state,
                target_state=target_state,
                source_phase=source_phase,
            ),
            n_trials=n_trials,
        )
        return result.to_dict()

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

    # ── SESSION-041: Visual Regression Evaluation ────────────────────────

    def evaluate_visual_regression(
        self,
        audit_report: dict | None = None,
    ) -> dict:
        """Evaluate visual regression and distill knowledge.

        This is the unified entry point for visual-regression-aware evolution.
        It runs all three layers of the visual regression cycle:
        1. Layer 1: Evaluate visual regression (SSIM + structural)
        2. Layer 2: Distill knowledge from results
        3. Layer 3: Compute fitness bonus

        Returns
        -------
        dict
            Visual regression evaluation results.
        """
        metrics = self.visual_regression_bridge.evaluate_visual_regression(
            audit_report
        )
        rules = self.visual_regression_bridge.distill_visual_knowledge(metrics)
        fitness_bonus = self.visual_regression_bridge.compute_visual_fitness_bonus(
            metrics
        )

        if self.verbose:
            status = "PASS" if metrics.all_pass else "FAIL"
            ssim_str = f"{metrics.ssim_score:.6f}" if metrics.ssim_score else "N/A"
            print(f"\n[VISUAL REGRESSION] Evaluation: {status}")
            print(f"  SSIM: {ssim_str}")
            print(f"  Levels: L0={metrics.level0_pass}, L1={metrics.level1_pass}, L2={metrics.level2_pass}")
            print(f"  Knowledge rules: {len(rules)} generated")
            print(f"  Fitness bonus: {fitness_bonus:+.3f}")

        return {
            "metrics": metrics.to_dict(),
            "rules": rules,
            "fitness_bonus": fitness_bonus,
            "visual_status": "PASS" if metrics.all_pass else "FAIL",
        }

    # ── SESSION-045: Temporal Consistency Evaluation ──────────────────────

    def evaluate_temporal_consistency(
        self,
        rendered_frames: list | None = None,
        mv_sequence: Any | None = None,
        warp_error_threshold: float = 0.15,
    ) -> dict:
        """Evaluate temporal consistency and distill knowledge.

        This is the unified entry point for temporal-consistency-aware evolution.
        It runs all three layers of the neural rendering cycle:
        1. Layer 1: Evaluate temporal consistency (warp error + flicker)
        2. Layer 2: Distill knowledge from results
        3. Layer 3: Compute fitness bonus

        Returns
        -------
        dict
            Temporal consistency evaluation results.
        """
        metrics = self.neural_rendering_bridge.evaluate_temporal_consistency(
            rendered_frames, mv_sequence, warp_error_threshold
        )
        rules = self.neural_rendering_bridge.distill_temporal_knowledge(metrics)
        fitness_bonus = self.neural_rendering_bridge.compute_temporal_fitness_bonus(
            metrics
        )

        if self.verbose:
            status = "PASS" if metrics.temporal_pass else "FAIL"
            print(f"\n[TEMPORAL CONSISTENCY] Evaluation: {status}")
            print(f"  Warp error: {metrics.mean_warp_error:.4f} "
                  f"(threshold: {metrics.warp_error_threshold})")
            print(f"  Flicker score: {metrics.flicker_score:.4f}")
            print(f"  Coverage: {metrics.coverage:.2%}")
            print(f"  Knowledge rules: {len(rules)} generated")
            print(f"  Fitness bonus: {fitness_bonus:+.3f}")

        return {
            "metrics": metrics.to_dict(),
            "rules": rules,
            "fitness_bonus": fitness_bonus,
            "temporal_status": "PASS" if metrics.temporal_pass else "FAIL",
        }

    def _update_brain(
        self,
        result: InnerLoopResult,
        physics_record: Optional[PhysicsEvolutionRecord] = None,
    ) -> None:
        """Update PROJECT_BRAIN.json with the latest run results.

        SESSION-035: Now also persists Layer 3 converged parameters
        for automatic export-time parameter selection (Gap #3 fix).
        SESSION-041: Also persists visual regression bridge state.
        """
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

                # SESSION-035: Persist converged parameters for export bridge
                # This is the critical Gap #3 fix: Layer 3 evaluation results
                # now automatically feed into the next produce_character_pack()
                # parameter selection via the convergence bridge.
                if self.physics_layer and self.physics_layer.converged_params:
                    import json
                    converged = self.physics_layer.converged_params
                    mem.set_note(
                        "layer3_converged_params",
                        json.dumps(converged)
                    )
                    mem.set_note(
                        "layer3_recommended_physics_stiffness",
                        f"{converged.get('physics_stiffness', 1.0):.3f}"
                    )
                    mem.set_note(
                        "layer3_recommended_compliance_alpha",
                        f"{converged.get('compliance_alpha', 0.6):.3f}"
                    )
                    mem.set_note(
                        "layer3_amp_style_reward",
                        f"{converged.get('amp_style_reward', 0.0):.3f}"
                    )
                    mem.set_note(
                        "layer3_vposer_naturalness",
                        f"{converged.get('vposer_naturalness', 0.0):.3f}"
                    )

                    # SESSION-035: Save convergence bridge file for pipeline consumption
                    bridge_path = self.project_root / "LAYER3_CONVERGENCE_BRIDGE.json"
                    bridge_path.write_text(
                        json.dumps(converged, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

            # SESSION-041: Persist visual regression bridge state
            if self.visual_regression_bridge:
                vrs = self.visual_regression_bridge.state
                mem.set_note(
                    "session041_visual_regression_cycles",
                    str(vrs.total_audit_cycles)
                )
                mem.set_note(
                    "session041_visual_regression_pass_rate",
                    f"{vrs.total_passes}/{vrs.total_audit_cycles}"
                    if vrs.total_audit_cycles > 0 else "N/A"
                )
                mem.set_note(
                    "session041_best_ssim",
                    f"{vrs.best_ssim:.6f}"
                )
                mem.set_note(
                    "session041_consecutive_passes",
                    str(vrs.consecutive_passes)
                )
                if vrs.golden_baseline_hash:
                    mem.set_note(
                        "session041_golden_baseline",
                        vrs.golden_baseline_hash[:24] + "..."
                    )

            # SESSION-045: Persist neural rendering bridge state
            if self.neural_rendering_bridge:
                nrs = self.neural_rendering_bridge.state
                mem.set_note(
                    "session045_neural_rendering_cycles",
                    str(nrs.total_evaluation_cycles)
                )
                mem.set_note(
                    "session045_neural_rendering_pass_rate",
                    f"{nrs.total_passes}/{nrs.total_evaluation_cycles}"
                    if nrs.total_evaluation_cycles > 0 else "N/A"
                )
                mem.set_note(
                    "session045_best_warp_error",
                    f"{nrs.best_warp_error:.4f}"
                )
                mem.set_note(
                    "session045_optimal_skinning_sigma",
                    f"{nrs.optimal_skinning_sigma:.3f}"
                )
                mem.set_note(
                    "session045_knowledge_rules",
                    str(nrs.knowledge_rules_total)
                )

            # SESSION-046: Persist fluid VFX bridge state
            if self.fluid_vfx_bridge:
                fvs = self.fluid_vfx_bridge.state
                mem.set_note(
                    "session046_fluid_vfx_cycles",
                    str(fvs.total_cycles)
                )
                mem.set_note(
                    "session046_fluid_vfx_pass_rate",
                    f"{fvs.total_passes}/{fvs.total_cycles}"
                    if fvs.total_cycles > 0 else "N/A"
                )
                mem.set_note(
                    "session046_best_flow_energy",
                    f"{fvs.best_flow_energy:.6f}"
                )
                mem.set_note(
                    "session046_lowest_obstacle_leak_ratio",
                    f"{fvs.lowest_obstacle_leak_ratio:.6f}"
                )
                mem.set_note(
                    "session046_fluid_knowledge_rules",
                    str(fvs.knowledge_rules_total)
                )

            # SESSION-047: Persist Jakobsen secondary-chain bridge state
            if self.jakobsen_bridge:
                jbs = self.jakobsen_bridge.state
                mem.set_note(
                    "session047_jakobsen_cycles",
                    str(jbs.total_cycles)
                )
                mem.set_note(
                    "session047_jakobsen_pass_rate",
                    f"{jbs.total_passes}/{jbs.total_cycles}"
                    if jbs.total_cycles > 0 else "N/A"
                )
                mem.set_note(
                    "session047_best_constraint_error",
                    f"{jbs.best_mean_constraint_error:.6f}"
                )
                mem.set_note(
                    "session047_best_tip_lag",
                    f"{jbs.best_mean_tip_lag:.6f}"
                )
                mem.set_note(
                    "session047_jakobsen_knowledge_rules",
                    str(jbs.knowledge_rules_total)
                )

            # SESSION-040: Persist contract evolution bridge state
            if self.contract_bridge:
                cs = self.contract_bridge.state
                mem.set_note(
                    "session040_contract_cycles",
                    str(cs.total_contract_cycles)
                )
                mem.set_note(
                    "session040_contract_pass_rate",
                    f"{cs.total_contract_passes}/{cs.total_contract_cycles}"
                    if cs.total_contract_cycles > 0 else "N/A"
                )
                mem.set_note(
                    "session040_hash_stability_streak",
                    str(cs.hash_stability_streak)
                )
                if cs.golden_master_hash:
                    mem.set_note(
                        "session040_golden_master",
                        cs.golden_master_hash[:24] + "..."
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

        # ── SESSION-040: Pipeline Contract Status ──
        lines.append("--- Pipeline Contract (SESSION-040) ---")
        lines.append(self.contract_bridge.status_report().replace(
            "--- Pipeline Contract Evolution Bridge (SESSION-040) ---", ""
        ).strip())
        lines.append("")

        # ── SESSION-041: Visual Regression Status ──
        lines.append("--- Visual Regression (SESSION-041) ---")
        lines.append(self.visual_regression_bridge.status_report().replace(
            "--- Visual Regression Evolution Bridge (SESSION-041) ---", ""
        ).strip())
        lines.append("")

        # ── SESSION-043: Active runtime tuning status ──
        lines.append("--- Layer 3A: Active Runtime Closed Loop (SESSION-043) ---")
        closed_loop = collect_closed_loop_status(self.project_root)
        lines.extend([
            f"   Distilled transition rules: {closed_loop.rule_count}",
            f"   Last tuned transition: {closed_loop.last_transition_key or 'N/A'}",
            f"   Last best loss: {closed_loop.last_best_loss:.6f}",
            f"   Bridge active: {'yes' if closed_loop.bridge_exists else 'no'}",
        ])
        if closed_loop.tracked_rules:
            lines.append(f"   Rule keys: {', '.join(closed_loop.tracked_rules)}")
        if closed_loop.report_path:
            lines.append(f"   Latest report: {closed_loop.report_path}")
        lines.append("")

        # ── SESSION-045: Neural Rendering Bridge status ──
        lines.append("--- Neural Rendering Bridge (SESSION-045 / Gap C3) ---")
        lines.append(self.neural_rendering_bridge.status_report().replace(
            "--- Neural Rendering Evolution Bridge (SESSION-045 / Gap C3) ---", ""
        ).strip())
        lines.append("")

        # ── SESSION-046: Fluid VFX Bridge status ──
        lines.append("--- Fluid VFX Bridge (SESSION-046 / Gap C2) ---")
        lines.append(self.fluid_vfx_bridge.status_report().replace(
            "--- Fluid VFX Evolution Bridge (SESSION-046 / Gap C2) ---", ""
        ).strip())
        fluid_status = collect_fluid_vfx_status(self.project_root)
        lines.extend([
            f"   Module active: {'yes' if fluid_status.module_exists else 'no'}",
            f"   Pipeline presets integrated: {'yes' if fluid_status.pipeline_supports_fluid_presets else 'no'}",
            f"   Public API export: {'yes' if fluid_status.public_api_exports_fluid_vfx else 'no'}",
            f"   Test present: {'yes' if fluid_status.test_exists else 'no'}",
        ])
        if fluid_status.tracked_exports:
            lines.append(f"   Tracked exports: {', '.join(fluid_status.tracked_exports)}")
        if fluid_status.research_notes_path:
            lines.append(f"   Research notes: {fluid_status.research_notes_path}")
        lines.append("")

        # ── SESSION-047: Jakobsen Secondary Chain Bridge status ──
        lines.append("--- Jakobsen Secondary Chain Bridge (SESSION-047 / Gap B1) ---")
        lines.append(self.jakobsen_bridge.status_report().replace(
            "--- Jakobsen Secondary Chain Evolution Bridge (SESSION-047 / Gap B1) ---", ""
        ).strip())
        jakobsen_status = collect_jakobsen_chain_status(self.project_root)
        lines.extend([
            f"   Module active: {'yes' if jakobsen_status.module_exists else 'no'}",
            f"   Pipeline integration: {'yes' if jakobsen_status.pipeline_supports_secondary_chains else 'no'}",
            f"   Public API export: {'yes' if jakobsen_status.public_api_exports_chain else 'no'}",
            f"   Test present: {'yes' if jakobsen_status.test_exists else 'no'}",
        ])
        if jakobsen_status.tracked_exports:
            lines.append(f"   Tracked exports: {', '.join(jakobsen_status.tracked_exports)}")
        if jakobsen_status.research_notes_path:
            lines.append(f"   Research notes: {jakobsen_status.research_notes_path}")
        lines.append("")

        # ── SESSION-048: Terrain Sensor Bridge status ──
        lines.append("--- Terrain Sensor Bridge (SESSION-048 / Gap B2) ---")
        lines.append(self.terrain_sensor_bridge.status_report().replace(
            "--- Terrain Sensor Evolution Bridge (SESSION-048 / Gap B2) ---", ""
        ).strip())
        terrain_status = collect_terrain_sensor_status(self.project_root)
        lines.extend([
            f"   Module active: {'yes' if terrain_status.module_exists else 'no'}",
            f"   Pipeline integration: {'yes' if terrain_status.pipeline_supports_terrain_sensor else 'no'}",
            f"   Public API export: {'yes' if terrain_status.public_api_exports_sensor else 'no'}",
            f"   Test present: {'yes' if terrain_status.test_exists else 'no'}",
        ])
        if terrain_status.tracked_exports:
            lines.append(f"   Tracked exports: {', '.join(terrain_status.tracked_exports)}")
        if terrain_status.research_notes_path:
            lines.append(f"   Research notes: {terrain_status.research_notes_path}")
        lines.append("")

        # ── SESSION-049: Gait Blend Bridge status ──
        lines.append("--- Gait Blend Bridge (SESSION-049 / Gap B3) ---")
        gait_status = collect_gait_blend_status(self.project_root)
        lines.extend([
            f"   Module active: {'yes' if gait_status.module_exists else 'no'}",
            f"   Bridge active: {'yes' if gait_status.bridge_exists else 'no'}",
            f"   Public API export: {'yes' if gait_status.public_api_exports_blender else 'no'}",
            f"   Test present: {'yes' if gait_status.test_exists else 'no'}",
        ])
        if gait_status.tracked_exports:
            lines.append(f"   Tracked exports: {', '.join(gait_status.tracked_exports)}")
        if gait_status.research_notes_path:
            lines.append(f"   Research notes: {gait_status.research_notes_path}")
        lines.extend([
            f"   Total cycles: {gait_status.total_cycles}",
            f"   Consecutive passes: {gait_status.consecutive_passes}",
            f"   Best sliding error: {gait_status.best_mean_sliding_error:.6f}",
        ])
        lines.append("")

        # ── SESSION-044: Analytical SDF rendering status ──
        lines.append("--- Layer 2.5: Analytical SDF Rendering (SESSION-044) ---")
        analytical = collect_analytical_rendering_status(self.project_root)
        lines.extend([
            f"   Aux module active: {'yes' if analytical.aux_module_exists else 'no'}",
            f"   Industrial aux-map export: {'yes' if analytical.industrial_renderer_supports_aux_maps else 'no'}",
            f"   Public API export: {'yes' if analytical.public_api_exports_aux_maps else 'no'}",
            f"   Auxiliary test present: {'yes' if analytical.auxiliary_test_exists else 'no'}",
        ])
        if analytical.tracked_exports:
            lines.append(f"   Tracked exports: {', '.join(analytical.tracked_exports)}")
        if analytical.research_notes_path:
            lines.append(f"   Research notes: {analytical.research_notes_path}")
        lines.append("")

        # ── Layer 3: Physics Evolution Status ──
        lines.append("--- Layer 3B: Physics Evolution (Self-Iteration) ---")
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
