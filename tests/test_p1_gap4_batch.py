import json
import math
from pathlib import Path

import pytest

from mathart.animation.locomotion_cns import (
    default_transient_transition_requests,
    evaluate_transient_transition_batch,
    save_transient_motion_knowledge_asset,
)
from mathart.animation.unified_gait_blender import resolve_unified_transition_runtime_config
from mathart.animation.unified_motion import UnifiedMotionClip
from mathart.core.builtin_backends import UnifiedMotionBackend
from mathart.distill import (
    RuntimeDistillationBus,
    TRANSIENT_MOTION_MODULE,
    preload_all_distilled_knowledge,
)


def _write_transient_asset(project_root: Path, *, recovery_half_life: float, impact_damping_weight: float, landing_anticipation_window: float) -> Path:
    knowledge_dir = project_root / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "best_config": {
            "recovery_half_life": recovery_half_life,
            "impact_damping_weight": impact_damping_weight,
            "landing_anticipation_window": landing_anticipation_window,
            "peak_residual_threshold": 0.75,
            "frames_to_stability_threshold": 8.0,
            "peak_jerk_threshold": 2.50,
            "peak_root_velocity_delta_threshold": 1.80,
            "peak_pose_gap_threshold": 0.95,
        },
        "parameter_space_constraints": {
            "transient_motion.recovery_half_life": {
                "min_value": 0.02,
                "max_value": 0.35,
                "default_value": recovery_half_life,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
            "transient_motion.impact_damping_weight": {
                "min_value": 0.10,
                "max_value": 4.00,
                "default_value": impact_damping_weight,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
            "transient_motion.landing_anticipation_window": {
                "min_value": 0.02,
                "max_value": 0.45,
                "default_value": landing_anticipation_window,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
            "transient_motion.peak_residual_threshold": {
                "min_value": 0.10,
                "max_value": 4.00,
                "default_value": 0.75,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
            "transient_motion.frames_to_stability_threshold": {
                "min_value": 1.0,
                "max_value": 12.0,
                "default_value": 8.0,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
            "transient_motion.peak_jerk_threshold": {
                "min_value": 0.10,
                "max_value": 4.00,
                "default_value": 2.50,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
            "transient_motion.peak_root_velocity_delta_threshold": {
                "min_value": 0.10,
                "max_value": 4.00,
                "default_value": 1.80,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
            "transient_motion.peak_pose_gap_threshold": {
                "min_value": 0.10,
                "max_value": 4.00,
                "default_value": 0.95,
                "is_hard": True,
                "source_rule_id": "test_transient",
            },
        },
    }
    path = knowledge_dir / "transient_motion_rules.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _load_bus(project_root: Path) -> RuntimeDistillationBus:
    bus = RuntimeDistillationBus(project_root=project_root)
    loaded = preload_all_distilled_knowledge(bus, project_root / "knowledge")
    assert TRANSIENT_MOTION_MODULE in loaded
    return bus


def _render_clip(project_root: Path, bus: RuntimeDistillationBus, state: str, stem: str) -> UnifiedMotionClip:
    manifest = UnifiedMotionBackend().execute(
        {
            "state": state,
            "frame_count": 12,
            "fps": 24,
            "output_dir": str(project_root / "output"),
            "name": stem,
            "runtime_distillation_bus": bus,
        }
    )
    return UnifiedMotionClip.from_dict(json.loads(Path(manifest.outputs["motion_clip_json"]).read_text(encoding="utf-8")))


@pytest.mark.unit
def test_transient_knowledge_preload_changes_jump_and_hit_rendering(tmp_path: Path) -> None:
    low_root = tmp_path / "low"
    high_root = tmp_path / "high"
    _write_transient_asset(low_root, recovery_half_life=0.05, impact_damping_weight=0.50, landing_anticipation_window=0.05)
    _write_transient_asset(high_root, recovery_half_life=0.28, impact_damping_weight=2.50, landing_anticipation_window=0.35)

    low_bus = _load_bus(low_root)
    high_bus = _load_bus(high_root)

    low_cfg = resolve_unified_transition_runtime_config(low_bus)
    high_cfg = resolve_unified_transition_runtime_config(high_bus)
    assert low_cfg.parameter_source == "runtime_distillation_bus"
    assert high_cfg.parameter_source == "runtime_distillation_bus"
    assert high_cfg.landing_anticipation_window > low_cfg.landing_anticipation_window
    assert high_cfg.recovery_half_life > low_cfg.recovery_half_life

    jump_low = _render_clip(low_root, low_bus, "jump", "jump_low")
    jump_high = _render_clip(high_root, high_bus, "jump", "jump_high")
    low_tail_speed = sum(abs(frame.root_transform.velocity_y) for frame in jump_low.frames[-4:]) / 4.0
    high_tail_speed = sum(abs(frame.root_transform.velocity_y) for frame in jump_high.frames[-4:]) / 4.0
    assert high_tail_speed < low_tail_speed
    assert jump_high.frames[-2].root_transform.x > jump_low.frames[-2].root_transform.x

    hit_low = _render_clip(low_root, low_bus, "hit", "hit_low")
    hit_high = _render_clip(high_root, high_bus, "hit", "hit_high")
    assert abs(hit_high.frames[-1].root_transform.x) > abs(hit_low.frames[-1].root_transform.x)
    assert hit_high.frames[0].metadata["transient_param_source"] == "runtime_distillation_bus"


@pytest.mark.unit
def test_transient_batch_roundtrip_writes_and_reloads_knowledge(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    bus = RuntimeDistillationBus(project_root=project_root)

    batch = evaluate_transient_transition_batch(
        default_transient_transition_requests(),
        runtime_bus=bus,
        fps=24,
        output_dir=project_root / "batch_output",
    )

    assert len(batch.metrics) == 3
    assert math.isfinite(batch.mean_runtime_score)
    assert math.isfinite(batch.mean_peak_residual)
    assert batch.worst_peak_jerk >= 0.0
    assert batch.worst_peak_root_velocity_delta >= 0.0

    knowledge_path = save_transient_motion_knowledge_asset(
        batch,
        project_root / "knowledge" / "transient_motion_rules.json",
        session_id="SESSION-079",
    )
    assert knowledge_path.exists()

    reloaded_bus = RuntimeDistillationBus(project_root=project_root)
    loaded = preload_all_distilled_knowledge(reloaded_bus, project_root / "knowledge")
    assert TRANSIENT_MOTION_MODULE in loaded

    cfg = resolve_unified_transition_runtime_config(reloaded_bus)
    assert cfg.parameter_source == "runtime_distillation_bus"
    assert cfg.recovery_half_life >= 0.05
    assert cfg.landing_anticipation_window >= 0.08

    jump_clip = _render_clip(project_root, reloaded_bus, "jump", "jump_reloaded")
    assert jump_clip.frames[0].metadata["transient_param_source"] == "runtime_distillation_bus"
    assert jump_clip.metadata["transient_impact_damping_weight"] == pytest.approx(cfg.impact_damping_weight)
