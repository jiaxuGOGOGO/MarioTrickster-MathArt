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
]
