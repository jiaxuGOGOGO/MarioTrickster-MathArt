"""End-to-end Director Studio Blueprint tests.

SESSION-139: P0-SESSION-136-DIRECTOR-STUDIO-V2

Mandatory assertions (from task spec):
1. Preview gate correctly handles two rounds of ``[+]`` feedback.
2. Final ``[1]`` approval triggers Blueprint YAML serialization.
3. Blueprint-based derivation with ``freeze_locks=["physics"]`` and 3 variants
   produces offspring where:
   - Physics trajectory parameters have **variance == 0.0** (100% identical).
   - Unfrozen palette parameters exhibit random mutation (variance > 0).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace with required directory structure."""
    (tmp_path / "workspace" / "blueprints").mkdir(parents=True)
    (tmp_path / "workspace" / "proxy").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def sample_genotype():
    """Create a sample Genotype for testing."""
    from mathart.workspace.director_intent import (
        Genotype, PhysicsConfig, ProportionsConfig, AnimationConfig, ColorPalette,
    )
    return Genotype(
        physics=PhysicsConfig(
            gravity=9.81, mass=1.5, stiffness=60.0,
            damping=0.35, bounce=0.7, friction=0.45,
        ),
        proportions=ProportionsConfig(
            head_ratio=0.28, body_ratio=0.48, limb_ratio=0.24,
            scale=1.1, squash_stretch=1.2,
        ),
        animation=AnimationConfig(
            frame_rate=12, anticipation=0.18, follow_through=0.22,
            exaggeration=1.3, ease_in=0.35, ease_out=0.35, cycle_frames=24,
        ),
        palette=ColorPalette(
            primary="#FF6B35", secondary="#004E89",
            accent="#F7C948", shadow="#1A1A2E", highlight="#FFFFFF",
        ),
    )


@pytest.fixture
def sample_blueprint(workspace, sample_genotype):
    """Create and save a sample Blueprint YAML file."""
    from mathart.workspace.director_intent import Blueprint, BlueprintMeta

    bp = Blueprint(
        meta=BlueprintMeta(name="hero_v1", description="Test hero blueprint"),
        genotype=sample_genotype,
    )
    bp_path = workspace / "workspace" / "blueprints" / "hero_v1.yaml"
    bp.save_yaml(bp_path)
    return bp_path


# ---------------------------------------------------------------------------
# Test 1: Genotype serialization round-trip
# ---------------------------------------------------------------------------

class TestGenotypeRoundTrip:
    """Verify Genotype serialization is lossless and backward-compatible."""

    def test_round_trip_dict(self, sample_genotype):
        from mathart.workspace.director_intent import Genotype
        d = sample_genotype.to_dict()
        restored = Genotype.from_dict(d)
        assert restored.physics.mass == sample_genotype.physics.mass
        assert restored.animation.exaggeration == sample_genotype.animation.exaggeration
        assert restored.palette.primary == sample_genotype.palette.primary

    def test_backward_compat_missing_keys(self):
        """Old blueprints with missing keys should not crash."""
        from mathart.workspace.director_intent import Genotype
        partial = {"physics": {"mass": 2.0}}  # Missing most keys
        g = Genotype.from_dict(partial)
        assert g.physics.mass == 2.0
        assert g.physics.gravity == 9.81  # Default
        assert g.animation.frame_rate == 12  # Default
        assert g.palette.primary == "#FF6B35"  # Default

    def test_no_base64_no_absolute_paths(self, workspace, sample_genotype):
        """Blueprint YAML must be pure — no Base64, no absolute paths."""
        from mathart.workspace.director_intent import Blueprint, BlueprintMeta
        bp = Blueprint(
            meta=BlueprintMeta(name="purity_test"),
            genotype=sample_genotype,
        )
        bp_path = workspace / "workspace" / "blueprints" / "purity_test.yaml"
        bp.save_yaml(bp_path)

        raw_text = bp_path.read_text(encoding="utf-8")
        # No Base64 patterns (long strings of alphanumeric + /+= )
        import re
        assert not re.search(r"[A-Za-z0-9+/]{50,}={0,2}", raw_text), \
            "Blueprint contains Base64-like data"
        # No absolute paths
        assert "/home/" not in raw_text
        assert "C:\\" not in raw_text
        assert "/tmp/" not in raw_text


# ---------------------------------------------------------------------------
# Test 2: Semantic vibe translation
# ---------------------------------------------------------------------------

class TestSemanticTranslation:
    """Verify that fuzzy vibe strings produce meaningful parameter changes."""

    def test_lively_vibe_increases_exaggeration(self, workspace):
        from mathart.workspace.director_intent import DirectorIntentParser
        parser = DirectorIntentParser(workspace_root=workspace)
        spec = parser.parse_dict({"vibe": "活泼"})
        # "活泼" should increase exaggeration above default 1.0
        assert spec.genotype.animation.exaggeration > 1.0

    def test_heavy_vibe_increases_mass(self, workspace):
        from mathart.workspace.director_intent import DirectorIntentParser
        parser = DirectorIntentParser(workspace_root=workspace)
        spec = parser.parse_dict({"vibe": "厚重"})
        assert spec.genotype.physics.mass > 1.0

    def test_combined_vibes(self, workspace):
        from mathart.workspace.director_intent import DirectorIntentParser
        parser = DirectorIntentParser(workspace_root=workspace)
        spec = parser.parse_dict({"vibe": "活泼 夸张"})
        assert spec.genotype.animation.exaggeration > 1.5  # Both contribute


# ---------------------------------------------------------------------------
# Test 3: Blueprint loading and inheritance
# ---------------------------------------------------------------------------

class TestBlueprintInheritance:
    """Verify Blueprint load/save and inheritance via base_blueprint."""

    def test_load_save_round_trip(self, sample_blueprint):
        from mathart.workspace.director_intent import Blueprint
        bp = Blueprint.load_yaml(sample_blueprint)
        assert bp.meta.name == "hero_v1"
        assert bp.genotype.physics.mass == 1.5

    def test_base_blueprint_derivation(self, workspace, sample_blueprint):
        from mathart.workspace.director_intent import DirectorIntentParser
        parser = DirectorIntentParser(workspace_root=workspace)
        spec = parser.parse_dict({
            "base_blueprint": str(sample_blueprint),
            "vibe": "夸张",
        })
        # Should inherit base mass but modify exaggeration
        assert spec.genotype.physics.mass == 1.5  # Inherited
        assert spec.genotype.animation.exaggeration > 1.3  # Modified by vibe


# ---------------------------------------------------------------------------
# Test 4: Interactive Preview Gate — two rounds of [+] then approve
# ---------------------------------------------------------------------------

class TestInteractivePreviewGate:
    """MANDATORY: Gate handles two [+] rounds then [1] approval with blueprint save."""

    def test_two_amplify_rounds_then_approve_and_save(self, workspace, sample_genotype):
        """Assertion ①: Two rounds of [+] feedback, then [1] approve + blueprint save."""
        from mathart.workspace.director_intent import CreatorIntentSpec
        from mathart.quality.interactive_gate import (
            ProgrammaticPreviewGate, GateDecision,
        )

        spec = CreatorIntentSpec(genotype=sample_genotype)

        # Simulate: [+], [+], [1] approve, [Y] save, name="test_hero"
        gate = ProgrammaticPreviewGate(
            workspace_root=workspace,
            choices=["2", "2", "1", "Y", "test_hero"],
        )
        result = gate.run(spec)

        # Verify gate decision
        assert result.decision == GateDecision.BLUEPRINT_SAVED, \
            f"Expected BLUEPRINT_SAVED, got {result.decision}"

        # Verify feedback history
        assert result.total_rounds == 3  # 2 amplify + 1 approve
        actions = [fb.action for fb in result.feedback_history]
        assert actions == ["amplify", "amplify", "approve"]

        # Verify exaggeration increased after two amplify rounds
        assert result.final_genotype is not None
        assert result.final_genotype.animation.exaggeration > sample_genotype.animation.exaggeration

        # Assertion ②: Blueprint YAML file was correctly serialized
        assert result.blueprint_path is not None
        bp_path = Path(result.blueprint_path)
        assert bp_path.exists(), f"Blueprint file not found: {bp_path}"

        # Verify YAML content
        with open(bp_path, "r") as f:
            data = yaml.safe_load(f)
        assert "genotype" in data
        assert "meta" in data
        assert data["meta"]["name"] == "test_hero"

    def test_abort_does_not_save(self, workspace, sample_genotype):
        """Aborting should not save any blueprint."""
        from mathart.workspace.director_intent import CreatorIntentSpec
        from mathart.quality.interactive_gate import (
            ProgrammaticPreviewGate, GateDecision,
        )

        spec = CreatorIntentSpec(genotype=sample_genotype)
        gate = ProgrammaticPreviewGate(
            workspace_root=workspace,
            choices=["4"],  # Immediate abort
        )
        result = gate.run(spec)
        assert result.decision == GateDecision.ABORTED
        assert result.blueprint_path is None

    def test_approve_without_save(self, workspace, sample_genotype):
        """Approving but declining save should return APPROVED (not BLUEPRINT_SAVED)."""
        from mathart.workspace.director_intent import CreatorIntentSpec
        from mathart.quality.interactive_gate import (
            ProgrammaticPreviewGate, GateDecision,
        )

        spec = CreatorIntentSpec(genotype=sample_genotype)
        gate = ProgrammaticPreviewGate(
            workspace_root=workspace,
            choices=["1", "N"],  # Approve, decline save
        )
        result = gate.run(spec)
        assert result.decision == GateDecision.APPROVED
        assert result.blueprint_path is None


# ---------------------------------------------------------------------------
# Test 5: Blueprint Evolution with Freeze Mask — THE CRITICAL TEST
# ---------------------------------------------------------------------------

class TestBlueprintEvolutionFreezeMask:
    """MANDATORY: Controlled-variable evolution with freeze_locks=["physics"].

    Assertion ③:
    - 3 offspring derived from a blueprint
    - Physics parameters have variance == 0.0 (100% identical)
    - Unfrozen palette parameters have variance > 0 (random mutation)
    """

    def test_freeze_physics_derive_3_variants(self, sample_genotype):
        """The SACRED test: frozen physics must have zero variance across offspring."""
        from mathart.evolution.blueprint_evolution import BlueprintEvolutionEngine

        engine = BlueprintEvolutionEngine(mutation_strength=0.15, seed=42)
        result = engine.evolve(
            parent_genotype=sample_genotype,
            num_variants=3,
            freeze_locks=["physics"],
            parent_name="hero_v1",
        )

        assert result.num_variants == 3
        assert len(result.offspring) == 3

        # CRITICAL ASSERTION: All frozen physics parameters have ZERO variance
        # (tolerance 1e-20 for IEEE-754 float representation noise)
        for key, variance in result.frozen_param_variance.items():
            assert variance < 1e-20, \
                f"FREEZE VIOLATION: {key} has variance {variance} (must be ~0.0)"

        # Verify actual values are identical to parent
        parent_flat = sample_genotype.flat_params()
        for offspring in result.offspring:
            for key in result.frozen_param_variance:
                assert offspring.flat_params[key] == parent_flat[key], \
                    f"FREEZE VIOLATION: {key} differs from parent in variant {offspring.variant_index}"

        # CRITICAL ASSERTION: Unfrozen parameters have NON-ZERO variance
        # (at least some of them should have mutated)
        unfrozen_variances = list(result.mutated_param_variance.values())
        assert any(v > 0 for v in unfrozen_variances), \
            "No mutation occurred in unfrozen parameters — evolution is broken"

    def test_freeze_physics_and_proportions(self, sample_genotype):
        """Freezing multiple families should protect all of them."""
        from mathart.evolution.blueprint_evolution import BlueprintEvolutionEngine

        engine = BlueprintEvolutionEngine(mutation_strength=0.20, seed=123)
        result = engine.evolve(
            parent_genotype=sample_genotype,
            num_variants=5,
            freeze_locks=["physics", "proportions"],
            parent_name="hero_v1",
        )

        # All physics AND proportions must be frozen
        parent_flat = sample_genotype.flat_params()
        for offspring in result.offspring:
            for key, value in offspring.flat_params.items():
                if key.startswith("physics.") or key.startswith("proportions."):
                    assert value == parent_flat[key], \
                        f"FREEZE VIOLATION: {key} modified in variant {offspring.variant_index}"

        # Animation params should have mutated
        anim_variances = {
            k: v for k, v in result.mutated_param_variance.items()
            if k.startswith("animation.")
        }
        assert any(v > 0 for v in anim_variances.values()), \
            "Animation parameters should have mutated but didn't"

    def test_no_freeze_all_mutate(self, sample_genotype):
        """With no freeze locks, all parameters should mutate."""
        from mathart.evolution.blueprint_evolution import BlueprintEvolutionEngine

        engine = BlueprintEvolutionEngine(mutation_strength=0.20, seed=99)
        result = engine.evolve(
            parent_genotype=sample_genotype,
            num_variants=5,
            freeze_locks=[],
            parent_name="hero_v1",
        )

        assert len(result.frozen_param_variance) == 0
        assert any(v > 0 for v in result.mutated_param_variance.values())


# ---------------------------------------------------------------------------
# Test 6: End-to-end flywheel — intent → preview → save → evolve
# ---------------------------------------------------------------------------

class TestDirectorStudioFlywheel:
    """Full end-to-end test of the Director Studio flywheel."""

    def test_full_flywheel_emotive_to_evolution(self, workspace):
        """Complete flow: vibe → preview → save blueprint → load → evolve with freeze."""
        from mathart.workspace.director_intent import (
            DirectorIntentParser, Blueprint, CreatorIntentSpec,
        )
        from mathart.quality.interactive_gate import ProgrammaticPreviewGate, GateDecision
        from mathart.evolution.blueprint_evolution import BlueprintEvolutionEngine

        # Phase 1: Parse emotive intent
        parser = DirectorIntentParser(workspace_root=workspace)
        spec = parser.parse_dict({"vibe": "活泼 弹性"})

        # Phase 2: Preview gate with two [+] rounds, approve, save
        gate = ProgrammaticPreviewGate(
            workspace_root=workspace,
            choices=["2", "2", "1", "Y", "bouncy_hero"],
        )
        gate_result = gate.run(spec)
        assert gate_result.decision == GateDecision.BLUEPRINT_SAVED
        assert gate_result.blueprint_path is not None

        # Phase 3: Load the saved blueprint
        saved_bp = Blueprint.load_yaml(Path(gate_result.blueprint_path))
        assert saved_bp.meta.name == "bouncy_hero"

        # Phase 4: Evolve 3 variants with physics frozen
        engine = BlueprintEvolutionEngine(mutation_strength=0.15, seed=42)
        evo_result = engine.evolve(
            parent_genotype=saved_bp.genotype,
            num_variants=3,
            freeze_locks=["physics"],
            parent_name=saved_bp.meta.name,
        )

        # Verify freeze integrity
        for key, variance in evo_result.frozen_param_variance.items():
            assert variance < 1e-20, f"Freeze violated: {key} variance={variance}"

        # Verify mutation occurred
        assert any(v > 0 for v in evo_result.mutated_param_variance.values())


# ---------------------------------------------------------------------------
# Test 7: Proxy renderer smoke test
# ---------------------------------------------------------------------------

class TestProxyRenderer:
    """Verify the proxy renderer produces valid image files."""

    def test_render_produces_png(self, workspace, sample_genotype):
        from mathart.quality.interactive_gate import ProxyRenderer
        renderer = ProxyRenderer()
        output = workspace / "test_proxy.png"
        result = renderer.render_proxy(sample_genotype, output_path=output)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_render_default_path(self, sample_genotype):
        from mathart.quality.interactive_gate import ProxyRenderer
        renderer = ProxyRenderer()
        result = renderer.render_proxy(sample_genotype)
        assert result.exists()
        assert result.suffix == ".png"


# ---------------------------------------------------------------------------
# Test 8: Amplify / Dampen helpers
# ---------------------------------------------------------------------------

class TestAmplifyDampen:
    """Verify parameter adjustment helpers work correctly."""

    def test_amplify_increases_exaggeration(self, sample_genotype):
        from mathart.quality.interactive_gate import amplify_genotype
        amplified = amplify_genotype(sample_genotype)
        assert amplified.animation.exaggeration > sample_genotype.animation.exaggeration

    def test_dampen_decreases_exaggeration(self, sample_genotype):
        from mathart.quality.interactive_gate import dampen_genotype
        dampened = dampen_genotype(sample_genotype)
        assert dampened.animation.exaggeration < sample_genotype.animation.exaggeration

    def test_amplify_does_not_modify_original(self, sample_genotype):
        from mathart.quality.interactive_gate import amplify_genotype
        original_mass = sample_genotype.physics.mass
        _ = amplify_genotype(sample_genotype)
        assert sample_genotype.physics.mass == original_mass


# ---------------------------------------------------------------------------
# Test 9: Freeze mask utilities
# ---------------------------------------------------------------------------

class TestFreezeMask:
    """Verify freeze mask construction and query."""

    def test_family_level_freeze(self):
        from mathart.evolution.blueprint_evolution import build_freeze_mask, is_frozen
        mask = build_freeze_mask(["physics"])
        assert is_frozen("physics.mass", mask)
        assert is_frozen("physics.stiffness", mask)
        assert not is_frozen("animation.exaggeration", mask)

    def test_individual_param_freeze(self):
        from mathart.evolution.blueprint_evolution import build_freeze_mask, is_frozen
        mask = build_freeze_mask(["physics.mass"])
        assert is_frozen("physics.mass", mask)
        assert not is_frozen("physics.stiffness", mask)

    def test_empty_freeze(self):
        from mathart.evolution.blueprint_evolution import build_freeze_mask, is_frozen
        mask = build_freeze_mask([])
        assert not is_frozen("physics.mass", mask)
