"""Aviation-Grade Blackbox Flight Recorder & Global Crash Interceptor.

SESSION-141: P0-SESSION-138-SYSTEM-PURGE-AND-OBSERVABILITY

This module implements the project's **Blackbox Flight Recorder** — an
always-on, crash-resilient logging backbone inspired by aviation black-box
recorders.  It guarantees that:

1. **Every execution** writes DEBUG-level detail to ``logs/`` with daily
   rotation and 7-day automatic retention (cold pruning of old logs).
2. **Any unhandled exception** — no matter how deep or fatal — is captured
   by a global ``sys.excepthook`` override and force-written to the log
   file *before* the process dies.
3. The hook itself is **double-fault protected**: if the log-write fails
   (e.g. disk full), it silently degrades to ``sys.stderr`` and never
   causes a secondary hang or crash.

Usage — call ``install_blackbox()`` as the **very first statement** in
your entry-point module::

    from mathart.core.logger import install_blackbox
    install_blackbox()          # installs excepthook + file handler
    # ... rest of application

External research anchors:
- Lesinskis (2018): sys.excepthook logging pattern
- Python docs: logging.handlers.TimedRotatingFileHandler
- Aviation ICAO Annex 6: Flight Recorder survivability requirements
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

__all__ = [
    "install_blackbox",
    "get_blackbox_logger",
    "BlackboxConfig",
]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class BlackboxConfig:
    """Tunables for the blackbox logger — all overridable via env vars."""

    def __init__(self) -> None:
        self.log_dir: str = os.environ.get(
            "MATHART_LOG_DIR", str(Path.cwd() / "logs")
        )
        self.log_filename: str = os.environ.get(
            "MATHART_LOG_FILENAME", "mathart.log"
        )
        self.file_level: int = getattr(
            logging,
            os.environ.get("MATHART_LOG_FILE_LEVEL", "DEBUG").upper(),
            logging.DEBUG,
        )
        self.console_level: int = getattr(
            logging,
            os.environ.get("MATHART_LOG_CONSOLE_LEVEL", "WARNING").upper(),
            logging.WARNING,
        )
        self.rotation_when: str = os.environ.get(
            "MATHART_LOG_ROTATION_WHEN", "midnight"
        )
        self.backup_count: int = int(
            os.environ.get("MATHART_LOG_BACKUP_COUNT", "7")
        )
        self.fmt: str = os.environ.get(
            "MATHART_LOG_FORMAT",
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
        self.date_fmt: str = os.environ.get(
            "MATHART_LOG_DATE_FORMAT", "%Y-%m-%d %H:%M:%S"
        )


# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------

_installed: bool = False
_blackbox_logger: Optional[logging.Logger] = None


def get_blackbox_logger() -> logging.Logger:
    """Return the root blackbox logger (creates if needed)."""
    global _blackbox_logger
    if _blackbox_logger is None:
        _blackbox_logger = logging.getLogger("mathart")
    return _blackbox_logger


# ---------------------------------------------------------------------------
# Global crash hook
# ---------------------------------------------------------------------------

def _blackbox_excepthook(
    exc_type: type,
    exc_value: BaseException,
    exc_tb: object,
) -> None:
    """Global unhandled-exception handler — the *last line of defence*.

    Design invariants:
    - KeyboardInterrupt is delegated to the original hook (no log spam).
    - The hook body is wrapped in a bare try/except so that a secondary
      failure (e.g. disk-full while writing) **never** causes a deadlock
      or secondary crash (Red Line #2).
    - On secondary failure, a minimal message is written to stderr.
    """
    # Let KeyboardInterrupt propagate normally
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    try:
        logger = get_blackbox_logger()
        logger.critical(
            "BLACKBOX FLIGHT RECORDER — Unhandled exception captured",
            exc_info=(exc_type, exc_value, exc_tb),
        )
    except Exception:
        # Double-fault protection: if logging itself fails, write to stderr
        try:
            sys.stderr.write(
                "\n[BLACKBOX] CRITICAL: Failed to write crash log. "
                "Dumping to stderr:\n"
            )
            traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)
        except Exception:
            pass  # Triple fault — nothing more we can do

    # User-friendly console message
    try:
        sys.stderr.write(
            "\n\U0001F6A8 发生致命错误，程序已拦截。"
            "请将 logs/ 目录下的最新日志发送给 AI 助手进行诊断修复。\n"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

def install_blackbox(
    config: Optional[BlackboxConfig] = None,
    *,
    project_root: Optional[Path] = None,
) -> logging.Logger:
    """Install the blackbox flight recorder.

    This function is **idempotent** — calling it multiple times is safe.

    Parameters
    ----------
    config : BlackboxConfig, optional
        Custom configuration.  Defaults are read from environment variables.
    project_root : Path, optional
        Override the log directory to ``project_root / "logs"``.

    Returns
    -------
    logging.Logger
        The configured root ``mathart`` logger.
    """
    global _installed

    if _installed:
        return get_blackbox_logger()

    cfg = config or BlackboxConfig()

    # Allow project_root override
    if project_root is not None:
        cfg.log_dir = str(project_root / "logs")

    log_dir = Path(cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / cfg.log_filename
    formatter = logging.Formatter(cfg.fmt, datefmt=cfg.date_fmt)

    # ── File handler: DEBUG, daily rotation, 7-day retention ──────────
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_path),
        when=cfg.rotation_when,
        backupCount=cfg.backup_count,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setLevel(cfg.file_level)
    file_handler.setFormatter(formatter)

    # ── Console handler: WARNING+ only (keep TUI clean) ──────────────
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(cfg.console_level)
    console_handler.setFormatter(formatter)

    # ── Root mathart logger ──────────────────────────────────────────
    logger = get_blackbox_logger()
    logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers on re-import
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    # ── Install global crash hook ────────────────────────────────────
    sys.excepthook = _blackbox_excepthook

    _installed = True
    logger.info(
        "Blackbox Flight Recorder installed — log_dir=%s, rotation=%s, retention=%d days",
        cfg.log_dir,
        cfg.rotation_when,
        cfg.backup_count,
    )
    return logger


def reset_blackbox() -> None:
    """Reset the blackbox state (for testing only)."""
    global _installed, _blackbox_logger
    _installed = False
    if _blackbox_logger is not None:
        for h in _blackbox_logger.handlers[:]:
            _blackbox_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    _blackbox_logger = None
    sys.excepthook = sys.__excepthook__
