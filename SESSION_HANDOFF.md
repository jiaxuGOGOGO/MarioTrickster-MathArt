## Goal & Status

**Objective**: close **P2-SPINE-PREVIEW-1** by landing a complete, engine-independent preview pipeline that reads exported **Spine JSON**, solves hierarchical bone transforms through a tensorized FK path, renders the skeleton headlessly, and emits `.mp4` / `.gif` verification assets suitable for fracture / topology debugging.

**Status**: **CLOSED**.

The landed implementation introduces two physically separated layers. `mathart/animation/spine_preview.py` is the pure algorithm module: it parses Spine JSON from disk, samples translate / rotate / scale timelines, builds `local_matrices[F, B, 3, 3]`, propagates `world_matrices[F, B, 3, 3]` depth-by-depth using broadcast `np.matmul`, and converts solved bone segments into headless preview media. `mathart/core/spine_preview_backend.py` is the thin registry adapter that normalizes context, synthesizes a demo Spine clip when CI supplies a placeholder path, and returns a strongly typed `ArtifactManifest` with family `ANIMATION_PREVIEW`.

## Research Alignment Audit

The implementation is deliberately constrained by the external research captured in `research/session125_spine_preview_research_notes.md`. The following table records the exact rule each source imposed on the code path.

| Reference pillar | Practical rule adopted in code | Why it matters here |
|---|---|---|
| Spine runtime skeleton hierarchy [1] | Bone transforms are treated as parent-relative data, and preview rendering is based on world transforms rather than raw local channels. | Without hierarchical accumulation, the preview would draw disconnected or physically wrong poses. |
| Formal FK composition from CSE169 [2] | The solver is split into local-matrix construction and world-matrix concatenation. | This separation makes the FK path debuggable, testable, and easy to tensorize. |
| NumPy stacked-matrix semantics [3] | World propagation is executed with broadcast `np.matmul` over `[..., 3, 3]` matrices rather than per-frame scalar loops. | This is the key performance lever that keeps the FK hot path vectorized. |
| OpenCV headless video writing [4] | Preview export writes files via `cv2.VideoWriter`; no display surface is involved. | CI, remote workers, and server-side jobs cannot depend on a local GUI. |
| Matplotlib non-interactive backend guidance [5] | The preview path is explicitly headless-only, and GUI calls are forbidden by design. | This locks the implementation away from blocking window APIs and keeps it production-safe. |

> “A skeleton has a hierarchy of bones. Bone transforms are applied relative to the parent bone and combined to produce world transforms used for rendering.” This Spine runtime principle directly determined the world-matrix propagation contract in the new solver. [1]

## Architecture Decisions Locked

The first locked decision is that **the math module and the backend plugin remain physically separated**. `mathart/animation/spine_preview.py` contains the portable algorithm core only; it knows nothing about the registry, manifests, or orchestration layer. `mathart/core/spine_preview_backend.py` owns all plugin concerns such as context normalization, synthetic demo self-healing, and artifact packaging. This mirrors the architectural split previously used by `LevelTopologyBackend` and `Unity2DAnimBackend`.

The second locked decision is that **the FK hot path is tensorized across frames**. Metadata preparation is still allowed to iterate over bones while parsing channels, but world-matrix propagation never performs a nested `for frame in ... / for bone in ...` scalar solve. Instead, the backend precomputes `parent_indices` and depth levels, and each depth layer is resolved by batched `np.matmul`, which matches NumPy’s stacked-matrix semantics [3].

The third locked decision is that **the preview path is permanently headless**. `cv2.imshow()` and `cv2.waitKey()` are banned from the production preview module. Video is written via `VideoWriter`, GIF is written via Pillow, and the renderer projects all coordinates into image space with an explicit Y-axis inversion so the skeleton remains visually upright.

The fourth locked decision is that **the backend survives CI smoke tests with synthetic input**. When `spine_json_path` is absent or contains the CI placeholder string, `validate_config()` generates a deterministic demo Spine JSON clip on disk and uses it as the preview source. This guarantees that the backend remains executable through the registry bridge even when no upstream motion pipeline has run.

## Code Change Table

| File | Action | Details |
|---|---|---|
| `mathart/animation/spine_preview.py` | Added | `SpineJSONTensorSolver`, `HeadlessSpineRenderer`, `SpinePreviewClip`, `SpinePreviewRenderResult`, and `create_demo_spine_json()`; implements Spine JSON parsing, timeline interpolation, tensorized FK, Y-flipped screen projection, and MP4 / GIF export. |
| `mathart/core/spine_preview_backend.py` | Added | `SpinePreviewBackend` registry plugin with synthetic-demo self-healing, typed manifest emission, and preview diagnostics packaging. |
| `tests/test_spine_preview.py` | Added | 6 targeted regression tests covering FK tensor shape integrity, media export, backend manifest validity, registry discovery, non-GUI headless path, and subsecond multi-frame preview performance. |
| `research/session125_spine_preview_research_notes.md` | Added | Finalized external-alignment memo covering Spine hierarchy rules, FK composition, NumPy batch matmul, and headless render constraints. |
| `mathart/core/backend_types.py` | Modified | Added `SPINE_PREVIEW = "spine_preview"` plus aliases (`animation_preview`, `spine_json_preview`, `spine_preview_backend`, `spine_headless_preview`). |
| `mathart/core/artifact_schema.py` | Modified | Added `ANIMATION_PREVIEW` artifact family and required metadata keys (`bone_count`, `frame_count`, `fps`, `canvas_size`, `render_time_ms`, `animation_name`). |
| `mathart/core/backend_registry.py` | Modified | Added `spine_preview_backend` to the builtin auto-load sequence so the microkernel discovers it without trunk branching. |
| `tests/conftest.py` | Modified | Added `mathart.core.spine_preview_backend` to `_BUILTIN_BACKEND_MODULES` for bootstrap-safe registry restoration. |
| `tests/test_ci_backend_schemas.py` | Modified | Extended required-metadata coverage assertions to include the new `ANIMATION_PREVIEW` family. |
| `PROJECT_BRAIN.json` | Updated | SESSION-125 state, validation, and resolved-issue metadata appended. |
| `SESSION_HANDOFF.md` | Updated | Replaced prior handoff with the present SESSION-125 closure summary and next-step guidance. |

## White-Box Validation Closure

Local touched-lane validation is complete.

| Validation command / scope | Result |
|---|---|
| `python3.11 -m pytest -q tests/test_spine_preview.py` | **6/6 PASS** |
| `python3.11 -m pytest -q tests/test_spine_preview.py tests/test_ci_backend_schemas.py -k 'test_required_metadata_keys_coverage'` | **1/1 selected PASS** |
| `python3.11 tmp_validate_spine_preview_backend.py` | **PASS** — `MicrokernelPipelineBridge.run_backend("spine_preview", ctx)` returned a valid `ANIMATION_PREVIEW` manifest and materialized `.mp4`, `.gif`, diagnostics JSON, and the synthesized Spine JSON input. |

The validation matrix closes four practical red lines simultaneously. First, it proves that the solver produces the expected `world_matrices[F, B, 3, 3]` and world-space bone endpoints. Second, it proves that the renderer exports media without any GUI dependency. Third, it proves that the backend self-heals under CI placeholder input. Fourth, it proves that a 180-frame preview workload remains subsecond in the touched performance lane.

## Red-Line Guards

| Guard | Enforcement |
|---|---|
| **Anti-GUI-Blocking** | Production preview code never depends on `cv2.imshow()` / `cv2.waitKey()`; tests pin the headless writer path. |
| **Anti-Scalar-Frame-Loop** | FK world propagation is executed with batched `np.matmul` over matrix stacks rather than nested Python frame/bone scalar solves. |
| **Anti-Coordinate-Inversion** | Screen projection explicitly flips Y so skeletons do not render upside-down in image coordinates. |
| **Anti-CI-Placeholder-Failure** | Backend `validate_config()` synthesizes a minimal Spine JSON demo clip when upstream data is missing. |

## Practical Implication for the Architecture Roadmap

SESSION-125 closes the missing **engine-independent animation verification** layer between the existing Spine JSON exporter and downstream engine-native importers. The project can now export motion to Spine JSON and immediately produce a visual proof artifact without requiring Spine Editor, Unity, or any browser-based runtime. This materially reduces the debugging distance when validating parent-child topology, bone lengths, or unexpected discontinuities in 2D projection output.

This also improves the broader architecture closure path. Because the previewer consumes a disk-backed Spine JSON file and returns a typed `ArtifactManifest`, it obeys the repository’s Context-in / Manifest-out microkernel discipline. As a result, future pipelines can insert `spine_preview` as a pure plugin stage after `motion_2d` export or any later Spine-compatible serializer without touching trunk orchestration code.

## Recommended Next Steps

The highest-value immediate follow-up is to **wire `spine_preview` behind the existing motion export flow**, so a standard motion generation job can optionally emit both Spine JSON and its preview media in one pass.

The second follow-up is to **extend the preview diagnostics from skeleton-only line rendering to slot / attachment overlays**. The current solver is already world-transform complete; the next increment is to project slot rectangles, attachment pivots, and draw-order layers so visual verification can catch not only bone fracture but also attachment drift.

The third follow-up is to **formalize a real-time transport contract for P2-REALTIME-COMM-1**. The current previewer already computes stable world-space packetizable data (`origin_xy`, `tip_xy`, `rotation_deg`, `parent_index`). The natural next step is to define a compact per-frame serialization schema and ship that over WebSocket / shared memory for live remote preview instead of re-encoding full video frames.

## P2-REALTIME-COMM-1 Preparation: Serialization Interface

With the headless preview lane now stable, the next architectural extension toward **P2-REALTIME-COMM-1** should avoid sending rasterized frames whenever low latency matters. Instead, the backend should expose a structured frame packet stream with one record per bone and frame. The minimal packet contract should include `frame_index`, `time_s`, `bone_index`, `bone_name`, `parent_index`, `origin_x`, `origin_y`, `tip_x`, `tip_y`, and `rotation_deg`. This packet shape is sufficient for a thin remote viewer to reconstruct the same skeleton visualization while using a fraction of the bandwidth required by MP4 or GIF transport.

A second preparation step is to **version the packet schema explicitly**. The current `ANIMATION_PREVIEW` manifest proves that the project already benefits from strong typing; the same discipline should be carried into real-time transport by adding fields such as `packet_schema_version`, `coordinate_space`, and `fps`. That will let future viewers evolve independently without silently misinterpreting packet layouts.

A third preparation step is to **separate solver cadence from transport cadence**. The tensor solver works naturally at the animation sample rate, while a live viewer may want frame-dropping or interpolation under network jitter. Therefore, the real-time bridge should treat the solved world-transform tensor as the authoritative source and let the transport layer choose whether to send every frame, every Nth frame, or delta-compressed packets.

## References

[1]: http://esotericsoftware.com/spine-runtime-skeletons "Esoteric Software — Spine Runtimes Guide: Skeletons"
[2]: https://cseweb.ucsd.edu/classes/sp16/cse169-a/readings/2-Skeleton.html "UCSD CSE169 — Chapter 2: Skeletons"
[3]: https://numpy.org/doc/2.2/reference/generated/numpy.matmul.html "NumPy — numpy.matmul"
[4]: https://docs.opencv.org/4.x/dd/d9e/classcv_1_1VideoWriter.html "OpenCV — cv::VideoWriter"
[5]: https://matplotlib.org/stable/users/explain/figure/backends.html "Matplotlib — Backends"
