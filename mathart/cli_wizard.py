"""Interactive and non-interactive top-level wizard for dual-track modes.

SESSION-153 (P0-SESSION-150-UX-DOCS-SYNC) upgrade — "Golden Handoff" UX:

The wizard now guarantees three UX contracts on top of the existing SESSION-146
blackbox + SESSION-147 rescue + SESSION-148 encoding shield + SESSION-149/150
quality circuit boundary:

1. **Global ``while True`` main loop**: The top-level interactive shell never
   returns to a dead terminal after a sub-flow finishes.  Every sub-flow
   (production / evolution / local distill / dry-run / director studio) now
   yields control back to the main menu.  A dedicated ``[0] 退出系统`` option
   is the *only* way to leave the shell.

2. **Director Studio "Golden Handoff" menu**: After the preview REPL approves
   a genotype (``GateDecision.APPROVED`` / ``BLUEPRINT_SAVED``) the wizard
   intercepts the original "direct return" and offers three smooth handoffs:
       [1] 🚀 趁热打铁：立刻将当前参数发往后台 ComfyUI 渲染最终大片！
       [2] 🔍 真理查账：打印【全链路知识血统溯源审计表】
       [0] 🏠 暂存并退回主菜单
   Option [1] physically reuses the approved ``final_genotype`` in-memory,
   threading it straight into ``ProductionStrategy`` without asking the user
   to re-enter any parameters.  Option [2] invokes
   ``ProvenanceAuditBackend.execute(intent_spec=..., knowledge_bus=...)``
   with the exact same in-memory intent/bus, printing the Knowledge Lineage
   audit table to the terminal.

3. **ComfyUI pre-flight warning**: Before any action that will talk to the
   ComfyUI HTTP API (option [1] above, plus the top-level production mode)
   the wizard MUST block and display a RED highlighted banner so the user
   is not left wondering why the terminal looks frozen.  The banner text is
   kept in ``COMFYUI_PREFLIGHT_WARNING`` and is mirrored verbatim in
   ``docs/USER_GUIDE.md`` (Docs-as-Code contract).

Hard-red lines honoured by this revision:
- NEVER modifies low-level render / mutation / audit business logic.
- NEVER removes the SESSION-147 ComfyUI rescue path or SESSION-148 encoding
  shield; both still fire first.
- NEVER swallows PipelineQualityCircuitBreak silently — the SESSION-150 red
  notice remains the canonical break boundary, only wrapped in ``continue``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# SESSION-148: Global stdout/stderr safe-encoding shield.
#
# Windows CMD / PowerShell defaults its console code page to GBK (cp936) /
# cp437, which cannot encode high-range Emoji (U+1F300+) or many CJK-adjacent
# glyphs that appear in wizard output.  When Python flushes such a string
# through ``sys.stdout`` on those terminals the interpreter raises
#   UnicodeEncodeError: 'utf-8' codec can't encode characters in
#   position 2-3: surrogates not allowed
# which tears down the entire wizard process mid-flow — including the
# ComfyUI interactive rescue gateway.  This guard force-reconfigures both
# standard streams to UTF-8 and, critically, swaps the error handler to
# ``replace`` so any unencodable glyph degrades to ``?`` instead of
# aborting the process.  Kept tightly defensive: the import happens
# before any other wizard code writes to stdout, and any exception during
# reconfiguration is swallowed so headless / redirected streams (unit
# tests, CI) are never destabilised.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Never allow the shield to become a new failure mode.
        pass

from mathart.workspace.hitl_boundary import ManualInterventionRequiredError, ManualOption
from mathart.workspace.mode_dispatcher import ModeDispatcher, PipelineQualityCircuitBreak

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SESSION-150: Enhanced Graceful Error Boundary for Pipeline Quality Circuit
# Breakers — upgraded from SESSION-149 with RED ANSI highlighting and
# improved user-facing messaging.
# ---------------------------------------------------------------------------
_QUALITY_CIRCUIT_BREAK_NOTICE = (
    "\n\033[1;31m[!] 质量防线拦截：渲染管线检测到动画序列波动不足，"
    "为保护下游 GPU 算力，任务已安全中止。\033[0m\n"
    "    \033[90m* 完整堆栈已落盘至 logs/mathart.log 黑匣子。\033[0m\n"
    "    \033[90m* 请检查上游动画输入（骨骼动画 / 帧间位移）或调整意图参数后重试。\033[0m\n"
)


# ---------------------------------------------------------------------------
# SESSION-153: ComfyUI Pre-flight Warning banner (Docs-as-Code contract).
#
# This exact string is mirrored verbatim inside docs/USER_GUIDE.md.  Any
# edit to the wording MUST be reflected in the whitepaper within the same
# commit; the guide's "黄金连招" section assumes 100% textual parity.
# ---------------------------------------------------------------------------
COMFYUI_PREFLIGHT_WARNING = (
    "\n\033[1;33m[🚨 提示] 即将呼叫显卡渲染！请确保您的 ComfyUI 服务端"
    "已在后台启动并就绪。\033[0m\n"
    "    \033[90m* 默认地址：http://localhost:8188\033[0m\n"
    "    \033[90m* 若尚未启动，请另开一个终端运行 `python main.py` "
    "再回到本窗口继续。\033[0m\n"
)


# ---------------------------------------------------------------------------
# SESSION-153: Golden Handoff menu text (Docs-as-Code contract).
#
# These three option labels are the SINGLE SOURCE OF TRUTH.  They are
# mirrored character-for-character in docs/USER_GUIDE.md §5 "黄金连招".
# If you change one, you MUST change the other.
# ---------------------------------------------------------------------------
GOLDEN_HANDOFF_TITLE = "🎬 导演工坊预演通过 — 黄金连招"
GOLDEN_HANDOFF_PROMPT = "白模已获批，请选择下一步："
GOLDEN_HANDOFF_OPTION_PRODUCE = (
    "[1] 🚀 趁热打铁：立刻将当前参数发往后台 ComfyUI 渲染最终大片！"
)
GOLDEN_HANDOFF_OPTION_AUDIT = (
    "[2] 🔍 真理查账：打印【全链路知识血统溯源审计表】"
)
GOLDEN_HANDOFF_OPTION_HOME = (
    "[0] 🏠 暂存并退回主菜单"
)


def _render_quality_circuit_break(
    exc: PipelineQualityCircuitBreak,
    *,
    output_fn: Callable[[str], None],
    selection: str | None = None,
) -> None:
    """Print a friendly notice for a quality circuit break and log into blackbox."""
    logger.error(
        "[CLI] Quality circuit break absorbed by wizard boundary "
        "(selection=%s, violation=%s): %s",
        selection,
        getattr(exc, "violation_type", "unknown"),
        getattr(exc, "detail", str(exc)),
        exc_info=True,
    )
    output_fn(_QUALITY_CIRCUIT_BREAK_NOTICE)


def emit_comfyui_preflight_warning(
    output_fn: Callable[[str], None] = print,
) -> None:
    """Emit the ComfyUI pre-flight warning banner.

    Called before any wizard action that will talk to the ComfyUI HTTP API
    (Golden Handoff option [1], top-level production mode, etc.).  This
    helper is deliberately a no-op on its own — the caller is still
    responsible for the actual dispatch — so it stays trivially testable
    and cannot accidentally suppress a render.
    """
    output_fn(COMFYUI_PREFLIGHT_WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mathart-wizard",
        description="Top-level dual-track wizard for MarioTrickster-MathArt.",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Mode index or alias. Supported: 1/production, 2/evolution, 3/local_distill, 4/dry_run, 5/director_studio.",
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
    # SESSION-146: Install blackbox at wizard entry to guarantee all
    # downstream logger.info/warning calls land in logs/mathart.log.
    try:
        from mathart.core.logger import install_blackbox
        install_blackbox()
    except Exception:  # pragma: no cover — defensive
        pass

    raw_argv = [] if argv is None else list(argv)
    interactive = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
    logger.info("[CLI] Wizard invoked: argv=%s, interactive=%s", raw_argv, interactive)

    if not raw_argv and interactive:
        return _run_interactive_shell(input_fn=input_fn, output_fn=output_fn)

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
    except PipelineQualityCircuitBreak as exc:
        logger.error(
            "[CLI] Non-interactive dispatch quality circuit break: mode=%s, violation=%s, detail=%s",
            args.mode,
            getattr(exc, "violation_type", "unknown"),
            getattr(exc, "detail", str(exc)),
            exc_info=True,
        )
        error_payload = {
            "status": "quality_circuit_break",
            "violation_type": getattr(exc, "violation_type", "unknown"),
            "detail": getattr(exc, "detail", str(exc)),
            "message": "质量防线拦截：渲染管线检测到动画序列波动不足，为保护下游 GPU 算力，任务已安全中止。",
        }
        sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
        sys.stdout.flush()
        return 3
    except Exception as exc:
        logger.warning(
            "[CLI] Non-interactive dispatch FAILED for mode=%s",
            args.mode,
            exc_info=True,
        )
        error_payload = {
            "status": "error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }
        sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
        sys.stdout.flush()
        return 1


# ---------------------------------------------------------------------------
# SESSION-153: Global interactive shell — infinite main-menu loop.
#
# This replaces the old single-shot ``_run_interactive`` entry-point with a
# ``while True`` shell.  Each iteration:
#   1. Renders the main menu with the new [0] exit option.
#   2. Dispatches to the corresponding sub-flow.
#   3. Catches ALL exceptions (quality break, ComfyUI rescue rollback,
#      KeyboardInterrupt, bare Exception) so a sub-flow failure NEVER
#      drops the user into a dead terminal.
#   4. Returns to the main menu via ``continue``.
# ---------------------------------------------------------------------------

def _print_main_menu(
    dispatcher: ModeDispatcher,
    *,
    output_fn: Callable[[str], None],
) -> None:
    output_fn("")
    output_fn("=" * 60)
    output_fn("  MarioTrickster-MathArt · 顶层交互向导主菜单")
    output_fn("=" * 60)
    output_fn("请选择当前工作模式：")
    for item in dispatcher.available_modes():
        output_fn(f"  [{item['index']}] {item['label']}")
    output_fn("  [0] 🚪 退出系统")


def _run_interactive_shell(
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> int:
    """Top-level ``while True`` main menu shell.

    Guarantees that any sub-flow (including ComfyUI rescue abort, quality
    circuit break, ValueError from dispatcher.resolve_mode, or an
    unexpected ``Exception``) bounces the user back to the main menu
    instead of exiting the terminal.

    The shell only exits when the user explicitly selects ``[0]`` or
    sends EOF (Ctrl-D / Ctrl-Z on Windows), both of which are treated as
    a clean ``return 0``.
    """
    project_root = Path.cwd().resolve()
    # SESSION-147: Inject the wizard's REPL transport so that if the
    # preflight radar blocks on comfyui_not_found, the rescue prompt
    # talks to the same terminal as the wizard.
    dispatcher = ModeDispatcher(
        project_root=project_root,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    render_defender_whitelist_warning(project_root=project_root, output_fn=output_fn)

    while True:
        try:
            _print_main_menu(dispatcher, output_fn=output_fn)
            try:
                selection = standard_text_prompt(
                    "输入编号并回车",
                    input_fn=input_fn,
                    output_fn=output_fn,
                ).strip()
            except (EOFError, KeyboardInterrupt):
                output_fn("\n检测到退出信号，正在离开向导...")
                logger.info("[CLI] Interactive shell exiting via EOF/KeyboardInterrupt")
                return 0

            logger.info("[CLI] Interactive mode selection: %s", selection)

            # --- [0] Explicit graceful exit ------------------------------
            if selection in {"0", "exit", "quit", "q"}:
                output_fn("\n已退出顶层向导。再见！")
                logger.info("[CLI] Interactive shell exiting via menu [0]")
                return 0

            # --- [5] Director Studio shortcut — Golden Handoff owner ----
            if selection in {"5", "director_studio", "director", "studio"}:
                logger.info("[CLI] Routing to Director Studio (with Golden Handoff)")
                try:
                    _run_director_studio(
                        project_root=project_root,
                        dispatcher=dispatcher,
                        input_fn=input_fn,
                        output_fn=output_fn,
                    )
                except PipelineQualityCircuitBreak as exc:
                    _render_quality_circuit_break(
                        exc, output_fn=output_fn, selection=selection,
                    )
                except Exception as exc:  # [防假死红线] catch-all
                    logger.warning(
                        "[CLI] Director Studio sub-flow FAILED", exc_info=True,
                    )
                    output_fn(json.dumps({
                        "status": "error",
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    }, ensure_ascii=False, indent=2))
                continue

            # --- [1-4] Standard dispatcher modes -------------------------
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

            # SESSION-153: Pre-flight ComfyUI warning before Production.
            # We fire the banner BEFORE dispatch so users see it even when
            # the radar immediately blocks (they still learn that ComfyUI
            # is the next required piece).  This is a UI nudge only; it
            # does NOT touch the dispatcher / radar / rescue flow.
            if execute_now and selection in {"1", "production", "prod"}:
                emit_comfyui_preflight_warning(output_fn=output_fn)

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
            except PipelineQualityCircuitBreak as exc:
                # SESSION-150: Render the friendly RED-highlighted notice and
                # bounce the operator back to the wizard main menu.
                _render_quality_circuit_break(
                    exc, output_fn=output_fn, selection=selection,
                )
            except ValueError as exc:
                # Unsupported mode value — just nudge & re-prompt.
                output_fn(f"\n[提示] 无法识别的选项：{exc}")
            except Exception as exc:  # [防假死红线] catch-all
                logger.warning(
                    "[CLI] Interactive dispatch FAILED for selection=%s",
                    selection,
                    exc_info=True,
                )
                output_fn(json.dumps({
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }, ensure_ascii=False, indent=2))
            # explicit continue not required; loop naturally iterates.
        except Exception as outer_exc:  # [防假死红线] ultimate safety net
            logger.error(
                "[CLI] Interactive shell outer guard absorbed exception",
                exc_info=True,
            )
            output_fn(
                f"\n[⚠️ 系统自愈] 主循环捕获异常 {outer_exc.__class__.__name__}，"
                "已安全返回主菜单。"
            )
            continue


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


# ---------------------------------------------------------------------------
# SESSION-153: Director Studio — Golden Handoff gateway.
# ---------------------------------------------------------------------------

def _extract_vibe_adjustments(raw_vibe: str) -> dict:
    """Reconstruct ``SEMANTIC_VIBE_MAP`` matches for honest audit tracking.

    Mirrors the logic in ``provenance_audit_backend.run_standalone_audit``
    so the interactive audit path sees exactly the same vibe_adjustments
    map that the standalone runner would.
    """
    try:
        import re
        from mathart.workspace.director_intent import SEMANTIC_VIBE_MAP
    except Exception:
        return {}
    out: dict = {}
    if not raw_vibe:
        return out
    tokens = re.split(r"[,;，；\s的]+", raw_vibe.strip().lower())
    for token in tokens:
        token = token.strip()
        if token and token in SEMANTIC_VIBE_MAP:
            out[token] = dict(SEMANTIC_VIBE_MAP[token])
    return out


def _golden_handoff_menu(
    *,
    project_root: Path,
    dispatcher: ModeDispatcher,
    spec: Any,
    final_genotype: Any,
    knowledge_bus: Any,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> None:
    """Render the post-preview Golden Handoff menu in an inner ``while True``.

    [防失忆红线] Every branch reuses the in-memory ``spec`` / ``final_genotype``
    / ``knowledge_bus`` objects — nothing is re-parsed from disk, nothing is
    lost between rounds.  The inner loop only returns when the user picks
    ``[0]`` or the audit/production branch completes and falls through.
    """
    while True:
        output_fn("")
        output_fn("─" * 60)
        output_fn(GOLDEN_HANDOFF_TITLE)
        output_fn("─" * 60)
        output_fn(GOLDEN_HANDOFF_PROMPT)
        output_fn(f"  {GOLDEN_HANDOFF_OPTION_PRODUCE}")
        output_fn(f"  {GOLDEN_HANDOFF_OPTION_AUDIT}")
        output_fn(f"  {GOLDEN_HANDOFF_OPTION_HOME}")

        try:
            choice = standard_text_prompt(
                "输入编号并回车",
                input_fn=input_fn,
                output_fn=output_fn,
                default="0",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\n检测到退出信号，黄金连招菜单已关闭。")
            return

        # --- [0] 暂存并退回主菜单 --------------------------------------
        if choice in {"0", "home", "main", "back"}:
            output_fn("已暂存当前意图至内存，返回主菜单。")
            logger.info("[CLI] Golden Handoff: user chose [0] return to main menu")
            return

        # --- [1] 趁热打铁：立刻渲染大片 --------------------------------
        if choice in {"1", "produce", "render"}:
            logger.info("[CLI] Golden Handoff: user chose [1] launch ComfyUI render")
            # Pre-flight warning BEFORE any network call.
            emit_comfyui_preflight_warning(output_fn=output_fn)
            try:
                proceed = standard_text_prompt(
                    "ComfyUI 已就绪？输入 y 继续发起渲染，其他键取消",
                    input_fn=input_fn,
                    output_fn=output_fn,
                    default="n",
                ).strip().lower() in {"y", "yes"}
            except (EOFError, KeyboardInterrupt):
                output_fn("\n已取消渲染，返回黄金连招菜单。")
                continue

            if not proceed:
                output_fn("已取消本次渲染请求，参数仍保留在内存中。")
                continue

            # [防失忆红线] Pipe the in-memory spec + genotype into
            # ProductionStrategy via dispatcher.  We deliberately set
            # ``skip_ai_render=False`` so the production lane will talk
            # to ComfyUI; the radar / rescue chain will still protect
            # the user if the server is absent.
            try:
                output_fn("\n[⏳] 正在唤醒 ProductionStrategy，请稍候...")
                result = dispatcher.dispatch(
                    "production",
                    options={
                        "interactive": True,
                        "project_root": str(project_root),
                        "skip_ai_render": False,
                        # [防失忆红线] carry the approved context so
                        # downstream factory stages can pick it up when
                        # they look at ctx.extra.director_studio_* keys.
                        "director_studio_spec": spec.to_dict() if hasattr(spec, "to_dict") else None,
                        "director_studio_flat_params": final_genotype.flat_params() if hasattr(final_genotype, "flat_params") else {},
                    },
                    execute=True,
                )
                payload = result.to_dict()
                output_fn(json.dumps(payload, ensure_ascii=False, indent=2))
            except PipelineQualityCircuitBreak as exc:
                _render_quality_circuit_break(
                    exc, output_fn=output_fn, selection="golden_handoff_produce",
                )
            except Exception as exc:
                logger.warning(
                    "[CLI] Golden Handoff production dispatch FAILED",
                    exc_info=True,
                )
                output_fn(json.dumps({
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }, ensure_ascii=False, indent=2))
            # Fall through — after the render attempt we return to the
            # handoff menu so the user can additionally print the audit
            # table, or cleanly go home.
            continue

        # --- [2] 真理查账：打印全链路审计表 ----------------------------
        if choice in {"2", "audit", "provenance"}:
            logger.info("[CLI] Golden Handoff: user chose [2] provenance audit")
            try:
                from mathart.core.provenance_audit_backend import ProvenanceAuditBackend

                output_fn("")
                output_fn("─" * 60)
                output_fn("🔍 【全链路知识血统溯源审计表】")
                output_fn("─" * 60)

                backend = ProvenanceAuditBackend(project_root=project_root)
                artifact = backend.execute(
                    knowledge_bus=knowledge_bus,
                    intent_spec=spec,
                    raw_vibe=getattr(spec, "raw_vibe", ""),
                    vibe_adjustments=_extract_vibe_adjustments(
                        getattr(spec, "raw_vibe", "")
                    ),
                    output_fn=output_fn,
                    session_id="SESSION-153-GOLDEN-HANDOFF",
                )
                output_fn("")
                output_fn(
                    f"[✓] 审计完成 — verdict={artifact.health_verdict}, "
                    f"硬编码死区={len(artifact.dead_zone_params)} 项, "
                    f"JSON 日志：{artifact.json_log_path}"
                )
            except Exception as exc:
                logger.warning(
                    "[CLI] Golden Handoff provenance audit FAILED",
                    exc_info=True,
                )
                output_fn(json.dumps({
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }, ensure_ascii=False, indent=2))
            continue

        output_fn("[提示] 请输入 1 / 2 / 0 中的一个数字。")


def _run_director_studio(
    *,
    project_root: Path,
    dispatcher: ModeDispatcher | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Run the Director Studio workflow: intent → preview REPL → Golden Handoff.

    SESSION-153 change: after the preview REPL is approved we no longer
    immediately ``return 0``.  Instead we enter ``_golden_handoff_menu``
    which offers the [1] render / [2] audit / [0] home choices, then
    falls back to the main menu.  The sub-flow is idempotent — it can
    be re-entered multiple times without re-parsing intent.
    """
    from mathart.workspace.director_intent import (
        DirectorIntentParser, CreatorIntentSpec, Blueprint, BlueprintMeta, Genotype,
    )
    from mathart.quality.interactive_gate import (
        InteractivePreviewGate, GateDecision,
    )
    from mathart.evolution.blueprint_evolution import BlueprintEvolutionEngine
    from mathart.workspace.knowledge_bus_factory import build_project_knowledge_bus

    # SESSION-153: Fall back to a local dispatcher if the caller did not
    # inject one (e.g. legacy tests that still call _run_director_studio
    # directly).  The caller-supplied dispatcher is preferred so I/O
    # channels and registered strategies stay consistent.
    if dispatcher is None:
        dispatcher = ModeDispatcher(
            project_root=project_root,
            input_fn=input_fn,
            output_fn=output_fn,
        )

    output_fn("")
    output_fn("🎬 ═══════════════════════════════════════════")
    output_fn("   语义导演工坊 (Director Studio)")
    output_fn("═══════════════════════════════════════════")
    output_fn("")

    # SESSION-147: Build the project-level knowledge bus BEFORE constructing
    # any intent-parsing primitives so the bus is hot-ready for first query.
    knowledge_bus = build_project_knowledge_bus(project_root=project_root)
    if knowledge_bus is not None:
        logger.info(
            "[CLI] Director Studio knowledge bus wired: modules=%d",
            len(getattr(knowledge_bus, "compiled_spaces", {}) or {}),
        )
    else:
        logger.warning(
            "[CLI] Director Studio knowledge bus UNAVAILABLE — falling back "
            "to heuristic-only semantic translation."
        )

    parser = DirectorIntentParser(
        workspace_root=project_root,
        knowledge_bus=knowledge_bus,
    )

    # Step 1: Gather intent
    output_fn("请选择创作方式：")
    output_fn("  [A] 感性创世 — 用自然语言描述你想要的风格")
    output_fn("  [B] 蓝图派生 — 基于已有蓝图进行控制变量繁衍")
    output_fn("  [C] 混合模式 — 在蓝图基础上叠加感性描述")
    creation_mode = standard_text_prompt(
        "选择模式", input_fn=input_fn, output_fn=output_fn, default="A",
    ).strip().upper()
    logger.info("[CLI] Director Studio creation mode: %s", creation_mode)

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
        logger.info(
            "[CLI] Director intent parsed: vibe=%s, evolve_variants=%s",
            raw_intent.get("vibe", ""), raw_intent.get("evolve_variants", 0),
        )
    except Exception as exc:
        logger.warning("[CLI] Director intent parse FAILED", exc_info=True)
        output_fn(json.dumps({
            "status": "error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1

    output_fn("")
    output_fn("✅ 意图解析完成，进入白模预演...")

    # Step 2: Interactive preview gate (reuse the same knowledge bus).
    gate = InteractivePreviewGate(
        workspace_root=project_root,
        input_fn=input_fn,
        output_fn=output_fn,
        knowledge_bus=knowledge_bus,
    )
    gate_result = gate.run(spec)

    logger.info(
        "[CLI] Director gate decision: %s (rounds=%d)",
        gate_result.decision.value,
        gate_result.total_rounds,
    )
    if gate_result.decision == GateDecision.ABORTED:
        output_fn("\n导演工坊已退出。")
        return 0

    # Step 3: Optional blueprint evolution (unchanged from SESSION-142).
    if spec.evolve_variants > 0 and gate_result.final_genotype:
        output_fn(f"\n🧬 开始控制变量繁衍: {spec.evolve_variants} 个变种...")
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

    # SESSION-153: Golden Handoff — intercept the old "direct return 0"
    # and offer the three-way handoff menu.  Only reachable on APPROVED
    # or BLUEPRINT_SAVED because ABORTED already returned above.
    if gate_result.final_genotype is not None:
        _golden_handoff_menu(
            project_root=project_root,
            dispatcher=dispatcher,
            spec=spec,
            final_genotype=gate_result.final_genotype,
            knowledge_bus=knowledge_bus,
            input_fn=input_fn,
            output_fn=output_fn,
        )

    output_fn("\n🎬 导演工坊流程完成！")
    logger.info("[CLI] Director Studio workflow completed successfully")
    return 0


# ---------------------------------------------------------------------------
# Backward-compat alias for SESSION-146/147/148 callers that imported
# ``_run_interactive``.  Keep the name working but delegate to the new
# shell so no external test or automation breaks.
# ---------------------------------------------------------------------------

def _run_interactive(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    return _run_interactive_shell(input_fn=input_fn, output_fn=output_fn)


__all__ = [
    "build_parser",
    "prompt_manual_intervention",
    "render_defender_whitelist_warning",
    "run_wizard",
    "standard_menu_prompt",
    "standard_text_prompt",
    "emit_comfyui_preflight_warning",
    "COMFYUI_PREFLIGHT_WARNING",
    "GOLDEN_HANDOFF_TITLE",
    "GOLDEN_HANDOFF_PROMPT",
    "GOLDEN_HANDOFF_OPTION_PRODUCE",
    "GOLDEN_HANDOFF_OPTION_AUDIT",
    "GOLDEN_HANDOFF_OPTION_HOME",
    "_run_director_studio",
    "_run_interactive",
    "_run_interactive_shell",
]
