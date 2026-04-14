"""Tests for the upgraded character rendering system."""
import numpy as np
import pytest
from PIL import Image
from mathart.animation.skeleton import Skeleton
from mathart.animation.parts import (
    CharacterStyle, BodyPart, assemble_character,
    head_sdf, torso_sdf, limb_sdf, hat_sdf, eye_sdf, foot_sdf, hand_sdf,
)
from mathart.animation.character_renderer import (
    render_character_frame, render_character_sheet,
)
from mathart.animation.character_presets import (
    mario_style, mario_palette, trickster_style, trickster_palette,
    simple_enemy_style, simple_enemy_palette,
    flying_enemy_style, flying_enemy_palette,
    bouncing_enemy_style, bouncing_enemy_palette,
    get_preset, CHARACTER_PRESETS,
)
from mathart.animation.presets import idle_animation, run_animation, jump_animation


class TestBodyParts:
    """Test individual body part SDFs."""

    @pytest.mark.unit
    def test_head_center_inside(self):
        style = CharacterStyle()
        sdf = head_sdf(style)
        x = np.array([0.0])
        y = np.array([0.0])
        assert sdf(x, y)[0] < 0, "Center of head should be inside"

    @pytest.mark.unit
    def test_head_outside(self):
        style = CharacterStyle()
        sdf = head_sdf(style)
        x = np.array([1.0])
        y = np.array([0.0])
        assert sdf(x, y)[0] > 0, "Far point should be outside head"

    @pytest.mark.unit
    def test_torso_center_inside(self):
        style = CharacterStyle()
        sdf = torso_sdf(style)
        x = np.array([0.0])
        y = np.array([0.0])
        assert sdf(x, y)[0] < 0

    @pytest.mark.unit
    def test_limb_center_inside(self):
        sdf = limb_sdf(0.1, 0.5)
        x = np.array([0.0])
        y = np.array([-0.25])
        assert sdf(x, y)[0] < 0

    @pytest.mark.unit
    def test_hat_cap(self):
        style = CharacterStyle(has_hat=True, hat_style="cap")
        sdf = hat_sdf(style)
        assert sdf is not None

    @pytest.mark.unit
    def test_hat_top(self):
        style = CharacterStyle(has_hat=True, hat_style="top")
        sdf = hat_sdf(style)
        assert sdf is not None

    @pytest.mark.unit
    def test_hat_none(self):
        style = CharacterStyle(has_hat=False)
        sdf = hat_sdf(style)
        assert sdf is None

    @pytest.mark.unit
    def test_eye_dot(self):
        style = CharacterStyle(eye_style="dot")
        sdf = eye_sdf(style, side=1.0)
        x = np.array([style.head_radius * 0.25])
        y = np.array([style.head_radius * 0.05])
        assert sdf(x, y)[0] < 0

    @pytest.mark.unit
    def test_foot_center(self):
        style = CharacterStyle()
        sdf = foot_sdf(style)
        x = np.array([0.0])
        y = np.array([0.0])
        assert sdf(x, y)[0] < 0


class TestAssembly:
    """Test character assembly."""

    @pytest.mark.unit
    def test_assemble_default(self):
        style = CharacterStyle()
        parts = assemble_character(style)
        assert len(parts) > 10, "Should have many body parts"
        names = [p.name for p in parts]
        assert "head" in names
        assert "torso" in names
        assert "l_thigh" in names

    @pytest.mark.unit
    def test_assemble_with_hat(self):
        style = CharacterStyle(has_hat=True, hat_style="cap")
        parts = assemble_character(style)
        names = [p.name for p in parts]
        assert "hat" in names

    @pytest.mark.unit
    def test_assemble_with_mustache(self):
        style = CharacterStyle(has_mustache=True)
        parts = assemble_character(style)
        names = [p.name for p in parts]
        assert "mustache" in names

    @pytest.mark.unit
    def test_z_order_sorted(self):
        style = CharacterStyle()
        parts = assemble_character(style)
        z_orders = [p.z_order for p in parts]
        assert z_orders == sorted(z_orders), "Parts should be sorted by z_order"


class TestCharacterRenderer:
    """Test the high-fidelity character renderer."""

    @pytest.mark.unit
    def test_render_frame_basic(self):
        skel = Skeleton.create_humanoid()
        style = CharacterStyle()
        pose = idle_animation(0.0)
        img = render_character_frame(skel, pose, style, 32, 32)
        assert img.mode == "RGBA"
        assert img.size == (32, 32)

    @pytest.mark.unit
    def test_render_frame_has_pixels(self):
        skel = Skeleton.create_humanoid()
        style = CharacterStyle()
        pose = idle_animation(0.0)
        img = render_character_frame(skel, pose, style, 32, 32)
        arr = np.array(img)
        # Should have some non-transparent pixels
        assert np.sum(arr[:, :, 3] > 0) > 20, "Should have visible pixels"

    @pytest.mark.unit
    def test_render_frame_with_palette(self):
        skel = Skeleton.create_humanoid()
        style, palette = get_preset("mario")
        pose = idle_animation(0.0)
        img = render_character_frame(skel, pose, style, 32, 32, palette)
        assert img.mode == "RGBA"

    @pytest.mark.unit
    def test_render_frame_no_lighting(self):
        skel = Skeleton.create_humanoid()
        style = CharacterStyle()
        pose = idle_animation(0.0)
        img = render_character_frame(skel, pose, style, 32, 32, enable_lighting=False)
        assert img.mode == "RGBA"

    @pytest.mark.unit
    def test_render_frame_no_outline(self):
        skel = Skeleton.create_humanoid()
        style = CharacterStyle()
        pose = idle_animation(0.0)
        img = render_character_frame(skel, pose, style, 32, 32, enable_outline=False)
        assert img.mode == "RGBA"

    @pytest.mark.unit
    def test_render_frame_no_dither(self):
        skel = Skeleton.create_humanoid()
        style = CharacterStyle()
        pose = idle_animation(0.0)
        img = render_character_frame(skel, pose, style, 32, 32, enable_dither=False)
        assert img.mode == "RGBA"

    @pytest.mark.unit
    def test_render_sheet(self):
        skel = Skeleton.create_humanoid()
        style, palette = get_preset("mario")
        sheet = render_character_sheet(skel, idle_animation, style, 8, 32, 32, palette)
        assert sheet.size == (256, 32)
        assert sheet.mode == "RGBA"

    @pytest.mark.unit
    def test_render_sheet_run(self):
        skel = Skeleton.create_humanoid()
        style, palette = get_preset("mario")
        sheet = render_character_sheet(skel, run_animation, style, 8, 32, 32, palette)
        assert sheet.size == (256, 32)

    @pytest.mark.unit
    def test_render_sheet_jump(self):
        skel = Skeleton.create_humanoid()
        style, palette = get_preset("mario")
        sheet = render_character_sheet(skel, jump_animation, style, 8, 32, 32, palette)
        assert sheet.size == (256, 32)


class TestPresets:
    """Test all character presets."""

    @pytest.mark.unit
    def test_all_presets_exist(self):
        assert len(CHARACTER_PRESETS) >= 5

    @pytest.mark.unit
    def test_get_preset_mario(self):
        style, palette = get_preset("mario")
        assert style.has_hat
        assert style.has_mustache
        assert palette.count >= 6

    @pytest.mark.unit
    def test_get_preset_trickster(self):
        style, palette = get_preset("trickster")
        assert style.has_hat
        assert style.hat_style == "top"
        assert not style.has_mustache

    @pytest.mark.unit
    def test_get_preset_invalid(self):
        with pytest.raises(ValueError):
            get_preset("nonexistent")

    @pytest.mark.unit
    def test_all_presets_renderable(self):
        """Every preset should produce a valid 32x32 RGBA frame."""
        skel = Skeleton.create_humanoid()
        pose = idle_animation(0.0)
        for name in CHARACTER_PRESETS:
            style, palette = get_preset(name)
            img = render_character_frame(skel, pose, style, 32, 32, palette)
            assert img.size == (32, 32), f"Failed for {name}"
            assert img.mode == "RGBA", f"Failed for {name}"
            arr = np.array(img)
            assert np.sum(arr[:, :, 3] > 0) > 10, f"No visible pixels for {name}"

    @pytest.mark.unit
    def test_all_presets_sheet(self):
        """Every preset should produce a valid sprite sheet."""
        for name in CHARACTER_PRESETS:
            skel = Skeleton.create_humanoid()
            style, palette = get_preset(name)
            sheet = render_character_sheet(skel, run_animation, style, 4, 32, 32, palette)
            assert sheet.size == (128, 32), f"Failed for {name}"


@pytest.mark.integration
class TestCharacterIntegration:
    """End-to-end character pipeline tests."""

    def test_mario_full_pipeline(self, tmp_path):
        """Generate Mario idle sheet → export with metadata."""
        from mathart.export.bridge import AssetExporter, ExportConfig

        skel = Skeleton.create_humanoid()
        style, palette = get_preset("mario")
        sheet = render_character_sheet(skel, idle_animation, style, 8, 32, 32, palette)

        config = ExportConfig(output_dir=str(tmp_path))
        exporter = AssetExporter(config)
        path = exporter.export_spritesheet(sheet, "mario_idle_hq", "Characters", 8)

        assert path.exists()
        loaded = Image.open(path)
        assert loaded.size == (256, 32)
        assert loaded.mode == "RGBA"

    def test_all_animations_all_presets(self, tmp_path):
        """Every preset × every animation should produce valid output."""
        from mathart.animation.presets import (
            idle_animation, run_animation, jump_animation,
            fall_animation, hit_animation,
        )
        animations = {
            "idle": idle_animation,
            "run": run_animation,
            "jump": jump_animation,
            "fall": fall_animation,
            "hit": hit_animation,
        }
        for char_name in CHARACTER_PRESETS:
            skel = Skeleton.create_humanoid()
            style, palette = get_preset(char_name)
            for anim_name, anim_func in animations.items():
                sheet = render_character_sheet(
                    skel, anim_func, style, 4, 32, 32, palette
                )
                assert sheet.size == (128, 32), f"{char_name}/{anim_name} failed"
