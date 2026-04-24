"""Dynamic Pipeline Weaver — Middleware-Chain VFX Plugin Execution.

SESSION-187: P0-SESSION-187-SEMANTIC-ORCHESTRATOR-AND-GRAND-UNIFICATION

This module implements the **Dynamic Pipeline Weaver**, the runtime execution
engine that takes the ``active_vfx_plugins`` list from ``CreatorIntentSpec``
and dynamically invokes each plugin through the BackendRegistry using a
**middleware chain** pattern.

Research Foundations
--------------------
1. **Middleware / Decorator Pattern (ASP.NET Core Pipeline)**:
   Each VFX plugin is treated as a middleware in a chain.  The weaver
   iterates over the ``active_vfx_plugins`` list and invokes each plugin's
   ``execute(context)`` method sequentially.  Each plugin can enrich the
   shared context dict (adding artifacts, modifying parameters) before
   the next plugin in the chain receives it.
   Ref: Microsoft ASP.NET Core Middleware (2026); Martin Fowler (2004)
   "Inversion of Control Containers and the Dependency Injection pattern";
   StackOverflow "Are middlewares an implementation of the Decorator pattern?"

2. **Observer Pattern for Plugin Orchestration**:
   The weaver emits lifecycle events (``on_plugin_start``, ``on_plugin_done``,
   ``on_plugin_error``) that observers can subscribe to for telemetry,
   logging, or UX feedback.
   Ref: Gang of Four Observer Pattern; Unity Engine Event System.

3. **Anti-Hardcoded Red Line**:
   ZERO hardcoded plugin-specific branches.  The weaver uses
   a **uniform loop** over the plugin list, resolving each by name through
   ``BackendRegistry.get_backend(name)``.  All plugins share the same
   ``execute(context) -> ArtifactManifest`` interface.

Architecture Discipline
-----------------------
- This module is a **standalone execution engine** — it does NOT modify
  any existing pipeline, BackendRegistry, or core backend code.
- It is designed to be called from the Director Studio flow after intent
  parsing has populated ``spec.active_vfx_plugins``.
- Each plugin receives a shared ``context: dict`` and returns an
  ``ArtifactManifest``.  The weaver collects all manifests into a
  ``WeaverResult``.

Red-Line Enforcement
--------------------
- 🔴 **Anti-Hardcoded Red Line**: Uniform loop + registry reflection.
  NO per-plugin ``if`` branches.
- 🔴 **Graceful Degradation Red Line**: If a plugin fails, the weaver
  logs a WARNING, records the error, and continues to the next plugin.
  The pipeline NEVER crashes due to a single plugin failure.
- 🔴 **Zero-Trunk-Modification Red Line**: This module does NOT import
  or modify ``AssetPipeline``, ``MicrokernelOrchestrator``, or any
  production pipeline code.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Weaver Result — Aggregated output of the middleware chain
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PluginExecutionRecord:
    """Record of a single plugin's execution within the weaver chain."""
    plugin_name: str = ""
    display_name: str = ""
    success: bool = False
    duration_ms: float = 0.0
    artifact_count: int = 0
    error_message: str = ""
    artifact_manifest: Optional[Any] = None  # ArtifactManifest when available

    def to_dict(self) -> dict:
        return {
            "plugin_name": self.plugin_name,
            "display_name": self.display_name,
            "success": self.success,
            "duration_ms": round(self.duration_ms, 2),
            "artifact_count": self.artifact_count,
            "error_message": self.error_message,
        }


@dataclass
class WeaverResult:
    """Aggregated result of the Dynamic Pipeline Weaver execution."""
    total_plugins: int = 0
    executed: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)
    successful_plugins: int = 0
    failed_plugins: int = 0
    total_duration_ms: float = 0.0
    plugin_records: List[PluginExecutionRecord] = field(default_factory=list)
    merged_artifacts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_plugins": self.total_plugins,
            "successful_plugins": self.successful_plugins,
            "failed_plugins": self.failed_plugins,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "plugin_records": [r.to_dict() for r in self.plugin_records],
        }

    @property
    def all_succeeded(self) -> bool:
        return self.failed_plugins == 0 and self.total_plugins > 0

    @property
    def total_ms(self) -> float:
        """Alias for total_duration_ms for SESSION-187 contract compatibility."""
        return self.total_duration_ms


# ═══════════════════════════════════════════════════════════════════════════
#  Lifecycle Event Callbacks (Observer Pattern)
# ═══════════════════════════════════════════════════════════════════════════

# Type aliases for lifecycle callbacks
OnPluginStart = Callable[[str, str, int, int], None]  # (name, display, idx, total)
OnPluginDone = Callable[[str, str, bool, float], None]  # (name, display, success, ms)
OnPluginError = Callable[[str, str, Exception], None]  # (name, display, error)


# ═══════════════════════════════════════════════════════════════════════════
#  Dynamic Pipeline Weaver — Core Engine
# ═══════════════════════════════════════════════════════════════════════════


class PipelineObserver:
    def __init__(self):
        self.on_plugin_start = None
        self.on_plugin_done = None
        self.on_plugin_error = None

class DynamicPipelineWeaver:
    """Middleware-chain executor for VFX plugins resolved by the Semantic Orchestrator.

    Usage::

        weaver = DynamicPipelineWeaver(
            active_plugins=["cppn_texture_evolution", "fluid_momentum_controller"],
            base_context={"output_dir": "/tmp/output", "resolution": 512},
        )
        result = weaver.execute()

    The weaver resolves each plugin name through ``BackendRegistry``, invokes
    ``plugin.execute(context)``, and collects results.  Failed plugins are
    logged but do NOT halt the chain (Graceful Degradation).
    """

    def __init__(
        self,
        *,
        registry=None,
        observer=None,
        active_plugins: Optional[List[str]] = None,
        base_context: Optional[Dict[str, Any]] = None,
        on_plugin_start: Optional[OnPluginStart] = None,
        on_plugin_done: Optional[OnPluginDone] = None,
        on_plugin_error: Optional[OnPluginError] = None,
    ) -> None:
        self.registry = registry
        self.observer = observer
        self.active_plugins = list(active_plugins) if active_plugins else []
        self.base_context = dict(base_context) if base_context else {}
        self._on_start = on_plugin_start or (observer.on_plugin_start if observer else None)
        self._on_done = on_plugin_done or (observer.on_plugin_done if observer else None)
        self._on_error = on_plugin_error or (observer.on_plugin_error if observer else None)

    def execute(self, plugin_names: Optional[List[str]] = None, context: Optional[Dict[str, Any]] = None) -> WeaverResult:
        """Execute the middleware chain: iterate over active plugins and invoke each.

        This is the core of the Dynamic Pipeline Weaver.  It implements:
        1. **Uniform Loop** — NO per-plugin ``if`` branches.
        2. **Registry Reflection** — each plugin resolved via ``get_backend(name)``.
        3. **Shared Context Enrichment** — each plugin can add to the context.
        4. **Graceful Degradation** — failed plugins logged, chain continues.

        Returns
        -------
        WeaverResult
            Aggregated execution result with per-plugin records.
        """
        if plugin_names is not None:
            self.active_plugins = list(plugin_names)
        if context is not None:
            self.base_context = dict(context)
            
        if self.registry:
            registry = self.registry
        else:
            from mathart.core.backend_registry import get_registry
            registry = get_registry()
            
        all_backends = registry.all_backends()

        result = WeaverResult(total_plugins=len(self.active_plugins))
        chain_start = time.perf_counter()

        # ── Shared context that flows through the middleware chain ──────────
        context = dict(self.base_context)

        # ═══════════════════════════════════════════════════════════════════
        #  [Anti-Hardcoded Red Line] UNIFORM LOOP — NO per-plugin if/elif
        # ═══════════════════════════════════════════════════════════════════
        for idx, plugin_name in enumerate(self.active_plugins):
            record = PluginExecutionRecord(plugin_name=plugin_name)

            # ── Resolve plugin from registry ───────────────────────────────
            backend_entry = all_backends.get(plugin_name)
            if backend_entry is None:
                # Plugin not found in registry — hallucination slipped through
                record.success = False
                record.error_message = (
                    f"Plugin '{plugin_name}' not found in BackendRegistry "
                    f"(available: {sorted(all_backends.keys())})"
                )
                logger.warning(
                    "[PipelineWeaver] %s — skipping",
                    record.error_message,
                )
                result.failed_plugins += 1
                result.skipped.append(plugin_name)
                result.errors[plugin_name] = record.error_message
                result.plugin_records.append(record)
                continue

            # Extract display name from registry metadata (兼容三种返回:
            # 1) dict (test fake)
            # 2) (BackendMeta, cls) 元组 (真实 BackendRegistry.all_backends)
            # 3) 自定义带属性的对象
            backend_cls = None
            display_name = plugin_name
            if isinstance(backend_entry, dict):
                backend_cls = backend_entry.get("cls") or backend_entry.get("class")
                display_name = backend_entry.get("display_name", plugin_name)
            elif isinstance(backend_entry, tuple) and len(backend_entry) >= 2:
                _meta, _cls = backend_entry[0], backend_entry[1]
                backend_cls = _cls
                display_name = getattr(_meta, "display_name", plugin_name) or plugin_name
            else:
                backend_cls = getattr(backend_entry, "cls", None) or getattr(backend_entry, "class_", None)
                display_name = getattr(backend_entry, "display_name", plugin_name)
            record.display_name = display_name

            # ── Lifecycle: on_plugin_start ─────────────────────────────────
            if self._on_start:
                try:
                    try:
                        self._on_start(
                            plugin_name=plugin_name,
                            display_name=display_name,
                            index=idx + 1,
                            total=len(self.active_plugins),
                        )
                    except TypeError:
                        self._on_start(
                            plugin_name, display_name,
                            idx + 1, len(self.active_plugins),
                        )
                except Exception:
                    pass  # Observer errors are non-fatal

            # ── Instantiate and execute ────────────────────────────────────
            plugin_start = time.perf_counter()
            try:
                # Instantiate the backend class
                if backend_cls is not None:
                    instance = backend_cls()
                else:
                    # Fallback: try to get via registry.get_backend
                    instance = registry.get_backend(plugin_name)

                if instance is None:
                    raise RuntimeError(
                        f"Could not instantiate backend '{plugin_name}'"
                    )

                # Execute with shared context
                manifest = instance.execute(context)
                plugin_end = time.perf_counter()
                duration_ms = (plugin_end - plugin_start) * 1000

                record.success = True
                record.duration_ms = duration_ms
                record.artifact_manifest = manifest

                # Count artifacts if manifest has the expected interface
                if hasattr(manifest, "artifacts"):
                    record.artifact_count = len(manifest.artifacts)
                elif hasattr(manifest, "to_dict"):
                    md = manifest.to_dict()
                    record.artifact_count = len(md.get("artifacts", []))

                result.successful_plugins += 1
                result.executed.append(plugin_name)

                # ── Context enrichment: add this plugin's output ───────────
                context[f"_weaver_{plugin_name}_manifest"] = manifest
                context[f"_weaver_{plugin_name}_success"] = True

                logger.info(
                    "[PipelineWeaver] Plugin '%s' completed in %.1fms "
                    "(%d artifacts)",
                    plugin_name, duration_ms, record.artifact_count,
                )

            except Exception as exc:
                plugin_end = time.perf_counter()
                duration_ms = (plugin_end - plugin_start) * 1000

                record.success = False
                record.duration_ms = duration_ms
                record.error_message = f"{exc.__class__.__name__}: {exc}"
                result.failed_plugins += 1
                result.skipped.append(plugin_name)
                result.errors[plugin_name] = record.error_message

                # ── Context enrichment: mark failure ───────────────────────
                context[f"_weaver_{plugin_name}_success"] = False
                context[f"_weaver_{plugin_name}_error"] = str(exc)

                logger.warning(
                    "[PipelineWeaver] Plugin '%s' FAILED in %.1fms: %s "
                    "(graceful degradation — continuing chain)",
                    plugin_name, duration_ms, exc,
                )

                # ── Lifecycle: on_plugin_error ─────────────────────────────
                if self._on_error:
                    try:
                        try:
                            self._on_error(
                                plugin_name=plugin_name,
                                display_name=display_name,
                                error=exc,
                            )
                        except TypeError:
                            self._on_error(plugin_name, display_name, exc)
                    except Exception:
                        pass

            # ── Lifecycle: on_plugin_done ──────────────────────────────────
            if self._on_done:
                try:
                    try:
                        self._on_done(
                            plugin_name=plugin_name,
                            display_name=display_name,
                            success=record.success,
                            duration_ms=record.duration_ms,
                        )
                    except TypeError:
                        self._on_done(
                            plugin_name, display_name,
                            record.success, record.duration_ms,
                        )
                except Exception:
                    pass

            result.plugin_records.append(record)

        # ── Chain complete ─────────────────────────────────────────────────
        chain_end = time.perf_counter()
        result.total_duration_ms = (chain_end - chain_start) * 1000

        logger.info(
            "[PipelineWeaver] Chain complete: %d/%d plugins succeeded in %.1fms",
            result.successful_plugins,
            result.total_plugins,
            result.total_duration_ms,
        )

        return result


def weave_vfx_pipeline(
    *,
    active_plugins: List[str],
    output_dir: str | Path,
    resolution: int = 512,
    extra_context: Optional[Dict[str, Any]] = None,
    on_plugin_start: Optional[OnPluginStart] = None,
    on_plugin_done: Optional[OnPluginDone] = None,
    on_plugin_error: Optional[OnPluginError] = None,
) -> WeaverResult:
    """Convenience function: create a DynamicPipelineWeaver and execute.

    Parameters
    ----------
    active_plugins : list[str]
        Validated list of backend type names to execute.
    output_dir : str or Path
        Output directory for all plugin artifacts.
    resolution : int
        Default texture/image resolution.
    extra_context : dict, optional
        Additional context entries to pass to plugins.
    on_plugin_start, on_plugin_done, on_plugin_error : callable, optional
        Lifecycle event callbacks for UX telemetry.

    Returns
    -------
    WeaverResult
        Aggregated execution result.
    """
    base_context: Dict[str, Any] = {
        "output_dir": str(output_dir),
        "resolution": resolution,
    }
    if extra_context:
        base_context.update(extra_context)

    weaver = DynamicPipelineWeaver(
        active_plugins=active_plugins,
        base_context=base_context,
        on_plugin_start=on_plugin_start,
        on_plugin_done=on_plugin_done,
        on_plugin_error=on_plugin_error,
    )
    return weaver.execute()


__all__ = [
    "PipelineObserver",
    "DynamicPipelineWeaver",
    "PluginExecutionRecord",
    "WeaverResult",
    "weave_vfx_pipeline",
]
