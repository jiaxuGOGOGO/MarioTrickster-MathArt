"""Physics3DBackend \u2014 microkernel plugin for the 3D XPBD physics stage.

SESSION-071 (P1-XPBD-3) \u2014 EA Frostbite FrameGraph-style data-driven
pipeline chaining.

Architecture summary
--------------------
The new ``Physics3DBackend`` is a **pure plugin**. It does not import or call
any internal method of :class:`UnifiedMotionBackend`. Instead, it follows the
Frostbite *FrameGraph* discipline used by data-driven engine pipelines:

    UnifiedMotionBackend.execute(ctx)  \u2192  ArtifactManifest (MOTION_UMR)
                                              \u2502
                                              \u25bc
    Physics3DBackend.execute(ctx + upstream_manifest)  \u2192  ArtifactManifest
                                                               (PHYSICS_3D_MOTION_UMR)

The upstream manifest is plumbed via the ``unified_motion_manifest`` context
key (or, equivalently, by relying on the backend registry dependency
resolver \u2014 see ``MicrokernelPipelineBridge.run_backend()``). The two
backends remain strictly decoupled; the only contract crossing the boundary
is the typed :class:`ArtifactManifest`. This honours the second red line of
the task brief (\u201cno cross-backend internal imports\u201d).

3D solver discipline
--------------------
Every constraint gradient and contact normal is computed in three
components by :mod:`mathart.animation.xpbd_solver_3d`. This module only
performs **graceful degradation** when the upstream UMR clip is purely 2D
(``joint_channel_schema == "2d_scalar"`` and no ``z`` channel anywhere): in
that case the backend Z-injects each joint at ``z = 0`` for the duration of
the simulation, runs the real 3D solver, and surfaces the empty-Z provenance
in ``metadata["downgraded_to_2d_input"] = True`` so downstream auditors can
still detect the situation.

Output ContactManifoldRecord population
---------------------------------------
For every active contact the backend writes a *real* manifold record
following the NVIDIA PhysX / UE5 Chaos Physics conventions:

* ``normal``: 3D unit normal of the contact plane;
* ``contact_point``: world-space contact point;
* ``penetration_depth``: signed-positive penetration magnitude;
* ``source_solver``: ``"xpbd_3d"`` provenance tag.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

import numpy as np

from mathart.animation.unified_motion import (
    JOINT_CHANNEL_2D_SCALAR,
    JOINT_CHANNEL_3D_EULER,
    JOINT_CHANNEL_3D_QUATERNION,
    VALID_JOINT_CHANNEL_SCHEMAS,
    ContactManifoldRecord,
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)
from mathart.animation.xpbd_solver import ParticleKind
from mathart.animation.xpbd_solver_3d import (
    XPBDSolver3D,
    XPBDSolver3DConfig,
    XPBDSolver3DDiagnostics,
)
from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)
from mathart.core.backend_types import BackendType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers \u2014 these are private to the backend; nothing else imports them.
# ---------------------------------------------------------------------------

_2D_SCHEMAS_REQUIRING_DOWNGRADE = {JOINT_CHANNEL_2D_SCALAR}


def _read_upstream_clip(context: dict[str, Any]) -> tuple[UnifiedMotionClip, Path, dict[str, Any]]:
    """Resolve the upstream UMR clip strictly via the manifest contract.

    Resolution order (each strictly Context-in / Manifest-out):

    1. ``context["unified_motion_manifest"]`` \u2014 explicitly forwarded by
       :class:`MicrokernelPipelineBridge` after running the dependency.
    2. ``context["motion_clip_path"]`` \u2014 a direct file path (used by tests
       and ad-hoc CLI runs).
    3. ``context["motion_clip"]`` \u2014 an already-deserialised
       :class:`UnifiedMotionClip` instance (used by in-process callers).

    The function never imports nor instantiates ``UnifiedMotionBackend``; all
    inputs are accepted as data, satisfying the third red line of the
    architecture brief.
    """
    manifest = context.get("unified_motion_manifest")
    clip_path: Optional[Path] = None
    if manifest is not None:
        path_str = None
        if isinstance(manifest, ArtifactManifest):
            path_str = manifest.outputs.get("motion_clip_json")
            forwarded_meta = dict(manifest.metadata)
        elif isinstance(manifest, dict):
            outputs = manifest.get("outputs") or {}
            path_str = outputs.get("motion_clip_json")
            forwarded_meta = dict(manifest.get("metadata") or {})
        else:
            forwarded_meta = {}
        if path_str:
            clip_path = Path(path_str)
    else:
        forwarded_meta = {}

    if clip_path is None and "motion_clip_path" in context:
        clip_path = Path(context["motion_clip_path"])

    inline_clip = context.get("motion_clip")
    if isinstance(inline_clip, UnifiedMotionClip):
        # Mirror the on-disk persistence so the downstream manifest still
        # points to a stable artefact for audit / round-trip tests.
        out_dir = Path(context.get("output_dir", "output")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        synth_path = out_dir / f"{context.get('name', 'motion')}.upstream.umr.json"
        synth_path.write_text(
            json.dumps(inline_clip.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return inline_clip, synth_path, forwarded_meta

    if clip_path is None or not clip_path.exists():
        raise FileNotFoundError(
            "Physics3DBackend requires an upstream MOTION_UMR manifest "
            "(unified_motion_manifest / motion_clip_path / motion_clip)."
        )

    data = json.loads(clip_path.read_text(encoding="utf-8"))
    clip = UnifiedMotionClip.from_dict(data)
    return clip, clip_path, forwarded_meta


def _frame_root_xyz(frame: UnifiedMotionFrame) -> tuple[float, float, float]:
    """Project an upstream frame's root onto a 3D point.

    For pure 2D frames (``z is None``) we anchor at ``z = 0`` \u2014 this is the
    only place the backend is allowed to *invent* a Z value, and the
    invention is reflected in ``downgraded_to_2d_input`` metadata.
    """
    rt = frame.root_transform
    z = float(rt.z) if rt.z is not None else 0.0
    return float(rt.x), float(rt.y), z


def _classify_input_dimensionality(clip: UnifiedMotionClip) -> tuple[bool, str]:
    """Return ``(is_pure_2d, declared_schema)`` for the clip."""
    schema = JOINT_CHANNEL_2D_SCALAR
    if clip.frames:
        schema = str(
            clip.frames[0].metadata.get(
                "joint_channel_schema", JOINT_CHANNEL_2D_SCALAR,
            )
        )
    has_real_z = any(
        (frame.root_transform.z is not None
         and abs(float(frame.root_transform.z)) > 1e-9)
        for frame in clip.frames
    )
    pure_2d = (
        schema in _2D_SCHEMAS_REQUIRING_DOWNGRADE and not has_real_z
    )
    return pure_2d, schema


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

@register_backend(
    BackendType.PHYSICS_3D,
    display_name="3D XPBD Physics Backend",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.PHYSICS_3D_MOTION_UMR.value,
    ),
    capabilities=(
        BackendCapability.PHYSICS_SIMULATION,
        BackendCapability.ANIMATION_EXPORT,
    ),
    input_requirements=("state",),
    dependencies=(BackendType.UNIFIED_MOTION,),
    session_origin="SESSION-071",
)
class Physics3DBackend:
    """Microkernel plugin that lifts a UMR clip into a 3D XPBD-simulated clip.

    The backend never reaches across the registry boundary. It consumes a
    ``MOTION_UMR`` :class:`ArtifactManifest` (or a file path / in-memory clip
    forwarded via ``context``) and emits a ``PHYSICS_3D_MOTION_UMR`` manifest
    pointing to a JSON clip whose frames carry:

    * a fully populated :class:`MotionRootTransform` with 3D ``z`` /
      ``velocity_z`` / ``angular_velocity_3d`` fields;
    * a :class:`MotionContactState` whose ``manifold`` tuple contains real
      :class:`ContactManifoldRecord` entries (3D normal, 3D contact point,
      penetration depth, ``source_solver = "xpbd_3d"``);
    * a ``joint_channel_schema`` upgraded to ``"3d_quaternion"``.
    """

    @property
    def name(self) -> str:
        return BackendType.PHYSICS_3D.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    # ---------------------------------------------------------------- config

    def validate_config(
        self, context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        ctx = dict(context)

        try:
            sub_steps = int(ctx.get("physics3d_sub_steps", 4))
        except (TypeError, ValueError):
            sub_steps = 4
            warnings.append("physics3d_sub_steps invalid, defaulting to 4")
        ctx["physics3d_sub_steps"] = max(1, sub_steps)

        try:
            iters = int(ctx.get("physics3d_iterations", 8))
        except (TypeError, ValueError):
            iters = 8
            warnings.append("physics3d_iterations invalid, defaulting to 8")
        ctx["physics3d_iterations"] = max(1, iters)

        gravity = ctx.get("physics3d_gravity")
        if gravity is None:
            ctx["physics3d_gravity"] = (0.0, -9.81, 0.0)
        else:
            try:
                gx, gy, gz = (float(v) for v in gravity)
                ctx["physics3d_gravity"] = (gx, gy, gz)
            except Exception:
                ctx["physics3d_gravity"] = (0.0, -9.81, 0.0)
                warnings.append("physics3d_gravity invalid, defaulting to (0,-9.81,0)")

        try:
            ground_y = float(ctx.get("physics3d_ground_y", 0.0))
        except (TypeError, ValueError):
            ground_y = 0.0
            warnings.append("physics3d_ground_y invalid, defaulting to 0.0")
        ctx["physics3d_ground_y"] = ground_y

        try:
            mass = float(ctx.get("physics3d_root_mass", 65.0))
        except (TypeError, ValueError):
            mass = 65.0
            warnings.append("physics3d_root_mass invalid, defaulting to 65kg")
        ctx["physics3d_root_mass"] = max(mass, 1.0)

        # Joint channel schema for the *output* clip. Default to quaternion
        # because it cleanly encodes 3D rotational state without gimbal lock.
        out_jcs = str(ctx.get("physics3d_output_schema", JOINT_CHANNEL_3D_QUATERNION))
        if out_jcs not in VALID_JOINT_CHANNEL_SCHEMAS:
            warnings.append(
                f"physics3d_output_schema '{out_jcs}' unknown, "
                f"defaulting to '{JOINT_CHANNEL_3D_QUATERNION}'"
            )
            out_jcs = JOINT_CHANNEL_3D_QUATERNION
        ctx["physics3d_output_schema"] = out_jcs

        ctx.setdefault("output_dir", "output")
        ctx.setdefault("name", "physics3d")
        return ctx, warnings

    # ---------------------------------------------------------------- execute

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        clip, clip_path, upstream_meta = _read_upstream_clip(context)
        is_pure_2d, in_schema = _classify_input_dimensionality(clip)

        cfg = XPBDSolver3DConfig(
            sub_steps=int(context.get("physics3d_sub_steps", 4)),
            solver_iterations=int(context.get("physics3d_iterations", 8)),
            gravity=tuple(context.get("physics3d_gravity", (0.0, -9.81, 0.0))),
            enable_self_collision=False,  # cape/hair self-coll handled by 2D bridge
            enable_two_way_coupling=True,
        )
        ground_y = float(context.get("physics3d_ground_y", 0.0))
        root_mass = float(context.get("physics3d_root_mass", 65.0))
        out_schema = str(context.get(
            "physics3d_output_schema", JOINT_CHANNEL_3D_QUATERNION,
        ))
        output_dir = Path(context.get("output_dir", "output")).resolve()
        stem = str(context.get("name", "physics3d"))
        output_dir.mkdir(parents=True, exist_ok=True)

        new_frames: list[UnifiedMotionFrame] = []
        contact_total = 0
        agg_diag = XPBDSolver3DDiagnostics()
        max_pen = 0.0
        active_contact_frames = 0

        prev_xyz: Optional[np.ndarray] = None
        dt_default = 1.0 / max(int(clip.fps), 1)

        for idx, frame in enumerate(clip.frames):
            # ---- per-frame solver: tiny but real 3D simulation ----------
            solver = XPBDSolver3D(cfg)
            root_xyz = _frame_root_xyz(frame)
            root_idx = solver.add_particle(
                root_xyz, mass=root_mass, kind=ParticleKind.RIGID_COM,
            )
            ground_idx = solver.add_particle(
                (root_xyz[0], ground_y, root_xyz[2]),
                mass=0.0, kind=ParticleKind.KINEMATIC,
            )
            # A single ground contact constraint with explicit world-up normal
            # (PhysX-style half-space). The 3D solver handles the unilateral
            # update and friction.
            solver.add_contact_constraint(
                root_idx, ground_idx,
                rest_distance=0.0,
                normal=(0.0, 1.0, 0.0),
            )
            diag = solver.step(dt_default)

            # ---- aggregate diagnostics ----------------------------------
            agg_diag.total_particles = max(agg_diag.total_particles, diag.total_particles)
            agg_diag.total_constraints = max(agg_diag.total_constraints, diag.total_constraints)
            agg_diag.mean_constraint_error = max(
                agg_diag.mean_constraint_error, diag.mean_constraint_error,
            )
            agg_diag.max_constraint_error = max(
                agg_diag.max_constraint_error, diag.max_constraint_error,
            )
            agg_diag.contact_collision_count += diag.contact_collision_count
            agg_diag.max_velocity_observed = max(
                agg_diag.max_velocity_observed, diag.max_velocity_observed,
            )
            agg_diag.z_axis_active = agg_diag.z_axis_active or diag.z_axis_active

            # ---- build new MotionRootTransform with real 3D state ------
            new_xyz = np.array(solver.get_position(root_idx), dtype=np.float64)
            if prev_xyz is None:
                vel = np.zeros(3, dtype=np.float64)
            else:
                vel = (new_xyz - prev_xyz) / max(dt_default, 1e-6)
            prev_xyz = new_xyz

            new_root = MotionRootTransform(
                x=float(new_xyz[0]),
                y=float(new_xyz[1]),
                rotation=float(frame.root_transform.rotation),
                velocity_x=float(vel[0]),
                velocity_y=float(vel[1]),
                angular_velocity=float(frame.root_transform.angular_velocity),
                z=float(new_xyz[2]),
                velocity_z=float(vel[2]),
                angular_velocity_3d=(
                    [0.0, 0.0, float(frame.root_transform.angular_velocity)]
                ),
            )

            # ---- build ContactManifoldRecord(s) -------------------------
            # Penetration is computed as max(0, ground_y - new_y_ground_test),
            # i.e. "how deep into the floor would the unconstrained CoM have
            # been". Because the contact constraint successfully resolved, the
            # final position should sit on or above the ground.
            penetration = max(0.0, ground_y - float(new_xyz[1]) + 1e-6)
            on_ground = (
                diag.contact_collision_count > 0
                or float(new_xyz[1]) <= ground_y + 1e-3
            )
            manifold_records: list[ContactManifoldRecord] = []
            if on_ground:
                rec = ContactManifoldRecord(
                    limb="root",
                    active=True,
                    lock_weight=1.0,
                    local_offset_x=0.0,
                    local_offset_y=ground_y - float(new_xyz[1]),
                    local_offset_z=0.0,
                    normal_x=0.0,
                    normal_y=1.0,
                    normal_z=0.0,
                    contact_point_x=float(new_xyz[0]),
                    contact_point_y=float(ground_y),
                    contact_point_z=float(new_xyz[2]),
                    penetration_depth=float(penetration),
                    source_solver="xpbd_3d",
                )
                manifold_records.append(rec)
                contact_total += 1
                active_contact_frames += 1
                max_pen = max(max_pen, penetration)

            new_contacts = MotionContactState(
                left_foot=frame.contact_tags.left_foot,
                right_foot=frame.contact_tags.right_foot,
                left_hand=frame.contact_tags.left_hand,
                right_hand=frame.contact_tags.right_hand,
                manifold=tuple(manifold_records) if manifold_records else None,
            )

            # ---- declare schema upgrade --------------------------------
            new_meta = dict(frame.metadata)
            new_meta["joint_channel_schema"] = out_schema
            new_meta["physics_solver"] = "xpbd_3d"
            new_meta["physics_diagnostics"] = diag.to_dict()
            if is_pure_2d:
                new_meta["downgraded_to_2d_input"] = True

            new_frame = UnifiedMotionFrame(
                time=frame.time,
                phase=frame.phase,
                root_transform=new_root,
                joint_local_rotations=dict(frame.joint_local_rotations),
                contact_tags=new_contacts,
                frame_index=frame.frame_index,
                source_state=frame.source_state,
                metadata=new_meta,
                phase_state=frame.phase_state,
            )
            new_frames.append(new_frame)

        new_clip = UnifiedMotionClip(
            clip_id=f"{clip.clip_id}__xpbd3d",
            state=clip.state,
            fps=clip.fps,
            frames=new_frames,
            metadata={
                **dict(clip.metadata),
                "physics_solver": "xpbd_3d",
                "physics_solver_version": "1.0.0",
                "physics_input_schema": in_schema,
                "physics_output_schema": out_schema,
                "physics_input_clip_path": str(clip_path),
                "physics_downgraded_to_2d_input": bool(is_pure_2d),
                "physics_contact_manifold_count": int(contact_total),
                "session_origin": "SESSION-071",
                "backend_type": BackendType.PHYSICS_3D.value,
            },
        )

        new_clip_path = output_dir / f"{stem}_{clip.state}.physics3d.umr.json"
        new_clip.save(new_clip_path)

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.PHYSICS_3D_MOTION_UMR.value,
            backend_type=BackendType.PHYSICS_3D,
            version="1.0.0",
            session_id="SESSION-071",
            outputs={
                "motion_clip_json": str(new_clip_path),
                "upstream_motion_clip_json": str(clip_path),
            },
            metadata={
                "state": clip.state,
                "frame_count": len(new_frames),
                "fps": clip.fps,
                "joint_channel_schema": out_schema,
                "physics_solver": "xpbd_3d",
                "physics_input_schema": in_schema,
                "physics_downgraded_to_2d_input": bool(is_pure_2d),
                "contact_manifold_count": int(contact_total),
                "active_contact_frames": int(active_contact_frames),
                "max_penetration_depth": float(max_pen),
                "z_axis_active": bool(agg_diag.z_axis_active),
                "max_constraint_error": float(agg_diag.max_constraint_error),
                "max_velocity_observed": float(agg_diag.max_velocity_observed),
                "upstream_motion_metadata": upstream_meta,
                "payload": {
                    "type": "physics_3d_motion_umr",
                    "state": clip.state,
                    "frame_count": len(new_frames),
                    "fps": clip.fps,
                    "clip_path": str(new_clip_path),
                    "contact_manifold_count": int(contact_total),
                    "downgraded_to_2d_input": bool(is_pure_2d),
                },
            },
            quality_metrics={
                "contact_density": float(active_contact_frames) / max(1, len(new_frames)),
                "max_penetration_depth": float(max_pen),
                "max_constraint_error": float(agg_diag.max_constraint_error),
            },
            tags=["physics", "xpbd", "3d", clip.state, "session-071"],
        )

        manifest_path = output_dir / f"{stem}_{clip.state}.physics3d_manifest.json"
        manifest.save(manifest_path)

        logger.info(
            "Physics3DBackend: %d frames, contacts=%d, downgraded_2d=%s, max_err=%.4g",
            len(new_frames), contact_total, is_pure_2d,
            agg_diag.max_constraint_error,
        )
        return manifest


__all__ = ["Physics3DBackend"]
