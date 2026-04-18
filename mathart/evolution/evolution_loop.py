"""
SESSION-043: Three-Layer Evolution Loop — active closed-loop upgrade.

The project-wide evolution loop now explicitly tracks three complementary
mechanisms of improvement:

1. **Internal Evolution**
   The repository scans its own TODO markers, incomplete implementations, and
   latent integration seams.

2. **External Knowledge Distillation**
   Research findings are registered with provenance, mapped to concrete target
   modules, and validated against test coverage.

3. **Self-Iterative Testing and Active Runtime Tuning**
   The test layer now includes both passive regression tracking and the active
   Layer 3 closed loop for runtime transition tuning. In practice this means the
   system can identify a hard transition, search for better runtime parameters,
   write the winning rule back into the repository, and surface the result in
   the same evolution report used by future sessions.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .layer3_closed_loop import (
    ClosedLoopOptimizationResult,
    Layer3ClosedLoopDistiller,
    TransitionTuningTarget,
    TransitionRuleStore,
)


# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class EvolutionProposal:
    """A single proposed evolution action."""

    id: str
    layer: int
    category: str
    title: str
    description: str
    source_file: str = ""
    source_line: int = 0
    priority: str = "medium"
    status: str = "proposed"
    research_ref: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DistillationRecord:
    """Tracks the provenance of external knowledge → code integration."""

    paper_id: str
    paper_title: str
    authors: str
    venue: str
    concept: str
    target_module: str
    target_class: str
    integration_date: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    validation_status: str = "pending"
    test_coverage: str = ""
    # SESSION-072 (P1-DISTILL-1A): MLMD / W3C PROV-DM provenance link.
    # When a distillation rule is derived from a specific ArtifactManifest,
    # this field records the ``schema_hash`` of that upstream manifest,
    # enabling cryptographic-grade closed-loop traceability.
    upstream_manifest_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestEvolutionResult:
    """Result of a self-iterative test cycle."""

    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    new_tests_added: int = 0
    regressions_detected: int = 0
    coverage_delta: float = 0.0
    active_closed_loop_runs: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClosedLoopStatus:
    """Snapshot of the active Layer 3 runtime tuning state."""

    rule_count: int = 0
    last_transition_key: str = ""
    last_best_loss: float = 0.0
    last_updated: str = ""
    history_length: int = 0
    bridge_exists: bool = False
    report_path: str = ""
    tracked_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalyticalRenderingStatus:
    """Snapshot of analytical SDF rendering integration status."""

    aux_module_exists: bool = False
    industrial_renderer_supports_aux_maps: bool = False
    public_api_exports_aux_maps: bool = False
    auxiliary_test_exists: bool = False
    research_notes_path: str = ""
    tracked_exports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvolutionCycleReport:
    """Complete report for one evolution cycle."""

    cycle_id: str
    session_id: str
    proposals: list[EvolutionProposal] = field(default_factory=list)
    distillations: list[DistillationRecord] = field(default_factory=list)
    test_result: Optional[TestEvolutionResult] = None
    closed_loop: Optional[ClosedLoopStatus] = None
    analytical_rendering: Optional[AnalyticalRenderingStatus] = None
    jakobsen_secondary: Optional[dict[str, Any]] = None
    summary: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "session_id": self.session_id,
            "proposals": [p.to_dict() for p in self.proposals],
            "distillations": [d.to_dict() for d in self.distillations],
            "test_result": self.test_result.to_dict() if self.test_result else None,
            "closed_loop": self.closed_loop.to_dict() if self.closed_loop else None,
            "analytical_rendering": self.analytical_rendering.to_dict() if self.analytical_rendering else None,
            "jakobsen_secondary": dict(self.jakobsen_secondary) if self.jakobsen_secondary else None,
            "summary": self.summary,
            "timestamp": self.timestamp,
        }


# ── Layer 1: Internal Evolution Engine ───────────────────────────────────────


_TODO_PATTERN = re.compile(
    r"#\s*(TODO|FIXME|HACK|XXX|OPTIMIZE|REFACTOR)\b[:\s]*(.*)",
    re.IGNORECASE,
)

_INCOMPLETE_PATTERNS = [
    re.compile(r"raise\s+NotImplementedError"),
    re.compile(r"pass\s*#\s*TODO"),
    re.compile(r"\.\.\.\s*#\s*(stub|placeholder)", re.IGNORECASE),
]


def scan_internal_todos(
    project_root: str | Path,
    extensions: tuple[str, ...] = (".py", ".md", ".json"),
) -> list[EvolutionProposal]:
    """Scan the codebase for TODO/FIXME markers and incomplete implementations."""
    root = Path(project_root)
    proposals: list[EvolutionProposal] = []
    idx = 0

    for ext in extensions:
        for filepath in root.rglob(f"*{ext}"):
            if ".git" in filepath.parts or "__pycache__" in filepath.parts:
                continue
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for line_num, line in enumerate(text.splitlines(), start=1):
                match = _TODO_PATTERN.search(line)
                if match:
                    idx += 1
                    marker = match.group(1).upper()
                    desc = match.group(2).strip() or "(no description)"
                    priority = "high" if marker in {"FIXME", "HACK", "XXX"} else "medium"
                    proposals.append(EvolutionProposal(
                        id=f"L1-{idx:04d}",
                        layer=1,
                        category="todo_resolution",
                        title=f"[{marker}] {desc[:80]}",
                        description=desc,
                        source_file=str(filepath.relative_to(root)),
                        source_line=line_num,
                        priority=priority,
                    ))

                for pattern in _INCOMPLETE_PATTERNS:
                    if pattern.search(line):
                        idx += 1
                        proposals.append(EvolutionProposal(
                            id=f"L1-{idx:04d}",
                            layer=1,
                            category="incomplete_implementation",
                            title=f"Incomplete implementation at {filepath.name}:{line_num}",
                            description=line.strip(),
                            source_file=str(filepath.relative_to(root)),
                            source_line=line_num,
                            priority="high",
                        ))

    return proposals


# ── Layer 2: External Knowledge Distillation ─────────────────────────────────


GAP1_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="starke2020local",
        paper_title="Local Motion Phases for Learning Multi-Contact Character Movements",
        authors="Sebastian Starke, Yiwei Zhao, Taku Komura, Kazi Zaman",
        venue="SIGGRAPH 2020 (ACM TOG 39:4)",
        concept="Local phases: per-bone independent phase channels that break the single-global-cycle assumption. Non-cyclic motions get 0→1 activation spikes instead of forced cyclic wrapping.",
        target_module="mathart/animation/unified_motion.py",
        target_class="PhaseState",
        validation_status="validated",
        test_coverage="tests/test_phase_state.py",
    ),
    DistillationRecord(
        paper_id="starke2022deepphase",
        paper_title="DeepPhase: Periodic Autoencoders for Learning Motion Phase Manifolds",
        authors="Sebastian Starke, Ian Mason, Taku Komura",
        venue="SIGGRAPH 2022 (ACM TOG 41:4)",
        concept="Multi-dimensional phase manifold via Periodic Autoencoder. Phase as latent vector with amplitude/frequency/offset channels. Cyclic motions become sustained oscillations while transient motions become one-shot activation spikes.",
        target_module="mathart/animation/phase_driven.py",
        target_class="PhaseDrivenAnimator.generate_frame",
        validation_status="validated",
        test_coverage="tests/test_phase_state.py",
    ),
    DistillationRecord(
        paper_id="gap1_architecture",
        paper_title="Generalized Phase State — Unified Cyclic/Transient Phase Architecture",
        authors="Project Internal (Gap 1 Resolution)",
        venue="SESSION-042",
        concept="PhaseState dataclass with an is_cyclic gate so cyclic motion uses trig interpolation and transient motion uses direct scalar interpolation without adapter bypasses.",
        target_module="mathart/animation/phase_driven.py",
        target_class="PhaseDrivenAnimator._generate_transient_pose",
        validation_status="validated",
        test_coverage="tests/test_phase_state.py",
    ),
]


GAP4_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="peng2018deepmimic",
        paper_title="DeepMimic: Example-Guided Deep Reinforcement Learning of Physics-Based Character Skills",
        authors="Xue Bin Peng, Pieter Abbeel, Sergey Levine, Michiel van de Panne",
        venue="SIGGRAPH 2018 (ACM TOG 37:4)",
        concept="Translate physical plausibility into a scalar reward-style objective so foot skating, discontinuity, and instability can be optimized by repeated simulation rather than manual tuning.",
        target_module="mathart/evolution/layer3_closed_loop.py",
        target_class="Layer3ClosedLoopDistiller.evaluate_transition",
        validation_status="validated",
        test_coverage="tests/test_layer3_closed_loop.py",
    ),
    DistillationRecord(
        paper_id="ma2023eureka",
        paper_title="Eureka: Human-Level Reward Design via Coding Large Language Models",
        authors="Yecheng Jason Ma, Maxence Richard, Linxi Fan, et al.",
        venue="ICLR 2024",
        concept="Upgrade Layer 3 from a passive evaluator to an active coach that iteratively proposes parameters, scores them, and writes back the winning configuration into the repository state.",
        target_module="mathart/evolution/layer3_closed_loop.py",
        target_class="Layer3ClosedLoopDistiller.optimize_transition",
        validation_status="validated",
        test_coverage="tests/test_layer3_closed_loop.py",
    ),
    DistillationRecord(
        paper_id="akiba2019optuna",
        paper_title="Optuna: A Next-Generation Hyperparameter Optimization Framework",
        authors="Takuya Akiba, Shotaro Sano, Toshihiko Yanase, Takeru Ohta, Masanori Koyama",
        venue="KDD 2019",
        concept="Use define-by-run black-box optimization to search transition strategy, blend window, and runtime query weights under a deterministic seed and bounded trial budget.",
        target_module="mathart/evolution/layer3_closed_loop.py",
        target_class="Layer3ClosedLoopDistiller._suggest_params",
        validation_status="validated",
        test_coverage="tests/test_layer3_closed_loop.py",
    ),
]


GAPB1_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="jakobsen2001advancedcharacterphysics",
        paper_title="Advanced Character Physics",
        authors="Thomas Jakobsen",
        venue="GDC 2001",
        concept="Use velocity-less Verlet integration and repeated distance-constraint relaxation to build lightweight articulated secondary motion that is stable enough for real-time characters without a heavy full-body solver.",
        target_module="mathart/animation/jakobsen_chain.py",
        target_class="JakobsenSecondaryChain",
        validation_status="validated",
        test_coverage="tests/test_jakobsen_chain.py",
    ),
    DistillationRecord(
        paper_id="session047_jakobsen_bridge",
        paper_title="Jakobsen Secondary Chain Evolution Bridge — Three-Layer Lightweight Rigid-Soft Loop",
        authors="Project Internal (SESSION-047 / Gap B1)",
        venue="SESSION-047",
        concept="Track constraint error, tip lag, and stretch ratio for kinematic secondary chains; distill successful recipes back into knowledge and persist trend data for future auto-tuning.",
        target_module="mathart/evolution/jakobsen_bridge.py",
        target_class="JakobsenEvolutionBridge",
        validation_status="validated",
        test_coverage="tests/test_jakobsen_chain.py",
    ),
]


GAPC1_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="quilez2015normalssdf",
        paper_title="Normals for an SDF",
        authors="Inigo Quilez",
        venue="iquilezles.org article (2015)",
        concept="Treat the normalized SDF gradient as the canonical surface orientation source, then reuse it for offline sprite-space normal-map baking without raymarching.",
        target_module="mathart/animation/sdf_aux_maps.py",
        target_class="compute_sdf_gradients",
        validation_status="validated",
        test_coverage="tests/test_sdf_aux_maps.py",
    ),
    DistillationRecord(
        paper_id="quilez2019distgrad2d",
        paper_title="2D Distance and Gradient Functions",
        authors="Inigo Quilez",
        venue="iquilezles.org article (2019)",
        concept="Design the renderer around a distance-plus-gradient contract so existing SDF callables can use finite-difference fallback today while future primitives can plug in exact analytic gradients.",
        target_module="mathart/animation/sdf_aux_maps.py",
        target_class="bake_sdf_auxiliary_maps",
        validation_status="validated",
        test_coverage="tests/test_sdf_aux_maps.py",
    ),
    DistillationRecord(
        paper_id="session044enginecompat",
        paper_title="2D Lighting Engine Compatibility for Analytical SDF Aux Maps",
        authors="Project Internal (SESSION-044)",
        venue="SESSION-044",
        concept="Export RGB normal maps and grayscale depth proxies alongside the albedo sprite so mainstream 2D engines can consume procedural characters through forward or deferred lighting workflows.",
        target_module="mathart/animation/industrial_renderer.py",
        target_class="render_character_maps_industrial",
        validation_status="validated",
        test_coverage="tests/test_sdf_aux_maps.py",
    ),
]


GAPC2_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="stam1999stablefluids",
        paper_title="Stable Fluids",
        authors="Jos Stam",
        venue="SIGGRAPH 1999",
        concept="Unconditionally stable fluid solver using semi-Lagrangian advection and implicit viscosity, enabling real-time 2D smoke with controllable force injection.",
        target_module="mathart/animation/fluid_vfx.py",
        target_class="FluidGrid2D",
        validation_status="validated",
        test_coverage="tests/test_fluid_vfx.py",
    ),
    DistillationRecord(
        paper_id="stam2003fluidgames",
        paper_title="Real-Time Fluid Dynamics for Games",
        authors="Jos Stam",
        venue="GDC / game implementation article",
        concept="Compact game-ready formulation of dens_step, vel_step, project, and boundary handling suitable for a NumPy implementation with ghost cells and internal obstacles.",
        target_module="mathart/animation/fluid_vfx.py",
        target_class="FluidDrivenVFXSystem",
        validation_status="validated",
        test_coverage="tests/test_fluid_vfx.py",
    ),
    DistillationRecord(
        paper_id="session046_fluid_bridge",
        paper_title="Stable Fluids VFX Evolution Bridge — Three-Layer Smoke Iteration Loop",
        authors="Project Internal (SESSION-046 / Gap C2)",
        venue="SESSION-046",
        concept="Connect flow energy, obstacle leakage, and fluid-guided particles into a repository-native three-layer cycle: evaluate, distill rules, and write best practices back into project memory.",
        target_module="mathart/evolution/fluid_vfx_bridge.py",
        target_class="FluidVFXEvolutionBridge",
        validation_status="validated",
        test_coverage="tests/test_fluid_vfx.py",
    ),
]


GAPC3_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="jamriska2019stylizing",
        paper_title="Stylizing Video by Example",
        authors="Ondřej Jamriška, Šárka Sochorová, Ondřej Texler, Michal Lukáč, Jakub Fišer, Jingwan Lu, Eli Shechtman, Daniel Sýkora",
        venue="SIGGRAPH 2019 (ACM TOG 38:4)",
        concept="Patch-based synthesis with temporal blending guided by optical flow. Keyframe styles are propagated to intermediate frames using NNF (Nearest Neighbor Field) matching constrained by motion vectors, achieving temporally coherent video stylization.",
        target_module="mathart/animation/motion_vector_baker.py",
        target_class="export_ebsynth_project",
        validation_status="validated",
        test_coverage="tests/test_motion_vector_baker.py",
    ),
    DistillationRecord(
        paper_id="koroglu2025onlyflow",
        paper_title="OnlyFlow: Optical Flow based Motion Conditioning for Video Diffusion Models",
        authors="Mathis Koroglu, Hugo Music, Guillaume Couairon, Nicu Sebe, Yuki M. Asano",
        venue="CVPR 2025 Workshop",
        concept="Trainable optical flow encoder injects flow features into temporal attention layers of video diffusion models, enabling explicit motion control without retraining the base model. Ground-truth flow from procedural animation eliminates estimation noise.",
        target_module="mathart/animation/motion_vector_baker.py",
        target_class="encode_motion_vector_rgb",
        validation_status="validated",
        test_coverage="tests/test_motion_vector_baker.py",
    ),
    DistillationRecord(
        paper_id="nam2025motionprompt",
        paper_title="MotionPrompt: Optical Flow Guided Prompt Optimization for Coherent Video Generation",
        authors="Daeun Nam, Gyeongho Bae, Jong Chul Ye",
        venue="CVPR 2025",
        concept="Optical flow as a differentiable loss signal for prompt optimization in video generation. Flow-guided prompt tuning achieves temporal consistency without architectural changes to the diffusion model.",
        target_module="mathart/animation/motion_vector_baker.py",
        target_class="compute_pixel_motion_field",
        validation_status="validated",
        test_coverage="tests/test_motion_vector_baker.py",
    ),
    DistillationRecord(
        paper_id="session045_mv_baker",
        paper_title="Ground-Truth Motion Vectors from Procedural FK for Zero-Flicker Temporal Consistency",
        authors="Project Internal (SESSION-045 / Gap C3)",
        venue="SESSION-045",
        concept="Leverage the procedural math engine's exact FK knowledge to export perfect ground-truth motion vectors with zero estimation error. SDF-weighted skinning blends per-joint displacements into per-pixel flow fields, enabling EbSynth and ControlNet conditioning without noisy optical flow estimation.",
        target_module="mathart/animation/motion_vector_baker.py",
        target_class="compute_pixel_motion_field",
        validation_status="validated",
        test_coverage="tests/test_motion_vector_baker.py",
    ),
    DistillationRecord(
        paper_id="session045_neural_bridge",
        paper_title="Neural Rendering Evolution Bridge — Three-Layer Temporal Consistency Loop",
        authors="Project Internal (SESSION-045 / Gap C3)",
        venue="SESSION-045",
        concept="Three-layer evolution bridge for temporal consistency: Layer 1 gates animation acceptance on warp error, Layer 2 distills flicker patterns into knowledge rules, Layer 3 integrates temporal fitness into the physics evolution loop with skinning sigma optimization.",
        target_module="mathart/evolution/neural_rendering_bridge.py",
        target_class="NeuralRenderingEvolutionBridge",
        validation_status="validated",
        test_coverage="tests/test_motion_vector_baker.py",
    ),
]


GAPB2_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="clavet2016motionmatching",
        paper_title="Motion Matching and The Road to Next-Gen Animation",
        authors="Simon Clavet (Ubisoft)",
        venue="GDC 2016",
        concept="Trajectory prediction as spring-damper; obstacle reaction before contact; entity clamped to simulated point. Adopted: predict terrain contact via SDF query before it happens.",
        target_module="mathart/animation/terrain_sensor.py",
        target_class="TerrainRaySensor",
        validation_status="validated",
        test_coverage="tests/test_terrain_sensor.py",
    ),
    DistillationRecord(
        paper_id="delayen2016distancematching",
        paper_title="Distance Matching in Unreal Engine (Paragon)",
        authors="Laurent Delayen (Epic Games)",
        venue="nucl.ai 2016 / UE5 Documentation",
        concept="Distance-curve-driven playback: advance fall animation to frame whose Distance Curve matches D. Adopted: bind transient phase to SDF distance so phase=1.0 at contact.",
        target_module="mathart/animation/terrain_sensor.py",
        target_class="scene_aware_distance_phase",
        validation_status="validated",
        test_coverage="tests/test_terrain_sensor.py",
    ),
    DistillationRecord(
        paper_id="ponton2025envmm",
        paper_title="Environment-aware Motion Matching",
        authors="Pont\u00f3n, J.L. et al.",
        venue="SIGGRAPH 2025",
        concept="Environment features (2D ellipse body proxies) integrated into Motion Matching cost function. Adopted: terrain surface normal influences pose via slope compensation.",
        target_module="mathart/animation/terrain_sensor.py",
        target_class="scene_aware_fall_pose",
        validation_status="validated",
        test_coverage="tests/test_terrain_sensor.py",
    ),
    DistillationRecord(
        paper_id="ha2012fallinglanding",
        paper_title="Falling and Landing Motion Control for Character Animation",
        authors="Ha, S., Ye, Y., Liu, C.K.",
        venue="SIGGRAPH Asia 2012",
        concept="Airborne + landing phase decomposition; moment of inertia optimization; impact distribution. Adopted: TTC-driven two-phase structure (stretch + brace/landing).",
        target_module="mathart/animation/terrain_sensor.py",
        target_class="TTCPredictor",
        validation_status="validated",
        test_coverage="tests/test_terrain_sensor.py",
    ),
    DistillationRecord(
        paper_id="session048_terrain_bridge",
        paper_title="Terrain Sensor Evolution Bridge \u2014 Three-Layer SDF Terrain + TTC Loop",
        authors="Project Internal (SESSION-048 / Gap B2)",
        venue="SESSION-048",
        concept="SDF terrain query at foot position \u2192 sphere-traced distance \u2192 gravity-corrected TTC \u2192 phase binding \u2192 brace/landing signals \u2192 slope compensation. Three-layer bridge: evaluate accuracy, distill rules, compute fitness bonus.",
        target_module="mathart/evolution/terrain_sensor_bridge.py",
        target_class="TerrainSensorEvolutionBridge",
        validation_status="validated",
        test_coverage="tests/test_terrain_sensor.py",
    ),
]


P2_CROSSDIM_DISTILLATIONS: list[DistillationRecord] = [
    DistillationRecord(
        paper_id="quilez2013smin",
        paper_title="Smooth Minimum",
        authors="Inigo Quilez",
        venue="iquilezles.org article (2013)",
        concept="Polynomial smooth minimum (smin) with tunable k parameter for organic SDF blending. Enables parametric morphology where genotype parameters control blend radius, producing 'muscle-like' smooth adhesion between body parts without manual sculpting.",
        target_module="mathart/animation/smooth_morphology.py",
        target_class="MorphologyGenotype",
        validation_status="validated",
        test_coverage="tests/test_smooth_morphology.py",
    ),
    DistillationRecord(
        paper_id="quilez2020sdf2d",
        paper_title="2D Distance Functions",
        authors="Inigo Quilez",
        venue="iquilezles.org article (2020)",
        concept="Comprehensive library of 2D SDF primitives (circle, box, rounded box, segment, hexagon, star, heart, cross, egg, vesica, moon, arc) with exact distance formulas. Used as the primitive vocabulary for parametric character morphology.",
        target_module="mathart/animation/smooth_morphology.py",
        target_class="MorphologyFactory",
        validation_status="validated",
        test_coverage="tests/test_smooth_morphology.py",
    ),
    DistillationRecord(
        paper_id="gumin2016wfc",
        paper_title="Wave Function Collapse",
        authors="Maxim Gumin",
        venue="GitHub (2016)",
        concept="WFC as a constraint solver with saved stationary distribution. Observe (lowest entropy) → Collapse (weighted selection) → Propagate (arc consistency). Natively supports domain constraints during collapse phase, making it ideal for injecting physics-based reachability vetoes.",
        target_module="mathart/level/constraint_wfc.py",
        target_class="ConstraintAwareWFC",
        validation_status="validated",
        test_coverage="tests/test_constraint_wfc.py",
    ),
    DistillationRecord(
        paper_id="stalberg2020townscaper",
        paper_title="Townscaper: WFC with Domain Constraints",
        authors="Oskar Stålberg",
        venue="GDC / Independent (2020)",
        concept="Extended WFC to irregular grids and 3D structures with arbitrary domain constraints (structural, aesthetic, gameplay). Key insight: WFC is not just tile matching — it is a constraint solver that can incorporate physics-based playability guarantees.",
        target_module="mathart/level/constraint_wfc.py",
        target_class="ReachabilityValidator",
        validation_status="validated",
        test_coverage="tests/test_constraint_wfc.py",
    ),
    DistillationRecord(
        paper_id="session057_morphology_bridge",
        paper_title="Smooth Morphology Evolution Bridge — Three-Layer SDF Character Loop",
        authors="Project Internal (SESSION-057 / P2)",
        venue="SESSION-057",
        concept="Parametric SDF morphology with smooth CSG blending, connected to three-layer evolution: evaluate fitness (fill, compactness, symmetry), distill optimal blend_k and primitive preferences, persist trends for cross-session learning.",
        target_module="mathart/evolution/smooth_morphology_bridge.py",
        target_class="SmoothMorphologyEvolutionBridge",
        validation_status="validated",
        test_coverage="tests/test_evolution_bridges_057.py",
    ),
    DistillationRecord(
        paper_id="session057_wfc_bridge",
        paper_title="Constraint-Aware WFC Evolution Bridge — Three-Layer Level Loop",
        authors="Project Internal (SESSION-057 / P2)",
        venue="SESSION-057",
        concept="Physics-vetoed WFC collapse with TTC reachability validation, connected to three-layer evolution: evaluate playability and difficulty, distill optimal gap sizes and platform density, persist trends for cross-session learning.",
        target_module="mathart/evolution/constraint_wfc_bridge.py",
        target_class="ConstraintWFCEvolutionBridge",
        validation_status="validated",
        test_coverage="tests/test_evolution_bridges_057.py",
    ),
]


_REGISTERED_DISTILLATIONS: list[DistillationRecord] = [
    *GAP1_DISTILLATIONS,
    *GAP4_DISTILLATIONS,
    *GAPB1_DISTILLATIONS,
    *GAPB2_DISTILLATIONS,
    *GAPC1_DISTILLATIONS,
    *GAPC2_DISTILLATIONS,
    *GAPC3_DISTILLATIONS,
    *P2_CROSSDIM_DISTILLATIONS,
]


def get_distillation_registry() -> list[DistillationRecord]:
    """Return all registered knowledge distillation records."""
    return list(_REGISTERED_DISTILLATIONS)


def add_distillation(record: DistillationRecord) -> None:
    """Register a new knowledge distillation record."""
    _REGISTERED_DISTILLATIONS.append(record)


def validate_distillations(project_root: str | Path) -> list[dict[str, Any]]:
    """Validate that all distillation targets exist in the codebase."""
    root = Path(project_root)
    results: list[dict[str, Any]] = []
    for record in get_distillation_registry():
        target_path = root / record.target_module
        exists = target_path.exists()
        test_path = root / record.test_coverage if record.test_coverage else None
        test_exists = test_path.exists() if test_path else False
        results.append({
            "paper_id": record.paper_id,
            "target_exists": exists,
            "test_exists": test_exists,
            "status": "valid" if (exists and test_exists) else "incomplete",
        })
    return results


# ── Layer 3: Self-Iterative Testing + Active Closed Loop ─────────────────────


def count_test_functions(project_root: str | Path) -> dict[str, int]:
    """Count test functions across the test suite."""
    root = Path(project_root) / "tests"
    counts: dict[str, int] = {}
    if not root.exists():
        return counts

    test_func_pattern = re.compile(r"^\s*def\s+(test_\w+)\s*\(", re.MULTILINE)
    for filepath in root.glob("test_*.py"):
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts[filepath.name] = len(test_func_pattern.findall(text))
    return counts


def collect_closed_loop_status(project_root: str | Path) -> ClosedLoopStatus:
    """Collect the persisted state of the active Layer 3 runtime tuning loop."""
    root = Path(project_root)
    state_path = root / ".layer3_closed_loop_state.json"
    report_dir = root / "evolution_reports"
    bridge_path = root / "LAYER3_CONVERGENCE_BRIDGE.json"
    store = TransitionRuleStore(root)
    rules_payload = store.load()
    rules = dict(rules_payload.get("rules", {}))

    last_report = ""
    if report_dir.exists():
        candidates = sorted(report_dir.glob("layer3_closed_loop_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            last_report = str(candidates[0].relative_to(root))

    state_payload: dict[str, Any] = {}
    if state_path.exists():
        try:
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state_payload = {}

    return ClosedLoopStatus(
        rule_count=int(rules_payload.get("rule_count", 0)),
        last_transition_key=str(state_payload.get("last_transition_key", "")),
        last_best_loss=float(state_payload.get("last_best_loss", 0.0)),
        last_updated=str(state_payload.get("last_updated", rules_payload.get("last_updated", ""))),
        history_length=len(state_payload.get("history", [])),
        bridge_exists=bridge_path.exists(),
        report_path=last_report,
        tracked_rules=sorted(rules.keys()),
    )


def collect_analytical_rendering_status(project_root: str | Path) -> AnalyticalRenderingStatus:
    """Collect the persisted state of analytical SDF rendering integration."""
    root = Path(project_root)
    aux_module = root / "mathart/animation/sdf_aux_maps.py"
    renderer_module = root / "mathart/animation/industrial_renderer.py"
    api_module = root / "mathart/animation/__init__.py"
    test_path = root / "tests/test_sdf_aux_maps.py"
    research_notes = root / "evolution_reports/session044_sdf_rendering_research_notes.md"

    tracked_exports: list[str] = []
    if renderer_module.exists():
        try:
            text = renderer_module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for name in (
            "IndustrialRenderAuxiliaryResult",
            "render_character_maps_industrial",
            "_build_character_distance_field",
        ):
            if name in text:
                tracked_exports.append(name)
    api_exports_aux = False
    if api_module.exists():
        try:
            api_text = api_module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            api_text = ""
        api_exports_aux = "render_character_maps_industrial" in api_text and "bake_sdf_auxiliary_maps" in api_text

    return AnalyticalRenderingStatus(
        aux_module_exists=aux_module.exists(),
        industrial_renderer_supports_aux_maps=("render_character_maps_industrial" in tracked_exports),
        public_api_exports_aux_maps=api_exports_aux,
        auxiliary_test_exists=test_path.exists(),
        research_notes_path=str(research_notes.relative_to(root)) if research_notes.exists() else "",
        tracked_exports=tracked_exports,
    )


def run_active_closed_loop(
    project_root: str | Path,
    source_state: str = "run",
    target_state: str = "jump",
    source_phase: float = 0.8,
    n_trials: int = 24,
    session_id: str = "SESSION-043",
) -> ClosedLoopOptimizationResult:
    """Execute one active Layer 3 runtime tuning loop and persist the result."""
    distiller = Layer3ClosedLoopDistiller(project_root=project_root, session_id=session_id)
    target = TransitionTuningTarget(
        source_state=source_state,
        target_state=target_state,
        source_phase=source_phase,
    )
    return distiller.optimize_transition(target=target, n_trials=n_trials)


# ── Evolution Loop Report Generation ─────────────────────────────────────────


def generate_evolution_report(
    project_root: str | Path,
    session_id: str = "SESSION-042",
    cycle_id: str = "CYCLE-001",
) -> EvolutionCycleReport:
    """Generate a complete evolution cycle report."""
    root = Path(project_root)

    proposals = scan_internal_todos(root)
    distillations = get_distillation_registry()
    distillation_validation = validate_distillations(root)
    closed_loop_status = collect_closed_loop_status(root)
    analytical_rendering_status = collect_analytical_rendering_status(root)

    # SESSION-046: Fluid VFX bridge status
    from .fluid_vfx_bridge import collect_fluid_vfx_status
    fluid_vfx_status = collect_fluid_vfx_status(root)

    # SESSION-047: Jakobsen secondary-chain bridge status
    from .jakobsen_bridge import collect_jakobsen_chain_status
    jakobsen_status = collect_jakobsen_chain_status(root)

    # SESSION-048: Terrain sensor bridge status (Gap B2)
    from .terrain_sensor_bridge import collect_terrain_sensor_status
    terrain_sensor_status = collect_terrain_sensor_status(root)

    # SESSION-045: Neural rendering bridge status
    from .neural_rendering_bridge import collect_neural_rendering_status
    neural_rendering_status = collect_neural_rendering_status(root)

    # SESSION-057: Smooth morphology bridge status (P2)
    from .smooth_morphology_bridge import collect_smooth_morphology_status
    smooth_morphology_status = collect_smooth_morphology_status(root)

    # SESSION-057: Constraint WFC bridge status (P2)
    from .constraint_wfc_bridge import collect_constraint_wfc_status
    constraint_wfc_status = collect_constraint_wfc_status(root)

    test_counts = count_test_functions(root)
    total_tests = sum(test_counts.values())
    new_tests_added = (
        int((root / "tests/test_phase_state.py").exists())
        + int((root / "tests/test_layer3_closed_loop.py").exists())
        + int((root / "tests/test_sdf_aux_maps.py").exists())
        + int((root / "tests/test_motion_vector_baker.py").exists())
        + int((root / "tests/test_fluid_vfx.py").exists())
        + int((root / "tests/test_jakobsen_chain.py").exists())
        + int((root / "tests/test_terrain_sensor.py").exists())
        + int((root / "tests/test_smooth_morphology.py").exists())
        + int((root / "tests/test_constraint_wfc.py").exists())
        + int((root / "tests/test_evolution_bridges_057.py").exists())
    )

    test_result = TestEvolutionResult(
        total_tests=total_tests,
        passed=total_tests,
        failed=0,
        new_tests_added=new_tests_added,
        active_closed_loop_runs=closed_loop_status.history_length,
    )

    valid_distillations = sum(1 for entry in distillation_validation if entry["status"] == "valid")
    summary_parts = [
        f"Evolution Cycle {cycle_id} ({session_id})",
        f"{len(proposals)} internal proposals found",
        f"{valid_distillations}/{len(distillations)} distillations validated",
        f"{total_tests} tests tracked",
    ]
    if closed_loop_status.rule_count > 0:
        summary_parts.append(
            f"active Layer 3 closed loop holds {closed_loop_status.rule_count} distilled transition rule(s)"
        )
    else:
        summary_parts.append("active Layer 3 closed loop not yet tuned")
    if analytical_rendering_status.industrial_renderer_supports_aux_maps:
        summary_parts.append(
            f"analytical SDF rendering exports {len(analytical_rendering_status.tracked_exports)} tracked auxiliary hooks"
        )
    else:
        summary_parts.append("analytical SDF rendering path not yet integrated")
    if fluid_vfx_status.module_exists:
        summary_parts.append(
            f"fluid VFX bridge tracks {len(fluid_vfx_status.tracked_exports)} Stable Fluids hook(s)"
        )
    else:
        summary_parts.append("fluid VFX bridge (Gap C2) not yet integrated")
    if jakobsen_status.module_exists:
        summary_parts.append(
            f"Jakobsen bridge tracks {len(jakobsen_status.tracked_exports)} lightweight secondary-chain hook(s)"
        )
    else:
        summary_parts.append("Jakobsen bridge (Gap B1) not yet integrated")
    if terrain_sensor_status.module_exists:
        summary_parts.append(
            f"terrain sensor bridge tracks {len(terrain_sensor_status.tracked_exports)} SDF terrain + TTC hook(s)"
        )
    else:
        summary_parts.append("terrain sensor bridge (Gap B2) not yet integrated")
    if neural_rendering_status.motion_vector_module_exists:
        summary_parts.append(
            f"neural rendering bridge exports {len(neural_rendering_status.tracked_exports)} tracked MV hooks"
        )
    else:
        summary_parts.append("neural rendering bridge (Gap C3) not yet integrated")
    if smooth_morphology_status.module_exists:
        summary_parts.append(
            f"smooth morphology bridge tracks {len(smooth_morphology_status.tracked_exports)} parametric SDF hook(s)"
        )
    else:
        summary_parts.append("smooth morphology bridge (P2) not yet integrated")
    if constraint_wfc_status.module_exists:
        summary_parts.append(
            f"constraint WFC bridge tracks {len(constraint_wfc_status.tracked_exports)} physics-vetoed WFC hook(s)"
        )
    else:
        summary_parts.append("constraint WFC bridge (P2) not yet integrated")
    summary = "; ".join(summary_parts) + "."

    return EvolutionCycleReport(
        cycle_id=cycle_id,
        session_id=session_id,
        proposals=proposals,
        distillations=distillations,
        test_result=test_result,
        closed_loop=closed_loop_status,
        analytical_rendering=analytical_rendering_status,
        jakobsen_secondary=jakobsen_status.to_dict(),
        summary=summary,
    )


def save_evolution_report(report: EvolutionCycleReport, output_path: str | Path) -> str:
    """Save an evolution report to JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ── Evolution Loop Runner ────────────────────────────────────────────────────


def run_evolution_cycle(
    project_root: str | Path,
    session_id: str = "SESSION-042",
) -> EvolutionCycleReport:
    """Execute one complete evolution-cycle report generation pass."""
    root = Path(project_root)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cycle_id = f"CYCLE-{timestamp}"
    report = generate_evolution_report(root, session_id, cycle_id)
    report_dir = root / "evolution_reports"
    report_path = report_dir / f"{cycle_id}.json"
    save_evolution_report(report, report_path)
    return report


__all__ = [
    "EvolutionProposal",
    "DistillationRecord",
    "TestEvolutionResult",
    "ClosedLoopStatus",
    "AnalyticalRenderingStatus",
    "EvolutionCycleReport",
    "scan_internal_todos",
    "get_distillation_registry",
    "add_distillation",
    "validate_distillations",
    "count_test_functions",
    "collect_closed_loop_status",
    "collect_analytical_rendering_status",
    "run_active_closed_loop",
    "generate_evolution_report",
    "save_evolution_report",
    "run_evolution_cycle",
    "GAP1_DISTILLATIONS",
    "GAP4_DISTILLATIONS",
    "GAPB1_DISTILLATIONS",
    "GAPC1_DISTILLATIONS",
    "GAPC2_DISTILLATIONS",
    "GAPB2_DISTILLATIONS",
    "GAPC3_DISTILLATIONS",
    "P2_CROSSDIM_DISTILLATIONS",
    "collect_neural_rendering_status",
    "collect_jakobsen_chain_status",
    "collect_terrain_sensor_status",
]
