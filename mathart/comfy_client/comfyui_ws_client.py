"""ComfyUI WebSocket execution client for production-grade async workflow execution.

SESSION-087 (P1-AI-2D-SPARSECTRL endpoint closure)
----------------------------------------------------
This module implements the **industrial-standard** ComfyUI execution paradigm:

1. Submit workflow via ``POST /prompt`` with a unique ``client_id``.
2. Connect to ``ws://{server}/ws?clientId={client_id}`` for real-time events.
3. Listen for ``executing`` (node=null) as the **completion signal**.
4. Retrieve outputs via ``GET /history/{prompt_id}``.
5. Download generated images via ``GET /view?filename=...&type=output``.
6. Save all outputs to the project's ``outputs/comfyui_renders/`` directory.

Anti-pattern guards (SESSION-087 red lines):
- 🚫 Blind HTTP POST Trap: NEVER use ``time.sleep()`` polling.
  MUST use WebSocket to detect execution completion.
- 🚫 Offline Crash Trap: ALL network calls wrapped in try-except.
  ``ConnectionRefusedError`` → graceful degradation, never crash.
- 🚫 Orphan Output Trap: MUST download images via ``/view`` API
  into the project's own ``outputs/comfyui_renders/`` directory.

Research grounding:
- ComfyUI WebSocket Events (Official Docs): ``status``, ``executing``,
  ``executed``, ``progress``, ``execution_error`` event types.
- POST /prompt: ``{"prompt": workflow, "client_id": uuid}``
- GET /history/{prompt_id}: outputs per node with image metadata.
- GET /view: binary image download by filename/subfolder/type.
"""
from __future__ import annotations

import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Result of a ComfyUI workflow execution."""

    success: bool
    prompt_id: str = ""
    output_images: list[str] = field(default_factory=list)
    output_videos: list[str] = field(default_factory=list)
    output_dir: str = ""
    error_message: str = ""
    degraded: bool = False
    degraded_reason: str = ""
    elapsed_seconds: float = 0.0
    node_progress: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "prompt_id": self.prompt_id,
            "output_images": self.output_images,
            "output_videos": self.output_videos,
            "output_dir": self.output_dir,
            "error_message": self.error_message,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


# ---------------------------------------------------------------------------
# ComfyUI WebSocket Client
# ---------------------------------------------------------------------------

class ComfyUIClient:
    """Production-grade ComfyUI execution client with WebSocket monitoring.

    This client implements the full ComfyUI API execution paradigm:
    POST /prompt → WebSocket listen → GET /history → GET /view download.

    All network operations are wrapped in graceful degradation guards.
    If the ComfyUI server is offline, the client returns a ``degraded``
    result without crashing.

    Parameters
    ----------
    server_address : str
        ComfyUI server address (default: ``127.0.0.1:8188``).
    output_root : str | Path
        Root directory for saving downloaded outputs.
    connect_timeout : float
        Timeout in seconds for HTTP connections.
    ws_timeout : float
        Timeout in seconds for WebSocket recv operations.
    max_execution_time : float
        Maximum time in seconds to wait for workflow execution.
    """

    def __init__(
        self,
        server_address: str = "127.0.0.1:8188",
        output_root: str | Path | None = None,
        connect_timeout: float = 10.0,
        ws_timeout: float = 600.0,
        max_execution_time: float = 1800.0,
    ) -> None:
        self.server_address = server_address
        self.connect_timeout = connect_timeout
        self.ws_timeout = ws_timeout
        self.max_execution_time = max_execution_time

        if output_root is None:
            project_root = Path(__file__).resolve().parents[2]
            self.output_root = project_root / "outputs" / "comfyui_renders"
        else:
            self.output_root = Path(output_root)

        # SESSION-091 (P1-AI-2E): Workflow cache and reload resilience.
        # Cached workflow payloads are invalidated when a backend is reloaded
        # to prevent stale SparseCtrl parameters from persisting.
        self._workflow_cache: dict[str, dict[str, Any]] = {}
        self._safe_point_lock: Any = None  # Lazy-loaded to avoid circular import

    # ------------------------------------------------------------------
    # SESSION-091 (P1-AI-2E): Hot-Reload Resilience
    # ------------------------------------------------------------------

    def on_backend_reload(self, backend_name: str) -> None:
        """Callback for backend hot-reload — invalidate cached workflows.

        SESSION-091 (P1-AI-2E): When a backend is reloaded (e.g.,
        MotionAdaptiveKeyframeBackend with new SparseCtrl parameters),
        any cached workflow payloads that reference that backend must be
        purged.  This prevents the Stale Cache Leak anti-pattern.
        """
        # Purge all workflow caches that reference this backend
        keys_to_purge = [
            k for k in self._workflow_cache
            if backend_name in k
        ]
        for k in keys_to_purge:
            del self._workflow_cache[k]

        # Also purge any generic cached workflows
        if backend_name in self._workflow_cache:
            del self._workflow_cache[backend_name]

        logger.info(
            "[ComfyUIClient] on_backend_reload: purged %d cached workflows "
            "for backend '%s'",
            len(keys_to_purge),
            backend_name,
        )

    def cache_workflow(self, key: str, payload: dict[str, Any]) -> None:
        """Cache a workflow payload for reuse."""
        self._workflow_cache[key] = payload

    def get_cached_workflow(self, key: str) -> dict[str, Any] | None:
        """Retrieve a cached workflow payload, or None if invalidated."""
        return self._workflow_cache.get(key)

    def set_safe_point_lock(self, lock: Any) -> None:
        """Inject the SafePointExecutionLock for frame-boundary coordination.

        SESSION-092 removed the incorrect batch-wide outer fence from this
        client.  The lock is still injectable for callers that need shared
        observability, but frame-boundary polling must happen in the actual
        multi-frame render loop instead of around ``execute_workflow()``.
        """
        self._safe_point_lock = lock

    def execute_workflow_safe(
        self,
        payload: dict[str, Any],
        *,
        backend_name: str = "comfyui",
        run_label: str = "mathart",
    ) -> "ExecutionResult":
        """Execute a workflow without reintroducing a batch-wide coarse lock.

        SESSION-092 hotfix: frame-boundary Safe Point checks must live inside
        the caller's per-frame loop.  Wrapping the entire workflow call in a
        single outer fence would recreate the original lock-granularity bug.
        """
        _ = backend_name  # kept for backward-compatible call signatures
        return self.execute_workflow(payload, run_label=run_label)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def is_server_online(self) -> bool:
        """Check if the ComfyUI server is reachable.

        Returns ``False`` on any network error — never raises.
        """
        try:
            url = f"http://{self.server_address}/system_stats"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                return resp.status == 200
        except (
            ConnectionRefusedError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
            Exception,
        ):
            return False

    # ------------------------------------------------------------------
    # Core execution: POST /prompt → WebSocket → /history → /view
    # ------------------------------------------------------------------

    def execute_workflow(
        self,
        payload: dict[str, Any],
        *,
        run_label: str = "mathart",
    ) -> ExecutionResult:
        """Execute a ComfyUI workflow end-to-end with WebSocket monitoring.

        Parameters
        ----------
        payload : dict
            The assembled payload from ``ComfyUIPresetManager.assemble_payload()``
            or ``assemble_sequence_payload()``.  Must contain ``"prompt"`` and
            ``"client_id"`` keys.
        run_label : str
            Human-readable label for this execution run (used in output dir name).

        Returns
        -------
        ExecutionResult
            On success: ``success=True`` with downloaded image paths.
            On server offline: ``degraded=True`` with reason.
            On execution error: ``success=False`` with error message.
        """
        t0 = time.monotonic()

        # --- Extract payload components ---
        workflow = payload.get("prompt")
        client_id = payload.get("client_id", str(uuid.uuid4()))

        if workflow is None:
            return ExecutionResult(
                success=False,
                error_message="Payload missing 'prompt' key",
                elapsed_seconds=time.monotonic() - t0,
            )

        # --- Check server availability (graceful degradation) ---
        if not self.is_server_online():
            logger.warning(
                "[ComfyUIClient] Server at %s is offline. "
                "Returning degraded result.",
                self.server_address,
            )
            return ExecutionResult(
                success=False,
                degraded=True,
                degraded_reason=f"ComfyUI server at {self.server_address} is offline",
                elapsed_seconds=time.monotonic() - t0,
            )

        # --- Step 1: POST /prompt ---
        prompt_id = self._queue_prompt(workflow, client_id)
        if prompt_id is None:
            return ExecutionResult(
                success=False,
                error_message="Failed to queue prompt via POST /prompt",
                elapsed_seconds=time.monotonic() - t0,
            )

        logger.info(
            "[ComfyUIClient] Prompt queued: prompt_id=%s, client_id=%s",
            prompt_id,
            client_id,
        )

        # --- Step 2: WebSocket listen for completion ---
        ws_result = self._ws_listen_until_complete(client_id, prompt_id)
        if ws_result.get("error"):
            return ExecutionResult(
                success=False,
                prompt_id=prompt_id,
                error_message=ws_result["error"],
                elapsed_seconds=time.monotonic() - t0,
            )

        # --- Step 3: GET /history/{prompt_id} ---
        history = self._get_history(prompt_id)
        if history is None:
            return ExecutionResult(
                success=False,
                prompt_id=prompt_id,
                error_message="Failed to retrieve history after execution",
                elapsed_seconds=time.monotonic() - t0,
            )

        # --- Step 4: Download outputs via GET /view ---
        output_dir = self._create_output_dir(run_label, prompt_id)
        downloaded = self._download_outputs(history, prompt_id, output_dir)

        elapsed = time.monotonic() - t0
        logger.info(
            "[ComfyUIClient] Execution complete: %d images, %d videos in %.1fs",
            len(downloaded["images"]),
            len(downloaded["videos"]),
            elapsed,
        )

        return ExecutionResult(
            success=True,
            prompt_id=prompt_id,
            output_images=downloaded["images"],
            output_videos=downloaded["videos"],
            output_dir=str(output_dir),
            elapsed_seconds=elapsed,
            node_progress=ws_result.get("progress", {}),
        )

    # ------------------------------------------------------------------
    # Step 1: Queue prompt via HTTP POST
    # ------------------------------------------------------------------

    def _queue_prompt(
        self,
        workflow: dict[str, Any],
        client_id: str,
    ) -> str | None:
        """Submit workflow to ComfyUI via POST /prompt.

        Returns the ``prompt_id`` on success, ``None`` on failure.
        """
        try:
            body = json.dumps({
                "prompt": workflow,
                "client_id": client_id,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"http://{self.server_address}/prompt",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                prompt_id = result.get("prompt_id", "")
                node_errors = result.get("node_errors", {})
                if node_errors:
                    logger.warning(
                        "[ComfyUIClient] Node validation errors: %s",
                        json.dumps(node_errors, indent=2),
                    )
                return prompt_id if prompt_id else None

        except urllib.error.HTTPError as e:
            # Capture the response body for detailed error diagnosis
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            logger.error(
                "[ComfyUIClient] POST /prompt failed: HTTP %s %s\n"
                "Response body: %s",
                e.code, e.reason, error_body[:2000],
            )
            return None
        except (
            ConnectionRefusedError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
            json.JSONDecodeError,
        ) as e:
            logger.error("[ComfyUIClient] POST /prompt failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Step 2: WebSocket listen for execution completion
    # ------------------------------------------------------------------

    def _ws_listen_until_complete(
        self,
        client_id: str,
        prompt_id: str,
    ) -> dict[str, Any]:
        """Connect to WebSocket and listen until execution completes.

        The completion signal is an ``executing`` event with ``node: null``.
        This is the **industrial-standard** approach — NO blind polling.

        Returns
        -------
        dict
            ``{"error": None, "progress": {...}}`` on success.
            ``{"error": "message"}`` on failure.
        """
        try:
            import websocket as ws_lib
        except ImportError:
            logger.warning(
                "[ComfyUIClient] 'websocket-client' not installed. "
                "Falling back to HTTP polling."
            )
            return self._http_poll_until_complete(prompt_id)

        ws_url = f"ws://{self.server_address}/ws?clientId={client_id}"
        progress_data: dict[str, Any] = {}

        try:
            ws = ws_lib.WebSocket()
            ws.settimeout(self.ws_timeout)
            ws.connect(ws_url)
            logger.info("[ComfyUIClient] WebSocket connected: %s", ws_url)

            deadline = time.monotonic() + self.max_execution_time

            while time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except ws_lib.WebSocketTimeoutException:
                    logger.warning("[ComfyUIClient] WebSocket recv timeout")
                    continue

                if isinstance(raw, bytes):
                    # Binary event (preview image) — skip for now
                    if len(raw) >= 4:
                        event_type_id = struct.unpack(">I", raw[:4])[0]
                        logger.debug(
                            "[ComfyUIClient] Binary event type=%d, size=%d",
                            event_type_id,
                            len(raw),
                        )
                    continue

                # JSON event
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = msg.get("type", "")
                data = msg.get("data", {})

                if event_type == "status":
                    queue_remaining = (
                        data.get("status", {})
                        .get("exec_info", {})
                        .get("queue_remaining", "?")
                    )
                    logger.info(
                        "[ComfyUIClient] Queue remaining: %s", queue_remaining
                    )

                elif event_type == "executing":
                    node = data.get("node")
                    msg_prompt_id = data.get("prompt_id", "")

                    if node is None and msg_prompt_id == prompt_id:
                        # *** EXECUTION COMPLETE SIGNAL ***
                        logger.info(
                            "[ComfyUIClient] Execution complete for prompt %s",
                            prompt_id,
                        )
                        ws.close()
                        return {"error": None, "progress": progress_data}
                    elif node is not None:
                        logger.info(
                            "[ComfyUIClient] Executing node: %s", node
                        )

                elif event_type == "executed":
                    node = data.get("node", "")
                    output = data.get("output", {})
                    progress_data[f"node_{node}"] = output
                    logger.info(
                        "[ComfyUIClient] Node %s executed, output keys: %s",
                        node,
                        list(output.keys()),
                    )

                elif event_type == "progress":
                    value = data.get("value", 0)
                    max_val = data.get("max", 0)
                    node = data.get("node", "?")
                    logger.info(
                        "[ComfyUIClient] Progress: %d/%d (node %s)",
                        value,
                        max_val,
                        node,
                    )

                elif event_type == "execution_error":
                    error_msg = data.get("exception_message", "Unknown error")
                    error_node = data.get("node_id", "?")
                    logger.error(
                        "[ComfyUIClient] Execution error in node %s: %s",
                        error_node,
                        error_msg,
                    )
                    ws.close()
                    return {
                        "error": f"Execution error in node {error_node}: {error_msg}"
                    }

            # Deadline exceeded
            ws.close()
            return {"error": f"Execution timed out after {self.max_execution_time}s"}

        except (
            ConnectionRefusedError,
            OSError,
            TimeoutError,
            Exception,
        ) as e:
            logger.warning(
                "[ComfyUIClient] WebSocket connection failed: %s. "
                "Falling back to HTTP polling.",
                e,
            )
            return self._http_poll_until_complete(prompt_id)

    def _http_poll_until_complete(
        self,
        prompt_id: str,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """Fallback: poll GET /history/{prompt_id} until execution completes.

        This is the **last resort** when WebSocket is unavailable.
        It is NOT the preferred method — WebSocket is always preferred.
        """
        deadline = time.monotonic() + self.max_execution_time

        while time.monotonic() < deadline:
            try:
                url = f"http://{self.server_address}/history/{prompt_id}"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                    history = json.loads(resp.read().decode("utf-8"))
                    if prompt_id in history:
                        status = (
                            history[prompt_id]
                            .get("status", {})
                            .get("status_str", "")
                        )
                        if status in ("success", "error"):
                            if status == "error":
                                return {"error": "Execution failed (from history poll)"}
                            return {"error": None, "progress": {}}
            except (
                ConnectionRefusedError,
                urllib.error.URLError,
                OSError,
                TimeoutError,
            ):
                pass

            time.sleep(poll_interval)

        return {"error": f"HTTP polling timed out after {self.max_execution_time}s"}

    # ------------------------------------------------------------------
    # Step 3: Retrieve execution history
    # ------------------------------------------------------------------

    def _get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """Retrieve execution history for a specific prompt_id.

        Returns the history dict on success, ``None`` on failure.
        """
        try:
            url = f"http://{self.server_address}/history/{prompt_id}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                history = json.loads(resp.read().decode("utf-8"))
                return history
        except (
            ConnectionRefusedError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
            json.JSONDecodeError,
        ) as e:
            logger.error("[ComfyUIClient] GET /history failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Step 4: Download outputs via GET /view
    # ------------------------------------------------------------------

    def _create_output_dir(self, run_label: str, prompt_id: str) -> Path:
        """Create a timestamped output directory for this execution run."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        short_id = prompt_id[:8] if prompt_id else "unknown"
        dir_name = f"{timestamp}_{run_label}_{short_id}"
        output_dir = self.output_root / dir_name
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _download_outputs(
        self,
        history: dict[str, Any],
        prompt_id: str,
        output_dir: Path,
    ) -> dict[str, list[str]]:
        """Download all output images and videos from execution history.

        Uses the ``GET /view`` endpoint to retrieve binary image data
        and saves to the project's output directory.

        Returns
        -------
        dict
            ``{"images": [path, ...], "videos": [path, ...]}``
        """
        downloaded: dict[str, list[str]] = {"images": [], "videos": []}

        prompt_history = history.get(prompt_id, {})
        outputs = prompt_history.get("outputs", {})

        for node_id, node_output in outputs.items():
            # Handle image outputs
            if "images" in node_output:
                for img_info in node_output["images"]:
                    filename = img_info.get("filename", "")
                    subfolder = img_info.get("subfolder", "")
                    file_type = img_info.get("type", "output")

                    if not filename:
                        continue

                    local_path = self._download_file(
                        filename, subfolder, file_type, output_dir
                    )
                    if local_path:
                        downloaded["images"].append(local_path)

            # Handle video/GIF outputs (VHS_VideoCombine)
            if "gifs" in node_output:
                for gif_info in node_output["gifs"]:
                    filename = gif_info.get("filename", "")
                    subfolder = gif_info.get("subfolder", "")
                    file_type = gif_info.get("type", "output")

                    if not filename:
                        continue

                    local_path = self._download_file(
                        filename, subfolder, file_type, output_dir
                    )
                    if local_path:
                        downloaded["videos"].append(local_path)

        # Write execution metadata
        meta_path = output_dir / "execution_metadata.json"
        meta = {
            "prompt_id": prompt_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "server": self.server_address,
            "images_downloaded": len(downloaded["images"]),
            "videos_downloaded": len(downloaded["videos"]),
            "output_dir": str(output_dir),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        return downloaded

    def _download_file(
        self,
        filename: str,
        subfolder: str,
        file_type: str,
        output_dir: Path,
    ) -> str | None:
        """Download a single file from ComfyUI via GET /view.

        Returns the local file path on success, ``None`` on failure.
        """
        try:
            params = urllib.parse.urlencode({
                "filename": filename,
                "subfolder": subfolder,
                "type": file_type,
            })
            url = f"http://{self.server_address}/view?{params}"
            req = urllib.request.Request(url, method="GET")

            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                data = resp.read()

            local_path = output_dir / filename
            local_path.write_bytes(data)
            logger.info(
                "[ComfyUIClient] Downloaded: %s (%d bytes)",
                local_path,
                len(data),
            )
            return str(local_path)

        except (
            ConnectionRefusedError,
            urllib.error.URLError,
            OSError,
            TimeoutError,
        ) as e:
            logger.warning(
                "[ComfyUIClient] Failed to download %s: %s", filename, e
            )
            return None


__all__ = ["ComfyUIClient", "ExecutionResult"]
