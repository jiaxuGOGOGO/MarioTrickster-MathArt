# SESSION-195 Research Notes: Industrial & Academic Reference Synthesis

**Date:** 2026-04-25
**Session:** SESSION-195
**Priority:** P0

---

## 1. Unreal Engine 5 — AnimGraph State Machine & Data-Driven Animation

**Source:** Epic Games, "Game Animation Sample Project in Unreal Engine" (UE5.7 Documentation)

### Key Architectural Principles Extracted

1. **Motion Matching as Data-Driven Asset Selection**: UE5's Game Animation Sample Project is built around Motion Matching — an animation system that queries the character's movement model to select animation poses from a **database of animation assets**. Each animation state (walk, run, jump, idle) is stored as an independent **Pose Search Database** asset, not hardcoded in if/else logic.

2. **Chooser Table Pattern (Registry)**: A `Chooser Table` asset (`CHT_PoseSearchDatabases`) acts as a registry that maps gameplay context to the correct Pose Search Database. The system evaluates the chooser to select which database to search, enabling **higher-level filtering** — e.g., "only search walk databases when the character is walking."

3. **AnimNode Functions as Strategy Pattern**: Each animation state transition is handled by an `AnimNode Function` that encapsulates selection logic. This is the Strategy pattern: the algorithm for selecting poses is decoupled from the state machine itself.

4. **Absolute Decoupling of Motion Resolution and Rendering**: The Offset Root Translation/Rotation modes demonstrate that motion calculation (bone-driven kinematics) is completely decoupled from visual rendering. The capsule (physics proxy) and the visual mesh operate on independent coordinate systems with interpolation bridges.

### Application to MarioTrickster-MathArt SESSION-195

- **Gait templates (walk/run/jump/idle/dash) must be independent data-driven assets** registered in a central registry, not hardcoded in if/elif chains.
- The existing `MotionStateLaneRegistry` in `unified_gait_blender.py` already implements this UE5-style Chooser Table pattern. SESSION-195 must mirror this pattern in `openpose_pose_provider.py`.
- Each gait generator function should be a **self-contained strategy** that can be registered/unregistered without modifying the core provider.

---

## 2. Spring Framework — ResourceLoader & Context Late-Binding

**Source:** Spring Framework Documentation (docs.spring.io), "Resources" chapter; Spring DI reference

### Key Architectural Principles Extracted

1. **Late-Binding of Resources**: Spring sets properties and resolves dependencies **as late as possible**, when the bean is actually created. This means resource paths are not resolved at configuration time but at runtime when the component actually needs them.

2. **ResourceLoader Interface**: All ApplicationContexts implement `ResourceLoader`, providing a uniform interface for loading resources regardless of their physical location (classpath, filesystem, URL). The key insight: **components declare what they need (a Resource), not where it lives**.

3. **Context Variable Resolution**: Spring's `PropertyPlaceholderConfigurer` and `@Value("${...}")` annotations resolve placeholder variables at context initialization time. The pattern: **declare placeholders in configuration → resolve to actual values at assembly time**.

4. **Fail-Fast on Missing Resources**: Spring throws `BeanCreationException` immediately if a required resource cannot be resolved, rather than silently proceeding with a null reference.

### Application to MarioTrickster-MathArt SESSION-195

- **`_visual_reference_path` is a runtime context variable** that must be resolved at the `_execute_live_pipeline` chunk assembly site, not at configuration time.
- The pattern: `identity_hydration.extract_visual_reference_path(validated)` acts as the ResourceLoader — it searches multiple context locations and validates physical existence.
- **Fail-Fast**: If the path is non-empty but the file doesn't exist, raise `PipelineIntegrityError` immediately. Never pass a ghost path to ComfyUI.
- **Graceful Degradation**: If the path is empty/None (user didn't provide a reference image), skip IPAdapter identity injection entirely — this is the "optional dependency" pattern from Spring.

---

## 3. Martin Fowler — Evolutionary Architecture & Contract Testing

**Source:** Martin Fowler, "Contract Test" (martinfowler.com/bliki/ContractTest.html, 2011); "Building Evolutionary Architectures" foreword (2017)

### Key Architectural Principles Extracted

1. **Tests as First-Class Contracts**: Contract tests verify that the interface contract between components hasn't changed. When the contract changes (e.g., SESSION-193 changed Depth/Normal strength from 0.85 to 0.45), **all downstream contract tests must be updated to reflect the new contract**.

2. **Broken Build = Broken Window**: A failing test that is ignored or skipped is a "broken window" that invites further degradation. Fowler emphasizes: "A failure in a contract test... should trigger a task to get things consistent again."

3. **Evolutionary Architecture**: The heart of evolutionary architecture is to "make small changes, and put in feedback loops that allow everyone to learn from how the system is developing." Tests are the primary feedback loop.

4. **Fitness Functions**: Architectural fitness functions are objective integrity assessments of architecture characteristics. In our context, the test assertions (`>= 0.85` vs `>= 0.40`) ARE fitness functions that must evolve with the architecture.

### Application to MarioTrickster-MathArt SESSION-195

- **SESSION-193 changed the arbitration contract**: Depth/Normal strength was softened from 0.85→0.45 because OpenPose now takes over motion control. The old `>= 0.85` assertions in test_session190 and test_session192 are **stale contracts** that must be updated to `>= 0.40`.
- **No skipping, no commenting out**: Per the anti-broken-window principle, we must precisely update the expected values, not skip or disable the tests.
- **Banner text assertions must evolve**: If the telemetry banner now reports "0.45" instead of "0.90", the text assertions must be updated accordingly.
- **The Three-Layer Evolution Loop**: Tests → Architecture → Documentation must all evolve in lockstep. SESSION-195 closes this loop.

---

## Synthesis: SESSION-195 Architectural Mandate

| Principle | Source | SESSION-195 Application |
|-----------|--------|------------------------|
| Data-driven animation assets | UE5 AnimGraph | Gait templates as registry entries, not if/elif |
| Chooser Table / Registry | UE5 Motion Matching | `OpenPoseGaitRegistry` mirrors `MotionStateLaneRegistry` |
| Late-binding resource resolution | Spring ResourceLoader | `_visual_reference_path` resolved at chunk assembly site |
| Fail-Fast on missing resources | Spring DI | `PipelineIntegrityError` for ghost paths |
| Optional dependency degradation | Spring DI | Skip IPAdapter if no reference image provided |
| Contract tests as first-class citizens | Fowler Contract Test | Update stale `>= 0.85` assertions to `>= 0.40` |
| Anti-broken-window | Fowler Evolutionary Arch | No `@pytest.mark.skip`, no commented-out tests |
| Fitness functions evolve with architecture | Fowler Fitness Functions | Test assertions = architecture fitness functions |
