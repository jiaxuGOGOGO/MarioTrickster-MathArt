"""Core microkernel infrastructure — Registry Pattern + IoC + Artifact Schema.

SESSION-064: Paradigm Shift — Contract-Based Microkernel Plugin Architecture.

This package implements the three foundational pillars:

1. **Backend Registry** (LLVM-inspired): Dynamic self-registration of export
   backends via ``@register_backend`` decorator. The trunk pipeline never
   needs modification when new backends are added.

2. **Artifact Schema** (Pixar USD-inspired): Strongly-typed artifact manifests
   with ``artifact_family`` and ``backend_type`` fields. All pipeline outputs
   must pass schema validation before acceptance.

3. **Niche Registry** (MAP-Elites-inspired): Per-lane evolution niches with
   isolated fitness functions. No cross-lane weighted averaging.

References
----------
[1] Chris Lattner, "LLVM", The Architecture of Open Source Applications, 2012.
[2] Pixar, "OpenUSD Validation Framework", openusd.org, 2024.
[3] Yuriy O'Donnell, "FrameGraph: Extensible Rendering Architecture in
    Frostbite", GDC 2017.
[4] Jean-Baptiste Mouret & Jeff Clune, "Illuminating Search Spaces by
    Mapping Elites", arXiv:1504.04909, 2015.
[5] Kalyanmoy Deb et al., "A Fast and Elitist Multiobjective Genetic
    Algorithm: NSGA-II", IEEE TEC 6(2), 2002.
"""

from mathart.core.backend_registry import (
    BackendRegistry,
    BackendMeta,
    register_backend,
    get_registry,
)
from mathart.core.backend_file_watcher import (
    BackendFileWatcher,
)
from mathart.core.safe_point_execution import (
    SafePointExecutionLock,
    get_safe_point_lock,
)
from mathart.core.backend_types import (
    BackendType,
    backend_alias_map,
    backend_type_value,
    known_backend_types,
)
from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    ArtifactValidationError,
    validate_artifact,
)
from mathart.core.niche_registry import (
    EvolutionNiche,
    NicheRegistry,
    NicheReport,
    ParetoFront,
    register_niche,
    get_niche_registry,
)
# SESSION-141: Blackbox Logger & Centralized Settings
from mathart.core.logger import (
    install_blackbox,
    get_blackbox_logger,
    BlackboxConfig,
)
from mathart.core.settings import (
    Settings,
    get_settings,
)

__all__ = [
    # Backend Registry
    "BackendRegistry",
    "BackendMeta",
    "register_backend",
    "get_registry",
    # Backend Hot-Reload (SESSION-090)
    "BackendFileWatcher",
    # Safe-Point Execution Lock (SESSION-091)
    "SafePointExecutionLock",
    "get_safe_point_lock",
    "BackendType",
    "backend_alias_map",
    "backend_type_value",
    "known_backend_types",
    # Artifact Schema
    "ArtifactFamily",
    "ArtifactManifest",
    "ArtifactValidationError",
    "validate_artifact",
    # Niche Registry
    "EvolutionNiche",
    "NicheRegistry",
    "NicheReport",
    "ParetoFront",
    "register_niche",
    "get_niche_registry",
    # SESSION-141: Blackbox Logger
    "install_blackbox",
    "get_blackbox_logger",
    "BlackboxConfig",
    # SESSION-141: Centralized Settings
    "Settings",
    "get_settings",
]
