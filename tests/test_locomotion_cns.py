from pathlib import Path

import pytest

from mathart.animation.gait_blend import GaitMode
from mathart.animation.locomotion_cns import (
    GaitTransitionRequest,
    build_phase_aligned_transition_clip,
    compute_clip_sliding_metrics,
    default_cns_transition_requests,
    evaluate_transition_batch,
    evaluate_transition_case,
    sample_gait_umr_frame,
)
from mathart.distill.runtime_bus import RuntimeDistillationBus
from mathart.evolution.locomotion_cns_bridge import LocomotionCNSBridge


@pytest.mark.unit
def test_runtime_bus_builds_gait_transition_program() -> None:
    bus = RuntimeDistillationBus()
    program = bus.build_gait_transition_program(
        phase_jump_threshold=0.08,
        sliding_threshold=0.08,
        contact_mismatch_threshold=0.25,
        foot_lock_threshold=0.80,
        transition_cost_threshold=0.75,
    )

    good = program.evaluate(
        {
            "phase_jump": 0.01,
            "sliding_error": 0.03,
            "contact_mismatch": 0.0,
            "foot_lock": 0.90,
            "transition_cost": 0.20,
        }
    )
    bad = program.evaluate(
        {
            "phase_jump": 0.30,
            "sliding_error": 0.20,
            "contact_mismatch": 1.0,
            "foot_lock": 0.20,
            "transition_cost": 1.20,
        }
    )

    assert good.accepted is True
    assert bad.accepted is False


@pytest.mark.unit
def test_phase_aligned_transition_clip_preserves_target_contacts() -> None:
    request = GaitTransitionRequest(
        source_gait=GaitMode.WALK,
        target_gait=GaitMode.RUN,
        source_phase=0.0,
        source_speed=0.8,
        target_speed=1.8,
        duration_s=0.25,
    )
    clip = build_phase_aligned_transition_clip(request, fps=24)

    assert clip.clip_id == "walk_to_run"
    assert clip.state == "run"
    assert clip.metadata["transition_type"] == "phase_aligned_inertialization"
    assert clip.frames[0].contact_tags.left_foot is True
    assert clip.frames[0].metadata["generator_mode"] == "gait_cns"


@pytest.mark.unit
def test_phase_aligned_transition_clip_preserves_c0_c1_root_continuity() -> None:
    request = GaitTransitionRequest(
        source_gait=GaitMode.WALK,
        target_gait=GaitMode.RUN,
        source_phase=0.125,
        source_speed=0.8,
        target_speed=1.8,
        duration_s=0.25,
        inertial_blend_time=0.2,
    )
    clip = build_phase_aligned_transition_clip(request, fps=24)
    dt = 1.0 / 24.0

    source_start = sample_gait_umr_frame(
        GaitMode.WALK,
        phase=request.source_phase,
        speed=request.source_speed,
        time=0.0,
        frame_index=0,
        root_x=0.0,
    )

    first = clip.frames[0]
    assert first.root_transform.x == pytest.approx(source_start.root_transform.x, abs=1e-6)
    assert first.root_transform.y == pytest.approx(source_start.root_transform.y, abs=1e-6)
    assert first.root_transform.velocity_x == pytest.approx(source_start.root_transform.velocity_x, rel=1e-4, abs=1e-4)
    assert first.root_transform.velocity_y == pytest.approx(source_start.root_transform.velocity_y, rel=1e-4, abs=1e-4)

    if len(clip.frames) >= 2:
        finite_diff_vx = (clip.frames[1].root_transform.x - clip.frames[0].root_transform.x) / dt
        assert finite_diff_vx == pytest.approx(first.root_transform.velocity_x, rel=0.35, abs=0.35)


@pytest.mark.unit
def test_transition_case_and_sliding_metrics_are_finite() -> None:
    clip, metrics, evaluation = evaluate_transition_case(
        GaitTransitionRequest(GaitMode.WALK, GaitMode.RUN, source_phase=0.0, source_speed=0.8, target_speed=1.8),
        fps=24,
    )
    mean_slide, max_slide = compute_clip_sliding_metrics(clip)

    assert len(clip.frames) >= 3
    assert metrics.accepted is True
    assert evaluation.score >= 0.75
    assert mean_slide >= 0.0
    assert max_slide >= mean_slide
    assert metrics.max_phase_step_error <= 0.08


@pytest.mark.unit
def test_transition_batch_uses_runtime_program_batch_path() -> None:
    bus = RuntimeDistillationBus()
    result = evaluate_transition_batch(default_cns_transition_requests(), runtime_bus=bus, fps=24)

    assert len(result.metrics) == 5
    assert result.accepted_ratio >= 0.6
    assert result.mean_runtime_score >= 0.75
    assert result.mean_sliding_error >= 0.0


@pytest.mark.unit
def test_pipeline_sampler_generates_umr_frame_for_walk() -> None:
    frame = sample_gait_umr_frame(
        GaitMode.WALK,
        phase=0.0,
        speed=0.8,
        time=0.0,
        frame_index=0,
        root_x=0.0,
        metadata={"generator_mode": "gait_cns"},
    )
    assert frame.source_state == "walk"
    assert frame.contact_tags.left_foot is True
    assert frame.root_transform.velocity_x == pytest.approx(0.8)


@pytest.mark.unit
def test_locomotion_cns_bridge_persists_cycle_outputs(tmp_path: Path) -> None:
    bridge = LocomotionCNSBridge(project_root=tmp_path)
    bus = RuntimeDistillationBus(project_root=tmp_path)
    result = bridge.run_cycle(bus=bus)

    assert result["accepted"] is True
    assert Path(result["knowledge_path"]).exists()
    assert bridge.state_path.exists()
    assert result["fitness_bonus"] >= 0.0
