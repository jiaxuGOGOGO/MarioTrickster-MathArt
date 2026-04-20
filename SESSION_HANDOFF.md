# SESSION HANDOFF — SESSION-091

## Session Identity

| Field | Value |
|---|---|
| Session ID | SESSION-091 |
| Previous Session | SESSION-090 |
| Date | 2026-04-20 |
| Commit | *(to be filled after push)* |
| PROJECT_BRAIN.json version | v0.82.0 |

## Mission Accomplished

**P1-AI-2E: CLOSED** — Motion-Adaptive Keyframe Planning for High-Nonlinearity Action Segments.

### Deliverables

| Module | File | Lines | Status |
|---|---|---|---|
| **MotionAdaptiveKeyframeBackend** | `mathart/core/motion_adaptive_keyframe_backend.py` | 530+ | NEW |
| **SafePointExecutionLock** | `mathart/core/safe_point_execution.py` | 180+ | NEW |
| **Orchestrator Hot-Reload Coordination** | `mathart/core/microkernel_orchestrator.py` | +80 | MODIFIED |
| **ComfyUI Client Resilience** | `mathart/comfy_client/comfyui_ws_client.py` | +90 | MODIFIED |
| **BackendType Enum** | `mathart/core/backend_types.py` | +5 | MODIFIED |
| **ArtifactFamily Enum** | `mathart/core/artifact_schema.py` | +8 | MODIFIED |
| **Package Exports** | `mathart/core/__init__.py` | +6 | MODIFIED |
| **E2E Test Suite** | `tests/test_motion_adaptive_keyframe.py` | 750+ | NEW |
| **Research Notes** | `research_notes_session091.md` | 200+ | NEW |

### Test Results

| Suite | Tests | Status |
|---|---|---|
| `test_motion_adaptive_keyframe.py` (SESSION-091) | 51/51 | ALL PASS |
| `test_backend_hot_reload.py` (SESSION-090 regression) | 29/29 | ALL PASS |
| **Total** | **80/80** | **ZERO FAILURES** |

## Architecture Decisions

### 1. MotionAdaptiveKeyframeBackend — Core Algorithm

The backend implements a three-stage pipeline grounded in industrial references:

**Stage 1: Nonlinearity Score Computation** (Clavet GDC 2016 / Ubisoft Motion Matching)

Computes per-frame nonlinearity scores from UMR physical features:
- Root acceleration magnitude (finite differences of velocity)
- Angular velocity jerk (second derivative of angular velocity)
- Contact event transitions (foot contact state changes)

Scores are weighted-summed and normalized to [0, 1].

**Stage 2: Adaptive Keyframe Selection** (Guilty Gear Xrd / Motomura GDC 2015)

Selects keyframes using a priority-queue algorithm with three constraints:
- **Contact Safe Points**: All contact events (hitstop, landing, foot-strike) are forced keyframes — the Guilty Gear Xrd discipline.
- **min_gap**: Prevents cluster packing near extrema (anti-Cluster Trap).
- **max_gap**: Prevents starvation in smooth segments (anti-Void Trap).
- **Extrema Capture**: Local maxima above threshold are prioritized.

This is NOT `frame_idx % step == 0`. Gaps are non-uniform by design.

**Stage 3: SparseCtrl end_percent Mapping** (Guo et al., ECCV 2024)

Maps nonlinearity scores to SparseCtrl `end_percent` values:
- High-nonlinearity keyframes get high end_percent (stronger guidance)
- Low-nonlinearity keyframes get base end_percent (lighter guidance)
- Linear interpolation: `base + score * (max - base)`

### 2. SafePointExecutionLock — Frame-Boundary Gating

Per-backend reader-writer lock inspired by Unity Domain Reloading:
- `execution_fence(backend_name)`: Multiple concurrent executions allowed (readers).
- `reload_gate(backend_name)`: Exclusive access, waits for all executions to complete (writer).
- Different backends are fully independent — no cross-backend blocking.

### 3. Orchestrator Hot-Reload Coordination

`on_backend_reload(backend_name)` implements targeted cache invalidation:
- Purges `_result_cache[backend_name]` (stale KEYFRAME_PLAN discarded)
- Resets `_iteration_counters[backend_name]` (evolution restarts from scratch)
- Fires registered reload callbacks (ComfyUI Client, etc.)
- **Never** calls `clear()` on the entire cache (anti-State-Wipeout).

### 4. ComfyUI Client Resilience

- `on_backend_reload()`: Purges cached workflow payloads referencing the reloaded backend.
- `execute_workflow_safe()`: Wraps execution in SafePointExecutionLock fence.
- `set_safe_point_lock()`: Dependency injection from Orchestrator.

## Anti-Pattern Guards Verified

| Anti-Pattern | Guard | Tests |
|---|---|---|
| **Stale Cache Leak Trap** | `on_backend_reload()` targeted invalidation | 4 tests |
| **Extrema Omission & Void Trap** | `min_gap` / `max_gap` constraints + contact forced capture | 6 tests |
| **Mid-Frame Reload Trap** | `SafePointExecutionLock` reader-writer fence | 5 tests |

## Academic & Industrial References Aligned

| Reference | How Applied |
|---|---|
| SparseCtrl (Guo et al., ECCV 2024) | end_percent dynamic mapping based on nonlinearity scores |
| Ubisoft Motion Matching (Clavet GDC 2016) | Root acceleration + angular jerk as nonlinearity features |
| Guilty Gear Xrd (Motomura GDC 2015) | Contact events forced as safe-point keyframes |
| Erlang/OTP Hot Code Swapping | SafePointExecutionLock — code swap only at safe boundaries |
| Unity Domain Reloading | Reader-writer lock pattern for execution/reload mutual exclusion |
| Eclipse OSGi | Targeted unregister then re-register lifecycle (not global clear) |

## Next Session Preparation: P1-ARCH-4 (PDG v2 Runtime Semantics)

### What P1-ARCH-4 Requires

P1-ARCH-4 targets **PDG v2 runtime semantics**: cache key management, partition/collect, fan-out/fan-in orchestration, and reusable work-item attributes for the lightweight DAG runtime.

### Current Architecture Readiness

The microkernel and scheduler architecture is well-positioned for P1-ARCH-4. The following micro-adjustments are recommended:

**1. Work-Item Abstraction Layer**

The current Orchestrator operates on backends directly. P1-ARCH-4 needs a `WorkItem` abstraction that wraps backend execution with:
- Cache key computation (hash of inputs + config + backend version)
- Partition assignment (which shard of a fan-out this item belongs to)
- Dependency tracking (which upstream work-items must complete first)

**Recommended**: Create `mathart/core/work_item.py` with a `WorkItem` dataclass that references a backend name, input hash, and partition ID.

**2. Cache Key Manager**

The current `_result_cache` in Orchestrator uses backend names as keys. P1-ARCH-4 needs content-addressable caching:
- Key = `hash(backend_name + input_data_hash + config_hash + backend_version)`
- This enables cache reuse across iterations when only some inputs change.

**Recommended**: Create `mathart/core/cache_key_manager.py` with a `CacheKeyManager` class. The existing `_result_cache` dict becomes the storage backend; the key manager computes keys.

**3. Fan-Out / Fan-In Scheduler**

The current Orchestrator runs backends sequentially. P1-ARCH-4 needs:
- Fan-out: Split a batch of frames into N partitions, each processed by a separate backend instance.
- Fan-in: Collect results from all partitions and merge.
- The `SafePointExecutionLock` already supports concurrent executions of the same backend — this is the foundation for fan-out.

**Recommended**: Extend `MicrokernelOrchestrator` with `run_fan_out(backend_name, partitions)` and `run_fan_in(results)` methods. Use `concurrent.futures.ThreadPoolExecutor` for parallel partition execution.

**4. Partition / Collect Primitives**

Frame-level partitioning for the keyframe planner:
- Partition by time segments (e.g., 0-2s, 2-4s, 4-6s)
- Partition by nonlinearity regions (high-NL segments get finer partitions)
- Collect: merge keyframe plans from all partitions, resolving boundary overlaps.

**Recommended**: Add `partition()` and `collect()` methods to `MotionAdaptiveKeyframeBackend` as a reference implementation.

**5. Hot-Reload Integration**

The SafePointExecutionLock and on_backend_reload() infrastructure from SESSION-091 directly supports P1-ARCH-4:
- Cache invalidation on reload already works per-backend.
- The lock prevents mid-execution reload during fan-out partitions.
- **No additional changes needed** for hot-reload support.

### Minimal Code Changes Required

| Component | Change | Effort |
|---|---|---|
| `mathart/core/work_item.py` | NEW: WorkItem dataclass | Small |
| `mathart/core/cache_key_manager.py` | NEW: Content-addressable cache keys | Small |
| `microkernel_orchestrator.py` | ADD: `run_fan_out()`, `run_fan_in()` | Medium |
| `motion_adaptive_keyframe_backend.py` | ADD: `partition()`, `collect()` | Small |
| `tests/test_pdg_v2_runtime.py` | NEW: Full E2E test suite | Medium |

## Updated TODO List

### Closed This Session

- [x] **P1-AI-2E**: Motion-Adaptive Keyframe Planning (SESSION-091)
- [x] **P1-MIGRATE-4**: Backend Hot-Reload Ecosystem (SESSION-090)

### Next Priorities

1. **P1-ARCH-4**: PDG v2 runtime semantics (cache keys, partition, fan-out/fan-in)
2. **P3-GPU-BENCH-1**: GPU benchmark infrastructure
3. **P1-AI-2D-SPARSECTRL**: SparseCtrl temporal consistency (SUBSTANTIALLY-CLOSED)
4. **P1-INDUSTRIAL-34C**: Industrial compliance
5. **P1-ARCH-5**: OpenUSD-compatible scene interchange
6. **P1-B1-1**: Cape/hair ribbon rendering from Jakobsen chain

### Files Changed This Session

```
NEW:  mathart/core/motion_adaptive_keyframe_backend.py  (530+ lines)
NEW:  mathart/core/safe_point_execution.py              (180+ lines)
NEW:  tests/test_motion_adaptive_keyframe.py            (750+ lines)
NEW:  research_notes_session091.md                      (200+ lines)
MOD:  mathart/core/backend_types.py                     (+5 lines)
MOD:  mathart/core/artifact_schema.py                   (+8 lines)
MOD:  mathart/core/microkernel_orchestrator.py           (+80 lines)
MOD:  mathart/comfy_client/comfyui_ws_client.py          (+90 lines)
MOD:  mathart/core/__init__.py                           (+6 lines)
MOD:  PROJECT_BRAIN.json                                 (v0.82.0)
MOD:  SESSION_HANDOFF.md                                 (this file)
```

## Context for Next AI Agent

If you are the next AI agent reading this handoff:

1. **Read `PROJECT_BRAIN.json`** first — it contains the full gap inventory and priority ordering.
2. **Read `research_notes_session091.md`** for the academic/industrial references that guided this session.
3. **The Registry Pattern is LAW** — never add if/else to trunk code. New backends self-register via `@register_backend`.
4. **The SafePointExecutionLock is deployed** — use `execution_fence()` for any batch render, `reload_gate()` for any hot-reload.
5. **The Orchestrator has hot-reload coordination** — call `on_backend_reload()` after any `registry.reload()`.
6. **80/80 tests pass** — run `python3 -m pytest tests/ -v` to verify before any changes.
