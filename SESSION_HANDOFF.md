## Session Identity

| Field | Value |
|---|---|
| Session ID | SESSION-092 |
| Previous Session | SESSION-091 |
| Date | 2026-04-20 |
| Commit | *(to be filled after push)* |
| PROJECT_BRAIN.json version | v0.82.0 |

## Session Outcome

**SESSION-092** was executed as a targeted hotfix session rather than a feature expansion session. The scope was intentionally constrained to three red-line defects that blocked safe progression toward **P1-ARCH-4**: the coarse-grained Safe Point implementation, the failure to guarantee Contact-frame survival under `min_gap` conflicts, and the inaccurate physical naming of the angular signal.

| Hotfix Axis | Status | Result |
|---|---|---|
| Safe Point lock granularity | Fixed | Safe Point moved to **frame-boundary coordination** rather than batch-wide outer locking |
| File watcher hot-reload chain | Fixed | Watcher now emits **reload requests** and waits for main-thread boundary consumption |
| Contact-frame absolute retention | Fixed | Contact frames are now **protected anchors** that ordinary peaks cannot evict |
| Angular signal naming | Fixed | `jerk` wording replaced with **angular acceleration** semantics |
| Regression validation | Fixed | `82/82` targeted tests passed with **zero FAIL** and **zero SKIP** |

## Architecture State After SESSION-092

The repository now reflects a stricter interpretation of Safe Point semantics. A long-running batch render is no longer wrapped by a coarse outer execution fence. Instead, execution ownership is entered and exited at the **per-frame boundary**, and reload is only consumed in the narrow interval after frame `N` completes and before frame `N+1` starts. This closes the deadlock class that would otherwise stall reload for the full batch duration.

At the same time, the keyframe planner now treats **Contact frames as hard anchors** rather than ordinary candidates inside a score contest. This means the planner may still use score-driven extrema selection for non-contact frames, but it is no longer allowed to sacrifice a Contact frame merely because a neighboring non-contact peak has a higher score.

| Component | File | SESSION-092 Change |
|---|---|---|
| Safe Point coordinator | `mathart/core/safe_point_execution.py` | Added explicit reload-request signaling and boundary-time consumption primitives |
| File watcher | `mathart/core/backend_file_watcher.py` | Replaced direct watcher-thread reload with queued `reload_requested` + boundary processing |
| ComfyUI client | `mathart/comfy_client/comfyui_ws_client.py` | Removed the incorrect batch-wide outer Safe Point wrapper from `execute_workflow_safe()` |
| Keyframe backend | `mathart/core/motion_adaptive_keyframe_backend.py` | Enforced Contact absolute override and corrected angular-acceleration naming |
| Motion/keyframe tests | `tests/test_motion_adaptive_keyframe.py` | Added Contact conflict regression and watcher/boundary coordination test |
| Hot-reload regression tests | `tests/test_backend_hot_reload.py` | Updated watcher tests to the new queued-reload semantics |

## SESSION-092 HOTFIX REPORT

### 1. Safe Point was rebuilt as a frame-boundary mechanism

The previous defect was architectural, not cosmetic. The old approach allowed a Safe Point context to wrap an entire multi-frame render task, which meant hot-reload could be delayed for the full length of the batch. In practice, that pattern risks apparent deadlock and makes the watcher thread useless during long renders.

The fix was to preserve mutual exclusion semantics while changing the **time of coordination**. The Safe Point layer now supports a request/consume model: a background watcher can mark that a backend reload is requested, but only the render thread is allowed to consume that request, and only at a frame boundary. The render loop therefore owns the moment of reload, not the watcher daemon.

| Old behavior | New behavior |
|---|---|
| Watcher thread could call `registry.reload()` directly | Watcher thread only queues a reload request |
| Outer fence could cover the whole batch | `frame_execution()` covers only the current frame-sized section |
| Reload timing depended on watcher thread scheduling | Reload timing is explicit and deterministic at the main-thread frame boundary |
| Long render could stall hot-reload for minutes | Reload is consumed at the next reachable frame boundary |

This hotfix also keeps backend isolation intact. Different backends still coordinate independently, but the contract is now honest: **the batch loop must poll and consume reload requests between frames** rather than relying on a giant outer lock.

### 2. BackendFileWatcher now closes the handoff loop correctly

The previous chain was broken because the watcher observed file changes yet bypassed the render pipeline by reloading immediately on the daemon thread. That contradicted the Safe Point design intent.

The watcher has now been converted into a two-stage pipeline. First, filesystem debounce resolves the changed module and stores a `reload_requested` record. Second, the render owner calls `process_pending_reloads()` at a frame boundary, which consumes the queued request through the Safe Point coordinator and only then executes `registry.reload()`.

| Watcher event stage | SESSION-092 behavior |
|---|---|
| File modified | Debounce scheduler coalesces events |
| Module resolved to backend | Watcher records `reload_requested` history |
| Main render loop reaches boundary | `process_pending_reloads()` attempts reload |
| Reload completes | `reload_event` and optional callback fire with final success/failure |

This design explicitly prevents the watcher from re-entering the registry during an active frame. It also gives tests and orchestration code separate visibility into **request queued** versus **request consumed**.

### 3. Contact frames are now absolute protected anchors

The second red-line defect was in keyframe filtering semantics. The previous approach could gather Contact frames and non-contact extrema into one common candidate pool and then resolve `min_gap` conflicts by score. That made Contact frames vulnerable to eviction by nearby higher-scoring ordinary peaks.

The hotfix separates **protected anchors** from **ordinary optional peaks**. Boundary anchors and Contact frames are inserted into a locked set first. Later non-contact extrema are considered only if they do not violate those protected anchors. If a non-contact peak conflicts with a Contact frame inside `min_gap`, the non-contact peak is discarded unconditionally.

| Candidate type | Conflict rule after SESSION-092 |
|---|---|
| Boundary anchor | Always retained |
| Contact frame | Always retained |
| Ordinary extrema | May be inserted only if they do not evict a protected anchor |
| Gap filler frame | May be inserted only if legal under current protected selection |

A dedicated regression now verifies the precise failure mode reported in review: with `scores=[0.0, 0.2, 0.9, 0.1, 0.0]`, Contact at index `1`, and `min_gap=2`, the planner **must** retain frame `1` and **must** reject frame `2` even though frame `2` has the higher scalar score.

### 4. Physical naming now matches the actual quantity being computed

The implementation computes the first discrete difference of angular velocity magnitude scaled by FPS. That corresponds to **angular acceleration magnitude**, not angular jerk. The codebase has therefore been renamed to use `angular_acceleration` / `angular_accel_magnitude` semantics in comments, configuration fields, and metadata emission.

For compatibility with earlier config payloads, the computation function still accepts the legacy keyword `weight_ang` and the planner still tolerates a legacy override key when present. However, the canonical naming in code and emitted metadata is now the corrected angular-acceleration form.

## Validation Summary

The hotfix was accepted only after the targeted regression suites passed without FAIL or SKIP.

| Test Suite | Result |
|---|---|
| `tests/test_motion_adaptive_keyframe.py` | `53/53` passed |
| `tests/test_backend_hot_reload.py` | `29/29` passed |
| **Total** | **`82/82` passed** |

The new coverage explicitly includes a watcher-driven frame-boundary reload test and a `min_gap=2` Contact-protection conflict test. The watcher test verifies that reload is queued during rendering and consumed only in a true frame-boundary gap. The Contact test verifies that a higher-scoring non-contact peak cannot evict a Contact frame.

## Current Guidance for the Next Agent

The system is now in a safer state for concurrency-oriented orchestration work, but the following operational rules are mandatory.

| Rule | Guidance |
|---|---|
| Safe Point usage | Do **not** wrap an entire batch render with a coarse outer lock |
| Render loop contract | Call boundary-time reload consumption between frames |
| Watcher contract | Treat watcher output as a **reload request**, not immediate permission to reload |
| Keyframe filtering | Treat Contact frames as **absolute protected anchors** |
| Physics terminology | Use **angular acceleration** wording, not jerk, for this signal |

## Updated TODO Status

| Priority | Item | State |
|---|---|---|
| P1-AI-2E | Motion-Adaptive Keyframe Planning | Closed, then hotfixed in SESSION-092 |
| P1-MIGRATE-4 | Backend Hot-Reload Ecosystem | Closed, with SESSION-092 safety correction |
| P1-ARCH-4 | PDG v2 runtime semantics | **Unblocked after SESSION-092 hotfix** |
| P3-GPU-BENCH-1 | GPU benchmark infrastructure | Pending |
| P1-AI-2D-SPARSECTRL | SparseCtrl temporal consistency | Pending follow-up |

## Files Touched in SESSION-092

```text
MOD: mathart/core/safe_point_execution.py
MOD: mathart/core/backend_file_watcher.py
MOD: mathart/comfy_client/comfyui_ws_client.py
MOD: mathart/core/motion_adaptive_keyframe_backend.py
MOD: tests/test_motion_adaptive_keyframe.py
MOD: tests/test_backend_hot_reload.py
MOD: SESSION_HANDOFF.md
```

## Handoff Note

If the next session begins work on **P1-ARCH-4**, start from the current frame-boundary contract rather than the SESSION-091 coarse-lock model. The watcher and Safe Point systems now assume a cooperative render loop that explicitly polls for pending reloads between frames. Reverting to a batch-wide outer fence would reopen the exact red-line failure that SESSION-092 was created to eliminate.
