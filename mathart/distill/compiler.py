"""Compilation Layer: Transform rules into mathematical constraints.

This module takes structured ``KnowledgeRule`` objects from the parser and
compiles them into ``ParameterSpace`` definitions that the optimization layer
can search. It bridges the gap between human-readable knowledge and
machine-optimizable parameter configurations.

The compiler maintains a registry of known parameters and their relationships,
enabling automatic constraint propagation when new rules are added.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union

from .parser import KnowledgeRule, RuleType, TargetModule


@dataclass
class Constraint:
    """A mathematical constraint on a parameter.

    Attributes
    ----------
    param_name : str
        Fully qualified parameter name (e.g., "animation.skeleton.shoulder_rom").
    min_value : float or None
        Minimum allowed value.
    max_value : float or None
        Maximum allowed value.
    default_value : float or None
        Recommended default.
    allowed_values : list or None
        For enum-type constraints.
    formula : str or None
        Mathematical relationship (e.g., "damping = mass * 0.3").
    is_hard : bool
        Whether this is a hard constraint (must not be violated).
    source_rule_id : str
        ID of the KnowledgeRule that generated this constraint.
    """

    param_name: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    default_value: Optional[float] = None
    allowed_values: Optional[list] = None
    formula: Optional[str] = None
    is_hard: bool = False
    source_rule_id: str = ""

    def contains(self, value: float) -> bool:
        """Check if a value satisfies this constraint."""
        if self.allowed_values is not None:
            return value in self.allowed_values
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Constraint":
        return cls(**d)


@dataclass
class ParameterSpace:
    """A searchable parameter space defined by compiled constraints.

    This represents the "search space" that the optimization layer explores.
    Each dimension corresponds to a parameter with its valid range.
    """

    name: str
    constraints: dict[str, Constraint] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def add_constraint(self, constraint: Constraint) -> None:
        """Add or merge a constraint into the space."""
        key = constraint.param_name
        if key in self.constraints:
            existing = self.constraints[key]
            # Merge: tighten ranges
            if constraint.min_value is not None:
                if existing.min_value is None:
                    existing.min_value = constraint.min_value
                else:
                    existing.min_value = max(existing.min_value, constraint.min_value)
            if constraint.max_value is not None:
                if existing.max_value is None:
                    existing.max_value = constraint.max_value
                else:
                    existing.max_value = min(existing.max_value, constraint.max_value)
            if constraint.default_value is not None:
                existing.default_value = constraint.default_value
            if constraint.is_hard:
                existing.is_hard = True
        else:
            self.constraints[key] = constraint

    def get_ranges(self) -> dict[str, tuple[float, float]]:
        """Return parameter ranges as {name: (min, max)} dict."""
        ranges = {}
        for name, c in self.constraints.items():
            lo = c.min_value if c.min_value is not None else 0.0
            hi = c.max_value if c.max_value is not None else 1.0
            ranges[name] = (lo, hi)
        return ranges

    def get_defaults(self) -> dict[str, float]:
        """Return default values for all parameters."""
        defaults = {}
        for name, c in self.constraints.items():
            if c.default_value is not None:
                defaults[name] = c.default_value
            elif c.min_value is not None and c.max_value is not None:
                defaults[name] = (c.min_value + c.max_value) / 2.0
            elif c.min_value is not None:
                defaults[name] = c.min_value
            elif c.max_value is not None:
                defaults[name] = c.max_value
            else:
                defaults[name] = 0.0
        return defaults

    def validate(self, params: dict[str, float]) -> list[str]:
        """Validate a parameter set against all constraints.

        Returns a list of violation messages (empty if valid).
        """
        violations = []
        for name, value in params.items():
            if name in self.constraints:
                c = self.constraints[name]
                if not c.contains(value):
                    violations.append(
                        f"{name}={value} violates constraint "
                        f"[{c.min_value}, {c.max_value}] "
                        f"(hard={c.is_hard}, rule={c.source_rule_id})"
                    )
        return violations

    @property
    def dimensions(self) -> int:
        """Number of parameters in the space."""
        return len(self.constraints)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "constraints": {k: v.to_dict() for k, v in self.constraints.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParameterSpace":
        space = cls(name=d["name"], metadata=d.get("metadata", {}))
        for k, v in d.get("constraints", {}).items():
            space.constraints[k] = Constraint.from_dict(v)
        return space


# ── Parameter name mapping ────────────────────────────────────────────

# Maps common knowledge terms to fully qualified parameter names
_PARAM_MAPPING: dict[str, str] = {
    # Animation / Skeleton
    "肩关节": "animation.skeleton.shoulder_rom",
    "肘关节": "animation.skeleton.elbow_rom",
    "髋关节": "animation.skeleton.hip_rom",
    "膝关节": "animation.skeleton.knee_rom",
    "头身比": "animation.skeleton.head_ratio",
    "shoulder": "animation.skeleton.shoulder_rom",
    "elbow": "animation.skeleton.elbow_rom",
    "hip": "animation.skeleton.hip_rom",
    "knee": "animation.skeleton.knee_rom",
    "head_ratio": "animation.skeleton.head_ratio",
    # Animation / Dynamics
    "弹簧刚度": "animation.spring.stiffness",
    "阻尼系数": "animation.spring.damping",
    "质量": "animation.spring.mass",
    "stiffness": "animation.spring.stiffness",
    "damping": "animation.spring.damping",
    "mass": "animation.spring.mass",
    # Animation / Timing
    "帧数": "animation.timing.frame_count",
    "帧率": "animation.timing.fps",
    "frame_count": "animation.timing.frame_count",
    "fps": "animation.timing.fps",
    # OKLAB / Palette
    "色相偏移": "oklab.palette.hue_shift",
    "饱和度": "oklab.palette.saturation",
    "明度范围": "oklab.palette.lightness_range",
    "hue_shift": "oklab.palette.hue_shift",
    "saturation": "oklab.palette.saturation",
    # Export / Unity
    "PPU": "export.unity.ppu",
    "ppu": "export.unity.ppu",
    "filter_mode": "export.unity.filter_mode",
    # Level / WFC
    "关卡宽度": "level.wfc.width",
    "关卡高度": "level.wfc.height",
    "level_width": "level.wfc.width",
    "level_height": "level.wfc.height",
}


class RuleCompiler:
    """Compiles KnowledgeRules into ParameterSpaces.

    Usage::

        compiler = RuleCompiler()
        space = compiler.compile(rules, "animation_params")
        print(space.get_ranges())
    """

    def __init__(self, param_mapping: Optional[dict[str, str]] = None):
        self.param_mapping = param_mapping or _PARAM_MAPPING

    def compile(
        self, rules: list[KnowledgeRule], space_name: str = "default"
    ) -> ParameterSpace:
        """Compile a list of rules into a parameter space."""
        space = ParameterSpace(name=space_name)

        for rule in rules:
            constraint = self._rule_to_constraint(rule)
            if constraint is not None:
                space.add_constraint(constraint)

        space.metadata = {
            "compiled_from": len(rules),
            "dimensions": space.dimensions,
        }
        return space

    def compile_by_module(
        self, rules: list[KnowledgeRule]
    ) -> dict[str, ParameterSpace]:
        """Compile rules into separate parameter spaces per module."""
        by_module: dict[str, list[KnowledgeRule]] = {}
        for rule in rules:
            mod = rule.target_module.value
            by_module.setdefault(mod, []).append(rule)

        spaces = {}
        for mod_name, mod_rules in by_module.items():
            spaces[mod_name] = self.compile(mod_rules, f"{mod_name}_params")
        return spaces

    def _rule_to_constraint(self, rule: KnowledgeRule) -> Optional[Constraint]:
        """Convert a single rule to a constraint."""
        constraint_data = rule.constraint
        if not constraint_data:
            return None

        # Resolve parameter name
        param_name = self._resolve_param_name(rule.target_param, rule.target_module)

        ctype = constraint_data.get("type", "")

        if ctype == "range":
            return Constraint(
                param_name=param_name,
                min_value=constraint_data.get("min"),
                max_value=constraint_data.get("max"),
                is_hard=rule.rule_type == RuleType.HARD_CONSTRAINT,
                source_rule_id=rule.id,
            )
        elif ctype == "exact":
            val = constraint_data.get("value")
            return Constraint(
                param_name=param_name,
                default_value=val,
                min_value=val,
                max_value=val,
                is_hard=rule.rule_type == RuleType.HARD_CONSTRAINT,
                source_rule_id=rule.id,
            )
        elif ctype == "enum":
            return Constraint(
                param_name=param_name,
                allowed_values=constraint_data.get("values"),
                is_hard=rule.rule_type == RuleType.HARD_CONSTRAINT,
                source_rule_id=rule.id,
            )
        elif ctype == "formula":
            return Constraint(
                param_name=param_name,
                formula=constraint_data.get("expr"),
                is_hard=False,
                source_rule_id=rule.id,
            )

        return None

    def _resolve_param_name(self, raw_name: str, module: TargetModule) -> str:
        """Resolve a raw parameter name to a fully qualified name."""
        raw_clean = raw_name.strip()

        # Direct lookup
        if raw_clean in self.param_mapping:
            return self.param_mapping[raw_clean]

        # Case-insensitive lookup
        for key, value in self.param_mapping.items():
            if key.lower() == raw_clean.lower():
                return value

        # Fallback: construct from module + raw name
        safe_name = re.sub(r"[^\w]", "_", raw_clean).lower().strip("_")
        return f"{module.value}.{safe_name}"

    # ── Serialization ────────────────────────────────────────────────

    @staticmethod
    def save_space(space: ParameterSpace, filepath: Union[str, Path]) -> None:
        """Save a parameter space to JSON."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(
            json.dumps(space.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def load_space(filepath: Union[str, Path]) -> ParameterSpace:
        """Load a parameter space from JSON."""
        filepath = Path(filepath)
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return ParameterSpace.from_dict(data)


# Need re for _resolve_param_name fallback
import re
