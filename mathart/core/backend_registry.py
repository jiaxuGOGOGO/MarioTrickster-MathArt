"""Backend Registry — LLVM-Inspired Dynamic Self-Registration for Export Backends.

SESSION-064: Paradigm Shift #1 — From Centralized Routing to Plugin Architecture.

This module implements the **Registry Pattern** inspired by LLVM's pass
infrastructure and Chris Lattner's Inversion of Control (IoC) design:

    1. Each backend is a standalone module that self-registers via the
       ``@register_backend`` decorator at import time.
    2. The ``BackendRegistry`` singleton discovers all registered backends
       and provides lookup by name, family, or capability.
    3. The trunk pipeline (``AssetPipeline``, ``EvolutionOrchestrator``)
       never needs modification when new backends are added.
    4. Backends declare their input requirements and output artifact families
       via ``BackendMeta``, enabling automatic dependency resolution.

This is the Python equivalent of LLVM's ``TargetRegistry`` + ``PassRegistry``:
instead of ``RegisterTarget<X86TargetMachine>`` we use
``@register_backend("dimension_uplift")``.

Architecture Diagram::

    ┌─────────────────────────────────────────────────────────┐
    │                  BackendRegistry (Singleton)            │
    │                                                         │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
    │  │ motion_2d   │  │ urp_2d      │  │ dim_uplift  │... │
    │  │ (backend)   │  │ (backend)   │  │ (backend)   │    │
    │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘    │
    │         │                │                │            │
    │         ▼                ▼                ▼            │
    │     BackendMeta      BackendMeta      BackendMeta     │
    │     (contract)       (contract)       (contract)      │
    └─────────────────────────────────────────────────────────┘
              │                │                │
              ▼                ▼                ▼
         ArtifactManifest ArtifactManifest ArtifactManifest

References
----------
[1] Chris Lattner, "LLVM", The Architecture of Open Source Applications, 2012.
[2] LLVM TargetRegistry: llvm/include/llvm/MC/TargetRegistry.h
[3] Martin Fowler, "Inversion of Control Containers and the Dependency
    Injection pattern", martinfowler.com, 2004.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Type, runtime_checkable

from mathart.core.backend_types import BackendType, backend_type_value

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend Capability Enum
# ---------------------------------------------------------------------------

class BackendCapability(Enum):
    """Capabilities a backend can declare.

    Inspired by LLVM's ``TargetMachine::CodeGenFileType`` — each backend
    advertises what it can produce so the pipeline can route accordingly.
    """
    SPRITE_EXPORT = auto()
    MESH_EXPORT = auto()
    VAT_EXPORT = auto()
    SHADER_EXPORT = auto()
    ANIMATION_EXPORT = auto()
    LEVEL_EXPORT = auto()
    VFX_EXPORT = auto()
    ATLAS_EXPORT = auto()
    EVOLUTION_BRIDGE = auto()
    KNOWLEDGE_DISTILL = auto()
    # SESSION-071 (P1-XPBD-3): 3D physics simulation capability — declares
    # that a backend can consume an upstream MOTION_UMR manifest and produce
    # a 3D-enriched motion clip plus contact manifold.
    PHYSICS_SIMULATION = auto()
    # SESSION-072 (P1-DISTILL-1A): Hot-path instrumentation capability.
    # Backends declaring this flag are expected to read a TelemetrySink from
    # the context reserved key ``__telemetry_sink__`` and call
    # ``sink.record(...)`` from their inner loops.  Backends that do NOT
    # declare this capability MUST NOT read the sink — the bridge enforces
    # this invariant.  Design reference: eBPF / DTrace zero-overhead dynamic
    # tracing — the probe is opt-in and zero-cost when absent.
    HOT_PATH_INSTRUMENTED = auto()
    # SESSION-075 (P1-DISTILL-1B): GPU-accelerated execution capability.
    # Backends declaring this flag provide an explicit accelerated device
    # path and are expected to surface enough telemetry for CPU/GPU A/B
    # benchmarking without leaking device-specific details into the trunk.
    GPU_ACCELERATED = auto()
    # SESSION-073 (P1-XPBD-4): Continuous Collision Detection capability.
    # Backends declaring this flag implement CCD sweep tests gated by a
    # velocity threshold, preventing fast-moving bodies from tunneling
    # through thin geometry (Erin Catto GDC 2013 / Brian Mirtich 1996).
    CCD_ENABLED = auto()
    # SESSION-074 (P1-MIGRATE-2): Evolution bridge capability marker.
    # Backends declaring this flag are evolution-domain bridges migrated
    # from the legacy EvolutionOrchestrator into the microkernel registry.
    # The orchestrator uses this capability to dynamically discover all
    # evolution backends without hardcoded import lists.
    EVOLUTION_DOMAIN = auto()
    # SESSION-083 (P1-B4-1): RL rollout / training capability marker.
    # Backends declaring this capability produce training-loop artifacts such
    # as micro-batch rollout reports without requiring trunk routing changes.
    RL_TRAINING = auto()
    # SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH): ComfyUI
    # headless render capability.  Backends declaring this flag provide
    # end-to-end BFF payload mutation, ephemeral upload, WebSocket
    # telemetry, and VRAM garbage collection for production AI rendering.
    COMFYUI_RENDER = auto()
    # SESSION-163 (P0-SESSION-161-COMFYUI-API-BRIDGE): Full-array AI render
    # streaming capability.  Backends declaring this flag iterate all motion
    # actions from the dynamic registry, stream baked guides to ComfyUI with
    # circuit breaker protection, and hydrate the pipeline context with
    # renamed AI-rendered outputs.
    AI_RENDER_STREAM = auto()


# ---------------------------------------------------------------------------
# Backend Protocol (Interface Contract)
# ---------------------------------------------------------------------------

@runtime_checkable
class BackendProtocol(Protocol):
    """Protocol that all backends must satisfy.

    This is the "Pass" base class equivalent from LLVM. Every backend
    must implement ``execute()`` which takes a context dict and returns
    an ``ArtifactManifest``.
    """

    @property
    def name(self) -> str:
        """Unique backend identifier."""
        ...

    @property
    def meta(self) -> "BackendMeta":
        """Backend metadata describing capabilities and requirements."""
        ...

    def execute(self, context: dict[str, Any]) -> Any:
        """Execute the backend pipeline and return an ArtifactManifest."""
        ...


# ---------------------------------------------------------------------------
# Backend Metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BackendMeta:
    """Immutable metadata for a registered backend.

    Inspired by LLVM's ``Target`` descriptor — captures everything the
    registry needs to know about a backend without instantiating it.

    Attributes
    ----------
    name : str
        Unique identifier (e.g., ``"motion_2d"``, ``"dimension_uplift"``).
    display_name : str
        Human-readable name for reports and UI.
    version : str
        Semantic version of this backend.
    artifact_families : tuple[str, ...]
        Output artifact families this backend can produce.
    capabilities : tuple[BackendCapability, ...]
        Declared capabilities.
    input_requirements : tuple[str, ...]
        Required input keys in the execution context.
    dependencies : tuple[str, ...]
        Names of other backends this one depends on.
    author : str
        Attribution for the backend implementation.
    session_origin : str
        Session that introduced this backend.
    """
    name: str | BackendType
    display_name: str = ""
    version: str = "1.0.0"
    artifact_families: tuple[str, ...] = ()
    capabilities: tuple[BackendCapability, ...] = ()
    input_requirements: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    author: str = "MarioTrickster-MathArt"
    session_origin: str = "SESSION-064"
    # SESSION-073 (P1-MIGRATE-3): Schema version pinning per backend.
    # When set, validate_artifact() will block manifests whose version
    # is lower than the declared minimum, preventing silent schema
    # downgrade (Pixar USD Schema Compliance pattern).
    schema_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", backend_type_value(self.name))
        object.__setattr__(
            self,
            "dependencies",
            tuple(backend_type_value(dep) for dep in self.dependencies),
        )


# ---------------------------------------------------------------------------
# Backend Registry Singleton
# ---------------------------------------------------------------------------

class BackendRegistry:
    """Singleton registry for all export backends.

    This is the central "motherboard with slots" — backends plug in via
    ``@register_backend`` and the pipeline discovers them here.

    The registry supports:
    - Registration by name (``register``)
    - Lookup by name (``get``)
    - Lookup by capability (``find_by_capability``)
    - Lookup by artifact family (``find_by_family``)
    - Auto-discovery of backends in a package (``discover``)
    - Dependency resolution (``resolve_dependencies``)

    Thread Safety
    -------------
    SESSION-090 (P1-MIGRATE-4) upgrades thread safety: a ``_reload_lock``
    (``threading.Lock``) serializes all mutation operations (register,
    unregister, reload) so the daemon file-watcher thread can safely
    trigger hot-reload without corrupting the registry dict.  Read-only
    lookups remain lock-free for zero-overhead pipeline execution.
    """

    _instance: Optional["BackendRegistry"] = None
    _backends: dict[str, tuple[BackendMeta, Type]] = {}
    _builtins_loaded: bool = False
    # SESSION-090 (P1-MIGRATE-4): Per-module reload tracking.
    # Maps canonical backend name → the fully-qualified module name that
    # registered it, enabling targeted ``importlib.reload`` without
    # scanning all of sys.modules.
    _backend_module_map: dict[str, str] = {}
    # SESSION-090 (P1-MIGRATE-4): Thread-safe mutation lock.
    # Uses RLock (reentrant lock) because reload() holds the lock while
    # calling importlib.import_module(), which triggers @register_backend
    # → register() on the same thread.  A plain Lock would deadlock.
    # Design reference: Erlang/OTP code_server uses a reentrant gen_server
    # call pattern for the same reason.
    _reload_lock: threading.RLock = threading.RLock()

    def __new__(cls) -> "BackendRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (for testing)."""
        cls._backends = {}
        cls._builtins_loaded = False

    def register(self, meta: BackendMeta, backend_class: Type) -> None:
        """Register a backend class with its metadata.

        Parameters
        ----------
        meta : BackendMeta
            Backend descriptor.
        backend_class : Type
            The class implementing ``BackendProtocol``.

        Raises
        ------
        ValueError
            If a backend with the same name is already registered.
        """
        canonical_name = backend_type_value(meta.name)
        with self._reload_lock:
            if canonical_name in self._backends:
                existing_meta, _ = self._backends[canonical_name]
                if existing_meta.version < meta.version:
                    logger.info(
                        "Upgrading backend %r from v%s to v%s",
                        canonical_name, existing_meta.version, meta.version,
                    )
                    self._backends[canonical_name] = (meta, backend_class)
                else:
                    logger.warning(
                        "Backend %r already registered (v%s). Skipping v%s.",
                        canonical_name, existing_meta.version, meta.version,
                    )
                return
            self._backends[canonical_name] = (meta, backend_class)
            # SESSION-090 (P1-MIGRATE-4): Track which module registered this
            # backend so targeted reload can find the right sys.modules key.
            caller_module = getattr(backend_class, "__module__", None)
            if caller_module:
                self._backend_module_map[canonical_name] = caller_module
            logger.debug("Registered backend: %s (v%s)", canonical_name, meta.version)

    def get(self, name: str) -> Optional[tuple[BackendMeta, Type]]:
        """Look up a backend by name.

        Returns ``None`` if not found (fail-soft for optional backends).
        """
        return self._backends.get(backend_type_value(name))

    def get_or_raise(self, name: str) -> tuple[BackendMeta, Type]:
        """Look up a backend by name, raising if not found."""
        canonical_name = backend_type_value(name)
        result = self._backends.get(canonical_name)
        if result is None:
            available = ", ".join(sorted(self._backends.keys()))
            raise KeyError(
                f"Backend {canonical_name!r} not registered. Available: [{available}]"
            )
        return result

    def find_by_capability(
        self, capability: BackendCapability,
    ) -> list[tuple[BackendMeta, Type]]:
        """Find all backends declaring a given capability."""
        return [
            (meta, cls)
            for meta, cls in self._backends.values()
            if capability in meta.capabilities
        ]

    def find_by_family(self, family: str) -> list[tuple[BackendMeta, Type]]:
        """Find all backends that produce a given artifact family."""
        return [
            (meta, cls)
            for meta, cls in self._backends.values()
            if family in meta.artifact_families
        ]

    def all_backends(self) -> dict[str, tuple[BackendMeta, Type]]:
        """Return a copy of all registered backends."""
        return dict(self._backends)

    def get_meta(self, name: str) -> BackendMeta | None:
        """SESSION-186 兼容接口：返回指定 backend 的 BackendMeta。

        Returns ``None`` if the backend is not registered (no exception),
        keeping the API friendlier for diagnostic / introspection callers.
        """
        try:
            canonical_name = backend_type_value(name)
        except Exception:
            canonical_name = str(name)
        entry = self._backends.get(canonical_name)
        if entry is None:
            return None
        meta, _cls = entry
        return meta

    def resolve_dependencies(self, name: str) -> list[str]:
        """Topological sort of backend dependencies (BFS).

        Returns an ordered list of backend names that must be executed
        before the target backend.
        """
        visited: set[str] = set()
        order: list[str] = []

        def _visit(n: str) -> None:
            canonical_name = backend_type_value(n)
            if canonical_name in visited:
                return
            visited.add(canonical_name)
            entry = self._backends.get(canonical_name)
            if entry is None:
                return
            meta, _ = entry
            for dep in meta.dependencies:
                _visit(dep)
            order.append(canonical_name)

        _visit(name)
        return order

    def discover(self, package_path: str) -> int:
        """Auto-discover backends in a Python package.

        Imports all modules in the given package path, triggering
        ``@register_backend`` decorators. Returns the count of
        newly discovered backends.

        This is analogous to LLVM's ``--load`` mechanism for dynamic
        pass plugins.
        """
        before = len(self._backends)
        try:
            pkg = importlib.import_module(package_path)
            pkg_path = getattr(pkg, "__path__", None)
            if pkg_path is None:
                return 0
            for importer, modname, ispkg in pkgutil.walk_packages(
                pkg_path, prefix=package_path + ".",
            ):
                try:
                    importlib.import_module(modname)
                except Exception as e:
                    logger.debug("Failed to import %s: %s", modname, e)
        except Exception as e:
            logger.debug("Failed to discover package %s: %s", package_path, e)
        return len(self._backends) - before

    # ------------------------------------------------------------------
    # SESSION-090 (P1-MIGRATE-4): Hot-Reload Primitives
    # ------------------------------------------------------------------

    def unregister(self, name: str) -> bool:
        """Atomically remove a single backend from the registry.

        This is the **targeted eviction** primitive required by the
        hot-reload ecosystem.  It surgically pops only the named backend
        from ``_backends`` and ``_backend_module_map`` — all other entries
        remain untouched (Erlang/OTP two-version coexistence discipline).

        Parameters
        ----------
        name : str
            Backend name (canonical or alias).

        Returns
        -------
        bool
            ``True`` if the backend was found and removed, ``False`` otherwise.
        """
        canonical_name = backend_type_value(name)
        with self._reload_lock:
            removed = self._backends.pop(canonical_name, None)
            self._backend_module_map.pop(canonical_name, None)
            if removed is not None:
                logger.info("Unregistered backend: %s", canonical_name)
                return True
            logger.warning("Unregister: backend %r not found.", canonical_name)
            return False

    def reload(self, name: str) -> bool:
        """Atomically unregister, reimport, and re-register a single backend.

        Implements the full Erlang/OTP-inspired hot-swap sequence:

        1. **Evict** the old ``(BackendMeta, Type)`` tuple from ``_backends``.
        2. **Deep-clean** the target module from ``sys.modules`` so
           ``importlib.reload`` fetches fresh bytecode from disk.
        3. **Re-import** the module, which re-executes the top-level
           ``@register_backend`` decorator and inserts the new class.
        4. **Verify** the new class ``id()`` differs from the old one,
           catching the "zombie reference" anti-pattern.

        The entire sequence is protected by ``_reload_lock`` to prevent
        concurrent mutation from the daemon file-watcher thread.

        Parameters
        ----------
        name : str
            Backend name (canonical or alias).

        Returns
        -------
        bool
            ``True`` if the reload succeeded and a new class was registered.

        Raises
        ------
        RuntimeError
            If the module cannot be found or the reload produces a
            SyntaxError / ImportError.
        """
        canonical_name = backend_type_value(name)
        with self._reload_lock:
            # --- Step 0: Capture old class identity for zombie detection ---
            old_entry = self._backends.get(canonical_name)
            old_class_id = id(old_entry[1]) if old_entry else None
            module_name = self._backend_module_map.get(canonical_name)

            if module_name is None:
                raise RuntimeError(
                    f"Cannot reload backend {canonical_name!r}: "
                    f"no module mapping found. Was it registered via "
                    f"@register_backend?"
                )

            # --- Step 1: Evict old entry (targeted, NOT clear()) ---
            self._backends.pop(canonical_name, None)
            # Do NOT pop from _backend_module_map yet — we need the
            # module name for reimport.

            # --- Step 2: Deep-clean sys.modules, bytecode cache, and
            #     import finder caches ---
            # Strategy: (a) pop the module from sys.modules, (b) purge
            # __pycache__ .pyc files for the module so Python does not
            # serve stale bytecode, (c) call invalidate_caches() so
            # FileFinder picks up the new source.  Then import_module()
            # will compile fresh bytecode from the updated .py file.
            # This is the Python equivalent of OSGi classloader isolation.
            old_module_obj = sys.modules.pop(module_name, None)

            # Purge __pycache__ for the target module
            if old_module_obj is not None:
                src_file = getattr(old_module_obj, "__file__", None)
                if src_file:
                    import shutil
                    cache_dir = Path(src_file).parent / "__pycache__"
                    if cache_dir.is_dir():
                        # Only remove .pyc files matching this module stem
                        stem = Path(src_file).stem
                        for pyc in cache_dir.glob(f"{stem}*.pyc"):
                            try:
                                pyc.unlink()
                            except OSError:
                                pass

            # Invalidate all import finder caches
            importlib.invalidate_caches()

            # --- Step 3: Re-import the module (fresh import, not reload) ---
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                # Restore the old entry on failure to prevent registry
                # corruption (atomic rollback).
                if old_entry is not None:
                    self._backends[canonical_name] = old_entry
                if old_module_obj is not None:
                    sys.modules[module_name] = old_module_obj
                logger.error(
                    "Hot-reload FAILED for %s: %s. Old version restored.",
                    canonical_name, exc,
                )
                raise RuntimeError(
                    f"Hot-reload failed for {canonical_name!r}: {exc}"
                ) from exc

            # --- Step 4: Verify new class registered & zombie check ---
            new_entry = self._backends.get(canonical_name)
            if new_entry is None:
                # The reimported module did not re-register — restore old.
                if old_entry is not None:
                    self._backends[canonical_name] = old_entry
                raise RuntimeError(
                    f"Hot-reload of {canonical_name!r}: module reimported "
                    f"but backend did not re-register. Check "
                    f"@register_backend decorator."
                )

            new_class_id = id(new_entry[1])
            if old_class_id is not None and new_class_id == old_class_id:
                logger.warning(
                    "Hot-reload of %s: class id() unchanged (%d). "
                    "Possible zombie reference — verify sys.modules cleanup.",
                    canonical_name, new_class_id,
                )

            logger.info(
                "Hot-reload SUCCESS: %s (old_id=%s → new_id=%s)",
                canonical_name, old_class_id, new_class_id,
            )
            return True

    def get_watched_package_paths(self) -> list[str]:
        """Return filesystem paths that should be monitored for hot-reload.

        Derives paths from the same package roots used by ``discover()``
        — currently ``mathart.core`` and ``mathart.export``.  The daemon
        file-watcher uses these to set up ``watchdog`` observers without
        any hardcoded directory strings.

        Returns
        -------
        list[str]
            Absolute filesystem directory paths.
        """
        paths: list[str] = []
        for pkg_name in ("mathart.core", "mathart.export"):
            try:
                pkg = importlib.import_module(pkg_name)
                pkg_path = getattr(pkg, "__path__", None)
                if pkg_path:
                    for p in pkg_path:
                        abs_p = str(Path(p).resolve())
                        if abs_p not in paths:
                            paths.append(abs_p)
            except Exception:
                pass
        return paths

    def module_to_backend_name(self, module_name: str) -> Optional[str]:
        """Reverse-lookup: given a module name, find the backend it registered.

        Returns
        -------
        str or None
            The canonical backend name, or ``None`` if no mapping exists.
        """
        for bname, mname in self._backend_module_map.items():
            if mname == module_name:
                return bname
        return None

    def summary_table(self) -> str:
        """Generate a Markdown summary table of all registered backends."""
        lines = [
            "| Backend | Version | Families | Capabilities | Session |",
            "|---|---|---|---|---|",
        ]
        for name in sorted(self._backends.keys()):
            meta, _ = self._backends[name]
            families = ", ".join(meta.artifact_families) or "—"
            caps = ", ".join(c.name for c in meta.capabilities) or "—"
            lines.append(
                f"| {meta.display_name or name} | {meta.version} "
                f"| {families} | {caps} | {meta.session_origin} |"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decorator: @register_backend
# ---------------------------------------------------------------------------

def register_backend(
    name: str | BackendType,
    *,
    display_name: str = "",
    version: str = "1.0.0",
    artifact_families: tuple[str, ...] = (),
    capabilities: tuple[BackendCapability, ...] = (),
    input_requirements: tuple[str, ...] = (),
    dependencies: tuple[str | BackendType, ...] = (),
    author: str = "MarioTrickster-MathArt",
    session_origin: str = "SESSION-064",
    schema_version: str = "",
) -> Callable[[Type], Type]:
    """Decorator to register a class as an export backend.

    Usage::

        @register_backend(
            "dimension_uplift",
            display_name="2.5D/3D Dimension Uplift",
            artifact_families=("mesh_obj", "shader_hlsl"),
            capabilities=(BackendCapability.MESH_EXPORT,
                          BackendCapability.SHADER_EXPORT),
        )
        class DimensionUpliftBackend:
            ...

    The decorated class is registered in the global ``BackendRegistry``
    singleton at import time. No trunk code modification required.
    """
    def decorator(cls: Type) -> Type:
        meta = BackendMeta(
            name=name,
            display_name=display_name or name,
            version=version,
            artifact_families=artifact_families,
            capabilities=capabilities,
            input_requirements=input_requirements,
            dependencies=dependencies,
            author=author,
            session_origin=session_origin,
            schema_version=schema_version,
        )
        registry = get_registry()
        registry.register(meta, cls)
        # Attach meta to the class for introspection
        cls._backend_meta = meta
        return cls

    return decorator


def get_registry() -> BackendRegistry:
    """Get the global BackendRegistry singleton and ensure built-ins are loaded."""
    registry = BackendRegistry()
    if not BackendRegistry._builtins_loaded:
        BackendRegistry._builtins_loaded = True
        try:
            importlib.import_module("mathart.core.builtin_backends")
        except Exception as e:
            logger.debug("Failed to auto-load builtin backends: %s", e)
        try:
            importlib.import_module("mathart.core.builtin_niches")
        except Exception as e:
            logger.debug("Failed to auto-load builtin niches: %s", e)
        # SESSION-071 (P1-XPBD-3): auto-register the 3D physics microkernel
        # plugin alongside the other built-in backends so the orchestrator
        # can resolve BackendType.PHYSICS_3D without any explicit import
        # from trunk code.
        try:
            importlib.import_module("mathart.core.physics3d_backend")
        except Exception as e:
            logger.debug("Failed to auto-load physics3d backend: %s", e)
        # SESSION-075 (P1-DISTILL-1B): auto-register the Taichi XPBD
        # benchmark backend so CPU/GPU performance lanes remain fully
        # pluginized and discoverable through the registry.
        try:
            importlib.import_module("mathart.core.taichi_xpbd_backend")
        except Exception as e:
            logger.debug("Failed to auto-load taichi xpbd backend: %s", e)
        # SESSION-074 (P1-MIGRATE-2): auto-register all evolution bridge
        # backends.  Each legacy EvolutionBridge is now a first-class
        # @register_backend plugin discovered here — the orchestrator
        # finds them via BackendCapability.EVOLUTION_DOMAIN.
        try:
            importlib.import_module("mathart.core.evolution_backends")
        except Exception as e:
            logger.debug("Failed to auto-load evolution backends: %s", e)
        # SESSION-076 (P1-DISTILL-3): auto-register the physics-gait
        # distillation backend so the orchestrator can discover it via
        # BackendCapability.EVOLUTION_DOMAIN without any hardcoded import.
        try:
            importlib.import_module("mathart.core.physics_gait_distill_backend")
        except Exception as e:
            logger.debug("Failed to auto-load physics gait distill backend: %s", e)
        # SESSION-078 (P1-DISTILL-4): auto-register the cognitive science
        # distillation backend using the same plugin-discovery path.
        try:
            importlib.import_module("mathart.core.cognitive_distillation_backend")
        except Exception as e:
            logger.debug("Failed to auto-load cognitive distillation backend: %s", e)
        # SESSION-083 (P1-B4-1): auto-register the RL training backend so the
        # microkernel can discover rollout/training execution without any trunk
        # if/else path modification.
        try:
            importlib.import_module("mathart.core.rl_training_backend")
        except Exception as e:
            logger.debug("Failed to auto-load rl training backend: %s", e)
        # SESSION-089 (P1-INDUSTRIAL-34C): auto-register the Dead Cells-style
        # orthographic pixel render backend so the microkernel can discover
        # 3D→2D dimension-reduction rendering without any trunk modification.
        try:
            importlib.import_module("mathart.core.orthographic_pixel_backend")
        except Exception as e:
            logger.debug("Failed to auto-load orthographic pixel backend: %s", e)
        # SESSION-109 (P1-ARCH-6): auto-register the tensor-based level
        # topology extractor backend so the microkernel can discover the
        # rich-topology lane / anchor extractor without any trunk
        # modification.  The backend is a pure plugin — it consumes a
        # logical tile-id grid via context (or upstream WFC manifest)
        # and emits a strongly-typed LEVEL_TOPOLOGY ArtifactManifest.
        try:
            importlib.import_module("mathart.core.level_topology_backend")
        except Exception as e:
            logger.debug("Failed to auto-load level topology backend: %s", e)

        # SESSION-118 (P1-HUMAN-31C): Pseudo-3D paper-doll / mesh-shell
        # deformation backend using tensorized dual quaternion skinning.
        try:
            importlib.import_module("mathart.core.pseudo3d_shell_backend")
        except Exception as e:
            logger.debug("Failed to auto-load pseudo 3D shell backend: %s", e)
        # SESSION-119 (P1-NEW-2): Tensorized Gray-Scott reaction-diffusion
        # organic texture backend.  Self-registers via @register_backend and
        # emits MATERIAL_BUNDLE artifacts with PBR channel semantics.
        try:
            importlib.import_module("mathart.core.reaction_diffusion_backend")
        except Exception as e:
            logger.debug("Failed to auto-load reaction diffusion backend: %s", e)
        # SESSION-124 (P2-UNITY-2DANIM-1): Unity 2D native animation format
        # zero-dependency direct export backend.  Self-registers via
        # @register_backend and emits UNITY_NATIVE_ANIM artifacts.
        try:
            importlib.import_module("mathart.core.unity_2d_anim_backend")
        except Exception as e:
            logger.debug("Failed to auto-load Unity 2D anim backend: %s", e)
        # SESSION-125 (P2-SPINE-PREVIEW-1): Spine JSON tensorized FK preview
        # backend. Self-registers via @register_backend and emits
        # ANIMATION_PREVIEW artifacts for headless visual verification.
        try:
            importlib.import_module("mathart.core.spine_preview_backend")
        except Exception as e:
            logger.debug("Failed to auto-load Spine preview backend: %s", e)
        # SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH):
        # auto-register the ComfyUI headless render backend so the
        # microkernel can discover the BFF payload mutation + render lane
        # without any trunk modification.
        try:
            importlib.import_module("mathart.backend.comfyui_render_backend")
        except Exception as e:
            logger.debug("Failed to auto-load ComfyUI render backend: %s", e)
        # SESSION-152 (P0-SESSION-148-KNOWLEDGE-PROVENANCE-AUDIT):
        # auto-register the non-intrusive provenance audit sidecar backend
        # so the pipeline can discover knowledge lineage tracking without
        # any trunk modification.  Design: OpenLineage + XAI audit trail.
        try:
            importlib.import_module("mathart.core.provenance_audit_backend")
        except Exception as e:
            logger.debug("Failed to auto-load provenance audit backend: %s", e)
        # SESSION-163 (P0-SESSION-161-COMFYUI-API-BRIDGE):
        # auto-register the full-array AI render stream backend so the pipeline
        # can discover artifact hydration streaming without trunk modification.
        try:
            importlib.import_module("mathart.backend.ai_render_stream_backend")
        except Exception as e:
            logger.debug("Failed to auto-load AI render stream backend: %s", e)
        # SESSION-183 (P0-SESSION-183-MICROKERNEL-HUB-AND-VAT-INTEGRATION):
        # auto-register the high-precision float VAT baking backend so the
        # microkernel can discover the industrial-grade HDR VAT export lane
        # without any trunk modification.  Design: SideFX Houdini VAT 3.0
        # float precision specification + Global Bounding Box Quantization.
        try:
            importlib.import_module("mathart.core.high_precision_vat_backend")
        except Exception as e:
            logger.debug("Failed to auto-load high precision VAT backend: %s", e)
        # SESSION-185 (P0-SESSION-185-PROCEDURAL-VFX-AND-TEXTURE-REVIVAL):
        # auto-register the CPPN Texture Evolution Engine backend so the
        # microkernel can discover the procedural texture generation lane
        # without any trunk modification.  Design: Stanley (2007) CPPN,
        # resolution-independent coordinate-based texture synthesis.
        try:
            importlib.import_module("mathart.core.cppn_texture_backend")
        except Exception as e:
            logger.debug("Failed to auto-load CPPN texture backend: %s", e)
        # SESSION-185 (P0-SESSION-185-PROCEDURAL-VFX-AND-TEXTURE-REVIVAL):
        # auto-register the Fluid Momentum VFX Controller backend so the
        # microkernel can discover the Eulerian-Lagrangian fluid coupling
        # lane without any trunk modification.  Design: GPU Gems 3 Ch. 30,
        # Jos Stam Stable Fluids, CFL stability guard.
        try:
            importlib.import_module("mathart.core.fluid_momentum_backend")
        except Exception as e:
            logger.debug("Failed to auto-load fluid momentum backend: %s", e)
        # SESSION-186 (P0-SESSION-186-AUTONOMOUS-MINER-AND-POLICY-SYNTHESIZER):
        # auto-register the Academic Paper Miner backend so the microkernel
        # can discover the autonomous knowledge mining lane without any
        # trunk modification.  Design: Agentic RAG + Exponential Backoff
        # + Mock Fallback (Netflix Hystrix Circuit Breaker).
        try:
            importlib.import_module("mathart.core.academic_miner_backend")
        except Exception as e:
            logger.debug("Failed to auto-load academic miner backend: %s", e)
        # SESSION-186 (P0-SESSION-186-AUTONOMOUS-MINER-AND-POLICY-SYNTHESIZER):
        # auto-register the Auto-Enforcer Synthesizer backend so the
        # microkernel can discover the policy-as-code synthesis lane
        # without any trunk modification.  Design: LLM code generation
        # + AST validation + Zero-Trust dynamic loading.
        try:
            importlib.import_module("mathart.core.auto_enforcer_synth_backend")
        except Exception as e:
            logger.debug("Failed to auto-load auto enforcer synth backend: %s", e)
    return registry



# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------

def test_backend_registry_basic():
    """Backend registry can register and retrieve backends."""
    BackendRegistry.reset()
    reg = get_registry()

    @register_backend(
        "test_backend",
        display_name="Test Backend",
        artifact_families=("test_output",),
        capabilities=(BackendCapability.SPRITE_EXPORT,),
    )
    class TestBackend:
        @property
        def name(self) -> str:
            return "test_backend"

        @property
        def meta(self) -> BackendMeta:
            return self._backend_meta

        def execute(self, context: dict) -> dict:
            return {"status": "ok"}

    assert reg.get("test_backend") is not None
    meta, cls = reg.get_or_raise("test_backend")
    assert meta.name == "test_backend"
    assert BackendCapability.SPRITE_EXPORT in meta.capabilities
    assert cls is TestBackend

    # Find by capability
    results = reg.find_by_capability(BackendCapability.SPRITE_EXPORT)
    assert len(results) >= 1

    # Find by family
    results = reg.find_by_family("test_output")
    assert len(results) >= 1

    BackendRegistry.reset()


def test_backend_registry_dependency_resolution():
    """Backend registry resolves dependencies in topological order."""
    BackendRegistry.reset()
    reg = get_registry()

    @register_backend("base_sdf", version="1.0.0")
    class BaseSDF:
        pass

    @register_backend("mesh_gen", dependencies=("base_sdf",))
    class MeshGen:
        pass

    @register_backend("shader_gen", dependencies=("mesh_gen",))
    class ShaderGen:
        pass

    order = reg.resolve_dependencies("shader_gen")
    assert order == ["base_sdf", "mesh_gen", "shader_gen"]

    BackendRegistry.reset()


def test_backend_registry_summary():
    """Backend registry generates a valid Markdown summary table."""
    BackendRegistry.reset()
    reg = get_registry()

    @register_backend("demo", display_name="Demo Backend", version="2.0.0")
    class Demo:
        pass

    table = reg.summary_table()
    assert "Demo Backend" in table
    assert "2.0.0" in table

    BackendRegistry.reset()
