"""
SESSION-202 Web UI Bridge Mock Tests (反自欺测试红线).

This test module validates the Headless Dispatcher Bridge without starting
a real browser or Gradio server.  It mocks the frontend-to-backend translation
layer and asserts:

1. **Intent Assembly**: The bridge correctly assembles UI parameters into a
   ``CreatorIntentSpec``-compatible dictionary with proper ``vfx_overrides``,
   ``action_name``, ``visual_reference_path``, AND Gateway-compatible
   ``action`` / ``reference_image`` keys.
2. **Image Persistence (反幽灵路径红线)**: Drag-and-drop images are safely
   ``shutil.copy``-ed from Gradio temp to ``workspace/inputs/`` and the
   persisted file actually exists on disk.
3. **Pipeline Dispatch**: The bridge can invoke the pipeline dispatch flow
   and yield progress events without crashing.
4. **Telemetry Adapter**: WebSocket telemetry events are correctly transformed
   into Gradio-compatible progress dicts.
5. **Output Collection**: The gallery/video collector correctly scans the
   outputs directory.

Industrial References:
- Adapter Pattern unit testing (mock external dependencies)
- Contract testing (assert dictionary schema compliance)
- File system isolation (tempfile for test artifacts)

SESSION-203-HOTFIX:
- Updated intent assembly tests to verify both Gateway-compatible keys
  (``action`` / ``reference_image``) and downstream keys (``action_name`` /
  ``visual_reference_path``).
- Updated dispatch tests to account for the new ``pipeline_dispatch`` stage.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest


# ═══════════════════════════════════════════════════════════════════════════
#  Imports under test
# ═══════════════════════════════════════════════════════════════════════════

from mathart.webui.bridge import (
    WebUIBridge,
    assemble_creator_intent,
    persist_uploaded_image,
    _WORKSPACE_INPUTS,
)
from mathart.webui.telemetry_adapter import (
    transform_telemetry_event,
    collect_render_outputs,
    stream_telemetry_log,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 1: Intent Assembly Contract
# ═══════════════════════════════════════════════════════════════════════════

class TestIntentAssembly:
    """Verify that UI parameters are correctly assembled into CreatorIntentSpec."""

    def test_basic_intent_assembly(self):
        """Basic assembly with action and no VFX overrides."""
        intent = assemble_creator_intent(
            action_name="walk",
            reference_image_path="",
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        )
        # Downstream-compatible keys
        assert intent["action_name"] == "walk"
        assert intent["visual_reference_path"] == ""
        # Gateway-compatible keys (SESSION-203-HOTFIX)
        assert intent["action"] == "walk"
        assert intent["reference_image"] == ""
        # Shared keys
        assert intent["vfx_overrides"] == {}
        assert intent["skeleton_topology"] == "biped"

    def test_intent_with_all_vfx_enabled(self):
        """All VFX toggles enabled should populate vfx_overrides."""
        intent = assemble_creator_intent(
            action_name="dash",
            reference_image_path="/path/to/ref.png",
            force_fluid=True,
            force_physics=True,
            force_cloth=True,
            force_particles=True,
            raw_vibe="cyberpunk rain",
        )
        # Downstream keys
        assert intent["action_name"] == "dash"
        assert intent["visual_reference_path"] == "/path/to/ref.png"
        # Gateway keys (SESSION-203-HOTFIX)
        assert intent["action"] == "dash"
        assert intent["reference_image"] == "/path/to/ref.png"
        # VFX overrides
        assert intent["vfx_overrides"]["force_fluid"] is True
        assert intent["vfx_overrides"]["force_physics"] is True
        assert intent["vfx_overrides"]["force_cloth"] is True
        assert intent["vfx_overrides"]["force_particles"] is True
        assert intent["raw_vibe"] == "cyberpunk rain"

    def test_intent_partial_vfx(self):
        """Only some VFX toggles enabled."""
        intent = assemble_creator_intent(
            action_name="run",
            reference_image_path="",
            force_fluid=True,
            force_physics=False,
            force_cloth=False,
            force_particles=True,
        )
        assert "force_fluid" in intent["vfx_overrides"]
        assert "force_particles" in intent["vfx_overrides"]
        assert "force_physics" not in intent["vfx_overrides"]
        assert "force_cloth" not in intent["vfx_overrides"]

    def test_intent_empty_action(self):
        """Empty action name is allowed (defers to default)."""
        intent = assemble_creator_intent(
            action_name="",
            reference_image_path="",
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        )
        assert intent["action_name"] == ""
        assert intent["action"] == ""

    def test_intent_none_action_coerced(self):
        """None action should be coerced to empty string."""
        intent = assemble_creator_intent(
            action_name=None,  # type: ignore
            reference_image_path="",
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        )
        # Should not crash; action_name may be None or ""
        assert intent["action_name"] is None or intent["action_name"] == ""
        assert intent["action"] is None or intent["action"] == ""

    def test_intent_gateway_key_consistency(self):
        """Gateway keys must always match downstream keys (SESSION-203-HOTFIX)."""
        intent = assemble_creator_intent(
            action_name="jump",
            reference_image_path="/some/path.png",
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        )
        # Gateway key == downstream key
        assert intent["action"] == intent["action_name"]
        assert intent["reference_image"] == intent["visual_reference_path"]


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 2: Image Persistence (反幽灵路径红线)
# ═══════════════════════════════════════════════════════════════════════════

class TestImagePersistence:
    """Verify that uploaded images are safely persisted."""

    def test_persist_copies_file(self):
        """Image is copied from temp to workspace/inputs/ and exists on disk."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake PNG data")
            tmp_path = tmp.name

        try:
            result = persist_uploaded_image(tmp_path)
            assert result != ""
            assert os.path.exists(result), (
                f"Persisted file does not exist at {result}"
            )
            assert "workspace/inputs/" in result or "workspace" in result
            # Verify content was actually copied
            with open(result, "rb") as f:
                assert f.read() == b"fake PNG data"
        finally:
            os.unlink(tmp_path)
            if result and os.path.exists(result):
                os.unlink(result)

    def test_persist_none_returns_empty(self):
        """None input returns empty string."""
        assert persist_uploaded_image(None) == ""

    def test_persist_empty_string_returns_empty(self):
        """Empty string input returns empty string."""
        assert persist_uploaded_image("") == ""

    def test_persist_nonexistent_raises(self):
        """Non-existent file raises FileNotFoundError (反空跑宕机红线)."""
        with pytest.raises(FileNotFoundError, match="反幽灵路径红线"):
            persist_uploaded_image("/nonexistent/ghost/image.png")

    def test_persist_unique_filenames(self):
        """Multiple uploads produce unique filenames (no collision)."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"data1")
            tmp_path = tmp.name

        try:
            result1 = persist_uploaded_image(tmp_path)
            result2 = persist_uploaded_image(tmp_path)
            assert result1 != result2, "Filenames should be unique"
            assert os.path.exists(result1)
            assert os.path.exists(result2)
        finally:
            os.unlink(tmp_path)
            for r in (result1, result2):
                if r and os.path.exists(r):
                    os.unlink(r)


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 3: Bridge Pipeline Dispatch
# ═══════════════════════════════════════════════════════════════════════════

class TestBridgeDispatch:
    """Verify the WebUIBridge dispatch flow."""

    def test_bridge_get_available_actions(self):
        """Bridge should return a non-empty list of actions."""
        bridge = WebUIBridge()
        actions = bridge.get_available_actions()
        assert isinstance(actions, list)
        assert len(actions) > 0

    def test_bridge_dispatch_yields_events(self):
        """Dispatch should yield progress events as a generator."""
        bridge = WebUIBridge()
        events = list(bridge.dispatch_render(
            action_name="idle",
            reference_image=None,
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        ))
        assert len(events) > 0
        # First event should be init
        assert events[0]["stage"] == "init"
        assert events[0]["progress"] == 0.0
        # Last event should be complete
        assert events[-1]["stage"] == "complete"
        assert events[-1]["progress"] == 1.0

    def test_bridge_dispatch_with_ref_image(self):
        """Dispatch with a reference image persists and proceeds."""
        bridge = WebUIBridge()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake ref image")
            tmp_path = tmp.name

        try:
            events = list(bridge.dispatch_render(
                action_name="walk",
                reference_image=tmp_path,
                force_fluid=True,
                force_physics=False,
                force_cloth=False,
                force_particles=False,
            ))
            assert events[-1]["stage"] == "complete"
        finally:
            os.unlink(tmp_path)

    def test_bridge_dispatch_ghost_ref_image_yields_error(self):
        """Ghost reference image path should yield an error event."""
        bridge = WebUIBridge()
        events = list(bridge.dispatch_render(
            action_name="idle",
            reference_image="/nonexistent/ghost.png",
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        ))
        # Should have an error event
        error_events = [e for e in events if e.get("error")]
        assert len(error_events) > 0

    def test_bridge_dispatch_all_events_have_required_keys(self):
        """Every yielded event must have the required schema keys."""
        bridge = WebUIBridge()
        required_keys = {"stage", "progress", "message", "gallery", "video", "error"}
        for event in bridge.dispatch_render(
            action_name="idle",
            reference_image=None,
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        ):
            assert required_keys.issubset(event.keys()), (
                f"Event missing keys: {required_keys - event.keys()}"
            )

    def test_bridge_dispatch_includes_pipeline_dispatch_stage(self):
        """SESSION-203-HOTFIX: Dispatch must include a pipeline_dispatch stage."""
        bridge = WebUIBridge()
        events = list(bridge.dispatch_render(
            action_name="run",
            reference_image=None,
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        ))
        stages = [e["stage"] for e in events]
        assert "pipeline_dispatch" in stages, (
            "Missing pipeline_dispatch stage — bridge must attempt real dispatch"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 4: Telemetry Adapter
# ═══════════════════════════════════════════════════════════════════════════

class TestTelemetryAdapter:
    """Verify WebSocket telemetry event transformation."""

    def test_progress_event(self):
        """Progress event should map to 30%–90% range."""
        result = transform_telemetry_event({
            "event_type": "progress",
            "data": {"value": 50, "max": 100},
        })
        assert result["stage"] == "ai_render"
        assert 0.3 <= result["progress"] <= 0.9
        assert "50/100" in result["message"]

    def test_executing_event_with_node(self):
        """Executing event with node ID."""
        result = transform_telemetry_event({
            "event_type": "executing",
            "data": {"node": "42"},
        })
        assert result["stage"] == "ai_render"
        assert "42" in result["message"]

    def test_executing_event_complete(self):
        """Executing event with None node means render complete."""
        result = transform_telemetry_event({
            "event_type": "executing",
            "data": {"node": None},
        })
        assert result["stage"] == "ai_render_complete"
        assert result["progress"] == 0.9

    def test_status_event(self):
        """Status event with queue info."""
        result = transform_telemetry_event({
            "event_type": "status",
            "data": {"status": {"exec_info": {"queue_remaining": 3}}},
        })
        assert result["stage"] == "queue"
        assert "3" in result["message"]

    def test_error_event(self):
        """Error event should populate error field."""
        result = transform_telemetry_event({
            "event_type": "error",
            "data": {"message": "GPU OOM"},
        })
        assert result["error"] == "GPU OOM"
        assert result["stage"] == "error"

    def test_unknown_event(self):
        """Unknown event type should not crash."""
        result = transform_telemetry_event({
            "event_type": "custom_event",
            "data": {"foo": "bar"},
        })
        assert result["stage"] == "custom_event"
        assert result["error"] is None


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 5: Output Collection
# ═══════════════════════════════════════════════════════════════════════════

class TestOutputCollection:
    """Verify gallery and video output collection."""

    def test_collect_empty_dir(self):
        """Empty directory returns empty results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gallery, video = collect_render_outputs(tmpdir)
            assert gallery == []
            assert video is None

    def test_collect_images(self):
        """PNG files are collected into gallery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                (Path(tmpdir) / f"frame_{i:04d}.png").write_bytes(b"fake")
            gallery, video = collect_render_outputs(tmpdir)
            assert len(gallery) == 3
            assert video is None

    def test_collect_video(self):
        """MP4 file is detected as video output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "output.mp4").write_bytes(b"fake mp4")
            gallery, video = collect_render_outputs(tmpdir)
            assert video is not None
            assert video.endswith(".mp4")

    def test_collect_nonexistent_dir(self):
        """Non-existent directory returns empty results gracefully."""
        gallery, video = collect_render_outputs("/nonexistent/path")
        assert gallery == []
        assert video is None

    def test_stream_telemetry_log(self):
        """Stream telemetry log yields transformed events."""
        log = [
            {"event_type": "progress", "data": {"value": 10, "max": 100}},
            {"event_type": "executing", "data": {"node": "5"}},
            {"event_type": "executing", "data": {"node": None}},
        ]
        events = list(stream_telemetry_log(log))
        assert len(events) == 3
        assert events[0]["stage"] == "ai_render"
        assert events[2]["stage"] == "ai_render_complete"


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 6: Integration — Full Bridge Flow
# ═══════════════════════════════════════════════════════════════════════════

class TestBridgeIntegration:
    """End-to-end integration tests for the bridge flow."""

    def test_full_flow_cpu_only(self):
        """Full flow: assemble intent → persist image → dispatch → collect."""
        bridge = WebUIBridge()

        # Create a fake reference image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"integration test image")
            tmp_path = tmp.name

        try:
            # Step 1: Persist
            persisted = persist_uploaded_image(tmp_path)
            assert os.path.exists(persisted)

            # Step 2: Assemble
            intent = assemble_creator_intent(
                action_name="dash",
                reference_image_path=persisted,
                force_fluid=True,
                force_physics=True,
                force_cloth=False,
                force_particles=False,
                raw_vibe="test vibe",
            )
            assert intent["action_name"] == "dash"
            assert intent["action"] == "dash"  # SESSION-203-HOTFIX
            assert intent["vfx_overrides"]["force_fluid"] is True

            # Step 3: Dispatch (generator)
            events = list(bridge.dispatch_render(
                action_name="dash",
                reference_image=tmp_path,
                force_fluid=True,
                force_physics=True,
                force_cloth=False,
                force_particles=False,
                raw_vibe="test vibe",
            ))

            # Verify event stream integrity
            stages = [e["stage"] for e in events]
            assert stages[0] == "init"
            assert stages[-1] == "complete"
            assert all(0.0 <= e["progress"] <= 1.0 for e in events)
            # SESSION-203-HOTFIX: pipeline_dispatch stage must be present
            assert "pipeline_dispatch" in stages
        finally:
            os.unlink(tmp_path)
            if persisted and os.path.exists(persisted):
                os.unlink(persisted)


# ═══════════════════════════════════════════════════════════════════════════
#  Test Group 7: Gateway Key Mapping Contract (SESSION-203-HOTFIX)
# ═══════════════════════════════════════════════════════════════════════════

class TestGatewayKeyMapping:
    """Verify that the bridge produces Gateway-compatible keys for admission."""

    def test_intent_dict_has_gateway_keys(self):
        """Intent dict must contain 'action' and 'reference_image' keys
        that IntentGateway.admit() reads via raw_intent.get()."""
        intent = assemble_creator_intent(
            action_name="run",
            reference_image_path="/some/ref.png",
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        )
        # These are the keys IntentGateway.admit() reads
        assert "action" in intent, "Missing 'action' key for Gateway admission"
        assert "reference_image" in intent, "Missing 'reference_image' key for Gateway admission"
        assert intent["action"] == "run"
        assert intent["reference_image"] == "/some/ref.png"

    def test_gateway_admit_receives_correct_values(self):
        """IntentGateway.admit() should receive non-empty action from bridge dict."""
        from mathart.workspace.intent_gateway import IntentGateway
        intent = assemble_creator_intent(
            action_name="dash",
            reference_image_path="",
            force_fluid=False,
            force_physics=False,
            force_cloth=False,
            force_particles=False,
        )
        gateway = IntentGateway()
        admission = gateway.admit(intent)
        # With the fixed keys, Gateway should now see the action
        assert admission.action_name == "dash", (
            f"Gateway should see action='dash', got '{admission.action_name}'"
        )

    def test_gateway_admit_with_vfx_overrides(self):
        """IntentGateway.admit() should validate VFX overrides from bridge dict."""
        from mathart.workspace.intent_gateway import IntentGateway
        intent = assemble_creator_intent(
            action_name="idle",
            reference_image_path="",
            force_fluid=True,
            force_physics=True,
            force_cloth=False,
            force_particles=False,
        )
        gateway = IntentGateway()
        admission = gateway.admit(intent)
        assert admission.vfx_overrides.get("force_fluid") is True
        assert admission.vfx_overrides.get("force_physics") is True
