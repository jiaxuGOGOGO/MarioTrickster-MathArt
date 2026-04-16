"""SESSION-040: Pipeline Contract & End-to-End Determinism Tests.

Tests for:
1. UMR_Context immutability and deterministic hashing
2. PipelineContractGuard fail-fast enforcement
3. PipelineContractError on legacy bypass attempts
4. UMR_Auditor deterministic hash sealing
5. ContactFlickerDetector validation
6. Phase-driven idle frame generation (no legacy path)
7. End-to-end produce_character_pack contract integration
8. .umr_manifest.json generation and verification

References:
- Mike Acton, "Data-Oriented Design and C++", CppCon 2014
- Glenn Fiedler, "Deterministic Lockstep", Gaffer on Games, 2014
- Pixar USD Schema Validation & CI mechanism
"""
import json
import os
import tempfile
from pathlib import Path

import pytest

from mathart.pipeline_contract import (
    UMR_Context,
    PipelineContractError,
    PipelineContractGuard,
)
from mathart.pipeline_auditor import (
    UMR_Auditor,
    ManifestSeal,
    ContactFlickerDetector,
)
from mathart.animation.phase_driven_idle import (
    phase_driven_idle,
    phase_driven_idle_frame,
)
from mathart.animation.unified_motion import (
    UnifiedMotionFrame,
    MotionRootTransform,
    MotionContactState,
    infer_contact_tags,
)


# ── UMR_Context Tests ────────────────────────────────────────────────────────


class TestUMRContext:
    """Test the immutable pipeline context."""

    def test_frozen_immutability(self):
        """UMR_Context must be frozen — no attribute mutation allowed."""
        ctx = UMR_Context(character_name="test")
        with pytest.raises(AttributeError):
            ctx.character_name = "modified"

    def test_deterministic_hash(self):
        """Same parameters must produce the same context_hash."""
        ctx1 = UMR_Context(character_name="mario", random_seed=42)
        ctx2 = UMR_Context(character_name="mario", random_seed=42)
        assert ctx1.context_hash == ctx2.context_hash

    def test_different_params_different_hash(self):
        """Different parameters must produce different hashes."""
        ctx1 = UMR_Context(character_name="mario", random_seed=42)
        ctx2 = UMR_Context(character_name="mario", random_seed=43)
        assert ctx1.context_hash != ctx2.context_hash

    def test_serialization_roundtrip(self):
        """to_dict() must produce a JSON-serializable dictionary."""
        ctx = UMR_Context(
            character_name="test",
            states=("idle", "run"),
            state_frames=(("idle", 4), ("run", 8)),
        )
        d = ctx.to_dict()
        assert d["character_name"] == "test"
        assert d["states"] == ["idle", "run"]
        assert d["state_frames"] == {"idle": 4, "run": 8}
        # Must be JSON-serializable
        json_str = json.dumps(d, sort_keys=True)
        assert "test" in json_str

    def test_from_character_spec(self):
        """from_character_spec must correctly bridge CharacterSpec to UMR_Context."""
        from mathart.pipeline import CharacterSpec
        spec = CharacterSpec(
            name="test_char",
            preset="mario",
            frame_width=32,
            frame_height=32,
            fps=12,
            states=["idle", "run"],
        )
        ctx = UMR_Context.from_character_spec(spec)
        assert ctx.character_name == "test_char"
        assert ctx.preset == "mario"
        assert ctx.states == ("idle", "run")
        assert isinstance(ctx.context_hash, str)
        assert len(ctx.context_hash) == 64  # SHA-256 hex


# ── PipelineContractGuard Tests ──────────────────────────────────────────────


class TestPipelineContractGuard:
    """Test the runtime contract enforcer."""

    def test_requires_umr_context(self):
        """Guard must reject non-UMR_Context objects."""
        with pytest.raises(PipelineContractError, match="missing_context"):
            PipelineContractGuard("not a context")

    def test_reject_legacy_bypass(self):
        """Guard must reject legacy_pose_adapter invocations."""
        ctx = UMR_Context(character_name="test")
        guard = PipelineContractGuard(ctx)
        with pytest.raises(PipelineContractError, match="legacy_path_invoked"):
            guard.reject_legacy_bypass("legacy_pose_adapter", caller="test")

    def test_accept_phase_driven(self):
        """Guard must accept phase_driven generator mode."""
        ctx = UMR_Context(character_name="test")
        guard = PipelineContractGuard(ctx)
        # Should not raise
        guard.reject_legacy_bypass("phase_driven", caller="test")
        assert len(guard.violations) == 0

    def test_validate_required_fields(self):
        """Guard must reject frames missing required UMR fields."""
        ctx = UMR_Context(character_name="test")
        guard = PipelineContractGuard(ctx)
        incomplete_frame = {"time": 0.0, "phase": 0.0}
        with pytest.raises(PipelineContractError, match="missing_fields"):
            guard.validate_required_fields(incomplete_frame, caller="test")

    def test_validate_complete_frame(self):
        """Guard must accept frames with all required fields."""
        ctx = UMR_Context(character_name="test")
        guard = PipelineContractGuard(ctx)
        complete_frame = {
            "time": 0.0,
            "phase": 0.0,
            "root_transform": {"x": 0, "y": 0},
            "joint_local_rotations": {"spine": 0.1},
            "contact_tags": {"left_foot": True},
        }
        guard.validate_required_fields(complete_frame, caller="test")
        assert len(guard.violations) == 0

    def test_hash_seal_validation(self):
        """Guard must reject hash mismatches."""
        ctx = UMR_Context(character_name="test")
        guard = PipelineContractGuard(ctx)
        with pytest.raises(PipelineContractError, match="hash_mismatch"):
            guard.validate_hash_seal("abc123", "def456", caller="test")

    def test_summary(self):
        """Guard summary must report clean status when no violations."""
        ctx = UMR_Context(character_name="test")
        guard = PipelineContractGuard(ctx)
        summary = guard.summary()
        assert summary["contract_status"] == "CLEAN"
        assert summary["violation_count"] == 0


# ── UMR_Auditor Tests ────────────────────────────────────────────────────────


class TestUMRAuditor:
    """Test the deterministic hash sealing auditor."""

    def _make_frame_dict(self, t: float, state: str = "idle") -> dict:
        """Helper: create a minimal frame dictionary."""
        return {
            "time": t,
            "phase": t % 1.0,
            "frame_index": int(t * 8),
            "source_state": state,
            "root_transform": {"x": 0.0, "y": 0.0, "rotation": 0.0,
                               "velocity_x": 0.0, "velocity_y": 0.0,
                               "angular_velocity": 0.0},
            "joint_local_rotations": {"spine": 0.03 * t, "head": 0.02 * t},
            "contact_tags": {"left_foot": True, "right_foot": True,
                             "left_hand": False, "right_hand": False},
        }

    def test_deterministic_seal(self):
        """Same inputs must produce the same pipeline_hash."""
        ctx = UMR_Context(character_name="test", random_seed=42)

        auditor1 = UMR_Auditor(ctx)
        auditor2 = UMR_Auditor(ctx)

        frames = [self._make_frame_dict(i / 8.0) for i in range(8)]
        auditor1.register_clip("idle", frames)
        auditor2.register_clip("idle", frames)

        seal1 = auditor1.seal()
        seal2 = auditor2.seal()

        assert seal1.pipeline_hash == seal2.pipeline_hash
        assert seal1.contact_tag_hash == seal2.contact_tag_hash

    def test_different_frames_different_hash(self):
        """Different frame data must produce different hashes."""
        ctx = UMR_Context(character_name="test")

        auditor1 = UMR_Auditor(ctx)
        auditor2 = UMR_Auditor(ctx)

        frames1 = [self._make_frame_dict(i / 8.0) for i in range(8)]
        frames2 = [self._make_frame_dict(i / 8.0 + 0.001) for i in range(8)]

        auditor1.register_clip("idle", frames1)
        auditor2.register_clip("idle", frames2)

        assert auditor1.seal().pipeline_hash != auditor2.seal().pipeline_hash

    def test_save_manifest(self):
        """save_manifest must write a valid .umr_manifest.json file."""
        ctx = UMR_Context(character_name="test")
        auditor = UMR_Auditor(ctx)
        frames = [self._make_frame_dict(i / 8.0) for i in range(8)]
        auditor.register_clip("idle", frames)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".umr_manifest.json")
            seal = auditor.save_manifest(path)

            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)

            assert "seal" in data
            assert "context" in data
            assert data["seal"]["pipeline_hash"] == seal.pipeline_hash
            assert data["seal"]["seal_version"] == "umr_manifest_seal_v1"

    def test_verify_against_golden_master(self):
        """verify_against must pass for identical runs and fail for different ones."""
        ctx = UMR_Context(character_name="test", random_seed=42)

        # First run: create golden master
        auditor1 = UMR_Auditor(ctx)
        frames = [self._make_frame_dict(i / 8.0) for i in range(8)]
        auditor1.register_clip("idle", frames)
        golden = auditor1.seal()

        # Second run: same data
        auditor2 = UMR_Auditor(ctx)
        auditor2.register_clip("idle", frames)
        assert auditor2.verify_against(golden) is True

        # Third run: different data
        auditor3 = UMR_Auditor(ctx)
        frames_bad = [self._make_frame_dict(i / 8.0 + 0.1) for i in range(8)]
        auditor3.register_clip("idle", frames_bad)
        with pytest.raises(PipelineContractError, match="hash_mismatch"):
            auditor3.verify_against(golden)

    def test_manifest_seal_roundtrip(self):
        """ManifestSeal must survive to_dict/from_dict roundtrip."""
        seal = ManifestSeal(
            context_hash="abc123",
            pipeline_hash="def456",
            state_hashes=(("idle", "hash1"), ("run", "hash2")),
            contact_tag_hash="ghi789",
            node_order=("physics", "biomechanics"),
            frame_count=16,
            timestamp="2026-04-16T00:00:00Z",
        )
        d = seal.to_dict()
        restored = ManifestSeal.from_dict(d)
        assert restored.pipeline_hash == seal.pipeline_hash
        assert restored.state_hashes == seal.state_hashes


# ── ContactFlickerDetector Tests ─────────────────────────────────────────────


class TestContactFlickerDetector:
    """Test the contact tag oscillation detector."""

    def test_clean_clip(self):
        """Stable contacts should pass without flicker."""
        detector = ContactFlickerDetector()
        frames = [
            {"contact_tags": {"left_foot": True, "right_foot": True}} for _ in range(8)
        ]
        report = detector.check_clip(frames)
        assert report["clean"] is True

    def test_detect_flicker(self):
        """Rapid toggling should be detected as flicker."""
        detector = ContactFlickerDetector(max_toggles_per_window=2, window_size=4)
        frames = []
        for i in range(8):
            frames.append({
                "contact_tags": {
                    "left_foot": i % 2 == 0,
                    "right_foot": True,
                    "left_hand": False,
                    "right_hand": False,
                }
            })
        report = detector.check_clip(frames)
        assert report["clean"] is False
        assert report["flicker_count"] > 0


# ── Phase-Driven Idle Tests ──────────────────────────────────────────────────


class TestPhaseDrivenIdle:
    """Test the SESSION-040 phase-driven idle generator."""

    def test_idle_pose_keys(self):
        """phase_driven_idle must return the same joint keys as idle_animation."""
        pose = phase_driven_idle(0.0)
        expected_keys = {"spine", "chest", "neck", "head",
                         "l_shoulder", "r_shoulder", "l_elbow", "r_elbow"}
        assert set(pose.keys()) == expected_keys

    def test_idle_frame_is_umr(self):
        """phase_driven_idle_frame must return a UnifiedMotionFrame."""
        frame = phase_driven_idle_frame(0.0, time=0.0, frame_index=0)
        assert isinstance(frame, UnifiedMotionFrame)
        assert frame.source_state == "idle"
        assert frame.metadata.get("generator") == "phase_driven_idle"

    def test_idle_frame_contacts(self):
        """Idle frames must have both feet grounded."""
        frame = phase_driven_idle_frame(0.5, time=0.5, frame_index=4)
        assert frame.contact_tags.left_foot is True
        assert frame.contact_tags.right_foot is True

    def test_idle_determinism(self):
        """Same inputs must produce identical idle frames."""
        f1 = phase_driven_idle_frame(0.25, time=0.25, frame_index=2)
        f2 = phase_driven_idle_frame(0.25, time=0.25, frame_index=2)
        assert f1.joint_local_rotations == f2.joint_local_rotations
        assert f1.root_transform == f2.root_transform


# ── End-to-End Integration Tests ─────────────────────────────────────────────


class TestPipelineContractIntegration:
    """End-to-end tests for the SESSION-040 pipeline contract."""

    def test_produce_character_pack_has_umr_manifest(self):
        """produce_character_pack must generate .umr_manifest.json."""
        from mathart.pipeline import AssetPipeline, CharacterSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = AssetPipeline(output_dir=tmpdir, verbose=False)
            spec = CharacterSpec(
                name="test_contract",
                preset="mario",
                frame_width=16,
                frame_height=16,
                fps=8,
                frames_per_state=4,
                states=["idle", "run"],
                enable_physics=False,
                enable_biomechanics=False,
            )
            result = pipeline.produce_character_pack(spec)

            # Check .umr_manifest.json exists
            manifest_path = os.path.join(tmpdir, "test_contract", ".umr_manifest.json")
            assert os.path.exists(manifest_path), \
                f".umr_manifest.json not found in {os.listdir(os.path.join(tmpdir, 'test_contract'))}"

            with open(manifest_path) as f:
                manifest = json.load(f)

            assert "seal" in manifest
            assert manifest["seal"]["seal_version"] == "umr_manifest_seal_v1"
            assert len(manifest["seal"]["pipeline_hash"]) == 64
            assert manifest["seal"]["frame_count"] == 8  # 4 frames * 2 states

    def test_produce_character_pack_pipeline_contract_in_manifest(self):
        """Character manifest must include pipeline_contract section."""
        from mathart.pipeline import AssetPipeline, CharacterSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = AssetPipeline(output_dir=tmpdir, verbose=False)
            spec = CharacterSpec(
                name="test_contract2",
                preset="mario",
                frame_width=16,
                frame_height=16,
                fps=8,
                frames_per_state=4,
                states=["idle"],
                enable_physics=False,
                enable_biomechanics=False,
            )
            result = pipeline.produce_character_pack(spec)

            char_manifest_path = os.path.join(tmpdir, "test_contract2",
                                               "test_contract2_character_manifest.json")
            with open(char_manifest_path) as f:
                manifest = json.load(f)

            assert "pipeline_contract" in manifest
            assert manifest["pipeline_contract"]["all_states_phase_driven"] is True
            assert manifest["pipeline_contract"]["legacy_bypass_blocked"] is True
            assert manifest["pipeline_contract"]["session"] == "SESSION-040"

    def test_deterministic_hash_across_runs(self):
        """Two identical runs must produce the same pipeline_hash."""
        from mathart.pipeline import AssetPipeline, CharacterSpec

        hashes = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmpdir:
                pipeline = AssetPipeline(output_dir=tmpdir, verbose=False)
                spec = CharacterSpec(
                    name="determinism_test",
                    preset="mario",
                    frame_width=16,
                    frame_height=16,
                    fps=8,
                    frames_per_state=4,
                    states=["idle"],
                    enable_physics=False,
                    enable_biomechanics=False,
                )
                pipeline.produce_character_pack(spec)
                manifest_path = os.path.join(tmpdir, "determinism_test",
                                              ".umr_manifest.json")
                with open(manifest_path) as f:
                    data = json.load(f)
                hashes.append(data["seal"]["pipeline_hash"])

        assert hashes[0] == hashes[1], \
            f"Deterministic hash mismatch: {hashes[0][:16]}... vs {hashes[1][:16]}..."

    def test_unknown_state_raises_contract_error(self):
        """Unknown states must raise PipelineContractError, not ValueError."""
        from mathart.pipeline import AssetPipeline, CharacterSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = AssetPipeline(output_dir=tmpdir, verbose=False)
            spec = CharacterSpec(
                name="bad_state",
                preset="mario",
                states=["idle", "nonexistent_state"],
                enable_physics=False,
                enable_biomechanics=False,
            )
            with pytest.raises(PipelineContractError, match="unknown_state"):
                pipeline.produce_character_pack(spec)
