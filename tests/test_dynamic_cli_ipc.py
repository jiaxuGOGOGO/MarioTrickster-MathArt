from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{ROOT}:{existing}"
    return env


def test_registry_list_is_machine_readable_json() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "MarioTrickster", "--quiet", "registry", "list"],
        cwd=ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    backend_names = {entry["name"] for entry in payload["backends"]}
    assert "urp2d_bundle" in backend_names
    assert "motion_2d" in backend_names


def test_run_urp2d_bundle_subprocess_stdout_is_pure_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "urp2d_cli"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "MarioTrickster",
            "--quiet",
            "run",
            "--backend",
            "urp2d_bundle",
            "--output-dir",
            str(output_dir),
            "--name",
            "cli_smoke",
            "--set",
            "frame_count=6",
            "--set",
            "particle_budget=16",
        ],
        cwd=ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["resolved_backend"] == "urp2d_bundle"
    assert Path(payload["manifest_path"]).exists()
    assert Path(payload["artifact_paths"]["plugin_source"]).exists()
    assert Path(payload["artifact_paths"]["shader_source"]).exists()
    assert Path(payload["artifact_paths"]["vat_manifest"]).exists()

    manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["backend_type"] == "urp2d_bundle"
    assert manifest["artifact_family"] == "engine_plugin"
    assert manifest["metadata"]["engine"] == "Unity"
    assert manifest["metadata"]["plugin_type"] == "URP_2D_Bundle"
