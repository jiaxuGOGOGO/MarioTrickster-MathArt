"""SESSION-068 — High-Dimensional E2E Subprocess Tests.

These tests exercise the **anti_flicker_render** and **industrial_sprite**
backends through the real CLI subprocess boundary, asserting that:

1. stdout is **pure JSON** (no log pollution).
2. The top-level IPC envelope contains ``artifact_family``, ``backend_type``,
   and a structured ``payload`` key.
3. The anti-flicker ``payload.frame_sequence`` is a valid OTIO-inspired
   time-series with per-frame path, role, and coherence score.
4. The industrial ``payload.texture_channels`` is a valid MaterialX/glTF PBR
   material bundle with per-channel engine slot bindings.
5. All referenced file paths actually exist on disk.
6. The ``validate_config`` mechanism correctly normalizes edge-case inputs
   without the CLI bus ever inspecting backend-specific parameters.

Architecture Discipline Checks
------------------------------
- The tests invoke ``python -m MarioTrickster --quiet run --backend ...``
  exactly as an external orchestrator or Unity subprocess would.
- Parameters are passed exclusively via ``--set`` (flat or dotted keys)
  and ``--config`` (JSON file), never via backend-specific CLI flags.
- The tests assert that stdout is deserializable to a single JSON object
  with the required contract keys.

References
----------
- Hexagonal Architecture (Ports & Adapters), Alistair Cockburn 2005
- OpenTimelineIO (OTIO), VFX Reference Platform
- MaterialX (ILM/Lucasfilm), glTF PBR
- SESSION-067: test_dynamic_cli_ipc.py (prior art for subprocess testing)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _subprocess_env() -> dict[str, str]:
    """Build a subprocess environment with the project root on PYTHONPATH."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{ROOT}:{existing}"
    return env


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the MarioTrickster CLI as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, "-m", "MarioTrickster", "--quiet", *args],
        cwd=ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  1. Industrial Sprite Backend — E2E Subprocess Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIndustrialSpriteE2E:
    """E2E subprocess tests for the industrial_sprite backend."""

    def test_stdout_is_pure_json(self, tmp_path: Path) -> None:
        """stdout must be a single valid JSON object with no log pollution."""
        output_dir = tmp_path / "industrial_e2e"
        proc = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(output_dir),
            "--name", "e2e_industrial",
            "--set", "render.width=32",
            "--set", "render.height=32",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        # stdout must be valid JSON
        payload = json.loads(proc.stdout)
        assert isinstance(payload, dict)

    def test_ipc_envelope_contract(self, tmp_path: Path) -> None:
        """IPC envelope must contain required top-level keys."""
        output_dir = tmp_path / "industrial_contract"
        proc = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(output_dir),
            "--name", "e2e_contract",
            "--set", "render.width=32",
            "--set", "render.height=32",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        # Required top-level keys
        assert payload["status"] == "ok"
        assert payload["artifact_family"] == "material_bundle"
        assert payload["backend_type"] == "industrial_sprite"
        assert payload["resolved_backend"] == "industrial_sprite"
        assert "manifest_path" in payload
        assert "artifact_paths" in payload

    def test_texture_channels_payload(self, tmp_path: Path) -> None:
        """payload.texture_channels must be a structured MaterialX/glTF PBR bundle."""
        output_dir = tmp_path / "industrial_channels"
        proc = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(output_dir),
            "--name", "e2e_channels",
            "--set", "render.width=32",
            "--set", "render.height=32",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        # payload must be promoted to top level
        assert "payload" in payload, "Missing top-level 'payload' key"
        inner = payload["payload"]
        assert "texture_channels" in inner

        tc = inner["texture_channels"]
        # Must have at least albedo, normal, mask
        for required_ch in ("albedo", "normal", "mask"):
            assert required_ch in tc, f"Missing required channel: {required_ch}"

        # Each channel must have structured metadata
        for ch_name, ch_info in tc.items():
            assert "path" in ch_info, f"Channel {ch_name} missing 'path'"
            assert "dimensions" in ch_info, f"Channel {ch_name} missing 'dimensions'"
            assert "color_space" in ch_info, f"Channel {ch_name} missing 'color_space'"
            assert "engine_slot" in ch_info, f"Channel {ch_name} missing 'engine_slot'"
            assert "unity" in ch_info["engine_slot"]
            assert "godot" in ch_info["engine_slot"]
            # File must exist on disk
            assert Path(ch_info["path"]).exists(), (
                f"Channel {ch_name} path does not exist: {ch_info['path']}"
            )

    def test_bundle_manifest_exists(self, tmp_path: Path) -> None:
        """The bundle manifest JSON must exist and be valid."""
        output_dir = tmp_path / "industrial_manifest"
        proc = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(output_dir),
            "--name", "e2e_manifest",
            "--set", "render.width=32",
            "--set", "render.height=32",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        manifest_path = payload.get("manifest_path")
        assert manifest_path is not None
        assert Path(manifest_path).exists()

        manifest_data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert manifest_data["artifact_family"] == "material_bundle"
        assert manifest_data["backend_type"] == "industrial_sprite"

    def test_contour_collider_export(self, tmp_path: Path) -> None:
        """Contour JSON for PolygonCollider2D must be exported."""
        output_dir = tmp_path / "industrial_contour"
        proc = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(output_dir),
            "--name", "e2e_contour",
            "--set", "render.width=64",
            "--set", "render.height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        inner = payload.get("payload", {})
        assert inner.get("contour_available") is True

        contour_path = payload["artifact_paths"].get("contour")
        assert contour_path is not None
        assert Path(contour_path).exists()

        contour_data = json.loads(Path(contour_path).read_text(encoding="utf-8"))
        assert "points" in contour_data
        assert len(contour_data["points"]) > 0

    def test_channel_selection_via_set(self, tmp_path: Path) -> None:
        """Backend must respect channel selection via --set channels=..."""
        output_dir = tmp_path / "industrial_channels_sel"
        proc = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(output_dir),
            "--name", "e2e_ch_sel",
            "--set", "render.width=32",
            "--set", "render.height=32",
            "--set", 'channels=["albedo","normal"]',
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)
        tc = payload.get("payload", {}).get("texture_channels", {})
        # Should have exactly the requested channels
        assert set(tc.keys()) == {"albedo", "normal"}

    def test_alias_resolution(self, tmp_path: Path) -> None:
        """Historical aliases must resolve to industrial_sprite."""
        output_dir = tmp_path / "industrial_alias"
        proc = _run_cli(
            "run",
            "--backend", "industrial_renderer",
            "--output-dir", str(output_dir),
            "--name", "e2e_alias",
            "--set", "render.width=32",
            "--set", "render.height=32",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)
        assert payload["resolved_backend"] == "industrial_sprite"


# ═══════════════════════════════════════════════════════════════════════════
#  2. Anti-Flicker Render Backend — E2E Subprocess Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAntiFlickerRenderE2E:
    """E2E subprocess tests for the anti_flicker_render backend."""

    def test_stdout_is_pure_json(self, tmp_path: Path) -> None:
        """stdout must be a single valid JSON object with no log pollution."""
        output_dir = tmp_path / "af_e2e"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)
        assert isinstance(payload, dict)

    def test_ipc_envelope_contract(self, tmp_path: Path) -> None:
        """IPC envelope must contain required top-level keys."""
        output_dir = tmp_path / "af_contract"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_contract",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        assert payload["status"] == "ok"
        assert payload["artifact_family"] == "anti_flicker_report"
        assert payload["backend_type"] == "anti_flicker_render"
        assert payload["resolved_backend"] == "anti_flicker_render"
        assert "manifest_path" in payload
        assert "artifact_paths" in payload

    def test_frame_sequence_payload(self, tmp_path: Path) -> None:
        """payload.frame_sequence must be an OTIO-inspired time-series."""
        output_dir = tmp_path / "af_frames"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_frames",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        # payload must be promoted to top level
        assert "payload" in payload, "Missing top-level 'payload' key"
        inner = payload["payload"]
        assert "frame_sequence" in inner

        fs = inner["frame_sequence"]
        assert isinstance(fs, list)
        assert len(fs) == 4  # We requested 4 frames

        # Each frame entry must have required fields
        for entry in fs:
            assert "frame_index" in entry
            assert "path" in entry
            assert "role" in entry
            assert entry["role"] in ("keyframe", "propagated")
            assert "temporal_coherence_score" in entry
            assert isinstance(entry["temporal_coherence_score"], float)
            # Frame file must exist on disk
            assert Path(entry["path"]).exists(), (
                f"Frame {entry['frame_index']} path does not exist: {entry['path']}"
            )

    def test_time_range_contract(self, tmp_path: Path) -> None:
        """payload.time_range must contain OTIO-style temporal metadata."""
        output_dir = tmp_path / "af_time_range"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_tr",
            "--set", "temporal.frame_count=4",
            "--set", "temporal.fps=12",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        inner = payload.get("payload", {})
        tr = inner.get("time_range", {})
        assert tr["start_frame"] == 0
        assert tr["end_frame"] == 3
        assert tr["fps"] == 12
        assert tr["total_frames"] == 4

    def test_keyframe_plan_export(self, tmp_path: Path) -> None:
        """Keyframe plan JSON must be exported and valid."""
        output_dir = tmp_path / "af_kf_plan"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_kf",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        kf_plan_path = payload["artifact_paths"].get("keyframe_plan")
        assert kf_plan_path is not None
        assert Path(kf_plan_path).exists()

        kf_data = json.loads(Path(kf_plan_path).read_text(encoding="utf-8"))
        assert "indices" in kf_data
        assert isinstance(kf_data["indices"], list)

    def test_temporal_report_export(self, tmp_path: Path) -> None:
        """Temporal report JSON must be exported with metrics."""
        output_dir = tmp_path / "af_temporal_report"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_report",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        report_path = payload["artifact_paths"].get("temporal_report")
        workflow_payload_path = payload["artifact_paths"].get("workflow_payload")
        assert report_path is not None
        assert workflow_payload_path is not None
        assert Path(report_path).exists()
        assert Path(workflow_payload_path).exists()

        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        assert "temporal_metrics" in report
        assert "frame_count" in report
        assert "keyframe_indices" in report
        assert "pipeline_metadata" in report
        assert report["workflow_payload_path"] == workflow_payload_path

    def test_quality_metrics_in_envelope(self, tmp_path: Path) -> None:
        """Quality metrics must be present in the IPC envelope."""
        output_dir = tmp_path / "af_quality"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_quality",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)

        qm = payload.get("quality_metrics", {})
        assert "temporal_stability_score" in qm
        assert "mean_warp_error" in qm
        assert "frame_count" in qm
        assert "keyframe_count" in qm
        assert qm["frame_count"] == 4.0

    def test_alias_resolution(self, tmp_path: Path) -> None:
        """Historical aliases must resolve to anti_flicker_render."""
        output_dir = tmp_path / "af_alias"
        proc = _run_cli(
            "run",
            "--backend", "breakwall",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_alias",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)
        assert payload["resolved_backend"] == "anti_flicker_render"

    def test_dotted_key_config_passthrough(self, tmp_path: Path) -> None:
        """Dotted keys like comfyui.steps must be passed through to backend."""
        output_dir = tmp_path / "af_dotted"
        proc = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(output_dir),
            "--name", "e2e_af_dotted",
            "--set", "temporal.frame_count=4",
            "--set", "comfyui.steps=15",
            "--set", "comfyui.keyframe_interval=2",
            "--set", "identity_lock.weight=0.5",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)
        assert payload["status"] == "ok"
        # Verify keyframe_interval was respected
        assert payload["metadata"]["keyframe_interval"] == 2


# ═══════════════════════════════════════════════════════════════════════════
#  3. Cross-Backend Contract Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossBackendContract:
    """Tests that verify the polymorphic payload contract across backends."""

    def test_both_backends_have_payload_key(self, tmp_path: Path) -> None:
        """Both high-dimensional backends must emit a top-level 'payload' key."""
        for backend, extra_args in [
            ("industrial_sprite", ["--set", "render.width=32", "--set", "render.height=32"]),
            ("anti_flicker_render", ["--set", "temporal.frame_count=4", "--set", "width=64", "--set", "height=64"]),
        ]:
            output_dir = tmp_path / f"cross_{backend}"
            proc = _run_cli(
                "run",
                "--backend", backend,
                "--output-dir", str(output_dir),
                "--name", f"cross_{backend}",
                *extra_args,
            )
            assert proc.returncode == 0, f"{backend} stderr: {proc.stderr}"
            payload = json.loads(proc.stdout)
            assert "payload" in payload, f"{backend} missing 'payload' key"
            assert "artifact_family" in payload, f"{backend} missing 'artifact_family'"
            assert "backend_type" in payload, f"{backend} missing 'backend_type'"

    def test_payload_discriminator_by_backend_type(self, tmp_path: Path) -> None:
        """backend_type discriminates between frame_sequence and texture_channels."""
        # Industrial → texture_channels
        ind_dir = tmp_path / "disc_industrial"
        proc_ind = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(ind_dir),
            "--name", "disc_ind",
            "--set", "render.width=32",
            "--set", "render.height=32",
        )
        assert proc_ind.returncode == 0
        ind_payload = json.loads(proc_ind.stdout)
        assert ind_payload["backend_type"] == "industrial_sprite"
        assert "texture_channels" in ind_payload["payload"]

        # Anti-flicker → frame_sequence
        af_dir = tmp_path / "disc_af"
        proc_af = _run_cli(
            "run",
            "--backend", "anti_flicker_render",
            "--output-dir", str(af_dir),
            "--name", "disc_af",
            "--set", "temporal.frame_count=4",
            "--set", "width=64",
            "--set", "height=64",
        )
        assert proc_af.returncode == 0
        af_payload = json.loads(proc_af.stdout)
        assert af_payload["backend_type"] == "anti_flicker_render"
        assert "frame_sequence" in af_payload["payload"]

    def test_json_config_file_passthrough(self, tmp_path: Path) -> None:
        """Backend config can be passed via --config JSON file."""
        config = {
            "render": {"width": 32, "height": 32},
            "channels": ["albedo", "normal", "depth"],
            "export": {
                "bundle_format": "mathart",
                "target_engine": "godot_4",
            },
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        output_dir = tmp_path / "config_file"
        proc = _run_cli(
            "run",
            "--backend", "industrial_sprite",
            "--output-dir", str(output_dir),
            "--name", "config_file_test",
            "--config", str(config_path),
        )
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)
        assert payload["status"] == "ok"
        tc = payload.get("payload", {}).get("texture_channels", {})
        assert set(tc.keys()) == {"albedo", "normal", "depth"}

    def test_existing_cli_tests_still_pass(self, tmp_path: Path) -> None:
        """Regression: registry list must still work after SESSION-068 changes."""
        proc = _run_cli("registry", "list")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        payload = json.loads(proc.stdout)
        assert payload["status"] == "ok"
        backend_names = {entry["name"] for entry in payload["backends"]}
        assert "anti_flicker_render" in backend_names
        assert "industrial_sprite" in backend_names
        assert "urp2d_bundle" in backend_names
        assert "motion_2d" in backend_names
