# SESSION-090 Handoff — P1-MIGRATE-4: Backend Hot-Reload Ecosystem

**Commit**: SESSION-090
**Status**: **P1-MIGRATE-4 CLOSED**
**Date**: 2026-04-20
**Previous Session**: SESSION-089 (P1-INDUSTRIAL-34C: Orthographic Pixel Render)

## Executive Summary

SESSION-090 delivers the complete **Backend Hot-Reload Ecosystem**, closing the P1-MIGRATE-4 gap that has been tracked since SESSION-064. The implementation follows industrial-grade hot-reload patterns from Erlang/OTP, Eclipse OSGi, Unity Domain Reloading, and Python `watchdog` + `importlib.reload` best practices.

| Metric | Value |
|---|---|
| **Gap closed** | P1-MIGRATE-4 |
| **New files** | 2 production + 1 test + 1 research |
| **Registry upgrades** | `unregister()`, `reload()`, `_backend_module_map`, `_reload_lock` (RLock), `get_watched_package_paths()`, `module_to_backend_name()` |
| **Daemon watcher** | `BackendFileWatcher` with 400ms debounce, daemon thread, context manager |
| **Anti-pattern guards** | Zombie Reference Trap, State Wipeout Trap, Blocking & Debounce Trap |
| **Test coverage** | **29 PASS, 0 FAIL** — 29 test cases across 11 test groups |
| **Research** | Erlang/OTP, Eclipse OSGi, Unity/Unreal Domain Reloading, Python importlib |
| **Total new code** | ~1,800 lines (registry upgrades + watcher + tests + research notes) |

## What Landed in Code

### 1. BackendRegistry Atomic Hot-Reload Primitives (`mathart/core/backend_registry.py`)

Upgrades to the existing singleton registry, fully backward-compatible:

- **`unregister(name) -> bool`**: Atomically removes a single backend from `_backends` and `_backend_module_map`. Targeted eviction — all other backends remain untouched (Erlang/OTP two-version coexistence discipline). Returns `True` if found and removed, `False` otherwise.

- **`reload(name) -> bool`**: Full Erlang/OTP-inspired hot-swap sequence:
  1. **Capture** old class identity (`id()`) for zombie detection.
  2. **Evict** the old `(BackendMeta, Type)` tuple from `_backends`.
  3. **Deep-clean** `sys.modules` (pop the module), purge `__pycache__` `.pyc` files for the module stem, and call `importlib.invalidate_caches()`.
  4. **Re-import** the module via `importlib.import_module()` (not `reload()` — the module was popped from `sys.modules`).
  5. **Verify** the new class `id()` differs from the old one (zombie reference check).
  6. **Atomic rollback** on failure: if `SyntaxError` or `ImportError`, restore the old entry and old `sys.modules` mapping.

- **`_reload_lock: threading.RLock`**: Reentrant lock protecting all mutation operations (`register`, `unregister`, `reload`). Uses `RLock` (not `Lock`) because `reload()` holds the lock while calling `importlib.import_module()`, which triggers `@register_backend` -> `register()` on the same thread. A plain `Lock` would deadlock.

- **`_backend_module_map: dict[str, str]`**: Maps canonical backend name -> fully-qualified module name. Populated automatically during `register()` from `backend_class.__module__`. Enables targeted `importlib.import_module()` without scanning all of `sys.modules`.

- **`get_watched_package_paths() -> list[str]`**: Returns absolute filesystem paths for `mathart.core` and `mathart.export` packages. Used by `BackendFileWatcher` to set up `watchdog` observers without hardcoded directory strings.

- **`module_to_backend_name(module_name) -> Optional[str]`**: Reverse lookup from module name to backend name. Used by the file watcher to determine which backend to reload when a `.py` file changes.

### 2. BackendFileWatcher Daemon (`mathart/core/backend_file_watcher.py`)

A non-blocking daemon file watcher for backend hot-reload:

- **Daemon Thread**: The `watchdog.Observer` runs as a daemon thread that automatically terminates when the main process exits. No `while True` blocking of the main thread (anti-Blocking Trap).

- **Debounce Scheduler** (`_DebouncedReloadScheduler`): Per-file debounce timer (default 400ms, configurable) coalesces rapid filesystem events from IDE save bursts. Only when the timer expires without further events does the reload callback fire (anti-Debounce Trap).

- **Targeted Reload**: Only the backend whose source file changed is reloaded. All other backends remain untouched (anti-State Wipeout Trap).

- **New Backend Detection**: When a new `.py` file appears in a watched directory that doesn't map to any existing backend, the watcher attempts `importlib.import_module()` to trigger `@register_backend`.

- **Fail-Safe**: If `reload()` raises (e.g., `SyntaxError`), the old backend version is atomically restored by the registry. The watcher logs the error and continues monitoring.

- **Introspection API**: `reload_history` property returns a list of reload event dicts. `reload_event` (`threading.Event`) enables test synchronization. `on_reload` callback fires after each reload attempt.

- **Context Manager**: `with BackendFileWatcher(registry) as watcher:` for automatic start/stop.

### 3. E2E Tests (`tests/test_backend_hot_reload.py`)

29 tests across 11 test groups, all running headless with zero external dependencies:

| Test Group | Count | Purpose |
|---|---|---|
| Registry Unregister | 4 | Targeted eviction, nonexistent returns False, preserves other backends, cleans module map |
| Registry Reload | 4 | Class identity change (zombie check), preserves other backends, atomic rollback on SyntaxError, nonexistent raises |
| Module Mapping | 3 | Register populates map, reverse lookup, unknown returns None |
| Watched Paths | 2 | Absolute paths, includes mathart.core |
| File Watcher Lifecycle | 3 | Start/stop, context manager, daemon thread |
| File Watcher E2E | 2 | Detects new backend file, hot-reloads modified backend |
| Debounce Scheduler | 2 | Coalesces rapid events, independent per-file |
| File Path Conversion | 2 | Valid path converts, non-Python rejected |
| Thread Safety | 1 | Concurrent register + lookup no errors |
| Full E2E Lifecycle | 2 | v1->v2 complete lifecycle, v1->v2->v3 sequential reloads |
| Reload Callback + Edge Cases | 4 | on_reload fires, empty file, double unregister, register after unregister |

### 4. Package Integration

- **`mathart/core/__init__.py`**: Added `BackendFileWatcher` to imports and `__all__`.

## Files Changed in SESSION-090

| File | Purpose |
|---|---|
| `mathart/core/backend_registry.py` | Atomic `unregister()`/`reload()` primitives, RLock, module map, watched paths |
| `mathart/core/backend_file_watcher.py` | **NEW** — Daemon file watcher with debounce (380 lines) |
| `mathart/core/__init__.py` | Added `BackendFileWatcher` export |
| `tests/test_backend_hot_reload.py` | **NEW** — 29 E2E tests (750 lines) |
| `research_notes_session090.md` | **NEW** — Research notes and implementation plan |
| `PROJECT_BRAIN.json` | SESSION-090 metadata, P1-MIGRATE-4 -> CLOSED |
| `SESSION_HANDOFF.md` | This file |

## Research Decisions That Were Enforced

### Erlang/OTP Hot Code Swapping

The gold standard for zero-downtime code replacement. Key principle enforced: **code state and singleton runtime state are strictly isolated**. The `BackendRegistry` singleton identity is preserved across reloads — only the internal `_backends` dict entries are surgically replaced. Long-lived objects (e.g., `MicrokernelOrchestrator`, `MicrokernelPipelineBridge`) that cache `self.backend_registry = get_registry()` continue to work because they hold a reference to the same singleton, and the singleton's internal state is mutated in-place.

### Eclipse OSGi Dynamic Module System

Strict lifecycle: **atomic unregister -> clean -> reload -> re-register**. The `reload()` method follows this exact sequence. The old entry is evicted before the new module is imported, preventing "dual registration" conflicts. If the new import fails, the old entry is atomically restored.

### Unity Domain Reloading / Unreal Live Coding

Background thread compilation at "safe points". The `BackendFileWatcher` runs on a daemon thread and uses debounce to ensure files are fully written before triggering reload. The reload itself is serialized by `_reload_lock` (RLock) to prevent concurrent mutation.

### Python `watchdog` + `importlib.reload` Best Practices

Three critical discoveries enforced in the implementation:

1. **`importlib.reload()` requires the module in `sys.modules`** — but we need to pop it to force fresh bytecode. Solution: use `sys.modules.pop()` + `importlib.import_module()` instead of `importlib.reload()`.

2. **`__pycache__` bytecode caching** — Python's import system serves `.pyc` files from `__pycache__/` even after `sys.modules.pop()`. Solution: purge `.pyc` files matching the module stem before re-import.

3. **`importlib.invalidate_caches()`** — Python's `FileFinder` caches directory listings. Without invalidation, `import_module()` may not see the updated file. Solution: call `importlib.invalidate_caches()` after `sys.modules.pop()` and `__pycache__` purge.

### Anti-Pattern Guards Enforced

| Anti-Pattern | Guard | Verification |
|---|---|---|
| Zombie Reference Trap | `id()` assertion on old vs new class | `test_reload_updates_class_identity`, `test_full_lifecycle_v1_to_v2`, `test_watcher_hot_reloads_modified_backend` |
| State Wipeout Trap | Targeted eviction, never `clear()` | `test_unregister_preserves_other_backends`, `test_reload_preserves_other_backends` |
| Blocking & Debounce Trap | Daemon thread + 400ms debounce | `test_watcher_daemon_thread`, `test_debounce_coalesces_rapid_events` |
| Deadlock Trap | RLock (reentrant) instead of Lock | `test_concurrent_register_and_lookup`, all reload tests |
| Bytecode Cache Trap | `__pycache__` purge + `invalidate_caches()` | `test_reload_updates_class_identity`, `test_multiple_sequential_reloads` |

## Testing and Validation

| Test command | Result |
|---|---|
| `pytest tests/test_backend_hot_reload.py -v` | **29 passed, 0 failed** |

## Architecture Micro-Adjustments for P1-AI-2E (Motion-Adaptive Keyframe Planning)

P1-AI-2E requires **high-frequency rendering and verification cycles** for high-nonlinearity action segments. The hot-reload ecosystem delivered in SESSION-090 is a direct force multiplier for this task. Here is what needs to be micro-adjusted:

### 1. Watcher Integration with MicrokernelOrchestrator

The `MicrokernelOrchestrator` currently caches `self.backend_registry = get_registry()` at init time. Because the hot-reload mutates the singleton's internal `_backends` dict (not the singleton reference itself), the orchestrator automatically picks up reloaded backends on the next `run_backend()` call. **No change needed** — this is by design.

However, for P1-AI-2E's high-frequency iteration loop, the orchestrator should be enhanced with:

- **Reload notification hook**: Wire `BackendFileWatcher.on_reload` callback to `MicrokernelOrchestrator` so it can invalidate any cached backend execution results when the underlying backend code changes mid-evolution.
- **Iteration counter reset**: When a backend is hot-reloaded during an evolution cycle, the orchestrator should reset the iteration counter for that backend's niche to avoid comparing results from different code versions.

### 2. ComfyUI Client Resilience

The `ComfyUIClient` (SESSION-087) maintains WebSocket connections and HTTP sessions. During hot-reload of the `ComfyUIPresetManager` or related backends:

- **Connection pooling**: The client should detect backend reload events and gracefully drain in-flight requests before switching to the new backend version.
- **Workflow cache invalidation**: ComfyUI workflow JSON is generated by backend code. If the backend is hot-reloaded, cached workflows must be invalidated.

### 3. Render Pipeline Safe Points

For P1-AI-2E's high-frequency render-verify loop:

- **Frame boundary safe points**: Hot-reload should only trigger between frame renders, not mid-frame. The `BackendFileWatcher` debounce (400ms) naturally provides this for typical frame rates (12-24 FPS), but explicit safe-point gating may be needed for batch renders.
- **Render result versioning**: Each render result should be tagged with the backend code version (commit hash or reload counter) so that results from different code versions are not mixed in quality comparisons.

### 4. Motion Complexity Metrics -> Keyframe Density

The hot-reload ecosystem enables rapid iteration on the keyframe density algorithm:

- **Modify `motion_adaptive_keyframe_planner.py`** -> save -> watcher detects change -> 400ms debounce -> targeted reload -> next render cycle uses new algorithm -> verify quality metrics.
- **No restart required** — the developer stays in the IDE and sees results in the next render cycle.

### 5. Suggested New Backend: `MotionAdaptiveKeyframeBackend`

Register a new backend (`BackendType.MOTION_ADAPTIVE_KEYFRAME`) that:
- Takes UMR motion clips as input.
- Computes per-frame nonlinearity scores (acceleration magnitude, angular velocity, contact events).
- Outputs a keyframe plan (`ArtifactFamily.KEYFRAME_PLAN`) with frame indices and SparseCtrl `end_percent` values.
- Hot-reloadable via the SESSION-090 ecosystem for rapid algorithm iteration.

## Recommended Next Priorities

| Priority | Recommendation | Reason |
|---|---|---|
| **Immediate** | **P1-AI-2E** | Motion-adaptive keyframe planning — hot-reload ecosystem is now the force multiplier |
| **High** | **P1-ARCH-4** | Architecture closure — registry migration work continues |
| **Medium** | **P1-AI-2D-SPARSECTRL** | SparseCtrl integration with orthographic render output |

## Updated Todo List

| ID | Status | Title |
|---|---|---|
| P1-MIGRATE-4 | **CLOSED** (SESSION-090) | Backend hot-reload ecosystem |
| P1-AI-2E | TODO | Motion-adaptive keyframe planning for high-nonlinearity segments |
| P1-ARCH-4 | TODO | Architecture closure — registry migration |
| P1-AI-2D-SPARSECTRL | SUBSTANTIALLY-CLOSED | SparseCtrl ComfyUI integration |
| P1-INDUSTRIAL-34C | CLOSED (SESSION-089) | Orthographic pixel render pipeline |
| P1-INDUSTRIAL-44A | TODO | Engine-ready export templates |
| P1-NEW-10 | SUBSTANTIALLY-ADVANCED | Production benchmark asset suite |

## Known Constraints and Non-Blocking Notes

| Constraint | Status |
|---|---|
| `watchdog` dependency | **Required** — added to project dependencies |
| Debounce window | **400ms default** — configurable via `debounce_seconds` parameter |
| RLock vs Lock | **RLock required** — `reload()` -> `import_module()` -> `@register_backend` -> `register()` is reentrant |
| `__pycache__` purge | **Per-module only** — does not clear entire `__pycache__` directory |
| Thread safety scope | **Mutation only** — read-only lookups remain lock-free for zero overhead |

## Files to Inspect First in the Next Session

| File | Why it matters |
|---|---|
| `mathart/core/backend_registry.py` | Registry singleton — `unregister()`, `reload()`, RLock, module map |
| `mathart/core/backend_file_watcher.py` | Daemon watcher — debounce, targeted reload, context manager |
| `tests/test_backend_hot_reload.py` | 29 E2E tests — the contract specification for hot-reload |
| `research_notes_session090.md` | Research notes: Erlang/OTP, OSGi, Unity, Python importlib |
| `mathart/core/pipeline_bridge.py` | Pipeline bridge — consumers of registry entries |
| `mathart/core/microkernel_orchestrator.py` | Orchestrator — cached registry reference, evolution loop |

## SESSION-089 Archive (Previous Handoff)

**SESSION-089** delivered the **Dead Cells-style 3D->2D Orthographic Pixel Render Pipeline**, closing P1-INDUSTRIAL-34C. `OrthographicPixelRenderBackend` self-registers via `@register_backend`, pure NumPy software rasterizer with edge-function triangle rasterization and Z-buffer, multi-pass Albedo/Normal/Depth extraction, hard-stepped cel-shading kernel, nearest-neighbor downscale. 22 E2E tests enforce anti-perspective, anti-bilinear, and anti-GUI red lines.

## References

[1]: https://www.erlang.org/doc/design_principles/release_handling "Erlang/OTP Release Handling — Hot Code Replacement"
[2]: https://docs.osgi.org/specification/osgi.core/8.0.0/framework.module.html "OSGi Core Release 8 — Module Layer Specification"
[3]: https://docs.unity3d.com/Manual/DomainReloading.html "Unity Manual — Domain Reloading"
[4]: https://docs.unrealengine.com/5.0/en-US/live-coding-in-unreal-engine/ "Unreal Engine 5 — Live Coding"
[5]: https://docs.python.org/3/library/importlib.html "Python importlib — The implementation of import"
[6]: https://python-watchdog.readthedocs.io/ "watchdog — Filesystem Events Monitoring"
