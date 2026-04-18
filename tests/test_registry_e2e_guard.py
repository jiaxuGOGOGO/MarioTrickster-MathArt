from __future__ import annotations

from pathlib import Path

from scripts.registry_e2e_guard import run_registry_guard


def test_registry_e2e_guard_runs(tmp_path: Path):
    payload = run_registry_guard(project_root=tmp_path, output_dir=tmp_path / "guard")
    assert payload["backend_count"] >= 5
    assert payload["failed"] == 0
    assert (tmp_path / "guard" / "registry_e2e_report.json").exists()
    assert (tmp_path / "guard" / "registry_e2e_report.md").exists()
