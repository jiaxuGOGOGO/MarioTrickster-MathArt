# P1-ARCH-4 Session 104 Audit

## Context Snapshot

The repository head is already at commit `7b90e6adae3d856cb27b6fe5067472add3ae60c9`, whose message claims closure of `P1-ARCH-4`. The local handoff and project brain also mark this gap as closed. However, the current source audit shows that the implementation is only **partially** aligned with the stricter execution semantics requested in the present task.

## What Is Already Correct

The current `mathart/level/pdg.py` does introduce immutable `WorkItem` contracts, explicit `PDGFanOutItem` / `PDGFanOutResult`, disk-backed SHA-256 action/cache separation, work-item trace records, `collect` topology, and a backward-compatible facade exposed through `ProceduralDependencyGraph.run()`. These aspects are meaningfully aligned with SideFX PDG work-item centric runtime semantics and Bazel-style Action Cache plus CAS layering.[1] [2]

## Residual Gap

The remaining material gap is **physical bounded local concurrency**. Although the code now models fan-out and fan-in structurally, `_execute_task_node()` still iterates invocations with a plain sequential `for` loop, so the runtime does not yet use a bounded local executor to dispatch mapped work items. This means the implementation still lacks the requested single-machine scheduler semantics derived from `concurrent.futures`: explicit worker-cap control, real concurrent fan-out dispatch, and a barrier-based fan-in that completes outside worker tasks rather than via nested future waiting.[3]

## Required Closure for This Session

The runtime should therefore be upgraded to support configurable bounded local execution while preserving backward compatibility. The safest path is to keep `WorkItem`/cache/collect semantics intact, add a scheduler configuration with `max_workers`, and dispatch independent mapped invocations through a local executor. The collect stage must remain a fan-in barrier on the runtime side, not a worker-internal wait. White-box tests must prove three facts: identical hash means zero recomputation, mapped fan-out actually runs through the bounded scheduler, and fan-in aggregation completes without data contamination or deadlock.

## References

[1]: https://www.sidefx.com/docs/houdini/tops/pdg/index.html "SideFX Houdini PDG / TOPs"
[2]: https://bazel.build/remote/caching "Bazel Remote Caching"
[3]: https://docs.python.org/3/library/concurrent.futures.html "Python concurrent.futures"
