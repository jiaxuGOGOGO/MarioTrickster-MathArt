"""
Parametric SDF Morphology System — Smooth CSG Character Generation.

SESSION-057: Research-grounded implementation of parametric character
morphology using Inigo Quilez's Smooth Minimum (smin) operators for
automatic organic shape blending.

Research foundations:
  1. **Inigo Quilez — Smooth Minimum (2013)**:
     Normalized polynomial smin operators that blend SDF primitives with
     parameter k mapping directly to blending thickness in distance units.
     The quadratic variant: smin = min(a,b) - h²·k/4 where h = max(k-|a-b|,0)/k.
     The cubic variant: smin = min(a,b) - h³·k/6 for C2 continuity.
     Mix factor returned alongside distance for material/color interpolation.

  2. **Inigo Quilez — 2D Distance Functions**:
     Exact SDF primitives (circle, capsule, rounded box, ellipse, egg,
     trapezoid, vesica, moon) used as parametric body-part building blocks.

  3. **Constructive Solid Geometry (CSG) via SDF**:
     Union (min), intersection (max), subtraction (max(a,-b)), and their
     smooth variants enable sculpting complex organic forms from simple
     primitives — like clay modeling in code.

Architecture:
    ┌──────────────────────────────────────────────────────────────────────┐
    │  MorphologyPrimitive                                                 │
    │  ├─ Parametric SDF body-part primitives (IQ formulas)               │
    │  ├─ circle, capsule, rounded_box, ellipse, egg, trapezoid, vesica  │
    │  └─ Each returns (distance, gradient) for rendering pipeline        │
    ├──────────────────────────────────────────────────────────────────────┤
    │  SmoothCSGOperator                                                   │
    │  ├─ smin_quadratic(a, b, k) — C1 smooth union with mix factor      │
    │  ├─ smin_cubic(a, b, k) — C2 smooth union with mix factor          │
    │  ├─ smooth_subtraction(a, b, k) — carving/hollowing                │
    │  ├─ smooth_intersection(a, b, k) — organic intersection            │
    │  └─ All operators return (distance, blend_factor)                   │
    ├──────────────────────────────────────────────────────────────────────┤
    │  MorphologyGenotype                                                  │
    │  ├─ Body topology genes (which primitives, how many limbs)          │
    │  ├─ Per-part parameter genes (size, position, rotation)             │
    │  ├─ Blend genes (smin k-values between adjacent parts)             │
    │  ├─ Mutation and crossover operators                                │
    │  └─ decode_to_sdf() → composite SDF function                       │
    ├──────────────────────────────────────────────────────────────────────┤
    │  MorphologyFactory                                                   │
    │  ├─ generate_random(archetype) → MorphologyGenotype                 │
    │  ├─ evolve_population(pop, fitness) → next generation               │
    │  ├─ render_silhouette(genotype, resolution) → numpy array           │
    │  └─ evaluate_diversity(population) → diversity score                │
    └──────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.smooth_morphology import (
        MorphologyGenotype, MorphologyFactory, SmoothCSGOperator,
        render_morphology_silhouette,
    )

    # Generate a random monster morphology
    factory = MorphologyFactory(seed=42)
    genotype = factory.generate_random(archetype="monster_heavy")

    # Decode to SDF and render
    sdf_func = genotype.decode_to_sdf()
    silhouette = render_morphology_silhouette(genotype, resolution=128)

    # Evolve a population
    population = [factory.generate_random() for _ in range(20)]
    fitness_scores = [evaluate(g) for g in population]
    next_gen = factory.evolve_population(population, fitness_scores)
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Tuple

import numpy as np


# ── Type Aliases ─────────────────────────────────────────────────────────────

SDFFunc2D = Callable[[np.ndarray, np.ndarray], np.ndarray]
SDFWithMix = Callable[[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]]
ParameterContext = Mapping[str, Any]


def _resolve_contextual_scalar(
    raw_value: Any,
    *,
    context: Optional[ParameterContext],
    keys: tuple[str, ...],
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """Resolve a possibly dynamic scalar from an optional parameter context."""
    value = raw_value
    if context is not None:
        for key in keys:
            if key in context:
                value = context[key]
                break
    if callable(value):
        try:
            value = value(context or {})
        except TypeError:
            value = value()
    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim > 0:
        if arr.size != 1:
            raise ValueError(
                f"Context keys {keys!r} must resolve to a scalar, got shape {arr.shape}"
            )
        value_f = float(arr.reshape(-1)[0])
    else:
        value_f = float(arr)
    lo = -np.inf if minimum is None else minimum
    hi = np.inf if maximum is None else maximum
    return float(np.clip(value_f, lo, hi))


def _resolve_contextual_bool(
    raw_value: Any,
    *,
    context: Optional[ParameterContext],
    keys: tuple[str, ...],
) -> bool:
    value = raw_value
    if context is not None:
        for key in keys:
            if key in context:
                value = context[key]
                break
    if callable(value):
        try:
            value = value(context or {})
        except TypeError:
            value = value()
    return bool(value)


# ── Smooth CSG Operators (Inigo Quilez) ──────────────────────────────────────


class SmoothCSGOperator:
    """Smooth Constructive Solid Geometry operators based on IQ's smin family.

    All operators are normalized so that parameter k maps directly to the
    blending thickness in distance units (IQ normalization convention).

    Reference: https://iquilezles.org/articles/smin/
    """

    @staticmethod
    def smin_quadratic(a: np.ndarray, b: np.ndarray, k: float
                       ) -> Tuple[np.ndarray, np.ndarray]:
        """Quadratic polynomial smooth minimum (C1 continuous).

        Returns (blended_distance, mix_factor) where mix_factor ∈ [0,1]
        indicates the blend ratio (0 = fully a, 1 = fully b).

        IQ normalization: k *= 4.0 internally.
        """
        if k < 1e-10:
            result = np.minimum(a, b)
            mix = np.where(a < b, np.zeros_like(a), np.ones_like(a))
            return result, mix

        k_norm = k * 4.0
        h = np.clip((k_norm - np.abs(a - b)) / k_norm, 0.0, 1.0)
        w = h * h
        m = w * 0.5
        s = w * k_norm * 0.25
        dist = np.where(a < b, a - s, b - s)
        mix = np.where(a < b, m, 1.0 - m)
        return dist, mix

    @staticmethod
    def smin_cubic(a: np.ndarray, b: np.ndarray, k: float
                   ) -> Tuple[np.ndarray, np.ndarray]:
        """Cubic polynomial smooth minimum (C2 continuous).

        Smoother transitions than quadratic, better for organic shapes.
        IQ normalization: k *= 6.0 internally.
        """
        if k < 1e-10:
            result = np.minimum(a, b)
            mix = np.where(a < b, np.zeros_like(a), np.ones_like(a))
            return result, mix

        k_norm = k * 6.0
        h = np.clip((k_norm - np.abs(a - b)) / k_norm, 0.0, 1.0)
        w = h * h * h
        m = w * 0.5
        s = w * k_norm / 6.0
        dist = np.where(a < b, a - s, b - s)
        mix = np.where(a < b, m, 1.0 - m)
        return dist, mix

    @staticmethod
    def smin_exponential(a: np.ndarray, b: np.ndarray, k: float
                         ) -> Tuple[np.ndarray, np.ndarray]:
        """Exponential smooth minimum (C∞ continuous, infinite support).

        Most mathematically elegant but slightly more expensive.
        """
        if k < 1e-10:
            result = np.minimum(a, b)
            mix = np.where(a < b, np.zeros_like(a), np.ones_like(a))
            return result, mix

        ea = np.exp(-a / k)
        eb = np.exp(-b / k)
        r = ea + eb
        dist = -k * np.log(np.maximum(r, 1e-30))
        mix = eb / np.maximum(r, 1e-30)
        return dist, mix

    @staticmethod
    def smooth_subtraction(a: np.ndarray, b: np.ndarray, k: float
                           ) -> np.ndarray:
        """Smooth subtraction: carve shape b from shape a.

        Useful for creating hollows, eye sockets, mouth cavities.
        """
        if k < 1e-10:
            return np.maximum(a, -b)

        k_norm = k * 6.0
        h = np.clip((k_norm - np.abs(-b - a)) / k_norm, 0.0, 1.0)
        w = h * h * h
        return np.where(
            -b > a,
            -b + w * k_norm / 6.0,
            a + w * k_norm / 6.0,
        )

    @staticmethod
    def smooth_intersection(a: np.ndarray, b: np.ndarray, k: float
                            ) -> np.ndarray:
        """Smooth intersection: keep only where both shapes overlap.

        Useful for creating joint regions, armor plates.
        """
        neg_a = -a
        neg_b = -b
        result, _ = SmoothCSGOperator.smin_cubic(neg_a, neg_b, k)
        return -result


# ── 2D SDF Primitives (Inigo Quilez formulas) ───────────────────────────────


class MorphologyPrimitive:
    """Parametric 2D SDF primitives for body-part construction.

    All primitives are centered at origin; use translate/rotate/scale
    to position them. Formulas from IQ's distfunctions2d article.

    Reference: https://iquilezles.org/articles/distfunctions2d/
    """

    @staticmethod
    def circle(x: np.ndarray, y: np.ndarray, r: float) -> np.ndarray:
        """Exact circle SDF."""
        return np.sqrt(x * x + y * y) - r

    @staticmethod
    def capsule(x: np.ndarray, y: np.ndarray,
                r1: float, r2: float, h: float) -> np.ndarray:
        """Uneven capsule SDF (two different end radii).

        Perfect for limbs with varying thickness (thigh→calf, upper→forearm).
        """
        ax = np.abs(x)
        b = (r1 - r2) / max(h, 1e-10)
        a_coeff = math.sqrt(max(1.0 - b * b, 0.0))
        k = ax * (-b) + y * a_coeff
        cond1 = k < 0.0
        cond2 = k > a_coeff * h
        dist = np.where(
            cond1,
            np.sqrt(ax * ax + y * y) - r1,
            np.where(
                cond2,
                np.sqrt(ax * ax + (y - h) * (y - h)) - r2,
                ax * a_coeff + y * b - r1,
            ),
        )
        return dist

    @staticmethod
    def rounded_box(x: np.ndarray, y: np.ndarray,
                    bx: float, by: float, r: float) -> np.ndarray:
        """Rounded box SDF with uniform corner radius.

        Ideal for torsos, shields, rectangular body segments.
        """
        qx = np.abs(x) - bx + r
        qy = np.abs(y) - by + r
        return (np.minimum(np.maximum(qx, qy), 0.0)
                + np.sqrt(np.maximum(qx, 0.0)**2 + np.maximum(qy, 0.0)**2)
                - r)

    @staticmethod
    def ellipse(x: np.ndarray, y: np.ndarray,
                a: float, b: float) -> np.ndarray:
        """Approximate ellipse SDF.

        Uses the bound-based approximation for efficiency.
        Suitable for organic body cores, heads, egg-shaped parts.
        """
        # Simplified ellipse approximation (not exact but fast)
        px = np.abs(x)
        py = np.abs(y)
        # Normalize to unit circle and scale
        nx = px / max(a, 1e-10)
        ny = py / max(b, 1e-10)
        r = np.sqrt(nx * nx + ny * ny)
        return (r - 1.0) * min(a, b)

    @staticmethod
    def egg(x: np.ndarray, y: np.ndarray,
            ra: float, rb: float) -> np.ndarray:
        """Egg shape SDF (asymmetric ellipse).

        Perfect for organic body cores with top-heavy or bottom-heavy bias.
        """
        # Egg = circle with y-dependent radius modulation
        px = np.abs(x)
        # Modulate radius based on y position
        t = np.clip((y + ra) / (2.0 * ra + 1e-10), 0.0, 1.0)
        r_local = ra * (1.0 - rb * t * t)
        return np.sqrt(px * px + y * y) - r_local

    @staticmethod
    def trapezoid(x: np.ndarray, y: np.ndarray,
                  r1: float, r2: float, he: float) -> np.ndarray:
        """Isosceles trapezoid SDF.

        Good for torso variations (broad shoulders, narrow waist).
        """
        px = np.abs(x)
        # Simplified trapezoid
        k1x, k1y = r2, he
        k2x, k2y = r2 - r1, 2.0 * he

        cax = px - np.minimum(px, np.where(y < 0.0, r1, r2))
        cay = np.abs(y) - he
        ca_dist2 = cax * cax + cay * cay

        # Edge distance
        t = np.clip(
            ((k1x - px) * k2x + (k1y - np.abs(y)) * k2y)
            / (k2x * k2x + k2y * k2y + 1e-10),
            0.0, 1.0,
        )
        cbx = px - k1x + k2x * t
        cby = np.abs(y) - k1y + k2y * t
        cb_dist2 = cbx * cbx + cby * cby

        s = np.where(
            (cbx < 0.0) & (cay < 0.0),
            -np.ones_like(px),
            np.ones_like(px),
        )
        return s * np.sqrt(np.minimum(ca_dist2, cb_dist2))

    @staticmethod
    def vesica(x: np.ndarray, y: np.ndarray,
               d: float, r: float) -> np.ndarray:
        """Vesica (lens/leaf) shape SDF.

        Excellent for wings, fins, blade-like appendages.
        """
        px = np.abs(x)
        py = np.abs(y)
        # Simplified vesica: intersection of two offset circles
        b = math.sqrt(max(r * r - d * d, 0.0))
        cond = (py - b) * d > px * b
        dist = np.where(
            cond,
            np.sqrt(px * px + (py - b) * (py - b)) - r,
            np.sqrt((px - d) * (px - d) + py * py) - r,
        )
        return dist


# ── Transform Helpers ────────────────────────────────────────────────────────


def _translate(sdf_func: SDFFunc2D, tx: float, ty: float) -> SDFFunc2D:
    """Translate an SDF primitive."""
    def translated(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return sdf_func(x - tx, y - ty)
    return translated


def _rotate(sdf_func: SDFFunc2D, angle: float) -> SDFFunc2D:
    """Rotate an SDF primitive by angle (radians)."""
    c, s = math.cos(angle), math.sin(angle)
    def rotated(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        rx = c * x + s * y
        ry = -s * x + c * y
        return sdf_func(rx, ry)
    return rotated


def _scale(sdf_func: SDFFunc2D, sx: float, sy: float = 0.0) -> SDFFunc2D:
    """Scale an SDF primitive."""
    if sy == 0.0:
        sy = sx
    s_min = min(abs(sx), abs(sy))
    def scaled(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return sdf_func(x / sx, y / sy) * s_min
    return scaled


# ── Morphology Part Types ────────────────────────────────────────────────────


class PartType(str, Enum):
    """Types of body parts in the morphology system."""
    CORE = "core"           # Main body/torso
    HEAD = "head"           # Head/skull
    LIMB_UPPER = "limb_upper"  # Upper arm/thigh
    LIMB_LOWER = "limb_lower"  # Forearm/calf
    APPENDAGE = "appendage"    # Tail, wing, horn, tentacle
    WEAPON = "weapon"          # Blade, club, claw
    SHIELD = "shield"          # Armor plate, shell


class PrimitiveType(str, Enum):
    """Available SDF primitive shapes."""
    CIRCLE = "circle"
    CAPSULE = "capsule"
    ROUNDED_BOX = "rounded_box"
    ELLIPSE = "ellipse"
    EGG = "egg"
    TRAPEZOID = "trapezoid"
    VESICA = "vesica"


# ── Morphology Part Gene ─────────────────────────────────────────────────────


@dataclass
class MorphologyPartGene:
    """A single body part encoded as parametric SDF genes.

    Each part has:
    - A primitive type (what shape)
    - Position/rotation relative to parent
    - Size parameters specific to the primitive
    - Blend parameter k for smooth union with parent
    - Material index for color mixing
    """
    part_type: str = PartType.CORE.value
    primitive: str = PrimitiveType.CIRCLE.value

    # Transform genes
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0

    # Primitive-specific size parameters
    param_a: float = 0.15   # Primary size (radius, width, etc.)
    param_b: float = 0.10   # Secondary size (height, second radius, etc.)
    param_c: float = 0.03   # Tertiary (corner radius, taper, etc.)

    # Blend genes
    blend_k: float = 0.05   # smin blending radius with parent
    blend_type: str = "cubic"  # "quadratic", "cubic", "exponential"

    # Material gene
    material_index: int = 0  # Index into palette for color mixing

    # Connectivity
    parent_index: int = -1   # -1 = root part

    def resolve_parameters(
        self,
        parameter_context: Optional[ParameterContext] = None,
        *,
        context_prefix: str = "",
    ) -> dict[str, float]:
        """Resolve static or dynamic parameters for this part.

        The dynamic layer is additive and backward-compatible: if a parameter is
        absent from ``parameter_context`` the serialized gene value is used.
        """
        scoped = lambda name: (f"{context_prefix}{name}", name)
        return {
            "offset_x": _resolve_contextual_scalar(
                self.offset_x,
                context=parameter_context,
                keys=scoped("offset_x"),
                minimum=-4.0,
                maximum=4.0,
            ),
            "offset_y": _resolve_contextual_scalar(
                self.offset_y,
                context=parameter_context,
                keys=scoped("offset_y"),
                minimum=-4.0,
                maximum=4.0,
            ),
            "rotation": _resolve_contextual_scalar(
                self.rotation,
                context=parameter_context,
                keys=scoped("rotation"),
                minimum=-math.tau,
                maximum=math.tau,
            ),
            "scale_x": _resolve_contextual_scalar(
                self.scale_x,
                context=parameter_context,
                keys=scoped("scale_x"),
                minimum=0.05,
                maximum=4.0,
            ),
            "scale_y": _resolve_contextual_scalar(
                self.scale_y,
                context=parameter_context,
                keys=scoped("scale_y"),
                minimum=0.05,
                maximum=4.0,
            ),
            "param_a": _resolve_contextual_scalar(
                self.param_a,
                context=parameter_context,
                keys=scoped("param_a"),
                minimum=0.005,
                maximum=4.0,
            ),
            "param_b": _resolve_contextual_scalar(
                self.param_b,
                context=parameter_context,
                keys=scoped("param_b"),
                minimum=0.005,
                maximum=4.0,
            ),
            "param_c": _resolve_contextual_scalar(
                self.param_c,
                context=parameter_context,
                keys=scoped("param_c"),
                minimum=0.001,
                maximum=4.0,
            ),
            "blend_k": _resolve_contextual_scalar(
                self.blend_k,
                context=parameter_context,
                keys=scoped("blend_k"),
                minimum=0.0,
                maximum=0.5,
            ),
        }

    def build_sdf(
        self,
        parameter_context: Optional[ParameterContext] = None,
        *,
        context_prefix: str = "",
    ) -> SDFFunc2D:
        """Build the SDF function for this part.

        ``parameter_context`` may override serialized scalar fields at runtime,
        enabling time-varying parameter animation without mutating the trunk
        genotype representation.
        """
        prim = PrimitiveType(self.primitive)
        params = self.resolve_parameters(
            parameter_context=parameter_context,
            context_prefix=context_prefix,
        )
        a = params["param_a"]
        b = params["param_b"]
        c = params["param_c"]

        if prim == PrimitiveType.CIRCLE:
            base = lambda x, y: MorphologyPrimitive.circle(x, y, a)
        elif prim == PrimitiveType.CAPSULE:
            base = lambda x, y: MorphologyPrimitive.capsule(x, y, a, b, c)
        elif prim == PrimitiveType.ROUNDED_BOX:
            base = lambda x, y: MorphologyPrimitive.rounded_box(x, y, a, b, c)
        elif prim == PrimitiveType.ELLIPSE:
            base = lambda x, y: MorphologyPrimitive.ellipse(x, y, a, b)
        elif prim == PrimitiveType.EGG:
            base = lambda x, y: MorphologyPrimitive.egg(x, y, a, b)
        elif prim == PrimitiveType.TRAPEZOID:
            base = lambda x, y: MorphologyPrimitive.trapezoid(x, y, a, b, c)
        elif prim == PrimitiveType.VESICA:
            base = lambda x, y: MorphologyPrimitive.vesica(x, y, a, b)
        else:
            base = lambda x, y: MorphologyPrimitive.circle(x, y, a)

        # Apply transforms
        sdf = base
        if abs(params["scale_x"] - 1.0) > 1e-6 or abs(params["scale_y"] - 1.0) > 1e-6:
            sdf = _scale(sdf, params["scale_x"], params["scale_y"])
        if abs(params["rotation"]) > 1e-6:
            sdf = _rotate(sdf, params["rotation"])
        if abs(params["offset_x"]) > 1e-6 or abs(params["offset_y"]) > 1e-6:
            sdf = _translate(sdf, params["offset_x"], params["offset_y"])

        return sdf

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "part_type": self.part_type,
            "primitive": self.primitive,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "rotation": self.rotation,
            "scale_x": self.scale_x,
            "scale_y": self.scale_y,
            "param_a": self.param_a,
            "param_b": self.param_b,
            "param_c": self.param_c,
            "blend_k": self.blend_k,
            "blend_type": self.blend_type,
            "material_index": self.material_index,
            "parent_index": self.parent_index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MorphologyPartGene:
        """Deserialize from dict."""
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})


# ── Morphology Genotype ──────────────────────────────────────────────────────


@dataclass
class MorphologyGenotype:
    """Complete parametric morphology genotype for a creature.

    The genotype encodes:
    - A tree of body parts, each as a MorphologyPartGene
    - Global symmetry flag (bilateral symmetry for most creatures)
    - Global scale factor
    - Palette genes for material coloring

    The decode_to_sdf() method constructs the composite SDF by
    recursively blending parts using IQ's smooth minimum operators.
    """
    parts: list[MorphologyPartGene] = field(default_factory=list)
    bilateral_symmetry: bool = True
    global_scale: float = 1.0
    archetype: str = "monster_basic"

    # Palette: list of (L, a, b) in OKLAB color space
    palette: list[list[float]] = field(default_factory=lambda: [
        [0.55, 0.10, 0.05],   # Body primary
        [0.40, 0.15, 0.02],   # Body secondary
        [0.65, -0.05, 0.08],  # Accent
        [0.30, 0.05, -0.10],  # Dark detail
        [0.80, 0.00, 0.00],   # Highlight
    ])

    def decode_to_sdf(
        self,
        parameter_context: Optional[ParameterContext] = None,
    ) -> SDFFunc2D:
        """Decode genotype into a composite SDF function.

        Builds the SDF tree by:
        1. Creating individual part SDFs
        2. Blending children into parents using smin with per-part k
        3. Optionally applying bilateral symmetry
        4. Applying global scale

        Returns a function f(x, y) -> distance that represents the
        complete creature silhouette.
        """
        if not self.parts:
            # Default: single circle
            return lambda x, y: MorphologyPrimitive.circle(x, y, 0.15)

        # Build individual part SDFs. Runtime overrides are scoped per part so
        # the static serialized genotype remains the source of truth.
        part_sdfs = [
            part.build_sdf(
                parameter_context=parameter_context,
                context_prefix=f"parts.{idx}.",
            )
            for idx, part in enumerate(self.parts)
        ]

        # Composite by blending children into parents (bottom-up)
        # First pass: identify root and children
        children: dict[int, list[int]] = {}
        root_indices = []
        for i, part in enumerate(self.parts):
            if part.parent_index < 0 or part.parent_index >= len(self.parts):
                root_indices.append(i)
            else:
                children.setdefault(part.parent_index, []).append(i)

        if not root_indices:
            root_indices = [0]

        # Build composite SDF recursively
        def _build_subtree(idx: int) -> SDFFunc2D:
            base_sdf = part_sdfs[idx]
            child_indices = children.get(idx, [])

            if not child_indices:
                return base_sdf

            # Blend all children into this part
            composite = base_sdf
            for ci in child_indices:
                child_sdf = _build_subtree(ci)
                child_part = self.parts[ci]
                child_params = child_part.resolve_parameters(
                    parameter_context=parameter_context,
                    context_prefix=f"parts.{ci}.",
                )
                k = max(child_params["blend_k"], 0.001)
                blend_type = child_part.blend_type

                # Capture current composite and child for closure
                prev_composite = composite
                prev_child = child_sdf

                if blend_type == "quadratic":
                    def _blended(x, y, _pc=prev_composite, _ch=prev_child, _k=k):
                        d1 = _pc(x, y)
                        d2 = _ch(x, y)
                        result, _ = SmoothCSGOperator.smin_quadratic(d1, d2, _k)
                        return result
                elif blend_type == "exponential":
                    def _blended(x, y, _pc=prev_composite, _ch=prev_child, _k=k):
                        d1 = _pc(x, y)
                        d2 = _ch(x, y)
                        result, _ = SmoothCSGOperator.smin_exponential(d1, d2, _k)
                        return result
                else:  # cubic (default)
                    def _blended(x, y, _pc=prev_composite, _ch=prev_child, _k=k):
                        d1 = _pc(x, y)
                        d2 = _ch(x, y)
                        result, _ = SmoothCSGOperator.smin_cubic(d1, d2, _k)
                        return result

                composite = _blended

            return composite

        # Combine all root parts
        if len(root_indices) == 1:
            creature_sdf = _build_subtree(root_indices[0])
        else:
            creature_sdf = _build_subtree(root_indices[0])
            for ri in root_indices[1:]:
                subtree = _build_subtree(ri)
                prev = creature_sdf
                def _merged(x, y, _p=prev, _s=subtree):
                    d1 = _p(x, y)
                    d2 = _s(x, y)
                    result, _ = SmoothCSGOperator.smin_cubic(d1, d2, 0.05)
                    return result
                creature_sdf = _merged

        # Apply bilateral symmetry
        bilateral_symmetry = _resolve_contextual_bool(
            self.bilateral_symmetry,
            context=parameter_context,
            keys=("bilateral_symmetry",),
        )
        if bilateral_symmetry:
            sym_sdf = creature_sdf
            def _symmetric(x, y, _f=sym_sdf):
                return _f(np.abs(x), y)
            creature_sdf = _symmetric

        # Apply global scale
        global_scale = _resolve_contextual_scalar(
            self.global_scale,
            context=parameter_context,
            keys=("global_scale",),
            minimum=0.05,
            maximum=4.0,
        )
        if abs(global_scale - 1.0) > 1e-6:
            s = global_scale
            unscaled = creature_sdf
            def _scaled(x, y, _f=unscaled, _s=s):
                return _f(x / _s, y / _s) * _s
            creature_sdf = _scaled

        return creature_sdf

    def count_parts(self) -> int:
        """Return total number of body parts."""
        return len(self.parts)

    def get_bounding_radius(self) -> float:
        """Estimate bounding radius from part positions and sizes."""
        if not self.parts:
            return 0.2
        max_r = 0.0
        for part in self.parts:
            dist = math.sqrt(part.offset_x**2 + part.offset_y**2)
            size = max(part.param_a, part.param_b) * max(part.scale_x, part.scale_y)
            max_r = max(max_r, dist + size + part.blend_k)
        return max_r * self.global_scale

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "parts": [p.to_dict() for p in self.parts],
            "bilateral_symmetry": self.bilateral_symmetry,
            "global_scale": self.global_scale,
            "archetype": self.archetype,
            "palette": [list(c) for c in self.palette],
        }

    @classmethod
    def from_dict(cls, data: dict) -> MorphologyGenotype:
        """Deserialize from dict."""
        parts = [MorphologyPartGene.from_dict(p) for p in data.get("parts", [])]
        return cls(
            parts=parts,
            bilateral_symmetry=data.get("bilateral_symmetry", True),
            global_scale=data.get("global_scale", 1.0),
            archetype=data.get("archetype", "monster_basic"),
            palette=data.get("palette", []),
        )


# ── Morphology Factory ──────────────────────────────────────────────────────


# Archetype templates: define body plan constraints
ARCHETYPE_BODY_PLANS: dict[str, dict] = {
    "monster_basic": {
        "core_primitives": ["circle", "ellipse", "egg"],
        "min_limbs": 2,
        "max_limbs": 4,
        "can_have_appendages": True,
        "can_have_weapons": False,
        "symmetry_default": True,
        "scale_range": (0.8, 1.5),
    },
    "monster_heavy": {
        "core_primitives": ["rounded_box", "trapezoid", "ellipse"],
        "min_limbs": 2,
        "max_limbs": 6,
        "can_have_appendages": True,
        "can_have_weapons": True,
        "symmetry_default": True,
        "scale_range": (1.2, 2.0),
    },
    "monster_flying": {
        "core_primitives": ["ellipse", "egg", "vesica"],
        "min_limbs": 0,
        "max_limbs": 2,
        "can_have_appendages": True,
        "can_have_weapons": False,
        "symmetry_default": True,
        "scale_range": (0.6, 1.2),
    },
    "creature_mutant": {
        "core_primitives": ["circle", "ellipse", "egg", "capsule"],
        "min_limbs": 1,
        "max_limbs": 8,
        "can_have_appendages": True,
        "can_have_weapons": True,
        "symmetry_default": False,
        "scale_range": (0.5, 2.5),
    },
    "boss": {
        "core_primitives": ["rounded_box", "trapezoid", "ellipse"],
        "min_limbs": 4,
        "max_limbs": 8,
        "can_have_appendages": True,
        "can_have_weapons": True,
        "symmetry_default": True,
        "scale_range": (1.5, 3.0),
    },
}


class MorphologyFactory:
    """Factory for generating, mutating, and evolving morphology genotypes.

    Uses the archetype body plan constraints to generate valid creatures,
    and provides mutation/crossover operators for evolutionary search.
    """

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)

    def generate_random(self, archetype: str = "monster_basic"
                        ) -> MorphologyGenotype:
        """Generate a random morphology genotype for the given archetype."""
        plan = ARCHETYPE_BODY_PLANS.get(archetype, ARCHETYPE_BODY_PLANS["monster_basic"])

        parts: list[MorphologyPartGene] = []

        # 1. Core body
        core_prim = self.rng.choice(plan["core_primitives"])
        core = MorphologyPartGene(
            part_type=PartType.CORE.value,
            primitive=core_prim,
            param_a=self._rand_range(0.10, 0.25),
            param_b=self._rand_range(0.08, 0.20),
            param_c=self._rand_range(0.02, 0.06),
            blend_k=0.05,
            material_index=0,
            parent_index=-1,
        )
        parts.append(core)

        # 2. Head
        head = MorphologyPartGene(
            part_type=PartType.HEAD.value,
            primitive=self.rng.choice(["circle", "ellipse", "egg"]),
            offset_y=self._rand_range(0.15, 0.30),
            param_a=self._rand_range(0.08, 0.18),
            param_b=self._rand_range(0.06, 0.15),
            param_c=self._rand_range(0.01, 0.04),
            blend_k=self._rand_range(0.03, 0.12),
            material_index=0,
            parent_index=0,
        )
        parts.append(head)

        # 3. Limbs
        n_limbs = self.rng.integers(plan["min_limbs"], plan["max_limbs"] + 1)
        for i in range(n_limbs):
            # Upper limb
            side = 1.0 if i % 2 == 0 else -1.0
            y_offset = self._rand_range(-0.15, 0.10)
            upper = MorphologyPartGene(
                part_type=PartType.LIMB_UPPER.value,
                primitive=self.rng.choice(["capsule", "ellipse", "rounded_box"]),
                offset_x=side * self._rand_range(0.10, 0.25),
                offset_y=y_offset,
                rotation=side * self._rand_range(-0.8, 0.8),
                param_a=self._rand_range(0.04, 0.10),
                param_b=self._rand_range(0.03, 0.08),
                param_c=self._rand_range(0.06, 0.18),
                blend_k=self._rand_range(0.03, 0.10),
                material_index=1,
                parent_index=0,
            )
            parts.append(upper)
            upper_idx = len(parts) - 1

            # Lower limb (50% chance)
            if self.rng.random() > 0.4:
                lower = MorphologyPartGene(
                    part_type=PartType.LIMB_LOWER.value,
                    primitive=self.rng.choice(["capsule", "ellipse"]),
                    offset_x=side * self._rand_range(0.05, 0.15),
                    offset_y=y_offset + self._rand_range(-0.15, -0.05),
                    rotation=side * self._rand_range(-0.5, 0.5),
                    param_a=self._rand_range(0.03, 0.07),
                    param_b=self._rand_range(0.02, 0.05),
                    param_c=self._rand_range(0.05, 0.12),
                    blend_k=self._rand_range(0.02, 0.08),
                    material_index=1,
                    parent_index=upper_idx,
                )
                parts.append(lower)

        # 4. Appendages (tail, wings, horns)
        if plan["can_have_appendages"] and self.rng.random() > 0.3:
            n_appendages = self.rng.integers(1, 4)
            for _ in range(n_appendages):
                app = MorphologyPartGene(
                    part_type=PartType.APPENDAGE.value,
                    primitive=self.rng.choice(["vesica", "capsule", "ellipse"]),
                    offset_x=self._rand_range(-0.20, 0.20),
                    offset_y=self._rand_range(-0.25, 0.25),
                    rotation=self._rand_range(-1.5, 1.5),
                    param_a=self._rand_range(0.03, 0.12),
                    param_b=self._rand_range(0.02, 0.08),
                    param_c=self._rand_range(0.01, 0.04),
                    blend_k=self._rand_range(0.02, 0.08),
                    material_index=2,
                    parent_index=self.rng.integers(0, max(1, len(parts))),
                )
                parts.append(app)

        # 5. Weapons
        if plan["can_have_weapons"] and self.rng.random() > 0.5:
            weapon = MorphologyPartGene(
                part_type=PartType.WEAPON.value,
                primitive=self.rng.choice(["vesica", "capsule", "rounded_box"]),
                offset_x=self._rand_range(0.15, 0.35),
                offset_y=self._rand_range(-0.05, 0.10),
                rotation=self._rand_range(-1.0, 1.0),
                param_a=self._rand_range(0.05, 0.15),
                param_b=self._rand_range(0.02, 0.06),
                param_c=self._rand_range(0.01, 0.03),
                blend_k=self._rand_range(0.01, 0.05),
                material_index=3,
                parent_index=self.rng.integers(0, max(1, len(parts))),
            )
            parts.append(weapon)

        # Build genotype
        genotype = MorphologyGenotype(
            parts=parts,
            bilateral_symmetry=plan["symmetry_default"],
            global_scale=self._rand_range(*plan["scale_range"]),
            archetype=archetype,
        )

        # Randomize palette
        genotype.palette = [
            [self._rand_range(0.3, 0.8),
             self._rand_range(-0.15, 0.15),
             self._rand_range(-0.15, 0.15)]
            for _ in range(5)
        ]

        return genotype

    def mutate(self, genotype: MorphologyGenotype,
               mutation_rate: float = 0.15) -> MorphologyGenotype:
        """Mutate a morphology genotype.

        Mutations include:
        - Part parameter perturbation (continuous)
        - Part primitive type change (discrete)
        - Add/remove parts (structural)
        - Blend parameter adjustment
        - Palette mutation
        """
        g = copy.deepcopy(genotype)

        for part in g.parts:
            if self.rng.random() < mutation_rate:
                # Perturb continuous parameters
                part.offset_x += self.rng.normal(0, 0.03)
                part.offset_y += self.rng.normal(0, 0.03)
                part.rotation += self.rng.normal(0, 0.15)
                part.param_a = max(0.02, part.param_a + self.rng.normal(0, 0.02))
                part.param_b = max(0.01, part.param_b + self.rng.normal(0, 0.015))
                part.param_c = max(0.005, part.param_c + self.rng.normal(0, 0.01))
                part.blend_k = float(np.clip(
                    part.blend_k + self.rng.normal(0, 0.015), 0.005, 0.20))

            if self.rng.random() < mutation_rate * 0.3:
                # Change primitive type
                primitives = [p.value for p in PrimitiveType]
                part.primitive = self.rng.choice(primitives)

        # Structural mutation: add part (10% chance)
        if self.rng.random() < mutation_rate * 0.5 and len(g.parts) < 15:
            new_part = MorphologyPartGene(
                part_type=self.rng.choice([p.value for p in PartType]),
                primitive=self.rng.choice([p.value for p in PrimitiveType]),
                offset_x=self.rng.normal(0, 0.15),
                offset_y=self.rng.normal(0, 0.15),
                rotation=self.rng.normal(0, 0.5),
                param_a=self._rand_range(0.03, 0.12),
                param_b=self._rand_range(0.02, 0.08),
                param_c=self._rand_range(0.01, 0.04),
                blend_k=self._rand_range(0.02, 0.10),
                material_index=self.rng.integers(0, 5),
                parent_index=self.rng.integers(0, len(g.parts)),
            )
            g.parts.append(new_part)

        # Structural mutation: remove part (5% chance, keep at least 2)
        if self.rng.random() < mutation_rate * 0.3 and len(g.parts) > 2:
            idx = self.rng.integers(1, len(g.parts))  # Never remove root
            # Reparent children
            for part in g.parts:
                if part.parent_index == idx:
                    part.parent_index = g.parts[idx].parent_index
                elif part.parent_index > idx:
                    part.parent_index -= 1
            g.parts.pop(idx)

        # Palette mutation
        for color in g.palette:
            if self.rng.random() < mutation_rate:
                for j in range(3):
                    color[j] = float(np.clip(
                        color[j] + self.rng.normal(0, 0.05), -0.3, 1.0))

        # Global scale mutation
        if self.rng.random() < mutation_rate:
            g.global_scale = float(np.clip(
                g.global_scale + self.rng.normal(0, 0.1), 0.3, 3.0))

        return g

    def crossover(self, parent_a: MorphologyGenotype,
                  parent_b: MorphologyGenotype) -> MorphologyGenotype:
        """Crossover two morphology genotypes.

        Uses subtree crossover: randomly select a subtree from each parent
        and swap them, maintaining structural validity.
        """
        child = copy.deepcopy(parent_a)

        # Part-level crossover: take some parts from parent_b
        if len(parent_b.parts) > 1:
            n_swap = self.rng.integers(1, max(2, len(parent_b.parts) // 2))
            swap_indices = self.rng.choice(
                len(parent_b.parts), size=min(n_swap, len(parent_b.parts)),
                replace=False,
            )
            for idx in swap_indices:
                if idx < len(child.parts):
                    donor = copy.deepcopy(parent_b.parts[idx])
                    donor.parent_index = min(
                        donor.parent_index, len(child.parts) - 1)
                    child.parts[idx] = donor
                else:
                    donor = copy.deepcopy(parent_b.parts[idx])
                    donor.parent_index = self.rng.integers(0, len(child.parts))
                    child.parts.append(donor)

        # Blend palette from both parents
        for i in range(min(len(child.palette), len(parent_b.palette))):
            alpha = self.rng.random()
            for j in range(3):
                child.palette[i][j] = (
                    alpha * child.palette[i][j]
                    + (1 - alpha) * parent_b.palette[i][j]
                )

        # Average global scale
        child.global_scale = (parent_a.global_scale + parent_b.global_scale) / 2.0

        return child

    def evolve_population(
        self,
        population: list[MorphologyGenotype],
        fitness_scores: list[float],
        elite_ratio: float = 0.1,
        mutation_rate: float = 0.15,
    ) -> list[MorphologyGenotype]:
        """Evolve a population using tournament selection + crossover + mutation.

        Returns a new population of the same size.
        """
        pop_size = len(population)
        if pop_size == 0:
            return []

        # Sort by fitness (descending)
        sorted_indices = np.argsort(fitness_scores)[::-1]

        # Elite preservation
        n_elite = max(1, int(pop_size * elite_ratio))
        next_gen = [copy.deepcopy(population[sorted_indices[i]])
                    for i in range(n_elite)]

        # Fill rest with crossover + mutation
        while len(next_gen) < pop_size:
            # Tournament selection (size 3)
            t_size = min(3, pop_size)
            t1 = self.rng.choice(pop_size, size=t_size, replace=False)
            t2 = self.rng.choice(pop_size, size=t_size, replace=False)
            p1 = population[t1[np.argmax([fitness_scores[i] for i in t1])]]
            p2 = population[t2[np.argmax([fitness_scores[i] for i in t2])]]

            child = self.crossover(p1, p2)
            child = self.mutate(child, mutation_rate)
            next_gen.append(child)

        return next_gen[:pop_size]

    def _rand_range(self, lo: float, hi: float) -> float:
        """Generate a random float in [lo, hi]."""
        return float(self.rng.uniform(lo, hi))


# ── Rendering ────────────────────────────────────────────────────────────────


def render_morphology_silhouette(
    genotype: MorphologyGenotype,
    resolution: int = 128,
    padding: float = 0.1,
) -> np.ndarray:
    """Render a morphology genotype as a binary silhouette image.

    Returns a (resolution, resolution) numpy array where 1.0 = inside shape.
    """
    sdf_func = genotype.decode_to_sdf()
    bound = genotype.get_bounding_radius() + padding

    x = np.linspace(-bound, bound, resolution)
    y = np.linspace(-bound, bound, resolution)
    xx, yy = np.meshgrid(x, y[::-1])  # Flip y for image coordinates

    distances = sdf_func(xx, yy)
    silhouette = np.where(distances <= 0.0, 1.0, 0.0)
    return silhouette


def evaluate_morphology_diversity(
    population: list[MorphologyGenotype],
    resolution: int = 64,
) -> float:
    """Evaluate visual diversity of a population using silhouette comparison.

    Returns a diversity score in [0, 1] where 1 = maximally diverse.
    Uses pairwise Jaccard distance between silhouettes.
    """
    if len(population) < 2:
        return 0.0

    silhouettes = [render_morphology_silhouette(g, resolution) for g in population]
    n = len(silhouettes)
    total_distance = 0.0
    count = 0

    for i in range(n):
        for j in range(i + 1, n):
            intersection = np.sum(silhouettes[i] * silhouettes[j])
            union = np.sum(np.maximum(silhouettes[i], silhouettes[j]))
            if union > 0:
                jaccard = 1.0 - intersection / union
                total_distance += jaccard
            else:
                total_distance += 1.0
            count += 1

    return float(total_distance / max(count, 1))


def evaluate_morphology_quality(genotype: MorphologyGenotype,
                                resolution: int = 64) -> dict[str, float]:
    """Evaluate quality metrics for a single morphology.

    Returns dict with:
    - fill_ratio: fraction of bounding box filled (avoid too sparse/dense)
    - compactness: perimeter² / area ratio (lower = more compact)
    - part_count: number of body parts
    - symmetry_score: bilateral symmetry measure
    """
    silhouette = render_morphology_silhouette(genotype, resolution)
    area = float(np.sum(silhouette))
    total = float(silhouette.size)
    fill_ratio = area / max(total, 1.0)

    # Estimate perimeter (count boundary pixels)
    padded = np.pad(silhouette, 1, mode='constant', constant_values=0)
    edges = (
        np.abs(padded[1:-1, 1:-1] - padded[:-2, 1:-1])
        + np.abs(padded[1:-1, 1:-1] - padded[2:, 1:-1])
        + np.abs(padded[1:-1, 1:-1] - padded[1:-1, :-2])
        + np.abs(padded[1:-1, 1:-1] - padded[1:-1, 2:])
    )
    perimeter = float(np.sum(edges > 0))
    compactness = (perimeter * perimeter) / max(area, 1.0) / (4.0 * math.pi)

    # Symmetry score (compare left and right halves)
    mid = resolution // 2
    left = silhouette[:, :mid]
    right = silhouette[:, mid:][:, ::-1]
    min_w = min(left.shape[1], right.shape[1])
    if min_w > 0:
        sym_match = float(np.mean(left[:, :min_w] == right[:, :min_w]))
    else:
        sym_match = 1.0

    return {
        "fill_ratio": fill_ratio,
        "compactness": compactness,
        "part_count": float(genotype.count_parts()),
        "symmetry_score": sym_match,
    }


# ── Preset Morphology Factories ─────────────────────────────────────────────


def slime_morphology() -> MorphologyGenotype:
    """Create a simple slime/blob morphology."""
    return MorphologyGenotype(
        parts=[
            MorphologyPartGene(
                part_type=PartType.CORE.value,
                primitive=PrimitiveType.EGG.value,
                param_a=0.18, param_b=0.3, param_c=0.02,
                blend_k=0.05, parent_index=-1,
            ),
            MorphologyPartGene(
                part_type=PartType.HEAD.value,
                primitive=PrimitiveType.CIRCLE.value,
                offset_y=0.12,
                param_a=0.06, param_b=0.06,
                blend_k=0.08, parent_index=0,
            ),
        ],
        bilateral_symmetry=True,
        global_scale=0.8,
        archetype="monster_basic",
    )


def golem_morphology() -> MorphologyGenotype:
    """Create a heavy golem morphology with thick limbs."""
    return MorphologyGenotype(
        parts=[
            MorphologyPartGene(
                part_type=PartType.CORE.value,
                primitive=PrimitiveType.ROUNDED_BOX.value,
                param_a=0.18, param_b=0.22, param_c=0.04,
                blend_k=0.05, parent_index=-1,
            ),
            MorphologyPartGene(
                part_type=PartType.HEAD.value,
                primitive=PrimitiveType.CIRCLE.value,
                offset_y=0.25,
                param_a=0.12, param_b=0.10,
                blend_k=0.10, parent_index=0,
            ),
            # Left arm
            MorphologyPartGene(
                part_type=PartType.LIMB_UPPER.value,
                primitive=PrimitiveType.CAPSULE.value,
                offset_x=0.22, offset_y=0.05,
                rotation=-0.3,
                param_a=0.07, param_b=0.05, param_c=0.15,
                blend_k=0.08, material_index=1, parent_index=0,
            ),
            # Right arm
            MorphologyPartGene(
                part_type=PartType.LIMB_UPPER.value,
                primitive=PrimitiveType.CAPSULE.value,
                offset_x=-0.22, offset_y=0.05,
                rotation=0.3,
                param_a=0.07, param_b=0.05, param_c=0.15,
                blend_k=0.08, material_index=1, parent_index=0,
            ),
            # Left leg
            MorphologyPartGene(
                part_type=PartType.LIMB_UPPER.value,
                primitive=PrimitiveType.CAPSULE.value,
                offset_x=0.10, offset_y=-0.22,
                param_a=0.06, param_b=0.05, param_c=0.14,
                blend_k=0.06, material_index=1, parent_index=0,
            ),
            # Right leg
            MorphologyPartGene(
                part_type=PartType.LIMB_UPPER.value,
                primitive=PrimitiveType.CAPSULE.value,
                offset_x=-0.10, offset_y=-0.22,
                param_a=0.06, param_b=0.05, param_c=0.14,
                blend_k=0.06, material_index=1, parent_index=0,
            ),
            # Weapon arm (giant blade)
            MorphologyPartGene(
                part_type=PartType.WEAPON.value,
                primitive=PrimitiveType.VESICA.value,
                offset_x=0.35, offset_y=0.10,
                rotation=-0.5,
                param_a=0.08, param_b=0.20,
                blend_k=0.04, material_index=3, parent_index=2,
            ),
        ],
        bilateral_symmetry=False,
        global_scale=1.5,
        archetype="monster_heavy",
    )


def flying_morphology() -> MorphologyGenotype:
    """Create a flying creature with wings."""
    return MorphologyGenotype(
        parts=[
            MorphologyPartGene(
                part_type=PartType.CORE.value,
                primitive=PrimitiveType.ELLIPSE.value,
                param_a=0.12, param_b=0.08,
                blend_k=0.05, parent_index=-1,
            ),
            MorphologyPartGene(
                part_type=PartType.HEAD.value,
                primitive=PrimitiveType.CIRCLE.value,
                offset_y=0.12,
                param_a=0.07,
                blend_k=0.06, parent_index=0,
            ),
            # Left wing
            MorphologyPartGene(
                part_type=PartType.APPENDAGE.value,
                primitive=PrimitiveType.VESICA.value,
                offset_x=0.18, offset_y=0.05,
                rotation=-0.3,
                param_a=0.06, param_b=0.15,
                blend_k=0.04, material_index=2, parent_index=0,
            ),
            # Right wing
            MorphologyPartGene(
                part_type=PartType.APPENDAGE.value,
                primitive=PrimitiveType.VESICA.value,
                offset_x=-0.18, offset_y=0.05,
                rotation=0.3,
                param_a=0.06, param_b=0.15,
                blend_k=0.04, material_index=2, parent_index=0,
            ),
            # Tail
            MorphologyPartGene(
                part_type=PartType.APPENDAGE.value,
                primitive=PrimitiveType.CAPSULE.value,
                offset_y=-0.15,
                rotation=0.1,
                param_a=0.03, param_b=0.02, param_c=0.10,
                blend_k=0.03, material_index=2, parent_index=0,
            ),
        ],
        bilateral_symmetry=False,
        global_scale=0.9,
        archetype="monster_flying",
    )
