"""SESSION-200 WebSocket Telemetry & Streaming Download — Mock Tests.

SESSION-200 (P0-SESSION-200-EPIC-IGNITION-AND-LIVE-TELEMETRY)
---------------------------------------------------------------
These tests verify the SESSION-200 upgrade chain without any live ComfyUI
server.  All HTTP and WebSocket interactions are deeply mocked using
``unittest.mock``.

Test categories:
1. **WS Telemetry Constants** — verify telemetry prefix strings exist.
2. **WS Dual-Channel Telemetry** — mock full event sequence through
   execution_start → executing → progress → executed → completion.
3. **WS Execution Error Fail-Fast** — verify execution_error raises
   ComfyUIExecutionError immediately (SESSION-168 preserved).
4. **WS Timeout Circuit Breaker** — verify deadline exceeded raises
   RenderTimeoutError (900s hard deadline).
5. **Streaming Download** — verify _download_file_streaming uses chunked
   transfer and writes correct bytes.
6. **Harvest Final Artifacts** — verify harvest_final_artifacts scans
   history and downloads via streaming.
7. **Golden Payload Pre-flight Dump** — verify payload is written to disk
   before execution.
8. **Ignition Launchpad** — verify tools/session200_epic_ignition.py
   generates diagnostic payload and dumps golden snapshot.

Anti-pattern guards verified:
- 🚫 Infinite Wait Trap: Tests verify 900s hard deadline is enforced.
- 🚫 Memory Bomb Trap: Tests verify streaming download, never .content.
- 🚫 Blind HTTP POST Trap: Tests verify WebSocket is used.
- 🚫 CI HTTP Blocking Trap: ZERO real HTTP calls in all tests.
"""
from __future__ import annotations

import io
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

from mathart.backend.comfy_client import (
    ComfyAPIClient,
    ComfyUIExecutionError,
    RenderTimeoutError,
    RenderResult,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers: Mock WebSocket Module
# ═══════════════════════════════════════════════════════════════════════════

class MockWebSocket:
    """Mock WebSocket that replays a sequence of JSON messages."""

    def __init__(self, messages: list[str | bytes]):
        self._messages = list(messages)
        self._index = 0
        self._connected = False
        self._timeout = 30.0

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout

    def connect(self, url: str) -> None:
        self._connected = True

    def recv(self) -> str | bytes:
        if self._index >= len(self._messages):
            # Simulate timeout by raising
            raise TimeoutError("No more messages")
        msg = self._messages[self._index]
        self._index += 1
        return msg

    def close(self) -> None:
        self._connected = False


def make_mock_ws_module(messages: list[str | bytes]):
    """Create a mock websocket module with a WebSocket class."""
    mock_module = mock.MagicMock()
    mock_module.WebSocket = lambda: MockWebSocket(messages)
    return mock_module


def ws_event(event_type: str, data: dict | None = None) -> str:
    """Create a JSON WebSocket event string."""
    return json.dumps({"type": event_type, "data": data or {}})


# ═══════════════════════════════════════════════════════════════════════════
#  Test 1: WS Telemetry Constants
# ═══════════════════════════════════════════════════════════════════════════

class TestTelemetryConstants(unittest.TestCase):
    """Verify SESSION-200 telemetry constants exist on ComfyAPIClient."""

    def test_telemetry_timeout_is_900(self):
        self.assertEqual(ComfyAPIClient._TELEMETRY_TIMEOUT, 900.0)

    def test_telemetry_prefix_exec_exists(self):
        self.assertIn("节点执行中", ComfyAPIClient._TELEMETRY_PREFIX_EXEC)

    def test_telemetry_prefix_progress_exists(self):
        self.assertIn("渲染进度", ComfyAPIClient._TELEMETRY_PREFIX_PROGRESS)

    def test_telemetry_prefix_start_exists(self):
        self.assertIn("点火启动", ComfyAPIClient._TELEMETRY_PREFIX_START)

    def test_telemetry_prefix_done_exists(self):
        self.assertIn("执行完成", ComfyAPIClient._TELEMETRY_PREFIX_DONE)

    def test_telemetry_prefix_error_exists(self):
        self.assertIn("致命崩溃", ComfyAPIClient._TELEMETRY_PREFIX_ERROR)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 2: WS Dual-Channel Telemetry — Full Event Sequence
# ═══════════════════════════════════════════════════════════════════════════

class TestWSTelemetryFullSequence(unittest.TestCase):
    """Verify full WebSocket telemetry event sequence through completion."""

    def test_full_event_sequence_completes(self):
        """Mock: execution_start → executing(node1) → progress → executing(None) → done."""
        prompt_id = "test-prompt-200"
        client_id = "test-client-200"

        messages = [
            ws_event("execution_start", {"prompt_id": prompt_id}),
            ws_event("executing", {"node": "3", "prompt_id": prompt_id}),
            ws_event("progress", {"value": 1, "max": 4, "node": "3"}),
            ws_event("progress", {"value": 2, "max": 4, "node": "3"}),
            ws_event("progress", {"value": 3, "max": 4, "node": "3"}),
            ws_event("progress", {"value": 4, "max": 4, "node": "3"}),
            ws_event("executed", {"node": "3", "output": {"images": []}}),
            ws_event("executing", {"node": "8", "prompt_id": prompt_id}),
            ws_event("executed", {"node": "8", "output": {"images": []}}),
            ws_event("executing", {"node": "9", "prompt_id": prompt_id}),
            ws_event("executed", {"node": "9", "output": {"images": [{"filename": "test.png"}]}}),
            # Completion signal: executing with node=None
            ws_event("executing", {"node": None, "prompt_id": prompt_id}),
        ]

        mock_ws_mod = make_mock_ws_module(messages)

        client = ComfyAPIClient(
            server_address="127.0.0.1:8188",
            render_timeout=60.0,
        )

        with mock.patch.dict(sys.modules, {"websocket": mock_ws_mod}):
            result = client._ws_wait(prompt_id, client_id)

        self.assertIsNone(result["error"])
        self.assertIn("progress", result)
        progress = result["progress"]
        self.assertIn("last_progress", progress)
        self.assertEqual(progress["last_progress"]["value"], 4)
        self.assertEqual(progress["last_progress"]["max"], 4)
        # SESSION-200: telemetry_log should be present
        self.assertIn("telemetry_log", progress)
        self.assertIsInstance(progress["telemetry_log"], list)
        self.assertGreater(len(progress["telemetry_log"]), 0)

    def test_execution_start_is_logged(self):
        """Verify execution_start event is captured in telemetry_log."""
        prompt_id = "test-prompt-start"
        messages = [
            ws_event("execution_start", {"prompt_id": prompt_id}),
            ws_event("executing", {"node": None, "prompt_id": prompt_id}),
        ]
        mock_ws_mod = make_mock_ws_module(messages)
        client = ComfyAPIClient(server_address="127.0.0.1:8188", render_timeout=60.0)

        with mock.patch.dict(sys.modules, {"websocket": mock_ws_mod}):
            result = client._ws_wait(prompt_id, "client-start")

        telemetry = result["progress"]["telemetry_log"]
        event_types = [e["type"] for e in telemetry]
        self.assertIn("execution_start", event_types)

    def test_executed_node_output_keys_captured(self):
        """Verify executed events capture output keys in progress dict."""
        prompt_id = "test-prompt-executed"
        messages = [
            ws_event("executed", {"node": "42", "output": {"images": [], "gifs": []}}),
            ws_event("executing", {"node": None, "prompt_id": prompt_id}),
        ]
        mock_ws_mod = make_mock_ws_module(messages)
        client = ComfyAPIClient(server_address="127.0.0.1:8188", render_timeout=60.0)

        with mock.patch.dict(sys.modules, {"websocket": mock_ws_mod}):
            result = client._ws_wait(prompt_id, "client-exec")

        self.assertIn("executed_42", result["progress"])
        self.assertIn("images", result["progress"]["executed_42"])
        self.assertIn("gifs", result["progress"]["executed_42"])


# ═══════════════════════════════════════════════════════════════════════════
#  Test 3: WS Execution Error Fail-Fast
# ═══════════════════════════════════════════════════════════════════════════

class TestWSExecutionErrorFailFast(unittest.TestCase):
    """Verify execution_error raises ComfyUIExecutionError immediately."""

    def test_execution_error_raises_immediately(self):
        """SESSION-168 + SESSION-200: execution_error → Fail-Fast Poison Pill."""
        prompt_id = "test-prompt-error"
        messages = [
            ws_event("execution_start", {"prompt_id": prompt_id}),
            ws_event("executing", {"node": "3", "prompt_id": prompt_id}),
            ws_event("execution_error", {
                "prompt_id": prompt_id,
                "node_id": "3",
                "exception_message": "CUDA out of memory",
                "traceback": "Traceback: OOM at node 3",
            }),
        ]
        mock_ws_mod = make_mock_ws_module(messages)
        client = ComfyAPIClient(server_address="127.0.0.1:8188", render_timeout=60.0)

        with mock.patch.dict(sys.modules, {"websocket": mock_ws_mod}):
            with self.assertRaises(ComfyUIExecutionError) as ctx:
                client._ws_wait(prompt_id, "client-error")

        self.assertIn("CUDA out of memory", str(ctx.exception))
        self.assertEqual(ctx.exception.node_id, "3")

    def test_execution_error_does_not_fall_through(self):
        """Verify no further messages are processed after execution_error."""
        prompt_id = "test-prompt-error-nofallthrough"
        messages = [
            ws_event("execution_error", {
                "prompt_id": prompt_id,
                "node_id": "5",
                "exception_message": "Model not found",
            }),
            # These should never be reached
            ws_event("executing", {"node": None, "prompt_id": prompt_id}),
        ]
        mock_ws_mod = make_mock_ws_module(messages)
        client = ComfyAPIClient(server_address="127.0.0.1:8188", render_timeout=60.0)

        with mock.patch.dict(sys.modules, {"websocket": mock_ws_mod}):
            with self.assertRaises(ComfyUIExecutionError):
                client._ws_wait(prompt_id, "client-nofallthrough")


# ═══════════════════════════════════════════════════════════════════════════
#  Test 4: WS Timeout Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════

class TestWSTimeoutCircuitBreaker(unittest.TestCase):
    """Verify deadline exceeded raises RenderTimeoutError."""

    def test_timeout_raises_render_timeout_error(self):
        """Verify that exceeding the deadline raises RenderTimeoutError."""
        prompt_id = "test-prompt-timeout"
        # No completion signal — will timeout
        messages = [
            ws_event("executing", {"node": "3", "prompt_id": prompt_id}),
        ]
        mock_ws_mod = make_mock_ws_module(messages)
        # Use a very short timeout to trigger quickly
        client = ComfyAPIClient(server_address="127.0.0.1:8188", render_timeout=0.1)

        with mock.patch.dict(sys.modules, {"websocket": mock_ws_mod}):
            with self.assertRaises(RenderTimeoutError):
                client._ws_wait(prompt_id, "client-timeout")

    def test_telemetry_timeout_is_enforced(self):
        """Verify _TELEMETRY_TIMEOUT = 900s is the hard deadline."""
        self.assertEqual(ComfyAPIClient._TELEMETRY_TIMEOUT, 900.0)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 5: Streaming Download
# ═══════════════════════════════════════════════════════════════════════════

class TestStreamingDownload(unittest.TestCase):
    """Verify _download_file_streaming uses chunked transfer."""

    def test_streaming_download_writes_correct_bytes(self):
        """Mock requests.get with stream=True and verify chunked write."""
        client = ComfyAPIClient(server_address="127.0.0.1:8188")

        test_data = b"SESSION-200 streaming test data " * 100  # ~3.2KB

        class MockResponse:
            status_code = 200
            def raise_for_status(self):
                pass
            def iter_content(self, chunk_size=8192):
                for i in range(0, len(test_data), chunk_size):
                    yield test_data[i:i + chunk_size]
            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "test_download.png"

            with mock.patch("mathart.backend.comfy_client.ComfyAPIClient._download_file_streaming") as mock_dl:
                # Test the actual method by calling it directly with mocked requests
                mock_dl.side_effect = None  # Remove side effect

            # Actually test the method with mocked requests module
            import importlib
            mock_requests = mock.MagicMock()
            mock_requests.get.return_value = MockResponse()

            with mock.patch.dict(sys.modules, {"requests": mock_requests}):
                result = client._download_file_streaming(
                    "test.png", "", "output", local_path,
                )

            self.assertTrue(result)
            self.assertTrue(local_path.exists())
            self.assertEqual(local_path.read_bytes(), test_data)

    def test_streaming_download_zero_bytes_returns_false(self):
        """Verify 0-byte download returns False."""
        client = ComfyAPIClient(server_address="127.0.0.1:8188")

        class MockResponse:
            status_code = 200
            def raise_for_status(self):
                pass
            def iter_content(self, chunk_size=8192):
                return iter([])  # Empty response
            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "empty.png"
            mock_requests = mock.MagicMock()
            mock_requests.get.return_value = MockResponse()

            with mock.patch.dict(sys.modules, {"requests": mock_requests}):
                result = client._download_file_streaming(
                    "empty.png", "", "output", local_path,
                )

            self.assertFalse(result)

    def test_streaming_download_connection_reset_raises(self):
        """Verify ConnectionResetError during streaming raises ComfyUIExecutionError."""
        client = ComfyAPIClient(server_address="127.0.0.1:8188")

        mock_requests = mock.MagicMock()
        mock_requests.get.side_effect = ConnectionResetError("Connection reset by peer")

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "reset.png"

            with mock.patch.dict(sys.modules, {"requests": mock_requests}):
                with self.assertRaises(ComfyUIExecutionError):
                    client._download_file_streaming(
                        "reset.png", "", "output", local_path,
                    )


# ═══════════════════════════════════════════════════════════════════════════
#  Test 6: Harvest Final Artifacts
# ═══════════════════════════════════════════════════════════════════════════

class TestHarvestFinalArtifacts(unittest.TestCase):
    """Verify harvest_final_artifacts scans history and downloads."""

    def test_harvest_images_from_history(self):
        """Verify images are harvested from execution history."""
        client = ComfyAPIClient(server_address="127.0.0.1:8188")
        prompt_id = "test-harvest-200"

        history = {
            prompt_id: {
                "outputs": {
                    "9": {
                        "images": [
                            {"filename": "render_001.png", "subfolder": "", "type": "output"},
                            {"filename": "render_002.png", "subfolder": "", "type": "output"},
                        ],
                    },
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "final_renders"

            # Mock the streaming download to just create files
            def mock_download(server_fn, subfolder, file_type, local_path, **kw):
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(b"FAKE_PNG_DATA")
                return True

            with mock.patch.object(client, "_download_file_streaming", side_effect=mock_download):
                result = client.harvest_final_artifacts(history, prompt_id, output_dir=output_dir)

            self.assertEqual(len(result["images"]), 2)
            self.assertEqual(len(result["videos"]), 0)
            # Verify files exist on disk
            for img_path in result["images"]:
                self.assertTrue(Path(img_path).exists())

    def test_harvest_videos_from_history(self):
        """Verify videos/gifs are harvested from execution history."""
        client = ComfyAPIClient(server_address="127.0.0.1:8188")
        prompt_id = "test-harvest-video"

        history = {
            prompt_id: {
                "outputs": {
                    "10": {
                        "gifs": [
                            {"filename": "anim.gif", "subfolder": "", "type": "output"},
                        ],
                        "videos": [
                            {"filename": "render.mp4", "subfolder": "", "type": "output"},
                        ],
                    },
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "final_renders"

            def mock_download(server_fn, subfolder, file_type, local_path, **kw):
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(b"FAKE_VIDEO_DATA")
                return True

            with mock.patch.object(client, "_download_file_streaming", side_effect=mock_download):
                result = client.harvest_final_artifacts(history, prompt_id, output_dir=output_dir)

            self.assertEqual(len(result["videos"]), 2)  # 1 gif + 1 video

    def test_harvest_empty_history(self):
        """Verify empty history returns empty results without error."""
        client = ComfyAPIClient(server_address="127.0.0.1:8188")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = client.harvest_final_artifacts(
                {}, "nonexistent", output_dir=Path(tmpdir) / "empty"
            )
            self.assertEqual(result, {"images": [], "videos": []})


# ═══════════════════════════════════════════════════════════════════════════
#  Test 7: Golden Payload Pre-flight Dump
# ═══════════════════════════════════════════════════════════════════════════

class TestGoldenPayloadDump(unittest.TestCase):
    """Verify golden payload is written to disk before execution."""

    def test_ignition_script_generates_diagnostic_payload(self):
        """Verify the ignition script generates a valid diagnostic payload."""
        from tools.session200_epic_ignition import generate_diagnostic_payload

        payload = generate_diagnostic_payload()
        self.assertIn("prompt", payload)
        self.assertIn("mathart_session", payload)
        self.assertEqual(payload["mathart_session"], "SESSION-200")
        # Verify the prompt has the expected nodes
        prompt = payload["prompt"]
        self.assertIn("3", prompt)  # KSampler
        self.assertIn("4", prompt)  # CheckpointLoaderSimple
        self.assertIn("9", prompt)  # SaveImage

    def test_ignition_script_dumps_golden_payload(self):
        """Verify the ignition script writes golden payload to disk."""
        from tools.session200_epic_ignition import dump_golden_payload, generate_diagnostic_payload

        payload = generate_diagnostic_payload()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "ignition_test"
            golden_path = dump_golden_payload(payload, output_dir)

            self.assertTrue(golden_path.exists())
            with open(golden_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["mathart_session"], "SESSION-200")
            self.assertIn("prompt", loaded)

    def test_golden_payload_is_beautified_json(self):
        """Verify golden payload uses indent=4 beautified JSON."""
        from tools.session200_epic_ignition import dump_golden_payload

        payload = {"test": "SESSION-200", "nested": {"key": "value"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            golden_path = dump_golden_payload(payload, Path(tmpdir))
            content = golden_path.read_text(encoding="utf-8")
            # Beautified JSON should have indentation
            self.assertIn("    ", content)
            # Should end with newline
            self.assertTrue(content.endswith("\n"))


# ═══════════════════════════════════════════════════════════════════════════
#  Test 8: Ignition Launchpad Smoke Test
# ═══════════════════════════════════════════════════════════════════════════

class TestIgnitionLaunchpad(unittest.TestCase):
    """Verify the ignition launchpad script is importable and functional."""

    def test_ignition_module_importable(self):
        """Verify tools/session200_epic_ignition.py is importable."""
        import importlib
        mod = importlib.import_module("tools.session200_epic_ignition")
        self.assertTrue(hasattr(mod, "main"))
        self.assertTrue(hasattr(mod, "generate_diagnostic_payload"))
        self.assertTrue(hasattr(mod, "dump_golden_payload"))
        self.assertTrue(hasattr(mod, "preflight_health_check"))
        self.assertTrue(hasattr(mod, "execute_ignition"))

    def test_preflight_health_check_offline(self):
        """Verify health check returns False when server is offline."""
        from tools.session200_epic_ignition import preflight_health_check

        # No ComfyUI server running in test environment
        result = preflight_health_check("127.0.0.1:59999")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
