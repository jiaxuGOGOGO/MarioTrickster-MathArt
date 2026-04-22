"""Preflight Radar — Read-Only Startup Probe System (SESSION-132).

P0-SESSION-129-PREFLIGHT-RADAR
================================

This module implements the **Preflight Radar** — a strictly read-only
diagnostic probe that maps the absolute truth state of the host environment
*before* any installation, download, or workspace mutation is attempted.

Architectural references (mandatory anchors)
--------------------------------------------
1. **Terraform State Drift Detection (IaC Idempotency)** —
   The radar must build the *State Tree* of the current environment without
   mutating a single byte. It is the absolute foundation that prevents the
   "blind reinstall" failure mode. See HashiCorp's `terraform plan` /
   `terraform refresh` separation: refresh is read-only, plan is preview,
   apply is the only mutating step. This module corresponds to refresh.
2. **Heuristic Discovery Algorithms** —
   Like Docker Desktop, NVIDIA RTX experience, and large game launchers, we
   never ask the user where ComfyUI lives. We discover it via the process
   table (psutil), environment variables (``COMFYUI_HOME``), and conventional
   filesystem locations (sibling directories, system drives).
3. **Fail-Safe Preflight Checks (Aviation-Grade Checklist)** —
   Runtime errors are pulled forward to startup-time static audits. Symlinks
   (``Path.resolve()`` / ``os.path.realpath``) are *transparently* traversed
   so that a power-user's external-drive model farm is not misclassified as
   missing.

Strict red lines enforced by this module
----------------------------------------
- **R1 (read-only)**: No ``pip install``, no ``git clone``, no
  ``requests.get(<download>)``, no ``os.makedirs``. The only side effects
  permitted are local CPU cycles, transient memory, and ``logging`` output.
- **R2 (process-walk safety)**: All ``psutil`` iteration is guarded with
  ``try/except`` for ``AccessDenied``, ``NoSuchProcess``, ``ZombieProcess``,
  and generic ``OSError``. The radar itself must never crash the caller.
- **R3 (symlink transparency)**: Every asset existence check uses
  ``Path.resolve()`` so that ``ln -s`` / ``mklink`` model farms register as
  ready, not missing.
- **R4 (typed contract)**: The radar's public output is a frozen, typed
  ``PreflightReport`` with three discrete verdicts: ``READY``,
  ``AUTO_FIXABLE``, ``MANUAL_INTERVENTION_REQUIRED``.

Consumption discipline
----------------------
This module is *not* a backend in the ``BackendRegistry`` sense — it
operates *before* any backend is invoked, mirroring the CLR/JVM startup
phase. Higher layers (the future "non-destructive auto-assembler", Phase 2)
will consume the ``PreflightReport`` and decide whether to dispatch repairs.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import asdict, dataclass, field
from enum import Enum
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "HealthStatus",
    "PreflightVerdict",
    "GPUProbe",
    "PythonEnvironmentProbe",
    "ComfyUIDiscovery",
    "AssetCheck",
    "PreflightReport",
    "PreflightRadar",
    "scan_preflight",
]


# ---------------------------------------------------------------------------
# Strongly-typed health vocabulary
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    """Per-indicator health state. Inherits ``str`` for trivial JSON I/O."""

    OK = "ok"
    DEGRADED = "degraded"
    MISSING = "missing"
    UNKNOWN = "unknown"


class PreflightVerdict(str, Enum):
    """Top-level radar verdict — drives the auto-assembler decision."""

    READY = "ready"
    AUTO_FIXABLE = "auto_fixable"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"


# ---------------------------------------------------------------------------
# Required ComfyUI assets — declarative manifest
# ---------------------------------------------------------------------------
# Each entry describes an asset the project depends on. Paths are *relative*
# to the discovered ComfyUI root. ``kind`` is informational only.
#
# This list is the single source of truth for "what must exist" — the radar
# is otherwise asset-agnostic. Future phases (Phase 2 self-healing) will
# consume the same manifest.
# ---------------------------------------------------------------------------

REQUIRED_COMFYUI_ASSETS: tuple[dict[str, str], ...] = (
    # AnimateDiff custom node folder (Kosinkadink fork is canonical for this project)
    {
        "name": "animatediff_evolved_node",
        "kind": "custom_node_dir",
        "relpath": "custom_nodes/ComfyUI-AnimateDiff-Evolved",
    },
    # SparseCtrl custom node (Guo et al. ECCV 2024, integrated through AnimateDiff-Evolved)
    {
        "name": "sparsectrl_loader_module",
        "kind": "custom_node_file",
        "relpath": "custom_nodes/ComfyUI-AnimateDiff-Evolved/animatediff/sparse_ctrl.py",
    },
    # ControlNet aux preprocessors used for normal/depth guides
    {
        "name": "controlnet_aux_node",
        "kind": "custom_node_dir",
        "relpath": "custom_nodes/comfyui_controlnet_aux",
    },
    # SparseCtrl model checkpoint (RGB conditioning)
    {
        "name": "sparsectrl_rgb_model",
        "kind": "model_file",
        "relpath": "models/controlnet/v3_sd15_sparsectrl_rgb.ckpt",
    },
    # AnimateDiff motion module checkpoint
    {
        "name": "animatediff_motion_module",
        "kind": "model_file",
        "relpath": "models/animatediff_models/v3_sd15_mm.ckpt",
    },
)


# ---------------------------------------------------------------------------
# Dataclasses — frozen, JSON-serializable contract objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GPUProbe:
    """Result of GPU / VRAM probing.

    The probe is fully optional: on a headless CPU node, ``status`` is
    ``MISSING`` and ``available`` is ``False`` — never an exception.
    """

    available: bool
    status: HealthStatus
    device_count: int
    device_names: tuple[str, ...]
    total_vram_mb: tuple[int, ...]
    driver_version: Optional[str]
    cuda_version: Optional[str]
    detection_method: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass(frozen=True)
class PythonEnvironmentProbe:
    """Snapshot of the Python interpreter and key dependency versions."""

    python_version: str
    executable: str
    platform: str
    is_virtualenv: bool
    required_packages: dict[str, Optional[str]]
    missing_packages: tuple[str, ...]
    status: HealthStatus

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass(frozen=True)
class AssetCheck:
    """Result of a single ComfyUI asset existence check (symlink-resolved)."""

    name: str
    kind: str
    expected_relpath: str
    resolved_path: Optional[str]
    exists: bool
    is_symlink: bool
    status: HealthStatus

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass(frozen=True)
class ComfyUIDiscovery:
    """Outcome of the multi-strategy ComfyUI sniffer."""

    found: bool
    root_path: Optional[str]
    detection_method: Optional[str]
    candidate_roots: tuple[str, ...]
    process_pid: Optional[int]
    status: HealthStatus
    asset_checks: tuple[AssetCheck, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["asset_checks"] = [a.to_dict() for a in self.asset_checks]
        return d


@dataclass(frozen=True)
class PreflightReport:
    """Top-level strongly-typed contract returned by the radar.

    The verdict field is the *only* signal downstream auto-assembly logic
    is expected to switch on. ``ready`` means take off; ``auto_fixable``
    means a Phase-2 non-destructive repair path exists (e.g. one missing
    model file with a known download URL); ``manual_intervention_required``
    means the operator must act (no ComfyUI anywhere on disk, no GPU and
    GPU was demanded, etc.).
    """

    verdict: PreflightVerdict
    summary: str
    gpu: GPUProbe
    python_env: PythonEnvironmentProbe
    comfyui: ComfyUIDiscovery
    fixable_actions: tuple[str, ...]
    blocking_actions: tuple[str, ...]
    generated_at: str
    radar_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "summary": self.summary,
            "gpu": self.gpu.to_dict(),
            "python_env": self.python_env.to_dict(),
            "comfyui": self.comfyui.to_dict(),
            "fixable_actions": list(self.fixable_actions),
            "blocking_actions": list(self.blocking_actions),
            "generated_at": self.generated_at,
            "radar_version": self.radar_version,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Internal probe primitives
# ---------------------------------------------------------------------------

def _safe_iter_processes(psutil_module: Any) -> Iterable[Any]:
    """Iterate processes with a defensive guard.

    Wraps ``psutil.process_iter`` and silently skips ``AccessDenied``,
    ``NoSuchProcess``, ``ZombieProcess``, and generic ``OSError``. Pulling
    this out of inline code makes it trivial to unit-test the safety net.
    """

    try:
        iterator = psutil_module.process_iter(["pid", "name", "cmdline", "cwd"])
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("preflight_radar: psutil.process_iter failed: %s", exc)
        return iter(())

    def _generator() -> Iterable[Any]:
        for proc in iterator:
            try:
                yield proc
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("preflight_radar: skipping process due to %s", exc)
                continue

    return _generator()


def _try_import(name: str) -> Optional[Any]:
    """Best-effort import that returns ``None`` instead of raising."""

    try:
        return __import__(name)
    except Exception:  # noqa: BLE001 — defensive, do not crash radar
        return None


# ---------------------------------------------------------------------------
# GPU probe
# ---------------------------------------------------------------------------

def _probe_gpu(nvidia_smi_runner: Optional[Callable[[], Optional[str]]] = None,
               torch_module: Optional[Any] = None) -> GPUProbe:
    """Probe GPU availability through layered fallbacks.

    Detection order:
      1. ``torch.cuda`` (if torch is importable)
      2. ``nvidia-smi`` shell call (if executable found on PATH)

    Both are strictly read-only. Failures degrade gracefully to MISSING.

    Parameters
    ----------
    nvidia_smi_runner:
        Optional override that returns the raw ``nvidia-smi`` CSV output.
        Used by tests to inject a synthetic response without spawning a
        real subprocess.
    torch_module:
        Optional pre-imported torch module (test injection).
    """

    notes: list[str] = []

    # --- Strategy 1: torch.cuda ---
    if torch_module is None:
        torch_module = _try_import("torch")
    if torch_module is not None:
        try:
            cuda = getattr(torch_module, "cuda", None)
            if cuda is not None and cuda.is_available():
                count = int(cuda.device_count())
                names: list[str] = []
                vram: list[int] = []
                for i in range(count):
                    try:
                        names.append(str(cuda.get_device_name(i)))
                    except Exception:  # noqa: BLE001
                        names.append(f"cuda:{i}")
                    try:
                        props = cuda.get_device_properties(i)
                        vram.append(int(getattr(props, "total_memory", 0) // (1024 * 1024)))
                    except Exception:  # noqa: BLE001
                        vram.append(0)
                cuda_ver = getattr(getattr(torch_module, "version", None), "cuda", None)
                return GPUProbe(
                    available=True,
                    status=HealthStatus.OK,
                    device_count=count,
                    device_names=tuple(names),
                    total_vram_mb=tuple(vram),
                    driver_version=None,
                    cuda_version=str(cuda_ver) if cuda_ver else None,
                    detection_method="torch.cuda",
                    notes=tuple(notes),
                )
            notes.append("torch present but cuda.is_available() == False")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"torch.cuda probe raised: {exc!r}")
    else:
        notes.append("torch not importable")

    # --- Strategy 2: nvidia-smi subprocess ---
    if nvidia_smi_runner is None:
        nvidia_smi_runner = _default_nvidia_smi_runner

    raw = None
    try:
        raw = nvidia_smi_runner()
    except Exception as exc:  # noqa: BLE001
        notes.append(f"nvidia-smi runner raised: {exc!r}")

    if raw:
        names: list[str] = []
        vram: list[int] = []
        driver_version: Optional[str] = None
        cuda_version: Optional[str] = None
        for line in raw.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                names.append(parts[0])
                try:
                    # nvidia-smi reports "12288 MiB" or just "12288"
                    raw_mem = parts[1].split()[0]
                    vram.append(int(raw_mem))
                except (ValueError, IndexError):
                    vram.append(0)
            if len(parts) >= 3 and driver_version is None:
                driver_version = parts[2]
        if names:
            return GPUProbe(
                available=True,
                status=HealthStatus.OK,
                device_count=len(names),
                device_names=tuple(names),
                total_vram_mb=tuple(vram),
                driver_version=driver_version,
                cuda_version=cuda_version,
                detection_method="nvidia-smi",
                notes=tuple(notes),
            )

    notes.append("no GPU detected through any strategy")
    return GPUProbe(
        available=False,
        status=HealthStatus.MISSING,
        device_count=0,
        device_names=(),
        total_vram_mb=(),
        driver_version=None,
        cuda_version=None,
        detection_method="none",
        notes=tuple(notes),
    )


def _default_nvidia_smi_runner() -> Optional[str]:
    """Invoke ``nvidia-smi`` if available, otherwise return ``None``.

    Strictly read-only and bounded by a 4-second timeout to avoid hanging
    on a wedged driver. This function is the *only* place in the radar
    that spawns a subprocess.
    """

    binary = shutil.which("nvidia-smi")
    if not binary:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                binary,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4.0,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout
        logger.debug("nvidia-smi exited rc=%s stderr=%s",
                     completed.returncode, completed.stderr.strip())
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("nvidia-smi invocation failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Python environment probe
# ---------------------------------------------------------------------------

# Project's runtime-critical dependencies. Kept narrow on purpose — the radar
# is not a full pip-audit; it answers "can the rest of the pipeline import?".
_RUNTIME_CRITICAL_PACKAGES: tuple[str, ...] = (
    "numpy",
    "Pillow",
    "scipy",
    "requests",
    "networkx",
)


def _probe_python_env(packages: Iterable[str] = _RUNTIME_CRITICAL_PACKAGES) -> PythonEnvironmentProbe:
    """Snapshot interpreter info and check critical dependency versions."""

    is_venv = (
        getattr(sys, "real_prefix", None) is not None
        or sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or os.environ.get("VIRTUAL_ENV") is not None
        or os.environ.get("CONDA_PREFIX") is not None
    )

    versions: dict[str, Optional[str]] = {}
    missing: list[str] = []
    for pkg in packages:
        try:
            versions[pkg] = importlib_metadata.version(pkg)
        except importlib_metadata.PackageNotFoundError:
            versions[pkg] = None
            missing.append(pkg)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.debug("preflight_radar: version probe for %s failed: %s", pkg, exc)
            versions[pkg] = None
            missing.append(pkg)

    if not missing:
        status = HealthStatus.OK
    elif len(missing) <= 2:
        status = HealthStatus.DEGRADED
    else:
        status = HealthStatus.MISSING

    return PythonEnvironmentProbe(
        python_version=platform.python_version(),
        executable=sys.executable,
        platform=f"{platform.system()} {platform.release()} ({sysconfig.get_platform()})",
        is_virtualenv=bool(is_venv),
        required_packages=versions,
        missing_packages=tuple(missing),
        status=status,
    )


# ---------------------------------------------------------------------------
# ComfyUI discovery (heuristic, multi-strategy)
# ---------------------------------------------------------------------------

# Common installation prefixes for major OSes. These are *probed*, never
# *created*. Order matters: more specific user-local paths first.
_DEFAULT_CANDIDATE_PARENTS: tuple[str, ...] = (
    # POSIX user-local
    "~/ComfyUI",
    "~/comfyui",
    "~/Documents/ComfyUI",
    "~/code/ComfyUI",
    "~/projects/ComfyUI",
    "~/dev/ComfyUI",
    # Windows-style (works on WSL too)
    "~/AppData/Local/Programs/ComfyUI",
    # System-wide
    "/opt/ComfyUI",
    "/usr/local/ComfyUI",
    # Windows drive roots
    "C:/ComfyUI",
    "C:/AI/ComfyUI",
    "C:/Tools/ComfyUI",
    "D:/ComfyUI",
    "D:/AI/ComfyUI",
    "E:/ComfyUI",
    "E:/AI/ComfyUI",
    "F:/ComfyUI",
)


def _looks_like_comfyui_root(path: Path) -> bool:
    """Return True if ``path`` resembles a ComfyUI installation root.

    We use a *triangulated* heuristic: a real ComfyUI root contains both
    ``main.py`` and the canonical ``custom_nodes`` directory. A coincidental
    user folder named ``ComfyUI`` will not pass this check.
    """

    try:
        resolved = path.resolve()
    except OSError:
        return False
    if not resolved.is_dir():
        return False
    main_py = resolved / "main.py"
    custom_nodes = resolved / "custom_nodes"
    return main_py.is_file() and custom_nodes.is_dir()


def _scan_processes_for_comfyui(psutil_module: Any) -> list[tuple[int, Path]]:
    """Walk the process table and pull out ComfyUI candidate roots.

    Strategy: any process whose ``cmdline`` mentions ``main.py`` and whose
    name is python-flavoured is a candidate. We then derive the working
    root either from the cmdline argument's parent or from the process'
    ``cwd``. All ``psutil`` exceptions are swallowed — the radar must be
    crash-proof in environments with locked-down /proc.
    """

    if psutil_module is None:
        return []
    AccessDenied = getattr(psutil_module, "AccessDenied", Exception)
    NoSuchProcess = getattr(psutil_module, "NoSuchProcess", Exception)
    ZombieProcess = getattr(psutil_module, "ZombieProcess", Exception)

    hits: list[tuple[int, Path]] = []
    for proc in _safe_iter_processes(psutil_module):
        try:
            info = proc.info if hasattr(proc, "info") else {}
            name = (info.get("name") or "").lower()
            cmdline = info.get("cmdline") or []
            cwd = info.get("cwd")
            if not cmdline:
                continue
            joined = " ".join(str(p) for p in cmdline).lower()
            looks_python = "python" in name or any(
                "python" in str(p).lower() for p in cmdline[:1]
            )
            if not looks_python:
                continue
            if "main.py" not in joined and "comfyui" not in joined:
                continue
            for arg in cmdline:
                arg_str = str(arg)
                if arg_str.endswith("main.py"):
                    candidate = Path(arg_str).expanduser()
                    if not candidate.is_absolute() and cwd:
                        candidate = Path(cwd) / candidate
                    candidate = candidate.parent
                    if _looks_like_comfyui_root(candidate):
                        hits.append((int(info.get("pid") or 0), candidate.resolve()))
                        break
            else:
                if cwd:
                    candidate = Path(cwd)
                    if _looks_like_comfyui_root(candidate):
                        hits.append((int(info.get("pid") or 0), candidate.resolve()))
        except (AccessDenied, NoSuchProcess, ZombieProcess):
            continue
        except OSError:
            continue
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.debug("preflight_radar: unexpected error probing process: %s", exc)
            continue
    return hits


def _scan_filesystem_for_comfyui(extra_candidates: Iterable[Path] = ()) -> list[Path]:
    """Heuristic filesystem scan for a ComfyUI root.

    Searches:
      - ``$COMFYUI_HOME``
      - The list of conventional install locations
      - Sibling directories of the current working directory
      - Any caller-supplied ``extra_candidates``

    Never recurses — only direct directory probes — to keep latency O(1).
    """

    candidates: list[Path] = []

    env_home = os.environ.get("COMFYUI_HOME")
    if env_home:
        candidates.append(Path(env_home).expanduser())

    for raw in _DEFAULT_CANDIDATE_PARENTS:
        candidates.append(Path(raw).expanduser())

    cwd = Path.cwd()
    candidates.extend([
        cwd / "ComfyUI",
        cwd.parent / "ComfyUI",
        cwd.parent / "comfyui",
    ])

    candidates.extend(Path(c).expanduser() for c in extra_candidates)

    seen: set[str] = set()
    matches: list[Path] = []
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if _looks_like_comfyui_root(resolved):
            matches.append(resolved)
    return matches


def _check_asset(root: Path, manifest_entry: dict[str, str]) -> AssetCheck:
    """Symlink-resolved existence check for a single manifest entry."""

    relpath = manifest_entry["relpath"]
    expected = root / relpath
    is_symlink = False
    resolved_str: Optional[str] = None
    exists = False

    try:
        # Detect *any* symlink anywhere on the resolved path so that the
        # caller knows the asset lives behind a redirection (a frequent
        # pattern for power users keeping models on a secondary drive).
        if expected.exists() or expected.is_symlink():
            is_symlink = expected.is_symlink() or any(
                p.is_symlink() for p in [expected, *expected.parents] if p != p.parent
            )
            real = expected.resolve(strict=False)
            resolved_str = str(real)
            exists = real.exists()
    except OSError as exc:
        logger.debug("preflight_radar: asset probe failed for %s: %s", expected, exc)

    if exists:
        status = HealthStatus.OK
    else:
        status = HealthStatus.MISSING

    return AssetCheck(
        name=manifest_entry["name"],
        kind=manifest_entry["kind"],
        expected_relpath=relpath,
        resolved_path=resolved_str,
        exists=exists,
        is_symlink=is_symlink,
        status=status,
    )


def _discover_comfyui(psutil_module: Optional[Any],
                      manifest: Iterable[dict[str, str]] = REQUIRED_COMFYUI_ASSETS,
                      extra_candidates: Iterable[Path] = ()) -> ComfyUIDiscovery:
    """Run the full ComfyUI sniffer pipeline and verify required assets."""

    notes: list[str] = []
    process_pid: Optional[int] = None
    method: Optional[str] = None
    chosen: Optional[Path] = None
    candidates: list[Path] = []

    # 1. Process-table reverse lookup
    if psutil_module is not None:
        for pid, root in _scan_processes_for_comfyui(psutil_module):
            candidates.append(root)
            if chosen is None:
                chosen = root
                process_pid = pid
                method = "process_scan"
                notes.append(f"discovered via psutil pid={pid}")
    else:
        notes.append("psutil not importable — process scan skipped")

    # 2. Heuristic filesystem fallback
    fs_hits = _scan_filesystem_for_comfyui(extra_candidates=extra_candidates)
    for hit in fs_hits:
        if hit not in candidates:
            candidates.append(hit)
        if chosen is None:
            chosen = hit
            method = "filesystem_heuristic"
            notes.append(f"discovered via filesystem heuristic: {hit}")

    if chosen is None:
        return ComfyUIDiscovery(
            found=False,
            root_path=None,
            detection_method=None,
            candidate_roots=tuple(str(c) for c in candidates),
            process_pid=None,
            status=HealthStatus.MISSING,
            asset_checks=(),
            notes=tuple(notes),
        )

    # 3. Asset audit (symlink-resolved)
    asset_checks: list[AssetCheck] = []
    for entry in manifest:
        asset_checks.append(_check_asset(chosen, entry))

    if all(a.exists for a in asset_checks):
        status = HealthStatus.OK
    elif any(a.exists for a in asset_checks):
        status = HealthStatus.DEGRADED
    else:
        status = HealthStatus.MISSING

    return ComfyUIDiscovery(
        found=True,
        root_path=str(chosen),
        detection_method=method,
        candidate_roots=tuple(str(c) for c in candidates),
        process_pid=process_pid,
        status=status,
        asset_checks=tuple(asset_checks),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Verdict synthesis
# ---------------------------------------------------------------------------

def _synthesize_verdict(gpu: GPUProbe,
                        env: PythonEnvironmentProbe,
                        comfy: ComfyUIDiscovery,
                        require_gpu: bool) -> tuple[PreflightVerdict, str,
                                                    tuple[str, ...], tuple[str, ...]]:
    """Combine probe results into the top-level verdict.

    Decision matrix (intentionally explicit, no implicit precedence):

      - ``manual_intervention_required`` if:
          * ComfyUI was not discovered at all, OR
          * ``require_gpu`` is True and no GPU was probed, OR
          * 3+ critical Python packages are missing.
      - ``auto_fixable`` if:
          * ComfyUI was discovered but at least one asset is missing, OR
          * 1-2 Python packages are missing.
      - ``ready`` otherwise.

    The returned ``fixable_actions`` and ``blocking_actions`` are *advisory*
    strings consumed by the future Phase-2 self-healing assembler.
    """

    fixable: list[str] = []
    blocking: list[str] = []

    if not comfy.found:
        blocking.append(
            "comfyui_not_found: scan process table and conventional install locations"
            " yielded no candidate; user must specify COMFYUI_HOME or install ComfyUI"
        )
    else:
        for check in comfy.asset_checks:
            if not check.exists:
                fixable.append(
                    f"missing_asset:{check.name} -> {check.expected_relpath}"
                )

    if env.missing_packages:
        if len(env.missing_packages) >= 3:
            blocking.append(
                f"python_env_critical: missing {len(env.missing_packages)} core packages "
                f"({', '.join(env.missing_packages)})"
            )
        else:
            for pkg in env.missing_packages:
                fixable.append(f"missing_package:{pkg}")

    if require_gpu and not gpu.available:
        blocking.append("gpu_required_but_not_available")

    if blocking:
        verdict = PreflightVerdict.MANUAL_INTERVENTION_REQUIRED
        summary = (
            f"manual intervention required: {len(blocking)} blocking issue(s), "
            f"{len(fixable)} auto-fixable"
        )
    elif fixable:
        verdict = PreflightVerdict.AUTO_FIXABLE
        summary = f"auto-fixable: {len(fixable)} repair action(s) queued"
    else:
        verdict = PreflightVerdict.READY
        summary = "ready: GPU + python_env + comfyui all green"

    return verdict, summary, tuple(fixable), tuple(blocking)


# ---------------------------------------------------------------------------
# Public façade — PreflightRadar
# ---------------------------------------------------------------------------

class PreflightRadar:
    """High-level façade that orchestrates the three probes and emits a report.

    The class is parameterized for *test injection*: every external
    dependency (the ``psutil`` module, the ``nvidia-smi`` runner, the
    ``torch`` module, the asset manifest, extra candidate roots) can be
    swapped without monkey-patching the global state. This is the same
    Dependency Inversion discipline used by the project's
    ``BackendRegistry`` and ``MicrokernelOrchestrator``.

    Parameters
    ----------
    psutil_module:
        Override the ``psutil`` import (defaults to a best-effort import,
        ``None`` if psutil is not installed).
    nvidia_smi_runner:
        Callable returning the raw ``nvidia-smi`` CSV output, or ``None``.
    torch_module:
        Optional pre-imported torch module.
    manifest:
        Iterable of asset descriptors; defaults to ``REQUIRED_COMFYUI_ASSETS``.
    extra_candidate_roots:
        Additional ComfyUI candidate parent directories (e.g. project-local
        sibling installations).
    require_gpu:
        If True, the absence of a GPU escalates the verdict to
        ``manual_intervention_required``. Defaults to False so that headless
        CI and CPU-only dry-runs remain ``ready``.
    packages:
        Iterable of pip-package names whose presence determines the
        ``python_env`` health rating.
    """

    def __init__(
        self,
        *,
        psutil_module: Optional[Any] = None,
        nvidia_smi_runner: Optional[Callable[[], Optional[str]]] = None,
        torch_module: Optional[Any] = None,
        manifest: Iterable[dict[str, str]] = REQUIRED_COMFYUI_ASSETS,
        extra_candidate_roots: Iterable[Path] = (),
        require_gpu: bool = False,
        packages: Iterable[str] = _RUNTIME_CRITICAL_PACKAGES,
    ) -> None:
        if psutil_module is None:
            psutil_module = _try_import("psutil")
        self._psutil = psutil_module
        self._nvidia_smi_runner = nvidia_smi_runner
        self._torch = torch_module
        self._manifest = tuple(manifest)
        self._extra_candidates = tuple(extra_candidate_roots)
        self._require_gpu = bool(require_gpu)
        self._packages = tuple(packages)

    # ------------------------------------------------------------------
    # Probe entry points (each is independently testable)
    # ------------------------------------------------------------------

    def probe_gpu(self) -> GPUProbe:
        return _probe_gpu(
            nvidia_smi_runner=self._nvidia_smi_runner,
            torch_module=self._torch,
        )

    def probe_python_env(self) -> PythonEnvironmentProbe:
        return _probe_python_env(self._packages)

    def discover_comfyui(self) -> ComfyUIDiscovery:
        return _discover_comfyui(
            psutil_module=self._psutil,
            manifest=self._manifest,
            extra_candidates=self._extra_candidates,
        )

    # ------------------------------------------------------------------
    # End-to-end scan
    # ------------------------------------------------------------------

    def scan(self) -> PreflightReport:
        """Run all three probes and synthesize the verdict.

        This method is the canonical public entry point. It is *guaranteed*
        not to raise: even if every individual probe fails, the result is
        a ``PreflightReport`` with ``MANUAL_INTERVENTION_REQUIRED``.
        """

        from datetime import datetime, timezone

        try:
            gpu = self.probe_gpu()
        except Exception as exc:  # pragma: no cover - last-resort safety
            logger.exception("preflight_radar: gpu probe crashed: %s", exc)
            gpu = GPUProbe(
                available=False, status=HealthStatus.UNKNOWN, device_count=0,
                device_names=(), total_vram_mb=(), driver_version=None,
                cuda_version=None, detection_method="error",
                notes=(f"probe crashed: {exc!r}",),
            )

        try:
            env = self.probe_python_env()
        except Exception as exc:  # pragma: no cover
            logger.exception("preflight_radar: python_env probe crashed: %s", exc)
            env = PythonEnvironmentProbe(
                python_version=platform.python_version(), executable=sys.executable,
                platform=platform.platform(), is_virtualenv=False,
                required_packages={}, missing_packages=(),
                status=HealthStatus.UNKNOWN,
            )

        try:
            comfy = self.discover_comfyui()
        except Exception as exc:  # pragma: no cover
            logger.exception("preflight_radar: comfyui probe crashed: %s", exc)
            comfy = ComfyUIDiscovery(
                found=False, root_path=None, detection_method=None,
                candidate_roots=(), process_pid=None,
                status=HealthStatus.UNKNOWN,
                asset_checks=(), notes=(f"probe crashed: {exc!r}",),
            )

        verdict, summary, fixable, blocking = _synthesize_verdict(
            gpu=gpu, env=env, comfy=comfy, require_gpu=self._require_gpu,
        )

        return PreflightReport(
            verdict=verdict,
            summary=summary,
            gpu=gpu,
            python_env=env,
            comfyui=comfy,
            fixable_actions=fixable,
            blocking_actions=blocking,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# Convenience module-level entry point
# ---------------------------------------------------------------------------

def scan_preflight(*, require_gpu: bool = False,
                   extra_candidate_roots: Iterable[Path] = ()) -> PreflightReport:
    """Top-level helper: instantiate the radar with defaults and run a scan."""

    radar = PreflightRadar(
        require_gpu=require_gpu,
        extra_candidate_roots=extra_candidate_roots,
    )
    return radar.scan()


# ---------------------------------------------------------------------------
# Stdout-safe CLI shim — JSON only, never prints diagnostics on stdout.
# ---------------------------------------------------------------------------

def _cli(argv: Optional[list[str]] = None) -> int:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(
        prog="preflight-radar",
        description=(
            "Read-only environment + ComfyUI sniffer. Emits a JSON "
            "PreflightReport on stdout; logging goes to stderr."
        ),
    )
    parser.add_argument("--require-gpu", action="store_true",
                        help="Fail to MANUAL_INTERVENTION_REQUIRED if no GPU is detected.")
    parser.add_argument("--extra-root", action="append", default=[],
                        help="Additional ComfyUI candidate root path (repeatable).")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="[preflight] %(message)s")

    report = scan_preflight(
        require_gpu=args.require_gpu,
        extra_candidate_roots=[Path(p) for p in args.extra_root],
    )
    print(report.to_json())
    if report.verdict == PreflightVerdict.MANUAL_INTERVENTION_REQUIRED:
        return 2
    if report.verdict == PreflightVerdict.AUTO_FIXABLE:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
