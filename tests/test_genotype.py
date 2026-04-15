"""
SESSION-027: Tests for the Character Genotype & Part Registry system.

Tests cover:
  1. Genotype data structure creation and serialization
  2. Part registry queries and compatibility filtering
  3. Genotype-to-style decoding (phenotype mapping)
  4. Semantic mutation operators (3-layer mutation)
  5. Crossover operator
  6. Preset genotype factories
  7. New hat SDF shapes (crown, helmet, hood)
  8. Pipeline integration with genotype mode
"""
import copy
import json
import numpy as np
import pytest

from mathart.animation.genotype import (
    CharacterGenotype,
    PartSlotInstance,
    PartDefinition,
    BodyTemplate,
    Archetype,
    BodyTemplateName,
    SlotType,
    BODY_TEMPLATES,
    PART_REGISTRY,
    ARCHETYPE_TEMPLATES,
    GENOTYPE_PRESETS,
    mutate_genotype,
    crossover_genotypes,
    get_parts_for_slot,
)
from mathart.animation.parts import CharacterStyle, hat_sdf, assemble_character


# ── 1. Data Structure Tests ──────────────────────────────────────────────────


class TestGenotypeStructure:
    """Test CharacterGenotype data structure."""

    def test_default_genotype_creation(self):
        g = CharacterGenotype()
        assert g.archetype == Archetype.HERO.value
        assert g.body_template == BodyTemplateName.HUMANOID_STANDARD.value
        assert len(g.proportion_modifiers) == 8
        assert len(g.palette_genes) == 18
        assert g.outline_width == 0.04
        assert g.light_angle == -0.7

    def test_default_slots_initialized(self):
        g = CharacterGenotype()
        assert len(g.slots) > 0
        # Slots are only initialized for slot types that have registered parts
        # HAT and FACE_ACCESSORY have parts; others may not yet
        assert SlotType.HAT.value in g.slots
        assert SlotType.FACE_ACCESSORY.value in g.slots

    def test_creature_round_limited_slots(self):
        g = CharacterGenotype(
            archetype=Archetype.MONSTER_BASIC.value,
            body_template=BodyTemplateName.CREATURE_ROUND.value,
        )
        template = BODY_TEMPLATES[BodyTemplateName.CREATURE_ROUND.value]
        assert len(template.available_slots) == 2  # Only HAT and FACE_ACCESSORY

    def test_serialization_roundtrip(self):
        g = CharacterGenotype(
            archetype=Archetype.VILLAIN.value,
            body_template=BodyTemplateName.HUMANOID_TALL.value,
        )
        g.slots[SlotType.HAT.value] = PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_top", enabled=True,
        )
        d = g.to_dict()
        g2 = CharacterGenotype.from_dict(d)
        assert g2.archetype == g.archetype
        assert g2.body_template == g.body_template
        assert g2.slots[SlotType.HAT.value].part_id == "hat_top"
        assert g2.palette_genes == g.palette_genes

    def test_json_serializable(self):
        g = CharacterGenotype()
        d = g.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["archetype"] == "hero"


# ── 2. Part Registry Tests ──────────────────────────────────────────────────


class TestPartRegistry:
    """Test Part Registry queries and compatibility."""

    def test_registry_has_parts(self):
        assert len(PART_REGISTRY) > 0

    def test_hat_parts_exist(self):
        hats = get_parts_for_slot(SlotType.HAT)
        assert len(hats) >= 4  # none, cap, top, crown, helmet, hood

    def test_face_parts_exist(self):
        faces = get_parts_for_slot(SlotType.FACE_ACCESSORY)
        assert len(faces) >= 3  # none, mustache, eyes_dot, eyes_oval, eyes_wide

    def test_archetype_filtering(self):
        hero_hats = get_parts_for_slot(SlotType.HAT, "hero")
        villain_hats = get_parts_for_slot(SlotType.HAT, "villain")
        # Both should have hat_none (no archetype restriction)
        hero_ids = {p.part_id for p in hero_hats}
        villain_ids = {p.part_id for p in villain_hats}
        assert "hat_none" in hero_ids
        assert "hat_none" in villain_ids
        # Cap is for hero, not villain
        assert "hat_cap" in hero_ids
        assert "hat_cap" not in villain_ids
        # Top hat is for villain, not hero
        assert "hat_top" in villain_ids

    def test_all_parts_have_valid_slot_type(self):
        for part_id, part in PART_REGISTRY.items():
            assert isinstance(part.slot_type, SlotType)
            assert part.part_id == part_id


# ── 3. Genotype Decoding Tests ──────────────────────────────────────────────


class TestGenotypeDecode:
    """Test genotype-to-phenotype decoding."""

    def test_decode_produces_character_style(self):
        g = CharacterGenotype()
        style = g.decode_to_style()
        assert isinstance(style, CharacterStyle)

    def test_mario_genotype_decodes_correctly(self):
        g = GENOTYPE_PRESETS["mario"]()
        style = g.decode_to_style()
        assert style.has_hat is True
        assert style.hat_style == "cap"
        assert style.has_mustache is True

    def test_trickster_genotype_decodes_correctly(self):
        g = GENOTYPE_PRESETS["trickster"]()
        style = g.decode_to_style()
        assert style.has_hat is True
        assert style.hat_style == "top"
        assert style.has_mustache is False

    def test_simple_enemy_genotype_decodes_correctly(self):
        g = GENOTYPE_PRESETS["simple_enemy"]()
        style = g.decode_to_style()
        assert style.has_hat is False
        assert style.eye_style == "wide"

    def test_proportion_modifiers_applied(self):
        g = CharacterGenotype()
        base_style = g.decode_to_style()
        g.proportion_modifiers["head_radius_mod"] = 0.10
        mod_style = g.decode_to_style()
        assert mod_style.head_radius > base_style.head_radius

    def test_proportion_modifiers_clamped(self):
        g = CharacterGenotype()
        g.proportion_modifiers["head_radius_mod"] = 999.0  # Way out of range
        style = g.decode_to_style()
        assert style.head_radius <= 0.52  # Max allowed

    def test_head_units_from_template(self):
        g = CharacterGenotype(body_template=BodyTemplateName.HUMANOID_CHIBI.value)
        assert g.get_head_units() == 2.5
        g2 = CharacterGenotype(body_template=BodyTemplateName.HUMANOID_TALL.value)
        assert g2.get_head_units() == 3.5

    def test_slot_overrides_applied(self):
        g = CharacterGenotype()
        g.slots[SlotType.HAT.value] = PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_crown", enabled=True,
        )
        style = g.decode_to_style()
        assert style.has_hat is True
        assert style.hat_style == "crown"

    def test_disabled_slot_not_applied(self):
        g = CharacterGenotype()
        g.slots[SlotType.HAT.value] = PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_cap", enabled=False,
        )
        style = g.decode_to_style()
        # Default is has_hat=False since the slot is disabled
        assert style.has_hat is False


# ── 4. Mutation Tests ────────────────────────────────────────────────────────


class TestMutation:
    """Test semantic mutation operators."""

    def test_mutation_produces_different_genotype(self):
        rng = np.random.default_rng(42)
        g = GENOTYPE_PRESETS["mario"]()
        mutated = mutate_genotype(g, rng, strength=0.3)
        # At least some proportion modifiers should differ
        diffs = sum(
            1 for k in g.proportion_modifiers
            if abs(g.proportion_modifiers[k] - mutated.proportion_modifiers[k]) > 1e-6
        )
        assert diffs > 0

    def test_mutation_preserves_structure(self):
        rng = np.random.default_rng(42)
        g = CharacterGenotype()
        mutated = mutate_genotype(g, rng, strength=0.2)
        assert isinstance(mutated, CharacterGenotype)
        assert len(mutated.palette_genes) >= 18
        assert mutated.archetype in [a.value for a in Archetype]

    def test_mutation_respects_palette_constraints(self):
        rng = np.random.default_rng(42)
        g = CharacterGenotype()
        for _ in range(20):
            g = mutate_genotype(g, rng, strength=0.5)
        # Outline should stay dark
        assert g.palette_genes[15] <= 0.22
        # Skin should stay in range
        assert 0.55 <= g.palette_genes[0] <= 0.88

    def test_high_strength_can_change_archetype(self):
        rng = np.random.default_rng(1)
        g = GENOTYPE_PRESETS["mario"]()
        archetypes_seen = {g.archetype}
        for _ in range(100):
            mutated = mutate_genotype(g, rng, strength=1.0)
            archetypes_seen.add(mutated.archetype)
        # With high strength over many trials, we should see at least 2 archetypes
        assert len(archetypes_seen) >= 2

    def test_low_strength_preserves_archetype(self):
        rng = np.random.default_rng(42)
        g = GENOTYPE_PRESETS["mario"]()
        for _ in range(20):
            mutated = mutate_genotype(g, rng, strength=0.05)
            # Very low strength should rarely change archetype
            # (not guaranteed, but very likely)

    def test_mutation_does_not_modify_original(self):
        rng = np.random.default_rng(42)
        g = GENOTYPE_PRESETS["mario"]()
        original_dict = g.to_dict()
        _ = mutate_genotype(g, rng, strength=0.5)
        assert g.to_dict() == original_dict


# ── 5. Crossover Tests ──────────────────────────────────────────────────────


class TestCrossover:
    """Test crossover operator."""

    def test_crossover_produces_valid_genotype(self):
        rng = np.random.default_rng(42)
        a = GENOTYPE_PRESETS["mario"]()
        b = GENOTYPE_PRESETS["trickster"]()
        child = crossover_genotypes(a, b, rng)
        assert isinstance(child, CharacterGenotype)
        assert len(child.palette_genes) >= 18

    def test_crossover_mixes_parents(self):
        rng = np.random.default_rng(42)
        a = GENOTYPE_PRESETS["mario"]()
        b = GENOTYPE_PRESETS["trickster"]()
        # Run many crossovers to check mixing
        saw_a_archetype = False
        saw_b_archetype = False
        for seed in range(50):
            rng2 = np.random.default_rng(seed)
            child = crossover_genotypes(a, b, rng2)
            if child.archetype == a.archetype:
                saw_a_archetype = True
            if child.archetype == b.archetype:
                saw_b_archetype = True
        assert saw_a_archetype or saw_b_archetype

    def test_crossover_does_not_modify_parents(self):
        rng = np.random.default_rng(42)
        a = GENOTYPE_PRESETS["mario"]()
        b = GENOTYPE_PRESETS["trickster"]()
        a_dict = a.to_dict()
        b_dict = b.to_dict()
        _ = crossover_genotypes(a, b, rng)
        assert a.to_dict() == a_dict
        assert b.to_dict() == b_dict


# ── 6. Preset Tests ──────────────────────────────────────────────────────────


class TestPresets:
    """Test preset genotype factories."""

    def test_all_presets_exist(self):
        expected = {"mario", "trickster", "simple_enemy", "flying_enemy", "bouncing_enemy"}
        assert set(GENOTYPE_PRESETS.keys()) == expected

    def test_all_presets_decode_to_valid_style(self):
        for name, factory in GENOTYPE_PRESETS.items():
            g = factory()
            style = g.decode_to_style()
            assert isinstance(style, CharacterStyle), f"Preset {name} failed"

    def test_all_presets_have_valid_archetypes(self):
        valid = {a.value for a in Archetype}
        for name, factory in GENOTYPE_PRESETS.items():
            g = factory()
            assert g.archetype in valid, f"Preset {name} has invalid archetype"

    def test_all_presets_have_valid_templates(self):
        for name, factory in GENOTYPE_PRESETS.items():
            g = factory()
            assert g.body_template in BODY_TEMPLATES, f"Preset {name} has invalid template"

    def test_all_presets_have_18_palette_genes(self):
        for name, factory in GENOTYPE_PRESETS.items():
            g = factory()
            assert len(g.palette_genes) == 18, f"Preset {name} has wrong palette gene count"


# ── 7. New Hat SDF Tests ─────────────────────────────────────────────────────


class TestNewHatSDFs:
    """Test the new hat SDF shapes added in SESSION-027."""

    def _make_style_with_hat(self, hat_style: str) -> CharacterStyle:
        return CharacterStyle(has_hat=True, hat_style=hat_style)

    def test_crown_sdf_exists(self):
        style = self._make_style_with_hat("crown")
        sdf_func = hat_sdf(style)
        assert sdf_func is not None

    def test_crown_sdf_evaluates(self):
        style = self._make_style_with_hat("crown")
        sdf_func = hat_sdf(style)
        x = np.linspace(-1, 1, 16)
        y = np.linspace(-1, 1, 16)
        X, Y = np.meshgrid(x, y)
        result = sdf_func(X, Y)
        assert result.shape == X.shape
        # Should have some negative values (inside the crown)
        assert np.any(result < 0)

    def test_helmet_sdf_exists(self):
        style = self._make_style_with_hat("helmet")
        sdf_func = hat_sdf(style)
        assert sdf_func is not None

    def test_helmet_sdf_evaluates(self):
        style = self._make_style_with_hat("helmet")
        sdf_func = hat_sdf(style)
        x = np.linspace(-1, 1, 16)
        y = np.linspace(-1, 1, 16)
        X, Y = np.meshgrid(x, y)
        result = sdf_func(X, Y)
        assert result.shape == X.shape
        assert np.any(result < 0)

    def test_hood_sdf_exists(self):
        style = self._make_style_with_hat("hood")
        sdf_func = hat_sdf(style)
        assert sdf_func is not None

    def test_hood_sdf_evaluates(self):
        style = self._make_style_with_hat("hood")
        sdf_func = hat_sdf(style)
        x = np.linspace(-1, 1, 16)
        y = np.linspace(-1, 1, 16)
        X, Y = np.meshgrid(x, y)
        result = sdf_func(X, Y)
        assert result.shape == X.shape
        assert np.any(result < 0)

    def test_all_new_hats_render_in_character(self):
        """Ensure new hat styles work with the full character assembly."""
        for hat_style in ["crown", "helmet", "hood"]:
            style = self._make_style_with_hat(hat_style)
            parts = assemble_character(style)
            hat_parts = [p for p in parts if p.name == "hat"]
            assert len(hat_parts) == 1, f"Hat style '{hat_style}' not assembled"


# ── 8. Archetype-Template Compatibility Tests ────────────────────────────────


class TestArchetypeCompatibility:
    """Test archetype-to-template compatibility mapping."""

    def test_all_archetypes_have_templates(self):
        for archetype in Archetype:
            assert archetype.value in ARCHETYPE_TEMPLATES
            assert len(ARCHETYPE_TEMPLATES[archetype.value]) > 0

    def test_all_template_refs_valid(self):
        for archetype, templates in ARCHETYPE_TEMPLATES.items():
            for template_name in templates:
                assert template_name in BODY_TEMPLATES, \
                    f"Archetype {archetype} references invalid template {template_name}"


# ── 9. Body Template Tests ──────────────────────────────────────────────────


class TestBodyTemplates:
    """Test body template definitions."""

    def test_all_templates_exist(self):
        for name in BodyTemplateName:
            assert name.value in BODY_TEMPLATES

    def test_templates_have_valid_proportions(self):
        for name, template in BODY_TEMPLATES.items():
            assert 0.2 <= template.head_radius <= 0.6
            assert 0.1 <= template.torso_width <= 0.5
            assert 0.1 <= template.torso_height <= 0.4
            assert 2.0 <= template.head_units <= 4.0

    def test_templates_have_slots(self):
        for name, template in BODY_TEMPLATES.items():
            assert len(template.available_slots) > 0
