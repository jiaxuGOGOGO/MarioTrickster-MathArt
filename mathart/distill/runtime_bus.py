"""SESSION-050 — Runtime Distillation Bus for data-oriented rule execution.

This module closes Gap A2 by turning the repository's existing knowledge
pipeline — ``KnowledgeParser`` → ``KnowledgeRule`` → ``RuleCompiler`` →
``ParameterSpace`` — into a runtime-consumable service.

The core idea is deliberately data-oriented:

1. Keep human-authored / LLM-distilled knowledge in flexible JSON/Markdown form.
2. Compile it into ``ParameterSpace`` objects as the semantic source of truth.
3. Lower those constraints into dense numeric vectors and generated evaluators.
4. JIT-compile hot-path rule checks with Numba when available.

This lets high-frequency loops consume compact arrays and compiled closures
instead of repeatedly walking Python dictionaries and string keys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
import re
import time

import numpy as np

from .compiler import ParameterSpace, RuleCompiler
from .parser import KnowledgeParser

try:  # pragma: no cover - optional dependency path
    from numba import njit
    NUMBA_AVAILABLE = True
except Exception:  # pragma: no cover - fallback when numba is unavailable
    njit = None
    NUMBA_AVAILABLE = False


_RUNTIME_PARAM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "physics.contact.height_threshold": (
        "contact_height",
        "foot_contact_height",
        "contact_threshold",
        "foot_height_threshold",
    ),
    "physics.contact.velocity_threshold": (
        "contact_velocity",
        "foot_contact_velocity",
        "velocity_threshold",
        "foot_velocity_threshold",
    ),
    "physics.foot_lock.min_blend_weight": (
        "foot_lock_blend",
        "foot_lock_min_blend",
        "min_blend_weight",
    ),
    "physics.constraint.blend_in_frames": (
        "blend_in_frames",
        "constraint_blend_in",
    ),
    "physics.constraint.blend_out_frames": (
        "blend_out_frames",
        "constraint_blend_out",
    ),
    # SESSION-072 (P1-DISTILL-1A): 3D XPBD compliance knobs exposed to
    # the global JIT and Layer 3 (Optuna) tuning closed loop.
    "physics3d.compliance_distance": (
        "compliance_distance",
        "xpbd_compliance_distance",
        "distance_compliance",
    ),
    "physics3d.compliance_bending": (
        "compliance_bending",
        "xpbd_compliance_bending",
        "bending_compliance",
    ),
}


def _safe_identifier(name: str) -> str:
    ident = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not ident:
        ident = "runtime_kernel"
    if ident[0].isdigit():
        ident = f"k_{ident}"
    return ident


@dataclass(frozen=True)
class RuntimeConstraintEvaluation:
    """Result of evaluating runtime constraints."""

    accepted: bool
    score: float
    penalty: float
    hard_violations: int = 0
    soft_violations: int = 0
    satisfied_mask: int = 0
    clamped_values: Optional[np.ndarray] = None


@dataclass(frozen=True)
class RuntimeRuleClause:
    """One low-level rule clause compiled into a hot-path evaluator."""

    feature: str
    op: str
    threshold: float
    weight: float = 1.0
    threshold_hi: Optional[float] = None
    tag: str = ""


@dataclass
class CompiledParameterSpace:
    """Dense, runtime-friendly view of a ``ParameterSpace``."""

    module_name: str
    space: ParameterSpace
    backend: str = "python"
    param_names: list[str] = field(default_factory=list)
    defaults: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    min_values: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    max_values: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    has_min: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))
    has_max: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))
    hard_mask: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))

    def __post_init__(self) -> None:
        if self.param_names:
            return
        self.param_names = list(self.space.constraints.keys())
        n = len(self.param_names)
        defaults = np.zeros(n, dtype=np.float64)
        min_values = np.zeros(n, dtype=np.float64)
        max_values = np.zeros(n, dtype=np.float64)
        has_min = np.zeros(n, dtype=np.int8)
        has_max = np.zeros(n, dtype=np.int8)
        hard_mask = np.zeros(n, dtype=np.int8)

        for idx, name in enumerate(self.param_names):
            constraint = self.space.constraints[name]
            if constraint.default_value is not None:
                defaults[idx] = float(constraint.default_value)
            elif constraint.min_value is not None and constraint.max_value is not None:
                defaults[idx] = 0.5 * (float(constraint.min_value) + float(constraint.max_value))
            elif constraint.min_value is not None:
                defaults[idx] = float(constraint.min_value)
            elif constraint.max_value is not None:
                defaults[idx] = float(constraint.max_value)
            else:
                defaults[idx] = 0.0

            if constraint.min_value is not None:
                min_values[idx] = float(constraint.min_value)
                has_min[idx] = 1
            if constraint.max_value is not None:
                max_values[idx] = float(constraint.max_value)
                has_max[idx] = 1
            hard_mask[idx] = 1 if constraint.is_hard else 0

        self.defaults = defaults
        self.min_values = min_values
        self.max_values = max_values
        self.has_min = has_min
        self.has_max = has_max
        self.hard_mask = hard_mask
        self.backend = self.backend if self.backend in ("python", "numba") else "python"
        self._full_index = {name: idx for idx, name in enumerate(self.param_names)}
        self._leaf_index: dict[str, int] = {}
        leaf_counts: dict[str, int] = {}
        for idx, name in enumerate(self.param_names):
            leaf = name.split(".")[-1]
            leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1
            for alias in _RUNTIME_PARAM_SYNONYMS.get(name, ()):  # explicit aliases first
                self._leaf_index.setdefault(alias, idx)
        for idx, name in enumerate(self.param_names):
            leaf = name.split(".")[-1]
            if leaf_counts.get(leaf, 0) == 1:
                self._leaf_index.setdefault(leaf, idx)
        self._python_eval = self._build_python_eval()
        self._runtime_eval = self._python_eval
        if self.backend == "numba" and NUMBA_AVAILABLE:
            self._runtime_eval = self._build_numba_eval()

    @property
    def dimensions(self) -> int:
        return len(self.param_names)

    def _build_python_eval(self):
        defaults = self.defaults.copy()
        min_values = self.min_values.copy()
        max_values = self.max_values.copy()
        has_min = self.has_min.copy()
        has_max = self.has_max.copy()
        hard_mask = self.hard_mask.copy()

        def _eval(values: np.ndarray):
            penalty = 0.0
            hard_violations = 0
            soft_violations = 0
            clamped = np.empty_like(values)
            for i in range(values.shape[0]):
                v = float(values[i])
                if np.isnan(v):
                    v = float(defaults[i])
                    penalty += 1.0
                    if hard_mask[i]:
                        hard_violations += 1
                    else:
                        soft_violations += 1
                if has_min[i] and v < min_values[i]:
                    penalty += float(min_values[i] - v)
                    v = float(min_values[i])
                    if hard_mask[i]:
                        hard_violations += 1
                    else:
                        soft_violations += 1
                if has_max[i] and v > max_values[i]:
                    penalty += float(v - max_values[i])
                    v = float(max_values[i])
                    if hard_mask[i]:
                        hard_violations += 1
                    else:
                        soft_violations += 1
                clamped[i] = v
            return penalty, hard_violations, soft_violations, clamped

        return _eval

    def _build_numba_eval(self):
        if not NUMBA_AVAILABLE:  # pragma: no cover
            return self._python_eval
        kernel_name = _safe_identifier(f"{self.module_name}_space_eval")
        body = [
            f"def {kernel_name}(values):",
            "    penalty = 0.0",
            "    hard_violations = 0",
            "    soft_violations = 0",
            f"    clamped = np.empty({self.dimensions}, dtype=np.float64)",
        ]
        for idx in range(self.dimensions):
            body.extend([
                f"    v_{idx} = float(values[{idx}])",
                f"    if np.isnan(v_{idx}):",
                f"        v_{idx} = {float(self.defaults[idx]):.17g}",
                "        penalty += 1.0",
                f"        {'hard_violations += 1' if self.hard_mask[idx] else 'soft_violations += 1'}",
            ])
            if self.has_min[idx]:
                body.extend([
                    f"    if v_{idx} < {float(self.min_values[idx]):.17g}:",
                    f"        penalty += ({float(self.min_values[idx]):.17g} - v_{idx})",
                    f"        v_{idx} = {float(self.min_values[idx]):.17g}",
                    f"        {'hard_violations += 1' if self.hard_mask[idx] else 'soft_violations += 1'}",
                ])
            if self.has_max[idx]:
                body.extend([
                    f"    if v_{idx} > {float(self.max_values[idx]):.17g}:",
                    f"        penalty += (v_{idx} - {float(self.max_values[idx]):.17g})",
                    f"        v_{idx} = {float(self.max_values[idx]):.17g}",
                    f"        {'hard_violations += 1' if self.hard_mask[idx] else 'soft_violations += 1'}",
                ])
            body.append(f"    clamped[{idx}] = v_{idx}")
        body.append("    return penalty, hard_violations, soft_violations, clamped")
        namespace: dict[str, Any] = {"np": np}
        exec("\n".join(body), namespace)
        return njit(cache=False)(namespace[kernel_name])

    def make_vector(
        self,
        params: Mapping[str, float],
        *,
        inject_defaults: bool = True,
        use_aliases: bool = True,
    ) -> np.ndarray:
        values = self.defaults.copy() if inject_defaults else np.full(self.dimensions, np.nan, dtype=np.float64)
        for key, value in params.items():
            idx = self._full_index.get(key)
            if idx is None and use_aliases:
                idx = self._leaf_index.get(key)
            if idx is not None:
                values[idx] = float(value)
        return values

    def evaluate_vector(self, values: np.ndarray) -> RuntimeConstraintEvaluation:
        penalty, hard_violations, soft_violations, clamped = self._runtime_eval(values)
        total = max(self.dimensions, 1)
        score = max(0.0, 1.0 - float(penalty) / total)
        accepted = bool(hard_violations == 0)
        return RuntimeConstraintEvaluation(
            accepted=accepted,
            score=score,
            penalty=float(penalty),
            hard_violations=int(hard_violations),
            soft_violations=int(soft_violations),
            clamped_values=clamped,
        )

    def evaluate_params(
        self,
        params: Mapping[str, float],
        *,
        inject_defaults: bool = True,
        use_aliases: bool = True,
    ) -> RuntimeConstraintEvaluation:
        values = self.make_vector(params, inject_defaults=inject_defaults, use_aliases=use_aliases)
        return self.evaluate_vector(values)

    def clamp_params(
        self,
        params: Mapping[str, float],
        *,
        use_aliases: bool = True,
    ) -> dict[str, float]:
        result = dict(params)
        evaluation = self.evaluate_params(result, inject_defaults=False, use_aliases=use_aliases)
        if evaluation.clamped_values is None:
            return result
        for key in list(result.keys()):
            idx = self._full_index.get(key)
            if idx is None and use_aliases:
                idx = self._leaf_index.get(key)
            if idx is not None:
                result[key] = float(evaluation.clamped_values[idx])
        return result

    def defaults_as_dict(self, *, leaf_aliases: bool = False) -> dict[str, float]:
        if leaf_aliases:
            return {name.split(".")[-1]: float(self.defaults[i]) for i, name in enumerate(self.param_names)}
        return {name: float(self.defaults[i]) for i, name in enumerate(self.param_names)}

    def resolve_scalar(self, names: Sequence[str], default: float) -> float:
        for name in names:
            idx = self._full_index.get(name)
            if idx is None:
                idx = self._leaf_index.get(name)
            if idx is not None:
                return float(self.defaults[idx])
        return float(default)


@dataclass
class RuntimeRuleProgram:
    """Generated rule program that evaluates dense feature vectors."""

    name: str
    feature_names: list[str]
    clauses: list[RuntimeRuleClause]
    min_score: float = 1.0
    backend: str = "python"

    def __post_init__(self) -> None:
        self._feature_index = {name: idx for idx, name in enumerate(self.feature_names)}
        self._python_eval = self._build_python_eval()
        self._runtime_eval = self._python_eval
        if self.backend == "numba" and NUMBA_AVAILABLE:
            self._runtime_eval = self._build_numba_eval()

    def _clause_check(self, value: float, clause: RuntimeRuleClause) -> tuple[bool, float]:
        op = clause.op
        thr = float(clause.threshold)
        if op == "le":
            ok = value <= thr
            penalty = max(0.0, value - thr)
        elif op == "lt":
            ok = value < thr
            penalty = max(0.0, value - thr)
        elif op == "ge":
            ok = value >= thr
            penalty = max(0.0, thr - value)
        elif op == "gt":
            ok = value > thr
            penalty = max(0.0, thr - value)
        elif op == "abs_le":
            av = abs(value)
            ok = av <= thr
            penalty = max(0.0, av - thr)
        elif op == "abs_ge":
            av = abs(value)
            ok = av >= thr
            penalty = max(0.0, thr - av)
        elif op == "between":
            hi = float(clause.threshold_hi if clause.threshold_hi is not None else clause.threshold)
            lo = min(thr, hi)
            hi = max(thr, hi)
            ok = lo <= value <= hi
            penalty = max(0.0, lo - value, value - hi)
        else:
            raise ValueError(f"Unsupported runtime rule op: {op}")
        return ok, penalty

    def _build_python_eval(self):
        clauses = list(self.clauses)
        feature_index = dict(self._feature_index)
        min_score = float(self.min_score)

        def _eval(values: np.ndarray):
            total = 0.0
            score = 0.0
            penalty = 0.0
            mask = 0
            for bit, clause in enumerate(clauses):
                idx = feature_index[clause.feature]
                value = float(values[idx])
                ok, residual = self._clause_check(value, clause)
                total += float(clause.weight)
                if ok:
                    score += float(clause.weight)
                    mask |= (1 << bit)
                else:
                    penalty += float(clause.weight) * residual
            satisfied_ratio = score / total if total > 1e-8 else 1.0
            accepted = 1 if satisfied_ratio >= min_score else 0
            return accepted, satisfied_ratio, penalty, mask

        return _eval

    def _build_numba_eval(self):
        if not NUMBA_AVAILABLE:  # pragma: no cover
            return self._python_eval
        kernel_name = _safe_identifier(f"{self.name}_program")
        body = [
            f"def {kernel_name}(values):",
            "    total = 0.0",
            "    score = 0.0",
            "    penalty = 0.0",
            "    mask = 0",
        ]
        for bit, clause in enumerate(self.clauses):
            idx = self._feature_index[clause.feature]
            body.append(f"    total += {float(clause.weight):.17g}")
            body.append(f"    feature_{bit} = float(values[{idx}])")
            if clause.op == "le":
                condition = f"feature_{bit} <= {float(clause.threshold):.17g}"
                residual = f"max(0.0, feature_{bit} - {float(clause.threshold):.17g})"
            elif clause.op == "lt":
                condition = f"feature_{bit} < {float(clause.threshold):.17g}"
                residual = f"max(0.0, feature_{bit} - {float(clause.threshold):.17g})"
            elif clause.op == "ge":
                condition = f"feature_{bit} >= {float(clause.threshold):.17g}"
                residual = f"max(0.0, {float(clause.threshold):.17g} - feature_{bit})"
            elif clause.op == "gt":
                condition = f"feature_{bit} > {float(clause.threshold):.17g}"
                residual = f"max(0.0, {float(clause.threshold):.17g} - feature_{bit})"
            elif clause.op == "abs_le":
                body.append(f"    abs_feature_{bit} = abs(feature_{bit})")
                condition = f"abs_feature_{bit} <= {float(clause.threshold):.17g}"
                residual = f"max(0.0, abs_feature_{bit} - {float(clause.threshold):.17g})"
            elif clause.op == "abs_ge":
                body.append(f"    abs_feature_{bit} = abs(feature_{bit})")
                condition = f"abs_feature_{bit} >= {float(clause.threshold):.17g}"
                residual = f"max(0.0, {float(clause.threshold):.17g} - abs_feature_{bit})"
            elif clause.op == "between":
                lo = min(float(clause.threshold), float(clause.threshold_hi if clause.threshold_hi is not None else clause.threshold))
                hi = max(float(clause.threshold), float(clause.threshold_hi if clause.threshold_hi is not None else clause.threshold))
                condition = f"(feature_{bit} >= {lo:.17g}) and (feature_{bit} <= {hi:.17g})"
                residual = f"max(0.0, {lo:.17g} - feature_{bit}, feature_{bit} - {hi:.17g})"
            else:
                raise ValueError(f"Unsupported runtime rule op: {clause.op}")
            body.extend([
                f"    if {condition}:",
                f"        score += {float(clause.weight):.17g}",
                f"        mask |= {1 << bit}",
                "    else:",
                f"        penalty += {float(clause.weight):.17g} * ({residual})",
            ])
        body.extend([
            "    satisfied_ratio = score / total if total > 1e-8 else 1.0",
            f"    accepted = 1 if satisfied_ratio >= {float(self.min_score):.17g} else 0",
            "    return accepted, satisfied_ratio, penalty, mask",
        ])
        namespace: dict[str, Any] = {"np": np, "max": max, "abs": abs}
        exec("\n".join(body), namespace)
        return njit(cache=False)(namespace[kernel_name])

    def make_vector(self, features: Mapping[str, float]) -> np.ndarray:
        values = np.zeros(len(self.feature_names), dtype=np.float64)
        for name, idx in self._feature_index.items():
            values[idx] = float(features.get(name, 0.0))
        return values

    def evaluate_array(self, values: np.ndarray) -> RuntimeConstraintEvaluation:
        accepted, score, penalty, mask = self._runtime_eval(values)
        return RuntimeConstraintEvaluation(
            accepted=bool(accepted),
            score=float(score),
            penalty=float(penalty),
            satisfied_mask=int(mask),
        )

    def evaluate(self, features: Mapping[str, float]) -> RuntimeConstraintEvaluation:
        return self.evaluate_array(self.make_vector(features))

    def make_matrix(self, feature_rows: Sequence[Mapping[str, float]]) -> np.ndarray:
        values = np.zeros((len(feature_rows), len(self.feature_names)), dtype=np.float64)
        for row_idx, features in enumerate(feature_rows):
            for name, idx in self._feature_index.items():
                values[row_idx, idx] = float(features.get(name, 0.0))
        return values

    def evaluate_feature_rows(self, feature_rows: Sequence[Mapping[str, float]]) -> dict[str, Any]:
        if not feature_rows:
            return {
                "accepted_ratio": 0.0,
                "mean_score": 0.0,
                "mean_penalty": 0.0,
                "rows": [],
            }
        matrix = self.make_matrix(feature_rows)
        rows: list[dict[str, Any]] = []
        accepted = 0
        score_total = 0.0
        penalty_total = 0.0
        for values in matrix:
            evaluation = self.evaluate_array(values)
            rows.append({
                "accepted": bool(evaluation.accepted),
                "score": float(evaluation.score),
                "penalty": float(evaluation.penalty),
                "mask": int(evaluation.satisfied_mask),
            })
            accepted += int(evaluation.accepted)
            score_total += float(evaluation.score)
            penalty_total += float(evaluation.penalty)
        count = len(rows)
        return {
            "accepted_ratio": float(accepted / max(count, 1)),
            "mean_score": float(score_total / max(count, 1)),
            "mean_penalty": float(penalty_total / max(count, 1)),
            "rows": rows,
        }

    def benchmark(self, sample_count: int = 2000) -> dict[str, float]:
        rng = np.random.default_rng(7)
        samples = rng.normal(0.0, 0.25, size=(sample_count, len(self.feature_names))).astype(np.float64)
        self.evaluate_array(samples[0])  # warm up JIT if enabled
        start = time.perf_counter()
        accepted = 0
        for row in samples:
            accepted += int(self.evaluate_array(row).accepted)
        elapsed = time.perf_counter() - start
        return {
            "sample_count": float(sample_count),
            "accepted": float(accepted),
            "elapsed_s": float(elapsed),
            "throughput_per_s": float(sample_count / max(elapsed, 1e-9)),
        }


class RuntimeDistillationBus:
    """Global runtime bus that compiles repository knowledge into hot-path evaluators."""

    def __init__(
        self,
        project_root: Optional[str | Path] = None,
        *,
        backend_preference: Sequence[str] = ("numba", "python"),
        verbose: bool = False,
    ) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.knowledge_dir = self.project_root / "knowledge"
        self.backend_preference = tuple(backend_preference)
        self.verbose = verbose
        self.parser = KnowledgeParser()
        self.compiler = RuleCompiler()
        self.spaces: dict[str, ParameterSpace] = {}
        self.compiled_spaces: dict[str, CompiledParameterSpace] = {}
        self.runtime_programs: dict[str, RuntimeRuleProgram] = {}
        self.last_refresh_summary: dict[str, Any] = {}

    def _pick_backend(self) -> str:
        for backend in self.backend_preference:
            if backend == "numba" and NUMBA_AVAILABLE:
                return "numba"
            if backend == "python":
                return "python"
        return "python"

    def refresh_from_knowledge(self) -> dict[str, Any]:
        if not self.knowledge_dir.exists():
            self.last_refresh_summary = {
                "knowledge_dir": str(self.knowledge_dir),
                "knowledge_files": 0,
                "module_count": 0,
                "constraint_count": 0,
                "backend": self._pick_backend(),
            }
            return dict(self.last_refresh_summary)
        rules = self.parser.parse_directory(self.knowledge_dir)
        spaces = self.compiler.compile_by_module(rules)
        self.register_spaces(spaces)
        summary = {
            "knowledge_dir": str(self.knowledge_dir),
            "knowledge_files": len(list(self.knowledge_dir.glob("*.md"))) + len(list(self.knowledge_dir.glob("*.json"))),
            "module_count": len(self.compiled_spaces),
            "constraint_count": int(sum(space.dimensions for space in self.compiled_spaces.values())),
            "backend": self._pick_backend(),
        }
        self.last_refresh_summary = summary
        return dict(summary)

    def register_space(self, module_name: str, space: ParameterSpace) -> CompiledParameterSpace:
        self.spaces[module_name] = space
        compiled = CompiledParameterSpace(module_name=module_name, space=space, backend=self._pick_backend())
        self.compiled_spaces[module_name] = compiled
        return compiled

    def register_spaces(self, spaces: Mapping[str, ParameterSpace]) -> None:
        self.spaces.clear()
        self.compiled_spaces.clear()
        for module_name, space in spaces.items():
            self.register_space(module_name, space)

    def get_space(self, module_name: str) -> Optional[ParameterSpace]:
        return self.spaces.get(module_name)

    def get_compiled_space(self, module_name: str) -> Optional[CompiledParameterSpace]:
        return self.compiled_spaces.get(module_name)

    def defaults_for(self, module_name: str, *, leaf_aliases: bool = False) -> dict[str, float]:
        compiled = self.get_compiled_space(module_name)
        if compiled is None:
            return {}
        return compiled.defaults_as_dict(leaf_aliases=leaf_aliases)

    def apply_module_constraints(
        self,
        module_name: str,
        params: Mapping[str, float],
        *,
        use_aliases: bool = True,
    ) -> dict[str, float]:
        compiled = self.get_compiled_space(module_name)
        if compiled is None:
            return dict(params)
        return compiled.clamp_params(params, use_aliases=use_aliases)

    def apply_global_constraints(
        self,
        params: Mapping[str, float],
        *,
        use_aliases: bool = True,
    ) -> dict[str, float]:
        result = dict(params)
        for compiled in self.compiled_spaces.values():
            result = compiled.clamp_params(result, use_aliases=use_aliases)
        return result

    def resolve_scalar(self, names: Sequence[str], default: float) -> float:
        for compiled in self.compiled_spaces.values():
            value = compiled.resolve_scalar(names, default=np.nan)
            if not np.isnan(value):
                return float(value)
        return float(default)

    def build_rule_program(
        self,
        name: str,
        feature_names: Sequence[str],
        clauses: Sequence[RuntimeRuleClause],
        *,
        min_score: float = 1.0,
    ) -> RuntimeRuleProgram:
        program = RuntimeRuleProgram(
            name=name,
            feature_names=list(feature_names),
            clauses=list(clauses),
            min_score=float(min_score),
            backend=self._pick_backend(),
        )
        self.runtime_programs[name] = program
        return program

    def build_foot_contact_program(
        self,
        *,
        contact_threshold: Optional[float] = None,
        velocity_threshold: Optional[float] = None,
    ) -> RuntimeRuleProgram:
        height = float(contact_threshold) if contact_threshold is not None else self.resolve_scalar(
            [
                "physics.contact.height_threshold",
                "foot_contact_height",
                "contact_height",
                "contact_threshold",
            ],
            0.05,
        )
        velocity = float(velocity_threshold) if velocity_threshold is not None else self.resolve_scalar(
            [
                "physics.contact.velocity_threshold",
                "foot_contact_velocity",
                "contact_velocity",
                "velocity_threshold",
            ],
            0.15,
        )
        clauses = [
            RuntimeRuleClause(feature="foot_height", op="le", threshold=height, weight=1.0, tag="height_gate"),
            RuntimeRuleClause(feature="foot_vertical_velocity", op="abs_le", threshold=velocity, weight=1.0, tag="velocity_gate"),
        ]
        return self.build_rule_program(
            name="foot_contact_runtime",
            feature_names=["foot_height", "foot_vertical_velocity"],
            clauses=clauses,
            min_score=1.0,
        )

    def build_gait_transition_program(
        self,
        *,
        phase_jump_threshold: Optional[float] = None,
        sliding_threshold: Optional[float] = None,
        contact_mismatch_threshold: Optional[float] = None,
        foot_lock_threshold: Optional[float] = None,
        transition_cost_threshold: Optional[float] = None,
    ) -> RuntimeRuleProgram:
        phase_jump = float(phase_jump_threshold) if phase_jump_threshold is not None else self.resolve_scalar(
            [
                "locomotion.phase_jump_threshold",
                "gait.phase_jump_threshold",
                "phase_jump_threshold",
            ],
            0.08,
        )
        sliding = float(sliding_threshold) if sliding_threshold is not None else self.resolve_scalar(
            [
                "locomotion.sliding_threshold",
                "gait.sliding_threshold",
                "sliding_threshold",
            ],
            0.08,
        )
        contact_mismatch = float(contact_mismatch_threshold) if contact_mismatch_threshold is not None else self.resolve_scalar(
            [
                "locomotion.contact_mismatch_threshold",
                "gait.contact_mismatch_threshold",
                "contact_mismatch_threshold",
            ],
            0.25,
        )
        foot_lock = float(foot_lock_threshold) if foot_lock_threshold is not None else self.resolve_scalar(
            [
                "locomotion.foot_lock_threshold",
                "gait.foot_lock_threshold",
                "foot_lock_threshold",
            ],
            0.80,
        )
        transition_cost = float(transition_cost_threshold) if transition_cost_threshold is not None else self.resolve_scalar(
            [
                "locomotion.transition_cost_threshold",
                "gait.transition_cost_threshold",
                "transition_cost_threshold",
            ],
            0.75,
        )
        clauses = [
            RuntimeRuleClause(feature="phase_jump", op="le", threshold=phase_jump, weight=1.0, tag="phase_gate"),
            RuntimeRuleClause(feature="sliding_error", op="le", threshold=sliding, weight=1.0, tag="sliding_gate"),
            RuntimeRuleClause(feature="contact_mismatch", op="le", threshold=contact_mismatch, weight=1.0, tag="contact_gate"),
            RuntimeRuleClause(feature="foot_lock", op="ge", threshold=foot_lock, weight=1.0, tag="foot_lock_gate"),
            RuntimeRuleClause(feature="transition_cost", op="le", threshold=transition_cost, weight=0.5, tag="transition_cost_gate"),
        ]
        return self.build_rule_program(
            name="gait_transition_runtime",
            feature_names=["phase_jump", "sliding_error", "contact_mismatch", "foot_lock", "transition_cost"],
            clauses=clauses,
            min_score=0.75,
        )

    def summary(self) -> dict[str, Any]:
        if not self.last_refresh_summary:
            self.refresh_from_knowledge()
        return {
            **self.last_refresh_summary,
            "program_count": len(self.runtime_programs),
        }


def load_runtime_distillation_bus(
    project_root: Optional[str | Path] = None,
    *,
    backend_preference: Sequence[str] = ("numba", "python"),
    verbose: bool = False,
) -> RuntimeDistillationBus:
    bus = RuntimeDistillationBus(
        project_root=project_root,
        backend_preference=backend_preference,
        verbose=verbose,
    )
    bus.refresh_from_knowledge()
    return bus


__all__ = [
    "NUMBA_AVAILABLE",
    "RuntimeConstraintEvaluation",
    "RuntimeRuleClause",
    "CompiledParameterSpace",
    "RuntimeRuleProgram",
    "RuntimeDistillationBus",
    "load_runtime_distillation_bus",
]
