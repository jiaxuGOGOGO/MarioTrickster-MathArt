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
from .rl_gym_env import (
    LocomotionRLEnv, LocomotionRLEnvConfig, RLEnvConfig,
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
    PhaseDrivenStateMachine, IllegalStateTransitionError,
    PHASE_DRIVEN_ALLOWED_TRANSITIONS,
    extract_phase_parameters, create_phase_channel_from_signal,
    WALK_KEY_POSES, RUN_KEY_POSES, WALK_CHANNELS, RUN_CHANNELS,
)
from .unified_motion import (
    PhaseState,
    MotionRootTransform, MotionContactState,
    UnifiedMotionFrame, UnifiedMotionClip,
    MotionPipelineAuditEntry, MotionPipelineResult, MotionPipelineNode,
    pose_to_umr, umr_to_pose, infer_contact_tags, run_motion_pipeline,
    ContactManifoldRecord,
    JOINT_CHANNEL_2D_SCALAR, JOINT_CHANNEL_2D_PLUS_DEPTH, JOINT_CHANNEL_3D_EULER,
    VALID_JOINT_CHANNEL_SCHEMAS,
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
# SESSION-051: Runtime state-machine graph coverage (Gap D1)
from .state_machine_graph import (
    RuntimeTransitionEdge,
    GraphCoverageSnapshot,
    RuntimeGraphExecutionResult,
    RuntimeStateGraph,
    RuntimeStateMachineHarness,
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
# SESSION-052: XPBD Physics Singularity (Gap P0-2 — Full Two-Way Rigid-Soft Coupling)
from .xpbd_solver import (
    ParticleKind, ConstraintKind,
    XPBDSolverConfig, XPBDChainPreset,
    XPBDDiagnostics, XPBDConstraint,
    XPBDSolver, build_xpbd_chain, create_default_xpbd_presets,
)
from .xpbd_collision import (
    SpatialHashGrid, BodyCollisionProxy,
    XPBDCollisionManager, build_body_proxies_from_joints,
)
from .xpbd_bridge import XPBDChainProjector
from .xpbd_evolution import (
    TuningAction, EvolutionDiagnosticSnapshot, InternalEvolver,
    KnowledgeEntry, KnowledgeDistiller,
    TestResult, PhysicsTestHarness,
    XPBDEvolutionOrchestrator,
)
try:
    from .xpbd_taichi import (
        TaichiXPBDBackendStatus,
        TaichiXPBDClothConfig,
        TaichiXPBDClothDiagnostics,
        TaichiXPBDBenchmarkResult,
        TaichiXPBDClothSystem,
        get_taichi_xpbd_backend_status,
        create_default_taichi_cloth_config,
    )
except (ImportError, AttributeError):  # taichi not available
    pass
from .sdf_ccd import (
    SDFCCDConfig,
    SDFCCDResult,
    SDFCCDBatchDiagnostics,
    SDFSphereTracingCCD,
    apply_sdf_ccd_to_particle_batch,
    clamp_solver_particle_motion_with_sdf_ccd,
)
from .nsm_gait import (
    LimbPhaseModel,
    AsymmetricGaitProfile,
    LimbContactState,
    NSMGaitFrame,
    DistilledNeuralStateMachine,
    apply_biped_fabrik_offsets,
    generate_asymmetric_biped_pose,
    plan_quadruped_gait,
    BIPED_LIMP_RIGHT_PROFILE,
    BIPED_INJURED_LEFT_PROFILE,
    QUADRUPED_TROT_PROFILE,
    QUADRUPED_PACE_PROFILE,
)
# SESSION-056: Headless Neural Render Pipeline (Jamriška EbSynth + Zhang ControlNet)
from .headless_comfy_ebsynth import (
    NeuralRenderConfig, KeyframePlan, NeuralRenderResult,
    ComfyUIHeadlessClient, EbSynthPropagationEngine,
    HeadlessNeuralRenderPipeline,
)
# SESSION-107: Math-to-AI ControlNet bridge exporters
from .controlnet_bridge_exporters import (
    PaddingInfo,
    ExportResult,
    NormalMapExportConfig,
    NormalMapExporter,
    DepthMapExportConfig,
    DepthMapExporter,
    encode_controlnet_normal_rgb,
)
from .frame_sequence_exporter import (
    FrameSequenceExportConfig,
    FrameSequenceExporter,
    FrameSequenceExportResult,
)
# SESSION-056: Engine Import Plugin (Bénard Dead Cells 2D Deferred Lighting)
from .engine_import_plugin import (
    MathArtBundle, EngineImportPluginGenerator,
    extract_sdf_contour, generate_mathart_bundle,
    validate_mathart_bundle,
)
# SESSION-059: Unity URP 2D native pipeline + XPBD VAT bridge
from .unity_urp_native import (
    SecondaryTextureBinding,
    SECONDARY_TEXTURE_BINDINGS,
    XPBDVATBakeConfig,
    VATBakeManifest,
    VATBakeResult,
    UnityNativePipelineAudit,
    UnityURP2DNativePipelineGenerator,
    collect_taichi_cloth_frames,
    encode_vat_position_texture,
    build_vat_preview,
    bake_cloth_vat,
    generate_unity_urp_2d_native_pipeline,
)
# SESSION-061: Motion Cognitive Dimensionality Reduction & 2D IK Closed Loop
from .orthographic_projector import (
    OrthographicProjector, ProjectionConfig, SpineJSONExporter,
    Bone3D, Bone2D, Clip3D, Clip2D, ProjectionQualityMetrics,
    create_biped_skeleton_3d, create_quadruped_skeleton_3d,
    create_sample_walk_clip_3d,
)
from .terrain_ik_2d import (
    Joint2D, IKConfig, TerrainProbe2D, FABRIK2DSolver,
    TerrainAdaptiveIKLoop, IKQualityMetrics,
    create_terrain_ik_loop,
)
from .principles_quantifier import (
    AnimFrame, PrincipleReport, PrincipleScorer,
)
from .motion_2d_pipeline import (
    PipelineConfig, PipelineResult, Motion2DPipeline,
)
# SESSION-049: Phase-Preserving Gait Transition Blending (Gap B3 — Marker-based DTW)
from .gait_blend import (
    SyncMarker, GaitSyncProfile, GaitBlendLayer,
    GaitBlender, StrideWheel,
    BIPEDAL_SYNC_MARKERS, WALK_SYNC_PROFILE, RUN_SYNC_PROFILE, SNEAK_SYNC_PROFILE,
    phase_warp, adaptive_bounce,
    blend_walk_run, blend_gaits_at_phase,
)
# SESSION-063: Dimension Uplift Engine (Phase 5 — 2.5D & True 3D)
from .dimension_uplift_engine import (
    SDF3DPrimitives, SmoothMin3D, SDFDimensionLifter,
    HermiteEdge, DCMesh, DualContouringExtractor,
    IsometricCameraConfig, IsometricDisplacementMapper,
    CelShadingConfig,
    TaichiAOTConfig, TaichiAOTBridge,
    AdaptiveSDFNode, AdaptiveSDFCache,
    DimensionUpliftStatus,
)
# SESSION-062: Fluid Sequence Exporter & Unity VFX Graph Bridge
from .fluid_sequence_exporter import (
    FluidSequenceConfig,
    FluidSequenceManifest,
    FluidSequenceExportResult,
    FlipbookAtlasBuilder,
    VelocityFieldRenderer,
    FluidSequenceExporter,
    generate_fluid_vfx_controller,
    export_fluid_vfx_bundle,
)
# SESSION-106: Physical Ribbon Mesh Extractor (P1-B1-1)
from .physical_ribbon_extractor import (
    RibbonExtractorConfig,
    RibbonMeshMetadata,
    PhysicalRibbonExtractor,
    catmull_rom_interpolate,
    compute_tangent_frames,
    extrude_ribbon_mesh,
    merge_meshes,
)
# SESSION-080: UMR→RL Reference Adapter (P1-B3-2 — DeepMimic Imitation Closed Loop)
from .umr_rl_adapter import (
    RL_JOINT_ORDER, RL_END_EFFECTOR_JOINTS,
    PrebakedReferenceBuffers, DeepMimicRewardConfig,
    flatten_umr_to_rl_state, interpolate_reference,
    compute_imitation_reward, generate_umr_reference_clips,
)
# SESSION-048: Scene-Aware Distance Sensor (Gap B2 — SDF Terrain + TTC)
from .terrain_sensor import (
    TerrainSDF,
    TerrainRaySensor,
    RayHit,
    TTCPredictor,
    TTCResult,
    scene_aware_distance_phase,
    scene_aware_fall_pose,
    scene_aware_fall_frame,
    scene_aware_jump_distance_phase,
    TerrainSensorDiagnostics,
    evaluate_terrain_sensor_accuracy,
    create_flat_terrain,
    create_slope_terrain,
    create_step_terrain,
    create_sine_terrain,
    create_platform_terrain,
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
    "LocomotionRLEnv", "LocomotionRLEnvConfig", "RLEnvConfig",
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
    "PhaseDrivenStateMachine", "IllegalStateTransitionError",
    "PHASE_DRIVEN_ALLOWED_TRANSITIONS",
    "extract_phase_parameters", "create_phase_channel_from_signal",
    "WALK_KEY_POSES", "RUN_KEY_POSES", "WALK_CHANNELS", "RUN_CHANNELS",
    # SESSION-036: Unified Motion Representation (UMR)
    "PhaseState",
    "MotionRootTransform", "MotionContactState",
    "UnifiedMotionFrame", "UnifiedMotionClip",
    "MotionPipelineAuditEntry", "MotionPipelineResult", "MotionPipelineNode",
    "pose_to_umr", "umr_to_pose", "infer_contact_tags", "run_motion_pipeline",
    "ContactManifoldRecord",
    "JOINT_CHANNEL_2D_SCALAR", "JOINT_CHANNEL_2D_PLUS_DEPTH", "JOINT_CHANNEL_3D_EULER",
    "VALID_JOINT_CHANNEL_SCHEMAS",
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
    # SESSION-051: Runtime state-machine graph coverage (Gap D1)
    "RuntimeTransitionEdge",
    "GraphCoverageSnapshot",
    "RuntimeGraphExecutionResult",
    "RuntimeStateGraph",
    "RuntimeStateMachineHarness",
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
    # SESSION-048: Scene-Aware Distance Sensor (Gap B2 — SDF Terrain + TTC)
    "TerrainSDF",
    "TerrainRaySensor",
    "RayHit",
    "TTCPredictor",
    "TTCResult",
    "scene_aware_distance_phase",
    "scene_aware_fall_pose",
    "scene_aware_fall_frame",
    "scene_aware_jump_distance_phase",
    "TerrainSensorDiagnostics",
    "evaluate_terrain_sensor_accuracy",
    "create_flat_terrain",
    "create_slope_terrain",
    "create_step_terrain",
    "create_sine_terrain",
    "create_platform_terrain",
    # SESSION-049: Phase-Preserving Gait Transition Blending (Gap B3)
    "SyncMarker", "GaitSyncProfile", "GaitBlendLayer",
    "GaitBlender", "StrideWheel",
    "BIPEDAL_SYNC_MARKERS", "WALK_SYNC_PROFILE", "RUN_SYNC_PROFILE", "SNEAK_SYNC_PROFILE",
    "phase_warp", "adaptive_bounce",
    "blend_walk_run", "blend_gaits_at_phase",
    # SESSION-052: XPBD Physics Singularity (Gap P0-2 + P1-B1-2)
    "ParticleKind", "ConstraintKind",
    "XPBDSolverConfig", "XPBDChainPreset",
    "XPBDDiagnostics", "XPBDConstraint",
    "XPBDSolver", "build_xpbd_chain", "create_default_xpbd_presets",
    "SpatialHashGrid", "BodyCollisionProxy",
    "XPBDCollisionManager", "build_body_proxies_from_joints",
    "XPBDChainProjector",
    "TuningAction", "EvolutionDiagnosticSnapshot", "InternalEvolver",
    "KnowledgeEntry", "KnowledgeDistiller",
    "TestResult", "PhysicsTestHarness",
    "XPBDEvolutionOrchestrator",
    "TaichiXPBDBackendStatus",
    "TaichiXPBDClothConfig",
    "TaichiXPBDClothDiagnostics",
    "TaichiXPBDBenchmarkResult",
    "TaichiXPBDClothSystem",
    "get_taichi_xpbd_backend_status",
    "create_default_taichi_cloth_config",
    "SDFCCDConfig",
    "SDFCCDResult",
    "SDFCCDBatchDiagnostics",
    "SDFSphereTracingCCD",
    "apply_sdf_ccd_to_particle_batch",
    "clamp_solver_particle_motion_with_sdf_ccd",
    "LimbPhaseModel",
    "AsymmetricGaitProfile",
    "LimbContactState",
    "NSMGaitFrame",
    "DistilledNeuralStateMachine",
    "apply_biped_fabrik_offsets",
    "generate_asymmetric_biped_pose",
    "plan_quadruped_gait",
    "BIPED_LIMP_RIGHT_PROFILE",
    "BIPED_INJURED_LEFT_PROFILE",
    "QUADRUPED_TROT_PROFILE",
    "QUADRUPED_PACE_PROFILE",
    # SESSION-056: Headless Neural Render Pipeline (EbSynth + ControlNet)
    "NeuralRenderConfig", "NeuralRenderResult",
    "ComfyUIHeadlessClient", "EbSynthPropagationEngine",
    "HeadlessNeuralRenderPipeline",
    # SESSION-107: Math-to-AI ControlNet bridge exporters
    "PaddingInfo", "ExportResult",
    "NormalMapExportConfig", "NormalMapExporter",
    "DepthMapExportConfig", "DepthMapExporter",
    "encode_controlnet_normal_rgb",
    "FrameSequenceExportConfig", "FrameSequenceExporter",
    "FrameSequenceExportResult",
    # SESSION-056: Engine Import Plugin (Godot 4 + Unity URP)
    "MathArtBundle", "EngineImportPluginGenerator",
    "extract_sdf_contour", "generate_mathart_bundle",
    "validate_mathart_bundle",
    "SecondaryTextureBinding", "SECONDARY_TEXTURE_BINDINGS",
    "XPBDVATBakeConfig", "VATBakeManifest", "VATBakeResult",
    "UnityNativePipelineAudit", "UnityURP2DNativePipelineGenerator",
    "collect_taichi_cloth_frames", "encode_vat_position_texture",
    "build_vat_preview", "bake_cloth_vat",
    "generate_unity_urp_2d_native_pipeline",
    # SESSION-061: Motion Cognitive Dimensionality Reduction & 2D IK Closed Loop
    "OrthographicProjector", "ProjectionConfig", "SpineJSONExporter",
    "Bone3D", "Bone2D", "Clip3D", "Clip2D", "ProjectionQualityMetrics",
    "create_biped_skeleton_3d", "create_quadruped_skeleton_3d",
    "create_sample_walk_clip_3d",
    "Joint2D", "IKConfig", "TerrainProbe2D", "FABRIK2DSolver",
    "TerrainAdaptiveIKLoop", "IKQualityMetrics",
    "create_terrain_ik_loop",
    "AnimFrame", "PrincipleReport", "PrincipleScorer",
    "PipelineConfig", "PipelineResult", "Motion2DPipeline",
    # SESSION-062: Fluid Sequence Exporter & Unity VFX Graph Bridge
    "FluidSequenceConfig", "FluidSequenceManifest", "FluidSequenceExportResult",
    "FlipbookAtlasBuilder", "VelocityFieldRenderer", "FluidSequenceExporter",
    "generate_fluid_vfx_controller", "export_fluid_vfx_bundle",
    # SESSION-063: Dimension Uplift Engine (Phase 5 — 2.5D & True 3D)
    "SDF3DPrimitives", "SmoothMin3D", "SDFDimensionLifter",
    "HermiteEdge", "DCMesh", "DualContouringExtractor",
    "IsometricCameraConfig", "IsometricDisplacementMapper",
    "CelShadingConfig",
    "TaichiAOTConfig", "TaichiAOTBridge",
    "AdaptiveSDFNode", "AdaptiveSDFCache",
    "DimensionUpliftStatus",
    # SESSION-080: UMR→RL Reference Adapter (P1-B3-2 — DeepMimic Imitation Closed Loop)
    "RL_JOINT_ORDER", "RL_END_EFFECTOR_JOINTS",
    "PrebakedReferenceBuffers", "DeepMimicRewardConfig",
    "flatten_umr_to_rl_state", "interpolate_reference",
    "compute_imitation_reward", "generate_umr_reference_clips",
    # SESSION-106: Physical Ribbon Mesh Extractor (P1-B1-1)
    "RibbonExtractorConfig",
    "RibbonMeshMetadata",
    "PhysicalRibbonExtractor",
    "catmull_rom_interpolate",
    "compute_tangent_frames",
    "extrude_ribbon_mesh",
    "merge_meshes",
]
