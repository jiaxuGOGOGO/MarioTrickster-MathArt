"""Mid-generation quality checkpoint — microsecond-level PDG branch pruning.

SESSION-120 (P1-NEW-8) upgrade
==============================

The production pipeline increasingly feeds a PDG v2 graph of heavy nodes
(physics XPBD, DQS skinning, multi-channel rendering, SSIM visual fitness).
Those nodes can take seconds per invocation, and the evolutionary loop may
sample thousands of candidates. Without an inexpensive gate that rejects
obviously-malformed candidates before they hit the GPU pool, the *cheap
checkpoint* itself will turn into the bottleneck.

This module is that gate.

Design pillars
--------------

1. **Multi-Fidelity Optimisation** (arXiv 2402.09638): a cheap surrogate
   decides whether the expensive evaluator is even worth invoking.
2. **Houdini PDG Conditional Execution** (`pdg.workItemState.CookedCancel`
   + ``dependencyState`` — see Houdini 21.0 docs): failure of a mid-pipeline
   check must *cancel* the work item and propagate ``SKIPPED`` status to
   every downstream node at zero cost. This module talks to the scheduler
   via :class:`mathart.level.pdg.EarlyRejectionError`.
3. **Data-Oriented Heuristics**: every gate operates only on raw NumPy
   arrays and Python scalars. There is no file I/O, no PIL `Image.open`,
   no rig instantiation, no physics object construction. We measure
   throughput in **microseconds**.

Architecture discipline
-----------------------

* The checkpoints are *independent filter components* — they are not
  welded into the heavy renderer. They can be composed into a PDG node via
  :class:`QualityCheckpointNode`, or invoked directly by the evolutionary
  controller for offline batch pruning.
* They NEVER catch generic exceptions. Bugs in upstream decoding must
  remain visible (Anti-Silent-Swallow guard).
* They NEVER hold resources: no locks, no semaphores, no open files.

Example
-------

>>> gate = SkeletonProportionGate.from_style_bounds()
>>> verdict = gate.evaluate(context={"head_radius": 0.8}, deps={})
>>> verdict.passed
False
>>> verdict.prune_reason
'skeleton_head_radius_out_of_bounds'
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Protocol, runtime_checkable

import numpy as np

from mathart.level.pdg import EarlyRejectionError

# ── Public Verdict Contract ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckpointVerdict:
    """Structured result of a :class:`MidGenerationCheckpoint` invocation.

    The verdict is deliberately *immutable* and JSON-friendly so that it can
    be persisted into the PDG trace and later aggregated by the evolutionary
    controller for fitness shaping.
    """

    passed: bool
    checkpoint_name: str
    prune_reason: Optional[str] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    fitness_penalty: Optional[float] = None
    duration_us: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": bool(self.passed),
            "checkpoint_name": self.checkpoint_name,
            "prune_reason": self.prune_reason,
            "diagnostics": dict(self.diagnostics),
            "fitness_penalty": self.fitness_penalty,
            "duration_us": round(self.duration_us, 3),
        }

    def raise_if_rejected(self, source_node: str = "") -> None:
        """Translate a failed verdict into :class:`EarlyRejectionError`.

        This is the canonical bridge between the data-only verdict and the
        PDG scheduler's cancellation signal. When the verdict passes this
        method is a no-op.
        """
        if self.passed:
            return
        raise EarlyRejectionError(
            prune_reason=self.prune_reason or "checkpoint_failed",
            source_node=source_node or self.checkpoint_name,
            diagnostics=dict(self.diagnostics),
            fitness_penalty=self.fitness_penalty,
        )


@runtime_checkable
class MidGenerationCheckpoint(Protocol):
    """Protocol for any microsecond-level mid-generation quality gate.

    Implementations must:

    * Accept only read-only ``context`` and ``deps`` mappings.
    * Never perform I/O, instantiate renderers, or allocate GPU memory.
    * Return a :class:`CheckpointVerdict` \u2014 they MUST NOT raise on
      semantic rejection. Raising is reserved for true bugs (invalid input
      types, missing required keys that indicate programmer error).
    """

    name: str

    def evaluate(
        self,
        context: Mapping[str, Any],
        deps: Mapping[str, Any],
    ) -> CheckpointVerdict:
        ...


# ── Default Proportion Bounds (mirrors STYLE_PARAMETER_BOUNDS) ────────────────

# Kept as a plain dict to stay microsecond-level: no BoundInterval objects, no
# imports of the heavy animation module. The numbers below are copied verbatim
# from ``mathart.animation.genotype.STYLE_PARAMETER_BOUNDS`` so that this file
# can be unit-tested with zero downstream import cost. If the canonical bounds
# shift, the test ``test_skeleton_bounds_mirror_genotype_contract`` asserts the
# two stay in sync.

DEFAULT_SKELETON_PROPORTION_BOUNDS: dict[str, tuple[float, float]] = {
    "head_radius":    (0.26, 0.52),
    "torso_width":    (0.16, 0.36),
    "torso_height":   (0.12, 0.30),
    "arm_thickness":  (0.04, 0.12),
    "leg_thickness":  (0.05, 0.14),
    "hand_radius":    (0.03, 0.09),
    "foot_width":     (0.05, 0.16),
    "foot_height":    (0.025, 0.09),
}

# Relative-proportion guards (dimensionless ratios). These catch "inverted" or
# "folded back" skeletons where individual fields might still be in-range but
# their *composition* is clearly non-anatomical.
DEFAULT_PROPORTION_RATIO_GUARDS: dict[str, tuple[float, float]] = {
    # head / torso_height: from 0.9 (tall humanoid) to 3.5 (chibi). Anything
    # below 0.5 means head is absurdly small vs torso; above 5.0 is a giant
    # floating head.
    "head_to_torso_height": (0.5, 5.0),
    # leg_thickness / arm_thickness: legs should be at least as thick as arms
    # but not 3x thicker. Lower bound 0.6 tolerates some stylisation.
    "leg_to_arm_thickness": (0.6, 3.0),
    # foot_width / hand_radius: feet wider than hands but within a sane ratio.
    "foot_to_hand": (0.8, 4.0),
}


# ── Gate 1: Skeleton Proportion Interceptor ──────────────────────────────────


@dataclass
class SkeletonProportionGate:
    """Microsecond-level bounding-box / proportion guard on a decoded skeleton.

    The gate reads ONLY scalar body-proportion fields from the context
    (same field names as ``mathart.animation.genotype.STYLE_PARAMETER_BOUNDS``)
    and emits ``EarlyRejectionError`` when any field is out of bounds, NaN,
    or the composite ratios imply an inverted/folded anatomy.

    The whole evaluation is pure NumPy arithmetic \u2014 typically < 100 \u00b5s
    on a modern laptop. No skeleton instance is built, no FK is computed,
    no bone list is traversed.
    """

    bounds: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_SKELETON_PROPORTION_BOUNDS)
    )
    ratio_guards: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_PROPORTION_RATIO_GUARDS)
    )
    name: str = "skeleton_proportion_gate"
    fitness_penalty: float = 0.85
    context_key: Optional[str] = None  # If set, look inside ``context[key]`` first

    @classmethod
    def from_style_bounds(cls, **overrides: Any) -> "SkeletonProportionGate":
        return cls(**overrides)

    def _extract_scalars(
        self,
        context: Mapping[str, Any],
        deps: Mapping[str, Any],
    ) -> dict[str, float]:
        source: Mapping[str, Any] = context
        if self.context_key is not None and self.context_key in context:
            nested = context[self.context_key]
            if isinstance(nested, Mapping):
                source = nested
        values: dict[str, float] = {}
        for field_name in self.bounds.keys():
            if field_name in source:
                values[field_name] = float(source[field_name])
            elif field_name in deps:
                values[field_name] = float(deps[field_name])
        return values

    def evaluate(
        self,
        context: Mapping[str, Any],
        deps: Mapping[str, Any],
    ) -> CheckpointVerdict:
        t0 = time.perf_counter_ns()
        values = self._extract_scalars(context, deps)

        # (1) Per-field bounds check + NaN guard, vectorised.
        names = list(values.keys())
        if names:
            arr = np.array([values[n] for n in names], dtype=np.float64)
            nan_mask = ~np.isfinite(arr)
            if np.any(nan_mask):
                offending = [names[i] for i, flag in enumerate(nan_mask) if flag]
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                return CheckpointVerdict(
                    passed=False,
                    checkpoint_name=self.name,
                    prune_reason="skeleton_nan_proportion",
                    diagnostics={"fields": offending, "values": {n: values[n] for n in offending}},
                    fitness_penalty=1.0,  # maximum penalty \u2014 NaN is catastrophic
                    duration_us=duration_us,
                )
            lo = np.array([self.bounds[n][0] for n in names], dtype=np.float64)
            hi = np.array([self.bounds[n][1] for n in names], dtype=np.float64)
            out_of_bounds = (arr < lo) | (arr > hi)
            if np.any(out_of_bounds):
                bad_idx = int(np.argmax(out_of_bounds))
                bad_name = names[bad_idx]
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                return CheckpointVerdict(
                    passed=False,
                    checkpoint_name=self.name,
                    prune_reason=f"skeleton_{bad_name}_out_of_bounds",
                    diagnostics={
                        "field": bad_name,
                        "value": float(arr[bad_idx]),
                        "bounds": [float(lo[bad_idx]), float(hi[bad_idx])],
                        "all_values": dict(values),
                    },
                    fitness_penalty=self.fitness_penalty,
                    duration_us=duration_us,
                )

        # (2) Composite-ratio anti-fold guards.
        ratio_diagnostics: dict[str, float] = {}
        for ratio_name, (lo_r, hi_r) in self.ratio_guards.items():
            numerator_key, denominator_key = _RATIO_KEYS.get(ratio_name, (None, None))
            if numerator_key is None or denominator_key is None:
                continue
            if numerator_key not in values or denominator_key not in values:
                continue
            den = values[denominator_key]
            if den <= 0.0 or not math.isfinite(den):
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                return CheckpointVerdict(
                    passed=False,
                    checkpoint_name=self.name,
                    prune_reason="skeleton_proportion_inverted",
                    diagnostics={
                        "ratio": ratio_name,
                        "numerator": numerator_key,
                        "denominator": denominator_key,
                        "denominator_value": float(den),
                    },
                    fitness_penalty=1.0,
                    duration_us=duration_us,
                )
            ratio = float(values[numerator_key]) / float(den)
            ratio_diagnostics[ratio_name] = ratio
            if ratio < lo_r or ratio > hi_r:
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                return CheckpointVerdict(
                    passed=False,
                    checkpoint_name=self.name,
                    prune_reason="skeleton_proportion_inverted",
                    diagnostics={
                        "ratio": ratio_name,
                        "value": ratio,
                        "bounds": [float(lo_r), float(hi_r)],
                        "numerator": numerator_key,
                        "denominator": denominator_key,
                        "all_values": dict(values),
                    },
                    fitness_penalty=self.fitness_penalty,
                    duration_us=duration_us,
                )

        duration_us = (time.perf_counter_ns() - t0) / 1000.0
        return CheckpointVerdict(
            passed=True,
            checkpoint_name=self.name,
            diagnostics={"observed_fields": names, "ratios": ratio_diagnostics},
            duration_us=duration_us,
        )


_RATIO_KEYS: dict[str, tuple[str, str]] = {
    "head_to_torso_height": ("head_radius", "torso_height"),
    "leg_to_arm_thickness": ("leg_thickness", "arm_thickness"),
    "foot_to_hand": ("foot_width", "hand_radius"),
}


# ── Gate 2: Numerical Toxin Interceptor ──────────────────────────────────────


@dataclass
class NumericalToxinGate:
    """Catch NaN / \u00b1\u221E / magnitude explosion in early-stage tensors.

    Applies a vectorised ``np.isnan`` + ``np.isinf`` scan to every array-like
    value found in ``context``/``deps`` (whitelisted by ``tensor_keys`` when
    provided) and rejects the work item the moment poison is detected.

    This is the very first defensive line against an exploded XPBD state,
    corrupted skinning weights, or a physics step that overran CFL.
    """

    tensor_keys: Optional[Iterable[str]] = None
    max_abs_value: float = 1.0e6
    name: str = "numerical_toxin_gate"
    fitness_penalty: float = 1.0

    def _iter_arrays(
        self,
        context: Mapping[str, Any],
        deps: Mapping[str, Any],
    ) -> list[tuple[str, np.ndarray]]:
        allowed: Optional[set[str]] = (
            set(self.tensor_keys) if self.tensor_keys is not None else None
        )
        arrays: list[tuple[str, np.ndarray]] = []
        for source_name, source in (("context", context), ("deps", deps)):
            for key, value in source.items():
                if allowed is not None and key not in allowed:
                    continue
                if isinstance(value, np.ndarray):
                    arrays.append((f"{source_name}.{key}", value))
                elif isinstance(value, (list, tuple)):
                    try:
                        arr = np.asarray(value, dtype=np.float64)
                    except (TypeError, ValueError):
                        continue
                    if arr.dtype.kind in {"f", "i", "u"}:
                        arrays.append((f"{source_name}.{key}", arr))
        return arrays

    def evaluate(
        self,
        context: Mapping[str, Any],
        deps: Mapping[str, Any],
    ) -> CheckpointVerdict:
        t0 = time.perf_counter_ns()
        arrays = self._iter_arrays(context, deps)
        for qualified_key, arr in arrays:
            if arr.size == 0:
                continue
            # NaN scan \u2014 bitwise OR reduction is \u00b5s-class even for 10^6 floats.
            if np.isnan(arr).any():
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                return CheckpointVerdict(
                    passed=False,
                    checkpoint_name=self.name,
                    prune_reason="numerical_nan_detected",
                    diagnostics={
                        "tensor": qualified_key,
                        "shape": list(arr.shape),
                        "nan_count": int(np.isnan(arr).sum()),
                    },
                    fitness_penalty=self.fitness_penalty,
                    duration_us=duration_us,
                )
            if np.isinf(arr).any():
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                return CheckpointVerdict(
                    passed=False,
                    checkpoint_name=self.name,
                    prune_reason="numerical_inf_detected",
                    diagnostics={
                        "tensor": qualified_key,
                        "shape": list(arr.shape),
                        "inf_count": int(np.isinf(arr).sum()),
                    },
                    fitness_penalty=self.fitness_penalty,
                    duration_us=duration_us,
                )
            # Magnitude explosion: cheap max-abs check.
            # np.abs on float arrays is fully vectorised; no Python loop.
            peak = float(np.max(np.abs(arr)))
            if peak > self.max_abs_value:
                duration_us = (time.perf_counter_ns() - t0) / 1000.0
                return CheckpointVerdict(
                    passed=False,
                    checkpoint_name=self.name,
                    prune_reason="numerical_magnitude_explosion",
                    diagnostics={
                        "tensor": qualified_key,
                        "shape": list(arr.shape),
                        "peak_abs": peak,
                        "limit": self.max_abs_value,
                    },
                    fitness_penalty=self.fitness_penalty,
                    duration_us=duration_us,
                )

        duration_us = (time.perf_counter_ns() - t0) / 1000.0
        return CheckpointVerdict(
            passed=True,
            checkpoint_name=self.name,
            diagnostics={"scanned_tensors": [name for name, _ in arrays]},
            duration_us=duration_us,
        )


# ── Composition: QualityCheckpointNode ───────────────────────────────────────


@dataclass
class QualityCheckpointNode:
    """Composes one or more :class:`MidGenerationCheckpoint` gates into a
    PDG-compatible node operation.

    Usage inside a PDG graph::

        node = PDGNode(
            name="quality_gate",
            operation=QualityCheckpointNode([SkeletonProportionGate.from_style_bounds(),
                                             NumericalToxinGate()]),
            dependencies=["genotype_decode"],
            requires_gpu=False,
        )

    Semantics:

    * On *every gate passing* it returns a JSON payload ``{"verdict":
      "pass", "gate_results": [...]}`` that downstream nodes can inspect.
    * On the *first failing* gate it immediately raises
      :class:`EarlyRejectionError`, which the PDG v2 scheduler traps and
      converts into ``COOKED_CANCEL`` + downstream ``SKIPPED`` propagation.
    * It NEVER catches generic exceptions from the gates. Only the gate's
      own ``CheckpointVerdict`` decides rejection vs pass.
    """

    gates: list[MidGenerationCheckpoint] = field(default_factory=list)
    node_name: str = "mid_generation_quality_gate"

    def __call__(
        self,
        context: Mapping[str, Any],
        deps: Mapping[str, Any],
    ) -> dict[str, Any]:
        gate_results: list[dict[str, Any]] = []
        total_us = 0.0
        for gate in self.gates:
            verdict = gate.evaluate(context, deps)
            gate_results.append(verdict.to_dict())
            total_us += verdict.duration_us
            if not verdict.passed:
                # Hand the scheduler a typed exception with full diagnostic
                # payload. This is the ONLY place we translate a CheckpointVerdict
                # into control flow; gates themselves never raise.
                raise EarlyRejectionError(
                    prune_reason=verdict.prune_reason or "checkpoint_failed",
                    source_node=self.node_name,
                    diagnostics={
                        "failing_gate": verdict.checkpoint_name,
                        "gate_diagnostics": dict(verdict.diagnostics),
                        "cumulative_duration_us": total_us,
                        "prior_gate_results": gate_results[:-1],
                    },
                    fitness_penalty=verdict.fitness_penalty,
                )
        return {
            "verdict": "pass",
            "gate_results": gate_results,
            "total_duration_us": total_us,
            "gate_count": len(gate_results),
        }


__all__ = [
    "CheckpointVerdict",
    "MidGenerationCheckpoint",
    "SkeletonProportionGate",
    "NumericalToxinGate",
    "QualityCheckpointNode",
    "DEFAULT_SKELETON_PROPORTION_BOUNDS",
    "DEFAULT_PROPORTION_RATIO_GUARDS",
]
