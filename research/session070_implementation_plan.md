# SESSION-070 Implementation Plan — P1-MIGRATE-1 Microkernel Motion Takeover

## Industrial / Academic Reference Alignment

### 1. EA Frostbite FrameGraph (GDC 2017) — Data-Driven Pipeline Scheduling

**Principle**: The pipeline must degenerate into a pure data-driven scheduler.
Execution nodes declare their inputs/outputs via manifests; the orchestrator
resolves dependencies and schedules execution automatically.

**Landing**: `pipeline.py._build_umr_clip_for_state()` currently calls lane
registries directly and returns raw `UnifiedMotionFrame` objects. This must be
refactored so motion generation is a registered backend that receives a context
dict and returns an `ArtifactManifest` containing a UMR motion clip record.

### 2. Mach/QNX Microkernel Philosophy — Minimal Kernel + IPC

**Principle**: The kernel only handles IPC (cross-plugin data contract transfer)
and lifecycle management. All domain logic lives in backends.

**Landing**: `MotionStateLaneRegistry` must be promoted from a motion-domain-only
side registry into a first-class `BackendMeta`-compliant plugin discovered by
`MicrokernelOrchestrator`. The new `UnifiedMotionBackend` wraps the lane registry,
accepts context dicts, and emits `ArtifactManifest` records.

### 3. Clean Architecture (Hexagonal) — Context-in / Manifest-out

**Principle**: No internal Python objects cross the system boundary. Outputs must
be structured `ArtifactManifest` records with explicit `artifact_family` and
`backend_type` discriminators.

**Landing**: A new `ArtifactFamily.MOTION_UMR` family is added. The motion
backend returns manifests with `motion_clip_json` output and structured metadata
(frame_count, fps, joint_channel_schema, etc.). `pipeline.py` consumes the
manifest through `manifest_to_legacy()` or directly reads the clip JSON.

### 4. Pixar OpenUSD Schema — Backward-Compatible Extension

**Principle**: Schema widening uses optional nested metadata and preserves
existing scalar property types.

**Landing**: `UnifiedMotionFrame` gains optional `z`, `velocity_z`,
`angular_velocity_3d` fields with `None` defaults. `MotionContactState` gains
an optional `manifold` metadata dict. `metadata["joint_channel_schema"]`
declares `2d_scalar` / `3d_euler`. All 2D consumers see unchanged defaults.

---

## Precise Implementation Steps

### Step 1: Add `MOTION_UMR` to ArtifactFamily + FAMILY_SCHEMAS

File: `mathart/core/artifact_schema.py`
- Add `MOTION_UMR = "motion_umr"` to `ArtifactFamily` enum
- Add schema: required_outputs=["motion_clip_json"], required_metadata=["frame_count", "fps", "joint_channel_schema"]

### Step 2: Add `UNIFIED_MOTION` to BackendType + aliases

File: `mathart/core/backend_types.py`
- Add `UNIFIED_MOTION = "unified_motion"` to `BackendType` enum
- Add aliases: `"motion_trunk"`, `"unified_motion_trunk"`

### Step 3: Extend `UnifiedMotionFrame` for 3D-safe schema (backward-compatible)

File: `mathart/animation/unified_motion.py`
- `MotionRootTransform`: add optional `z`, `velocity_z`, `angular_velocity_3d` (all default `None`)
- `MotionContactState`: add optional `manifold: Optional[dict]` (default `None`)
- `UnifiedMotionFrame`: add `joint_channel_schema` metadata enforcement in `__post_init__`
- All `to_dict()` methods: only serialize 3D fields when non-None (backward compat)

### Step 4: Create `UnifiedMotionBackend` as a first-class registered backend

File: `mathart/core/builtin_backends.py` (append)
- `@register_backend(BackendType.UNIFIED_MOTION, ...)`
- `validate_config()`: normalize motion context (state, frame_count, fps, etc.)
- `execute()`: use `MotionStateLaneRegistry` to build clip, serialize to JSON, return `ArtifactManifest`

### Step 5: Refactor `pipeline.py` to use microkernel bridge for motion

File: `mathart/pipeline.py`
- `_build_umr_clip_for_state()` now calls `self._microkernel_bridge.run_backend("unified_motion", context)`
- Deserialize the manifest back to `UnifiedMotionClip` for downstream processing
- Remove direct lane registry calls from pipeline trunk

### Step 6: Add `MotionStateRequest.config` namespace

File: `mathart/animation/unified_gait_blender.py`
- Add `config: dict[str, Any]` field to `MotionStateRequest`
- Lanes can use config for future parameter normalization

### Step 7: Update tests

- Extend `test_unified_motion.py` with 3D schema backward-compat tests
- Extend `test_character_pipeline.py` to verify manifest-based motion path
- Add `test_unified_motion_backend.py` for backend E2E
- Ensure 68/68 regression tests still pass

### Step 8: Update PROJECT_BRAIN.json and SESSION_HANDOFF.md
