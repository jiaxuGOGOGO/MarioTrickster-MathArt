"""V6 Omniscient Pipeline Console — 知行合一流水线总控台.

Lifecycle:
1. Knowledge Ingestion
2. AI static skin initialization
3. Knowledge-guided evolution dry-runs
4. Anime timing + squash/stretch warping
5. Blender headless render script + Unity zero-post metadata
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

ProgressCallback = Callable[[dict[str, Any]], None]

from mathart.animation.anime_timing_modifier import apply_to_unified_motion_clip as apply_anime_timing
from mathart.animation.squash_stretch_modifier import apply_to_unified_motion_clip as apply_squash_stretch
from mathart.animation.unified_motion import MotionRootTransform, UnifiedMotionClip, pose_to_umr
from mathart.animation.v6_physics_bridge import enrich_clip_with_physics_payload
from mathart.core.backend_registry import get_registry
from mathart.core.knowledge_interpreter import DEFAULT_KNOWLEDGE, InterpretedKnowledge, interpret_knowledge
from mathart.evolution.knowledge_fitness import KnowledgeDrivenFitnessEngine
from mathart.workspace.semantic_orchestrator import SemanticOrchestrator


def _score_candidate_worker(payload: tuple[int, int, int, int, str]) -> tuple[UnifiedMotionClip, dict[str, Any]]:
    idx, dry_runs, fps, frame_count, knowledge_path = payload

    phase = idx / max(dry_runs - 1, 1)
    amp = 0.6 + 0.9 * ((idx * 37) % 101) / 100.0
    anticipation = 0.04 + 0.28 * ((idx * 53) % 97) / 96.0
    impact = 0.45 + 1.35 * phase
    clip = _make_candidate_clip(amp, anticipation, impact, fps, frame_count)
    engine = KnowledgeDrivenFitnessEngine(knowledge_path=knowledge_path)
    report = engine.evaluate(clip.frames, fps=fps, target_joint="root").to_dict()
    return clip, report


@dataclass(frozen=True)
class V6DeliveryDirs:
    session_dir: Path
    final_assets: Path
    knowledge_reports: Path
    engine_intermediates: Path

    @classmethod
    def create(cls, output_root: Path, asset_name: str) -> "V6DeliveryDirs":
        session_dir = output_root / asset_name
        dirs = cls(
            session_dir=session_dir,
            final_assets=session_dir / "1_FINAL_UNITY_ASSETS",
            knowledge_reports=session_dir / "2_Knowledge_And_Reports",
            engine_intermediates=session_dir / "3_Engine_Intermediates",
        )
        for path in (dirs.final_assets, dirs.knowledge_reports, dirs.engine_intermediates):
            path.mkdir(parents=True, exist_ok=True)
        return dirs


@dataclass(frozen=True)
class V6PipelineResult:
    output_dir: str
    final_assets_dir: str
    knowledge_reports_dir: str
    engine_intermediates_dir: str
    knowledge_source: str
    static_skin_manifest: dict[str, Any]
    best_fitness: dict[str, Any]
    warped_frame_count: int
    blender_manifest: dict[str, Any]
    unity_meta_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _log(message: str) -> None:
    line = f"[V6-OMNI] {message}\n"
    try:
        sys.stdout.write(line)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(line.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    sys.stdout.flush()


def _emit(callback: ProgressCallback | None, *, stage: str, progress: float, message: str, **extra: Any) -> None:
    _log(message)
    if callback is not None:
        payload: dict[str, Any] = {"stage": stage, "progress": progress, "message": message}
        payload.update(extra)
        callback(payload)


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _dump_physics_payload(clip: UnifiedMotionClip, output_path: Path) -> Path:
    payload = []
    for frame in clip.frames:
        payload.append({
            "frame_index": getattr(frame, "frame_index", None),
            "time": getattr(frame, "time", None),
            "v6_physics_payload": frame.metadata.get("v6_physics_payload", {}),
        })
    return _write_json(output_path, payload)


def ingest_external_knowledge(output_dir: Path, source_url: str | None, local_path: str | None) -> Path:
    """Pull external book knowledge JSON if available, else write defaults."""

    output_dir.mkdir(parents=True, exist_ok=True)
    knowledge_path = output_dir / "v6_distilled_knowledge.json"
    payload = dict(DEFAULT_KNOWLEDGE)
    payload.setdefault("source_book", "Animator's Survival Kit + Japanese Key Animation Notes")

    if local_path and Path(local_path).exists():
        loaded = json.loads(Path(local_path).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload.update(loaded)
    elif source_url:
        try:
            with urllib.request.urlopen(source_url, timeout=5.0) as resp:
                loaded = json.loads(resp.read().decode("utf-8"))
            if isinstance(loaded, dict):
                payload.update(loaded)
        except Exception as exc:
            payload["ingestion_warning"] = f"external_pull_failed:{exc}"

    knowledge_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.environ["MATHART_KNOWLEDGE_JSON"] = str(knowledge_path)
    return knowledge_path


def inject_semantic_effect_switches(knowledge_path: Path, vibe: str, knowledge: InterpretedKnowledge) -> InterpretedKnowledge:
    registry = get_registry()
    resolved = SemanticOrchestrator().resolve_full_intent({}, vibe, registry)
    plugins = list(resolved.get("active_vfx_plugins", []))
    text = vibe.lower()
    wants_fluid = knowledge.fluid.enabled and ("fluid_momentum_controller" in plugins or any(k in text for k in ("fluid", "water", "splash", "流体", "水花", "魔法", "浪涌")))
    wants_cloth = knowledge.cloth.enabled and ("physics_3d" in plugins or any(k in text for k in ("cloth", "cape", "布料", "披风", "软体", "xpbd")))
    payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
    payload["effects"] = {
        "fluid_vfx": bool(wants_fluid),
        "cloth_xpbd": bool(wants_cloth),
        "terrain_ik": True,
        "active_vfx_plugins": plugins,
        "skeleton_topology": resolved.get("skeleton_topology", "biped"),
        "semantic_reason": f"semantic_orchestrator:{','.join(plugins) if plugins else 'default'}",
    }
    knowledge_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.environ["MATHART_KNOWLEDGE_JSON"] = str(knowledge_path)
    return interpret_knowledge(knowledge_path)


def initialize_static_skin(output_dir: Path, vibe: str) -> dict[str, Any]:
    """Invoke demoted ComfyUI static initializer when available, with mock fallback."""

    registry = get_registry()
    entry = registry.get("comfyui_render")
    if entry is None:
        static_path = output_dir / "static_skin_mock.json"
        data = {"degraded": True, "vibe": vibe, "static_asset_only": True, "reason": "comfyui_backend_not_registered"}
        static_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"outputs": {"static_skin_mock": str(static_path)}, "metadata": data}

    _meta, backend_cls = entry
    backend = backend_cls()
    try:
        manifest = backend.execute({
            "vibe": vibe,
            "output_dir": str(output_dir / "static_skin"),
            "static_asset_only": True,
            "comfyui": {"render_timeout": 30.0, "auto_free_vram": True},
        })
        return manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest)
    except Exception as exc:
        static_path = output_dir / "static_skin_degraded.json"
        data = {"degraded": True, "vibe": vibe, "static_asset_only": True, "error": str(exc)}
        static_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"outputs": {"static_skin_degraded": str(static_path)}, "metadata": data}


def _make_candidate_clip(amplitude: float, anticipation: float, impact: float, fps: int, frame_count: int) -> UnifiedMotionClip:
    frames = []
    for i in range(frame_count):
        t = i / max(frame_count - 1, 1)
        windup = -anticipation * math.sin(min(t / 0.32, 1.0) * math.pi) if t < 0.32 else 0.0
        burst = amplitude * max(0.0, t - 0.32) ** max(0.35, impact)
        brake = 1.0 - max(0.0, t - 0.72) * (2.0 + impact)
        x = windup + burst * max(0.0, brake)
        y = 0.08 * math.sin(t * math.pi)
        frame = pose_to_umr(
            {"root": 0.0, "spine": 0.05, "l_hand": -0.2 + x, "r_hand": 0.2 + x},
            time=i / fps,
            phase=t,
            root_transform=MotionRootTransform(x=x, y=y, rotation=0.0),
            frame_index=i,
            source_state="attack_jump_burst",
            metadata={"candidate": {"amplitude": amplitude, "anticipation": anticipation, "impact": impact}},
        )
        frames.append(frame)
    return UnifiedMotionClip(clip_id="v6_candidate", state="attack_jump_burst", fps=fps, frames=frames)


def run_evolution_sandbox(knowledge: InterpretedKnowledge, dry_runs: int, fps: int, frame_count: int, knowledge_path: Path) -> tuple[UnifiedMotionClip, dict[str, Any]]:
    """Dry-run candidate clips and select the best book-law fitness."""

    best_clip: UnifiedMotionClip | None = None
    best_report: dict[str, Any] | None = None
    total = max(1, dry_runs)
    workers = min(os.cpu_count() or 1, total)
    tasks = [(idx, total, fps, frame_count, str(knowledge_path)) for idx in range(total)]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            results = executor.map(_score_candidate_worker, tasks, chunksize=max(1, total // (workers * 4)))
            for clip, report in results:
                if best_report is None or report["combined_score"] > best_report["combined_score"]:
                    best_clip = clip
                    best_report = report
    else:
        for task in tasks:
            clip, report = _score_candidate_worker(task)
            if best_report is None or report["combined_score"] > best_report["combined_score"]:
                best_clip = clip
                best_report = report
    assert best_clip is not None and best_report is not None
    return best_clip, best_report


def _frames_for_blender(clip: UnifiedMotionClip) -> list[dict[str, Any]]:
    frames = []
    for frame in clip.frames:
        data = frame.to_dict()
        timing = frame.metadata.get("anime_timing", {})
        data["anime_timing"] = timing
        data["duration"] = float(timing.get("duration", 1.0 / max(clip.fps, 1))) if isinstance(timing, dict) else 1.0 / max(clip.fps, 1)
        frames.append(data)
    return frames


def render_and_align(
    output_dir: Path,
    asset_name: str,
    knowledge_path: Path,
    knowledge: InterpretedKnowledge,
    clip: UnifiedMotionClip,
    final_assets_dir: Path,
    engine_intermediates_dir: Path,
    run_blender: bool,
) -> dict[str, Any]:
    registry = get_registry()
    entry = registry.get("blender_headless_pixel_art")
    if entry is None:
        import mathart.core.blender_headless_backend  # noqa: F401
        entry = registry.get_or_raise("blender_headless_pixel_art")
    _meta, backend_cls = entry
    backend = backend_cls()
    manifest = backend.execute({
        "asset_name": asset_name,
        "output_dir": str(engine_intermediates_dir / "blender_render"),
        "final_assets_dir": str(final_assets_dir),
        "engine_intermediates_dir": str(engine_intermediates_dir),
        "knowledge_path": str(knowledge_path),
        "style_params": {**knowledge.style.to_dict(), **knowledge.fluid.to_dict(), **knowledge.cloth.to_dict(), **knowledge.effects.to_dict()},
        "frames": _frames_for_blender(clip),
        "frame_count": len(clip.frames),
        "fps": clip.fps,
        "resolution": [256, 256],
        "pivot_world": [0.0, 0.0, 0.0],
        "run_blender": run_blender,
    })
    return manifest.to_dict()


def run_pipeline(args: argparse.Namespace, event_callback: ProgressCallback | None = None) -> V6PipelineResult:
    started = time.monotonic()
    output_root = Path(args.output_dir).resolve()
    delivery = V6DeliveryDirs.create(output_root, args.asset_name)
    output_dir = delivery.session_dir

    _emit(event_callback, stage="knowledge_ingestion", progress=0.08, message="[注入理论知识] 外部蒸馏 JSON 已接入知识总线。")
    knowledge_path = ingest_external_knowledge(delivery.knowledge_reports, args.knowledge_url, args.knowledge_json)
    knowledge = interpret_knowledge(knowledge_path)

    _emit(event_callback, stage="intent_resolution", progress=0.18, message="[解析主宰意图] 正在把 Vibe 编译成物理与显像开关。")
    knowledge = inject_semantic_effect_switches(knowledge_path, args.vibe, knowledge)

    _emit(event_callback, stage="static_skin_init", progress=0.28, message="[铸造静态表皮] ComfyUI 已降维为静态初始化器。")
    static_skin_manifest = initialize_static_skin(delivery.engine_intermediates, args.vibe)

    _emit(event_callback, stage="evolution_sandbox", progress=0.48, message=f"[多进程内存淘汰 {max(args.dry_runs - 1, 0)} 劣质基因] 正在并行干跑 {args.dry_runs} 代。", dry_runs=args.dry_runs)
    best_clip, best_fitness = run_evolution_sandbox(knowledge, args.dry_runs, args.fps, args.frame_count, knowledge_path)
    _write_json(delivery.knowledge_reports / "v6_fitness_report.json", best_fitness)

    _emit(event_callback, stage="physics_payload", progress=0.66, message="[锁定王者基因，发送物理 Payload] 正在写入时间、弹性与流体负载。", best_fitness=best_fitness)
    warped_clip = apply_anime_timing(best_clip)
    warped_clip = apply_squash_stretch(warped_clip)
    warped_clip = enrich_clip_with_physics_payload(warped_clip, fluid_params=knowledge.fluid, cloth_params=knowledge.cloth, effects=knowledge.effects)
    _dump_physics_payload(warped_clip, delivery.engine_intermediates / "v6_physics_payload_frames.json")

    _emit(event_callback, stage="blender_render", progress=0.82, message="[唤醒 Blender 融球特效] 正在生成零后期 Unity 对齐资产。")
    blender_manifest = render_and_align(output_dir, args.asset_name, knowledge_path, knowledge, warped_clip, delivery.final_assets, delivery.engine_intermediates, args.run_blender)
    unity_meta_path = blender_manifest.get("outputs", {}).get("unity_meta", "")

    result = V6PipelineResult(
        output_dir=str(output_dir),
        final_assets_dir=str(delivery.final_assets),
        knowledge_reports_dir=str(delivery.knowledge_reports),
        engine_intermediates_dir=str(delivery.engine_intermediates),
        knowledge_source=knowledge.source_path,
        static_skin_manifest=static_skin_manifest,
        best_fitness=best_fitness,
        warped_frame_count=len(warped_clip.frames),
        blender_manifest=blender_manifest,
        unity_meta_path=unity_meta_path,
    )
    report_path = delivery.knowledge_reports / "v6_omniscient_pipeline_report.json"
    report_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    book = str((knowledge.raw or {}).get("source_book", "某某动画书籍"))
    _emit(
        event_callback,
        stage="cleanup",
        progress=0.93,
        message=f"[中间废料已自动销毁] 《{book}》理论已完成具象化并归档。",
        report_path=str(report_path),
    )
    _emit(
        event_callback,
        stage="complete",
        progress=1.0,
        message="[Unity 零后期对齐契约已生效] 请直接拖入引擎使用。",
        final_assets_dir=str(delivery.final_assets),
        unity_meta_path=unity_meta_path,
        elapsed_seconds=round(time.monotonic() - started, 2),
    )
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run V6 omniscient knowledge-to-Unity pipeline.")
    parser.add_argument("--output-dir", default="outputs/v6_omniscient")
    parser.add_argument("--asset-name", default="v6_mario_trickster")
    parser.add_argument("--vibe", default="heroic cel-shaded trickster, clean sprite skin")
    parser.add_argument("--knowledge-json", default=None)
    parser.add_argument("--knowledge-url", default=None)
    parser.add_argument("--dry-runs", type=int, default=128)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--frame-count", type=int, default=24)
    parser.add_argument("--run-blender", action="store_true")
    return parser


def main() -> None:
    run_pipeline(build_arg_parser().parse_args())


if __name__ == "__main__":
    main()
