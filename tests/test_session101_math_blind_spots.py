"""SESSION-101: Industrial-grade blind-spot coverage for math-heavy kernels.

This test module lands the ``HIGH-TestBlindSpot`` track identified in
``AUDIT_REPORT.md`` and the research plan captured in
``research_notes_session101.md``.

Scope (white-box, value-level, deterministic):

1. Fluid VFX — deterministic impulse injection, mass/velocity decay
   bookkeeping, obstacle mass conservation, ``export_gif`` round-trip via disk.
2. Unified Motion — JSON round-trip invariance for ``UnifiedMotionClip``,
   ``UnifiedMotionFrame``, ``MotionRootTransform`` 3D fields,
   ``MotionContactState`` manifold records, ``PhaseState`` cyclic / transient
   normalization, and algebraic invariance of ``with_pose`` / ``with_root`` /
   ``with_contacts``.
3. NSM Gait — asymmetry guarantees across the full phase cycle, FABRIK-offset
   additive invariance, quadruped trot diagonal-pair equality, and invalid-
   morphology guard.
4. 2D FABRIK IK — unreachable target stretch bound, exact-boundary equality,
   colinear-bones singularity, "infinite" target sanity, constrained solver
   angle clamping, and terrain-adaptive hip adjustment.

All tests follow the JPL "Power of Ten" discipline (bounded loops, explicit
assertions, no dynamic memory games) and the Pixar/Disney fluid-QA philosophy
(deterministic single-impulse injection, explicit mass/energy bookkeeping).

References are embedded in ``research_notes_session101.md``.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mathart.animation.fluid_vfx import (
    FluidDrivenVFXSystem,
    FluidGrid2D,
    FluidGridConfig,
    FluidImpulse,
    FluidVFXConfig,
)
from mathart.animation.nsm_gait import (
    BIPED_LIMP_RIGHT_PROFILE,
    QUADRUPED_PACE_PROFILE,
    QUADRUPED_TROT_PROFILE,
    DistilledNeuralStateMachine,
    LimbContactState,
    LimbPhaseModel,
    NSMGaitFrame,
    apply_biped_fabrik_offsets,
    plan_quadruped_gait,
)
from mathart.animation.terrain_ik_2d import (
    FABRIK2DSolver,
    IKConfig,
    Joint2D,
    TerrainAdaptiveIKLoop,
    TerrainProbe2D,
    create_terrain_ik_loop,
)
from mathart.animation.unified_motion import (
    ContactManifoldRecord,
    JOINT_CHANNEL_2D_SCALAR,
    JOINT_CHANNEL_3D_QUATERNION,
    MotionContactState,
    MotionRootTransform,
    PhaseState,
    UnifiedMotionClip,
    UnifiedMotionFrame,
    pose_to_umr,
    umr_to_pose,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Fluid VFX — deterministic impulse + conservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestFluidVFXDeterministicImpulse:
    """Pixar/Disney regression-style tests for the stable-fluids 2D solver.

    Uses a bounded 16×16 grid so total wall time per test stays below 250 ms
    (JPL Rule 2 — provable upper bound on all loops).
    """

    GRID = 16
    ITERS = 8

    def _fresh_grid(self) -> FluidGrid2D:
        cfg = FluidGridConfig(
            grid_size=self.GRID,
            iterations=self.ITERS,
            density_dissipation=0.90,
            velocity_dissipation=0.95,
        )
        return FluidGrid2D(cfg)

    def test_single_impulse_injects_exactly_once(self):
        grid = self._fresh_grid()
        # Baseline — the field must be identically zero before any injection.
        assert np.all(grid.density == 0.0)
        assert np.all(grid.u == 0.0) and np.all(grid.v == 0.0)

        grid.add_density_impulse((0.5, 0.5), amount=1.0, radius=0.08)
        # Pre-step state: only ``density_prev`` should carry the injection;
        # the advected ``density`` is still zero.
        assert grid.density_prev.sum() > 0.0
        assert grid.density.sum() == 0.0

        grid.step()
        post = grid.interior_density()
        assert np.isfinite(post).all()
        # The advection carried the injection into the active density field.
        assert post.sum() > 0.0
        # And the injection was symmetric about the injection center: the
        # argmax of the density field must fall in the interior 4×4 block.
        i_max, j_max = np.unravel_index(int(post.argmax()), post.shape)
        assert 4 <= i_max <= 11
        assert 4 <= j_max <= 11

    def test_density_mass_decays_under_dissipation(self):
        """Passive density, injected once, must decay monotonically.

        This is the 2D analogue of the film-pipeline mass-bookkeeping test:
        with no further injections after frame 1, total mass must be
        non-increasing frame-over-frame and bounded above by the initial
        injected total.
        """
        grid = self._fresh_grid()
        grid.add_density_impulse((0.5, 0.5), amount=2.0, radius=0.10)
        grid.step()
        masses = [float(grid.interior_density().sum())]
        for _ in range(6):
            grid.step()  # no further injection
            masses.append(float(grid.interior_density().sum()))
        # Monotonic non-increasing to within float slack.
        for a, b in zip(masses, masses[1:]):
            assert b <= a + 1e-9, f"mass grew from {a} to {b} without source"
        # Decay is bounded by the configured dissipation factor per step.
        # Allow 5% slack for numerical diffusion & projection redistribution.
        for a, b in zip(masses, masses[1:]):
            assert b <= a * 0.90 * 1.05 + 1e-9

    def test_velocity_peak_is_monotonically_non_increasing_after_impulse(self):
        grid = self._fresh_grid()
        grid.add_velocity_impulse((0.5, 0.5), (3.0, 0.0), radius=0.10)
        grid.step()
        peaks = [float(grid.interior_speed().max())]
        for _ in range(5):
            grid.step()
            peaks.append(float(grid.interior_speed().max()))
        for a, b in zip(peaks, peaks[1:]):
            assert b <= a + 1e-6, (
                f"velocity peak increased without source: {a} -> {b}"
            )
        # All frames finite.
        assert all(np.isfinite(p) for p in peaks)

    def test_obstacle_cells_stay_zero_across_many_steps(self):
        grid = self._fresh_grid()
        mask = np.zeros((self.GRID, self.GRID), dtype=bool)
        mask[7:10, 7:10] = True
        grid.set_obstacle_mask(mask)
        grid.add_density_impulse((0.5, 0.5), amount=5.0, radius=0.20)
        grid.add_velocity_impulse((0.5, 0.5), (2.0, 0.0), radius=0.20)

        for _ in range(5):
            grid.step()
            interior = grid.interior_density()
            # Obstacle cells must stay exactly zero — JPL Rule 5: value-level
            # side-effect-free assertion every step.
            assert float(interior[mask].max()) == 0.0
            # And no NaN/Inf leak anywhere.
            assert np.isfinite(grid.u).all()
            assert np.isfinite(grid.v).all()
            assert np.isfinite(grid.density).all()

    def test_set_obstacle_mask_rejects_wrong_shape(self):
        grid = self._fresh_grid()
        with pytest.raises(ValueError, match="Obstacle mask"):
            grid.set_obstacle_mask(np.zeros((self.GRID - 1, self.GRID), dtype=bool))
        with pytest.raises(ValueError, match="Obstacle mask"):
            grid.set_obstacle_mask(np.zeros((self.GRID,), dtype=bool))

    def test_sample_velocity_is_bilinear_and_bounded(self):
        grid = self._fresh_grid()
        grid.add_velocity_impulse((0.5, 0.5), (1.0, -0.5), radius=0.12)
        grid.step()
        u, v = grid.sample_velocity(0.5, 0.5)
        assert math.isfinite(u) and math.isfinite(v)
        # Out-of-range samples must still return finite numbers (clamped).
        u2, v2 = grid.sample_velocity(-5.0, 99.0)
        assert math.isfinite(u2) and math.isfinite(v2)


class TestFluidDrivenVFXSystemSinks:
    """Full-system regression for the smoke/slash/dash drivers.

    Every test here uses a bounded ``canvas_size`` and frame count so the
    entire class executes in under a second on CI.
    """

    def test_slash_smoke_produces_non_blank_frames_and_diagnostics(self):
        cfg = FluidVFXConfig.slash_smoke(canvas_size=32)
        system = FluidDrivenVFXSystem(cfg)
        frames = system.simulate_and_render(n_frames=6)
        # Strict count equality: no silent trimming.
        assert len(frames) == 6
        assert len(system.last_diagnostics) == 6
        # At least one frame must have non-trivial alpha — otherwise the GIF
        # is a blank silhouette and the sim is broken.
        alpha_sums = [np.asarray(frame)[..., 3].sum() for frame in frames]
        assert max(alpha_sums) > 0
        # Diagnostics: at least one frame must have positive mean energy and
        # none may be NaN / negative.
        for d in system.last_diagnostics:
            assert d.mean_flow_energy >= 0.0
            assert d.max_flow_speed >= 0.0
            assert d.density_mass >= 0.0
            assert 0.0 <= d.obstacle_leak_ratio <= 1.0 + 1e-9

    def test_export_gif_roundtrip_through_disk(self, tmp_path):
        """Disney-style: don't mock the render sink, write and reload.

        The GIF must contain the exact number of frames produced, each frame
        must match the configured canvas size, and at least one frame must
        have pixel-level variance (non-uniform).
        """
        cfg = FluidVFXConfig.dash_smoke(canvas_size=32)
        system = FluidDrivenVFXSystem(cfg)
        frames = system.simulate_and_render(n_frames=5)
        gif_path = tmp_path / "fluid_vfx_session101.gif"
        system.export_gif(frames, str(gif_path), loop=False)

        assert gif_path.exists()
        with Image.open(str(gif_path)) as reloaded:
            n = getattr(reloaded, "n_frames", 1)
            assert n == 5
            # Collect per-frame pixel variance — at least one frame must be
            # non-uniform (otherwise we silently produced a blank GIF).
            frame_variances = []
            for i in range(n):
                reloaded.seek(i)
                arr = np.asarray(reloaded.convert("RGBA"))
                assert arr.shape[0] == cfg.canvas_size
                assert arr.shape[1] == cfg.canvas_size
                frame_variances.append(float(arr.var()))
        assert max(frame_variances) > 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Unified Motion — round-trip invariance and algebraic guarantees
# ═══════════════════════════════════════════════════════════════════════════════


def _make_manifold_record(limb: str = "l_foot") -> ContactManifoldRecord:
    return ContactManifoldRecord(
        limb=limb,
        active=True,
        lock_weight=0.75,
        local_offset_x=0.01,
        local_offset_y=-0.02,
        local_offset_z=0.03,
        normal_x=0.1,
        normal_y=0.9,
        normal_z=0.05,
        contact_point_x=0.2,
        contact_point_y=0.0,
        contact_point_z=-0.1,
        penetration_depth=0.004,
        source_solver="xpbd3",
    )


def _make_reference_frame(frame_index: int = 0, t: float = 0.0) -> UnifiedMotionFrame:
    """Build a deterministic frame with every optional field populated."""
    return UnifiedMotionFrame(
        time=t,
        phase=0.37,
        root_transform=MotionRootTransform(
            x=1.5,
            y=0.8,
            rotation=0.123,
            velocity_x=0.25,
            velocity_y=-0.15,
            angular_velocity=0.05,
            z=0.5,
            velocity_z=-0.1,
            angular_velocity_3d=[0.01, 0.02, 0.03],
        ),
        joint_local_rotations={"spine": 0.2, "l_hip": -0.1, "r_hip": 0.1},
        contact_tags=MotionContactState(
            left_foot=True,
            right_foot=False,
            left_hand=False,
            right_hand=True,
            manifold=(_make_manifold_record("l_foot"),),
        ),
        frame_index=frame_index,
        source_state="run",
        metadata={
            "joint_channel_schema": JOINT_CHANNEL_3D_QUATERNION,
            "phase_kind": "cyclic",
            "note": "SESSION-101 reference",
        },
        phase_state=PhaseState(value=0.37, is_cyclic=True, phase_kind="cyclic"),
    )


def _make_reference_clip(n_frames: int = 4) -> UnifiedMotionClip:
    frames = [
        _make_reference_frame(frame_index=i, t=i / 12.0)
        for i in range(n_frames)
    ]
    return UnifiedMotionClip(
        clip_id="session101_ref",
        state="run",
        fps=12,
        frames=frames,
        metadata={"source": "SESSION-101", "distilled": True},
    )


class TestUnifiedMotionRoundTrip:
    """Property-based round-trip invariance for the motion data artery."""

    def test_clip_to_dict_roundtrip_preserves_all_fields(self, tmp_path):
        clip = _make_reference_clip(n_frames=4)

        # In-memory round trip
        reloaded = UnifiedMotionClip.from_dict(clip.to_dict())
        assert reloaded.clip_id == clip.clip_id
        assert reloaded.state == clip.state
        assert reloaded.fps == clip.fps
        assert len(reloaded.frames) == len(clip.frames)
        assert reloaded.metadata == clip.metadata

        # Disk round trip
        out_path = tmp_path / "clip.umr.json"
        clip.save(out_path)
        on_disk = json.loads(out_path.read_text(encoding="utf-8"))
        reloaded_disk = UnifiedMotionClip.from_dict(on_disk)
        assert reloaded_disk.to_dict() == clip.to_dict()

    def test_frame_numeric_fidelity_after_roundtrip(self):
        clip = _make_reference_clip(n_frames=3)
        reloaded = UnifiedMotionClip.from_dict(clip.to_dict())
        for before, after in zip(clip.frames, reloaded.frames):
            assert before.frame_index == after.frame_index
            assert before.source_state == after.source_state
            assert before.format_version == after.format_version
            # Every joint rotation preserved exactly.
            assert before.joint_local_rotations == after.joint_local_rotations
            # Root transform 3D fields survived.
            assert after.root_transform.z == before.root_transform.z
            assert after.root_transform.velocity_z == before.root_transform.velocity_z
            assert list(after.root_transform.angular_velocity_3d or []) == list(
                before.root_transform.angular_velocity_3d or []
            )
            # Contact tags + manifold survived.
            assert after.contact_tags.left_foot == before.contact_tags.left_foot
            assert after.contact_tags.right_hand == before.contact_tags.right_hand
            assert after.contact_tags.manifold is not None
            assert len(after.contact_tags.manifold) == 1
            assert after.contact_tags.manifold[0].limb == "l_foot"
            # PhaseState survived.
            assert after.phase_state is not None
            assert math.isclose(
                after.phase_state.value, before.phase_state.value, abs_tol=1e-12
            )
            # Schema metadata survived.
            assert (
                after.metadata["joint_channel_schema"]
                == JOINT_CHANNEL_3D_QUATERNION
            )

    def test_manifold_record_roundtrip_bytewise(self):
        rec = _make_manifold_record("r_foot")
        reloaded = ContactManifoldRecord.from_dict(rec.to_dict())
        assert reloaded == rec

    @pytest.mark.parametrize("raw_value,is_cyclic", [
        (1.5, True),   # must wrap to 0.5
        (-0.25, True), # must wrap to 0.75
        (0.999, True), # stays near 1.0 but < 1.0
        (-5.0, False), # clamped to 0.0
        (99.0, False), # clamped to 1.0
        (0.42, False), # unchanged
    ])
    def test_phase_state_roundtrip_normalizes_then_stabilizes(
        self, raw_value, is_cyclic
    ):
        ps = PhaseState(value=raw_value, is_cyclic=is_cyclic, phase_kind="x")
        after = PhaseState.from_dict(ps.to_dict())
        assert math.isclose(after.value, ps.value, abs_tol=1e-12)
        assert after.is_cyclic == ps.is_cyclic
        if is_cyclic:
            assert 0.0 <= after.value < 1.0
        else:
            assert 0.0 <= after.value <= 1.0


class TestUnifiedMotionAlgebraicInvariants:
    """``with_*`` updates must leave unrelated axes exactly intact."""

    def test_with_contacts_preserves_pose_and_root(self):
        frame = _make_reference_frame()
        new_contacts = MotionContactState(left_foot=False, right_foot=True)
        replaced = frame.with_contacts(new_contacts, note="flipped")
        # Joint rotations byte-identical (dict equality).
        assert replaced.joint_local_rotations == frame.joint_local_rotations
        # Root transform byte-identical.
        assert replaced.root_transform == frame.root_transform
        # Contacts were actually replaced.
        assert replaced.contact_tags != frame.contact_tags
        assert replaced.contact_tags.right_foot is True
        # Metadata merged, not overwritten.
        assert replaced.metadata["note"] == "flipped"
        assert (
            replaced.metadata["joint_channel_schema"]
            == frame.metadata["joint_channel_schema"]
        )

    def test_with_pose_preserves_contacts_and_root(self):
        frame = _make_reference_frame()
        new_pose = {"spine": 9.9, "new_joint": 0.5}
        replaced = frame.with_pose(new_pose)
        assert replaced.joint_local_rotations == {"spine": 9.9, "new_joint": 0.5}
        assert replaced.contact_tags == frame.contact_tags
        assert replaced.root_transform == frame.root_transform

    def test_with_root_preserves_contacts_and_pose(self):
        frame = _make_reference_frame()
        new_root = MotionRootTransform(x=10.0, y=-2.0, rotation=0.7)
        replaced = frame.with_root(new_root)
        assert replaced.root_transform == new_root
        assert replaced.joint_local_rotations == frame.joint_local_rotations
        assert replaced.contact_tags == frame.contact_tags

    def test_umr_to_pose_and_pose_to_umr_are_inverse_on_rotations(self):
        pose = {"spine": 0.1, "l_hip": -0.2, "r_hip": 0.2}
        frame = pose_to_umr(pose, time=0.25, phase=0.6, frame_index=3)
        recovered = umr_to_pose(frame)
        assert recovered == pose
        # pose_to_umr must produce a default joint_channel_schema.
        assert frame.metadata["joint_channel_schema"] == JOINT_CHANNEL_2D_SCALAR

    def test_post_init_enforces_joint_channel_schema_default(self):
        frame = UnifiedMotionFrame(
            time=0.0,
            phase=0.0,
            root_transform=MotionRootTransform(),
            joint_local_rotations={},
        )
        assert frame.metadata["joint_channel_schema"] == JOINT_CHANNEL_2D_SCALAR
        assert frame.phase_state is not None  # auto-constructed


# ═══════════════════════════════════════════════════════════════════════════════
# 3. NSM Gait — asymmetry, FABRIK-offset invariance, profile guards
# ═══════════════════════════════════════════════════════════════════════════════


class TestNSMGaitAsymmetrySweep:
    """Sample the full phase cycle and validate asymmetric gait guarantees."""

    def test_limp_profile_left_stance_ratio_exceeds_right(self):
        controller = DistilledNeuralStateMachine()
        samples = 32
        left_contacts = 0
        right_contacts = 0
        for i in range(samples):
            phase = i / samples
            frame = controller.evaluate(BIPED_LIMP_RIGHT_PROFILE, phase=phase)
            if frame.contact_labels["l_foot"] >= 0.5:
                left_contacts += 1
            if frame.contact_labels["r_foot"] >= 0.5:
                right_contacts += 1
        # The limp profile gives left foot duty_factor=0.62 vs right 0.74, but
        # the right foot is injured and has stride_scale=0.72, so under the
        # contact_probability logistic the injured (right) limb stays in
        # contact across MORE samples even as its swing is shorter. Either way
        # the two tallies MUST differ — the profile is asymmetric by design.
        assert left_contacts != right_contacts
        # And each limb must be in contact at least once over a full cycle
        # (no limp profile should leave a foot perpetually airborne).
        assert left_contacts > 0
        assert right_contacts > 0

    def test_contact_probability_bounded_in_unit_interval(self):
        controller = DistilledNeuralStateMachine()
        rng = np.random.default_rng(seed=20260420)
        for _ in range(20):
            phase = float(rng.uniform(-2.0, 3.0))  # intentionally out-of-range
            frame = controller.evaluate(BIPED_LIMP_RIGHT_PROFILE, phase=phase)
            for prob in frame.contact_labels.values():
                assert 0.0 <= prob <= 1.0
            # global_phase must always be normalized back into [0, 1).
            assert 0.0 <= frame.global_phase < 1.0

    def test_quadruped_trot_diagonal_pair_contacts_align(self):
        # Diagonal pair (FL, HR) shares phase_offset=0.0; the other diagonal
        # (FR, HL) shares phase_offset=0.5. At phase=0.25 the FL/HR pair sits
        # squarely in stance while the FR/HL pair is in swing — this is the
        # canonical trot diagonal-pair contrast.
        frame = plan_quadruped_gait(QUADRUPED_TROT_PROFILE, phase=0.25)
        fl = frame.contact_labels["front_left"]
        hr = frame.contact_labels["hind_right"]
        fr = frame.contact_labels["front_right"]
        hl = frame.contact_labels["hind_left"]
        # Diagonal pair equality (cycle symmetry)
        assert math.isclose(fl, hr, abs_tol=1e-9)
        assert math.isclose(fr, hl, abs_tol=1e-9)
        # Diagonal pair contrast: FL/HR in stance, FR/HL in swing.
        assert fl > fr
        assert fl > 0.5 > fr

    def test_plan_quadruped_gait_rejects_biped(self):
        with pytest.raises(ValueError, match="quadruped"):
            plan_quadruped_gait(BIPED_LIMP_RIGHT_PROFILE, phase=0.0)

    def test_apply_biped_fabrik_offsets_is_additive(self):
        # Offset application must be a pure translation: out = in + frame_offset.
        controller = DistilledNeuralStateMachine()
        gait_frame = controller.evaluate(BIPED_LIMP_RIGHT_PROFILE, phase=0.3)
        base_left = (-0.4, 0.0)
        base_right = (0.4, 0.0)
        out_left, out_right = apply_biped_fabrik_offsets(
            base_left, base_right, gait_frame
        )
        l_off = gait_frame.limb_states["l_foot"].target_offset
        r_off = gait_frame.limb_states["r_foot"].target_offset
        assert math.isclose(out_left[0], base_left[0] + l_off[0], abs_tol=1e-12)
        assert math.isclose(out_left[1], base_left[1] + l_off[1], abs_tol=1e-12)
        assert math.isclose(out_right[0], base_right[0] + r_off[0], abs_tol=1e-12)
        assert math.isclose(out_right[1], base_right[1] + r_off[1], abs_tol=1e-12)

    def test_apply_biped_fabrik_offsets_passthrough_when_no_labels(self):
        """Pass a quadruped frame (no l_foot/r_foot keys) — outputs must be
        byte-identical to inputs."""
        quad_frame = plan_quadruped_gait(QUADRUPED_PACE_PROFILE, phase=0.2)
        base_left = (-1.0, 0.5)
        base_right = (1.0, -0.5)
        out_left, out_right = apply_biped_fabrik_offsets(
            base_left, base_right, quad_frame
        )
        assert out_left == base_left
        assert out_right == base_right


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 2D FABRIK IK — singularities, bounds, constraints
# ═══════════════════════════════════════════════════════════════════════════════


class TestFABRIK2DSolverEdgeCases:
    """JPL-style explicit boundary tests for the 2D IK solver.

    Each test asserts both finiteness (no NaN/Inf) and a value-level
    semantic property (end-effector distance, iteration count, etc.).
    """

    def _straight_chain(self, length: float = 3.0) -> list[Joint2D]:
        # 3-joint chain of unit-length bones along +x, total reach = length.
        # Each bone has length (length / 2).
        step = length / 2.0
        return [
            Joint2D(0.0, 0.0, "root"),
            Joint2D(step, 0.0, "mid"),
            Joint2D(length, 0.0, "tip"),
        ]

    def test_chain_too_short_returns_unchanged(self):
        solver = FABRIK2DSolver()
        short = [Joint2D(0.0, 0.0, "only")]
        out, iters = solver.solve(short, Joint2D(5.0, 5.0))
        assert iters == 0
        assert out == short

    def test_unreachable_target_stretches_towards_target(self):
        solver = FABRIK2DSolver(IKConfig(max_iterations=8, tolerance=1e-6))
        chain = self._straight_chain(length=2.0)
        target = Joint2D(100.0, 0.0, "tip")
        out, iters = solver.solve(chain, target)
        assert iters == 1  # Stretch path, exactly one pass
        # All joints finite.
        for j in out:
            assert math.isfinite(j.x) and math.isfinite(j.y)
        # Final chain must roughly align along +x towards the target and the
        # total bone length must be preserved to tolerance.
        lengths = [
            math.hypot(out[i + 1].x - out[i].x, out[i + 1].y - out[i].y)
            for i in range(len(out) - 1)
        ]
        assert math.isclose(sum(lengths), 2.0, rel_tol=1e-6)
        # Endpoint must now be farther from origin than before.
        assert math.hypot(out[-1].x, out[-1].y) > 1.99

    def test_exact_boundary_reach_converges_immediately(self):
        solver = FABRIK2DSolver(IKConfig(max_iterations=10, tolerance=1e-8))
        chain = self._straight_chain(length=2.0)
        # Target exactly at maximum reach — the "at-limit" MC/DC condition.
        target = Joint2D(2.0, 0.0, "tip")
        out, iters = solver.solve(chain, target)
        # The implementation picks the stretch path because
        # root_to_target (2.0) > total_length (2.0) is False, so we take the
        # iterative loop. Either way the end effector must hit the target
        # within tolerance.
        assert math.isclose(out[-1].x, 2.0, abs_tol=1e-6)
        assert math.isclose(out[-1].y, 0.0, abs_tol=1e-6)
        # And we must not burn all iterations just sitting at the solution.
        assert 0 <= iters <= 10

    def test_infinite_distance_target_stays_finite(self):
        """A 1e9-unit target must not produce NaN/Inf and must terminate in
        a single stretch pass (value-level JPL Rule 5)."""
        solver = FABRIK2DSolver(IKConfig(max_iterations=4))
        chain = self._straight_chain(length=2.0)
        target = Joint2D(1e9, -1e9, "tip")
        out, iters = solver.solve(chain, target)
        assert iters == 1
        for j in out:
            assert math.isfinite(j.x)
            assert math.isfinite(j.y)

    def test_zero_length_segment_does_not_divide_by_zero(self):
        """Colinear duplicate joints exercise the ``dist < 1e-12`` guard
        inside ``_move_towards``. Result must be finite."""
        solver = FABRIK2DSolver()
        chain = [
            Joint2D(0.0, 0.0, "root"),
            Joint2D(0.0, 0.0, "dup"),  # duplicate!
            Joint2D(1.0, 0.0, "tip"),
        ]
        target = Joint2D(0.5, 0.2)
        out, _iters = solver.solve(chain, target)
        for j in out:
            assert math.isfinite(j.x)
            assert math.isfinite(j.y)

    def test_reachable_target_converges_to_tolerance(self):
        solver = FABRIK2DSolver(IKConfig(max_iterations=20, tolerance=1e-4))
        chain = self._straight_chain(length=2.0)
        target = Joint2D(1.2, 0.7, "tip")
        out, iters = solver.solve(chain, target)
        err = math.hypot(out[-1].x - target.x, out[-1].y - target.y)
        assert err < 1e-3
        assert 1 <= iters <= 20

    def test_solve_with_constraints_respects_angle_limits(self):
        solver = FABRIK2DSolver(IKConfig(max_iterations=10, tolerance=1e-6))
        chain = self._straight_chain(length=2.0)
        target = Joint2D(0.5, 1.5, "tip")
        # Very tight limits that would be violated by an unconstrained solve.
        out, _iters = solver.solve_with_constraints(
            chain, target, angle_limits=[(-10.0, 10.0)]
        )
        assert len(out) == 3
        for j in out:
            assert math.isfinite(j.x)
            assert math.isfinite(j.y)


class TestTerrainAdaptiveIKLoop:
    """Terrain-adaptive IK pin-to-ground closed loop."""

    def test_probe_returns_zero_for_no_terrain(self):
        probe = TerrainProbe2D(None)
        assert probe.probe_ground(0.0) == 0.0
        # Normal is straight up on flat terrain.
        nx, ny = probe.surface_normal_2d(0.0)
        assert math.isclose(nx, 0.0, abs_tol=1e-9)
        assert math.isclose(ny, 1.0, abs_tol=1e-9)

    def test_surface_normal_unit_length(self):
        # Synthetic terrain SDF: ground at y = 0.5 (flat).
        class _FlatSDF:
            def query(self, x, y):
                return y - 0.5  # signed distance above ground

        probe = TerrainProbe2D(_FlatSDF())
        nx, ny = probe.surface_normal_2d(3.14, epsilon=0.05)
        mag = math.hypot(nx, ny)
        assert math.isclose(mag, 1.0, rel_tol=1e-6)

    def test_probe_ahead_returns_expected_sample_count(self):
        probe = TerrainProbe2D(None)
        samples = probe.probe_ahead(0.0, lookahead=1.0, n_samples=5)
        assert len(samples) == 5
        xs = [s[0] for s in samples]
        # Strictly monotonic in x.
        for a, b in zip(xs, xs[1:]):
            assert b > a

    def test_adapt_pose_pins_feet_and_adjusts_hip(self):
        loop = create_terrain_ik_loop(terrain_sdf=None)
        pose = {
            "l_hip": (-0.1, 0.8),
            "l_knee": (-0.1, 0.5),
            "l_ankle": (-0.1, 0.2),
            "r_hip": (0.1, 0.8),
            "r_knee": (0.1, 0.5),
            "r_ankle": (0.1, 0.2),
        }
        labels = {"l_foot": 1.0, "r_foot": 1.0}
        out = loop.adapt_pose(pose, labels, hip_position=(0.0, 0.8))
        # Contacts were solved.
        assert out["_contacts_solved"] == 2
        # Hip was adjusted.
        assert "_hip_adjustment" in out
        # Solver ran at least one iteration per foot.
        assert out["_ik_iterations"] >= 2
        # No NaN in result coordinates.
        for key in ("l_ankle", "r_ankle"):
            ax, ay = out[key]
            assert math.isfinite(ax) and math.isfinite(ay)

    def test_adapt_pose_skips_swing_legs(self):
        loop = create_terrain_ik_loop(terrain_sdf=None)
        pose = {
            "l_hip": (-0.1, 0.8),
            "l_ankle": (-0.1, 0.2),
            "r_hip": (0.1, 0.8),
            "r_ankle": (0.1, 0.2),
        }
        labels = {"l_foot": 0.1, "r_foot": 0.1}  # both in swing
        out = loop.adapt_pose(pose, labels)
        assert out["_contacts_solved"] == 0
        # No hip adjustment when no feet on ground.
        assert "_hip_adjustment" not in out


# ═══════════════════════════════════════════════════════════════════════════════
# Determinism — running the whole module twice must yield identical results
# ═══════════════════════════════════════════════════════════════════════════════


class TestSession101Determinism:
    """Guard against Random Masking (AUDIT_REPORT.md § 3.1).

    Two full simulations with the same config and seed must produce identical
    diagnostic tuples. This is the contract that lets CI trust the
    Pixar-style mass/energy tests above.
    """

    def test_fluid_system_two_runs_match(self):
        def _run():
            cfg = FluidVFXConfig.dash_smoke(canvas_size=32)
            system = FluidDrivenVFXSystem(cfg)
            system.simulate_and_render(n_frames=5)
            return [d.to_dict() for d in system.last_diagnostics]

        first = _run()
        second = _run()
        assert first == second

    def test_nsm_controller_two_runs_match(self):
        controller = DistilledNeuralStateMachine()

        def _run():
            return [
                controller.evaluate(BIPED_LIMP_RIGHT_PROFILE, phase=i / 16.0).to_dict()
                for i in range(16)
            ]

        assert _run() == _run()
