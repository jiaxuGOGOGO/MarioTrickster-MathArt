"""Blueprint Evolution — Knowledge-Projected Controlled Variational Derivation.

SESSION-139: P0-SESSION-136-DIRECTOR-STUDIO-V2
SESSION-140: P0-SESSION-137-KNOWLEDGE-SYNERGY-BRIDGE

This module implements the **Controlled Variational Evolution** engine that
derives offspring from a Blueprint base while strictly respecting freeze locks
AND distilled knowledge constraints.

Core invariant (SACRED RED LINE):
    If ``freeze_locks`` includes ``"physics"``, then the physics parameters
    of ALL offspring MUST be **byte-identical** to the parent.  The variance
    across the population for frozen gene families is **exactly 0.0**.

SESSION-140 upgrade — Knowledge-Projected Mutation:
    When a ``RuntimeDistillationBus`` is injected, the mutation operator is
    constrained by distilled knowledge boundaries.  If a random mutation
    attempts to push a parameter beyond the knowledge-defined min/max, it is
    **clamped** (not rejected) to the nearest feasible boundary.  This
    preserves genetic diversity while enforcing safety (Clamp-Not-Reject).

The freeze mask is enforced at THREE levels:
1. **Initialization**: Frozen genes are copied verbatim from the parent.
2. **Mutation**: Frozen genes are excluded from the mutation operator.
3. **Post-enforcement**: After every genetic operation, frozen genes are
   force-restored from the parent snapshot (belt-and-suspenders).

Knowledge clamping is enforced AFTER mutation but BEFORE post-enforcement:
    mutate → clamp_by_knowledge → freeze re-stamp

External research anchors:
- SESSION-139: GAAF (2026), Gene Masking (PMC 2016), Eiben et al.
- SESSION-140: Knowledge-Constrained EA (IJAISC 2014), Constraint-Aware
  Mutation Operators (ResearchGate 2022), Manifold-Assisted Coevolutionary
  Algorithm (Swarm & Evo Comp 2024), Safe RL on Constraint Manifold (2024)
"""
from __future__ import annotations

import copy
import logging
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..distill.runtime_bus import RuntimeDistillationBus, CompiledParameterSpace

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
# Knowledge Clamping Record
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeClampRecord:
    """Record of a single knowledge-driven clamp action during mutation.

    SESSION-140: Lightweight provenance — only stores the parameter key,
    the pre-clamp and post-clamp values, and the rule that triggered it.
    """
    param_key: str = ""
    pre_clamp_value: float = 0.0
    post_clamp_value: float = 0.0
    knowledge_min: Optional[float] = None
    knowledge_max: Optional[float] = None
    rule_id: str = ""
    is_hard: bool = False

    def to_dict(self) -> dict:
        return {
            "param_key": self.param_key,
            "pre_clamp_value": round(self.pre_clamp_value, 6),
            "post_clamp_value": round(self.post_clamp_value, 6),
            "knowledge_min": round(self.knowledge_min, 6) if self.knowledge_min is not None else None,
            "knowledge_max": round(self.knowledge_max, 6) if self.knowledge_max is not None else None,
            "rule_id": self.rule_id,
            "is_hard": self.is_hard,
        }


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
    knowledge_clamp_log: List[KnowledgeClampRecord] = field(default_factory=list)


@dataclass
class BlueprintEvolutionResult:
    """Result of a blueprint evolution run."""
    parent_blueprint_name: str
    freeze_locks: List[str]
    num_variants: int
    offspring: List[VariantOffspring] = field(default_factory=list)
    frozen_param_variance: Dict[str, float] = field(default_factory=dict)
    mutated_param_variance: Dict[str, float] = field(default_factory=dict)
    # SESSION-140: aggregate knowledge clamping stats
    total_knowledge_clamps: int = 0
    knowledge_grounded: bool = False


# ---------------------------------------------------------------------------
# Blueprint Evolution Engine
# ---------------------------------------------------------------------------

class BlueprintEvolutionEngine:
    """Derives controlled variants from a Blueprint base with freeze-mask
    protection and knowledge-driven mutation clamping.

    This engine is the core of the "Controlled Variational Evolution" system.
    It takes a parent genotype, a freeze mask, and produces N offspring where:
    - Frozen genes are 100% identical to the parent (variance = 0).
    - Unfrozen genes undergo bounded random mutation.
    - Knowledge constraints clamp mutations to feasible boundaries.

    SESSION-140: Accepts an optional ``RuntimeDistillationBus`` via dependency
    injection.  When present, ``clamp_by_knowledge()`` is applied after each
    mutation step.
    """

    def __init__(
        self,
        mutation_strength: float = 0.15,
        seed: Optional[int] = None,
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
    ) -> None:
        """
        Parameters
        ----------
        mutation_strength : float
            Base mutation strength as a fraction of the parameter range.
            Default 0.15 = ±15% variation.
        seed : int, optional
            Random seed for reproducibility.
        knowledge_bus : RuntimeDistillationBus, optional
            SESSION-140: Injected knowledge bus for constraint clamping.
        """
        self.mutation_strength = mutation_strength
        self.rng = np.random.RandomState(seed)
        if seed is not None:
            random.seed(seed)
        self.knowledge_bus = knowledge_bus

    def clamp_by_knowledge(
        self,
        flat_params: Dict[str, float],
    ) -> Tuple[Dict[str, float], List[KnowledgeClampRecord]]:
        """Clamp mutated parameters to knowledge-defined boundaries.

        SESSION-140: Knowledge-Projected Mutation — the core safety mechanism.

        For each parameter in ``flat_params``, check all compiled knowledge
        spaces for matching constraints.  If the value exceeds the knowledge
        boundary, clamp it to the nearest feasible point.

        Returns
        -------
        tuple of (clamped_params, clamp_records)
            The clamped parameter dict and a list of clamp records for
            provenance tracking.
        """
        if self.knowledge_bus is None:
            return dict(flat_params), []

        clamped = dict(flat_params)
        records: List[KnowledgeClampRecord] = []

        for module_name, compiled in self.knowledge_bus.compiled_spaces.items():
            for idx, param_name in enumerate(compiled.param_names):
                # Try to match against flat params
                matched_key = self._resolve_param_key(param_name, clamped)
                if matched_key is None:
                    continue

                original_value = clamped[matched_key]
                has_min = bool(compiled.has_min[idx])
                has_max = bool(compiled.has_max[idx])
                min_val = float(compiled.min_values[idx]) if has_min else None
                max_val = float(compiled.max_values[idx]) if has_max else None
                is_hard = bool(compiled.hard_mask[idx])
                new_value = original_value

                needs_clamp = False
                if has_min and original_value < min_val:
                    new_value = min_val
                    needs_clamp = True
                if has_max and original_value > max_val:
                    new_value = max_val
                    needs_clamp = True

                if needs_clamp:
                    clamped[matched_key] = new_value
                    records.append(KnowledgeClampRecord(
                        param_key=matched_key,
                        pre_clamp_value=original_value,
                        post_clamp_value=new_value,
                        knowledge_min=min_val,
                        knowledge_max=max_val,
                        rule_id=f"{module_name}.{param_name}",
                        is_hard=is_hard,
                    ))
                    logger.debug(
                        "Knowledge clamp: %s = %.4f → %.4f [%s.%s]",
                        matched_key, original_value, new_value,
                        module_name, param_name,
                    )

        return clamped, records

    @staticmethod
    def _resolve_param_key(
        knowledge_param: str, flat: Dict[str, float]
    ) -> Optional[str]:
        """Resolve a knowledge parameter name to a flat genotype key."""
        if knowledge_param in flat:
            return knowledge_param
        leaf = knowledge_param.split(".")[-1]
        candidates = [k for k in flat if k.endswith(f".{leaf}")]
        if len(candidates) == 1:
            return candidates[0]
        return None

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
        total_clamps = 0

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

            # SESSION-140: Knowledge-Projected Mutation Clamping
            # Applied AFTER mutation but BEFORE freeze re-stamp
            child_flat, clamp_records = self.clamp_by_knowledge(child_flat)
            total_clamps += len(clamp_records)
            for rec in clamp_records:
                mutation_log.append(
                    f"[KNOWLEDGE CLAMP] {rec.param_key}: "
                    f"{rec.pre_clamp_value:.4f} → {rec.post_clamp_value:.4f} "
                    f"(rule: {rec.rule_id})"
                )

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
                knowledge_clamp_log=clamp_records,
            ))

        # Compute variance statistics
        result = BlueprintEvolutionResult(
            parent_blueprint_name=parent_name,
            freeze_locks=list(freeze_locks),
            num_variants=num_variants,
            offspring=offspring_list,
            total_knowledge_clamps=total_clamps,
            knowledge_grounded=total_clamps > 0,
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
            "Evolved %d variants from '%s' | frozen=%d params (var=%.6f) | "
            "mutated=%d params | knowledge_clamps=%d",
            num_variants,
            parent_name,
            len(frozen_snapshot),
            sum(result.frozen_param_variance.values()),
            len(unfrozen_keys),
            total_clamps,
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
    knowledge_bus: Optional["RuntimeDistillationBus"] = None,
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
    knowledge_bus : RuntimeDistillationBus, optional
        SESSION-140: Injected knowledge bus for constraint clamping.

    Returns
    -------
    BlueprintEvolutionResult
    """
    from ..workspace.director_intent import Blueprint

    bp = Blueprint.load_yaml(Path(blueprint_path))
    engine = BlueprintEvolutionEngine(
        mutation_strength=mutation_strength,
        seed=seed,
        knowledge_bus=knowledge_bus,
    )
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
    "KnowledgeClampRecord",
    "VariantOffspring",
    "build_freeze_mask",
    "evolve_from_blueprint",
    "is_frozen",
]
