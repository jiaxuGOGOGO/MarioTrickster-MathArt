"""Backend type system for Golden Path registry hardening.

SESSION-066 introduces a strong ``BackendType`` enum so artifact manifests,
registries, and orchestration reports stop drifting across historical naming
variants. The design deliberately keeps a compatibility alias layer so older
strings such as ``dimension_uplift`` or ``unity_urp_2d`` still resolve to the
new canonical backend types.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class BackendType(str, Enum):
    """Canonical backend types used by the registry and manifest contract."""

    MOTION_2D = "motion_2d"
    URP2D_BUNDLE = "urp2d_bundle"
    INDUSTRIAL_SPRITE = "industrial_sprite"
    DIMENSION_UPLIFT_MESH = "dimension_uplift_mesh"
    ANTI_FLICKER_RENDER = "anti_flicker_render"
    WFC_TILEMAP = "wfc_tilemap"
    PHYSICS_VFX = "physics_vfx"
    CEL_SHADING = "cel_shading"
    KNOWLEDGE_DISTILL = "knowledge_distill"
    COMPOSITE = "composite"
    LEGACY = "legacy"
    UNIFIED_MOTION = "unified_motion"
    MICROKERNEL = "microkernel"
    PHYSICS_3D = "physics_3d"


_BACKEND_ALIASES: dict[str, str] = {
    # Canonical values
    BackendType.MOTION_2D.value: BackendType.MOTION_2D.value,
    BackendType.URP2D_BUNDLE.value: BackendType.URP2D_BUNDLE.value,
    BackendType.INDUSTRIAL_SPRITE.value: BackendType.INDUSTRIAL_SPRITE.value,
    BackendType.DIMENSION_UPLIFT_MESH.value: BackendType.DIMENSION_UPLIFT_MESH.value,
    BackendType.ANTI_FLICKER_RENDER.value: BackendType.ANTI_FLICKER_RENDER.value,
    BackendType.WFC_TILEMAP.value: BackendType.WFC_TILEMAP.value,
    BackendType.PHYSICS_VFX.value: BackendType.PHYSICS_VFX.value,
    BackendType.CEL_SHADING.value: BackendType.CEL_SHADING.value,
    BackendType.KNOWLEDGE_DISTILL.value: BackendType.KNOWLEDGE_DISTILL.value,
    BackendType.COMPOSITE.value: BackendType.COMPOSITE.value,
    BackendType.LEGACY.value: BackendType.LEGACY.value,
    BackendType.UNIFIED_MOTION.value: BackendType.UNIFIED_MOTION.value,
    BackendType.MICROKERNEL.value: BackendType.MICROKERNEL.value,
    BackendType.PHYSICS_3D.value: BackendType.PHYSICS_3D.value,
    # Historical / user-requested variants
    "dimension_uplift": BackendType.DIMENSION_UPLIFT_MESH.value,
    "dimension_uplift_bundle": BackendType.DIMENSION_UPLIFT_MESH.value,
    "unity_urp_2d": BackendType.URP2D_BUNDLE.value,
    "unity_urp_2d_bundle": BackendType.URP2D_BUNDLE.value,
    "unity_urp2d_bundle": BackendType.URP2D_BUNDLE.value,
    "unity_urp_native": BackendType.URP2D_BUNDLE.value,
    "industrial_sprite_bundle": BackendType.INDUSTRIAL_SPRITE.value,
    "industrial_renderer": BackendType.INDUSTRIAL_SPRITE.value,
    "breakwall": BackendType.ANTI_FLICKER_RENDER.value,
    "sparse_ctrl": BackendType.ANTI_FLICKER_RENDER.value,
    "anti_flicker": BackendType.ANTI_FLICKER_RENDER.value,
    "motion_trunk": BackendType.UNIFIED_MOTION.value,
    "unified_motion_trunk": BackendType.UNIFIED_MOTION.value,
    "motion_lane_registry": BackendType.UNIFIED_MOTION.value,
    # SESSION-071: P1-XPBD-3 — 3D XPBD physics backend aliases
    "xpbd_3d": BackendType.PHYSICS_3D.value,
    "physics3d": BackendType.PHYSICS_3D.value,
    "physics_xpbd_3d": BackendType.PHYSICS_3D.value,
}


def backend_type_value(
    value: str | BackendType | None,
    *,
    allow_unknown: bool = True,
) -> str:
    """Return a canonical backend type string.

    Known aliases are normalized to the canonical ``BackendType`` value. Unknown
    strings are preserved by default for backward compatibility with ad-hoc test
    backends and experimental local plugins.
    """
    if value is None:
        return ""
    if isinstance(value, BackendType):
        return value.value
    text = str(value).strip()
    if not text:
        return ""
    normalized = text.lower().replace("-", "_")
    resolved = _BACKEND_ALIASES.get(normalized)
    if resolved is not None:
        return resolved
    if allow_unknown:
        return normalized
    raise ValueError(f"Unknown backend type: {value!r}")


def parse_backend_type(
    value: str | BackendType | None,
    *,
    allow_unknown: bool = True,
) -> BackendType | str:
    """Parse a backend value into ``BackendType`` when possible."""
    canonical = backend_type_value(value, allow_unknown=allow_unknown)
    if not canonical:
        return canonical
    try:
        return BackendType(canonical)
    except ValueError:
        if allow_unknown:
            return canonical
        raise


def known_backend_types() -> tuple[str, ...]:
    """Return all canonical backend type values."""
    return tuple(member.value for member in BackendType)


def backend_alias_map() -> dict[str, str]:
    """Return a copy of the alias map for reporting and audits."""
    return dict(_BACKEND_ALIASES)


def is_known_backend_type(value: str | BackendType | None) -> bool:
    """Return whether the value resolves to a canonical backend enum."""
    if value is None:
        return False
    canonical = backend_type_value(value, allow_unknown=True)
    return canonical in known_backend_types()


__all__ = [
    "BackendType",
    "backend_alias_map",
    "backend_type_value",
    "is_known_backend_type",
    "known_backend_types",
    "parse_backend_type",
]
