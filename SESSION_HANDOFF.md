# SESSION_HANDOFF

## Executive Summary

**SESSION-087** delivers the **ComfyUI WebSocket End-to-End Async Execution Engine**, closing the final gap between the offline preset-assembly pipeline (SESSION-084/086) and live GPU-accelerated pixel output. The system now has a complete, production-grade path from physics simulation through SDF rendering, ComfyUI payload assembly, WebSocket-monitored execution, and automated image/video download to the project tree.

The new `ComfyUIClient` class in `mathart/comfy_client/` implements the industrial-standard ComfyUI automation pattern: HTTP POST `/prompt` for queue submission, WebSocket event-stream monitoring for real-time progress tracking, and HTTP GET `/history` + `/view` for artifact retrieval. A one-click pipeline runner script (`tools/run_sparsectrl_pipeline.py`) orchestrates the full chain. All code enforces three anti-pattern red lines: no blind HTTP polling, graceful offline degradation, and mandatory artifact download into the project directory.

| Area | SESSION-087 outcome |
|---|---|
| **Task closure** | **P1-AI-2D-SPARSECTRL endpoint fully closed** — end-to-end execution engine landed |
| **New module** | `mathart/comfy_client/` — `ComfyUIClient` + `ExecutionResult` |
| **Pipeline runner** | `tools/run_sparsectrl_pipeline.py` — one-click physics-to-pixel automation |
| **Anti-pattern guards** | Blind HTTP POST Trap, Offline Crash Trap, Orphan Output Trap |
| **Test coverage** | **35 PASS, 0 FAIL** — 12 test classes, 35 test cases (SESSION-087 only) |
| **Cumulative P1-AI-2D tests** | **79 PASS, 0 FAIL** (SESSION-084: 3 + SESSION-086: 41 + SESSION-087: 35) |
| **Research** | ComfyUI WebSocket API, microservice resilience patterns, data-driven pipeline orchestration |

## What Landed in Code

### 1. ComfyUI WebSocket Execution Client (`mathart/comfy_client/comfyui_ws_client.py`)

A production-grade async execution client implementing the full ComfyUI API contract:

- **Health check**: `is_server_online()` probes `GET /system_stats` with configurable timeout.
- **Queue submission**: `_queue_prompt()` sends `POST /prompt` with `{prompt, client_id}` payload.
- **WebSocket monitoring**: `_ws_listen_until_complete()` connects to `ws://{server}/ws?clientId={id}` and parses the event stream (`status`, `executing`, `progress`, `executed`, `execution_error`). Completion is detected by `executing` with `node: null` — the official ComfyUI completion signal.
- **HTTP fallback polling**: `_http_poll_until_complete()` polls `GET /history/{prompt_id}` when `websocket-client` is not installed, with exponential backoff.
- **Artifact download**: `_download_outputs()` retrieves all generated images and videos via `GET /view?filename={f}&subfolder={s}&type={t}` and saves them to timestamped directories under `outputs/comfyui_renders/`.
- **Graceful degradation**: Every network call is wrapped in `try-except` for `ConnectionRefusedError`, `OSError`, `TimeoutError`, and `urllib.error.URLError`. Server offline → `ExecutionResult(degraded=True)`, never an unhandled crash.

### 2. End-to-End Pipeline Runner (`tools/run_sparsectrl_pipeline.py`)

A CLI facade script that orchestrates the complete chain in four phases:

1. **Phase 1 — Guide Generation**: Invokes the industrial SDF renderer to produce normal/depth/RGB frame sequences. Falls back to placeholder frames when the renderer is unavailable (CI safety).
2. **Phase 2 — Payload Assembly**: Calls `ComfyUIPresetManager.assemble_sequence_payload()` to inject guide directories into the SparseCtrl + AnimateDiff preset topology.
3. **Phase 3 — ComfyUI Execution**: Submits the payload via `ComfyUIClient`, monitors via WebSocket, downloads outputs. Degrades gracefully if server is offline.
4. **Phase 4 — Report Generation**: Saves execution report JSON and raw payload snapshot for reproducibility.

**One-click command for local 4070 execution:**

```bash
# Start ComfyUI first (in a separate terminal):
cd /path/to/ComfyUI && python main.py --listen 0.0.0.0 --port 8188

# Then run the pipeline:
python tools/run_sparsectrl_pipeline.py \
    --server 127.0.0.1:8188 \
    --frames 16 \
    --width 512 --height 512 \
    --steps 20 --cfg 7.5 \
    --prompt "pixel art game character sprite, Dead Cells style, detailed shading"

# Dry run (assemble payload without submitting):
python tools/run_sparsectrl_pipeline.py --dry-run
```

### 3. Offline-Safe E2E Tests (`tests/test_p1_ai_2d_sparsectrl_client.py`)

35 tests across 12 test classes, all using `unittest.mock` to deeply mock `urllib.request.urlopen` and `websocket.WebSocket`. Zero real HTTP or WebSocket calls.

| File | Purpose |
|---|---|
| `mathart/comfy_client/__init__.py` | Package init — exports `ComfyUIClient`, `ExecutionResult` |
| `mathart/comfy_client/comfyui_ws_client.py` | WebSocket execution engine with graceful degradation |
| `tools/run_sparsectrl_pipeline.py` | One-click end-to-end pipeline runner |
| `tests/test_p1_ai_2d_sparsectrl_client.py` | 35 offline-safe E2E tests for client and pipeline |
| `research/session087_comfyui_ws_client_research.md` | Research notes: ComfyUI WebSocket API, resilience patterns |
| `PROJECT_BRAIN.json` | SESSION-087 metadata, updated gap status |
| `SESSION_HANDOFF.md` | This file |

## Research Decisions That Were Enforced

### ComfyUI WebSocket Execution Paradigm

The ComfyUI official API documentation [1] establishes that the correct automation pattern is: (1) POST `/prompt` to queue the workflow, (2) connect to `ws://{server}/ws?clientId={id}` to receive real-time events, (3) detect completion via the `executing` event with `node: null`, (4) retrieve outputs via `GET /history/{prompt_id}`. This directly constrained the implementation to use WebSocket monitoring rather than blind HTTP polling or `time.sleep()` loops.

### Microservice Resilience and Graceful Degradation

The system assumes ComfyUI may be offline (CI environments, cold starts, GPU maintenance). Every network call in `ComfyUIClient` is wrapped in comprehensive exception handling. The `ExecutionResult` dataclass has explicit `degraded` and `degraded_reason` fields. Tests verify that `ConnectionRefusedError`, `OSError`, and `TimeoutError` all result in graceful degradation, not crashes.

### Data-Driven Pipeline Orchestration

The pipeline runner script lives in `tools/` as a facade layer. It consumes `AntiFlickerRenderBackend` output manifests and `ComfyUIPresetManager` payloads as pure data. It NEVER modifies core math engine code. The `ComfyUIClient` lives in `mathart/comfy_client/` as an independent module that can be registered as a backend in the future without polluting the core.

| Research theme | Enforced implementation consequence |
|---|---|
| **WebSocket execution paradigm** | `_ws_listen_until_complete()` with `executing(node=null)` completion signal [1] |
| **Graceful degradation** | Every network call wrapped in `try-except`; `ExecutionResult.degraded` field [2] |
| **Data-driven orchestration** | Pipeline runner in `tools/`; client in `mathart/comfy_client/`; core untouched [3] |

## Anti-Pattern Guards (SESSION-087 Red Lines)

### Blind HTTP POST Trap

The client MUST NOT use `requests.post()` followed by `time.sleep()` polling. It MUST use WebSocket event monitoring to detect completion. The test `test_ws_listen_complete_flow` verifies the full WebSocket event sequence. The test `test_full_successful_execution` verifies end-to-end flow with WebSocket.

### Offline Crash Trap

The client MUST NOT crash when ComfyUI is offline. `ConnectionRefusedError` MUST result in a degraded `ExecutionResult`, not an unhandled exception. Tests `test_execute_workflow_offline_returns_degraded`, `test_execute_workflow_offline_no_exception`, `test_execute_workflow_os_error_degraded`, `test_server_offline_connection_refused`, `test_server_offline_os_error`, and `test_server_offline_timeout` all verify this.

### Orphan Output Trap

The client MUST NOT submit a workflow and then abandon the outputs in ComfyUI's internal `output/` directory. It MUST download all generated images and videos to the project's `outputs/comfyui_renders/` directory. Tests `test_download_file_success`, `test_download_outputs_with_images_and_videos`, and `test_full_successful_execution` verify that files are physically written to disk.

## Testing and Validation

| Test command | Result |
|---|---|
| `pytest tests/test_p1_ai_2d_sparsectrl_client.py -v` | **35 passed, 0 failed** |
| `pytest tests/test_p1_ai_2d_preset_injection.py tests/test_p1_ai_2d_sparsectrl.py tests/test_p1_ai_2d_sparsectrl_client.py -v` | **79 passed, 0 failed** |

| Test class | Count | Purpose |
|---|---|---|
| `TestComfyUIClientConstruction` | 6 | Default and custom configuration |
| `TestHealthCheck` | 4 | Online/offline detection with multiple error types |
| `TestGracefulDegradation` | 4 | Server offline → degraded result, no crash |
| `TestQueuePrompt` | 2 | POST /prompt success and offline handling |
| `TestWebSocketExecution` | 2 | Full WS event flow and execution_error handling |
| `TestHTTPFallbackPolling` | 2 | HTTP polling success and offline timeout |
| `TestHistoryAndDownload` | 5 | /history retrieval and /view image download |
| `TestExecutionResult` | 3 | Dataclass defaults and serialization |
| `TestPipelineRunnerIntegration` | 3 | Guide generation, payload assembly, offline execution |
| `TestFullE2EMockExecution` | 1 | Complete end-to-end flow with all mocks |
| `TestOutputDirectoryStructure` | 1 | Timestamped output directory creation |
| `TestBackwardCompatibility` | 2 | SESSION-086 and SESSION-084 presets still load |

## How to Run the Full Pipeline on Local 4070

### Prerequisites

1. **ComfyUI** installed with the following custom nodes:
   - `ComfyUI-AnimateDiff-Evolved` (AnimateDiff motion modules)
   - `ComfyUI-Advanced-ControlNet` (SparseCtrl support)
   - `ComfyUI-VideoHelperSuite` (VHS directory I/O)

2. **Model weights** downloaded to ComfyUI's `models/` directory:
   - SD1.5 checkpoint (e.g., `v1-5-pruned-emaonly.safetensors`)
   - AnimateDiff v3 motion module (`v3_sd15_mm.ckpt`)
   - SparseCtrl RGB model (`v3_sd15_sparsectrl_rgb.ckpt`)
   - ControlNet normal (`control_v11p_sd15_normalbae.pth`)
   - ControlNet depth (`control_v11f1p_sd15_depth.pth`)
   - (Optional) IP-Adapter Plus (`ip-adapter-plus_sdxl_vit-h.safetensors`)
   - (Optional) Pixel art LoRA

3. **Start ComfyUI**:
   ```bash
   cd /path/to/ComfyUI
   python main.py --listen 0.0.0.0 --port 8188
   ```

### Execute

```bash
cd /path/to/MarioTrickster-MathArt
python tools/run_sparsectrl_pipeline.py --server 127.0.0.1:8188 --frames 16 --steps 20
```

### Expected Output

```
outputs/comfyui_renders/run_YYYYMMDD_HHMMSS/
├── guides/
│   ├── normal/frame_0000.png ... frame_0015.png
│   ├── depth/frame_0000.png ... frame_0015.png
│   └── rgb/frame_0000.png ... frame_0015.png
├── images/
│   └── (downloaded ComfyUI output frames)
├── videos/
│   └── (downloaded ComfyUI output video)
├── reports/
│   ├── execution_report_YYYYMMDD_HHMMSS.json
│   └── payload_YYYYMMDD_HHMMSS.json
└── execution_metadata.json
```

## Recommended Next Priorities

| Priority | Recommendation | Reason |
|---|---|---|
| **Immediate** | **P1-INDUSTRIAL-34C** | Dead Cells style 3D→2D dimension reduction pipeline — the next visual delivery gap |
| **High** | **P1-MIGRATE-4** | Backend hot-reload — force multiplier for rapid iteration |
| **High** | **P1-AI-2E** | Motion-adaptive keyframe planning for high-nonlinearity action segments |

### Architecture Micro-Adjustments for Next Tasks

**For P1-INDUSTRIAL-34C (Dead Cells 3D→2D)**: The `ComfyUIClient` can be reused as the execution backend. The industrial renderer's multi-channel output (albedo/normal/depth/mask/roughness) maps directly to ControlNet guide inputs. The next step is to build a Dead Cells-specific preset asset that applies the characteristic hand-painted 2D aesthetic to 3D-rendered frames.

**For P1-MIGRATE-4 (Backend Hot-Reload)**: The `ComfyUIClient` is already an independent module in `mathart/comfy_client/`. It can be registered as a new `BackendType.COMFYUI_EXECUTOR` in the registry with minimal wiring. Hot-reload would allow swapping preset assets and client configurations without restarting the pipeline.

**For P1-AI-2E (Motion-Adaptive Keyframes)**: The `assemble_sequence_payload()` method's `frame_count` and SparseCtrl `end_percent` parameters can be dynamically adjusted based on motion complexity metrics from the motion vector baker. High-nonlinearity segments would get more keyframes (higher SparseCtrl influence) while stable segments use fewer.

## Known Constraints and Non-Blocking Notes

| Constraint | Status |
|---|---|
| `websocket-client` Python package | **Optional** — HTTP fallback polling available if not installed |
| SparseCtrl/AnimateDiff model weights | **Not included** — must be downloaded separately |
| Live ComfyUI execution | **Not tested in CI** — all 35 tests are offline-safe with deep mocks |
| VRAM requirement | **~8-10GB** for SD1.5 + AnimateDiff + SparseCtrl on RTX 4070 (12GB) |
| Pixel art style | **Prompt-driven** — no dedicated LoRA included; recommend pixel-art-style LoRA |

## Files to Inspect First in the Next Session

| File | Why it matters |
|---|---|
| `mathart/comfy_client/comfyui_ws_client.py` | The WebSocket execution engine — all network I/O lives here |
| `tools/run_sparsectrl_pipeline.py` | The one-click pipeline runner — the user-facing entry point |
| `mathart/assets/comfyui_presets/sparsectrl_animatediff.json` | The 23-node preset topology — all wiring lives here |
| `mathart/animation/comfyui_preset_manager.py` | The sequence-aware injector — payload assembly logic |
| `tests/test_p1_ai_2d_sparsectrl_client.py` | 35 E2E tests — the contract specification for the execution engine |

## References

[1]: https://mintlify.wiki/Comfy-Org/ComfyUI/api/websocket-events "ComfyUI WebSocket Events API Documentation"
[2]: https://mintlify.wiki/Comfy-Org/ComfyUI/api/prompt "ComfyUI POST /prompt API Documentation"
[3]: https://mintlify.wiki/Comfy-Org/ComfyUI/api/history "ComfyUI GET /history API Documentation"
[4]: https://arxiv.org/abs/2311.16933 "Guo et al., SparseCtrl: Adding Sparse Controls to Text-to-Video Diffusion Models, ECCV 2024"
[5]: https://arxiv.org/abs/2307.04725 "Guo et al., AnimateDiff: Animate Your Personalized Text-to-Image Diffusion Models, ICLR 2024"
[6]: https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite "ComfyUI-VideoHelperSuite — Industrial sequence I/O for ComfyUI"
[7]: https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved "ComfyUI-AnimateDiff-Evolved — AnimateDiff integration for ComfyUI"
