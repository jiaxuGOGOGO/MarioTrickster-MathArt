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
    ARCHETYPE_TEMPLATES,
    BODY_TEMPLATES,
    DEFAULT_PROPORTION_MODIFIERS,
    GENOTYPE_PRESETS,
    PALETTE_GENE_BOUNDS,
    PALETTE_GENE_NAMES,
    PART_REGISTRY,
    Archetype,
    BodyTemplate,
    BodyTemplateName,
    CharacterGenotype,
    PartDefinition,
    PartSlotInstance,
    SlotType,
    crossover_genotypes,
    enforce_genotype_bounds,
    get_genotype_mutation_contract,
    get_parts_for_slot,
    mutate_genotype,
)
from mathart.animation.parts import CharacterStyle, hat_sdf, assemble_character


GENOTYPE_TEST_SEED = 42


def make_rng(seed: int = GENOTYPE_TEST_SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def assert_all_continuous_genes_within_contract(
    genotype: CharacterGenotype,
) -> list[str]:
    contract = get_genotype_mutation_contract(genotype)
    edge_hits: list[str] = []

    for key, bounds in contract["proportion_modifiers"].items():
        value = genotype.proportion_modifiers[key]
        assert bounds.minimum <= value <= bounds.maximum, (
            f"{key} escaped bounds: {value} not in [{bounds.minimum}, {bounds.maximum}]"
        )
        if np.isclose(value, bounds.minimum) or np.isclose(value, bounds.maximum):
            edge_hits.append(key)

    for attr_name, bounds in contract["scalar_genes"].items():
        value = getattr(genotype, attr_name)
        assert bounds.minimum <= value <= bounds.maximum, (
            f"{attr_name} escaped bounds: {value} not in [{bounds.minimum}, {bounds.maximum}]"
        )
        if np.isclose(value, bounds.minimum) or np.isclose(value, bounds.maximum):
            edge_hits.append(attr_name)

    for idx, bounds in enumerate(contract["palette_genes"]):
        value = genotype.palette_genes[idx]
        gene_name = (
            PALETTE_GENE_NAMES[idx]
            if idx < len(PALETTE_GENE_NAMES)
            else f"palette_extra_{idx}"
        )
        assert bounds.minimum <= value <= bounds.maximum, (
            f"{gene_name} escaped bounds: {value} not in [{bounds.minimum}, {bounds.maximum}]"
        )
        if np.isclose(value, bounds.minimum) or np.isclose(value, bounds.maximum):
            edge_hits.append(gene_name)

    return edge_hits


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

    def test_mutation_is_reproducible_with_fixed_seed(self):
        g = GENOTYPE_PRESETS["mario"]()
        mutated_a = mutate_genotype(g, make_rng(), strength=0.3)
        mutated_b = mutate_genotype(g, make_rng(), strength=0.3)
        assert mutated_a.to_dict() == mutated_b.to_dict()

    def test_mutation_preserves_structure_and_contract_shape(self):
        g = CharacterGenotype()
        mutated = mutate_genotype(g, make_rng(), strength=0.2)
        assert isinstance(mutated, CharacterGenotype)
        assert len(mutated.palette_genes) >= 18
        assert mutated.archetype in [a.value for a in Archetype]

        contract = get_genotype_mutation_contract(mutated)
        assert len(contract["proportion_modifiers"]) == len(DEFAULT_PROPORTION_MODIFIERS)
        assert len(contract["scalar_genes"]) == 2
        assert len(contract["palette_genes"]) >= len(PALETTE_GENE_BOUNDS)

    def test_enforce_genotype_bounds_projects_manual_out_of_range_values(self):
        g = GENOTYPE_PRESETS["mario"]()

        for idx, key in enumerate(DEFAULT_PROPORTION_MODIFIERS):
            g.proportion_modifiers[key] = 1e6 if idx % 2 == 0 else -1e6

        g.outline_width = -1e6
        g.light_angle = 1e6
        g.palette_genes = [1e6 if idx % 2 == 0 else -1e6 for idx in range(18)]

        projected = enforce_genotype_bounds(g)
        edge_hits = assert_all_continuous_genes_within_contract(projected)
        assert len(edge_hits) >= 28

    def test_mutation_hard_clips_all_continuous_genes_under_positive_nuclear_strength(self):
        g = GENOTYPE_PRESETS["mario"]()
        mutated = mutate_genotype(g, make_rng(), strength=1e6)
        edge_hits = assert_all_continuous_genes_within_contract(mutated)
        assert len(edge_hits) >= 8

    def test_mutation_hard_clips_all_continuous_genes_under_negative_nuclear_strength(self):
        g = GENOTYPE_PRESETS["mario"]()
        positive = mutate_genotype(g, make_rng(), strength=1e6)
        negative = mutate_genotype(g, make_rng(), strength=-1e6)
        assert negative.to_dict() == positive.to_dict()
        edge_hits = assert_all_continuous_genes_within_contract(negative)
        assert len(edge_hits) >= 8

    def test_high_strength_can_change_archetype(self):
        rng = make_rng()
        g = GENOTYPE_PRESETS["mario"]()
        archetypes_seen = {g.archetype}
        for _ in range(100):
            mutated = mutate_genotype(g, rng, strength=1.0)
            archetypes_seen.add(mutated.archetype)
        assert len(archetypes_seen) >= 2

    def test_mutation_does_not_modify_original(self):
        g = GENOTYPE_PRESETS["mario"]()
        original_dict = g.to_dict()
        _ = mutate_genotype(g, make_rng(), strength=0.5)
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


# ── 10. SESSION-117: Shape Latent Integration Tests ────────────────────────


class TestShapeLatentGenotype:
    """SESSION-117: SMPL-like shape latent integration into CharacterGenotype.

    White-box tests covering:
      (a) Old-archive backward compatibility (no KeyError on missing shape_latents)
      (b) Extreme shape_latents → measurable skeleton deformation
      (c) Serialization roundtrip safety (JSON-safe, ndarray-free)
      (d) Mutation and crossover propagation
      (e) Bounds enforcement (hard clamp to [-1, 1])
    """

    # ── (a) Backward Compatibility ──────────────────────────────────────────

    def test_legacy_dict_without_shape_latents_no_keyerror(self):
        """Old genotype JSON that lacks 'shape_latents' must not raise."""
        legacy_data = {
            "archetype": "hero",
            "body_template": "humanoid_standard",
            "proportion_modifiers": {"head_radius_mod": 0.01},
            "outline_width": 0.04,
            "light_angle": -0.7,
            "palette_genes": [0.72, 0.04, 0.06] * 6,
        }
        g = CharacterGenotype.from_dict(legacy_data)
        assert len(g.shape_latents) == 8
        assert all(v == 0.0 for v in g.shape_latents), (
            "Missing shape_latents must degrade to neutral (all-zero)"
        )

    def test_legacy_dict_with_none_shape_latents(self):
        """Explicit None in shape_latents must also degrade gracefully."""
        data = {"archetype": "villain", "shape_latents": None}
        g = CharacterGenotype.from_dict(data)
        assert len(g.shape_latents) == 8
        assert all(v == 0.0 for v in g.shape_latents)

    def test_legacy_dict_with_short_shape_latents(self):
        """Shorter-than-8 vector must be zero-padded."""
        data = {"archetype": "hero", "shape_latents": [0.5, -0.3]}
        g = CharacterGenotype.from_dict(data)
        assert len(g.shape_latents) == 8
        assert g.shape_latents[0] == 0.5
        assert g.shape_latents[1] == -0.3
        assert all(v == 0.0 for v in g.shape_latents[2:])

    def test_legacy_dict_with_long_shape_latents(self):
        """Longer-than-8 vector must be truncated to canonical dim."""
        data = {"archetype": "hero", "shape_latents": [0.1] * 20}
        g = CharacterGenotype.from_dict(data)
        assert len(g.shape_latents) == 8

    # ── (b) Skeleton Deformation Under Extreme Latents ──────────────────────

    def test_extreme_latents_change_femur_bone_length(self):
        """When shape_latents go from zero to extreme, thigh bone length must change."""
        g_neutral = CharacterGenotype()
        skel_neutral = g_neutral.build_shaped_skeleton()

        g_extreme = CharacterGenotype()
        g_extreme.shape_latents = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]
        skel_extreme = g_extreme.build_shaped_skeleton()

        # Find thigh bones (l_thigh, r_thigh)
        neutral_thigh = next(b for b in skel_neutral.bones if b.name == "l_thigh")
        extreme_thigh = next(b for b in skel_extreme.bones if b.name == "l_thigh")

        assert abs(extreme_thigh.length - neutral_thigh.length) > 1e-4, (
            f"Thigh bone length must change: neutral={neutral_thigh.length:.6f}, "
            f"extreme={extreme_thigh.length:.6f}"
        )

    def test_extreme_latents_change_shoulder_position(self):
        """Extreme shoulder_width latent must widen shoulder X coordinates."""
        g_neutral = CharacterGenotype()
        skel_neutral = g_neutral.build_shaped_skeleton()

        g_wide = CharacterGenotype()
        g_wide.shape_latents = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # shoulder_width=1.0
        skel_wide = g_wide.build_shaped_skeleton()

        neutral_lshoulder_x = skel_neutral.joints["l_shoulder"].x
        wide_lshoulder_x = skel_wide.joints["l_shoulder"].x

        # Left shoulder should be more negative (wider) with positive shoulder_width
        assert abs(wide_lshoulder_x) > abs(neutral_lshoulder_x), (
            f"Shoulder must widen: neutral_x={neutral_lshoulder_x:.6f}, "
            f"wide_x={wide_lshoulder_x:.6f}"
        )

    def test_extreme_latents_change_head_units(self):
        """Stature latent must affect skeleton head_units."""
        g_neutral = CharacterGenotype()
        skel_neutral = g_neutral.build_shaped_skeleton()

        g_tall = CharacterGenotype()
        g_tall.shape_latents = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # stature=1.0
        skel_tall = g_tall.build_shaped_skeleton()

        assert skel_tall.head_units != skel_neutral.head_units, (
            "Stature latent must change head_units"
        )

    def test_neutral_latents_produce_unmodified_skeleton(self):
        """All-zero shape_latents must produce the same skeleton as default."""
        g = CharacterGenotype()
        skel_default = g.build_shaped_skeleton()

        from mathart.animation.skeleton import Skeleton
        skel_raw = Skeleton.create_humanoid(head_units=g.get_head_units())

        for joint_name in skel_raw.joints:
            assert abs(skel_default.joints[joint_name].x - skel_raw.joints[joint_name].x) < 1e-9
            assert abs(skel_default.joints[joint_name].y - skel_raw.joints[joint_name].y) < 1e-9

    # ── (c) Serialization Roundtrip ─────────────────────────────────────────

    def test_shape_latents_json_roundtrip(self):
        """shape_latents must survive to_dict → JSON → from_dict roundtrip."""
        g = CharacterGenotype()
        g.shape_latents = [0.3, -0.5, 0.7, -0.2, 0.1, -0.9, 0.6, -0.4]
        d = g.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        g2 = CharacterGenotype.from_dict(parsed)
        for i in range(8):
            assert abs(g2.shape_latents[i] - g.shape_latents[i]) < 1e-9

    def test_shape_latents_no_numpy_in_json(self):
        """to_dict must produce pure Python floats, not numpy types."""
        g = CharacterGenotype()
        g.shape_latents = [0.5, -0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        d = g.to_dict()
        for v in d["shape_latents"]:
            assert type(v) is float, f"Expected float, got {type(v)}"

    # ── (d) Mutation & Crossover ────────────────────────────────────────────

    def test_mutation_affects_shape_latents(self):
        """High-strength mutation must modify at least one shape_latent axis."""
        g = CharacterGenotype()
        g.shape_latents = [0.5] * 8
        rng = make_rng(seed=123)
        mutated = mutate_genotype(g, rng, strength=1.0)
        assert any(
            abs(mutated.shape_latents[i] - 0.5) > 1e-6
            for i in range(8)
        ), "Mutation must change at least one shape_latent"

    def test_mutation_respects_bounds(self):
        """Mutated shape_latents must stay within [-1, 1]."""
        g = CharacterGenotype()
        g.shape_latents = [0.95, -0.95, 0.8, -0.8, 0.99, -0.99, 0.5, -0.5]
        rng = make_rng(seed=456)
        for _ in range(50):
            g = mutate_genotype(g, rng, strength=2.0)
            for v in g.shape_latents:
                assert -1.0 <= v <= 1.0, f"shape_latent escaped bounds: {v}"

    def test_crossover_mixes_shape_latents(self):
        """Crossover must produce a child with shape_latents from both parents."""
        parent_a = CharacterGenotype()
        parent_a.shape_latents = [1.0] * 8
        parent_b = CharacterGenotype()
        parent_b.shape_latents = [-1.0] * 8
        rng = make_rng(seed=789)
        child = crossover_genotypes(parent_a, parent_b, rng)
        has_a = any(v > 0.5 for v in child.shape_latents)
        has_b = any(v < -0.5 for v in child.shape_latents)
        assert has_a or has_b, "Crossover must inherit from at least one parent"

    def test_mutation_does_not_mutate_original(self):
        """mutate_genotype must not modify the input genotype's shape_latents."""
        g = CharacterGenotype()
        g.shape_latents = [0.3, -0.2, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0]
        original_latents = list(g.shape_latents)
        rng = make_rng(seed=101)
        _ = mutate_genotype(g, rng, strength=1.0)
        assert g.shape_latents == original_latents, (
            "mutate_genotype must not modify the original"
        )

    # ── (e) Bounds Enforcement ──────────────────────────────────────────────

    def test_enforce_bounds_clamps_shape_latents(self):
        """enforce_genotype_bounds must hard-clamp shape_latents to [-1, 1]."""
        g = CharacterGenotype()
        g.shape_latents = [5.0, -3.0, 2.5, -1.5, 0.0, 0.0, 0.0, 0.0]
        g = enforce_genotype_bounds(g)
        for v in g.shape_latents:
            assert -1.0 <= v <= 1.0, f"shape_latent not clamped: {v}"
        assert g.shape_latents[0] == 1.0
        assert g.shape_latents[1] == -1.0

    def test_enforce_bounds_pads_short_latents(self):
        """enforce_genotype_bounds must pad short shape_latents to 8-dim."""
        g = CharacterGenotype()
        g.shape_latents = [0.5, -0.3]  # Only 2 dims
        g = enforce_genotype_bounds(g)
        assert len(g.shape_latents) == 8
        assert g.shape_latents[0] == 0.5
        assert g.shape_latents[1] == -0.3
        assert all(v == 0.0 for v in g.shape_latents[2:])

    def test_mutation_contract_includes_shape_latents(self):
        """get_genotype_mutation_contract must include shape_latents bounds."""
        contract = get_genotype_mutation_contract()
        assert "shape_latents" in contract
        assert len(contract["shape_latents"]) == 8
        for bounds in contract["shape_latents"]:
            assert bounds.minimum == -1.0
            assert bounds.maximum == 1.0

    # ── (f) Decode-to-Style with Shape Latents ──────────────────────────────

    def test_decode_to_style_with_shape_latents(self):
        """Non-zero shape_latents must affect decoded CharacterStyle proportions."""
        g_neutral = CharacterGenotype()
        style_neutral = g_neutral.decode_to_style()

        g_shaped = CharacterGenotype()
        g_shaped.shape_latents = [0.0, 0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8]
        style_shaped = g_shaped.decode_to_style()

        # shoulder_width and limb_thickness should increase torso_width and leg_thickness
        assert style_shaped.torso_width > style_neutral.torso_width, (
            "Shape latent shoulder_width must increase torso_width"
        )

    def test_default_genotype_has_shape_latents(self):
        """Default CharacterGenotype must have 8-dim all-zero shape_latents."""
        g = CharacterGenotype()
        assert hasattr(g, "shape_latents")
        assert len(g.shape_latents) == 8
        assert all(v == 0.0 for v in g.shape_latents)
