# SESSION-139 Research: Director Studio Architecture

## 1. Semantic-to-Parametric Translation

**Core Concept**: A translation bridge that maps natural language artistic intent (e.g., "lively", "exaggerated") into strongly-typed physical parameters and animation coefficients.

**Key References**:
- **Proc3D (2026)**: Procedural 3D generation with parametric editing — demonstrates how high-level semantic descriptions can be decomposed into procedural graph parameters.
- **MapStory (2025)**: LLM-powered text-driven animation authoring — shows how natural language can be parsed into editable animation sequences with parametric controls.
- **Make-an-Animation (ICCV 2023, Azadi et al.)**: Large-scale text-conditional 3D motion generation — establishes the pattern of text→motion parameter mapping.
- **NLP-driven Text-to-Animation Framework (IEEE 2024)**: Demonstrates the full pipeline from NLP parsing to animation parameter extraction.

**Design Principles Applied**:
- Fuzzy semantic descriptors ("vibe") must be mapped to numeric ranges via a configurable lookup table (semantic_map.yaml)
- Visual reference images can be reverse-engineered for feature extraction (color palette, proportions, motion energy)
- The output must always be a strongly-typed `CreatorIntentSpec` dataclass, never raw strings

## 2. Interactive Animatic REPL (Proxy Preview Loop)

**Core Concept**: Before committing to expensive AI rendering, generate low-cost wireframe/proxy previews in milliseconds, with a terminal REPL supporting multi-round feedback.

**Key References**:
- **DreamCrafter (ACM 2025)**: Immersive editing of 3D radiance fields through proxy previews — users felt more in control with iterative proxy-based interaction.
- **Interactive Genetic Algorithm (IGA) with AHP-CRITIC proxy model (Electronics 2024)**: Bidirectional complementarity between human subjective evaluation and algorithmic proxy scoring.
- **Pixar's Animatic Pipeline**: Industry standard of using low-fidelity "animatics" (storyboard-level previews) before committing to full rendering.

**Design Principles Applied**:
- Proxy generation must be sub-second (matplotlib/PIL wireframe, no GPU)
- REPL supports: [1] Approve → render, [2] More exaggerated, [3] More conservative, [4] Abort
- Feedback modifiers adjust parameters by ±15-30% per round
- NO ComfyUI/GPU invocation until explicit [1] approval

## 3. Asset Lineage & Prefab Inheritance (Blueprint System)

**Key References**:
- **Pixar USD Variant Sets & Composition Arcs**: USD's LIVRPS (Local, Inherits, VariantSets, References, Payloads, Specializes) ordering provides a robust model for asset inheritance. VariantSets allow switching between pre-authored variations while maintaining shared base properties.
- **Walt Disney Animation Studios USD Lessons (SIGGRAPH 2023)**: Asset-root inherits arcs for sharing properties across variant families.
- **Unreal Engine Blueprint Inheritance**: IS-A relationship hierarchy where child Blueprints inherit all parent functionality but can override specific properties.
- **Game Serialization Patterns**: Binary/YAML serialization with versioning, UUID-based asset references, and schema validation.

**Design Principles Applied**:
- Blueprint = YAML file containing complete Genotype (physics params, proportions, color palette, animation coefficients)
- Pure serialization: no Base64 blobs, no absolute paths, no runtime state
- Backward compatibility: missing keys get sensible defaults via `dict.get(key, default)`
- Cross-platform ready: structure maps cleanly to Unity ScriptableObject or UE DataAsset

## 4. Controlled Variational Evolution (Freeze Mask)

**Key References**:
- **Genetic Algorithm with Adaptive Freezing (GAAF, 2026)**: Proposes freezing specific gene subsets during evolution to maintain stability while optimizing others.
- **Gene Masking (PMC 2016)**: Binary-encoded mask templates representing which chromosomes are active/frozen during genetic operations.
- **Parameter Control in Evolutionary Algorithms (Eiben et al.)**: Comprehensive survey on how to handle parameter constraints, including fixed vs. adaptive vs. self-adaptive control.
- **Constraint-Handling Techniques for EAs (ACM GECCO 2022)**: Modern approaches to enforcing hard constraints during evolutionary search.

**Design Principles Applied**:
- `freeze_locks` list maps to a binary mask over the parameter space
- Frozen parameters are NEVER modified by crossover or mutation operators
- Variance of frozen parameters across all offspring must be exactly 0.0
- Unfrozen parameters undergo normal evolutionary variation within their constraint bounds
- The mask is sacred: no optimizer, no "smoothness pass", no post-processing may violate it
