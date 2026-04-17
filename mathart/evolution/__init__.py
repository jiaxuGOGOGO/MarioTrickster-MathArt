"""Self-Evolution Engine — the orchestrator of the three-loop system.

This module coordinates the three layers of the self-evolution architecture:

1. **Inner Loop** (``InnerLoopRunner``):
   Generate → Evaluate → Optimize → Repeat until quality threshold met.

2. **Outer Loop** (``OuterLoopDistiller``):
   Parse PDF/Markdown → Extract rules → Update knowledge/ → Update code params.

3. **Math Engine** (``MathModelRegistry``):
   Registry of all mathematical models with versioning and capability tracking.

Usage::

    from mathart.evolution import SelfEvolutionEngine
    engine = SelfEvolutionEngine()
    # Run inner loop on a generation function
    result = engine.inner_loop.run(generator_fn, max_iterations=20)
    # Distill a new document
    engine.outer_loop.distill_file("path/to/new_book.pdf")
    # Check evolution status
    engine.status()
"""
from .engine import SelfEvolutionEngine
from .inner_loop import InnerLoopRunner, InnerLoopResult
from .outer_loop import OuterLoopDistiller, DistillResult
from .math_registry import MathModelRegistry, ModelEntry
from .cppn import CPPNGenome, CPPNEvolver, CPPNArchiveCell
# SESSION-030: Layer 3 Physics Evolution
from .evolution_layer3 import (
    PhysicsEvolutionLayer, PhysicsEvolutionRecord, PhysicsEvolutionState,
    PhysicsTestBattery, PhysicsTestReport, PhysicsTestResult,
    PhysicsDiagnosisEngine, DiagnosisAction,
    PhysicsKnowledgeDistiller,
)
# SESSION-040: Pipeline Contract Evolution Bridge
from .evolution_contract_bridge import (
    ContractEvolutionBridge,
    ContractEvolutionMetrics,
    ContractEvolutionState,
)
# SESSION-041: Visual Regression Evolution Bridge
from .visual_regression_bridge import (
    VisualRegressionEvolutionBridge,
    VisualRegressionMetrics,
    VisualRegressionState,
)
# SESSION-043: Active Layer 3 Closed Loop
from .layer3_closed_loop import (
    TransitionTuningTarget,
    TransitionLossWeights,
    ClosedLoopRuleRecord,
    ClosedLoopOptimizationResult,
    Layer3ClosedLoopState,
    TransitionRuleStore,
    Layer3ClosedLoopDistiller,
    load_distilled_transition_params,
)
# SESSION-046: Stable Fluids VFX Bridge (Gap C2)
from .fluid_vfx_bridge import (
    FluidVFXMetrics,
    FluidVFXState,
    FluidVFXStatus,
    collect_fluid_vfx_status,
    FluidVFXEvolutionBridge,
)
# SESSION-047: Jakobsen Secondary Chain Bridge (Gap B1)
from .jakobsen_bridge import (
    JakobsenChainMetrics,
    JakobsenChainState,
    JakobsenChainStatus,
    collect_jakobsen_chain_status,
    JakobsenEvolutionBridge,
)
# SESSION-048: Terrain Sensor Bridge (Gap B2)
from .terrain_sensor_bridge import (
    TerrainSensorMetrics,
    TerrainSensorState,
    TerrainSensorStatus,
    collect_terrain_sensor_status,
    TerrainSensorEvolutionBridge,
)
# SESSION-049: Gait Blend Bridge (Gap B3)
from .gait_blend_bridge import (
    GaitBlendMetrics,
    GaitBlendState,
    GaitBlendStatus,
    collect_gait_blend_status,
    GaitBlendEvolutionBridge,
)
# SESSION-051: State-machine graph coverage bridge (Gap D1)
from .state_machine_coverage_bridge import (
    StateMachineCoverageMetrics,
    StateMachineCoverageState,
    StateMachineCoverageBridge,
)
# SESSION-054: Industrial Skin Bridge
from .industrial_skin_bridge import (
    IndustrialSkinMetrics,
    IndustrialSkinState,
    IndustrialSkinBridge,
)
# SESSION-055: Asset Factory + Evolution Orchestrator
from .asset_factory_bridge import (
    AssetSpec,
    AssetQualityReport,
    FactoryProductionReport,
    FactoryState,
    AssetFactory,
)
from .evolution_orchestrator import (
    EvolutionCycleReport,
    EvolutionState,
    EvolutionOrchestrator,
)
# SESSION-056: Breakwall Evolution Bridge (Phase 1 — Neural Rendering + Engine Import)
from .breakwall_evolution_bridge import (
    BreakwallMetrics,
    BreakwallState,
    BreakwallStatus,
    collect_breakwall_status,
    BreakwallEvolutionBridge,
)
# SESSION-057: Smooth Morphology Bridge (P2 — Cross-Dimensional Spawning)
from .smooth_morphology_bridge import (
    SmoothMorphologyMetrics,
    SmoothMorphologyState,
    SmoothMorphologyStatus,
    collect_smooth_morphology_status,
    SmoothMorphologyEvolutionBridge,
)
# SESSION-057: Constraint WFC Bridge (P2 — Cross-Dimensional Spawning)
from .constraint_wfc_bridge import (
    ConstraintWFCMetrics,
    ConstraintWFCState,
    ConstraintWFCStatus,
    collect_constraint_wfc_status,
    ConstraintWFCEvolutionBridge,
)
# SESSION-058: Phase 3 Physics Bridge (Taichi XPBD + SDF CCD + NSM Gait)
from .phase3_physics_bridge import (
    Phase3PhysicsMetrics,
    Phase3PhysicsState,
    Phase3PhysicsStatus,
    collect_phase3_physics_status,
    Phase3PhysicsEvolutionBridge,
)
# SESSION-059: Unity URP 2D native bridge (Secondary Textures + XPBD VAT)
from .unity_urp_2d_bridge import (
    UnityURP2DMetrics,
    UnityURP2DState,
    UnityURP2DStatus,
    collect_unity_urp_2d_status,
    UnityURP2DEvolutionBridge,
)

__all__ = [
    "SelfEvolutionEngine",
    "InnerLoopRunner", "InnerLoopResult",
    "OuterLoopDistiller", "DistillResult",
    "MathModelRegistry", "ModelEntry",
    "CPPNGenome", "CPPNEvolver", "CPPNArchiveCell",
    # SESSION-030: Layer 3 Physics Evolution
    "PhysicsEvolutionLayer", "PhysicsEvolutionRecord", "PhysicsEvolutionState",
    "PhysicsTestBattery", "PhysicsTestReport", "PhysicsTestResult",
    "PhysicsDiagnosisEngine", "DiagnosisAction",
    "PhysicsKnowledgeDistiller",
    # SESSION-040: Pipeline Contract Evolution Bridge
    "ContractEvolutionBridge",
    "ContractEvolutionMetrics",
    "ContractEvolutionState",
    # SESSION-041: Visual Regression Evolution Bridge
    "VisualRegressionEvolutionBridge",
    "VisualRegressionMetrics",
    "VisualRegressionState",
    # SESSION-043: Active Layer 3 Closed Loop
    "TransitionTuningTarget",
    "TransitionLossWeights",
    "ClosedLoopRuleRecord",
    "ClosedLoopOptimizationResult",
    "Layer3ClosedLoopState",
    "TransitionRuleStore",
    "Layer3ClosedLoopDistiller",
    "load_distilled_transition_params",
    # SESSION-046: Stable Fluids VFX Bridge (Gap C2)
    "FluidVFXMetrics",
    "FluidVFXState",
    "FluidVFXStatus",
    "collect_fluid_vfx_status",
    "FluidVFXEvolutionBridge",
    # SESSION-047: Jakobsen Secondary Chain Bridge (Gap B1)
    "JakobsenChainMetrics",
    "JakobsenChainState",
    "JakobsenChainStatus",
    "collect_jakobsen_chain_status",
    "JakobsenEvolutionBridge",
    # SESSION-048: Terrain Sensor Bridge (Gap B2)
    "TerrainSensorMetrics",
    "TerrainSensorState",
    "TerrainSensorStatus",
    "collect_terrain_sensor_status",
    "TerrainSensorEvolutionBridge",
    # SESSION-049: Gait Blend Bridge (Gap B3)
    "GaitBlendMetrics",
    "GaitBlendState",
    "GaitBlendStatus",
    "collect_gait_blend_status",
    "GaitBlendEvolutionBridge",
    # SESSION-051: State-machine graph coverage bridge (Gap D1)
    "StateMachineCoverageMetrics",
    "StateMachineCoverageState",
    "StateMachineCoverageBridge",
    # SESSION-054: Industrial Skin Bridge
    "IndustrialSkinMetrics",
    "IndustrialSkinState",
    "IndustrialSkinBridge",
    # SESSION-055: Asset Factory + Evolution Orchestrator
    "AssetSpec",
    "AssetQualityReport",
    "FactoryProductionReport",
    "FactoryState",
    "AssetFactory",
    "EvolutionCycleReport",
    "EvolutionState",
    "EvolutionOrchestrator",
    # SESSION-056: Breakwall Evolution Bridge
    "BreakwallMetrics",
    "BreakwallState",
    "BreakwallStatus",
    "collect_breakwall_status",
    "BreakwallEvolutionBridge",
    # SESSION-057: Smooth Morphology Bridge (P2)
    "SmoothMorphologyMetrics",
    "SmoothMorphologyState",
    "SmoothMorphologyStatus",
    "collect_smooth_morphology_status",
    "SmoothMorphologyEvolutionBridge",
    # SESSION-057: Constraint WFC Bridge (P2)
    "ConstraintWFCMetrics",
    "ConstraintWFCState",
    "ConstraintWFCStatus",
    "collect_constraint_wfc_status",
    "ConstraintWFCEvolutionBridge",
    # SESSION-058: Phase 3 Physics Bridge (P3)
    "Phase3PhysicsMetrics",
    "Phase3PhysicsState",
    "Phase3PhysicsStatus",
    "collect_phase3_physics_status",
    "Phase3PhysicsEvolutionBridge",
    # SESSION-059: Unity URP 2D native bridge
    "UnityURP2DMetrics",
    "UnityURP2DState",
    "UnityURP2DStatus",
    "collect_unity_urp_2d_status",
    "UnityURP2DEvolutionBridge",
]
