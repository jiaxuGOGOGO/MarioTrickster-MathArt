# SESSION-087 Research: ComfyUI WebSocket Execution Engine

## 1. ComfyUI API WebSocket Execution Paradigm

### POST /prompt Endpoint
- **URL**: `POST http://127.0.0.1:8188/prompt`
- **Request body**: `{"prompt": {workflow_api_json}, "client_id": "uuid"}`
- **Response**: `{"prompt_id": "uuid", "number": 42, "node_errors": {}}`
- The `prompt` field is the workflow_api JSON (node IDs as keys)
- `client_id` is used to filter WebSocket events for this client

### WebSocket Connection
- **URL**: `ws://127.0.0.1:8188/ws?clientId={client_id}`
- Must connect BEFORE or AFTER POST /prompt (events are queued)

### WebSocket Event Flow (Official Documentation)
1. `status` — initial queue state + session ID
2. `executing` — `{"node": "3", "prompt_id": "..."}` — node starts
3. `progress` — `{"value": 15, "max": 20}` — sampling progress
4. `executed` — `{"node": "9", "output": {"images": [...]}}` — node done
5. `executing` — `{"node": null, "prompt_id": "..."}` — **EXECUTION COMPLETE SIGNAL**
6. `execution_error` — error with traceback

### Critical Signal: Execution Complete
When `executing` event has `node: null`, execution is done.
This is the signal to call `GET /history/{prompt_id}` for results.

### GET /history/{prompt_id}
- Returns outputs per node: `history[prompt_id]["outputs"][node_id]["images"]`
- Each image: `{"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}`

### GET /view (Image Download)
- **URL**: `GET http://127.0.0.1:8188/view?filename=X&subfolder=Y&type=output`
- Returns raw binary image data

## 2. Microservice Resilience & Graceful Degradation

### Key Principles
- ComfyUI server at 127.0.0.1:8188 may be offline (CI, no GPU, not started)
- All network calls MUST be wrapped in try-except
- ConnectionRefusedError, TimeoutError, OSError must be caught
- On failure: log warning, return degraded result, never crash
- Tests must NEVER require a live ComfyUI server

### Implementation Pattern
```python
try:
    response = urllib.request.urlopen(req, timeout=10)
except (ConnectionRefusedError, urllib.error.URLError, OSError) as e:
    logger.warning("ComfyUI server offline: %s", e)
    return DegradedResult(reason="server_offline")
```

## 3. Data-Driven Pipeline Orchestration

### Architecture Discipline
- Glue code lives in `tools/` or `mathart/comfy_client/` — NEVER in core math engine
- ComfyUIClient is a standalone facade that consumes ArtifactManifest
- Pipeline runner orchestrates: physics → render → preset assembly → client submission
- Core engine outputs data; client converts data to network I/O

## 4. Anti-Pattern Red Lines

### Blind HTTP POST Trap
- NEVER use `requests.post()` then `time.sleep()` polling
- MUST use WebSocket to detect execution completion

### Offline Crash Trap
- ConnectionRefusedError must NEVER crash tests or main process
- CI environments have no ComfyUI — graceful skip required

### Orphan Output Trap
- MUST download images via `/view` API after execution
- Save to project's `outputs/comfyui_renders/` with timestamp directories
- Never rely on ComfyUI's internal output folder

## References
- [ComfyUI WebSocket Events](https://mintlify.wiki/Comfy-Org/ComfyUI/api/websocket-events)
- [ComfyUI POST /prompt](https://mintlify.wiki/Comfy-Org/ComfyUI/api/prompt)
- [ComfyUI GET /history](https://mintlify.wiki/Comfy-Org/ComfyUI/api/history)
