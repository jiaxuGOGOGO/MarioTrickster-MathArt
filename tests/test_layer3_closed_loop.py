import json
import math
from pathlib import Path

import pytest

from mathart.animation.runtime_motion_query import RuntimeMotionDatabase
from mathart.animation.unified_motion import (
    MotionContactState,
    MotionRootTransform,
    PhaseState,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)
from mathart.evolution.layer3_closed_loop import (
    Layer3ClosedLoopDistiller,
    TransitionTuningTarget,
    load_distilled_transition_params,
)


def _make_frame(
    state: str,
    index: int,
    x: float,
    vx: float,
    l_hip: float,
    r_hip: float,
    l_knee: float,
    r_knee: float,
    left_contact: bool,
    right_contact: bool,
    cyclic: bool = True,
) -> UnifiedMotionFrame:
    phase_value = index / 5.0
    phase_state = (
        PhaseState.cyclic(phase_value, phase_kind="gait")
        if cyclic
        else PhaseState.transient(phase_value, phase_kind="transient")
    )
    return UnifiedMotionFrame(
        time=index / 24.0,
        phase=phase_state.to_float(),
        phase_state=phase_state,
        root_transform=MotionRootTransform(
            x=x,
            y=0.0,
            rotation=0.0,
            velocity_x=vx,
            velocity_y=0.0,
            angular_velocity=0.0,
        ),
        joint_local_rotations={
            "l_hip": l_hip,
            "r_hip": r_hip,
            "l_knee": l_knee,
            "r_knee": r_knee,
            "spine": 0.05 * index,
        },
        contact_tags=MotionContactState(
            left_foot=left_contact,
            right_foot=right_contact,
        ),
        frame_index=index,
        source_state=state,
    )


def _build_synthetic_db(weights) -> RuntimeMotionDatabase:
    db = RuntimeMotionDatabase(weights=weights)

    run_frames = [
        _make_frame("run", 0, 0.00, 0.10, 0.10, -0.10, 0.20, -0.20, True, False),
        _make_frame("run", 1, 0.10, 0.12, 0.15, -0.15, 0.25, -0.25, True, False),
        _make_frame("run", 2, 0.22, 0.14, 0.20, -0.20, 0.30, -0.30, False, True),
        _make_frame("run", 3, 0.36, 0.16, 0.16, -0.16, 0.24, -0.24, False, True),
        _make_frame("run", 4, 0.52, 0.18, 0.12, -0.12, 0.18, -0.18, True, False),
        _make_frame("run", 5, 0.70, 0.18, 0.08, -0.08, 0.12, -0.12, True, False),
    ]
    jump_frames = [
        _make_frame("jump", 0, 0.70, 0.18, 0.10, -0.10, 0.18, -0.18, True, False, cyclic=False),
        _make_frame("jump", 1, 0.82, 0.16, 0.20, -0.12, 0.10, -0.08, False, False, cyclic=False),
        _make_frame("jump", 2, 0.94, 0.14, 0.28, -0.14, 0.02, -0.02, False, False, cyclic=False),
        _make_frame("jump", 3, 1.04, 0.10, 0.20, -0.10, 0.06, -0.04, False, False, cyclic=False),
        _make_frame("jump", 4, 1.10, 0.06, 0.12, -0.08, 0.10, -0.08, False, True, cyclic=False),
        _make_frame("jump", 5, 1.14, 0.04, 0.06, -0.06, 0.12, -0.10, True, True, cyclic=False),
    ]

    db.add_umr_clip(UnifiedMotionClip(clip_id="run", state="run", fps=24, frames=run_frames))
    db.add_umr_clip(UnifiedMotionClip(clip_id="jump", state="jump", fps=24, frames=jump_frames))
    db.normalize()
    return db


def test_evaluate_transition_returns_finite_metrics(tmp_path, monkeypatch):
    distiller = Layer3ClosedLoopDistiller(project_root=tmp_path, random_seed=7)
    monkeypatch.setattr(distiller, "_build_database", lambda weights: _build_synthetic_db(weights))

    result = distiller.evaluate_transition(
        TransitionTuningTarget(source_state="run", target_state="jump", source_phase=0.6),
        {},
    )

    assert math.isfinite(result["loss"])
    assert result["loss"] >= 0.0
    assert "entry_cost" in result["objective_breakdown"]
    assert 0.0 <= result["transition_quality"] <= 1.0


def test_optimize_transition_writes_rule_bridge_and_report(tmp_path, monkeypatch):
    pytest.importorskip("optuna")
    distiller = Layer3ClosedLoopDistiller(project_root=tmp_path, random_seed=11)
    monkeypatch.setattr(distiller, "_build_database", lambda weights: _build_synthetic_db(weights))

    result = distiller.optimize_transition(
        TransitionTuningTarget(source_state="run", target_state="jump", source_phase=0.6),
        n_trials=4,
    )

    report_path = Path(result.report_path)
    assert report_path.exists()

    rules_payload = json.loads((tmp_path / "transition_rules.json").read_text(encoding="utf-8"))
    assert rules_payload["rule_count"] == 1
    assert "run->jump" in rules_payload["rules"]
    assert rules_payload["rules"]["run->jump"]["best_loss"] == pytest.approx(result.rule.best_loss)

    bridge_payload = json.loads((tmp_path / "LAYER3_CONVERGENCE_BRIDGE.json").read_text(encoding="utf-8"))
    assert bridge_payload["transition_rule_key"] == "run->jump"
    assert bridge_payload["transition_strategy"] in {"dead_blending", "inertialization"}
    assert bridge_payload["transition_best_loss"] == pytest.approx(result.rule.best_loss)

    loaded = load_distilled_transition_params(project_root=tmp_path, transition_key="run->jump")
    assert loaded["blend_time"] == pytest.approx(result.rule.params["blend_time"])
    assert loaded["foot_contact_weight"] == pytest.approx(result.rule.params["foot_contact_weight"])
