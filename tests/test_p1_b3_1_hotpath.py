import json
from pathlib import Path

import pytest

from mathart.animation.unified_gait_blender import (
    TransitionStrategy,
    UnifiedGaitBlender,
    resolve_unified_gait_runtime_config,
)
from mathart.animation.unified_motion import (
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge
from mathart.distill.compiler import Constraint, ParameterSpace
from mathart.distill.runtime_bus import RuntimeDistillationBus


def _make_runtime_bus(tmp_path: Path, *, blend_time: float, phase_weight: float) -> RuntimeDistillationBus:
    bus = RuntimeDistillationBus(project_root=tmp_path)
    space = ParameterSpace(name="physics_gait_test")
    space.add_constraint(Constraint(
        param_name="physics_gait.blend_time",
        min_value=0.01,
        max_value=1.0,
        default_value=blend_time,
        is_hard=False,
        source_rule_id="P1-B3-1",
    ))
    space.add_constraint(Constraint(
        param_name="physics_gait.phase_weight",
        min_value=0.0,
        max_value=1.0,
        default_value=phase_weight,
        is_hard=False,
        source_rule_id="P1-B3-1",
    ))
    bus.register_space("physics_gait", space)
    return bus


def _make_frame(
    state: str,
    index: int,
    *,
    x: float,
    vx: float,
    l_hip: float,
    r_hip: float,
    left_contact: bool,
    right_contact: bool,
) -> UnifiedMotionFrame:
    return UnifiedMotionFrame(
        time=index / 24.0,
        phase=index / 6.0,
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
            "l_knee": l_hip * 0.7,
            "r_knee": r_hip * 0.7,
            "spine": 0.05 * index,
        },
        contact_tags=MotionContactState(
            left_foot=left_contact,
            right_foot=right_contact,
        ),
        frame_index=index,
        source_state=state,
    )


def _run_transition(blend_time: float) -> list[float]:
    prev_source = _make_frame(
        "run",
        -1,
        x=0.75,
        vx=0.20,
        l_hip=0.10,
        r_hip=-0.10,
        left_contact=True,
        right_contact=False,
    )
    source = _make_frame(
        "run",
        0,
        x=1.00,
        vx=0.24,
        l_hip=0.18,
        r_hip=-0.18,
        left_contact=False,
        right_contact=True,
    )
    targets = [
        _make_frame(
            "jump",
            i,
            x=1.05 + 0.03 * i,
            vx=0.10,
            l_hip=0.34 - 0.04 * i,
            r_hip=-0.08 + 0.02 * i,
            left_contact=False,
            right_contact=False,
        )
        for i in range(4)
    ]

    hub = UnifiedGaitBlender(
        transition_strategy=TransitionStrategy.INERTIALIZATION,
        blend_time=blend_time,
        phase_weight=1.0,
    )
    hub.request_transition(source, targets[0], prev_source_frame=prev_source, dt=1.0 / 24.0)
    outputs: list[float] = []
    for target in targets:
        outputs.append(hub.apply_transition(target, dt=1.0 / 24.0).root_transform.x)
    return outputs


def _load_root_y_sequence(path: str | Path) -> list[float]:
    clip = UnifiedMotionClip.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    return [float(frame.root_transform.y) for frame in clip.frames]


def test_runtime_bus_blend_time_changes_transition_root_trajectory(tmp_path: Path) -> None:
    bus = _make_runtime_bus(tmp_path, blend_time=0.99, phase_weight=1.0)
    resolved = resolve_unified_gait_runtime_config(bus, blend_time=0.2, phase_weight=1.0)
    assert resolved.blend_time == pytest.approx(0.99)
    assert resolved.parameter_source == "runtime_distillation_bus"

    default_root_x = _run_transition(blend_time=0.2)
    distilled_root_x = _run_transition(blend_time=resolved.blend_time)

    max_delta = max(abs(a - b) for a, b in zip(default_root_x, distilled_root_x))
    assert max_delta > 1.0e-4


def test_runtime_bus_phase_weight_changes_unified_motion_backend_output(tmp_path: Path) -> None:
    bridge = MicrokernelPipelineBridge(project_root=".")

    baseline_manifest = bridge.run_backend("unified_motion", {
        "state": "run",
        "frame_count": 8,
        "fps": 12,
        "name": "baseline",
        "output_dir": tmp_path / "baseline",
    })
    runtime_bus = _make_runtime_bus(tmp_path, blend_time=0.99, phase_weight=0.0)
    injected_manifest = bridge.run_backend("unified_motion", {
        "state": "run",
        "frame_count": 8,
        "fps": 12,
        "name": "distilled",
        "output_dir": tmp_path / "distilled",
        "runtime_distillation_bus": runtime_bus,
    })

    baseline_root_y = _load_root_y_sequence(baseline_manifest.outputs["motion_clip_json"])
    injected_root_y = _load_root_y_sequence(injected_manifest.outputs["motion_clip_json"])
    max_delta = max(abs(a - b) for a, b in zip(baseline_root_y, injected_root_y))

    assert max_delta > 1.0e-6
    assert injected_manifest.metadata["gait_param_source"] == "runtime_distillation_bus"
    assert injected_manifest.metadata["gait_blend_time"] == pytest.approx(0.99)
    assert injected_manifest.metadata["gait_phase_weight"] == pytest.approx(0.0)
