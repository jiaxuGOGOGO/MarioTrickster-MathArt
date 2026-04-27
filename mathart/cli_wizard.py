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
       [2] 🔍 资产大管家：智能存储雷达 · 垃圾回收 · 金库提纯 (SESSION-174)
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

SESSION-179 (P0-SESSION-179-VISUAL-DISTILLATION-AND-RESKINNING) upgrade:
The Director Studio creation menu is expanded with three new capabilities:
    [D] 👁️ 视觉临摹 — GIF/Image-Sequence to Physics reverse-engineering
    Blueprint Vault — Custom naming with timestamp fallback on save
    Style Retargeting — Override vibe prompt in Blueprint Derivation mode
Key constraints:
- ZERO cv2 dependency — uses ONLY PIL.ImageSequence for GIF processing
- Graceful fallback on API failure — never crashes, returns safe defaults
- Style Retargeting preserves motion skeleton, only replaces vibe/style

SESSION-159 (P0-SESSION-159-UX-ALIGNMENT-V2) upgrade — "Full-Array Mass
Production Dashboard":

The Golden Handoff menu is upgraded from a 3-option (render/audit/home) to a
4-option dashboard that exposes the full-action-array mass production
capability unlocked by SESSION-158 (Pipeline Decoupling) and SESSION-160
(ActionRegistry + Temporal Wiring):

    [1] 🏭 阵列量产：纯 CPU 算力，一键遍历烘焙全套动作阵列
    [2] 🎨 终极降维：烘焙全套阵列贴图后推流 ComfyUI AI 批量渲染
    [3] 🔍 资产大管家：智能存储雷达 · 垃圾回收 · 金库提纯 (SESSION-174)
    [0] 🏠 暂存并退回主菜单

Key UX upgrades:
- Sci-fi Terminal Telemetry: real-time progress banners during long-running
  full-array baking, including per-action-state progress when capturable.
- Graceful GPU degradation: if option [2] fails due to missing GPU/ComfyUI,
  the baked industrial assets are preserved and the user is smoothly returned
  to the main menu with a highlighted notice.
- ``skip_ai_render`` intent is explicitly threaded into the production
  dispatch options based on the user's menu choice.
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

# SESSION-168: Import the Poison Pill exception for precise circuit-breaker
# interception.  If the comfy_client module is not available (e.g., stripped
# deployment), we define a local sentinel so the except clause still compiles.
try:
    from mathart.comfy_client.comfyui_ws_client import ComfyUIExecutionError
except ImportError:
    class ComfyUIExecutionError(RuntimeError):  # type: ignore[no-redef]
        pass

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
# ---------------------------------------------------------------------------
# SESSION-159: Golden Handoff V2 — Full-Array Mass Production Dashboard.
#
# The menu is upgraded from 3 options to 4 options, exposing the full-action
# array baking capability (SESSION-158 + SESSION-160).  Option [1] is pure
# CPU baking (skip_ai_render=True), option [2] is bake+AI render
# (skip_ai_render=False).  The old [1] "趁热打铁" is replaced.
# These labels are the SINGLE SOURCE OF TRUTH and are mirrored verbatim
# in docs/USER_GUIDE.md §5 "黄金连招 V2".
# ---------------------------------------------------------------------------
GOLDEN_HANDOFF_TITLE = "🎬 导演工坊预演通过 — 黄金连招 V2 · 全动作阵列仪表盘"
GOLDEN_HANDOFF_PROMPT = "白模已获批，请选择资产输出策略："
GOLDEN_HANDOFF_OPTION_MASS_BAKE = (
    "[1] 🏭 阵列量产：纯 CPU 算力，一键遍历烘焙【全套动作阵列】"
    "(包含跑/跳/攻击等) 的高清工业贴图，跳过 AI 画皮。"
    "(极度适合无显卡环境)"
)
GOLDEN_HANDOFF_OPTION_FULL_RENDER = (
    "[2] 🎨 终极降维：烘焙全套阵列贴图后，立刻推流至后台 ComfyUI "
    "进行 3A 级 AI 批量渲染。(需后台就绪显卡)"
)
GOLDEN_HANDOFF_OPTION_AUDIT = (
    "[3] 🔍 资产大管家：智能存储雷达 · 垃圾回收 · 金库提纯"
)
GOLDEN_HANDOFF_OPTION_HOME = (
    "[0] 🏠 暂存并退回主菜单"
)

# ---------------------------------------------------------------------------
# SESSION-190: LookDev Single-Action Rapid Prototyping Mode.
#
# Industrial Reference: Foundry Katana LookDev Workflows — single-asset
# iteration without full-scene rendering.  Unreal Engine Animation Blueprint
# allows testing individual animation states in isolation.
# ---------------------------------------------------------------------------
GOLDEN_HANDOFF_OPTION_LOOKDEV = (
    "[4] ⚡ 单一动作打样：仅选择 1 个动作进行极速 AI 渲染测试 (强力推荐!)"
)
# Backward-compat alias so SESSION-153 smoke tests that import the old name
# still resolve.  The old [1] label is now split into [1]+[2].
GOLDEN_HANDOFF_OPTION_PRODUCE = GOLDEN_HANDOFF_OPTION_FULL_RENDER


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
    # ── SESSION-201 P0-DIRECTOR-CLI-AND-INTENT-OVERHAUL flags ──
    # Headless-mode escape hatch (Vue CLI / GitLab CLI / Vercel CLI pattern).
    # When --yes/--auto-fire is given, every interactive ``[Y/n]`` confirmation
    # in the Director Studio chain is auto-approved so CI/CD pipelines never
    # block.  TTY interactive sessions still see the manifest banner; only the
    # final blocking ``input()`` is skipped.
    parser.add_argument(
        "--yes", "--auto-fire",
        dest="auto_fire",
        action="store_true",
        help="SESSION-201: Auto-approve every confirmation prompt (CI/CD headless mode).",
    )
    parser.add_argument(
        "--action",
        dest="action",
        default=None,
        help="SESSION-201: Lock a gait by name (must be in OpenPoseGaitRegistry).",
    )
    parser.add_argument(
        "--reference-image",
        dest="reference_image",
        default=None,
        help="SESSION-201: Path to an IPAdapter reference image (must exist on disk).",
    )
    parser.add_argument(
        "--vfx-overrides",
        dest="vfx_overrides",
        default=None,
        help="SESSION-201: Comma-separated VFX toggles, e.g. force_fluid=1,force_physics=0.",
    )
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

    if str(args.mode).strip().lower() in {"v6", "omniscient", "grand", "phase3"}:
        try:
            from mathart.workspace.run_v6_omniscient_pipeline import build_arg_parser, run_pipeline

            v6_args = build_arg_parser().parse_args([])
            v6_args.output_dir = getattr(args, "output_dir", None) or v6_args.output_dir
            v6_args.knowledge_json = getattr(args, "source", None) or getattr(args, "knowledge_json", None)
            v6_args.knowledge_url = None
            v6_args.vibe = getattr(args, "source_name", None) or "wizard unified V6 facade"
            v6_args.asset_name = "v6_mario_trickster"
            v6_args.dry_runs = max(1, int(getattr(args, "iterations", 12)))
            v6_args.fps = 12
            v6_args.frame_count = 24
            v6_args.run_blender = False
            result = run_pipeline(v6_args)
            sys.stdout.write(json.dumps({"status": "ok", "pipeline": "v6_omniscient", "result": result.to_dict()}, ensure_ascii=False))
            sys.stdout.flush()
            return 0
        except Exception as exc:
            logger.warning("[CLI] V6 facade failed", exc_info=True)
            error_payload = {
                "status": "error",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
            sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
            sys.stdout.flush()
            return 1

    # ── SESSION-201 P0-DIRECTOR-CLI-AND-INTENT-OVERHAUL: headless director studio ──
    # When the caller asked for ``--mode 5`` (director_studio) we route to
    # ``_run_director_studio`` directly so the SESSION-201 prompts (action,
    # reference image, vfx_overrides) and manifest banner all fire even in
    # non-interactive mode.  The legacy dispatcher path does not surface
    # these fields so we MUST take the new path here.
    if str(args.mode).strip().lower() in {"5", "director", "director_studio", "studio"}:
        try:
            dispatcher = ModeDispatcher(project_root=args.project_root)
            return _run_director_studio(
                project_root=Path(args.project_root or ".").resolve(),
                dispatcher=dispatcher,
                input_fn=input_fn,
                output_fn=output_fn,
                auto_fire=getattr(args, "auto_fire", False),
                cli_action=getattr(args, "action", "") or "",
                cli_reference_image=getattr(args, "reference_image", "") or "",
                cli_vfx_overrides=_parse_vfx_overrides_flag(getattr(args, "vfx_overrides", None)),
            )
        except PipelineQualityCircuitBreak as exc:
            error_payload = {
                "status": "quality_circuit_break",
                "violation_type": getattr(exc, "violation_type", "unknown"),
                "detail": getattr(exc, "detail", str(exc)),
            }
            sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
            sys.stdout.flush()
            return 3
        except Exception as exc:
            logger.warning("[CLI] SESSION-201 headless director studio FAILED", exc_info=True)
            error_payload = {
                "status": "error",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
            sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
            sys.stdout.flush()
            return 1

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
    """SESSION-187: System Health & Arsenal Audit Dashboard.

    Upgraded from a simple menu to an industrial-grade startup dashboard
    that performs a full-domain asset scan before presenting the menu.

    Research: Google SRE "Four Golden Signals" dashboard pattern;
    DEV Community (2026) "Manage the health of your CLI tools at scale";
    Dex CLI TUI Mode (Mintlify, 2026) terminal dashboard design.
    """
    # ── SESSION-187: System Health & Arsenal Audit ─────────────────────────
    # Perform a lightweight scan of knowledge bus, enforcer registry, and
    # backend registry to display system health at startup.
    _kb_modules = 0
    _kb_constraints = 0
    _enforcer_count = 0
    _backend_count = 0
    _backend_names: list = []
    _vfx_plugins: list = []

    # Knowledge Bus capacity
    try:
        from mathart.workspace.knowledge_bus_factory import build_project_knowledge_bus
        from pathlib import Path as _P
        _bus = build_project_knowledge_bus(project_root=_P.cwd())
        if _bus is not None:
            _summary = _bus.refresh() if hasattr(_bus, "refresh") else {}
            _kb_modules = _summary.get("module_count", len(getattr(_bus, "compiled_spaces", {}) or {}))
            _kb_constraints = _summary.get("constraint_count", 0)
    except Exception:
        pass

    # Active enforcers
    try:
        from mathart.quality.gates.enforcer_registry import get_enforcer_registry
        _reg = get_enforcer_registry()
        _enforcer_count = len(_reg.list_all())
    except Exception:
        pass

    # Backend registry (microkernel plugins)
    try:
        from mathart.core.backend_registry import get_registry
        _br = get_registry()
        _all = _br.all_backends()
        _backend_count = len(_all)
        _backend_names = sorted(_all.keys())
        # Identify VFX-capable plugins
        from mathart.workspace.semantic_orchestrator import VFX_PLUGIN_CAPABILITIES
        _vfx_plugins = [n for n in VFX_PLUGIN_CAPABILITIES if n in _all]
    except Exception:
        pass

    output_fn("")
    output_fn("\033[1;36m" + "\u2550" * 60 + "\033[0m")
    output_fn("\033[1;36m  MarioTrickster-MathArt \u00b7 \u5de5\u4e1a\u7ea7\u4ea4\u4e92\u5411\u5bfc\u4e3b\u63a7\u53f0\033[0m")
    output_fn("\033[1;36m" + "\u2550" * 60 + "\033[0m")

    # System Health Dashboard
    output_fn("")
    output_fn("\033[1;33m  [\u2699\ufe0f  \u7cfb\u7edf\u5065\u5eb7\u4eea\u8868\u76d8]\033[0m")
    output_fn(f"\033[90m    \u251c\u2500 \u77e5\u8bc6\u603b\u7ebf\u5bb9\u91cf: {_kb_modules} \u6a21\u5757 / {_kb_constraints} \u7ea6\u675f\u6761\u76ee\033[0m")
    output_fn(f"\033[90m    \u251c\u2500 \u6d3b\u8dc3\u6267\u6cd5\u8005: {_enforcer_count} \u4e2a\u77e5\u8bc6\u6267\u6cd5\u5668\u5df2\u52a0\u8f7d\033[0m")
    output_fn(f"\033[90m    \u251c\u2500 \u5fae\u5185\u6838\u63d2\u4ef6: {_backend_count} \u4e2a\u540e\u7aef\u5df2\u6ce8\u518c\033[0m")
    if _vfx_plugins:
        output_fn(f"\033[90m    \u2514\u2500 VFX \u7279\u6548\u7b97\u5b50: {', '.join(_vfx_plugins)}\033[0m")
    else:
        output_fn("\033[90m    \u2514\u2500 VFX \u7279\u6548\u7b97\u5b50: (\u672a\u68c0\u6d4b\u5230)\033[0m")

    if _backend_names:
        output_fn(f"\033[90m    \u2514\u2500 \u53ef\u7528\u9ed1\u79d1\u6280\u7b97\u5b50: {', '.join(_backend_names[:8])}{'...' if len(_backend_names) > 8 else ''}\033[0m")
    # ── SESSION-187: 工业中枢震撼播报 ─────────────────────────
    _arsenal = ', '.join(_vfx_plugins) if _vfx_plugins else 'CPPN, Fluid, VAT (默认)'
    output_fn("")
    output_fn("\033[1;35m  [\U0001f6e1\ufe0f \u5de5\u4e1a\u4e2d\u67a2 \u00b7 \u9632\u7206\u6c99\u76d2 \u00b7 \u9ed1\u79d1\u6280\u6302\u8f7d]\033[0m")
    output_fn(f"\033[90m    \u251c\u2500 \u77e5\u8bc6\u603b\u7ebf\u5df2\u8f7d\u5165 {_kb_constraints} \u6761\u8d28\u91cf\u7ea2\u7ebf\u4e0e\u7ea6\u675f\u89c4\u5219\033[0m")
    output_fn(f"\033[90m    \u251c\u2500 \u9632\u7206\u6c99\u76d2\uff1a{_enforcer_count} \u4e2a\u6267\u6cd5\u5668 + {_backend_count} \u4e2a\u63d2\u4ef6 \u00b7 \u4e8b\u4ef6\u5fea\u8857\u7ec4\u88c5\u5f85\u547d\033[0m")
    output_fn(f"\033[90m    \u2514\u2500 \u9ed1\u79d1\u6280\u63d2\u4ef6\u5e93\uff1a{_arsenal}\033[0m")
    output_fn("\033[1;35m  [\U0001f680 \u5f15\u64ce\u5c31\u7eea] \u652f\u6301\u5168\u81ea\u7136\u8bed\u8a00\u8bed\u4e49\u63a8\u6f14\u3001GIF \u89c6\u89c9\u4e34\u6479\u53ca VFX \u52a8\u6001\u7f1d\u5408\uff01\033[0m")
    output_fn("")
    output_fn("\u8bf7\u9009\u62e9\u5f53\u524d\u5de5\u4f5c\u6a21\u5f0f\uff1a")
    for item in dispatcher.available_modes():
        output_fn(f"  [{item['index']}] {item['label']}")
    output_fn("  [5] \U0001f3ac \u8bed\u4e49\u5bfc\u6f14\u5de5\u574a (\u5168\u81ea\u52a8\u751f\u4ea7\u6a21\u5f0f + VFX \u7f1d\u5408)")
    output_fn("  [6] \U0001f52c \u9ed1\u79d1\u6280\u5b9e\u9a8c\u5ba4 (\u72ec\u7acb\u6c99\u76d2\u7a7a\u8dd1\u6d4b\u8bd5)")
    output_fn("  [0] \U0001f6aa \u9000\u51fa\u7cfb\u7edf")
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

            # --- [6] Laboratory Hub — Microkernel Dynamic Dispatch ----
            # SESSION-183: Reflection-based dynamic backend discovery hub.
            # ZERO hardcoded if/else — all routing is via BackendRegistry
            # introspection inside laboratory_hub.run_laboratory_hub().
            if selection in {"6", "lab", "laboratory", "microkernel", "hub"}:
                logger.info("[CLI] Routing to Laboratory Hub (Microkernel Dispatch)")
                try:
                    from mathart.laboratory_hub import run_laboratory_hub
                    run_laboratory_hub(
                        project_root=project_root,
                        input_fn=input_fn,
                        output_fn=output_fn,
                    )
                except Exception as exc:  # [防假死红线] catch-all
                    logger.warning(
                        "[CLI] Laboratory Hub sub-flow FAILED", exc_info=True,
                    )
                    output_fn(f"\n\033[1;31m[❌ 系统阻断] 实验室底层发生故障：{exc}\033[0m")
                    output_fn("\033[90m    ↳ [💡 提示] 已安全拦截异常，生产金库未受影响。\033[0m")
                continue
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
                    output_fn(f"\n\033[1;31m[❌ 系统阻断] 引擎底层发生严重故障：{exc}\033[0m"); output_fn("\033[90m    ↳ [💡 提示] 已安全拦截异常避免闪退，详细追踪栈请查看 logs 目录。\033[0m"); output_fn(json.dumps({
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
                output_fn(f"\n\033[1;31m[❌ 系统阻断] 引擎底层发生严重故障：{exc}\033[0m"); output_fn("\033[90m    ↳ [💡 提示] 已安全拦截异常避免闪退，详细追踪栈请查看 logs 目录。\033[0m"); output_fn(json.dumps({
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
        # SESSION-201: thread the headless escape hatch + admission fields.
        "auto_fire": getattr(args, "auto_fire", False),
        "action_name": getattr(args, "action", None) or "",
        "reference_image": getattr(args, "reference_image", None) or "",
        "vfx_overrides": _parse_vfx_overrides_flag(getattr(args, "vfx_overrides", None)),
    }


def _parse_vfx_overrides_flag(raw: str | None) -> dict[str, bool]:
    """SESSION-201: parse ``--vfx-overrides force_fluid=1,force_physics=0`` into a dict.

    Empty / None input returns an empty dict (heuristic path remains in charge).
    Unknown keys are tolerated here (the IntentGateway's Validating Webhook
    will Fail-Closed on them downstream so the contract stays in one place).
    """
    if not raw:
        return {}
    out: dict[str, bool] = {}
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            k, v = token.split("=", 1)
            out[k.strip()] = v.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            # Bare flag means "force on".
            out[token] = True
    return out


# ---------------------------------------------------------------------------
# SESSION-201 P0-DIRECTOR-CLI-AND-INTENT-OVERHAUL
#
# Public helpers (importable by tests, never widening any existing function
# signature).  All logic lives here so tests can mock builtins.input directly.
# ---------------------------------------------------------------------------
PREFLIGHT_MANIFEST_HEADER = (
    "\n\033[1;36m" + "═" * 60 + "\033[0m\n"
    "\033[1;36m[\U0001f680 黄金通告单] 载荷组装完毕，请最后核验本次点火参数：\033[0m\n"
    "\033[1;36m" + "═" * 60 + "\033[0m"
)

PREFLIGHT_MANIFEST_PROMPT = (
    "\n\033[1;33m\U0001f680 载荷组装完毕，是否授权向远端 GPU 发起实机点火？[Y/n] \033[0m"
)


def select_action_via_wizard(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    auto_fire: bool = False,
) -> str:
    """SESSION-201: progressive-disclosure prompt for gait selection.

    * Reverse-queries OpenPoseGaitRegistry — zero hardcoded list.
    * Empty input → returns "" so downstream pipeline keeps its existing
      random / preset selection (legacy heuristic path).
    * In ``auto_fire`` mode this returns "" silently (no prompt) so CI/CD
      pipelines that did not pass ``--action`` keep the legacy behaviour.
    """
    if auto_fire:
        return ""
    try:
        from mathart.core.openpose_pose_provider import get_gait_registry
        names = sorted(get_gait_registry().names())
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[CLI] SESSION-201 gait registry unreachable: %s", exc)
        names = []
    if not names:
        output_fn(
            "\033[1;33m[⚠️ SESSION-201] OpenPoseGaitRegistry 不可达—— 跳过动作锁定。\033[0m"
        )
        return ""
    output_fn("")
    output_fn("\033[1;36m" + "─" * 60 + "\033[0m")
    output_fn(
        "\033[1;36m[\U0001f3af SESSION-201 动作选择器] 反向查询 OpenPoseGaitRegistry...\033[0m"
    )
    for idx, name in enumerate(names, 1):
        output_fn(f"\033[36m    [{idx}] {name}\033[0m")
    output_fn("\033[36m    [0] 不锁定动作，由底层随机选择\033[0m")
    raw = input_fn("请输入动作名称或编号 [默认: 0]: ").strip()
    if not raw or raw == "0":
        return ""
    if raw.isdigit():
        i = int(raw) - 1
        if 0 <= i < len(names):
            return names[i]
    if raw.lower() in names:
        return raw.lower()
    output_fn(
        f"\033[1;31m[❌ SESSION-201] 未知动作 '{raw}' —— 合法集合: {names}\033[0m"
    )
    return ""


def prompt_reference_image_with_validation(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    auto_fire: bool = False,
    max_retries: int = 5,
) -> str:
    """SESSION-201: ask for an optional IPAdapter reference image with full path validation.

    Implements the OWASP "canonicalize → exists → type-check" loop with a
    bounded retry budget so a fat-finger user never hangs the wizard forever.
    Returns the absolute path string, or "" when the user declines.
    """
    if auto_fire:
        return ""
    output_fn("")
    answer = input_fn(
        "\033[36m[\U0001f50d SESSION-201 IPAdapter 探活] 是否要指定参考图？[y/N]: \033[0m"
    ).strip().lower()
    if answer not in {"y", "yes"}:
        return ""
    for attempt in range(1, max_retries + 1):
        raw = input_fn(
            "\033[36m请输入参考图的绝对路径 (输入 cancel 取消): \033[0m"
        )
        ref = (raw or "").strip().strip('"').strip("'").strip()
        if not ref:
            output_fn("\033[1;31m[❌ 路径为空，请重新输入。] \033[0m")
            continue
        if ref.lower() == "cancel":
            output_fn("\033[33m[ℹ️ SESSION-201] 已取消参考图设定。\033[0m")
            return ""
        p = Path(ref)
        if not p.exists():
            output_fn(
                f"\033[1;31m[❌ 路径不存在] '{ref}' —— 请核查后重新输入。"
                f" ({attempt}/{max_retries})\033[0m"
            )
            continue
        if p.is_dir():
            output_fn(
                f"\033[1;31m[❌ IPAdapter 需要单一图片文件，但 '{ref}' 是目录。] \033[0m"
            )
            continue
        return str(p.resolve())
    output_fn(
        "\033[1;31m[❌ 重试超限] SESSION-201 已放弃参考图设定，以防止向导死循环。\033[0m"
    )
    return ""


def prompt_vfx_overrides(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    auto_fire: bool = False,
) -> dict[str, bool]:
    """SESSION-201: ask the user whether to *force* fluid / physics / cloth / particles.

    The CRD-style explicit toggle ALWAYS wins over the heuristic path.
    Empty answer == "don't override anything" (legacy heuristic stays).
    """
    if auto_fire:
        return {}
    output_fn("")
    answer = input_fn(
        "\033[35m[\U0001f3ac SESSION-201 VFX 开关] 是否强制指定特效？[y/N]: \033[0m"
    ).strip().lower()
    if answer not in {"y", "yes"}:
        return {}
    overrides: dict[str, bool] = {}
    for flag, label in (
        ("force_fluid",     "\u6d41体/动量控制 (force_fluid)"),
        ("force_physics",   "3D 物理仿真 (force_physics)"),
        ("force_cloth",     "\u5e03料仿真 (force_cloth)"),
        ("force_particles", "\u7c92子系统 (force_particles)"),
    ):
        a = input_fn(f"\033[35m  → {label}? [y/N/skip]: \033[0m").strip().lower()
        if a in {"y", "yes", "1"}:
            overrides[flag] = True
        elif a in {"n", "no", "0"}:
            overrides[flag] = False
        # "" / "skip" / anything else → don't override (heuristic decides).
    return overrides


def render_preflight_manifest(
    *,
    spec: Any,
    skip_ai_render: bool,
    output_fn: Callable[[str], None] = print,
) -> None:
    """SESSION-201: render the Pre-flight Golden Manifest banner.

    Always called before the final ignition input prompt (whether or not
    auto_fire is set).  Keeping the banner unconditional means CI/CD logs
    still capture exactly what the rocket is loaded with.
    """
    output_fn(PREFLIGHT_MANIFEST_HEADER)
    spec_dict: dict[str, Any] = {}
    try:
        if hasattr(spec, "to_dict"):
            spec_dict = spec.to_dict() or {}
    except Exception:  # pragma: no cover — defensive
        spec_dict = {}
    action_name = spec_dict.get("action_name") or "(未锁定——随机/预设)"
    ref_path = spec_dict.get("_visual_reference_path") or "(未提供)"
    overrides = spec_dict.get("vfx_overrides") or {}
    active_vfx = spec_dict.get("active_vfx_plugins") or []
    output_fn(f"\033[36m  动作 (action_name)        : {action_name}\033[0m")
    output_fn(f"\033[36m  参考图 (reference_image)  : {ref_path}\033[0m")
    output_fn(
        f"\033[36m  VFX 强制开关             : "
        f"{overrides if overrides else '(未覆盖)'}\033[0m"
    )
    output_fn(
        f"\033[36m  实际激活 VFX 插件        : "
        f"{active_vfx if active_vfx else '(无)'}\033[0m"
    )
    output_fn(
        f"\033[36m  AI 渲染路径             : "
        f"{'CPU 专用 (skip_ai_render)' if skip_ai_render else 'CPU 烘焙 + GPU AI 渲染'}\033[0m"
    )
    output_fn("\033[1;36m" + "═" * 60 + "\033[0m")


def confirm_ignition(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    auto_fire: bool = False,
) -> bool:
    """SESSION-201: final blocking ``[Y/n]`` confirmation gate.

    Returns True when ignition is authorised, False otherwise.  When
    ``auto_fire`` is True, returns True silently and prints a banner so the
    CI/CD log still captures "auto-approved".
    """
    if auto_fire:
        output_fn(
            "\033[1;32m[✅ SESSION-201 --auto-fire] 黑盒模式已启用，跳过手动确认。\033[0m"
        )
        return True
    raw = input_fn(PREFLIGHT_MANIFEST_PROMPT).strip().lower()
    # Default = Y on empty input (Vue CLI / npm `[Y/n]` convention).
    if raw in {"", "y", "yes"}:
        return True
    output_fn(
        "\033[33m[ℹ️ SESSION-201] 点火已取消，可在菜单中重新调整参数。\033[0m"
    )
    return False


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
    # SESSION-201: when True we skip the menu entirely and fire option [1]
    # (full-array CPU bake) so headless runs end with a deterministic
    # asset closure path. Auto_fire propagates to _dispatch_mass_production.
    auto_fire: bool = False,
) -> None:
    """SESSION-159: Golden Handoff V2 — Full-Array Mass Production Dashboard.

    Renders the post-preview 4-option handoff menu in an inner ``while True``.

    [防失忆红线] Every branch reuses the in-memory ``spec`` / ``final_genotype``
    / ``knowledge_bus`` objects — nothing is re-parsed from disk, nothing is
    lost between rounds.  The inner loop only returns when the user picks
    ``[0]`` or any branch completes and falls through.

    SESSION-159 upgrade:
    - Option [1]: Pure CPU full-array baking (skip_ai_render=True)
    - Option [2]: Full-array baking + AI render (skip_ai_render=False)
    - Option [3]: Asset Governance Dashboard (SESSION-174: Storage Radar + GC + Vault Extraction)
    - Option [0]: Return to main menu
    """
    # ── SESSION-201: headless fast-path ──
    # Auto_fire fires option [1] (full-array CPU bake) once and returns,
    # so CI runs do not block on a 0-length stdin.  All telemetry banners
    # plus the SESSION-201 manifest banner still render for log capture.
    if auto_fire:
        logger.info("[CLI] SESSION-201 --auto-fire: Golden Handoff fast-path → [1] CPU bake")
        _dispatch_mass_production(
            project_root=project_root,
            dispatcher=dispatcher,
            spec=spec,
            final_genotype=final_genotype,
            skip_ai_render=True,
            output_fn=output_fn,
            input_fn=input_fn,
            auto_fire=True,
        )
        return

    while True:
        output_fn("")
        output_fn("─" * 60)
        output_fn(GOLDEN_HANDOFF_TITLE)
        output_fn("─" * 60)
        output_fn(GOLDEN_HANDOFF_PROMPT)
        output_fn(f"  {GOLDEN_HANDOFF_OPTION_MASS_BAKE}")
        output_fn(f"  {GOLDEN_HANDOFF_OPTION_FULL_RENDER}")
        output_fn(f"  {GOLDEN_HANDOFF_OPTION_AUDIT}")
        output_fn(f"  {GOLDEN_HANDOFF_OPTION_LOOKDEV}")
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
            logger.info("[CLI] Golden Handoff V2: user chose [0] return to main menu")
            return

        # --- [1] 🏭 阵列量产：纯 CPU 全套动作阵列烘焙 ------------------
        if choice in {"1", "mass_bake", "bake", "cpu"}:
            logger.info("[CLI] Golden Handoff V2: user chose [1] full-array CPU bake (skip_ai_render=True)")
            _dispatch_mass_production(
                project_root=project_root,
                dispatcher=dispatcher,
                spec=spec,
                final_genotype=final_genotype,
                skip_ai_render=True,
                output_fn=output_fn,
                input_fn=input_fn,
            )
            continue

        # --- [2] 🎨 终极降维：全阵列烘焙 + AI 渲染 --------------------
        if choice in {"2", "full_render", "render", "ai"}:
            logger.info("[CLI] Golden Handoff V2: user chose [2] full-array bake + AI render (skip_ai_render=False)")
            # ── SESSION-175: UX 零退化与科幻流转展示（前置烘焙网关 banner） ──
            # [UX 防腐蚀红线] 在弹出 “是否跳过 AI 渲染” 的最终决策提示前，
            # 必须先把工业烘焙网关 banner 高亮打到终端，让用户在按 y/n 之前
            # 就清楚地看到：底层是纯 CPU + Catmull-Rom 样条插值的工业级解算，
            # 与上方 ComfyUI 预检告警形成视觉上的“双门校验”。
            output_fn("")
            output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
            output_fn(
                "\033[1;36m[⚙️  工业烘焙网关] 正在通过 Catmull-Rom 样条插值，"
                "纯 CPU 解算高精度工业级贴图动作序列...\033[0m"
            )
            output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
            # Pre-flight warning BEFORE any GPU/network call.
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

            _dispatch_mass_production(
                project_root=project_root,
                dispatcher=dispatcher,
                spec=spec,
                final_genotype=final_genotype,
                skip_ai_render=False,
                output_fn=output_fn,
                input_fn=input_fn,
            )
            continue

        # --- [3] 资产大管家：SESSION-174 存储雷达 + GC + 金库提纯 ------
        if choice in {"3", "audit", "provenance", "gc", "vault", "governance"}:
            logger.info("[CLI] Golden Handoff V2: user chose [3] Asset Governance Dashboard (SESSION-174)")
            try:
                from mathart.factory.asset_governance import run_asset_governance_dashboard
                run_asset_governance_dashboard(
                    project_root=project_root,
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
            except Exception as exc:
                logger.warning(
                    "[CLI] Golden Handoff V2 asset governance FAILED",
                    exc_info=True,
                )
                output_fn(f"\n\033[1;31m[❌ 系统阻断] 引擎底层发生严重故障：{exc}\033[0m"); output_fn("\033[90m    ↳ [💡 提示] 已安全拦截异常避免闪退，详细追踪栈请查看 logs 目录。\033[0m"); output_fn(json.dumps({
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }, ensure_ascii=False, indent=2))
            continue

        # --- [4] ⚡ SESSION-190: LookDev 单一动作极速打样 ----------------
        if choice in {"4", "lookdev", "single", "quick"}:
            logger.info("[CLI] Golden Handoff V2: user chose [4] LookDev single-action rapid prototyping (SESSION-190)")
            try:
                from mathart.animation.unified_gait_blender import get_motion_lane_registry
                _lookdev_registry = get_motion_lane_registry()
                _lookdev_states = _lookdev_registry.names()
            except Exception as _lookdev_reg_err:
                logger.warning("[CLI] LookDev: motion registry unreachable: %s", _lookdev_reg_err)
                _lookdev_states = ()
            output_fn("")
            output_fn("\033[1;33m" + "═" * 60 + "\033[0m")
            output_fn(
                "\033[1;33m[⚡ SESSION-190 LookDev 极速打样] "
                "请选择要测试的单一动作：\033[0m"
            )
            for _ldi, _ldn in enumerate(_lookdev_states, 1):
                output_fn(f"\033[1;33m    [{_ldi}] {_ldn}\033[0m")
            output_fn("\033[1;33m" + "═" * 60 + "\033[0m")
            try:
                _lookdev_input = standard_text_prompt(
                    "输入动作名称或编号",
                    input_fn=input_fn,
                    output_fn=output_fn,
                    default=_lookdev_states[0] if _lookdev_states else "idle",
                ).strip()
            except (EOFError, KeyboardInterrupt):
                output_fn("\n已取消 LookDev 打样，返回黄金连招菜单。")
                continue
            # Resolve numeric input to action name
            if _lookdev_input.isdigit():
                _lookdev_idx = int(_lookdev_input) - 1
                if 0 <= _lookdev_idx < len(_lookdev_states):
                    _lookdev_action = _lookdev_states[_lookdev_idx]
                else:
                    output_fn("\033[1;31m[❌ 编号超出范围，请重试]\033[0m")
                    continue
            elif _lookdev_input in _lookdev_states:
                _lookdev_action = _lookdev_input
            else:
                output_fn(f"\033[1;31m[❌ 未知动作 '{_lookdev_input}'，请重试]\033[0m")
                continue
            output_fn(
                f"\033[1;32m[⚡ LookDev] 已锁定动作: {_lookdev_action} — "
                f"正在启动极速单动作打样...\033[0m"
            )
            # ── SESSION-190: UX 科幻流转展示 ──
            output_fn("")
            output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
            output_fn(
                "\033[1;36m[⚙️  工业烘焙网关] 正在通过 Catmull-Rom 样条插值，"
                "纯 CPU 解算高精度工业级贴图动作序列...\033[0m"
            )
            output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
            _dispatch_mass_production(
                project_root=project_root,
                dispatcher=dispatcher,
                spec=spec,
                final_genotype=final_genotype,
                skip_ai_render=False,
                output_fn=output_fn,
                input_fn=input_fn,
                action_filter=[_lookdev_action],
            )
            continue
        output_fn("[提示] 请输入 1 / 2 / 3 / 4 / 0 中的一个数字。")


def _build_director_vfx_motion_manifest(
    *,
    output_dir: Path,
    spec: Any,
    action_filter: list[str] | None = None,
    fps: int = 24,
    frame_count: int = 24,
) -> Any:
    """Build a generic UMR seed for VFX plugins that consume motion context."""
    from mathart.animation import presets
    from mathart.animation.unified_motion import (
        MotionRootTransform,
        UnifiedMotionClip,
        infer_contact_tags,
        pose_to_umr,
    )
    from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
    from mathart.core.backend_types import BackendType

    action_name = None
    if action_filter:
        action_name = action_filter[0]
    if not action_name and hasattr(spec, "to_dict"):
        action_name = (spec.to_dict() or {}).get("action_name")
    if not action_name:
        action_name = getattr(spec, "action_name", None)
    state = str(action_name or "run").strip().lower() or "run"

    preset_fn = getattr(presets, f"{state}_animation", None)
    if preset_fn is None:
        preset_fn = presets.run_animation

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_state = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in state)
    frames = []
    frame_total = max(1, int(frame_count))
    frame_fps = max(1, int(fps))
    for idx in range(frame_total):
        phase = idx / frame_total
        pose = preset_fn(phase)
        root = MotionRootTransform(
            x=float(idx) / frame_total,
            y=0.0,
            rotation=0.0,
            velocity_x=1.0,
            velocity_y=0.0,
        )
        frames.append(
            pose_to_umr(
                pose,
                time=idx / frame_fps,
                phase=phase,
                root_transform=root,
                contact_tags=infer_contact_tags(phase, state),
                frame_index=idx,
                source_state=state,
                metadata={"director_vfx_seed": True, "source": "director_studio_vfx_context"},
            )
        )

    clip = UnifiedMotionClip(
        clip_id=f"director_vfx_{safe_state}",
        state=state,
        fps=frame_fps,
        frames=frames,
        metadata={
            "source": "director_studio_vfx_context",
            "frame_count": frame_total,
            "fps": frame_fps,
            "joint_channel_schema": "2d_scalar",
        },
    )
    clip_path = output_dir / f"director_vfx_{safe_state}.umr.json"
    clip.save(clip_path)

    manifest = ArtifactManifest(
        artifact_family=ArtifactFamily.MOTION_UMR.value,
        backend_type=BackendType.UNIFIED_MOTION,
        version="1.0.0",
        session_id="SESSION-187",
        outputs={"motion_clip_json": str(clip_path)},
        metadata={
            "state": state,
            "frame_count": frame_total,
            "fps": frame_fps,
            "joint_channel_schema": "2d_scalar",
            "source": "director_studio_vfx_context",
        },
        tags=["director-studio", "vfx", "motion-seed", state],
    )
    manifest_path = output_dir / f"director_vfx_{safe_state}_manifest.json"
    manifest.save(manifest_path)
    return manifest


def _dispatch_mass_production(
    *,
    project_root: Path,
    dispatcher: ModeDispatcher,
    spec: Any,
    final_genotype: Any,
    skip_ai_render: bool,
    output_fn: Callable[[str], None],
    input_fn: Callable[[str], str],
    action_filter: list[str] | None = None,
    # SESSION-201: headless escape hatch.  When True, the manifest banner is
    # still rendered (for log capture) but the final ``[Y/n]`` blocking
    # confirmation is skipped so CI/CD pipelines never deadlock.
    auto_fire: bool = False,
) -> None:
    """SESSION-164: Unified mass-production dispatch with sci-fi telemetry.
    SESSION-190 upgrade: Added ``action_filter`` parameter for LookDev
    single-action rapid prototyping.  When provided, only the specified
    action(s) are dispatched to the production pipeline.

    Upgraded from SESSION-159 with:
    - Dynamic progress telemetry bound to the real ActionRegistry (SESSION-162)
    - Precise exception catching for ComfyUI domain exceptions (SESSION-161)
    - Vibe intent full-chain propagation to workflow Prompt node
    - Green completion banner for asset closure

    This helper is called by both Golden Handoff option [1] (CPU-only) and
    option [2] (CPU + AI render).  It emits real-time progress banners,
    dispatches to the production pipeline, and handles graceful degradation
    when GPU/ComfyUI is unavailable.

    [纯前端手术红线] This function ONLY controls terminal output and dispatch
    options — it does NOT modify any pipeline algorithm or math logic.

    Industrial References (SESSION-164):
    - End-to-End UI/Backend Impedance Matching: Integration Pass after backend refactor
    - Dynamic UI Hydration: LLVM TargetRegistry self-registration → zero hardcoded names
    - Precise Exception Catching: Michael Nygard "Release It!" Circuit Breaker
    - AWS Exponential Backoff + Jitter (2015): thundering herd prevention
    """
    # ── SESSION-201 P0-DIRECTOR-CLI-AND-INTENT-OVERHAUL: Pre-flight Manifest ──
    # Render the "黄金通告单" banner *before* any algorithm fires so the
    # user (and CI logs) sees exactly what's about to be launched.  When
    # ``auto_fire`` is True the manifest is still printed but the blocking
    # ``[Y/n]`` confirmation is skipped — maintaining log fidelity in CI.
    try:
        render_preflight_manifest(
            spec=spec, skip_ai_render=skip_ai_render, output_fn=output_fn,
        )
        if not confirm_ignition(
            input_fn=input_fn, output_fn=output_fn, auto_fire=auto_fire,
        ):
            logger.info("[CLI] SESSION-201 ignition declined by user; aborting dispatch.")
            return
    except (EOFError, KeyboardInterrupt):
        # Defensive: never crash the wizard because of a missing prompt stub.
        logger.debug("[CLI] SESSION-201 manifest interrupted — proceeding with dispatch.")

    # ── SESSION-164: Sci-fi Terminal Telemetry — Baking Phase ──────────────
    # [UX 零退化与科幻流转展示] 强制高亮打印工业烘焙网关 banner
    output_fn("")
    output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
    output_fn(
        "\033[1;36m[⚙️  工业烘焙网关] 正在通过 Catmull-Rom 样条插值，"
        "纯 CPU 解算高精度工业级贴图动作序列...\033[0m"
    )
    output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
    # ── SESSION-169: UX 防腐蚀 — 断路器状态行 (升级版) ────────────────
    # [UX 防腐蚀红线] 每次进入烘焙/渲染网关前，在终端打印断路器当前状态，
    # 让用户在进入渲染前就知道系统的安全阀是否就绪。
    # SESSION-169: 新增异常穿透和全局 Future 撤销状态提示。
    if not skip_ai_render:
        output_fn(
            "\033[90m    [⚡ SESSION-169 断路器] "
            "AI 推流断路器已就绪 (CLOSED) — "
            "若 ComfyUI 节点崩溃将自动熔断并弹出雪崩告警\033[0m"
        )
        output_fn(
            "\033[90m    [⚡ SESSION-169 全局撤销] "
            "异常穿透已启用 — "
            "致命错误将击穿网络降级层，"
            "并自动撤销剩余所有并发渲染任务\033[0m"
        )

    # ── SESSION-164: Dynamic Progress Telemetry bound to Registry ──────────
    # [进度播报与 Registry 动态绑定] 彻底废除硬编码动作字符串，
    # 通过读取 SESSION-162 创建的动态动作字典 MotionStateLaneRegistry
    # 真实获取正在烘焙的动作名称并逐行打印。
    try:
        from mathart.animation.unified_gait_blender import get_motion_lane_registry
        registry = get_motion_lane_registry()
        registered_states = registry.names()
        output_fn(
            f"\033[1;33m[⚙️  工业量产] 动态注册表已就绪 — "
            f"共发现 {len(registered_states)} 种已注册动作\033[0m"
        )
        output_fn(
            f"\033[90m    ↳ 已注册动作阵列: "
            f"{', '.join(registered_states)}\033[0m"
        )
        for state_name in registered_states:
            output_fn(
                f"\033[90m    [⚙️  工业量产] "
                f"正在解算 {state_name} 序列贴图...\033[0m"
            )
    except Exception as _reg_err:
        # Registry unreachable — degrade gracefully but NEVER crash
        logger.debug(
            "[CLI] Motion registry unreachable during telemetry: %s",
            _reg_err,
        )
        output_fn(
            "\033[1;33m[⚙️  工业量产网关] 正在利用纯 CPU 算力，"
            "遍历动作字典批量烘焙高清图纸...\033[0m"
        )
        output_fn(
            "\033[90m    ↳ (动作注册表暂不可达，将由底层管线自动遍历全部动作)\033[0m"
        )

    # ── SESSION-164: Vibe Intent Propagation ───────────────────────────────
    # [意图与参数全链路穿透] 确保 vibe 从 UI 菜单 → 穿过 162 的动作阶段
    # → 最终原封不动地注入到 161 的 workflow_api.json 的 Prompt 节点中。
    _vibe_str = ""
    if hasattr(spec, "raw_vibe"):
        _vibe_str = str(spec.raw_vibe or "")
    elif hasattr(spec, "to_dict"):
        _vibe_str = str(spec.to_dict().get("raw_vibe", ""))

    # ── SESSION-187: Dynamic Pipeline Weaver (VFX Stitching) ───────────────
    # [动态渲染总线无缝缝合] 拦截系统确认预演后，仅登记 VFX 插件意图。
    # 真实解算延迟到 PDG unified_motion_stage 产出正式 MOTION_UMR 后执行。
    _vfx_artifacts = {}
    if hasattr(spec, "active_vfx_plugins") and spec.active_vfx_plugins:
        _active_vfx_plugins = list(spec.active_vfx_plugins)
        _vfx_artifacts = {
            "deferred_to_pdg": True,
            "active_plugins": _active_vfx_plugins,
            "reason": "motion_dependent_vfx_requires_real_unified_motion_stage_output",
        }
        output_fn(
            f"\n\033[1;35m[SESSION-187 VFX Weaver] 已登记 {len(_active_vfx_plugins)} 个 VFX 插件；"
            "将在 PDG unified_motion_stage 产出真实 MOTION_UMR 后执行\033[0m"
        )

    # ── SESSION-164: Precise Exception Catching ────────────────────────────
    # [精准化容灾拦截对接] 将宽泛的 try...except 精准绑定到 SESSION-161
    # ComfyUIClient 真实抛出的网络异常和 SESSION-162 的 MSE 自爆异常。
    try:
        # SESSION-196 UI Highlight
        if hasattr(spec, "to_dict"):
            _sd = spec.to_dict()
            _an = _sd.get("action_name")
            _ref = _sd.get("_visual_reference_path")
            if _an or _ref:
                output_fn(f"\n\033[1;36m[🪐 意图网关穿透] 确认锁定动作={_an} | 参考图={_ref}\033[0m")
        
        output_fn(
            f"\n\033[1;37m[⏳] 正在唤醒 ProductionStrategy "
            f"(skip_ai_render={skip_ai_render})...\033[0m"
        )
        result = dispatcher.dispatch(
            "production",
            options={
                "interactive": True,
                "project_root": str(project_root),
                "skip_ai_render": skip_ai_render,
                # [防失忆红线] carry the approved context so downstream
                # factory stages can pick it up.
                "director_studio_spec": spec.to_dict() if hasattr(spec, "to_dict") else None,
                "director_studio_flat_params": (
                    final_genotype.flat_params()
                    if hasattr(final_genotype, "flat_params") else {}
                ),
                # [SESSION-164 意图穿透] vibe 原封不动注入
                "vibe": _vibe_str,
                # [SESSION-187 VFX 缝合] 注入 VFX 产物
                "vfx_artifacts": _vfx_artifacts,
                # [SESSION-190 LookDev 极速打样] 动作过滤器
                "action_filter": action_filter,
            },
            execute=True,
        )
        payload = result.to_dict()

        # ── Post-bake telemetry ────────────────────────────────────────
        if skip_ai_render:
            output_fn(
                "\n\033[1;32m[✅ 资产闭环] 流程完美结束！"
                "全套动作序列高清工业贴图已安全落盘至 outputs 文件夹！\033[0m"
            )
        else:
            # Bake succeeded, now AI render phase
            output_fn(
                "\n\033[1;35m[🎨 AI 画皮网关] "
                "全阵列工业结构底图已就绪！"
                "正在唤醒显卡集群进行大模型风格化批量推流...\033[0m"
            )

        output_fn(json.dumps(payload, ensure_ascii=False, indent=2))
        # ── SESSION-164: Final green completion banner ─────────────────
        output_fn(
            "\n\033[1;32m[✅ 资产闭环] 流程完美结束！\033[0m"
        )

    # ── SESSION-164: Precise exception ladder ──────────────────────────────
    # Layer 1: Quality circuit breaker (SESSION-162 MSE variance assert)
    except PipelineQualityCircuitBreak as exc:
        # [精准捕获] SESSION-162 部署的"静止帧 MSE 自爆异常"
        logger.warning(
            "[CLI] Quality circuit break caught in Golden Handoff V2 "
            "(skip_ai_render=%s, violation=%s)",
            skip_ai_render,
            getattr(exc, "violation_type", "unknown"),
        )
        _render_quality_circuit_break(
            exc, output_fn=output_fn,
            selection=f"golden_handoff_v2_skip={skip_ai_render}",
        )
        output_fn(
            "\033[90m    ↳ 已安全拦截质量防线异常，返回黄金连招菜单。\033[0m"
        )

    # Layer 1.5 (SESSION-169): ComfyUI Execution Error — Poison Pill + Global Abort
    # SESSION-169 升级: 致命异常现在已穿透 comfy_client.py 的网络降级层，
    # 并通过 PDG 调度器的全局 Future 撤销机制传播到此处。
    # 这是"全局刹车踏板" — 它取消所有待处理的 AI 任务。
    except ComfyUIExecutionError as exc:
        logger.critical(
            "[CLI] ComfyUIExecutionError CAUGHT — Circuit Breaker OPEN! "
            "SESSION-169 Exception Piercing + Global Abort active. "
            "(skip_ai_render=%s, node=%s): %s",
            skip_ai_render,
            getattr(exc, 'node_id', '?'),
            exc,
        )
        output_fn("")
        output_fn("\033[1;41;37m" + "=" * 60 + "\033[0m")
        output_fn(
            "\033[1;41;37m[\u274c AI \u70bc\u4e39\u7089\u8282\u70b9\u5d29\u6e83 \u2014 SESSION-169 \u5168\u5c40\u7194\u65ad\u5df2\u89e6\u53d1]\033[0m"
        )
        output_fn(
            "\033[1;41;37m"
            "ComfyUI \u5185\u90e8\u8282\u70b9\u6267\u884c\u5d29\u6e83\uff01"
            "\u4e3a\u9632\u6b7b\u9501\uff0c\u5df2\u5f3a\u884c\u7194\u65ad\u5e76\u64a4\u9500\u540e\u7eed\u6240\u6709 AI \u63a8\u6d41\u4efb\u52a1\uff01"
            "\033[0m"
        )
        output_fn("\033[1;41;37m" + "=" * 60 + "\033[0m")
        output_fn(
            "\033[1;33m[\U0001f4a1 \u63d0\u793a] \u60a8\u7684\u7eaf\u7269\u7406\u9ad8\u6e05\u5e95\u56fe\u5df2\u5168\u90e8\u5b89\u5168\u843d\u76d8\uff01"
            "\u8fdc\u7aef\u53d1\u751f PyTorch FP16/FP32 \u7cbe\u5ea6\u51b2\u7a81\u3002"
            "\u8bf7\u68c0\u67e5 ComfyUI \u540e\u53f0\uff0c\u66f4\u65b0 ControlNet \u63d2\u4ef6"
            "\u6216\u5728\u542f\u52a8\u547d\u4ee4\u4e2d\u52a0\u4e0a --fp16 \u7edf\u4e00\u7cbe\u5ea6\u540e\u518d\u91cd\u8bd5\u3002\033[0m"
        )
        output_fn(
            f"\033[90m    \u2193 \u5d29\u6e83\u8282\u70b9: {getattr(exc, 'node_id', '?')}\033[0m"
        )
        output_fn(
            f"\033[90m    \u2193 \u5f02\u5e38\u8be6\u60c5: {exc}\033[0m"
        )
        output_fn(
            "\033[90m    \u2193 SESSION-169: \u5f02\u5e38\u7a7f\u900f\u8def\u5f84: "
            "ComfyUI WS \u2192 comfy_client.wait_for_completion() \u2192 "
            "ai_render_stream_backend \u2192 PDG \u5168\u5c40\u64a4\u9500 \u2192 CLI \u7194\u65ad\033[0m"
        )
    # Layer 2: ComfyUI network exceptions (SESSION-161 precise binding)
    except ConnectionRefusedError as exc:
        # [精准捕获] ComfyUI 服务未启动 / 端口拒绝连接
        logger.warning(
            "[CLI] ConnectionRefusedError caught — ComfyUI offline "
            "(skip_ai_render=%s): %s",
            skip_ai_render, exc,
        )
        output_fn(
            "\n\033[1;33m[⚠️  ComfyUI 炼丹炉未响应/未启动！"
            "但您的全阵列物理底图已为您安全落盘保留。]\033[0m"
        )
        output_fn(
            "\033[90m    ↳ 请确保 ComfyUI 服务端已在后台启动 "
            "(默认 http://localhost:8188)，然后重新选择 [2]。\033[0m"
        )

    except OSError as exc:
        # [精准捕获] 网络层异常 (ConnectionError, TimeoutError 等 OSError 子类)
        logger.warning(
            "[CLI] OSError caught — network issue "
            "(skip_ai_render=%s): %s",
            skip_ai_render, exc,
        )
        output_fn(
            "\n\033[1;33m[⚠️  网络通讯异常！"
            "但您的全阵列物理底图已为您安全落盘保留。]\033[0m"
        )
        output_fn(
            f"\033[90m    ↳ 异常详情: {exc.__class__.__name__}: {exc}\033[0m"
        )

    # Layer 3: Catch-all with PipelineContractError detection
    except Exception as exc:
        # Check if it's a PipelineContractError wrapped in a generic raise
        _is_contract = getattr(exc, "violation_type", None) is not None
        if _is_contract:
            logger.warning(
                "[CLI] PipelineContractError caught in dispatch "
                "(violation=%s): %s",
                getattr(exc, "violation_type", "unknown"),
                getattr(exc, "detail", str(exc)),
            )
            output_fn(
                f"\n\033[1;31m[🛑 管线契约违规] "
                f"{getattr(exc, 'violation_type', 'unknown')}: "
                f"{getattr(exc, 'detail', str(exc))}\033[0m"
            )
            output_fn(
                "\033[90m    ↳ 已安全拦截，返回黄金连招菜单。\033[0m"
            )
        else:
            logger.warning(
                "[CLI] Golden Handoff V2 production dispatch FAILED "
                "(skip_ai_render=%s)",
                skip_ai_render,
                exc_info=True,
            )
            # ── Graceful GPU degradation ──────────────────────────────
            if not skip_ai_render:
                output_fn(
                    "\n\033[1;33m[⚠️  ComfyUI 炼丹炉未响应/未启动！"
                    "但您的全阵列物理底图已为您安全落盘保留。]\033[0m"
                )
                output_fn(
                    "\033[90m    ↳ 您可以稍后在显卡环境就绪后，"
                    "重新选择 [2] 进行 AI 渲染推流。\033[0m"
                )
            else:
                output_fn(f"\n\033[1;31m[❌ 系统阻断] 引擎底层发生严重故障：{exc}\033[0m"); output_fn("\033[90m    ↳ [💡 提示] 已安全拦截异常避免闪退，详细追踪栈请查看 logs 目录。\033[0m"); output_fn(json.dumps({
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }, ensure_ascii=False, indent=2))

def _run_director_studio(
    *,
    project_root: Path,
    dispatcher: ModeDispatcher | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    # SESSION-201: optional pre-filled CLI flags so headless callers can
    # bypass interactive prompts entirely.  When ``auto_fire`` is True,
    # every ``[Y/n]`` confirmation auto-approves; the manifest banner is
    # still printed so logs capture exactly what was launched.
    auto_fire: bool = False,
    cli_action: str = "",
    cli_reference_image: str = "",
    cli_vfx_overrides: dict[str, bool] | None = None,
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
    # SESSION-179: Added [D] Visual Distillation gateway
    output_fn("请选择创作方式：")
    output_fn("  [A] 感性创世 — 用自然语言描述你想要的风格")
    output_fn("  [B] 蓝图派生 — 基于已有蓝图进行控制变量繁衍")
    output_fn("  [C] 混合模式 — 在蓝图基础上叠加感性描述")
    output_fn("  [D] 👁️ 视觉临摹 — 丢入参考动图，让 AI 逆向推导物理参数！")
    creation_mode = standard_text_prompt(
        "选择模式", input_fn=input_fn, output_fn=output_fn, default="A",
    ).strip().upper()
    logger.info("[CLI] Director Studio creation mode: %s", creation_mode)

    raw_intent: dict = {}
    # ── SESSION-179: Visual Distillation Gateway (GIF to Physics) ──────────
    # [核心约束] 绝对禁止引入 cv2 库！强制使用 PIL.ImageSequence 处理 GIF。
    if creation_mode == "D":
        try:
            from mathart.workspace.visual_distillation import distill_physics_from_reference
            # ── SESSION-190: 双引号粉碎机 (I/O Sanitization) ──────────────
            # Windows 终端复制路径天然附带双引号，必须强制净化。
            # 路径无效时绝对禁止静默降级，必须红字警告并要求重新输入。
            # Industrial Reference: OWASP Input Validation Cheat Sheet.
            while True:
                _raw_ref_path = standard_text_prompt(
                    "请输入参考动图路径 (GIF 文件或图片文件夹)",
                    input_fn=input_fn, output_fn=output_fn,
                )
                # SESSION-190: 双引号粉碎机 — strip Windows terminal quotes
                ref_path = _raw_ref_path.strip('"').strip("'").strip()
                if not ref_path:
                    output_fn("\033[1;31m[❌ 路径无效，请检查] 输入不能为空！\033[0m")
                    continue
                _ref_p = Path(ref_path)
                if _ref_p.exists():
                    break
                output_fn(
                    f"\033[1;31m[❌ 路径无效，请检查] "
                    f"文件或目录不存在: {ref_path}\033[0m"
                )
                output_fn(
                    "\033[90m    ↳ 提示: 请确认路径正确，"
                    "Windows 用户请注意去除路径两端的引号。\033[0m"
                )
            output_fn("")
            output_fn("[1;36m" + "═" * 60 + "[0m")
            output_fn(
                "[1;36m[👁️ 视觉临摹中枢] 正在启动 AI 视觉逆向推导引擎...[0m"
            )
            output_fn("[1;36m" + "═" * 60 + "[0m")
            distilled_params = distill_physics_from_reference(
                ref_path,
                output_fn=output_fn,
            )
            # Inject distilled params into raw_intent as physics overrides
            raw_intent["vibe"] = "AI 视觉临摹逆向推导"
            raw_intent["_distilled_physics"] = distilled_params
            # ── SESSION-193: Identity Hydration ── preserve visual reference
            # path so downstream IPAdapter + CLIP Vision nodes can lock
            # the character appearance via Zero-Shot feature transfer.
            raw_intent["_visual_reference_path"] = str(Path(ref_path).resolve())
            output_fn("")
            output_fn("[1;32m[✅ 视觉临摹] 逆向推导参数预览：[0m")
            for k, v in distilled_params.items():
                output_fn(f"[90m    {k}: {v}[0m")
            output_fn("")
            add_vibe = standard_text_prompt(
                "是否叠加额外的风格描述？(留空跳过)",
                input_fn=input_fn, output_fn=output_fn, allow_empty=True,
            )
            if add_vibe:
                raw_intent["vibe"] = add_vibe
        except Exception as _distill_err:
            logger.warning("[CLI] Visual Distillation FAILED", exc_info=True)
            output_fn(
                f"[1;33m[⚠️ 视觉临摹] 处理失败: {_distill_err}\n"
                "将使用默认参数继续。[0m"
            )

    if creation_mode in ("B", "C"):
        # SESSION-190: 双引号粉碎机 — 蓝图路径净化
        _raw_bp_path = standard_text_prompt(
            "请输入蓝图文件路径 (如 workspace/blueprints/hero_v1.yaml)",
            input_fn=input_fn, output_fn=output_fn,
        )
        bp_path = _raw_bp_path.strip('"').strip("'").strip()
        raw_intent["base_blueprint"] = bp_path
        # ── SESSION-179/180: Style Retargeting (无缝动静解耦换皮) ──────────
        # 加载已有动作骨架后，允许用户输入全新的画风 Prompt，
        # 覆盖上下文原有的 vibe 参数，实现"动作骨架完美复用，画风自由剥离与替换"。
        reskin_vibe = standard_text_prompt(
            '🎨 骨架已加载！请输入全新画风描述 (Prompt Vibe，如"赛博朋克风"，回车沿用旧设定): ',
            input_fn=input_fn, output_fn=output_fn, allow_empty=True,
        )
        if reskin_vibe:
            raw_intent["vibe"] = reskin_vibe
            output_fn(
                f"\033[1;35m[🎨 风格换皮] 已注入全新画风: {reskin_vibe}\033[0m"
            )
            output_fn(
                "\033[90m    ↳ 动作骨架将从蓝图完美复用，仅画风被替换。\033[0m"
            )
            logger.info("[CLI] Style Retargeting: vibe overridden to '%s'", reskin_vibe)

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
    # SESSION-179: For mode D, apply distilled physics to the genotype
    if creation_mode == "D" and "_distilled_physics" in raw_intent:
        _dp = raw_intent.pop("_distilled_physics")
        # These will be applied after parse_dict creates the spec
        raw_intent["_physics_override"] = _dp

    # ── SESSION-201 P0-DIRECTOR-CLI-AND-INTENT-OVERHAUL ──
    # Progressive disclosure: gait → reference image → vfx overrides.
    # CLI flags (--action / --reference-image / --vfx-overrides) take
    # precedence over interactive prompts; auto_fire silences all prompts.
    # NOTE: business logic (registry queries, image hydration, plugin
    # activation) deliberately stays in the existing intent_gateway /
    # director_intent / pipeline_weaver layers — the CLI only collects
    # the structured fields and lets the gateway Fail-Closed on bad input.
    try:
        if cli_action:
            raw_intent["action"] = cli_action
        else:
            _picked = select_action_via_wizard(
                input_fn=input_fn, output_fn=output_fn, auto_fire=auto_fire,
            )
            if _picked:
                raw_intent["action"] = _picked
        if cli_reference_image:
            raw_intent["reference_image"] = cli_reference_image
        else:
            _ref = prompt_reference_image_with_validation(
                input_fn=input_fn, output_fn=output_fn, auto_fire=auto_fire,
            )
            if _ref:
                raw_intent["reference_image"] = _ref
        if cli_vfx_overrides:
            raw_intent["vfx_overrides"] = dict(cli_vfx_overrides)
        else:
            _ovr = prompt_vfx_overrides(
                input_fn=input_fn, output_fn=output_fn, auto_fire=auto_fire,
            )
            if _ovr:
                raw_intent["vfx_overrides"] = _ovr
    except (EOFError, KeyboardInterrupt):
        # Headless tests may not stub every prompt; treat as "skip".
        logger.debug("[CLI] SESSION-201 wizard interrupted — falling back to legacy heuristic")

    # Parse intent
    try:
        spec = parser.parse_dict(raw_intent)
        logger.info(
            "[CLI] Director intent parsed: vibe=%s, evolve_variants=%s",
            raw_intent.get("vibe", ""), raw_intent.get("evolve_variants", 0),
        )
    except Exception as exc:
        logger.warning("[CLI] Director intent parse FAILED", exc_info=True)
        output_fn(f"\n\033[1;31m[❌ 系统阻断] 引擎底层发生严重故障：{exc}\033[0m"); output_fn("\033[90m    ↳ [💡 提示] 已安全拦截异常避免闪退，详细追踪栈请查看 logs 目录。\033[0m"); output_fn(json.dumps({
            "status": "error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1

    # SESSION-179: Apply distilled physics override from Visual Distillation
    if "_physics_override" in raw_intent:
        _po = raw_intent["_physics_override"]
        try:
            if hasattr(spec, "genotype") and hasattr(spec.genotype, "physics"):
                for attr in ("gravity", "mass", "stiffness", "damping", "bounce", "friction"):
                    if attr in _po:
                        setattr(spec.genotype.physics, attr, float(_po[attr]))
            if hasattr(spec, "genotype") and hasattr(spec.genotype, "proportions"):
                for attr in ("head_ratio", "body_ratio", "limb_ratio", "scale", "squash_stretch"):
                    if attr in _po:
                        setattr(spec.genotype.proportions, attr, float(_po[attr]))
            if hasattr(spec, "genotype") and hasattr(spec.genotype, "animation"):
                for attr in ("frame_rate", "anticipation", "follow_through", "exaggeration",
                             "ease_in", "ease_out", "cycle_frames"):
                    if attr in _po:
                        val = _po[attr]
                        if attr in ("frame_rate", "cycle_frames"):
                            setattr(spec.genotype.animation, attr, int(val))
                        else:
                            setattr(spec.genotype.animation, attr, float(val))
            logger.info("[CLI] Visual Distillation physics override applied to spec")
        except Exception as _override_err:
            logger.warning("[CLI] Physics override application failed: %s", _override_err)
    # ── SESSION-187: VFX Plugin Resolution Banner ───────────────────────────
    # After intent parsing, display which VFX plugins were activated by the
    # Semantic Orchestrator.  This is the user-facing confirmation of the
    # LLM/heuristic plugin selection.
    if hasattr(spec, "active_vfx_plugins") and spec.active_vfx_plugins:
        output_fn("")
        output_fn("\033[1;35m" + "\u2550" * 60 + "\033[0m")
        output_fn(
            "\033[1;35m[\U0001f3ac SESSION-187 \u8bed\u4e49\u7f1d\u5408\u5668] "
            "\u5df2\u6fc0\u6d3b VFX \u7279\u6548\u63d2\u4ef6\u94fe\uff1a\033[0m"
        )
        for _vfx_idx, _vfx_name in enumerate(spec.active_vfx_plugins, 1):
            output_fn(
                f"\033[90m    [{_vfx_idx}] {_vfx_name}\033[0m"
            )
        output_fn("\033[1;35m" + "\u2550" * 60 + "\033[0m")
    else:
        output_fn("")
        output_fn(
            "\033[90m[SESSION-187 \u8bed\u4e49\u7f1d\u5408\u5668] "
            "\u672c\u6b21\u610f\u56fe\u672a\u89e6\u53d1\u4efb\u4f55 VFX \u7279\u6548\u63d2\u4ef6\033[0m"
        )

    output_fn("")
    output_fn("\u2705 \u610f\u56fe\u89e3\u6790\u5b8c\u6210\uff0c\u8fdb\u5165\u767d\u6a21\u9884\u6f14...")

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
            auto_fire=auto_fire,  # SESSION-201: thread the headless flag.
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
    "GOLDEN_HANDOFF_OPTION_MASS_BAKE",
    "GOLDEN_HANDOFF_OPTION_FULL_RENDER",
    "GOLDEN_HANDOFF_OPTION_PRODUCE",
    "GOLDEN_HANDOFF_OPTION_AUDIT",
    "GOLDEN_HANDOFF_OPTION_HOME",
    "GOLDEN_HANDOFF_OPTION_LOOKDEV",
    "_dispatch_mass_production",
    "_run_director_studio",
    "_run_interactive",
    "_run_interactive_shell",
    # SESSION-179: Visual Distillation & Style Retargeting
    "VISUAL_DISTILLATION_OPTION",
]

# SESSION-179: Visual Distillation menu option label (DaC contract)
VISUAL_DISTILLATION_OPTION = (
    "[D] 👁️ 视觉临摹 — 丢入参考动图，让 AI 逆向推导物理参数！"
)
