"""SESSION-058 — Distilled Neural State Machine / DeepPhase gait control.

This module does not attempt to reproduce Sebastian Starke's neural models
verbatim.  Instead it distills the *operational essence* of Neural State
Machine, Local Motion Phases, and DeepPhase into a repository-native runtime
controller that can already drive:

1. Multi-contact label prediction per limb.
2. Asymmetric biped locomotion (limp / injured gait).
3. Quadruped contact-phase planning for alien or creature rigs.
4. FABRIK-compatible foot target offsets for the repository's existing
   procedural locomotion stack.

The result is intentionally deterministic, lightweight, and testable, so it can
participate in the project's internal evolution loop immediately.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional
import math

from .phase_driven import PhaseChannel

Morphology = Literal["biped", "quadruped"]


@dataclass(frozen=True)
class LimbPhaseModel:
    """Distilled per-limb local phase / contact descriptor."""

    name: str
    phase_offset: float = 0.0
    duty_factor: float = 0.6
    stride_scale: float = 1.0
    swing_height_scale: float = 1.0
    stance_bias_x: float = 0.0
    stance_bias_y: float = 0.0
    lift_channel: PhaseChannel = field(default_factory=lambda: PhaseChannel(amplitude=1.0, frequency=1.0))

    def local_phase(self, global_phase: float) -> float:
        return (float(global_phase) + self.phase_offset) % 1.0

    def contact_probability(self, global_phase: float, sharpness: float = 8.0) -> float:
        p = self.local_phase(global_phase)
        center = 0.5 * self.duty_factor
        width = max(self.duty_factor * 0.5, 1e-4)
        dist = min(abs(p - center), abs(p - center + 1.0), abs(p - center - 1.0))
        signed = 1.0 - dist / width
        return 1.0 / (1.0 + math.exp(-sharpness * signed))

    def in_contact(self, global_phase: float, threshold: float = 0.5) -> bool:
        return self.contact_probability(global_phase) >= threshold

    def swing_phase(self, global_phase: float) -> float:
        p = self.local_phase(global_phase)
        if p <= self.duty_factor:
            return 0.0
        return (p - self.duty_factor) / max(1.0 - self.duty_factor, 1e-6)

    def target_offset(self, global_phase: float, base_stride: float, base_height: float) -> tuple[float, float]:
        local = self.local_phase(global_phase)
        stride = base_stride * self.stride_scale
        if local <= self.duty_factor:
            stance_t = local / max(self.duty_factor, 1e-6)
            x = (0.5 - stance_t) * stride + self.stance_bias_x
            y = self.stance_bias_y
            return (x, y)
        swing_t = self.swing_phase(global_phase)
        x = (-0.5 + swing_t) * stride + self.stance_bias_x
        lift = max(self.lift_channel.evaluate(swing_t), 0.0)
        y = math.sin(math.pi * swing_t) * base_height * self.swing_height_scale * max(lift, 0.35) + self.stance_bias_y
        return (x, y)


@dataclass(frozen=True)
class AsymmetricGaitProfile:
    """Repository-native distilled gait profile."""

    name: str
    morphology: Morphology = "biped"
    limbs: dict[str, LimbPhaseModel] = field(default_factory=dict)
    root_bounce: float = 0.02
    torso_twist: float = 0.04
    lateral_shift: float = 0.02
    injured_limb: str = ""
    description: str = ""

    def limb_names(self) -> tuple[str, ...]:
        return tuple(self.limbs.keys())


@dataclass(frozen=True)
class LimbContactState:
    """Evaluated local limb state at a given global phase."""

    name: str
    local_phase: float
    contact_probability: float
    in_contact: bool
    target_offset: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NSMGaitFrame:
    """Evaluated multi-contact gait frame."""

    profile_name: str
    morphology: Morphology
    global_phase: float
    speed: float
    root_bounce: float
    torso_twist: float
    contact_labels: dict[str, float]
    limb_states: dict[str, LimbContactState]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "morphology": self.morphology,
            "global_phase": self.global_phase,
            "speed": self.speed,
            "root_bounce": self.root_bounce,
            "torso_twist": self.torso_twist,
            "contact_labels": dict(self.contact_labels),
            "limb_states": {k: v.to_dict() for k, v in self.limb_states.items()},
        }


class DistilledNeuralStateMachine:
    """Deterministic runtime approximation of NSM / DeepPhase behavior."""

    def __init__(self, *, contact_threshold: float = 0.5) -> None:
        self.contact_threshold = float(contact_threshold)

    def evaluate(
        self,
        profile: AsymmetricGaitProfile,
        *,
        phase: float,
        speed: float = 1.0,
        base_stride: float = 0.8,
        base_height: float = 0.12,
    ) -> NSMGaitFrame:
        limb_states: dict[str, LimbContactState] = {}
        contact_labels: dict[str, float] = {}
        for name, limb in profile.limbs.items():
            offset = limb.target_offset(phase, base_stride * max(speed, 0.25), base_height)
            prob = limb.contact_probability(phase)
            state = LimbContactState(
                name=name,
                local_phase=limb.local_phase(phase),
                contact_probability=prob,
                in_contact=prob >= self.contact_threshold,
                target_offset=offset,
            )
            limb_states[name] = state
            contact_labels[name] = prob

        left_support = contact_labels.get("l_foot", contact_labels.get("front_left", 0.5))
        right_support = contact_labels.get("r_foot", contact_labels.get("front_right", 0.5))
        support_delta = left_support - right_support
        torso_twist = profile.torso_twist * math.sin(2.0 * math.pi * phase) + profile.lateral_shift * support_delta
        root_bounce = profile.root_bounce * math.sin(4.0 * math.pi * phase)
        if profile.injured_limb and profile.injured_limb in contact_labels:
            injury_weight = 1.0 - contact_labels[profile.injured_limb]
            root_bounce *= 1.0 - 0.35 * injury_weight
            torso_twist += 0.03 * injury_weight

        return NSMGaitFrame(
            profile_name=profile.name,
            morphology=profile.morphology,
            global_phase=float(phase % 1.0),
            speed=float(speed),
            root_bounce=float(root_bounce),
            torso_twist=float(torso_twist),
            contact_labels=contact_labels,
            limb_states=limb_states,
        )


def apply_biped_fabrik_offsets(
    left_target: tuple[float, float],
    right_target: tuple[float, float],
    gait_frame: NSMGaitFrame,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Apply NSM foot offsets to existing FABRIK targets."""
    l_state = gait_frame.limb_states.get("l_foot")
    r_state = gait_frame.limb_states.get("r_foot")
    l_out = left_target
    r_out = right_target
    if l_state is not None:
        l_out = (left_target[0] + l_state.target_offset[0], left_target[1] + l_state.target_offset[1])
    if r_state is not None:
        r_out = (right_target[0] + r_state.target_offset[0], right_target[1] + r_state.target_offset[1])
    return l_out, r_out


def generate_asymmetric_biped_pose(
    gait_generator: Any,
    *,
    phase: float,
    profile: AsymmetricGaitProfile,
    speed: float = 1.0,
) -> tuple[dict[str, float], NSMGaitFrame]:
    """Inject distilled NSM asymmetry into the existing FABRIK gait generator."""
    if profile.morphology != "biped":
        raise ValueError("generate_asymmetric_biped_pose requires a biped profile")

    controller = DistilledNeuralStateMachine()
    frame = controller.evaluate(
        profile,
        phase=phase,
        speed=speed,
        base_stride=float(getattr(gait_generator, "step_length", 0.8)),
        base_height=float(getattr(gait_generator, "step_height", 0.12)),
    )

    base_pose = gait_generator.generate_walk_pose(phase)
    half_step = float(getattr(gait_generator, "step_length", 0.8)) * 0.5
    left_base = (-half_step, 0.0)
    right_base = (half_step, 0.0)
    left_target, right_target = apply_biped_fabrik_offsets(left_base, right_base, frame)

    l_hip = gait_generator._skeleton.joints["l_hip"]
    r_hip = gait_generator._skeleton.joints["r_hip"]
    l_joints = gait_generator._l_solver.solve(target=left_target, root=(l_hip.x, l_hip.y))
    r_joints = gait_generator._r_solver.solve(target=right_target, root=(r_hip.x, r_hip.y))
    l_angles = gait_generator._fabrik_to_angles(l_joints, "l")
    r_angles = gait_generator._fabrik_to_angles(r_joints, "r")

    pose = dict(base_pose)
    pose.update(l_angles)
    pose.update(r_angles)
    pose["spine"] = pose.get("spine", 0.0) + frame.root_bounce * 2.0 + frame.torso_twist
    pose["chest"] = pose.get("chest", 0.0) - frame.torso_twist * 0.8
    pose["l_shoulder"] = pose.get("l_shoulder", 0.0) + 0.1 * (1.0 - frame.contact_labels.get("l_foot", 0.5))
    pose["r_shoulder"] = pose.get("r_shoulder", 0.0) + 0.1 * (1.0 - frame.contact_labels.get("r_foot", 0.5))
    return pose, frame


def plan_quadruped_gait(
    profile: AsymmetricGaitProfile,
    *,
    phase: float,
    speed: float = 1.0,
    body_length: float = 1.2,
    step_height: float = 0.14,
) -> NSMGaitFrame:
    """Evaluate a quadruped contact plan for creature rigs."""
    if profile.morphology != "quadruped":
        raise ValueError("plan_quadruped_gait requires a quadruped profile")
    controller = DistilledNeuralStateMachine()
    return controller.evaluate(
        profile,
        phase=phase,
        speed=speed,
        base_stride=body_length * 0.35,
        base_height=step_height,
    )


# ── Predefined distilled profiles ────────────────────────────────────────────


BIPED_LIMP_RIGHT_PROFILE = AsymmetricGaitProfile(
    name="biped_limp_right",
    morphology="biped",
    limbs={
        "l_foot": LimbPhaseModel(
            name="l_foot",
            phase_offset=0.0,
            duty_factor=0.62,
            stride_scale=1.0,
            swing_height_scale=1.0,
            lift_channel=PhaseChannel(amplitude=1.0, frequency=1.0, phase_shift=0.0),
        ),
        "r_foot": LimbPhaseModel(
            name="r_foot",
            phase_offset=0.47,
            duty_factor=0.74,
            stride_scale=0.72,
            swing_height_scale=0.65,
            stance_bias_x=-0.04,
            lift_channel=PhaseChannel(amplitude=0.75, frequency=1.0, phase_shift=0.05),
        ),
    },
    root_bounce=0.016,
    torso_twist=0.05,
    lateral_shift=0.035,
    injured_limb="r_foot",
    description="Distilled limping profile with longer right stance and shorter injured swing.",
)


BIPED_INJURED_LEFT_PROFILE = AsymmetricGaitProfile(
    name="biped_injured_left",
    morphology="biped",
    limbs={
        "l_foot": LimbPhaseModel(
            name="l_foot",
            phase_offset=0.02,
            duty_factor=0.76,
            stride_scale=0.70,
            swing_height_scale=0.60,
            stance_bias_x=-0.03,
            lift_channel=PhaseChannel(amplitude=0.70, frequency=1.0, phase_shift=0.04),
        ),
        "r_foot": LimbPhaseModel(
            name="r_foot",
            phase_offset=0.50,
            duty_factor=0.60,
            stride_scale=1.0,
            swing_height_scale=1.0,
            lift_channel=PhaseChannel(amplitude=1.0, frequency=1.0, phase_shift=0.0),
        ),
    },
    root_bounce=0.016,
    torso_twist=0.05,
    lateral_shift=0.035,
    injured_limb="l_foot",
    description="Mirror limp profile for left-side injury.",
)


QUADRUPED_TROT_PROFILE = AsymmetricGaitProfile(
    name="quadruped_trot",
    morphology="quadruped",
    limbs={
        "front_left": LimbPhaseModel(name="front_left", phase_offset=0.0, duty_factor=0.56, stride_scale=1.0, swing_height_scale=1.0),
        "hind_right": LimbPhaseModel(name="hind_right", phase_offset=0.0, duty_factor=0.56, stride_scale=1.0, swing_height_scale=1.0),
        "front_right": LimbPhaseModel(name="front_right", phase_offset=0.5, duty_factor=0.56, stride_scale=1.0, swing_height_scale=1.0),
        "hind_left": LimbPhaseModel(name="hind_left", phase_offset=0.5, duty_factor=0.56, stride_scale=1.0, swing_height_scale=1.0),
    },
    root_bounce=0.018,
    torso_twist=0.03,
    lateral_shift=0.015,
    description="Diagonal-pair trot distilled from mode-adaptive quadruped control literature.",
)


QUADRUPED_PACE_PROFILE = AsymmetricGaitProfile(
    name="quadruped_pace",
    morphology="quadruped",
    limbs={
        "front_left": LimbPhaseModel(name="front_left", phase_offset=0.0, duty_factor=0.60, stride_scale=1.05, swing_height_scale=0.95),
        "hind_left": LimbPhaseModel(name="hind_left", phase_offset=0.0, duty_factor=0.60, stride_scale=0.95, swing_height_scale=1.0),
        "front_right": LimbPhaseModel(name="front_right", phase_offset=0.5, duty_factor=0.60, stride_scale=1.05, swing_height_scale=0.95),
        "hind_right": LimbPhaseModel(name="hind_right", phase_offset=0.5, duty_factor=0.60, stride_scale=0.95, swing_height_scale=1.0),
    },
    root_bounce=0.014,
    torso_twist=0.025,
    lateral_shift=0.02,
    description="Lateral-pair pace profile suitable for stylized creature locomotion.",
)


__all__ = [
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
]
