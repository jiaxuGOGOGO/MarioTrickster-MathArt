"""
SESSION-027: End-to-end integration test for genotype-based character evolution.

This test verifies:
1. Genotype mode produces valid character packs (no evolution)
2. Genotype mode with evolution produces improved characters
3. All preset genotypes produce renderable characters
4. Evolution metadata includes genotype information
"""
import json
import os
import tempfile
from pathlib import Path

from mathart.pipeline import AssetPipeline, CharacterSpec
from mathart.animation.genotype import GENOTYPE_PRESETS, CharacterGenotype


def test_genotype_character_pack_no_evolution():
    """Test genotype mode produces a valid character pack without evolution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline = AssetPipeline(output_dir=tmpdir, verbose=True)
        spec = CharacterSpec(
            name="test_mario_genotype",
            preset="mario",
            use_genotype=True,
            evolution_iterations=0,
            frames_per_state=4,
            states=["idle", "run"],
        )
        result = pipeline.produce_character_pack(spec)
        assert result is not None
        assert result.score > 0.0
        assert result.image is not None
        # Check manifest has genotype info
        assert result.metadata["character"]["genotype"] is not None
        assert result.metadata["character"]["genotype"]["archetype"] == "hero"
        print(f"[PASS] No-evolution genotype pack: score={result.score:.4f}")


def test_genotype_evolution_improves_score():
    """Test genotype evolution produces score improvement."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline = AssetPipeline(output_dir=tmpdir, verbose=True)
        spec = CharacterSpec(
            name="test_evolved_genotype",
            preset="mario",
            use_genotype=True,
            evolution_iterations=3,
            evolution_population=4,
            evolution_variation_strength=0.20,
            evolution_crossover_rate=0.25,
            frames_per_state=4,
            states=["idle", "run"],
        )
        result = pipeline.produce_character_pack(spec)
        assert result is not None
        assert result.score > 0.0

        # Check evolution metadata
        evo = result.metadata["evolution"]
        assert evo["enabled"] is True
        assert evo["mode"] == "genotype_semantic"
        assert evo["iterations"] == 3
        assert evo["crossover_rate"] == 0.25
        assert "genotype" in evo["best_character"]
        assert len(evo["history"]) > 1
        assert evo["best_score"] >= evo["initial_score"]

        print(f"[PASS] Genotype evolution: initial={evo['initial_score']:.4f} → best={evo['best_score']:.4f}")
        print(f"  Archetype: {evo['best_character']['genotype']['archetype']}")
        print(f"  Template: {evo['best_character']['genotype']['body_template']}")


def test_all_preset_genotypes_renderable():
    """Test all preset genotypes produce valid character packs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline = AssetPipeline(output_dir=tmpdir, verbose=False)
        for preset_name in GENOTYPE_PRESETS:
            spec = CharacterSpec(
                name=f"test_{preset_name}_genotype",
                preset=preset_name,
                use_genotype=True,
                evolution_iterations=0,
                frames_per_state=2,
                states=["idle"],
            )
            result = pipeline.produce_character_pack(spec)
            assert result is not None
            assert result.score > 0.0
            assert result.image is not None
            print(f"[PASS] Preset '{preset_name}' genotype: score={result.score:.4f}")


def test_legacy_mode_still_works():
    """Test that legacy (non-genotype) mode is unaffected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline = AssetPipeline(output_dir=tmpdir, verbose=False)
        spec = CharacterSpec(
            name="test_legacy",
            preset="mario",
            use_genotype=False,
            evolution_iterations=0,
            frames_per_state=2,
            states=["idle"],
        )
        result = pipeline.produce_character_pack(spec)
        assert result is not None
        assert result.score > 0.0
        # Legacy mode should not have genotype in manifest
        assert result.metadata["character"]["genotype"] is None
        print(f"[PASS] Legacy mode: score={result.score:.4f}")


if __name__ == "__main__":
    print("=" * 60)
    print("SESSION-027: Genotype E2E Integration Tests")
    print("=" * 60)

    test_genotype_character_pack_no_evolution()
    test_all_preset_genotypes_renderable()
    test_legacy_mode_still_works()
    test_genotype_evolution_improves_score()

    print()
    print("=" * 60)
    print("ALL E2E TESTS PASSED")
    print("=" * 60)
