"""
Animation presets for MarioTrickster character states.

Maps to the required character animation states:
  - idle: breathing + subtle sway (secondary action)
  - run: **phase-driven** cyclic locomotion (SESSION-033 upgrade)
  - walk: **phase-driven** walk cycle (SESSION-033 new)
  - jump: anticipation → launch → apex
  - fall: stretched pose → impact squash
  - hit: recoil with spring follow-through

Each preset returns a list of keyframe poses (joint_name → angle).
Interpolation between keyframes uses the curve functions.

Distilled knowledge applied:
  - 収縮拉伸法則: bend side compresses, opposite stretches
  - 拮抗筋連動: biceps contract → triceps stretch
  - 胸骨盆独立回転: chest/pelvis counter-rotate for gesture
  - Line of Action: curves toward contraction side

SESSION-033 upgrade:
  - run_animation() now delegates to phase_driven_run() internally
  - New walk_animation() delegates to phase_driven_walk()
  - Phase-driven system based on PFNN phase variable, Animator's Survival Kit
    key poses (Contact/Down/Pass/Up), and DeepPhase channel overlays
  - Legacy sin()-based implementation preserved as run_animation_legacy()
"""
from __future__ import annotations
from typing import Callable
import numpy as np
from .curves import sine_wave, ease_in_out, spring, squash_stretch


AnimationFunc = Callable[[float], dict[str, float]]


def idle_animation(t: float) -> dict[str, float]:
    """Idle breathing animation.

    Subtle chest expansion + head bob + arm sway.
    ~2 second cycle at 8 frames.
    """
    breath = sine_wave(t, frequency=1.0, amplitude=0.03)
    head_bob = sine_wave(t, frequency=1.0, amplitude=0.02, phase=0.5)
    arm_sway = sine_wave(t, frequency=0.5, amplitude=0.05)

    return {
        "spine": breath,
        "chest": breath * 0.5,
        "neck": -breath * 0.3,
        "head": head_bob,
        "l_shoulder": arm_sway,
        "r_shoulder": -arm_sway,
        "l_elbow": 0.1 + arm_sway * 0.3,
        "r_elbow": 0.1 - arm_sway * 0.3,
    }


def run_animation(t: float) -> dict[str, float]:
    """Run cycle animation — **Phase-Driven** (SESSION-033).

    Upgraded from sin()-based to phase-driven key-pose interpolation.
    Uses Contact→Down→Passing→Up→Flight key poses from Animator's
    Survival Kit, Catmull-Rom spline interpolation (PFNN), and
    DeepPhase-style secondary motion channels.

    Full stride cycle with:
    - Four canonical key poses per half-cycle + flight phase
    - Pelvis height trajectory: Contact(neutral)→Down(lowest)→Pass(rising)→Up(highest)
    - Counter-rotating arms (胸骨盆独立回転)
    - Forward lean scaling with speed
    - Flight phase (both feet off ground)

    Parameters
    ----------
    t : float
        Normalized cycle time [0, 1). Full gait cycle.

    Returns
    -------
    dict[str, float] : Joint angles for the entire body.
    """
    from .phase_driven import phase_driven_run
    return phase_driven_run(t)


def walk_animation(t: float) -> dict[str, float]:
    """Walk cycle animation — **Phase-Driven** (SESSION-033).

    New preset based on Animator's Survival Kit walk cycle:
    Contact→Down→Passing→Up key poses with Catmull-Rom interpolation.

    Parameters
    ----------
    t : float
        Normalized cycle time [0, 1). Full gait cycle.

    Returns
    -------
    dict[str, float] : Joint angles for the entire body.
    """
    from .phase_driven import phase_driven_walk
    return phase_driven_walk(t)


def run_animation_legacy(t: float) -> dict[str, float]:
    """Legacy run cycle animation (pre-SESSION-033, sin()-based).

    Preserved for backward compatibility and A/B comparison testing.
    Full stride cycle with:
    - Alternating leg swing via sin()
    - Counter-rotating arms (胸骨盆独立回転)
    - Torso lean forward
    - Squash at contact, stretch at push-off
    """
    # Leg phase: left forward at t=0, right forward at t=0.5
    leg_swing = 0.6  # Max leg angle
    arm_swing = 0.4

    l_leg = leg_swing * np.sin(2 * np.pi * t)
    r_leg = leg_swing * np.sin(2 * np.pi * t + np.pi)

    # Knee bend (backward only, per ROM constraint)
    l_knee = -0.5 * max(0, np.sin(2 * np.pi * t - 0.3))
    r_knee = -0.5 * max(0, np.sin(2 * np.pi * t + np.pi - 0.3))

    # Counter-rotating arms (opposite to legs)
    l_arm = -arm_swing * np.sin(2 * np.pi * t)
    r_arm = -arm_swing * np.sin(2 * np.pi * t + np.pi)

    # Torso: slight forward lean + counter-rotation
    torso_rot = 0.05 * np.sin(2 * np.pi * t * 2)

    return {
        "spine": 0.1 + torso_rot,  # Forward lean
        "chest": -torso_rot,  # Counter-rotate (胸骨盆独立回転)
        "head": -0.05,  # Slight forward look
        "l_hip": l_leg,
        "r_hip": r_leg,
        "l_knee": l_knee,
        "r_knee": r_knee,
        "l_shoulder": l_arm,
        "r_shoulder": r_arm,
        "l_elbow": 0.4 + 0.2 * np.sin(2 * np.pi * t),
        "r_elbow": 0.4 + 0.2 * np.sin(2 * np.pi * t + np.pi),
    }


def jump_animation(t: float) -> dict[str, float]:
    """Jump animation — distance-matched transient phase (SESSION-037).

    Public API is preserved, but the internal control law is no longer a
    fixed time-sliced anticipation/launch/apex split. The pose is now driven
    by a distance-to-apex transient phase implemented in `phase_driven.py`.
    """
    from .phase_driven import phase_driven_jump
    return phase_driven_jump(t)


def fall_animation(t: float) -> dict[str, float]:
    """Fall animation — distance-matched transient phase (SESSION-037).

    Public API is preserved, but the internal control law now uses a
    distance-to-ground transient phase instead of fixed time buckets.
    """
    from .phase_driven import phase_driven_fall
    return phase_driven_fall(t)


def hit_animation(t: float) -> dict[str, float]:
    """Hit/damage reaction — action-goal-progress transient phase (SESSION-037).

    Public API is preserved, but the internal control law now follows a
    one-way recovery deficit phase rather than a spring-timed recoil curve.
    """
    from .phase_driven import phase_driven_hit
    return phase_driven_hit(t)
