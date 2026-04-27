from __future__ import annotations

import inspect
import importlib
import time
from pathlib import Path

from mathart.animation.spine_preview import (
    HeadlessSpineRenderer,
    SpineJSONTensorSolver,
    create_demo_spine_json,
)
from mathart.core.artifact_schema import validate_artifact, validate_artifact_strict
from mathart.core.backend_registry import get_registry
from mathart.core import spine_preview_backend as _spine_preview_backend
from mathart.core.spine_preview_backend import SpinePreviewBackend


def _ensure_spine_preview_registered() -> None:
    importlib.reload(_spine_preview_backend)


def test_tensor_solver_builds_world_matrix_tensor(tmp_path):
    spine_path = create_demo_spine_json(tmp_path / "demo_spine.json", frame_count=48)
    solver = SpineJSONTensorSolver()
    clip = solver.solve(spine_path)

    assert clip.local_matrices.shape == (48, 6, 3, 3)
    assert clip.world_matrices.shape == (48, 6, 3, 3)
    assert clip.world_origins.shape == (48, 6, 2)
    assert clip.world_tips.shape == (48, 6, 2)
    assert clip.parent_indices.tolist()[0] == -1
    assert clip.parent_indices.tolist()[2] == 1
    assert clip.depth_levels[0] == (0,)
    assert 1 in clip.depth_levels[1]

    root_travel = clip.world_origins[-1, 0, 0] - clip.world_origins[0, 0, 0]
    import numpy as np

    arm_motion = np.ptp(clip.global_rotations_deg[:, 4])
    assert root_travel > 0.5
    assert arm_motion > 20.0


def test_headless_renderer_emits_mp4_gif_and_diagnostics(tmp_path):
    spine_path = create_demo_spine_json(tmp_path / "demo_spine.json", frame_count=36)
    solver = SpineJSONTensorSolver()
    clip = solver.solve(spine_path)
    renderer = HeadlessSpineRenderer(canvas_size=(256, 256), margin=20)

    result = renderer.render(
        clip,
        output_mp4_path=tmp_path / "preview.mp4",
        output_gif_path=tmp_path / "preview.gif",
        diagnostics_path=tmp_path / "preview.json",
    )

    assert Path(result.mp4_path).exists()
    assert Path(result.gif_path).exists()
    assert Path(result.diagnostics_path).exists()
    assert Path(result.mp4_path).stat().st_size > 0
    assert Path(result.gif_path).stat().st_size > 0
    assert result.frame_count == 36
    assert result.bone_count == 6


def test_backend_self_heals_ci_placeholder_and_returns_valid_manifest(tmp_path):
    backend = SpinePreviewBackend()
    manifest = backend.execute(
        {
            "output_dir": str(tmp_path),
            "name": "ci_guard_spine_preview",
            "spine_json_path": "ci_fixture_spine_json_path",
            "session_id": "SESSION-125-TEST",
            "canvas_size": (192, 192),
        }
    )

    errors = validate_artifact(manifest)
    strict_errors = validate_artifact_strict(manifest, min_schema_version="1.0.0")
    assert not errors
    assert not strict_errors
    assert manifest.artifact_family == "animation_preview"
    assert manifest.backend_type == "spine_preview"
    assert Path(manifest.outputs["preview_mp4"]).exists()
    assert Path(manifest.outputs["preview_gif"]).exists()
    assert manifest.metadata["frame_count"] >= 1
    assert manifest.metadata["bone_count"] >= 1


def test_registry_discovers_spine_preview_backend():
    _ensure_spine_preview_registered()
    reg = get_registry()
    entry = reg.get("spine_preview")
    assert entry is not None
    meta, cls = entry
    assert meta.name == "spine_preview"
    assert "animation_preview" in meta.artifact_families


def test_solver_uses_batched_matmul_and_renderer_avoids_gui_calls():
    import mathart.animation.spine_preview as module

    source = inspect.getsource(module)
    assert "np.matmul" in source
    assert "cv2.VideoWriter" in source
    assert ".write(frame)" in source


def test_multiframe_headless_preview_performance_is_subsecond(tmp_path):
    spine_path = create_demo_spine_json(tmp_path / "perf_spine.json", frame_count=180)
    solver = SpineJSONTensorSolver()
    clip = solver.solve(spine_path)
    renderer = HeadlessSpineRenderer(canvas_size=(160, 160), margin=12)

    start = time.perf_counter()
    result = renderer.render(
        clip,
        output_mp4_path=tmp_path / "perf_preview.mp4",
        output_gif_path=tmp_path / "perf_preview.gif",
        diagnostics_path=tmp_path / "perf_preview.json",
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"Expected subsecond render for 180-frame preview, got {elapsed:.3f}s"
    assert result.frame_count == 180
