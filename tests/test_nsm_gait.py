"""Tests for SESSION-058 distilled Neural State Machine gait control."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.animation.biomechanics import FABRIKGaitGenerator
from mathart.animation.nsm_gait import (
    DistilledNeuralStateMachine,
    generate_asymmetric_biped_pose,
    plan_quadruped_gait,
    BIPED_LIMP_RIGHT_PROFILE,
    QUADRUPED_TROT_PROFILE,
)
from mathart.animation.skeleton import Skeleton


def test_biped_limp_profile_is_asymmetric():
    controller = DistilledNeuralStateMachine()
    frame = controller.evaluate(BIPED_LIMP_RIGHT_PROFILE, phase=0.20, speed=1.0)

    left = frame.limb_states["l_foot"]
    right = frame.limb_states["r_foot"]
    assert abs(left.contact_probability - right.contact_probability) > 1e-3
    assert left.target_offset != right.target_offset


def test_generate_asymmetric_biped_pose_produces_leg_angles_and_metadata():
    skeleton = Skeleton.create_humanoid(head_units=3.0)
    gait = FABRIKGaitGenerator(skeleton=skeleton)
    pose, frame = generate_asymmetric_biped_pose(
        gait,
        phase=0.35,
        profile=BIPED_LIMP_RIGHT_PROFILE,
        speed=1.0,
    )

    for joint in ("l_hip", "l_knee", "r_hip", "r_knee", "spine", "chest"):
        assert joint in pose
    assert frame.profile_name == "biped_limp_right"
    assert frame.morphology == "biped"
    assert "l_foot" in frame.contact_labels and "r_foot" in frame.contact_labels


def test_quadruped_trot_diagonal_pairs_align():
    frame = plan_quadruped_gait(QUADRUPED_TROT_PROFILE, phase=0.10, speed=1.2)

    fl = frame.limb_states["front_left"].contact_probability
    hr = frame.limb_states["hind_right"].contact_probability
    fr = frame.limb_states["front_right"].contact_probability
    hl = frame.limb_states["hind_left"].contact_probability

    assert abs(fl - hr) < 1e-6
    assert abs(fr - hl) < 1e-6
    assert abs(fl - fr) > 1e-3
