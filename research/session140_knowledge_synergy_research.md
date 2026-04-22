# SESSION-140 Research: Knowledge Synergy Bridge — External References

## 1. Knowledge-Grounded Generation (基于知识先验的生成)

**Core Principle**: In procedural content generation, semantic translation must not rely on isolated static rules. The system must dynamically retrieve runtime knowledge trees (distilled knowledge) and use externally distilled prior mathematical laws to strictly constrain generation and mutation parameter boundaries.

### Key References

- **Knowledge-Augmented Generation (KAG)** framework (2025): Upgrades RAG by integrating structured knowledge graphs with LLM reasoning, reducing hallucinations through grounded constraint enforcement. The key insight is that knowledge should serve as hard constraints on generation, not just soft context.

- **Aligning Constraint Generation with Design Intent in Parametric CAD** (ICCV 2025, Casey et al.): Adapts alignment techniques from reasoning LLMs to generate engineering sketch constraints that match designer intent. Directly relevant to our semantic-to-parametric translation bridge — the system must align generated parameters with both user intent AND knowledge constraints.

- **Knowledge-Informed Optimization for Performance-Based Generative Design** (Applied Energy, 2024): Integrates knowledge graphs into parametric generative design. The KG provides hard physical constraints that bound the search space, preventing the optimizer from exploring physically impossible regions.

- **Constrained Evolutionary Optimization Based on Dynamic Knowledge Transfer** (Expert Systems, 2024): Proposes a constrained optimization framework where knowledge transfer between problems guides the evolutionary search. Directly applicable to our RuntimeDistillationBus → mutation engine pipeline.

## 2. Constraint Reconciliation (约束调解与仲裁)

**Core Principle**: Resolve the conflict between "human sensibility" and "scientific rationality". When human intent conflicts with distilled academic truth, the system must have arbitration capability — exposing physics conflicts through the REPL gateway.

### Key References

- **Requirements for Aligned, Dynamic Resolution of Conflicts in Open Contexts** (arXiv 2511.10952, 2025): Clarifies the kinds of knowledge needed to navigate constraint conflicts in open contexts. Proposes a taxonomy of conflict types: fatal (must block), soft (warn and offer override), and informational (log only).

- **Boundary-Conditioned Inpainting for Constraint-Consistent Generation** (ACM SIGGRAPH 2025): Demonstrates a pipeline that generates constraint-satisfying content given surrounding boundary context. When conflicting edge constraints are detected, the system resolves them through a priority-based arbitration mechanism.

- **Balancing Survival of Feasible and Infeasible Solutions in Constrained Evolutionary Optimization** (Deb et al., MSU): The seminal work on allowing controlled infeasibility in evolutionary search. Key insight: sometimes the optimal solution lies near the constraint boundary, so the system should allow users to "push" into infeasible territory with explicit acknowledgment.

- **Human-AI Interface Layers for Creative Pursuits** (ProQuest, 2025): Proposes a Steering Interface Layer that partitions and constrains generated outputs while preserving human creative agency. Directly relevant to our conflict arbitrator design — the system must warn but never dictate.

## 3. Data Lineage & Provenance (数据血统追踪)

**Core Principle**: Industrial-grade asset pipelines must guarantee traceability. If the final art asset applied an algorithm distilled from a top conference paper, its ArtifactManifest must carry that knowledge rule's citation ID.

### Key References

- **Provenance Tracking in Large-Scale ML Pipelines** (ACM Computing Surveys, 2025, Padovani et al.): Comprehensive survey of provenance tracking approaches. Key recommendation: provenance metadata should be lightweight (IDs + summaries), not full data copies. Track only "activated" rules, not the entire knowledge base.

- **C2PA (Coalition for Content Provenance and Authenticity)** standard: Industry standard for embedding provenance metadata in digital assets. Our ArtifactManifest `applied_knowledge_rules` field follows this pattern — each asset carries its own provenance chain.

- **Atlas: A Framework for ML Lifecycle Provenance & Transparency** (arXiv 2502.19567, 2025): Combines trusted hardware and transparency logs to enhance metadata integrity. Key design: each pipeline stage appends its provenance record to a chain, creating an immutable audit trail.

- **ProVe: Pipeline for Automated Provenance Verification** (Semantic Web Journal, 2024): Automatically verifies whether a knowledge graph triple is supported by text extracted from its documented provenance. Applicable to our system: we can verify that applied_knowledge_rules actually trace back to real distilled rules.

## 4. Knowledge-Constrained Mutation & Manifold Clamping

### Key References

- **Knowledge Constrained Evolutionary Algorithms** (IJAISC, 2014): Examines the role of domain knowledge in guiding evolution. Hypothesis confirmed: domain knowledge significantly improves convergence by constraining the mutation operator to feasible regions.

- **Constraint-Aware Mutation Operators** (ResearchGate, 2022): Proposes mutation operators that are aware of constraint boundaries. When a mutation would violate a constraint, the operator clamps the value to the nearest feasible point — exactly our `clamp_by_knowledge()` design.

- **Manifold-Assisted Coevolutionary Algorithm for Constrained Multi-Objective Optimization** (Swarm and Evolutionary Computation, 2024): Uses manifold learning to identify the feasible region boundary and guide mutations to stay within it. The "manifold clamp" concept directly maps to our knowledge-projected mutation design.

- **Safe Reinforcement Learning on the Constraint Manifold** (arXiv 2404.09080, 2024): Shows how to impose complex safety constraints on learning-based systems in a principled manner. The constraint manifold projection ensures that all actions (mutations in our case) remain within the safe set.

## 5. Design Decisions for SESSION-140

Based on the above research, the following design decisions are adopted:

1. **Knowledge Bus Integration**: The `RuntimeDistillationBus` is injected into `DirectorIntentParser` via dependency injection (not hard-coded). When translating vibes, the parser first queries the bus for relevant constraints before applying semantic mappings.

2. **Graceful Degradation**: If the knowledge bus returns no matching rules for a vibe keyword, the system falls back to the built-in `SEMANTIC_VIBE_MAP` heuristics and logs a warning. No exceptions are thrown.

3. **Three-Tier Conflict Classification**: Fatal (math errors like div-by-zero → hard block), Physical (knowledge boundary violations → warn + offer override), Informational (style suggestions → log only).

4. **Lightweight Provenance**: `applied_knowledge_rules` stores only rule IDs, brief descriptions, and the specific parameter they constrained. No full-text knowledge dumps.

5. **Clamp-Not-Reject**: The mutation engine clamps out-of-bounds values to the nearest feasible boundary rather than rejecting the entire offspring. This preserves genetic diversity while enforcing safety.
