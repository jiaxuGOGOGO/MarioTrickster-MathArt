from __future__ import annotations

"""Compatibility re-export for the unified motion hub.

SESSION-069 consolidates the historical gait blending and transition logic in
``unified_gait_blender.py``. This module is kept as a thin compatibility layer
so legacy imports continue to resolve while all real math now lives in the
single-source implementation.
"""

from .unified_gait_blender import *  # noqa: F401,F403
