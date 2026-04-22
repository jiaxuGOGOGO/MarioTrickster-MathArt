"""Director Intent — Semantic-to-Parametric Translation & Blueprint Inheritance.

SESSION-139: P0-SESSION-136-DIRECTOR-STUDIO-V2

This module implements the **Semantic-to-Parametric Translation Bridge** that
converts an artist's natural-language intent (``intent.yaml``) into a strongly-
typed ``CreatorIntentSpec``.  It supports three declaration modes:

1. **Emotive Genesis** — fuzzy vibe strings (e.g. ``"活泼的跳跃"``) are mapped
   to numeric parameter ranges via a configurable semantic lookup table.
2. **Blueprint Derivation** — a ``base_blueprint`` path is loaded, its genotype
   is used as the seed, and ``freeze_locks`` protect specified gene families
   from mutation during controlled-variable evolution.
3. **Hybrid** — emotive overrides applied on top of a blueprint base.

Architecture discipline:
- This module is an **independent logic bridge** injected via the workspace
  package.  It NEVER touches the core pipeline directly.
- Output is always a ``CreatorIntentSpec`` dataclass — the single strongly-typed
  contract consumed downstream by the interactive gate and the inner-loop
  evolution engine.

External research anchors (SESSION-139):
- Proc3D (2026): semantic → procedural-graph parameter decomposition
- Pixar USD VariantSets / LIVRPS: asset-root inherits arcs for variant families
- GAAF (2026): adaptive gene freezing during evolutionary search
- DreamCrafter (ACM 2025): proxy-preview iterative editing loop
"""
from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
# Semantic Translation Table
# ---------------------------------------------------------------------------

# Maps fuzzy vibe keywords → parameter adjustment multipliers.
# Each entry: { "family.param": multiplier_delta }
# Multiplier delta is added to the base value (1.0 = no change).
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
# CreatorIntentSpec — the single strongly-typed output contract
# ---------------------------------------------------------------------------

@dataclass
class CreatorIntentSpec:
    """The strongly-typed output of the Director Intent parser.

    Regardless of how fuzzy the user's description was, this spec is the
    **only** contract passed downstream to the interactive gate and the
    inner-loop evolution engine.
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

    def to_dict(self) -> dict:
        return {
            "genotype": self.genotype.to_dict(),
            "base_blueprint_path": self.base_blueprint_path,
            "evolve_variants": self.evolve_variants,
            "freeze_locks": list(self.freeze_locks),
            "raw_vibe": self.raw_vibe,
            "raw_description": self.raw_description,
            "intent_id": self.intent_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CreatorIntentSpec":
        return cls(
            genotype=Genotype.from_dict(d.get("genotype", {})),
            base_blueprint_path=d.get("base_blueprint_path", ""),
            evolve_variants=int(d.get("evolve_variants", 0)),
            freeze_locks=list(d.get("freeze_locks", [])),
            raw_vibe=d.get("raw_vibe", ""),
            raw_description=d.get("raw_description", ""),
            intent_id=d.get("intent_id", str(uuid.uuid4())),
        )


# ---------------------------------------------------------------------------
# Intent Parser
# ---------------------------------------------------------------------------

class DirectorIntentParser:
    """Parses an ``intent.yaml`` declaration into a ``CreatorIntentSpec``.

    Supports three declaration modes:
    1. Emotive Genesis (``vibe`` key)
    2. Blueprint Derivation (``base_blueprint`` key)
    3. Hybrid (both keys present)
    """

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()

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

        # Step 2: Apply semantic vibe overlays
        if spec.raw_vibe:
            self._apply_vibe(spec.genotype, spec.raw_vibe)

        # Step 3: Apply explicit parameter overrides
        overrides = raw.get("overrides", {})
        if overrides:
            self._apply_overrides(spec.genotype, overrides)

        # Step 4: Derivation controls
        spec.evolve_variants = int(raw.get("evolve_variants", 0))
        spec.freeze_locks = list(raw.get("freeze_locks", []))

        return spec

    # -- internal helpers ----------------------------------------------------

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

def parse_intent(intent_path: Path | str, workspace_root: Path | str | None = None) -> CreatorIntentSpec:
    """One-shot convenience: parse an intent file into a CreatorIntentSpec."""
    parser = DirectorIntentParser(workspace_root=workspace_root)
    return parser.parse_file(intent_path)


__all__ = [
    "AnimationConfig",
    "Blueprint",
    "BlueprintMeta",
    "ColorPalette",
    "CreatorIntentSpec",
    "DirectorIntentParser",
    "Genotype",
    "PhysicsConfig",
    "ProportionsConfig",
    "SEMANTIC_VIBE_MAP",
    "parse_intent",
]
