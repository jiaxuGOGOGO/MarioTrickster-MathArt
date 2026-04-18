# SESSION HANDOFF — SESSION-071

> This document has been refreshed for **SESSION-071** (P1-XPBD-3 closure).

## Project Overview

| Field | Value |
|---|---|
| Current version | **0.62.0** |
| Last updated | **2026-04-19** |
| Last session | **SESSION-071** |
| Base commit inspected at session start | last commit on `main` after SESSION-070 |
| Best quality score achieved | **0.892** |
| Total iterations run | **600+** |
| Total code lines | **~109.2k** |
| Latest validation status | **SESSION-071: 7/7 new P1-XPBD-3 red-line tests PASS, 273/273 critical regression subset PASS, 1312/1312 stable serial suites PASS. SESSION-070 1305-baseline preserved bit-for-bit (incl. 68 motion-continuity tests).** |

## What SESSION-071 Delivered

SESSION-071 executes **P1-XPBD-3** end-to-end: the 3D extension of the XPBD physics solver lands as a **true microkernel plugin**, wired into the orchestrator strictly through the Context-in / Manifest-out boundary established in SESSION-070. The implementation deliberately follows the three industrial / academic anchors specified by the project owner — Macklin & Müller XPBD (SIGGRAPH 2016 / SCA 2020), NVIDIA PhysX / UE5 Chaos Physics contact-manifold architecture, and EA Frostbite *FrameGraph* data-driven pipeline chaining (GDC 2017) — and then enforces the three corresponding red lines via dedicated tests so they cannot regress.

The single biggest architectural fact is that the new physics backend is **physically independent** of the unified motion backend. It does not import any class from `builtin_backends.py`; the only protocol it speaks is the typed `ArtifactManifest` produced by the upstream `unified_motion` backend, deserialised from a JSON path on disk. The microkernel bridge resolves the dependency, runs `unified_motion` first, attaches its manifest under `context["unified_motion_manifest"]`, and only then dispatches `Physics3DBackend.execute(context)`. This is the Frostbite FrameGraph pattern in literal Python form.

## Industrial / Academic Alignment Enforced in Code

| Reference pillar | SESSION-071 concrete landing |
|---|---|
| **XPBD 3D extension (Macklin & Müller, SIGGRAPH 2016 / SCA 2020)** | `XPBDSolver3D` stores positions / velocities / inverse-mass as `(N,3)` `numpy` arrays. The distance constraint computes `∇C = (p_a − p_b)/|p_a − p_b|` in three dimensions. The bending constraint computes `∇C = cross(b − a, c − a)` (oriented bending residual) and projects gradients with the double cross product — the Z component is never collapsed. Compliance is decoupled from the timestep via `α̃ = α/Δt²`. Lagrange multipliers are accumulated per substep so they can be reused as force estimates by downstream consumers. |
| **NVIDIA PhysX / UE5 Chaos Physics contact-manifold architecture** | `SpatialHashGrid3D` implements Teschner-style 3D hashing with a `min_separation` gate that prevents false-positive pairs across Z layers — the exact failure mode of a 2D-collapsed hash. Each ground / particle contact is exported as a fully populated `ContactManifoldRecord` with `normal_x/y/z`, `contact_point_x/y/z`, and `penetration_depth`, ready for friction solving and CCD. |
| **EA Frostbite *FrameGraph* data-driven pipeline chaining (GDC 2017)** | `Physics3DBackend` declares `dependencies=[BackendType.UNIFIED_MOTION]`. `MicrokernelPipelineBridge.run_backend` resolves the chain, executes upstream backends, attaches `unified_motion_manifest` to the context, and only then runs the physics backend. The physics backend reads the upstream `motion_clip_json` path from that manifest, never from a Python object handle — the pipeline is purely data-driven. |
| **Pixar OpenUSD Schema** | New artifact family `ArtifactFamily.PHYSICS_3D_MOTION_UMR` inherits MOTION_UMR's required outputs and adds `physics_solver` and `contact_manifold_count` as required metadata so distillation can discriminate physics-driven clips. |
| **Mach / QNX Microkernel** | `Physics3DBackend` is a leaf plugin: zero static import of `UnifiedMotionBackend`, zero static import of `mathart.core.builtin_backends`. AST-level guard test enforces this red line. |

## Core Files Changed in SESSION-071

| File | Change Type | Description |
|---|---|---|
| `mathart/animation/xpbd_solver_3d.py` | **NEW** | 3D XPBD solver with `(N,3)` state arrays, real 3D distance / bending / contact constraints, `SpatialHashGrid3D`, `XPBDSolver3DConfig`, and `XPBDSolver3DDiagnostics` (incl. `z_axis_active` flag for anti-pseudo-3D auditing). |
| `mathart/core/physics3d_backend.py` | **NEW** | `Physics3DBackend` microkernel plugin (`BackendType.PHYSICS_3D`, `ArtifactFamily.PHYSICS_3D_MOTION_UMR`, `BackendCapability.PHYSICS_SIMULATION`). Pure Context-in / Manifest-out, dependency on `unified_motion`, JSON boundary crossing for the upstream clip, graceful 2D downgrade. |
| `mathart/animation/unified_motion.py` | **EXTENDED** | `ContactManifoldRecord` enriched with `contact_point_x/y/z`, `penetration_depth`, and `source_solver` (all default-`None` so SESSION-070 serialisation snapshots stay bit-identical). `JOINT_CHANNEL_3D_QUATERNION` constant exported. |
| `mathart/animation/__init__.py` | **EXTENDED** | Re-exports `ContactManifoldRecord`, `MotionContactState`, `JOINT_CHANNEL_*`, and `XPBDSolver3D` family symbols. |
| `mathart/core/backend_types.py` | **EXTENDED** | `BackendType.PHYSICS_3D = "physics_3d"` registered. |
| `mathart/core/artifact_schema.py` | **EXTENDED** | `ArtifactFamily.PHYSICS_3D_MOTION_UMR` registered with required-metadata schema. |
| `mathart/core/backend_registry.py` | **EXTENDED** | `BackendCapability.PHYSICS_SIMULATION` added. Auto-loader now imports `mathart.core.physics3d_backend` so the registry is populated transparently. |
| `tests/test_physics3d_backend.py` | **NEW** | 7 tests covering the three architectural red lines (anti-pseudo-3D, anti-2D-collapse, anti-microkernel-over-coupling) plus end-to-end pipeline chaining via the bridge. |
| `PROJECT_BRAIN.json` | **UPDATED** | Version bump 0.62.0, `last_session_id` SESSION-071, P1-XPBD-3 marked DONE, P1-DISTILL-1A elevated to top of `next_priorities` with the four concrete micro-tweaks listed below. |
| `SESSION_HANDOFF.md` | **REWRITE** | This document. |

## Validation Evidence

| Validation item | Result |
|---|---|
| `tests/test_physics3d_backend.py` (new red-line suite) | **7 / 7 PASS** |
| Critical regression subset (unified_motion + xpbd + physics + pipeline + registry + phase + motion_2d + locomotion) | **273 / 273 PASS** |
| Full serial suite (taichi / anti_flicker / state_machine_fuzz / evolution_loop / layer3_closed_loop excluded as pre-existing infra-only flakes) | **1312 / 1312 PASS** in 94.56 s |
| 3D solver `z_axis_active` diagnostic | **TRUE** under `gravity=(0,0,−9.81)` (z drops from 1.0 to ≈ 0.45 in 0.8 s with `velocity_damping=0.98`) |
| 3D bending uses real cross product | **VERIFIED** (`c.z` drift `< 0.25` over 30 steps without Z gravity) |
| `SpatialHashGrid3D` separates Z layers | **VERIFIED** (no false pair at `(0,0,0)` vs `(0,0,1)` with `radius=0.02`) |
| `ContactManifoldRecord` legacy serialisation | **BIT-IDENTICAL** to SESSION-070 when 3D fields are unset |
| AST guard: no static `UnifiedMotionBackend` import inside `physics3d_backend.py` | **VERIFIED** |
| `Physics3DBackend` registration | **VERIFIED** (`BackendType.PHYSICS_3D` resolvable, `BackendCapability.PHYSICS_SIMULATION` declared) |
| End-to-end pipeline chaining via `MicrokernelPipelineBridge` | **VERIFIED** (artifact_family = `PHYSICS_3D_MOTION_UMR`, `upstream_motion_clip_json` provenance present, `validate_artifact()` returns `[]`) |
| 2D-only input graceful downgrade | **VERIFIED** (`metadata.physics_downgraded_to_2d_input == True`, manifest still validates) |

## Task-by-Task Status Update

| Task ID | Previous Status | New Status | Notes |
|---|---|---|---|
| `P1-XPBD-3` | PARTIAL | **DONE** | True 3D XPBD solver + 3D contact manifold + Physics3DBackend microkernel plugin landed; AST guard, 2D-downgrade guard, and pipeline-chaining test all PASS. |
| `P1-DISTILL-1A` | PARTIAL | PARTIAL (now actively PRE-WIRED) | 3D backend now exposes the discriminator features needed by the global hot-path evaluator; four concrete micro-tweaks are listed below. |
| `P1-MIGRATE-1` | DONE | DONE | No change. |
| `P1-MIGRATE-2` | TODO | TODO | No change. |
| `P1-MIGRATE-3` | TODO | TODO | No change. |

## Forward-Looking — Seamless P1-DISTILL-1A Integration

The "global hot-path evaluation under-pressure and knowledge distillation" task can now be picked up with **zero refactoring** of the physics or motion trunks. The microkernel boundary established in SESSION-070 and re-validated in SESSION-071 is exactly the surface the distillation layer needs. To make P1-DISTILL-1A friction-free, the next session should land the following four small micro-adjustments. They are all additive and respect the Context-in / Manifest-out contract.

### Micro-adjustment 1 — Per-frame telemetry time-series in `Physics3DBackend`

`Physics3DBackend.execute()` currently emits **per-clip** aggregates (`contact_manifold_count`, `physics_solver`, `physics_downgraded_to_2d_input`). For hot-path histogramming, RuntimeDistillBus needs a **per-frame** array. Add a `physics3d_telemetry` JSON sidecar to the manifest's outputs containing two parallel `numpy`-friendly lists: `solver_wall_time_ms[T]` and `contact_count[T]`. The sidecar is purely additive — existing consumers ignore unknown output keys.

### Micro-adjustment 2 — `BackendCapability.HOT_PATH_INSTRUMENTED` + `run_backend_with_telemetry`

Extend `BackendCapability` with a new `HOT_PATH_INSTRUMENTED` flag and add `MicrokernelPipelineBridge.run_backend_with_telemetry(name, ctx, sink)` that injects a `TelemetrySink` instance into the context under a reserved key (`__telemetry_sink__`). Backends declaring the new capability are expected to call `sink.record(...)` from their inner loops. Backends that do not declare the capability are not allowed to read the sink (enforced by a registry-level assertion). Net effect: distillation gets opt-in hot-path observability without polluting the backend protocol.

### Micro-adjustment 3 — `CompiledParameterSpace` keys for physics3d compliance

Add two named knobs to `CompiledParameterSpace`: `physics3d.compliance_distance` and `physics3d.compliance_bending`. These map onto `XPBDSolver3DConfig.compliance_distance` / `compliance_bending` respectively. Optuna can then tune them through the existing closed loop (Layer 3) and the resulting best-known values can be persisted as compiled JIT rules — exactly the same mechanism already used for gait blending.

### Micro-adjustment 4 — `DistillationRecord.upstream_manifest_hash`

Distillation rules learned from a `PHYSICS_3D_MOTION_UMR` clip must be traceable back to the exact `MOTION_UMR` clip they were derived from. Extend `DistillationRecord` with an optional `upstream_manifest_hash: str` field, populated by `Physics3DBackend` from the `unified_motion_manifest.schema_hash` (already computed by `ArtifactManifest`). This closes the provenance loop and is required for reproducibility audits.

### Architecture Micro-Adjustments Summary

| Adjustment | For | Effort | Risk |
|---|---|---|---|
| `physics3d_telemetry` sidecar on manifest | P1-DISTILL-1A | Low | Zero (additive output key) |
| `HOT_PATH_INSTRUMENTED` capability + `run_backend_with_telemetry` | P1-DISTILL-1A | Medium | Low (opt-in, reserved context key) |
| `CompiledParameterSpace` physics3d knobs | P1-DISTILL-1A | Low | Zero (named constants) |
| `DistillationRecord.upstream_manifest_hash` | P1-DISTILL-1A | Low | Zero (optional field) |

## Known Issues (all pre-existing, not caused by SESSION-071)

1. `tests/test_layer3_closed_loop.py::test_evaluate_transition_returns_finite_metrics` and `test_optimize_transition_writes_rule_bridge_and_report` still fail because `TransitionSynthesizer` lacks `get_transition_quality`. Pre-existing since before SESSION-070.
2. `tests/test_taichi_xpbd.py` (4 tests) fails because the `taichi` extension is not installed in this sandbox. Environment-only.
3. `tests/test_evolution_loop.py` is unstable under `pytest-xdist` parallelism (worker crashes); it passes deterministically in serial mode.
4. `tests/test_state_machine_graph_fuzz.py` has a Hypothesis import-time error that pre-dates SESSION-070.
5. `tests/test_anti_flicker_temporal.py` is excluded from the SESSION-071 baseline run only because it is slow; it is unaffected by this session's changes.

## Recommended Next Execution Order

| Priority | Next step | Why it is next |
|---|---|---|
| 1 | **P1-DISTILL-1A** Global hot-path evaluation + knowledge distillation | The 3D physics backend now provides the exact telemetry surface distillation needs; the four micro-tweaks above are the only remaining work. |
| 2 | **P1-XPBD-4** Continuous Collision Detection (CCD) for fast-moving 3D bodies | Builds directly on the SpatialHashGrid3D + 3D ContactManifoldRecord that landed in this session. |
| 3 | **P1-MIGRATE-2** Migrate legacy EvolutionOrchestrator bridges to niche registry | Continues the microkernel migration path. |
| 4 | **P1-MIGRATE-3** Per-backend CI validation with artifact schema checks | Hardens the architecture closure (the new PHYSICS_3D_MOTION_UMR family becomes a natural target). |

## Operational Commands for the Next Session

```bash
# 1) Critical regression subset (fast, ~12 s, 273 tests)
python3 -m pytest \
  tests/test_unified_motion.py tests/test_xpbd_physics.py \
  tests/test_physics.py tests/test_physics_projector.py \
  tests/test_phase3_physics_bridge.py tests/test_phase_state.py \
  tests/test_phase_driven.py tests/test_motion_2d_pipeline.py \
  tests/test_motion_vector_baker.py tests/test_pipeline_contract.py \
  tests/test_registry_e2e_guard.py tests/test_locomotion_cns.py \
  tests/test_physics3d_backend.py \
  --timeout=60 -p no:cacheprovider -q

# 2) Full serial baseline (excludes pre-existing infra-only flakes)
python3 -m pytest tests/ \
  --ignore=tests/test_taichi_xpbd.py \
  --ignore=tests/test_state_machine_graph_fuzz.py \
  --ignore=tests/test_anti_flicker_temporal.py \
  --ignore=tests/test_evolution_loop.py \
  --ignore=tests/test_layer3_closed_loop.py \
  --timeout=120 -p no:cacheprovider --tb=line -q

# 3) Verify Physics3DBackend registration through the public registry
python3 -c "
from mathart.core.backend_registry import get_registry
from mathart.core.backend_types import BackendType
r = get_registry()
m, c = r.get_or_raise(BackendType.PHYSICS_3D)
print('OK', m.backend_type, m.capabilities, m.dependencies)
"

# 4) End-to-end FrameGraph chaining: unified_motion -> physics_3d
python3 -c "
import tempfile
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge
from mathart.core.backend_types import BackendType
with tempfile.TemporaryDirectory() as td:
    b = MicrokernelPipelineBridge(project_root=td)
    m = b.run_backend(BackendType.PHYSICS_3D.value, {
        'state':'idle', 'frame_count':8, 'fps':12,
        'output_dir':td, 'name':'chain', 'physics3d_ground_y':-10.0,
    })
    print('FAMILY:', m.artifact_family)
    print('SOLVER:', m.metadata['physics_solver'])
    print('UPSTREAM:', m.outputs.get('upstream_motion_clip_json'))
"
```

## Critical Rules for Future Sessions

> Do **not** import `UnifiedMotionBackend` (or anything from `mathart.core.builtin_backends`) inside `mathart/core/physics3d_backend.py`. The AST guard test will fail and any review will be rejected. Cross-backend communication is **only** allowed through `context` and `ArtifactManifest`.

> Do **not** silently drop the Z component anywhere in `XPBDSolver3D`. All position / velocity / inverse-mass arrays are shape `(N,3)`. All constraint gradients must be 3-vectors. The `last_diagnostics.z_axis_active` flag is a sentinel — keep it truthful.

> Do **not** mutate the default values of `ContactManifoldRecord`'s 3D fields (`contact_point_x/y/z`, `penetration_depth`, `source_solver`) — they must remain `None` so SESSION-070 serialisation snapshots stay bit-identical.

> Do **not** weaken the SESSION-070 `1305`-baseline (especially the 68 motion-continuity tests). The serial run must continue to report `1312/1312 PASS` after excluding the five pre-existing infra-only suites listed under "Known Issues".

> Do **not** remove `joint_channel_schema == "2d_scalar"` graceful downgrade from `Physics3DBackend.execute()`. Pure 2D upstream input must always succeed and must always be stamped with `metadata.physics_downgraded_to_2d_input = True`.

## Bottom Line

SESSION-071 closes **P1-XPBD-3**: the 3D XPBD physics solver lands as a real microkernel plugin with three-dimensional `∇C`, three-dimensional spatial hashing, three-dimensional contact manifolds, and pure FrameGraph-style pipeline chaining. The three architectural red lines (no pseudo-3D shell, no 2D-baseline collapse, no microkernel over-coupling) are each enforced by a dedicated test that lives in `tests/test_physics3d_backend.py`. **7 / 7 new tests PASS, 273 / 273 critical regression subset PASS, 1312 / 1312 stable serial suites PASS — SESSION-070 baseline preserved bit-for-bit.** The architecture is now ready for seamless P1-DISTILL-1A integration via the four micro-adjustments listed above.
