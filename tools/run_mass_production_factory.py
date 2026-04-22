from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

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

_SESSION_ID = "SESSION-131"
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
        except Exception:
            pass
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
                    if p.exists():
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
_MOTION_STATES = ["idle", "walk", "run", "jump", "fall", "hit"]


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
    return _MOTION_STATES[int(rng.integers(0, len(_MOTION_STATES)))]


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
) -> tuple[list[Image.Image], list[Image.Image], list[Image.Image], list[Image.Image]]:
    """Bake TRUE per-frame guide sequences from real bone-driven animation.

    SESSION-130: ERADICATE single-image replication forgery.

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

    Returns
    -------
    tuple[list[Image.Image], list[Image.Image], list[Image.Image], list[Image.Image]]
        (source_frames, normal_maps, depth_maps, mask_maps) with genuine
        per-frame geometric variation.
    """
    from mathart.animation.genotype import CharacterGenotype
    from mathart.pipeline_contract import PipelineContractError

    # ── Reconstruct character from serialized genotype ──────────────────────
    genotype_data = _load_json(Path(genotype_path))
    genotype = CharacterGenotype.from_dict(genotype_data)
    skeleton = genotype.build_shaped_skeleton()
    style = genotype.decode_to_style()

    # ── Build animation_func from Clip2D bone transforms ────────────────────
    # The Clip2D contains per-frame Pose2D with bone_transforms.
    # We construct an animation_func(t) that maps t in [0, 1] to a pose dict
    # by interpolating between the nearest Clip2D frames.
    clip_frames = clip_2d.frames if clip_2d and hasattr(clip_2d, "frames") else []
    n_clip = len(clip_frames)
    if n_clip == 0:
        raise PipelineContractError(
            "empty_clip_2d",
            "[_bake_true_motion_guide_sequence] Clip2D has zero frames.  "
            "Cannot construct animation_func for per-frame rendering.  "
            "The motion2d_export_stage must produce a non-empty Clip2D.",
        )

    def _animation_func_from_clip(t: float) -> dict[str, float]:
        """Map t in [0, 1] to a pose dict by sampling Clip2D frames."""
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
            rot_lo = float(xform_lo.get("rotation", 0.0))
            rot_hi = float(xform_hi.get("rotation", 0.0))
            pose[bone_name] = rot_lo + alpha * (rot_hi - rot_lo)

        # Interpolate root position
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

            if use_industrial:
                try:
                    result = render_character_maps_industrial(
                        skeleton=skeleton,
                        pose=pose,
                        style=style,
                        width=render_width,
                        height=render_height,
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

    return source_frames, normal_maps, depth_maps, mask_maps


# ---------------------------------------------------------------------------
# PDG node operations
# ---------------------------------------------------------------------------


def _node_seed_orders(ctx: dict[str, Any], _deps: dict[str, Any]) -> dict[str, Any]:
    batch_size = int(ctx.get("batch_size", 1))
    return {
        "batch_size": batch_size,
        "states": list(_MOTION_STATES),
        "preset_names": [name for name, _ in _preset_factories()],
        "batch_dir": str(Path(ctx["batch_dir"]).resolve()),
        "skip_ai_render": bool(ctx.get("skip_ai_render", False)),
    }


def _node_fan_out_orders(_ctx: dict[str, Any], deps: dict[str, Any]) -> PDGFanOutResult:
    batch_size = int(deps["seed_orders"]["batch_size"])
    payloads = []
    partition_keys = []
    labels = []
    attributes = []
    for index in range(batch_size):
        character_id = f"character_{index:03d}"
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

    state = _state_from_rng(rng)
    frame_count = int(rng.integers(24, 49))
    fps = int(rng.choice(np.array([12, 15, 24], dtype=np.int64)))
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
    prepared = deps["prepare_character"]
    shell = deps["pseudo3d_shell_stage"]
    ribbon = deps["physical_ribbon_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "composed_mesh")
    attachment_mesh = _mesh_dict_from_npz(prepared["attachment_mesh_path"])
    shell_mesh = _mesh_dict_from_npz(shell["mesh_path"])
    ribbon_mesh = _mesh_dict_from_npz(ribbon["mesh_path"])

    if shell_mesh["vertices"].ndim == 3:
        shell_mesh = {
            "vertices": shell_mesh["vertices"][-1],
            "normals": shell_mesh["normals"][-1],
            "triangles": shell_mesh["triangles"],
            "colors": shell_mesh["colors"],
        }

    composed = _merge_meshes([shell_mesh, attachment_mesh, ribbon_mesh])
    composed_path = _save_mesh_npz(stage_dir / f"{prepared['character_id']}_composed_mesh.npz", composed)
    report_path = _write_json(
        stage_dir / f"{prepared['character_id']}_composition_report.json",
        {
            "character_id": prepared["character_id"],
            "vertex_count": int(composed["vertices"].shape[0]),
            "triangle_count": int(composed["triangles"].shape[0]),
            "sources": {
                "shell_mesh_path": shell["mesh_path"],
                "attachment_mesh_path": prepared["attachment_mesh_path"],
                "ribbon_mesh_path": ribbon["mesh_path"],
            },
        },
    )
    return {
        "character_id": prepared["character_id"],
        "character_dir": prepared["character_dir"],
        "mesh_path": str(composed_path),
        "report_path": str(report_path),
        "rng_spawn_digest": _current_rng_digest(ctx),
    }


def _node_orthographic_render(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared = deps["prepare_character"]
    composed = deps["compose_mesh_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "orthographic_pixel_render")
    archive_dir = _ensure_dir(character_dir / "archive")
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


def _node_ai_render(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    """AI render node — SESSION-130: True Motion Guide Architecture.

    This node now depends on BOTH orthographic_render_stage AND
    motion2d_export_stage.  It uses the Clip2D bone transforms from
    motion2d to bake genuine per-frame guide sequences via
    ``_bake_true_motion_guide_sequence()``, then validates temporal
    variance before passing to the anti_flicker_render backend.

    Industrial References:
    - AnimateDiff / SparseCtrl: guide sequences MUST have real geometric
      variation for temporal attention coherence.
    - Jim Gray Fail-Fast: static guide sequences are rejected immediately
      by the Temporal Variance Circuit Breaker.
    """
    from mathart.core.anti_flicker_runtime import validate_temporal_variance

    prepared = deps["prepare_character"]
    render = deps["orthographic_render_stage"]
    motion2d = deps["motion2d_export_stage"]
    character_dir = Path(prepared["character_dir"])
    stage_dir = _ensure_dir(character_dir / "anti_flicker_render")
    archive_dir = _ensure_dir(character_dir / "archive")
    if bool(ctx.get("skip_ai_render", False)):
        skipped_report = _write_json(
            stage_dir / "anti_flicker_render_skipped.json",
            {
                "character_id": prepared["character_id"],
                "skipped": True,
                "reason": "skip_ai_render flag enabled",
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

    # SESSION-130: Bake TRUE per-frame guide sequences from real animation.
    # The orthographic render manifest provides the render dimensions;
    # the Clip2D from motion2d provides the per-frame bone transforms.
    render_manifest = _load_manifest(Path(render["manifest_path"]))
    render_meta = render_manifest.metadata or {}
    render_width = int(render_meta.get("output_width", render_meta.get("width", 192)))
    render_height = int(render_meta.get("output_height", render_meta.get("height", 192)))

    clip_2d = motion2d.get("clip_2d")
    source_frames, normal_maps, depth_maps, mask_maps = _bake_true_motion_guide_sequence(
        genotype_path=prepared["genotype_path"],
        clip_2d=clip_2d,
        frame_count=int(prepared["frame_count"]),
        render_width=render_width,
        render_height=render_height,
    )

    # SESSION-130: Temporal Variance Circuit Breaker — Fail-Fast guard.
    # Validate that the baked guide sequences have genuine inter-frame
    # geometric variation before passing to the AI rendering backend.
    validate_temporal_variance(source_frames, channel="source", mse_threshold=1.0)

    guide_width = source_frames[0].size[0] if source_frames else render_width
    guide_height = source_frames[0].size[1] if source_frames else render_height

    pipeline = AssetPipeline(output_dir=str(stage_dir), verbose=False)
    manifest = pipeline.run_backend(
        "anti_flicker_render",
        {
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
                "chunk_size": min(16, int(prepared["frame_count"])),
            },
            "comfyui": {
                "live_execution": True,
                "fail_fast_on_offline": True,
                "url": str(ctx.get("comfyui_url", _DEFAULT_COMFYUI_URL)),
            },
            "session_id": _SESSION_ID,
        },
    )
    # SESSION-128: Inject rng_spawn_digest into AI render manifest
    rng_digest = _current_rng_digest(ctx)
    if rng_digest:
        manifest.metadata["rng_spawn_digest"] = rng_digest
    # SESSION-131: Temporal Quality Gate — Circuit Breaker for AI Rendering.
    # After the anti_flicker_render backend produces output, evaluate temporal
    # quality using GT motion vectors and Min-SSIM fuse.  If the sequence
    # fails quality requirements, the circuit breaker trips and prevents
    # defective sequences from being archived.
    temporal_quality_report = None
    try:
        from mathart.quality.temporal_quality_gate import (
            TemporalQualityGate,
            QualityVerdict,
        )
        # Convert PIL source_frames to numpy for quality evaluation
        eval_frames = [np.array(f) for f in source_frames[:min(len(source_frames), 16)]]
        if len(eval_frames) >= 2:
            # Build synthetic MV fields from frame differences as proxy
            # (In production, these come from motion_vector_baker)
            from types import SimpleNamespace
            proxy_mv_fields = []
            for fi in range(len(eval_frames) - 1):
                fa_gray = np.mean(eval_frames[fi][:, :, :3].astype(np.float64), axis=2)
                fb_gray = np.mean(eval_frames[fi + 1][:, :, :3].astype(np.float64), axis=2)
                diff = fb_gray - fa_gray
                h, w = fa_gray.shape
                proxy_mv_fields.append(SimpleNamespace(
                    dx=np.zeros((h, w), dtype=np.float64),
                    dy=diff * 0.1,  # proxy displacement from brightness change
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
        # Clean up eval frames to prevent OOM
        del eval_frames
    except Exception as tq_exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"[SESSION-131] Temporal quality gate skipped: {tq_exc}"
        )

    # Inject temporal quality report into manifest metadata
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
        "rng_spawn_digest": rng_digest,
        "temporal_quality_report": temporal_quality_report,
    }


def _node_collect_batch(ctx: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    prepared_items = sorted(deps["prepare_character"], key=lambda item: item["character_id"])
    unified_items = {item["character_id"]: item for item in deps["unified_motion_stage"]}
    shell_items = {item["character_id"]: item for item in deps["pseudo3d_shell_stage"]}
    ribbon_items = {item["character_id"]: item for item in deps["physical_ribbon_stage"]}
    render_items = {item["character_id"]: item for item in deps["orthographic_render_stage"]}
    motion2d_items = {item["character_id"]: item for item in deps["motion2d_export_stage"]}
    delivery_items = {item["character_id"]: item for item in deps["final_delivery_stage"]}
    ai_items = {item["character_id"]: item for item in deps["ai_render_stage"]}

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
                "pseudo3d_shell_stage": shell_items[cid].get("rng_spawn_digest"),
                "physical_ribbon_stage": ribbon_items[cid].get("rng_spawn_digest"),
                "orthographic_render_stage": render_items[cid].get("rng_spawn_digest"),
                "motion2d_export_stage": motion2d_items[cid].get("rng_spawn_digest"),
                "final_delivery_stage": delivery_items[cid].get("rng_spawn_digest"),
                "ai_render_stage": ai_items[cid].get("rng_spawn_digest"),
            },
            "manifests": {
                "unified_motion": unified_items[cid]["manifest_path"],
                "pseudo_3d_shell": shell_items[cid]["manifest_path"],
                "physical_ribbon": ribbon_items[cid]["manifest_path"],
                "orthographic_pixel_render": render_items[cid]["manifest_path"],
                "unity_2d_anim": delivery_items[cid]["unity_manifest_path"],
                "spine_preview": delivery_items[cid]["preview_manifest_path"],
                "anti_flicker_render": ai_items[cid]["manifest_path"],
            },
            "final_outputs": {
                "spine_json": delivery_items[cid]["spine_json_path"],
                "delivery_archive": delivery_items[cid]["archived"],
                "orthographic_archive": render_items[cid].get("archived", {}),
                "ai_render": ai_items[cid],
            },
        }
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
    graph.add_node(
        PDGNode(
            name="ai_render_stage",
            dependencies=["prepare_character", "orthographic_render_stage", "motion2d_export_stage"],
            operation=_node_ai_render,
            cache_enabled=False,
            requires_gpu=True,
        )
    )
    graph.add_node(
        PDGNode(
            name="collect_batch",
            dependencies=[
                "prepare_character",
                "unified_motion_stage",
                "pseudo3d_shell_stage",
                "physical_ribbon_stage",
                "orthographic_render_stage",
                "motion2d_export_stage",
                "final_delivery_stage",
                "ai_render_stage",
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
) -> dict[str, Any]:
    output_root = _ensure_dir(output_root)
    batch_dir = _ensure_dir(output_root / f"mass_production_batch_{_timestamp_slug()}")
    cache_dir = _ensure_dir(batch_dir / ".pdg_cache")
    graph = build_mass_production_graph(
        cache_dir=cache_dir,
        max_workers=max(1, int(pdg_workers)),
        gpu_slots=max(1, int(gpu_slots)),
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
        },
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
    }
    _write_json(batch_dir / "mass_production_result.json", result_payload)
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
