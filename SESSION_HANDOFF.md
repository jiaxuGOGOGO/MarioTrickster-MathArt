# SESSION_HANDOFF

## Executive Summary

**SESSION-086** closes **`P1-AI-2D-SPARSECTRL`** by landing the full **SparseCtrl + AnimateDiff temporal consistency visual pipeline** as a production-grade preset asset with a sequence-aware injector and comprehensive offline-safe E2E test coverage.

The SESSION-084 preset-asset architecture (`ComfyUIPresetManager` + external `workflow_api` JSON) has been extended from single-image dual-ControlNet workflows to **frame-sequence temporal workflows**. The new `sparsectrl_animatediff.json` preset defines a 23-node ComfyUI graph topology incorporating AnimateDiff motion modules, SparseCtrl sparse conditioning, VHS directory-based sequence I/O, and VHS_VideoCombine video output. The upgraded `ComfyUIPresetManager.assemble_sequence_payload()` injects directory paths, frame counts, context lengths, and temporal parameters without ever modifying the node graph topology.

| Area | SESSION-086 outcome |
|---|---|
| **Task closure** | **`P1-AI-2D-SPARSECTRL` CLOSED** |
| **New preset asset** | `sparsectrl_animatediff.json` — 23-node SparseCtrl + AnimateDiff + VHS topology |
| **Injector upgrade** | `assemble_sequence_payload()` with VHS directory injection, batch_size sync, temporal config |
| **Anti-pattern guards** | Single-Frame Fallacy, Python Topology Trap, CI HTTP Blocking Trap |
| **Test coverage** | **41 PASS, 0 FAIL** — 5 test classes, 41 test cases |
| **Backward compatibility** | Original `dual_controlnet_ipadapter` preset and `assemble_payload()` fully preserved |
| **Research** | SparseCtrl (ECCV 2024), AnimateDiff (ICLR 2024), VHS industrial I/O |

## What Landed in Code

The main code landing consists of three deliverables: a new preset asset, an upgraded injector, and a comprehensive test suite.

**1. Preset Asset: `sparsectrl_animatediff.json`**

A 23-node ComfyUI `workflow_api` JSON defining the complete SparseCtrl + AnimateDiff temporal consistency pipeline. The topology includes: `CheckpointLoaderSimple` → `ADE_AnimateDiffLoaderWithContext` (with `ADE_AnimateDiffUniformContextOptions` for sliding window context) → three `VHS_LoadImagesPath` nodes for normal/depth/RGB frame sequences → dual `ControlNetLoader` + `ControlNetApplyAdvanced` chains → `ACN_SparseCtrlLoaderAdvanced` with `ControlNetApplyAdvanced` for sparse RGB conditioning → `EmptyLatentImage` (batch_size = frame_count) → `KSampler` → `VAEDecode` → `VHS_VideoCombine` + `SaveImage`. Optional IP-Adapter identity lock chain preserved from SESSION-084.

**2. Sequence-Aware Injector: `assemble_sequence_payload()`**

New method on `ComfyUIPresetManager` that accepts frame sequence directories instead of single image paths. Key injection points: VHS `directory` paths, `EmptyLatentImage.batch_size` synchronized with `frame_count`, `ADE_AnimateDiffUniformContextOptions.context_length/context_overlap`, `ACN_SparseCtrlLoaderAdvanced.sparsectrl_name/motion_strength`, `VHS_VideoCombine.frame_rate`, and all existing ControlNet/KSampler/IP-Adapter parameters. The `_SPARSECTRL_SELECTORS` tuple defines 33 semantic bindings for the new preset.

**3. Lock Manifest Extension**

The `mathart_lock_manifest` now includes `temporal_config` (frame_count, context_length, context_overlap, frame_rate, animatediff/sparsectrl model names, batch_size_synced flag), `sequence_directories` (normal/depth/rgb absolute paths), and an extended `workflow_contract` with `sequence_aware: true` and `vhs_directory_injection: true`.

| File | Purpose |
|---|---|
| `mathart/assets/comfyui_presets/sparsectrl_animatediff.json` | 23-node SparseCtrl + AnimateDiff + VHS preset topology |
| `mathart/animation/comfyui_preset_manager.py` | Upgraded with `assemble_sequence_payload()`, `_SPARSECTRL_SELECTORS`, preset-specific validation |
| `tests/test_p1_ai_2d_sparsectrl.py` | 41 offline-safe E2E tests across 5 test classes |
| `research/session086_sparsectrl_animatediff_research.md` | Research notes: SparseCtrl, AnimateDiff, VHS node specifications and workflow topology |
| `PROJECT_BRAIN.json` | P1-AI-2D-SPARSECTRL → CLOSED, session metadata |
| `SESSION_HANDOFF.md` | This file |

## Research Decisions That Were Enforced

The external research was not decorative. It directly constrained implementation across three domains.

**SparseCtrl Sparse Conditioning** (Guo et al., ECCV 2024) [1]. The paper establishes that SparseCtrl injects temporally sparse condition maps (keyframes) into the temporal attention layers of a video diffusion model. The `ACN_SparseCtrlLoaderAdvanced` node in ComfyUI-Advanced-ControlNet implements this as a loadable model with `use_motion`, `motion_strength`, and `sparse_method` parameters. This directly constrained the preset to include the SparseCtrl loader as a separate node from the standard ControlNet loaders, with its own `ControlNetApplyAdvanced` chain using `end_percent` to control temporal influence decay.

**AnimateDiff Motion Module** (Guo et al., ICLR 2024) [2]. AnimateDiff inserts temporal attention modules into a frozen text-to-image model. The critical implementation constraint is that `EmptyLatentImage.batch_size` MUST equal the desired frame count — AnimateDiff connects independent frames in latent space into a coherent video tensor through temporal attention. The `ADE_AnimateDiffUniformContextOptions` node controls the sliding window size (`context_length`) and overlap for infinite-length generation. This directly constrained the injector to synchronize `batch_size` with `frame_count` and expose `context_length`/`context_overlap` as injectable parameters.

**VHS Directory-Based Sequence I/O** (ComfyUI-VideoHelperSuite) [3]. The `VHS_LoadImagesPath` node loads frame sequences from a directory path, not individual image files. This is the industrial-standard approach for temporal workflows. The anti-pattern guard "Single-Frame Fallacy" was enforced: the preset uses three `VHS_LoadImagesPath` nodes (normal, depth, RGB) with `directory` inputs, NOT `LoadImage` nodes. The `VHS_VideoCombine` node combines decoded frames into video output with configurable `frame_rate`.

| Research theme | Enforced implementation consequence |
|---|---|
| **SparseCtrl sparse conditioning** | Separate `ACN_SparseCtrlLoaderAdvanced` node with `end_percent`-controlled temporal decay [1] |
| **AnimateDiff batch_size sync** | `EmptyLatentImage.batch_size` = `frame_count`; `context_length`/`context_overlap` exposed [2] |
| **VHS directory I/O** | Three `VHS_LoadImagesPath` nodes with `directory` inputs; `VHS_VideoCombine` for video output [3] |

## Anti-Pattern Guards (SESSION-086 Red Lines)

Three anti-patterns were identified during research and explicitly guarded against in both code and tests.

**Single-Frame Fallacy**: Temporal workflows MUST use `VHS_LoadImagesPath` with directory paths, NOT single-image `LoadImage` nodes. The test `test_preset_contains_vhs_load_images_path` asserts >= 3 VHS loader nodes exist. The tests `test_vhs_normal_directory_injected`, `test_vhs_depth_directory_injected`, and `test_vhs_rgb_directory_injected` verify that actual directory paths are injected.

**Python Topology Trap**: All node wiring MUST exist in the external JSON preset. The injector MUST NOT add or remove nodes. The tests `test_node_count_unchanged_after_injection` and `test_class_types_unchanged_after_injection` verify topology invariance before and after injection.

**CI HTTP Blocking Trap**: Zero HTTP calls in tests. All 41 tests validate JSON payload structure only, with no live ComfyUI server dependency. The `mathart_lock_manifest` is validated for structural completeness offline.

## Testing and Validation

| Test command | Result |
|---|---|
| `pytest tests/test_p1_ai_2d_sparsectrl.py -v` | **41 passed, 0 failed** |

| Test class | Count | Purpose |
|---|---|---|
| `TestSparseCtrlPresetAsset` | 10 | Preset file existence, JSON validity, required node class_types, selector validation |
| `TestSequencePayloadAssembly` | 17 | Directory injection, batch_size sync, parameter injection for all temporal nodes |
| `TestSequenceLockManifest` | 8 | Lock manifest structure: temporal_config, sequence_directories, workflow_contract |
| `TestTopologyInvariance` | 2 | Node count and class_type invariance after injection |
| `TestBackwardCompatibility` | 2 | Original preset and `assemble_payload()` still work |
| `TestAntiFlickerReportIntegration` | 2 | JSON serialization round-trip and disk persistence |

## Recommended Next Priorities

| Priority | Recommendation | Reason |
|---|---|---|
| **Immediate** | Start **`P1-INDUSTRIAL-34C`** | Industrial sprite quality is the next visual-delivery gap |
| **High** | Continue **`P1-MIGRATE-4`** | Registry hot-reload remains a force multiplier for future backends |
| **High** | Start **`P1-AI-2E`** | Motion-adaptive keyframe planning for high-nonlinearity action segments |

### Architecture Micro-Adjustments for Next Tasks

**For P1-INDUSTRIAL-34C**: The industrial sprite backend (`IndustrialSpriteBackend`) and its MaterialX/glTF PBR-inspired `texture_channels` manifest are established. The next step is to improve the quality of the generated texture channels (albedo, normal, depth, roughness) to match commercial 2D game art standards.

**For P1-AI-2E**: The `assemble_sequence_payload()` method provides the foundation for motion-adaptive keyframe planning. The next step is to integrate the motion vector baker's temporal analysis with the SparseCtrl preset to automatically select sparse keyframes based on motion complexity metrics.

**For P1-MIGRATE-4**: The registry pattern is fully established. Hot-reload requires adding a file-watcher or signal-based reload trigger to `BackendRegistry.discover()`.

## Known Constraints and Non-Blocking Notes

| Constraint | Status |
|---|---|
| SparseCtrl/AnimateDiff model weights | **Not included** — must be downloaded separately for live execution |
| Live ComfyUI server execution | **Not tested** — all tests are offline structural validation |
| VHS_VideoCombine output format | **h264-mp4 default** — configurable via `format` parameter |
| IP-Adapter in temporal workflows | **Optional** — weight set to 0.0 when `use_ip_adapter=False` |

## Files to Inspect First in the Next Session

| File | Why it matters |
|---|---|
| `mathart/assets/comfyui_presets/sparsectrl_animatediff.json` | The 23-node preset topology — all wiring lives here |
| `mathart/animation/comfyui_preset_manager.py` | The upgraded injector with `assemble_sequence_payload()` |
| `tests/test_p1_ai_2d_sparsectrl.py` | 41 E2E tests — the contract specification for the temporal pipeline |
| `research/session086_sparsectrl_animatediff_research.md` | Research notes with node specifications and workflow topology |

## References

[1]: https://arxiv.org/abs/2311.16933 "Guo et al., SparseCtrl: Adding Sparse Controls to Text-to-Video Diffusion Models, ECCV 2024"
[2]: https://arxiv.org/abs/2307.04725 "Guo et al., AnimateDiff: Animate Your Personalized Text-to-Image Diffusion Models, ICLR 2024"
[3]: https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite "ComfyUI-VideoHelperSuite — Industrial sequence I/O for ComfyUI"
[4]: https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved "ComfyUI-AnimateDiff-Evolved — AnimateDiff integration for ComfyUI"
[5]: https://github.com/Kosinkadink/ComfyUI-Advanced-ControlNet "ComfyUI-Advanced-ControlNet — SparseCtrl support for ComfyUI"
