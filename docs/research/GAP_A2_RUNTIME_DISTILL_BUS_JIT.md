# Gap A2 — Runtime Distillation Bus with Data-Oriented JIT Constraint Execution

## Problem Statement

Gap A2 asked for a **global distillation bus** that does not stop at `KnowledgeRule -> ParameterSpace`, but actually reaches the runtime. The critical concern is straightforward: if distilled rules remain as nested JSON objects or Python dictionaries, then a 60fps motion loop pays repeated string-lookup and branch interpretation costs exactly where the project can least afford them.

> The engineering target is therefore not "more rules" but **lower-latency rule execution**: knowledge must be compiled once, lowered into dense numeric layouts, and then executed by the runtime as compiled kernels rather than interpreted metadata.

## Research Summary

| Reference | Practical takeaway for this project | Landed decision |
|---|---|---|
| Mike Acton — Data-Oriented Design lectures and notes [1][2] | Optimize for the actual data flow and hardware cost model, not for object hierarchy elegance. Hot loops should consume dense arrays with predictable access. | `ParameterSpace` is lowered into contiguous numeric vectors and alias maps before entering runtime evaluators. |
| 胡渊鸣 / Taichi language paper and Taichi docs [3][4][5] | High-level authoring and low-level execution can coexist if kernels are compiled from data-oriented fields. Runtime classes should expose compact fields and kernel-style APIs. | The bus is designed as **Taichi-ready**, but this session lands a Numba-first backend because it fits the current NumPy-heavy repository with lower integration risk. |
| Numba performance guidance [6] | Numeric loops over arrays can be compiled effectively, but Python containers and dynamic object graphs should be removed from the hot path before JIT. | Runtime programs operate on `np.ndarray` feature vectors and generated closures, not on nested dict traversal inside the frame loop. |

## Why Numba First, Taichi Ready

The project already uses Python and NumPy pervasively across animation, physics, evaluation, and distillation. Because of that, **Numba** is the shortest path from repository knowledge to machine-code execution: it can compile dense-array kernels immediately without a whole-engine rewrite. Taichi remains highly relevant as a future backend, especially for wider field-oriented kernels or GPU deployment, but the current repository has not yet standardized a field lifecycle, sparse storage policy, or kernel scheduler across subsystems.

The correct sequencing is therefore:

1. close the architectural gap now with a **runtime bus**;
2. prove that rules can be compiled out of JSON/dicts and into real executable kernels;
3. keep the lowering boundary clean enough that a future Taichi backend can replace or extend the Numba backend without changing knowledge authoring semantics.

## Landed Architecture

| Layer | Before SESSION-050 | After SESSION-050 |
|---|---|---|
| Knowledge authoring | Markdown / JSON rules | unchanged |
| Compilation | `KnowledgeParser` + `RuleCompiler` + `ParameterSpace` | unchanged as semantic source of truth |
| Runtime lowering | missing | `mathart/distill/runtime_bus.py` lowers spaces into dense arrays, alias maps, and generated evaluators |
| Hot-path execution | Python dict checks inside runtime modules | Numba-compiled closures execute dense feature vectors |
| Evolution closure | no dedicated Gap A2 bridge | `mathart/evolution/runtime_distill_bridge.py` evaluates, distills, and persists runtime-bus progress |

## What Was Implemented

### 1. Global Runtime Distillation Bus

`RuntimeDistillationBus` now compiles repository knowledge into runtime-consumable artifacts.

It performs four jobs. First, it reuses the existing parser and compiler pipeline to preserve the repository's knowledge semantics. Second, it lowers `ParameterSpace` constraints into dense min/max/default arrays. Third, it generates specialized evaluators and compiles them with **Numba** when available. Fourth, it exposes module-level and global clamp/default APIs so downstream systems can consume compiled constraints without re-parsing knowledge files.

### 2. Data-Oriented ParameterSpace Lowering

The new `CompiledParameterSpace` converts human-facing parameter definitions into a runtime form with the following properties:

| Runtime field | Purpose |
|---|---|
| `param_names` | stable parameter ordering |
| `defaults` | dense fallback/default vector |
| `min_values`, `max_values` | dense range bounds |
| `has_min`, `has_max` | branch-friendly presence masks |
| `hard_mask` | hard/soft violation classification |
| alias maps | allow runtime use of leaf names such as `contact_height` without losing fully-qualified names |

This is the exact DOD move Gap A2 needed: **shape the data once, then reuse the layout everywhere**.

### 3. Generated JIT Rule Programs

The new `RuntimeRuleProgram` turns structured clauses into generated evaluators. In practice, the bus can now take rules such as:

- `foot_height <= threshold`
- `abs(foot_vertical_velocity) <= threshold`

and generate a specialized evaluator that returns acceptance, score, penalty, and a clause bitmask. This is the project-side realization of the idea that rules like `foot_lock > 0.8` should become **compiled closures**, not repeatedly interpreted condition trees.

### 4. Runtime Integration into the 60fps Contact Path

The most important proof point is not the existence of a new module, but the fact that it is now used by a real hot path.

`mathart/animation/physics_projector.py` now accepts a runtime distillation bus and/or a compiled foot-contact program. `ContactDetector.update()` uses a preallocated feature buffer and dispatches directly into the compiled rule evaluator when present. This means the frame loop no longer has to reinterpret nested knowledge structures while deciding foot-ground contact.

### 5. Global Pre-Generation Injection

`ArtMathQualityController.pre_generation()` now loads the runtime distillation bus lazily and applies globally compiled constraints before asset generation continues. That closes the other half of the gap: the bus is not only available to physics, but also available to repository-wide parameter adjustment logic.

### 6. Three-Layer Evolution Closure for Gap A2

`RuntimeDistillBridge` now provides a dedicated self-evolution loop:

| Layer | Mechanism |
|---|---|
| Layer 1 | evaluate compiled module count, constraint count, rule correctness, and runtime throughput |
| Layer 2 | distill validated runtime-bus rules into `knowledge/runtime_distill_bus.md` |
| Layer 3 | persist trend state in `.runtime_distill_state.json` and compute a small fitness bonus |

This matters because Gap A2 was never just a missing class. It was a missing **evolutionary closure** between new knowledge, runtime execution, and future self-improvement.

## Evidence from the First Runtime Bus Cycle

Running `tools/run_runtime_distill_cycle.py` on the repository after the implementation produced the following first-cycle metrics:

| Metric | Result |
|---|---|
| Backend | `numba` |
| Compiled module count | `18` |
| Compiled constraint count | `297` |
| Contact-rule benchmark throughput | `458629.2 eval/s` |
| Expected contact-gate matches | `6 / 6` |
| Acceptance | `True` |

These numbers confirm that the bus is not theoretical. It compiled real repository knowledge and executed a real runtime program successfully.

## Why This Closes Gap A2

Gap A2 was defined as **“全局蒸馏总线未接入运行时”**. After SESSION-050, this statement is no longer accurate for the core architectural path.

The repository now has:

1. a **global bus** that compiles knowledge into runtime artifacts;
2. a **JIT lowering path** from semantic constraints to machine-executable closures;
3. a **hot-path consumer** in foot contact / foot locking;
4. a **global pre-generation consumer** in quality control; and
5. a **three-layer evolution bridge** that lets the system keep learning from future knowledge and future audits.

The remaining work is not “bus missing,” but **bus expansion**: more runtime consumers, more specialized kernels, optional Taichi backend, and deeper rollout into animation, level, export, and evaluator subsystems.

## Next Expansion Paths

| Priority | Next step | Why it matters |
|---|---|---|
| P1 | Extend runtime bus into gait blending, locomotion scoring, and `compute_physics_penalty()` batch paths | Wider hot-path payoff and better GA fitness throughput |
| P1 | Add Taichi backend behind the same lowering boundary | Enables field-oriented kernels and future GPU scaling without changing knowledge authoring |
| P1 | Push compiled defaults into more modules from `pipeline.py` directly | Improves consistency between generation-time and runtime-time constraint use |
| P2 | Add benchmark suites for larger batch evaluators | Converts architectural correctness into production-facing performance evidence |

## References

[1]: https://dataorienteddesign.com/dodbook/
[2]: https://neil3d.github.io/assets/img/ecs/DOD-Cpp.pdf
[3]: https://www.taichi-lang.org/
[4]: https://docs.taichi-lang.org/docs/odop
[5]: https://docs.taichi-lang.org/docs/data_oriented_class
[6]: https://numba.readthedocs.io/en/stable/user/performance-tips.html
