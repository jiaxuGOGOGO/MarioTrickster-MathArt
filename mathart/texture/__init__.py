"""``mathart.texture`` — Procedural texture synthesis subsystem.

SESSION-119 (P1-NEW-2) introduces this package with the first member,
``reaction_diffusion`` — a fully tensorized Gray-Scott PDE solver plus
PBR (height → normal/albedo/mask) derivation used by the new
``ReactionDiffusionBackend`` plugin.

The package deliberately stays independent of every legacy trunk path
(``AssetPipeline``, ``Orchestrator``, CLI).  New texture engines plug into
the project through the registry pattern and the strongly-typed
``ArtifactManifest`` contract, not through the trunk.
"""
from __future__ import annotations

from mathart.texture.reaction_diffusion import (
    GRAY_SCOTT_PRESETS,
    GRAY_SCOTT_LAPLACIAN_KERNEL,
    SOBEL_X,
    SOBEL_Y,
    GrayScottSolver,
    GrayScottSolverConfig,
    GrayScottState,
    PBRDerivationResult,
    derive_pbr_from_concentration,
    encode_normal_map,
    encode_albedo_map,
    encode_height_map,
    encode_mask_map,
    list_preset_names,
    get_preset,
)

__all__ = [
    "GRAY_SCOTT_PRESETS",
    "GRAY_SCOTT_LAPLACIAN_KERNEL",
    "SOBEL_X",
    "SOBEL_Y",
    "GrayScottSolver",
    "GrayScottSolverConfig",
    "GrayScottState",
    "PBRDerivationResult",
    "derive_pbr_from_concentration",
    "encode_normal_map",
    "encode_albedo_map",
    "encode_height_map",
    "encode_mask_map",
    "list_preset_names",
    "get_preset",
]
