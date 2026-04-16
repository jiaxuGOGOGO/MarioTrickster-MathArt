from __future__ import annotations

import json
from pathlib import Path

from mathart.animation import (
    AnglePoseProjector,
    UnifiedMotionFrame,
    fall_distance_phase,
    hit_recovery_phase,
    idle_animation,
    jump_distance_phase,
    phase_driven_fall_frame,
    phase_driven_hit_frame,
    phase_driven_jump_frame,
    phase_driven_run_frame,
    pose_to_umr,
)
from mathart.pipeline import AssetPipeline, CharacterSpec


def test_pose_to_umr_roundtrip_contract():
    pose = idle_animation(0.0)
    frame = pose_to_umr(
        pose,
        time=0.125,
        phase=0.25,
        source_state="idle",
        metadata={"generator": "test"},
    )

    assert isinstance(frame, UnifiedMotionFrame)
    assert frame.time == 0.125
    assert frame.phase == 0.25
    assert frame.source_state == "idle"
    assert frame.joint_local_rotations.keys() == pose.keys()
    assert frame.metadata["generator"] == "test"


def test_phase_driven_run_frame_has_required_umr_fields():
    frame = phase_driven_run_frame(
        0.25,
        time=0.1,
        frame_index=1,
        source_state="run",
        root_x=0.05,
        root_velocity_x=0.2,
    )

    assert isinstance(frame, UnifiedMotionFrame)
    assert frame.root_transform.x == 0.05
    assert frame.root_transform.velocity_x == 0.2
    assert "l_hip" in frame.joint_local_rotations
    assert frame.contact_tags.left_foot in {True, False}
    assert frame.contact_tags.right_foot in {True, False}


def test_physics_projector_step_frame_preserves_umr_contract():
    projector = AnglePoseProjector()
    raw = pose_to_umr(idle_animation(0.0), time=0.0, phase=0.0, source_state="idle")

    projected = projector.step_frame(raw, dt=1.0 / 12.0)

    assert isinstance(projected, UnifiedMotionFrame)
    assert projected.joint_local_rotations.keys() == raw.joint_local_rotations.keys()
    assert projected.metadata["physics_projected"] is True
    assert projected.metadata["physics_layer_guard"] is True


def test_transient_phase_models_are_monotonic_and_semantic():
    jump_start = jump_distance_phase(root_y=0.0, root_velocity_y=0.3, apex_height=0.18)
    jump_mid = jump_distance_phase(root_y=0.09, root_velocity_y=0.15, apex_height=0.18)
    jump_apex = jump_distance_phase(root_y=0.18, root_velocity_y=0.0, apex_height=0.18)
    assert jump_start["phase"] < jump_mid["phase"] < jump_apex["phase"]
    assert jump_apex["phase_kind"] == "distance_to_apex"

    fall_far = fall_distance_phase(root_y=0.22, ground_height=0.0, fall_reference_height=0.22)
    fall_near = fall_distance_phase(root_y=0.04, ground_height=0.0, fall_reference_height=0.22)
    fall_ground = fall_distance_phase(root_y=0.0, ground_height=0.0, fall_reference_height=0.22)
    assert fall_far["phase"] < fall_near["phase"] < fall_ground["phase"]
    assert fall_ground["is_landing_window"] is True

    hit_peak = hit_recovery_phase(0.0, damping=4.0, impact_energy=1.0)
    hit_recovering = hit_recovery_phase(0.4, damping=4.0, impact_energy=1.0)
    hit_restored = hit_recovery_phase(2.0, damping=4.0, impact_energy=1.0)
    assert hit_peak["phase"] > hit_recovering["phase"] > hit_restored["phase"]
    assert hit_restored["recovery_progress"] > hit_recovering["recovery_progress"]


def test_transient_phase_frame_generators_write_umr_metadata():
    jump = phase_driven_jump_frame(
        0.5,
        time=0.1,
        frame_index=1,
        source_state="jump",
        root_y=0.12,
        root_velocity_y=0.08,
        apex_height=0.18,
    )
    assert jump.metadata["phase_kind"] == "distance_to_apex"
    assert jump.metadata["distance_to_apex"] >= 0.0

    fall = phase_driven_fall_frame(
        0.5,
        time=0.1,
        frame_index=1,
        source_state="fall",
        root_y=0.06,
        root_velocity_y=-0.2,
        ground_height=0.0,
        fall_reference_height=0.22,
    )
    assert fall.metadata["phase_kind"] == "distance_to_ground"
    assert fall.metadata["distance_to_ground"] >= 0.0

    hit = phase_driven_hit_frame(
        0.25,
        time=0.1,
        frame_index=1,
        source_state="hit",
        damping=4.0,
        impact_energy=1.0,
    )
    assert hit.metadata["phase_kind"] == "hit_recovery"
    assert 0.0 <= hit.metadata["recovery_progress"] <= 1.0


def test_character_pipeline_exports_umr_artifacts(tmp_path: Path):
    output_dir = tmp_path / "output"
    pipeline = AssetPipeline(output_dir=str(output_dir), seed=42)
    spec = CharacterSpec(
        name="umr_probe",
        preset="mario",
        states=["idle", "run", "jump", "fall", "hit"],
        frames_per_state=2,
        enable_physics=False,
        enable_biomechanics=False,
        export_palette=False,
    )

    result = pipeline.produce_character_pack(spec)

    manifest_path = output_dir / "umr_probe" / "umr_probe_character_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["motion_contract"]["name"] == "UnifiedMotionFrame"
    assert manifest["summary"]["umr_pipeline_ready_for_layer3"] is True
    assert any(path.endswith(".umr.json") for path in result.output_paths)
    assert manifest["states"]["idle"]["motion_bus"]["format"] == "umr_motion_clip_v1"
    assert manifest["states"]["run"]["motion_bus"]["format"] == "umr_motion_clip_v1"
    assert manifest["states"]["jump"]["motion_bus"]["audit"]["contract"] == "UnifiedMotionFrame"
    assert manifest["states"]["fall"]["motion_bus"]["audit"]["contract"] == "UnifiedMotionFrame"
    assert manifest["states"]["hit"]["motion_bus"]["audit"]["contract"] == "UnifiedMotionFrame"

    jump_umr = json.loads((output_dir / "umr_probe" / "umr_probe_jump.umr.json").read_text(encoding="utf-8"))
    fall_umr = json.loads((output_dir / "umr_probe" / "umr_probe_fall.umr.json").read_text(encoding="utf-8"))
    hit_umr = json.loads((output_dir / "umr_probe" / "umr_probe_hit.umr.json").read_text(encoding="utf-8"))

    assert jump_umr["frames"][0]["metadata"]["phase_kind"] == "distance_to_apex"
    assert fall_umr["frames"][0]["metadata"]["phase_kind"] == "distance_to_ground"
    assert hit_umr["frames"][0]["metadata"]["phase_kind"] == "hit_recovery"
