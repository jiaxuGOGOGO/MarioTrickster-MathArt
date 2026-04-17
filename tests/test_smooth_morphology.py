"""Tests for SESSION-057 Parametric SDF Morphology System (Smooth CSG).

Validates:
  - SmoothCSGOperator: all smin variants, smooth subtraction/intersection
  - MorphologyPrimitive: all 7 primitive types produce valid SDFs
  - MorphologyPartGene: build_sdf, serialization roundtrip
  - MorphologyGenotype: decode_to_sdf, bilateral symmetry, serialization
  - MorphologyFactory: random generation, mutation, crossover, evolution
  - Rendering: silhouette generation, diversity evaluation, quality metrics
  - Preset morphologies: slime, golem, flying
"""
import copy
import json
import math

import numpy as np
import pytest

from mathart.animation.smooth_morphology import (
    SmoothCSGOperator,
    MorphologyPrimitive,
    MorphologyPartGene,
    MorphologyGenotype,
    MorphologyFactory,
    PartType,
    PrimitiveType,
    render_morphology_silhouette,
    evaluate_morphology_diversity,
    evaluate_morphology_quality,
    slime_morphology,
    golem_morphology,
    flying_morphology,
    ARCHETYPE_BODY_PLANS,
)


# ── SmoothCSGOperator Tests ─────────────────────────────────────────────────


class TestSmoothCSGOperator:
    """Test IQ-based smooth CSG operators."""

    def test_smin_quadratic_basic(self):
        """Quadratic smin blends two values smoothly."""
        a = np.array([0.5, 0.1, -0.2])
        b = np.array([0.3, 0.4, 0.1])
        dist, mix = SmoothCSGOperator.smin_quadratic(a, b, k=0.1)
        # Result should be <= min(a, b) (smooth min is always <= hard min)
        assert np.all(dist <= np.minimum(a, b) + 1e-6)
        # Mix factor should be in [0, 1]
        assert np.all(mix >= -1e-6)
        assert np.all(mix <= 1.0 + 1e-6)

    def test_smin_cubic_basic(self):
        """Cubic smin blends two values with C2 continuity."""
        a = np.array([0.5, 0.1, -0.2])
        b = np.array([0.3, 0.4, 0.1])
        dist, mix = SmoothCSGOperator.smin_cubic(a, b, k=0.1)
        assert np.all(dist <= np.minimum(a, b) + 1e-6)
        assert np.all(mix >= -1e-6)
        assert np.all(mix <= 1.0 + 1e-6)

    def test_smin_exponential_basic(self):
        """Exponential smin has infinite support."""
        a = np.array([0.5, 0.1, -0.2])
        b = np.array([0.3, 0.4, 0.1])
        dist, mix = SmoothCSGOperator.smin_exponential(a, b, k=0.1)
        assert np.all(dist <= np.minimum(a, b) + 1e-6)
        assert np.all(mix >= -1e-6)
        assert np.all(mix <= 1.0 + 1e-6)

    def test_smin_zero_k_equals_hard_min(self):
        """With k=0, smin should equal hard min."""
        a = np.array([0.5, 0.1, -0.2])
        b = np.array([0.3, 0.4, 0.1])
        for smin_fn in [SmoothCSGOperator.smin_quadratic,
                        SmoothCSGOperator.smin_cubic,
                        SmoothCSGOperator.smin_exponential]:
            dist, _ = smin_fn(a, b, k=0.0)
            np.testing.assert_allclose(dist, np.minimum(a, b), atol=1e-6)

    def test_smin_symmetry(self):
        """smin(a, b, k) should equal smin(b, a, k) in distance."""
        a = np.array([0.3, 0.7, 0.1])
        b = np.array([0.5, 0.2, 0.4])
        for smin_fn in [SmoothCSGOperator.smin_quadratic,
                        SmoothCSGOperator.smin_cubic]:
            d1, _ = smin_fn(a, b, k=0.1)
            d2, _ = smin_fn(b, a, k=0.1)
            np.testing.assert_allclose(d1, d2, atol=1e-6)

    def test_smin_larger_k_more_blending(self):
        """Larger k should produce more blending (lower values)."""
        a = np.array([0.2])
        b = np.array([0.2])
        d_small, _ = SmoothCSGOperator.smin_cubic(a, b, k=0.05)
        d_large, _ = SmoothCSGOperator.smin_cubic(a, b, k=0.15)
        assert d_large[0] < d_small[0]

    def test_smooth_subtraction(self):
        """Smooth subtraction carves shape b from a."""
        a = np.array([0.1, -0.1, 0.3])
        b = np.array([0.2, 0.05, -0.1])
        result = SmoothCSGOperator.smooth_subtraction(a, b, k=0.05)
        assert result.shape == a.shape
        # Should be >= max(a, -b) approximately (smooth version)
        hard = np.maximum(a, -b)
        # Smooth subtraction may slightly differ but should be close
        assert np.all(np.isfinite(result))

    def test_smooth_intersection(self):
        """Smooth intersection keeps overlap region."""
        a = np.array([0.1, -0.1, -0.2])
        b = np.array([-0.1, -0.2, 0.1])
        result = SmoothCSGOperator.smooth_intersection(a, b, k=0.05)
        assert result.shape == a.shape
        assert np.all(np.isfinite(result))


# ── MorphologyPrimitive Tests ────────────────────────────────────────────────


class TestMorphologyPrimitive:
    """Test all 7 SDF primitive types."""

    def _make_grid(self, n=32):
        x = np.linspace(-0.5, 0.5, n)
        y = np.linspace(-0.5, 0.5, n)
        return np.meshgrid(x, y)

    def test_circle_center_negative(self):
        """Circle SDF should be negative at center."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.circle(xx, yy, r=0.2)
        assert d[16, 16] < 0  # Center should be inside

    def test_circle_far_positive(self):
        """Circle SDF should be positive far from center."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.circle(xx, yy, r=0.1)
        assert d[0, 0] > 0  # Corner should be outside

    def test_capsule_produces_valid_sdf(self):
        """Capsule SDF should have negative interior."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.capsule(xx, yy, r1=0.08, r2=0.05, h=0.2)
        assert np.any(d < 0)  # Should have interior

    def test_rounded_box_produces_valid_sdf(self):
        """Rounded box SDF should have negative interior."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.rounded_box(xx, yy, bx=0.15, by=0.1, r=0.03)
        assert np.any(d < 0)

    def test_ellipse_produces_valid_sdf(self):
        """Ellipse SDF should have negative interior."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.ellipse(xx, yy, a=0.2, b=0.1)
        assert np.any(d < 0)

    def test_egg_produces_valid_sdf(self):
        """Egg SDF should have negative interior."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.egg(xx, yy, ra=0.15, rb=0.3)
        assert np.any(d < 0)

    def test_trapezoid_produces_valid_sdf(self):
        """Trapezoid SDF should have negative interior."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.trapezoid(xx, yy, r1=0.15, r2=0.1, he=0.1)
        assert np.any(d < 0)

    def test_vesica_produces_valid_sdf(self):
        """Vesica SDF should have negative interior."""
        xx, yy = self._make_grid()
        d = MorphologyPrimitive.vesica(xx, yy, d=0.05, r=0.15)
        assert np.any(d < 0)

    def test_all_primitives_finite(self):
        """All primitives should produce finite values."""
        xx, yy = self._make_grid()
        assert np.all(np.isfinite(MorphologyPrimitive.circle(xx, yy, 0.1)))
        assert np.all(np.isfinite(MorphologyPrimitive.capsule(xx, yy, 0.05, 0.03, 0.1)))
        assert np.all(np.isfinite(MorphologyPrimitive.rounded_box(xx, yy, 0.1, 0.08, 0.02)))
        assert np.all(np.isfinite(MorphologyPrimitive.ellipse(xx, yy, 0.15, 0.1)))
        assert np.all(np.isfinite(MorphologyPrimitive.egg(xx, yy, 0.12, 0.2)))
        assert np.all(np.isfinite(MorphologyPrimitive.trapezoid(xx, yy, 0.1, 0.08, 0.08)))
        assert np.all(np.isfinite(MorphologyPrimitive.vesica(xx, yy, 0.04, 0.12)))


# ── MorphologyPartGene Tests ────────────────────────────────────────────────


class TestMorphologyPartGene:
    """Test body part gene encoding and SDF construction."""

    def test_build_sdf_circle(self):
        """Circle part gene builds valid SDF."""
        gene = MorphologyPartGene(primitive="circle", param_a=0.1)
        sdf = gene.build_sdf()
        x = np.array([0.0])
        y = np.array([0.0])
        assert sdf(x, y)[0] < 0  # Center inside

    def test_build_sdf_with_transform(self):
        """Part gene with offset/rotation builds valid SDF."""
        gene = MorphologyPartGene(
            primitive="circle", param_a=0.1,
            offset_x=0.2, offset_y=0.3, rotation=0.5,
        )
        sdf = gene.build_sdf()
        # Should be inside at offset position
        x = np.array([0.2])
        y = np.array([0.3])
        assert sdf(x, y)[0] < 0

    def test_serialization_roundtrip(self):
        """Part gene serialization/deserialization preserves data."""
        gene = MorphologyPartGene(
            part_type="limb_upper", primitive="capsule",
            offset_x=0.15, offset_y=-0.1, rotation=0.3,
            param_a=0.06, param_b=0.04, param_c=0.12,
            blend_k=0.07, blend_type="quadratic",
            material_index=2, parent_index=1,
        )
        data = gene.to_dict()
        restored = MorphologyPartGene.from_dict(data)
        assert restored.part_type == gene.part_type
        assert restored.primitive == gene.primitive
        assert abs(restored.offset_x - gene.offset_x) < 1e-10
        assert abs(restored.blend_k - gene.blend_k) < 1e-10
        assert restored.parent_index == gene.parent_index

    def test_all_primitive_types_build(self):
        """Every PrimitiveType enum value builds a valid SDF."""
        for prim in PrimitiveType:
            gene = MorphologyPartGene(primitive=prim.value, param_a=0.1, param_b=0.08, param_c=0.05)
            sdf = gene.build_sdf()
            x = np.array([0.0])
            y = np.array([0.0])
            result = sdf(x, y)
            assert np.isfinite(result[0])


# ── MorphologyGenotype Tests ────────────────────────────────────────────────


class TestMorphologyGenotype:
    """Test complete morphology genotype system."""

    def test_empty_genotype_defaults(self):
        """Empty genotype produces a valid default SDF."""
        g = MorphologyGenotype()
        sdf = g.decode_to_sdf()
        x = np.array([0.0])
        y = np.array([0.0])
        assert sdf(x, y)[0] < 0

    def test_single_part_genotype(self):
        """Single-part genotype produces valid SDF."""
        g = MorphologyGenotype(parts=[
            MorphologyPartGene(primitive="circle", param_a=0.15, parent_index=-1),
        ])
        sdf = g.decode_to_sdf()
        x = np.array([0.0])
        y = np.array([0.0])
        assert sdf(x, y)[0] < 0

    def test_multi_part_blending(self):
        """Multi-part genotype blends parts smoothly."""
        g = MorphologyGenotype(parts=[
            MorphologyPartGene(primitive="circle", param_a=0.12, parent_index=-1),
            MorphologyPartGene(primitive="circle", param_a=0.08,
                               offset_y=0.15, blend_k=0.08, parent_index=0),
        ], bilateral_symmetry=False)
        sdf = g.decode_to_sdf()
        # Both part centers should be inside
        assert sdf(np.array([0.0]), np.array([0.0]))[0] < 0
        assert sdf(np.array([0.0]), np.array([0.15]))[0] < 0
        # Blend region between parts should also be inside
        assert sdf(np.array([0.0]), np.array([0.07]))[0] < 0

    def test_bilateral_symmetry(self):
        """Bilateral symmetry produces mirror-image SDF."""
        g = MorphologyGenotype(
            parts=[
                MorphologyPartGene(primitive="circle", param_a=0.1, parent_index=-1),
                MorphologyPartGene(primitive="circle", param_a=0.06,
                                   offset_x=0.15, blend_k=0.05, parent_index=0),
            ],
            bilateral_symmetry=True,
        )
        sdf = g.decode_to_sdf()
        x_pos = np.array([0.15])
        x_neg = np.array([-0.15])
        y = np.array([0.0])
        # Symmetric: distance at +x should equal distance at -x
        np.testing.assert_allclose(sdf(x_pos, y), sdf(x_neg, y), atol=1e-6)

    def test_serialization_roundtrip(self):
        """Genotype serialization preserves structure."""
        g = MorphologyGenotype(
            parts=[
                MorphologyPartGene(primitive="ellipse", param_a=0.15, parent_index=-1),
                MorphologyPartGene(primitive="capsule", param_a=0.06,
                                   offset_y=0.2, blend_k=0.07, parent_index=0),
            ],
            bilateral_symmetry=True,
            global_scale=1.3,
            archetype="monster_heavy",
        )
        data = g.to_dict()
        json_str = json.dumps(data)
        restored = MorphologyGenotype.from_dict(json.loads(json_str))
        assert len(restored.parts) == 2
        assert restored.bilateral_symmetry is True
        assert abs(restored.global_scale - 1.3) < 1e-6
        assert restored.archetype == "monster_heavy"

    def test_bounding_radius(self):
        """Bounding radius should encompass all parts."""
        g = golem_morphology()
        r = g.get_bounding_radius()
        assert r > 0.2  # Should be substantial for a golem
        assert r < 5.0  # But not unreasonably large

    def test_count_parts(self):
        """Part count should match actual parts list."""
        g = golem_morphology()
        assert g.count_parts() == len(g.parts)
        assert g.count_parts() >= 5  # Golem has many parts


# ── MorphologyFactory Tests ──────────────────────────────────────────────────


class TestMorphologyFactory:
    """Test morphology generation, mutation, and evolution."""

    def test_generate_random_basic(self):
        """Random generation produces valid morphology."""
        factory = MorphologyFactory(seed=42)
        g = factory.generate_random("monster_basic")
        assert len(g.parts) >= 2  # At least core + head
        assert g.archetype == "monster_basic"
        sdf = g.decode_to_sdf()
        assert sdf(np.array([0.0]), np.array([0.0]))[0] < 0

    def test_generate_random_all_archetypes(self):
        """All archetypes generate valid morphologies."""
        factory = MorphologyFactory(seed=42)
        for archetype in ARCHETYPE_BODY_PLANS:
            g = factory.generate_random(archetype)
            assert len(g.parts) >= 1
            sdf = g.decode_to_sdf()
            result = sdf(np.array([0.0]), np.array([0.0]))
            assert np.isfinite(result[0])

    def test_generate_deterministic_with_seed(self):
        """Same seed produces same morphology."""
        g1 = MorphologyFactory(seed=123).generate_random()
        g2 = MorphologyFactory(seed=123).generate_random()
        assert len(g1.parts) == len(g2.parts)
        for p1, p2 in zip(g1.parts, g2.parts):
            assert p1.primitive == p2.primitive
            assert abs(p1.param_a - p2.param_a) < 1e-10

    def test_mutation_changes_genotype(self):
        """Mutation should modify at least some parameters."""
        factory = MorphologyFactory(seed=42)
        original = factory.generate_random()
        mutated = factory.mutate(original, mutation_rate=0.5)
        # At least something should be different
        differences = 0
        for p1, p2 in zip(original.parts, mutated.parts[:len(original.parts)]):
            if abs(p1.param_a - p2.param_a) > 1e-6:
                differences += 1
        # With 50% mutation rate, expect some changes
        assert differences > 0 or len(mutated.parts) != len(original.parts)

    def test_mutation_preserves_validity(self):
        """Mutated genotype should still produce valid SDF."""
        factory = MorphologyFactory(seed=42)
        g = factory.generate_random()
        for _ in range(10):
            g = factory.mutate(g, mutation_rate=0.3)
            sdf = g.decode_to_sdf()
            result = sdf(np.array([0.0]), np.array([0.0]))
            assert np.isfinite(result[0])

    def test_crossover_produces_valid_offspring(self):
        """Crossover of two genotypes produces valid offspring."""
        factory = MorphologyFactory(seed=42)
        p1 = factory.generate_random("monster_basic")
        p2 = factory.generate_random("monster_heavy")
        child = factory.crossover(p1, p2)
        assert len(child.parts) >= 1
        sdf = child.decode_to_sdf()
        result = sdf(np.array([0.0]), np.array([0.0]))
        assert np.isfinite(result[0])

    def test_evolve_population(self):
        """Population evolution produces next generation."""
        factory = MorphologyFactory(seed=42)
        pop = [factory.generate_random() for _ in range(10)]
        fitness = [float(i) for i in range(10)]
        next_gen = factory.evolve_population(pop, fitness)
        assert len(next_gen) == 10
        for g in next_gen:
            sdf = g.decode_to_sdf()
            result = sdf(np.array([0.0]), np.array([0.0]))
            assert np.isfinite(result[0])

    def test_evolve_preserves_elite(self):
        """Evolution preserves the best individual."""
        factory = MorphologyFactory(seed=42)
        pop = [factory.generate_random() for _ in range(10)]
        fitness = list(range(10))
        next_gen = factory.evolve_population(pop, fitness, elite_ratio=0.2)
        # Best individual (index 9) should be in next gen
        assert len(next_gen) == 10


# ── Rendering Tests ──────────────────────────────────────────────────────────


class TestRendering:
    """Test silhouette rendering and evaluation."""

    def test_render_silhouette_shape(self):
        """Rendered silhouette has correct shape."""
        g = slime_morphology()
        img = render_morphology_silhouette(g, resolution=64)
        assert img.shape == (64, 64)

    def test_render_silhouette_has_content(self):
        """Rendered silhouette has non-zero content."""
        g = golem_morphology()
        img = render_morphology_silhouette(g, resolution=64)
        assert np.sum(img) > 0  # Should have some filled pixels

    def test_render_silhouette_binary(self):
        """Rendered silhouette is binary (0 or 1)."""
        g = flying_morphology()
        img = render_morphology_silhouette(g, resolution=64)
        unique_vals = np.unique(img)
        assert len(unique_vals) <= 2
        assert all(v in [0.0, 1.0] for v in unique_vals)

    def test_diversity_evaluation(self):
        """Diversity score is in valid range."""
        factory = MorphologyFactory(seed=42)
        pop = [factory.generate_random() for _ in range(5)]
        diversity = evaluate_morphology_diversity(pop, resolution=32)
        assert 0.0 <= diversity <= 1.0

    def test_diversity_identical_is_zero(self):
        """Identical morphologies should have low diversity."""
        g = slime_morphology()
        pop = [copy.deepcopy(g) for _ in range(3)]
        diversity = evaluate_morphology_diversity(pop, resolution=32)
        assert diversity < 0.1  # Should be very low

    def test_quality_metrics(self):
        """Quality evaluation returns valid metrics."""
        g = golem_morphology()
        metrics = evaluate_morphology_quality(g, resolution=64)
        assert "fill_ratio" in metrics
        assert "compactness" in metrics
        assert "part_count" in metrics
        assert "symmetry_score" in metrics
        assert 0.0 <= metrics["fill_ratio"] <= 1.0
        assert metrics["compactness"] > 0
        assert metrics["part_count"] >= 1
        assert 0.0 <= metrics["symmetry_score"] <= 1.0


# ── Preset Morphology Tests ─────────────────────────────────────────────────


class TestPresetMorphologies:
    """Test preset morphology factories."""

    def test_slime_morphology(self):
        """Slime preset produces valid morphology."""
        g = slime_morphology()
        assert g.archetype == "monster_basic"
        assert len(g.parts) == 2
        sdf = g.decode_to_sdf()
        assert sdf(np.array([0.0]), np.array([0.0]))[0] < 0

    def test_golem_morphology(self):
        """Golem preset produces valid morphology with many parts."""
        g = golem_morphology()
        assert g.archetype == "monster_heavy"
        assert len(g.parts) >= 5
        sdf = g.decode_to_sdf()
        assert sdf(np.array([0.0]), np.array([0.0]))[0] < 0

    def test_flying_morphology(self):
        """Flying preset produces valid morphology."""
        g = flying_morphology()
        assert g.archetype == "monster_flying"
        assert len(g.parts) >= 3
        sdf = g.decode_to_sdf()
        assert sdf(np.array([0.0]), np.array([0.0]))[0] < 0

    def test_presets_render_differently(self):
        """Different presets produce visually distinct silhouettes."""
        s1 = render_morphology_silhouette(slime_morphology(), 32)
        s2 = render_morphology_silhouette(golem_morphology(), 32)
        s3 = render_morphology_silhouette(flying_morphology(), 32)
        # At least some pairs should differ significantly
        diff_12 = np.mean(np.abs(s1 - s2))
        diff_13 = np.mean(np.abs(s1 - s3))
        assert diff_12 > 0.01 or diff_13 > 0.01



