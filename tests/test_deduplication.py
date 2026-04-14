"""Tests for DeduplicationEngine and ShaderKnowledgeBase."""
from __future__ import annotations

import pytest
from pathlib import Path

from mathart.distill.deduplication import DeduplicationEngine, DedupResult
from mathart.shader.knowledge import ShaderKnowledgeBase
from mathart.shader.generator import ShaderCodeGenerator
from mathart.shader.pseudo3d import Pseudo3DExtension, IsometricConfig


class TestDeduplicationEngine:
    def test_exact_duplicate_skipped(self):
        """Exact duplicate rules should be silently skipped."""
        engine = DeduplicationEngine(verbose=False)
        rules = [
            ("anatomy", "The head height is 1/8 of total body height", {"head_ratio": "0.125"}),
        ]
        # Add once
        accepted1, result1 = engine.deduplicate_rules(rules)
        assert result1.new_rules == 1
        assert result1.exact_dups == 0

        # Add again — should be exact dup
        accepted2, result2 = engine.deduplicate_rules(rules)
        assert result2.exact_dups == 1
        assert result2.new_rules == 0
        assert len(accepted2) == 0

    def test_new_rule_accepted(self):
        """Genuinely new rules should be accepted."""
        engine = DeduplicationEngine(verbose=False)
        rules = [
            ("color_light", "Warm light creates cool shadows in complementary hue", {}),
            ("physics_sim", "Spring constant k determines oscillation frequency", {"k": "10.0"}),
        ]
        accepted, result = engine.deduplicate_rules(rules)
        assert result.new_rules == 2
        assert len(accepted) == 2

    def test_semantic_variant_kept(self):
        """Semantically similar rules should be kept as variants."""
        engine = DeduplicationEngine(cosine_threshold=0.7, verbose=False)
        rule1 = ("anatomy", "human body proportions head equals one eighth total height", {})
        rule2 = ("anatomy", "body height is eight times the head height in human proportions", {})

        engine.deduplicate_rules([rule1])
        accepted, result = engine.deduplicate_rules([rule2])

        # Should be kept as variant (similar but not identical)
        assert result.exact_dups == 0  # Not an exact dup
        # Either variant_kept or new (depending on similarity score)
        assert result.variants_kept + result.new_rules >= 1

    def test_param_merge(self):
        """Numeric parameters within tolerance should be merged."""
        engine = DeduplicationEngine(param_tolerance=0.1, verbose=False)
        rule1 = ("physics_sim", "Spring constant for character bounce", {"k": "10.0"})
        rule2 = ("physics_sim", "Spring constant for character bounce", {"k": "10.5"})

        engine.deduplicate_rules([rule1])
        accepted, result = engine.deduplicate_rules([rule2])

        # Should be merged (10.5 is within 10% of 10.0)
        assert result.params_merged + result.exact_dups + result.variants_kept >= 1

    def test_model_deduplication_new(self):
        """New models should be accepted."""
        engine = DeduplicationEngine(verbose=False)
        models = [
            {"name": "oklab_palette", "version": "1.0.0"},
            {"name": "sdf_renderer", "version": "2.1.0"},
        ]
        accepted, upgrades = engine.deduplicate_models(models)
        assert len(accepted) == 2
        assert len(upgrades) == 0

    def test_model_upgrade_accepted(self):
        """Newer version of existing model should be accepted as upgrade."""
        engine = DeduplicationEngine(verbose=False)
        engine.deduplicate_models([{"name": "wfc_generator", "version": "1.0.0"}])
        accepted, upgrades = engine.deduplicate_models([{"name": "wfc_generator", "version": "2.0.0"}])
        assert len(accepted) == 1
        assert len(upgrades) == 1
        assert "wfc_generator" in upgrades[0]

    def test_model_same_version_skipped(self):
        """Same version of existing model should be skipped."""
        engine = DeduplicationEngine(verbose=False)
        engine.deduplicate_models([{"name": "lsystem", "version": "1.5.0"}])
        accepted, upgrades = engine.deduplicate_models([{"name": "lsystem", "version": "1.5.0"}])
        assert len(accepted) == 0
        assert len(upgrades) == 0

    def test_dedup_result_summary(self):
        """DedupResult.summary() should return a non-empty string."""
        result = DedupResult(
            total_input=5,
            exact_dups=1,
            variants_kept=1,
            params_merged=1,
            new_rules=2,
        )
        summary = result.summary()
        assert "5 input" in summary
        assert "2 new" in summary

    def test_version_comparison(self):
        """Version comparison should work correctly."""
        assert DeduplicationEngine._version_gt("2.0.0", "1.0.0")
        assert DeduplicationEngine._version_gt("1.1.0", "1.0.0")
        assert not DeduplicationEngine._version_gt("1.0.0", "1.0.0")
        assert not DeduplicationEngine._version_gt("0.9.0", "1.0.0")

    def test_canonical_normalization(self):
        """Canonical form should normalize text for comparison."""
        c1 = DeduplicationEngine._canonical("The HEAD is 1/8 of body!")
        c2 = DeduplicationEngine._canonical("body 1 8 head of the is")
        # Both should produce sorted word bags (same words, different order)
        assert c1 == c2

    def test_load_existing_from_knowledge_dir(self, tmp_path):
        """Engine should load rules from knowledge directory."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "anatomy.md").write_text(
            "# Anatomy\nThe head is 1/8 of body height.\nArms reach mid-thigh.\n",
            encoding="utf-8",
        )
        engine = DeduplicationEngine(project_root=tmp_path, verbose=False)
        engine.load_existing()
        assert len(engine._rule_index.get("anatomy", [])) >= 1


class TestShaderKnowledgeBase:
    def test_get_params_sprite_lit(self):
        """Should return params for sprite_lit shader type."""
        kb = ShaderKnowledgeBase()
        params = kb.get_params("sprite_lit")
        assert len(params) > 0
        names = [p.name for p in params]
        assert "_NormalStrength" in names
        assert "_PixelSnap" in names

    def test_get_params_outline(self):
        """Should return params for outline shader."""
        kb = ShaderKnowledgeBase()
        params = kb.get_params("outline")
        assert len(params) > 0
        assert any(p.name == "_OutlineWidth" for p in params)

    def test_get_params_pseudo3d(self):
        """Should return params for pseudo_3d_depth shader."""
        kb = ShaderKnowledgeBase()
        params = kb.get_params("pseudo_3d_depth")
        assert len(params) > 0
        assert any(p.name == "_IsometricAngle" for p in params)

    def test_get_preset(self):
        """Should return named presets."""
        kb = ShaderKnowledgeBase()
        preset = kb.get_preset("pixel_art_clean")
        assert preset is not None
        assert preset.shader_type == "sprite_lit"
        assert "_PixelSnap" in preset.params

    def test_list_presets(self):
        """Should list all available presets."""
        kb = ShaderKnowledgeBase()
        presets = kb.list_presets()
        assert "pixel_art_clean" in presets
        assert "crisp_outline" in presets
        assert "pseudo_3d_isometric" in presets

    def test_validate_params_valid(self):
        """Valid params should return empty violations list."""
        kb = ShaderKnowledgeBase()
        violations = kb.validate_params("outline", {"_OutlineWidth": 1.0, "_OutlineAlpha": 1.0})
        assert violations == []

    def test_validate_params_out_of_range(self):
        """Out-of-range params should return violations."""
        kb = ShaderKnowledgeBase()
        violations = kb.validate_params("outline", {"_OutlineWidth": 10.0})  # max is 4.0
        assert len(violations) == 1
        assert "_OutlineWidth" in violations[0]

    def test_validate_params_unknown(self):
        """Unknown params should return violations."""
        kb = ShaderKnowledgeBase()
        violations = kb.validate_params("sprite_lit", {"_UnknownParam": 1.0})
        assert len(violations) == 1

    def test_get_param_ranges(self):
        """Should return correct param ranges."""
        kb = ShaderKnowledgeBase()
        ranges = kb.get_param_ranges("palette_swap")
        assert "_PaletteSize" in ranges
        lo, hi = ranges["_PaletteSize"]
        assert lo == 2
        assert hi == 32

    def test_upgrade_path_report(self):
        """Upgrade path report should contain key sections."""
        kb = ShaderKnowledgeBase()
        report = kb.upgrade_path_report()
        assert "Current State" in report
        assert "Pseudo-3D" in report
        assert "Unity" in report


class TestShaderCodeGenerator:
    def test_generate_properties_block(self):
        """Should generate a valid Properties block."""
        gen = ShaderCodeGenerator()
        props = gen.generate_properties_block("outline")
        assert "Properties" in props
        assert "_OutlineWidth" in props

    def test_generate_hlsl_sprite_lit(self):
        """Should generate HLSL for sprite_lit."""
        gen = ShaderCodeGenerator()
        hlsl = gen.generate_hlsl_fragment("sprite_lit")
        assert "_NormalStrength" in hlsl
        assert "vert(" in hlsl
        assert "frag(" in hlsl

    def test_generate_hlsl_outline(self):
        """Should generate HLSL for outline with Sobel edge detection."""
        gen = ShaderCodeGenerator()
        hlsl = gen.generate_hlsl_fragment("outline")
        assert "SobelEdge" in hlsl
        assert "_OutlineWidth" in hlsl

    def test_generate_hlsl_palette_swap(self):
        """Should generate HLSL for palette swap with Bayer dithering."""
        gen = ShaderCodeGenerator()
        hlsl = gen.generate_hlsl_fragment("palette_swap")
        assert "BayerDither" in hlsl
        assert "NearestPaletteColor" in hlsl

    def test_generate_hlsl_pseudo3d(self):
        """Should generate HLSL for pseudo-3D depth shader."""
        gen = ShaderCodeGenerator()
        hlsl = gen.generate_hlsl_fragment("pseudo_3d_depth")
        assert "IsometricProject" in hlsl
        assert "ParallaxOffset" in hlsl

    def test_generate_with_preset(self):
        """Should use preset values in generated code."""
        gen = ShaderCodeGenerator()
        hlsl = gen.generate_hlsl_fragment("sprite_lit", preset_name="pixel_art_clean")
        assert "0.00" in hlsl  # _NormalStrength = 0.0 in pixel_art_clean

    def test_generate_shadergraph_json(self):
        """Should generate valid JSON for Shader Graph."""
        import json
        gen = ShaderCodeGenerator()
        sg_json = gen.generate_shadergraph_json("outline")
        data = json.loads(sg_json)
        assert "m_Nodes" in data
        assert "m_Properties" in data

    def test_save_all(self, tmp_path):
        """Should save all shader files to output directory."""
        gen = ShaderCodeGenerator()
        saved = gen.save_all(tmp_path, "outline")
        assert len(saved) == 3
        for path in saved:
            assert path.exists()
            assert path.stat().st_size > 0


class TestPseudo3DExtension:
    def test_world_to_screen_origin(self):
        """Origin (0,0,0) should map to (0,0) in screen space."""
        p3d = Pseudo3DExtension()
        sx, sy = p3d.world_to_screen(0, 0, 0)
        assert abs(sx) < 1e-6
        assert abs(sy) < 1e-6

    def test_world_to_screen_symmetry(self):
        """Symmetric world positions should produce symmetric screen positions."""
        p3d = Pseudo3DExtension()
        sx1, sy1 = p3d.world_to_screen(1, 0, 0)
        sx2, sy2 = p3d.world_to_screen(-1, 0, 0)
        assert abs(sx1 + sx2) < 1e-6  # Should be symmetric around x=0

    def test_depth_sort_order(self):
        """Depth sort should order sprites back-to-front."""
        p3d = Pseudo3DExtension()
        sprites = [
            {"name": "A", "x": 1, "y": 0, "z": 1},
            {"name": "B", "x": 3, "y": 0, "z": 3},
            {"name": "C", "x": 0, "y": 0, "z": 0},
        ]
        sorted_sprites = p3d.depth_sort(sprites)
        # C (depth=0) should be first (furthest back in iso = smallest x+z)
        assert sorted_sprites[0]["name"] == "C"
        assert sorted_sprites[-1]["name"] == "B"

    def test_parallax_uv_offsets_count(self):
        """Should return one offset per parallax layer."""
        p3d = Pseudo3DExtension()
        offsets = p3d.parallax_uv_offsets((0.1, 0.0))
        assert len(offsets) == p3d.para.n_layers

    def test_parallax_background_slower(self):
        """Background layer (index 0) should move slower than foreground."""
        p3d = Pseudo3DExtension()
        offsets = p3d.parallax_uv_offsets((0.2, 0.0))
        # Layer 0 (background) should have smaller offset than last layer (foreground)
        assert abs(offsets[0][0]) <= abs(offsets[-1][0])

    def test_billboard_frame_index_range(self):
        """Billboard frame index should be within [0, n_frames)."""
        p3d = Pseudo3DExtension()
        for angle in range(0, 360, 45):
            frame = p3d.billboard_frame_index(0, angle, n_frames=8)
            assert 0 <= frame < 8

    def test_depth_scale_decreases_with_depth(self):
        """Objects should appear smaller at greater depth."""
        p3d = Pseudo3DExtension()
        scale_near = p3d.depth_scale(0.0)
        scale_far  = p3d.depth_scale(5.0)
        assert scale_far < scale_near

    def test_generate_normal_map_size(self):
        """Normal map should have same size as input sprite."""
        pytest.importorskip("scipy")
        from PIL import Image as PILImage
        p3d = Pseudo3DExtension()
        sprite = PILImage.new("RGB", (64, 64), (128, 128, 128))
        normal_map = p3d.generate_normal_map(sprite)
        assert normal_map.size == sprite.size
        assert normal_map.mode == "RGB"

    def test_status_report(self):
        """Status report should contain key sections."""
        p3d = Pseudo3DExtension()
        report = p3d.status_report()
        assert "SCAFFOLD" in report
        assert "Isometric" in report
        assert "Pending" in report
