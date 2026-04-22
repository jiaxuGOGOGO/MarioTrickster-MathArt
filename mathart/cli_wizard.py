"""Interactive and non-interactive top-level wizard for dual-track modes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

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
    output_fn("MarioTrickster-MathArt Dual Wizard")
    output_fn("请选择当前工作模式：")
    for item in dispatcher.available_modes():
        output_fn(f"  [{item['index']}] {item['label']}")
    selection = input_fn("输入编号并回车: ").strip()
    execute_now = input_fn("是否立即执行该模式？[y/N]: ").strip().lower() in {"y", "yes"}
    source = None
    if selection in {"3", "local", "local_distill", "distill", "research"}:
        source = input_fn("若要执行本地科研蒸馏，请输入素材文件路径（可留空稍后再传参）: ").strip() or None
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


__all__ = ["build_parser", "run_wizard"]
