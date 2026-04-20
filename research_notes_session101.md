# Research Notes — SESSION-101

**Focus**: `HIGH-TestBlindSpot` — Industrial-grade white-box math kernel coverage for
`fluid_vfx`, `unified_motion`, `nsm_gait`, and the 2D FABRIK / 2-Bone IK solver.

These notes capture the external research that anchors the Session-101 code
landing. They are the concrete translation of three external tracks into
project-local testing rules, written before any test file was touched so that
every new case in `tests/test_session101_math_blind_spots.py` can be justified
against a citation instead of an implementation detail.

## 1. NASA JPL "Power of Ten" + MC/DC for Math Kernels

Source: Holzmann, *The Power of 10: Rules for Developing Safety-Critical Code*
(NASA/JPL, 2006); Hayhurst et al., *A Practical Tutorial on Modified
Condition/Decision Coverage* (NASA TM-2001-210876).

Key translations for this repository's NumPy-only math kernels:

| JPL / MC-DC principle | Project-local test discipline |
|---|---|
| Rule 2 — every loop has a proven static upper bound | All new Fluid VFX and UMR round-trip tests run on a micro grid (≤ 24²) and ≤ 8 frames; no unbounded `while` constructs are introduced. |
| Rule 5 — ≥ 2 assertions per function, each a side-effect-free Boolean | Each new test has a **value-level** assertion wall: at minimum `np.isfinite(...).all()`, a shape assertion, and a semantic (mass, energy, or error-bound) assertion. |
| Rule 7 — callers validate return values, callees validate parameters | Tests explicitly target the division-by-zero branch inside `FABRIK2DSolver._move_towards` (`dist < 1e-12`) and the zero-length normal branch inside `TerrainProbe2D.surface_normal_2d` (`length < 1e-12`). |
| MC/DC independence criterion — each condition must independently swing the decision | `solve()` has **two** decision branches: `root_to_target > total_length` (unreachable stretch path) vs. iterative FABRIK. Both branches get a dedicated test with value-level end-effector checks, plus an exact-boundary (`== total_length`) case that exercises the "at-limit" equality condition. |
| Singularities + division-by-zero protection | Tests inject colinear bones, zero-length segments, and "infinite" reach (1e9 units) to prove the solver neither emits NaN/Inf nor exceeds `max_iterations`. |

## 2. Property-Based Testing in JAX/PyTorch (Hypothesis)

Sources: `hypothesisworks/hypothesis` repo; Alan Du, *Intermediate
Property-Based Testing* (2023); de Koning, *Property-Based Testing in Practice
using Hypothesis* (TU Delft, 2025); Tjoa, *Property-Based Testing as
Probabilistic Programming* (OOPSLA 2025).

The dominant, transferable categories used here:

1. **Round-trip invariance** — `f(g(x)) ≈ x` within a tolerance.  Applied to
   `UnifiedMotionClip.to_dict` ↔ `from_dict`, `umr_to_pose` ↔ `pose_to_umr`,
   and `PhaseState.to_dict` ↔ `PhaseState.from_dict`.
2. **Metamorphic invariance** — a transformation of the input produces a
   predictable transformation of the output.  Applied to the Fluid VFX solver:
   injecting a horizontal velocity impulse at a symmetric point produces an
   interior speed field whose argmax is symmetric about the injection center
   after a bounded number of steps.
3. **Algebraic invariants** — `with_contacts` / `with_root` / `with_pose` must
   preserve pose data not on the replacement axis.  We assert byte-identical
   `joint_local_rotations` before and after `with_contacts(...)` calls.
4. **Bounded error envelopes** — Hypothesis samples are bounded to ranges that
   are physically meaningful (phases in `[0, 1)`, stride scales in `[0.1, 3.0]`,
   target distances in `[1e-9, 1e9]`) and each sampled case must land inside a
   `np.testing.assert_allclose(..., atol=1e-5, rtol=1e-5)` envelope after a
   round trip.

We pin `hypothesis` to its deterministic mode via `@settings(derandomize=True,
database=None, deadline=None)` to keep CI bit-reproducible and NEP-19
compliant, matching the existing repo-wide seed-discipline rules.

## 3. Disney/Pixar Industrial Graphics Testing Philosophy

Sources: Stam, *Stable Fluids* (SIGGRAPH 1999) — the exact algorithm used by
`mathart/animation/fluid_vfx.py`; Chentanez et al., *Mass-Conserving Eulerian
Liquid Simulation*; Pixar Technical Memo 13-04, *Mass Preserving Multi-Scale
SPH*.

What we steal from the film-pipeline culture:

1. **Deterministic impulse injection** — hit the grid with a single,
   analytically-described Gaussian impulse (not random noise) and check the
   response.  This matches how Pixar regression tests lock a shot: one clean
   input, many frames of forward simulation, strict per-field checks.
2. **Mass / density bookkeeping** — a passive density field that is only
   injected once must remain non-negative, must have a finite total mass, and
   must decay monotonically at or below the configured
   `density_dissipation`.  We assert `np.testing.assert_allclose(mass_k+1,
   mass_k * density_dissipation, rtol=0.05)` after the active impulse window
   closes.
3. **Velocity-field bounded energy** — after the impulse stops, `max |u|` must
   be monotonically non-increasing (`velocity_dissipation < 1.0`).  This is
   the 2D scalar-field analogue of the energy-decay invariants used in
   industrial Eulerian fluid QA.
4. **GIF / export sink isolation** — per the user's red-line rule, we do not
   mock the math.  For `FluidDrivenVFXSystem.export_gif` we only redirect the
   *disk* write to `tmp_path`, letting `PIL.Image.save` actually run and then
   we **reload** the file to assert frame count, size, and alpha-channel
   variance (so the GIF is not a blank frame).

## 4. Red-Line Compliance Checklist (applied to Session-101 code)

- No `@patch` of core math.  Every new test lets NumPy compute.  Only
  file-system sinks (`tmp_path`, `io.BytesIO`) are redirected.
- No "assert result is not None" sugar.  Every test pulls out a statistic —
  `.sum()`, `.max()`, shape, or an explicit `np.allclose` — and asserts a
  value-level expectation.
- No global seeds.  Each test owns its local `np.random.default_rng(seed=...)`,
  and `random.Random` is instantiated per-test when needed.
- Micro-grid fixtures.  Fluid-grid tests use `grid_size ≤ 24` and
  `iterations ≤ 12`, matching the existing `test_fluid_vfx.py` idiom, to
  avoid OOM.
- Explicit teardown.  Any temporary files are inside `tmp_path`;
  `gc.collect()` is invoked after every Fluid sub-test class.
- Registry / IoC neutrality.  No new test touches
  `BackendRegistry.reset()`; all tests operate on plain Python constructors
  so they compose with the repo-wide session-scoped backend fixture.

## References

1. Holzmann, *The Power of 10: Rules for Developing Safety-Critical Code*,
   IEEE Computer, 2006. DOI 10.1109/MC.2006.212.
2. Hayhurst, Veerhusen, Chilenski, Rierson, *A Practical Tutorial on Modified
   Condition/Decision Coverage*, NASA/TM-2001-210876.
3. Hypothesis documentation, `hypothesisworks/hypothesis`.
4. de Koning, *Property-Based Testing in Practice using Hypothesis*, TU
   Delft, 2025.
5. Stam, *Stable Fluids*, SIGGRAPH 1999.
6. Chentanez & Müller, *Mass-Conserving Eulerian Liquid Simulation*.
7. Aristidou & Lasenby, *FABRIK: A fast, iterative solver for the inverse
   kinematics problem*, Graphical Models 2011.
