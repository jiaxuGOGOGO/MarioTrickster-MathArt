import pytest

from mathart.animation.phase_driven import (
    IllegalStateTransitionError,
    PhaseDrivenStateMachine,
)


def _assert_machine_unchanged(machine: PhaseDrivenStateMachine, snapshot: dict[str, float | int | str]) -> None:
    assert machine.current_state == snapshot["current_state"]
    assert machine.phase_clock == snapshot["phase_clock"]
    assert machine.transition_blend_weight == snapshot["transition_blend_weight"]
    assert machine.cycle_count == snapshot["cycle_count"]


def test_illegal_state_transitions() -> None:
    hit_machine = PhaseDrivenStateMachine(
        current_state="hit",
        phase_clock=0.37,
        transition_blend_weight=0.64,
    )
    hit_snapshot = hit_machine.snapshot()

    hit_result = hit_machine.transition_to("sprint")

    assert hit_result is False
    assert hit_machine.last_transition_error is not None
    assert hit_machine.last_transition_error.current_state == "hit"
    assert hit_machine.last_transition_error.target_state == "sprint"
    _assert_machine_unchanged(hit_machine, hit_snapshot)

    dead_machine = PhaseDrivenStateMachine(
        current_state="dead",
        phase_clock=0.91,
        transition_blend_weight=0.18,
    )
    dead_snapshot = dead_machine.snapshot()

    dead_result = dead_machine.transition_to("idle")

    assert dead_result is False
    assert dead_machine.last_transition_error is not None
    assert dead_machine.last_transition_error.current_state == "dead"
    assert dead_machine.last_transition_error.target_state == "idle"
    _assert_machine_unchanged(dead_machine, dead_snapshot)


def test_illegal_state_transition_strict_mode_preserves_state() -> None:
    machine = PhaseDrivenStateMachine(
        current_state="hit",
        phase_clock=0.42,
        transition_blend_weight=0.73,
    )
    snapshot = machine.snapshot()

    with pytest.raises(IllegalStateTransitionError) as exc_info:
        machine.transition_to("sprint", strict=True)

    assert exc_info.value.current_state == "hit"
    assert exc_info.value.target_state == "sprint"
    _assert_machine_unchanged(machine, snapshot)


def test_legal_state_transition_updates_state_and_resets_phase() -> None:
    machine = PhaseDrivenStateMachine(
        current_state="hit",
        phase_clock=0.55,
        transition_blend_weight=0.81,
    )

    result = machine.transition_to("stable_balance", blend_weight=0.25)

    assert result is True
    assert machine.current_state == "stable_balance"
    assert machine.phase_clock == 0.0
    assert machine.transition_blend_weight == 0.25
    assert machine.last_transition_error is None
