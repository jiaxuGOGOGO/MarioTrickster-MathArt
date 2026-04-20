# SESSION-095 Genotype Boundary Research Notes

## Repository Context Sync

The repository enters this session immediately after **SESSION-094**, which closed the WFC lock-immunity propagation defect. The new target shifts upward into the **Morphology & Evolution** layer, specifically the boundary-contract weakness in `mathart/animation/genotype.py` identified by the repository audit.

## Immediate In-Repo Findings

`AUDIT_REPORT.md` section 2.3 explicitly states that the genotype module contains **17 clamp/clip constraint sites** while the current mutation tests mostly prove only that mutation happens, not that all parameters remain legal under extreme mutation pressure.

`mathart/animation/genotype.py` currently enforces validity in a scattered way. Continuous constraints are split across decode-time clipping, mutation-time clipping, and special-case palette postprocessing. This means legality exists, but the enforcement surface is fragmented rather than centralized.

`tests/test_genotype.py` is partially deterministic already through `np.random.default_rng(...)`, but its mutation assertions are still weak. It does not yet perform exhaustive value-level checking across every constrained continuous gene under nuclear-strength mutation inputs.

## External Research Focus for This Session

1. **Karl Sims (1994)**: directed-graph genotype parameters and why unconstrained morphology/controller perturbation risks invalid physical bodies.
2. **Gymnasium Box spaces**: modern continuous-space contract model, especially interval projection / clipping semantics.
3. **CMA-ES boundary handling**: post-perturbation coordinate projection into legal hyper-rectangles.

## Working Engineering Hypothesis

The correct landing is likely a **single post-mutation hard-clipping pipeline** that executes after *all* continuous perturbations, retrieves per-gene bounds from a declared contract, and silently projects every out-of-range coordinate back into its legal interval. Tests must then prove that even absurd mutation strengths still terminate with every constrained value inside bounds.

## External Research Notes: Gymnasium Box

The official Gymnasium documentation defines `Box` as the **Cartesian product of n closed intervals**, with each coordinate bounded by an explicit `low` and `high`. The important engineering interpretation is that legality is a **per-dimension contract**, not an emergent property. A value is valid because every coordinate lies inside its declared interval. This strongly supports refactoring genotype mutation into a **coordinate-wise contract table** rather than scattered one-off clamps.

The same documentation also makes `seed` a first-class part of the space API, reinforcing that reproducibility is not incidental. For our tests, the equivalent discipline is to standardize on `np.random.default_rng(seed=42)` for mutation-path determinism.

## External Research Notes: CMA-ES Boundary Handling

The Hansen CMA-ES tutorial visibly dedicates a specific subsection to **Boundaries and Constraints**. Even before extracting the full text, this is enough to confirm the architectural norm: boundary handling is a first-class post-perturbation concern in industrial evolution strategies, not an optional decode-time afterthought.

Working interpretation for this repository: after all perturbations are accumulated, every continuous gene should be repaired by a **single coordinate projection pass** back into the legal hyper-rectangle, while the evolutionary process itself continues running without throwing errors for ordinary boundary excursions.

## External Research Notes: Gym / ClipAction Boundary Projection

The OpenAI Gym `ClipAction` wrapper states its purpose as clipping continuous actions within the valid `Box` bound, and the implementation directly returns:

> `np.clip(action, self.action_space.low, self.action_space.high)`

This matters because it demonstrates the exact industrial pattern the user requested: mutation or action generation may explore freely, but the system applies a **uniform final projection** right before downstream consumption. For this repository, the direct analogue is: after all genotype perturbations complete, a single contract-driven pass should project every continuous gene into its declared `[min, max]` interval.

## External Research Notes: Karl Sims (1994)

The extracted paper text confirms that Sims' directed-graph genotype stores physical dimensions, joint types, joint limits, recursive limits, and attachment transforms inside nodes and connections. In the most relevant passage, Sims writes that a joint type defines the degrees of freedom and that **joint-limits determine the point beyond which restoring spring forces will be exerted**. This reinforces the repository-facing rule that morphology/control parameters are not free-floating decorative numbers: they are **physical contract inputs** whose legal range directly conditions whether downstream simulation remains well-posed.

Combined with the genotype audit, the practical rule for this repository is now clear: every continuous morphology gene must be treated as a bounded coordinate in a legal hyper-rectangle, and the mutation pipeline must project back into that region before decode/physics ever sees the result.
