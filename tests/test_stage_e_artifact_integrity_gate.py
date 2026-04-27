"""Stage E — Artifact integrity gate.

This guard goes beyond schema validation: every declared artifact path must
exist, be non-empty, and pass a lightweight format parser appropriate for its
file extension. The test reuses a deterministic offline production chain so it
can run in CI without GPU, network, or ComfyUI services.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np

from mathart.core.artifact_schema import ArtifactManifest, validate_artifact
from mathart.core.backend_registry import get_registry
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge
from mathart.workspace.semantic_orchestrator import SemanticOrchestrator


def _parse_png(path: Path) -> dict[str, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"{path} is not a PNG file"
    assert len(data) >= 33, f"{path} is too small for a PNG IHDR"
    assert data[12:16] == b"IHDR", f"{path} missing PNG IHDR chunk"
    width, height = struct.unpack(">II", data[16:24])
    assert width > 0 and height > 0, f"{path} has invalid PNG dimensions"
    assert b"IEND" in data[-32:], f"{path} missing PNG IEND chunk"
    return {"width": width, "height": height}


def _parse_json(path: Path) -> Any:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload is not None, f"{path} parsed to None"
    return payload


def _parse_npy(path: Path) -> np.ndarray:
    arr = np.load(path, allow_pickle=False)
    assert arr.size > 0, f"{path} contains an empty array"
    if np.issubdtype(arr.dtype, np.number):
        assert np.isfinite(arr).all(), f"{path} contains NaN or Inf values"
    return arr


def _parse_npz(path: Path) -> list[str]:
    archive = np.load(path, allow_pickle=False)
    keys = list(archive.files)
    assert keys, f"{path} contains no arrays"
    for key in keys:
        arr = archive[key]
        assert arr.size > 0, f"{path}:{key} contains an empty array"
        if np.issubdtype(arr.dtype, np.number):
            assert np.isfinite(arr).all(), f"{path}:{key} contains NaN or Inf values"
    return keys


def _parse_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    assert text.strip(), f"{path} is blank"
    return text


def _parse_declared_output(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _parse_json(path)
    if suffix == ".npy":
        return _parse_npy(path)
    if suffix == ".npz":
        return _parse_npz(path)
    if suffix == ".png":
        return _parse_png(path)
    if suffix in {
        ".anim",
        ".asset",
        ".controller",
        ".cs",
        ".hlsl",
        ".mat",
        ".meta",
        ".obj",
        ".shader",
        ".txt",
        ".yaml",
        ".yml",
    }:
        return _parse_text(path)
    if suffix in {".hdr", ".exr", ".gif", ".jpg", ".jpeg", ".webp"}:
        data = path.read_bytes()
        assert data, f"{path} is empty"
        return {"bytes": len(data)}
    data = path.read_bytes()
    assert data, f"{path} is empty"
    return {"bytes": len(data)}


def _assert_manifest_integrity(manifest: ArtifactManifest, backend_name: str) -> None:
    errors = validate_artifact(manifest)
    assert not errors, f"{backend_name} produced invalid manifest: {errors}"
    assert manifest.schema_hash == manifest.compute_hash(), (
        f"{backend_name} schema hash mismatch: "
        f"{manifest.schema_hash} != {manifest.compute_hash()}"
    )
    assert manifest.outputs, f"{backend_name} declared no outputs"

    parsed_roles: dict[str, str] = {}
    for role, raw_path in manifest.outputs.items():
        assert raw_path, f"{backend_name}.{role} has an empty path"
        path = Path(raw_path)
        assert path.exists(), f"{backend_name}.{role} missing: {path}"
        if path.is_file():
            assert path.stat().st_size > 0, f"{backend_name}.{role} is empty: {path}"
            _parse_declared_output(path)
            parsed_roles[role] = path.suffix.lower() or "<no_suffix>"
        else:
            children = [p for p in path.rglob("*") if p.is_file()]
            assert children, f"{backend_name}.{role} directory has no files: {path}"
            for child in children[:8]:
                assert child.stat().st_size > 0, f"{backend_name}.{role} child empty: {child}"
                _parse_declared_output(child)
            parsed_roles[role] = "<directory>"

    assert parsed_roles, f"{backend_name} had no parseable declared outputs"


def _run_offline_asset_chain(tmp_path: Path) -> dict[str, ArtifactManifest]:
    registry = get_registry()
    intent = SemanticOrchestrator().resolve_full_intent(
        raw_intent={},
        vibe="四足机械狗 高精度导出 赛博纹理",
        registry=registry,
    )
    assert intent["skeleton_topology"] == "quadruped"

    bridge = MicrokernelPipelineBridge(
        project_root=tmp_path,
        session_id="SESSION-STAGE-E",
    )

    quadruped_manifest = bridge.run_backend(
        "quadruped_physics",
        {
            "output_dir": str(tmp_path / "01_quadruped_physics"),
            "num_frames": 8,
            "num_vertices": 16,
            "channels": 3,
            "fps": 12,
            "skeleton_topology": "quadruped",
            "vibe": "四足机械狗",
        },
    )
    positions = _parse_npy(Path(quadruped_manifest.outputs["positions_npy"]))
    assert positions.shape == (8, 16, 3)

    vat_manifest = bridge.run_backend(
        "high_precision_vat",
        {
            "output_dir": str(tmp_path / "02_high_precision_vat"),
            "positions": positions,
            "num_vertices": 16,
            "channels": 3,
            "fps": 12,
            "asset_name": "stage_e_quadruped_vat",
            "skeleton_topology": "quadruped",
        },
    )

    texture_manifest = bridge.run_backend(
        "cppn_texture_evolution",
        {
            "output_dir": str(tmp_path / "03_cppn_texture"),
            "num_textures": 1,
            "resolution": 32,
            "seed": 11,
            "vibe": "赛博纹理",
        },
    )

    provenance_manifest = bridge.run_backend(
        "provenance_audit",
        {
            "output_dir": str(tmp_path / "04_provenance_audit"),
            "session_id": "SESSION-STAGE-E",
            "raw_vibe": "四足机械狗 高精度导出 赛博纹理",
            "genotype_flat": {"speed": 1.0, "fps": 12, "num_vertices": 16},
            "backend_name": "stage_e_integrity_chain",
            "backend_consumed_params": {
                "quadruped_positions": quadruped_manifest.outputs["positions_npy"],
                "vat_position_tex": vat_manifest.outputs["position_tex"],
                "texture_albedo": texture_manifest.outputs["albedo"],
            },
        },
    )

    return {
        "quadruped_physics": quadruped_manifest,
        "high_precision_vat": vat_manifest,
        "cppn_texture_evolution": texture_manifest,
        "provenance_audit": provenance_manifest,
    }


def test_stage_e_declared_artifacts_are_parseable_and_integral(tmp_path: Path) -> None:
    manifests = _run_offline_asset_chain(tmp_path)

    for backend_name, manifest in manifests.items():
        _assert_manifest_integrity(manifest, backend_name)

    vat_manifest_json = _parse_json(Path(manifests["high_precision_vat"].outputs["manifest"]))
    assert int(vat_manifest_json["frame_count"]) == 8
    assert int(vat_manifest_json["vertex_count"]) == 16

    texture_png = _parse_png(Path(manifests["cppn_texture_evolution"].outputs["albedo"]))
    assert texture_png == {"width": 32, "height": 32}

    provenance_payload = _parse_json(Path(manifests["provenance_audit"].outputs["report_file"]))
    assert "summary" in provenance_payload
