"""SESSION-030 Comprehensive Test Suite — Physics-Based Character Animation.

Tests all new modules:
1. PD Controller
2. MuJoCo Bridge (PhysicsWorld, ContactSolver)
3. RL Locomotion
4. Skill Embeddings (ASE)
5. Physics Genotype
6. Evolution Layer 3
7. Upgraded Engine integration
"""
import sys
import numpy as np

sys.path.insert(0, ".")

PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  [PASS] {name}")
        PASS += 1
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        FAIL += 1


# ── 1. PD Controller Tests ──────────────────────────────────────────────────

print("\n=== 1. PD Controller ===")

from mathart.animation.pd_controller import (
    PDController, PDJointConfig, PDControllerConfig,
    PDSimulationState, DeepMimicReward, HumanoidPDPreset,
    create_humanoid_pd_controller,
)

def test_pd_create():
    pd = create_humanoid_pd_controller(HumanoidPDPreset.DEEPMIMIC_STANDARD)
    assert len(pd.joint_configs) > 0
test("Create PD controller from preset", test_pd_create)

def test_pd_compute_torque():
    pd = create_humanoid_pd_controller(HumanoidPDPreset.DEEPMIMIC_STANDARD)
    torques = pd.compute_torques(
        target_angles={"hip": 0.5, "knee": -0.3},
        current_angles={"hip": 0.0, "knee": 0.0},
        current_velocities={"hip": 0.0, "knee": 0.0},
    )
    assert "hip" in torques
    assert torques["hip"] != 0.0
test("Compute PD torques", test_pd_compute_torque)

def test_pd_stability():
    pd = create_humanoid_pd_controller(HumanoidPDPreset.DEEPMIMIC_STANDARD)
    report = pd.stability_report()
    assert len(report) > 0
    for joint, info in report.items():
        assert "damping_ratio" in info
        assert "natural_frequency_hz" in info
test("PD stability report", test_pd_stability)

def test_pd_simulation():
    pd = create_humanoid_pd_controller(HumanoidPDPreset.DEEPMIMIC_STANDARD)
    target = {"hip": 0.5, "knee": -0.3}
    state = PDSimulationState.from_pose({"hip": 0.0, "knee": 0.0})
    state = pd.step_simulation(target, state)
    assert "hip" in state.angles
    assert state.angles["hip"] != 0.0
test("PD step simulation", test_pd_simulation)

def test_deepmimic_reward():
    reward = DeepMimicReward()
    result = reward.compute(
        ref_angles={"hip": 0.5, "knee": -0.3},
        sim_angles={"hip": 0.45, "knee": -0.28},
        sim_velocities={"hip": 0.1, "knee": -0.05},
    )
    assert "total" in result
    assert 0.0 <= result["total"] <= 1.0
test("DeepMimic reward computation", test_deepmimic_reward)


# ── 2. MuJoCo Bridge Tests ──────────────────────────────────────────────────

print("\n=== 2. MuJoCo Bridge ===")

from mathart.animation.mujoco_bridge import (
    PhysicsWorld, RigidBody, ContactMaterial,
    ContactSolver, ContactResult, GroundPlane,
    create_humanoid_world, create_contact_material_library,
)

def test_physics_world():
    world = PhysicsWorld()
    assert world.gravity[1] == -9.81
test("Create PhysicsWorld", test_physics_world)

def test_rigid_body():
    body = RigidBody(name="test", mass=1.0, position=np.array([0.0, 1.0]))
    assert body.mass == 1.0
test("Create rigid body", test_rigid_body)

def test_contact_material():
    mat = ContactMaterial(friction=0.8, restitution=0.3)
    assert mat.friction == 0.8
test("Create contact material", test_contact_material)

def test_contact_solver():
    solver = ContactSolver()
    assert solver.ground is not None
test("Create contact solver", test_contact_solver)

def test_material_presets():
    lib = create_contact_material_library()
    assert "rubber_concrete" in lib
    assert "ice" in lib
test("Material presets available", test_material_presets)

def test_world_simulation():
    world = PhysicsWorld()
    body = RigidBody(name="ball", mass=1.0, position=np.array([0.0, 2.0]))
    world.add_body(body)
    world.step()
    assert body.position[1] < 2.0  # Should fall
test("World simulation step", test_world_simulation)

def test_world_energy():
    world = PhysicsWorld()
    body = RigidBody(name="ball", mass=1.0, position=np.array([0.0, 2.0]))
    world.add_body(body)
    e = world.total_energy()
    assert e > 0
test("World total energy", test_world_energy)

def test_humanoid_world():
    world = create_humanoid_world()
    assert len(world.bodies) > 0
test("Create humanoid world", test_humanoid_world)


# ── 3. RL Locomotion Tests ───────────────────────────────────────────────────

print("\n=== 3. RL Locomotion ===")

from mathart.animation.rl_locomotion import (
    LocomotionEnv, LocomotionConfig, GaitType,
    PPOTrainer, ReferenceMotionLibrary,
    LocomotionPolicy, PPOConfig,
)

def test_gait_types():
    assert GaitType.WALK.value == "walk"
    assert GaitType.RUN.value == "run"
test("GaitType enum", test_gait_types)

def test_locomotion_config():
    cfg = LocomotionConfig(gait=GaitType.WALK, target_velocity=1.0)
    assert cfg.gait == GaitType.WALK
test("LocomotionConfig creation", test_locomotion_config)

def test_reference_library():
    lib = ReferenceMotionLibrary()
    walk = lib.get_motion("walk")
    assert walk is not None
    assert len(walk) > 0
test("Reference motion library", test_reference_library)

def test_sample_frame():
    lib = ReferenceMotionLibrary()
    frame = lib.sample_frame("walk", 0.5)
    assert frame is not None
test("Sample reference frame", test_sample_frame)

def test_locomotion_env():
    cfg = LocomotionConfig(gait=GaitType.WALK, target_velocity=1.0)
    env = LocomotionEnv(config=cfg)
    obs = env.reset()
    assert obs is not None
test("LocomotionEnv reset", test_locomotion_env)

def test_locomotion_env_step():
    cfg = LocomotionConfig(gait=GaitType.WALK, target_velocity=1.0)
    env = LocomotionEnv(config=cfg)
    obs = env.reset()
    action = np.zeros(env.act_dim)
    obs2, reward, done, info = env.step(action)
    assert obs2 is not None
    assert isinstance(reward, float)
test("LocomotionEnv step", test_locomotion_env_step)

def test_locomotion_policy():
    policy = LocomotionPolicy(obs_dim=32, act_dim=12)
    obs = np.zeros(32)
    action = policy.act(obs)
    assert len(action) == 12
test("Locomotion policy action", test_locomotion_policy)


# ── 4. Skill Embeddings (ASE) Tests ─────────────────────────────────────────

print("\n=== 4. Skill Embeddings (ASE) ===")

from mathart.animation.skill_embeddings import (
    ASEFramework, SkillEncoder, MotionDiscriminator,
    LowLevelController, HighLevelController,
    SkillLibrary, SkillType, SkillEntry,
)

def test_skill_types():
    assert SkillType.WALK.value == "walk"
    assert SkillType.FALL_RECOVER.value == "fall_recover"
test("SkillType enum", test_skill_types)

def test_skill_encoder():
    enc = SkillEncoder(obs_dim=32, skill_dim=16, trajectory_length=5)
    # encode expects a trajectory (trajectory_length, obs_dim)
    trajectory = np.random.randn(5, 32)
    mu, logvar = enc.encode(trajectory)
    assert len(mu) == 16
test("Skill encoder", test_skill_encoder)

def test_motion_discriminator():
    disc = MotionDiscriminator(obs_dim=32)
    state = np.random.randn(32)
    next_state = np.random.randn(32)
    score = disc.score(state, next_state)
    assert isinstance(score, float)
test("Motion discriminator", test_motion_discriminator)

def test_skill_library():
    from mathart.animation.skill_embeddings import SkillEntry
    lib = SkillLibrary()
    entry = SkillEntry(name="walk", skill_type=SkillType.WALK, latent=np.random.randn(32))
    lib.register(entry)
    retrieved = lib.get("walk")
    assert retrieved is not None
    z = lib.get_latent("walk")
    assert z is not None and len(z) == 32
test("Skill library", test_skill_library)

def test_llc():
    llc = LowLevelController(obs_dim=32, act_dim=12, skill_dim=16)
    state = np.random.randn(32)
    latent = np.random.randn(16)
    action = llc.act(state, latent)
    assert len(action) == 12
test("Low-level controller", test_llc)

def test_hlc():
    hlc = HighLevelController(obs_dim=32, skill_dim=16, goal_dim=4)
    state = np.random.randn(32)
    goal = np.random.randn(4)
    latent = hlc.select_skill(state, goal)
    assert len(latent) == 16
test("High-level controller", test_hlc)

def test_ase_framework():
    ase = ASEFramework(obs_dim=32, skill_dim=16, act_dim=12)
    assert ase.encoder is not None
    assert ase.discriminator is not None
    assert ase.llc is not None
    assert ase.hlc is not None
test("ASE framework creation", test_ase_framework)


# ── 5. Physics Genotype Tests ───────────────────────────────────────────────

print("\n=== 5. Physics Genotype ===")

from mathart.animation.physics_genotype import (
    PhysicsGenotype, LocomotionGenotype,
    create_physics_genotype, create_locomotion_genotype,
    decode_pd_controller, decode_locomotion_config, decode_contact_material,
    mutate_physics_genotype, mutate_locomotion_genotype,
    crossover_physics_genotype, crossover_locomotion_genotype,
    evaluate_physics_fitness,
)

def test_physics_geno_create():
    pg = create_physics_genotype("hero")
    assert pg.pd_stiffness_scale == 1.0
test("Create physics genotype", test_physics_geno_create)

def test_loco_geno_create():
    lg = create_locomotion_genotype("hero")
    assert lg.gait_type == "walk"
test("Create locomotion genotype", test_loco_geno_create)

def test_decode_pd():
    pg = create_physics_genotype("hero")
    pd = decode_pd_controller(pg)
    assert len(pd.joint_configs) > 0
test("Decode PD controller from genotype", test_decode_pd)

def test_decode_loco():
    lg = create_locomotion_genotype("hero")
    cfg = decode_locomotion_config(lg)
    assert cfg.gait == GaitType.WALK
test("Decode locomotion config", test_decode_loco)

def test_decode_contact():
    pg = create_physics_genotype("hero")
    mat = decode_contact_material(pg)
    assert mat.friction > 0
test("Decode contact material", test_decode_contact)

def test_mutate_physics():
    rng = np.random.default_rng(42)
    pg = create_physics_genotype("hero")
    pg2 = mutate_physics_genotype(pg, rng, strength=0.5)
    assert pg.to_dict() != pg2.to_dict()
test("Mutate physics genotype", test_mutate_physics)

def test_mutate_loco():
    rng = np.random.default_rng(42)
    lg = create_locomotion_genotype("hero")
    lg2 = mutate_locomotion_genotype(lg, rng, strength=0.5)
    assert lg.to_dict() != lg2.to_dict()
test("Mutate locomotion genotype", test_mutate_loco)

def test_crossover_physics():
    rng = np.random.default_rng(42)
    pa = create_physics_genotype("hero")
    pb = create_physics_genotype("monster_heavy")
    child = crossover_physics_genotype(pa, pb, rng)
    assert child is not None
test("Crossover physics genotypes", test_crossover_physics)

def test_evaluate_fitness():
    pg = create_physics_genotype("hero")
    lg = create_locomotion_genotype("hero")
    fitness = evaluate_physics_fitness(pg, lg, n_eval_steps=10)
    assert "overall" in fitness
    assert "stability" in fitness
    assert 0.0 <= fitness["overall"] <= 1.0
test("Evaluate physics fitness", test_evaluate_fitness)

def test_physics_geno_serialization():
    pg = create_physics_genotype("hero")
    d = pg.to_dict()
    pg2 = PhysicsGenotype.from_dict(d)
    assert pg.pd_stiffness_scale == pg2.pd_stiffness_scale
test("Physics genotype serialization", test_physics_geno_serialization)

def test_physics_geno_vector():
    pg = create_physics_genotype("hero")
    v = pg.to_vector()
    pg2 = PhysicsGenotype.from_vector(v)
    assert abs(pg.pd_stiffness_scale - pg2.pd_stiffness_scale) < 0.01
test("Physics genotype vector conversion", test_physics_geno_vector)


# ── 6. Evolution Layer 3 Tests ──────────────────────────────────────────────

print("\n=== 6. Evolution Layer 3 ===")

from mathart.evolution.evolution_layer3 import (
    PhysicsEvolutionLayer, PhysicsEvolutionRecord,
    PhysicsTestBattery, PhysicsDiagnosisEngine,
    PhysicsKnowledgeDistiller, PhysicsTestResult,
)

def test_test_battery():
    battery = PhysicsTestBattery()
    report = battery.run_full_battery(
        {"stability": 0.8, "energy_efficiency": 0.6, "imitation_score": 0.5, "damping_quality": 0.7, "overall": 0.65}
    )
    assert report.result in (PhysicsTestResult.PASS, PhysicsTestResult.WARN_MARGINAL)
test("Physics test battery (passing)", test_test_battery)

def test_test_battery_fail():
    battery = PhysicsTestBattery()
    report = battery.run_full_battery(
        {"stability": 0.2, "energy_efficiency": 0.1, "imitation_score": 0.1, "damping_quality": 0.1, "overall": 0.1}
    )
    assert report.result.value.startswith("fail")
test("Physics test battery (failing)", test_test_battery_fail)

def test_diagnosis():
    battery = PhysicsTestBattery()
    report = battery.run_full_battery(
        {"stability": 0.2, "energy_efficiency": 0.1, "imitation_score": 0.1, "damping_quality": 0.1, "overall": 0.1}
    )
    engine = PhysicsDiagnosisEngine()
    actions, mods = engine.diagnose(report)
    assert len(actions) > 0
test("Physics diagnosis engine", test_diagnosis)

def test_knowledge_distiller():
    distiller = PhysicsKnowledgeDistiller()
    pg = create_physics_genotype("hero")
    lg = create_locomotion_genotype("hero")
    rules = distiller.distill_success(pg, lg, {"overall": 0.8, "stability": 0.9, "imitation_score": 0.7}, "hero")
    assert len(rules) > 0
    assert rules[0]["domain"] in ("physics_pd", "physics_contact", "locomotion")
test("Knowledge distiller", test_knowledge_distiller)

def test_layer3_single_cycle():
    import tempfile, os
    tmpdir = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmpdir, "knowledge"), exist_ok=True)
    layer = PhysicsEvolutionLayer(project_root=tmpdir, verbose=False, max_cycles=1)
    pg = create_physics_genotype("hero")
    lg = create_locomotion_genotype("hero")
    rng = np.random.default_rng(42)
    record = layer.run(pg, lg, "hero", population_size=3, rng=rng)
    assert record.cycle_id == 1
    assert record.combined_fitness >= 0.0
test("Layer 3 single evolution cycle", test_layer3_single_cycle)


# ── 7. Upgraded Engine Tests ─────────────────────────────────────────────────

print("\n=== 7. Upgraded Engine ===")

from mathart.evolution.engine import SelfEvolutionEngine

def test_engine_with_physics():
    import tempfile
    tmpdir = tempfile.mkdtemp()
    engine = SelfEvolutionEngine(project_root=tmpdir, verbose=False, enable_physics=True)
    assert engine.physics_layer is not None
test("Engine with physics enabled", test_engine_with_physics)

def test_engine_without_physics():
    import tempfile
    tmpdir = tempfile.mkdtemp()
    engine = SelfEvolutionEngine(project_root=tmpdir, verbose=False, enable_physics=False)
    assert engine.physics_layer is None
test("Engine with physics disabled", test_engine_without_physics)

def test_engine_physics_only():
    import tempfile, os
    tmpdir = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmpdir, "knowledge"), exist_ok=True)
    engine = SelfEvolutionEngine(project_root=tmpdir, verbose=False, enable_physics=True)
    records = engine.run_physics_only(archetype="hero", n_cycles=2, seed=42)
    assert len(records) > 0
test("Engine run_physics_only", test_engine_physics_only)

def test_engine_status():
    import tempfile
    tmpdir = tempfile.mkdtemp()
    engine = SelfEvolutionEngine(project_root=tmpdir, verbose=False, enable_physics=True)
    report = engine.status()
    assert "Layer 1" in report
    assert "Layer 2" in report
    assert "Layer 3" in report
test("Engine three-layer status report", test_engine_status)

def test_engine_capability_gap():
    import tempfile
    tmpdir = tempfile.mkdtemp()
    engine = SelfEvolutionEngine(project_root=tmpdir, verbose=False, enable_physics=True)
    report = engine.capability_gap_report()
    assert "covered" in report
    assert "missing" in report
test("Engine capability gap report", test_engine_capability_gap)


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"SESSION-030 Test Results: {PASS} PASSED, {FAIL} FAILED")
print(f"{'='*60}")

if FAIL > 0:
    sys.exit(1)
