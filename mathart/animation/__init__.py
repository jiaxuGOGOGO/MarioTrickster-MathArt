"""Programmatic 2D skeletal animation module."""
from .skeleton import Skeleton, Bone, Joint
from .curves import ease_in_out, spring, sine_wave, bezier_curve
from .presets import idle_animation, run_animation, walk_animation, jump_animation, fall_animation, hit_animation
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
# SESSION-033: Phase-Driven Animation Control (PFNN/DeepPhase/Animator's Survival Kit)
from .phase_driven import (
    PhaseDrivenAnimator, PhaseVariable, GaitMode,
    PhaseInterpolator, PhaseChannel, KeyPose,
    phase_driven_walk, phase_driven_run,
    phase_driven_jump, phase_driven_fall, phase_driven_hit,
    phase_driven_walk_frame, phase_driven_run_frame,
    phase_driven_jump_frame, phase_driven_fall_frame, phase_driven_hit_frame,
    jump_distance_phase, fall_distance_phase, hit_recovery_phase,
    TransientPhaseVariable, critically_damped_hit_phase,
    extract_phase_parameters, create_phase_channel_from_signal,
    WALK_KEY_POSES, RUN_KEY_POSES, WALK_CHANNELS, RUN_CHANNELS,
)
from .unified_motion import (
    PhaseState,
    MotionRootTransform, MotionContactState,
    UnifiedMotionFrame, UnifiedMotionClip,
    MotionPipelineAuditEntry, MotionPipelineResult, MotionPipelineNode,
    pose_to_umr, umr_to_pose, infer_contact_tags, run_motion_pipeline,
)
# SESSION-031: Distilled human math stack (SMPL/VPoser/DQ/Motion Matching)
from .human_math import (
    SMPLShapeLatent, DistilledSMPLBodyModel,
    PosePriorScore, VPoserDistilledPrior,
    DualQuaternion,
    MotionFeatureSchema2D, MotionMatchResult, MotionMatcher2D,
    DistilledHumanMathRuntime,
)
# SESSION-034: Industrial Motion Matching Evaluator (Clavet GDC 2016)
from .motion_matching_evaluator import (
    IndustrialFeatureSchema, FeatureNormalizer,
    MotionFeatureExtractor, MatchResult, SequenceEvaluation,
    MotionMatchingEvaluator, create_evaluator_with_defaults,
)
# SESSION-034: Industrial Renderer (Dead Cells GDC 2018 + Guilty Gear Xrd GDC 2015)
from .industrial_renderer import (
    AnimationPhaseType, HoldFrameConfig, HOLD_FRAME_DEFAULTS,
    GuiltyGearFrameScheduler, IndustrialRenderAuxiliaryResult,
    render_character_frame_industrial, render_character_maps_industrial,
    render_character_sheet_industrial,
)
# SESSION-044: Analytical SDF auxiliary-map baking
from .sdf_aux_maps import (
    SDFSamplingGrid, SDFBakeConfig, SDFAuxiliaryMaps,
    sample_sdf_grid, compute_sdf_gradients, compute_depth_map,
    compute_normal_vectors, encode_normal_map, encode_depth_map,
    encode_mask, bake_sdf_auxiliary_maps,
)
# SESSION-039: Inertialized Transition Synthesis (Bollo GDC 2018 / Holden Dead Blending)
from .transition_synthesizer import (
    TransitionStrategy,
    InertializationChannel, DeadBlendingChannel,
    TransitionQualityMetrics, TransitionSynthesizer,
    TransitionPipelineNode,
    create_transition_synthesizer, inertialize_transition,
)
# SESSION-039: Runtime Motion Matching Query (Clavet GDC 2016 / Holden 2020)
from .runtime_motion_query import (
    RuntimeFeatureWeights, RuntimeFeatureVector,
    extract_runtime_features, EntryFrameResult,
    RuntimeMotionDatabase, RuntimeMotionQuery,
    PlaybackState, MotionMatchingRuntime,
    create_runtime_database, create_motion_matching_runtime,
)
# SESSION-040: Phase-Driven Idle (UMR Contract Enforcement)
from .phase_driven_idle import (
    phase_driven_idle, phase_driven_idle_frame,
)
# SESSION-045: Motion Vector Baker (Gap C3 — Neural Rendering Bridge)
from .motion_vector_baker import (
    MotionVectorField, MotionVectorSequence,
    compute_joint_displacement, compute_pixel_motion_field,
    encode_motion_vector_rgb, encode_motion_vector_hsv, encode_motion_vector_raw,
    bake_motion_vector_sequence, export_ebsynth_project,
    compute_temporal_consistency_score,
)
# SESSION-046: Stable Fluids VFX (Gap C2 — Grid-Based Vector Fields)
from .fluid_vfx import (
    FluidGridConfig, FluidImpulse, FluidFrameDiagnostics, FluidParticle,
    FluidVFXConfig, FluidGrid2D, FluidDrivenVFXSystem,
    resize_mask_to_grid, default_character_obstacle_mask,
)
# SESSION-047: Jakobsen-style secondary chains (Gap B1 — lightweight rigid-soft coupling)
from .jakobsen_chain import (
    BodyCollisionCircle,
    SecondaryChainConfig,
    SecondaryChainDiagnostics,
    SecondaryChainSnapshot,
    JakobsenSecondaryChain,
    SecondaryChainProjector,
    create_default_secondary_chain_configs,
)

__all__ = [
    "Skeleton", "Bone", "Joint",
    "ease_in_out", "spring", "sine_wave", "bezier_curve",
    "idle_animation", "run_animation", "walk_animation", "jump_animation",
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
    # SESSION-033: Phase-Driven Animation Control
    "PhaseDrivenAnimator", "PhaseVariable", "GaitMode",
    "PhaseInterpolator", "PhaseChannel", "KeyPose",
    "phase_driven_walk", "phase_driven_run",
    "phase_driven_jump", "phase_driven_fall", "phase_driven_hit",
    "phase_driven_walk_frame", "phase_driven_run_frame",
    "phase_driven_jump_frame", "phase_driven_fall_frame", "phase_driven_hit_frame",
    "jump_distance_phase", "fall_distance_phase", "hit_recovery_phase",
    "TransientPhaseVariable", "critically_damped_hit_phase",
    "extract_phase_parameters", "create_phase_channel_from_signal",
    "WALK_KEY_POSES", "RUN_KEY_POSES", "WALK_CHANNELS", "RUN_CHANNELS",
    # SESSION-036: Unified Motion Representation (UMR)
    "PhaseState",
    "MotionRootTransform", "MotionContactState",
    "UnifiedMotionFrame", "UnifiedMotionClip",
    "MotionPipelineAuditEntry", "MotionPipelineResult", "MotionPipelineNode",
    "pose_to_umr", "umr_to_pose", "infer_contact_tags", "run_motion_pipeline",
    # SESSION-031: Distilled human math stack
    "SMPLShapeLatent", "DistilledSMPLBodyModel",
    "PosePriorScore", "VPoserDistilledPrior",
    "DualQuaternion",
    "MotionFeatureSchema2D", "MotionMatchResult", "MotionMatcher2D",
    "DistilledHumanMathRuntime",
    # SESSION-034: Industrial Motion Matching Evaluator
    "IndustrialFeatureSchema", "FeatureNormalizer",
    "MotionFeatureExtractor", "MatchResult", "SequenceEvaluation",
    "MotionMatchingEvaluator", "create_evaluator_with_defaults",
    # SESSION-034: Industrial Renderer (Dead Cells + Guilty Gear Xrd)
    "AnimationPhaseType", "HoldFrameConfig", "HOLD_FRAME_DEFAULTS",
    "GuiltyGearFrameScheduler", "IndustrialRenderAuxiliaryResult",
    "render_character_frame_industrial", "render_character_maps_industrial",
    "render_character_sheet_industrial",
    # SESSION-044: Analytical SDF auxiliary-map baking
    "SDFSamplingGrid", "SDFBakeConfig", "SDFAuxiliaryMaps",
    "sample_sdf_grid", "compute_sdf_gradients", "compute_depth_map",
    "compute_normal_vectors", "encode_normal_map", "encode_depth_map",
    "encode_mask", "bake_sdf_auxiliary_maps",
    # SESSION-039: Inertialized Transition Synthesis
    "TransitionStrategy",
    "InertializationChannel", "DeadBlendingChannel",
    "TransitionQualityMetrics", "TransitionSynthesizer",
    "TransitionPipelineNode",
    "create_transition_synthesizer", "inertialize_transition",
    # SESSION-039: Runtime Motion Matching Query
    "RuntimeFeatureWeights", "RuntimeFeatureVector",
    "extract_runtime_features", "EntryFrameResult",
    "RuntimeMotionDatabase", "RuntimeMotionQuery",
    "PlaybackState", "MotionMatchingRuntime",
    "create_runtime_database", "create_motion_matching_runtime",
    # SESSION-040: Phase-Driven Idle (UMR Contract Enforcement)
    "phase_driven_idle", "phase_driven_idle_frame",
    # SESSION-045: Motion Vector Baker (Gap C3 — Neural Rendering Bridge)
    "MotionVectorField", "MotionVectorSequence",
    "compute_joint_displacement", "compute_pixel_motion_field",
    "encode_motion_vector_rgb", "encode_motion_vector_hsv", "encode_motion_vector_raw",
    "bake_motion_vector_sequence", "export_ebsynth_project",
    "compute_temporal_consistency_score",
    # SESSION-046: Stable Fluids VFX (Gap C2 — Grid-Based Vector Fields)
    "FluidGridConfig", "FluidImpulse", "FluidFrameDiagnostics", "FluidParticle",
    "FluidVFXConfig", "FluidGrid2D", "FluidDrivenVFXSystem",
    "resize_mask_to_grid", "default_character_obstacle_mask",
    # SESSION-047: Jakobsen-style secondary chains
    "BodyCollisionCircle",
    "SecondaryChainConfig",
    "SecondaryChainDiagnostics",
    "SecondaryChainSnapshot",
    "JakobsenSecondaryChain",
    "SecondaryChainProjector",
    "create_default_secondary_chain_configs",
]
