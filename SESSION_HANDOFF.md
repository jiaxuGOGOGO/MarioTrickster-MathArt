# SESSION-123 Handoff — PDG v2 SeedSequence Determinism and Deep RNG Injection

## Goal & Status
**Objective**: close the deterministic-RNG follow-through for the level-generation lane by making **PDG v2** derive **stable per-invocation random streams** from a root seed, inject a real `numpy.random.Generator` into `ctx["_pdg"]["rng"]`, keep cache keys hermetic, and push that explicit RNG contract all the way down into **WFC** and **ConstraintAwareWFC**.

**Status**: **CLOSED**.

The landed implementation upgrades `mathart/level/pdg.py` so every invocation receives a child stream derived from a stable **NumPy `SeedSequence` contract**, exposes an audit-friendly `rng_contract` alongside the live generator, and strictly separates cache-key context from runtime-only objects so no live generator identity can poison hermetic hashing.[1] [4] The same session also refactors `mathart/level/wfc.py`, `mathart/level/constraint_wfc.py`, and the WFC node in `mathart/pipeline.py` so real level-generation work can consume the injected generator rather than silently relying on thread-order-sensitive internal randomness.[1] [3]

## Research Alignment Audit
The implementation was deliberately constrained by four external references, and the raw notes were saved to `research/session123_rng_determinism_research.md`.

| Reference pillar | Practical rule adopted in code | Why it matters here |
|---|---|---|
| NumPy `SeedSequence.spawn()` / parallel RNG guidance | Treat child streams as **independent descendants** of one root entropy source instead of reseeding ad hoc generators.[1] | This is the foundation for deterministic mapped fan-out without stream overlap or thread-order bleed. |
| JAX PRNG design | Make randomness an **explicitly propagated resource** rather than hidden mutable global state.[2] | This directly motivated `ctx["_pdg"]["rng"]` and `rng_contract` as first-class runtime inputs. |
| Scientific Python RNG best practices | Prefer **passing `Generator` instances explicitly** and avoid legacy global/random-module side effects.[3] | This shaped the WFC / ConstraintAwareWFC constructor changes and the pipeline deep-injection path. |
| Bazel hermeticity | Cache keys must depend only on **declared, stable inputs**, never on ambient or process-local state.[4] | This forced a clean split between cacheable RNG metadata and the live runtime generator object. |

> “Hermetic builds are insensitive to libraries and other software installed on the local or remote host machine.” That same principle was applied here to PDG execution: the cache contract now depends on a stable RNG descriptor, not on in-memory generator identity or execution order.[4]

## Architecture Decisions Locked
The first locked decision is that **PDG v2 now owns the root random-state contract for mapped execution**. `ProceduralDependencyGraph.run()` derives a root `SeedSequence` from `pdg_seed`, `rng_seed`, or `seed` when present, and otherwise deterministically hashes graph/context material into a derived root entropy bundle. Every invocation then materializes a child stream from a stable spawn descriptor containing graph name, node name, invocation index, partition key, and upstream work-item lineage.[1] [4]

The second locked decision is that **runtime RNG injection is explicit but cache-safe**. `ctx["_pdg"]` now includes a live `rng`, a `seed_sequence`, and a serializable `rng_contract`. The live generator is added only to the runtime context passed into node execution; cache-key computation uses a separate context containing only the stable contract. This preserves hermetic SHA-256 action keys while still giving node code a real generator object.[3] [4]

The third locked decision is that **the explicit RNG contract had to reach a real production lane, not just synthetic tests**. `WFCGenerator` now supports `rng: np.random.Generator | None`, replaces `random.Random` usage with NumPy-backed `_choice()` / `_weighted_choice()` helpers, and keeps `seed` construction for backward compatibility. `ConstraintAwareWFC` adopts the same contract, and the level WFC node in `mathart/pipeline.py` now consumes `ctx["_pdg"]["rng"]` when present.[1] [3]

The fourth locked decision is that **scheduler concurrency must not perturb outputs**. The new white-box PDG test compares mapped fan-out output from **1 thread** and **16 threads** and requires byte-identical generated random blocks per partition. This closes the thread-order nondeterminism risk that would otherwise remain hidden behind “same average behavior” style tests.[1] [2]

## Code Change Table
| File | Action | Details |
|---|---|---|
| `mathart/level/pdg.py` | Modified | Added root-seed derivation, stable per-invocation SeedSequence materialization, `ctx["_pdg"]["rng"]`, `seed_sequence`, `rng_contract`, and cache/runtime context separation so live generators never enter cache hashing. |
| `mathart/level/wfc.py` | Modified | Replaced `random.Random` with NumPy `Generator` support, introduced `_random()` / `_choice()` / `_weighted_choice()` helpers, and preserved legacy `seed=` construction. |
| `mathart/level/constraint_wfc.py` | Modified | Added optional `rng` injection and switched constrained-collapse selection to the new helper methods from the base WFC generator. |
| `mathart/pipeline.py` | Modified | Updated the WFC PDG node to consume the injected RNG from `ctx["_pdg"]`, proving deep RNG propagation reaches the real level lane. |
| `tests/test_level_pdg.py` | Modified | Added a 1-thread vs 16-thread byte-level deterministic mapped-fan-out regression for `ctx["_pdg"]["rng"]` plus spawn-contract uniqueness assertions. |
| `tests/test_level.py` | Modified | Added a WFC explicit-Generator determinism regression to lock the new injection API. |
| `research/session123_rng_determinism_research.md` | Added | Working notes for NumPy SeedSequence, JAX PRNG design, Scientific Python RNG practice, and Bazel hermeticity. |
| `PROJECT_BRAIN.json` | Updated | Recorded SESSION-123 closure, new validation summary, recent-focus snapshot, and architecture-memory notes. |
| `SESSION_HANDOFF.md` | Updated | Replaced the prior handoff with the current closure summary and next-step guidance. |

## White-Box Validation Closure
Local touched-lane validation is complete.

| Validation command / scope | Result |
|---|---|
| `python3.11 -m pytest tests/test_level_pdg.py tests/test_level.py -q` | **33/33 PASS** |
| `python3.11 -m pytest tests/test_constraint_wfc.py -q` | **36/36 PASS** |
| Total touched-lane validation | **69/69 PASS** |

The validation matrix closes three red lines at once. First, it proves that mapped PDG fan-out now emits **byte-identical random outputs** under 1-thread and 16-thread execution. Second, it proves that the live generator reaches real node code through `ctx["_pdg"]["rng"]` without contaminating cache contracts. Third, it proves that the deep consumer lane—`WFCGenerator` and `ConstraintAwareWFC`—remains backward-compatible while accepting explicit `Generator` injection.

## Practical Implication for the Architecture Roadmap
SESSION-123 does **not** introduce a new scene-format feature, but it materially hardens the production credibility of the level lane. The repository now has a clean distinction between **stable random-input contract** and **live runtime random-state object**, which is exactly the separation needed for future distributed PDG execution, reproducible benchmark capture, and stronger WorkItem lineage auditing.[1] [4]

In practical terms, future PDG-powered subsystems no longer need to improvise their own concurrency-safe RNG story. They can accept `ctx["_pdg"]["rng"]`, preserve local backward compatibility with optional constructor injection, and rely on the runtime to keep stream derivation deterministic across scheduler widths.[2] [3]

## Recommended Next Steps
The highest-value immediate follow-up is to **extend the same explicit RNG contract into additional stochastic production lanes** that still construct local random behavior internally. The most obvious candidates are any remaining procedural layout, noise, sampler, or policy-search components that execute beneath PDG fan-out but do not yet consume `ctx["_pdg"]["rng"]` directly.

The second follow-up is to **promote `rng_contract` into stronger lineage reporting**. At the moment, the contract is visible to node code and present in cache-safe context, but it would be valuable to persist a compact subset of that metadata into more exported manifests or benchmark records so external audit tools can prove that two artifacts came from the same deterministic seed lineage.[1] [4]

The third follow-up is to **evaluate remote or multi-process execution readiness**. The current scheduler is local-threaded, but the explicit root-seed/child-stream contract is now strong enough that future executor backends can preserve determinism without sharing mutable RNG state, which was one of the core design lessons extracted from JAX-style split PRNG systems.[2]

## References
[1]: https://numpy.org/doc/2.2/reference/random/bit_generators/generated/numpy.random.SeedSequence.spawn.html "NumPy — SeedSequence.spawn"
[2]: https://docs.jax.dev/en/latest/jep/263-prng.html "JAX PRNG Design (JEP 263)"
[3]: https://blog.scientific-python.org/numpy/numpy-rng/ "Scientific Python — Best Practices for Using NumPy's Random Number Generators"
[4]: https://bazel.build/basics/hermeticity "Bazel — Hermeticity"
