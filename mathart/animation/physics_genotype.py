"""Physics-Aware Genotype Extension — Evolvable physics and locomotion parameters.

SESSION-030: Extends the CharacterGenotype system (SESSION-027) with physics-aware
genes that allow the evolutionary algorithm to optimize PD controller gains, gait
parameters, skill weights, and contact material properties alongside visual traits.

This is the bridge between the existing evolution system (inner_loop + genotype) and
the new physics-based animation stack (pd_controller + mujoco_bridge + rl_locomotion
+ skill_embeddings).

Design philosophy:
    - Physics genes are OPTIONAL: a genotype without them still works perfectly
    - Physics genes are EVOLVABLE: mutation and crossover operators included
    - Physics genes DECODE to PD gains, gait configs, and skill latents
    - The existing 3-layer mutation strategy is extended to 5 layers:
        Layer 1: Structural (archetype, body template, parts) — existing
        Layer 2: Proportion (body dimensions) — existing
        Layer 3: Palette (colors) — existing
        Layer 4: Physics (PD gains, contact materials, mass distribution) — NEW
        Layer 5: Locomotion (gait type, step frequency, skill weights) — NEW

Integration with existing systems:
    - CharacterGenotype.physics_genes → PDController gains
    - CharacterGenotype.locomotion_genes → LocomotionConfig + SkillLibrary weights
    - InnerLoop fitness includes physics_penalty from PD stability + contact quality
    - OuterLoop distills physics knowledge into physics_genes constraints

References:
    - SESSION-027: CharacterGenotype, mutate_genotype, crossover_genotypes
    - SESSION-028: AnglePoseProjector, JointPhysicsConfig
    - SESSION-029: BiomechanicsProjector, ZMPAnalyzer
    - SESSION-030: PDController, PhysicsWorld, LocomotionEnv, ASEFramework

Usage::

    from mathart.animation.physics_genotype import (
        PhysicsGenotype, LocomotionGenotype,
        create_physics_genotype, mutate_physics_genotype,
        decode_pd_controller, decode_locomotion_config,
    )

    # Create a physics-aware genotype
    pg = create_physics_genotype(archetype="hero")

    # Decode to PD controller
    pd_ctrl = decode_pd_controller(pg)

    # Mutate
    pg_child = mutate_physics_genotype(pg, rng, strength=0.2)
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

import numpy as np

from .pd_controller import (
    PDController, PDJointConfig, PDControllerConfig,
    HumanoidPDPreset, _DEEPMIMIC_GAINS,
)
from .mujoco_bridge import ContactMaterial
from .rl_locomotion import LocomotionConfig, GaitType
from .skill_embeddings import SkillType


# ── Physics Genotype ─────────────────────────────────────────────────────────


@dataclass
class PhysicsGenotype:
    """Evolvable physics parameters for a character.

    These genes control how the character interacts with the physics world:
    PD controller gains, contact materials, mass distribution, and damping.

    All values are normalized to [0, 1] for uniform mutation, then decoded
    to their actual ranges when creating PD controllers or physics bodies.

    Attributes
    ----------
    pd_stiffness_scale : float
        Global PD proportional gain multiplier [0.2, 3.0].
        Higher = stiffer joints, snappier response.
    pd_damping_scale : float
        Global PD derivative gain multiplier [0.2, 3.0].
        Higher = more damped, less oscillation.
    pd_torque_limit_scale : float
        Global torque limit multiplier [0.3, 2.0].
    mass_distribution : dict[str, float]
        Per-region mass multipliers (normalized).
        Keys: 'upper', 'core', 'lower', 'extremities'.
    contact_friction : float
        Foot-ground friction coefficient [0.3, 1.5].
    contact_stiffness : float
        Contact spring stiffness (normalized) [0.1, 1.0].
    contact_damping : float
        Contact damping (normalized) [0.1, 1.0].
    gravity_compensation : float
        Gravity compensation strength [0.0, 1.0].
    joint_damping_profile : str
        Damping profile: 'uniform', 'top_heavy', 'bottom_heavy', 'balanced'.
    """
    pd_stiffness_scale: float = 1.0
    pd_damping_scale: float = 1.0
    pd_torque_limit_scale: float = 1.0
    mass_distribution: dict[str, float] = field(default_factory=lambda: {
        "upper": 1.0,    # head, neck, shoulders
        "core": 1.0,     # spine, chest, hip
        "lower": 1.0,    # thighs, shins
        "extremities": 1.0,  # hands, feet
    })
    contact_friction: float = 0.8
    contact_stiffness: float = 0.5
    contact_damping: float = 0.5
    gravity_compensation: float = 0.7
    joint_damping_profile: str = "balanced"

    def to_vector(self) -> np.ndarray:
        """Flatten to a numpy vector for batch operations."""
        return np.array([
            self.pd_stiffness_scale,
            self.pd_damping_scale,
            self.pd_torque_limit_scale,
            self.mass_distribution["upper"],
            self.mass_distribution["core"],
            self.mass_distribution["lower"],
            self.mass_distribution["extremities"],
            self.contact_friction,
            self.contact_stiffness,
            self.contact_damping,
            self.gravity_compensation,
        ], dtype=np.float32)

    @classmethod
    def from_vector(cls, v: np.ndarray) -> "PhysicsGenotype":
        return cls(
            pd_stiffness_scale=float(v[0]),
            pd_damping_scale=float(v[1]),
            pd_torque_limit_scale=float(v[2]),
            mass_distribution={
                "upper": float(v[3]),
                "core": float(v[4]),
                "lower": float(v[5]),
                "extremities": float(v[6]),
            },
            contact_friction=float(v[7]),
            contact_stiffness=float(v[8]),
            contact_damping=float(v[9]),
            gravity_compensation=float(v[10]),
        )

    def to_dict(self) -> dict:
        return {
            "pd_stiffness_scale": self.pd_stiffness_scale,
            "pd_damping_scale": self.pd_damping_scale,
            "pd_torque_limit_scale": self.pd_torque_limit_scale,
            "mass_distribution": dict(self.mass_distribution),
            "contact_friction": self.contact_friction,
            "contact_stiffness": self.contact_stiffness,
            "contact_damping": self.contact_damping,
            "gravity_compensation": self.gravity_compensation,
            "joint_damping_profile": self.joint_damping_profile,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PhysicsGenotype":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Locomotion Genotype ──────────────────────────────────────────────────────


@dataclass
class LocomotionGenotype:
    """Evolvable locomotion and skill parameters.

    These genes control how the character moves: gait type, step frequency,
    skill blending weights, and RL training hyperparameters.

    Attributes
    ----------
    gait_type : str
        Primary gait type (walk, run, jump, idle).
    step_frequency : float
        Steps per second [0.5, 3.0].
    stride_length : float
        Stride length multiplier [0.5, 2.0].
    arm_swing_amplitude : float
        Arm counter-swing amplitude [0.0, 1.0].
    hip_rom : float
        Hip range of motion multiplier [0.5, 1.5].
    knee_rom : float
        Knee range of motion multiplier [0.5, 1.5].
    forward_lean : float
        Forward lean angle [0.0, 0.3] radians.
    bounce_amplitude : float
        Vertical bounce during locomotion [0.0, 0.1].
    skill_weights : dict[str, float]
        Blending weights for ASE skill latents.
    reward_weights : dict[str, float]
        RL reward component weights (evolvable!).
    """
    gait_type: str = GaitType.WALK.value
    step_frequency: float = 1.0
    stride_length: float = 1.0
    arm_swing_amplitude: float = 0.5
    hip_rom: float = 1.0
    knee_rom: float = 1.0
    forward_lean: float = 0.05
    bounce_amplitude: float = 0.02
    skill_weights: dict[str, float] = field(default_factory=lambda: {
        "walk": 0.5,
        "run": 0.2,
        "jump": 0.1,
        "idle": 0.15,
        "fall_recover": 0.05,
    })
    reward_weights: dict[str, float] = field(default_factory=lambda: {
        "imitation": 0.5,
        "velocity": 0.2,
        "alive": 0.1,
        "energy": 0.1,
        "smoothness": 0.1,
    })

    def to_dict(self) -> dict:
        return {
            "gait_type": self.gait_type,
            "step_frequency": self.step_frequency,
            "stride_length": self.stride_length,
            "arm_swing_amplitude": self.arm_swing_amplitude,
            "hip_rom": self.hip_rom,
            "knee_rom": self.knee_rom,
            "forward_lean": self.forward_lean,
            "bounce_amplitude": self.bounce_amplitude,
            "skill_weights": dict(self.skill_weights),
            "reward_weights": dict(self.reward_weights),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LocomotionGenotype":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Archetype-Specific Defaults ──────────────────────────────────────────────


_ARCHETYPE_PHYSICS_DEFAULTS: dict[str, PhysicsGenotype] = {
    "hero": PhysicsGenotype(
        pd_stiffness_scale=1.0, pd_damping_scale=1.0,
        contact_friction=0.9, gravity_compensation=0.8,
        joint_damping_profile="balanced",
    ),
    "villain": PhysicsGenotype(
        pd_stiffness_scale=0.8, pd_damping_scale=1.2,
        contact_friction=0.7, gravity_compensation=0.6,
        joint_damping_profile="top_heavy",
    ),
    "monster_heavy": PhysicsGenotype(
        pd_stiffness_scale=1.5, pd_damping_scale=2.0,
        pd_torque_limit_scale=1.8,
        mass_distribution={"upper": 0.8, "core": 1.5, "lower": 1.3, "extremities": 0.7},
        contact_friction=1.2, gravity_compensation=0.9,
        joint_damping_profile="bottom_heavy",
    ),
    "monster_flying": PhysicsGenotype(
        pd_stiffness_scale=0.6, pd_damping_scale=0.5,
        pd_torque_limit_scale=0.5,
        mass_distribution={"upper": 0.6, "core": 0.8, "lower": 0.5, "extremities": 0.4},
        contact_friction=0.5, gravity_compensation=0.3,
        joint_damping_profile="uniform",
    ),
}

_ARCHETYPE_LOCOMOTION_DEFAULTS: dict[str, LocomotionGenotype] = {
    "hero": LocomotionGenotype(
        gait_type="walk", step_frequency=1.0, stride_length=1.0,
        arm_swing_amplitude=0.5, forward_lean=0.05,
    ),
    "villain": LocomotionGenotype(
        gait_type="walk", step_frequency=0.8, stride_length=1.1,
        arm_swing_amplitude=0.3, forward_lean=0.08,
    ),
    "monster_heavy": LocomotionGenotype(
        gait_type="walk", step_frequency=0.6, stride_length=1.3,
        arm_swing_amplitude=0.2, forward_lean=0.1, bounce_amplitude=0.04,
    ),
    "monster_flying": LocomotionGenotype(
        gait_type="idle", step_frequency=2.0, stride_length=0.3,
        arm_swing_amplitude=0.8, forward_lean=0.0, bounce_amplitude=0.06,
    ),
}


# ── Decode Functions ─────────────────────────────────────────────────────────


def decode_pd_controller(
    physics_geno: PhysicsGenotype,
    base_preset: HumanoidPDPreset = HumanoidPDPreset.DEEPMIMIC_STANDARD,
) -> PDController:
    """Decode a PhysicsGenotype into a configured PD controller.

    Applies the genotype's scale factors to the base preset gains.

    Parameters
    ----------
    physics_geno : PhysicsGenotype
        Physics genes to decode.
    base_preset : HumanoidPDPreset
        Base gain preset to scale from.

    Returns
    -------
    PDController
    """
    base_gains = dict(_DEEPMIMIC_GAINS)
    scaled_gains = {}

    # Region mapping for mass-aware scaling
    region_map = {
        "upper": ["head", "neck", "l_shoulder", "r_shoulder"],
        "core": ["spine", "chest", "hip"],
        "lower": ["l_hip", "r_hip", "l_knee", "r_knee"],
        "extremities": ["l_elbow", "r_elbow", "l_hand", "r_hand", "l_foot", "r_foot"],
    }

    # Build reverse mapping: joint → region
    joint_region = {}
    for region, joints in region_map.items():
        for j in joints:
            joint_region[j] = region

    # Damping profile multipliers
    profile_mults = {
        "uniform": {"upper": 1.0, "core": 1.0, "lower": 1.0, "extremities": 1.0},
        "top_heavy": {"upper": 1.3, "core": 1.0, "lower": 0.8, "extremities": 0.7},
        "bottom_heavy": {"upper": 0.7, "core": 1.0, "lower": 1.3, "extremities": 0.8},
        "balanced": {"upper": 0.9, "core": 1.1, "lower": 1.1, "extremities": 0.9},
    }
    profile = profile_mults.get(physics_geno.joint_damping_profile, profile_mults["balanced"])

    for joint_name, base_cfg in base_gains.items():
        region = joint_region.get(joint_name, "core")
        mass_mult = physics_geno.mass_distribution.get(region, 1.0)
        damp_mult = profile.get(region, 1.0)

        scaled_gains[joint_name] = PDJointConfig(
            k_p=base_cfg.k_p * physics_geno.pd_stiffness_scale * mass_mult,
            k_d=base_cfg.k_d * physics_geno.pd_damping_scale * damp_mult,
            max_torque=base_cfg.max_torque * physics_geno.pd_torque_limit_scale,
            inertia=base_cfg.inertia * mass_mult,
        )

    config = PDControllerConfig(
        enable_gravity_compensation=physics_geno.gravity_compensation > 0.3,
        torque_smoothing_alpha=0.1 + 0.3 * (1.0 - physics_geno.pd_damping_scale / 3.0),
    )

    return PDController(joint_configs=scaled_gains, config=config)


def decode_locomotion_config(
    loco_geno: LocomotionGenotype,
) -> LocomotionConfig:
    """Decode a LocomotionGenotype into a LocomotionConfig.

    Parameters
    ----------
    loco_geno : LocomotionGenotype
        Locomotion genes.

    Returns
    -------
    LocomotionConfig
    """
    gait = GaitType.WALK
    for gt in GaitType:
        if gt.value == loco_geno.gait_type:
            gait = gt
            break

    # Map step frequency to target velocity
    target_vel = loco_geno.step_frequency * loco_geno.stride_length

    return LocomotionConfig(
        gait=gait,
        target_velocity=target_vel,
        reward_weights=dict(loco_geno.reward_weights),
    )


def decode_contact_material(
    physics_geno: PhysicsGenotype,
) -> ContactMaterial:
    """Decode a PhysicsGenotype into a ContactMaterial.

    Parameters
    ----------
    physics_geno : PhysicsGenotype
        Physics genes.

    Returns
    -------
    ContactMaterial
    """
    return ContactMaterial(
        friction=physics_geno.contact_friction,
        contact_stiffness=2000.0 + 16000.0 * physics_geno.contact_stiffness,
        contact_damping=50.0 + 450.0 * physics_geno.contact_damping,
    )


# ── Mutation Operators ───────────────────────────────────────────────────────


def mutate_physics_genotype(
    geno: PhysicsGenotype,
    rng: np.random.Generator,
    strength: float = 0.2,
) -> PhysicsGenotype:
    """Mutate physics genes.

    Layer 4 mutation: jitters PD gains, mass distribution, contact properties.
    """
    g = copy.deepcopy(geno)
    s = max(strength, 0.05)

    # PD scales
    if rng.random() < 0.6:
        g.pd_stiffness_scale = float(np.clip(
            g.pd_stiffness_scale + rng.normal(0, 0.15 * s), 0.2, 3.0
        ))
    if rng.random() < 0.6:
        g.pd_damping_scale = float(np.clip(
            g.pd_damping_scale + rng.normal(0, 0.15 * s), 0.2, 3.0
        ))
    if rng.random() < 0.4:
        g.pd_torque_limit_scale = float(np.clip(
            g.pd_torque_limit_scale + rng.normal(0, 0.1 * s), 0.3, 2.0
        ))

    # Mass distribution
    for key in g.mass_distribution:
        if rng.random() < 0.5:
            g.mass_distribution[key] = float(np.clip(
                g.mass_distribution[key] + rng.normal(0, 0.1 * s), 0.3, 2.0
            ))

    # Contact properties
    if rng.random() < 0.5:
        g.contact_friction = float(np.clip(
            g.contact_friction + rng.normal(0, 0.1 * s), 0.3, 1.5
        ))
    if rng.random() < 0.4:
        g.contact_stiffness = float(np.clip(
            g.contact_stiffness + rng.normal(0, 0.08 * s), 0.1, 1.0
        ))
    if rng.random() < 0.4:
        g.contact_damping = float(np.clip(
            g.contact_damping + rng.normal(0, 0.08 * s), 0.1, 1.0
        ))

    # Gravity compensation
    if rng.random() < 0.3:
        g.gravity_compensation = float(np.clip(
            g.gravity_compensation + rng.normal(0, 0.1 * s), 0.0, 1.0
        ))

    # Damping profile (rare structural mutation)
    if rng.random() < 0.1 * s:
        profiles = ["uniform", "top_heavy", "bottom_heavy", "balanced"]
        g.joint_damping_profile = str(rng.choice(profiles))

    return g


def mutate_locomotion_genotype(
    geno: LocomotionGenotype,
    rng: np.random.Generator,
    strength: float = 0.2,
) -> LocomotionGenotype:
    """Mutate locomotion genes.

    Layer 5 mutation: jitters gait parameters, skill weights, reward weights.
    """
    g = copy.deepcopy(geno)
    s = max(strength, 0.05)

    # Gait type (rare structural mutation)
    if rng.random() < 0.08 * s:
        gaits = [gt.value for gt in GaitType]
        g.gait_type = str(rng.choice(gaits))

    # Continuous gait parameters
    if rng.random() < 0.6:
        g.step_frequency = float(np.clip(
            g.step_frequency + rng.normal(0, 0.15 * s), 0.5, 3.0
        ))
    if rng.random() < 0.6:
        g.stride_length = float(np.clip(
            g.stride_length + rng.normal(0, 0.1 * s), 0.5, 2.0
        ))
    if rng.random() < 0.5:
        g.arm_swing_amplitude = float(np.clip(
            g.arm_swing_amplitude + rng.normal(0, 0.08 * s), 0.0, 1.0
        ))
    if rng.random() < 0.5:
        g.hip_rom = float(np.clip(
            g.hip_rom + rng.normal(0, 0.08 * s), 0.5, 1.5
        ))
    if rng.random() < 0.5:
        g.knee_rom = float(np.clip(
            g.knee_rom + rng.normal(0, 0.08 * s), 0.5, 1.5
        ))
    if rng.random() < 0.4:
        g.forward_lean = float(np.clip(
            g.forward_lean + rng.normal(0, 0.03 * s), 0.0, 0.3
        ))
    if rng.random() < 0.4:
        g.bounce_amplitude = float(np.clip(
            g.bounce_amplitude + rng.normal(0, 0.01 * s), 0.0, 0.1
        ))

    # Skill weights (Dirichlet-like mutation)
    for key in g.skill_weights:
        if rng.random() < 0.4:
            g.skill_weights[key] = float(np.clip(
                g.skill_weights[key] + rng.normal(0, 0.05 * s), 0.0, 1.0
            ))
    # Normalize skill weights
    total = sum(g.skill_weights.values())
    if total > 0:
        g.skill_weights = {k: v / total for k, v in g.skill_weights.items()}

    # Reward weights (Dirichlet-like mutation)
    for key in g.reward_weights:
        if rng.random() < 0.3:
            g.reward_weights[key] = float(np.clip(
                g.reward_weights[key] + rng.normal(0, 0.04 * s), 0.01, 1.0
            ))
    total = sum(g.reward_weights.values())
    if total > 0:
        g.reward_weights = {k: v / total for k, v in g.reward_weights.items()}

    return g


def crossover_physics_genotype(
    parent_a: PhysicsGenotype,
    parent_b: PhysicsGenotype,
    rng: np.random.Generator,
) -> PhysicsGenotype:
    """Crossover two physics genotypes."""
    child = copy.deepcopy(parent_a)

    if rng.random() < 0.5:
        child.pd_stiffness_scale = parent_b.pd_stiffness_scale
    if rng.random() < 0.5:
        child.pd_damping_scale = parent_b.pd_damping_scale
    if rng.random() < 0.5:
        child.pd_torque_limit_scale = parent_b.pd_torque_limit_scale

    for key in child.mass_distribution:
        if rng.random() < 0.5 and key in parent_b.mass_distribution:
            child.mass_distribution[key] = parent_b.mass_distribution[key]

    if rng.random() < 0.5:
        child.contact_friction = parent_b.contact_friction
    if rng.random() < 0.5:
        child.joint_damping_profile = parent_b.joint_damping_profile

    return child


def crossover_locomotion_genotype(
    parent_a: LocomotionGenotype,
    parent_b: LocomotionGenotype,
    rng: np.random.Generator,
) -> LocomotionGenotype:
    """Crossover two locomotion genotypes."""
    child = copy.deepcopy(parent_a)

    if rng.random() < 0.5:
        child.gait_type = parent_b.gait_type
    if rng.random() < 0.5:
        child.step_frequency = parent_b.step_frequency
    if rng.random() < 0.5:
        child.stride_length = parent_b.stride_length
    if rng.random() < 0.5:
        child.arm_swing_amplitude = parent_b.arm_swing_amplitude

    for key in child.skill_weights:
        if rng.random() < 0.5 and key in parent_b.skill_weights:
            child.skill_weights[key] = parent_b.skill_weights[key]

    return child


# ── Factory Functions ────────────────────────────────────────────────────────


def create_physics_genotype(archetype: str = "hero") -> PhysicsGenotype:
    """Create a PhysicsGenotype with archetype-appropriate defaults."""
    return copy.deepcopy(
        _ARCHETYPE_PHYSICS_DEFAULTS.get(archetype, PhysicsGenotype())
    )


def create_locomotion_genotype(archetype: str = "hero") -> LocomotionGenotype:
    """Create a LocomotionGenotype with archetype-appropriate defaults."""
    return copy.deepcopy(
        _ARCHETYPE_LOCOMOTION_DEFAULTS.get(archetype, LocomotionGenotype())
    )


# ── Physics Fitness Evaluation ───────────────────────────────────────────────


def evaluate_physics_fitness(
    physics_geno: PhysicsGenotype,
    loco_geno: LocomotionGenotype,
    n_eval_steps: int = 100,
) -> dict[str, float]:
    """Evaluate the physics fitness of a genotype pair.

    Runs a short simulation and computes quality metrics:
    - stability: PD controller damping ratio analysis
    - energy_efficiency: total torque / distance traveled
    - imitation_score: DeepMimic reward against reference
    - anatomical_score: VPoser-like pose prior plausibility on simulated poses
    - motion_match_score: low-dimensional 2D motion retrieval consistency

    The added SESSION-031 metrics connect Layer 3 physics evolution to the new
    distilled human-math stack without requiring a full 3D mesh pipeline.
    """
    from .pd_controller import PDSimulationState, DeepMimicReward
    from .rl_locomotion import ReferenceMotionLibrary
    from .human_math import MotionMatcher2D, VPoserDistilledPrior
    from .skeleton import Skeleton

    # Decode genotypes
    pd_ctrl = decode_pd_controller(physics_geno)
    loco_cfg = decode_locomotion_config(loco_geno)

    # Stability analysis
    stability_report = pd_ctrl.stability_report()
    n_stable = sum(1 for j in stability_report.values() if j["is_stable"])
    n_critical = sum(1 for j in stability_report.values() if j["is_critically_damped"])
    stability_score = n_stable / max(len(stability_report), 1)
    damping_score = n_critical / max(len(stability_report), 1)

    # Reference motions and distilled human-math helpers
    ref_lib = ReferenceMotionLibrary()
    reward_fn = DeepMimicReward()
    ref_motion = ref_lib.get_motion(loco_geno.gait_type) or ref_lib.get_motion("walk")
    skeleton = Skeleton.create_humanoid(head_units=3.0)
    pose_prior = VPoserDistilledPrior()

    motion_clips: dict[str, list[dict[str, float]]] = {}
    trajectory_hints: dict[str, Sequence[float]] = {}
    gait_defaults = {
        "walk": (0.8, 0.0, 1.0, 0.0),
        "run": (1.6, 0.0, 1.0, 0.0),
        "jump": (0.6, 0.8, 1.0, 0.0),
        "idle": (0.0, 0.0, 1.0, 0.0),
        "fall": (0.1, -0.8, 1.0, 0.0),
    }
    for gait in ["walk", "run", "jump", "idle", "fall"]:
        clip = ref_lib.get_motion(gait)
        if clip:
            motion_clips[gait] = clip
            trajectory_hints[gait] = gait_defaults.get(gait, (loco_cfg.target_velocity, 0.0, 1.0, 0.0))
    matcher = MotionMatcher2D()
    matcher.build_from_clips(motion_clips, trajectory_hints=trajectory_hints)

    total_reward = 0.0
    total_torque = 0.0
    anatomy_total = 0.0
    motion_total = 0.0
    prev_sim_pose = None
    desired_traj = gait_defaults.get(loco_geno.gait_type, (loco_cfg.target_velocity, 0.0, 1.0, 0.0))

    initial_pose = ref_motion[0] if ref_motion else {}
    state = PDSimulationState.from_pose(initial_pose)
    effective_steps = min(n_eval_steps, len(ref_motion) if ref_motion else n_eval_steps)

    for step in range(effective_steps):
        phase = step / max(effective_steps, 1)
        ref_frame = ref_lib.sample_frame(loco_geno.gait_type, phase)
        if not ref_frame:
            ref_frame = ref_lib.sample_frame("walk", phase)
        if not ref_frame:
            ref_frame = initial_pose

        # Simulate using the target motion frame.
        state = pd_ctrl.step_simulation(ref_frame, state)

        # DeepMimic-style reward.
        reward_result = reward_fn.compute(
            ref_angles=ref_frame,
            sim_angles=state.angles,
            sim_velocities=state.velocities,
        )
        total_reward += reward_result["total"]
        total_torque += sum(abs(t) for t in state.torques.values())

        # VPoser-style anatomical prior score.
        projected = pose_prior.project_pose(state.angles, skeleton=skeleton)
        anatomy_total += pose_prior.score_pose(projected, skeleton=skeleton).total

        # Motion matching consistency in compact feature space.
        match = matcher.query(projected, prev_pose=prev_sim_pose, desired_trajectory=desired_traj)
        motion_total += match.similarity
        prev_sim_pose = dict(projected)

    avg_reward = total_reward / max(effective_steps, 1)
    energy_efficiency = math.exp(-0.0001 * total_torque / max(effective_steps, 1))
    anatomical_score = anatomy_total / max(effective_steps, 1)
    motion_match_score = motion_total / max(effective_steps, 1)

    overall = float(np.clip(
        0.20 * stability_score
        + 0.12 * damping_score
        + 0.28 * avg_reward
        + 0.15 * energy_efficiency
        + 0.13 * anatomical_score
        + 0.12 * motion_match_score,
        0.0,
        1.0,
    ))

    return {
        "stability": stability_score,
        "damping_quality": damping_score,
        "imitation_score": avg_reward,
        "energy_efficiency": energy_efficiency,
        "anatomical_score": anatomical_score,
        "motion_match_score": motion_match_score,
        "overall": overall,
    }
