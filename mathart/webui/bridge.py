"""Web UI → V6 omniscient pipeline bridge."""
from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_INPUTS = _PROJECT_ROOT / "workspace" / "inputs"
_OUTPUTS_DIR = _PROJECT_ROOT / "outputs" / "v6_webui"
_PIPELINE_STAGES = {"knowledge_ingestion", "intent_resolution", "static_skin_init", "evolution_sandbox", "physics_payload", "blender_render", "cleanup", "complete"}


def _ensure_dirs() -> None:
    _WORKSPACE_INPUTS.mkdir(parents=True, exist_ok=True)
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def persist_uploaded_image(gradio_temp_path: str | None) -> str:
    if not gradio_temp_path:
        return ""
    _ensure_dirs()
    src = Path(gradio_temp_path)
    if not src.exists():
        raise FileNotFoundError(f"反幽灵路径红线: uploaded image not found: {gradio_temp_path}")
    dest = _WORKSPACE_INPUTS / f"ref_{uuid.uuid4().hex[:8]}{src.suffix}"
    shutil.copy2(str(src), str(dest))
    return str(dest)


def persist_uploaded_file(gradio_temp_path: str | None, prefix: str = "knowledge") -> str:
    if not gradio_temp_path:
        return ""
    _ensure_dirs()
    src = Path(gradio_temp_path)
    if not src.exists():
        raise FileNotFoundError(f"Uploaded file not found: {gradio_temp_path}")
    dest = _WORKSPACE_INPUTS / f"{prefix}_{uuid.uuid4().hex[:8]}{src.suffix}"
    shutil.copy2(str(src), str(dest))
    return str(dest)


def assemble_creator_intent(action_name: str | None, reference_image_path: str, force_fluid: bool, force_physics: bool, force_cloth: bool, force_particles: bool, raw_vibe: str = "") -> dict[str, Any]:
    vfx_overrides: dict[str, bool] = {}
    if force_fluid:
        vfx_overrides["force_fluid"] = True
    if force_physics:
        vfx_overrides["force_physics"] = True
    if force_cloth:
        vfx_overrides["force_cloth"] = True
    if force_particles:
        vfx_overrides["force_particles"] = True
    return {
        "action_name": action_name,
        "visual_reference_path": reference_image_path,
        "action": action_name,
        "reference_image": reference_image_path,
        "vfx_overrides": vfx_overrides,
        "raw_vibe": raw_vibe,
        "skeleton_topology": "biped",
    }


def _load_json_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Uploaded knowledge JSON must be an object")
    return data


def _merge_knowledge_sources(text: str, file_path: str | None, output_dir: Path) -> tuple[str | None, dict[str, Any]]:
    payload: dict[str, Any] = {}
    if file_path:
        payload.update(_load_json_file(file_path))
    if text and text.strip():
        text_data = json.loads(text)
        if not isinstance(text_data, dict):
            raise ValueError("Knowledge Feed JSON must be an object")
        payload.update(text_data)
    if not payload:
        return None, {}
    path = output_dir / "webui_knowledge_feed.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path), payload


def _extract_float(payload: dict[str, Any], *path: str) -> float | None:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return float(node) if isinstance(node, (int, float)) else None


def _summarize_knowledge(path: str | None, raw: dict[str, Any]) -> dict[str, Any]:
    from mathart.core.knowledge_interpreter import interpret_knowledge
    knowledge = interpret_knowledge(path)
    source = knowledge.raw or raw or {}
    return {
        "book": str(source.get("source_book", "默认 V6 动画/物理理论")),
        "source": knowledge.source_path,
        "viscosity": _extract_float(source, "fluid", "viscosity"),
        "damping": _extract_float(source, "cloth", "damping"),
        "hold_ratio": _extract_float(source, "timing", "hold_ratio"),
        "platform_spacing": _extract_float(source, "EnvironmentParams", "wfc_platform_spacing") or _extract_float(source, "environment", "wfc_platform_spacing"),
        "vertical_bias": _extract_float(source, "EnvironmentParams", "vertical_bias") or _extract_float(source, "environment", "vertical_bias"),
        "style": knowledge.style.to_dict(),
        "physics": knowledge.physics.to_dict(),
        "fluid": knowledge.fluid.to_dict(),
        "cloth": knowledge.cloth.to_dict(),
        "environment": knowledge.environment.to_dict(),
        "effects": knowledge.effects.to_dict(),
    }


def build_knowledge_card(summary: dict[str, Any]) -> str:
    if not summary:
        return '<div class="omni-card omni-card-muted"><div class="omni-card-title">等待知识接入</div><div class="omni-card-body">粘贴或上传蒸馏 JSON 后，这里会亮起主导理论卡片。</div></div>'
    chips = []
    if summary.get("viscosity") is not None:
        chips.append(f"流体粘滞度={summary['viscosity']:.2f}")
    if summary.get("damping") is not None:
        chips.append(f"布料阻尼={summary['damping']:.2f}")
    if summary.get("hold_ratio") is not None:
        chips.append(f"卡肉顿帧={summary['hold_ratio']:.2f}")
    if summary.get("platform_spacing") is not None:
        chips.append(f"????={summary['platform_spacing']:.2f}")
    if summary.get("vertical_bias") is not None:
        chips.append(f"????={summary['vertical_bias']:.2f}")
    if not chips:
        chips.append("核心参数已同化")
    chips_html = "".join(f'<span class="omni-chip">{chip}</span>' for chip in chips)
    return f'<div class="omni-card omni-card-success"><div class="omni-card-title">✅ 知识同化完成！</div><div class="omni-card-body">当前主导理论：<strong>{summary["book"]}</strong></div><div class="omni-chip-row">{chips_html}</div></div>'


def build_knowledge_report(summary: dict[str, Any]) -> str:
    return "等待 Knowledge Hub 输入..." if not summary else json.dumps(summary, ensure_ascii=False, indent=2)


class WebUIBridge:
    PRODUCTION_TIMEOUT_SECONDS = 120.0

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or _PROJECT_ROOT
        self.outputs_dir = self.project_root / "outputs" / "v6_webui"
        _ensure_dirs()

    def get_available_actions(self) -> list[str]:
        return ["idle", "walk", "run", "dash", "jump", "attack", "spellcast"]

    def dispatch_render(self, action_name: str, reference_image: str | None, force_fluid: bool, force_physics: bool, force_cloth: bool, force_particles: bool, raw_vibe: str = "", knowledge_feed: str = "", knowledge_file: str | None = None, dry_runs: int = 128) -> Generator[dict[str, Any], None, None]:
        yield self._ui_event("init", 0.0, "[系统待命] Omniscient Pipeline 已连接。", {}, None, [])
        try:
            persisted_ref = persist_uploaded_image(reference_image)
            persisted_knowledge = persist_uploaded_file(knowledge_file, prefix="knowledge") if knowledge_file else ""
            output_dir = self.outputs_dir / f"run_{uuid.uuid4().hex[:8]}"
            output_dir.mkdir(parents=True, exist_ok=True)
            knowledge_path, raw_knowledge = _merge_knowledge_sources(knowledge_feed, persisted_knowledge or None, output_dir)
            knowledge_summary = _summarize_knowledge(knowledge_path, raw_knowledge) if knowledge_path else {}
            yield self._ui_event("knowledge_feed", 0.12, "[知识大脑进食槽] 理论 JSON 已被解析并接入。", knowledge_summary, None, [], "知识已同化")

            intent = assemble_creator_intent(action_name or "", persisted_ref, force_fluid, force_physics, force_cloth, force_particles, raw_vibe)
            spec = {"action_name": action_name or "", "_visual_reference_path": persisted_ref, "knowledge_path": knowledge_path, "output_dir": str(output_dir), "dry_runs": int(max(1, int(dry_runs))), "vibe": self._compose_vibe(intent)}
            yield self._ui_event("pipeline_dispatch", 0.2, "[静默路由] UI 仅作为触发器，已接管至 run_v6_omniscient_pipeline.py。", knowledge_summary, None, [], "路由中")

            events: queue.Queue[dict[str, Any]] = queue.Queue()
            terminal_messages: list[str] = []
            result_holder: dict[str, Any] = {}
            error_holder: dict[str, BaseException] = {}

            def _callback(event: dict[str, Any]) -> None:
                events.put(event)

            def _worker() -> None:
                try:
                    result_holder["result"] = self._execute_pipeline(intent, spec, event_callback=_callback)
                except BaseException as exc:
                    error_holder["error"] = exc
                finally:
                    events.put({"stage": "__thread_done__"})

            thread = threading.Thread(target=_worker, daemon=True)
            thread.start()
            thread.join(timeout=self.PRODUCTION_TIMEOUT_SECONDS)
            if thread.is_alive():
                yield self._ui_event("complete", 1.0, "pipeline timed out", knowledge_summary, None, [], "超时")
                return

            while True:
                event = events.get()
                if event.get("stage") == "__thread_done__":
                    break
                terminal_messages.append(event.get("message", ""))
                payload = self._ui_event(event.get("stage", "progress"), float(event.get("progress", 0.0)), "\n".join(terminal_messages[-12:]), knowledge_summary, None, [], "生命孵化中")
                if event.get("stage") not in _PIPELINE_STAGES:
                    payload["production_event"] = event
                yield payload

            if "error" in error_holder:
                raise error_holder["error"]

            result = result_holder["result"]
            result_obj = result if hasattr(result, "final_assets_dir") else None
            final_assets_dir = Path(result_obj.final_assets_dir) if result_obj else Path(str(result.get("batch_dir", output_dir)))
            knowledge_reports_dir = Path(result_obj.knowledge_reports_dir) if result_obj else output_dir
            final_summary = _summarize_knowledge(str(knowledge_reports_dir / "v6_distilled_knowledge.json"), raw_knowledge) if result_obj else knowledge_summary
            gallery = self._collect_output_gallery([final_assets_dir])
            yield self._ui_event("complete", 1.0, "\n".join(terminal_messages[-12:]), final_summary, gallery[0] if gallery else None, gallery, "绿灯常亮")
        except Exception as exc:
            logger.exception("V6 WebUI dispatch failed")
            payload = self._ui_event("error", 1.0, str(exc), {}, None, [], "错误")
            payload["error"] = str(exc)
            yield payload

    def _ui_event(self, stage: str, progress: float, message: str, knowledge_summary: dict[str, Any], hero_asset: str | None, gallery: list[str], status_light: str = "待机") -> dict[str, Any]:
        return {"stage": stage, "progress": progress, "message": message, "gallery": gallery, "video": None, "error": None, "knowledge_summary": build_knowledge_report(knowledge_summary), "knowledge_card": build_knowledge_card(knowledge_summary), "hero_asset": hero_asset, "status_light": status_light}

    def _build_production_options(self, intent: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        action_name = spec.get("action_name") or intent.get("action_name") or ""
        return {"batch_size": 1, "pdg_workers": 1, "action_filter": [action_name] if action_name else [], "comfyui_url": "http://127.0.0.1:8188", "output_dir": str(self.project_root / "output" / "production")}

    def _execute_pipeline(self, intent: dict[str, Any], spec: dict[str, Any], event_callback=None) -> Any:
        _ = intent
        from mathart.workspace.run_v6_omniscient_pipeline import build_arg_parser, run_pipeline
        argv = ["--output-dir", spec["output_dir"], "--vibe", spec["vibe"], "--dry-runs", str(spec["dry_runs"]), "--frame-count", "12"]
        if spec.get("knowledge_path"):
            argv.extend(["--knowledge-json", spec["knowledge_path"]])
        return run_pipeline(build_arg_parser().parse_args(argv), event_callback=event_callback)

    def _compose_vibe(self, intent: dict[str, Any]) -> str:
        parts = [intent.get("raw_vibe") or ""]
        if intent.get("action_name"):
            parts.append(str(intent["action_name"]))
        if intent.get("visual_reference_path"):
            parts.append(f"reference image: {intent['visual_reference_path']}")
        overrides = intent.get("vfx_overrides", {})
        if overrides.get("force_fluid"):
            parts.append("fluid splash magic water")
        if overrides.get("force_physics"):
            parts.append("xpbd physics softbody")
        if overrides.get("force_cloth"):
            parts.append("cloth cape fabric")
        if overrides.get("force_particles"):
            parts.append("particles sparkle")
        return ", ".join(part for part in parts if part)

    def _collect_output_gallery(self, roots: list[Path]) -> list[str]:
        files: list[str] = []
        for root in roots:
            if root and root.exists():
                for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                    files.extend(str(p) for p in sorted(root.rglob(ext)))
        seen: set[str] = set()
        ordered: list[str] = []
        for path in files:
            if path not in seen:
                seen.add(path)
                ordered.append(path)
        return ordered[-50:]
