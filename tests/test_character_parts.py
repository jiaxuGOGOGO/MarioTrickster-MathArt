"""
Regression tests for SESSION-121 (P1-NEW-9C) — Character Evolution 3.0
expanded part registry, 3D parametric equipment library, and tensorized
bone-socket mounter.

Test suite goals (taken straight from the operational brief):

* **Backward-compatible deserialisation**: legacy JSON archives that
  pre-date the new slots must round-trip without raising and without
  silently dropping the new-style equipment fields.
* **Microsecond-class hot path**: ``TensorSocketMounter.mount`` must
  fully transform a typical 3-attachment loadout in well under a
  millisecond on the reference CI lane (numpy matmul broadcast).
* **Bounding-box envelope (anti Z-fighting)**: every equipment mesh
  produced through the registry must have a bounding box that strictly
  envelopes the underlying base bone position by at least the declared
  ``inflate_radius`` margin — proving the SDF-offset derivation actually
  fired.

Aligns with the three reference pillars documented in
``docs/research/P1_NEW_9C_research_alignment.md`` (UE5 sockets, IQ SDF
operators, OpenUSD reference composition).
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pytest

from mathart.animation.genotype import (
    BodyTemplateName,
    CharacterGenotype,
    PART_REGISTRY,
    PartSlotInstance,
    SlotType,
    get_parts_for_slot,
)
from mathart.animation.parts3d import (
    DEFAULT_SOCKETS,
    PART_FACTORIES_3D,
    MountedAttachment,
    PartShape3D,
    SocketSpec,
    TensorSocketMounter,
    build_attachments_from_genotype,
    build_foot_boots,
    build_foot_greaves,
    build_foot_sandals,
    build_hand_shield,
    build_hand_staff,
    build_hand_sword,
    build_socket_world_matrix,
    build_torso_breastplate,
    build_torso_robe,
    build_torso_vest,
)
from mathart.animation.skeleton import Skeleton


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Registry expansion contract
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistryExpansion:
    """The PART_REGISTRY must expose the three new equipment families."""

    @pytest.mark.parametrize(
        "slot, expected_part_ids",
        [
            (
                SlotType.TORSO_OVERLAY,
                {"torso_none", "torso_breastplate", "torso_robe", "torso_vest"},
            ),
            (
                SlotType.HAND_ITEM,
                {"hand_none", "hand_sword", "hand_staff", "hand_shield"},
            ),
            (
                SlotType.FOOT_ACCESSORY,
                {"foot_none", "foot_boots", "foot_sandals", "foot_greaves"},
            ),
        ],
    )
    def test_expected_parts_present(self, slot, expected_part_ids):
        actual_ids = {
            part.part_id for part in PART_REGISTRY.values()
            if part.slot_type == slot
        }
        # Every expected ID is registered; extras are tolerated for forward growth.
        missing = expected_part_ids - actual_ids
        assert not missing, (
            f"Slot {slot.value!r} is missing {missing!r}; "
            f"actual registered: {actual_ids!r}"
        )

    def test_no_orphan_factories(self):
        """Every 3D factory must correspond to a registered PART_REGISTRY id."""
        registered = set(PART_REGISTRY)
        orphans = set(PART_FACTORIES_3D) - registered
        assert not orphans, (
            f"PART_FACTORIES_3D has unregistered entries: {orphans!r}"
        )

    def test_slot_lookup_for_humanoid(self):
        """The default humanoid template must surface all three new slots."""
        for slot in (
            SlotType.TORSO_OVERLAY,
            SlotType.HAND_ITEM,
            SlotType.FOOT_ACCESSORY,
        ):
            parts = get_parts_for_slot(slot, archetype="hero")
            assert parts, (
                f"Hero archetype must have at least one compatible part "
                f"for slot {slot.value!r}"
            )

    def test_foot_accessory_slot_enum_present(self):
        """SlotType must expose FOOT_ACCESSORY (added in SESSION-121)."""
        assert hasattr(SlotType, "FOOT_ACCESSORY")
        assert SlotType.FOOT_ACCESSORY.value == "foot_accessory"


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Backward-compatible (de)serialisation
# ─────────────────────────────────────────────────────────────────────────────


class TestBackwardCompatibleSerialization:
    """A legacy JSON blob (no new slots) must round-trip cleanly."""

    LEGACY_PAYLOAD = {
        "archetype": "hero",
        "body_template": "humanoid_standard",
        "slots": {
            "hat": {
                "slot_type": "hat",
                "part_id": "hat_cap",
                "enabled": True,
                "local_params": {},
            },
            "face_accessory": {
                "slot_type": "face_accessory",
                "part_id": "face_mustache",
                "enabled": True,
                "local_params": {},
            },
        },
        "proportion_modifiers": {
            "head_radius_mod": 0.01,
            "torso_width_mod": -0.01,
            "torso_height_mod": -0.01,
            "arm_thickness_mod": -0.005,
            "leg_thickness_mod": -0.005,
            "hand_radius_mod": 0.0,
            "foot_width_mod": -0.005,
            "foot_height_mod": 0.0,
        },
        "outline_width": 0.04,
        "light_angle": -0.7,
        "palette_genes": [
            0.78, 0.04, 0.06,
            0.52, 0.18, 0.08,
            0.52, 0.18, 0.08,
            0.42, -0.02, -0.12,
            0.35, 0.05, 0.04,
            0.15, 0.01, 0.01,
        ],
    }

    def test_legacy_json_roundtrip(self):
        legacy = json.loads(json.dumps(self.LEGACY_PAYLOAD))
        g = CharacterGenotype.from_dict(legacy)
        # Must preserve the legacy slot identities verbatim.
        assert g.slots["hat"].part_id == "hat_cap"
        assert g.slots["face_accessory"].part_id == "face_mustache"
        # Must NOT auto-inject new equipment slots when reading old data —
        # that would silently mutate evolutionary lineage.
        assert "torso_overlay" not in g.slots
        assert "hand_item" not in g.slots
        assert "foot_accessory" not in g.slots
        # And shape latents fall back to the neutral default.
        assert g.shape_latents == [0.0] * 8

    def test_legacy_json_decode_to_style(self):
        g = CharacterGenotype.from_dict(self.LEGACY_PAYLOAD)
        style = g.decode_to_style()
        assert style.has_hat is True
        assert style.has_mustache is True

    def test_new_slots_serialise(self):
        """Writing then re-reading a fully-equipped genotype is lossless."""
        g = CharacterGenotype()
        g.slots["torso_overlay"] = PartSlotInstance(
            slot_type=SlotType.TORSO_OVERLAY, part_id="torso_breastplate"
        )
        g.slots["hand_item"] = PartSlotInstance(
            slot_type=SlotType.HAND_ITEM, part_id="hand_sword"
        )
        g.slots["foot_accessory"] = PartSlotInstance(
            slot_type=SlotType.FOOT_ACCESSORY, part_id="foot_boots"
        )
        encoded = json.dumps(g.to_dict())
        decoded = CharacterGenotype.from_dict(json.loads(encoded))
        for key, expected_id in [
            ("torso_overlay", "torso_breastplate"),
            ("hand_item", "hand_sword"),
            ("foot_accessory", "foot_boots"),
        ]:
            assert decoded.slots[key].part_id == expected_id


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — SDF-offset / bounding-box envelope (anti Z-fighting)
# ─────────────────────────────────────────────────────────────────────────────


class TestSDFOffsetGeometry:
    """The math contract: every shell mesh must envelope its base extents."""

    def test_breastplate_vertex_norms_exceed_base(self):
        bp = build_torso_breastplate(
            torso_width=0.27, torso_height=0.21, inflate_radius=0.018
        )
        # The furthest *uninflated* corner from origin would have
        # length = sqrt(hx^2+hy^2+hz^2).  Every inflated vertex must be
        # at least that distance + inflate_radius.
        hx, hy, hz = bp.half_extents
        base_diag = math.sqrt(hx * hx + hy * hy + hz * hz)
        v_norms = np.linalg.norm(bp.mesh.vertices, axis=1)
        assert np.all(v_norms >= base_diag + bp.inflate_radius - 1e-9), (
            "Inflated breastplate vertices should sit outside the base box"
        )

    @pytest.mark.parametrize(
        "factory",
        [build_torso_breastplate, build_torso_robe, build_torso_vest,
         build_foot_boots, build_foot_sandals, build_foot_greaves],
    )
    def test_inflate_radius_strictly_positive(self, factory):
        part = factory()
        assert part.inflate_radius > 0.0, (
            f"{part.part_id}: inflate_radius must be > 0 to guarantee no "
            f"Z-fighting against base body mesh"
        )

    def test_sword_elongation_exceeds_radius(self):
        sword = build_hand_sword(blade_length=0.55, blade_radius=0.018)
        bbox_y = sword.mesh.vertices[:, 1].max() - sword.mesh.vertices[:, 1].min()
        # Total Y-extent must equal blade_length + 2 * radius (caps).
        expected = 0.55 + 2 * 0.018
        assert abs(bbox_y - expected) < 1e-6, (
            f"Sword Y-extent {bbox_y!r} != expected {expected!r}"
        )

    def test_normals_unit_length(self):
        """Every primitive must emit unit-length normals (cel-shading sanity)."""
        for factory in PART_FACTORIES_3D.values():
            part = factory()
            n = np.linalg.norm(part.mesh.normals, axis=1)
            assert np.allclose(n, 1.0, atol=1e-6), (
                f"{part.part_id} normals not unit length: min={n.min()}, max={n.max()}"
            )

    def test_all_factories_emit_nonempty_meshes(self):
        for key, factory in PART_FACTORIES_3D.items():
            part = factory()
            assert part.mesh.vertex_count >= 4, (
                f"{key}: too few vertices ({part.mesh.vertex_count})"
            )
            assert part.mesh.triangle_count >= 4, (
                f"{key}: too few triangles ({part.mesh.triangle_count})"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Socket world-matrix correctness
# ─────────────────────────────────────────────────────────────────────────────


class TestSocketTransform:
    def test_identity_socket_returns_bone_position(self):
        skel = Skeleton.create_humanoid()
        socket = SocketSpec(
            socket_name="t_chest",
            parent_bone="chest",
            local_translation=(0.0, 0.0, 0.0),
            local_rotation_deg=(0.0, 0.0, 0.0),
            local_scale=(1.0, 1.0, 1.0),
        )
        m = build_socket_world_matrix(skel, socket)
        chest = skel.joints["chest"]
        np.testing.assert_allclose(m[:3, 3], [chest.x, chest.y, 0.0], atol=1e-9)
        np.testing.assert_allclose(m[:3, :3], np.eye(3), atol=1e-9)

    def test_translation_offsets_apply_in_bone_space(self):
        skel = Skeleton.create_humanoid()
        socket = SocketSpec(
            socket_name="r_hand_offset",
            parent_bone="r_hand",
            local_translation=(0.05, -0.02, 0.03),
        )
        m = build_socket_world_matrix(skel, socket)
        hand = skel.joints["r_hand"]
        np.testing.assert_allclose(
            m[:3, 3],
            [hand.x + 0.05, hand.y - 0.02, 0.0 + 0.03],
            atol=1e-9,
        )

    def test_rotation_90_around_z_swaps_x_and_y(self):
        skel = Skeleton.create_humanoid()
        socket = SocketSpec(
            socket_name="rot_test",
            parent_bone="chest",
            local_rotation_deg=(0.0, 0.0, 90.0),
        )
        m = build_socket_world_matrix(skel, socket)
        # Apply to a unit-X vector — should map to +Y after 90° z rotation.
        v = np.array([1.0, 0.0, 0.0, 0.0])
        out = m @ v
        np.testing.assert_allclose(out[:3], [0.0, 1.0, 0.0], atol=1e-9)

    def test_unknown_bone_raises(self):
        skel = Skeleton.create_humanoid()
        bad = SocketSpec(socket_name="bad", parent_bone="not_a_bone")
        with pytest.raises(KeyError):
            build_socket_world_matrix(skel, bad)


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — TensorSocketMounter end-to-end
# ─────────────────────────────────────────────────────────────────────────────


class TestSocketMounter:
    def test_mount_returns_correct_count(self):
        skel = Skeleton.create_humanoid()
        mounter = TensorSocketMounter()
        out = mounter.mount(
            skel,
            [
                (build_torso_breastplate(), "torso_overlay"),
                (build_hand_sword(), "hand_item"),
                (build_foot_boots(), "foot_accessory"),
            ],
        )
        assert len(out) == 3
        for m in out:
            assert isinstance(m, MountedAttachment)
            assert m.world_matrix.shape == (4, 4)

    def test_mounted_mesh_is_independent_copy(self):
        """Mutating the returned mesh must NOT touch the original."""
        skel = Skeleton.create_humanoid()
        mounter = TensorSocketMounter()
        original = build_torso_breastplate()
        out = mounter.mount(skel, [(original, "torso_overlay")])
        out[0].mesh.vertices[0, 0] = 999.0
        assert original.mesh.vertices[0, 0] != 999.0

    def test_unknown_slot_key_raises(self):
        skel = Skeleton.create_humanoid()
        mounter = TensorSocketMounter()
        with pytest.raises(KeyError):
            mounter.mount(skel, [(build_hand_sword(), "no_such_slot")])

    def test_world_matrix_translates_meshes_to_chest(self):
        skel = Skeleton.create_humanoid()
        chest = skel.joints["chest"]
        mounter = TensorSocketMounter()
        out = mounter.mount(skel, [(build_torso_breastplate(), "torso_overlay")])
        bp = out[0].mesh
        # Centroid of the breastplate (8 corners of a centred box) should
        # land exactly on the chest bone in world space.
        centroid = bp.vertices.mean(axis=0)
        np.testing.assert_allclose(
            centroid, [chest.x, chest.y, 0.0], atol=1e-9
        )

    def test_microsecond_class_hot_path(self):
        """``mount`` must finish in well under 1 ms for a typical loadout."""
        skel = Skeleton.create_humanoid()
        mounter = TensorSocketMounter()
        loadout = [
            (build_torso_breastplate(), "torso_overlay"),
            (build_hand_sword(), "hand_item"),
            (build_foot_boots(), "foot_accessory"),
        ]
        # Warm-up to avoid first-call import cost dominating timing.
        mounter.mount(skel, loadout)
        n = 50
        start = time.perf_counter()
        for _ in range(n):
            mounter.mount(skel, loadout)
        elapsed = (time.perf_counter() - start) / n
        assert elapsed < 1e-3, (
            f"TensorSocketMounter.mount took {elapsed*1e6:.1f} µs, "
            f"expected < 1 ms"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — Genotype-driven 3D pipeline
# ─────────────────────────────────────────────────────────────────────────────


class TestGenotypePipeline:
    def test_only_3d_eligible_slots_emit_meshes(self):
        """Hat / face slots must NOT show up as 3D attachments."""
        g = CharacterGenotype()
        g.slots["hat"] = PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_helmet"
        )
        g.slots["torso_overlay"] = PartSlotInstance(
            slot_type=SlotType.TORSO_OVERLAY, part_id="torso_breastplate"
        )
        g.slots["foot_accessory"] = PartSlotInstance(
            slot_type=SlotType.FOOT_ACCESSORY, part_id="foot_boots"
        )
        out = build_attachments_from_genotype(g)
        ids = {att.part_id for att in out}
        assert "torso_breastplate" in ids
        assert "foot_boots" in ids
        assert "hat_helmet" not in ids  # 2D path consumes hats

    def test_disabled_slots_skipped(self):
        g = CharacterGenotype()
        g.slots["torso_overlay"] = PartSlotInstance(
            slot_type=SlotType.TORSO_OVERLAY,
            part_id="torso_breastplate",
            enabled=False,
        )
        out = build_attachments_from_genotype(g)
        assert all(att.part_id != "torso_breastplate" for att in out)

    def test_none_choices_skipped(self):
        g = CharacterGenotype()
        g.slots["torso_overlay"] = PartSlotInstance(
            slot_type=SlotType.TORSO_OVERLAY, part_id="torso_none"
        )
        g.slots["hand_item"] = PartSlotInstance(
            slot_type=SlotType.HAND_ITEM, part_id="hand_none"
        )
        g.slots["foot_accessory"] = PartSlotInstance(
            slot_type=SlotType.FOOT_ACCESSORY, part_id="foot_none"
        )
        out = build_attachments_from_genotype(g)
        assert out == []

    def test_default_humanoid_template_supports_all_slots(self):
        """Default humanoid_standard template now exposes all five canonical slots."""
        from mathart.animation.genotype import BODY_TEMPLATES
        template = BODY_TEMPLATES[BodyTemplateName.HUMANOID_STANDARD.value]
        slot_values = {s.value for s in template.available_slots}
        assert {"hat", "face_accessory", "torso_overlay",
                "back_accessory", "hand_item", "foot_accessory"} <= slot_values
