"""Director Intent — Knowledge-Grounded Semantic-to-Parametric Translation Bridge.

SESSION-139: P0-SESSION-136-DIRECTOR-STUDIO-V2
SESSION-140: P0-SESSION-137-KNOWLEDGE-SYNERGY-BRIDGE

This module implements the **Knowledge-Grounded Semantic-to-Parametric Translation
Bridge** that converts an artist's natural-language intent (``intent.yaml``) into a
strongly-typed ``CreatorIntentSpec``.  It supports three declaration modes:

1. **Emotive Genesis** — fuzzy vibe strings (e.g. ``"活泼的跳跃"``) are mapped
   to numeric parameter ranges via a configurable semantic lookup table, then
   **clamped** by runtime distilled knowledge constraints.
2. **Blueprint Derivation** — a ``base_blueprint`` path is loaded, its genotype
   is used as the seed, and ``freeze_locks`` protect specified gene families
   from mutation during controlled-variable evolution.
3. **Hybrid** — emotive overrides applied on top of a blueprint base.

SESSION-140 upgrade — Knowledge-Grounded Vibe-to-Math:
- The parser now accepts an optional ``RuntimeDistillationBus`` via dependency
  injection (IoC).  When present, vibe translation first queries the bus for
  relevant constraints, then clamps the heuristic-derived parameters to the
  distilled knowledge boundaries.
- If the bus is absent or returns no constraints, the system gracefully degrades
  to the built-in ``SEMANTIC_VIBE_MAP`` heuristics (no exceptions thrown).
- Every activated knowledge rule is tracked in ``CreatorIntentSpec.applied_knowledge_rules``
  for downstream provenance tagging (Data Lineage & Provenance).

Architecture discipline:
- This module is an **independent logic bridge** injected via the workspace
  package.  It NEVER touches the core pipeline directly.
- Output is always a ``CreatorIntentSpec`` dataclass — the single strongly-typed
  contract consumed downstream by the interactive gate and the inner-loop
  evolution engine.

External research anchors:
- SESSION-139: Proc3D, Pixar USD VariantSets, GAAF, DreamCrafter
- SESSION-140: Knowledge-Grounded Generation (KAG 2025), Constraint Reconciliation
  (arXiv 2511.10952), Data Lineage (Atlas 2025, C2PA), Knowledge-Constrained
  Evolutionary Algorithms (IJAISC 2014)
"""
from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..distill.runtime_bus import RuntimeDistillationBus, CompiledParameterSpace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strongly-typed contracts
# ---------------------------------------------------------------------------

@dataclass
class PhysicsConfig:
    """Physics simulation parameters — part of the asset genotype."""
    gravity: float = 9.81
    mass: float = 1.0
    stiffness: float = 50.0
    damping: float = 0.3
    bounce: float = 0.6
    friction: float = 0.4

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PhysicsConfig":
        return cls(**{k: d.get(k, getattr(cls(), k)) for k in cls.__dataclass_fields__})


@dataclass
class ProportionsConfig:
    """Body / shape proportions — part of the asset genotype."""
    head_ratio: float = 0.25
    body_ratio: float = 0.50
    limb_ratio: float = 0.25
    scale: float = 1.0
    squash_stretch: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProportionsConfig":
        return cls(**{k: d.get(k, getattr(cls(), k)) for k in cls.__dataclass_fields__})


@dataclass
class AnimationConfig:
    """Animation timing / easing parameters — part of the asset genotype."""
    frame_rate: int = 12
    anticipation: float = 0.15
    follow_through: float = 0.20
    exaggeration: float = 1.0
    ease_in: float = 0.3
    ease_out: float = 0.3
    cycle_frames: int = 24

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AnimationConfig":
        return cls(**{k: d.get(k, getattr(cls(), k)) for k in cls.__dataclass_fields__})


@dataclass
class ColorPalette:
    """Color palette specification — part of the asset genotype."""
    primary: str = "#FF6B35"
    secondary: str = "#004E89"
    accent: str = "#F7C948"
    shadow: str = "#1A1A2E"
    highlight: str = "#FFFFFF"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ColorPalette":
        return cls(**{k: d.get(k, getattr(cls(), k)) for k in cls.__dataclass_fields__})


@dataclass
class Genotype:
    """Complete genetic fingerprint of an asset — the atomic unit of Blueprint
    serialization.  Every field has a sensible default so that old blueprints
    missing newer keys never crash on deserialization.
    """
    physics: PhysicsConfig = field(default_factory=PhysicsConfig)
    proportions: ProportionsConfig = field(default_factory=ProportionsConfig)
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    palette: ColorPalette = field(default_factory=ColorPalette)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "physics": self.physics.to_dict(),
            "proportions": self.proportions.to_dict(),
            "animation": self.animation.to_dict(),
            "palette": self.palette.to_dict(),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Genotype":
        return cls(
            physics=PhysicsConfig.from_dict(d.get("physics", {})),
            proportions=ProportionsConfig.from_dict(d.get("proportions", {})),
            animation=AnimationConfig.from_dict(d.get("animation", {})),
            palette=ColorPalette.from_dict(d.get("palette", {})),
            extra=dict(d.get("extra", {})),
        )

    def flat_params(self) -> Dict[str, float]:
        """Flatten genotype into a single dict of ``family.param: value``."""
        out: Dict[str, float] = {}
        for family_name in ("physics", "proportions", "animation"):
            cfg = getattr(self, family_name)
            for k, v in asdict(cfg).items():
                out[f"{family_name}.{k}"] = float(v)
        # palette is string-based, skip in numeric flat view
        return out

    def apply_flat_params(self, flat: Dict[str, float]) -> None:
        """Apply a flat param dict back into the genotype sub-configs."""
        for key, value in flat.items():
            parts = key.split(".", 1)
            if len(parts) != 2:
                continue
            family, param = parts
            cfg = getattr(self, family, None)
            if cfg is None:
                continue
            if hasattr(cfg, param):
                # Respect int fields
                current = getattr(cfg, param)
                if isinstance(current, int):
                    setattr(cfg, param, int(round(value)))
                else:
                    setattr(cfg, param, float(value))


@dataclass
class BlueprintMeta:
    """Metadata header for a serialized Blueprint YAML file."""
    name: str = ""
    version: str = "1.0.0"
    created_by: str = "director_studio"
    blueprint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_blueprint: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BlueprintMeta":
        return cls(**{k: d.get(k, getattr(cls(), k)) for k in cls.__dataclass_fields__})


@dataclass
class Blueprint:
    """A complete, serializable Blueprint template.

    This is the atomic unit of the asset-lineage system.  It contains the full
    ``Genotype`` plus metadata.  Serialization is **pure YAML** — no Base64
    blobs, no absolute paths, no runtime state.
    """
    meta: BlueprintMeta = field(default_factory=BlueprintMeta)
    genotype: Genotype = field(default_factory=Genotype)

    def to_dict(self) -> dict:
        return {
            "meta": self.meta.to_dict(),
            "genotype": self.genotype.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Blueprint":
        return cls(
            meta=BlueprintMeta.from_dict(d.get("meta", {})),
            genotype=Genotype.from_dict(d.get("genotype", {})),
        )

    def save_yaml(self, path: Path) -> Path:
        """Serialize to a pure YAML file.  No Base64, no absolute paths."""
        import yaml
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        logger.info("Blueprint saved → %s", path)
        return path

    @classmethod
    def load_yaml(cls, path: Path) -> "Blueprint":
        """Deserialize from YAML with robust default handling for old schemas."""
        import yaml
        path = Path(path)
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw)


# ---------------------------------------------------------------------------
# Semantic Translation Table (Heuristic Fallback)
# ---------------------------------------------------------------------------

# Maps fuzzy vibe keywords → parameter adjustment multipliers.
# Each entry: { "family.param": multiplier_delta }
# Multiplier delta is added to the base value (1.0 = no change).
# SESSION-140: This table serves as the FALLBACK when RuntimeDistillationBus
# returns no matching constraints (Graceful Degradation).
SEMANTIC_VIBE_MAP: Dict[str, Dict[str, float]] = {
    "活泼": {
        "animation.exaggeration": 0.3,
        "animation.anticipation": 0.1,
        "physics.bounce": 0.2,
        "physics.stiffness": 10.0,
    },
    "夸张": {
        "animation.exaggeration": 0.6,
        "animation.follow_through": 0.15,
        "proportions.squash_stretch": 0.4,
        "physics.bounce": 0.3,
    },
    "沉稳": {
        "animation.exaggeration": -0.2,
        "animation.ease_in": 0.15,
        "animation.ease_out": 0.15,
        "physics.damping": 0.15,
        "physics.mass": 0.5,
    },
    "轻盈": {
        "physics.mass": -0.3,
        "physics.gravity": -2.0,
        "animation.anticipation": -0.05,
        "proportions.scale": -0.1,
    },
    "厚重": {
        "physics.mass": 0.8,
        "physics.gravity": 1.5,
        "animation.ease_in": 0.1,
        "proportions.scale": 0.15,
    },
    "弹性": {
        "physics.bounce": 0.4,
        "physics.stiffness": 15.0,
        "proportions.squash_stretch": 0.5,
        "animation.follow_through": 0.2,
    },
    "沉重": {
        "physics.mass": 1.2,
        "physics.gravity": 2.5,
        "physics.damping": 0.25,
        "animation.ease_in": 0.15,
        "proportions.scale": 0.2,
    },
    "lively": {
        "animation.exaggeration": 0.3,
        "animation.anticipation": 0.1,
        "physics.bounce": 0.2,
        "physics.stiffness": 10.0,
    },
    "exaggerated": {
        "animation.exaggeration": 0.6,
        "animation.follow_through": 0.15,
        "proportions.squash_stretch": 0.4,
        "physics.bounce": 0.3,
    },
    "heavy": {
        "physics.mass": 0.8,
        "physics.gravity": 1.5,
        "animation.ease_in": 0.1,
        "proportions.scale": 0.15,
    },
    "bouncy": {
        "physics.bounce": 0.4,
        "physics.stiffness": 15.0,
        "proportions.squash_stretch": 0.5,
        "animation.follow_through": 0.2,
    },
}

# ---------------------------------------------------------------------------
# Vibe-to-Knowledge Module Mapping
# ---------------------------------------------------------------------------
# Maps vibe keywords to the knowledge module names that should be queried
# on the RuntimeDistillationBus for relevant constraints.
VIBE_TO_KNOWLEDGE_MODULES: Dict[str, List[str]] = {
    "沉重": ["physics", "game_feel"],
    "厚重": ["physics", "game_feel"],
    "heavy": ["physics", "game_feel"],
    "轻盈": ["physics", "game_feel"],
    "弹性": ["physics", "game_feel"],
    "bouncy": ["physics", "game_feel"],
    "活泼": ["animation", "game_feel"],
    "lively": ["animation", "game_feel"],
    "夸张": ["animation", "anatomy"],
    "exaggerated": ["animation", "anatomy"],
    "沉稳": ["animation", "physics"],
    "落地": ["physics", "game_feel"],
    "跳跃": ["physics", "game_feel"],
    "jump": ["physics", "game_feel"],
    "landing": ["physics", "game_feel"],
}


# ---------------------------------------------------------------------------
# Knowledge Provenance Record (lightweight)
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeProvenanceRecord:
    """Lightweight provenance record for a single activated knowledge rule.

    SESSION-140: Data Lineage & Provenance — only stores the rule ID, a brief
    description, the parameter it constrained, and the clamping action taken.
    This is intentionally minimal to prevent metadata bloat (防血统数据膨胀红线).
    """
    rule_id: str = ""
    module_name: str = ""
    param_constrained: str = ""
    original_value: float = 0.0
    clamped_value: float = 0.0
    constraint_type: str = ""  # "hard" or "soft"
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "module_name": self.module_name,
            "param_constrained": self.param_constrained,
            "original_value": round(self.original_value, 6),
            "clamped_value": round(self.clamped_value, 6),
            "constraint_type": self.constraint_type,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeProvenanceRecord":
        return cls(**{k: d.get(k, getattr(cls(), k)) for k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Knowledge Conflict Report
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeConflict:
    """Describes a conflict between user intent and distilled knowledge.

    SESSION-140: Constraint Reconciliation — three severity tiers:
    - FATAL: mathematical impossibility (e.g. div-by-zero) → hard block
    - PHYSICAL: knowledge boundary violation → warn + offer override
    - INFO: style suggestion → log only
    """
    severity: str = "physical"  # "fatal", "physical", "info"
    param_key: str = ""
    user_value: float = 0.0
    knowledge_min: Optional[float] = None
    knowledge_max: Optional[float] = None
    clamped_value: float = 0.0
    rule_description: str = ""
    is_hard_constraint: bool = False

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "param_key": self.param_key,
            "user_value": round(self.user_value, 6),
            "knowledge_min": round(self.knowledge_min, 6) if self.knowledge_min is not None else None,
            "knowledge_max": round(self.knowledge_max, 6) if self.knowledge_max is not None else None,
            "clamped_value": round(self.clamped_value, 6),
            "rule_description": self.rule_description,
            "is_hard_constraint": self.is_hard_constraint,
        }


# ---------------------------------------------------------------------------
# CreatorIntentSpec — the single strongly-typed output contract
# ---------------------------------------------------------------------------

@dataclass
class CreatorIntentSpec:
    """The strongly-typed output of the Director Intent parser.

    Regardless of how fuzzy the user's description was, this spec is the
    **only** contract passed downstream to the interactive gate and the
    inner-loop evolution engine.

    SESSION-140 additions:
    - ``applied_knowledge_rules``: provenance records of all knowledge rules
      that were activated during translation (Data Lineage).
    - ``knowledge_conflicts``: conflicts detected between user intent and
      distilled knowledge (Constraint Reconciliation).
    - ``knowledge_grounded``: whether the translation was grounded by the
      RuntimeDistillationBus (True) or fell back to heuristics (False).
    """
    # Resolved genotype (after semantic translation + blueprint inheritance)
    genotype: Genotype = field(default_factory=Genotype)

    # Blueprint derivation controls
    base_blueprint_path: str = ""
    evolve_variants: int = 0
    freeze_locks: List[str] = field(default_factory=list)

    # Original user intent (for audit trail)
    raw_vibe: str = ""
    raw_description: str = ""

    # Metadata
    intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # SESSION-140: Knowledge provenance & conflict tracking
    applied_knowledge_rules: List[KnowledgeProvenanceRecord] = field(default_factory=list)
    knowledge_conflicts: List[KnowledgeConflict] = field(default_factory=list)
    knowledge_grounded: bool = False

    # SESSION-187: Semantic Orchestrator — LLM-driven VFX plugin activation.
    # This list contains validated backend type names that should be activated
    # during the rendering pipeline.  Populated by the SemanticOrchestrator
    # after intent parsing (heuristic or LLM-based resolution).
    # [幻觉防呆红线] Only contains names validated against BackendRegistry.
    active_vfx_plugins: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        _genotype = getattr(self, "genotype", None)
        _rules = getattr(self, "applied_knowledge_rules", None) or []
        _conflicts = getattr(self, "knowledge_conflicts", None) or []
        _vfx = getattr(self, "active_vfx_plugins", None) or []
        return {
            "genotype": _genotype.to_dict() if _genotype else {},
            "base_blueprint_path": getattr(self, "base_blueprint_path", ""),
            "evolve_variants": getattr(self, "evolve_variants", 0),
            "freeze_locks": list(getattr(self, "freeze_locks", []) or []),
            "raw_vibe": getattr(self, "raw_vibe", getattr(self, "vibe", "")),
            "raw_description": getattr(self, "raw_description", getattr(self, "description", "")),
            "intent_id": getattr(self, "intent_id", ""),
            "applied_knowledge_rules": [r.to_dict() for r in _rules],
            "knowledge_conflicts": [c.to_dict() for c in _conflicts],
            "knowledge_grounded": getattr(self, "knowledge_grounded", False),
            "active_vfx_plugins": list(_vfx),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CreatorIntentSpec":
        rules = [KnowledgeProvenanceRecord.from_dict(r) for r in d.get("applied_knowledge_rules", [])]
        conflicts = [KnowledgeConflict(**c) for c in d.get("knowledge_conflicts", [])]
        return cls(
            genotype=Genotype.from_dict(d.get("genotype", {})),
            base_blueprint_path=d.get("base_blueprint_path", ""),
            evolve_variants=int(d.get("evolve_variants", 0)),
            freeze_locks=list(d.get("freeze_locks", [])),
            raw_vibe=d.get("raw_vibe", ""),
            raw_description=d.get("raw_description", ""),
            intent_id=d.get("intent_id", str(uuid.uuid4())),
            applied_knowledge_rules=rules,
            knowledge_conflicts=conflicts,
            knowledge_grounded=d.get("knowledge_grounded", False),
            active_vfx_plugins=list(d.get("active_vfx_plugins", [])),
        )


# ---------------------------------------------------------------------------
# Intent Parser — Knowledge-Grounded
# ---------------------------------------------------------------------------

class DirectorIntentParser:
    """Parses an ``intent.yaml`` declaration into a ``CreatorIntentSpec``.

    Supports three declaration modes:
    1. Emotive Genesis (``vibe`` key)
    2. Blueprint Derivation (``base_blueprint`` key)
    3. Hybrid (both keys present)

    SESSION-140: Accepts an optional ``RuntimeDistillationBus`` via dependency
    injection.  When present, the parser queries the bus for knowledge
    constraints and clamps translated parameters accordingly.  When absent,
    gracefully degrades to built-in heuristics.
    """

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.knowledge_bus = knowledge_bus

    # -- public API ----------------------------------------------------------

    def parse_file(self, intent_path: Path | str) -> CreatorIntentSpec:
        """Parse an intent YAML file into a CreatorIntentSpec."""
        import yaml
        path = Path(intent_path)
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return self.parse_dict(raw)

    def parse_dict(self, raw: dict) -> CreatorIntentSpec:
        """Parse a raw dict (from YAML or programmatic input) into a spec."""
        spec = CreatorIntentSpec()
        spec.raw_vibe = str(raw.get("vibe", ""))
        spec.raw_description = str(raw.get("description", ""))

        # Step 1: If base_blueprint is declared, load it as the seed genotype
        bp_path = raw.get("base_blueprint", "")
        if bp_path:
            spec.base_blueprint_path = str(bp_path)
            resolved = self._resolve_blueprint_path(bp_path)
            blueprint = Blueprint.load_yaml(resolved)
            spec.genotype = copy.deepcopy(blueprint.genotype)
            logger.info("Loaded base blueprint: %s", resolved)

        # Step 2: Apply semantic vibe overlays (heuristic)
        if spec.raw_vibe:
            self._apply_vibe(spec.genotype, spec.raw_vibe)

        # Step 3: Apply explicit parameter overrides
        overrides = raw.get("overrides", {})
        if overrides:
            self._apply_overrides(spec.genotype, overrides)

        # Step 4: Knowledge-Grounded Clamping (SESSION-140)
        # After heuristic translation + overrides, clamp to knowledge boundaries
        if spec.raw_vibe or overrides:
            self._apply_knowledge_grounding(spec)

        # Step 5: Derivation controls
        spec.evolve_variants = int(raw.get("evolve_variants", 0))
        spec.freeze_locks = list(raw.get("freeze_locks", []))

        # Step 6 (SESSION-187): Semantic VFX Plugin Resolution
        # Resolve which VFX plugins should be activated based on the user's
        # natural-language vibe.  Uses the SemanticOrchestrator which performs
        # heuristic keyword matching with hallucination guard filtering.
        try:
            from .semantic_orchestrator import resolve_active_vfx_plugins
            llm_suggested = raw.get("active_vfx_plugins", None)
            spec.active_vfx_plugins = resolve_active_vfx_plugins(
                raw_vibe=spec.raw_vibe,
                llm_suggested=llm_suggested,
            )
            if spec.active_vfx_plugins:
                logger.info(
                    "[DirectorIntent] SESSION-187 VFX plugins resolved: %s",
                    spec.active_vfx_plugins,
                )
        except Exception as _vfx_err:
            # Graceful degradation: VFX resolution failure is non-fatal
            logger.warning(
                "[DirectorIntent] SESSION-187 VFX plugin resolution failed "
                "(graceful degradation): %s",
                _vfx_err,
            )
            spec.active_vfx_plugins = []

        return spec

    # -- Knowledge-Grounded Clamping (SESSION-140) ---------------------------

    def _apply_knowledge_grounding(self, spec: CreatorIntentSpec) -> None:
        """Query the RuntimeDistillationBus and clamp parameters to knowledge
        boundaries.  Gracefully degrades if bus is absent or returns nothing.

        This is the core of the Knowledge-Grounded Vibe-to-Math bridge:
        1. Determine which knowledge modules are relevant to the vibe tokens.
        2. For each relevant module, query the bus for compiled constraints.
        3. Compare each flat parameter against knowledge min/max boundaries.
        4. Clamp out-of-bounds values and record provenance + conflicts.
        """
        if self.knowledge_bus is None:
            logger.debug("No knowledge bus injected — using heuristic fallback only")
            return

        # Determine relevant modules from vibe tokens
        import re
        tokens = re.split(r"[,;，；\s的]+", spec.raw_vibe.strip().lower()) if spec.raw_vibe else []
        relevant_modules: set = set()
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            if token in VIBE_TO_KNOWLEDGE_MODULES:
                relevant_modules.update(VIBE_TO_KNOWLEDGE_MODULES[token])
            else:
                # Partial match
                for key, modules in VIBE_TO_KNOWLEDGE_MODULES.items():
                    if key in token or token in key:
                        relevant_modules.update(modules)

        if not relevant_modules:
            # No matching knowledge modules — graceful degradation
            logger.info(
                "No knowledge modules matched vibe '%s' — heuristic fallback active",
                spec.raw_vibe,
            )
            return

        # Query each relevant module's compiled parameter space
        flat = spec.genotype.flat_params()
        any_clamped = False

        for module_name in relevant_modules:
            compiled = self.knowledge_bus.get_compiled_space(module_name)
            if compiled is None:
                logger.debug("Module '%s' has no compiled space — skipping", module_name)
                continue

            # Iterate over the compiled space's constraints
            for idx, param_name in enumerate(compiled.param_names):
                # Try to match against flat params (with alias resolution)
                matched_key = self._resolve_param_key(param_name, flat)
                if matched_key is None:
                    continue

                original_value = flat[matched_key]
                has_min = bool(compiled.has_min[idx])
                has_max = bool(compiled.has_max[idx])
                min_val = float(compiled.min_values[idx]) if has_min else None
                max_val = float(compiled.max_values[idx]) if has_max else None
                is_hard = bool(compiled.hard_mask[idx])
                clamped_value = original_value

                # Check and clamp
                needs_clamp = False
                if has_min and original_value < min_val:
                    clamped_value = min_val
                    needs_clamp = True
                if has_max and original_value > max_val:
                    clamped_value = max_val
                    needs_clamp = True

                if needs_clamp:
                    flat[matched_key] = clamped_value
                    any_clamped = True

                    # Record provenance
                    spec.applied_knowledge_rules.append(KnowledgeProvenanceRecord(
                        rule_id=f"{module_name}.{param_name}",
                        module_name=module_name,
                        param_constrained=matched_key,
                        original_value=original_value,
                        clamped_value=clamped_value,
                        constraint_type="hard" if is_hard else "soft",
                        description=f"Distilled from {module_name}: {param_name} "
                                    f"[{min_val}, {max_val}]",
                    ))

                    # Record conflict
                    severity = "fatal" if is_hard else "physical"
                    spec.knowledge_conflicts.append(KnowledgeConflict(
                        severity=severity,
                        param_key=matched_key,
                        user_value=original_value,
                        knowledge_min=min_val,
                        knowledge_max=max_val,
                        clamped_value=clamped_value,
                        rule_description=f"Distilled from {module_name}: {param_name}",
                        is_hard_constraint=is_hard,
                    ))

                    logger.info(
                        "Knowledge clamp: %s = %.4f → %.4f (rule: %s.%s, %s)",
                        matched_key, original_value, clamped_value,
                        module_name, param_name,
                        "HARD" if is_hard else "soft",
                    )

        if any_clamped:
            spec.genotype.apply_flat_params(flat)
            spec.knowledge_grounded = True
            logger.info(
                "Knowledge grounding applied: %d rules activated, %d conflicts detected",
                len(spec.applied_knowledge_rules),
                len(spec.knowledge_conflicts),
            )
        else:
            logger.debug("Knowledge grounding: no parameters needed clamping")

    @staticmethod
    def _resolve_param_key(
        knowledge_param: str, flat: Dict[str, float]
    ) -> Optional[str]:
        """Resolve a knowledge parameter name to a flat genotype key.

        Knowledge params may be in forms like:
        - ``physics.mass`` → direct match
        - ``mass`` → leaf match against ``physics.mass``
        - ``spring_k`` → alias match (future extension)
        """
        # Direct match
        if knowledge_param in flat:
            return knowledge_param

        # Leaf match: knowledge param might be just the leaf name
        leaf = knowledge_param.split(".")[-1]
        candidates = [k for k in flat if k.endswith(f".{leaf}")]
        if len(candidates) == 1:
            return candidates[0]

        # No match
        return None

    # -- internal helpers (preserved from SESSION-139) -----------------------

    def _resolve_blueprint_path(self, bp_path: str) -> Path:
        """Resolve a blueprint path relative to workspace root."""
        p = Path(bp_path)
        if p.is_absolute() and p.exists():
            return p
        candidate = self.workspace_root / bp_path
        if candidate.exists():
            return candidate
        # Try under workspace/blueprints/
        candidate2 = self.workspace_root / "workspace" / "blueprints" / Path(bp_path).name
        if candidate2.exists():
            return candidate2
        raise FileNotFoundError(
            f"Blueprint not found: {bp_path} (searched {self.workspace_root})"
        )

    def _apply_vibe(self, genotype: Genotype, vibe: str) -> None:
        """Apply semantic vibe keywords to the genotype.

        The vibe string is tokenized by common delimiters and each token is
        looked up in ``SEMANTIC_VIBE_MAP``.  Matching adjustments are applied
        additively to the genotype's flat parameters.
        """
        import re
        tokens = re.split(r"[,;，；\s的]+", vibe.strip().lower())
        flat = genotype.flat_params()
        applied_any = False

        for token in tokens:
            token = token.strip()
            if not token:
                continue
            adjustments = SEMANTIC_VIBE_MAP.get(token)
            if adjustments is None:
                # Try partial match
                for key, adj in SEMANTIC_VIBE_MAP.items():
                    if key in token or token in key:
                        adjustments = adj
                        break
            if adjustments:
                for param_key, delta in adjustments.items():
                    if param_key in flat:
                        flat[param_key] = flat[param_key] + delta
                applied_any = True

        if applied_any:
            genotype.apply_flat_params(flat)
            logger.info("Applied vibe '%s' → %d param adjustments", vibe, len(flat))

    @staticmethod
    def _apply_overrides(genotype: Genotype, overrides: dict) -> None:
        """Apply explicit numeric overrides from the intent declaration."""
        flat = genotype.flat_params()
        for key, value in overrides.items():
            if key in flat:
                flat[key] = float(value)
        genotype.apply_flat_params(flat)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def parse_intent(
    intent_path: Path | str,
    workspace_root: Path | str | None = None,
    knowledge_bus: Optional["RuntimeDistillationBus"] = None,
) -> CreatorIntentSpec:
    """One-shot convenience: parse an intent file into a CreatorIntentSpec.

    SESSION-140: Now accepts an optional ``knowledge_bus`` for knowledge-grounded
    translation.
    """
    parser = DirectorIntentParser(
        workspace_root=workspace_root,
        knowledge_bus=knowledge_bus,
    )
    return parser.parse_file(intent_path)


__all__ = [
    "AnimationConfig",
    "Blueprint",
    "BlueprintMeta",
    "ColorPalette",
    "CreatorIntentSpec",
    "DirectorIntentParser",
    "Genotype",
    "KnowledgeConflict",
    "KnowledgeProvenanceRecord",
    "PhysicsConfig",
    "ProportionsConfig",
    "SEMANTIC_VIBE_MAP",
    "VIBE_TO_KNOWLEDGE_MODULES",
    "parse_intent",
]
