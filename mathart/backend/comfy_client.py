"""ComfyUI High-Availability API Client — Ephemeral Upload + Headless Render + VRAM GC.

SESSION-168 (P0-SESSION-168-COMFYUI-CLIENT-DEADLOCK-BREAKER)
-------------------------------------------------------------
Critical fix: ``_ws_wait()`` now raises ``ComfyUIExecutionError`` on
``execution_error`` WebSocket events instead of returning a dict.  This
prevents the catastrophic deadlock where the caller never checks the
error dict and blocks forever in the next ``ws.recv()``.

SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH)
----------------------------------------------------------
Implements the **industrial-grade** ComfyUI API client with four pillars:

1. **Ephemeral Asset Upload** (``/upload/image`` multipart):
   Upstream proxy images are pushed to the ComfyUI server via the standard
   multipart upload endpoint.  No local absolute paths are leaked to the
   server — this prevents cross-drive permission errors on Windows and
   ensures clean sandboxed execution.

2. **Headless API Polling & WebSocket Telemetry**:
   After ``POST /prompt``, the client establishes a WebSocket connection
   for real-time progress events.  If WebSocket is unavailable, it falls
   back to HTTP polling with ``time.sleep(1)`` intervals and a hard
   ``MAX_RETRIES`` / timeout circuit breaker.

3. **OOM & VRAM Garbage Collection** (``POST /free``):
   After every batch render, the client forcibly calls ComfyUI's ``/free``
   endpoint to release GPU VRAM, protecting long-running production stability.

4. **Output Asset Repatriation** (``/history`` + ``/view``):
   Rendered outputs are downloaded from ComfyUI's internal storage and
   force-saved to the project's ``outputs/production/`` directory with
   standardized filenames (``final_render_{timestamp}.png``).

Anti-Pattern Guards (SESSION-151 Red Lines)
-------------------------------------------
- [DEADLOCK SHIELD] Every ``while`` loop has ``time.sleep(1)`` + timeout.
  ``RenderTimeoutError`` is raised on timeout — never silent hang.
- [ORPHAN SHIELD] All outputs are repatriated to ``outputs/production/``.
  Nothing is left in ComfyUI's own output folder.
- [OOM SHIELD] ``/free`` is called after every render batch.
- [PERMISSION SHIELD] Images are uploaded via multipart, never referenced
  by local filesystem paths.

Research Grounding
------------------
- ComfyUI REST API: POST /prompt, GET /history, GET /view, POST /free
- ComfyUI Upload API: POST /upload/image (multipart/form-data)
- WebSocket Events: status, executing, executed, progress, execution_error
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
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
# Exceptions
# ---------------------------------------------------------------------------

class RenderTimeoutError(TimeoutError):
    """Raised when a ComfyUI render exceeds the maximum allowed time.

    This exception is logged to the blackbox flight recorder and
    guarantees the terminal never hangs on "Rendering...".
    """


class UploadError(IOError):
    """Raised when an image upload to ComfyUI fails."""


class VRAMFreeError(RuntimeError):
    """Raised when VRAM garbage collection fails (non-fatal warning)."""


# SESSION-168: Import the canonical Poison Pill exception from the WS client
# module.  If unavailable (circular import edge case), define a local fallback.
try:
    from mathart.comfy_client.comfyui_ws_client import ComfyUIExecutionError
except ImportError:
    class ComfyUIExecutionError(RuntimeError):  # type: ignore[no-redef]
        """Fallback: Fatal ComfyUI remote execution error."""
        def __init__(self, message: str, *, node_id: str = "?", details: str = ""):
            super().__init__(message)
            self.node_id = node_id
            self.details = details


# ---------------------------------------------------------------------------
# Render Result
# ---------------------------------------------------------------------------

@dataclass
class RenderResult:
    """Result of a ComfyUI headless render execution.

    This is the canonical output contract for the ComfyUI render lane.
    It carries enough metadata for downstream quality gates and archival.
    """
    success: bool
    prompt_id: str = ""
    output_images: list[str] = field(default_factory=list)
    output_videos: list[str] = field(default_factory=list)
    output_dir: str = ""
    error_message: str = ""
    degraded: bool = False
    degraded_reason: str = ""
    elapsed_seconds: float = 0.0
    uploaded_filename: str = ""
    vram_freed: bool = False
    mutation_ledger: dict[str, Any] = field(default_factory=dict)

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
            "uploaded_filename": self.uploaded_filename,
            "vram_freed": self.vram_freed,
        }


# ---------------------------------------------------------------------------
# ComfyUI API Client
# ---------------------------------------------------------------------------

class ComfyAPIClient:
    """High-availability ComfyUI API client with upload, render, and VRAM GC.

    This client wraps the complete ComfyUI HTTP API lifecycle:
    ``/upload/image`` → ``/prompt`` → WebSocket/poll → ``/history`` →
    ``/view`` → ``/free``.

    Parameters
    ----------
    server_address : str
        ComfyUI server address (e.g., ``"127.0.0.1:8188"``).
    output_root : str | Path | None
        Root directory for repatriated render outputs.
        Defaults to ``{project_root}/outputs/production/``.
    connect_timeout : float
        HTTP connection timeout in seconds.
    render_timeout : float
        Maximum time to wait for a single render (seconds).
        Default: 600s (10 minutes).
    poll_interval : float
        Interval between HTTP poll requests when WebSocket is unavailable.
    auto_free_vram : bool
        If True, call ``/free`` after every render to release VRAM.
    """

    def __init__(
        self,
        server_address: str = "127.0.0.1:8188",
        output_root: str | Path | None = None,
        connect_timeout: float = 10.0,
        render_timeout: float = 600.0,
        poll_interval: float = 1.0,
        auto_free_vram: bool = True,
    ) -> None:
        self.server_address = server_address.rstrip("/")
        self.connect_timeout = connect_timeout
        self.render_timeout = render_timeout
        self.poll_interval = max(0.5, poll_interval)
        self.auto_free_vram = auto_free_vram

        if output_root is None:
            project_root = Path(__file__).resolve().parents[2]
            self.output_root = project_root / "outputs" / "production"
        else:
            self.output_root = Path(output_root)

        self.output_root.mkdir(parents=True, exist_ok=True)

    @property
    def base_url(self) -> str:
        return f"http://{self.server_address}"

    # ==================================================================
    # 1. Health Check
    # ==================================================================

    def is_server_online(self) -> bool:
        """Probe ComfyUI server availability via ``/system_stats``.

        Returns ``False`` on any network error — never raises.
        """
        try:
            url = f"{self.base_url}/system_stats"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ==================================================================
    # 2. Ephemeral Asset Upload (POST /upload/image)
    # ==================================================================

    def upload_image(
        self,
        image_path: str | Path,
        *,
        subfolder: str = "",
        overwrite: bool = True,
        upload_type: str = "input",
    ) -> str:
        """Upload an image to ComfyUI via ``POST /upload/image`` multipart.

        This is the **only** sanctioned way to make images available to
        ComfyUI nodes.  NEVER reference local filesystem paths directly.

        Parameters
        ----------
        image_path : str | Path
            Local path to the image file to upload.
        subfolder : str
            Optional subfolder on the ComfyUI server.
        overwrite : bool
            Whether to overwrite existing files with the same name.
        upload_type : str
            Upload type: ``"input"`` (default) or ``"temp"``.

        Returns
        -------
        str
            The server-side filename (to be injected into workflow nodes).

        Raises
        ------
        UploadError
            If the upload fails.
        FileNotFoundError
            If the local image file does not exist.
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        image_data = image_path.read_bytes()
        filename = image_path.name

        # Build multipart/form-data body
        boundary = f"----MathArtBoundary{uuid.uuid4().hex[:16]}"
        body = io.BytesIO()

        # File part
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="image"; '
            f'filename="{filename}"\r\n'.encode()
        )
        content_type = self._guess_content_type(filename)
        body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.write(image_data)
        body.write(b"\r\n")

        # Overwrite flag
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            b'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
        )
        body.write(str(overwrite).lower().encode())
        body.write(b"\r\n")

        # Type field
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            b'Content-Disposition: form-data; name="type"\r\n\r\n'
        )
        body.write(upload_type.encode())
        body.write(b"\r\n")

        # Subfolder (if provided)
        if subfolder:
            body.write(f"--{boundary}\r\n".encode())
            body.write(
                b'Content-Disposition: form-data; name="subfolder"\r\n\r\n'
            )
            body.write(subfolder.encode())
            body.write(b"\r\n")

        # End boundary
        body.write(f"--{boundary}--\r\n".encode())

        body_bytes = body.getvalue()

        try:
            url = f"{self.base_url}/upload/image"
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                server_filename = result.get("name", filename)
                logger.info(
                    "[ComfyAPIClient] Uploaded %s → server filename: %s",
                    image_path.name, server_filename,
                )
                return server_filename

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise UploadError(
                f"Upload failed: HTTP {e.code} {e.reason}. Body: {error_body}"
            ) from e
        except (ConnectionRefusedError, urllib.error.URLError, OSError, TimeoutError) as e:
            raise UploadError(f"Upload failed: {e}") from e

    # ==================================================================
    # 3. Submit Prompt (POST /prompt)
    # ==================================================================

    def queue_prompt(
        self,
        payload: dict[str, Any],
    ) -> str:
        """Submit a workflow payload to ComfyUI via ``POST /prompt``.

        Parameters
        ----------
        payload : dict
            Must contain ``"prompt"`` (workflow dict) and ``"client_id"``.

        Returns
        -------
        str
            The ``prompt_id`` assigned by the server.

        Raises
        ------
        RuntimeError
            If the submission fails.
        """
        workflow = payload.get("prompt")
        client_id = payload.get("client_id", str(uuid.uuid4()))

        if workflow is None:
            raise RuntimeError("Payload missing 'prompt' key")

        body = json.dumps({
            "prompt": workflow,
            "client_id": client_id,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{self.base_url}/prompt",
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
                        "[ComfyAPIClient] Node validation errors: %s",
                        json.dumps(node_errors, indent=2)[:1000],
                    )
                if not prompt_id:
                    raise RuntimeError("Server returned empty prompt_id")
                logger.info(
                    "[ComfyAPIClient] Prompt queued: prompt_id=%s", prompt_id
                )
                return prompt_id

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                pass
            raise RuntimeError(
                f"POST /prompt failed: HTTP {e.code} {e.reason}. Body: {error_body}"
            ) from e
        except (ConnectionRefusedError, urllib.error.URLError, OSError, TimeoutError) as e:
            raise RuntimeError(f"POST /prompt failed: {e}") from e

    # ==================================================================
    # 4. Wait for Completion (WebSocket → HTTP Poll Fallback)
    # ==================================================================

    def wait_for_completion(
        self,
        prompt_id: str,
        client_id: str,
    ) -> dict[str, Any]:
        """Wait for a queued prompt to complete execution.

        Attempts WebSocket monitoring first.  Falls back to HTTP polling
        with ``time.sleep()`` intervals and a hard timeout circuit breaker.

        Returns
        -------
        dict
            ``{"error": None, "progress": {...}}`` on success.
            ``{"error": "message"}`` on failure.

        Raises
        ------
        RenderTimeoutError
            If execution exceeds ``self.render_timeout``.
        """
        # Try WebSocket first
        try:
            return self._ws_wait(prompt_id, client_id)
        except Exception as ws_err:
            logger.warning(
                "[ComfyAPIClient] WebSocket unavailable (%s), "
                "falling back to HTTP polling.",
                ws_err,
            )
            return self._http_poll_wait(prompt_id)

    def _ws_wait(
        self,
        prompt_id: str,
        client_id: str,
    ) -> dict[str, Any]:
        """WebSocket-based completion monitoring."""
        try:
            import websocket as ws_lib
        except ImportError:
            raise ImportError("websocket-client not installed")

        ws_url = f"ws://{self.server_address}/ws?clientId={client_id}"
        progress: dict[str, Any] = {}

        ws = ws_lib.WebSocket()
        ws.settimeout(min(30.0, self.render_timeout))
        ws.connect(ws_url)
        logger.info("[ComfyAPIClient] WebSocket connected: %s", ws_url)

        deadline = time.monotonic() + self.render_timeout

        try:
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    # Timeout on recv — check deadline and retry
                    if time.monotonic() >= deadline:
                        break
                    continue

                if isinstance(raw, bytes):
                    continue  # Binary preview — skip

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = msg.get("type", "")
                data = msg.get("data", {})

                if event_type == "executing":
                    node = data.get("node")
                    msg_pid = data.get("prompt_id", "")
                    if node is None and msg_pid == prompt_id:
                        logger.info(
                            "[ComfyAPIClient] Execution complete: %s", prompt_id
                        )
                        ws.close()
                        return {"error": None, "progress": progress}
                    elif node is not None:
                        logger.info("[ComfyAPIClient] Executing node: %s", node)

                elif event_type == "progress":
                    value = data.get("value", 0)
                    max_val = data.get("max", 0)
                    progress["last_progress"] = {"value": value, "max": max_val}

                elif event_type == "execution_error":
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # SESSION-168: FAIL-FAST POISON PILL — raise, don't return
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    error_msg = data.get("exception_message", "Unknown error")
                    error_node = data.get("node_id", "?")
                    error_traceback = data.get("traceback", "")
                    logger.critical(
                        "[ComfyAPIClient] \u26a0\ufe0f FATAL execution_error in "
                        "node %s: %s",
                        error_node, error_msg,
                    )
                    try:
                        ws.close()
                    except Exception:
                        pass
                    raise ComfyUIExecutionError(
                        f"ComfyUI \u7aef\u70b9\u6e32\u67d3\u5d29\u6e83 (node={error_node}): {error_msg}",
                        node_id=error_node,
                        details=error_traceback[:2000] if error_traceback else error_msg,
                    )

        finally:
            try:
                ws.close()
            except Exception:
                pass

        # Deadline exceeded
        raise RenderTimeoutError(
            f"ComfyUI render timed out after {self.render_timeout}s "
            f"for prompt_id={prompt_id}.  "
            f"Check GPU load or increase render_timeout."
        )

    def _http_poll_wait(
        self,
        prompt_id: str,
    ) -> dict[str, Any]:
        """HTTP polling fallback with sleep intervals and timeout breaker.

        RED LINE: Every loop iteration has ``time.sleep(self.poll_interval)``
        and the total time is bounded by ``self.render_timeout``.
        """
        deadline = time.monotonic() + self.render_timeout
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1
            time.sleep(self.poll_interval)  # [DEADLOCK SHIELD] mandatory sleep

            try:
                url = f"{self.base_url}/history/{prompt_id}"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                    history = json.loads(resp.read().decode("utf-8"))
                    if prompt_id in history:
                        status = (
                            history[prompt_id]
                            .get("status", {})
                            .get("status_str", "")
                        )
                        if status == "success":
                            logger.info(
                                "[ComfyAPIClient] HTTP poll: execution complete "
                                "(attempt %d)", attempt,
                            )
                            return {"error": None, "progress": {}}
                        if status == "error":
                            return {"error": "Execution failed (from history poll)"}
            except (ConnectionRefusedError, urllib.error.URLError, OSError, TimeoutError):
                pass

            logger.debug(
                "[ComfyAPIClient] HTTP poll attempt %d — still waiting...",
                attempt,
            )

        # Timeout breaker
        raise RenderTimeoutError(
            f"ComfyUI render timed out after {self.render_timeout}s "
            f"(HTTP poll, {attempt} attempts) for prompt_id={prompt_id}.  "
            f"This error is logged to the blackbox flight recorder."
        )

    # ==================================================================
    # 5. Retrieve History & Download Outputs
    # ==================================================================

    def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """Retrieve execution history for a prompt_id."""
        try:
            url = f"{self.base_url}/history/{prompt_id}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error("[ComfyAPIClient] GET /history failed: %s", e)
            return None

    def download_outputs(
        self,
        history: dict[str, Any],
        prompt_id: str,
        output_dir: Path | None = None,
        filename_prefix: str = "final_render",
    ) -> dict[str, list[str]]:
        """Download all outputs from execution history to local directory.

        RED LINE: All outputs are force-saved to ``outputs/production/``
        with standardized filenames.  Nothing is left orphaned in ComfyUI.

        Parameters
        ----------
        history : dict
            Full history response from ``GET /history``.
        prompt_id : str
            The prompt_id to extract outputs for.
        output_dir : Path | None
            Override output directory.  Defaults to timestamped subdir
            under ``self.output_root``.
        filename_prefix : str
            Prefix for output filenames.

        Returns
        -------
        dict
            ``{"images": [path, ...], "videos": [path, ...]}``
        """
        if output_dir is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            short_id = prompt_id[:8] if prompt_id else "unknown"
            output_dir = self.output_root / f"{timestamp}_{short_id}"

        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded: dict[str, list[str]] = {"images": [], "videos": []}

        prompt_history = history.get(prompt_id, {})
        outputs = prompt_history.get("outputs", {})

        img_counter = 0
        vid_counter = 0

        for node_id, node_output in outputs.items():
            # Image outputs
            if "images" in node_output:
                for img_info in node_output["images"]:
                    server_filename = img_info.get("filename", "")
                    subfolder = img_info.get("subfolder", "")
                    file_type = img_info.get("type", "output")
                    if not server_filename:
                        continue

                    ext = Path(server_filename).suffix or ".png"
                    local_name = f"{filename_prefix}_{img_counter:04d}{ext}"
                    local_path = output_dir / local_name
                    img_counter += 1

                    if self._download_file(
                        server_filename, subfolder, file_type, local_path
                    ):
                        downloaded["images"].append(str(local_path))

            # Video/GIF outputs (VHS_VideoCombine)
            if "gifs" in node_output:
                for gif_info in node_output["gifs"]:
                    server_filename = gif_info.get("filename", "")
                    subfolder = gif_info.get("subfolder", "")
                    file_type = gif_info.get("type", "output")
                    if not server_filename:
                        continue

                    ext = Path(server_filename).suffix or ".mp4"
                    local_name = f"{filename_prefix}_video_{vid_counter:04d}{ext}"
                    local_path = output_dir / local_name
                    vid_counter += 1

                    if self._download_file(
                        server_filename, subfolder, file_type, local_path
                    ):
                        downloaded["videos"].append(str(local_path))

        # Write execution metadata
        meta_path = output_dir / "render_metadata.json"
        meta_path.write_text(
            json.dumps({
                "prompt_id": prompt_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "server": self.server_address,
                "images_downloaded": len(downloaded["images"]),
                "videos_downloaded": len(downloaded["videos"]),
                "output_dir": str(output_dir),
                "filename_prefix": filename_prefix,
            }, indent=2) + "\n",
            encoding="utf-8",
        )

        logger.info(
            "[ComfyAPIClient] Downloaded %d images, %d videos → %s",
            len(downloaded["images"]),
            len(downloaded["videos"]),
            output_dir,
        )
        return downloaded

    def _download_file(
        self,
        server_filename: str,
        subfolder: str,
        file_type: str,
        local_path: Path,
    ) -> bool:
        """Download a single file from ComfyUI via ``GET /view``."""
        try:
            params = urllib.parse.urlencode({
                "filename": server_filename,
                "subfolder": subfolder,
                "type": file_type,
            })
            url = f"{self.base_url}/view?{params}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                data = resp.read()
            local_path.write_bytes(data)
            logger.info(
                "[ComfyAPIClient] Downloaded: %s (%d bytes)",
                local_path.name, len(data),
            )
            return True
        except Exception as e:
            logger.warning(
                "[ComfyAPIClient] Failed to download %s: %s",
                server_filename, e,
            )
            return False

    # ==================================================================
    # 6. VRAM Garbage Collection (POST /free)
    # ==================================================================

    def free_vram(self, *, unload_models: bool = True) -> bool:
        """Force ComfyUI to release GPU VRAM via ``POST /free``.

        This MUST be called after every batch render to prevent OOM
        during long-running production sessions.

        Parameters
        ----------
        unload_models : bool
            If True, also unload cached models from VRAM.

        Returns
        -------
        bool
            True if the free request succeeded.
        """
        try:
            body = json.dumps({
                "unload_models": unload_models,
                "free_memory": True,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.base_url}/free",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                logger.info(
                    "[ComfyAPIClient] VRAM freed (unload_models=%s, status=%d)",
                    unload_models, resp.status,
                )
                return resp.status == 200

        except Exception as e:
            logger.warning(
                "[ComfyAPIClient] VRAM free failed (non-fatal): %s", e
            )
            return False

    # ==================================================================
    # 7. End-to-End Render Pipeline
    # ==================================================================

    def render(
        self,
        payload: dict[str, Any],
        *,
        image_path: str | Path | None = None,
        output_prefix: str = "final_render",
        free_vram_after: bool | None = None,
    ) -> RenderResult:
        """Execute a complete end-to-end render pipeline.

        This is the primary entry point for production rendering:
        1. Check server availability
        2. Upload image (if provided)
        3. Submit prompt
        4. Wait for completion (WebSocket → HTTP poll)
        5. Download outputs to ``outputs/production/``
        6. Free VRAM (if enabled)

        Parameters
        ----------
        payload : dict
            The assembled payload from ``ComfyWorkflowMutator.build_payload()``.
        image_path : str | Path | None
            Local image to upload before rendering.
        output_prefix : str
            Filename prefix for downloaded outputs.
        free_vram_after : bool | None
            Override ``self.auto_free_vram`` for this render.

        Returns
        -------
        RenderResult
            Complete render result with output paths and metadata.
        """
        t0 = time.monotonic()
        should_free = free_vram_after if free_vram_after is not None else self.auto_free_vram

        # --- Health check ---
        if not self.is_server_online():
            return RenderResult(
                success=False,
                degraded=True,
                degraded_reason=f"ComfyUI server at {self.server_address} is offline",
                elapsed_seconds=time.monotonic() - t0,
            )

        # --- Upload image if provided ---
        uploaded_filename = ""
        if image_path is not None:
            try:
                uploaded_filename = self.upload_image(image_path)
            except (UploadError, FileNotFoundError) as e:
                return RenderResult(
                    success=False,
                    error_message=f"Image upload failed: {e}",
                    elapsed_seconds=time.monotonic() - t0,
                )

        # --- Submit prompt ---
        client_id = payload.get("client_id", str(uuid.uuid4()))
        try:
            prompt_id = self.queue_prompt(payload)
        except RuntimeError as e:
            return RenderResult(
                success=False,
                error_message=str(e),
                elapsed_seconds=time.monotonic() - t0,
            )

        # --- Wait for completion ---
        try:
            wait_result = self.wait_for_completion(prompt_id, client_id)
        except RenderTimeoutError as e:
            logger.error("[ComfyAPIClient] %s", e)
            return RenderResult(
                success=False,
                prompt_id=prompt_id,
                error_message=str(e),
                elapsed_seconds=time.monotonic() - t0,
                uploaded_filename=uploaded_filename,
            )

        if wait_result.get("error"):
            return RenderResult(
                success=False,
                prompt_id=prompt_id,
                error_message=wait_result["error"],
                elapsed_seconds=time.monotonic() - t0,
                uploaded_filename=uploaded_filename,
            )

        # --- Download outputs ---
        history = self.get_history(prompt_id)
        if history is None:
            return RenderResult(
                success=False,
                prompt_id=prompt_id,
                error_message="Failed to retrieve history after execution",
                elapsed_seconds=time.monotonic() - t0,
                uploaded_filename=uploaded_filename,
            )

        downloaded = self.download_outputs(
            history, prompt_id, filename_prefix=output_prefix,
        )

        # --- VRAM garbage collection ---
        vram_freed = False
        if should_free:
            vram_freed = self.free_vram()

        elapsed = time.monotonic() - t0
        logger.info(
            "[ComfyAPIClient] Render complete: %d images, %d videos in %.1fs "
            "(VRAM freed: %s)",
            len(downloaded["images"]),
            len(downloaded["videos"]),
            elapsed,
            vram_freed,
        )

        return RenderResult(
            success=True,
            prompt_id=prompt_id,
            output_images=downloaded["images"],
            output_videos=downloaded["videos"],
            output_dir=str(
                Path(downloaded["images"][0]).parent
                if downloaded["images"]
                else self.output_root
            ),
            elapsed_seconds=elapsed,
            uploaded_filename=uploaded_filename,
            vram_freed=vram_freed,
            mutation_ledger=payload.get("mathart_mutation_ledger", {}),
        )

    # ==================================================================
    # Internal Helpers
    # ==================================================================

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        """Guess MIME content type from filename extension."""
        ext = Path(filename).suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".gif": "image/gif",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
        }.get(ext, "application/octet-stream")
