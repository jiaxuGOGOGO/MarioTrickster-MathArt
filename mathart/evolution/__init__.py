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
]
