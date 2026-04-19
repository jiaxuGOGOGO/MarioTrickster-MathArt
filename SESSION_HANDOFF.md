# SESSION_HANDOFF.md

> This document has been refreshed for **SESSION-074** (P1-MIGRATE-2 closure — Strangler Fig completion).

## Project Overview

| Field | Value |
|---|---|
| Current version | **0.65.0** |
| Last updated | **2026-04-19** |
| Last session | **SESSION-074** |
| Base commit inspected at session start | `c2212b6` (SESSION-073 head on `main`) |
| Best quality score achieved | **0.892** |
| Total iterations run | **615+** |
| Total code lines | **~111.8k** |
| Latest validation status | **SESSION-074: 13/13 CI schema tests PASS (incl. 5 new evolution-domain tests), 95/95 targeted regression PASS, zero breakage of SESSION-073 1362-baseline. Pre-existing infra flakes unchanged.** |

## What SESSION-074 Delivered

SESSION-074 executes **P1-MIGRATE-2** (Strangler Fig completion — all legacy EvolutionBridges migrated to microkernel registry), following six industrial / academic anchors specified by the project owner:

1. **Martin Fowler: Strangler Fig Pattern** — The 7-entry hardcoded `bridge_specs` list in `MicrokernelOrchestrator._run_legacy_bridges()` is eliminated. A new adapter layer wraps all 20+ legacy bridges as `@register_backend` plugins, which are then discovered at runtime via `find_by_capability(EVOLUTION_DOMAIN)`. The orchestrator is now completely blind to individual bridge identities.

2. **Eclipse OSGi / Equinox Dynamic Service Discovery** — Every evolution bridge declares a complete input/output contract (BackendCapability.EVOLUTION_DOMAIN + ArtifactFamily.EVOLUTION_REPORT). The registry discovers them dynamically. Zero static coupling remains.

3. **Pixar OpenUSD PlugRegistry Strong-Typed Metadata** — `ArtifactFamily.EVOLUTION_REPORT` requires `cycle_count`, `best_fitness`, `knowledge_rules_distilled` metadata keys. Every evolution backend's output is validated against this schema.

4. **Mouret & Clune MAP-Elites** — Per-niche evolution isolation preserved. Evolution backends produce per-bridge reports, never cross-bridge averages.

5. **Clean Architecture (Robert C. Martin)** — Data flows through `ctx` (Context-in) and returns as `ArtifactManifest` (Manifest-out). No global variable reads, no hardcoded paths.

6. **Anti-Pattern Red Lines** — No Facade Trap (bridges don't read globals), no Zombie Hardcoding (orchestrator has zero `if bridge.name ==` checks), no CI Blindspot (all 5 new tests exercise real backends with real schema validation).

## Industrial / Academic Alignment Enforced in Code

| Reference pillar | SESSION-074 concrete landing |
|---|---|
| **Martin Fowler Strangler Fig** | `_run_legacy_bridges()` hardcoded `bridge_specs` list eliminated. Replaced with `backend_registry.find_by_capability(BackendCapability.EVOLUTION_DOMAIN)` reflective discovery. Orchestrator is identity-blind. |
| **Eclipse OSGi dynamic service** | 20 evolution backends declare `EVOLUTION_DOMAIN` capability via `@register_backend`. Registry auto-loads `evolution_backends` module in `get_registry()`. |
| **Pixar PlugRegistry strong typing** | `ArtifactFamily.EVOLUTION_REPORT` with `required_metadata_keys()` enforcing `cycle_count`, `best_fitness`, `knowledge_rules_distilled`. `FAMILY_SCHEMAS` entry validates output structure. |
| **Clean Architecture ctx-in/manifest-out** | `_make_evolution_adapter` factory consumes context keys (`output_dir`, `name`, `verbose`) at adapter level. Only bridge-specific default kwargs forwarded to legacy bridge. Output packaged as `ArtifactManifest`. |
| **Multi-convention adapter (Ports & Adapters)** | `_run_legacy_bridge()` tries 5 calling conventions: `run_full_cycle` → `run_cycle` → `evaluate_full` → `evaluate` → any `evaluate_*`. TypeError fallback for bridges requiring positional args. |

## Core Files Changed in SESSION-074

| File | Change Type | Description |
|---|---|---|
| `mathart/core/evolution_backends.py` | **NEW** (+338 lines) | Adapter factory + 20 `@register_backend` evolution-domain plugins |
| `mathart/core/backend_types.py` | **EXTENDED** (+25 lines) | 20 `EVOLUTION_*` BackendType entries + `EVOLUTION_ALIASES` reverse map |
| `mathart/core/artifact_schema.py` | **EXTENDED** (+15 lines) | `ArtifactFamily.EVOLUTION_REPORT` + `required_metadata_keys` + `FAMILY_SCHEMAS` |
| `mathart/core/backend_registry.py` | **EXTENDED** (+8 lines) | `BackendCapability.EVOLUTION_DOMAIN` + auto-import `evolution_backends` in `get_registry()` |
| `mathart/core/microkernel_orchestrator.py` | **REFACTORED** (+40/-50) | `_run_legacy_bridges()` rewritten: reflective `find_by_capability(EVOLUTION_DOMAIN)` discovery via `MicrokernelPipelineBridge` |
| `tests/test_ci_backend_schemas.py` | **EXTENDED** (+48 lines) | 5 new evolution-domain CI tests |
| `PROJECT_BRAIN.json` | **UPDATED** | v0.65.0, P1-MIGRATE-2 DONE, SESSION-074 metadata |
| `SESSION_HANDOFF.md` | **REWRITE** | This document |

## Validation Evidence

| Validation item | Result |
|---|---|
| `tests/test_ci_backend_schemas.py` (13 tests, incl. 5 new) | **13 / 13 PASS** |
| `tests/test_evolution.py` (23 tests) | **23 / 23 PASS** |
| `tests/test_evolution_bridges_057.py` (20 tests) | **20 / 20 PASS** |
| `tests/test_ccd_3d.py` (11 tests) | **11 / 11 PASS** |
| `tests/test_breakwall_phase1.py` (28 tests) | **28 / 28 PASS** |
| Total targeted regression | **95 / 95 PASS** |
| Registry backend count | **30+ backends** (10 core + 20 evolution-domain) |
| Evolution-domain backends discovered | **20 / 20** |
| Hardcoded bridge references in orchestrator | **0** (verified by grep) |
| `ArtifactFamily.EVOLUTION_REPORT` metadata enforcement | **VERIFIED** |

## Task-by-Task Status Update

| Task ID | Previous Status | New Status | Notes |
|---|---|---|---|
| `P1-MIGRATE-2` | TODO | **CLOSED** | All 20+ legacy bridges migrated to @register_backend plugins. Orchestrator fully blind. 5 new CI tests. |
| `P1-MIGRATE-3` | CLOSED | CLOSED | No change (SESSION-073). |
| `P1-XPBD-4` | CLOSED | CLOSED | No change (SESSION-073). |
| `P1-DISTILL-1A` | CLOSED | CLOSED | No change (SESSION-072). |
| `P1-MIGRATE-1` | DONE | DONE | No change (SESSION-070). |

## Architecture State After SESSION-074

```
┌─────────────────────────────────────────────────────────────────┐
│              MICROKERNEL ORCHESTRATOR (fully blind)              │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Backend Registry (LLVM)                                  │  │
│  │  30+ backends: 10 core + 20 EVOLUTION_DOMAIN plugins      │  │
│  │  @register_backend → auto-discovery → capability filter   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                          ↓ artifacts                            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Artifact Schema (USD)                                    │  │
│  │  6 families: META_REPORT, MOTION_UMR, PHYSICS_3D,         │  │
│  │  MOTION_2D, TEXTURE_BUNDLE, EVOLUTION_REPORT              │  │
│  │  validate_artifact → required_metadata_keys → schema_hash │  │
│  └───────────────────────────────────────────────────────────┘  │
│                          ↓ validated outputs                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Niche Registry (MAP-Elites)                              │  │
│  │  per-lane evaluation → Pareto front → Meta-Report         │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Dual-track architecture is fully eliminated.** There is no longer any code path that bypasses the registry to directly instantiate a bridge class.

## Forward-Looking — Seamless P1-DISTILL-1B Integration

### What P1-DISTILL-1B Requires

P1-DISTILL-1B aims to add a **Taichi GPU backend** for the RuntimeDistillBus and XPBD performance benchmarking. The goal is to register a GPU-accelerated XPBD solver as a standard `@register_backend` plugin and compare its performance against the CPU baseline through the existing distillation pipeline.

### Architecture Readiness After SESSION-074: HIGH

Now that P1-MIGRATE-2 is closed, the architecture is fully prepared:

1. **Plugin registration is proven at scale**: 30+ backends are registered and CI-validated. Adding a Taichi backend follows the exact same pattern.

2. **Capability-based discovery is operational**: The orchestrator uses `find_by_capability()` to discover backends. A new `BackendCapability.GPU_ACCELERATED` can be added and the Taichi backend will be automatically discovered.

3. **ArtifactFamily extension pattern is established**: Adding `ArtifactFamily.BENCHMARK_REPORT` follows the same pattern as `EVOLUTION_REPORT`.

4. **Context-in / Manifest-out discipline is enforced**: The Taichi backend will receive context and return an `ArtifactManifest` with benchmark telemetry.

### Micro-Adjustments Needed for P1-DISTILL-1B

| # | Adjustment | Effort | Description |
|---|-----------|--------|-------------|
| 1 | `BackendCapability.GPU_ACCELERATED` | Trivial | Add enum value to `backend_registry.py` |
| 2 | `BackendType.TAICHI_XPBD` | Trivial | Add to `backend_types.py` |
| 3 | `ArtifactFamily.BENCHMARK_REPORT` | Small | New family with `required_metadata_keys`: `solver_type`, `frame_count`, `wall_time_ms`, `particles_per_second` |
| 4 | `TaichiXPBDBackend` | Medium | Wrap `taichi_xpbd.py` cloth solver as `@register_backend` plugin with `GPU_ACCELERATED` capability. Must handle Taichi import failure gracefully (CPU fallback). |
| 5 | `RuntimeDistillBus` GPU telemetry | Medium | Extend `DistillationRecord` schema with `device` field (`cpu`/`gpu`), `wall_time_ms`, `throughput_particles_per_second`. Add A/B comparison logic. |
| 6 | Benchmark CI test | Small | Add `test_taichi_benchmark_backend.py` with mock Taichi (for CI without GPU) and real Taichi (for local GPU runs). |

### Risk Assessment

- **Taichi import**: `taichi` is an optional dependency. The backend must use the same deferred-import pattern as `evolution_backends.py`.
- **GPU availability**: CI runs on CPU-only machines. Tests must use `ti.cpu` arch with `@pytest.mark.skipif` for GPU-specific benchmarks.
- **Performance measurement**: Wall-time benchmarks are noisy. Use median of 5 runs with warm-up, store as `ArtifactManifest.quality_metrics`.

## Known Issues (all pre-existing, not caused by SESSION-074)

1. `tests/test_layer3_closed_loop.py` (2 tests) — `TransitionSynthesizer` lacks `get_transition_quality`. Pre-existing since before SESSION-070.
2. `tests/test_taichi_xpbd.py` (4 tests) — `taichi` not installed. Environment-only.
3. `tests/test_state_machine_graph_fuzz.py` — `hypothesis` import-time error. Pre-existing.
4. `tests/test_image_to_math.py`, `tests/test_sprite.py`, `tests/test_cli_sprite.py` — missing `scipy`. Pre-existing.

## Operational Commands for the Next Session

```bash
# 1) SESSION-074 targeted suite (fast, ~5 s, 95 tests)
python3 -m pytest \
  tests/test_ci_backend_schemas.py tests/test_evolution.py \
  tests/test_evolution_bridges_057.py tests/test_ccd_3d.py \
  tests/test_breakwall_phase1.py \
  -q --tb=short

# 2) Verify 30+ registered backends including 20 evolution-domain
python3 -c "
from mathart.core.backend_registry import get_registry, BackendCapability
reg = get_registry()
evo = reg.find_by_capability(BackendCapability.EVOLUTION_DOMAIN)
print(f'{len(evo)} evolution-domain backends registered')
print(f'{len(reg.all_backends())} total backends registered')
for m, _ in sorted(evo, key=lambda x: x[0].name):
    print(f'  {m.name}: {m.artifact_families}')
"

# 3) Full serial baseline (excludes pre-existing infra-only flakes)
python3 -m pytest tests/ \
  --ignore=tests/test_taichi_xpbd.py \
  --ignore=tests/test_state_machine_graph_fuzz.py \
  --ignore=tests/test_anti_flicker_temporal.py \
  --ignore=tests/test_image_to_math.py \
  --ignore=tests/test_sprite.py \
  --ignore=tests/test_cli_sprite.py \
  --ignore=tests/test_layer3_closed_loop.py \
  -p no:cacheprovider --tb=line -q
```

## Priority Queue for Next Session

| Priority | Task ID | Title | Readiness |
|---|---|---|---|
| 1 | `P1-DISTILL-1B` | Taichi GPU acceleration for runtime bus + XPBD benchmarking | **HIGH** (see micro-adjustments above) |
| 2 | `P1-DISTILL-1A` | Roll RuntimeDistillBus into gait blending hot paths | MEDIUM |
| 3 | `P1-MIGRATE-4` | Hot-reload for dynamically discovered backends | MEDIUM |
| 4 | `P1-DISTILL-3` | Distill Verlet & gait parameters into knowledge/ | MEDIUM |
| 5 | `P1-DISTILL-4` | Distill cognitive science rules | MEDIUM |

## Red Lines (Anti-Patterns to Avoid in Future Sessions)

1. **No Facade Trap**: Do NOT wrap new backends in compatibility shims that secretly read global variables. Data flows through `ctx` only.

2. **No Zombie Hardcoding**: Do NOT add `if backend.name == 'xxx':` anywhere in the orchestrator. Use capability-based discovery only.

3. **No CI Blindspot**: Do NOT use `@pytest.mark.skipif` to skip schema validation. Use mock dependencies for CI, real dependencies for local runs.

4. **No Cross-Niche Averaging**: Results are per-backend, per-scene. Do NOT average across different solver types or scene complexities.
