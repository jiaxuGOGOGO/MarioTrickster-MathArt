"""Interactive ComfyUI path self-rescue gateway.

SESSION-147 — Interactive Path Auto-Resolution (\u96f7\u8fbe\u963b\u65ad\u4ea4\u4e92\u5f0f\u81ea\u6108).

Background
----------
The SESSION-146 blackbox audit showed that whenever the PreflightRadar
returned ``comfyui_not_found``, the wizard hard-blocked and asked the
non-technical end user to *manually* export ``COMFYUI_HOME`` as an OS
environment variable.  This is a dead-end UX: for every artist the
correct answer to "the radar could not find ComfyUI" is almost always
"it lives at ``X:\\\\AI\\\\ComfyUI_windows_portable\\\\ComfyUI`` — please
remember it for me".

This module implements a minimal, dependency-light rescue gateway that:

1. Detects the ``comfyui_not_found`` blocking action coming out of the
   radar payload.
2. Prompts the user with a single friendly question — they can either
   paste / drag-and-drop a ComfyUI root, or press Enter to fall back to
   the safe sandbox (dry-run) mode.
3. Strips shell-escaped quotes from the drag-and-drop path (Windows Explorer
   wraps paths in double quotes), resolves the path and validates that it
   is actually a ComfyUI root via the radar's own ``_looks_like_comfyui_root``
   heuristic (``main.py`` + ``custom_nodes/``).
4. Persists the accepted path to ``<project_root>/.env`` as
   ``COMFYUI_HOME="..."`` via ``python-dotenv``'s ``set_key`` (with a pure-
   Python fallback so the rescue survives when ``dotenv`` is unavailable).
5. Hot-injects the variable into the live ``os.environ`` so the caller
   can re-run the radar **inside the same process** without asking the
   user to restart the terminal.

The rescue is strictly opt-in: callers must pass ``interactive=True``
explicitly, mirroring the wizard's own ``_run_interactive`` guard.  This
keeps CI / non-interactive executions deterministic.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

COMFYUI_ENV_VAR = "COMFYUI_HOME"


# ---------------------------------------------------------------------------
# Public contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RescueOutcome:
    """Outcome of an interactive ComfyUI rescue attempt.

    Attributes
    ----------
    resolved:
        True when the user supplied a valid path AND it was persisted and
        hot-injected.  False when the user opted into the sandbox fallback
        or provided an invalid path after the allowed retries.
    path:
        The validated, resolved ComfyUI root (``str``) when ``resolved`` is
        True, else ``None``.
    env_file:
        The ``.env`` path that was updated, or ``None`` when nothing was
        persisted.
    fallback_to_sandbox:
        True when the user explicitly accepted the dry-run fallback (empty
        input).  Exposed so callers can downgrade the mode gracefully
        instead of raising.
    """

    resolved: bool
    path: Optional[str]
    env_file: Optional[str]
    fallback_to_sandbox: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _looks_like_comfyui_root(candidate: Path) -> bool:
    """Replicate the radar's triangulated heuristic locally.

    We intentionally *do not* import ``preflight_radar._looks_like_comfyui_root``
    directly to avoid a circular import: the radar module is consumed by
    the mode dispatcher at production-mode boot, while this rescue module
    is invoked **after** the radar's verdict has already been rendered.
    """
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    if not resolved.is_dir():
        return False
    return (resolved / "main.py").is_file() and (resolved / "custom_nodes").is_dir()


def _clean_pasted_path(raw: str) -> str:
    """Strip shell-quotes and surrounding whitespace from a pasted path.

    Windows Explorer drag-and-drop wraps paths in double quotes
    (``"D:\\AI\\ComfyUI"``), PowerShell sometimes emits single quotes,
    and users frequently paste a trailing newline.  We peel *one* layer
    of matched quotes so that multi-quoted paths remain detectable as
    invalid instead of silently normalising to something unexpected.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    return text


def _encode_env_value(value: str) -> str:
    """Encode a filesystem path so it survives ``.env`` parsing intact.

    Paths that contain whitespace, ``#``, or nested quotes MUST be
    double-quoted to prevent downstream ``.env`` readers (our own
    ``config_manager`` or ``python-dotenv``) from truncating them.
    """
    unsafe = any(ch.isspace() for ch in value) or any(ch in value for ch in ("#", '"', "'"))
    if not unsafe:
        return value
    # Escape embedded double quotes, then wrap.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _native_append_or_update(env_path: Path, key: str, value: str) -> None:
    """Append-or-update ``KEY=value`` inside ``env_path`` without clobbering
    unrelated keys.  Pure Python fallback used when ``python-dotenv`` is
    not importable.
    """
    encoded = _encode_env_value(value)
    new_line = f"{key}={encoded}\n"
    if not env_path.exists():
        env_path.write_text(new_line, encoding="utf-8")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    replaced = False
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[idx] = new_line
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(new_line)
    env_path.write_text("".join(lines), encoding="utf-8")


def persist_comfyui_home(project_root: Path | str, comfyui_root: Path | str) -> Path:
    """Persist ``COMFYUI_HOME=<comfyui_root>`` to ``<project_root>/.env``.

    Prefers ``dotenv.set_key`` (``python-dotenv``) which handles quoting,
    file creation and in-place update natively.  Falls back to a pure
    Python append-or-update implementation so that missing ``dotenv``
    never becomes a new failure mode for the rescue.
    """
    env_path = Path(project_root).resolve() / ".env"
    value = str(Path(comfyui_root).resolve())

    try:
        from dotenv import set_key  # type: ignore
    except Exception:
        _native_append_or_update(env_path, COMFYUI_ENV_VAR, value)
        logger.info(
            "[ComfyUIRescue] Persisted %s to %s (native fallback)",
            COMFYUI_ENV_VAR, env_path,
        )
        return env_path

    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch()
    try:
        set_key(str(env_path), COMFYUI_ENV_VAR, value, quote_mode="always")
    except TypeError:
        # Older python-dotenv releases don't accept ``quote_mode``.
        set_key(str(env_path), COMFYUI_ENV_VAR, value)
    logger.info(
        "[ComfyUIRescue] Persisted %s to %s (python-dotenv)",
        COMFYUI_ENV_VAR, env_path,
    )
    return env_path


def hot_inject_env(key: str, value: str) -> None:
    """Inject ``key=value`` into the live ``os.environ`` so a follow-up
    radar scan picks up the configured ComfyUI root without the user
    having to restart the terminal.
    """
    os.environ[key] = value
    logger.info("[ComfyUIRescue] Hot-injected %s into os.environ", key)


def is_comfyui_not_found_payload(payload: dict) -> bool:
    """Return True when the production-mode radar payload signals that the
    *sole* reason for blocking is the missing ComfyUI root.

    We inspect ``blocking_actions`` (which is the canonical field defined
    by :class:`mathart.workspace.preflight_radar.PreflightReport`).  The
    ``status=="blocked"`` check guards against false positives on
    ``ready``/``auto_fixable`` verdicts.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("status") != "blocked":
        return False
    actions = payload.get("blocking_actions") or []
    if not isinstance(actions, (list, tuple)):
        return False
    return any(
        isinstance(action, str) and action.startswith("comfyui_not_found")
        for action in actions
    )


# ---------------------------------------------------------------------------
# Interactive rescue prompt
# ---------------------------------------------------------------------------

_PROMPT_HEADER = (
    "\n\ud83d\udea8 \u96f7\u8fbe\u672a\u80fd\u81ea\u52a8\u5b9a\u4f4d\u5230 ComfyUI \u5f15\u64ce\u3002"
)
_PROMPT_BODY = (
    "\u5982\u679c\u60a8\u5df2\u5b89\u88c5\uff0c\u8bf7\u5c06 ComfyUI \u7684\u6839\u76ee\u5f55\uff08\u5305\u542b main.py \u7684\u6587\u4ef6\u5939\uff09"
    "\u62d6\u62fd\u5230\u6b64\u5904\u5e76\u56de\u8f66\uff1b"
    "\u6216\u76f4\u63a5\u6309\u56de\u8f66\u9000\u56de\u6c99\u76d2\uff08dry-run\uff09\u6a21\u5f0f\u3002"
)
_PROMPT_INPUT = "\u8bf7\u7c98\u8d34\u6216\u62d6\u5165 ComfyUI \u6839\u76ee\u5f55\uff1a"
_MSG_ACCEPT = (
    "\u2705 \u5f15\u64ce\u7ed1\u5b9a\u6210\u529f\u5e76\u6c38\u4e45\u4fdd\u5b58\uff01COMFYUI_HOME = {path}"
)
_MSG_INVALID = (
    "\u26a0\ufe0f  \u8def\u5f84\u65e0\u6548\uff1a{path}\n    "
    "\u672a\u627e\u5230 main.py / custom_nodes\u3002\u8bf7\u91cd\u65b0\u62d6\u5165\uff0c\u6216\u6309\u56de\u8f66\u9000\u56de\u6c99\u76d2\u6a21\u5f0f\u3002"
)
_MSG_FALLBACK = (
    "\u21aa\ufe0f  \u5df2\u9000\u56de\u6c99\u76d2\u6a21\u5f0f\u3002\u60a8\u968f\u65f6\u53ef\u4ee5\u8bbe\u7f6e COMFYUI_HOME \u73af\u5883\u53d8\u91cf\u6216\u91cd\u65b0\u8fd0\u884c\u751f\u4ea7\u6a21\u5f0f\u3002"
)


def prompt_comfyui_path_rescue(
    *,
    project_root: Path | str,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    max_attempts: int = 3,
) -> RescueOutcome:
    """Run the interactive rescue prompt.

    Parameters
    ----------
    project_root:
        Root directory used to anchor the ``.env`` file.
    input_fn, output_fn:
        Hooks that keep the prompt unit-testable (see the accompanying
        pytest suite which drives both the accept and fallback paths).
    max_attempts:
        Maximum number of invalid inputs the user is allowed before we
        downgrade to the sandbox fallback automatically.
    """
    output_fn(_PROMPT_HEADER)
    output_fn(_PROMPT_BODY)

    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        raw = input_fn(_PROMPT_INPUT)
        cleaned = _clean_pasted_path(raw or "")
        if not cleaned:
            output_fn(_MSG_FALLBACK)
            logger.info(
                "[ComfyUIRescue] User declined to supply a path (attempt %d); "
                "falling back to sandbox mode.",
                attempts,
            )
            return RescueOutcome(
                resolved=False,
                path=None,
                env_file=None,
                fallback_to_sandbox=True,
            )

        candidate = Path(cleaned).expanduser()
        if not _looks_like_comfyui_root(candidate):
            logger.warning(
                "[ComfyUIRescue] Attempt %d failed validation: %s",
                attempts, candidate,
            )
            output_fn(_MSG_INVALID.format(path=candidate))
            continue

        resolved = candidate.resolve()
        env_path = persist_comfyui_home(project_root, resolved)
        hot_inject_env(COMFYUI_ENV_VAR, str(resolved))
        output_fn(_MSG_ACCEPT.format(path=resolved))
        logger.info(
            "[ComfyUIRescue] Rescue succeeded on attempt %d: path=%s, env_file=%s",
            attempts, resolved, env_path,
        )
        return RescueOutcome(
            resolved=True,
            path=str(resolved),
            env_file=str(env_path),
            fallback_to_sandbox=False,
        )

    output_fn(_MSG_FALLBACK)
    logger.warning(
        "[ComfyUIRescue] Exhausted %d attempts without a valid path; "
        "falling back to sandbox mode.",
        max_attempts,
    )
    return RescueOutcome(
        resolved=False,
        path=None,
        env_file=None,
        fallback_to_sandbox=True,
    )


__all__ = [
    "COMFYUI_ENV_VAR",
    "RescueOutcome",
    "hot_inject_env",
    "is_comfyui_not_found_payload",
    "persist_comfyui_home",
    "prompt_comfyui_path_rescue",
]
