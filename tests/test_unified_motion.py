from __future__ import annotations

import json
from pathlib import Path

from mathart.animation import (
    AnglePoseProjector,
    UnifiedMotionFrame,
    idle_animation,
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


def test_character_pipeline_exports_umr_artifacts(tmp_path: Path):
    output_dir = tmp_path / "output"
    pipeline = AssetPipeline(output_dir=str(output_dir), seed=42)
    spec = CharacterSpec(
        name="umr_probe",
        preset="mario",
        states=["idle", "run"],
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
