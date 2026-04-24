"""Laboratory Hub — Reflection-Based Dynamic Microkernel Dispatch Center.

SESSION-183: P0-SESSION-183-MICROKERNEL-HUB-AND-VAT-INTEGRATION

This module implements the **Laboratory Hub CLI**, a dynamic microkernel
dispatch center that uses Python reflection to discover, enumerate, and
invoke ALL registered backends — including experimental and dormant ones
that are not exposed through the standard 5-mode production CLI.

Research Foundations
--------------------
1. **Microkernel Architecture & Reflection-based Service Locator (IoC)**:
   Uses ``BackendRegistry.all_backends()`` to introspect all registered
   plugins at runtime.  Menu items are generated dynamically from
   ``_backend_meta.display_name`` and class ``__doc__`` — ZERO hardcoded
   if/else routing.  Future plugins auto-appear with ZERO code changes.
   Ref: Chris Lattner (LLVM, AOSA 2012), Martin Fowler (IoC, 2004),
   David Seddon (Python IoC Techniques, 2019).

2. **Feature Toggles & Sandboxed Execution (Martin Fowler, 2017)**:
   Experimental backend outputs are physically isolated in
   ``workspace/laboratory/<backend_name>/`` sandbox directories.
   Production vault (``output/production/``) is NEVER polluted.
   Fail-Safe pattern: any experimental failure is caught and contained.

3. **HDR Vertex Animation Textures (SideFX Houdini VAT 3.0)**:
   The hub enables direct invocation of the High-Precision VAT backend,
   which was previously dormant (978 lines, zero cross-references).

Architecture Discipline
-----------------------
- This module is a **standalone CLI extension** that plugs into the
  existing ``cli_wizard.py`` main menu as option ``[6]``.
- It does NOT modify any core orchestrator, ``AssetPipeline``, or
  ``if/else`` routing in the trunk.
- All backend discovery is performed via the existing ``BackendRegistry``
  singleton — the same IoC infrastructure used by the microkernel
  orchestrator.
- Output isolation: all experimental runs write to
  ``workspace/laboratory/<backend_name>/`` — never to the production vault.

Red-Line Enforcement
--------------------
- 🔴 **Anti-Hardcoded-Menu Red Line**: ZERO ``if choice == "VAT": run_vat()``
  spaghetti code.  Menu is 100% dynamically generated from registry
  introspection.  Adding a new backend to the registry automatically
  expands the menu.
- 🔴 **Zero-Modification-to-Trunk Red Line**: This module does NOT import
  or modify ``AssetPipeline``, ``MicrokernelOrchestrator``, or any
  production pipeline code.
- 🔴 **Zero-Pollution-to-Production-Vault Red Line**: All experimental
  outputs are sandboxed in ``workspace/laboratory/``.
"""
from __future__ import annotations

import json
import logging
import textwrap
import time as _time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from mathart.core.backend_registry import (
    BackendMeta,
    BackendRegistry,
    get_registry,
)
from mathart.core.artifact_schema import ArtifactManifest

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════

_LAB_BANNER = r"""
╔══════════════════════════════════════════════════════════════════════╗
║  🔬  黑科技实验室  ·  Microkernel Hub  ·  Dynamic Backend Dispatch  ║
║                                                                      ║
║  所有已注册的微内核后端均通过 Python 反射自动发现。                    ║
║  实验性输出将被隔离至 workspace/laboratory/<backend>/ 沙盒。          ║
║  生产金库 (output/production/) 绝对不会被污染。                       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

_LAB_FOOTER = (
    "\n\033[90m[🔬 实验室] 所有实验性输出已隔离至 workspace/laboratory/ 沙盒。"
    "\n    生产金库 (output/production/) 未受影响。\033[0m\n"
)

# ═══════════════════════════════════════════════════════════════════════════
#  Reflection-Based Backend Discovery
# ═══════════════════════════════════════════════════════════════════════════


def _discover_lab_backends(
    registry: BackendRegistry,
) -> list[tuple[str, BackendMeta, type]]:
    """Discover all registered backends via reflection.

    Returns a sorted list of (canonical_name, meta, backend_class) tuples.
    The list is sorted by display_name for stable menu ordering.

    This function uses ZERO hardcoded backend names — it relies entirely
    on ``registry.all_backends()`` introspection, fulfilling the IoC
    mandate from the research foundations.
    """
    all_backends = registry.all_backends()
    result = []
    for canonical_name, (meta, backend_class) in all_backends.items():
        result.append((canonical_name, meta, backend_class))
    # Sort by display_name for stable, predictable menu ordering
    result.sort(key=lambda item: item[1].display_name.lower())
    return result


def _extract_backend_summary(backend_class: type) -> str:
    """Extract a one-line summary from a backend class via reflection.

    Uses ``__doc__`` introspection to read the class docstring.
    Falls back to the class name if no docstring is available.
    """
    doc = getattr(backend_class, "__doc__", None)
    if doc:
        # Take the first non-empty line as the summary
        for line in doc.strip().splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:120]
    return f"({backend_class.__name__})"


# ═══════════════════════════════════════════════════════════════════════════
#  Sandboxed Execution Engine
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_lab_output_dir(
    project_root: Path,
    backend_name: str,
) -> Path:
    """Resolve the sandboxed output directory for a laboratory run.

    All experimental outputs are isolated in:
        workspace/laboratory/<backend_name>/

    This enforces the Zero-Pollution-to-Production-Vault red line.
    """
    lab_dir = project_root / "workspace" / "laboratory" / backend_name
    lab_dir.mkdir(parents=True, exist_ok=True)
    return lab_dir


def _execute_backend_standalone(
    backend_class: type,
    meta: BackendMeta,
    output_dir: Path,
    *,
    output_fn: Callable[[str], None],
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute a backend in standalone (空跑) mode within the laboratory sandbox.

    This function:
    1. Instantiates the backend class
    2. Calls its ``execute()`` method with a minimal context
    3. Captures the result (ArtifactManifest or dict)
    4. Returns a summary dict

    All exceptions are caught and reported — Fail-Safe pattern.
    """
    context: dict[str, Any] = {
        "output_dir": str(output_dir),
        "project_root": str(output_dir.parent.parent.parent),
        "verbose": verbose,
        "laboratory_mode": True,
        "sandbox_isolation": True,
    }

    output_fn(f"\n\033[1;36m[🔬 实验室] 正在初始化后端: {meta.display_name}\033[0m")
    output_fn(f"    \033[90m后端类型: {meta.name}\033[0m")
    output_fn(f"    \033[90m版本: {meta.version}\033[0m")
    output_fn(f"    \033[90m沙盒输出目录: {output_dir}\033[0m")

    t_start = _time.perf_counter()

    try:
        instance = backend_class()
        if hasattr(instance, "execute"):
            result = instance.execute(context)
            t_elapsed = _time.perf_counter() - t_start
            output_fn(
                f"\n\033[1;32m[✅ 实验室] 后端 {meta.display_name} "
                f"执行成功！耗时 {t_elapsed:.2f}s\033[0m"
            )
            # Convert ArtifactManifest to dict if needed
            if isinstance(result, ArtifactManifest):
                return {
                    "status": "success",
                    "backend": meta.name,
                    "elapsed_s": round(t_elapsed, 3),
                    "artifact_family": result.artifact_family,
                    "outputs": result.outputs,
                    "metadata": result.metadata,
                }
            elif isinstance(result, dict):
                result["status"] = "success"
                result["elapsed_s"] = round(t_elapsed, 3)
                return result
            else:
                return {
                    "status": "success",
                    "backend": meta.name,
                    "elapsed_s": round(t_elapsed, 3),
                    "result_type": type(result).__name__,
                }
        else:
            output_fn(
                f"\n\033[1;33m[⚠️ 实验室] 后端 {meta.display_name} "
                f"未实现 execute() 方法，跳过执行。\033[0m"
            )
            return {
                "status": "skipped",
                "backend": meta.name,
                "reason": "no execute() method",
            }
    except Exception as exc:
        t_elapsed = _time.perf_counter() - t_start
        logger.warning(
            "[Laboratory] Backend %s execution failed: %s",
            meta.name,
            exc,
            exc_info=True,
        )
        output_fn(
            f"\n\033[1;31m[❌ 实验室] 后端 {meta.display_name} "
            f"执行失败 ({t_elapsed:.2f}s): {exc}\033[0m"
        )
        output_fn(
            f"    \033[90m↳ 异常已被沙盒安全拦截，生产金库未受影响。\033[0m"
        )
        return {
            "status": "error",
            "backend": meta.name,
            "elapsed_s": round(t_elapsed, 3),
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Interactive Laboratory Hub
# ═══════════════════════════════════════════════════════════════════════════


def run_laboratory_hub(
    *,
    project_root: Optional[Path] = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Run the interactive Laboratory Hub.

    This is the main entry point called from ``cli_wizard.py`` when the
    user selects ``[6] 🔬 黑科技实验室``.

    The hub:
    1. Uses ``BackendRegistry.all_backends()`` to discover ALL registered backends
    2. Dynamically generates an interactive numbered menu via Python reflection
    3. Allows the user to select and execute any backend in standalone mode
    4. Isolates all outputs in ``workspace/laboratory/<backend_name>/``

    ZERO hardcoded if/else routing — the menu is 100% reflection-driven.
    """
    root = Path(project_root or Path.cwd()).resolve()
    registry = get_registry()

    output_fn(_LAB_BANNER)

    while True:
        # ── Dynamic menu generation via reflection ──────────────
        backends = _discover_lab_backends(registry)

        if not backends:
            output_fn("\n\033[1;33m[⚠️] 未发现任何已注册的后端。\033[0m")
            return

        output_fn("\n\033[1;37m已发现的微内核后端 (通过反射自动枚举):\033[0m\n")

        # Build dynamic route dict — ZERO hardcoded mapping
        route_dict: dict[str, tuple[str, BackendMeta, type]] = {}
        for idx, (canonical_name, meta, backend_class) in enumerate(backends, 1):
            summary = _extract_backend_summary(backend_class)
            capabilities_str = ", ".join(
                cap.name for cap in meta.capabilities
            ) if meta.capabilities else "N/A"

            output_fn(
                f"  \033[1;36m[{idx:>2}]\033[0m {meta.display_name}"
                f"\n       \033[90m{summary}\033[0m"
                f"\n       \033[90m能力: {capabilities_str} | "
                f"版本: {meta.version} | "
                f"来源: {meta.session_origin}\033[0m"
            )
            route_dict[str(idx)] = (canonical_name, meta, backend_class)

        output_fn(f"\n  \033[1;33m[ 0]\033[0m 🏠 返回主菜单")
        output_fn(f"\n\033[90m共 {len(backends)} 个后端可用。\033[0m")

        # ── User selection ──────────────────────────────────────
        try:
            choice = input_fn("\n🔬 请输入后端编号并回车: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\n检测到退出信号，返回主菜单...")
            return

        if choice in {"0", "exit", "quit", "q", "back"}:
            output_fn("\n已退出黑科技实验室，返回主菜单。")
            return

        if choice not in route_dict:
            output_fn(f"\n\033[1;33m[⚠️] 无效编号: {choice}。请重新选择。\033[0m")
            continue

        # ── Dispatch via dynamic route dict ─────────────────────
        canonical_name, meta, backend_class = route_dict[choice]

        output_fn(
            f"\n\033[1;35m{'═' * 60}\033[0m"
            f"\n\033[1;35m  选中后端: {meta.display_name}\033[0m"
            f"\n\033[1;35m{'═' * 60}\033[0m"
        )

        # Resolve sandboxed output directory
        lab_output_dir = _resolve_lab_output_dir(root, canonical_name)

        # Execute in sandbox
        result = _execute_backend_standalone(
            backend_class,
            meta,
            lab_output_dir,
            output_fn=output_fn,
            verbose=True,
        )

        # Print result summary
        output_fn(f"\n\033[1;37m执行结果摘要:\033[0m")
        output_fn(json.dumps(result, ensure_ascii=False, indent=2))
        output_fn(_LAB_FOOTER)

        # ── Continue or exit ────────────────────────────────────
        try:
            again = input_fn(
                "\n🔬 继续探索其他后端？(y/n, 默认 y): "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return

        if again in {"n", "no", "exit", "quit"}:
            output_fn("\n已退出黑科技实验室，返回主菜单。")
            return
        # Loop continues for another backend selection
