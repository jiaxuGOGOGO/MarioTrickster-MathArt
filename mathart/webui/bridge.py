"""Web UI → V6 omniscient pipeline bridge."""
from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_INPUTS = _PROJECT_ROOT / "workspace" / "inputs"
_OUTPUTS_DIR = _PROJECT_ROOT / "outputs" / "v6_webui"


def _ensure_dirs() -> None:
    _WORKSPACE_INPUTS.mkdir(parents=True, exist_ok=True)
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def persist_uploaded_image(gradio_temp_path: str | None) -> str:
    if not gradio_temp_path:
        return ""
    _ensure_dirs()
    src = Path(gradio_temp_path)
    if not src.exists():
        raise FileNotFoundError(f"Uploaded image not found: {gradio_temp_path}")
    dest = _WORKSPACE_INPUTS / f"ref_{uuid.uuid4().hex[:8]}{src.suffix}"
    shutil.copy2(str(src), str(dest))
    return str(dest)


def _knowledge_from_text(text: str, output_dir: Path) -> tuple[str | None, dict[str, Any]]:
    if not text or not text.strip():
        return None, {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Knowledge Feed JSON must be an object")
    path = output_dir / "webui_knowledge_feed.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path), data


def _summarize_knowledge(path: str | None, raw: dict[str, Any]) -> str:
    from mathart.core.knowledge_interpreter import interpret_knowledge

    knowledge = interpret_knowledge(path)
    book = str((knowledge.raw or raw or {}).get("source_book", "默认 V6 动画/物理理论"))
    return json.dumps(
        {
            "reading": book,
            "source": knowledge.source_path,
            "style_interference": knowledge.style.to_dict(),
            "physics_weights": knowledge.physics.to_dict(),
            "fluid_tension": knowledge.fluid.to_dict(),
            "cloth_tension": knowledge.cloth.to_dict(),
            "effect_switches": knowledge.effects.to_dict(),
        },
        ensure_ascii=False,
        indent=2,
    )


class WebUIBridge:
    """Thin V6-only bridge for Gradio."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or _PROJECT_ROOT
        self.outputs_dir = self.project_root / "outputs" / "v6_webui"
        _ensure_dirs()

    def get_available_actions(self) -> list[str]:
        return ["idle", "walk", "run", "dash", "jump", "attack", "spellcast"]

    def dispatch_render(
        self,
        action_name: str,
        reference_image: str | None,
        force_fluid: bool,
        force_physics: bool,
        force_cloth: bool,
        force_particles: bool,
        raw_vibe: str = "",
        knowledge_feed: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        yield {"stage": "init", "progress": 0.0, "message": "初始化 V6 生命管线...", "gallery": [], "video": None, "error": None, "knowledge_summary": ""}
        try:
            persisted_ref = persist_uploaded_image(reference_image)
            output_dir = self.outputs_dir / f"run_{uuid.uuid4().hex[:8]}"
            output_dir.mkdir(parents=True, exist_ok=True)
            knowledge_path, raw_knowledge = _knowledge_from_text(knowledge_feed, output_dir)
            knowledge_summary = _summarize_knowledge(knowledge_path, raw_knowledge)
            yield {"stage": "knowledge_feed", "progress": 0.18, "message": "知识进食区已接入，正在研读理论参数...", "gallery": [], "video": None, "error": None, "knowledge_summary": knowledge_summary}

            vibe_parts = [raw_vibe or ""]
            if action_name:
                vibe_parts.append(action_name)
            if persisted_ref:
                vibe_parts.append(f"reference image: {persisted_ref}")
            if force_fluid:
                vibe_parts.append("fluid splash magic water")
            if force_physics:
                vibe_parts.append("xpbd physics softbody")
            if force_cloth:
                vibe_parts.append("cloth cape fabric")
            if force_particles:
                vibe_parts.append("particles sparkle")
            vibe = ", ".join(p for p in vibe_parts if p)

            yield {"stage": "v6_dispatch", "progress": 0.42, "message": "所有前端事件已重定向至 run_v6_omniscient_pipeline.py", "gallery": [], "video": None, "error": None, "knowledge_summary": knowledge_summary}

            from mathart.workspace.run_v6_omniscient_pipeline import build_arg_parser, run_pipeline
            argv = ["--output-dir", str(output_dir), "--vibe", vibe, "--dry-runs", "8", "--frame-count", "12"]
            if knowledge_path:
                argv.extend(["--knowledge-json", knowledge_path])
            result = run_pipeline(build_arg_parser().parse_args(argv))
            roots = [Path(result.final_assets_dir)]
            gallery = self._collect_output_gallery(roots)
            yield {
                "stage": "complete",
                "progress": 1.0,
                "message": f"V6 数字生命资产生成完成。Unity Meta: {result.unity_meta_path}",
                "gallery": gallery,
                "video": None,
                "error": None,
                "knowledge_summary": _summarize_knowledge(str(Path(result.knowledge_reports_dir) / "v6_distilled_knowledge.json"), raw_knowledge),
            }
        except Exception as exc:
            logger.exception("V6 WebUI dispatch failed")
            yield {"stage": "error", "progress": 1.0, "message": str(exc), "gallery": [], "video": None, "error": str(exc), "knowledge_summary": ""}

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
