"""Workspace management: inbox hot folder, output classification, file picker,
preflight radar, the idempotent auto-assembly surgeon (SESSION-133),
and the daemon supervisor + launcher facade (SESSION-134).
"""
from .manager import WorkspaceManager, pick_files
from .preflight_radar import (
    AssetCheck,
    ComfyUIDiscovery,
    GPUProbe,
    HealthStatus,
    PreflightRadar,
    PreflightReport,
    PreflightVerdict,
    PythonEnvironmentProbe,
    scan_preflight,
)
from .asset_injector import (
    AssetInjector,
    DEFAULT_CACHE_ROOTS,
    InjectionMethod,
    InjectionOutcome,
    InjectionStatus,
)
from .atomic_downloader import (
    AtomicDownloader,
    DownloadOutcome,
    DownloadStatus,
    DownloadTransport,
    TransportResponse,
    UrllibTransport,
)
from .idempotent_surgeon import (
    ActionKind,
    ActionOutcome,
    AssemblyReport,
    AssetPlan,
    IdempotentSurgeon,
)
from .daemon_supervisor import (
    ComfyUINotResponsiveError,
    DaemonCrashedError,
    DaemonLifecycleEvent,
    DaemonState,
    DaemonStatus,
    DaemonSupervisor,
    HttpReadinessProbe,
    ReadinessProbe,
)
from .launcher_facade import (
    LauncherFacade,
    LauncherOutcome,
    LauncherStage,
    LauncherVerdict,
)

__all__ = [
    # Legacy workspace helpers
    "WorkspaceManager",
    "pick_files",
    # Preflight Radar (Phase 1)
    "AssetCheck",
    "ComfyUIDiscovery",
    "GPUProbe",
    "HealthStatus",
    "PreflightRadar",
    "PreflightReport",
    "PreflightVerdict",
    "PythonEnvironmentProbe",
    "scan_preflight",
    # Asset Injector (Phase 2a)
    "AssetInjector",
    "DEFAULT_CACHE_ROOTS",
    "InjectionMethod",
    "InjectionOutcome",
    "InjectionStatus",
    # Atomic Downloader (Phase 2b)
    "AtomicDownloader",
    "DownloadOutcome",
    "DownloadStatus",
    "DownloadTransport",
    "TransportResponse",
    "UrllibTransport",
    # Idempotent Surgeon (Phase 2c)
    "ActionKind",
    "ActionOutcome",
    "AssemblyReport",
    "AssetPlan",
    "IdempotentSurgeon",
    # Daemon Supervisor (Phase 3a)
    "ComfyUINotResponsiveError",
    "DaemonCrashedError",
    "DaemonLifecycleEvent",
    "DaemonState",
    "DaemonStatus",
    "DaemonSupervisor",
    "HttpReadinessProbe",
    "ReadinessProbe",
    # Launcher Facade (Phase 3b)
    "LauncherFacade",
    "LauncherOutcome",
    "LauncherStage",
    "LauncherVerdict",
]

# SESSION-141: Garbage Collector & In-Flight Pruner
from .garbage_collector import (
    GarbageCollector,
    GCConfig,
    GCReport,
    InFlightPruner,
    PruneReport,
)

# SESSION-139: Director Intent — Semantic Translation & Blueprint Inheritance
from .director_intent import (
    AnimationConfig,
    Blueprint,
    BlueprintMeta,
    ColorPalette,
    CreatorIntentSpec,
    DirectorIntentParser,
    Genotype,
    PhysicsConfig,
    ProportionsConfig,
    SEMANTIC_VIBE_MAP,
    parse_intent,
)
from mathart.workspace.director_intent import KnowledgeConflict
from mathart.workspace.director_intent import KnowledgeProvenanceRecord
from mathart.workspace.director_intent import VIBE_TO_KNOWLEDGE_MODULES

# SESSION-147: Project-level knowledge bus factory used by Director Studio
# routes (both interactive wizard and non-interactive strategy dispatch)
# to physically connect RuntimeDistillationBus → DirectorIntentParser.
from .knowledge_bus_factory import build_project_knowledge_bus  # noqa: E402

# SESSION-147: ComfyUI interactive path rescue (used by ProductionStrategy
# when the radar returns comfyui_not_found in interactive mode).
from .comfyui_rescue import (
    COMFYUI_ENV_VAR,
    RescueOutcome,
    is_comfyui_not_found_payload,
    persist_comfyui_home,
    hot_inject_env,
    prompt_comfyui_path_rescue,
)
