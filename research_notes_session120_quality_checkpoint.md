# SESSION-120 Research Notes — P1-NEW-8 Mid-Generation Checkpoint & PDG Branch Pruning

## Research Pillars (ALL READ BEFORE CODE LANDING)

### 1. Houdini PDG/TOPs Conditional Execution & Failure Propagation

**Source:** [SideFX Houdini 21.0 pdg.WorkItem documentation](https://www.sidefx.com/docs/houdini/tops/pdg/WorkItem.html)

Canonical language cited verbatim from the SideFX API reference:

- **`pdg.workItemState` enum distinguishes success, cancel, and fail**. The class
  reference states: "If the work item in a unsuccessful cooked state, either
  `pdg.workItemState.CookedCancel` or `pdg.workItemState.CookedFail`, then this
  is set to True." This is the industrial signal that a cancelled work item is
  **not** an error — it is a first-class runtime state.
- **Upstream failure automatically taints downstream dependency state**. The
  `dependencyState` property is described as: "The list of failed upstream
  dependencies, when `pdg.WorkItem.dependencyState` is set to something other
  than `pdg.workItemState.CookedSuccess`". Downstream work items therefore
  *observe* upstream state and can self-skip without executing their operation.
- **What's New in PDG (H19)** also documents that when Farm Schedulers have the
  *Block on Failed Work items* parameter turned on, "their status icons will
  now indicate when their cooks are blocked and their nodes [are skipped]". This
  confirms that fail-fast + skip-downstream is the intended industrial pattern.

**Design implication for our PDG v2 scheduler:**

1. Introduce an explicit `WorkItemState` enum with `COOKED_SUCCESS`,
   `COOKED_CANCEL`, `COOKED_FAIL`, `SKIPPED`, `PENDING`.
2. Introduce a typed `EarlyRejectionError` exception carrying a machine-readable
   `prune_reason`; when it propagates out of a node operation, the runtime must
   translate it into `COOKED_CANCEL` with the reason attached to the trace, NOT
   a generic `COOKED_FAIL`.
3. Once a node is cancelled, **every transitively downstream node is marked
   `SKIPPED` and its operation is never invoked**. This is the zero-cost
   semantic we need to release the 16-thread pool instantly.
4. Resource acquisition (GPU semaphore) must always release even when the
   operation raises, via a `finally:` block. We already have one guard — we
   must extend it to cover the full cancellation path, not only the happy path.

### 2. Multi-Fidelity Optimization & Early Pruning

**Source:** Multi-Fidelity Methods for Optimization — survey (arXiv 2402.09638)
and adjacent literature (Kolencherry 2011; Janet MIT 2020).

The key principle, synthesised:

> The full (high-fidelity) evaluator of a candidate solution is orders of
> magnitude more expensive than a low-fidelity surrogate. A multi-fidelity
> optimiser therefore runs every candidate through the cheap surrogate first
> and only promotes survivors to the expensive evaluator. Candidates pruned at
> the low-fidelity stage are discarded with **zero cost** at the high-fidelity
> stage.

Applied to our character-generation evolutionary loop:

- **Low-fidelity surrogate** = pure-numeric checks on `CharacterGenotype`:
  bounded proportion modifiers, non-inverted skeleton anatomy, finite-valued
  tensors. Cost measured in **microseconds** (no I/O, no rendering).
- **High-fidelity evaluator** = full rigging + DQS skinning + physics +
  multi-channel PBR rendering + visual fitness SSIM. Cost measured in
  **seconds** per candidate.
- **Our job** is to insert the surrogate as a PDG checkpoint node whose
  rejection is surgically lifted into the scheduler as `EarlyRejectionError`.

### 3. Data-Oriented Heuristics (Microsecond-Level Guards)

Anti-patterns to AVOID (explicit red-line from the user brief):

- No file I/O, no `Image.open`, no PNG encoding in the checkpoint.
- No tree-walking or graph traversal of full rigging structures.
- No instantiation of heavy physics/renderer objects.

Approved patterns:

- Operate only on `np.ndarray` and Python `dataclass` scalars.
- Vectorised comparisons: `np.any(np.isnan(tensor))`, `np.ptp(bounds)`,
  simple arithmetic ratios.
- Return a bounded diagnostic string; do not format images.

### 4. Anti-Silent-Swallow & Anti-VRAM-Leak Hygiene (Self-Audit Red Lines)

From the user brief we carry forward three non-negotiable guards into code
review and tests:

1. **Only** `EarlyRejectionError` triggers the `COOKED_CANCEL` path. Any other
   exception (`TypeError`, `IndexError`, `KeyError`, bare `Exception`) MUST
   bubble out as `PDGError` so bugs stay visible. Never `except Exception`.
2. Even when the node body raises, the GPU semaphore's inflight counter MUST
   decrement and `release()` MUST run. Use a `try/finally` block at the
   resource-acquisition layer.
3. Every SKIPPED downstream node MUST be logged in the execution trace with
   `prune_reason` so the evolutionary controller can convert it into a Fitness
   penalty score, and no downstream effect (cache write, GPU acquire) may
   occur.

## Canonical API Decisions Locked for Implementation

| Concept | Name | Module |
|---|---|---|
| Early-rejection exception | `EarlyRejectionError(prune_reason: str, source_node: str, diagnostics: dict)` | `mathart.level.pdg` |
| Runtime work-item state | `WorkItemState` enum: `COOKED_SUCCESS`, `COOKED_CANCEL`, `SKIPPED`, `COOKED_FAIL` | `mathart.level.pdg` |
| Independent gate interface | `MidGenerationCheckpoint` protocol with `.evaluate(context, deps) -> CheckpointVerdict` | `mathart.quality.mid_generation_checkpoint` |
| Skeleton proportion gate | `SkeletonProportionGate(bounds=STYLE_PARAMETER_BOUNDS)` | `mathart.quality.mid_generation_checkpoint` |
| Numerical toxin gate | `NumericalToxinGate()` | `mathart.quality.mid_generation_checkpoint` |
| PDG node adapter | `QualityCheckpointNode(checkpoint: MidGenerationCheckpoint)` callable producing `EarlyRejectionError` or `{"verdict": "pass"}` | `mathart.quality.mid_generation_checkpoint` |

## References

- [SideFX Houdini — pdg.WorkItem](https://www.sidefx.com/docs/houdini/tops/pdg/WorkItem.html)
- [SideFX Houdini 19 — What's new PDG](https://www.sidefx.com/docs/houdini/news/19/pdg.html)
- [Multi-Fidelity Methods for Optimization: A Survey (arXiv 2402.09638)](https://arxiv.org/html/2402.09638v1)
