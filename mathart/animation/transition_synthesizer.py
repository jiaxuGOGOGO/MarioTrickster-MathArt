from __future__ import annotations

"""Compatibility re-export for the unified motion hub.

SESSION-069 retires the split transition implementation. Legacy callers may
still import from this module, but all transition math is now provided by the
single-source implementation in ``unified_gait_blender.py``.
"""

from .unified_gait_blender import *  # noqa: F401,F403
