"""Tests for animation module."""
import numpy as np
import pytest
from mathart.animation.skeleton import Skeleton, Joint
from mathart.animation.curves import ease_in_out, spring, sine_wave, squash_stretch, bounce
from mathart.animation.presets import idle_animation, run_animation, jump_animation, fall_animation, hit_animation
from mathart.animation.renderer import render_skeleton_frame, render_skeleton_sheet
from mathart.oklab.palette import PaletteGenerator


class TestSkeleton:
    @pytest.mark.unit
    def test_create_humanoid(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        assert "root" in skel.joints
        assert "head" in skel.joints
        assert "l_hand" in skel.joints
        assert "r_foot" in skel.joints
        assert len(skel.bones) == 12

    @pytest.mark.unit
    def test_joint_rom_clamping(self):
        """Elbow should only flex forward (distilled from 松岡)."""
        skel = Skeleton.create_humanoid()
        # Try to set elbow to backward angle
        skel.joints["l_elbow"].angle = -1.0
        skel.joints["l_elbow"].clamp_angle()
        assert skel.joints["l_elbow"].angle >= 0, "Elbow should not flex backward"

    @pytest.mark.unit
    def test_knee_rom_clamping(self):
        """Knee should only flex backward (distilled from 松岡)."""
        skel = Skeleton.create_humanoid()
        skel.joints["l_knee"].angle = 0.5
        skel.joints["l_knee"].clamp_angle()
        assert skel.joints["l_knee"].angle <= 0, "Knee should not flex forward"

    @pytest.mark.unit
    def test_apply_pose(self):
        skel = Skeleton.create_humanoid()
        pose = {"spine": 0.1, "head": -0.1}
        skel.apply_pose(pose)
        assert skel.joints["spine"].angle == 0.1
        assert skel.joints["head"].angle == -0.1

    @pytest.mark.unit
    def test_json_roundtrip(self, tmp_path):
        skel = Skeleton.create_humanoid()
        path = str(tmp_path / "skel.json")
        skel.save_json(path)
        import json
        with open(path) as f:
            data = json.load(f)
        assert data["head_units"] == 3.0
        assert "root" in data["joints"]


class TestCurves:
    @pytest.mark.unit
    def test_ease_in_out_boundaries(self):
        assert abs(ease_in_out(0.0)) < 1e-6
        assert abs(ease_in_out(1.0) - 1.0) < 1e-6

    @pytest.mark.unit
    def test_ease_in_out_midpoint(self):
        assert abs(ease_in_out(0.5) - 0.5) < 1e-6

    @pytest.mark.unit
    def test_spring_converges(self):
        """Spring should converge to 1.0."""
        val = spring(5.0, stiffness=10, damping=3)
        assert abs(val - 1.0) < 0.05

    @pytest.mark.unit
    def test_sine_wave_period(self):
        t = np.linspace(0, 1, 100)
        vals = sine_wave(t, frequency=1.0, amplitude=1.0)
        # Should complete one full cycle
        assert abs(vals[0]) < 0.1
        assert abs(vals[-1]) < 0.1

    @pytest.mark.unit
    def test_squash_stretch_volume(self):
        """Volume conservation: sx * sy ≈ 1.0."""
        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
            sx, sy = squash_stretch(t)
            assert abs(sx * sy - 1.0) < 0.15, f"Volume not conserved at t={t}"


class TestPresets:
    @pytest.mark.unit
    def test_all_presets_return_dict(self):
        for anim_func in [idle_animation, run_animation, jump_animation,
                          fall_animation, hit_animation]:
            for t in [0.0, 0.25, 0.5, 0.75, 0.99]:
                pose = anim_func(t)
                assert isinstance(pose, dict)
                assert len(pose) > 0

    @pytest.mark.unit
    def test_run_cycle_alternates(self):
        """Left and right legs should be opposite in run cycle."""
        pose_0 = run_animation(0.0)
        pose_half = run_animation(0.5)
        if "l_hip" in pose_0 and "r_hip" in pose_0:
            # At t=0 and t=0.5, legs should be roughly opposite
            assert pose_0["l_hip"] * pose_half["l_hip"] < 0.01


class TestRenderer:
    @pytest.mark.unit
    def test_render_frame(self):
        skel = Skeleton.create_humanoid()
        pose = idle_animation(0.0)
        img = render_skeleton_frame(skel, pose, 32, 32)
        assert img.mode == "RGBA"
        assert img.size == (32, 32)

    @pytest.mark.unit
    def test_render_sheet(self):
        skel = Skeleton.create_humanoid()
        sheet = render_skeleton_sheet(skel, idle_animation, 8, 32, 32)
        assert sheet.size == (256, 32)
        assert sheet.mode == "RGBA"

    @pytest.mark.unit
    def test_render_with_palette(self):
        gen = PaletteGenerator(seed=42)
        pal = gen.generate("warm_cool_shadow", count=6)
        skel = Skeleton.create_humanoid()
        sheet = render_skeleton_sheet(skel, run_animation, 8, 32, 32, pal)
        assert sheet.size == (256, 32)
