"""Unified Motion Representation (UMR) for animation pipeline closure.

This module introduces a strict shared contract for motion data, inspired by:

- Pixar OpenUSD `UsdSkel`: stable skeletal animation interchange schema
- SideFX Houdini KineFX: animation as point/attribute streams through filters
- Unreal Engine AnimGraph: intent -> base pose -> root motion -> localized correction

The design goal is deliberately narrow and practical for this repository:
all motion-producing and motion-correcting subsystems should be able to
communicate through the same frame structure instead of ad-hoc pose dicts.

Backward compatibility is preserved through `pose_to_umr()` and `umr_to_pose()`.
The renderer and legacy APIs can keep using raw `dict[str, float]` while the
internal trunk progressively converges on UMR.

SESSION-070 Extension — 3D-Safe Schema Widening (P1-XPBD-3 Prep)
-----------------------------------------------------------------
Following Pixar OpenUSD Schema backward-compatible extension principles:

1. ``MotionRootTransform`` gains optional ``z``, ``velocity_z``, and
   ``angular_velocity_3d`` fields (all default ``None``). Existing 2D
   consumers see unchanged scalar defaults.

2. ``MotionContactState`` gains an optional ``manifold`` dict for rich
   Contact Manifold records (support-point identity, lock weight, local
   contact offset, contact normal). Boolean tags remain the primary
   interface for 2D consumers.

3. ``UnifiedMotionFrame.metadata`` now enforces a ``joint_channel_schema``
   key (default ``"2d_scalar"``) that explicitly declares the rotation
   encoding used by the frame's joint data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Joint Channel Schema Constants (SESSION-070)
# ---------------------------------------------------------------------------

JOINT_CHANNEL_2D_SCALAR = "2d_scalar"
JOINT_CHANNEL_2D_PLUS_DEPTH = "2d_plus_depth"
JOINT_CHANNEL_3D_EULER = "3d_euler"
# SESSION-071 (P1-XPBD-3): Added quaternion encoding for 3D rotational state
# emitted by the new Physics3DBackend. Maintains additive backward compatibility:
# 2D consumers never observe this value because pure-2D inputs default to
# JOINT_CHANNEL_2D_SCALAR.
JOINT_CHANNEL_3D_QUATERNION = "3d_quaternion"

VALID_JOINT_CHANNEL_SCHEMAS = frozenset({
    JOINT_CHANNEL_2D_SCALAR,
    JOINT_CHANNEL_2D_PLUS_DEPTH,
    JOINT_CHANNEL_3D_EULER,
    JOINT_CHANNEL_3D_QUATERNION,
})


# ---------------------------------------------------------------------------
# Contact Manifold Record (SESSION-070 — XPBD-3 Prep)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContactManifoldRecord:
    """Rich contact information for a single support point.

    This replaces the boolean-only contact representation for 3D solver
    coupling. XPBD-3 grounding, friction, and anti-sliding constraints
    need support-point identity, lock weight, local offset, and contact
    normal.

    For 2D consumers, the boolean ``active`` flag is the only required
    field. All other fields default to safe neutral values.
    """

    limb: str = ""
    active: bool = False
    lock_weight: float = 0.0
    local_offset_x: float = 0.0
    local_offset_y: float = 0.0
    local_offset_z: float = 0.0
    normal_x: float = 0.0
    normal_y: float = 1.0
    normal_z: float = 0.0
    # SESSION-071 (P1-XPBD-3): NVIDIA PhysX / UE5 Chaos Physics-style fields.
    # Added world-space contact point and a non-negative penetration depth so
    # the 3D XPBD solver can build correct unilateral contact constraints and
    # friction. Defaults are zero so 2D consumers stay bit-compatible.
    contact_point_x: float = 0.0
    contact_point_y: float = 0.0
    contact_point_z: float = 0.0
    penetration_depth: float = 0.0
    source_solver: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "limb": self.limb,
            "active": bool(self.active),
            "lock_weight": float(self.lock_weight),
            "local_offset": [
                float(self.local_offset_x),
                float(self.local_offset_y),
                float(self.local_offset_z),
            ],
            "normal": [
                float(self.normal_x),
                float(self.normal_y),
                float(self.normal_z),
            ],
        }
        # SESSION-071: only serialize the 3D-physics fields when an authoring
        # solver actually populated them, keeping legacy serialized records
        # byte-identical to before.
        if (self.contact_point_x or self.contact_point_y or self.contact_point_z
                or self.penetration_depth or self.source_solver):
            d["contact_point"] = [
                float(self.contact_point_x),
                float(self.contact_point_y),
                float(self.contact_point_z),
            ]
            d["penetration_depth"] = float(self.penetration_depth)
            if self.source_solver:
                d["source_solver"] = self.source_solver
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContactManifoldRecord":
        offset = d.get("local_offset", [0.0, 0.0, 0.0])
        normal = d.get("normal", [0.0, 1.0, 0.0])
        cp = d.get("contact_point", [0.0, 0.0, 0.0])
        return cls(
            limb=str(d.get("limb", "")),
            active=bool(d.get("active", False)),
            lock_weight=float(d.get("lock_weight", 0.0)),
            local_offset_x=float(offset[0]) if len(offset) > 0 else 0.0,
            local_offset_y=float(offset[1]) if len(offset) > 1 else 0.0,
            local_offset_z=float(offset[2]) if len(offset) > 2 else 0.0,
            normal_x=float(normal[0]) if len(normal) > 0 else 0.0,
            normal_y=float(normal[1]) if len(normal) > 1 else 1.0,
            normal_z=float(normal[2]) if len(normal) > 2 else 0.0,
            contact_point_x=float(cp[0]) if len(cp) > 0 else 0.0,
            contact_point_y=float(cp[1]) if len(cp) > 1 else 0.0,
            contact_point_z=float(cp[2]) if len(cp) > 2 else 0.0,
            penetration_depth=float(d.get("penetration_depth", 0.0)),
            source_solver=str(d.get("source_solver", "")),
        )


@dataclass(frozen=True)
class MotionRootTransform:
    """Minimal root motion payload for a single frame.

    The repository is still largely 2D and pose-centric, so the transform stays
    lightweight: planar translation, rotation, and first-order velocities.

    SESSION-070 Extension: Optional 3D fields for XPBD-3 preparation.
    These are ``None`` by default, preserving full 2D backward compatibility.
    Existing consumers that only read ``x``, ``y``, ``rotation`` are unaffected.
    """

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    angular_velocity: float = 0.0
    # SESSION-070: Optional 3D expansion (None = 2D-only frame)
    z: Optional[float] = None
    velocity_z: Optional[float] = None
    angular_velocity_3d: Optional[Sequence[float]] = None  # [wx, wy, wz] Euler rates

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "x": float(self.x),
            "y": float(self.y),
            "rotation": float(self.rotation),
            "velocity_x": float(self.velocity_x),
            "velocity_y": float(self.velocity_y),
            "angular_velocity": float(self.angular_velocity),
        }
        # SESSION-070: Only serialize 3D fields when populated (backward compat)
        if self.z is not None:
            d["z"] = float(self.z)
        if self.velocity_z is not None:
            d["velocity_z"] = float(self.velocity_z)
        if self.angular_velocity_3d is not None:
            d["angular_velocity_3d"] = [float(v) for v in self.angular_velocity_3d]
        return d


@dataclass(frozen=True)
class MotionContactState:
    """Canonical contact tags for a motion frame.

    At minimum, left/right foot contact must be explicit because the current
    repository repeatedly needs this information in phase logic, skating
    cleanup, motion matching, and rendering heuristics.

    SESSION-070 Extension: Optional ``manifold`` list for rich Contact Manifold
    records. When present, each entry is a ``ContactManifoldRecord`` providing
    support-point identity, lock weight, local offset, and contact normal for
    XPBD-3 solver coupling. The boolean tags remain the primary interface for
    2D consumers and are always authoritative.
    """

    left_foot: bool = False
    right_foot: bool = False
    left_hand: bool = False
    right_hand: bool = False
    # SESSION-070: Optional rich contact manifold for 3D solver coupling
    manifold: Optional[tuple[ContactManifoldRecord, ...]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "left_foot": bool(self.left_foot),
            "right_foot": bool(self.right_foot),
            "left_hand": bool(self.left_hand),
            "right_hand": bool(self.right_hand),
        }
        # SESSION-070: Only serialize manifold when populated
        if self.manifold is not None:
            d["manifold"] = [rec.to_dict() for rec in self.manifold]
        return d


@dataclass(frozen=True)
class PhaseState:
    """Generalized Phase State — unified representation for cyclic and transient motion phases.

    Inspired by:
      - Local Motion Phases (Starke et al., SIGGRAPH 2020): per-bone local phases
        that break the single-global-cycle assumption.
      - DeepPhase / Periodic Autoencoder (Starke, Mason, Komura, SIGGRAPH 2022):
        multi-dimensional phase manifold where cyclic motions are sustained
        sinusoidal oscillations and transient motions are one-shot activation spikes.

    Design:
      - ``is_cyclic=True``:  value wraps in [0, 1) via modulo (walk, run, idle).
      - ``is_cyclic=False``: value clamped to [0, 1] with no wrapping (jump, fall, hit).
      - ``amplitude`` captures the DeepPhase-style activation strength (default 1.0).
      - ``phase_kind`` is a semantic label for downstream consumers.

    The gate mechanism inside ``PhaseDrivenAnimator.generate_frame()`` uses
    ``is_cyclic`` to select the interpolation path:
      - Cyclic  → sin/cos trig mapping → Catmull-Rom key-pose interpolation.
      - Transient → direct scalar [0,1] → Bezier/spline time parameter.

    References:
      [1] S. Starke et al., "Local Motion Phases for Learning Multi-Contact
          Character Movements", ACM TOG 39(4), 2020.
      [2] S. Starke, I. Mason, T. Komura, "DeepPhase: Periodic Autoencoders
          for Learning Motion Phase Manifolds", ACM TOG 41(4), 2022.
    """

    value: float
    is_cyclic: bool = True
    phase_kind: str = "cyclic"
    amplitude: float = 1.0

    def __post_init__(self) -> None:
        if self.is_cyclic:
            object.__setattr__(self, "value", float(self.value) % 1.0)
        else:
            object.__setattr__(self, "value", max(0.0, min(1.0, float(self.value))))
        object.__setattr__(self, "amplitude", max(0.0, float(self.amplitude)))

    def to_float(self) -> float:
        """Backward-compatible scalar output for legacy consumers."""
        return float(self.value)

    def to_sin_cos(self) -> tuple[float, float]:
        """Circular encoding to avoid discontinuity at 0/1 boundary.

        For cyclic phases this gives the standard trig mapping.
        For transient phases the mapping still works but the consumer
        should prefer the raw ``value`` via the gate mechanism.
        """
        import math as _math
        angle = 2.0 * _math.pi * float(self.value)
        return (_math.sin(angle), _math.cos(angle))

    def to_dict(self) -> dict[str, object]:
        return {
            "value": float(self.value),
            "is_cyclic": bool(self.is_cyclic),
            "phase_kind": str(self.phase_kind),
            "amplitude": float(self.amplitude),
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> "PhaseState":
        return cls(
            value=float(d.get("value", d.get("phase", 0.0))),  # type: ignore[arg-type]
            is_cyclic=bool(d.get("is_cyclic", True)),
            phase_kind=str(d.get("phase_kind", "cyclic")),
            amplitude=float(d.get("amplitude", 1.0)),
        )

    @classmethod
    def cyclic(cls, value: float, phase_kind: str = "cyclic") -> "PhaseState":
        """Factory for cyclic locomotion phases."""
        return cls(value=value, is_cyclic=True, phase_kind=phase_kind)

    @classmethod
    def transient(cls, value: float, phase_kind: str = "transient", amplitude: float = 1.0) -> "PhaseState":
        """Factory for one-shot transient phases (jump, fall, hit)."""
        return cls(value=value, is_cyclic=False, phase_kind=phase_kind, amplitude=amplitude)


@dataclass(frozen=True)
class UnifiedMotionFrame:
    """Strict cross-module motion frame contract.

    Required fields intentionally mirror the architecture request from the user:
    time, normalized phase, root transform, local joint rotations, and explicit
    contact tags.

    The ``phase_state`` field is the canonical phase representation (PhaseState).
    The legacy ``phase`` float field is kept for backward compatibility and is
    always derived from ``phase_state.to_float()``.

    SESSION-070 Extension: ``metadata["joint_channel_schema"]`` is now enforced
    to always be present (default ``"2d_scalar"``). This declares the rotation
    encoding used by the frame's joint data, enabling downstream consumers to
    discriminate between 2D scalar angles and future 3D Euler rotations without
    inspecting the actual joint values.
    """

    time: float
    phase: float
    root_transform: MotionRootTransform
    joint_local_rotations: dict[str, float]
    contact_tags: MotionContactState = field(default_factory=MotionContactState)
    frame_index: int = 0
    source_state: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    format_version: str = "umr_motion_frame_v1"
    phase_state: Optional[PhaseState] = None

    def __post_init__(self) -> None:
        metadata = dict(self.metadata)
        phase_kind = str(metadata.get("phase_kind", "cyclic"))
        phase_value = float(self.phase)

        # --- Generalized Phase State gate (Gap 1 resolution) ---
        # If a PhaseState was explicitly provided, it is the canonical source.
        # Otherwise, construct one from the legacy float + metadata.phase_kind.
        if self.phase_state is not None:
            ps = self.phase_state
            phase_value = ps.to_float()
            # Propagate phase_kind into metadata for legacy consumers
            if "phase_kind" not in metadata:
                metadata["phase_kind"] = ps.phase_kind
        else:
            is_cyclic = phase_kind not in {
                "distance_to_apex", "distance_to_ground", "hit_recovery",
                "transient",
            }
            ps = PhaseState(
                value=phase_value,
                is_cyclic=is_cyclic,
                phase_kind=phase_kind,
            )
            phase_value = ps.to_float()

        # SESSION-070: Enforce joint_channel_schema metadata (default 2d_scalar)
        if "joint_channel_schema" not in metadata:
            metadata["joint_channel_schema"] = JOINT_CHANNEL_2D_SCALAR

        object.__setattr__(self, "phase", phase_value)
        object.__setattr__(self, "phase_state", ps)
        object.__setattr__(
            self,
            "joint_local_rotations",
            {k: float(v) for k, v in dict(self.joint_local_rotations).items()},
        )
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "format": self.format_version,
            "time": float(self.time),
            "phase": float(self.phase),
            "frame_index": int(self.frame_index),
            "source_state": self.source_state,
            "root_transform": self.root_transform.to_dict(),
            "joint_local_rotations": dict(self.joint_local_rotations),
            "contact_tags": self.contact_tags.to_dict(),
            "metadata": dict(self.metadata),
        }
        if self.phase_state is not None:
            d["phase_state"] = self.phase_state.to_dict()
        return d

    def with_pose(self, pose: Mapping[str, float], **metadata_updates: Any) -> "UnifiedMotionFrame":
        merged = dict(self.metadata)
        merged.update(metadata_updates)
        return replace(
            self,
            joint_local_rotations={k: float(v) for k, v in dict(pose).items()},
            metadata=merged,
        )

    def with_root(self, root_transform: MotionRootTransform, **metadata_updates: Any) -> "UnifiedMotionFrame":
        merged = dict(self.metadata)
        merged.update(metadata_updates)
        return replace(self, root_transform=root_transform, metadata=merged)

    def with_contacts(self, contact_tags: MotionContactState, **metadata_updates: Any) -> "UnifiedMotionFrame":
        merged = dict(self.metadata)
        merged.update(metadata_updates)
        return replace(self, contact_tags=contact_tags, metadata=merged)


@dataclass
class UnifiedMotionClip:
    """A sequence of UMR frames plus clip-level metadata."""

    clip_id: str
    state: str
    fps: int
    frames: list[UnifiedMotionFrame]
    metadata: dict[str, Any] = field(default_factory=dict)
    format_version: str = "umr_motion_clip_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format_version,
            "clip_id": self.clip_id,
            "state": self.state,
            "fps": int(self.fps),
            "frame_count": len(self.frames),
            "metadata": dict(self.metadata),
            "frames": [frame.to_dict() for frame in self.frames],
        }

    def save(
        self,
        path: str | Path,
        *,
        apply_anime_timing: bool = True,
        apply_squash_stretch: bool = True,
    ) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        export_clip = self
        if apply_anime_timing:
            from mathart.animation.anime_timing_modifier import apply_to_unified_motion_clip as apply_anime_timing_to_clip
            export_clip = apply_anime_timing_to_clip(export_clip)
        if apply_squash_stretch:
            from mathart.animation.squash_stretch_modifier import apply_to_unified_motion_clip
            export_clip = apply_to_unified_motion_clip(export_clip)
        path.write_text(json.dumps(export_clip.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnifiedMotionClip":
        """Deserialize a clip from a dictionary (SESSION-070).

        This enables round-trip serialization through the Context-in /
        Manifest-out boundary: the motion backend serializes clips to JSON,
        and the pipeline deserializes them back for downstream processing.
        """
        frames: list[UnifiedMotionFrame] = []
        for fd in data.get("frames", []):
            root_d = fd.get("root_transform", {})
            root = MotionRootTransform(
                x=float(root_d.get("x", 0.0)),
                y=float(root_d.get("y", 0.0)),
                rotation=float(root_d.get("rotation", 0.0)),
                velocity_x=float(root_d.get("velocity_x", 0.0)),
                velocity_y=float(root_d.get("velocity_y", 0.0)),
                angular_velocity=float(root_d.get("angular_velocity", 0.0)),
                z=float(root_d["z"]) if "z" in root_d else None,
                velocity_z=float(root_d["velocity_z"]) if "velocity_z" in root_d else None,
                angular_velocity_3d=(
                    [float(v) for v in root_d["angular_velocity_3d"]]
                    if "angular_velocity_3d" in root_d else None
                ),
            )
            contact_d = fd.get("contact_tags", {})
            manifold_raw = contact_d.get("manifold")
            manifold = None
            if manifold_raw is not None:
                manifold = tuple(ContactManifoldRecord.from_dict(m) for m in manifold_raw)
            contacts = MotionContactState(
                left_foot=bool(contact_d.get("left_foot", False)),
                right_foot=bool(contact_d.get("right_foot", False)),
                left_hand=bool(contact_d.get("left_hand", False)),
                right_hand=bool(contact_d.get("right_hand", False)),
                manifold=manifold,
            )
            ps_d = fd.get("phase_state")
            ps = PhaseState.from_dict(ps_d) if ps_d else None
            frames.append(UnifiedMotionFrame(
                time=float(fd.get("time", 0.0)),
                phase=float(fd.get("phase", 0.0)),
                root_transform=root,
                joint_local_rotations={
                    k: float(v) for k, v in fd.get("joint_local_rotations", {}).items()
                },
                contact_tags=contacts,
                frame_index=int(fd.get("frame_index", 0)),
                source_state=str(fd.get("source_state", "")),
                metadata=dict(fd.get("metadata", {})),
                format_version=str(fd.get("format", "umr_motion_frame_v1")),
                phase_state=ps,
            ))
        return cls(
            clip_id=str(data.get("clip_id", "")),
            state=str(data.get("state", "")),
            fps=int(data.get("fps", 12)),
            frames=frames,
            metadata=dict(data.get("metadata", {})),
            format_version=str(data.get("format", "umr_motion_clip_v1")),
        )


@dataclass(frozen=True)
class MotionPipelineAuditEntry:
    """One audit record for a pipeline node processing one frame."""

    node_name: str
    stage: str
    frame_index: int
    time: float
    phase: float
    source_state: str
    metadata_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_name": self.node_name,
            "stage": self.stage,
            "frame_index": int(self.frame_index),
            "time": float(self.time),
            "phase": float(self.phase),
            "source_state": self.source_state,
            "metadata_snapshot": dict(self.metadata_snapshot),
        }


@dataclass
class MotionPipelineResult:
    """Clip plus structured audit trail from the filter chain."""

    clip: UnifiedMotionClip
    audit_log: list[MotionPipelineAuditEntry] = field(default_factory=list)

    def audit_summary(self) -> dict[str, Any]:
        stage_counts: dict[str, int] = {}
        node_counts: dict[str, int] = {}
        for entry in self.audit_log:
            stage_counts[entry.stage] = stage_counts.get(entry.stage, 0) + 1
            node_counts[entry.node_name] = node_counts.get(entry.node_name, 0) + 1
        return {
            "entry_count": len(self.audit_log),
            "stage_counts": stage_counts,
            "node_counts": node_counts,
        }


@dataclass
class MotionPipelineNode:
    """A lightweight KineFX/AnimGraph-style frame filter node."""

    name: str
    stage: str
    processor: Callable[[UnifiedMotionFrame, float], UnifiedMotionFrame]

    def process(self, frame: UnifiedMotionFrame, dt: float) -> UnifiedMotionFrame:
        return self.processor(frame, dt)


def umr_to_pose(frame: UnifiedMotionFrame | Mapping[str, float]) -> dict[str, float]:
    """Convert a UMR frame back into the legacy pose-dict contract."""

    if isinstance(frame, UnifiedMotionFrame):
        return dict(frame.joint_local_rotations)
    return {k: float(v) for k, v in dict(frame).items()}


def pose_to_umr(
    pose: Mapping[str, float],
    *,
    time: float = 0.0,
    phase: float = 0.0,
    root_transform: Optional[MotionRootTransform] = None,
    contact_tags: Optional[MotionContactState] = None,
    frame_index: int = 0,
    source_state: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
    phase_state: Optional[PhaseState] = None,
) -> UnifiedMotionFrame:
    """Wrap a legacy pose dict in the UMR contract.

    If ``phase_state`` is provided, it becomes the canonical phase source.
    Otherwise a PhaseState is auto-constructed from ``phase`` + ``metadata.phase_kind``.
    """

    metadata_dict = dict(metadata or {})

    if phase_state is not None:
        # PhaseState is the canonical source; derive scalar for backward compat
        normalized_phase = phase_state.to_float()
    else:
        phase_kind = str(metadata_dict.get("phase_kind", "cyclic"))
        normalized_phase = float(phase)
        is_cyclic = phase_kind not in {
            "distance_to_apex", "distance_to_ground", "hit_recovery", "transient",
        }
        phase_state = PhaseState(
            value=normalized_phase,
            is_cyclic=is_cyclic,
            phase_kind=phase_kind,
        )
        normalized_phase = phase_state.to_float()

    return UnifiedMotionFrame(
        time=float(time),
        phase=normalized_phase,
        root_transform=root_transform or MotionRootTransform(),
        joint_local_rotations={k: float(v) for k, v in dict(pose).items()},
        contact_tags=contact_tags or MotionContactState(),
        frame_index=int(frame_index),
        source_state=source_state,
        metadata=metadata_dict,
        phase_state=phase_state,
    )


def infer_contact_tags(phase: float, state: str) -> MotionContactState:
    """Infer conservative foot contacts from state + phase.

    This is intentionally deterministic and lightweight. It avoids the current
    situation where multiple subsystems repeatedly re-guess contact timing from
    raw poses. For non-locomotion states we choose conservative defaults.
    """

    p = float(phase) % 1.0
    state_key = str(state).lower()

    if state_key == "run":
        left = (p >= 0.0 and p < 0.18) or (p >= 0.92 and p < 1.0)
        right = p >= 0.42 and p < 0.68
        return MotionContactState(left_foot=left, right_foot=right)
    if state_key in {"walk", "idle"}:
        left = (p >= 0.0 and p < 0.32) or (p >= 0.90 and p < 1.0)
        right = p >= 0.50 and p < 0.82
        if state_key == "idle":
            left = True
            right = True
        return MotionContactState(left_foot=left, right_foot=right)
    if state_key == "jump":
        return MotionContactState(left_foot=False, right_foot=False)
    if state_key == "fall":
        return MotionContactState(left_foot=False, right_foot=False)
    if state_key == "hit":
        return MotionContactState(left_foot=True, right_foot=True)
    return MotionContactState()


def run_motion_pipeline(
    clip: UnifiedMotionClip,
    nodes: list[MotionPipelineNode],
    *,
    dt: Optional[float] = None,
) -> MotionPipelineResult:
    """Run a sequence of frame filters over a clip and collect audit traces."""

    if not nodes:
        return MotionPipelineResult(clip=clip, audit_log=[])

    frame_dt = float(dt) if dt is not None else (1.0 / max(1, int(clip.fps)))
    frames = list(clip.frames)
    audit_log: list[MotionPipelineAuditEntry] = []

    for node in nodes:
        next_frames: list[UnifiedMotionFrame] = []
        for frame in frames:
            processed = node.process(frame, frame_dt)
            next_frames.append(processed)
            audit_log.append(
                MotionPipelineAuditEntry(
                    node_name=node.name,
                    stage=node.stage,
                    frame_index=processed.frame_index,
                    time=processed.time,
                    phase=processed.phase,
                    source_state=processed.source_state,
                    metadata_snapshot=dict(processed.metadata),
                )
            )
        frames = next_frames

    merged_meta = dict(clip.metadata)
    merged_meta.setdefault("motion_pipeline", {})
    merged_meta["motion_pipeline"].update(
        {
            "node_order": [node.name for node in nodes],
            "stage_order": [node.stage for node in nodes],
            "audit_entry_count": len(audit_log),
        }
    )

    out_clip = UnifiedMotionClip(
        clip_id=clip.clip_id,
        state=clip.state,
        fps=clip.fps,
        frames=frames,
        metadata=merged_meta,
        format_version=clip.format_version,
    )
    return MotionPipelineResult(clip=out_clip, audit_log=audit_log)


__all__ = [
    "ContactManifoldRecord",
    "JOINT_CHANNEL_2D_SCALAR",
    "JOINT_CHANNEL_2D_PLUS_DEPTH",
    "JOINT_CHANNEL_3D_EULER",
    "JOINT_CHANNEL_3D_QUATERNION",
    "VALID_JOINT_CHANNEL_SCHEMAS",
    "PhaseState",
    "MotionRootTransform",
    "MotionContactState",
    "UnifiedMotionFrame",
    "UnifiedMotionClip",
    "MotionPipelineAuditEntry",
    "MotionPipelineResult",
    "MotionPipelineNode",
    "pose_to_umr",
    "umr_to_pose",
    "infer_contact_tags",
    "run_motion_pipeline",
]
