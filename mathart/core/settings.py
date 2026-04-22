"""Centralized Configuration & Magic-Number Elimination.

SESSION-141: P0-SESSION-138-SYSTEM-PURGE-AND-OBSERVABILITY

This module is the **single source of truth** for all tuneable system
constants.  Every magic number that was previously scattered across the
codebase is now extracted here, documented, and overridable via
environment variables (or a ``.env`` file loaded before import).

Design principles:
1. **Frozen dataclass** — immutable after construction; thread-safe.
2. **Environment-first** — every field reads ``os.environ`` with a typed
   default so that operators can tune without touching code.
3. **Zero external deps** — uses only stdlib (no pydantic-settings).
4. **Backward compatible** — existing call-sites that pass explicit kwargs
   still work; these settings are *defaults*, not overrides.

Usage::

    from mathart.core.settings import get_settings

    cfg = get_settings()
    print(cfg.network_timeout)       # 60.0 (or MATHART_NETWORK_TIMEOUT)
    print(cfg.gc_ttl_days)           # 7    (or MATHART_GC_TTL_DAYS)

External research anchors:
- 12-Factor App: Store config in the environment (Heroku, 2012)
- Python dataclasses: PEP 557
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "Settings",
    "get_settings",
]


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Centralized, immutable configuration for the MathArt pipeline.

    All fields are overridable via environment variables prefixed with
    ``MATHART_``.  The mapping is documented per-field below.
    """

    # ── Network ──────────────────────────────────────────────────────
    network_timeout: float = 60.0
    """Default HTTP / WebSocket timeout in seconds.
    Env: MATHART_NETWORK_TIMEOUT"""

    network_connect_timeout: float = 10.0
    """TCP connect timeout in seconds.
    Env: MATHART_NETWORK_CONNECT_TIMEOUT"""

    network_ws_timeout: float = 600.0
    """WebSocket long-poll timeout in seconds.
    Env: MATHART_NETWORK_WS_TIMEOUT"""

    network_max_retries: int = 3
    """Maximum retry attempts for transient network failures.
    Env: MATHART_NETWORK_MAX_RETRIES"""

    network_backoff_initial: float = 1.0
    """Initial backoff delay between retries (seconds).
    Env: MATHART_NETWORK_BACKOFF_INITIAL"""

    network_backoff_max: float = 5.0
    """Maximum backoff delay between retries (seconds).
    Env: MATHART_NETWORK_BACKOFF_MAX"""

    # ── Sandbox / Validation ─────────────────────────────────────────
    sandbox_timeout: float = 3.0
    """Timeout for sandbox code validation execution (seconds).
    Env: MATHART_SANDBOX_TIMEOUT"""

    sandbox_subprocess_timeout: float = 30.0
    """Timeout for subprocess invocations in sandbox (seconds).
    Env: MATHART_SANDBOX_SUBPROCESS_TIMEOUT"""

    # ── Daemon / ComfyUI ─────────────────────────────────────────────
    daemon_readiness_timeout: float = 120.0
    """Maximum wait for daemon readiness probe (seconds).
    Env: MATHART_DAEMON_READINESS_TIMEOUT"""

    daemon_graceful_timeout: float = 8.0
    """Graceful shutdown timeout for managed daemons (seconds).
    Env: MATHART_DAEMON_GRACEFUL_TIMEOUT"""

    daemon_health_timeout: float = 3.0
    """Timeout for daemon health-check HTTP probes (seconds).
    Env: MATHART_DAEMON_HEALTH_TIMEOUT"""

    comfyui_base_url: str = "http://localhost:8188"
    """Default ComfyUI server base URL.
    Env: MATHART_COMFYUI_BASE_URL"""

    comfyui_connect_timeout: float = 5.0
    """ComfyUI WebSocket connect timeout (seconds).
    Env: MATHART_COMFYUI_CONNECT_TIMEOUT"""

    comfyui_ws_timeout: float = 600.0
    """ComfyUI WebSocket operation timeout (seconds).
    Env: MATHART_COMFYUI_WS_TIMEOUT"""

    # ── Evolution ────────────────────────────────────────────────────
    evolution_max_iterations: int = 10
    """Maximum iterations for the three-layer evolution loop.
    Env: MATHART_EVOLUTION_MAX_ITERATIONS"""

    evolution_convergence_threshold: float = 0.95
    """Pass-rate threshold to declare convergence.
    Env: MATHART_EVOLUTION_CONVERGENCE_THRESHOLD"""

    evolution_population_size: int = 8
    """Default population size for evolutionary operators.
    Env: MATHART_EVOLUTION_POPULATION_SIZE"""

    wfc_max_retries: int = 20
    """Maximum retries for WFC constraint propagation.
    Env: MATHART_WFC_MAX_RETRIES"""

    # ── Garbage Collection ───────────────────────────────────────────
    gc_ttl_days: int = 7
    """Time-to-live for stale workspace artefacts (days).
    Env: MATHART_GC_TTL_DAYS"""

    gc_dry_run: bool = False
    """If True, GC reports but does not delete.
    Env: MATHART_GC_DRY_RUN"""

    # ── Logging ──────────────────────────────────────────────────────
    log_dir: str = "logs"
    """Directory for blackbox log files (relative to project root).
    Env: MATHART_LOG_DIR"""

    log_backup_count: int = 7
    """Number of daily log rotations to retain.
    Env: MATHART_LOG_BACKUP_COUNT"""

    log_file_level: str = "DEBUG"
    """Minimum log level written to file.
    Env: MATHART_LOG_FILE_LEVEL"""

    log_console_level: str = "WARNING"
    """Minimum log level shown on console.
    Env: MATHART_LOG_CONSOLE_LEVEL"""

    # ── Paper Miner / Community Sources ──────────────────────────────
    api_timeout: float = 20.0
    """Timeout for external API calls (arXiv, Semantic Scholar, etc.).
    Env: MATHART_API_TIMEOUT"""

    community_timeout: float = 15.0
    """Timeout for community source fetches.
    Env: MATHART_COMMUNITY_TIMEOUT"""

    # ── Backend File Watcher ─────────────────────────────────────────
    file_watcher_debounce: float = 2.0
    """Debounce interval for the backend file watcher (seconds).
    Env: MATHART_FILE_WATCHER_DEBOUNCE"""

    safe_point_reload_timeout: float = 10.0
    """Timeout for safe-point execution lock reload (seconds).
    Env: MATHART_SAFE_POINT_RELOAD_TIMEOUT"""


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_singleton: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the global Settings singleton, reading env vars on first call."""
    global _singleton
    if _singleton is not None:
        return _singleton

    _singleton = Settings(
        # Network
        network_timeout=_env_float("MATHART_NETWORK_TIMEOUT", 60.0),
        network_connect_timeout=_env_float("MATHART_NETWORK_CONNECT_TIMEOUT", 10.0),
        network_ws_timeout=_env_float("MATHART_NETWORK_WS_TIMEOUT", 600.0),
        network_max_retries=_env_int("MATHART_NETWORK_MAX_RETRIES", 3),
        network_backoff_initial=_env_float("MATHART_NETWORK_BACKOFF_INITIAL", 1.0),
        network_backoff_max=_env_float("MATHART_NETWORK_BACKOFF_MAX", 5.0),
        # Sandbox
        sandbox_timeout=_env_float("MATHART_SANDBOX_TIMEOUT", 3.0),
        sandbox_subprocess_timeout=_env_float("MATHART_SANDBOX_SUBPROCESS_TIMEOUT", 30.0),
        # Daemon
        daemon_readiness_timeout=_env_float("MATHART_DAEMON_READINESS_TIMEOUT", 120.0),
        daemon_graceful_timeout=_env_float("MATHART_DAEMON_GRACEFUL_TIMEOUT", 8.0),
        daemon_health_timeout=_env_float("MATHART_DAEMON_HEALTH_TIMEOUT", 3.0),
        comfyui_base_url=_env_str("MATHART_COMFYUI_BASE_URL", "http://localhost:8188"),
        comfyui_connect_timeout=_env_float("MATHART_COMFYUI_CONNECT_TIMEOUT", 5.0),
        comfyui_ws_timeout=_env_float("MATHART_COMFYUI_WS_TIMEOUT", 600.0),
        # Evolution
        evolution_max_iterations=_env_int("MATHART_EVOLUTION_MAX_ITERATIONS", 10),
        evolution_convergence_threshold=_env_float("MATHART_EVOLUTION_CONVERGENCE_THRESHOLD", 0.95),
        evolution_population_size=_env_int("MATHART_EVOLUTION_POPULATION_SIZE", 8),
        wfc_max_retries=_env_int("MATHART_WFC_MAX_RETRIES", 20),
        # GC
        gc_ttl_days=_env_int("MATHART_GC_TTL_DAYS", 7),
        gc_dry_run=_env_bool("MATHART_GC_DRY_RUN", False),
        # Logging
        log_dir=_env_str("MATHART_LOG_DIR", "logs"),
        log_backup_count=_env_int("MATHART_LOG_BACKUP_COUNT", 7),
        log_file_level=_env_str("MATHART_LOG_FILE_LEVEL", "DEBUG"),
        log_console_level=_env_str("MATHART_LOG_CONSOLE_LEVEL", "WARNING"),
        # API
        api_timeout=_env_float("MATHART_API_TIMEOUT", 20.0),
        community_timeout=_env_float("MATHART_COMMUNITY_TIMEOUT", 15.0),
        # File Watcher
        file_watcher_debounce=_env_float("MATHART_FILE_WATCHER_DEBOUNCE", 2.0),
        safe_point_reload_timeout=_env_float("MATHART_SAFE_POINT_RELOAD_TIMEOUT", 10.0),
    )
    return _singleton


def reset_settings() -> None:
    """Reset the singleton (for testing only)."""
    global _singleton
    _singleton = None
