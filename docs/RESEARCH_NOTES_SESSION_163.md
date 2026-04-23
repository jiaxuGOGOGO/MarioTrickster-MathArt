# Research Notes — SESSION-163: ComfyUI API Bridge & Full-Array Artifact Hydration

> **Task ID**: P0-SESSION-161-COMFYUI-API-BRIDGE
> **Date**: 2026-04-23
> **Status**: CLOSED

## 1. Headless AI Rendering Architecture (无头 AI 渲染架构)

The ComfyUI server exposes a complete REST + WebSocket API for headless operation, enabling programmatic workflow submission without any manual UI node-wiring.

| Endpoint | Method | Purpose |
|---|---|---|
| `/prompt` | POST | Submit a workflow JSON payload for execution |
| `/upload/image` | POST | Upload an image via multipart/form-data |
| `/history/{prompt_id}` | GET | Retrieve execution history and output metadata |
| `/view` | GET | Download a rendered output file by filename |
| `/free` | POST | Force VRAM garbage collection |
| `/system_stats` | GET | Health check / server availability probe |
| `/ws?clientId={id}` | WebSocket | Real-time progress events (executing, progress, executed, execution_error) |

The system constructs a complete Workflow JSON (computation graph) in pure Python, submits it via HTTP `POST /prompt`, and monitors completion through WebSocket events. This eliminates all dependency on manual UI operations.

**Source**: [ComfyUI Official API Routes Documentation](https://docs.comfy.org/development/comfyui-server/comms_routes), [9elements Blog — Hosting a ComfyUI Workflow via API](https://9elements.com/blog/hosting-a-comfyui-workflow-via-api/)

## 2. Multi-Modal ControlNet Injection (多模态控制网动态注入)

ControlNet and T2I-Adapter provide strong spatial conditioning for diffusion models by injecting auxiliary signals (Normal maps, Depth maps, Canny edges) as additional inputs to the denoising process.

In the MathArt pipeline, the upstream math engine produces three guide channels per frame:

| Channel | Purpose | ControlNet Model |
|---|---|---|
| **Albedo** (Source) | Base color/texture reference | LoadImage → IP-Adapter or direct conditioning |
| **Normal** | Surface orientation constraint | `control_v11p_sd15_normalbae` |
| **Depth** | Spatial depth constraint | `control_v11f1p_sd15_depth` |

The workflow mutator uses **semantic addressing** (matching `_meta.title` markers like `[MathArt_Normal_Guide]`) to dynamically inject local guide image paths into the correct ControlNet input nodes. This avoids brittle hardcoded node IDs that break whenever the workflow is edited in the ComfyUI GUI.

**Source**: [ComfyUI ControlNet Tutorial](https://docs.comfy.org/tutorials/controlnet/controlnet), [ComfyUI ControlNet Examples](https://comfyanonymous.github.io/ComfyUI_examples/controlnet/), [T2I-Adapter HuggingFace Documentation](https://huggingface.co/docs/diffusers/using-diffusers/t2i_adapter)

## 3. Idempotent API Clients & Circuit Breakers (幂等 API 客户端与熔断机制)

External GPU render nodes are inherently unreliable due to network latency, VRAM pressure, and service availability. The system implements three resilience patterns:

### 3.1 Exponential Backoff with Jitter

Retry delays follow the formula: `min(base * 2^attempt + random_jitter, max_delay)`. This prevents thundering herd effects when multiple clients retry simultaneously against a recovering server.

| Parameter | Value | Rationale |
|---|---|---|
| Base delay | 2 seconds | Allows brief transient recovery |
| Max delay | 32 seconds | Prevents excessive wait times |
| Jitter range | 0–1.5 seconds | Decorrelates concurrent retries |
| Max attempts | 5 | Bounds total retry duration |

### 3.2 Circuit Breaker (Three-State Machine)

Implements Michael Nygard's canonical circuit breaker from "Release It!" (2007):

| State | Behavior |
|---|---|
| **CLOSED** | Normal operation; failures increment counter |
| **OPEN** | All calls short-circuited for `recovery_timeout` seconds |
| **HALF_OPEN** | Single probe call allowed; success resets to CLOSED, failure re-opens |

The circuit opens after 3 consecutive failures and enters recovery after 30 seconds. This prevents the main scheduler from deadlocking or cascading failures when the GPU node is down.

### 3.3 Graceful Degradation

When the ComfyUI server is unreachable, the system:
1. Prints a yellow warning: `[⚠️ AI 渲染服务器未就绪，已为您安全保留原生物理底图并终止推流]`
2. Returns a valid `ArtifactManifest` with `degraded=True` metadata
3. Preserves the original CPU-baked guide sequences as canonical output
4. Smoothly returns to the main loop without any Traceback or crash

**Source**: [Michael Nygard, "Release It!" (2007)](https://pragprog.com/titles/mnee2/release-it-second-edition/), [AWS Architecture Blog — Exponential Backoff And Jitter (2015)](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/), [Martin Fowler — Circuit Breaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)

## 4. Workflow JSON Mutation (工作流 JSON 变异器)

The BFF (Backend for Frontend) Payload Mutation pattern ensures the Python layer never rebuilds the ComfyUI node graph from scratch. Instead:

1. A **template** workflow JSON is loaded from `mathart/assets/workflows/workflow_api_template.json`
2. The template is **deep-copied** (immutable blueprint principle)
3. Nodes are located by **semantic `_meta.title` markers** (e.g., `[MathArt_Input_Image]`)
4. Runtime values (uploaded filenames, prompts, seeds) are **injected** into matched nodes
5. A **mutation ledger** records every change for full auditability

This approach is inspired by LLVM's pass infrastructure where transformations operate on an existing IR rather than constructing it from scratch.

**Source**: [Sam Newman, "Building Microservices" (2021)](https://samnewman.io/books/building_microservices_2nd_edition/), [LLVM Pass Infrastructure](https://llvm.org/docs/WritingAnLLVMPass.html)

## 5. Full-Array Artifact Hydration (全阵列资产推流与流水线水合)

The AI Render Stream Backend iterates ALL available motion states from the dynamic registry (`get_motion_lane_registry().names()`) and processes each action's guide sequence through the render pipeline:

1. **Enumerate**: Query registry for all actions (run, jump, idle, fall, hit, walk, ...)
2. **Upload**: Push baked Albedo/Normal/Depth guides to ComfyUI via `/upload/image`
3. **Mutate**: Inject uploaded filenames and action-specific prompts into the workflow template
4. **Render**: Submit mutated workflow via `POST /prompt` and wait for completion
5. **Hydrate**: Rename outputs to `ai_render_{action}_{frame:02d}.png` and register in Pipeline Context

This ensures downstream packaging backends can consume AI-rendered assets without any knowledge of the render pipeline internals.

## 6. Implementation Artifacts

| File | Purpose |
|---|---|
| `mathart/backend/ai_render_stream_backend.py` | Registry-native streaming backend with circuit breaker |
| `mathart/assets/workflows/workflow_api_template.json` | Minimal ControlNet + KSampler workflow template |
| `mathart/core/backend_types.py` | Added `AI_RENDER_STREAM` backend type + aliases |
| `mathart/core/artifact_schema.py` | Added `AI_RENDER_STREAM_REPORT` artifact family |
| `mathart/core/backend_registry.py` | Added `AI_RENDER_STREAM` capability + auto-import |
| `mathart/backend/__init__.py` | Updated package documentation |
| `tests/test_ai_render_stream_backend.py` | 20+ test cases covering all red lines |
| `docs/RESEARCH_NOTES_SESSION_163.md` | This document (Docs-as-Code) |
