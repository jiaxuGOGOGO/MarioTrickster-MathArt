"""
tests/test_unity_2d_anim.py — SESSION-124 Regression Tests

Comprehensive test suite for the Unity 2D Native Animation Format
zero-dependency direct export pipeline.

Test Categories
---------------
1. **Tensor Space Converter**: Coordinate transformation, Euler unwrap,
   tangent computation, batch tangent computation.
2. **Unity YAML Emitter**: .anim file structure, header compliance,
   keyframe formatting, curve section presence.
3. **Meta & GUID Generator**: Deterministic GUID, .meta file format,
   controller YAML structure.
4. **End-to-End Export**: Full Clip2D → files pipeline, file existence,
   content validation.
5. **Backend Registry Integration**: Discovery, validate_config, execute,
   manifest schema compliance.
6. **Performance**: Throughput for large bone/frame counts.

Architecture Discipline
-----------------------
- Each test uses its own ``np.random.default_rng(seed)`` per NEP-19.
- Registry tests call ``restore_builtin_backends()`` to avoid pollution.
- All file I/O uses ``tmp_path`` fixtures for hermetic isolation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pytest

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mathart.animation.orthographic_projector import (
    Bone2D, Clip2D, Pose2D,
)
from mathart.animation.unity_2d_anim import (
    BoneCurveData,
    TangentArrays,
    TensorSpaceConverter,
    Unity2DAnimExporter,
    Unity2DAnimExportResult,
    UnityYAMLEmitter,
    emit_animator_controller,
    emit_meta_file,
    generate_deterministic_guid,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def _make_simple_clip(
    n_bones: int = 3,
    n_frames: int = 10,
    fps: float = 30.0,
    seed: int = 42,
) -> Clip2D:
    """Build a synthetic Clip2D with controllable bone/frame counts."""
    rng = np.random.default_rng(seed)

    bones = []
    parent = None
    for i in range(n_bones):
        name = f"bone_{i}"
        bones.append(Bone2D(
            name=name,
            parent=parent,
            x=rng.uniform(-1, 1),
            y=rng.uniform(0, 2),
            rotation=rng.uniform(-30, 30),
            length=rng.uniform(0.2, 0.8),
            scale_x=1.0,
            scale_y=1.0,
            sorting_order=0,
        ))
        parent = name

    frames = []
    for fi in range(n_frames):
        t = fi / max(n_frames - 1, 1)
        bt = {}
        for bi, bone in enumerate(bones):
            angle = math.sin(t * 2 * math.pi + bi * 0.5) * 20.0
            bt[bone.name] = {
                "x": bone.x + math.sin(t * math.pi) * 0.1,
                "y": bone.y + math.cos(t * math.pi) * 0.05,
                "rotation": angle,
                "scale_x": 1.0,
                "scale_y": 1.0,
            }
        frames.append(Pose2D(
            bone_transforms=bt,
            root_x=0.0,
            root_y=0.0,
            root_rotation=0.0,
            sorting_orders={b.name: 0 for b in bones},
        ))

    return Clip2D(
        name="test_clip",
        fps=fps,
        frames=frames,
        skeleton_bones=bones,
    )


def _make_euler_wrap_clip() -> Clip2D:
    """Build a clip where rotation crosses the ±180° boundary.

    This is the critical test for Euler angle unwrapping.
    """
    bones = [
        Bone2D(name="spinner", parent=None, x=0.0, y=0.0, rotation=0.0,
               length=1.0, scale_x=1.0, scale_y=1.0, sorting_order=0),
    ]

    frames = []
    # Rotation goes: 170, 175, 180, -175, -170 (wraps at ±180)
    angles = [170.0, 175.0, 179.0, -179.0, -175.0, -170.0]
    for angle in angles:
        frames.append(Pose2D(
            bone_transforms={
                "spinner": {"x": 0.0, "y": 0.0, "rotation": angle,
                            "scale_x": 1.0, "scale_y": 1.0},
            },
            root_x=0.0, root_y=0.0, root_rotation=0.0,
            sorting_orders={"spinner": 0},
        ))

    return Clip2D(
        name="euler_wrap_test",
        fps=30.0,
        frames=frames,
        skeleton_bones=bones,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Tensor Space Converter Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestTensorSpaceConverter:
    """Tests for coordinate transformation and Euler unwrapping."""

    def test_basic_conversion(self):
        """Converter produces correct number of BoneCurveData objects."""
        clip = _make_simple_clip(n_bones=4, n_frames=8)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)

        assert len(curves) == 4
        for bc in curves:
            assert isinstance(bc, BoneCurveData)
            assert len(bc.pos_x) == 8
            assert len(bc.rot_z) == 8

    def test_empty_clip(self):
        """Converter handles empty clips gracefully."""
        clip = Clip2D(name="empty", fps=30.0, frames=[], skeleton_bones=[])
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        assert curves == []

    def test_single_frame(self):
        """Converter handles single-frame clips."""
        clip = _make_simple_clip(n_bones=2, n_frames=1)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        assert len(curves) == 2
        for bc in curves:
            assert len(bc.pos_x) == 1

    def test_euler_unwrap_prevents_discontinuity(self):
        """Euler angles crossing ±180° are unwrapped to prevent jumps.

        🔴 Anti-Euler-Flip Guard: Adjacent frame angle difference must
        NEVER exceed 180° after unwrapping.
        """
        clip = _make_euler_wrap_clip()
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)

        assert len(curves) == 1
        rot_z = curves[0].rot_z

        # After unwrapping and negation, adjacent differences should be small
        diffs = np.abs(np.diff(rot_z))
        max_diff = np.max(diffs)
        assert max_diff < 180.0, (
            f"Euler unwrap failed: max adjacent diff = {max_diff:.1f}° (must be < 180°)"
        )

    def test_rotation_negation_for_handedness(self):
        """Rotation Z is negated for left-hand coordinate system."""
        bones = [
            Bone2D(name="test", parent=None, x=0.0, y=0.0, rotation=0.0,
                   length=1.0, scale_x=1.0, scale_y=1.0, sorting_order=0),
        ]
        frames = [
            Pose2D(
                bone_transforms={"test": {"x": 0.0, "y": 0.0, "rotation": 45.0,
                                           "scale_x": 1.0, "scale_y": 1.0}},
                root_x=0.0, root_y=0.0, root_rotation=0.0,
                sorting_orders={"test": 0},
            ),
        ]
        clip = Clip2D(name="neg_test", fps=30.0, frames=frames, skeleton_bones=bones)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)

        # 45° right-hand → -45° left-hand
        assert len(curves) == 1
        assert abs(curves[0].rot_z[0] - (-45.0)) < 0.01

    def test_bone_hierarchy_paths(self):
        """Bone paths follow Unity parent/child notation."""
        clip = _make_simple_clip(n_bones=3, n_frames=2)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)

        # bone_0 has no parent → path = "bone_0"
        assert curves[0].path == "bone_0"
        # bone_1 parent = bone_0 → path = "bone_0/bone_1"
        assert curves[1].path == "bone_0/bone_1"
        # bone_2 parent = bone_1 → path = "bone_0/bone_1/bone_2"
        assert curves[2].path == "bone_0/bone_1/bone_2"


class TestTangentComputation:
    """Tests for tangent (slope) computation."""

    def test_linear_values(self):
        """Linear values produce constant slopes."""
        converter = TensorSpaceConverter(fps=30.0)
        times = np.array([0.0, 1.0, 2.0, 3.0])
        values = np.array([0.0, 2.0, 4.0, 6.0])  # slope = 2.0

        tangents = converter.compute_tangents(times, values)
        np.testing.assert_allclose(tangents.out_slopes, 2.0, atol=1e-10)
        np.testing.assert_allclose(tangents.in_slopes, 2.0, atol=1e-10)

    def test_single_value(self):
        """Single-value arrays produce zero slopes."""
        converter = TensorSpaceConverter(fps=30.0)
        times = np.array([0.0])
        values = np.array([5.0])

        tangents = converter.compute_tangents(times, values)
        assert tangents.in_slopes[0] == 0.0
        assert tangents.out_slopes[0] == 0.0

    def test_boundary_clamping(self):
        """First in_slope = first out_slope, last out_slope = last in_slope."""
        converter = TensorSpaceConverter(fps=30.0)
        times = np.array([0.0, 1.0, 2.0])
        values = np.array([0.0, 3.0, 1.0])

        tangents = converter.compute_tangents(times, values)
        # First: in_slope[0] should equal out_slope[0] = (3-0)/(1-0) = 3
        assert abs(tangents.in_slopes[0] - tangents.out_slopes[0]) < 1e-10
        # Last: out_slope[-1] should equal in_slope[-1] = (1-3)/(2-1) = -2
        assert abs(tangents.out_slopes[-1] - tangents.in_slopes[-1]) < 1e-10

    def test_batch_tangents(self):
        """Batch tangent computation matches per-channel computation."""
        converter = TensorSpaceConverter(fps=30.0)
        rng = np.random.default_rng(123)
        times = np.linspace(0, 1, 20)
        values = rng.standard_normal((20, 5))

        in_batch, out_batch = converter.compute_tangents_batch(times, values)

        for ch in range(5):
            single = converter.compute_tangents(times, values[:, ch])
            np.testing.assert_allclose(in_batch[:, ch], single.in_slopes, atol=1e-10)
            np.testing.assert_allclose(out_batch[:, ch], single.out_slopes, atol=1e-10)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Unity YAML Emitter Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnityYAMLEmitter:
    """Tests for the high-throughput YAML string emitter."""

    def test_anim_header_compliance(self):
        """Generated .anim starts with correct YAML header and class ID."""
        clip = _make_simple_clip(n_bones=2, n_frames=5)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=30.0)

        content = emitter.emit_anim_clip("test_clip", curves, 5)

        assert content.startswith("%YAML 1.1\n")
        assert "%TAG !u! tag:unity3d.com,2011:" in content
        assert "--- !u!74 &7400000" in content
        assert "AnimationClip:" in content

    def test_anim_contains_clip_name(self):
        """Generated .anim contains the specified clip name."""
        clip = _make_simple_clip(n_bones=1, n_frames=3)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=30.0)

        content = emitter.emit_anim_clip("my_animation", curves, 3)
        assert "m_Name: my_animation" in content

    def test_anim_contains_euler_curves(self):
        """Generated .anim contains m_EulerCurves section."""
        clip = _make_simple_clip(n_bones=2, n_frames=4)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=30.0)

        content = emitter.emit_anim_clip("test", curves, 4)
        assert "m_EulerCurves:" in content

    def test_anim_contains_position_curves(self):
        """Generated .anim contains m_PositionCurves section."""
        clip = _make_simple_clip(n_bones=2, n_frames=4)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=30.0)

        content = emitter.emit_anim_clip("test", curves, 4)
        assert "m_PositionCurves:" in content

    def test_anim_contains_scale_curves(self):
        """Generated .anim contains m_ScaleCurves section."""
        clip = _make_simple_clip(n_bones=2, n_frames=4)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=30.0)

        content = emitter.emit_anim_clip("test", curves, 4)
        assert "m_ScaleCurves:" in content

    def test_anim_keyframe_structure(self):
        """Keyframes have correct serializedVersion, time, value, slope fields."""
        clip = _make_simple_clip(n_bones=1, n_frames=3)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=30.0)

        content = emitter.emit_anim_clip("test", curves, 3)
        assert "serializedVersion: 3" in content
        assert "time:" in content
        assert "value:" in content
        assert "inSlope:" in content
        assert "outSlope:" in content
        assert "tangentMode: 0" in content
        assert "weightedMode: 0" in content
        assert "inWeight:" in content
        assert "outWeight:" in content

    def test_anim_loop_setting(self):
        """Loop flag is correctly set in AnimationClipSettings."""
        clip = _make_simple_clip(n_bones=1, n_frames=3)
        converter = TensorSpaceConverter(fps=30.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=30.0)

        content_loop = emitter.emit_anim_clip("test", curves, 3, loop=True)
        assert "m_LoopTime: 1" in content_loop

        content_no_loop = emitter.emit_anim_clip("test", curves, 3, loop=False)
        assert "m_LoopTime: 0" in content_no_loop

    def test_no_pyyaml_import(self):
        """🔴 Anti-PyYAML-Overhead: The module must NEVER import yaml."""
        import mathart.animation.unity_2d_anim as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "import yaml" not in source, "unity_2d_anim.py must NEVER import yaml"

    def test_anim_sample_rate(self):
        """Sample rate matches configured FPS."""
        clip = _make_simple_clip(n_bones=1, n_frames=3)
        converter = TensorSpaceConverter(fps=24.0)
        curves = converter.clip2d_to_bone_curves(clip)
        emitter = UnityYAMLEmitter(fps=24.0)

        content = emitter.emit_anim_clip("test", curves, 3)
        assert "m_SampleRate: 24.0" in content


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Meta & GUID Generator Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGUIDAndMeta:
    """Tests for deterministic GUID generation and .meta file emission."""

    def test_guid_deterministic(self):
        """Same asset name always produces the same GUID."""
        guid1 = generate_deterministic_guid("biped_walk.anim")
        guid2 = generate_deterministic_guid("biped_walk.anim")
        assert guid1 == guid2

    def test_guid_is_32_hex(self):
        """GUID is exactly 32 lowercase hex characters."""
        guid = generate_deterministic_guid("test.anim")
        assert len(guid) == 32
        assert all(c in "0123456789abcdef" for c in guid)

    def test_guid_different_names(self):
        """Different asset names produce different GUIDs."""
        guid1 = generate_deterministic_guid("walk.anim")
        guid2 = generate_deterministic_guid("run.anim")
        assert guid1 != guid2

    def test_guid_matches_md5(self):
        """🔴 Anti-GUID-Collision: GUID must be md5(name.encode()).hexdigest()."""
        name = "test_asset.anim"
        expected = hashlib.md5(name.encode("utf-8")).hexdigest()
        actual = generate_deterministic_guid(name)
        assert actual == expected

    def test_meta_file_structure(self):
        """Meta file has correct format and contains GUID."""
        content = emit_meta_file("test.anim", main_object_file_id=7400000)
        assert "fileFormatVersion: 2" in content
        assert "guid:" in content
        assert "NativeFormatImporter:" in content
        assert "mainObjectFileID: 7400000" in content

    def test_meta_file_guid_matches(self):
        """Meta file GUID matches the deterministic generator."""
        name = "my_clip.anim"
        content = emit_meta_file(name)
        expected_guid = generate_deterministic_guid(name)
        assert expected_guid in content

    def test_controller_structure(self):
        """Controller YAML has correct headers and state machine."""
        content = emit_animator_controller(
            "test_ctrl",
            anim_clip_entries=[{
                "name": "idle",
                "anim_guid": "a" * 32,
                "anim_file_id": "7400000",
            }],
        )
        assert "%YAML 1.1" in content
        assert "%TAG !u! tag:unity3d.com,2011:" in content
        assert "--- !u!91 &9100000" in content
        assert "AnimatorController:" in content
        assert "m_Name: test_ctrl" in content
        assert "AnimatorStateMachine:" in content
        assert "AnimatorState:" in content

    def test_controller_multiple_states(self):
        """Controller with multiple clips creates multiple states."""
        entries = [
            {"name": "walk", "anim_guid": "a" * 32, "anim_file_id": "7400000"},
            {"name": "run", "anim_guid": "b" * 32, "anim_file_id": "7400000"},
            {"name": "idle", "anim_guid": "c" * 32, "anim_file_id": "7400000"},
        ]
        content = emit_animator_controller("multi_ctrl", entries)

        # Should have 3 AnimatorState blocks
        state_count = content.count("AnimatorState:")
        assert state_count == 3

        # All state names present
        assert "m_Name: walk" in content
        assert "m_Name: run" in content
        assert "m_Name: idle" in content


# ═══════════════════════════════════════════════════════════════════════════════
# 4. End-to-End Export Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndToEndExport:
    """Tests for the full Clip2D → files export pipeline."""

    def test_export_creates_all_files(self, tmp_path):
        """Export creates .anim, .controller, and .meta files."""
        clip = _make_simple_clip(n_bones=3, n_frames=10)
        exporter = Unity2DAnimExporter(fps=30.0)

        result = exporter.export(clip, tmp_path, clip_name="test_clip")

        assert isinstance(result, Unity2DAnimExportResult)
        assert len(result.anim_paths) == 1
        assert result.controller_path != ""
        assert len(result.meta_paths) == 2

        # All files exist
        for path in result.anim_paths:
            assert Path(path).exists()
        assert Path(result.controller_path).exists()
        for path in result.meta_paths:
            assert Path(path).exists()

    def test_export_file_names(self, tmp_path):
        """Exported files have correct names."""
        clip = _make_simple_clip(n_bones=2, n_frames=5)
        exporter = Unity2DAnimExporter(fps=30.0)

        result = exporter.export(clip, tmp_path, clip_name="biped_walk")

        assert Path(result.anim_paths[0]).name == "biped_walk.anim"
        assert Path(result.controller_path).name == "biped_walk_controller.controller"

    def test_export_anim_content_valid(self, tmp_path):
        """Exported .anim file content is valid Unity YAML."""
        clip = _make_simple_clip(n_bones=2, n_frames=5)
        exporter = Unity2DAnimExporter(fps=30.0)

        result = exporter.export(clip, tmp_path, clip_name="valid_test")

        content = Path(result.anim_paths[0]).read_text()
        assert content.startswith("%YAML 1.1\n")
        assert "AnimationClip:" in content
        assert "m_Name: valid_test" in content

    def test_export_result_metrics(self, tmp_path):
        """Export result contains correct metrics."""
        clip = _make_simple_clip(n_bones=4, n_frames=20)
        exporter = Unity2DAnimExporter(fps=30.0)

        result = exporter.export(clip, tmp_path)

        assert result.bone_count == 4
        assert result.frame_count == 20
        assert result.total_keyframes == 20 * 4 * 3  # frames * bones * 3 curve types
        assert result.export_time_ms > 0

    def test_export_guids_deterministic(self, tmp_path):
        """GUIDs in export result are deterministic."""
        clip = _make_simple_clip(n_bones=2, n_frames=5)
        exporter = Unity2DAnimExporter(fps=30.0)

        result1 = exporter.export(clip, tmp_path / "run1", clip_name="det_test")
        result2 = exporter.export(clip, tmp_path / "run2", clip_name="det_test")

        assert result1.guids == result2.guids

    def test_export_single_frame(self, tmp_path):
        """Single-frame clip exports without error."""
        clip = _make_simple_clip(n_bones=2, n_frames=1)
        exporter = Unity2DAnimExporter(fps=30.0)

        result = exporter.export(clip, tmp_path, clip_name="single_frame")
        assert result.frame_count == 1
        assert Path(result.anim_paths[0]).exists()

    def test_export_custom_controller_name(self, tmp_path):
        """Custom controller name is used in file naming."""
        clip = _make_simple_clip(n_bones=2, n_frames=5)
        exporter = Unity2DAnimExporter(fps=30.0)

        result = exporter.export(
            clip, tmp_path,
            clip_name="walk",
            controller_name="my_controller",
        )

        assert "my_controller" in Path(result.controller_path).name


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Backend Registry Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackendRegistryIntegration:
    """Tests for backend discovery and registry compliance."""

    def test_backend_discoverable(self):
        """Unity2DAnimBackend is discoverable in the registry."""
        from tests.conftest import restore_builtin_backends
        from mathart.core.backend_registry import get_registry
        restore_builtin_backends()
        registry = get_registry()

        entry = registry.get("unity_2d_anim")
        assert entry is not None
        meta, cls = entry
        assert meta.name == "unity_2d_anim"
        assert meta.display_name == "Unity 2D Native Animation Exporter"

    def test_backend_alias_resolution(self):
        """Backend aliases resolve correctly."""
        from tests.conftest import restore_builtin_backends
        from mathart.core.backend_registry import get_registry
        restore_builtin_backends()
        registry = get_registry()

        for alias in ("unity_2d_anim", "unity_native_anim",
                       "unity_2d_animation", "unity_anim_export"):
            entry = registry.get(alias)
            assert entry is not None, f"Alias {alias!r} not found"

    def test_backend_validate_config_with_placeholder(self):
        """validate_config handles CI placeholder strings."""
        from mathart.core.unity_2d_anim_backend import Unity2DAnimBackend
        backend = Unity2DAnimBackend()
        ctx, warnings = backend.validate_config({"clip_2d": "placeholder"})

        assert "clip_2d" in ctx
        assert not isinstance(ctx["clip_2d"], str)
        assert len(warnings) > 0

    def test_backend_validate_config_defaults(self):
        """validate_config sets sensible defaults."""
        from mathart.core.unity_2d_anim_backend import Unity2DAnimBackend
        backend = Unity2DAnimBackend()
        ctx, _ = backend.validate_config({})

        assert "output_dir" in ctx
        assert "clip_2d" in ctx
        assert ctx["loop"] is True
        assert ctx["fps"] == 30.0

    def test_backend_execute_returns_manifest(self, tmp_path):
        """execute() returns a valid ArtifactManifest."""
        from mathart.core.unity_2d_anim_backend import Unity2DAnimBackend
        from mathart.core.artifact_schema import ArtifactManifest

        backend = Unity2DAnimBackend()
        clip = _make_simple_clip(n_bones=3, n_frames=10)

        manifest = backend.execute({
            "clip_2d": clip,
            "output_dir": str(tmp_path),
            "clip_name": "manifest_test",
        })

        assert isinstance(manifest, ArtifactManifest)
        assert manifest.artifact_family == "unity_native_anim"
        assert manifest.backend_type == "unity_2d_anim"

    def test_backend_manifest_metadata_keys(self, tmp_path):
        """Manifest metadata contains all required keys for UNITY_NATIVE_ANIM."""
        from mathart.core.unity_2d_anim_backend import Unity2DAnimBackend
        from mathart.core.artifact_schema import ArtifactFamily

        backend = Unity2DAnimBackend()
        clip = _make_simple_clip(n_bones=3, n_frames=10)

        manifest = backend.execute({
            "clip_2d": clip,
            "output_dir": str(tmp_path),
        })

        required = ArtifactFamily.required_metadata_keys("unity_native_anim")
        for key in required:
            assert key in manifest.metadata, f"Missing required metadata key: {key}"

    def test_backend_manifest_outputs(self, tmp_path):
        """Manifest outputs contain expected file paths."""
        from mathart.core.unity_2d_anim_backend import Unity2DAnimBackend

        backend = Unity2DAnimBackend()
        clip = _make_simple_clip(n_bones=2, n_frames=5)

        manifest = backend.execute({
            "clip_2d": clip,
            "output_dir": str(tmp_path),
        })

        assert "anim_file" in manifest.outputs
        assert "controller_file" in manifest.outputs
        assert Path(manifest.outputs["anim_file"]).exists()
        assert Path(manifest.outputs["controller_file"]).exists()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Performance Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPerformance:
    """Throughput tests for the export pipeline."""

    def test_large_clip_throughput(self, tmp_path):
        """Export of 20 bones × 120 frames completes in < 5 seconds."""
        clip = _make_simple_clip(n_bones=20, n_frames=120)
        exporter = Unity2DAnimExporter(fps=30.0)

        t_start = time.perf_counter()
        result = exporter.export(clip, tmp_path, clip_name="perf_test")
        t_elapsed = time.perf_counter() - t_start

        assert t_elapsed < 5.0, (
            f"Export took {t_elapsed:.2f}s (must be < 5s for 20 bones × 120 frames)"
        )
        assert result.bone_count == 20
        assert result.frame_count == 120

    def test_anim_file_size_reasonable(self, tmp_path):
        """Generated .anim file size is within expected bounds."""
        clip = _make_simple_clip(n_bones=10, n_frames=60)
        exporter = Unity2DAnimExporter(fps=30.0)

        result = exporter.export(clip, tmp_path, clip_name="size_test")

        anim_size = Path(result.anim_paths[0]).stat().st_size
        # Should be non-trivial (> 1KB) but not absurdly large (< 10MB)
        assert anim_size > 1024, f"Anim file too small: {anim_size} bytes"
        assert anim_size < 10 * 1024 * 1024, f"Anim file too large: {anim_size} bytes"
