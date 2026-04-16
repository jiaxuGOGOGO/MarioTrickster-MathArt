"""Programmatic 2D skeletal animation module."""
from .skeleton import Skeleton, Bone, Joint
from .curves import ease_in_out, spring, sine_wave, bezier_curve
from .presets import idle_animation, run_animation, jump_animation, fall_animation, hit_animation
from .renderer import render_skeleton_sheet
from .parts import CharacterStyle, BodyPart, assemble_character
from .character_renderer import render_character_frame, render_character_sheet
from .character_presets import get_preset, CHARACTER_PRESETS
from .principles import (
    PrincipledAnimation, AnimationKeyframe,
    SquashStretch, Anticipation, FollowThrough, ArcMotion,
    SecondaryAction, Exaggeration,
    EASING_FUNCTIONS, ANIMATION_PRESETS,
    create_jump_animation, create_walk_cycle, create_idle_breathe,
    create_attack_swing, create_death_animation,
)
# SESSION-019: Export new animation modules
from .particles import ParticleSystem, ParticleConfig
from .cage_deform import CageDeformer, CagePreset, CageAnimation, CageKeyframe
# SESSION-028: Physics-guided animation (PhysDiff-inspired)
from .physics_projector import (
    AnglePoseProjector, PositionPhysicsProjector,
    JointPhysicsConfig, CognitiveMotionConfig,
    DEFAULT_JOINT_PHYSICS, PENNER_EASING_FUNCTIONS,
    compute_physics_penalty,
    # SESSION-028-SUPP: PhysDiff foot contact & skating cleanup
    ContactDetector, ContactState, ConstraintBlender,
    FootLockingConstraint, PhysDiffProjectionScheduler,
)
# SESSION-027: Semantic genotype system
from .genotype import (
    CharacterGenotype, PartSlotInstance, PartDefinition, BodyTemplate,
    Archetype, BodyTemplateName, SlotType,
    BODY_TEMPLATES, PART_REGISTRY, ARCHETYPE_TEMPLATES, GENOTYPE_PRESETS,
    mutate_genotype, crossover_genotypes, get_parts_for_slot,
)
# SESSION-029: Biomechanics engine (ZMP/CoM, IPM, Skating Cleanup, FABRIK Gait)
from .biomechanics import (
    ZMPAnalyzer, ZMPResult, InvertedPendulumModel, IPMState,
    SkatingCleanupCalculus, SkatingCleanupState,
    FABRIKGaitGenerator, GaitPhase,
    BiomechanicsProjector,
    compute_biomechanics_penalty,
    DEFAULT_JOINT_MASSES,
)
# SESSION-030: Physics-based character animation (PD Controller, MuJoCo, RL, ASE)
from .pd_controller import (
    PDController, PDJointConfig, PDControllerConfig,
    PDSimulationState, DeepMimicReward,
    HumanoidPDPreset,
)
from .mujoco_bridge import (
    PhysicsWorld, RigidBody, ContactMaterial,
    ContactSolver, ContactResult, GroundPlane,
    create_humanoid_world, create_contact_material_library,
)
from .rl_locomotion import (
    LocomotionEnv, LocomotionConfig, GaitType,
    PPOTrainer, ReferenceMotionLibrary,
    LocomotionPolicy, PPOConfig,
)
from .skill_embeddings import (
    ASEFramework, SkillEncoder, MotionDiscriminator,
    LowLevelController, HighLevelController,
    SkillLibrary, SkillType, SkillEntry,
)
from .physics_genotype import (
    PhysicsGenotype, LocomotionGenotype,
    create_physics_genotype, create_locomotion_genotype,
    decode_pd_controller, decode_locomotion_config, decode_contact_material,
    mutate_physics_genotype, mutate_locomotion_genotype,
    crossover_physics_genotype, crossover_locomotion_genotype,
    evaluate_physics_fitness,
)

__all__ = [
    "Skeleton", "Bone", "Joint",
    "ease_in_out", "spring", "sine_wave", "bezier_curve",
    "idle_animation", "run_animation", "jump_animation",
    "fall_animation", "hit_animation",
    "render_skeleton_sheet",
    "CharacterStyle", "BodyPart", "assemble_character",
    "render_character_frame", "render_character_sheet",
    "get_preset", "CHARACTER_PRESETS",
    "PrincipledAnimation", "AnimationKeyframe",
    "SquashStretch", "Anticipation", "FollowThrough", "ArcMotion",
    "SecondaryAction", "Exaggeration",
    "EASING_FUNCTIONS", "ANIMATION_PRESETS",
    "create_jump_animation", "create_walk_cycle", "create_idle_breathe",
    "create_attack_swing", "create_death_animation",
    # SESSION-019: New animation modules
    "ParticleSystem", "ParticleConfig",
    "CageDeformer", "CagePreset", "CageAnimation", "CageKeyframe",
    # SESSION-027: Semantic genotype
    "CharacterGenotype", "PartSlotInstance", "PartDefinition", "BodyTemplate",
    "Archetype", "BodyTemplateName", "SlotType",
    "BODY_TEMPLATES", "PART_REGISTRY", "ARCHETYPE_TEMPLATES", "GENOTYPE_PRESETS",
    "mutate_genotype", "crossover_genotypes", "get_parts_for_slot",
    # SESSION-028: Physics-guided animation
    "AnglePoseProjector", "PositionPhysicsProjector",
    "JointPhysicsConfig", "CognitiveMotionConfig",
    "DEFAULT_JOINT_PHYSICS", "PENNER_EASING_FUNCTIONS",
    "compute_physics_penalty",
    # SESSION-028-SUPP: PhysDiff foot contact & skating cleanup
    "ContactDetector", "ContactState", "ConstraintBlender",
    "FootLockingConstraint", "PhysDiffProjectionScheduler",
    # SESSION-029: Biomechanics engine
    "ZMPAnalyzer", "ZMPResult", "InvertedPendulumModel", "IPMState",
    "SkatingCleanupCalculus", "SkatingCleanupState",
    "FABRIKGaitGenerator", "GaitPhase",
    "BiomechanicsProjector",
    "compute_biomechanics_penalty",
    "DEFAULT_JOINT_MASSES",
    # SESSION-030: Physics-based character animation
    "PDController", "PDJointConfig", "PDControllerConfig",
    "PDSimulationState", "DeepMimicReward", "HumanoidPDPreset",
    "PhysicsWorld", "RigidBody", "ContactMaterial",
    "ContactSolver", "ContactResult", "GroundPlane",
    "create_humanoid_world", "create_contact_material_library",
    "LocomotionEnv", "LocomotionConfig", "GaitType",
    "PPOTrainer", "ReferenceMotionLibrary",
    "LocomotionPolicy", "PPOConfig",
    "ASEFramework", "SkillEncoder", "MotionDiscriminator",
    "LowLevelController", "HighLevelController",
    "SkillLibrary", "SkillType", "SkillEntry",
    "PhysicsGenotype", "LocomotionGenotype",
    "create_physics_genotype", "create_locomotion_genotype",
    "decode_pd_controller", "decode_locomotion_config", "decode_contact_material",
    "mutate_physics_genotype", "mutate_locomotion_genotype",
    "crossover_physics_genotype", "crossover_locomotion_genotype",
    "evaluate_physics_fitness",
]
