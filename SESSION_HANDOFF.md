# SESSION_HANDOFF

## Executive Summary

**SESSION-084** closes **`P1-AI-2D`** by converting the anti-flicker ComfyUI lane from **Python-hardcoded graph assembly** into a **data-driven preset asset pipeline**. The repository now ships a real **`workflow_api` preset asset**, a **semantic selector-based preset manager**, and a backend export path that emits a strongly typed **`ANTI_FLICKER_REPORT`** manifest together with the exact preset asset and the assembled execution payload. This means anti-flicker jobs are now reproducible, inspectable, and packageable as repository assets instead of being trapped inside ad-hoc Python wiring. The implementation follows the external research constraint that **execution topology should live in external API workflow JSON**, while runtime should apply only parameter overrides, not reconstruct graph topology in code [1] [2].

The session also enforced a second architectural constraint from the literature and platform references: **geometry lock** and **identity lock** must remain distinct conditioning tracks. ControlNet is used to preserve spatial structure, while IP-Adapter remains the light-weight image-reference path for identity/style anchoring rather than becoming a geometry surrogate [3] [4]. That separation is now visible in the shipped preset asset, in the semantic injection table, and in the exported lock manifest embedded in the workflow payload.

| Area | SESSION-084 outcome |
|---|---|
| **Task closure** | **`P1-AI-2D` closed** |
| **New preset manager** | `mathart/animation/comfyui_preset_manager.py` |
| **New preset asset** | `mathart/assets/comfyui_presets/dual_controlnet_ipadapter.json` |
| **Backend report family** | `ArtifactFamily.ANTI_FLICKER_REPORT` |
| **Injection strategy** | `class_type + _meta.title` semantic selector binding |
| **Validation result** | **37 PASS, 0 FAIL** |

## What Landed in Code

The main code landing is **`ComfyUIPresetManager`**, which loads external preset JSON assets, validates their structural requirements, and assembles a runtime payload by binding file paths and hyperparameters through **semantic node signatures** rather than numeric node IDs. This matters because ComfyUI node IDs are not stable under graph editing or export/import churn. The new tests explicitly renumber the preset graph and confirm that payload assembly still succeeds, which turns node-ID instability from a hidden operational risk into a guarded contract.

The shipped preset asset is **`mathart/assets/comfyui_presets/dual_controlnet_ipadapter.json`**. It represents a production anti-flicker baseline using **dual ControlNet** guide lanes for normal and depth, plus an **IP-Adapter** identity-reference lane. The runtime now treats that JSON file as the source of truth for graph topology. The execution path no longer re-specifies the graph structure line by line in Python; instead, Python only injects runtime values such as image paths, prompt text, checkpoint names, guide strengths, seed, sampler settings, and output prefix.

| File | Purpose |
|---|---|
| `mathart/animation/comfyui_preset_manager.py` | External preset loader, semantic selector binder, and workflow payload assembler |
| `mathart/assets/comfyui_presets/dual_controlnet_ipadapter.json` | Shipped anti-flicker `workflow_api` topology asset |
| `mathart/animation/headless_comfy_ebsynth.py` | Carries `workflow_preset_name` through runtime metadata and uses preset-driven payload assembly |
| `mathart/core/builtin_backends.py` | Upgrades `anti_flicker_render` to export `preset_asset`, `workflow_payload`, and typed report metadata |
| `mathart/core/artifact_schema.py` | Adds `ANTI_FLICKER_REPORT` family and required metadata contract |
| `pyproject.toml` | Includes preset JSON assets as package data |
| `tests/test_p1_ai_2d_preset_injection.py` | Guards semantic injection, node-ID independence, offline-safe backend export, and strict schema validity |

## Research Decisions That Were Enforced

The external research was not decorative. It directly constrained implementation. ComfyUI deployment guidance distinguishes UI-oriented workflow files from **API execution workflow JSON**, which is exactly why the preset topology is now stored as a repository asset and not regenerated in backend code [1] [2]. The ControlNet and IP-Adapter references also forced a clean split between **structural conditioning** and **identity conditioning**, which is why the preset manager preserves separate guide selectors and reports their activation state in the lock manifest [3] [4].

The data-architecture side of the research also mattered. Frostbite-style data-oriented thinking and data-driven rendering practice both argue that execution graphs and configuration data should be externalized so tools and runtime remain loosely coupled [5] [6]. SESSION-084 therefore chose the stronger architecture: the graph is data, the runtime is a binder, and the backend report captures both the immutable source asset and the final assembled payload for auditability.

| Research theme | Enforced implementation consequence |
|---|---|
| **ComfyUI API workflow JSON** | Graph topology moved into `mathart/assets/comfyui_presets/*.json` instead of being rebuilt in Python [1] [2] |
| **ControlNet conditioning** | Normal/depth guide lanes remain explicit geometry-lock channels [3] |
| **IP-Adapter conditioning** | Identity reference remains a distinct image-prompt path with independent weight and activation state [4] |
| **Data-driven engine design** | Preset topology and runtime overrides are decoupled; backend exports both source asset and assembled payload [5] [6] |

## Artifact and Backend Closure

The anti-flicker backend is no longer just a temporal frame exporter. It now emits a schema-enforced **`ANTI_FLICKER_REPORT`** artifact family with required metadata including **`preset_name`**, **`frame_count`**, **`fps`**, **`keyframe_count`**, **`guides_locked`**, and **`identity_lock_enabled`**. In addition to the previous temporal artifacts, the backend now persists the **source preset asset** and the **assembled `workflow_payload`**. This closes the auditability gap that existed when the workflow structure lived only in Python logic.

The report contract also makes offline CI truthful. In this sandbox, the tests do **not** depend on a live ComfyUI server. Instead, they verify that payload assembly is correct, that exported files exist, and that the manifest remains schema-valid when HTTP calls fail. That design is deliberate: the repository now proves the **preset asset pipeline** and **artifact contract** independently of external runtime availability, while live model-weight execution remains a separate follow-up task.

| Contract element | New value |
|---|---|
| **Artifact family** | `anti_flicker_report` |
| **Required outputs** | `workflow_payload`, `preset_asset`, `temporal_report` |
| **Required metadata** | `preset_name`, `frame_count`, `fps`, `keyframe_count`, `guides_locked`, `identity_lock_enabled` |
| **Audit payload** | `frame_sequence`, `time_range`, `keyframe_plan`, `workflow_manifest_path`, `workflow_payload_path`, `preset_asset_path`, `lock_manifest` |

## Testing and Validation

The new regression coverage proves three things. First, the shipped preset asset actually contains the node classes required for the anti-flicker lane. Second, semantic injection survives **node-ID renumbering**, which is the most important robustness check for a data-driven ComfyUI asset workflow. Third, the backend emits a strict `ANTI_FLICKER_REPORT` manifest with on-disk `preset_asset`, `workflow_payload`, and `temporal_report` outputs even when live HTTP submission is unavailable.

The older subprocess-based anti-flicker regression suite was also upgraded so the CLI-facing contract now expects **`anti_flicker_report`** instead of the previous generic family. The CI schema guard was extended accordingly, which means the new family is now part of the architectural audit instead of being protected only by a local unit test.

| Test command | Result |
|---|---|
| `pytest -q tests/test_p1_ai_2d_preset_injection.py` | **3 passed** |
| `pytest -q tests/test_session068_e2e.py` | **20 passed** |
| `pytest -q tests/test_ci_backend_schemas.py` | **14 passed** |
| **Combined** | **37 passed, 0 failed** |

## Why `P1-AI-2D` Is Considered Closed

The original gap was to **ship real ComfyUI preset packs for anti-flicker jobs**, not merely to keep a working graph hidden inside one Python function. SESSION-084 closes that gap because the repository now contains a reusable external preset asset, a semantic binding runtime, a typed manifest family, package-data inclusion for installation, and regression tests that lock the behavior under node-ID churn and offline execution. The preset path is now a first-class repository asset rather than a fragile implementation detail.

What remains is not baseline preset shipping, but **live-weight runtime expansion**. In other words, the project no longer lacks the preset-asset architecture itself. The remaining AI-visual follow-up is to run richer workflows with actual **SparseCtrl / AnimateDiff** weights and broader production runtime evidence under real ComfyUI environments.

## Recommended Next Priorities

With `P1-AI-2D` closed, the immediate next priority returns to **`P3-GPU-BENCH-1`**, because that task still lacks real CUDA evidence and sparse-topology validation. After that, **`P1-MIGRATE-4`** remains the strongest architecture multiplier because dynamic discovery and hot-reload closure reduce future integration friction for every new backend. On the visual side, the next direct continuation is **`P1-AI-2D-SPARSECTRL`**, which should use the new preset-asset architecture as its base rather than reopening hardcoded graph generation.

| Priority | Recommendation | Reason |
|---|---|---|
| **1** | Finish **`P3-GPU-BENCH-1`** on real CUDA hardware | Still the largest unresolved truth gap in benchmark evidence |
| **2** | Continue **`P1-MIGRATE-4`** | Registry hot-reload remains a force multiplier for future backends |
| **3** | Start **`P1-AI-2D-SPARSECTRL`** | The preset-asset foundation is now closed, so real SparseCtrl runtime execution is the natural next visual step |

## Known Constraints and Non-Blocking Notes

This sandbox still does **not** provide a live ComfyUI runtime with guaranteed production model weights, and the tests in SESSION-084 were intentionally written to stay truthful under that constraint. The repository now validates **asset structure**, **payload assembly**, and **manifest export** without pretending that server-side model execution happened. That is the correct boundary for this milestone.

Similarly, the closure of `P1-AI-2D` should not be confused with the still-open **SparseCtrl runtime** task. The architecture for preset assets is complete, but live execution with real SparseCtrl/AnimateDiff weights remains explicitly tracked under **`P1-AI-2D-SPARSECTRL`**.

| Constraint | Status |
|---|---|
| Live ComfyUI server not required in CI | **Non-blocking** — offline payload assembly is now a deliberate contract |
| Real SparseCtrl / AnimateDiff runtime execution | **Still open under `P1-AI-2D-SPARSECTRL`** |
| Preset asset packaging and typed anti-flicker report architecture | **Complete for `P1-AI-2D`** |

## Files to Inspect First in the Next Session

The fastest re-entry path is to inspect the research note, the preset manager, the shipped preset asset, and the new regression suite before attempting any further ComfyUI work. Together, those files define the contract that SESSION-084 established.

| File | Why it matters |
|---|---|
| `research/session084_ai2d_preset_research.md` | External rationale for externalized workflow assets and semantic injection |
| `mathart/animation/comfyui_preset_manager.py` | Canonical runtime binder for preset assets |
| `mathart/assets/comfyui_presets/dual_controlnet_ipadapter.json` | Source-of-truth anti-flicker workflow topology |
| `tests/test_p1_ai_2d_preset_injection.py` | Behavioral guardrail for selector-based injection and typed report export |
| `mathart/core/builtin_backends.py` | Registry-native anti-flicker backend export contract |

## References

[1]: https://www.timlrx.com/blog/executing-comfyui-workflows-as-standalone-scripts/ "Executing ComfyUI Workflows as Standalone Scripts"
[2]: https://docs.runcomfy.com/serverless/workflow-files "RunComfy Docs — Workflow Files"
[3]: https://openaccess.thecvf.com/content/ICCV2023/html/Zhang_Adding_Conditional_Control_to_Text-to-Image_Diffusion_Models_ICCV_2023_paper.html "Adding Conditional Control to Text-to-Image Diffusion Models"
[4]: https://arxiv.org/abs/2308.06721 "IP-Adapter: Text Compatible Image Prompt Adapter for Text-to-Image Diffusion Models"
[5]: https://www.ea.com/frostbite/news/introduction-to-data-oriented-design "Introduction to Data-Oriented Design"
[6]: https://jorenjoestar.github.io/post/data_driven_rendering_pipeline/ "Data Driven Rendering Pipeline"
