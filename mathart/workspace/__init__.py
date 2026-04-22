"""Workspace management: inbox hot folder, output classification, file picker,
preflight radar, and the idempotent auto-assembly surgeon (SESSION-133).
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
]
