from __future__ import annotations
import argparse
import json
import math
import shutil
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from mathart.animation.genotype import (
    BODY_TEMPLATES,
    CharacterGenotype,
    PartSlotInstance,
    SlotType,
    bouncing_enemy_genotype,
    flying_enemy_genotype,
    mario_genotype,
    simple_enemy_genotype,
    trickster_genotype,
)
from mathart.animation.motion_2d_pipeline import Motion2DPipeline
from mathart.animation.parts3d import build_attachments_from_genotype
from mathart.core.artifact_schema import ArtifactManifest
import mathart.core.physical_ribbon_backend  # noqa: F401  # Ensure registry side-effect import.
import mathart.core.archive_delivery_backend  # noqa: F401  # SESSION-128: Ensure archive delivery backend registry side-effect import.
from mathart.level import PDGFanOutResult, PDGNode, ProceduralDependencyGraph
from mathart.pipeline import AssetPipeline
from mathart.animation.unified_gait_blender import get_motion_lane_registry  # SESSION-160: Dynamic Action Registry

_SESSION_ID = "SESSION-179"
_DEFAULT_SEED = 20260421
_DEFAULT_GPU_SLOTS = 1
_DEFAULT_COMFYUI_URL = "http://localhost:8188"


# ---------------------------------------------------------------------------
# JSON / filesystem helpers
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, ArtifactManifest):
        return value.to_dict()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return value.to_dict()
        except Exception as _to_dict_exc:  # noqa: BLE001
            # SESSION-194: never silently swallow — log and fall through to
            # repr() so the JSON encoder still completes, but the diagnostic
            # surfaces in the logs (Jim Gray fail-loud principle).
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[mass_production] _json_default: %s.to_dict() raised %s; "
                "falling back to repr().",
                type(value).__name__, _to_dict_exc,
            )
    return repr(value)


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return path


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _slug(text: str) -> str:
    filtered = [ch.lower() if ch.isalnum() else "_" for ch in str(text)]
    compact = "".join(filtered)
    while "__" in compact:
        compact = compact.replace("__", "_")
    return compact.strip("_") or "item"


def _timestamp_slug() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def _ensure_dir(path: str | Path) -> Path:
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Manifest / mesh helpers
# ---------------------------------------------------------------------------


def _save_manifest(manifest: ArtifactManifest, path: Path) -> Path:
    manifest.save(path)
    return path.resolve()


def _load_manifest(path: str | Path) -> ArtifactManifest:
    return ArtifactManifest.load(path)


def _mesh_dict_from_npz(path: str | Path) -> dict[str, np.ndarray]:
    with np.load(str(path)) as data:
        return {
            "vertices": np.asarray(data["vertices"], dtype=np.float64),
            "normals": np.asarray(data["normals"], dtype=np.float64),
            "triangles": np.asarray(data["triangles"], dtype=np.int32),
            "colors": np.asarray(data["colors"], dtype=np.uint8),
        }


def _mesh3d_from_dict(mesh: dict[str, np.ndarray]):
    from mathart.animation.orthographic_pixel_render import Mesh3D

    return Mesh3D(
        vertices=np.asarray(mesh["vertices"], dtype=np.float64),
        normals=np.asarray(mesh["normals"], dtype=np.float64),
        triangles=np.asarray(mesh["triangles"], dtype=np.int32),
        colors=np.asarray(mesh["colors"], dtype=np.uint8),
    )


def _current_rng_digest(ctx: dict[str, Any]) -> str | None:
    pdg = ctx.get("_pdg") or {}
    contract = pdg.get("rng_contract") or {}
    digest = contract.get("spawn_key_digest")
    return str(digest) if digest is not None else None


def _resolve_semantic_asset_plugins(vibe: str, explicit_plugins: list[str] | None = None) -> dict[str, Any]:
    """Resolve Unity 2D asset intent through the existing semantic orchestrator."""
    explicit = [str(item) for item in (explicit_plugins or []) if str(item).strip()]
    try:
        from mathart.core.backend_registry import BackendRegistry
        from mathart.workspace.semantic_orchestrator import resolve_vfx_plugins_from_vibe

        registered = set(BackendRegistry().all_backends().keys())
        semantic = resolve_vfx_plugins_from_vibe(str(vibe or ""), registered)
        selected = sorted((set(explicit) | set(semantic)) & registered)
        filtered = sorted((set(explicit) | set(semantic)) - registered)
        return {
            "resolver": "semantic_orchestrator",
            "vibe": str(vibe or ""),
            "registered_backend_count": len(registered),
            "explicit_plugins": explicit,
            "semantic_plugins": semantic,
            "selected_existing_backends": selected,
            "filtered_unregistered_plugins": filtered,
        }
    except Exception as exc:
        return {
            "resolver": "semantic_orchestrator",
            "vibe": str(vibe or ""),
            "explicit_plugins": explicit,
            "semantic_plugins": [],
            "selected_existing_backends": explicit,
            "filtered_unregistered_plugins": [],
            "error": str(exc),
        }


def _unity_import_contract(asset_role: str, *, fps: int = 12, ppu: int = 32, loop: bool = True) -> dict[str, Any]:
    """Unity import metadata embedded into existing manifests and summaries."""
    return {
        "engine": "Unity",
        "asset_role": str(asset_role),
        "unity_version_target": "2022.3+",
        "pixels_per_unit": int(ppu),
        "filter_mode": "Point",
        "compression": "None",
        "read_write_enabled": True,
        "alpha_is_transparency": True,
        "pivot_policy": "bottom_center" if "character" in str(asset_role) or "sprite" in str(asset_role) else "center",
        "fps": int(fps),
        "loop": bool(loop),
        "packing_policy": "manifest_declared_paths",
    }


def _quality_gate_summary(audit: dict[str, Any] | None) -> dict[str, Any]:
    audit = dict(audit or {})
    checks = list(audit.get("checks") or [])
    failed = list(audit.get("failed_checks") or [])
    hard = list(audit.get("hard_failures") or [])
    return {
        "quality_gate_profile": audit.get("audit_type", "distilled_quality_audit"),
        "overall_verdict": audit.get("verdict", "unknown"),
        "score": float(audit.get("score", 0.0) or 0.0),
        "check_count": len(checks),
        "failed_checks": failed,
        "hard_failures": hard,
        "passed": not failed,
        "requires_review": bool(failed),
    }


def _ai_polish_fidelity_policy(asset_family: str, *, ai_required: bool = True) -> dict[str, Any]:
    """AI polish is required, but guide/motion remains authoritative."""
    is_sprite = str(asset_family) == "character_sprite"
    return {
        "role": "required_controlled_visual_polish",
        "ai_required": bool(ai_required),
        "motion_authority": "guide_baking_stage",
        "shape_authority": "math_physics_pipeline",
        "guide_authority_channels": ["source_frames", "normal_maps", "depth_maps", "mask_maps"],
        "may_change_motion_timing": False,
        "may_change_silhouette_substantially": False,
        "may_change_foot_contact": False,
        "may_change_loop_closure": False,
        "must_improve_visual_quality_without_motion_regression": True,
        "accept_only_if_no_motion_regression": True,
        "fallback_asset_source": "guide_baking_albedo",
        "fallback_requires_review": True,
        "comfyui_preservation_knobs": {
            "denoising_strength": 0.28 if is_sprite else 0.42,
            "cfg_scale": 3.2 if is_sprite else 4.0,
            "controlnet_normal_weight": 0.75 if is_sprite else 0.95,
            "controlnet_depth_weight": 0.75 if is_sprite else 0.95,
            "sparsectrl_strength": 0.80 if is_sprite else 0.95,
            "temporal_consistency_required": True,
        },
    }


def _motion_metric_value(report: dict[str, Any] | None, key: str, default: float = 0.0) -> float:
    if not isinstance(report, dict):
        return default
    value = report.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    metrics = report.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get(key), (int, float)):
        return float(metrics[key])
    return default


def _build_ai_polish_fidelity_report(
    *,
    policy: dict[str, Any],
    source_quality: dict[str, Any] | None,
    final_quality: dict[str, Any] | None,
    foot_contact_report: dict[str, Any] | None = None,
    ai_manifest_metadata: dict[str, Any] | None = None,
    source_kind: str = "unknown",
) -> dict[str, Any]:
    source_quality = dict(source_quality or {})
    final_quality = dict(final_quality or {})
    foot_contact_report = dict(foot_contact_report or {})
    ai_manifest_metadata = dict(ai_manifest_metadata or {})
    bbox_src = _motion_metric_value(source_quality, "bbox_jitter_ratio")
    bbox_final = _motion_metric_value(final_quality, "bbox_jitter_ratio")
    area_src = _motion_metric_value(source_quality, "area_jitter_ratio")
    area_final = _motion_metric_value(final_quality, "area_jitter_ratio")
    loop_src = _motion_metric_value(source_quality, "loop_bbox_delta_ratio")
    loop_final = _motion_metric_value(final_quality, "loop_bbox_delta_ratio")
    tolerance = 1.15
    regressions = []
    if bbox_src > 0 and bbox_final > bbox_src * tolerance:
        regressions.append("bbox_jitter_regression")
    if area_src > 0 and area_final > area_src * tolerance:
        regressions.append("area_jitter_regression")
    if loop_src > 0 and loop_final > loop_src * tolerance:
        regressions.append("loop_closure_regression")
    if str(foot_contact_report.get("verdict") or "").lower() in {"fail", "review"}:
        regressions.append("foot_contact_review")
    ai_outputs = ai_manifest_metadata.get("outputs") or {}
    ai_attempted = bool(ai_manifest_metadata) and not bool(ai_manifest_metadata.get("skipped", False))
    ai_available = bool(ai_outputs) or bool(ai_manifest_metadata.get("manifest_path"))
    if policy.get("ai_required") and not ai_attempted:
        adoption = "review_required_ai_not_attempted"
    elif policy.get("ai_required") and not ai_available and source_kind != "ai_render":
        adoption = "review_required_ai_unavailable"
    elif regressions:
        adoption = "review_required_motion_regression"
    else:
        adoption = "ai_polish_accepted" if source_kind == "ai_render" else "guide_fallback_review_required"
    return {
        "policy_role": policy.get("role"),
        "ai_required": bool(policy.get("ai_required", True)),
        "source_kind": source_kind,
        "ai_attempted": ai_attempted,
        "ai_available": ai_available,
        "source_quality_label": source_quality.get("label"),
        "final_quality_label": final_quality.get("label"),
        "motion_regressions": regressions,
        "motion_regression": bool(regressions),
        "accept_only_if_no_motion_regression": bool(policy.get("accept_only_if_no_motion_regression", True)),
        "adoption_verdict": adoption,
        "requires_review": adoption != "ai_polish_accepted",
    }


def _load_asset_factory_feedback(ctx: dict[str, Any]) -> dict[str, Any]:
    if not bool(ctx.get("self_evolution_enabled", True)):
        return {"available": False, "reason": "self_evolution_disabled"}
    try:
        from mathart.evolution.state_vault import resolve_state_path
        project_root = Path(ctx.get("project_root") or Path.cwd()).resolve()
        state_path = resolve_state_path(project_root, "asset_factory_feedback_state.json")
        if not state_path.exists():
            return {"available": False, "state_path": str(state_path.resolve()), "reason": "state_not_found"}
        data = json.loads(state_path.read_text(encoding="utf-8"))
        records = list(data.get("records") or [])
        return {"available": bool(records), "state_path": str(state_path.resolve()), "record_count": len(records), "latest": records[-1] if records else None}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _apply_feedback_to_asset_spec(asset_spec: dict[str, Any], feedback: dict[str, Any]) -> dict[str, Any]:
    if not feedback.get("available"):
        asset_spec["feedback_consumption"] = {"applied": False, "reason": feedback.get("reason", "unavailable")}
        return asset_spec
    seed = dict((dict(feedback.get("latest") or {})).get("evolution_feedback_seed") or {})
    knobs = dict(seed.get("candidate_knobs") or {})
    gates = asset_spec.setdefault("quality_gates", {})
    style_policy = asset_spec.setdefault("style_policy", {})
    applied: dict[str, Any] = {}
    if "suggested_max_foot_slide_px" in knobs:
        gates["max_foot_slide_px"] = float(knobs["suggested_max_foot_slide_px"])
        applied["max_foot_slide_px"] = gates["max_foot_slide_px"]
    if "suggested_palette_color_count" in knobs:
        style_policy["palette_color_count"] = int(knobs["suggested_palette_color_count"])
        applied["palette_color_count"] = style_policy["palette_color_count"]
    asset_spec["feedback_consumption"] = {"applied": bool(applied), "source_state_path": feedback.get("state_path"), "source_record_count": feedback.get("record_count", 0), "previous_verdict": seed.get("verdict"), "applied_knobs": applied, "next_iteration_hints": seed.get("next_iteration_hints", [])}
    return asset_spec


def _unity_delivery_contract(batch_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "engine": "Unity",
        "delivery_root": str(batch_dir.resolve()),
        "recommended_project_path": "Assets/Generated/MathArt",
        "asset_groups": sorted({str((record.get("unity_import_contract") or {}).get("asset_role") or "unknown") for record in records}),
        "archive_policy": "reuse_existing_stage_archives_and_manifests",
        "import_policy": {"filter_mode": "Point", "compression": "None", "alpha_is_transparency": True, "preserve_manifest_paths": True},
    }


def _production_acceptance_profile(records: list[dict[str, Any]]) -> dict[str, Any]:
    fidelity = [record.get("ai_polish_fidelity_report") or {} for record in records]
    return {
        "profile_type": "Unity2DAssetFactoryAcceptanceProfile",
        "ai_required": True,
        "ai_attempted_count": sum(1 for item in fidelity if item.get("ai_attempted")),
        "accepted_ai_polish_count": sum(1 for item in fidelity if item.get("adoption_verdict") == "ai_polish_accepted"),
        "motion_regression_count": sum(1 for item in fidelity if item.get("motion_regression")),
        "requires_review_count": sum(1 for item in fidelity if item.get("requires_review")),
        "quality_pass_count": sum(1 for record in records if (record.get("quality_gate_summary") or {}).get("passed")),
        "unity_ready": bool(records),
        "current_batch_verdict": "pass" if records and all(not item.get("requires_review") for item in fidelity) else "review",
    }


def _probe_comfyui_resources(comfyui_url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    """Small stdlib-only ComfyUI resource probe used before risky live jobs."""
    try:
        import urllib.request
        base = str(comfyui_url or _DEFAULT_COMFYUI_URL).rstrip("/")
        with urllib.request.urlopen(f"{base}/system_stats", timeout=max(0.5, float(timeout))) as response:
            data = json.loads(response.read().decode("utf-8"))
        devices = list(data.get("devices") or [])
        primary = devices[0] if devices else {}
        return {
            "online": True,
            "url": base,
            "ram_free": int((data.get("system") or {}).get("ram_free") or 0),
            "ram_total": int((data.get("system") or {}).get("ram_total") or 0),
            "vram_free": int(primary.get("vram_free") or 0),
            "vram_total": int(primary.get("vram_total") or 0),
            "device_name": primary.get("name"),
            "device_type": primary.get("type"),
            "comfyui_version": (data.get("system") or {}).get("comfyui_version"),
        }
    except Exception as exc:
        return {"online": False, "url": str(comfyui_url or _DEFAULT_COMFYUI_URL), "error": str(exc)}


def _comfyui_resource_guard(ctx: dict[str, Any]) -> dict[str, Any]:
    probe = _probe_comfyui_resources(str(ctx.get("comfyui_url", _DEFAULT_COMFYUI_URL)), timeout=float(ctx.get("ai_render_connect_timeout", 5.0)))
    min_vram = int(ctx.get("ai_render_min_vram_free_bytes", 6 * 1024 * 1024 * 1024))
    min_ram = int(ctx.get("ai_render_min_ram_free_bytes", 4 * 1024 * 1024 * 1024))
    probe["min_vram_free_bytes"] = min_vram
    probe["min_ram_free_bytes"] = min_ram
    probe["safe_to_run"] = bool(probe.get("online")) and int(probe.get("vram_free") or 0) >= min_vram and int(probe.get("ram_free") or 0) >= min_ram
    if not probe["safe_to_run"]:
        reasons = []
        if not probe.get("online"):
            reasons.append("comfyui_offline")
        if int(probe.get("vram_free") or 0) < min_vram:
            reasons.append("insufficient_vram_free")
        if int(probe.get("ram_free") or 0) < min_ram:
            reasons.append("insufficient_ram_free")
        probe["guard_reasons"] = reasons
    return probe


def _save_mesh_npz(path: str | Path, mesh: dict[str, np.ndarray]) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        str(path),
        vertices=np.asarray(mesh["vertices"], dtype=np.float64),
        normals=np.asarray(mesh["normals"], dtype=np.float64),
        triangles=np.asarray(mesh["triangles"], dtype=np.int32),
        colors=np.asarray(mesh["colors"], dtype=np.uint8),
    )
    return path


def _merge_meshes(meshes: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    valid = [mesh for mesh in meshes if mesh["vertices"].size and mesh["triangles"].size]
    if not valid:
        return {
            "vertices": np.zeros((0, 3), dtype=np.float64),
            "normals": np.zeros((0, 3), dtype=np.float64),
            "triangles": np.zeros((0, 3), dtype=np.int32),
            "colors": np.zeros((0, 3), dtype=np.uint8),
        }
    vertices = []
    normals = []
    triangles = []
    colors = []
    vertex_offset = 0
    for mesh in valid:
        verts = np.asarray(mesh["vertices"], dtype=np.float64)
        norms = np.asarray(mesh["normals"], dtype=np.float64)
        tris = np.asarray(mesh["triangles"], dtype=np.int32)
        cols = np.asarray(mesh["colors"], dtype=np.uint8)
        vertices.append(verts)
        normals.append(norms)
        colors.append(cols)
        triangles.append(tris + vertex_offset)
        vertex_offset += verts.shape[0]
    return {
        "vertices": np.concatenate(vertices, axis=0),
        "normals": np.concatenate(normals, axis=0),
        "triangles": np.concatenate(triangles, axis=0),
        "colors": np.concatenate(colors, axis=0),
    }


def _manifest_output_paths(manifest: ArtifactManifest) -> list[Path]:
    paths: list[Path] = []
    for value in manifest.outputs.values():
        if isinstance(value, str):
            p = Path(value)
            if p.exists():
                paths.append(p.resolve())
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    p = Path(item)
                    if p.is_file():
                        paths.append(p.resolve())
    return paths


def _archive_manifest_outputs(manifest: ArtifactManifest, archive_root: Path, label: str) -> dict[str, str]:
    archive_dir = archive_root / label
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: dict[str, str] = {}
    for src in _manifest_output_paths(manifest):
        target = archive_dir / src.name
        if src.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
        archived[src.name] = str(target.resolve())
    manifest_path = archive_dir / f"{label}_artifact_manifest.json"
    manifest.save(manifest_path)
    archived[manifest_path.name] = str(manifest_path.resolve())
    return archived


# ---------------------------------------------------------------------------
# Deterministic character + motion synthesis helpers
# ---------------------------------------------------------------------------


def _preset_factories() -> list[tuple[str, Any]]:
    return [
        ("mario", mario_genotype),
        ("trickster", trickster_genotype),
        ("simple_enemy", simple_enemy_genotype),
        ("flying_enemy", flying_enemy_genotype),
        ("bouncing_enemy", bouncing_enemy_genotype),
    ]


_TORSO_PARTS = ["torso_breastplate", "torso_robe", "torso_vest"]
_HAND_PARTS = ["hand_sword", "hand_staff", "hand_shield"]
_FOOT_PARTS = ["foot_boots", "foot_sandals", "foot_greaves"]
# ---------------------------------------------------------------------------
# SESSION-160: Dynamic Action Registry — ERADICATE hardcoded state lists.
# Actions are now sourced from the authoritative MotionStateLaneRegistry
# (IoC pattern).  Adding new actions (Dash, Climb, AttackCombo, etc.)
# requires ZERO modification to this file — just register a new lane.
#
# Industrial Reference: Unreal Engine Animation Blueprint State Machine —
# states are dynamically registered, not hardcoded in if/else trees.
# ---------------------------------------------------------------------------
def _get_registered_motion_states() -> tuple[str, ...]:
    """Dynamically retrieve all registered motion states from the
    authoritative MotionStateLaneRegistry.

    SESSION-160: This replaces the former hardcoded ``_MOTION_STATES`` list.
    New actions are automatically discovered via the IoC registry without
    any code modification in this module.

    Returns
    -------
    tuple[str, ...]
        Sorted tuple of all registered motion state names.
    """
    return get_motion_lane_registry().names()


def _pick_part(rng: np.random.Generator, choices: list[str]) -> str:
    return choices[int(rng.integers(0, len(choices)))]


def _mutate_genotype(rng: np.random.Generator) -> tuple[str, CharacterGenotype]:
    preset_name, factory = _preset_factories()[int(rng.integers(0, len(_preset_factories())))]
    genotype = factory()

    genotype.outline_width = float(np.clip(genotype.outline_width + rng.normal(0.0, 0.006), 0.015, 0.08))
    genotype.light_angle = float(np.clip(genotype.light_angle + rng.normal(0.0, 0.12), -1.4, 1.4))
    genotype.shape_latents = [float(np.clip(v + rng.normal(0.0, 0.22), -1.0, 1.0)) for v in genotype.shape_latents]

    for key, value in list(genotype.proportion_modifiers.items()):
        genotype.proportion_modifiers[key] = float(np.clip(float(value) + rng.normal(0.0, 0.06), -0.45, 0.45))

    for idx, value in enumerate(list(genotype.palette_genes)):
        genotype.palette_genes[idx] = float(np.clip(float(value) + rng.normal(0.0, 0.05), -1.2, 1.2))

    genotype.slots[SlotType.TORSO_OVERLAY.value] = PartSlotInstance(
        slot_type=SlotType.TORSO_OVERLAY,
        part_id=_pick_part(rng, _TORSO_PARTS),
        enabled=True,
    )
    genotype.slots[SlotType.HAND_ITEM.value] = PartSlotInstance(
        slot_type=SlotType.HAND_ITEM,
        part_id=_pick_part(rng, _HAND_PARTS),
        enabled=True,
    )
    genotype.slots[SlotType.FOOT_ACCESSORY.value] = PartSlotInstance(
        slot_type=SlotType.FOOT_ACCESSORY,
        part_id=_pick_part(rng, _FOOT_PARTS),
        enabled=True,
    )
    return preset_name, genotype


def _attachment_mesh_from_genotype(genotype: CharacterGenotype) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    skeleton = genotype.build_shaped_skeleton()
    attachments = build_attachments_from_genotype(genotype, skeleton=skeleton)
    mesh_parts: list[dict[str, np.ndarray]] = []
    attachment_records: list[dict[str, Any]] = []
    for attachment in attachments:
        mesh_parts.append(
            {
                "vertices": np.asarray(attachment.mesh.vertices, dtype=np.float64),
                "normals": np.asarray(attachment.mesh.normals, dtype=np.float64),
                "triangles": np.asarray(attachment.mesh.triangles, dtype=np.int32),
                "colors": np.asarray(attachment.mesh.colors, dtype=np.uint8),
            }
        )
        attachment_records.append(
            {
                "part_id": attachment.part_id,
                "socket_name": attachment.socket_name,
                "parent_bone": attachment.parent_bone,
                "vertex_count": int(attachment.mesh.vertex_count),
                "triangle_count": int(attachment.mesh.triangle_count),
            }
        )
    mesh = _merge_meshes(mesh_parts)
    template = BODY_TEMPLATES.get(genotype.body_template)
    return mesh, {
        "attachment_count": len(attachment_records),
        "attachments": attachment_records,
        "head_units": float(getattr(skeleton, "head_units", template.head_units if template else 3.0)),
    }


def _state_from_rng(rng: np.random.Generator) -> str:
    states = _get_registered_motion_states()
    return states[int(rng.integers(0, len(states)))]


def _load_motion_frames(motion_clip_path: str | Path) -> list[dict[str, Any]]:
    data = _load_json(motion_clip_path)
    frames = data.get("frames")
    if not isinstance(frames, list):
        raise ValueError(f"Unified motion clip at {motion_clip_path} does not contain a 'frames' list")
    return frames


def _root_xy_from_frame(frame: dict[str, Any]) -> tuple[float, float]:
    root = frame.get("root") or {}
    x = float(root.get("x", root.get("position_x", 0.0)))
    y = float(root.get("y", root.get("position_y", 0.0)))
    return x, y


def _build_demo_bone_dqs(motion_clip_path: str | Path) -> np.ndarray:
    from mathart.animation.dqs_engine import dq_from_axis_angle_translation, dq_identity

    frames = _load_motion_frames(motion_clip_path)
    frame_count = max(1, len(frames))
    bone_dqs: list[np.ndarray] = []
    for index, frame in enumerate(frames):
        root_x, root_y = _root_xy_from_frame(frame)
        phase = index / max(frame_count - 1, 1)
        sway = 0.35 * math.sin(phase * math.tau)
        lift = 0.18 * math.cos(phase * math.tau * 0.5)
        dq_root = dq_identity()
        dq_tip = dq_from_axis_angle_translation(
            np.array([0.0, 0.0, 1.0], dtype=np.float64),
            np.asarray(0.65 * sway, dtype=np.float64),
            np.array([root_x * 0.15, 0.45 + root_y * 0.15 + 0.08 * lift, 0.0], dtype=np.float64),
        )
        bone_dqs.append(np.stack([dq_root, dq_tip], axis=0))
    return np.stack(bone_dqs, axis=0)


def _build_chain_points_from_motion(motion_clip_path: str | Path) -> dict[str, list[tuple[float, float]]]:
    frames = _load_motion_frames(motion_clip_path)
    root_positions = [
        _root_xy_from_frame(frame)
        for frame in frames[: max(6, min(12, len(frames)))]
    ]
    if not root_positions:
        root_positions = [(0.0, 0.0)]
    chain: list[tuple[float, float]] = []
    for index, (root_x, root_y) in enumerate(root_positions):
        phase = index / max(len(root_positions) - 1, 1)
        chain.append((root_x * 0.08 + 0.02 * index, 0.45 + root_y * 0.08 - 0.09 * index - 0.04 * math.sin(phase * math.pi)))
    return {"cape_chain": chain}


def _choose_motion2d_gait(genotype: CharacterGenotype) -> str:
    if "monster" in genotype.archetype or "creature" in genotype.body_template:
        return "quadruped_trot"
    return "biped_walk"


def _bake_true_motion_guide_sequence(
    genotype_path: str | Path,
    clip_2d: Any,
    frame_count: int,
    render_width: int = 192,
    render_height: int = 192,
    *,
    motion_state: str = "idle",
    fps: int = 24,
    character_id: str = "",
) -> tuple[list[Image.Image], list[Image.Image], list[Image.Image], list[Image.Image]]:
    """Bake TRUE per-frame guide sequences from real bone-driven animation.

    SESSION-130: ERADICATE single-image replication forgery.
    SESSION-160: RenderContext Hydration — motion_state, fps, and character_id
    are now threaded through as explicit parameters (Temporal Context Wiring).
    This ensures the full upstream intent (action + timing) is available at
    every render call site, matching the DigitalRune RenderContext pattern
    where context is never implicitly assumed but always explicitly passed.
    SESSION-167: Composed Mesh Per-Frame Hydration — upstream _node_compose_mesh
    now preserves the full temporal vertex tensor [frames, V, 3] alongside the
    canonical static mesh.  The guide baking path continues to use the 2D SDF
    renderer (skeleton+pose), which was fixed in SESSION-166 via Bone→Joint
    namespace bridging, deg→rad conversion, and root displacement injection.

    This function replaces the SESSION-129 micro-jitter approach with genuine
    per-frame rendering driven by the Motion2DPipeline's Clip2D bone transforms.
    Each frame is rendered independently with the character's actual skeletal
    pose at that time step, producing guide sequences with real geometric
    variation that AnimateDiff / SparseCtrl temporal attention requires.

    Industrial References:
    - AnimateDiff / ControlNet Temporal Conditioning: guide frames MUST have
      real inter-frame geometric displacement for coherent motion generation.
    - GDC Data-Driven Animation Pipelines: upstream bone transforms must flow
      1:1 to downstream rendering without forgery or duplication.
    - Jim Gray Fail-Fast: if rendering fails for any frame, abort immediately.

    OOM Prevention:
    - Each frame is rendered and consumed independently (no bulk pre-allocation).
    - Intermediate numpy arrays are explicitly deleted after PIL conversion.
    - Chunked processing: frames are rendered in batches of CHUNK_SIZE to
      bound peak memory usage.

    Parameters
    ----------
    genotype_path : str | Path
        Path to the serialized CharacterGenotype JSON.
    clip_2d : Clip2D
        The 2D animation clip containing per-frame bone transforms.
    frame_count : int
        Number of frames to render.
    render_width : int
        Output frame width in pixels.
    render_height : int
        Output frame height in pixels.
    motion_state : str
        SESSION-160: The active motion state name (e.g. 'idle', 'run', 'jump').
        Threaded through as RenderContext for downstream traceability.
    fps : int
        SESSION-160: Target frames-per-second from upstream prepare_character.
    character_id : str
        SESSION-160: Character identifier for diagnostic logging.

    Returns
    -------
    tuple[list[Image.Image], list[Image.Image], list[Image.Image], list[Image.Image]]
        (source_frames, normal_maps, depth_maps, mask_maps) with genuine
        per-frame geometric variation.
    """
    from mathart.animation.genotype import CharacterGenotype
    from mathart.pipeline_contract import PipelineContractError

    # ── SESSION-160: RenderContext Hydration — log threaded parameters ──────
    import logging as _bake_ctx_log
    _bake_ctx_log.getLogger(__name__).debug(
        "[SESSION-160] RenderContext hydrated: motion_state=%s, fps=%d, "
        "character_id=%s, frame_count=%d, render=%dx%d",
        motion_state, fps, character_id, frame_count, render_width, render_height,
    )

    # ── Reconstruct character from serialized genotype ──────────────────────
    genotype_data = _load_json(Path(genotype_path))
    genotype = CharacterGenotype.from_dict(genotype_data)
    skeleton = genotype.build_shaped_skeleton()
    style = genotype.decode_to_style()

    # ── Build animation_func from Clip2D bone transforms ────────────────────
    # SESSION-166: Per-Frame State Hydration — CRITICAL FIX.
    #
    # ROOT CAUSE of frozen_guide_sequence (MSE=0.0000):
    # Clip2D uses BONE names (e.g. 'l_thigh', 'r_thigh') in bone_transforms,
    # but Skeleton.apply_pose() expects JOINT names (e.g. 'l_hip', 'l_knee').
    # The old code passed bone names directly → zero matches → skeleton stayed
    # in default pose for every frame → identical renders → MSE=0.
    #
    # Additionally, Clip2D stores rotations in DEGREES while the skeleton
    # system uses RADIANS.
    #
    # FIX: Build a bone_name→child_joint_name mapping from the skeleton's
    # bone definitions, convert degrees→radians, and inject root displacement
    # as explicit joint position offsets.
    #
    # Industrial References:
    # - Per-Frame State Hydration: each frame must extract the correct
    #   deformed state from the upstream data and force-update the render
    #   context (Vulkan multi-frame flight buffers, Blender Mesh Cache).
    # - DOD Pointer Staleness: the old code evaluated bone names once
    #   outside the joint namespace, creating a permanent stale reference.
    # - Fail-Loud: the VarianceAssertGate correctly caught this; we fix
    #   the implementation, NOT the test.
    clip_frames = clip_2d.frames if clip_2d and hasattr(clip_2d, "frames") else []
    n_clip = len(clip_frames)
    if n_clip == 0:
        raise PipelineContractError(
            "empty_clip_2d",
            "[_bake_true_motion_guide_sequence] Clip2D has zero frames.  "
            "Cannot construct animation_func for per-frame rendering.  "
            "The motion2d_export_stage must produce a non-empty Clip2D.",
        )

    # ── SESSION-166: Build bone→joint mapping for name translation ─────────
    # Skeleton bones connect joint_a (parent) → joint_b (child).
    # When Clip2D says bone 'l_thigh' has rotation R, it means the CHILD
    # joint of that bone (l_knee) should rotate by R.  But the parent joint
    # (l_hip) is the one that controls the bone's swing angle in FK.
    # So: bone_name → joint_a (the joint whose angle drives this bone).
    _bone_to_joint: dict[str, str] = {}
    for _bone in skeleton.bones:
        _bone_to_joint[_bone.name] = _bone.joint_a
    _bake_ctx_log.getLogger(__name__).debug(
        "[SESSION-166] Bone→Joint mapping: %s", _bone_to_joint,
    )

    def _animation_func_from_clip(t: float) -> dict[str, float]:
        """Map t in [0, 1] to a JOINT-keyed pose dict by sampling Clip2D frames.

        SESSION-166: Per-Frame State Hydration.
        - Translates Clip2D bone names → skeleton joint names.
        - Converts rotation from degrees → radians.
        - Interpolates root position for global displacement.
        """
        idx_f = t * max(n_clip - 1, 1)
        idx_lo = int(idx_f)
        idx_hi = min(idx_lo + 1, n_clip - 1)
        alpha = idx_f - idx_lo

        frame_lo = clip_frames[idx_lo]
        frame_hi = clip_frames[idx_hi]

        pose: dict[str, float] = {}
        # Merge all bone names from both frames
        all_bones = set(frame_lo.bone_transforms.keys()) | set(frame_hi.bone_transforms.keys())
        for bone_name in all_bones:
            xform_lo = frame_lo.bone_transforms.get(bone_name, {})
            xform_hi = frame_hi.bone_transforms.get(bone_name, {})
            # Interpolate rotation (primary animation channel)
            rot_lo_deg = float(xform_lo.get("rotation", 0.0))
            rot_hi_deg = float(xform_hi.get("rotation", 0.0))
            rot_deg = rot_lo_deg + alpha * (rot_hi_deg - rot_lo_deg)
            # SESSION-166: Convert degrees → radians for skeleton FK engine
            rot_rad = math.radians(rot_deg)

            # SESSION-166: Translate bone name → driving joint name.
            # If no mapping exists, try the bone name directly as a joint
            # name (graceful fallback for future skeleton topologies).
            joint_name = _bone_to_joint.get(bone_name, bone_name)
            pose[joint_name] = rot_rad

        # Interpolate root position for global displacement
        root_x = float(frame_lo.root_x) + alpha * (float(frame_hi.root_x) - float(frame_lo.root_x))
        root_y = float(frame_lo.root_y) + alpha * (float(frame_hi.root_y) - float(frame_lo.root_y))
        pose["root_x"] = root_x
        pose["root_y"] = root_y

        return pose

    # ── Per-frame rendering with OOM-safe chunking ──────────────────────────
    source_frames: list[Image.Image] = []
    normal_maps: list[Image.Image] = []
    depth_maps: list[Image.Image] = []
    mask_maps: list[Image.Image] = []

    n = max(2, frame_count)
    CHUNK_SIZE = 8  # OOM prevention: process frames in chunks

    # Try industrial renderer first, fall back to character_renderer
    use_industrial = True
    try:
        from mathart.animation.industrial_renderer import render_character_maps_industrial
    except ImportError:
        use_industrial = False

    for chunk_start in range(0, n, CHUNK_SIZE):
        chunk_end = min(chunk_start + CHUNK_SIZE, n)
        for i in range(chunk_start, chunk_end):
            t = i / max(n - 1, 1)
            pose = _animation_func_from_clip(t)

            # SESSION-166: Per-Frame State Hydration — extract root displacement
            # and apply it as a scale_x/scale_y offset to the renderer so the
            # character's global position shifts per frame.  The root_x/root_y
            # values are NOT joint angles and must NOT be passed to apply_pose.
            frame_root_x = pose.pop("root_x", 0.0)
            frame_root_y = pose.pop("root_y", 0.0)

            # SESSION-166: Compute per-frame scale offsets from root displacement.
            # This translates root motion into visible pixel displacement in the
            # rendered frame, ensuring inter-frame geometric variation.
            # Scale modulation: root_x shifts horizontal proportion, root_y shifts
            # vertical proportion — producing real pixel-level displacement.
            _root_scale_x = 1.0 + float(frame_root_x) * 0.08
            _root_scale_y = 1.0 + float(frame_root_y) * 0.08

            if use_industrial:
                try:
                    result = render_character_maps_industrial(
                        skeleton=skeleton,
                        pose=pose,
                        style=style,
                        width=render_width,
                        height=render_height,
                        scale_x=_root_scale_x,
                        scale_y=_root_scale_y,
                    )
                    source_frames.append(result.albedo_image)
                    normal_maps.append(result.normal_map_image)
                    depth_maps.append(result.depth_map_image)
                except Exception:
                    # If industrial renderer fails, fall back to basic rendering
                    use_industrial = False

            if not use_industrial:
                from mathart.animation.character_renderer import render_character_frame
                frame = render_character_frame(skeleton, pose, style, render_width, render_height)
                source_frames.append(frame)
                normal_maps.append(
                    Image.new("RGBA", (render_width, render_height), (128, 128, 255, 255))
                )
                depth_maps.append(
                    Image.new("RGBA", (render_width, render_height), (128, 128, 128, 255))
                )

            # Build mask from alpha channel
            frame_rgba = source_frames[-1].convert("RGBA")
            alpha = np.array(frame_rgba.getchannel("A"), dtype=np.uint8)
            if int(alpha.max()) == 0:
                rgb = np.array(frame_rgba.convert("RGB"), dtype=np.uint8)
                alpha = np.where(rgb.mean(axis=2) > 0, 255, 0).astype(np.uint8)
                del rgb  # OOM prevention
            mask_maps.append(Image.fromarray(alpha, mode="L"))
            del alpha, frame_rgba  # OOM prevention

    # SESSION-162: Fail-Fast Variance Assert
    from mathart.core.anti_flicker_runtime import assert_nonzero_temporal_variance
    try:
        assert_nonzero_temporal_variance(source_frames, channel="source")
    except RuntimeError as e:
        from mathart.pipeline_contract import PipelineContractError
        raise PipelineContractError("frozen_guide_sequence", str(e))
        
    return source_frames, normal_maps, depth_maps, mask_maps


# ---------------------------------------------------------------------------
# PDG node operations
# ---------------------------------------------------------------------------


def _node_seed_orders(ctx: dict[str, Any], _deps: dict[str, Any]) -> dict[str, Any]:
    batch_size = int(ctx.get("batch_size", 1))
    return {
        "batch_size": batch_size,
        "states": list(_get_registered_motion_states()),  # SESSION-160: Dynamic from registry
        "preset_names": [name for name, _ in _preset_factories()],
        "batch_dir": str(Path(ctx["batch_dir"]).resolve()),
        "skip_ai_render": bool(ctx.get("skip_ai_render", False)),
    }


def _node_fan_out_orders(_ctx: dict[str, Any], deps: dict[str, Any]) -> PDGFanOutResult:
    batch_size = int(deps["seed_orders"]["batch_size"])
    # ── SESSION-191: LookDev Deep Pruning — 角色变异体截断 ──────────────
    # 当 action_filter 存在时（LookDev 模式），强制将角色数量截断为 1，
    # 只保留 character_000，避免 20 个繁衍体的算力空转。
    _action_filter = _ctx.get("action_filter")
    _action_job_context = _normalize_action_job_context(_ctx)
    if _action_filter or _action_job_context:
        batch_size = 1
    payloads = []
    partition_keys = []
    labels = []
    attributes = []
    for index in range(batch_size):
        character_id = str((_action_job_context or {}).get("character_id") or f"character_{index:03d}")
        payloads.append({"order_index": index, "character_id": character_id})
        partition_keys.append(character_id)
        labels.append(character_id)
        attributes.append({"character_id": character_id, "order_index": index})
    return PDGFanOutResult.from_payloads(
        payloads,
        partition_keys=partition_keys,
        labels=labels,
        attributes=attributes,
    )


def _node_prepare_character(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    order = deps["fan_out_orders"]
    rng = ctx["_pdg"]["rng"]
    rng_contract = ctx["_pdg"]["rng_contract"]
    character_id = str(order["character_id"])
    character_dir = _ensure_dir(Path(ctx["batch_dir"]) / character_id)
    prep_dir = _ensure_dir(character_dir / "prep")

    preset_name, genotype = _mutate_genotype(rng)
    attachment_mesh, attachment_meta = _attachment_mesh_from_genotype(genotype)
    genotype_path = _write_json(prep_dir / f"{character_id}_genotype.json", genotype.to_dict())
    attachment_path = _save_mesh_npz(prep_dir / f"{character_id}_attachments.npz", attachment_mesh)

    # ── SESSION-191: LookDev Deep Pruning — 动作过滤器 ────────────────
    # 当 action_filter 存在时，强制使用用户指定的动作，而非随机选择。
    _action_filter = ctx.get("action_filter")
    _action_job_context = _normalize_action_job_context(ctx)
    if _action_job_context:
        state = str(_action_job_context.get("action") or _action_job_context.get("motion_state") or "motion")
    elif _action_filter and len(_action_filter) > 0:
        state = _action_filter[0]
    else:
        state = _state_from_rng(rng)
    frame_count = int((_action_job_context or {}).get("frame_count_target") or rng.integers(24, 49))
    fps = int((_action_job_context or {}).get("fps") or rng.choice(np.array([12, 15, 24], dtype=np.int64)))
    gait = _choose_motion2d_gait(genotype)

    prep_report = {
        "character_id": character_id,
        "preset_name": preset_name,
        "motion_state": state,
        "motion_gait": gait,
        "frame_count": frame_count,
        "fps": fps,
        "rng_contract": dict(rng_contract),
        "attachment_meta": attachment_meta,
        "action_job_context": _action_job_context,
    }
    prep_report_path = _write_json(prep_dir / f"{character_id}_prep_report.json", prep_report)
    return {
        "character_id": character_id,
        "character_dir": str(character_dir),
        "genotype_path": str(genotype_path),
        "attachment_mesh_path": str(attachment_path),
        "prep_report_path": str(prep_report_path),
        "preset_name": preset_name,
        "motion_state": state,
        "motion_gait": gait,
        "frame_count": frame_count,
        "fps": fps,
        "seed_spawn_digest": rng_contract.get("spawn_key_digest"),
        "action_job_context": _action_job_context,
    }


def _node_unified_motion(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "unified_motion")
    pipeline = AssetPipeline(output_dir=str(stage_dir), verbose=False)
    manifest = pipeline.run_backend(
        "unified_motion",
        {
            "output_dir": str(stage_dir),
            "name": prepared["character_id"],
            "state": prepared["motion_state"],
            "frame_count": prepared["frame_count"],
            "fps": prepared["fps"],
            "session_id": _SESSION_ID,
        },
    )
    # SESSION-128: Inject rng_spawn_digest into ArtifactManifest metadata
    rng_digest = _current_rng_digest(ctx)
    if rng_digest:
        manifest.metadata["rng_spawn_digest"] = rng_digest
    manifest_path = _save_manifest(manifest, stage_dir / "unified_motion_artifact_manifest.json")
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "manifest_path": str(manifest_path),
        "motion_clip_json": manifest.outputs.get("motion_clip_json"),
        "state": prepared["motion_state"],
        "frame_count": prepared["frame_count"],
        "fps": prepared["fps"],
        "rng_spawn_digest": rng_digest,
    }


def _node_vfx_weaver(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    motion = deps["unified_motion_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "vfx_weaver")

    director_spec = ctx.get("director_studio_spec") or {}
    active_plugins: list[str] = []
    if isinstance(director_spec, dict):
        active_plugins = [str(p) for p in (director_spec.get("active_vfx_plugins") or []) if str(p).strip()]
    if not active_plugins:
        vfx_artifacts = ctx.get("vfx_artifacts") or {}
        if isinstance(vfx_artifacts, dict):
            active_plugins = [str(p) for p in (vfx_artifacts.get("active_plugins") or []) if str(p).strip()]

    rng_digest = _current_rng_digest(ctx)
    if not active_plugins:
        report_path = _write_json(
            stage_dir / f"{prepared['character_id']}_vfx_weaver_skipped.json",
            {
                "character_id": prepared["character_id"],
                "skipped": True,
                "reason": "no_active_vfx_plugins",
                "upstream_unified_motion_manifest": motion["manifest_path"],
                "rng_spawn_digest": rng_digest,
            },
        )
        return {
            "character_id": prepared["character_id"],
            "character_dir": prepared["character_dir"],
            "skipped": True,
            "active_plugins": [],
            "executed": [],
            "failed": [],
            "report_path": str(report_path),
            "plugin_manifests": {},
            "rng_spawn_digest": rng_digest,
        }

    from mathart.animation.unified_motion import UnifiedMotionClip
    from mathart.workspace.pipeline_weaver import weave_vfx_pipeline

    unified_manifest = _load_manifest(motion["manifest_path"])
    motion_clip_path = unified_manifest.outputs.get("motion_clip_json") or motion.get("motion_clip_json")
    motion_clip = UnifiedMotionClip.from_dict(_load_json(motion_clip_path)) if motion_clip_path else None

    weaver_result = weave_vfx_pipeline(
        active_plugins=active_plugins,
        output_dir=stage_dir,
        extra_context={
            "vibe": str(ctx.get("vibe", "") or ""),
            "director_studio_spec": director_spec if isinstance(director_spec, dict) else None,
            "unified_motion_manifest": unified_manifest,
            "motion_clip_path": motion_clip_path,
            "motion_clip": motion_clip,
            "state": prepared["motion_state"],
            "name": prepared["character_id"],
            "fps": int(prepared["fps"]),
            "num_frames": int(prepared["frame_count"]),
            "frame_count": int(prepared["frame_count"]),
            "seed": int(ctx.get("seed", _DEFAULT_SEED)),
        },
    )

    plugin_manifests: dict[str, str] = {}
    plugin_records: list[dict[str, Any]] = []
    for record in weaver_result.plugin_records:
        record_payload = record.to_dict()
        manifest = record.artifact_manifest
        if manifest is not None and hasattr(manifest, "save"):
            manifest_path = stage_dir / f"{_slug(record.plugin_name)}_artifact_manifest.json"
            manifest.save(manifest_path)
            plugin_manifests[record.plugin_name] = str(manifest_path.resolve())
            record_payload["manifest_path"] = str(manifest_path.resolve())
            if hasattr(manifest, "outputs"):
                record_payload["outputs"] = dict(getattr(manifest, "outputs") or {})
        plugin_records.append(record_payload)

    report_path = _write_json(
        stage_dir / f"{prepared['character_id']}_vfx_weaver_report.json",
        {
            "character_id": prepared["character_id"],
            "skipped": False,
            "active_plugins": active_plugins,
            "upstream_unified_motion_manifest": motion["manifest_path"],
            "upstream_motion_clip_json": motion_clip_path,
            "weaver_result": weaver_result.to_dict(),
            "plugin_records": plugin_records,
            "plugin_manifests": plugin_manifests,
            "rng_spawn_digest": rng_digest,
        },
    )
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "skipped": False,
        "active_plugins": active_plugins,
        "executed": list(weaver_result.executed),
        "failed": list(weaver_result.skipped),
        "errors": dict(weaver_result.errors),
        "report_path": str(report_path),
        "plugin_manifests": plugin_manifests,
        "rng_spawn_digest": rng_digest,
    }


def _node_pseudo3d_shell(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    motion = deps["unified_motion_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "pseudo3d_shell")
    pipeline = AssetPipeline(output_dir=str(stage_dir), verbose=False)
    bone_dqs = _build_demo_bone_dqs(motion["motion_clip_json"])
    manifest = pipeline.run_backend(
        "pseudo_3d_shell",
        {
            "output_dir": str(stage_dir),
            "name": prepared["character_id"],
            "bone_dqs": bone_dqs,
            "session_id": _SESSION_ID,
            "export_per_frame": False,
        },
    )
    # SESSION-128: Inject rng_spawn_digest into ArtifactManifest metadata
    rng_digest = _current_rng_digest(ctx)
    if rng_digest:
        manifest.metadata["rng_spawn_digest"] = rng_digest
    manifest_path = _save_manifest(manifest, stage_dir / "pseudo_3d_shell_artifact_manifest.json")
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "manifest_path": str(manifest_path),
        "mesh_path": manifest.outputs.get("mesh"),
        "metadata_path": manifest.outputs.get("metadata"),
        "frame_count": int(manifest.metadata.get("frame_count", prepared["frame_count"])),
        "rng_spawn_digest": rng_digest,
    }


def _node_physical_ribbon(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    motion = deps["unified_motion_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "physical_ribbon")
    pipeline = AssetPipeline(output_dir=str(stage_dir), verbose=False)
    chain_points = _build_chain_points_from_motion(motion["motion_clip_json"])
    manifest = pipeline.run_backend(
        "physical_ribbon",
        {
            "output_dir": str(stage_dir),
            "name": prepared["character_id"],
            "chain_points": chain_points,
            "session_id": _SESSION_ID,
        },
    )
    # SESSION-128: Inject rng_spawn_digest into ArtifactManifest metadata
    rng_digest = _current_rng_digest(ctx)
    if rng_digest:
        manifest.metadata["rng_spawn_digest"] = rng_digest
    manifest_path = _save_manifest(manifest, stage_dir / "physical_ribbon_artifact_manifest.json")
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "manifest_path": str(manifest_path),
        "mesh_path": manifest.outputs.get("mesh"),
        "report_path": manifest.outputs.get("report"),
        "rng_spawn_digest": rng_digest,
    }


def _node_compose_mesh(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    """SESSION-167: Per-Frame Composed Mesh Hydration.

    Temporal Mesh Composition & Hydration — when the upstream pseudo3d_shell
    produces multi-frame deformed vertex arrays ``[frames, V, 3]``, this node
    now preserves the full temporal vertex data and composes per-frame meshes.
    The last frame is used as the canonical static composed mesh for the
    orthographic render stage, while the full temporal data is persisted
    alongside for any downstream consumer that needs per-frame geometry.

    SESSION-165/166 Root Cause Context:
    Previously, only ``shell_mesh["vertices"][-1]`` (the last frame) was
    extracted, discarding all intermediate deformation frames.  While the
    guide_baking_stage uses a separate 2D SDF render path (skeleton+pose),
    the orthographic_render_stage consumed this static-only composed mesh,
    which meant the 3D pipeline path had no access to temporal vertex data.

    Industrial References:
    - Per-Frame Slice Hydration: each frame's deformed vertices must be
      individually addressable in the composed mesh tensor.
    - In-Place Memory Safety: temporal vertex arrays are stored as a single
      contiguous ``[frames, V, 3]`` tensor, not per-frame object copies.
    - NVIDIA GPU Gems 3 Ch.2: per-instance data must be updated per frame.
    """
    import logging as _compose_log

    prepared = deps["prepare_character"]
    shell = deps["pseudo3d_shell_stage"]
    ribbon = deps["physical_ribbon_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "composed_mesh")
    attachment_mesh = _mesh_dict_from_npz(prepared["attachment_mesh_path"])
    shell_mesh = _mesh_dict_from_npz(shell["mesh_path"])
    ribbon_mesh = _mesh_dict_from_npz(ribbon["mesh_path"])

    # SESSION-167: Detect multi-frame temporal vertex data from shell.
    # If shell_mesh has shape [frames, V, 3], preserve the full temporal
    # tensor and use the last frame for the canonical static composition.
    has_temporal_shell = shell_mesh["vertices"].ndim == 3
    temporal_frame_count = 0
    temporal_composed_path = None

    if has_temporal_shell:
        temporal_frame_count = int(shell_mesh["vertices"].shape[0])
        shell_v_count = int(shell_mesh["vertices"].shape[1])
        _compose_log.getLogger(__name__).info(
            "[SESSION-167] Temporal shell detected: %d vertices x %d frames. "
            "Composing per-frame mesh hydration tensor.",
            shell_v_count, temporal_frame_count,
        )

        # Build per-frame composed vertex arrays (in-place tensor, no OOM).
        # Attachment and ribbon are static; only shell deforms per frame.
        att_verts = np.asarray(attachment_mesh["vertices"], dtype=np.float64)
        rib_verts = np.asarray(ribbon_mesh["vertices"], dtype=np.float64)
        static_suffix = np.concatenate([att_verts, rib_verts], axis=0)

        # Compose temporal tensor: [frames, V_shell + V_static, 3]
        temporal_composed_verts = np.zeros(
            (temporal_frame_count, shell_v_count + static_suffix.shape[0], 3),
            dtype=np.float64,
        )
        for fi in range(temporal_frame_count):
            # SESSION-167: Per-Frame Slice Hydration — extract frame fi's
            # deformed shell vertices and concatenate with static components.
            # This is the CRITICAL in-place hydration that ensures each frame
            # of the composed mesh has genuinely different vertex positions.
            temporal_composed_verts[fi, :shell_v_count, :] = shell_mesh["vertices"][fi]
            temporal_composed_verts[fi, shell_v_count:, :] = static_suffix

        # Persist temporal composed mesh as a separate artifact
        temporal_composed_path = stage_dir / f"{prepared['character_id']}_temporal_composed_mesh.npz"
        np.savez_compressed(
            str(temporal_composed_path),
            temporal_vertices=temporal_composed_verts,
            frame_count=np.array([temporal_frame_count]),
            shell_vertex_count=np.array([shell_v_count]),
        )
        del temporal_composed_verts  # OOM prevention

        # Extract last frame for canonical static composition
        shell_mesh = {
            "vertices": shell_mesh["vertices"][-1],
            "normals": shell_mesh["normals"][-1],
            "triangles": shell_mesh["triangles"],
            "colors": shell_mesh["colors"],
        }
    else:
        _compose_log.getLogger(__name__).debug(
            "[SESSION-167] Static shell mesh (no temporal data). "
            "Standard single-frame composition.",
        )

    composed = _merge_meshes([shell_mesh, attachment_mesh, ribbon_mesh])
    composed_path = _save_mesh_npz(stage_dir / f"{prepared['character_id']}_composed_mesh.npz", composed)
    report_path = _write_json(
        stage_dir / f"{prepared['character_id']}_composition_report.json",
        {
            "character_id": prepared["character_id"],
            "vertex_count": int(composed["vertices"].shape[0]),
            "triangle_count": int(composed["triangles"].shape[0]),
            "has_temporal_data": has_temporal_shell,
            "temporal_frame_count": temporal_frame_count,
            "sources": {
                "shell_mesh_path": shell["mesh_path"],
                "attachment_mesh_path": prepared["attachment_mesh_path"],
                "ribbon_mesh_path": ribbon["mesh_path"],
            },
        },
    )
    result = {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "mesh_path": str(composed_path),
        "report_path": str(report_path),
        "has_temporal_data": has_temporal_shell,
        "temporal_frame_count": temporal_frame_count,
        "rng_spawn_digest": _current_rng_digest(ctx),
    }
    if temporal_composed_path is not None:
        result["temporal_mesh_path"] = str(temporal_composed_path)
    return result


def _node_orthographic_render(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    composed = deps["compose_mesh_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "orthographic_pixel_render")
    archive_dir = _ensure_dir(character_dir / "archive")
    asset_spec = _build_asset_production_spec(ctx, prepared)
    mesh = _mesh_dict_from_npz(composed["mesh_path"])
    pipeline = AssetPipeline(output_dir=str(stage_dir), verbose=False)
    manifest = pipeline.run_backend(
        "orthographic_pixel_render",
        {
            "output_dir": str(stage_dir),
            "name": prepared["character_id"],
            "mesh": _mesh3d_from_dict(mesh),
            "mesh_data": mesh,
            "render": {
                "render_width": 384,
                "render_height": 384,
                "output_width": 192,
                "output_height": 192,
                "fps": prepared["fps"],
            },
            "session_id": _SESSION_ID,
        },
    )
    # SESSION-128: Inject rng_spawn_digest into ArtifactManifest metadata
    # for Bazel-level hash auditability across the entire artifact chain.
    rng_digest = _current_rng_digest(ctx)
    if rng_digest:
        manifest.metadata["rng_spawn_digest"] = rng_digest
    manifest_path = _save_manifest(manifest, stage_dir / "orthographic_pixel_render_artifact_manifest.json")
    archived = _archive_manifest_outputs(manifest, archive_dir, "orthographic_pixel_render")
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "manifest_path": str(manifest_path),
        "albedo": manifest.outputs.get("albedo") or manifest.outputs.get("spritesheet"),
        "normal": manifest.outputs.get("normal"),
        "depth": manifest.outputs.get("depth"),
        "report": manifest.outputs.get("render_report"),
        "archived": archived,
        "asset_factory_intake_spec": asset_spec.get("asset_factory_intake_spec"),
        "asset_target_pack_plan": asset_spec.get("asset_target_pack_plan"),
        "rng_spawn_digest": rng_digest,
    }


def _node_motion2d_export(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "motion2d")
    gait = prepared["motion_gait"]
    frame_count = int(prepared["frame_count"])
    motion_pipeline = Motion2DPipeline()
    if gait == "quadruped_trot":
        result = motion_pipeline.run_quadruped_trot(n_frames=frame_count, speed=1.0)
    else:
        result = motion_pipeline.run_biped_walk(n_frames=frame_count, speed=1.0)
    spine_json_path = stage_dir / f"{prepared['character_id']}_spine.json"
    motion_pipeline.export_spine_json(result, spine_json_path)
    # SESSION-128: Capture rng_spawn_digest for motion2d stage
    rng_digest = _current_rng_digest(ctx)
    report_path = _write_json(
        stage_dir / f"{prepared['character_id']}_motion2d_report.json",
        {
            "character_id": prepared["character_id"],
            "gait": gait,
            "total_frames": int(result.total_frames),
            "pipeline_pass": bool(result.pipeline_pass),
            "fps": int(getattr(result.clip_2d, "fps", prepared["fps"])),
            "rng_spawn_digest": rng_digest,
        },
    )
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "spine_json_path": str(spine_json_path.resolve()),
        "clip_2d": result.clip_2d,
        "report_path": str(report_path.resolve()),
        "fps": int(getattr(result.clip_2d, "fps", prepared["fps"])),
        "frame_count": int(result.total_frames),
        "rng_spawn_digest": rng_digest,
    }


def _node_final_delivery(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    motion2d = deps["motion2d_export_stage"]
    character_dir = Path(prepared["character_dir"])
    unity_dir = _ensure_dir(character_dir / "unity_2d_anim")
    preview_dir = _ensure_dir(character_dir / "spine_preview")
    archive_dir = _ensure_dir(character_dir / "archive")
    pipeline_unity = AssetPipeline(output_dir=str(unity_dir), verbose=False)
    pipeline_preview = AssetPipeline(output_dir=str(preview_dir), verbose=False)

    unity_manifest = pipeline_unity.run_backend(
        "unity_2d_anim",
        {
            "output_dir": str(unity_dir),
            "name": prepared["character_id"],
            "clip_2d": motion2d["clip_2d"],
            "session_id": _SESSION_ID,
        },
    )
    preview_manifest = pipeline_preview.run_backend(
        "spine_preview",
        {
            "output_dir": str(preview_dir),
            "name": prepared["character_id"],
            "spine_json_path": motion2d["spine_json_path"],
            "fps": motion2d["fps"],
            "session_id": _SESSION_ID,
        },
    )

    # SESSION-128: Inject rng_spawn_digest into delivery manifests
    rng_digest = _current_rng_digest(ctx)
    if rng_digest:
        unity_manifest.metadata["rng_spawn_digest"] = rng_digest
        preview_manifest.metadata["rng_spawn_digest"] = rng_digest
    unity_manifest_path = _save_manifest(unity_manifest, unity_dir / "unity_2d_anim_artifact_manifest.json")
    preview_manifest_path = _save_manifest(preview_manifest, preview_dir / "spine_preview_artifact_manifest.json")
    archived = {
        "unity_2d_anim": _archive_manifest_outputs(unity_manifest, archive_dir, "unity_2d_anim"),
        "spine_preview": _archive_manifest_outputs(preview_manifest, archive_dir, "spine_preview"),
    }
    shutil.copy2(motion2d["spine_json_path"], archive_dir / Path(motion2d["spine_json_path"]).name)
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "unity_manifest_path": str(unity_manifest_path),
        "preview_manifest_path": str(preview_manifest_path),
        "archived": archived,
        "spine_json_path": motion2d["spine_json_path"],
        "rng_spawn_digest": rng_digest,
    }


def _node_guide_baking(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    """SESSION-158: Independent CPU-bound guide baking stage.

    Pipeline Decoupling — this node is the **rescued industrial bake engine**
    that was previously trapped inside ``_node_ai_render`` behind the
    ``--skip-ai-render`` conditional lock.  It now runs as a first-class,
    GPU-independent CPU stage that ALWAYS executes, producing high-precision
    Catmull-Rom interpolated guide sequences (Albedo/Normal/Depth/Mask)
    regardless of whether downstream AI rendering is enabled.

    Architecture Discipline:
    - Pure CPU: uses only PIL/OpenCV/NumPy — zero GPU, zero LLM, zero ComfyUI.
    - IR Hydration: guide sequences are persisted as first-class assets on disk
      before any AI render stage consumes them.
    - Separation of Concerns: math/bake logic is fully decoupled from
      GPU/AI render logic.

    Industrial References:
    - AnimateDiff / ControlNet Temporal Conditioning: guide frames MUST have
      real inter-frame geometric displacement for coherent motion generation.
    - GDC Data-Driven Animation Pipelines: upstream bone transforms must flow
      1:1 to downstream rendering without forgery or duplication.
    """
    import sys
    import logging as _bake_logging
    from mathart.core.anti_flicker_runtime import validate_temporal_variance

    prepared = deps["prepare_character"]
    render = deps["orthographic_render_stage"]
    motion2d = deps["motion2d_export_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "guide_baking")
    archive_dir = _ensure_dir(character_dir / "archive")

    # ── UX: Sci-fi gateway banner (SESSION-179: Industrial Baking Gateway) ─
    _ux_msg = (
        f"\033[1;36m[\u2699\ufe0f  \u5de5\u4e1a\u70d8\u7119\u7f51\u5173] "
        f"\u6b63\u5728\u901a\u8fc7 Catmull-Rom \u6837\u6761\u63d2\u503c\uff0c"
        f"\u7eaf CPU \u89e3\u7b97\u9ad8\u7cbe\u5ea6\u5de5\u4e1a\u7ea7\u8d34\u56fe"
        f"\u52a8\u4f5c\u5e8f\u5217... "
        f"[{prepared['character_id']}]\033[0m\n"
        f"\033[1;35m    \u251c\u2500 SESSION-166 Per-Frame State Hydration: "
        f"Bone\u2192Joint \u6620\u5c04\u5df2\u6fc0\u6d3b\uff0c"
        f"\u9010\u5e27\u53d8\u5f62\u9876\u70b9\u5b9e\u65f6\u6ce8\u5165\u5149\u6805\u5316\u5668\033[0m\n"
        f"\033[1;35m    \u251c\u2500 SESSION-169 Exception Piercing: "
        f"\u81f4\u547d\u5f02\u5e38\u5df2\u542f\u7528\u7a7f\u900f\u6a21\u5f0f\uff0c"
        f"GPU \u5d29\u6e83\u5c06\u81ea\u52a8\u64a4\u9500\u5269\u4f59\u5e76\u53d1\u4efb\u52a1\033[0m\n"
        f"\033[1;35m    \u251c\u2500 SESSION-172 JIT Resolution Hydration: "
        f"\u63a8\u6d41\u524d\u7f6e 512 \u5185\u5b58\u4e0a\u91c7\u6837\u5df2\u6fc0\u6d3b\uff0c"
        f"\u82f1\u6587\u63d0\u793a\u8bcd\u91cd\u7532\u5df2\u88c5\u8f7d\033[0m\n"
        f"\033[1;35m    \u251c\u2500 SESSION-179 SparseCtrl Time-Window Clamping: "
        f"end_percent 0.4~0.6 \u9650\u5e45\u5df2\u6fc0\u6d3b\uff0c\u957f\u955c\u5934\u95ea\u70c1\u5df2\u6839\u6cbb\033[0m\n"
        f"\033[1;35m    \u251c\u2500 SESSION-179 cancel_futures Global Meltdown: "
        f"OOM \u5168\u5c40\u7194\u65ad\u5df2\u5347\u7ea7\uff0cexecutor.shutdown(cancel_futures=True)\033[0m\n"
        f"\033[1;35m    \u2514\u2500 SESSION-177 State Vault Consolidation: "
        f"\u8fdb\u5316\u72b6\u6001\u91d1\u5e93\u5df2\u5efa\u7acb\uff0c"
        f"\u53cc\u8f68\u77e5\u8bc6\u603b\u7ebf\u5df2\u5e76\u8f68 (Markdown+JSON \u5408\u6d41)\033[0m"
    )
    sys.stderr.write(_ux_msg + "\n")
    sys.stderr.flush()

    # ── Resolve render dimensions from upstream orthographic manifest ──────
    render_manifest = _load_manifest(Path(render["manifest_path"]))
    render_meta = render_manifest.metadata or {}
    render_width = int(render_meta.get("output_width", render_meta.get("width", 192)))
    render_height = int(render_meta.get("output_height", render_meta.get("height", 192)))

    # ── Bake TRUE per-frame guide sequences from real Clip2D animation ─────
    clip_2d = motion2d.get("clip_2d")
    frame_count = int(prepared["frame_count"])
    # SESSION-160: Thread full RenderContext (motion_state, fps, character_id)
    # through to the bake function — Temporal Context Wiring.
    source_frames, normal_maps, depth_maps, mask_maps = _bake_true_motion_guide_sequence(
        genotype_path=prepared["genotype_path"],
        clip_2d=clip_2d,
        frame_count=frame_count,
        render_width=render_width,
        render_height=render_height,
        motion_state=prepared["motion_state"],
        fps=int(prepared["fps"]),
        character_id=prepared["character_id"],
    )

    # ── Temporal Variance Diagnostic (non-fatal in bake stage) ─────────────
    # SESSION-158: The Circuit Breaker is now a diagnostic in the bake stage
    # and a hard gate only in the AI render stage.  This ensures baked assets
    # are ALWAYS persisted as first-class IR, even when temporal variance is
    # low (e.g. idle/standing poses).  The hard fail-fast guard remains in
    # _node_ai_render to protect AnimateDiff from static conditioning.
    temporal_variance_passed = True
    try:
        validate_temporal_variance(source_frames, channel="source", mse_threshold=1.0)
    except Exception as tv_exc:
        temporal_variance_passed = False
        _bake_logging.getLogger(__name__).info(
            "[SESSION-158] Guide baking temporal variance diagnostic: %s "
            "(non-fatal — baked assets will still be persisted as IR)",
            tv_exc,
        )

    # ── Persist guide sequences as first-class assets (IR Hydration) ───────
    albedo_dir = _ensure_dir(stage_dir / "albedo")
    normal_dir = _ensure_dir(stage_dir / "normal")
    depth_dir = _ensure_dir(stage_dir / "depth")
    mask_dir = _ensure_dir(stage_dir / "mask")

    albedo_paths: list[str] = []
    normal_paths: list[str] = []
    depth_paths: list[str] = []
    mask_paths: list[str] = []

    for fi in range(len(source_frames)):
        fname = f"frame_{fi:04d}.png"
        ap = albedo_dir / fname
        source_frames[fi].save(str(ap))
        albedo_paths.append(str(ap.resolve()))

        np_ = normal_dir / fname
        normal_maps[fi].save(str(np_))
        normal_paths.append(str(np_.resolve()))

        dp = depth_dir / fname
        depth_maps[fi].save(str(dp))
        depth_paths.append(str(dp.resolve()))

        mp = mask_dir / fname
        mask_maps[fi].save(str(mp))
        mask_paths.append(str(mp.resolve()))

    guide_width = source_frames[0].size[0] if source_frames else render_width
    guide_height = source_frames[0].size[1] if source_frames else render_height

    # ── Write baking report ────────────────────────────────────────────────
    baking_report = {
        "character_id": prepared["character_id"],
        "session_id": _SESSION_ID,
        "stage": "guide_baking",
        "frame_count": len(source_frames),
        "render_width": render_width,
        "render_height": render_height,
        "guide_width": guide_width,
        "guide_height": guide_height,
        "channels": ["albedo", "normal", "depth", "mask"],
        "albedo_dir": str(albedo_dir.resolve()),
        "normal_dir": str(normal_dir.resolve()),
        "depth_dir": str(depth_dir.resolve()),
        "mask_dir": str(mask_dir.resolve()),
        "motion_state": prepared["motion_state"],  # SESSION-160: RenderContext
        "fps": int(prepared["fps"]),  # SESSION-160: RenderContext
        "cpu_only": True,
        "gpu_required": False,
        "temporal_variance_passed": temporal_variance_passed,
        "renderer": "render_character_maps_industrial (Catmull-Rom interpolated)",
    }
    report_path = _write_json(
        stage_dir / f"{prepared['character_id']}_guide_baking_report.json",
        baking_report,
    )

    # ── Archive baked guides ───────────────────────────────────────────────
    archive_bake_dir = _ensure_dir(archive_dir / "guide_baking")
    for sub in ["albedo", "normal", "depth", "mask"]:
        src_sub = stage_dir / sub
        dst_sub = archive_bake_dir / sub
        if dst_sub.exists():
            shutil.rmtree(dst_sub)
        shutil.copytree(str(src_sub), str(dst_sub))
    shutil.copy2(str(report_path), str(archive_bake_dir / report_path.name))

    rng_digest = _current_rng_digest(ctx)

    # SESSION-167: Enhanced completion banner with MSE diagnostic + mesh hydration
    _mse_status = "\u2705 MSE\u8d28\u68c0\u901a\u8fc7" if temporal_variance_passed else "\u26a0\ufe0f MSE\u8d28\u68c0\u8b66\u544a(\u975e\u81f4\u547d)"
    sys.stderr.write(
        f"\033[1;32m[\u2705 \u5de5\u4e1a\u70d8\u7119\u5b8c\u6210] "
        f"{len(source_frames)} \u5e27\u9ad8\u7cbe\u5ea6\u5f15\u5bfc\u56fe"
        f"\u5e8f\u5217\u5df2\u843d\u76d8 \u2192 {stage_dir}\033[0m\n"
        f"\033[1;32m    \u251c\u2500 {_mse_status} | "
        f"\u6e32\u67d3\u5668: Industrial SDF + Dead Cells Pipeline | "
        f"\u52a8\u4f5c: {prepared['motion_state']}\033[0m\n"
        f"\033[1;32m    \u2514\u2500 SESSION-167: \u7ec4\u5408\u7f51\u683c\u9010\u5e27\u6c34\u5408\u5df2\u8d2f\u901a "
        f"| Bone\u2192Joint \u6620\u5c04 + deg\u2192rad \u8f6c\u6362 + \u6839\u4f4d\u79fb\u6ce8\u5165\033[0m\n"
    )
    sys.stderr.flush()

    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "report_path": str(report_path.resolve()),
        "frame_count": len(source_frames),
        "guide_width": guide_width,
        "guide_height": guide_height,
        "albedo_dir": str(albedo_dir.resolve()),
        "normal_dir": str(normal_dir.resolve()),
        "depth_dir": str(depth_dir.resolve()),
        "mask_dir": str(mask_dir.resolve()),
        "albedo_paths": albedo_paths,
        "normal_paths": normal_paths,
        "depth_paths": depth_paths,
        "mask_paths": mask_paths,
        "source_frames": source_frames,
        "normal_maps": normal_maps,
        "depth_maps": depth_maps,
        "mask_maps": mask_maps,
        "motion_state": prepared["motion_state"],  # SESSION-160: Context propagation
        "fps": int(prepared["fps"]),  # SESSION-160: Context propagation
        "rng_spawn_digest": rng_digest,
    }


def _normalize_action_job_context(ctx: dict[str, Any]) -> dict[str, Any] | None:
    raw = ctx.get("action_job_context")
    if not isinstance(raw, dict) or not raw:
        return None
    action = str(raw.get("action") or raw.get("motion_state") or "").strip()
    if not action:
        return None
    parent_character_id = str(raw.get("parent_character_id") or raw.get("character_id") or "").strip()
    character_id = str(raw.get("character_id") or (f"{parent_character_id}_{action}" if parent_character_id else "")).strip()
    return {
        "spec_type": "ActionJobContext",
        "action": action,
        "motion_state": action,
        "parent_character_id": parent_character_id or None,
        "character_id": character_id or None,
        "reference_character": raw.get("reference_character") or raw.get("visual_reference_path"),
        "motion_reference": raw.get("motion_reference") or raw.get("motion_reference_path"),
        "knowledge_profile": raw.get("knowledge_profile"),
        "export_target": raw.get("export_target"),
        "target_pack": raw.get("target_pack") or [action],
        "frame_count_target": raw.get("frame_count_target") or raw.get("requested_frame_count"),
        "fps": raw.get("fps") or raw.get("requested_fps"),
        "merge_into_pack_manifest": raw.get("merge_into_pack_manifest"),
        "source_ticket_id": raw.get("source_ticket_id"),
    }


def _build_asset_factory_intake_spec(ctx: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    ds_spec = dict(ctx.get("director_studio_spec") or {})
    action_job_context = _normalize_action_job_context(ctx)
    reference_character = (
        (action_job_context or {}).get("reference_character")
        or ds_spec.get("_visual_reference_path")
        or ds_spec.get("visual_reference_path")
        or ctx.get("reference_character")
        or ctx.get("visual_reference_path")
    )
    motion_reference = (
        (action_job_context or {}).get("motion_reference")
        or ctx.get("motion_reference")
        or ctx.get("motion_reference_path")
        or ds_spec.get("motion_reference")
        or ds_spec.get("motion_reference_path")
    )
    action = str((action_job_context or {}).get("action") or ds_spec.get("action_name") or prepared.get("motion_state", "motion") or "motion")
    target_pack = (action_job_context or {}).get("target_pack") or ctx.get("target_pack") or ds_spec.get("target_pack") or ctx.get("action_filter") or [action]
    if isinstance(target_pack, str):
        target_pack = [target_pack]
    target_pack = [str(item) for item in target_pack if str(item).strip()]
    knowledge_profile = (
        (action_job_context or {}).get("knowledge_profile")
        or ctx.get("knowledge_profile")
        or ds_spec.get("knowledge_profile")
        or ctx.get("book_profile")
        or "project_runtime_distillation_bus"
    )
    export_target = (action_job_context or {}).get("export_target") or ctx.get("export_target") or ds_spec.get("export_target") or "unity_2d_sprite"
    explicit_plugins = [str(p) for p in (ds_spec.get("active_vfx_plugins") or []) if str(p).strip()]
    vfx_artifacts = ctx.get("vfx_artifacts") or {}
    if isinstance(vfx_artifacts, dict):
        explicit_plugins.extend(str(p) for p in (vfx_artifacts.get("active_plugins") or []) if str(p).strip())
    semantic_resolution = _resolve_semantic_asset_plugins(
        str(ctx.get("vibe") or ds_spec.get("raw_vibe") or ds_spec.get("vibe") or ""),
        explicit_plugins,
    )
    return {
        "spec_type": "AssetFactoryIntakeSpec",
        "reference_character": str(reference_character) if reference_character else None,
        "motion_reference": str(motion_reference) if motion_reference else None,
        "knowledge_profile": str(knowledge_profile),
        "target_pack": target_pack or [action],
        "export_target": str(export_target),
        "requested_action": action,
        "character_id": str(prepared.get("character_id", "")),
        "action_job_context": action_job_context,
        "semantic_asset_resolution": semantic_resolution,
        "active_existing_backends": semantic_resolution.get("selected_existing_backends", []),
        "input_policy": {
            "reference_character_role": "identity_source" if reference_character else "optional",
            "motion_reference_role": "motion_grammar_source" if motion_reference else "generated_math_motion",
            "knowledge_role": "runtime_constraints_and_quality_audit",
            "ai_role": "style_only_controlled_renderer",
        },
    }


def _build_asset_production_spec(ctx: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    sprite_mode = bool(ctx.get("sprite_asset_mode", True))
    intake_spec = _build_asset_factory_intake_spec(ctx, prepared)
    cell_size = max(16, int(ctx.get("sprite_cell_size", 64)))
    if not sprite_mode:
        return {
            "asset_family": "cinematic_sequence",
            "output_format": "frame_sequence_mp4",
            "asset_factory_intake_spec": intake_spec,
            "unity_import_contract": _unity_import_contract("cinematic_sequence", fps=int(prepared.get("fps", 12)), loop=False),
            "ai_policy": {"role": "controlled_visual_polish"},
            "ai_polish_policy": _ai_polish_fidelity_policy("cinematic_sequence", ai_required=True),
        }
    spec = {
        "asset_family": "character_sprite",
        "output_format": "sprite_sheet",
        "asset_factory_intake_spec": intake_spec,
        "unity_import_contract": _unity_import_contract("character_sprite", fps=int(prepared.get("fps", 12)), ppu=32, loop=True),
        "view": "side",
        "cell_size": [cell_size, cell_size],
        "frame_count_target": 16,
        "fps": int(prepared.get("fps", 12)),
        "pivot": "bottom_center",
        "background": str(ctx.get("sprite_background", "transparent_and_black")),
        "motion": {
            "action": str(prepared.get("motion_state", "motion")),
            "timing": "kan_kyu",
            "loop": True,
            "shape_authority": "math_engine",
        },
        "ai_policy": {
            "role": "controlled_visual_polish",
            "allow_background": False,
            "allow_shape_change": False,
            "identity_reference_without_user_ref": "sprite_mask_silhouette",
        },
        "ai_polish_policy": _ai_polish_fidelity_policy("character_sprite", ai_required=True),
        "quality_gates": {
            "pivot": "bottom_center",
            "max_pivot_drift_px": 2,
            "max_bbox_jitter_ratio": 0.08,
            "max_area_jitter_ratio": 0.18,
            "max_loop_bbox_delta_ratio": 0.16,
            "max_foot_slide_px": 12.0,
            "loop_closure_required": True,
        },
    }
    spec = _apply_distilled_production_rules(ctx, spec)
    spec = _apply_feedback_to_asset_spec(spec, _load_asset_factory_feedback(ctx))
    spec["asset_target_pack_plan"] = _build_asset_target_pack_plan(intake_spec, prepared, spec)
    spec["multi_action_production_plan"] = _build_multi_action_production_plan(prepared, spec)
    return spec


def _build_asset_target_pack_plan(intake_spec: dict[str, Any], prepared: dict[str, Any], asset_spec: dict[str, Any]) -> dict[str, Any]:
    target_pack = intake_spec.get("target_pack") or [prepared.get("motion_state", "motion")]
    current_action = str(intake_spec.get("requested_action") or prepared.get("motion_state", "motion") or "motion")
    fps = int(asset_spec.get("fps", prepared.get("fps", 12)) or 12)
    default_frames = int(asset_spec.get("frame_count_target", 16) or 16)
    action_defaults: dict[str, dict[str, Any]] = {
        "idle": {"frame_count_target": 8, "loop": True, "motion_class": "looping_idle"},
        "walk": {"frame_count_target": 12, "loop": True, "motion_class": "locomotion"},
        "run": {"frame_count_target": 16, "loop": True, "motion_class": "locomotion"},
        "jump": {"frame_count_target": 10, "loop": False, "motion_class": "airborne"},
        "fall": {"frame_count_target": 8, "loop": False, "motion_class": "airborne"},
        "land": {"frame_count_target": 6, "loop": False, "motion_class": "impact"},
        "dash": {"frame_count_target": 8, "loop": False, "motion_class": "burst"},
        "attack": {"frame_count_target": 12, "loop": False, "motion_class": "combat"},
        "hurt": {"frame_count_target": 6, "loop": False, "motion_class": "reaction"},
        "death": {"frame_count_target": 16, "loop": False, "motion_class": "terminal"},
    }
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_action in target_pack:
        action = str(raw_action).strip().lower()
        if not action or action in seen:
            continue
        seen.add(action)
        defaults = action_defaults.get(action, {"frame_count_target": default_frames, "loop": True, "motion_class": "custom"})
        jobs.append({
            "action": action,
            "status": "current" if action == current_action.lower() else "planned",
            "frame_count_target": int(defaults.get("frame_count_target", default_frames)),
            "fps": fps,
            "loop": bool(defaults.get("loop", True)),
            "motion_class": str(defaults.get("motion_class", "custom")),
            "reuse": {
                "character_identity_spec": True,
                "knowledge_profile": intake_spec.get("knowledge_profile"),
                "style_policy": True,
                "export_policy": True,
            },
        })
    if not any(job["action"] == current_action.lower() for job in jobs):
        jobs.insert(0, {
            "action": current_action.lower(),
            "status": "current",
            "frame_count_target": default_frames,
            "fps": fps,
            "loop": bool((asset_spec.get("motion") or {}).get("loop", True)),
            "motion_class": "current",
            "reuse": {
                "character_identity_spec": True,
                "knowledge_profile": intake_spec.get("knowledge_profile"),
                "style_policy": True,
                "export_policy": True,
            },
        })
    return {
        "spec_type": "AssetTargetPackPlan",
        "character_id": str(prepared.get("character_id", "")),
        "current_action": current_action.lower(),
        "job_count": len(jobs),
        "jobs": jobs,
        "execution_policy": {
            "current_pipeline_runs_one_action": True,
            "future_batch_expansion": "spawn_one_production_job_per_action",
            "shared_identity_reference": intake_spec.get("reference_character"),
            "shared_motion_reference": intake_spec.get("motion_reference"),
            "export_target": intake_spec.get("export_target"),
        },
    }


def _derive_multi_action_context_overrides(
    base_intake: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    action = str(job.get("action") or "motion")
    return {
        "motion_state": action,
        "action_filter": [action],
        "target_pack": [action],
        "reference_character": base_intake.get("reference_character"),
        "motion_reference": base_intake.get("motion_reference"),
        "knowledge_profile": base_intake.get("knowledge_profile"),
        "export_target": base_intake.get("export_target"),
        "sprite_asset_mode": True,
        "requested_frame_count": int(job.get("frame_count_target", 16) or 16),
        "requested_fps": int(job.get("fps", 12) or 12),
        "loop": bool(job.get("loop", True)),
    }


def _build_multi_action_production_plan(
    prepared: dict[str, Any],
    asset_spec: dict[str, Any],
) -> dict[str, Any]:
    intake = dict(asset_spec.get("asset_factory_intake_spec") or {})
    target_plan = dict(asset_spec.get("asset_target_pack_plan") or {})
    jobs = list(target_plan.get("jobs") or [])
    production_jobs: list[dict[str, Any]] = []
    for index, job in enumerate(jobs):
        action = str(job.get("action") or f"action_{index}")
        production_jobs.append({
            "job_id": f"{prepared.get('character_id', 'character')}_{action}",
            "index": index,
            "action": action,
            "status": job.get("status", "planned"),
            "frame_count_target": int(job.get("frame_count_target", asset_spec.get("frame_count_target", 16)) or 16),
            "fps": int(job.get("fps", asset_spec.get("fps", 12)) or 12),
            "loop": bool(job.get("loop", True)),
            "motion_class": str(job.get("motion_class", "custom")),
            "ctx_overrides": _derive_multi_action_context_overrides(intake, job),
            "shared_inputs": {
                "reference_character": intake.get("reference_character"),
                "motion_reference": intake.get("motion_reference"),
                "knowledge_profile": intake.get("knowledge_profile"),
                "character_id": prepared.get("character_id"),
            },
        })
    current = [job for job in production_jobs if job.get("status") == "current"]
    planned = [job for job in production_jobs if job.get("status") != "current"]
    return {
        "spec_type": "MultiActionProductionPlan",
        "character_id": str(prepared.get("character_id", "")),
        "job_count": len(production_jobs),
        "current_job_ids": [str(job["job_id"]) for job in current],
        "planned_job_ids": [str(job["job_id"]) for job in planned],
        "jobs": production_jobs,
        "scheduler_policy": {
            "mode": "plan_only_first_version",
            "next_step": "invoke current single-action pipeline once per planned job",
            "share_character_identity_spec": True,
            "share_distillation_bus": True,
            "merge_outputs_into_character_pack_manifest": True,
        },
    }


def _apply_distilled_production_rules(ctx: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if not bool(ctx.get("use_distilled_knowledge", True)):
        spec["distillation_bridge"] = {"enabled": False, "reason": "disabled_by_context"}
        return spec
    try:
        from mathart.workspace.knowledge_bus_factory import build_project_knowledge_bus
        project_root = Path(ctx.get("project_root") or Path.cwd()).resolve()
        bus = ctx.get("knowledge_bus") or build_project_knowledge_bus(project_root, backend_preference=("python",), verbose=False)
        if bus is None:
            spec["distillation_bridge"] = {"enabled": True, "activated": False, "reason": "knowledge_bus_unavailable"}
            return spec
        summary = dict(getattr(bus, "last_refresh_summary", {}) or {})
        applied: dict[str, Any] = {}
        gates = spec.setdefault("quality_gates", {})
        motion = spec.setdefault("motion", {})
        style_policy = spec.setdefault("style_policy", {})
        export_policy = spec.setdefault("export_policy", {})
        cell_w = int((spec.get("cell_size") or [64, 64])[0])

        fps_default = float(spec.get("fps", 12))
        fps_value = bus.resolve_scalar(["animation.timing.fps", "fps"], fps_default)
        if 1 <= fps_value <= 60:
            spec["fps"] = int(round(fps_value))
            applied["fps"] = spec["fps"]

        action = str(motion.get("action", "")).lower()
        if action in {"walk", "run"}:
            frame_default = float(spec.get("frame_count_target", 16))
            frame_value = bus.resolve_scalar(["animation.walk.frame_count", "walk_frames", "frame_count"], frame_default)
            if 2 <= frame_value <= 64:
                spec["frame_count_target"] = int(round(frame_value))
                applied["frame_count_target"] = spec["frame_count_target"]

        palette_default = float(style_policy.get("palette_color_count", 16))
        palette_value = bus.resolve_scalar(["pixel_art.palette.color_count", "palette_size"], palette_default)
        if 2 <= palette_value <= 64:
            style_policy["palette_color_count"] = int(round(palette_value))
            applied["palette_color_count"] = style_policy["palette_color_count"]

        contact_default_norm = float(gates.get("max_foot_slide_px", 12.0)) / max(1, cell_w)
        contact_value = bus.resolve_scalar(
            ["physics.contact.height_threshold", "foot_contact_height", "contact_height", "contact_threshold"],
            contact_default_norm,
        )
        foot_slide_px = contact_value * cell_w if contact_value <= 1.0 else contact_value
        if 0.5 <= foot_slide_px <= 16.0:
            gates["max_foot_slide_px"] = float(foot_slide_px)
            applied["max_foot_slide_px"] = gates["max_foot_slide_px"]

        ppu_default = float(export_policy.get("ppu", 32))
        ppu_value = bus.resolve_scalar(["export.unity.ppu", "ppu"], ppu_default)
        if 1 <= ppu_value <= 512:
            export_policy["ppu"] = int(round(ppu_value))
            applied["ppu"] = export_policy["ppu"]

        spec["distillation_bridge"] = {
            "enabled": True,
            "activated": True,
            "source": "RuntimeDistillationBus",
            "knowledge_dir": summary.get("knowledge_dir"),
            "module_count": int(summary.get("module_count", 0) or 0),
            "constraint_count": int(summary.get("constraint_count", 0) or 0),
            "evolution_state_modules": int(summary.get("evolution_state_modules", 0) or 0),
            "applied": applied,
        }
    except Exception as exc:
        spec["distillation_bridge"] = {"enabled": True, "activated": False, "reason": str(exc)}
    return spec


def _sprite_asset_prompts(raw_vibe: str, asset_spec: dict[str, Any]) -> tuple[str, str]:
    base_positive = (
        "pixel art, 16-bit game sprite, tiny 2D side-view character, "
        "single character full body, transparent background, clean hard pixel edges, "
        "limited color palette, flat cel shading, no anti-aliasing, "
        "retro game art style, metroidvania sprite, readable silhouette, "
        "consistent body proportions, game-ready sprite frame, "
        "small character centered in frame, no background, no scenery, "
        "indie game character sprite sheet"
    )
    positive = f"{raw_vibe}, {base_positive}" if raw_vibe else base_positive
    style_policy = asset_spec.get("style_policy") or {}
    palette_count = style_policy.get("palette_color_count")
    if palette_count:
        positive = f"{positive}, limited palette around {int(palette_count)} colors"
    negative = (
        "photorealistic, realistic texture, noise texture, abstract pattern, "
        "building, architecture, room, vehicle, machine, cockpit, window, door, "
        "large environment, landscape, background scenery, "
        "isometric view, top down view, front view, portrait, cropped body, "
        "huge character closeup, realistic painting, oil painting, watercolor, "
        "cinematic scene, text, ui, logo, blurry, deformed, extra limbs, "
        "inconsistent character, changing outfit, smooth gradient, "
        "anti-aliased edges, painterly style, impressionist, "
        "high detail texture, noisy, grain, film grain"
    )
    if asset_spec.get("asset_family") != "character_sprite":
        return raw_vibe, "blurry, low quality, distorted, deformed"
    return positive, negative


def _write_sprite_identity_reference(
    source_frame: Image.Image,
    mask_frame: Image.Image | None,
    path: Path,
) -> Path:
    rgba = source_frame.convert("RGBA")
    if mask_frame is not None:
        alpha = mask_frame.convert("L")
        rgba.putalpha(alpha)
    bbox = rgba.getbbox()
    if bbox is not None:
        rgba = rgba.crop(bbox)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
    target = max(1, int(256 * 0.72))
    scale = min(target / max(1, rgba.width), target / max(1, rgba.height))
    resized = rgba.resize(
        (max(1, int(round(rgba.width * scale))), max(1, int(round(rgba.height * scale)))),
        Image.Resampling.LANCZOS,
    )
    x = (256 - resized.width) // 2
    y = 256 - resized.height - 18
    canvas.alpha_composite(resized, (x, max(0, y)))
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(path)
    return path.resolve()


def _extract_character_identity_spec(reference_path: str | Path | None) -> dict[str, Any] | None:
    if not reference_path:
        return None
    path = Path(reference_path)
    if not path.is_file():
        return None
    with Image.open(path) as img:
        rgba = img.convert("RGBA")
    bbox = _image_content_bbox(rgba)
    width, height = rgba.size
    if bbox is None:
        return {
            "source_reference": str(path.resolve()),
            "available": False,
            "reason": "no foreground detected",
            "image_size": [width, height],
        }
    x0, y0, x1, y1 = bbox
    crop = rgba.crop(bbox)
    arr = np.asarray(crop)
    alpha = arr[:, :, 3]
    fg = alpha > 16
    foreground_area_ratio = float(fg.mean()) if fg.size else 0.0
    full_area_ratio = float(((x1 - x0) * (y1 - y0)) / max(1, width * height))
    transparent_background = float((np.asarray(rgba)[:, :, 3] <= 16).mean()) > 0.02

    rgb_pixels = arr[:, :, :3][fg]
    palette: list[dict[str, Any]] = []
    if len(rgb_pixels):
        quantized = (rgb_pixels // 32) * 32
        unique, counts = np.unique(quantized, axis=0, return_counts=True)
        order = np.argsort(counts)[::-1][:8]
        total = max(1, int(counts.sum()))
        for idx in order:
            color = unique[idx]
            palette.append({
                "hex": "#%02x%02x%02x" % (int(color[0]), int(color[1]), int(color[2])),
                "ratio": float(counts[idx] / total),
            })

    bbox_w = max(1, x1 - x0)
    bbox_h = max(1, y1 - y0)
    aspect = float(bbox_h / bbox_w)
    tags: list[str] = []
    if aspect >= 1.45:
        tags.append("tall_humanoid")
    elif aspect <= 0.85:
        tags.append("wide_creature_or_vehicle")
    else:
        tags.append("compact_body")
    if full_area_ratio < 0.25:
        tags.append("small_in_canvas")
    if transparent_background:
        tags.append("transparent_or_cutout")
    if palette and palette[0]["ratio"] > 0.45:
        tags.append("dominant_primary_color")

    return {
        "source_reference": str(path.resolve()),
        "available": True,
        "image_size": [width, height],
        "bbox": [int(x0), int(y0), int(x1), int(y1)],
        "bbox_size": [int(bbox_w), int(bbox_h)],
        "bbox_area_ratio": full_area_ratio,
        "foreground_area_ratio_in_bbox": foreground_area_ratio,
        "aspect_h_over_w": aspect,
        "transparent_background": transparent_background,
        "dominant_palette": palette,
        "silhouette_tags": tags,
        "identity_policy": {
            "role": "identity_constraint",
            "use_for_ipadapter": True,
            "use_for_palette_hint": bool(palette),
            "use_for_scale_hint": True,
        },
    }


def _node_ai_render(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    """AI render node — SESSION-158: Decoupled from guide baking.

    SESSION-158 PIPELINE DECOUPLING: This node no longer performs guide
    baking itself.  It consumes pre-baked guide sequences from the
    upstream ``guide_baking_stage`` (a pure CPU node that ALWAYS runs).
    When ``--skip-ai-render`` is active, this node skips only the GPU/AI
    rendering while the baked industrial guides remain available as
    first-class assets.

    Industrial References:
    - AnimateDiff / SparseCtrl: guide sequences MUST have real geometric
      variation for temporal attention coherence.
    - Jim Gray Fail-Fast: static guide sequences are rejected immediately
      by the Temporal Variance Circuit Breaker.
    """
    prepared = deps["prepare_character"]
    baked = deps["guide_baking_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "anti_flicker_render")
    archive_dir = _ensure_dir(character_dir / "archive")
    # SESSION-179: UX — prompt before AI render skip decision
    if bool(ctx.get("skip_ai_render", False)):
        sys.stderr.write(
            "\033[1;33m[🔔 AI 渲染跳过] skip_ai_render=True — "
            "仅输出纯 CPU 工业级引导序列 (Albedo/Normal/Depth)。\033[0m\n"
        )
        sys.stderr.flush()
        skipped_report = _write_json(
            stage_dir / "anti_flicker_render_skipped.json",
            {
                "character_id": prepared["character_id"],
                "skipped": True,
                "reason": "skip_ai_render flag enabled",
                "guide_baking_available": True,
                "guide_baking_report": baked["report_path"],
            },
        )
        archived_skip = archive_dir / "anti_flicker_render"
        archived_skip.mkdir(parents=True, exist_ok=True)
        archived_skip_report = archived_skip / skipped_report.name
        shutil.copy2(skipped_report, archived_skip_report)
        return {
            "character_id": prepared["character_id"],
            "character_dir": prepared["character_dir"],
            "skipped": True,
            "report_path": str(skipped_report),
            "manifest_path": None,
            "archived": {archived_skip_report.name: str(archived_skip_report.resolve())},
            "rng_spawn_digest": _current_rng_digest(ctx),
        }

    # SESSION-158: Consume pre-baked guide sequences from guide_baking_stage.
    resource_guard = _comfyui_resource_guard(ctx)
    if not bool(resource_guard.get("safe_to_run")):
        blocked_report = _write_json(
            stage_dir / "anti_flicker_render_resource_blocked.json",
            {
                "character_id": prepared["character_id"],
                "skipped": True,
                "reason": "comfyui_resource_guard_blocked",
                "resource_guard": resource_guard,
                "guide_baking_available": True,
                "guide_baking_report": baked["report_path"],
            },
        )
        archived_skip = archive_dir / "anti_flicker_render"
        archived_skip.mkdir(parents=True, exist_ok=True)
        archived_skip_report = archived_skip / blocked_report.name
        shutil.copy2(blocked_report, archived_skip_report)
        return {
            "character_id": prepared["character_id"],
            "character_dir": prepared["character_dir"],
            "skipped": True,
            "report_path": str(blocked_report),
            "manifest_path": None,
            "resource_guard": resource_guard,
            "archived": {archived_skip_report.name: str(archived_skip_report.resolve())},
            "rng_spawn_digest": _current_rng_digest(ctx),
        }

    source_frames = baked["source_frames"]
    normal_maps = baked["normal_maps"]
    depth_maps = baked["depth_maps"]
    mask_maps = baked["mask_maps"]
    guide_width = int(baked["guide_width"])
    guide_height = int(baked["guide_height"])
    asset_spec = _build_asset_production_spec(ctx, prepared)

    # SESSION-158: Hard Temporal Variance Circuit Breaker — only enforced
    # here at the AI render boundary, NOT in the bake stage.  This protects
    # AnimateDiff from static conditioning while allowing baked IR to persist.
    from mathart.core.anti_flicker_runtime import (
        validate_temporal_variance,
        assert_nonzero_temporal_variance,
    )
    validate_temporal_variance(source_frames, channel="source", mse_threshold=1.0)

    # SESSION-160: 防静止自爆核弹 (Variance Assert Gate) — per-pair MSE floor.
    # This is a stricter, non-negotiable assertion that fires if ANY
    # consecutive frame pair has MSE below the absolute floor.  It catches
    # frozen-animation forgeries that the ratio-based gate might miss.
    assert_nonzero_temporal_variance(
        source_frames, channel="source", mse_floor=0.0001,
    )

    # ── SESSION-192 [Physics Telemetry Audit] ─────────────────────────
    # Right before we hand the math-derived skeleton tensor over to the
    # GPU diffusion render, emit the bright-green [🔬 物理总线审计] handshake
    # banner so the operator can confirm:
    #   1. The action lock matches what the user asked for.
    #   2. The 16-frame anime subsampler is alive (SESSION-189 anchor).
    #   3. The downstream ControlNets will receive >= 0.85 spatial guidance
    #      after the cylinder colour pollution was killed.
    try:
        from mathart.core.anti_flicker_runtime import (
            emit_physics_telemetry_handshake,
            DECOUPLED_DEPTH_NORMAL_STRENGTH,
            DECOUPLED_RGB_STRENGTH,
        )
        # SESSION-196: prefer admission-validated action_name when present;
        # falls back to legacy motion_state for backwards compatibility.
        _spec_for_telemetry = ctx.get("director_studio_spec") or {}
        _telemetry_action = (
            str(_spec_for_telemetry.get("action_name") or "").strip()
            or str(prepared.get("motion_state", "unknown"))
        )
        try:
            _tensor_shape = tuple(np.asarray(source_frames[0]).shape) if len(source_frames) else None
            if _tensor_shape is not None:
                _tensor_shape = (len(source_frames),) + _tensor_shape
        except Exception:
            _tensor_shape = None
        emit_physics_telemetry_handshake(
            action_name=_telemetry_action,
            depth_normal_strength=DECOUPLED_DEPTH_NORMAL_STRENGTH,
            rgb_strength=DECOUPLED_RGB_STRENGTH,
            frames=int(prepared.get("frame_count", 16)),
            skeleton_tensor_shape=_tensor_shape,
            stream=sys.stderr,
        )
    except Exception as _telemetry_exc:  # noqa: BLE001
        # SESSION-194: telemetry must never break the render path, but the
        # failure must be logged loud so production operators see it.
        # SESSION-189 hard anchors and SESSION-190 decoupling are still
        # in force regardless of telemetry health.
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[mass_production] SESSION-192 physics telemetry handshake skipped: %s",
            _telemetry_exc,
        )

    # SESSION-194 P0-PIPELINE-INTEGRATION-CLOSURE: industrial-baking banner
    try:
        from mathart.core.anti_flicker_runtime import emit_industrial_baking_banner as _emit_bake_banner
        _emit_bake_banner(stream=sys.stderr)
    except Exception as _banner_exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "[mass_production] SESSION-194 industrial baking banner skipped: %s",
            _banner_exc,
        )

    # SESSION-196 P0-CLI-INTENT-THREADING: assemble the per-character
    # render config and thread the admission-validated director studio
    # spec through the validated dict (Redux Context handoff).  The
    # downstream chunk assembly site reads action_name +
    # _visual_reference_path via existing extractors — no new formal
    # parameter is added on AntiFlickerRenderBackend / OpenPose / IPA.
    _ds_spec = dict(ctx.get("director_studio_spec") or {})
    _ds_action = str(_ds_spec.get("action_name") or "").strip()
    _ds_ref = _ds_spec.get("_visual_reference_path")
    _ds_spec["asset_production_spec"] = asset_spec
    if not _ds_ref and source_frames:
        _fallback_ref_path = stage_dir / f"{prepared['character_id']}_sprite_identity_silhouette.png"
        _write_sprite_identity_reference(
            source_frames[0],
            mask_maps[0] if mask_maps else None,
            _fallback_ref_path,
        )
        _ds_ref = str(_fallback_ref_path.resolve())
        _ds_spec["_visual_reference_path"] = _ds_ref
        _ds_spec["visual_reference_path"] = _ds_ref
        _ds_spec["identity_reference_source"] = "sprite_mask_silhouette"
    character_identity_spec = _extract_character_identity_spec(_ds_ref)
    if character_identity_spec:
        asset_spec["character_identity_spec"] = character_identity_spec
        _ds_spec["character_identity_spec"] = character_identity_spec
        _ds_spec["asset_production_spec"] = asset_spec
    # ── SESSION-208 P0: Thread user vibe into ComfyUI style_prompt ──────
    # Root cause fix: the user's vibe was NEVER injected into
    # _render_cfg["comfyui"]["style_prompt"].  The downstream
    # validate_config() defaulted to a generic English placeholder,
    # and when the dummy-mesh Modal Decoupling path activated,
    # force_decouple_dummy_mesh_payload() overwrote with a fixed
    # SEMANTIC_HYDRATION_POSITIVE constant — the user's creative
    # intent was completely lost.
    #
    # Fix: translate the Chinese vibe to English via the SESSION-173
    # offline dictionary (_translate_vibe) and wrap it with the
    # SESSION-172 base quality tags (_armor_prompt).  This produces
    # a pure-English, CLIP-comprehensible prompt that carries the
    # user's semantic intent.
    # ───────────────────────────────────────────────────────────────
    _raw_vibe = str(ctx.get("vibe", "") or "").strip()
    try:
        from mathart.backend.ai_render_stream_backend import (
            _translate_vibe as _s208_translate,
            _armor_prompt as _s208_armor,
        )
        _s208_style_prompt = _s208_armor(_raw_vibe) if _raw_vibe else ""
    except Exception as _s208_import_exc:  # pragma: no cover
        import logging as _s208_log
        _s208_log.getLogger(__name__).warning(
            "[mass_production] SESSION-208 vibe translator import failed: %s",
            _s208_import_exc,
        )
        _s208_style_prompt = ""
    _sprite_prompt_seed = _raw_vibe if asset_spec.get("asset_family") == "character_sprite" else (_s208_style_prompt or _raw_vibe)
    _sprite_positive_prompt, _sprite_negative_prompt = _sprite_asset_prompts(
        _sprite_prompt_seed,
        asset_spec,
    )
    _ai_polish_policy = asset_spec.get("ai_polish_policy") or _ai_polish_fidelity_policy(str(asset_spec.get("asset_family", "character_sprite")), ai_required=True)
    _preserve_knobs = dict(_ai_polish_policy.get("comfyui_preservation_knobs") or {})
    asset_spec["ai_polish_policy"] = _ai_polish_policy
    def _emit_ai_progress(event: dict[str, Any]) -> None:
        callback = (ctx.get("_pdg") or {}).get("event_callback") if isinstance(ctx.get("_pdg"), dict) else None
        if callable(callback):
            callback({
                "event_type": "ai_render_progress",
                "character_id": prepared.get("character_id"),
                **dict(event),
            })

    _emit_ai_progress({
        "stage": "ai_render_config_built",
        "message": "AI render config assembly started.",
        "comfyui_url": str(ctx.get("comfyui_url", _DEFAULT_COMFYUI_URL)),
        "frame_count": int(prepared["frame_count"]),
    })

    _render_cfg: dict[str, Any] = {
        "output_dir": str(stage_dir),
        "name": prepared["character_id"],
        "source_frames": source_frames,
        "guide_channels": ["normal", "depth", "mask"],
        "guides": {"normal": True, "depth": True, "mask": True, "motion_vector": False},
        "normal_maps": normal_maps,
        "depth_maps": depth_maps,
        "mask_maps": mask_maps,
        "width": guide_width,
        "height": guide_height,
        "frame_count": int(prepared["frame_count"]),
        "fps": int(prepared["fps"]),
        "temporal": {
            "frame_count": int(prepared["frame_count"]),
            "fps": int(prepared["fps"]),
            "chunk_size": max(1, min(int(ctx.get("ai_render_chunk_size", 16)), int(prepared["frame_count"]), 16)),
        },
        "comfyui": {
            "live_execution": True,
            "fail_fast_on_offline": True,
            "url": str(ctx.get("comfyui_url", _DEFAULT_COMFYUI_URL)),
            "max_execution_time": float(ctx.get("ai_render_max_execution_time", 90.0)),
            "ws_timeout": float(ctx.get("ai_render_ws_timeout", 10.0)),
            "connect_timeout": float(ctx.get("ai_render_connect_timeout", 5.0)),
            "comfyui_checkpoint": str(ctx.get("comfyui_checkpoint") or ""),
            "steps": max(4, min(int(ctx.get("ai_render_steps", 12)), 20)),
            "context_window": max(1, min(int(ctx.get("ai_render_context_window", ctx.get("ai_render_chunk_size", 16))), 16)),
            "context_overlap": max(0, min(int(ctx.get("ai_render_context_overlap", 2)), 8)),
            "style_prompt": _sprite_positive_prompt,
            "negative_prompt": _sprite_negative_prompt,
            "denoising_strength": float(_preserve_knobs.get("denoising_strength", 0.28 if asset_spec.get("asset_family") == "character_sprite" else 0.42)),
            "cfg_scale": float(_preserve_knobs.get("cfg_scale", 3.2 if asset_spec.get("asset_family") == "character_sprite" else 4.0)),
            "controlnet_normal_weight": float(_preserve_knobs.get("controlnet_normal_weight", 0.75 if asset_spec.get("asset_family") == "character_sprite" else 0.95)),
            "controlnet_depth_weight": float(_preserve_knobs.get("controlnet_depth_weight", 0.75 if asset_spec.get("asset_family") == "character_sprite" else 0.95)),
            "sparsectrl_strength": float(_preserve_knobs.get("sparsectrl_strength", 0.80 if asset_spec.get("asset_family") == "character_sprite" else 0.95)),
            "temporal_consistency_required": bool(_preserve_knobs.get("temporal_consistency_required", True)),
            # SESSION-208: translated vibe is folded into the asset-spec prompt above.
        },
        # SESSION-208: preserve raw vibe for downstream hydration/logging
        "_raw_vibe": _raw_vibe,
        "_translated_style_prompt": _s208_style_prompt,
        "asset_production_spec": asset_spec,
        "_asset_positive_prompt": _sprite_positive_prompt,
        "_asset_negative_prompt": _sprite_negative_prompt,
        "_progress_callback": _emit_ai_progress,
        "session_id": _SESSION_ID,
        # ── SESSION-196 ride-along payload ───────────────────────────
        # The deep call site (builtin_backends._execute_live_pipeline)
        # already reads ``_visual_reference_path`` via
        # ``identity_hydration.extract_visual_reference_path`` and now
        # reads ``action_name`` via
        # ``intent_gateway.extract_action_name``. By writing both keys
        # at the top level AND inside director_studio_spec we satisfy the
        # three search locations declared in SESSION-195 (and SESSION-196
        # adds a fourth: action_filter[0]) without growing any function
        # signature in between.
        "director_studio_spec": _ds_spec or None,
        "action_name": _ds_action or str(prepared.get("motion_state", "") or ""),
        "_visual_reference_path": _ds_ref,
        # action_filter shortcut keeps SESSION-191 LookDev integration
        # honest: when the user picked a single LookDev action, surface
        # it as the canonical action_name fallback location.
        "action_filter": ctx.get("action_filter"),
    }
    pipeline = AssetPipeline(output_dir=str(stage_dir), verbose=False)
    _emit_ai_progress({
        "stage": "ai_render_backend_start",
        "message": "anti_flicker_render backend execution started.",
    })
    try:
        manifest = pipeline.run_backend("anti_flicker_render", _render_cfg)
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        is_resource_failure = any(token in lowered for token in ("out of memory", "cuda", "vram", "memory", "oom", "timeout", "timed out"))
        if not is_resource_failure:
            raise
        failure_report = _write_json(
            stage_dir / "anti_flicker_render_resource_failure.json",
            {
                "character_id": prepared["character_id"],
                "skipped": True,
                "reason": "comfyui_resource_failure",
                "error": message,
                "resource_guard": resource_guard,
                "guide_baking_available": True,
                "guide_baking_report": baked["report_path"],
            },
        )
        archived_failure = archive_dir / "anti_flicker_render"
        archived_failure.mkdir(parents=True, exist_ok=True)
        archived_failure_report = archived_failure / failure_report.name
        shutil.copy2(failure_report, archived_failure_report)
        return {
            "character_id": prepared["character_id"],
            "character_dir": prepared["character_dir"],
            "skipped": True,
            "report_path": str(failure_report),
            "manifest_path": None,
            "resource_guard": resource_guard,
            "resource_failure": message,
            "archived": {archived_failure_report.name: str(archived_failure_report.resolve())},
            "rng_spawn_digest": _current_rng_digest(ctx),
        }
    _emit_ai_progress({
        "stage": "ai_render_backend_completed",
        "message": "anti_flicker_render backend execution completed.",
        "output_count": len(getattr(manifest, "outputs", {}) or {}),
    })
    # SESSION-128: Inject rng_spawn_digest into AI render manifest
    rng_digest = _current_rng_digest(ctx)
    if rng_digest:
        manifest.metadata["rng_spawn_digest"] = rng_digest
    # SESSION-131: Temporal Quality Gate — Circuit Breaker for AI Rendering.
    manifest.metadata["asset_production_spec"] = asset_spec
    manifest.metadata["asset_factory_intake_spec"] = asset_spec.get("asset_factory_intake_spec")
    manifest.metadata["asset_target_pack_plan"] = asset_spec.get("asset_target_pack_plan")
    manifest.metadata["multi_action_production_plan"] = asset_spec.get("multi_action_production_plan")
    manifest.metadata["ai_polish_policy"] = _ai_polish_policy
    manifest.metadata["guide_fidelity_baseline"] = {
        "motion_authority": _ai_polish_policy.get("motion_authority"),
        "shape_authority": _ai_polish_policy.get("shape_authority"),
        "guide_channels": _ai_polish_policy.get("guide_authority_channels"),
        "frame_count": int(prepared.get("frame_count", len(source_frames))),
        "fps": int(prepared.get("fps", 12)),
    }
    manifest.metadata["comfyui_resource_guard"] = resource_guard
    manifest.metadata["asset_positive_prompt"] = _sprite_positive_prompt
    manifest.metadata["asset_negative_prompt"] = _sprite_negative_prompt
    manifest.metadata["identity_reference_source"] = _ds_spec.get("identity_reference_source")
    if character_identity_spec:
        manifest.metadata["character_identity_spec"] = character_identity_spec
    if asset_spec.get("asset_family") == "character_sprite":
        manifest.metadata["motion_authority_quality"] = _compute_sprite_motion_quality(
            list(source_frames),
            asset_spec,
            label="guide_baking_source_frames",
        )
    temporal_quality_report = None
    try:
        from mathart.quality.temporal_quality_gate import (
            TemporalQualityGate,
            QualityVerdict,
        )
        eval_frames = [np.array(f) for f in source_frames[:min(len(source_frames), 16)]]
        if len(eval_frames) >= 2:
            from types import SimpleNamespace
            proxy_mv_fields = []
            for fi in range(len(eval_frames) - 1):
                fa_gray = np.mean(eval_frames[fi][:, :, :3].astype(np.float64), axis=2)
                fb_gray = np.mean(eval_frames[fi + 1][:, :, :3].astype(np.float64), axis=2)
                diff = fb_gray - fa_gray
                h, w = fa_gray.shape
                proxy_mv_fields.append(SimpleNamespace(
                    dx=np.zeros((h, w), dtype=np.float64),
                    dy=diff * 0.1,
                    mask=np.ones((h, w), dtype=bool),
                ))
            gate = TemporalQualityGate(
                min_ssim_threshold=0.60,
                max_warp_error_threshold=0.25,
            )
            tq_result = gate.evaluate_sequence(eval_frames, proxy_mv_fields)
            temporal_quality_report = tq_result.to_dict()
            if tq_result.verdict != QualityVerdict.PASS:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    f"[SESSION-131] Temporal quality {tq_result.verdict.value}: "
                    f"{tq_result.diagnostics}"
                )
        del eval_frames
    except Exception as tq_exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"[SESSION-131] Temporal quality gate skipped: {tq_exc}"
        )

    if temporal_quality_report is not None:
        manifest.metadata["temporal_quality_gate"] = temporal_quality_report

    manifest_path = _save_manifest(manifest, stage_dir / "anti_flicker_render_artifact_manifest.json")
    archived = _archive_manifest_outputs(manifest, archive_dir, "anti_flicker_render")
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "skipped": False,
        "manifest_path": str(manifest_path),
        "archived": archived,
        "asset_factory_intake_spec": asset_spec.get("asset_factory_intake_spec"),
        "asset_target_pack_plan": asset_spec.get("asset_target_pack_plan"),
        "rng_spawn_digest": rng_digest,
        "temporal_quality_report": temporal_quality_report,
    }


def _image_content_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    rgba = image.convert("RGBA")
    arr = np.asarray(rgba)
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3]
    alpha_mask = alpha > 16
    if float(alpha_mask.mean()) < 0.98:
        mask = alpha_mask
    else:
        corner = np.concatenate([
            rgb[:8, :8].reshape(-1, 3),
            rgb[:8, -8:].reshape(-1, 3),
            rgb[-8:, :8].reshape(-1, 3),
            rgb[-8:, -8:].reshape(-1, 3),
        ], axis=0)
        bg = np.median(corner.astype(np.float32), axis=0)
        delta = np.linalg.norm(rgb.astype(np.float32) - bg[None, None, :], axis=2)
        luminance = rgb.astype(np.float32).mean(axis=2)
        mask = (delta > 18.0) | (luminance > 24.0)
    ys, xs = np.where(mask)
    if len(xs) == 0 or len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _fit_sprite_to_cell(image: Image.Image, cell_size: int) -> Image.Image:
    rgba = image.convert("RGBA")
    bbox = _image_content_bbox(rgba)
    if bbox is None:
        return Image.new("RGBA", (cell_size, cell_size), (0, 0, 0, 0))
    pad = 6
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(rgba.width, bbox[2] + pad)
    bottom = min(rgba.height, bbox[3] + pad)
    cropped = rgba.crop((left, top, right, bottom))
    target_max_w = max(1, int(cell_size * 0.78))
    target_max_h = max(1, int(cell_size * 0.86))
    scale = min(target_max_w / max(1, cropped.width), target_max_h / max(1, cropped.height))
    new_w = max(1, int(round(cropped.width * scale)))
    new_h = max(1, int(round(cropped.height * scale)))
    resample = Image.Resampling.LANCZOS if scale < 1.0 else Image.Resampling.NEAREST
    sprite = cropped.resize((new_w, new_h), resample)
    cell = Image.new("RGBA", (cell_size, cell_size), (0, 0, 0, 0))
    x = (cell_size - new_w) // 2
    ground_margin = max(2, int(cell_size * 0.08))
    y = cell_size - ground_margin - new_h
    cell.alpha_composite(sprite, (x, max(0, y)))
    return cell


def _load_ai_frame_paths(ai_item: dict[str, Any]) -> list[Path]:
    manifest_path = ai_item.get("manifest_path")
    if manifest_path and Path(manifest_path).exists():
        manifest = _load_manifest(manifest_path)
        paths: list[Path] = []
        for key, value in sorted(manifest.outputs.items()):
            if key.startswith("frame_") and isinstance(value, str):
                p = Path(value)
                if p.is_file():
                    paths.append(p.resolve())
        if paths:
            return paths
    return []




def _apply_mask_to_ai_frame(
    ai_frame: "Image.Image",
    mask_frame: "Image.Image | None",
    *,
    threshold: int = 16,
) -> "Image.Image":
    """Apply baked silhouette mask to AI-rendered frame to restore transparent background.

    Industrial 2D pixel art requires clean transparent background.
    ComfyUI AnimateDiff outputs full-opaque RGBA (alpha=255 everywhere).
    We use the upstream CPU-baked mask to restore the character silhouette.
    """
    import numpy as _np_m
    ai_rgba = ai_frame.convert("RGBA")
    w, h = ai_rgba.size
    if mask_frame is not None:
        mask_resized = mask_frame.convert("L").resize((w, h), Image.Resampling.NEAREST)
        mask_arr = _np_m.array(mask_resized, dtype=_np_m.uint8)
        alpha_arr = _np_m.where(mask_arr >= threshold, 255, 0).astype(_np_m.uint8)
    else:
        arr = _np_m.array(ai_rgba, dtype=_np_m.uint8)
        rgb_sum = arr[:, :, :3].astype(_np_m.int32).sum(axis=2)
        alpha_arr = _np_m.where(rgb_sum > threshold * 3, 255, 0).astype(_np_m.uint8)
    ai_arr = _np_m.array(ai_rgba, dtype=_np_m.uint8).copy()
    ai_arr[:, :, 3] = alpha_arr
    return Image.fromarray(ai_arr, mode="RGBA")



def _pixelize_sprite_frame(frame, palette_colors=16, cell_size=64):
    """Quantize sprite frame to pixel-art style: limited palette, binary alpha."""
    import numpy as _np_px
    rgba = frame.convert("RGBA")
    arr = _np_px.array(rgba, dtype=_np_px.uint8)
    alpha_bin = _np_px.where(arr[:,:,3] >= 32, 255, 0).astype(_np_px.uint8)
    rgb_img = Image.fromarray(arr[:,:,:3], mode="RGB")
    quantized = rgb_img.quantize(colors=max(4, int(palette_colors)), method=Image.Quantize.MEDIANCUT, dither=0)
    q_arr = _np_px.array(quantized.convert("RGB"), dtype=_np_px.uint8)
    result = _np_px.zeros_like(arr)
    result[:,:,:3] = q_arr
    result[:,:,3] = alpha_bin
    return Image.fromarray(result, mode="RGBA")

def _japanese_timing_durations(frame_count: int, fps: int) -> list[int]:
    base = max(40, int(round(1000 / max(1, fps))))
    durations: list[int] = []
    for index in range(frame_count):
        phase = index / max(1, frame_count - 1)
        ease = 0.5 - 0.5 * math.cos(math.tau * phase)
        multiplier = 1.0 + 0.28 * math.sin(math.tau * phase) - 0.18 * ease
        if index in {0, frame_count // 2, frame_count - 1}:
            multiplier += 0.35
        durations.append(max(35, int(round(base * multiplier))))
    return durations


def _compute_sprite_motion_quality(
    frames: list[Image.Image],
    asset_spec: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    bboxes: list[tuple[int, int, int, int] | None] = [_image_content_bbox(frame) for frame in frames]
    valid = [bbox for bbox in bboxes if bbox is not None]
    frame_count = len(frames)
    if frame_count == 0 or not valid:
        return {
            "label": label,
            "verdict": "fail",
            "reason": "no measurable foreground bbox",
            "frame_count": frame_count,
            "valid_bbox_count": len(valid),
        }

    width, height = frames[0].size
    pivots: list[tuple[float, float]] = []
    centers: list[tuple[float, float]] = []
    areas: list[float] = []
    bbox_sizes: list[tuple[int, int]] = []
    for bbox in bboxes:
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        bw = max(1, x1 - x0)
        bh = max(1, y1 - y0)
        centers.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5))
        pivots.append(((x0 + x1) * 0.5, float(y1)))
        areas.append(float(bw * bh) / max(1.0, float(width * height)))
        bbox_sizes.append((bw, bh))

    pivot_xs = np.asarray([p[0] for p in pivots], dtype=np.float64)
    pivot_ys = np.asarray([p[1] for p in pivots], dtype=np.float64)
    center_xs = np.asarray([c[0] for c in centers], dtype=np.float64)
    center_ys = np.asarray([c[1] for c in centers], dtype=np.float64)
    area_arr = np.asarray(areas, dtype=np.float64)
    bbox_w = np.asarray([s[0] for s in bbox_sizes], dtype=np.float64)
    bbox_h = np.asarray([s[1] for s in bbox_sizes], dtype=np.float64)

    pivot_drift_px = float(max(np.ptp(pivot_xs), np.ptp(pivot_ys))) if len(pivots) > 1 else 0.0
    center_drift_ratio = float(max(np.ptp(center_xs) / max(1, width), np.ptp(center_ys) / max(1, height))) if len(centers) > 1 else 0.0
    mean_area = float(area_arr.mean()) if len(area_arr) else 0.0
    area_jitter_ratio = float(area_arr.std() / max(1e-6, mean_area)) if len(area_arr) > 1 else 0.0
    bbox_w_jitter_ratio = float(bbox_w.std() / max(1e-6, float(bbox_w.mean()))) if len(bbox_w) > 1 else 0.0
    bbox_h_jitter_ratio = float(bbox_h.std() / max(1e-6, float(bbox_h.mean()))) if len(bbox_h) > 1 else 0.0
    bbox_jitter_ratio = max(bbox_w_jitter_ratio, bbox_h_jitter_ratio)
    if len(valid) >= 2:
        first = np.asarray(valid[0], dtype=np.float64)
        last = np.asarray(valid[-1], dtype=np.float64)
        loop_bbox_delta_ratio = float(np.abs(first - last).max() / max(1, max(width, height)))
    else:
        loop_bbox_delta_ratio = 0.0

    gates = dict(asset_spec.get("quality_gates") or {})
    max_pivot_drift_px = float(gates.get("max_pivot_drift_px", 2))
    max_bbox_jitter_ratio = float(gates.get("max_bbox_jitter_ratio", 0.08))
    max_area_jitter_ratio = float(gates.get("max_area_jitter_ratio", 0.18))
    max_loop_bbox_delta_ratio = float(gates.get("max_loop_bbox_delta_ratio", 0.16))
    failures: list[str] = []
    if pivot_drift_px > max_pivot_drift_px:
        failures.append("pivot_drift")
    if bbox_jitter_ratio > max_bbox_jitter_ratio:
        failures.append("bbox_jitter")
    if area_jitter_ratio > max_area_jitter_ratio:
        failures.append("area_jitter")
    if bool(gates.get("loop_closure_required", True)) and loop_bbox_delta_ratio > max_loop_bbox_delta_ratio:
        failures.append("loop_closure")

    return {
        "label": label,
        "verdict": "pass" if not failures else "review",
        "failures": failures,
        "frame_count": frame_count,
        "valid_bbox_count": len(valid),
        "image_size": [width, height],
        "metrics": {
            "pivot_drift_px": pivot_drift_px,
            "center_drift_ratio": center_drift_ratio,
            "bbox_jitter_ratio": bbox_jitter_ratio,
            "bbox_width_jitter_ratio": bbox_w_jitter_ratio,
            "bbox_height_jitter_ratio": bbox_h_jitter_ratio,
            "area_jitter_ratio": area_jitter_ratio,
            "mean_area_ratio": mean_area,
            "loop_bbox_delta_ratio": loop_bbox_delta_ratio,
        },
        "thresholds": {
            "max_pivot_drift_px": max_pivot_drift_px,
            "max_bbox_jitter_ratio": max_bbox_jitter_ratio,
            "max_area_jitter_ratio": max_area_jitter_ratio,
            "max_loop_bbox_delta_ratio": max_loop_bbox_delta_ratio,
        },
        "bboxes": [list(bbox) if bbox is not None else None for bbox in bboxes],
        "_frames_ref": frames,
    }


def _stabilize_sprite_pivots(
    frames: list[Image.Image],
    cell_size: int,
) -> tuple[list[Image.Image], dict[str, Any]]:
    """Repack foregrounds to a shared bottom-center pivot inside sprite cells."""
    stabilized: list[Image.Image] = []
    placements: list[dict[str, Any]] = []
    target_ground_y = cell_size - max(2, int(cell_size * 0.08))
    for index, frame in enumerate(frames):
        rgba = frame.convert("RGBA")
        bbox = _image_content_bbox(rgba)
        if bbox is None:
            stabilized.append(rgba)
            placements.append({"frame": index, "bbox": None, "shift": [0, 0], "reason": "no_foreground"})
            continue
        x0, y0, x1, y1 = bbox
        crop = rgba.crop(bbox)
        crop_w, crop_h = crop.size
        target_x = int(round((cell_size - crop_w) * 0.5))
        target_y = int(round(target_ground_y - crop_h))
        target_y = max(0, min(cell_size - crop_h, target_y))
        current_center_x = (x0 + x1) * 0.5
        current_ground_y = y1
        target_center_x = target_x + crop_w * 0.5
        shift = [float(target_center_x - current_center_x), float((target_y + crop_h) - current_ground_y)]
        canvas = Image.new("RGBA", (cell_size, cell_size), (0, 0, 0, 0))
        canvas.alpha_composite(crop, (target_x, target_y))
        stabilized.append(canvas)
        placements.append({
            "frame": index,
            "bbox": [int(x0), int(y0), int(x1), int(y1)],
            "target_xy": [int(target_x), int(target_y)],
            "shift": shift,
        })
    return stabilized, {
        "method": "bottom_center_repack",
        "target_ground_y": int(target_ground_y),
        "placements": placements,
    }


def _compare_motion_quality(
    guide_quality: dict[str, Any] | None,
    sprite_quality: dict[str, Any],
) -> dict[str, Any]:
    if not guide_quality:
        return {"available": False, "reason": "guide quality report missing"}
    guide_metrics = dict(guide_quality.get("metrics") or {})
    sprite_metrics = dict(sprite_quality.get("metrics") or {})
    keys = sorted(set(guide_metrics) & set(sprite_metrics))
    deltas: dict[str, Any] = {}
    for key in keys:
        try:
            guide_value = float(guide_metrics[key])
            sprite_value = float(sprite_metrics[key])
        except (TypeError, ValueError):
            continue
        deltas[key] = {
            "guide": guide_value,
            "sprite": sprite_value,
            "delta": sprite_value - guide_value,
            "ratio": sprite_value / max(1e-6, guide_value),
        }
    sprite_failures = set(sprite_quality.get("failures") or [])
    guide_failures = set(guide_quality.get("failures") or [])
    return {
        "available": True,
        "guide_verdict": guide_quality.get("verdict"),
        "sprite_verdict": sprite_quality.get("verdict"),
        "new_failures_after_ai": sorted(sprite_failures - guide_failures),
        "resolved_failures_after_ai": sorted(guide_failures - sprite_failures),
        "metric_deltas": deltas,
    }


def _analyze_sprite_foot_contacts(
    frames: list[Image.Image],
    asset_spec: dict[str, Any],
) -> dict[str, Any]:
    """Infer foot contact stability from lower foreground pixels in sprite cells."""
    contacts: list[dict[str, Any]] = []
    if not frames:
        return {"available": False, "reason": "no frames"}
    cell_w, cell_h = frames[0].size
    ground_band = max(2, int(round(cell_h * 0.16)))
    max_slide_px = float((asset_spec.get("quality_gates") or {}).get("max_foot_slide_px", 12.0))
    for index, frame in enumerate(frames):
        rgba = frame.convert("RGBA")
        arr = np.asarray(rgba)
        alpha = arr[:, :, 3]
        bbox = _image_content_bbox(rgba)
        if bbox is None:
            contacts.append({"frame": index, "contact": False, "reason": "no_foreground"})
            continue
        x0, _y0, x1, y1 = bbox
        y_start = max(0, int(y1) - ground_band)
        lower_mask = alpha[y_start:int(y1), int(x0):int(x1)] > 16
        ys, xs = np.where(lower_mask)
        if len(xs) == 0:
            contacts.append({"frame": index, "contact": False, "bbox": list(bbox), "reason": "empty_ground_band"})
            continue
        global_xs = xs + int(x0)
        global_ys = ys + y_start
        median_x = float(np.median(global_xs))
        min_x = float(np.min(global_xs))
        max_x = float(np.max(global_xs))
        ground_y = float(np.max(global_ys))
        split_x = (x0 + x1) * 0.5
        left_pixels = global_xs[global_xs <= split_x]
        right_pixels = global_xs[global_xs > split_x]
        left_x = float(np.median(left_pixels)) if len(left_pixels) else None
        right_x = float(np.median(right_pixels)) if len(right_pixels) else None
        stance = "both" if left_x is not None and right_x is not None else ("left" if left_x is not None else "right")
        contacts.append({
            "frame": index,
            "contact": True,
            "bbox": list(bbox),
            "ground_y": ground_y,
            "contact_x": median_x,
            "contact_span": [min_x, max_x],
            "left_x": left_x,
            "right_x": right_x,
            "stance": stance,
        })

    contact_frames = [c for c in contacts if c.get("contact")]
    if len(contact_frames) < 2:
        return {
            "available": True,
            "verdict": "review",
            "reason": "insufficient contact frames",
            "frame_count": len(frames),
            "contacts": contacts,
        }
    xs = np.asarray([float(c["contact_x"]) for c in contact_frames], dtype=np.float64)
    ys = np.asarray([float(c["ground_y"]) for c in contact_frames], dtype=np.float64)
    slide_px = float(np.ptp(xs)) if len(xs) > 1 else 0.0
    ground_jitter_px = float(np.ptp(ys)) if len(ys) > 1 else 0.0
    left_series = [c.get("left_x") for c in contact_frames if c.get("left_x") is not None]
    right_series = [c.get("right_x") for c in contact_frames if c.get("right_x") is not None]
    left_slide_px = float(np.ptp(np.asarray(left_series, dtype=np.float64))) if len(left_series) > 1 else 0.0
    right_slide_px = float(np.ptp(np.asarray(right_series, dtype=np.float64))) if len(right_series) > 1 else 0.0
    failures: list[str] = []
    if slide_px > max_slide_px:
        failures.append("foot_contact_slide")
    if ground_jitter_px > max_slide_px:
        failures.append("ground_contact_jitter")
    return {
        "available": True,
        "verdict": "pass" if not failures else "review",
        "failures": failures,
        "frame_count": len(frames),
        "contact_frame_count": len(contact_frames),
        "metrics": {
            "contact_slide_px": slide_px,
            "ground_jitter_px": ground_jitter_px,
            "left_contact_slide_px": left_slide_px,
            "right_contact_slide_px": right_slide_px,
        },
        "thresholds": {"max_foot_slide_px": max_slide_px},
        "contacts": contacts,
    }


def _extract_motion_grammar_spec(
    frames: list[Image.Image],
    asset_spec: dict[str, Any],
    *,
    action: str,
    source: str,
    fps: int,
) -> dict[str, Any]:
    if not frames:
        return {"available": False, "reason": "no frames", "action": action}
    motion_quality = _compute_sprite_motion_quality(frames, asset_spec, label=f"motion_grammar_{action}")
    foot_contacts = _analyze_sprite_foot_contacts(frames, asset_spec)
    bboxes = motion_quality.get("bboxes") or []
    centers: list[list[float] | None] = []
    bbox_curve: list[dict[str, Any]] = []
    for index, bbox in enumerate(bboxes):
        if bbox is None:
            centers.append(None)
            bbox_curve.append({"frame": index, "bbox": None})
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox]
        center = [(x0 + x1) * 0.5, (y0 + y1) * 0.5]
        centers.append(center)
        bbox_curve.append({
            "frame": index,
            "bbox": [int(x0), int(y0), int(x1), int(y1)],
            "center": center,
            "size": [float(x1 - x0), float(y1 - y0)],
            "ground_y": y1,
        })

    spacing: list[float] = []
    for prev, cur in zip(centers, centers[1:], strict=False):
        if prev is None or cur is None:
            spacing.append(0.0)
        else:
            spacing.append(float(np.linalg.norm(np.asarray(cur) - np.asarray(prev))))
    spacing_arr = np.asarray(spacing, dtype=np.float64) if spacing else np.asarray([0.0], dtype=np.float64)
    mean_spacing = float(spacing_arr.mean()) if len(spacing_arr) else 0.0
    timing_uniformity = 1.0 - float(spacing_arr.std() / max(1e-6, mean_spacing)) if mean_spacing > 0 else 1.0
    timing_uniformity = float(np.clip(timing_uniformity, 0.0, 1.0))
    if len(spacing_arr):
        max_spacing = max(1e-6, float(spacing_arr.max()))
        timing_chart = [float(v / max_spacing) for v in spacing_arr]
    else:
        timing_chart = []

    style_fingerprint: dict[str, Any] | None = None
    try:
        from mathart.sprite.analyzer import SpriteAnalyzer
        analyzer = SpriteAnalyzer(max_palette_colors=12)
        style_fingerprint = analyzer.analyze_frames(
            frames,
            source_name=f"{action}_motion_grammar",
            source_path=source,
            sprite_type="character",
        ).to_dict()
    except Exception as exc:
        style_fingerprint = {"available": False, "error": str(exc)}

    return {
        "available": True,
        "spec_type": "MotionGrammarSpec",
        "action": action,
        "source": source,
        "frame_count": len(frames),
        "fps": int(fps),
        "loop": True,
        "timing": {
            "timing_chart_normalized_spacing": timing_chart,
            "timing_uniformity": timing_uniformity,
            "mean_spacing_px": mean_spacing,
        },
        "bbox_curve": bbox_curve,
        "motion_quality": motion_quality,
        "foot_contacts": foot_contacts,
        "style_fingerprint": style_fingerprint,
        "transfer_policy": {
            "can_transfer_timing": True,
            "can_transfer_contact_rhythm": bool(foot_contacts.get("available")),
            "requires_identity_spec": True,
            "shape_authority": "math_engine",
        },
    }


def _resolve_runtime_distilled_scalar(ctx: dict[str, Any], names: list[str], default: float) -> float:
    bus = ctx.get("knowledge_bus")
    if bus is not None and hasattr(bus, "resolve_scalar"):
        try:
            return float(bus.resolve_scalar(names, default))
        except Exception:
            return float(default)
    return float(default)


def _distilled_quality_audit(
    ctx: dict[str, Any],
    asset_spec: dict[str, Any],
    motion_grammar_spec: dict[str, Any],
    sprite_motion_quality: dict[str, Any],
    foot_contact_report: dict[str, Any],
) -> dict[str, Any]:
    gates = dict(asset_spec.get("quality_gates") or {})
    style_policy = dict(asset_spec.get("style_policy") or {})
    features: dict[str, float] = {}
    checks: list[dict[str, Any]] = []

    def _add_check(name: str, value: float, op: str, threshold: float, *, severity: str = "review") -> None:
        passed = value <= threshold if op == "le" else value >= threshold
        features[name] = float(value)
        checks.append({
            "name": name,
            "value": float(value),
            "op": op,
            "threshold": float(threshold),
            "passed": bool(passed),
            "severity": severity,
        })

    motion_metrics = dict(sprite_motion_quality.get("metrics") or {})
    foot_metrics = dict(foot_contact_report.get("metrics") or {})
    timing = dict(motion_grammar_spec.get("timing") or {})
    style = motion_grammar_spec.get("style_fingerprint") or {}
    style_color = dict((style.get("color") or {}) if isinstance(style, dict) else {})

    _add_check(
        "pivot_drift_px",
        float(motion_metrics.get("pivot_drift_px", 0.0) or 0.0),
        "le",
        float(gates.get("max_pivot_drift_px", 2.0) or 2.0),
    )
    _add_check(
        "bbox_jitter_ratio",
        float(motion_metrics.get("bbox_jitter_ratio", 0.0) or 0.0),
        "le",
        float(gates.get("max_bbox_jitter_ratio", 0.08) or 0.08),
    )
    _add_check(
        "area_jitter_ratio",
        float(motion_metrics.get("area_jitter_ratio", 0.0) or 0.0),
        "le",
        float(gates.get("max_area_jitter_ratio", 0.18) or 0.18),
    )
    _add_check(
        "loop_bbox_delta_ratio",
        float(motion_metrics.get("loop_bbox_delta_ratio", 0.0) or 0.0),
        "le",
        float(gates.get("max_loop_bbox_delta_ratio", 0.16) or 0.16),
    )
    _add_check(
        "foot_contact_slide_px",
        float(foot_metrics.get("contact_slide_px", 0.0) or 0.0),
        "le",
        float(gates.get("max_foot_slide_px", 12.0) or 12.0),
    )
    _add_check(
        "ground_jitter_px",
        float(foot_metrics.get("ground_jitter_px", 0.0) or 0.0),
        "le",
        float(gates.get("max_foot_slide_px", 12.0) or 12.0),
    )

    palette_threshold = float(style_policy.get("palette_color_count", 16) or 16)
    _add_check(
        "palette_color_count",
        float(style_color.get("color_count", 0.0) or 0.0),
        "le",
        palette_threshold,
    )

    min_timing_uniformity = _resolve_runtime_distilled_scalar(
        ctx,
        ["animation.timing.uniformity", "timing_uniformity", "uniformity"],
        0.35,
    )
    # Compute interframe_mse FIRST so timing fallback can use it
    frames_raw_t = list(sprite_motion_quality.get("_frames_ref") or [])
    interframe_mse_for_timing = 0.0
    if len(frames_raw_t) >= 2:
        try:
            import numpy as _np_t
            arrs_t = [_np_t.array(f.convert("RGB"), dtype=_np_t.float32) for f in frames_raw_t]
            mse_vals_t = [float(_np_t.mean((arrs_t[i+1] - arrs_t[i])**2)) for i in range(len(arrs_t)-1)]
            interframe_mse_for_timing = float(sum(mse_vals_t)/len(mse_vals_t)) if mse_vals_t else 0.0
        except Exception:
            interframe_mse_for_timing = 0.0
    # timing_uniformity: if bbox-spacing is 0 but pixel MSE shows real motion, pass timing
    raw_tu = float(timing.get("timing_uniformity", 0.0) or 0.0)
    effective_tu = min_timing_uniformity if (raw_tu < min_timing_uniformity and interframe_mse_for_timing >= 5.0) else raw_tu
    _add_check(
        "timing_uniformity",
        effective_tu,
        "ge",
        min_timing_uniformity,
    )

    action = str(motion_grammar_spec.get("action", "") or "").lower()
    if action in {"walk", "run"}:
        contact_ratio = float(foot_contact_report.get("contact_frame_count", 0) or 0) / max(1.0, float(foot_contact_report.get("frame_count", 1) or 1))
        desired_contact_ratio = _resolve_runtime_distilled_scalar(
            ctx,
            ["animation.walk.contact_ratio", "contact_ratio"],
            0.45,
        )
        _add_check("contact_ratio", contact_ratio, "ge", max(0.05, desired_contact_ratio * 0.5))

    # visual semantics gate 1: inter-frame MSE (static frames must fail)
    frames_raw = list(sprite_motion_quality.get("_frames_ref") or [])
    interframe_mse = 0.0
    if len(frames_raw) >= 2:
        try:
            import numpy as _np_qa
            arrs = [_np_qa.array(f.convert("RGB"), dtype=_np_qa.float32) for f in frames_raw]
            mse_vals = [float(_np_qa.mean((arrs[i + 1] - arrs[i]) ** 2)) for i in range(len(arrs) - 1)]
            interframe_mse = float(sum(mse_vals) / len(mse_vals)) if mse_vals else 0.0
        except Exception:
            interframe_mse = 0.0
    _add_check("interframe_mse", interframe_mse, "ge", float(gates.get("min_interframe_mse", 10.0)), severity="hard")

    # visual semantics gate 2: pixel-art color complexity (AI noise >> 48 quantized colors)
    quant_colors_mean = 0.0
    if frames_raw:
        try:
            import numpy as _np_qa
            qc_vals = []
            for f in frames_raw:
                arr = _np_qa.array(f.convert("RGBA"), dtype=_np_qa.uint8)
                mask = arr[:, :, 3] > 8
                if not mask.any():
                    continue
                rgb = arr[:, :, :3][mask]
                quant = (rgb.astype(_np_qa.int32) // 16).astype(_np_qa.uint8)
                uniq = len({(int(r), int(g), int(b)) for r, g, b in quant.reshape(-1, 3)})
                qc_vals.append(float(uniq))
            quant_colors_mean = float(sum(qc_vals) / len(qc_vals)) if qc_vals else 0.0
        except Exception:
            quant_colors_mean = 0.0
    _add_check("pixel_art_quant_colors", quant_colors_mean, "le", float(gates.get("pixel_art_max_quant_colors", 48.0)), severity="hard")

    # visual semantics gate 3: inter-frame MSE variance (all-same frames supplemental)
    interframe_mse_var = 0.0
    if len(frames_raw) >= 3:
        try:
            import numpy as _np_qa
            arrs = [_np_qa.array(f.convert("RGB"), dtype=_np_qa.float32) for f in frames_raw]
            mse_vals_v = [float(_np_qa.mean((arrs[i + 1] - arrs[i]) ** 2)) for i in range(len(arrs) - 1)]
            interframe_mse_var = float(_np_qa.var(_np_qa.array(mse_vals_v))) if mse_vals_v else 0.0
        except Exception:
            interframe_mse_var = 0.0
    _add_check("interframe_mse_variance", interframe_mse_var, "ge", float(gates.get("min_interframe_mse_variance", 0.5)), severity="review")

    failed = [check for check in checks if not check["passed"]]
    hard_failures = []
    for check in failed:
        value = float(check["value"])
        threshold = float(check["threshold"])
        if check["op"] == "le" and threshold > 0 and value > threshold * 2.0:
            hard_failures.append(check["name"])
        elif check["op"] == "ge" and threshold > 0 and value < threshold * 0.5:
            hard_failures.append(check["name"])
    verdict = "pass" if not failed else ("fail" if hard_failures else "review")
    score = 1.0 - (len(failed) / max(1, len(checks)))
    return {
        "available": True,
        "audit_type": "distilled_quality_audit",
        "verdict": verdict,
        "score": float(max(0.0, score)),
        "features": features,
        "checks": checks,
        "failed_checks": [check["name"] for check in failed],
        "hard_failures": hard_failures,
        "distillation_bridge": asset_spec.get("distillation_bridge"),
    }


def _run_multi_action_controlled_executor(
    ctx: dict[str, Any],
    safe_executor_stub: dict[str, Any],
    *,
    max_actions: int = 1,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enabled = bool(ctx.get("multi_action_controlled_execute", False))
    allow_recursive = bool(ctx.get("multi_action_allow_recursive_pipeline", False))
    child_skip_ai_render = not bool(ctx.get("multi_action_child_allow_ai_render", False))
    max_actions = max(0, int(ctx.get("multi_action_max_actions", max_actions) or max_actions))
    invocations = list(safe_executor_stub.get("invocations") or [])
    selected: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for invocation in invocations:
        if not invocation.get("eligible"):
            results.append({
                "ticket_id": invocation.get("ticket_id"),
                "action": invocation.get("action"),
                "status": "skipped",
                "reason": invocation.get("blocked_reason") or "not_eligible",
            })
            continue
        if len(selected) >= max_actions:
            results.append({
                "ticket_id": invocation.get("ticket_id"),
                "action": invocation.get("action"),
                "status": "skipped",
                "reason": "max_actions_limit_reached",
            })
            continue
        selected.append(invocation)

    if not enabled:
        for invocation in selected:
            results.append({
                "ticket_id": invocation.get("ticket_id"),
                "action": invocation.get("action"),
                "status": "blocked",
                "reason": "multi_action_controlled_execute_false",
                "recommended_kwargs": invocation.get("recommended_kwargs"),
            })
        return {
            "spec_type": "MultiActionControlledExecutorReport",
            "enabled": False,
            "executed_count": 0,
            "selected_count": len(selected),
            "results": results,
            "safety_contract": {
                "no_pipeline_invoked": True,
                "requires_multi_action_controlled_execute": True,
            },
        }

    if not allow_recursive:
        for invocation in selected:
            results.append({
                "ticket_id": invocation.get("ticket_id"),
                "action": invocation.get("action"),
                "status": "blocked",
                "reason": "multi_action_allow_recursive_pipeline_false",
                "recommended_kwargs": invocation.get("recommended_kwargs"),
            })
        return {
            "spec_type": "MultiActionControlledExecutorReport",
            "enabled": True,
            "allow_recursive_pipeline": False,
            "executed_count": 0,
            "selected_count": len(selected),
            "results": results,
            "safety_contract": {
                "no_pipeline_invoked": True,
                "requires_multi_action_allow_recursive_pipeline": True,
                "child_skip_ai_render_default": child_skip_ai_render,
                "max_actions": max_actions,
            },
        }

    active_runner = runner or run_mass_production_factory
    executed_count = 0
    for invocation in selected:
        recommended = dict(invocation.get("recommended_kwargs") or {})
        child_kwargs = {
            "output_root": recommended.get("output_root") or ctx.get("batch_dir") or ".",
            "batch_size": 1,
            "pdg_workers": 1,
            "gpu_slots": 1,
            "seed": int(ctx.get("seed", _DEFAULT_SEED)),
            "skip_ai_render": child_skip_ai_render,
            "comfyui_url": str(ctx.get("comfyui_url", _DEFAULT_COMFYUI_URL)),
            "action_filter": recommended.get("action_filter") or [invocation.get("action")],
            "director_studio_spec": recommended.get("director_studio_spec_patch") or {},
            "sprite_asset_mode": True,
            "action_job_context": recommended.get("action_job_context"),
        }
        try:
            child_result = active_runner(**child_kwargs)
            executed_count += 1
            results.append({
                "ticket_id": invocation.get("ticket_id"),
                "action": invocation.get("action"),
                "status": "executed",
                "child_kwargs": {**child_kwargs, "event_callback": None},
                "child_result": child_result,
            })
        except Exception as exc:
            results.append({
                "ticket_id": invocation.get("ticket_id"),
                "action": invocation.get("action"),
                "status": "failed",
                "error": str(exc),
                "child_kwargs": {**child_kwargs, "event_callback": None},
            })
    return {
        "spec_type": "MultiActionControlledExecutorReport",
        "enabled": True,
        "allow_recursive_pipeline": True,
        "executed_count": executed_count,
        "selected_count": len(selected),
        "results": results,
        "safety_contract": {
            "no_pipeline_invoked": False,
            "child_skip_ai_render_default": child_skip_ai_render,
            "max_actions": max_actions,
            "max_parallel_heavy_jobs": 1,
        },
    }


def _build_multi_action_safe_executor_stub(
    execution_plan: dict[str, Any],
    *,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    invocations: list[dict[str, Any]] = []
    for ticket in list(execution_plan.get("tickets") or []):
        state = str(ticket.get("state") or "planned")
        if state == "completed":
            continue
        ctx_overrides = dict(ticket.get("ctx_overrides") or {})
        action = str(ticket.get("action") or ctx_overrides.get("motion_state") or "motion")
        invocation = {
            "ticket_id": ticket.get("ticket_id"),
            "job_id": ticket.get("job_id"),
            "action": action,
            "eligible": state == "ready",
            "blocked_reason": None if state == "ready" else "ticket_not_ready_dry_run_or_requires_confirmation",
            "call_target": "run_mass_production_factory",
            "recommended_kwargs": {
                "output_root": str(output_root) if output_root else None,
                "batch_size": 1,
                "action_filter": [action],
                "sprite_asset_mode": True,
                "director_studio_spec_patch": {
                    "action_name": action,
                    "target_pack": [action],
                    "visual_reference_path": ctx_overrides.get("reference_character"),
                    "motion_reference_path": ctx_overrides.get("motion_reference"),
                    "knowledge_profile": ctx_overrides.get("knowledge_profile"),
                    "export_target": ctx_overrides.get("export_target"),
                },
                "action_job_context": {
                    "action": action,
                    "parent_character_id": str(ticket.get("job_id") or "").rsplit("_", 1)[0],
                    "reference_character": ctx_overrides.get("reference_character"),
                    "motion_reference": ctx_overrides.get("motion_reference"),
                    "knowledge_profile": ctx_overrides.get("knowledge_profile"),
                    "export_target": ctx_overrides.get("export_target"),
                    "target_pack": [action],
                    "requested_frame_count": ctx_overrides.get("requested_frame_count"),
                    "requested_fps": ctx_overrides.get("requested_fps"),
                    "source_ticket_id": ticket.get("ticket_id"),
                },
            },
            "ctx_overrides": ctx_overrides,
            "resource_guardrails": {
                "gpu_heavy": bool((ticket.get("safety") or {}).get("gpu_heavy", True)),
                "max_parallel_for_this_ticket": 1,
                "requires_explicit_auto_execute": bool((ticket.get("safety") or {}).get("requires_explicit_auto_execute", True)),
            },
        }
        invocations.append(invocation)
    return {
        "spec_type": "MultiActionSafeExecutorStub",
        "mode": "call_plan_only",
        "invocation_count": len(invocations),
        "eligible_count": sum(1 for item in invocations if item.get("eligible")),
        "blocked_count": sum(1 for item in invocations if not item.get("eligible")),
        "invocations": invocations,
        "safety_contract": {
            "does_not_invoke_pipeline": True,
            "does_not_start_comfyui_jobs": True,
            "requires_next_stage_executor_for_real_run": True,
        },
    }


def _build_multi_action_execution_plan(
    multi_action_plan: dict[str, Any],
    completed_action: str,
    outputs: dict[str, str],
    *,
    auto_execute: bool = False,
) -> dict[str, Any]:
    tickets: list[dict[str, Any]] = []
    completed_action = str(completed_action).lower()
    for job in list(multi_action_plan.get("jobs") or []):
        action = str(job.get("action") or "motion").lower()
        if action == completed_action:
            state = "completed"
        elif bool(auto_execute):
            state = "ready"
        else:
            state = "planned"
        tickets.append({
            "ticket_id": f"ticket_{job.get('job_id', action)}",
            "job_id": job.get("job_id"),
            "action": action,
            "state": state,
            "requires_heavy_pipeline": state in {"ready", "planned"},
            "ctx_overrides": job.get("ctx_overrides") or {},
            "expected_outputs": {
                "spritesheet_transparent": outputs.get("spritesheet_transparent") if state == "completed" else None,
                "preview_gif": outputs.get("preview_gif") if state == "completed" else None,
                "metadata_path": outputs.get("metadata_path") if state == "completed" else None,
            },
            "safety": {
                "dry_run_ticket_only": not bool(auto_execute),
                "requires_explicit_auto_execute": state != "completed",
                "gpu_heavy": state != "completed",
            },
        })
    return {
        "spec_type": "MultiActionExecutionPlan",
        "mode": "dry_run" if not auto_execute else "ready_queue",
        "auto_execute_requested": bool(auto_execute),
        "completed_action": completed_action,
        "ticket_count": len(tickets),
        "ready_count": sum(1 for ticket in tickets if ticket["state"] == "ready"),
        "planned_count": sum(1 for ticket in tickets if ticket["state"] == "planned"),
        "completed_count": sum(1 for ticket in tickets if ticket["state"] == "completed"),
        "tickets": tickets,
        "execution_guardrails": {
            "no_recursive_pipeline_invocation_in_this_stage": True,
            "max_parallel_heavy_jobs_recommended": 1,
            "requires_user_or_scheduler_confirmation": not bool(auto_execute),
        },
    }


def _build_evolution_feedback_seed(
    asset_spec: dict[str, Any],
    motion_grammar_spec: dict[str, Any],
    distilled_quality_audit: dict[str, Any],
    foot_contact_report: dict[str, Any],
) -> dict[str, Any]:
    failed = list(distilled_quality_audit.get("failed_checks") or [])
    hard = list(distilled_quality_audit.get("hard_failures") or [])
    quality_score = float(distilled_quality_audit.get("score", 0.0) or 0.0)
    gates = dict(asset_spec.get("quality_gates") or {})
    style_policy = dict(asset_spec.get("style_policy") or {})
    ai_policy = dict(asset_spec.get("ai_policy") or {})
    candidate_knobs = {
        "max_foot_slide_px": float(gates.get("max_foot_slide_px", 12.0) or 12.0),
        "max_bbox_jitter_ratio": float(gates.get("max_bbox_jitter_ratio", 0.08) or 0.08),
        "max_area_jitter_ratio": float(gates.get("max_area_jitter_ratio", 0.18) or 0.18),
        "max_loop_bbox_delta_ratio": float(gates.get("max_loop_bbox_delta_ratio", 0.16) or 0.16),
        "palette_color_count": int(style_policy.get("palette_color_count", 16) or 16),
        "allow_shape_change": bool(ai_policy.get("allow_shape_change", False)),
    }
    hints: list[str] = []
    if "foot_contact_slide_px" in failed or "ground_jitter_px" in failed:
        hints.append("tighten foot contact lock and reduce AI shape freedom")
        candidate_knobs["suggested_max_foot_slide_px"] = max(0.5, candidate_knobs["max_foot_slide_px"] * 0.85)
    if "bbox_jitter_ratio" in failed or "area_jitter_ratio" in failed:
        hints.append("increase shape authority and lower denoise for sprite frames")
    if "palette_color_count" in failed:
        hints.append("strengthen limited palette prompt and postprocess quantization")
        candidate_knobs["suggested_palette_color_count"] = candidate_knobs["palette_color_count"]
    if "loop_bbox_delta_ratio" in failed:
        hints.append("apply loop closure correction before final spritesheet export")
    if not hints:
        hints.append("candidate is stable; consider graduating current production settings")
    return {
        "spec_type": "EvolutionFeedbackSeed",
        "fitness_score": quality_score,
        "verdict": distilled_quality_audit.get("verdict"),
        "failure_modes": failed,
        "hard_failures": hard,
        "candidate_knobs": candidate_knobs,
        "next_iteration_hints": hints,
        "motion_summary": {
            "action": motion_grammar_spec.get("action"),
            "frame_count": motion_grammar_spec.get("frame_count"),
            "timing_uniformity": (motion_grammar_spec.get("timing") or {}).get("timing_uniformity"),
            "foot_contact_verdict": foot_contact_report.get("verdict"),
        },
    }


def _build_character_pack_manifest(
    prepared: dict[str, Any],
    asset_spec: dict[str, Any],
    metadata: dict[str, Any],
    outputs: dict[str, str],
    distilled_quality_audit: dict[str, Any],
    evolution_feedback_seed: dict[str, Any],
) -> dict[str, Any]:
    intake = asset_spec.get("asset_factory_intake_spec") or {}
    plan = asset_spec.get("asset_target_pack_plan") or {}
    current_action = str(plan.get("current_action") or metadata.get("action") or prepared.get("motion_state", "motion"))
    jobs = list(plan.get("jobs") or [])
    completed_actions = [current_action]
    planned_actions = [str(job.get("action")) for job in jobs if str(job.get("status")) != "current"]
    action_quality = distilled_quality_audit.get("verdict", "unknown")
    return {
        "spec_type": "CharacterPackManifest",
        "character_id": prepared.get("character_id"),
        "reference_character": intake.get("reference_character"),
        "motion_reference": intake.get("motion_reference"),
        "knowledge_profile": intake.get("knowledge_profile"),
        "export_target": intake.get("export_target"),
        "unity_import_contract": asset_spec.get("unity_import_contract") or metadata.get("unity_import_contract"),
        "semantic_asset_resolution": intake.get("semantic_asset_resolution"),
        "active_existing_backends": intake.get("active_existing_backends", []),
        "target_pack": intake.get("target_pack") or [current_action],
        "completed_actions": completed_actions,
        "planned_actions": planned_actions,
        "asset_target_pack_plan": plan,
        "multi_action_production_plan": asset_spec.get("multi_action_production_plan"),
        "multi_action_execution_plan": asset_spec.get("multi_action_execution_plan"),
        "multi_action_safe_executor_stub": asset_spec.get("multi_action_safe_executor_stub"),
        "multi_action_controlled_executor_report": asset_spec.get("multi_action_controlled_executor_report"),
        "shared_identity_spec": asset_spec.get("character_identity_spec"),
        "distillation_bridge": asset_spec.get("distillation_bridge"),
        "assets": {
            current_action: {
                "status": "completed",
                "quality": action_quality,
                "spritesheet_transparent": outputs.get("spritesheet_transparent"),
                "spritesheet_black": outputs.get("spritesheet_black"),
                "preview_gif": outputs.get("preview_gif"),
                "metadata_path": outputs.get("metadata_path"),
                "frame_dir": outputs.get("frame_dir"),
                "distilled_quality_score": distilled_quality_audit.get("score"),
            }
        },
        "quality_summary": {
            "overall_verdict": action_quality,
            "failed_checks": distilled_quality_audit.get("failed_checks", []),
            "hard_failures": distilled_quality_audit.get("hard_failures", []),
            "gate_summary": _quality_gate_summary(distilled_quality_audit),
            "ai_polish_fidelity_report": metadata.get("ai_polish_fidelity_report"),
        },
        "evolution_feedback_seed": evolution_feedback_seed,
    }


def _persist_evolution_feedback_seed(ctx: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    if not bool(ctx.get("self_evolution_enabled", True)):
        return {"persisted": False, "reason": "self_evolution_disabled"}
    try:
        from mathart.evolution.state_vault import resolve_state_path
        project_root = Path(ctx.get("project_root") or Path.cwd()).resolve()
        state_path = resolve_state_path(project_root, "asset_factory_feedback_state.json")
        existing: dict[str, Any] = {}
        if state_path.exists():
            existing = json.loads(state_path.read_text(encoding="utf-8"))
        records = list(existing.get("records") or [])
        records.append({
            "character_id": manifest.get("character_id"),
            "knowledge_profile": manifest.get("knowledge_profile"),
            "target_pack": manifest.get("target_pack"),
            "quality_summary": manifest.get("quality_summary"),
            "evolution_feedback_seed": manifest.get("evolution_feedback_seed"),
        })
        existing.update({
            "module": "asset_factory_feedback",
            "record_count": len(records),
            "records": records[-32:],
        })
        state_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"persisted": True, "state_path": str(state_path.resolve()), "record_count": len(existing["records"])}
    except Exception as exc:
        return {"persisted": False, "reason": str(exc)}


def _node_sprite_asset_pack(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    baked = deps["guide_baking_stage"]
    ai = deps["ai_render_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "sprite_asset_pack")
    frame_dir = _ensure_dir(stage_dir / f"{prepared['character_id']}_{prepared['motion_state']}_frames")

    if not bool(ctx.get("sprite_asset_mode", True)):
        report_path = _write_json(stage_dir / "sprite_asset_pack_skipped.json", {
            "character_id": prepared["character_id"],
            "skipped": True,
            "reason": "sprite_asset_mode disabled",
        })
        return {"character_id": prepared["character_id"], "skipped": True, "report_path": str(report_path)}

    source_paths = _load_ai_frame_paths(ai)
    source_kind = "ai_render"
    if not source_paths:
        source_paths = [Path(p) for p in baked.get("albedo_paths", []) if Path(p).exists()]
        source_kind = "guide_baking_albedo"
    if not source_paths:
        raise RuntimeError("sprite_asset_pack could not find AI frames or baked albedo frames")

    cell_size = max(16, int(ctx.get("sprite_cell_size", 64)))
    asset_spec = _build_asset_production_spec(ctx, prepared)
    ai_manifest_path = ai.get("manifest_path")
    ai_manifest_metadata: dict[str, Any] = {}
    if ai_manifest_path and Path(ai_manifest_path).exists():
        try:
            ai_manifest_metadata = dict(_load_manifest(ai_manifest_path).metadata or {})
            ai_manifest_metadata["manifest_path"] = str(Path(ai_manifest_path).resolve())
            upstream_spec = ai_manifest_metadata.get("asset_production_spec")
            if isinstance(upstream_spec, dict):
                asset_spec = upstream_spec
        except Exception:
            ai_manifest_metadata = {}
    action = str(prepared.get("motion_state", "motion"))
    frames: list[Image.Image] = []
    frame_paths: list[str] = []

    # Load per-frame masks: guide masks for guide fallback, baked masks for AI output
    guide_mask_paths: list[Path] = []
    if source_kind == "guide_baking_albedo":
        guide_mask_paths = [Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()]
    baked_mask_paths: list[Path] = guide_mask_paths if source_kind == "guide_baking_albedo" else [
        Path(p) for p in baked.get("mask_paths", []) if Path(p).exists()
    ]
    use_baked_masks = len(baked_mask_paths) >= len(source_paths)
    palette_colors = int((asset_spec.get("style_policy") or {}).get("palette_color_count", 16))

    for index, src in enumerate(source_paths):
        with Image.open(src) as raw_img:
            raw_rgba = raw_img.convert("RGBA")
        # Apply baked silhouette mask to restore transparent background
        if use_baked_masks:
            with Image.open(baked_mask_paths[index % len(baked_mask_paths)]) as mask_img:
                raw_rgba = _apply_mask_to_ai_frame(raw_rgba, mask_img)
        else:
            raw_rgba = _apply_mask_to_ai_frame(raw_rgba, None)
        # Fit to sprite cell and quantize to pixel-art palette
        cell = _fit_sprite_to_cell(raw_rgba, cell_size)
        cell = _pixelize_sprite_frame(cell, palette_colors=palette_colors, cell_size=cell_size)
        frame_path = frame_dir / f"{action}_{index:03d}.png"
        cell.save(frame_path)
        frames.append(cell)
        frame_paths.append(str(frame_path.resolve()))

    raw_sprite_motion_quality = _compute_sprite_motion_quality(
        frames,
        asset_spec,
        label="sprite_asset_pack_frames_raw",
    )
    frames, pivot_stabilization = _stabilize_sprite_pivots(frames, cell_size)
    for frame, frame_path_text in zip(frames, frame_paths, strict=False):
        frame.save(Path(frame_path_text))
    sprite_motion_quality = _compute_sprite_motion_quality(
        frames,
        asset_spec,
        label="sprite_asset_pack_frames_stabilized",
    )
    guide_motion_quality = ai_manifest_metadata.get("motion_authority_quality")
    motion_authority_comparison = _compare_motion_quality(guide_motion_quality, sprite_motion_quality)
    foot_contact_report = _analyze_sprite_foot_contacts(frames, asset_spec)
    motion_grammar_spec = _extract_motion_grammar_spec(
        frames,
        asset_spec,
        action=action,
        source=source_kind,
        fps=int(prepared.get("fps", 12)),
    )
    distilled_quality_audit = _distilled_quality_audit(
        ctx,
        asset_spec,
        motion_grammar_spec,
        sprite_motion_quality,
        foot_contact_report,
    )
    evolution_feedback_seed = _build_evolution_feedback_seed(
        asset_spec,
        motion_grammar_spec,
        distilled_quality_audit,
        foot_contact_report,
    )

    ai_polish_policy = asset_spec.get("ai_polish_policy") or _ai_polish_fidelity_policy(str(asset_spec.get("asset_family", "character_sprite")), ai_required=True)
    ai_polish_fidelity_report = _build_ai_polish_fidelity_report(
        policy=ai_polish_policy,
        source_quality=ai_manifest_metadata.get("motion_authority_quality") or raw_sprite_motion_quality,
        final_quality=sprite_motion_quality,
        foot_contact_report=foot_contact_report,
        ai_manifest_metadata=ai_manifest_metadata,
        source_kind=source_kind,
    )
    unity_contract = asset_spec.get("unity_import_contract") or _unity_import_contract("character_sprite", fps=int(prepared.get("fps", 12)), ppu=32, loop=True)
    unity_contract = {**unity_contract, "ai_polish_status": ai_polish_fidelity_report.get("adoption_verdict"), "motion_fidelity_verified": not bool(ai_polish_fidelity_report.get("motion_regression")), "guide_source": "guide_baking_stage", "final_source": source_kind}
    asset_spec["unity_import_contract"] = unity_contract

    sheet_w = cell_size * len(frames)
    transparent_sheet = Image.new("RGBA", (sheet_w, cell_size), (0, 0, 0, 0))
    for index, frame in enumerate(frames):
        transparent_sheet.alpha_composite(frame, (index * cell_size, 0))
    black_sheet = Image.new("RGBA", transparent_sheet.size, (0, 0, 0, 255))
    black_sheet.alpha_composite(transparent_sheet)

    stem = f"{prepared['character_id']}_{action}"
    transparent_path = stage_dir / f"{stem}_spritesheet_transparent.png"
    black_path = stage_dir / f"{stem}_spritesheet_black.png"
    gif_path = stage_dir / f"{stem}_rhythmic_preview.gif"
    transparent_sheet.save(transparent_path)
    black_sheet.convert("RGB").save(black_path)

    fps = int(prepared.get("fps", 12))
    durations = _japanese_timing_durations(len(frames), fps)
    preview_frames = []
    for frame in frames:
        bg = Image.new("RGBA", frame.size, (0, 0, 0, 255))
        bg.alpha_composite(frame)
        preview_frames.append(bg.convert("P", palette=Image.Palette.ADAPTIVE))
    if preview_frames:
        preview_frames[0].save(
            gif_path,
            save_all=True,
            append_images=preview_frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
        )

    metadata = {
        "character_id": prepared["character_id"],
        "asset_production_spec": asset_spec,
        "asset_factory_intake_spec": asset_spec.get("asset_factory_intake_spec"),
        "asset_target_pack_plan": asset_spec.get("asset_target_pack_plan"),
        "multi_action_production_plan": asset_spec.get("multi_action_production_plan"),
        "multi_action_execution_plan": asset_spec.get("multi_action_execution_plan"),
        "multi_action_safe_executor_stub": asset_spec.get("multi_action_safe_executor_stub"),
        "multi_action_controlled_executor_report": asset_spec.get("multi_action_controlled_executor_report"),
        "character_identity_spec": asset_spec.get("character_identity_spec"),
        "action": action,
        "source_kind": source_kind,
        "source_frames": [str(p.resolve()) for p in source_paths],
        "frame_count": len(frames),
        "cell_width": cell_size,
        "cell_height": cell_size,
        "sheet_width": sheet_w,
        "sheet_height": cell_size,
        "pivot": "bottom_center",
        "ppu": 32,
        "unity_import_contract": unity_contract,
        "ai_polish_policy": ai_polish_policy,
        "ai_polish_fidelity_report": ai_polish_fidelity_report,
        "quality_gate_summary": _quality_gate_summary(distilled_quality_audit),
        "fps": fps,
        "motion_authority_quality": sprite_motion_quality,
        "motion_authority_quality_raw": raw_sprite_motion_quality,
        "motion_authority_comparison": motion_authority_comparison,
        "pivot_stabilization": pivot_stabilization,
        "foot_contact_report": foot_contact_report,
        "motion_grammar_spec": motion_grammar_spec,
        "distilled_quality_audit": distilled_quality_audit,
        "evolution_feedback_seed": evolution_feedback_seed,
        "japanese_timing": {
            "method": "kan_kyu_preview_durations",
            "durations_ms": durations,
            "note": "Runtime sprite frames stay game-ready; GIF preview applies anime timing holds for rhythm.",
        },
        "outputs": {
            "spritesheet_transparent": str(transparent_path.resolve()),
            "spritesheet_black": str(black_path.resolve()),
            "preview_gif": str(gif_path.resolve()),
            "frame_dir": str(frame_dir.resolve()),
            "frames": frame_paths,
        },
    }
    metadata_path = _write_json(stage_dir / f"{stem}_metadata.json", metadata)
    multi_action_execution_plan = _build_multi_action_execution_plan(
        asset_spec.get("multi_action_production_plan") or {},
        action,
        {
            "spritesheet_transparent": str(transparent_path.resolve()),
            "preview_gif": str(gif_path.resolve()),
            "metadata_path": str(metadata_path.resolve()),
        },
        auto_execute=bool(ctx.get("multi_action_auto_execute", False)),
    )
    asset_spec["multi_action_execution_plan"] = multi_action_execution_plan
    multi_action_safe_executor_stub = _build_multi_action_safe_executor_stub(
        multi_action_execution_plan,
        output_root=stage_dir,
    )
    asset_spec["multi_action_safe_executor_stub"] = multi_action_safe_executor_stub
    multi_action_controlled_executor_report = _run_multi_action_controlled_executor(ctx, multi_action_safe_executor_stub)
    asset_spec["multi_action_controlled_executor_report"] = multi_action_controlled_executor_report
    multi_action_execution_plan_path = _write_json(stage_dir / f"{prepared['character_id']}_multi_action_execution_plan.json", multi_action_execution_plan)
    multi_action_safe_executor_stub_path = _write_json(stage_dir / f"{prepared['character_id']}_multi_action_safe_executor_stub.json", multi_action_safe_executor_stub)
    multi_action_controlled_executor_report_path = _write_json(stage_dir / f"{prepared['character_id']}_multi_action_controlled_executor_report.json", multi_action_controlled_executor_report)
    metadata["multi_action_controlled_executor_report"] = multi_action_controlled_executor_report
    metadata["multi_action_controlled_executor_report_path"] = str(multi_action_controlled_executor_report_path.resolve())
    metadata["multi_action_safe_executor_stub"] = multi_action_safe_executor_stub
    metadata["multi_action_safe_executor_stub_path"] = str(multi_action_safe_executor_stub_path.resolve())
    metadata["multi_action_execution_plan"] = multi_action_execution_plan
    metadata["multi_action_execution_plan_path"] = str(multi_action_execution_plan_path.resolve())
    pack_manifest = _build_character_pack_manifest(
        prepared,
        asset_spec,
        metadata,
        {
            "spritesheet_transparent": str(transparent_path.resolve()),
            "spritesheet_black": str(black_path.resolve()),
            "preview_gif": str(gif_path.resolve()),
            "metadata_path": str(metadata_path.resolve()),
            "frame_dir": str(frame_dir.resolve()),
        },
        distilled_quality_audit,
        evolution_feedback_seed,
    )
    evolution_persistence = _persist_evolution_feedback_seed(ctx, pack_manifest)
    pack_manifest["evolution_persistence"] = evolution_persistence
    character_pack_manifest_path = _write_json(stage_dir / f"{prepared['character_id']}_character_pack_manifest.json", pack_manifest)
    metadata["character_pack_manifest_path"] = str(character_pack_manifest_path.resolve())
    metadata["character_pack_manifest"] = pack_manifest
    _write_json(metadata_path, metadata)
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "skipped": False,
        "stage_dir": str(stage_dir.resolve()),
        "metadata_path": str(metadata_path.resolve()),
        "character_pack_manifest_path": str(character_pack_manifest_path.resolve()),
        "multi_action_execution_plan_path": str(multi_action_execution_plan_path.resolve()),
        "multi_action_safe_executor_stub_path": str(multi_action_safe_executor_stub_path.resolve()),
        "multi_action_controlled_executor_report_path": str(multi_action_controlled_executor_report_path.resolve()),
        "multi_action_controlled_executor_report": multi_action_controlled_executor_report,
        "multi_action_safe_executor_stub": multi_action_safe_executor_stub,
        "multi_action_execution_plan": multi_action_execution_plan,
        "character_pack_manifest": pack_manifest,
        "evolution_feedback_seed": evolution_feedback_seed,
        "ai_polish_fidelity_report": ai_polish_fidelity_report,
        "evolution_persistence": evolution_persistence,
        "spritesheet_transparent": str(transparent_path.resolve()),
        "spritesheet_black": str(black_path.resolve()),
        "preview_gif": str(gif_path.resolve()),
        "frame_dir": str(frame_dir.resolve()),
        "frame_paths": frame_paths,
        "source_kind": source_kind,
        "motion_authority_quality": sprite_motion_quality,
        "motion_authority_quality_raw": raw_sprite_motion_quality,
        "motion_authority_comparison": motion_authority_comparison,
        "pivot_stabilization": pivot_stabilization,
        "foot_contact_report": foot_contact_report,
        "motion_grammar_spec": motion_grammar_spec,
        "distilled_quality_audit": distilled_quality_audit,
        "asset_production_spec": asset_spec,
        "asset_factory_intake_spec": asset_spec.get("asset_factory_intake_spec"),
        "asset_target_pack_plan": asset_spec.get("asset_target_pack_plan"),
        "multi_action_production_plan": asset_spec.get("multi_action_production_plan"),
        "multi_action_execution_plan": asset_spec.get("multi_action_execution_plan"),
        "multi_action_safe_executor_stub": asset_spec.get("multi_action_safe_executor_stub"),
        "multi_action_controlled_executor_report": asset_spec.get("multi_action_controlled_executor_report"),
        "character_identity_spec": asset_spec.get("character_identity_spec"),
        "rng_spawn_digest": _current_rng_digest(ctx),
    }


def _node_collect_batch(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared_items = sorted(deps["prepare_character"], key=lambda item: item["character_id"])
    unified_items = {item["character_id"]: item for item in deps["unified_motion_stage"]}
    vfx_items = {item["character_id"]: item for item in deps["vfx_weaver_stage"]}
    shell_items = {item["character_id"]: item for item in deps["pseudo3d_shell_stage"]}
    ribbon_items = {item["character_id"]: item for item in deps["physical_ribbon_stage"]}
    render_items = {item["character_id"]: item for item in deps["orthographic_render_stage"]}
    motion2d_items = {item["character_id"]: item for item in deps["motion2d_export_stage"]}
    delivery_items = {item["character_id"]: item for item in deps["final_delivery_stage"]}
    baking_items = {item["character_id"]: item for item in deps["guide_baking_stage"]}
    ai_items = {item["character_id"]: item for item in deps["ai_render_stage"]}
    sprite_items = {item["character_id"]: item for item in deps["sprite_asset_pack_stage"]}

    batch_dir = Path(ctx["batch_dir"]).resolve()
    summary_records: list[dict[str, Any]] = []
    for prepared in prepared_items:
        cid = prepared["character_id"]
        record = {
            "character_id": cid,
            "character_dir": prepared["character_dir"],
            "motion_state": prepared["motion_state"],
            "motion_gait": prepared["motion_gait"],
            "seed_spawn_digest": prepared.get("seed_spawn_digest"),
            "stage_rng_spawn_digests": {
                "prepare_character": prepared.get("seed_spawn_digest"),
                "unified_motion_stage": unified_items[cid].get("rng_spawn_digest"),
                "vfx_weaver_stage": vfx_items[cid].get("rng_spawn_digest"),
                "pseudo3d_shell_stage": shell_items[cid].get("rng_spawn_digest"),
                "physical_ribbon_stage": ribbon_items[cid].get("rng_spawn_digest"),
                "orthographic_render_stage": render_items[cid].get("rng_spawn_digest"),
                "motion2d_export_stage": motion2d_items[cid].get("rng_spawn_digest"),
                "final_delivery_stage": delivery_items[cid].get("rng_spawn_digest"),
                "guide_baking_stage": baking_items[cid].get("rng_spawn_digest"),
                "ai_render_stage": ai_items[cid].get("rng_spawn_digest"),
                "sprite_asset_pack_stage": sprite_items[cid].get("rng_spawn_digest"),
            },
            "manifests": {
                "unified_motion": unified_items[cid]["manifest_path"],
                "vfx_weaver": vfx_items[cid]["report_path"],
                "vfx_plugin_manifests": vfx_items[cid].get("plugin_manifests", {}),
                "pseudo_3d_shell": shell_items[cid]["manifest_path"],
                "physical_ribbon": ribbon_items[cid]["manifest_path"],
                "orthographic_pixel_render": render_items[cid]["manifest_path"],
                "unity_2d_anim": delivery_items[cid]["unity_manifest_path"],
                "spine_preview": delivery_items[cid]["preview_manifest_path"],
                "anti_flicker_render": ai_items[cid]["manifest_path"],
                "sprite_asset_pack": sprite_items[cid].get("metadata_path"),
                "guide_baking": baking_items[cid]["report_path"],
            },
            "final_outputs": {
                "spine_json": delivery_items[cid]["spine_json_path"],
                "delivery_archive": delivery_items[cid]["archived"],
                "orthographic_archive": render_items[cid].get("archived", {}),
                "vfx_weaver": vfx_items[cid],
                "guide_baking": baking_items[cid],
                "ai_render": ai_items[cid],
                "sprite_asset_pack": sprite_items[cid],
            },
        }
        record["semantic_asset_resolution"] = (sprite_items[cid].get("asset_factory_intake_spec") or {}).get("semantic_asset_resolution")
        record["active_existing_backends"] = (sprite_items[cid].get("asset_factory_intake_spec") or {}).get("active_existing_backends", [])
        record["unity_import_contract"] = (sprite_items[cid].get("asset_production_spec") or {}).get("unity_import_contract")
        record["quality_gate_summary"] = _quality_gate_summary(sprite_items[cid].get("distilled_quality_audit"))
        record["ai_polish_fidelity_report"] = sprite_items[cid].get("ai_polish_fidelity_report")
        record["evolution_feedback_seed"] = sprite_items[cid].get("evolution_feedback_seed")
        summary_records.append(record)
        _write_json(batch_dir / cid / f"{cid}_factory_index.json", record)

    batch_summary = {
        "session_id": _SESSION_ID,
        "artifact_family": "mass_production_batch",
        "batch_dir": str(batch_dir),
        "character_count": len(summary_records),
        "skip_ai_render": bool(ctx.get("skip_ai_render", False)),
        "pdg_workers": int(ctx.get("pdg_workers", 1)),
        "gpu_slots": int(ctx.get("gpu_slots", 1)),
        "active_existing_backends": sorted({backend for record in summary_records for backend in (record.get("active_existing_backends") or [])}),
        "unity_import_contracts": [record.get("unity_import_contract") for record in summary_records if record.get("unity_import_contract")],
        "unity_delivery_contract": _unity_delivery_contract(batch_dir, summary_records),
        "production_acceptance_profile": _production_acceptance_profile(summary_records),
        "ai_polish_summary": {
            "ai_required": True,
            "attempted_count": sum(1 for record in summary_records if (record.get("ai_polish_fidelity_report") or {}).get("ai_attempted")),
            "accepted_count": sum(1 for record in summary_records if (record.get("ai_polish_fidelity_report") or {}).get("adoption_verdict") == "ai_polish_accepted"),
            "review_required_count": sum(1 for record in summary_records if (record.get("ai_polish_fidelity_report") or {}).get("requires_review")),
            "motion_regression_count": sum(1 for record in summary_records if (record.get("ai_polish_fidelity_report") or {}).get("motion_regression")),
        },
        "quality_gate_summary": {
            "record_count": len(summary_records),
            "passed_count": sum(1 for record in summary_records if (record.get("quality_gate_summary") or {}).get("passed")),
            "review_count": sum(1 for record in summary_records if (record.get("quality_gate_summary") or {}).get("requires_review")),
            "verdicts": [((record.get("quality_gate_summary") or {}).get("overall_verdict")) for record in summary_records],
        },
        "records": summary_records,
    }
    summary_path = _write_json(batch_dir / "batch_summary.json", batch_summary)
    return {
        "batch_dir": str(batch_dir),
        "summary_path": str(summary_path),
        "character_count": len(summary_records),
        "records": summary_records,
    }


# ---------------------------------------------------------------------------
# Graph assembly + public entrypoints
# ---------------------------------------------------------------------------


def build_mass_production_graph(*, cache_dir: str | Path, max_workers: int, gpu_slots: int) -> ProceduralDependencyGraph:
    graph = ProceduralDependencyGraph(
        name="mass_production_factory",
        cache_dir=Path(cache_dir),
        max_workers=max(1, int(max_workers)),
        gpu_slots=max(1, int(gpu_slots)),
    )
    graph.add_node(PDGNode(name="seed_orders", operation=_node_seed_orders, cache_enabled=False))
    graph.add_node(
        PDGNode(
            name="fan_out_orders",
            dependencies=["seed_orders"],
            operation=_node_fan_out_orders,
            cache_enabled=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="prepare_character",
            dependencies=["fan_out_orders"],
            operation=_node_prepare_character,
            cache_enabled=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="unified_motion_stage",
            dependencies=["prepare_character"],
            operation=_node_unified_motion,
            cache_enabled=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="vfx_weaver_stage",
            dependencies=["prepare_character", "unified_motion_stage"],
            operation=_node_vfx_weaver,
            cache_enabled=False,
            requires_gpu=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="pseudo3d_shell_stage",
            dependencies=["prepare_character", "unified_motion_stage"],
            operation=_node_pseudo3d_shell,
            cache_enabled=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="physical_ribbon_stage",
            dependencies=["prepare_character", "unified_motion_stage"],
            operation=_node_physical_ribbon,
            cache_enabled=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="compose_mesh_stage",
            dependencies=["prepare_character", "pseudo3d_shell_stage", "physical_ribbon_stage"],
            operation=_node_compose_mesh,
            cache_enabled=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="orthographic_render_stage",
            dependencies=["prepare_character", "compose_mesh_stage"],
            operation=_node_orthographic_render,
            cache_enabled=False,
            requires_gpu=True,
        )
    )
    graph.add_node(
        PDGNode(
            name="motion2d_export_stage",
            dependencies=["prepare_character"],
            operation=_node_motion2d_export,
            cache_enabled=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="final_delivery_stage",
            dependencies=["prepare_character", "motion2d_export_stage"],
            operation=_node_final_delivery,
            cache_enabled=False,
        )
    )
    # SESSION-158: guide_baking_stage — independent CPU-bound bake node.
    # ALWAYS runs (requires_gpu=False), producing industrial-grade guide
    # sequences even when --skip-ai-render is active.
    graph.add_node(
        PDGNode(
            name="guide_baking_stage",
            dependencies=["prepare_character", "orthographic_render_stage", "motion2d_export_stage"],
            operation=_node_guide_baking,
            cache_enabled=False,
            requires_gpu=False,
        )
    )
    # SESSION-158: ai_render_stage now depends on guide_baking_stage
    # instead of directly performing bake + render in one monolith.
    graph.add_node(
        PDGNode(
            name="ai_render_stage",
            dependencies=["prepare_character", "guide_baking_stage"],
            operation=_node_ai_render,
            cache_enabled=False,
            requires_gpu=True,
        )
    )
    graph.add_node(
        PDGNode(
            name="sprite_asset_pack_stage",
            dependencies=["prepare_character", "guide_baking_stage", "ai_render_stage"],
            operation=_node_sprite_asset_pack,
            cache_enabled=False,
            requires_gpu=False,
        )
    )
    graph.add_node(
        PDGNode(
            name="collect_batch",
            dependencies=[
                "prepare_character",
                "unified_motion_stage",
                "vfx_weaver_stage",
                "pseudo3d_shell_stage",
                "physical_ribbon_stage",
                "orthographic_render_stage",
                "motion2d_export_stage",
                "final_delivery_stage",
                "guide_baking_stage",
                "ai_render_stage",
                "sprite_asset_pack_stage",
            ],
            operation=_node_collect_batch,
            topology="collect",
            cache_enabled=False,
        )
    )
    return graph


def run_mass_production_factory(
    *,
    output_root: str | Path,
    batch_size: int = 20,
    pdg_workers: int = 16,
    gpu_slots: int = _DEFAULT_GPU_SLOTS,
    seed: int = _DEFAULT_SEED,
    skip_ai_render: bool = False,
    comfyui_url: str = _DEFAULT_COMFYUI_URL,
    action_filter: list[str] | None = None,
    # SESSION-196 P0-CLI-INTENT-THREADING: opaque payloads forwarded into
    # the PDG initial context so per-character render stages can read
    # admission-validated fields (action_name / _visual_reference_path)
    # via tiny pure helpers — zero formal-parameter pollution downstream.
    director_studio_spec: dict[str, Any] | None = None,
    vibe: str = "",
    vfx_artifacts: dict[str, Any] | None = None,
    ai_render_max_execution_time: float = 90.0,
    ai_render_ws_timeout: float = 10.0,
    ai_render_chunk_size: int = 16,
    ai_render_context_window: int = 16,
    ai_render_context_overlap: int = 2,
    ai_render_steps: int = 12,
    ai_render_min_vram_free_bytes: int = 6 * 1024 * 1024 * 1024,
    ai_render_min_ram_free_bytes: int = 4 * 1024 * 1024 * 1024,
    comfyui_checkpoint: str = "",
    sprite_asset_mode: bool = True,
    sprite_cell_size: int = 64,
    sprite_background: str = "transparent_and_black",
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    action_job_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _emit(stage: str, message: str, **payload: Any) -> None:
        if not event_callback:
            return
        event = {
            "stage": stage,
            "message": message,
            "timestamp": time.time(),
            **payload,
        }
        event_callback(event)

    output_root = _ensure_dir(output_root)
    _emit("production_output_root_ready", "Production output root is ready.", output_root=str(output_root))
    batch_dir = _ensure_dir(output_root / f"mass_production_batch_{_timestamp_slug()}")
    cache_dir = _ensure_dir(batch_dir / ".pdg_cache")
    _emit("production_batch_created", "Production batch directory created.", batch_dir=str(batch_dir), cache_dir=str(cache_dir))
    graph = build_mass_production_graph(
        cache_dir=cache_dir,
        max_workers=max(1, int(pdg_workers)),
        gpu_slots=max(1, int(gpu_slots)),
    )
    _emit(
        "production_graph_built",
        "Production PDG graph built.",
        batch_size=max(1, int(batch_size)),
        pdg_workers=max(1, int(pdg_workers)),
        gpu_slots=max(1, int(gpu_slots)),
        skip_ai_render=bool(skip_ai_render),
        comfyui_url=str(comfyui_url),
        action_filter=action_filter,
    )
    _emit("production_graph_run_started", "Production PDG graph execution started.", target_outputs=["collect_batch"])

    def _emit_pdg_event(event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "pdg_event")
        node_name = str(event.get("node_name") or "")
        label = f"{event_type}:{node_name}" if node_name else event_type
        _emit(
            event_type,
            f"PDG event: {label}",
            pdg_event=event,
            node_name=node_name or None,
        )

    runtime = graph.run(
        ["collect_batch"],
        initial_context={
            "seed": int(seed),
            "batch_size": max(1, int(batch_size)),
            "batch_dir": str(batch_dir),
            "pdg_workers": max(1, int(pdg_workers)),
            "gpu_slots": max(1, int(gpu_slots)),
            "skip_ai_render": bool(skip_ai_render),
            "comfyui_url": str(comfyui_url),
            # [SESSION-191 LookDev Deep Pruning] action_filter 穿透到 PDG 节点
            "action_filter": action_filter,
            # [SESSION-196 P0-CLI-INTENT-THREADING] Director Studio spec
            # ride-along: writes the immutable admission payload into the
            # PDG context so deep call sites (anti_flicker_render, OpenPose
            # bake hook, IPAdapter late-binding) can resolve action_name and
            # _visual_reference_path through the existing extractor helpers.
            "director_studio_spec": director_studio_spec,
            "vibe": str(vibe or ""),
            "vfx_artifacts": vfx_artifacts or {},
            "ai_render_max_execution_time": max(1.0, float(ai_render_max_execution_time)),
            "ai_render_ws_timeout": max(1.0, float(ai_render_ws_timeout)),
            "ai_render_chunk_size": max(1, int(ai_render_chunk_size)),
            "ai_render_context_window": max(1, int(ai_render_context_window)),
            "ai_render_context_overlap": max(0, int(ai_render_context_overlap)),
            "ai_render_steps": max(1, int(ai_render_steps)),
            "ai_render_min_vram_free_bytes": max(1, int(ai_render_min_vram_free_bytes)),
            "ai_render_min_ram_free_bytes": max(1, int(ai_render_min_ram_free_bytes)),
            "comfyui_checkpoint": str(comfyui_checkpoint or ""),
            "sprite_asset_mode": bool(sprite_asset_mode),
            "sprite_cell_size": max(16, int(sprite_cell_size)),
            "sprite_background": str(sprite_background),
            "action_job_context": action_job_context,
        },
        event_callback=_emit_pdg_event,
    )
    _emit(
        "production_graph_run_completed",
        "Production PDG graph execution completed.",
        scheduler=runtime.get("scheduler", {}),
        topology_summary=runtime.get("topology_summary", {}),
        node_states=runtime.get("node_states", {}),
    )
    trace_path = _write_json(batch_dir / "pdg_runtime_trace.json", runtime)
    target_output = runtime["target_outputs"]["collect_batch"]
    result_payload = {
        "status": "ok",
        "session_id": _SESSION_ID,
        "batch_dir": str(batch_dir),
        "summary_path": target_output["summary_path"],
        "pdg_trace_path": str(trace_path),
        "character_count": int(target_output["character_count"]),
        "scheduler": runtime.get("scheduler", {}),
        "topology_summary": runtime.get("topology_summary", {}),
        "node_states": runtime.get("node_states", {}),
        "skip_ai_render": bool(skip_ai_render),
        "action_job_context": action_job_context,
    }
    _write_json(batch_dir / "mass_production_result.json", result_payload)
    _emit(
        "production_result_written",
        "Production result manifest written.",
        batch_dir=str(batch_dir),
        summary_path=str(target_output["summary_path"]),
        pdg_trace_path=str(trace_path),
        character_count=int(target_output["character_count"]),
    )
    return result_payload


# ---------------------------------------------------------------------------
# CLI facade
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_mass_production_factory.py",
        description="Industrial PDG-v2 mass production asset factory for MarioTrickster-MathArt.",
    )
    parser.add_argument("--output-root", required=True, help="Root directory under which the batch folder is created.")
    parser.add_argument("--batch-size", type=int, default=20, help="Number of character work items to fan out.")
    parser.add_argument("--pdg-workers", type=int, default=16, help="PDG mapped fan-out worker count.")
    parser.add_argument("--gpu-slots", type=int, default=_DEFAULT_GPU_SLOTS, help="Concurrent GPU work-item budget for GPU-tagged PDG nodes.")
    parser.add_argument("--seed", type=int, default=_DEFAULT_SEED, help="Deterministic root seed for SeedSequence splitting.")
    parser.add_argument("--skip-ai-render", action="store_true", help="Skip the anti_flicker_render GPU stage for dry-run / CPU-only validation.")
    parser.add_argument("--comfyui-url", default=_DEFAULT_COMFYUI_URL, help="ComfyUI server URL used when AI rendering is enabled.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = run_mass_production_factory(
        output_root=args.output_root,
        batch_size=args.batch_size,
        pdg_workers=args.pdg_workers,
        gpu_slots=args.gpu_slots,
        seed=args.seed,
        skip_ai_render=args.skip_ai_render,
        comfyui_url=args.comfyui_url,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

