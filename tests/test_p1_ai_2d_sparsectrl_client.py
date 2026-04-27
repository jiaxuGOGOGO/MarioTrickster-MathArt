"""Offline-safe E2E tests for ComfyUI WebSocket client and pipeline runner.

SESSION-087 (P1-AI-2D-SPARSECTRL endpoint closure)
----------------------------------------------------
These tests verify the ComfyUIClient and run_sparsectrl_pipeline without
any live ComfyUI server.  All HTTP and WebSocket interactions are deeply
mocked using ``unittest.mock``.

Test categories:
1. **Client construction & health check** — verify defaults, offline detection.
2. **Graceful degradation** — server offline → degraded result, no crash.
3. **WebSocket execution flow** — mock full event sequence through completion.
4. **HTTP fallback polling** — when websocket-client is not installed.
5. **History retrieval & image download** — mock /history and /view endpoints.
6. **Pipeline runner phases** — guide generation, payload assembly, execution.
7. **Execution error handling** — server returns execution_error event.
8. **Payload validation** — assembled payload has correct structure.

Anti-pattern guards verified:
- 🚫 Blind HTTP POST Trap: Tests verify WebSocket is used, not sleep polling.
- 🚫 Offline Crash Trap: Tests verify ConnectionRefusedError → graceful skip.
- 🚫 Orphan Output Trap: Tests verify images are downloaded to project dir.
- 🚫 CI HTTP Blocking Trap: ZERO real HTTP calls in all tests.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mathart.comfy_client.comfyui_ws_client import ComfyUIClient, ComfyUIExecutionError, ExecutionResult
from mathart.animation.comfyui_preset_manager import (
    ComfyUIPresetManager,
    _SPARSECTRL_SELECTORS,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 1: Client Construction & Defaults
# ═══════════════════════════════════════════════════════════════════════════

class TestComfyUIClientConstruction(unittest.TestCase):
    """Verify ComfyUIClient construction and default configuration."""

    def test_default_server_address(self):
        client = ComfyUIClient()
        self.assertEqual(client.server_address, "127.0.0.1:8188")

    def test_custom_server_address(self):
        client = ComfyUIClient(server_address="192.168.1.100:9090")
        self.assertEqual(client.server_address, "192.168.1.100:9090")

    def test_default_output_root(self):
        client = ComfyUIClient()
        self.assertTrue(str(client.output_root).endswith("comfyui_renders"))

    def test_custom_output_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = ComfyUIClient(output_root=tmp)
            self.assertEqual(str(client.output_root), tmp)

    def test_default_timeouts(self):
        client = ComfyUIClient()
        self.assertEqual(client.connect_timeout, 10.0)
        self.assertEqual(client.ws_timeout, 600.0)
        self.assertEqual(client.max_execution_time, 1800.0)

    def test_custom_timeouts(self):
        client = ComfyUIClient(
            connect_timeout=5.0,
            ws_timeout=120.0,
            max_execution_time=300.0,
        )
        self.assertEqual(client.connect_timeout, 5.0)
        self.assertEqual(client.ws_timeout, 120.0)
        self.assertEqual(client.max_execution_time, 300.0)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 2: Health Check & Offline Detection
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthCheck(unittest.TestCase):
    """Verify server health check with graceful offline handling."""

    @mock.patch("urllib.request.urlopen")
    def test_server_online(self, mock_urlopen):
        """Server responds 200 → is_server_online() returns True."""
        mock_resp = mock.MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        client = ComfyUIClient()
        self.assertTrue(client.is_server_online())

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_server_offline_connection_refused(self, mock_urlopen):
        """ConnectionRefusedError → is_server_online() returns False, no crash."""
        client = ComfyUIClient()
        self.assertFalse(client.is_server_online())

    @mock.patch("urllib.request.urlopen", side_effect=OSError("Network unreachable"))
    def test_server_offline_os_error(self, mock_urlopen):
        """OSError → is_server_online() returns False, no crash."""
        client = ComfyUIClient()
        self.assertFalse(client.is_server_online())

    @mock.patch("urllib.request.urlopen", side_effect=TimeoutError)
    def test_server_offline_timeout(self, mock_urlopen):
        """TimeoutError → is_server_online() returns False, no crash."""
        client = ComfyUIClient()
        self.assertFalse(client.is_server_online())


# ═══════════════════════════════════════════════════════════════════════════
#  Test 3: Graceful Degradation (Server Offline)
# ═══════════════════════════════════════════════════════════════════════════

class TestGracefulDegradation(unittest.TestCase):
    """Verify the client degrades gracefully when server is offline.

    This is the critical test for the 'Offline Crash Trap' red line.
    """

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_execute_workflow_offline_returns_degraded(self, mock_urlopen):
        """Server offline → degraded result, NOT an exception."""
        client = ComfyUIClient()
        payload = {
            "prompt": {"1": {"class_type": "Test"}},
            "client_id": "test-client",
        }
        result = client.execute_workflow(payload)

        self.assertFalse(result.success)
        self.assertTrue(result.degraded)
        self.assertIn("offline", result.degraded_reason.lower())

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_execute_workflow_offline_no_exception(self, mock_urlopen):
        """Server offline → no exception raised."""
        client = ComfyUIClient()
        payload = {
            "prompt": {"1": {"class_type": "Test"}},
            "client_id": "test-client",
        }
        # This MUST NOT raise
        try:
            result = client.execute_workflow(payload)
        except Exception as e:
            self.fail(f"execute_workflow raised {type(e).__name__}: {e}")

    def test_execute_workflow_missing_prompt_key(self):
        """Payload without 'prompt' key → error result, no crash."""
        client = ComfyUIClient()
        result = client.execute_workflow({"client_id": "test"})
        self.assertFalse(result.success)
        self.assertIn("prompt", result.error_message.lower())

    def test_execute_workflow_batch_polls_between_tasks(self):
        """批量执行必须在任务间隙轮询安全点，而不是用粗粒度大锁包住整个批次。"""
        client = ComfyUIClient()
        payloads = [
            {"prompt": {"1": {"class_type": "Test"}}, "client_id": "task-a"},
            {"prompt": {"1": {"class_type": "Test"}}, "client_id": "task-b"},
        ]
        call_log: list[tuple[str, object]] = []

        def fake_execute_workflow_safe(payload, *, backend_name="comfyui", run_label="mathart", progress_callback=None):
            call_log.append(("execute", payload["client_id"]))
            return ExecutionResult(success=True, prompt_id=payload["client_id"])

        def fake_poll_safe_point(*, frame_index=None):
            call_log.append(("poll", frame_index))

        client.execute_workflow_safe = mock.MagicMock(side_effect=fake_execute_workflow_safe)

        results = client.execute_workflow_batch(
            payloads,
            backend_name="dummy_hot_backend",
            run_label="batch_run",
            poll_safe_point=fake_poll_safe_point,
        )

        self.assertEqual(
            call_log,
            [
                ("execute", "task-a"),
                ("poll", 0),
                ("execute", "task-b"),
                ("poll", 1),
            ],
        )
        self.assertEqual([r.prompt_id for r in results], ["task-a", "task-b"])

    @mock.patch("urllib.request.urlopen", side_effect=OSError("Connection refused"))
    def test_execute_workflow_os_error_degraded(self, mock_urlopen):
        """OSError → degraded result."""
        client = ComfyUIClient()
        payload = {
            "prompt": {"1": {"class_type": "Test"}},
            "client_id": "test-client",
        }
        result = client.execute_workflow(payload)
        self.assertFalse(result.success)
        self.assertTrue(result.degraded)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 4: POST /prompt Queue Submission
# ═══════════════════════════════════════════════════════════════════════════

class TestQueuePrompt(unittest.TestCase):
    """Verify POST /prompt submission logic."""

    def _make_mock_response(self, data: dict, status: int = 200):
        mock_resp = mock.MagicMock()
        mock_resp.status = status
        mock_resp.read.return_value = json.dumps(data).encode("utf-8")
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        return mock_resp

    @mock.patch("urllib.request.urlopen")
    def test_queue_prompt_success(self, mock_urlopen):
        """Successful POST /prompt returns prompt_id."""
        mock_urlopen.return_value = self._make_mock_response({
            "prompt_id": "abc-123-def",
            "number": 1,
            "node_errors": {},
        })

        client = ComfyUIClient()
        prompt_id = client._queue_prompt(
            {"1": {"class_type": "Test"}},
            "test-client",
        )
        self.assertEqual(prompt_id, "abc-123-def")

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_queue_prompt_offline(self, mock_urlopen):
        """POST /prompt when offline returns None, no crash."""
        client = ComfyUIClient()
        prompt_id = client._queue_prompt(
            {"1": {"class_type": "Test"}},
            "test-client",
        )
        self.assertIsNone(prompt_id)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 5: WebSocket Execution Flow (Full Mock)
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSocketExecution(unittest.TestCase):
    """Verify WebSocket-based execution monitoring.

    This is the critical test for the 'Blind HTTP POST Trap' red line.
    We mock the entire WebSocket interaction to verify the client correctly
    handles the event sequence: status → executing → progress → executed →
    executing(null) → completion.
    """

    def _build_ws_messages(self, prompt_id: str) -> list[str]:
        """Build a realistic sequence of WebSocket JSON messages."""
        return [
            json.dumps({
                "type": "status",
                "data": {
                    "status": {"exec_info": {"queue_remaining": 1}},
                    "sid": "test-session",
                },
            }),
            json.dumps({
                "type": "executing",
                "data": {
                    "node": "1",
                    "display_node": "1",
                    "prompt_id": prompt_id,
                },
            }),
            json.dumps({
                "type": "progress",
                "data": {
                    "value": 5,
                    "max": 20,
                    "prompt_id": prompt_id,
                    "node": "3",
                },
            }),
            json.dumps({
                "type": "progress",
                "data": {
                    "value": 20,
                    "max": 20,
                    "prompt_id": prompt_id,
                    "node": "3",
                },
            }),
            json.dumps({
                "type": "executed",
                "data": {
                    "node": "9",
                    "display_node": "9",
                    "output": {
                        "images": [
                            {
                                "filename": "ComfyUI_00001_.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    },
                    "prompt_id": prompt_id,
                },
            }),
            # *** COMPLETION SIGNAL: executing with node=null ***
            json.dumps({
                "type": "executing",
                "data": {
                    "node": None,
                    "prompt_id": prompt_id,
                },
            }),
        ]

    @mock.patch("mathart.comfy_client.comfyui_ws_client.ComfyUIClient.is_server_online", return_value=True)
    def test_ws_listen_complete_flow(self, mock_online):
        """Full WebSocket event sequence → successful completion."""
        prompt_id = "test-prompt-id-12345"
        messages = self._build_ws_messages(prompt_id)

        mock_ws_module = mock.MagicMock()
        mock_ws_instance = mock.MagicMock()
        mock_ws_module.WebSocket.return_value = mock_ws_instance
        mock_ws_instance.recv = mock.MagicMock(side_effect=messages)

        with mock.patch.dict("sys.modules", {"websocket": mock_ws_module}):
            client = ComfyUIClient()
            result = client._ws_listen_until_complete("test-client", prompt_id)

        self.assertIsNone(result.get("error"))
        self.assertIn("progress", result)

    @mock.patch("mathart.comfy_client.comfyui_ws_client.ComfyUIClient.is_server_online", return_value=True)
    def test_ws_execution_error_event(self, mock_online):
        """WebSocket execution_error event → error result."""
        prompt_id = "test-prompt-error"
        messages = [
            json.dumps({
                "type": "status",
                "data": {"status": {"exec_info": {"queue_remaining": 1}}},
            }),
            json.dumps({
                "type": "execution_error",
                "data": {
                    "prompt_id": prompt_id,
                    "node_id": "3",
                    "exception_message": "CUDA out of memory",
                    "exception_type": "RuntimeError",
                },
            }),
        ]

        mock_ws_module = mock.MagicMock()
        mock_ws_instance = mock.MagicMock()
        mock_ws_module.WebSocket.return_value = mock_ws_instance
        mock_ws_instance.recv = mock.MagicMock(side_effect=messages)

        with mock.patch.dict("sys.modules", {"websocket": mock_ws_module}):
            client = ComfyUIClient()
            with self.assertRaises(ComfyUIExecutionError) as exc:
                client._ws_listen_until_complete("test-client", prompt_id)

        self.assertIn("CUDA out of memory", str(exc.exception))


# ═══════════════════════════════════════════════════════════════════════════
#  Test 6: HTTP Fallback Polling
# ═══════════════════════════════════════════════════════════════════════════

class TestHTTPFallbackPolling(unittest.TestCase):
    """Verify HTTP polling fallback when websocket-client is unavailable."""

    def _make_mock_response(self, data: dict, status: int = 200):
        mock_resp = mock.MagicMock()
        mock_resp.status = status
        mock_resp.read.return_value = json.dumps(data).encode("utf-8")
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        return mock_resp

    @mock.patch("urllib.request.urlopen")
    def test_http_poll_success(self, mock_urlopen):
        """HTTP poll finds completed history → success."""
        prompt_id = "test-poll-id"
        mock_urlopen.return_value = self._make_mock_response({
            prompt_id: {
                "status": {"status_str": "success", "completed": True},
                "outputs": {},
            }
        })

        client = ComfyUIClient(max_execution_time=5.0)
        result = client._http_poll_until_complete(prompt_id, poll_interval=0.1)
        self.assertIsNone(result.get("error"))

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_http_poll_offline_timeout(self, mock_urlopen):
        """HTTP poll with offline server → timeout error, no crash."""
        client = ComfyUIClient(max_execution_time=1.0)
        result = client._http_poll_until_complete("test-id", poll_interval=0.1)
        self.assertIsNotNone(result.get("error"))
        self.assertIn("timed out", result["error"].lower())


# ═══════════════════════════════════════════════════════════════════════════
#  Test 7: History Retrieval & Image Download
# ═══════════════════════════════════════════════════════════════════════════

class TestHistoryAndDownload(unittest.TestCase):
    """Verify /history retrieval and /view image download."""

    def _make_mock_response(self, data, status: int = 200, binary: bool = False):
        mock_resp = mock.MagicMock()
        mock_resp.status = status
        if binary:
            mock_resp.read.return_value = data
        else:
            mock_resp.read.return_value = json.dumps(data).encode("utf-8")
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        return mock_resp

    @mock.patch("urllib.request.urlopen")
    def test_get_history_success(self, mock_urlopen):
        """GET /history returns valid history dict."""
        prompt_id = "test-history-id"
        history_data = {
            prompt_id: {
                "outputs": {
                    "9": {
                        "images": [
                            {
                                "filename": "ComfyUI_00001_.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    }
                }
            }
        }
        mock_urlopen.return_value = self._make_mock_response(history_data)

        client = ComfyUIClient()
        history = client._get_history(prompt_id)
        self.assertIsNotNone(history)
        self.assertIn(prompt_id, history)

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_get_history_offline(self, mock_urlopen):
        """GET /history when offline → None, no crash."""
        client = ComfyUIClient()
        history = client._get_history("test-id")
        self.assertIsNone(history)

    @mock.patch("urllib.request.urlopen")
    def test_download_file_success(self, mock_urlopen):
        """GET /view downloads binary data and saves to disk."""
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_urlopen.return_value = self._make_mock_response(
            fake_png, binary=True
        )

        with tempfile.TemporaryDirectory() as tmp:
            client = ComfyUIClient()
            result = client._download_file(
                "test.png", "", "output", Path(tmp)
            )
            self.assertIsNotNone(result)
            self.assertTrue(Path(result).exists())
            self.assertEqual(Path(result).read_bytes(), fake_png)

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_download_file_offline(self, mock_urlopen):
        """GET /view when offline → None, no crash."""
        with tempfile.TemporaryDirectory() as tmp:
            client = ComfyUIClient()
            with self.assertRaises(ComfyUIExecutionError):
                client._download_file(
                    "test.png", "", "output", Path(tmp)
                )

    @mock.patch("urllib.request.urlopen")
    def test_download_outputs_with_images_and_videos(self, mock_urlopen):
        """Download outputs handles both images and gifs/videos."""
        fake_data = b"\x89PNG" + b"\x00" * 50
        mock_urlopen.return_value = self._make_mock_response(
            fake_data, binary=True
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            client = ComfyUIClient()

            history = {
                "test-id": {
                    "outputs": {
                        "9": {
                            "images": [
                                {"filename": "frame_0001.png", "subfolder": "", "type": "output"},
                                {"filename": "frame_0002.png", "subfolder": "", "type": "output"},
                            ]
                        },
                        "20": {
                            "gifs": [
                                {"filename": "output.mp4", "subfolder": "", "type": "output"},
                            ]
                        },
                    }
                }
            }

            downloaded = client._download_outputs(history, "test-id", output_dir)
            self.assertEqual(len(downloaded["images"]), 2)
            self.assertEqual(len(downloaded["videos"]), 1)

            # Verify metadata file was written
            meta_path = output_dir / "execution_metadata.json"
            self.assertTrue(meta_path.exists())
            meta = json.loads(meta_path.read_text())
            self.assertEqual(meta["images_downloaded"], 2)
            self.assertEqual(meta["videos_downloaded"], 1)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 8: ExecutionResult Dataclass
# ═══════════════════════════════════════════════════════════════════════════

class TestExecutionResult(unittest.TestCase):
    """Verify ExecutionResult serialization and defaults."""

    def test_default_values(self):
        result = ExecutionResult(success=True)
        self.assertTrue(result.success)
        self.assertEqual(result.output_images, [])
        self.assertEqual(result.output_videos, [])
        self.assertFalse(result.degraded)

    def test_to_dict(self):
        result = ExecutionResult(
            success=True,
            prompt_id="abc-123",
            output_images=["/path/img.png"],
            elapsed_seconds=12.345,
        )
        d = result.to_dict()
        self.assertEqual(d["success"], True)
        self.assertEqual(d["prompt_id"], "abc-123")
        self.assertEqual(d["elapsed_seconds"], 12.345)

    def test_degraded_result(self):
        result = ExecutionResult(
            success=False,
            degraded=True,
            degraded_reason="Server offline",
        )
        d = result.to_dict()
        self.assertTrue(d["degraded"])
        self.assertEqual(d["degraded_reason"], "Server offline")


# ═══════════════════════════════════════════════════════════════════════════
#  Test 9: Pipeline Runner Integration (Offline-Safe)
# ═══════════════════════════════════════════════════════════════════════════

class TestPipelineRunnerIntegration(unittest.TestCase):
    """Verify the pipeline runner script phases work offline."""

    def test_placeholder_frame_generation(self):
        """Phase 1: Placeholder frames are created when renderer unavailable."""
        # Import the pipeline runner functions
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        from run_sparsectrl_pipeline import (
            _create_placeholder_frames,
        )

        with tempfile.TemporaryDirectory() as tmp:
            normal_dir = Path(tmp) / "normal"
            depth_dir = Path(tmp) / "depth"
            rgb_dir = Path(tmp) / "rgb"
            for d in (normal_dir, depth_dir, rgb_dir):
                d.mkdir()

            _create_placeholder_frames(
                normal_dir, depth_dir, rgb_dir,
                frame_count=4, width=64, height=64,
            )

            for d in (normal_dir, depth_dir, rgb_dir):
                frames = sorted(d.glob("*.png"))
                self.assertEqual(len(frames), 4)
                for f in frames:
                    self.assertTrue(f.stat().st_size > 0)

    def test_payload_assembly_integration(self):
        """Phase 2: Payload assembly works with real preset manager."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create dummy guide directories
            for channel in ("normal", "depth", "rgb"):
                d = Path(tmp) / channel
                d.mkdir()

            manager = ComfyUIPresetManager()
            payload = manager.assemble_sequence_payload(
                normal_sequence_dir=Path(tmp) / "normal",
                depth_sequence_dir=Path(tmp) / "depth",
                rgb_sequence_dir=Path(tmp) / "rgb",
                prompt="test prompt",
                frame_count=8,
            )

            # Verify payload structure
            self.assertIn("prompt", payload)
            self.assertIn("client_id", payload)
            self.assertIn("mathart_lock_manifest", payload)

            lock = payload["mathart_lock_manifest"]
            self.assertEqual(lock["preset_name"], "sparsectrl_animatediff")
            self.assertTrue(lock["temporal_config"]["batch_size_synced"])
            self.assertEqual(lock["temporal_config"]["frame_count"], 8)

    @mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_execute_on_comfyui_offline(self, mock_urlopen):
        """Phase 3: Execution with offline server → degraded, no crash."""
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        from run_sparsectrl_pipeline import execute_on_comfyui

        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "prompt": {"1": {"class_type": "Test"}},
                "client_id": "test",
            }
            result = execute_on_comfyui(
                payload=payload,
                server_address="127.0.0.1:8188",
                output_root=Path(tmp),
            )
            self.assertFalse(result.success)
            self.assertTrue(result.degraded)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 10: Full End-to-End Mock Execution
# ═══════════════════════════════════════════════════════════════════════════

class TestFullE2EMockExecution(unittest.TestCase):
    """Full end-to-end test with all network calls mocked.

    This simulates a complete successful execution:
    1. Health check → online
    2. POST /prompt → prompt_id
    3. WebSocket events → completion
    4. GET /history → outputs
    5. GET /view → image download
    """

    def test_full_successful_execution(self):
        """Complete execution flow with mocked server → success."""
        prompt_id = "e2e-test-prompt-id"
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        # Mock WebSocket module
        mock_ws_module = mock.MagicMock()
        mock_ws_instance = mock.MagicMock()
        mock_ws_module.WebSocket.return_value = mock_ws_instance

        ws_messages = [
            json.dumps({
                "type": "status",
                "data": {"status": {"exec_info": {"queue_remaining": 1}}},
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": "3", "prompt_id": prompt_id},
            }),
            json.dumps({
                "type": "progress",
                "data": {"value": 20, "max": 20, "node": "3", "prompt_id": prompt_id},
            }),
            json.dumps({
                "type": "executed",
                "data": {
                    "node": "9",
                    "output": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]},
                    "prompt_id": prompt_id,
                },
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None, "prompt_id": prompt_id},
            }),
        ]
        mock_ws_instance.recv = mock.MagicMock(side_effect=ws_messages)

        # Build HTTP response sequence
        def mock_urlopen_side_effect(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)

            mock_resp = mock.MagicMock()
            mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = mock.MagicMock(return_value=False)

            if "system_stats" in url:
                # Health check
                mock_resp.status = 200
                mock_resp.read.return_value = b'{"ok": true}'
            elif "/prompt" in url and "history" not in url:
                # POST /prompt
                mock_resp.status = 200
                mock_resp.read.return_value = json.dumps({
                    "prompt_id": prompt_id,
                    "number": 1,
                    "node_errors": {},
                }).encode()
            elif "/history/" in url:
                # GET /history
                mock_resp.status = 200
                mock_resp.read.return_value = json.dumps({
                    prompt_id: {
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "out.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                        "status": {"status_str": "success", "completed": True},
                    }
                }).encode()
            elif "/view" in url:
                # GET /view (image download)
                mock_resp.status = 200
                mock_resp.read.return_value = fake_png
            else:
                mock_resp.status = 404
                mock_resp.read.return_value = b'{}'

            return mock_resp

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("sys.modules", {"websocket": mock_ws_module}):
                with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
                    client = ComfyUIClient(output_root=tmp)
                    payload = {
                        "prompt": {"3": {"class_type": "KSampler"}},
                        "client_id": "e2e-test-client",
                    }
                    result = client.execute_workflow(payload, run_label="e2e_test")

            self.assertTrue(result.success)
            self.assertEqual(result.prompt_id, prompt_id)
            self.assertEqual(len(result.output_images), 1)
            self.assertTrue(result.elapsed_seconds >= 0)

            # Verify the image was actually downloaded to disk
            if result.output_images:
                img_path = Path(result.output_images[0])
                self.assertTrue(img_path.exists())
                self.assertEqual(img_path.read_bytes(), fake_png)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 11: Output Directory Structure
# ═══════════════════════════════════════════════════════════════════════════

class TestOutputDirectoryStructure(unittest.TestCase):
    """Verify output directory naming and metadata."""

    def test_create_output_dir(self):
        """Output directory has timestamp and run label."""
        with tempfile.TemporaryDirectory() as tmp:
            client = ComfyUIClient(output_root=tmp)
            output_dir = client._create_output_dir("test_run", "abc12345")
            self.assertTrue(output_dir.exists())
            self.assertIn("test_run", output_dir.name)
            self.assertIn("abc12345", output_dir.name)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 12: Backward Compatibility with SESSION-086 Preset
# ═══════════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility(unittest.TestCase):
    """Verify that SESSION-086 preset assembly still works correctly."""

    def test_sparsectrl_preset_loads(self):
        """SparseCtrl preset JSON loads and validates."""
        manager = ComfyUIPresetManager()
        preset = manager.load_preset(
            "sparsectrl_animatediff",
            selectors=_SPARSECTRL_SELECTORS,
        )
        self.assertIsInstance(preset, dict)
        self.assertTrue(len(preset) > 0)

    def test_dual_controlnet_preset_loads(self):
        """Original dual_controlnet preset still loads."""
        manager = ComfyUIPresetManager()
        preset = manager.load_preset("dual_controlnet_ipadapter")
        self.assertIsInstance(preset, dict)


if __name__ == "__main__":
    unittest.main()
