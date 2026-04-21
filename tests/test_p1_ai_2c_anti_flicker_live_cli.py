from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_types import BackendType
from mathart.core.builtin_backends import AntiFlickerRenderBackend
from mathart.core.anti_flicker_runtime import normalize_server_address, plan_frame_chunks


def _install_gymnasium_stub(monkeypatch) -> None:
    if "gymnasium" in sys.modules:
        return
    gymnasium = types.ModuleType("gymnasium")

    class _Env:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

    class _Box:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    gymnasium.Env = _Env
    gymnasium.spaces = SimpleNamespace(Box=_Box)
    monkeypatch.setitem(sys.modules, "gymnasium", gymnasium)
    monkeypatch.setitem(sys.modules, "gymnasium.spaces", gymnasium.spaces)


class DummyKeyframePlan:
    def __init__(self, indices: list[int]) -> None:
        self.indices = indices
        self.identity_reference_index = indices[0] if indices else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "indices": list(self.indices),
            "identity_reference_index": self.identity_reference_index,
        }


class FakeExecutionResult:
    def __init__(self, *, output_images: list[str], prompt_id: str) -> None:
        self.success = True
        self.error_message = ""
        self.degraded_reason = ""
        self.elapsed_seconds = 0.25
        self.output_images = output_images
        self.output_videos: list[str] = []
        self.node_progress = {"progress_test": {"value": 1, "max": 1, "node": "test"}}
        self.prompt_id = prompt_id

    def to_dict(self) -> dict[str, object]:
        return {
            "success": True,
            "elapsed_seconds": self.elapsed_seconds,
            "output_images": list(self.output_images),
            "output_videos": [],
            "prompt_id": self.prompt_id,
        }


def _make_rgba_frame(size: tuple[int, int] = (8, 8), color: tuple[int, int, int, int] = (255, 0, 0, 255)) -> Image.Image:
    return Image.new("RGBA", size, color)


def _make_normal_frame(size: tuple[int, int] = (8, 8)) -> Image.Image:
    return Image.new("RGB", size, (127, 127, 255))


def _make_depth_frame(size: tuple[int, int] = (8, 8), gray: int = 127) -> Image.Image:
    return Image.new("L", size, gray)


def test_plan_frame_chunks_enforces_16_frame_windows() -> None:
    chunks = plan_frame_chunks(37, 16)
    assert [chunk.to_dict() for chunk in chunks] == [
        {"chunk_index": 0, "start_frame": 0, "end_frame": 15, "frame_count": 16},
        {"chunk_index": 1, "start_frame": 16, "end_frame": 31, "frame_count": 16},
        {"chunk_index": 2, "start_frame": 32, "end_frame": 36, "frame_count": 5},
    ]
    assert normalize_server_address("http://127.0.0.1:8188/") == "127.0.0.1:8188"


def test_validate_config_clamps_context_window_and_chunk_size() -> None:
    backend = AntiFlickerRenderBackend()
    validated, warnings = backend.validate_config(
        {
            "temporal": {"frame_count": 48, "chunk_size": 64},
            "comfyui": {"live_execution": True, "context_window": 32},
        }
    )
    assert validated["temporal"]["chunk_size"] == 16
    assert validated["temporal"]["chunking_enabled"] is True
    assert validated["comfyui"]["context_window"] == 16
    assert any("12GB safe limit" in warning for warning in warnings)


def test_live_backend_fast_fails_before_render_when_comfyui_is_offline(tmp_path: Path, monkeypatch) -> None:
    _install_gymnasium_stub(monkeypatch)
    from mathart.comfy_client import comfyui_ws_client
    from mathart.animation import headless_comfy_ebsynth

    backend = AntiFlickerRenderBackend()

    monkeypatch.setattr(comfyui_ws_client.ComfyUIClient, "is_server_online", lambda self: False)

    def _should_not_bake(*args, **kwargs):
        raise AssertionError("bake_auxiliary_maps should not run when offline fast-fail triggers")

    monkeypatch.setattr(headless_comfy_ebsynth.HeadlessNeuralRenderPipeline, "bake_auxiliary_maps", _should_not_bake)

    try:
        backend.execute(
            {
                "output_dir": str(tmp_path),
                "name": "offline_guard",
                "session_id": "TEST-108",
                "comfyui": {
                    "live_execution": True,
                    "fail_fast_on_offline": True,
                    "url": "http://127.0.0.1:8188",
                },
                "preset": {"name": "normal_depth_dual_controlnet"},
            }
        )
    except RuntimeError as exc:
        assert "offline" in str(exc).lower()
    else:
        raise AssertionError("Expected RuntimeError when ComfyUI server is offline")


def test_live_backend_chunks_large_sequences_and_materializes_outputs(tmp_path: Path, monkeypatch) -> None:
    _install_gymnasium_stub(monkeypatch)
    from mathart.comfy_client import comfyui_ws_client
    from mathart.animation import headless_comfy_ebsynth

    backend = AntiFlickerRenderBackend()
    recorded_frame_counts: list[int] = []

    monkeypatch.setattr(comfyui_ws_client.ComfyUIClient, "is_server_online", lambda self: True)

    def _fake_bake_auxiliary_maps(self, skeleton, animation_func, style, frames, width, height):
        source_frames = [_make_rgba_frame() for _ in range(frames)]
        normal_maps = [_make_normal_frame() for _ in range(frames)]
        depth_maps = [_make_depth_frame() for _ in range(frames)]
        mask_maps = [Image.new("L", (8, 8), 255) for _ in range(frames)]
        mv_sequence = SimpleNamespace(fields=[], total_motion_energy=0.0)
        return source_frames, normal_maps, depth_maps, mask_maps, mv_sequence

    def _fake_plan_keyframes(self, source_frames, mask_maps, mv_sequence):
        return DummyKeyframePlan([0, 16, 32])

    def _fake_execute_workflow(self, payload, run_label="mathart", progress_callback=None):
        frame_count = int(payload["mathart_lock_manifest"]["temporal_config"]["frame_count"])
        recorded_frame_counts.append(frame_count)
        output_images: list[str] = []
        chunk_dir = tmp_path / "fake_comfy_outputs" / run_label
        chunk_dir.mkdir(parents=True, exist_ok=True)
        for index in range(frame_count):
            image_path = chunk_dir / f"render_{index:05d}.png"
            _make_rgba_frame().save(image_path)
            output_images.append(str(image_path))
        if callable(progress_callback):
            progress_callback({"event_type": "progress", "node": "ksampler", "value": frame_count, "max": frame_count})
        return FakeExecutionResult(output_images=output_images, prompt_id=run_label)

    monkeypatch.setattr(headless_comfy_ebsynth.HeadlessNeuralRenderPipeline, "bake_auxiliary_maps", _fake_bake_auxiliary_maps)
    monkeypatch.setattr(headless_comfy_ebsynth.HeadlessNeuralRenderPipeline, "plan_keyframes", _fake_plan_keyframes)
    monkeypatch.setattr(comfyui_ws_client.ComfyUIClient, "execute_workflow", _fake_execute_workflow)

    manifest = backend.execute(
        {
            "output_dir": str(tmp_path),
            "name": "chunked_runtime",
            "session_id": "TEST-108",
            "temporal": {"frame_count": 37, "chunk_size": 16, "fps": 12},
            "comfyui": {
                "live_execution": True,
                "fail_fast_on_offline": True,
                "url": "http://127.0.0.1:8188",
                "context_window": 16,
            },
        }
    )

    assert recorded_frame_counts == [16, 16, 5]
    assert manifest.metadata["execution_mode"] == "live_comfyui_chunked"
    assert manifest.metadata["chunking"]["chunk_size"] == 16
    assert len(manifest.metadata["payload"]["frame_sequence"]) == 37
    assert Path(manifest.outputs["frame_directory"]).exists()


def test_cli_anti_flicker_render_keeps_stdout_json_and_progress_on_stderr(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_pipeline_module = types.ModuleType("mathart.pipeline")

    class _StubPipeline:
        def __init__(self, output_dir: str, verbose: bool = False) -> None:
            self.output_dir = output_dir
            self.verbose = verbose

        def run_backend(self, backend_name: str, context: dict[str, object]) -> ArtifactManifest:
            raise AssertionError("run_backend should be monkeypatched inside the CLI test")

    fake_pipeline_module.AssetPipeline = _StubPipeline
    monkeypatch.setitem(sys.modules, "mathart.pipeline", fake_pipeline_module)

    import mathart.cli as cli_module
    cli_module = importlib.reload(cli_module)

    def _fake_run_backend(self, backend_name: str, context: dict[str, object]) -> ArtifactManifest:
        assert backend_name == "anti_flicker_render"
        assert context["comfyui"]["live_execution"] is True
        callback = context.get("_progress_callback")
        assert callable(callback)
        callback({
            "event_type": "chunk_start",
            "chunk_index": 0,
            "chunk_count": 1,
            "start_frame": 0,
            "end_frame": 3,
        })
        return ArtifactManifest(
            artifact_family=ArtifactFamily.ANTI_FLICKER_REPORT.value,
            backend_type=BackendType.ANTI_FLICKER_RENDER,
            version="5.0.0",
            session_id="CLI-108",
            outputs={"frame_directory": str(tmp_path / "frames")},
            metadata={"payload": {"frame_sequence": [], "time_range": {"start_frame": 0, "end_frame": -1, "fps": 12, "total_frames": 0}}},
            quality_metrics={},
            references=[],
            tags=["anti_flicker"],
        )

    monkeypatch.setattr(cli_module.AssetPipeline, "run_backend", _fake_run_backend)

    exit_code = cli_module.main(
        [
            "anti_flicker_render",
            "--output-dir",
            str(tmp_path),
            "--set",
            "temporal.frame_count=4",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["resolved_backend"] == "anti_flicker_render"
    assert "chunk_start" in captured.err
    assert captured.out.strip().startswith("{")
    assert "chunk_start" not in captured.out
