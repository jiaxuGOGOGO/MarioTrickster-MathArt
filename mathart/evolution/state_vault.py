"""SESSION-177 — Unified Evolution State Vault & Legacy Migration Engine.

P0-SESSION-177-DISTILLATION-STATE-CONSOLIDATION

This module implements the **State Vault** pattern — a centralized I/O routing
layer that forces all evolution bridge state files into a single, governed
directory (``workspace/evolution_states/``) instead of scattering hidden
dot-prefixed JSON files across the project root.

Architecture Discipline
-----------------------
- **Root Directory Defouling**: Agent-produced checkpoints and ephemeral states
  are ABSOLUTELY FORBIDDEN from polluting the project root.  All state files
  are routed to ``workspace/evolution_states/`` with the leading dot stripped.
- **Unified Persistence Bus**: The vault serves as the canonical I/O gateway
  for all inner-loop evolution state, making it the Single Source of Truth
  for the ``RuntimeDistillationBus`` to mount.
- **Zero Business Logic Mutation**: This module touches ONLY file paths and
  I/O routing.  No evolution algorithm math, gradient formulas, or scoring
  logic is modified.
- **Lossless Hot Migration**: Legacy hidden state files in the project root
  are automatically detected and moved (``shutil.move``) into the vault with
  dot-prefix stripping, preserving every byte of accumulated evolution data.

Industrial References
---------------------
- Root Directory Defouling & State Centralization (Data Governance best practice)
- Unified Persistence Bus (Single Source of Truth architecture)
- Hot Migration / Blue-Green State Cutover (zero-downtime deployment pattern)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
EVOLUTION_STATES_DIR = "workspace/evolution_states"


def get_vault_dir(project_root: Path | str | None = None) -> Path:
    """Return the canonical evolution state vault directory, creating it if needed.

    Parameters
    ----------
    project_root:
        The project root directory.  Falls back to ``Path.cwd()`` when ``None``.

    Returns
    -------
    Path
        Absolute path to ``<project_root>/workspace/evolution_states/``.
    """
    root = Path(project_root or Path.cwd()).resolve()
    vault = root / EVOLUTION_STATES_DIR
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def resolve_state_path(
    project_root: Path | str,
    legacy_filename: str,
) -> Path:
    """Resolve a state filename to its vault-governed path.

    This is the **single routing function** that all evolution bridges must use
    instead of ``self.root / ".some_state.json"``.

    The function:
    1. Strips the leading dot from the filename (if present).
    2. Routes the file into ``workspace/evolution_states/``.
    3. Ensures the vault directory exists.

    Parameters
    ----------
    project_root:
        The project root directory.
    legacy_filename:
        The original filename, e.g. ``".phase3_physics_state.json"``.

    Returns
    -------
    Path
        The vault-governed path, e.g.
        ``<root>/workspace/evolution_states/phase3_physics_state.json``.
    """
    vault = get_vault_dir(project_root)
    # Strip leading dot to de-hide the file
    clean_name = legacy_filename.lstrip(".")
    return vault / clean_name


def migrate_legacy_states(
    project_root: Path | str | None = None,
    *,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Scan the project root for legacy hidden state files and migrate them.

    This function implements the **Lossless Hot Migration** protocol:
    1. Scans the project root (depth=1) for files matching ``.*_state*.json``.
    2. For each match, computes the vault destination (dot-stripped).
    3. Uses ``shutil.move`` to atomically relocate the file.
    4. Returns a manifest of all migrations performed.

    Parameters
    ----------
    project_root:
        The project root to scan.  Falls back to ``Path.cwd()`` when ``None``.
    dry_run:
        When ``True``, report what would be migrated without actually moving.

    Returns
    -------
    list[dict[str, str]]
        A list of migration records, each containing ``source``, ``destination``,
        and ``status`` (``"migrated"`` or ``"dry_run"``).
    """
    root = Path(project_root or Path.cwd()).resolve()
    vault = get_vault_dir(root)
    manifest: list[dict[str, str]] = []

    for entry in sorted(root.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        # Match hidden state JSON files: starts with dot, contains "_state", ends with .json
        if not (name.startswith(".") and "_state" in name and name.endswith(".json")):
            continue

        clean_name = name.lstrip(".")
        destination = vault / clean_name

        record = {
            "source": str(entry),
            "destination": str(destination),
            "original_name": name,
            "clean_name": clean_name,
        }

        if dry_run:
            record["status"] = "dry_run"
            logger.info(
                "[StateVault] DRY RUN — would migrate: %s -> %s",
                entry, destination,
            )
        else:
            try:
                # If destination already exists, merge: keep the newer one
                if destination.exists():
                    src_mtime = entry.stat().st_mtime
                    dst_mtime = destination.stat().st_mtime
                    if src_mtime > dst_mtime:
                        shutil.move(str(entry), str(destination))
                        record["status"] = "migrated_overwrite_newer"
                        logger.info(
                            "[StateVault] Migrated (overwrite, newer): %s -> %s",
                            entry, destination,
                        )
                    else:
                        # Destination is newer or same age — remove the root copy
                        entry.unlink()
                        record["status"] = "removed_stale_root_copy"
                        logger.info(
                            "[StateVault] Removed stale root copy (vault is newer): %s",
                            entry,
                        )
                else:
                    shutil.move(str(entry), str(destination))
                    record["status"] = "migrated"
                    logger.info(
                        "[StateVault] Migrated: %s -> %s",
                        entry, destination,
                    )
            except OSError as exc:
                record["status"] = f"error: {exc}"
                logger.warning(
                    "[StateVault] Migration failed for %s: %s",
                    entry, exc,
                )

        manifest.append(record)

    if manifest:
        summary_path = vault / "_migration_manifest.json"
        try:
            # Append to existing manifest if present
            existing: list[dict[str, str]] = []
            if summary_path.exists():
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
            existing.extend(manifest)
            summary_path.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[StateVault] Failed to write migration manifest: %s", exc)

    count = len(manifest)
    if count > 0:
        logger.info(
            "[StateVault] Legacy migration complete: %d file(s) processed.",
            count,
        )
    else:
        logger.debug("[StateVault] No legacy state files found in root — root is clean.")

    return manifest


def load_all_vault_states(
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    """Load and deserialize all JSON state files from the vault.

    This is the read-side API consumed by ``RuntimeDistillationBus`` to mount
    inner-loop evolution parameters alongside external LLM knowledge.

    Parameters
    ----------
    project_root:
        The project root.  Falls back to ``Path.cwd()`` when ``None``.

    Returns
    -------
    dict[str, Any]
        A dictionary keyed by module name (derived from filename without
        ``_state.json`` suffix), containing the deserialized JSON data.
        Example: ``{"phase3_physics": {...}, "gait_blend": {...}}``.
    """
    vault = get_vault_dir(project_root)
    states: dict[str, Any] = {}

    for path in sorted(vault.glob("*_state.json")):
        if path.name.startswith("_"):
            continue  # Skip internal manifests
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Derive module name: "phase3_physics_state.json" -> "phase3_physics"
            module_name = path.stem.replace("_state", "")
            states[module_name] = data
            logger.debug(
                "[StateVault] Loaded evolution state: %s (%d keys)",
                module_name, len(data) if isinstance(data, dict) else 0,
            )
        except Exception as exc:
            logger.warning(
                "[StateVault] Failed to load %s: %s",
                path, exc,
            )

    return states


__all__ = [
    "EVOLUTION_STATES_DIR",
    "get_vault_dir",
    "resolve_state_path",
    "migrate_legacy_states",
    "load_all_vault_states",
]
