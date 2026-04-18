#!/usr/bin/env python3
"""Registry-wide E2E guard for Golden Path backends.

Runs every registered backend through the microkernel bridge with a minimal,
backend-agnostic context. The script emits both JSON and Markdown reports so it
can serve local audits, scheduled CI, and SESSION handoff updates.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathart.core.backend_types import backend_alias_map
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge


DEFAULT_CONTEXT: dict[str, Any] = {
    "name": "registry_e2e_guard",
    "frame_count": 8,
    "frame_width": 64,
    "frame_height": 64,
    "width": 18,
    "height": 7,
    "tile_count": 0,
    "vertex_count": 0,
    "face_count": 0,
    "rule_count": 0,
    "guide_channels": ["normal", "depth", "mask", "motion_vector", "identity_ref"],
}


def run_registry_guard(project_root: Path, output_dir: Path) -> dict[str, Any]:
    bridge = MicrokernelPipelineBridge(project_root=project_root, session_id="SESSION-066")
    backends = bridge.backend_registry.all_backends()
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    for backend_name in sorted(backends.keys()):
        backend_dir = output_dir / backend_name
        backend_dir.mkdir(parents=True, exist_ok=True)
        context = dict(DEFAULT_CONTEXT)
        context.update({
            "output_dir": str(backend_dir),
            "output_path": str(backend_dir / "artifact.out"),
            "plugin_path": str(backend_dir / "bundle.cs"),
            "shader_path": str(backend_dir / "bundle.hlsl"),
            "mesh_path": str(backend_dir / "artifact.obj"),
            "material_path": str(backend_dir / "material.json"),
            "vat_manifest_path": str(backend_dir / "vat_manifest.json"),
            "workflow_path": str(backend_dir / "workflow.json"),
            "preview_path": str(backend_dir / "preview.gif"),
            "report_path": str(backend_dir / "report.json"),
            "albedo_path": str(backend_dir / "albedo.png"),
            "normal_path": str(backend_dir / "normal.png"),
            "depth_path": str(backend_dir / "depth.png"),
            "mask_path": str(backend_dir / "mask.png"),
        })
        try:
            manifest = bridge.run_backend(backend_name, context)
            results.append({
                "backend": backend_name,
                "status": "PASS",
                "artifact_family": manifest.artifact_family,
                "outputs": manifest.outputs,
                "metadata": manifest.metadata,
                "schema_hash": manifest.schema_hash,
            })
            passed += 1
        except Exception as exc:  # pragma: no cover - defensive guard path
            results.append({
                "backend": backend_name,
                "status": "FAIL",
                "error": str(exc),
            })
            failed += 1

    payload = {
        "session_id": "SESSION-066",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "backend_count": len(backends),
        "passed": passed,
        "failed": failed,
        "backend_aliases": backend_alias_map(),
        "results": results,
    }

    json_path = output_dir / "registry_e2e_report.json"
    md_path = output_dir / "registry_e2e_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_report(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["markdown_path"] = str(md_path)
    return payload


def render_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Registry E2E Guard Report",
        "",
        f"- Timestamp: {payload['timestamp']}",
        f"- Backend count: {payload['backend_count']}",
        f"- Passed: {payload['passed']}",
        f"- Failed: {payload['failed']}",
        "",
        "## Results",
        "",
        "| Backend | Status | Artifact Family | Notes |",
        "|---|---|---|---|",
    ]
    for item in payload["results"]:
        notes = item.get("error") or ", ".join(sorted(item.get("outputs", {}).keys())) or "—"
        lines.append(
            f"| {item['backend']} | {item['status']} | {item.get('artifact_family', '—')} | {notes} |"
        )
    lines.extend([
        "",
        "## Alias Map",
        "",
        "| Alias | Canonical |",
        "|---|---|",
    ])
    for alias, canonical in sorted(payload["backend_aliases"].items()):
        lines.append(f"| {alias} | {canonical} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run registry-wide backend E2E guard")
    parser.add_argument("--project-root", default=".", help="Repository root")
    parser.add_argument(
        "--output-dir",
        default="artifacts/registry_e2e_guard",
        help="Directory for generated reports",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero exit code if any backend fails",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    payload = run_registry_guard(project_root=project_root, output_dir=output_dir)
    print(f"[registry-e2e] {payload['passed']}/{payload['backend_count']} backends passed")
    print(f"[registry-e2e] JSON: {payload['json_path']}")
    print(f"[registry-e2e] Markdown: {payload['markdown_path']}")
    if args.strict and payload["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
