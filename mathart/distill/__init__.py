"""Knowledge distillation pipeline for MarioTrickster-MathArt.

This module implements a three-layer knowledge distillation system:

1. **Perception Layer** — Extracts structured rules from PDF documents
   and unstructured text sources (art books, animation manuals, game design docs).

2. **Compilation Layer** — Transforms extracted rules into mathematical
   constraints and parameter spaces that the generation modules can consume.

3. **Optimization Layer** — Searches the parameter space to find optimal
   configurations, enabling the system to self-iterate and improve over time.

The pipeline reads from ``knowledge/*.md`` files and PDF sources, and outputs
parameter configurations that drive the OKLAB, SDF, Animation, and Level modules.
"""

from .parser import KnowledgeParser, KnowledgeRule, RuleType, TargetModule
from .compiler import RuleCompiler, ParameterSpace, Constraint
from .optimizer import EvolutionaryOptimizer, FitnessResult

__all__ = [
    "KnowledgeParser",
    "KnowledgeRule",
    "RuleType",
    "TargetModule",
    "RuleCompiler",
    "ParameterSpace",
    "Constraint",
    "EvolutionaryOptimizer",
    "FitnessResult",
]
