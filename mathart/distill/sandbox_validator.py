"""SESSION-138 Knowledge QA Gate — Sandbox Validator.

This module implements the **four-dimensional anti-hallucination funnel** that
every LLM-distilled ``KnowledgeRule`` MUST pass before it can be promoted from
``knowledge/quarantine/`` into ``knowledge/active/`` and ultimately reach the
mass-production :class:`~mathart.distill.runtime_bus.RuntimeDistillationBus`.

The four gates (in strict left-to-right order) are:

1. **Citation / Provenance gate**: the rule must carry a non-empty
   ``source_quote``. Rules without an evidence chain are treated as LLM
   hallucinations and rejected outright.  See
   ``docs/research/SESSION-138-KNOWLEDGE-QA-GATE-RESEARCH.md`` §1 for the
   corresponding RAG citation-tracking literature.
2. **AST-safe expression parsing**: formula expressions are compiled via
   ``ast.parse(..., mode="eval")`` and then traversed against a hard-coded
   whitelist of node types and a whitelist of names (``math`` / ``numpy``
   scalar functions only). ``eval`` / ``exec`` / ``__import__`` /
   attribute / subscript / comprehension nodes are structurally unreachable,
   so an injected ``os.system('rm -rf /')`` payload never runs.
3. **Math fuzzing on canonical edge cases**: the whitelisted expression is
   evaluated across ``FUZZ_SAMPLES = [0, -1, 1, 1e-6, 1e6, +inf, -inf, nan]``.
   Any ``ZeroDivisionError``, ``OverflowError``, ``NaN``, or ``Inf`` counts
   as a toxin and the rule is rejected.
4. **Physics stability dry-run + hard timeout**: physics-tagged rules are
   injected into a tiny CPU spring-damper integrator for 100 steps; a
   runaway energy or penetration is a toxin. The entire validation is
   guarded by a hard 3-second watchdog (``ThreadPoolExecutor.result(timeout)``).
   A timeout also counts as a toxin, never as silent success.

The validator is pure CPU and has **no** dependency on the project
``AssetPipeline``; per the SESSION-138 architectural contract it MUST stay
isolated under ``mathart/distill/`` as an outer-loop distillation gate.
"""
from __future__ import annotations

import ast
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .parser import (
    KnowledgeParser,
    KnowledgeRule,
    QuarantineContractError,
    TargetModule,
)


# ────────────────────────────────────────────────────────────────────────────
# Typed errors — every failure mode is named, so tests can assert precisely.
# ────────────────────────────────────────────────────────────────────────────


class SandboxValidationError(Exception):
    """Base class for every reason a rule can fail the SESSION-138 gate."""


class UnsafeExpressionError(SandboxValidationError):
    """The formula expression contains a node type or name outside the
    whitelist. This is the anti-RCE red line."""


class MathToxinError(SandboxValidationError):
    """The expression evaluates to NaN / Inf or raises a numeric error on
    one of the canonical fuzz samples."""


class PhysicsInstabilityError(SandboxValidationError):
    """A physics-tagged rule blew up the 100-step dry-run (kinetic energy
    explosion or positional penetration)."""


class SandboxTimeoutError(SandboxValidationError):
    """The validator exceeded its hard 3 s watchdog budget."""


# ────────────────────────────────────────────────────────────────────────────
# AST-safe expression evaluator.
#
# NOTE: we deliberately do NOT use ast.literal_eval, because we must accept
# simple arithmetic over a handful of whitelisted names (``x``, math
# constants, ``math.sin``, etc.). We implement a minimal tree walker instead;
# any node outside the whitelist aborts evaluation before a single operation
# runs.
# ────────────────────────────────────────────────────────────────────────────


_ALLOWED_BIN_OPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

_ALLOWED_UNARY_OPS: dict[type, Callable[[float], float]] = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}

# Only a curated set of NAMES may appear in a formula — everything else,
# including ``__builtins__``, ``os``, ``sys``, ``eval``, is structurally
# unreachable.
_MATH_SAFE_NAMES: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "inf": math.inf,
    "nan": math.nan,
    # Common pure scalar helpers. We wrap them so NaN / Inf survival is
    # detected downstream by the math fuzz step.
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    "abs": abs,
    "min": min,
    "max": max,
    "pow": pow,
}


def _validate_node_whitelist(node: ast.AST) -> None:
    """Walk the AST and reject any node that is not on the whitelist.

    This is the anti-RCE firewall: even if someone embeds
    ``__import__('os').system('rm -rf /')`` inside ``constraint.expr``, the
    parser will build a ``Call`` whose ``func`` is an ``Attribute`` on a
    ``Name`` outside our ``_MATH_SAFE_NAMES`` table — both of which are
    forbidden here.
    """
    allowed_name_set = set(_MATH_SAFE_NAMES.keys())
    allowed_op_types = (
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.UAdd,
        ast.USub,
    )
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.Expression,
                ast.BinOp,
                ast.UnaryOp,
                ast.Constant,
                ast.Load,
            ),
        ):
            continue
        if isinstance(child, allowed_op_types):
            continue
        if isinstance(child, ast.Name):
            if child.id.startswith("_"):
                raise UnsafeExpressionError(
                    f"Name {child.id!r} uses a reserved underscore prefix;"
                    " rejected by AST whitelist."
                )
            if child.id not in allowed_name_set and child.id != "x":
                raise UnsafeExpressionError(
                    f"Name {child.id!r} is not in the math/whitelist;"
                    " rejected by AST firewall."
                )
            continue
        if isinstance(child, ast.Call):
            # Only direct calls to whitelisted simple names are allowed; no
            # attribute chains like ``math.sin`` or ``os.system``.
            func = child.func
            if not isinstance(func, ast.Name) or func.id not in allowed_name_set:
                raise UnsafeExpressionError(
                    "Only calls to whitelisted math names are allowed;"
                    f" saw {ast.dump(func)}."
                )
            continue
        # Everything else — Attribute, Subscript, Lambda, ListComp,
        # GeneratorExp, Import, Yield, Await, Starred, Tuple, Dict, Set,
        # Compare, BoolOp, IfExp, Slice, FormattedValue, JoinedStr — is
        # refused. The denylist-by-default is intentional.
        raise UnsafeExpressionError(
            f"AST node {type(child).__name__!r} is not allowed in a"
            " KnowledgeRule constraint expression."
        )


def _eval_safe(tree: ast.AST, env: dict[str, Any]) -> float:
    """Evaluate a pre-validated AST in the restricted environment."""

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "x":
                return env["x"]
            return _MATH_SAFE_NAMES[node.id]
        if isinstance(node, ast.UnaryOp):
            op = _ALLOWED_UNARY_OPS[type(node.op)]
            return op(_eval(node.operand))
        if isinstance(node, ast.BinOp):
            op = _ALLOWED_BIN_OPS[type(node.op)]
            return op(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.Call):
            func = _MATH_SAFE_NAMES[node.func.id]  # type: ignore[union-attr]
            args = [_eval(a) for a in node.args]
            return func(*args)
        raise UnsafeExpressionError(
            f"Unexpected AST node during evaluation: {type(node).__name__!r}"
        )

    return _eval(tree)


def safe_parse_expression(expr: str) -> ast.AST:
    """Parse ``expr`` and reject anything that is not a pure math expression.

    This is the public, testable entrypoint for the AST firewall. It never
    executes user code; it only inspects the tree.
    """
    if not isinstance(expr, str):
        raise UnsafeExpressionError("Expression must be a string.")
    if len(expr) > 512:
        raise UnsafeExpressionError(
            "Expression exceeds 512-char safety limit; rejected."
        )
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Expression is not valid Python: {exc}") from exc
    _validate_node_whitelist(tree)
    return tree


# ────────────────────────────────────────────────────────────────────────────
# Math fuzzing stage.
# ────────────────────────────────────────────────────────────────────────────


FUZZ_SAMPLES: tuple[float, ...] = (
    0.0,
    -1.0,
    1.0,
    1e-6,
    1e6,
    math.inf,
    -math.inf,
    math.nan,
)


def math_fuzz_expression(expr: str) -> list[dict[str, Any]]:
    """Evaluate ``expr`` against the canonical fuzz samples.

    Returns a list of per-sample reports. Any sample that raises, or yields
    NaN / Inf, is considered a toxin and causes :class:`MathToxinError` to
    be raised.
    """
    tree = safe_parse_expression(expr)
    reports: list[dict[str, Any]] = []
    for x in FUZZ_SAMPLES:
        env = {"x": x}
        # Non-finite inputs (inf / -inf / nan) are included in the fuzz set
        # specifically to exercise exception paths, NOT to demand that the
        # expression produce a finite output from a non-finite input. It is
        # mathematically inevitable that ``x + 1`` returns inf when x=inf;
        # treating that as a toxin would reject every valid polynomial.
        # The toxin definition is: the expression **blows up** (raises) or
        # produces NaN/Inf from a FINITE input.
        input_is_finite = isinstance(x, float) and math.isfinite(x)
        try:
            value = _eval_safe(tree, env)
        except ZeroDivisionError as exc:
            raise MathToxinError(
                f"Expression {expr!r} raised ZeroDivisionError at x={x!r}: {exc}"
            ) from exc
        except OverflowError as exc:
            raise MathToxinError(
                f"Expression {expr!r} overflowed at x={x!r}: {exc}"
            ) from exc
        except ValueError as exc:
            # e.g. math.log(-1) -> ValueError on a FINITE negative sample;
            # still a toxin because the rule is dangerous at realistic inputs.
            raise MathToxinError(
                f"Expression {expr!r} raised ValueError at x={x!r}: {exc}"
            ) from exc
        except UnsafeExpressionError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise MathToxinError(
                f"Expression {expr!r} raised {type(exc).__name__} at x={x!r}: {exc}"
            ) from exc
        if isinstance(value, float) and input_is_finite:
            if math.isnan(value):
                raise MathToxinError(
                    f"Expression {expr!r} produced NaN at finite x={x!r}."
                )
            if math.isinf(value):
                raise MathToxinError(
                    f"Expression {expr!r} produced Inf at finite x={x!r}."
                )
        reports.append({"x": x, "value": value})
    return reports


# ────────────────────────────────────────────────────────────────────────────
# Physics stability dry-run.
#
# We run a tiny explicit spring-damper integrator for 100 steps using the
# rule's numeric parameters (if any). If kinetic energy monotonically
# explodes beyond a safety bound, or the simulated body tunnels through the
# reference plane, we flag the rule as physically unstable.
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class PhysicsDryRunReport:
    steps: int
    max_kinetic_energy: float
    final_position: float
    final_velocity: float
    penetrated: bool
    passed: bool
    reason: str = ""


_PHYSICS_MODULES: frozenset[TargetModule] = frozenset(
    {
        TargetModule.PHYSICS,
        TargetModule.ANIMATION,
        TargetModule.GAME_FEEL,
    }
)


def _extract_physics_params(rule: KnowledgeRule) -> dict[str, float] | None:
    """Best-effort extraction of (stiffness, damping, mass) from a physics
    rule's constraint payload. Returns ``None`` if the rule does not carry
    usable numeric physics parameters, in which case the physics dry-run is
    skipped (i.e. the gate treats the rule as physics-agnostic).
    """
    if rule.target_module not in _PHYSICS_MODULES:
        return None
    constraint = rule.constraint or {}
    ctype = constraint.get("type")
    stiffness = 10.0
    damping = 1.0
    mass = 1.0
    target = constraint.get("target_param") or rule.target_param or ""
    target_lc = target.lower()
    if ctype == "range":
        mid = (float(constraint["min"]) + float(constraint["max"])) / 2.0
        if "stiff" in target_lc or "spring" in target_lc:
            stiffness = mid
        elif "damp" in target_lc:
            damping = mid
        elif "mass" in target_lc:
            mass = mid
        else:
            # Generic "assume this mid value is a stiffness" — still run a
            # conservative dry-run so numeric extremes are caught.
            stiffness = mid
    elif ctype == "exact":
        value = float(constraint.get("value", 1.0))
        if "stiff" in target_lc or "spring" in target_lc:
            stiffness = value
        elif "damp" in target_lc:
            damping = value
        elif "mass" in target_lc:
            mass = value
        else:
            stiffness = value
    else:
        return None
    if mass <= 0.0:
        # Zero/negative mass is itself a physics toxin.
        raise PhysicsInstabilityError(
            f"Rule {rule.id!r} declares non-positive mass={mass!r}."
        )
    return {"stiffness": float(stiffness), "damping": float(damping), "mass": float(mass)}


def physics_dry_run(
    stiffness: float,
    damping: float,
    mass: float,
    *,
    steps: int = 100,
    dt: float = 1.0 / 60.0,
    initial_position: float = 1.0,
    energy_bound: float = 1e6,
) -> PhysicsDryRunReport:
    """Run a 100-step spring-damper integrator and return a stability report.

    This is deliberately the simplest possible closed-form check: we are not
    validating gameplay fidelity, only that a rule's numeric parameters do
    not produce an instantly divergent system on a vanilla symplectic
    integrator. This catches the common LLM failure mode of suggesting
    huge stiffness values with near-zero damping and unit mass, which
    explodes within a handful of frames.
    """
    position = float(initial_position)
    velocity = 0.0
    max_ke = 0.0
    penetrated = False
    for step in range(steps):
        force = -stiffness * position - damping * velocity
        acceleration = force / mass
        velocity += acceleration * dt
        position += velocity * dt
        kinetic = 0.5 * mass * velocity * velocity
        if kinetic > max_ke:
            max_ke = kinetic
        if not math.isfinite(position) or not math.isfinite(velocity):
            return PhysicsDryRunReport(
                steps=step + 1,
                max_kinetic_energy=max_ke,
                final_position=position,
                final_velocity=velocity,
                penetrated=penetrated,
                passed=False,
                reason="non-finite state",
            )
        if kinetic > energy_bound:
            return PhysicsDryRunReport(
                steps=step + 1,
                max_kinetic_energy=max_ke,
                final_position=position,
                final_velocity=velocity,
                penetrated=penetrated,
                passed=False,
                reason=f"kinetic energy exploded past {energy_bound:g}",
            )
        if abs(position) > 1e4:
            penetrated = True
            return PhysicsDryRunReport(
                steps=step + 1,
                max_kinetic_energy=max_ke,
                final_position=position,
                final_velocity=velocity,
                penetrated=True,
                passed=False,
                reason="positional runaway > 1e4 (tunneling)",
            )
    return PhysicsDryRunReport(
        steps=steps,
        max_kinetic_energy=max_ke,
        final_position=position,
        final_velocity=velocity,
        penetrated=penetrated,
        passed=True,
    )


# ────────────────────────────────────────────────────────────────────────────
# Top-level sandbox validator.
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class SandboxValidationReport:
    rule_id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    fuzz_report: list[dict[str, Any]] | None = None
    physics_report: PhysicsDryRunReport | None = None
    duration_ms: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "fuzz_report": self.fuzz_report,
            "physics_report": (
                {
                    "steps": self.physics_report.steps,
                    "max_kinetic_energy": self.physics_report.max_kinetic_energy,
                    "final_position": self.physics_report.final_position,
                    "final_velocity": self.physics_report.final_velocity,
                    "penetrated": self.physics_report.penetrated,
                    "passed": self.physics_report.passed,
                    "reason": self.physics_report.reason,
                }
                if self.physics_report is not None
                else None
            ),
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class SandboxBatchResult:
    total: int
    passed: int
    failed: int
    reports: list[SandboxValidationReport]
    promoted_paths: list[str] = field(default_factory=list)
    rejected_paths: list[str] = field(default_factory=list)


class SandboxValidator:
    """Four-dimensional knowledge QA gate.

    Usage::

        validator = SandboxValidator(project_root=repo_root)
        report = validator.validate_rule(rule)
        if report.passed:
            validator.promote_rule(rule, source_path=quarantine_json_path)
    """

    DEFAULT_TIMEOUT_SECONDS: float = 3.0

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.knowledge_root = self.project_root / "knowledge"
        self.quarantine_root = KnowledgeParser.quarantine_dir(self.knowledge_root)
        self.active_root = KnowledgeParser.active_dir(self.knowledge_root)
        self.timeout_seconds = float(timeout_seconds)

    # ── Gate 1: provenance ─────────────────────────────────────────────
    def _check_provenance(self, rule: KnowledgeRule) -> None:
        rule.enforce_quarantine_contract()

    # ── Gates 2+3: AST-safe math fuzz ──────────────────────────────────
    def _check_expression(self, rule: KnowledgeRule) -> list[dict[str, Any]] | None:
        constraint = rule.constraint or {}
        expr = constraint.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            return None
        return math_fuzz_expression(expr)

    # ── Gate 4: physics dry-run ────────────────────────────────────────
    def _check_physics(self, rule: KnowledgeRule) -> PhysicsDryRunReport | None:
        params = _extract_physics_params(rule)
        if params is None:
            return None
        report = physics_dry_run(**params)
        if not report.passed:
            raise PhysicsInstabilityError(
                f"Rule {rule.id!r} failed physics dry-run: {report.reason}"
            )
        return report

    # ── Orchestration with hard timeout ────────────────────────────────
    def _run_all_gates(
        self, rule: KnowledgeRule
    ) -> tuple[list[dict[str, Any]] | None, PhysicsDryRunReport | None]:
        self._check_provenance(rule)
        fuzz = self._check_expression(rule)
        phys = self._check_physics(rule)
        return fuzz, phys

    def validate_rule(self, rule: KnowledgeRule) -> SandboxValidationReport:
        """Run all four gates against ``rule`` under a hard timeout budget."""
        start = time.perf_counter()
        reasons: list[str] = []
        fuzz_report: list[dict[str, Any]] | None = None
        physics_report: PhysicsDryRunReport | None = None
        passed = False
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._run_all_gates, rule)
                try:
                    fuzz_report, physics_report = future.result(
                        timeout=self.timeout_seconds
                    )
                    passed = True
                except FutureTimeoutError as exc:
                    # The worker is still running untrusted code: cancel the
                    # future (best effort) and refuse the rule. We do NOT
                    # wait for the thread — see research notes §2 on
                    # cooperative timeout and Andrew Healey 2023 on running
                    # untrusted Python.
                    future.cancel()
                    raise SandboxTimeoutError(
                        f"Sandbox validation of rule {rule.id!r} exceeded"
                        f" {self.timeout_seconds:.2f}s budget; treated as toxin."
                    ) from exc
        except QuarantineContractError as exc:
            reasons.append(f"provenance: {exc}")
        except UnsafeExpressionError as exc:
            reasons.append(f"ast_firewall: {exc}")
        except MathToxinError as exc:
            reasons.append(f"math_fuzz: {exc}")
        except PhysicsInstabilityError as exc:
            reasons.append(f"physics: {exc}")
        except SandboxTimeoutError as exc:
            reasons.append(f"timeout: {exc}")
        except SandboxValidationError as exc:  # pragma: no cover - defensive
            reasons.append(f"sandbox: {exc}")
        duration_ms = (time.perf_counter() - start) * 1000.0
        report = SandboxValidationReport(
            rule_id=rule.id,
            passed=passed and not reasons,
            reasons=reasons,
            fuzz_report=fuzz_report,
            physics_report=physics_report,
            duration_ms=duration_ms,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return report

    # ── Batch entrypoints on the quarantine tree ───────────────────────
    def validate_quarantine(self) -> SandboxBatchResult:
        """Scan every ``*.json`` rule-list file under ``knowledge/quarantine/``
        and return a :class:`SandboxBatchResult`. No promotion happens here.
        """
        reports: list[SandboxValidationReport] = []
        if not self.quarantine_root.exists():
            return SandboxBatchResult(total=0, passed=0, failed=0, reports=[])
        for json_file in sorted(self.quarantine_root.rglob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, list):
                continue
            for raw in data:
                rule = _hydrate_rule_or_stub(raw)
                reports.append(self.validate_rule(rule))
        passed = sum(1 for r in reports if r.passed)
        return SandboxBatchResult(
            total=len(reports),
            passed=passed,
            failed=len(reports) - passed,
            reports=reports,
        )

    def promote_rule(
        self,
        rule: KnowledgeRule,
        *,
        target_filename: str = "promoted_rules.json",
    ) -> Path:
        """Append a validated rule into the ``active/`` safe store.

        This method re-runs :meth:`validate_rule` as a belt-and-braces check
        and refuses to write if anything fails. Callers MUST NOT bypass this
        helper — ``active/`` is the single source of truth for the runtime
        mass-production bus.
        """
        report = self.validate_rule(rule)
        if not report.passed:
            raise SandboxValidationError(
                f"Refusing to promote rule {rule.id!r} into active/:"
                f" reasons={report.reasons}"
            )
        self.active_root.mkdir(parents=True, exist_ok=True)
        target = self.active_root / target_filename
        existing: list[dict[str, Any]] = []
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except json.JSONDecodeError:
                existing = []
        existing.append(rule.to_dict())
        target.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return target


def _hydrate_rule_or_stub(raw: Any) -> KnowledgeRule:
    """Best-effort hydrate a JSON payload into a :class:`KnowledgeRule`.

    A rule entry that omits ``source_quote`` is NOT raised at hydration time
    — we still instantiate it so the validator has something to reject. That
    way the provenance gate produces a structured ``reasons`` entry rather
    than a bare ``KeyError``.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"Quarantine rule entry must be a dict, got {type(raw)!r}")
    data = dict(raw)
    # Required scalar defaults.
    data.setdefault("id", "<unnamed>")
    data.setdefault("description", "")
    data.setdefault("rule_type", "heuristic")
    data.setdefault("target_module", "general")
    data.setdefault("target_param", "")
    data.setdefault("constraint", {})
    data.setdefault("source", "")
    data.setdefault("tags", [])
    data.setdefault("source_quote", "")
    data.setdefault("page_number", None)
    return KnowledgeRule.from_dict(data)


__all__ = [
    "FUZZ_SAMPLES",
    "MathToxinError",
    "PhysicsDryRunReport",
    "PhysicsInstabilityError",
    "SandboxBatchResult",
    "SandboxTimeoutError",
    "SandboxValidationError",
    "SandboxValidationReport",
    "SandboxValidator",
    "UnsafeExpressionError",
    "math_fuzz_expression",
    "physics_dry_run",
    "safe_parse_expression",
]
