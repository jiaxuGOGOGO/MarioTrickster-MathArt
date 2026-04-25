# Research Notes — SESSION-194 (P0-PIPELINE-INTEGRATION-CLOSURE)

> Synthesised industrial / academic references that drive the SESSION-194
> closure of the OpenPose × IPAdapter × ControlNet integration loop.
> All design decisions encoded into code below trace back to these anchors.

## 1. UE5 — `UAnimGraph` / `UAnimInstance` Decoupling Paradigm

Unreal Engine 5 separates **bone control flow** (the `UAnimGraph` node
network, evaluated on the worker thread via `FAnimInstanceProxy`) from the
**mesh rendering layer** (`USkeletalMeshComponent::FinalizeBoneTransforms`
and the GPU skinning pipeline). The graph emits a *pose buffer* (an
opaque, strongly-typed contract) that the renderer consumes — the renderer
never reaches back into the graph nor hard-codes which graph emitted the
buffer. (Refs: Epic dev docs on `Skeletons & Skeletal Meshes`,
`Post-Process AnimBP`; Unreal forums on AnimInstance proxy thread-safety.)

**Project mapping (SESSION-194):**

| UE5 concept                       | MarioTrickster-MathArt analogue                                                    |
|-----------------------------------|------------------------------------------------------------------------------------|
| `UAnimGraph` node network         | `mathart/animation` skeleton + `openpose_skeleton_renderer` (control flow)         |
| Pose buffer (strongly-typed)      | `BackendArtifact` with `artifact_family`, `backend_type`, on-disk PNG sequence dir |
| `USkeletalMeshComponent` renderer | ComfyUI workflow (`Depth/Normal/RGB` ControlNet apply chain)                       |
| Bus assembly (Anim → Render)      | `assemble_sequence_payload` / payload-assembly hook in `builtin_backends`          |

**Discipline imported:** the bone-control producer (OpenPose renderer)
must drop a *pure data artifact* on disk; the renderer-layer payload
assembler then *picks it up by contract* (strongly-typed dict with
`artifact_family="openpose_pose_sequence"`). No `if openpose_enabled:`
hardcoding in the trunk — the trunk only iterates `controlnet_guides`.

## 2. Apache Airflow — DAG Strict-Topology Computation Model

Airflow models pipelines as DAGs of `Operator`s; each operator advertises
`inputs` and `outputs`, and the scheduler **refuses to execute** a DAG
whose edges are not closed (a downstream operator with an unbound input
is a hard scheduler error, not a silent skip). The DAG is *the source of
truth*; the executor never branches on per-task booleans. (Refs: StreamFlow
2020, IJSRA 2025 MLOps survey, Airflow scheduler docs.)

**Project mapping (SESSION-194):** the ComfyUI JSON preset is treated as
an Airflow DAG. Every newly injected node (OpenPose `ControlNetLoader`,
`VHS_LoadImagesPath`, `ControlNetApplyAdvanced`, IPAdapter chain) **must**
have its `inputs` keys end-to-end bound, and the final `KSampler`'s
`positive` / `negative` inputs **must** terminate at the *last* link of
the conditioning chain. We additionally bake a `DAG closure assertion`
inside `validate_preset_topology_closure()` that walks the graph and
fails the build with `PipelineIntegrityError` if any node has a dangling
input reference (a "ghost edge" or "phantom node").

**Cross-validated with:** ComfyUI Wiki on `ControlNetApplyAdvanced` —
output ports `positive` / `negative` must be threaded into the next
`ControlNetApplyAdvanced.positive` / `.negative` (chained conditioning).
SESSION-194 enforces this chain order: `Normal -> Depth -> OpenPose ->
SparseCtrl-RGB -> KSampler`.

## 3. Spring Framework — IoC Container & Dependency Injection

Martin Fowler's `Inversion of Control Containers and the Dependency
Injection pattern` (2004) and the Spring reference describe how a
container resolves *Providers* and injects them at construction time,
removing the `new ConcreteThing()` call from business code. The goal is
*configurability without source-code modification* — the trunk sees only
abstract `Provider` slots; concrete implementations self-register at
import time. (Refs: Baeldung 2024, Sookocheff 2020, Spring 3.2 reference.)

**Project mapping (SESSION-194):** the existing `BackendRegistry`
(`mathart/core/backend_registry.py`) is the project's IoC container.
SESSION-194 introduces two new **Provider** modules that self-register
and are picked up by the existing registrar:

| Provider                    | `artifact_family`             | `backend_type`              | Registered via                                |
|-----------------------------|-------------------------------|-----------------------------|-----------------------------------------------|
| `openpose_pose_provider`    | `openpose_pose_sequence`      | `openpose_skeleton_render`  | `mathart/core/openpose_pose_provider.py` (new) |
| `identity_lock_provider`    | `ipadapter_identity_lock`     | `identity_hydration`        | `mathart/core/identity_lock_provider.py` (new) |

The trunk (`mass_production._node_anti_flicker_render` and
`builtin_backends.AntiFlickerRenderBackend._execute_live_pipeline`) calls
`backend_registry.iter_payload_hooks()` and lets every registered hook
*augment* the assembled payload with its strongly-typed contribution.
**No `if enable_openpose:` exists anywhere on the trunk.** This satisfies
red lines #1 / #3 (no main-bus mutation, no spaghetti `if/else`).

## 4. ControlNet OpenPose × IPAdapter × AnimateDiff (ComfyUI community)

References: hinablue 2024 (IPAdapter + OpenPose + AnimateDiff for stable
video), Reddit r/comfyui 2024 on OpenPose ControlNet semantic control,
ComfyUI Wiki `ControlNetApplyAdvanced`. Best-practice node ordering:

```
LoadOpenPoseSequence ──► ControlNetLoader (control_v11p_sd15_openpose) ──┐
                                                                          ▼
positive,negative ──► ControlNetApplyAdvanced (Normal) ──► ControlNetApplyAdvanced (Depth) ──► ControlNetApplyAdvanced (OpenPose) ──► (RGB SparseCtrl) ──► KSampler
```

OpenPose ControlNet's recommended strength under SD1.5 is `1.0` when the
upstream pose is the only reliable motion signal (Dummy Mesh degraded
case); Depth/Normal must be softened to ≤ 0.45 to avoid geometric over-
fit on a featureless cylinder (matches SESSION-193 arbitration constants).

## 5. Synthesis: SESSION-194 Code-Level Mandates

1.  **Preset topology hydration** — extend
    `mathart/assets/comfyui_presets/sparsectrl_animatediff.json` at the
    JSON-AST level (no string concatenation) to include:
    - `OpenPose ControlNetLoader` (`control_v11p_sd15_openpose.pth`)
    - `OpenPose VHS_LoadImagesPath` (directory `__OPENPOSE_SEQUENCE_DIR__`)
    - `OpenPose ControlNetApplyAdvanced` chained between Depth and SparseCtrl-RGB
    - `IPAdapter` quartet (`LoadImage`, `CLIPVisionLoader`,
      `IPAdapterModelLoader`, `IPAdapterApply`) wired into the model
      input of `KSampler`
    - DAG closure: every new node's `inputs` resolve to a node that
      exists in the workflow.
2.  **IoC providers** — register `openpose_pose_provider` and
    `identity_lock_provider` as `payload_hook` style entries in the
    backend registry; expose `iter_payload_hooks()`.
3.  **Active arbitrator** — call `arbitrate_controlnet_strengths` *before*
    payload dispatch when `detect_dummy_mesh()` is True, *after* the
    payload's preset has been hydrated with the OpenPose nodes (so the
    arbitrator can find them by `class_type` + `_meta.title`).
4.  **Fail-fast integrity** — replace silent `except Exception: pass` in
    the *integration / topology* code paths with `PipelineIntegrityError`
    (a subclass of `PipelineContractError`). Benign `pass` on close /
    decode of *external* errors (urllib HTTPError body) is preserved
    because they are *post-failure context-extraction*, not *integration*.
5.  **E2E intercept test** — `tests/test_e2e_payload_assembly.py` calls
    the real `assemble_sequence_payload` plus the new provider hooks,
    and asserts:
    - the OpenPose `ControlNetApplyAdvanced` node exists with `strength == 1.0`
    - the IPAdapter `IPAdapterApply` node exists with `weight == 0.85`
    - the OpenPose `VHS_LoadImagesPath.directory` points to a path that
      physically exists on disk and contains ≥ 1 PNG frame
    - DAG closure (`validate_preset_topology_closure`) returns no errors.
6.  **UX zero-degradation** — the existing
    `emit_industrial_baking_banner()` is invoked **before** the AI-render
    skip prompt in the live pipeline (USER_GUIDE Section 24).
