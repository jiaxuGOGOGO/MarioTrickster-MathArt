"""
tests/conftest.py — Shared pytest fixtures for registry bootstrap safety.

SESSION-098 (HIGH-2.6): This conftest provides a session-scoped fixture that
ensures the BackendRegistry is populated with all builtin backends at the start
of the test session, and a reusable helper for any test that needs to reset and
restore the registry safely.

Architecture Discipline
-----------------------
- NO global ``np.random.seed()`` — per NEP-19, each test must use its own
  ``default_rng`` instance for deterministic, isolated random inputs.
- NO registry state leakage — any test that calls ``BackendRegistry.reset()``
  MUST restore builtins in its teardown path.

References
----------
[1] NumPy NEP 19 — Random number generator policy
[2] Martin Fowler — Eradicating Non-Determinism in Tests
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path for all tests
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mathart.core.backend_registry import BackendRegistry, get_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The canonical list of backend modules that get_registry() auto-loads.
# Kept in sync with the import sequence inside get_registry().
# ---------------------------------------------------------------------------
_BUILTIN_BACKEND_MODULES = [
    "mathart.core.builtin_backends",
    "mathart.core.builtin_niches",
    "mathart.core.physics3d_backend",
    "mathart.core.taichi_xpbd_backend",
    "mathart.core.evolution_backends",
    "mathart.core.physics_gait_distill_backend",
    "mathart.core.cognitive_distillation_backend",
    "mathart.core.rl_training_backend",
    "mathart.core.orthographic_pixel_backend",
    "mathart.core.pseudo3d_shell_backend",
    "mathart.core.reaction_diffusion_backend",
    "mathart.core.unity_2d_anim_backend",
]


# ---------------------------------------------------------------------------
# Helper: Restore builtin backends after a registry reset
# ---------------------------------------------------------------------------

def restore_builtin_backends() -> None:
    """Force-reload all builtin backend modules into the registry.

    This function is the canonical way to recover from a
    ``BackendRegistry.reset()`` call.  Because ``importlib.import_module``
    is a no-op when the module is already in ``sys.modules``, we must use
    ``importlib.reload`` to re-execute the ``@register_backend`` decorators
    that populate the singleton registry.

    Any test fixture that performs ``BackendRegistry.reset()`` MUST call
    this function in its teardown to avoid polluting downstream suites.
    """
    # Step 1: Wipe the registry and its flags completely
    BackendRegistry.reset()
    BackendRegistry._builtins_loaded = True  # prevent get_registry() from racing
    BackendRegistry._backend_module_map = {}

    # Step 2: Reload each builtin module so @register_backend decorators re-fire
    for mod_name in _BUILTIN_BACKEND_MODULES:
        try:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
            else:
                importlib.import_module(mod_name)
        except Exception as exc:
            logger.debug("restore_builtin_backends: failed to reload %s: %s", mod_name, exc)


# ---------------------------------------------------------------------------
# Session-scoped: ensure builtins are loaded once at session start
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _ensure_registry_bootstrapped():
    """Guarantee that builtin backends are registered before any test runs.

    This is a session-scoped, autouse fixture.  It calls ``get_registry()``
    once to trigger the standard auto-load sequence, then yields.  After the
    entire test session completes, it restores builtins one final time as a
    safety net.
    """
    get_registry()
    yield
    restore_builtin_backends()
