"""Blueprint Evolution — Controlled Variational Derivation with Freeze Mask.

SESSION-139: P0-SESSION-136-DIRECTOR-STUDIO-V2

This module implements the **Controlled Variational Evolution** engine that
derives offspring from a Blueprint base while strictly respecting freeze locks.

Core invariant (SACRED RED LINE):
    If ``freeze_locks`` includes ``"physics"``, then the physics parameters
    of ALL offspring MUST be **byte-identical** to the parent.  The variance
    across the population for frozen gene families is **exactly 0.0**.

The freeze mask is enforced at THREE levels:
1. **Initialization**: Frozen genes are copied verbatim from the parent.
2. **Mutation**: Frozen genes are excluded from the mutation operator.
3. **Post-enforcement**: After every genetic operation, frozen genes are
   force-restored from the parent snapshot (belt-and-suspenders).

External research anchors (SESSION-139):
- GAAF (2026): Genetic Algorithm with Adaptive Freezing
- Gene Masking (PMC 2016): Binary mask templates for chromosome protection
- Parameter Control in EAs (Eiben et al.): Fixed constraint enforcement
"""
from __future__ import annotations

import copy
import logging
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Freeze Mask
# ---------------------------------------------------------------------------

GENE_FAMILIES = {"physics", "proportions", "animation", "palette"}


def build_freeze_mask(freeze_locks: List[str]) -> Set[str]:
    """Convert a list of freeze lock names into a normalized set of gene family
    prefixes that must be protected during evolution.

    Supports both family-level locks (``"physics"``) and individual parameter
    locks (``"physics.mass"``).
    """
    mask: Set[str] = set()
    for lock in freeze_locks:
        lock = lock.strip().lower()
        if lock in GENE_FAMILIES:
            mask.add(lock)
        elif "." in lock:
            mask.add(lock)  # Individual param lock
        else:
            logger.warning("Unknown freeze lock: %s (ignored)", lock)
    return mask


def is_frozen(param_key: str, freeze_mask: Set[str]) -> bool:
    """Check if a parameter key is protected by the freeze mask.

    A key like ``"physics.mass"`` is frozen if either ``"physics"`` (family)
    or ``"physics.mass"`` (individual) is in the mask.
    """
    family = param_key.split(".")[0]
    return family in freeze_mask or param_key in freeze_mask


# ---------------------------------------------------------------------------
# Evolution Result
# ---------------------------------------------------------------------------

@dataclass
class VariantOffspring:
    """A single evolved variant offspring."""
    variant_index: int
    genotype_dict: Dict[str, Any]
    flat_params: Dict[str, float]
    mutation_log: List[str] = field(default_factory=list)


@dataclass
class BlueprintEvolutionResult:
    """Result of a blueprint evolution run."""
    parent_blueprint_name: str
    freeze_locks: List[str]
    num_variants: int
    offspring: List[VariantOffspring] = field(default_factory=list)
    frozen_param_variance: Dict[str, float] = field(default_factory=dict)
    mutated_param_variance: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Blueprint Evolution Engine
# ---------------------------------------------------------------------------

class BlueprintEvolutionEngine:
    """Derives controlled variants from a Blueprint base with freeze-mask
    protection.

    This engine is the core of the "Controlled Variational Evolution" system.
    It takes a parent genotype, a freeze mask, and produces N offspring where:
    - Frozen genes are 100% identical to the parent (variance = 0).
    - Unfrozen genes undergo bounded random mutation.
    """

    def __init__(
        self,
        mutation_strength: float = 0.15,
        seed: Optional[int] = None,
    ) -> None:
        """
        Parameters
        ----------
        mutation_strength : float
            Base mutation strength as a fraction of the parameter range.
            Default 0.15 = ±15% variation.
        seed : int, optional
            Random seed for reproducibility.
        """
        self.mutation_strength = mutation_strength
        self.rng = np.random.RandomState(seed)
        if seed is not None:
            random.seed(seed)

    def evolve(
        self,
        parent_genotype: "Genotype",
        num_variants: int,
        freeze_locks: List[str],
        parent_name: str = "unnamed",
    ) -> BlueprintEvolutionResult:
        """Derive ``num_variants`` offspring from the parent genotype.

        Parameters
        ----------
        parent_genotype : Genotype
            The base genotype to derive from.
        num_variants : int
            Number of offspring to produce.
        freeze_locks : list of str
            Gene families or individual params to freeze.
        parent_name : str
            Name of the parent blueprint (for audit trail).

        Returns
        -------
        BlueprintEvolutionResult
            Contains all offspring and variance statistics.
        """
        from ..workspace.director_intent import Genotype

        freeze_mask = build_freeze_mask(freeze_locks)
        parent_flat = parent_genotype.flat_params()

        # Snapshot of frozen values (the sacred reference)
        frozen_snapshot: Dict[str, float] = {
            k: v for k, v in parent_flat.items() if is_frozen(k, freeze_mask)
        }

        offspring_list: List[VariantOffspring] = []

        for i in range(num_variants):
            child_genotype = copy.deepcopy(parent_genotype)
            child_flat = child_genotype.flat_params()
            mutation_log: List[str] = []

            # Mutate only unfrozen parameters
            for key, value in child_flat.items():
                if is_frozen(key, freeze_mask):
                    # SACRED: force-restore from parent snapshot
                    child_flat[key] = frozen_snapshot[key]
                    continue

                # Apply bounded Gaussian mutation
                sigma = abs(value) * self.mutation_strength if value != 0 else self.mutation_strength
                delta = self.rng.normal(0, sigma)
                new_value = value + delta

                # Ensure non-negative for physical quantities
                if "mass" in key or "stiffness" in key or "scale" in key:
                    new_value = max(new_value, 0.01)
                if "ratio" in key:
                    new_value = max(min(new_value, 1.0), 0.01)

                child_flat[key] = new_value
                mutation_log.append(f"{key}: {value:.4f} → {new_value:.4f}")

            # POST-ENFORCEMENT: Belt-and-suspenders — re-stamp frozen values
            for key, sacred_value in frozen_snapshot.items():
                child_flat[key] = sacred_value

            child_genotype.apply_flat_params(child_flat)

            # Handle palette mutation for unfrozen palette
            if "palette" not in freeze_mask:
                child_genotype.palette = self._mutate_palette(
                    parent_genotype.palette
                )
                mutation_log.append("palette: mutated")
            # If palette is frozen, it stays identical (deepcopy from parent)

            offspring_list.append(VariantOffspring(
                variant_index=i,
                genotype_dict=child_genotype.to_dict(),
                flat_params=child_genotype.flat_params(),
                mutation_log=mutation_log,
            ))

        # Compute variance statistics
        result = BlueprintEvolutionResult(
            parent_blueprint_name=parent_name,
            freeze_locks=list(freeze_locks),
            num_variants=num_variants,
            offspring=offspring_list,
        )

        # Frozen param variance (MUST be 0.0)
        all_flat = [o.flat_params for o in offspring_list]
        for key in frozen_snapshot:
            values = [flat[key] for flat in all_flat]
            result.frozen_param_variance[key] = float(np.var(values))

        # Mutated param variance (should be > 0 for unfrozen)
        unfrozen_keys = [k for k in parent_flat if not is_frozen(k, freeze_mask)]
        for key in unfrozen_keys:
            values = [flat.get(key, 0.0) for flat in all_flat]
            result.mutated_param_variance[key] = float(np.var(values))

        logger.info(
            "Evolved %d variants from '%s' | frozen=%d params (var=%.6f) | mutated=%d params",
            num_variants,
            parent_name,
            len(frozen_snapshot),
            sum(result.frozen_param_variance.values()),
            len(unfrozen_keys),
        )

        return result

    def _mutate_palette(self, parent_palette: "ColorPalette") -> "ColorPalette":
        """Apply random color mutation to an unfrozen palette."""
        from ..workspace.director_intent import ColorPalette
        import colorsys

        def mutate_hex(hex_color: str) -> str:
            """Mutate a hex color by shifting HSV slightly."""
            hex_color = hex_color.lstrip("#")
            if len(hex_color) != 6:
                return f"#{hex_color}"
            r, g, b = int(hex_color[:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            h = (h + self.rng.normal(0, 0.05)) % 1.0
            s = max(0, min(1, s + self.rng.normal(0, 0.08)))
            v = max(0, min(1, v + self.rng.normal(0, 0.08)))
            r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
            return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"

        return ColorPalette(
            primary=mutate_hex(parent_palette.primary),
            secondary=mutate_hex(parent_palette.secondary),
            accent=mutate_hex(parent_palette.accent),
            shadow=mutate_hex(parent_palette.shadow),
            highlight=mutate_hex(parent_palette.highlight),
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def evolve_from_blueprint(
    blueprint_path: Path | str,
    num_variants: int,
    freeze_locks: List[str],
    mutation_strength: float = 0.15,
    seed: Optional[int] = None,
) -> BlueprintEvolutionResult:
    """One-shot convenience: load a blueprint and evolve variants.

    Parameters
    ----------
    blueprint_path : Path or str
        Path to the parent blueprint YAML file.
    num_variants : int
        Number of offspring to produce.
    freeze_locks : list of str
        Gene families to freeze during evolution.
    mutation_strength : float
        Base mutation strength.
    seed : int, optional
        Random seed.

    Returns
    -------
    BlueprintEvolutionResult
    """
    from ..workspace.director_intent import Blueprint

    bp = Blueprint.load_yaml(Path(blueprint_path))
    engine = BlueprintEvolutionEngine(mutation_strength=mutation_strength, seed=seed)
    return engine.evolve(
        parent_genotype=bp.genotype,
        num_variants=num_variants,
        freeze_locks=freeze_locks,
        parent_name=bp.meta.name,
    )


__all__ = [
    "BlueprintEvolutionEngine",
    "BlueprintEvolutionResult",
    "GENE_FAMILIES",
    "VariantOffspring",
    "build_freeze_mask",
    "evolve_from_blueprint",
    "is_frozen",
]
