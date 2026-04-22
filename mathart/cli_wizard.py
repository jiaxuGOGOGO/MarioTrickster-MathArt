"""Interactive and non-interactive top-level wizard for dual-track modes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from mathart.workspace.hitl_boundary import ManualInterventionRequiredError, ManualOption
from mathart.workspace.mode_dispatcher import ModeDispatcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mathart-wizard",
        description="Top-level dual-track wizard for MarioTrickster-MathArt.",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Mode index or alias. Supported: 1/production, 2/evolution, 3/local_distill, 4/dry_run.",
    )
    parser.add_argument("--project-root", default=None, help="Project root override.")
    parser.add_argument("--output-dir", default=None, help="Output directory override.")
    parser.add_argument("--source", default=None, help="Source file used by local distillation.")
    parser.add_argument("--source-name", default=None, help="Optional source name override.")
    parser.add_argument("--target", default="texture", help="Evolution target when mode=2.")
    parser.add_argument("--preset", default="terrain", help="Evolution preset when mode=2.")
    parser.add_argument("--iterations", type=int, default=12, help="Iterations for evolution mode.")
    parser.add_argument("--population", type=int, default=8, help="Population size for evolution mode.")
    parser.add_argument("--batch-size", type=int, default=20, help="Batch size for production mode.")
    parser.add_argument("--pdg-workers", type=int, default=16, help="PDG workers for production mode.")
    parser.add_argument("--gpu-slots", type=int, default=1, help="GPU concurrency budget for production mode.")
    parser.add_argument("--seed", type=int, default=20260422, help="Deterministic seed override.")
    parser.add_argument("--skip-ai-render", action="store_true", help="Disable GPU render lane in production mode.")
    parser.add_argument("--git-push", action="store_true", help="Allow local distillation mode to push knowledge via GitOps agent.")
    parser.add_argument("--config-storage", choices=["env", "json"], default="env", help="Preferred local secret storage backend.")
    parser.add_argument("--execute", action="store_true", help="Execute the selected mode instead of previewing it.")
    return parser


def standard_menu_prompt(
    *,
    title: str,
    message: str,
    options: Iterable[ManualOption],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> str:
    normalized = tuple(options)
    if not normalized:
        raise ValueError("standard_menu_prompt requires at least one option")

    output_fn("")
    output_fn(title)
    output_fn(message)
    for index, option in enumerate(normalized, start=1):
        suffix = " [推荐]" if option.recommended else ""
        output_fn(f"  [{index}] {option.label}{suffix}")
        output_fn(f"      {option.description}")

    while True:
        raw = input_fn("请输入选项编号并回车: ").strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(normalized):
                return normalized[idx].key
        output_fn("输入无效，请输入上方菜单中的编号。")


def standard_text_prompt(
    prompt: str,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    suffix = "" if default is None else f" [默认: {default}]"
    while True:
        raw = input_fn(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        if allow_empty:
            return ""
        output_fn("输入不能为空，请重新填写。")


def render_defender_whitelist_warning(
    *,
    project_root: str | Path,
    comfyui_root: str | Path | None = None,
    output_fn: Callable[[str], None] = print,
) -> None:
    project_root = Path(project_root).resolve()
    comfy_text = str(Path(comfyui_root).resolve()) if comfyui_root else "<你的 ComfyUI 根目录>"
    output_fn("MarioTrickster-MathArt Dual Wizard")
    output_fn("安全提示：如 Windows Defender 或第三方杀毒软件误报，请手动把项目目录和 ComfyUI 目录加入白名单。")
    output_fn(f"项目目录: {project_root}")
    output_fn(f"ComfyUI 目录: {comfy_text}")
    output_fn("可复制的 PowerShell 参考命令（请由你本人确认后手动执行，而不是让程序代替执行）：")
    output_fn(f"  Add-MpPreference -ExclusionPath '{project_root}'")
    output_fn(f"  Add-MpPreference -ExclusionPath '{comfy_text}'")
    output_fn("")


def prompt_manual_intervention(
    error: ManualInterventionRequiredError,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> str:
    for line in error.guidance:
        output_fn(line)
    return standard_menu_prompt(
        title=error.title,
        message=error.message,
        options=error.options,
        input_fn=input_fn,
        output_fn=output_fn,
    )


def run_wizard(
    argv: list[str] | None = None,
    *,
    stdin_isatty: bool | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    raw_argv = [] if argv is None else list(argv)
    interactive = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty

    if not raw_argv and interactive:
        return _run_interactive(input_fn=input_fn, output_fn=output_fn)

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if args.mode is None:
        error_payload = {
            "status": "error",
            "error_type": "MissingModeSelection",
            "message": "无交互环境下必须通过 --mode 指定模式编号或别名。",
        }
        sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
        sys.stdout.flush()
        return 2

    try:
        dispatcher = ModeDispatcher(project_root=args.project_root)
        result = dispatcher.dispatch(
            args.mode,
            options=_namespace_to_options(args, interactive=False),
            execute=args.execute,
        )
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False))
        sys.stdout.flush()
        return 0
    except Exception as exc:
        error_payload = {
            "status": "error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }
        sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
        sys.stdout.flush()
        return 1


def _run_interactive(
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> int:
    project_root = Path.cwd().resolve()
    dispatcher = ModeDispatcher(project_root=project_root)
    render_defender_whitelist_warning(project_root=project_root, output_fn=output_fn)
    output_fn("请选择当前工作模式：")
    for item in dispatcher.available_modes():
        output_fn(f"  [{item['index']}] {item['label']}")
    selection = standard_text_prompt("输入编号并回车", input_fn=input_fn, output_fn=output_fn)

    # Director Studio shortcut — bypass normal dispatch for mode 5
    if selection in {"5", "director_studio", "director", "studio"}:
        return _run_director_studio(project_root=project_root, input_fn=input_fn, output_fn=output_fn)

    execute_now = (
        standard_text_prompt(
            "是否立即执行该模式？输入 y/yes 表示执行，其余为仅预览",
            input_fn=input_fn,
            output_fn=output_fn,
            default="n",
        ).strip().lower()
        in {"y", "yes"}
    )
    source = None
    if selection in {"3", "local", "local_distill", "distill", "research"}:
        source = standard_text_prompt(
            "若要执行本地科研蒸馏，请输入素材文件路径（可留空稍后再传参）",
            input_fn=input_fn,
            output_fn=output_fn,
            allow_empty=True,
        ).strip() or None
    try:
        result = dispatcher.dispatch(
            selection,
            options={
                "interactive": True,
                "project_root": str(project_root),
                "source": source,
            },
            execute=execute_now,
        )
        payload = result.to_dict()
        output_fn(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        output_fn(json.dumps({
            "status": "error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1


def _namespace_to_options(args: argparse.Namespace, *, interactive: bool) -> dict[str, Any]:
    return {
        "interactive": interactive,
        "project_root": args.project_root,
        "output_dir": args.output_dir,
        "source": args.source,
        "source_name": args.source_name,
        "target": args.target,
        "preset": args.preset,
        "iterations": args.iterations,
        "population": args.population,
        "batch_size": args.batch_size,
        "pdg_workers": args.pdg_workers,
        "gpu_slots": args.gpu_slots,
        "seed": args.seed,
        "skip_ai_render": args.skip_ai_render,
        "git_push": args.git_push,
        "config_storage": args.config_storage,
    }




def _run_director_studio(
    *,
    project_root: Path,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Run the Director Studio workflow: intent → preview REPL → blueprint → evolution."""
    from mathart.workspace.director_intent import (
        DirectorIntentParser, CreatorIntentSpec, Blueprint, BlueprintMeta, Genotype,
    )
    from mathart.quality.interactive_gate import (
        InteractivePreviewGate, GateDecision,
    )
    from mathart.evolution.blueprint_evolution import BlueprintEvolutionEngine

    output_fn("")
    output_fn("🎬 ═══════════════════════════════════════════")
    output_fn("   语义导演工坊 (Director Studio)")
    output_fn("═══════════════════════════════════════════")
    output_fn("")

    parser = DirectorIntentParser(workspace_root=project_root)

    # Step 1: Gather intent
    output_fn("请选择创作方式：")
    output_fn("  [A] 感性创世 — 用自然语言描述你想要的风格")
    output_fn("  [B] 蓝图派生 — 基于已有蓝图进行控制变量繁衍")
    output_fn("  [C] 混合模式 — 在蓝图基础上叠加感性描述")
    creation_mode = standard_text_prompt("选择模式", input_fn=input_fn, output_fn=output_fn, default="A").strip().upper()

    raw_intent: dict = {}

    if creation_mode in ("B", "C"):
        bp_path = standard_text_prompt(
            "请输入蓝图文件路径 (如 workspace/blueprints/hero_v1.yaml)",
            input_fn=input_fn, output_fn=output_fn,
        )
        raw_intent["base_blueprint"] = bp_path

        variants_str = standard_text_prompt(
            "派生变种数量 (0=不派生)",
            input_fn=input_fn, output_fn=output_fn, default="0",
        )
        raw_intent["evolve_variants"] = int(variants_str) if variants_str.isdigit() else 0

        if raw_intent["evolve_variants"] > 0:
            locks = standard_text_prompt(
                "锁定基因族 (逗号分隔, 如 physics,proportions; 留空=不锁定)",
                input_fn=input_fn, output_fn=output_fn, allow_empty=True,
            )
            raw_intent["freeze_locks"] = [x.strip() for x in locks.split(",") if x.strip()] if locks else []

    if creation_mode in ("A", "C"):
        vibe = standard_text_prompt(
            "用自然语言描述你想要的风格 (如: 活泼的跳跃, 夸张弹性)",
            input_fn=input_fn, output_fn=output_fn,
        )
        raw_intent["vibe"] = vibe

    # Parse intent
    try:
        spec = parser.parse_dict(raw_intent)
    except Exception as exc:
        output_fn(json.dumps({"status": "error", "error_type": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    output_fn("")
    output_fn("✅ 意图解析完成，进入白模预演...")

    # Step 2: Interactive preview gate
    gate = InteractivePreviewGate(
        workspace_root=project_root,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    gate_result = gate.run(spec)

    if gate_result.decision == GateDecision.ABORTED:
        output_fn("
导演工坊已退出。")
        return 0

    # Step 3: Blueprint evolution (if requested)
    if spec.evolve_variants > 0 and gate_result.final_genotype:
        output_fn(f"
🧬 开始控制变量繁衍: {spec.evolve_variants} 个变种...")
        engine = BlueprintEvolutionEngine(seed=42)
        evo_result = engine.evolve(
            parent_genotype=gate_result.final_genotype,
            num_variants=spec.evolve_variants,
            freeze_locks=spec.freeze_locks,
            parent_name="director_session",
        )
        output_fn(f"✅ 繁衍完成: {evo_result.num_variants} 个变种")
        frozen_var = sum(evo_result.frozen_param_variance.values())
        output_fn(f"   冻结参数方差总和: {frozen_var:.10f} (应为 0.0)")
        output_fn(f"   变异参数数量: {len(evo_result.mutated_param_variance)}")

        # Save offspring blueprints
        bp_dir = project_root / "workspace" / "blueprints" / "variants"
        bp_dir.mkdir(parents=True, exist_ok=True)
        for offspring in evo_result.offspring:
            child_bp = Blueprint(
                meta=BlueprintMeta(
                    name=f"variant_{offspring.variant_index}",
                    parent_blueprint=evo_result.parent_blueprint_name,
                    description=f"Auto-derived variant #{offspring.variant_index}",
                ),
                genotype=Genotype.from_dict(offspring.genotype_dict),
            )
            child_bp.save_yaml(bp_dir / f"variant_{offspring.variant_index}.yaml")
        output_fn(f"   变种蓝图已保存 → {bp_dir}")

    output_fn("
🎬 导演工坊流程完成！")
    return 0

__all__ = [
    "build_parser",
    "prompt_manual_intervention",
    "render_defender_whitelist_warning",
    "run_wizard",
    "standard_menu_prompt",
    "standard_text_prompt",
    "_run_director_studio",
]
