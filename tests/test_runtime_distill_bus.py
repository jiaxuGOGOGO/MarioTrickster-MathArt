from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mathart.animation.physics_projector import ContactDetector
from mathart.distill.compiler import Constraint, ParameterSpace
from mathart.distill.runtime_bus import (
    CompiledParameterSpace,
    RuntimeDistillationBus,
    RuntimeRuleClause,
)
from mathart.evolution.runtime_distill_bridge import RuntimeDistillBridge
from mathart.quality.controller import ArtMathQualityController


@pytest.mark.unit
def test_compiled_parameter_space_clamps_alias_params() -> None:
    space = ParameterSpace(name="physics")
    space.add_constraint(Constraint(
        param_name="physics.contact.height_threshold",
        min_value=0.01,
        max_value=0.08,
        default_value=0.05,
        is_hard=True,
    ))
    space.add_constraint(Constraint(
        param_name="physics.contact.velocity_threshold",
        min_value=0.05,
        max_value=0.20,
        default_value=0.15,
        is_hard=True,
    ))

    compiled = CompiledParameterSpace(module_name="physics", space=space, backend="numba")
    adjusted = compiled.clamp_params({"contact_height": 0.20, "contact_velocity": 0.01})

    assert adjusted["contact_height"] == pytest.approx(0.08)
    assert adjusted["contact_velocity"] == pytest.approx(0.05)


@pytest.mark.unit
def test_runtime_rule_program_matches_expected_contact_gate(tmp_path: Path) -> None:
    bus = RuntimeDistillationBus(project_root=tmp_path)
    program = bus.build_foot_contact_program(contact_threshold=0.05, velocity_threshold=0.15)

    samples = [
        (np.array([0.00, 0.00], dtype=np.float64), True),
        (np.array([0.03, 0.04], dtype=np.float64), True),
        (np.array([0.12, 0.00], dtype=np.float64), False),
        (np.array([0.01, 0.30], dtype=np.float64), False),
    ]

    for features, expected in samples:
        evaluation = program.evaluate_array(features)
        assert evaluation.accepted is expected


@pytest.mark.unit
def test_contact_detector_uses_runtime_program(tmp_path: Path) -> None:
    bus = RuntimeDistillationBus(project_root=tmp_path)
    program = bus.build_foot_contact_program(contact_threshold=0.05, velocity_threshold=0.15)
    detector = ContactDetector(
        contact_threshold=1.0,
        velocity_threshold=1.0,
        runtime_program=program,
        foot_joints=["l_foot"],
    )

    first = detector.update({"l_foot": (0.0, 0.01)}, dt=1.0 / 60.0)
    assert first["l_foot"].is_contacting is True

    second = detector.update({"l_foot": (0.0, 0.20)}, dt=1.0 / 60.0)
    assert second["l_foot"].is_contacting is False


@pytest.mark.unit
def test_quality_controller_applies_runtime_bus_adjustments(tmp_path: Path) -> None:
    (tmp_path / "knowledge").mkdir(parents=True, exist_ok=True)
    controller = ArtMathQualityController(project_root=tmp_path)

    space = ParameterSpace(name="physics")
    space.add_constraint(Constraint(
        param_name="physics.contact.height_threshold",
        min_value=0.01,
        max_value=0.08,
        default_value=0.05,
        is_hard=True,
    ))
    bus = RuntimeDistillationBus(project_root=tmp_path)
    bus.register_space("physics", space)
    controller._runtime_bus = bus

    result = controller.pre_generation(iteration=1, current_params={"contact_height": 0.20})

    assert result.param_adjustments["contact_height"] == pytest.approx(0.08)


@pytest.mark.unit
def test_runtime_distill_bridge_persists_cycle_outputs(tmp_path: Path) -> None:
    bus = RuntimeDistillationBus(project_root=tmp_path)
    space = ParameterSpace(name="physics")
    space.add_constraint(Constraint(
        param_name="physics.contact.height_threshold",
        min_value=0.01,
        max_value=0.08,
        default_value=0.05,
        is_hard=True,
    ))
    bus.register_space("physics", space)

    bridge = RuntimeDistillBridge(project_root=tmp_path)
    metrics = bridge.evaluate_runtime_bus(bus=bus)
    rules = bridge.distill_rules(metrics)
    knowledge_path = bridge.write_knowledge_file(metrics, rules)
    bonus = bridge.apply_layer3(metrics)

    assert metrics.accepted is True
    assert knowledge_path.exists()
    assert bridge.state_path.exists()
    assert bonus >= 0.0
