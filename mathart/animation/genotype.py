"""
Character Genotype & Part Registry — Semantic mutation space for character evolution 2.5.

SESSION-027 UPGRADE:
  This module introduces a hierarchical, component-based character representation
  that replaces the flat numerical parameter vector with a structured genotype.

  Key concepts:
    - **CharacterArchetype**: High-level semantic identity (hero, villain, NPC, monster)
    - **BodyTemplate**: Defines body proportions and which slots are available
    - **PartDefinition**: A registered part that can fill a slot (hat, accessory, etc.)
    - **PartSlot**: An instance of a part in a specific slot with local parameters
    - **CharacterGenotype**: The complete, evolvable representation of a character

  The genotype is decoded into a standard CharacterStyle + extra PartSlot list,
  which the existing renderer can consume without modification.

Design philosophy:
  - The genotype is the search space; the phenotype is CharacterStyle + BodyPart list
  - All mutations operate on the genotype; decoding produces valid characters
  - Slot compatibility is enforced by the registry, not by the mutation operator
  - Mixed discrete/continuous encoding: discrete choices (archetype, part IDs) are
    stored as categorical values; continuous params are floats with defined ranges

References:
  - Shape Grammar for PCG (SESSION-027 REF-R027-003)
  - Mixed Integer-Discrete-Continuous Optimization (SESSION-027 REF-R027-002)
  - Hierarchical Component Model (SESSION-027 REF-R027-001)
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np

from .parts import CharacterStyle


# ── Enumerations ──────────────────────────────────────────────────────────────


class Archetype(str, Enum):
    """High-level character archetypes that define semantic identity."""
    HERO = "hero"
    VILLAIN = "villain"
    NPC_WORKER = "npc_worker"
    NPC_MERCHANT = "npc_merchant"
    MONSTER_BASIC = "monster_basic"
    MONSTER_FLYING = "monster_flying"
    MONSTER_HEAVY = "monster_heavy"


class BodyTemplateName(str, Enum):
    """Named body proportion templates."""
    HUMANOID_STANDARD = "humanoid_standard"
    HUMANOID_CHIBI = "humanoid_chibi"
    HUMANOID_TALL = "humanoid_tall"
    CREATURE_ROUND = "creature_round"
    CREATURE_TALL = "creature_tall"


class SlotType(str, Enum):
    """Types of equipment/accessory slots."""
    HAT = "hat"
    FACE_ACCESSORY = "face_accessory"
    TORSO_OVERLAY = "torso_overlay"
    BACK_ACCESSORY = "back_accessory"
    HAND_ITEM = "hand_item"


# ── Body Templates ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BodyTemplate:
    """Defines base body proportions and available slots for a body type.

    These proportions map directly to CharacterStyle fields.
    """
    name: str
    head_radius: float
    torso_width: float
    torso_height: float
    arm_thickness: float
    leg_thickness: float
    hand_radius: float
    foot_width: float
    foot_height: float
    head_units: float = 3.0
    available_slots: tuple[SlotType, ...] = (
        SlotType.HAT,
        SlotType.FACE_ACCESSORY,
        SlotType.TORSO_OVERLAY,
        SlotType.BACK_ACCESSORY,
        SlotType.HAND_ITEM,
    )


BODY_TEMPLATES: dict[str, BodyTemplate] = {
    BodyTemplateName.HUMANOID_STANDARD.value: BodyTemplate(
        name="humanoid_standard",
        head_radius=0.37, torso_width=0.27, torso_height=0.21,
        arm_thickness=0.075, leg_thickness=0.085,
        hand_radius=0.055, foot_width=0.095, foot_height=0.045,
        head_units=3.0,
    ),
    BodyTemplateName.HUMANOID_CHIBI.value: BodyTemplate(
        name="humanoid_chibi",
        head_radius=0.42, torso_width=0.24, torso_height=0.17,
        arm_thickness=0.065, leg_thickness=0.075,
        hand_radius=0.05, foot_width=0.085, foot_height=0.04,
        head_units=2.5,
    ),
    BodyTemplateName.HUMANOID_TALL.value: BodyTemplate(
        name="humanoid_tall",
        head_radius=0.32, torso_width=0.25, torso_height=0.26,
        arm_thickness=0.065, leg_thickness=0.075,
        hand_radius=0.05, foot_width=0.085, foot_height=0.04,
        head_units=3.5,
    ),
    BodyTemplateName.CREATURE_ROUND.value: BodyTemplate(
        name="creature_round",
        head_radius=0.44, torso_width=0.32, torso_height=0.16,
        arm_thickness=0.06, leg_thickness=0.08,
        hand_radius=0.04, foot_width=0.10, foot_height=0.05,
        head_units=2.5,
        available_slots=(SlotType.HAT, SlotType.FACE_ACCESSORY),
    ),
    BodyTemplateName.CREATURE_TALL.value: BodyTemplate(
        name="creature_tall",
        head_radius=0.34, torso_width=0.30, torso_height=0.24,
        arm_thickness=0.07, leg_thickness=0.09,
        hand_radius=0.045, foot_width=0.11, foot_height=0.05,
        head_units=3.2,
        available_slots=(SlotType.HAT, SlotType.BACK_ACCESSORY),
    ),
}


# ── Part Definitions ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PartDefinition:
    """A registered part that can fill a specific slot type.

    Each part has an ID, the slot it fills, and a set of parameters
    that control its appearance. The `style_overrides` dict maps to
    CharacterStyle fields that this part modifies.
    """
    part_id: str
    slot_type: SlotType
    display_name: str
    style_overrides: dict[str, Any] = field(default_factory=dict)
    param_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    compatible_archetypes: tuple[str, ...] = ()  # Empty = compatible with all


# ── Part Registry ─────────────────────────────────────────────────────────────


PART_REGISTRY: dict[str, PartDefinition] = {
    # ── Hats ──
    "hat_none": PartDefinition(
        part_id="hat_none",
        slot_type=SlotType.HAT,
        display_name="No Hat",
        style_overrides={"has_hat": False},
    ),
    "hat_cap": PartDefinition(
        part_id="hat_cap",
        slot_type=SlotType.HAT,
        display_name="Baseball Cap",
        style_overrides={"has_hat": True, "hat_style": "cap"},
        compatible_archetypes=("hero", "npc_worker", "npc_merchant"),
    ),
    "hat_top": PartDefinition(
        part_id="hat_top",
        slot_type=SlotType.HAT,
        display_name="Top Hat",
        style_overrides={"has_hat": True, "hat_style": "top"},
        compatible_archetypes=("villain", "npc_merchant"),
    ),
    "hat_crown": PartDefinition(
        part_id="hat_crown",
        slot_type=SlotType.HAT,
        display_name="Crown",
        style_overrides={"has_hat": True, "hat_style": "crown"},
        compatible_archetypes=("hero", "villain", "npc_merchant"),
    ),
    "hat_helmet": PartDefinition(
        part_id="hat_helmet",
        slot_type=SlotType.HAT,
        display_name="Helmet",
        style_overrides={"has_hat": True, "hat_style": "helmet"},
        compatible_archetypes=("hero", "villain", "monster_heavy"),
    ),
    "hat_hood": PartDefinition(
        part_id="hat_hood",
        slot_type=SlotType.HAT,
        display_name="Hood",
        style_overrides={"has_hat": True, "hat_style": "hood"},
        compatible_archetypes=("villain", "npc_worker"),
    ),

    # ── Face Accessories ──
    "face_none": PartDefinition(
        part_id="face_none",
        slot_type=SlotType.FACE_ACCESSORY,
        display_name="No Face Accessory",
        style_overrides={"has_mustache": False},
    ),
    "face_mustache": PartDefinition(
        part_id="face_mustache",
        slot_type=SlotType.FACE_ACCESSORY,
        display_name="Mustache",
        style_overrides={"has_mustache": True},
        compatible_archetypes=("hero", "villain", "npc_merchant"),
    ),

    # ── Eye Styles (treated as face sub-slot) ──
    "eyes_dot": PartDefinition(
        part_id="eyes_dot",
        slot_type=SlotType.FACE_ACCESSORY,
        display_name="Dot Eyes",
        style_overrides={"eye_style": "dot"},
    ),
    "eyes_oval": PartDefinition(
        part_id="eyes_oval",
        slot_type=SlotType.FACE_ACCESSORY,
        display_name="Oval Eyes",
        style_overrides={"eye_style": "oval"},
    ),
    "eyes_wide": PartDefinition(
        part_id="eyes_wide",
        slot_type=SlotType.FACE_ACCESSORY,
        display_name="Wide Eyes",
        style_overrides={"eye_style": "wide"},
        compatible_archetypes=("monster_basic", "monster_flying", "monster_heavy"),
    ),
}


def get_parts_for_slot(
    slot_type: SlotType,
    archetype: Optional[str] = None,
) -> list[PartDefinition]:
    """Get all registered parts compatible with a slot and archetype."""
    results = []
    for part in PART_REGISTRY.values():
        if part.slot_type != slot_type:
            continue
        if archetype and part.compatible_archetypes:
            if archetype not in part.compatible_archetypes:
                continue
        results.append(part)
    return results


# ── Character Genotype ────────────────────────────────────────────────────────


@dataclass
class PartSlotInstance:
    """An active part in a specific slot with its local parameters."""
    slot_type: SlotType
    part_id: str
    enabled: bool = True
    local_params: dict[str, float] = field(default_factory=dict)


@dataclass
class CharacterGenotype:
    """Complete evolvable character representation.

    This is the 'genome' that the evolutionary algorithm operates on.
    It encodes both structural choices (archetype, body template, parts)
    and continuous parameters (proportions, colors, style tweaks).

    The genotype is decoded into a CharacterStyle + palette for rendering.
    """
    # ── Structural genes (discrete) ──
    archetype: str = Archetype.HERO.value
    body_template: str = BodyTemplateName.HUMANOID_STANDARD.value

    # ── Equipment slots (discrete + continuous) ──
    slots: dict[str, PartSlotInstance] = field(default_factory=dict)

    # ── Continuous proportion modifiers (applied on top of body template) ──
    proportion_modifiers: dict[str, float] = field(default_factory=lambda: {
        "head_radius_mod": 0.0,
        "torso_width_mod": 0.0,
        "torso_height_mod": 0.0,
        "arm_thickness_mod": 0.0,
        "leg_thickness_mod": 0.0,
        "hand_radius_mod": 0.0,
        "foot_width_mod": 0.0,
        "foot_height_mod": 0.0,
    })

    # ── Style genes (continuous) ──
    outline_width: float = 0.04
    light_angle: float = -0.7

    # ── Palette genes (6 colors × 3 channels = 18 floats in OKLAB) ──
    palette_genes: list[float] = field(default_factory=lambda: [
        0.72, 0.04, 0.06,   # skin
        0.50, 0.15, 0.05,   # hair/hat
        0.50, 0.15, 0.05,   # shirt
        0.45, -0.02, -0.10, # pants
        0.35, 0.03, 0.02,   # shoes
        0.18, 0.00, 0.00,   # outline
    ])

    def __post_init__(self):
        """Initialize default slots if empty."""
        if not self.slots:
            template = BODY_TEMPLATES.get(self.body_template)
            if template:
                for slot_type in template.available_slots:
                    parts = get_parts_for_slot(slot_type, self.archetype)
                    if parts:
                        # Default to first available part (usually "none")
                        none_parts = [p for p in parts if "none" in p.part_id]
                        default_part = none_parts[0] if none_parts else parts[0]
                        self.slots[slot_type.value] = PartSlotInstance(
                            slot_type=slot_type,
                            part_id=default_part.part_id,
                            enabled=True,
                        )

    def decode_to_style(self) -> CharacterStyle:
        """Decode genotype into a CharacterStyle for rendering.

        This is the genotype-to-phenotype mapping. It:
        1. Starts from the body template's base proportions
        2. Applies proportion modifiers
        3. Applies part slot overrides (hat, mustache, eye style, etc.)
        """
        template = BODY_TEMPLATES.get(self.body_template)
        if template is None:
            template = BODY_TEMPLATES[BodyTemplateName.HUMANOID_STANDARD.value]

        mods = self.proportion_modifiers

        style = CharacterStyle(
            head_radius=float(np.clip(
                template.head_radius + mods.get("head_radius_mod", 0.0),
                0.26, 0.52,
            )),
            torso_width=float(np.clip(
                template.torso_width + mods.get("torso_width_mod", 0.0),
                0.16, 0.36,
            )),
            torso_height=float(np.clip(
                template.torso_height + mods.get("torso_height_mod", 0.0),
                0.12, 0.30,
            )),
            arm_thickness=float(np.clip(
                template.arm_thickness + mods.get("arm_thickness_mod", 0.0),
                0.04, 0.12,
            )),
            leg_thickness=float(np.clip(
                template.leg_thickness + mods.get("leg_thickness_mod", 0.0),
                0.05, 0.14,
            )),
            hand_radius=float(np.clip(
                template.hand_radius + mods.get("hand_radius_mod", 0.0),
                0.03, 0.09,
            )),
            foot_width=float(np.clip(
                template.foot_width + mods.get("foot_width_mod", 0.0),
                0.05, 0.16,
            )),
            foot_height=float(np.clip(
                template.foot_height + mods.get("foot_height_mod", 0.0),
                0.025, 0.09,
            )),
            outline_width=float(np.clip(self.outline_width, 0.02, 0.07)),
            light_angle=float(np.clip(self.light_angle, -1.5, 1.5)),
            # Defaults that will be overridden by slot parts
            has_hat=False,
            hat_style="cap",
            has_mustache=False,
            eye_style="dot",
        )

        # Apply slot overrides
        for slot_inst in self.slots.values():
            if not slot_inst.enabled:
                continue
            part_def = PART_REGISTRY.get(slot_inst.part_id)
            if part_def is None:
                continue
            for attr, value in part_def.style_overrides.items():
                if hasattr(style, attr):
                    setattr(style, attr, value)

        return style

    def get_head_units(self) -> float:
        """Get head_units from the body template."""
        template = BODY_TEMPLATES.get(self.body_template)
        if template is None:
            return 3.0
        return template.head_units

    def to_dict(self) -> dict[str, Any]:
        """Serialize genotype to a JSON-compatible dict."""
        return {
            "archetype": self.archetype,
            "body_template": self.body_template,
            "slots": {
                k: {
                    "slot_type": v.slot_type.value,
                    "part_id": v.part_id,
                    "enabled": v.enabled,
                    "local_params": dict(v.local_params),
                }
                for k, v in self.slots.items()
            },
            "proportion_modifiers": dict(self.proportion_modifiers),
            "outline_width": self.outline_width,
            "light_angle": self.light_angle,
            "palette_genes": list(self.palette_genes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterGenotype:
        """Deserialize genotype from a dict."""
        slots = {}
        for k, v in data.get("slots", {}).items():
            slots[k] = PartSlotInstance(
                slot_type=SlotType(v["slot_type"]),
                part_id=v["part_id"],
                enabled=v.get("enabled", True),
                local_params=v.get("local_params", {}),
            )
        return cls(
            archetype=data.get("archetype", Archetype.HERO.value),
            body_template=data.get("body_template", BodyTemplateName.HUMANOID_STANDARD.value),
            slots=slots,
            proportion_modifiers=data.get("proportion_modifiers", {}),
            outline_width=data.get("outline_width", 0.04),
            light_angle=data.get("light_angle", -0.7),
            palette_genes=data.get("palette_genes", []),
        )


# ── Archetype-to-Template Compatibility ───────────────────────────────────────


ARCHETYPE_TEMPLATES: dict[str, list[str]] = {
    Archetype.HERO.value: [
        BodyTemplateName.HUMANOID_STANDARD.value,
        BodyTemplateName.HUMANOID_CHIBI.value,
    ],
    Archetype.VILLAIN.value: [
        BodyTemplateName.HUMANOID_STANDARD.value,
        BodyTemplateName.HUMANOID_TALL.value,
    ],
    Archetype.NPC_WORKER.value: [
        BodyTemplateName.HUMANOID_STANDARD.value,
        BodyTemplateName.HUMANOID_CHIBI.value,
    ],
    Archetype.NPC_MERCHANT.value: [
        BodyTemplateName.HUMANOID_STANDARD.value,
        BodyTemplateName.HUMANOID_TALL.value,
    ],
    Archetype.MONSTER_BASIC.value: [
        BodyTemplateName.CREATURE_ROUND.value,
        BodyTemplateName.HUMANOID_CHIBI.value,
    ],
    Archetype.MONSTER_FLYING.value: [
        BodyTemplateName.CREATURE_ROUND.value,
        BodyTemplateName.HUMANOID_CHIBI.value,
    ],
    Archetype.MONSTER_HEAVY.value: [
        BodyTemplateName.CREATURE_TALL.value,
        BodyTemplateName.HUMANOID_STANDARD.value,
    ],
}


# ── Preset Genotype Factories ─────────────────────────────────────────────────


def mario_genotype() -> CharacterGenotype:
    """Create a Mario-like genotype."""
    g = CharacterGenotype(
        archetype=Archetype.HERO.value,
        body_template=BodyTemplateName.HUMANOID_STANDARD.value,
    )
    g.slots = {
        SlotType.HAT.value: PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_cap", enabled=True,
        ),
        SlotType.FACE_ACCESSORY.value: PartSlotInstance(
            slot_type=SlotType.FACE_ACCESSORY, part_id="face_mustache", enabled=True,
        ),
    }
    g.proportion_modifiers = {
        "head_radius_mod": 0.01,
        "torso_width_mod": -0.01,
        "torso_height_mod": -0.01,
        "arm_thickness_mod": -0.005,
        "leg_thickness_mod": -0.005,
        "hand_radius_mod": 0.0,
        "foot_width_mod": -0.005,
        "foot_height_mod": 0.0,
    }
    g.palette_genes = [
        0.78, 0.04, 0.06,   # skin (warm peach)
        0.52, 0.18, 0.08,   # hat (mario red)
        0.52, 0.18, 0.08,   # shirt (mario red)
        0.42, -0.02, -0.12, # pants (mario blue)
        0.35, 0.05, 0.04,   # shoes (brown)
        0.15, 0.01, 0.01,   # outline (near-black warm)
    ]
    return g


def trickster_genotype() -> CharacterGenotype:
    """Create a Trickster-like genotype."""
    g = CharacterGenotype(
        archetype=Archetype.VILLAIN.value,
        body_template=BodyTemplateName.HUMANOID_STANDARD.value,
    )
    g.slots = {
        SlotType.HAT.value: PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_top", enabled=True,
        ),
        SlotType.FACE_ACCESSORY.value: PartSlotInstance(
            slot_type=SlotType.FACE_ACCESSORY, part_id="face_none", enabled=True,
        ),
    }
    g.proportion_modifiers = {
        "head_radius_mod": -0.01,
        "torso_width_mod": -0.03,
        "torso_height_mod": 0.01,
        "arm_thickness_mod": -0.01,
        "leg_thickness_mod": -0.01,
        "hand_radius_mod": -0.005,
        "foot_width_mod": -0.01,
        "foot_height_mod": -0.005,
    }
    g.palette_genes = [
        0.70, 0.01, 0.02,   # skin (pale)
        0.28, 0.04, -0.06,  # hat (dark purple)
        0.28, 0.04, -0.06,  # suit (dark purple)
        0.22, 0.03, -0.04,  # pants (darker purple)
        0.16, 0.01, 0.00,   # shoes (near-black)
        0.10, 0.00, -0.01,  # outline (deep dark)
    ]
    return g


def simple_enemy_genotype() -> CharacterGenotype:
    """Create a simple enemy (Goomba-like) genotype."""
    g = CharacterGenotype(
        archetype=Archetype.MONSTER_BASIC.value,
        body_template=BodyTemplateName.CREATURE_ROUND.value,
    )
    g.slots = {
        SlotType.HAT.value: PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_none", enabled=True,
        ),
        SlotType.FACE_ACCESSORY.value: PartSlotInstance(
            slot_type=SlotType.FACE_ACCESSORY, part_id="eyes_wide", enabled=True,
        ),
    }
    g.palette_genes = [
        0.58, 0.06, 0.08,   # body (tan)
        0.42, 0.05, 0.06,   # dark accent
        0.58, 0.06, 0.08,   # same as body
        0.50, 0.05, 0.06,   # legs
        0.35, 0.04, 0.04,   # feet
        0.18, 0.02, 0.01,   # outline
    ]
    return g


def flying_enemy_genotype() -> CharacterGenotype:
    """Create a flying enemy genotype."""
    g = CharacterGenotype(
        archetype=Archetype.MONSTER_FLYING.value,
        body_template=BodyTemplateName.CREATURE_ROUND.value,
    )
    g.slots = {
        SlotType.HAT.value: PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_none", enabled=True,
        ),
        SlotType.FACE_ACCESSORY.value: PartSlotInstance(
            slot_type=SlotType.FACE_ACCESSORY, part_id="eyes_wide", enabled=True,
        ),
    }
    g.proportion_modifiers = {
        "head_radius_mod": -0.09,
        "torso_width_mod": -0.10,
        "torso_height_mod": 0.02,
        "arm_thickness_mod": 0.0,
        "leg_thickness_mod": -0.02,
        "hand_radius_mod": 0.0,
        "foot_width_mod": -0.03,
        "foot_height_mod": -0.015,
    }
    g.palette_genes = [
        0.68, -0.03, -0.04, # body (light blue-gray)
        0.55, -0.02, -0.04, # accent
        0.68, -0.03, -0.04, # same
        0.60, -0.02, -0.03, # lower body
        0.48, -0.02, -0.03, # feet
        0.18, -0.01, -0.02, # outline
    ]
    return g


def bouncing_enemy_genotype() -> CharacterGenotype:
    """Create a bouncing enemy genotype."""
    g = CharacterGenotype(
        archetype=Archetype.MONSTER_BASIC.value,
        body_template=BodyTemplateName.CREATURE_ROUND.value,
    )
    g.slots = {
        SlotType.HAT.value: PartSlotInstance(
            slot_type=SlotType.HAT, part_id="hat_none", enabled=True,
        ),
        SlotType.FACE_ACCESSORY.value: PartSlotInstance(
            slot_type=SlotType.FACE_ACCESSORY, part_id="eyes_dot", enabled=True,
        ),
    }
    g.proportion_modifiers = {
        "head_radius_mod": -0.08,
        "torso_width_mod": -0.04,
        "torso_height_mod": 0.04,
        "arm_thickness_mod": 0.005,
        "leg_thickness_mod": 0.01,
        "hand_radius_mod": 0.005,
        "foot_width_mod": 0.01,
        "foot_height_mod": 0.0,
    }
    g.palette_genes = [
        0.62, -0.10, 0.08,  # body (green)
        0.48, -0.08, 0.06,  # dark green
        0.62, -0.10, 0.08,  # same
        0.55, -0.09, 0.07,  # legs
        0.40, -0.07, 0.05,  # feet
        0.16, -0.03, 0.01,  # outline
    ]
    return g


GENOTYPE_PRESETS: dict[str, callable] = {
    "mario": mario_genotype,
    "trickster": trickster_genotype,
    "simple_enemy": simple_enemy_genotype,
    "flying_enemy": flying_enemy_genotype,
    "bouncing_enemy": bouncing_enemy_genotype,
}


# ── Semantic Mutation Operators ───────────────────────────────────────────────


def mutate_genotype(
    genotype: CharacterGenotype,
    rng: np.random.Generator,
    strength: float = 0.18,
) -> CharacterGenotype:
    """Apply semantic mutation to a character genotype.

    Three-layer mutation strategy:
    1. Structural mutation: swap archetype, body template, or parts (rare)
    2. Proportion mutation: jitter continuous body parameters (common)
    3. Palette mutation: jitter color genes (common)

    The strength parameter controls the overall mutation intensity.
    Higher strength = more likely to make structural changes.
    """
    g = copy.deepcopy(genotype)
    effective_strength = max(strength, 0.05)

    # ── Layer 1: Structural mutations (rare but impactful) ──

    # Archetype mutation (very rare — changes character identity)
    if rng.random() < 0.08 * effective_strength:
        archetypes = list(Archetype)
        g.archetype = str(rng.choice([a.value for a in archetypes]))
        # When archetype changes, ensure body template is compatible
        compatible = ARCHETYPE_TEMPLATES.get(g.archetype, [])
        if compatible and g.body_template not in compatible:
            g.body_template = str(rng.choice(compatible))
        # Re-initialize slots for new archetype
        _reinitialize_slots(g, rng)

    # Body template mutation (rare — changes proportions significantly)
    elif rng.random() < 0.12 * effective_strength:
        compatible = ARCHETYPE_TEMPLATES.get(g.archetype, list(BODY_TEMPLATES.keys()))
        if compatible:
            g.body_template = str(rng.choice(compatible))

    # Part slot mutation (moderate — swaps equipment)
    for slot_key, slot_inst in list(g.slots.items()):
        if rng.random() < 0.20 * effective_strength:
            available = get_parts_for_slot(slot_inst.slot_type, g.archetype)
            if available:
                new_part = rng.choice(available)
                slot_inst.part_id = new_part.part_id

    # ── Layer 2: Proportion mutations (common) ──

    for key in g.proportion_modifiers:
        if rng.random() < 0.70:
            current = g.proportion_modifiers[key]
            noise = float(rng.normal(0.0, 0.03 * effective_strength))
            g.proportion_modifiers[key] = float(np.clip(current + noise, -0.12, 0.12))

    # Outline and light
    g.outline_width = float(np.clip(
        g.outline_width + rng.normal(0.0, 0.015 * effective_strength),
        0.02, 0.07,
    ))
    g.light_angle = float(np.clip(
        g.light_angle + rng.normal(0.0, 0.30 * effective_strength),
        -1.5, 1.5,
    ))

    # ── Layer 3: Palette mutations (common) ──

    if len(g.palette_genes) >= 18:
        for i in range(0, len(g.palette_genes), 3):
            # L channel
            g.palette_genes[i] = float(np.clip(
                g.palette_genes[i] + rng.normal(0.0, 0.035 * effective_strength),
                0.10, 0.92,
            ))
            # a, b channels
            g.palette_genes[i + 1] = float(np.clip(
                g.palette_genes[i + 1] + rng.normal(0.0, 0.025 * effective_strength),
                -0.25, 0.25,
            ))
            g.palette_genes[i + 2] = float(np.clip(
                g.palette_genes[i + 2] + rng.normal(0.0, 0.025 * effective_strength),
                -0.25, 0.25,
            ))

        # Enforce outline darkness
        if len(g.palette_genes) >= 18:
            g.palette_genes[15] = min(g.palette_genes[15], 0.22)
            g.palette_genes[16] *= 0.75
            g.palette_genes[17] *= 0.75

        # Enforce skin lightness
        g.palette_genes[0] = float(np.clip(g.palette_genes[0], 0.55, 0.88))

    return g


def _reinitialize_slots(genotype: CharacterGenotype, rng: np.random.Generator) -> None:
    """Re-initialize slots when archetype changes."""
    template = BODY_TEMPLATES.get(genotype.body_template)
    if template is None:
        return

    new_slots = {}
    for slot_type in template.available_slots:
        available = get_parts_for_slot(slot_type, genotype.archetype)
        if available:
            chosen = rng.choice(available)
            new_slots[slot_type.value] = PartSlotInstance(
                slot_type=slot_type,
                part_id=chosen.part_id,
                enabled=True,
            )
    genotype.slots = new_slots


def crossover_genotypes(
    parent_a: CharacterGenotype,
    parent_b: CharacterGenotype,
    rng: np.random.Generator,
) -> CharacterGenotype:
    """Create a child genotype by crossing two parents.

    Uses uniform crossover for continuous genes and single-parent
    selection for structural genes.
    """
    child = copy.deepcopy(parent_a)

    # Structural: pick from one parent
    if rng.random() < 0.5:
        child.archetype = parent_b.archetype
        child.body_template = parent_b.body_template

    # Slots: mix from both parents
    for slot_key in set(list(parent_a.slots.keys()) + list(parent_b.slots.keys())):
        if rng.random() < 0.5 and slot_key in parent_b.slots:
            child.slots[slot_key] = copy.deepcopy(parent_b.slots[slot_key])

    # Proportions: uniform crossover
    for key in child.proportion_modifiers:
        if key in parent_b.proportion_modifiers and rng.random() < 0.5:
            child.proportion_modifiers[key] = parent_b.proportion_modifiers[key]

    # Style: mix
    if rng.random() < 0.5:
        child.outline_width = parent_b.outline_width
    if rng.random() < 0.5:
        child.light_angle = parent_b.light_angle

    # Palette: per-color crossover
    if len(parent_b.palette_genes) >= 18 and len(child.palette_genes) >= 18:
        for i in range(0, min(len(child.palette_genes), len(parent_b.palette_genes)), 3):
            if rng.random() < 0.5:
                child.palette_genes[i] = parent_b.palette_genes[i]
                child.palette_genes[i + 1] = parent_b.palette_genes[i + 1]
                child.palette_genes[i + 2] = parent_b.palette_genes[i + 2]

    return child
