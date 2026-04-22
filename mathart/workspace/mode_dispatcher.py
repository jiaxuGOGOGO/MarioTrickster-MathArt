"""Top-level mode dispatcher for the dual-track distillation bus.

The dispatcher provides a strongly-typed session contract and a registry-based
strategy layer so new modes can be mounted without editing hard-coded routing
branches in the CLI entry point.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from .config_manager import ConfigManager
from .git_agent import GitAgent


class SessionMode(str, Enum):
    PRODUCTION = "production"
    EVOLUTION = "evolution"
    LOCAL_DISTILL = "local_distill"
    DRY_RUN = "dry_run"


MODE_ALIASES = {
    "1": SessionMode.PRODUCTION,
    "production": SessionMode.PRODUCTION,
    "prod": SessionMode.PRODUCTION,
    "2": SessionMode.EVOLUTION,
    "evolution": SessionMode.EVOLUTION,
    "evolve": SessionMode.EVOLUTION,
    "3": SessionMode.LOCAL_DISTILL,
    "local": SessionMode.LOCAL_DISTILL,
    "local_distill": SessionMode.LOCAL_DISTILL,
    "distill": SessionMode.LOCAL_DISTILL,
    "research": SessionMode.LOCAL_DISTILL,
    "4": SessionMode.DRY_RUN,
    "dry_run": SessionMode.DRY_RUN,
    "dry-run": SessionMode.DRY_RUN,
    "audit": SessionMode.DRY_RUN,
}


@dataclass(frozen=True)
class SessionContext:
    mode: SessionMode
    label: str
    requires_gpu: bool
    starts_daemon: bool
    may_push_knowledge: bool
    knowledge_read_only: bool
    supports_cloud_agent_push: bool
    interactive: bool
    project_root: str
    output_dir: str | None = None
    source_path: str | None = None
    config_source: str | None = None
    dry_run: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModeDispatchResult:
    strategy_name: str
    session: SessionContext
    executed: bool
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "executed": self.executed,
            "session": self.session.to_dict(),
            "payload": self.payload,
        }


class SessionStrategy(ABC):
    mode: ClassVar[SessionMode]
    display_name: ClassVar[str]
    menu_index: ClassVar[str]

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()

    @abstractmethod
    def build_context(self, options: dict[str, Any]) -> SessionContext:
        raise NotImplementedError

    def preview(self, context: SessionContext) -> dict[str, Any]:
        return {
            "status": "preview",
            "message": f"{context.label} 已选中，但尚未执行。",
            "next_step": "传入 execute=True 或在 CLI 向导中选择立即执行。",
        }

    @abstractmethod
    def execute(self, context: SessionContext) -> dict[str, Any]:
        raise NotImplementedError


class ProductionStrategy(SessionStrategy):
    mode = SessionMode.PRODUCTION
    display_name = "🏭 工业量产(Production)"
    menu_index = "1"

    def build_context(self, options: dict[str, Any]) -> SessionContext:
        skip_ai_render = bool(options.get("skip_ai_render", False))
        return SessionContext(
            mode=self.mode,
            label=self.display_name,
            requires_gpu=not skip_ai_render,
            starts_daemon=not skip_ai_render,
            may_push_knowledge=False,
            knowledge_read_only=True,
            supports_cloud_agent_push=False,
            interactive=bool(options.get("interactive", False)),
            project_root=str(self.project_root),
            output_dir=self._output_dir(options),
            dry_run=bool(options.get("dry_run", False)),
            extra={
                "skip_ai_render": skip_ai_render,
                "batch_size": int(options.get("batch_size", 20)),
                "pdg_workers": int(options.get("pdg_workers", 16)),
                "gpu_slots": int(options.get("gpu_slots", 1)),
                "seed": int(options.get("seed", 20260422)),
            },
        )

    def execute(self, context: SessionContext) -> dict[str, Any]:
        from tools.run_mass_production_factory import run_mass_production_factory

        output_root = Path(context.output_dir or self.project_root / "output" / "production")
        output_root.mkdir(parents=True, exist_ok=True)
        payload = run_mass_production_factory(
            output_root=output_root,
            batch_size=int(context.extra["batch_size"]),
            pdg_workers=int(context.extra["pdg_workers"]),
            gpu_slots=int(context.extra["gpu_slots"]),
            seed=int(context.extra["seed"]),
            skip_ai_render=bool(context.extra["skip_ai_render"]),
            comfyui_url=str(context.extra.get("comfyui_url", "http://localhost:8188")),
        )
        payload["knowledge_write_mode"] = "read_only"
        return payload

    @staticmethod
    def _output_dir(options: dict[str, Any]) -> str:
        return str(Path(options.get("output_dir") or Path("output") / "production").resolve())


class EvolutionStrategy(SessionStrategy):
    mode = SessionMode.EVOLUTION
    display_name = "🧬 本地闭环进化(Evolution)"
    menu_index = "2"

    def build_context(self, options: dict[str, Any]) -> SessionContext:
        return SessionContext(
            mode=self.mode,
            label=self.display_name,
            requires_gpu=False,
            starts_daemon=False,
            may_push_knowledge=False,
            knowledge_read_only=False,
            supports_cloud_agent_push=False,
            interactive=bool(options.get("interactive", False)),
            project_root=str(self.project_root),
            output_dir=str(Path(options.get("output_dir") or Path("output") / "evolution").resolve()),
            dry_run=bool(options.get("dry_run", False)),
            extra={
                "argv": list(options.get("evolution_argv") or [
                    "run",
                    "--target",
                    str(options.get("target", "texture")),
                    "--preset",
                    str(options.get("preset", "terrain")),
                    "--iterations",
                    str(options.get("iterations", 12)),
                    "--population",
                    str(options.get("population", 8)),
                ]),
            },
        )

    def execute(self, context: SessionContext) -> dict[str, Any]:
        from mathart.evolution.cli import main as evolution_main

        argv = list(context.extra["argv"])
        exit_code = evolution_main(argv)
        return {
            "status": "ok" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "argv": argv,
        }


class LocalDistillStrategy(SessionStrategy):
    mode = SessionMode.LOCAL_DISTILL
    display_name = "💻 本地 AI 科研蒸馏(Local Distill + GitPush)"
    menu_index = "3"

    def build_context(self, options: dict[str, Any]) -> SessionContext:
        config_manager = ConfigManager(self.project_root)
        loaded = config_manager.load()
        return SessionContext(
            mode=self.mode,
            label=self.display_name,
            requires_gpu=False,
            starts_daemon=False,
            may_push_knowledge=True,
            knowledge_read_only=False,
            supports_cloud_agent_push=True,
            interactive=bool(options.get("interactive", False)),
            project_root=str(self.project_root),
            output_dir=str(Path(options.get("output_dir") or Path("output") / "distill").resolve()),
            source_path=(str(Path(options["source"]).resolve()) if options.get("source") else None),
            config_source=None if loaded is None else loaded.source,
            dry_run=bool(options.get("dry_run", False)),
            extra={
                "storage": str(options.get("config_storage", "env")),
                "git_push": bool(options.get("git_push", False)),
                "source_name": options.get("source_name"),
            },
        )

    def execute(self, context: SessionContext) -> dict[str, Any]:
        config_manager = ConfigManager(self.project_root)
        config = config_manager.ensure_local_api_config(
            interactive=context.interactive,
            storage=str(context.extra.get("storage", "env")),
        )
        if not context.source_path:
            return {
                "status": "blocked",
                "reason": "未提供蒸馏源文件路径。请通过 --source 指向书籍、文档或知识素材。",
                "config": config.redacted(),
            }
        from mathart.evolution.engine import SelfEvolutionEngine

        engine = SelfEvolutionEngine(project_root=self.project_root, verbose=False)
        engine.outer_loop.use_llm = True
        result = engine.outer_loop.distill_file(
            context.source_path,
            source_name=str(context.extra.get("source_name") or Path(context.source_path).stem),
        )
        payload = {
            "status": "ok",
            "config": config.redacted(),
            "source_path": context.source_path,
            "summary": result.summary() if hasattr(result, "summary") else "distillation completed",
            "knowledge_files_updated": list(getattr(result, "knowledge_files_updated", []) or []),
            "rules_extracted": getattr(result, "rules_extracted", None),
        }
        if context.extra.get("git_push"):
            agent = GitAgent(self.project_root)
            payload["git_sync"] = asdict(
                agent.sync_knowledge(
                    push=True,
                    session_id="SESSION-136",
                )
            )
        return payload


class DryRunAuditStrategy(SessionStrategy):
    mode = SessionMode.DRY_RUN
    display_name = "🧪 纯 CPU 沙盒审计(Dry-Run)"
    menu_index = "4"

    def build_context(self, options: dict[str, Any]) -> SessionContext:
        return SessionContext(
            mode=self.mode,
            label=self.display_name,
            requires_gpu=False,
            starts_daemon=False,
            may_push_knowledge=False,
            knowledge_read_only=True,
            supports_cloud_agent_push=False,
            interactive=bool(options.get("interactive", False)),
            project_root=str(self.project_root),
            output_dir=str(Path(options.get("output_dir") or Path("output") / "audit").resolve()),
            dry_run=True,
            extra={},
        )

    def execute(self, context: SessionContext) -> dict[str, Any]:
        config_state = ConfigManager(self.project_root).describe_state()
        git_state = GitAgent(self.project_root).preview_status()
        return {
            "status": "ok",
            "dry_run": True,
            "config_state": config_state,
            "git_state": git_state,
        }


class ModeDispatcher:
    """Registry-driven top-level router for dual-track workspace modes."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self._registry: dict[SessionMode, SessionStrategy] = {}
        self._register_defaults()

    def register(self, strategy: SessionStrategy) -> None:
        self._registry[strategy.mode] = strategy

    def available_modes(self) -> list[dict[str, str]]:
        strategies = sorted(self._registry.values(), key=lambda item: item.menu_index)
        return [
            {
                "index": strategy.menu_index,
                "mode": strategy.mode.value,
                "label": strategy.display_name,
            }
            for strategy in strategies
        ]

    def resolve_mode(self, value: SessionMode | str) -> SessionMode:
        if isinstance(value, SessionMode):
            return value
        key = str(value).strip().lower()
        if key not in MODE_ALIASES:
            raise ValueError(f"Unsupported session mode: {value!r}")
        return MODE_ALIASES[key]

    def build_session_context(
        self,
        mode: SessionMode | str,
        options: dict[str, Any] | None = None,
    ) -> SessionContext:
        resolved_mode = self.resolve_mode(mode)
        strategy = self._registry[resolved_mode]
        return strategy.build_context(dict(options or {}))

    def dispatch(
        self,
        mode: SessionMode | str,
        *,
        options: dict[str, Any] | None = None,
        execute: bool = False,
    ) -> ModeDispatchResult:
        resolved_mode = self.resolve_mode(mode)
        strategy = self._registry[resolved_mode]
        context = strategy.build_context(dict(options or {}))
        payload = strategy.execute(context) if execute else strategy.preview(context)
        return ModeDispatchResult(
            strategy_name=strategy.__class__.__name__,
            session=context,
            executed=execute,
            payload=payload,
        )

    def _register_defaults(self) -> None:
        self.register(ProductionStrategy(self.project_root))
        self.register(EvolutionStrategy(self.project_root))
        self.register(LocalDistillStrategy(self.project_root))
        self.register(DryRunAuditStrategy(self.project_root))


__all__ = [
    "MODE_ALIASES",
    "ModeDispatchResult",
    "ModeDispatcher",
    "SessionContext",
    "SessionMode",
]
