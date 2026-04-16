"""Tests for SESSION-045 Motion Vector Baker and Neural Rendering Bridge.

Covers:
- Joint displacement computation from FK
- Per-pixel motion field computation with SDF-weighted skinning
- RGB / HSV / raw encoding formats
- Motion vector sequence baking
- EbSynth project export
- Temporal consistency scoring
- Neural rendering evolution bridge (all three layers)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from mathart.animation.skeleton import Skeleton
from mathart.animation.parts import CharacterStyle
from mathart.animation.motion_vector_baker import (
    MotionVectorField,
    MotionVectorSequence,
    compute_joint_displacement,
    compute_pixel_motion_field,
    encode_motion_vector_rgb,
    encode_motion_vector_hsv,
    encode_motion_vector_raw,
    bake_motion_vector_sequence,
    export_ebsynth_project,
    compute_temporal_consistency_score,
)
from mathart.evolution.neural_rendering_bridge import (
    TemporalConsistencyMetrics,
    NeuralRenderingState,
    NeuralRenderingStatus,
    NeuralRenderingEvolutionBridge,
    collect_neural_rendering_status,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_skeleton() -> Skeleton:
    return Skeleton.create_humanoid(3.0)


def _make_style() -> CharacterStyle:
    return CharacterStyle()


def _zero_pose() -> dict[str, float]:
    return {}


def _small_pose() -> dict[str, float]:
    return {
        "l_shoulder": 0.2,
        "r_shoulder": -0.2,
        "l_hip": 0.15,
        "r_hip": -0.15,
    }


def _large_pose() -> dict[str, float]:
    return {
        "l_shoulder": 0.5,
        "r_shoulder": -0.5,
        "l_hip": 0.4,
        "r_hip": -0.4,
        "l_elbow": 0.3,
        "r_elbow": 0.3,
        "l_knee": -0.3,
        "r_knee": -0.3,
    }


def _walk_animation(t: float) -> dict[str, float]:
    """Simple walk cycle for testing."""
    import math
    phase = t * 2 * math.pi
    return {
        "l_hip": 0.3 * math.sin(phase),
        "r_hip": -0.3 * math.sin(phase),
        "l_shoulder": -0.2 * math.sin(phase),
        "r_shoulder": 0.2 * math.sin(phase),
        "l_knee": -0.2 * max(0, -math.sin(phase)),
        "r_knee": -0.2 * max(0, math.sin(phase)),
    }


# ── Joint Displacement Tests ───────────────────────────────────────────────


class TestJointDisplacement:
    def test_zero_displacement(self):
        """Identical poses should produce zero displacement."""
        skel = _make_skeleton()
        pose = _zero_pose()
        disp = compute_joint_displacement(skel, pose, pose)
        for name, (dx, dy) in disp.items():
            assert abs(dx) < 1e-10, f"Joint {name} has non-zero dx={dx}"
            assert abs(dy) < 1e-10, f"Joint {name} has non-zero dy={dy}"

    def test_nonzero_displacement(self):
        """Different poses should produce non-zero displacement."""
        skel = _make_skeleton()
        disp = compute_joint_displacement(skel, _zero_pose(), _small_pose())
        total_disp = sum(abs(dx) + abs(dy) for dx, dy in disp.values())
        assert total_disp > 0.0, "Expected non-zero total displacement"

    def test_all_joints_present(self):
        """All skeleton joints should have displacement entries."""
        skel = _make_skeleton()
        disp = compute_joint_displacement(skel, _zero_pose(), _small_pose())
        for joint_name in skel.joints:
            assert joint_name in disp, f"Missing joint {joint_name} in displacement"

    def test_symmetry(self):
        """Displacement A→B should be negative of B→A."""
        skel = _make_skeleton()
        disp_ab = compute_joint_displacement(skel, _zero_pose(), _small_pose())
        disp_ba = compute_joint_displacement(skel, _small_pose(), _zero_pose())
        for name in disp_ab:
            dx_ab, dy_ab = disp_ab[name]
            dx_ba, dy_ba = disp_ba[name]
            assert abs(dx_ab + dx_ba) < 1e-6, f"Joint {name} dx not symmetric"
            assert abs(dy_ab + dy_ba) < 1e-6, f"Joint {name} dy not symmetric"


# ── Pixel Motion Field Tests ───────────────────────────────────────────────


class TestPixelMotionField:
    def test_zero_motion(self):
        """Identical poses should produce near-zero motion field."""
        skel = _make_skeleton()
        style = _make_style()
        mv = compute_pixel_motion_field(skel, _zero_pose(), _zero_pose(), style)
        assert mv.max_magnitude < 1e-6, "Expected near-zero motion for identical poses"

    def test_nonzero_motion(self):
        """Different poses should produce non-zero motion field."""
        skel = _make_skeleton()
        style = _make_style()
        mv = compute_pixel_motion_field(skel, _zero_pose(), _large_pose(), style)
        assert mv.max_magnitude > 0.0, "Expected non-zero motion for different poses"

    def test_field_dimensions(self):
        """Motion field should match requested dimensions."""
        skel = _make_skeleton()
        style = _make_style()
        mv = compute_pixel_motion_field(skel, _zero_pose(), _small_pose(), style, width=64, height=64)
        assert mv.width == 64
        assert mv.height == 64
        assert mv.dx.shape == (64, 64)
        assert mv.dy.shape == (64, 64)
        assert mv.magnitude.shape == (64, 64)
        assert mv.mask.shape == (64, 64)

    def test_mask_has_valid_pixels(self):
        """Character mask should contain some valid pixels."""
        skel = _make_skeleton()
        style = _make_style()
        mv = compute_pixel_motion_field(skel, _zero_pose(), _small_pose(), style)
        assert mv.valid_pixel_count > 0, "Expected some valid pixels in mask"

    def test_motion_outside_mask_is_zero(self):
        """Motion vectors outside the character mask should be zero."""
        skel = _make_skeleton()
        style = _make_style()
        mv = compute_pixel_motion_field(skel, _zero_pose(), _large_pose(), style)
        outside = ~mv.mask
        assert np.all(mv.dx[outside] == 0.0), "dx should be zero outside mask"
        assert np.all(mv.dy[outside] == 0.0), "dy should be zero outside mask"

    def test_metadata_populated(self):
        """Metadata should contain expected keys."""
        skel = _make_skeleton()
        style = _make_style()
        mv = compute_pixel_motion_field(skel, _zero_pose(), _small_pose(), style)
        assert "skinning_sigma" in mv.metadata
        assert "max_magnitude_px" in mv.metadata
        assert "valid_pixels" in mv.metadata
        assert "joint_count" in mv.metadata

    def test_skinning_sigma_effect(self):
        """Different skinning sigmas should produce different motion fields."""
        skel = _make_skeleton()
        style = _make_style()
        mv_small = compute_pixel_motion_field(
            skel, _zero_pose(), _large_pose(), style, skinning_sigma=0.05
        )
        mv_large = compute_pixel_motion_field(
            skel, _zero_pose(), _large_pose(), style, skinning_sigma=0.5
        )
        # Different sigma should produce measurably different fields
        diff = np.abs(mv_small.dx - mv_large.dx)
        assert np.max(diff) > 0.01, "Different sigmas should produce different fields"


# ── Encoding Tests ─────────────────────────────────────────────────────────


class TestEncoding:
    def _make_mv_field(self) -> MotionVectorField:
        skel = _make_skeleton()
        style = _make_style()
        return compute_pixel_motion_field(skel, _zero_pose(), _large_pose(), style)

    def test_rgb_encoding_shape(self):
        """RGB encoding should produce RGBA image with correct dimensions."""
        mv = self._make_mv_field()
        img = encode_motion_vector_rgb(mv)
        assert img.mode == "RGBA"
        assert img.size == (mv.width, mv.height)

    def test_rgb_neutral_is_128(self):
        """Zero displacement should encode as ~128 in RGB."""
        mv = MotionVectorField(
            dx=np.zeros((8, 8)),
            dy=np.zeros((8, 8)),
            magnitude=np.zeros((8, 8)),
            mask=np.ones((8, 8), dtype=bool),
            width=8,
            height=8,
        )
        img = encode_motion_vector_rgb(mv, max_displacement=1.0)
        arr = np.array(img)
        # R and G channels should be ~128 for zero displacement
        assert abs(int(arr[4, 4, 0]) - 128) <= 1, "R channel should be ~128 for zero dx"
        assert abs(int(arr[4, 4, 1]) - 128) <= 1, "G channel should be ~128 for zero dy"

    def test_hsv_encoding_shape(self):
        """HSV encoding should produce RGBA image with correct dimensions."""
        mv = self._make_mv_field()
        img = encode_motion_vector_hsv(mv)
        assert img.mode == "RGBA"
        assert img.size == (mv.width, mv.height)

    def test_raw_encoding_shape(self):
        """Raw encoding should produce float32 array with 3 channels."""
        mv = self._make_mv_field()
        raw = encode_motion_vector_raw(mv)
        assert raw.dtype == np.float32
        assert raw.shape == (mv.height, mv.width, 3)

    def test_raw_encoding_channels(self):
        """Raw encoding channels should match dx, dy, mask."""
        mv = self._make_mv_field()
        raw = encode_motion_vector_raw(mv)
        np.testing.assert_array_almost_equal(raw[:, :, 0], mv.dx.astype(np.float32))
        np.testing.assert_array_almost_equal(raw[:, :, 1], mv.dy.astype(np.float32))
        np.testing.assert_array_equal(raw[:, :, 2], mv.mask.astype(np.float32))

    def test_rgb_alpha_matches_mask(self):
        """RGB alpha channel should match the motion field mask."""
        mv = self._make_mv_field()
        img = encode_motion_vector_rgb(mv)
        arr = np.array(img)
        expected_alpha = np.where(mv.mask, 255, 0).astype(np.uint8)
        np.testing.assert_array_equal(arr[:, :, 3], expected_alpha)


# ── Sequence Baking Tests ──────────────────────────────────────────────────


class TestSequenceBaking:
    def test_bake_sequence(self):
        """Baking a sequence should produce N-1 motion vector fields."""
        skel = _make_skeleton()
        style = _make_style()
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=8)
        assert seq.frame_count == 8
        assert len(seq.fields) == 7  # N-1 fields

    def test_sequence_dimensions(self):
        """Sequence fields should have consistent dimensions."""
        skel = _make_skeleton()
        style = _make_style()
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=4, width=16, height=16)
        for f in seq.fields:
            assert f.width == 16
            assert f.height == 16

    def test_sequence_has_motion(self):
        """Walk animation sequence should have non-zero total motion energy."""
        skel = _make_skeleton()
        style = _make_style()
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=8)
        assert seq.total_motion_energy > 0.0

    def test_sequence_metadata(self):
        """Each field in the sequence should have frame indices in metadata."""
        skel = _make_skeleton()
        style = _make_style()
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=4)
        for i, f in enumerate(seq.fields):
            assert f.metadata["frame_a"] == i
            assert f.metadata["frame_b"] == i + 1


# ── EbSynth Export Tests ───────────────────────────────────────────────────


class TestEbSynthExport:
    def test_export_creates_directory_structure(self):
        """EbSynth export should create the expected directory structure."""
        skel = _make_skeleton()
        style = _make_style()
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=4)

        from mathart.animation.industrial_renderer import render_character_frame_industrial
        frames = []
        for i in range(4):
            pose = _walk_animation(i / 3)
            fresh_skel = Skeleton.create_humanoid(3.0)
            img = render_character_frame_industrial(fresh_skel, pose, style)
            frames.append(img)

        with tempfile.TemporaryDirectory() as tmpdir:
            meta = export_ebsynth_project(seq, frames, tmpdir)
            out = Path(tmpdir)
            assert (out / "frames").is_dir()
            assert (out / "flow").is_dir()
            assert (out / "flow_vis").is_dir()
            assert (out / "keyframes").is_dir()
            assert (out / "project.json").is_file()

    def test_export_frame_count(self):
        """Export should produce correct number of frame and flow files."""
        skel = _make_skeleton()
        style = _make_style()
        n_frames = 4
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=n_frames)

        from mathart.animation.industrial_renderer import render_character_frame_industrial
        frames = []
        for i in range(n_frames):
            pose = _walk_animation(i / max(n_frames - 1, 1))
            fresh_skel = Skeleton.create_humanoid(3.0)
            img = render_character_frame_industrial(fresh_skel, pose, style)
            frames.append(img)

        with tempfile.TemporaryDirectory() as tmpdir:
            meta = export_ebsynth_project(seq, frames, tmpdir)
            out = Path(tmpdir)
            assert len(list((out / "frames").glob("*.png"))) == n_frames
            assert len(list((out / "flow").glob("*.png"))) == n_frames - 1
            assert len(list((out / "keyframes").glob("*.png"))) == 1  # Default: first frame

    def test_export_project_json(self):
        """Project JSON should contain expected metadata."""
        skel = _make_skeleton()
        style = _make_style()
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=4)

        from mathart.animation.industrial_renderer import render_character_frame_industrial
        frames = []
        for i in range(4):
            pose = _walk_animation(i / 3)
            fresh_skel = Skeleton.create_humanoid(3.0)
            img = render_character_frame_industrial(fresh_skel, pose, style)
            frames.append(img)

        with tempfile.TemporaryDirectory() as tmpdir:
            export_ebsynth_project(seq, frames, tmpdir)
            meta = json.loads((Path(tmpdir) / "project.json").read_text())
            assert meta["format"] == "ebsynth_project"
            assert meta["frame_count"] == 4
            assert "max_displacement" in meta
            assert "total_motion_energy" in meta


# ── Temporal Consistency Tests ─────────────────────────────────────────────


class TestTemporalConsistency:
    def test_identical_frames_zero_error(self):
        """Identical frames with zero motion should have zero warp error."""
        frame = np.random.randint(0, 255, (32, 32, 4), dtype=np.uint8)
        mv = MotionVectorField(
            dx=np.zeros((32, 32)),
            dy=np.zeros((32, 32)),
            magnitude=np.zeros((32, 32)),
            mask=np.ones((32, 32), dtype=bool),
            width=32,
            height=32,
        )
        scores = compute_temporal_consistency_score(frame, frame, mv)
        assert scores["warp_error"] < 1e-6
        assert scores["warp_ssim_proxy"] > 0.999

    def test_different_frames_nonzero_error(self):
        """Different frames should produce non-zero warp error."""
        frame_a = np.zeros((32, 32, 4), dtype=np.uint8)
        frame_a[:, :, 3] = 255
        frame_b = np.full((32, 32, 4), 128, dtype=np.uint8)
        frame_b[:, :, 3] = 255
        mv = MotionVectorField(
            dx=np.zeros((32, 32)),
            dy=np.zeros((32, 32)),
            magnitude=np.zeros((32, 32)),
            mask=np.ones((32, 32), dtype=bool),
            width=32,
            height=32,
        )
        scores = compute_temporal_consistency_score(frame_a, frame_b, mv)
        assert scores["warp_error"] > 0.0

    def test_coverage_metric(self):
        """Coverage should reflect the fraction of valid pixels."""
        mv = MotionVectorField(
            dx=np.zeros((32, 32)),
            dy=np.zeros((32, 32)),
            magnitude=np.zeros((32, 32)),
            mask=np.zeros((32, 32), dtype=bool),
            width=32,
            height=32,
        )
        # Half the pixels are valid
        mv.mask[:16, :] = True
        frame = np.random.randint(0, 255, (32, 32, 4), dtype=np.uint8)
        scores = compute_temporal_consistency_score(frame, frame, mv)
        assert abs(scores["coverage"] - 0.5) < 0.01


# ── Neural Rendering Bridge Tests ──────────────────────────────────────────


class TestNeuralRenderingBridge:
    def test_bridge_initialization(self):
        """Bridge should initialize with default state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            assert bridge.state.total_evaluation_cycles == 0
            assert bridge.state.total_passes == 0

    def test_evaluate_without_data(self):
        """Evaluation without data should fail gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            metrics = bridge.evaluate_temporal_consistency()
            assert not metrics.temporal_pass
            assert bridge.state.total_failures == 1

    def test_evaluate_with_data(self):
        """Evaluation with valid data should produce metrics."""
        skel = _make_skeleton()
        style = _make_style()
        n_frames = 4
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=n_frames)

        from mathart.animation.industrial_renderer import render_character_frame_industrial
        frames = []
        for i in range(n_frames):
            pose = _walk_animation(i / max(n_frames - 1, 1))
            fresh_skel = Skeleton.create_humanoid(3.0)
            img = render_character_frame_industrial(fresh_skel, pose, style)
            frames.append(np.array(img))

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            metrics = bridge.evaluate_temporal_consistency(
                rendered_frames=frames,
                mv_sequence=seq,
                warp_error_threshold=1.0,  # Generous threshold for test
            )
            assert metrics.frame_count == n_frames
            assert len(metrics.per_frame_errors) == n_frames - 1
            assert metrics.mean_warp_error >= 0.0

    def test_state_persistence(self):
        """Bridge state should persist across instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge1 = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            bridge1.evaluate_temporal_consistency()  # Fail (no data)
            assert bridge1.state.total_failures == 1

            bridge2 = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            assert bridge2.state.total_failures == 1

    def test_knowledge_distillation_on_failure(self):
        """Failed evaluation should produce knowledge rules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            metrics = TemporalConsistencyMetrics(
                cycle_id=1,
                mean_warp_error=0.25,
                warp_error_threshold=0.15,
                temporal_pass=False,
                flicker_score=0.08,
            )
            rules = bridge.distill_temporal_knowledge(metrics)
            assert len(rules) >= 1
            assert any(r["rule_type"] == "enforcement" for r in rules)

    def test_fitness_bonus_pass(self):
        """Passing evaluation should produce positive fitness bonus."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            metrics = TemporalConsistencyMetrics(
                temporal_pass=True,
                mean_warp_error=0.05,
                warp_error_threshold=0.15,
                flicker_score=0.01,
                coverage=0.5,
            )
            bonus = bridge.compute_temporal_fitness_bonus(metrics)
            assert bonus > 0.0

    def test_fitness_penalty_fail(self):
        """Failing evaluation should produce negative fitness bonus."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            metrics = TemporalConsistencyMetrics(
                temporal_pass=False,
                mean_warp_error=0.5,
                warp_error_threshold=0.15,
                flicker_score=0.2,
                coverage=0.5,
            )
            bonus = bridge.compute_temporal_fitness_bonus(metrics)
            assert bonus < 0.0

    def test_status_report(self):
        """Status report should be a non-empty string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            report = bridge.status_report()
            assert "Neural Rendering" in report
            assert "Gap C3" in report

    def test_collect_status(self):
        """collect_neural_rendering_status should return a valid status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = collect_neural_rendering_status(root)
            assert isinstance(status, NeuralRenderingStatus)
            assert not status.motion_vector_module_exists  # Not in tmpdir


# ── Integration Test ───────────────────────────────────────────────────────


class TestEndToEndIntegration:
    def test_full_pipeline(self):
        """Full pipeline: animate → bake MV → encode → validate → score."""
        skel = _make_skeleton()
        style = _make_style()
        n_frames = 6

        # Step 1: Bake motion vector sequence
        seq = bake_motion_vector_sequence(skel, _walk_animation, style, frames=n_frames)
        assert len(seq.fields) == n_frames - 1

        # Step 2: Render frames
        from mathart.animation.industrial_renderer import render_character_frame_industrial
        frames = []
        for i in range(n_frames):
            pose = _walk_animation(i / max(n_frames - 1, 1))
            fresh_skel = Skeleton.create_humanoid(3.0)
            img = render_character_frame_industrial(fresh_skel, pose, style)
            frames.append(img)

        # Step 3: Encode motion vectors in all formats
        for mv_field in seq.fields:
            rgb = encode_motion_vector_rgb(mv_field)
            hsv = encode_motion_vector_hsv(mv_field)
            raw = encode_motion_vector_raw(mv_field)
            assert rgb.mode == "RGBA"
            assert hsv.mode == "RGBA"
            assert raw.shape[2] == 3

        # Step 4: Validate temporal consistency
        for i in range(len(seq.fields)):
            scores = compute_temporal_consistency_score(
                np.array(frames[i]),
                np.array(frames[i + 1]),
                seq.fields[i],
            )
            assert "warp_error" in scores
            assert "warp_ssim_proxy" in scores

        # Step 5: Export EbSynth project
        with tempfile.TemporaryDirectory() as tmpdir:
            meta = export_ebsynth_project(seq, frames, tmpdir)
            assert meta["frame_count"] == n_frames
            assert Path(tmpdir, "project.json").exists()

        # Step 6: Run through neural rendering bridge
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(Path(tmpdir), verbose=False)
            np_frames = [np.array(f) for f in frames]
            metrics = bridge.evaluate_temporal_consistency(
                rendered_frames=np_frames,
                mv_sequence=seq,
                warp_error_threshold=1.0,
            )
            assert metrics.frame_count == n_frames
            rules = bridge.distill_temporal_knowledge(metrics)
            bonus = bridge.compute_temporal_fitness_bonus(metrics)
            assert isinstance(bonus, float)
