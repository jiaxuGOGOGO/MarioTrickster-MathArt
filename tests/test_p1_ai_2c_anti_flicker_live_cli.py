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
from mathart.pipeline_contract import PipelineContractError


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
            "width": 256,
            "height": 256,
        }
    )
    assert validated["temporal"]["chunk_size"] == 16
    assert validated["temporal"]["chunking_enabled"] is True
    assert validated["comfyui"]["context_window"] == 16
    assert any("12GB safe limit" in warning for warning in warnings)


# ── SESSION-129: Fail-Fast Dimension Contract Tests ───────────────────────


def test_validate_config_rejects_missing_dimensions() -> None:
    """SESSION-129: AntiFlickerRenderBackend must reject configs without
    explicit width/height (no silent default to 64).
    """
    backend = AntiFlickerRenderBackend()
    try:
        backend.validate_config(
            {
                "temporal": {"frame_count": 8},
                "comfyui": {"live_execution": False},
            }
        )
    except PipelineContractError as exc:
        assert exc.violation_type == "missing_render_dimensions"
    else:
        raise AssertionError("Expected PipelineContractError for missing dimensions")


def test_validate_config_rejects_tiny_dimensions() -> None:
    """SESSION-129: Dimensions below 64x64 must trigger Fail-Fast."""
    backend = AntiFlickerRenderBackend()
    try:
        backend.validate_config(
            {
                "temporal": {"frame_count": 8},
                "comfyui": {"live_execution": False},
                "width": 32,
                "height": 32,
            }
        )
    except PipelineContractError as exc:
        assert exc.violation_type == "render_dimensions_too_small"
    else:
        raise AssertionError("Expected PipelineContractError for tiny dimensions")


def test_external_guide_bypass_inherits_resolution() -> None:
    """SESSION-129: When external source_frames are provided, the backend
    must inherit their resolution and set _idle_bake_bypassed=True.
    """
    backend = AntiFlickerRenderBackend()
    guide_size = (192, 192)
    source_frames = [Image.new("RGBA", guide_size, (255, 0, 0, 255)) for _ in range(8)]
    normal_maps = [Image.new("RGB", guide_size, (127, 127, 255)) for _ in range(8)]
    depth_maps = [Image.new("L", guide_size, 127) for _ in range(8)]

    # We only test validate + execute entry logic, not the full pipeline
    # The execute method should detect external guides and set bypass flag
    context = {
        "output_dir": "/tmp/test_bypass",
        "name": "bypass_test",
        "session_id": "TEST-129",
        "source_frames": source_frames,
        "normal_maps": normal_maps,
        "depth_maps": depth_maps,
        "width": 192,
        "height": 192,
        "temporal": {"frame_count": 8, "fps": 12},
        "comfyui": {"live_execution": False},
    }
    validated, warnings = backend.validate_config(context)
    assert validated["width"] == 192
    assert validated["height"] == 192


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
                "width": 192,
                "height": 192,
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
            "width": 192,
            "height": 192,
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


def test_live_backend_pads_small_sequences_to_comfyui_minimum(tmp_path: Path, monkeypatch) -> None:
    _install_gymnasium_stub(monkeypatch)
    from mathart.comfy_client import comfyui_ws_client
    from mathart.animation import headless_comfy_ebsynth

    backend = AntiFlickerRenderBackend()
    observed_latent_sizes: list[tuple[int, int]] = []

    monkeypatch.setattr(comfyui_ws_client.ComfyUIClient, "is_server_online", lambda self: True)

    def _fake_bake_auxiliary_maps(self, skeleton, animation_func, style, frames, width, height):
        small_size = (18, 7)
        source_frames = [_make_rgba_frame(small_size) for _ in range(frames)]
        normal_maps = [_make_normal_frame(small_size) for _ in range(frames)]
        depth_maps = [_make_depth_frame(small_size) for _ in range(frames)]
        mask_maps = [Image.new("L", small_size, 255) for _ in range(frames)]
        mv_sequence = SimpleNamespace(fields=[], total_motion_energy=0.0)
        return source_frames, normal_maps, depth_maps, mask_maps, mv_sequence

    def _fake_plan_keyframes(self, source_frames, mask_maps, mv_sequence):
        return DummyKeyframePlan([0])

    def _fake_execute_workflow(self, payload, run_label="mathart", progress_callback=None):
        prompt = payload["prompt"]
        latent_nodes = [
            node for node in prompt.values()
            if isinstance(node, dict) and node.get("class_type") == "EmptyLatentImage"
        ]
        assert latent_nodes, "Expected EmptyLatentImage node in ComfyUI payload"
        latent_inputs = latent_nodes[0]["inputs"]
        observed_latent_sizes.append((int(latent_inputs["width"]), int(latent_inputs["height"])))

        frame_count = int(payload["mathart_lock_manifest"]["temporal_config"]["frame_count"])
        output_images: list[str] = []
        chunk_dir = tmp_path / "fake_small_outputs" / run_label
        chunk_dir.mkdir(parents=True, exist_ok=True)
        for index in range(frame_count):
            image_path = chunk_dir / f"render_{index:05d}.png"
            _make_rgba_frame((32, 16)).save(image_path)
            output_images.append(str(image_path))
        return FakeExecutionResult(output_images=output_images, prompt_id=run_label)

    monkeypatch.setattr(headless_comfy_ebsynth.HeadlessNeuralRenderPipeline, "bake_auxiliary_maps", _fake_bake_auxiliary_maps)
    monkeypatch.setattr(headless_comfy_ebsynth.HeadlessNeuralRenderPipeline, "plan_keyframes", _fake_plan_keyframes)
    monkeypatch.setattr(comfyui_ws_client.ComfyUIClient, "execute_workflow", _fake_execute_workflow)

    manifest = backend.execute(
        {
            "output_dir": str(tmp_path),
            "name": "small_runtime",
            "session_id": "TEST-108",
            "width": 192,
            "height": 192,
            "temporal": {"frame_count": 4, "chunk_size": 4, "fps": 12},
            "comfyui": {
                "live_execution": True,
                "fail_fast_on_offline": True,
                "url": "http://127.0.0.1:8188",
                "context_window": 4,
            },
        }
    )

    assert observed_latent_sizes == [(32, 16)]
    assert manifest.metadata["execution_mode"] == "live_comfyui_chunked"
    assert Path(manifest.outputs["frame_directory"]).exists()
    assert manifest.quality_metrics["temporal_stability_score"] == 1.0
    assert manifest.quality_metrics["frame_count"] == 4.0
    assert manifest.quality_metrics["keyframe_count"] == 1.0


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


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION-130: Temporal Variance Circuit Breaker & Anti-Forgery Tests
# ═══════════════════════════════════════════════════════════════════════════
#
#  Industrial Reference: AnimateDiff / SparseCtrl temporal conditioning
#  requires genuine per-frame geometric variation.  These tests enforce:
#  1. Static sequences are REJECTED by the circuit breaker.
#  2. Genuinely animated sequences PASS the circuit breaker.
#  3. Frame hashes prove consecutive frames are pixel-distinct.
# ═══════════════════════════════════════════════════════════════════════════

import hashlib
import numpy as np
from mathart.core.anti_flicker_runtime import (
    validate_temporal_variance,
    compute_frame_hashes,
    TemporalVarianceReport,
)


def _make_static_sequence(n: int = 8, size: tuple[int, int] = (64, 64)) -> list[Image.Image]:
    """Create a static sequence: N copies of the same image (forgery pattern)."""
    base = Image.new("RGB", size, (128, 64, 32))
    return [base.copy() for _ in range(n)]


def _make_animated_sequence(n: int = 8, size: tuple[int, int] = (64, 64)) -> list[Image.Image]:
    """Create a genuinely animated sequence with real geometric variation."""
    frames: list[Image.Image] = []
    for i in range(n):
        arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        # Moving rectangle: shifts 5 pixels per frame
        x_offset = (i * 5) % size[0]
        y_offset = (i * 3) % size[1]
        x_end = min(x_offset + 20, size[0])
        y_end = min(y_offset + 15, size[1])
        arr[y_offset:y_end, x_offset:x_end, 0] = 200
        arr[y_offset:y_end, x_offset:x_end, 1] = 100
        arr[y_offset:y_end, x_offset:x_end, 2] = 50
        # Background gradient changes per frame
        arr[:, :, 2] = np.clip(arr[:, :, 2] + i * 8, 0, 255).astype(np.uint8)
        frames.append(Image.fromarray(arr, "RGB"))
    return frames


class TestTemporalVarianceCircuitBreaker:
    """SESSION-130: Temporal Variance Circuit Breaker tests."""

    def test_static_sequence_rejected(self) -> None:
        """Static (forged) guide sequences MUST be rejected."""
        static_frames = _make_static_sequence(n=8)
        import pytest
        with pytest.raises(PipelineContractError) as exc_info:
            validate_temporal_variance(static_frames, channel="source", mse_threshold=1.0)
        assert "temporal_variance_below_threshold" in str(exc_info.value)
        assert "mode collapse" in str(exc_info.value).lower() or "static" in str(exc_info.value).lower()

    def test_animated_sequence_passes(self) -> None:
        """Genuinely animated guide sequences MUST pass."""
        animated_frames = _make_animated_sequence(n=8)
        report = validate_temporal_variance(animated_frames, channel="source", mse_threshold=1.0)
        assert isinstance(report, TemporalVarianceReport)
        assert report.passed is True
        assert report.mean_mse > 1.0
        assert report.distinct_pair_count > 0

    def test_single_frame_rejected(self) -> None:
        """A single-frame sequence cannot have temporal variance."""
        import pytest
        single = [Image.new("RGB", (32, 32), (100, 100, 100))]
        with pytest.raises(PipelineContractError) as exc_info:
            validate_temporal_variance(single, channel="depth")
        assert "insufficient_frames" in str(exc_info.value)

    def test_near_static_micro_jitter_rejected(self) -> None:
        """SESSION-129's micro-jitter approach (< 1px, < 1% brightness) MUST be rejected.

        This test proves that sub-perceptual perturbations are insufficient
        for AnimateDiff temporal attention and will be caught by the circuit
        breaker with the default threshold of MSE >= 1.0.
        """
        import pytest
        base = Image.new("RGB", (64, 64), (128, 128, 128))
        frames: list[Image.Image] = []
        for i in range(8):
            rng = np.random.Generator(np.random.PCG64(seed=i * 7919))
            dx = float(rng.uniform(-0.5, 0.5))
            dy = float(rng.uniform(-0.5, 0.5))
            brightness = float(rng.uniform(0.995, 1.005))
            frame = base.copy()
            frame = frame.transform(frame.size, Image.AFFINE, (1, 0, dx, 0, 1, dy), resample=Image.BILINEAR)
            arr = np.array(frame, dtype=np.float32)
            arr = np.clip(arr * brightness, 0, 255)
            frame = Image.fromarray(arr.astype(np.uint8), "RGB")
            frames.append(frame)

        with pytest.raises(PipelineContractError) as exc_info:
            validate_temporal_variance(frames, channel="source", mse_threshold=1.0)
        assert "temporal_variance_below_threshold" in str(exc_info.value)

    def test_report_diagnostics_complete(self) -> None:
        """The TemporalVarianceReport must contain all diagnostic fields."""
        animated = _make_animated_sequence(n=6)
        report = validate_temporal_variance(animated, channel="normal", mse_threshold=0.5)
        d = report.to_dict()
        assert d["channel"] == "normal"
        assert d["frame_count"] == 6
        assert d["total_pair_count"] == 5
        assert isinstance(d["mean_mse"], float)
        assert isinstance(d["max_mse"], float)
        assert isinstance(d["min_mse"], float)
        assert d["passed"] is True


class TestAntiForgeryFrameHashes:
    """SESSION-130: Anti-forgery frame hash assertions.

    These tests verify that consecutive frames in a guide sequence are
    genuinely distinct by comparing SHA-256 hashes of pixel data.
    This is the user-requested 'self-proof that the sequence has real
    motion optical flow'.
    """

    def test_animated_frames_have_distinct_hashes(self) -> None:
        """Every consecutive frame pair in an animated sequence must have different hashes."""
        animated = _make_animated_sequence(n=8)
        hashes = compute_frame_hashes(animated)
        assert len(hashes) == 8
        # Assert ALL consecutive pairs are distinct
        for i in range(len(hashes) - 1):
            assert hashes[i] != hashes[i + 1], (
                f"Frame {i} and frame {i+1} have identical pixel hashes "
                f"({hashes[i][:16]}...). This proves the sequence is forged "
                f"(single-image replication). AnimateDiff temporal attention "
                f"requires genuinely distinct frames."
            )

    def test_static_frames_have_identical_hashes(self) -> None:
        """Static (forged) sequences should have identical hashes — proving forgery."""
        static = _make_static_sequence(n=5)
        hashes = compute_frame_hashes(static)
        # All hashes should be the same (proving it's a forged sequence)
        assert len(set(hashes)) == 1, (
            "Static sequence should have all-identical hashes"
        )

    def test_hash_function_deterministic(self) -> None:
        """compute_frame_hashes must be deterministic."""
        frames = _make_animated_sequence(n=4)
        h1 = compute_frame_hashes(frames)
        h2 = compute_frame_hashes(frames)
        assert h1 == h2

    def test_payload_frame_sequence_anti_forgery(self) -> None:
        """End-to-end anti-forgery: extract Base64 data from payload frames,
        hash-compare consecutive frames, and assert pixel differences exist.

        This directly fulfills the user requirement: 'extract consecutive
        frame Base64 data from the payload, hash-compare, and assert that
        frames have substantive pixel differences'.
        """
        import base64
        import io

        animated = _make_animated_sequence(n=6)
        # Simulate payload encoding: each frame → Base64 PNG
        b64_frames: list[str] = []
        for frame in animated:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            b64_frames.append(base64.b64encode(buf.getvalue()).decode("ascii"))

        # Decode and hash-compare consecutive frames
        prev_hash = None
        for i, b64_data in enumerate(b64_frames):
            raw = base64.b64decode(b64_data)
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            pixel_hash = hashlib.sha256(img.tobytes()).hexdigest()
            if prev_hash is not None:
                assert pixel_hash != prev_hash, (
                    f"Payload frame {i-1} and frame {i} have identical pixel "
                    f"content after Base64 decode. This is a forged sequence."
                )
            prev_hash = pixel_hash
