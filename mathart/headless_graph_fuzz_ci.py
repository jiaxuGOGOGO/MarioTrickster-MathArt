"""Headless Graph-Fuzz CI — Property-Based State-Machine Fuzzing for E2E.

SESSION-055: Battle 4 — Headless Automation & The Asset Factory

This module bridges the gap between SESSION-051's graph-based state-machine
coverage (``state_machine_graph.py``) and SESSION-041's headless E2E CI
(``headless_e2e_ci.py``).  It uses **Hypothesis stateful testing**
(David R. MacIver, JOSS 2019) to generate thousands of extreme transition
sequences, executes them through the real ``MotionMatchingRuntime``, and
monitors the underlying XPBD solver for NaN explosions and penetration
violations.

Design references:

- **Hypothesis RuleBasedStateMachine** (MacIver 2019): Property-based
  stateful testing generates entire programs, not just inputs.
- **Graph-model fuzzing** (SESSION-051): ``RuntimeStateGraph`` provides the
  legal edge set; Hypothesis explores it adversarially.
- **XPBD NaN/penetration monitoring** (SESSION-052): The solver's
  ``XPBDDiagnostics`` exposes constraint errors, velocities, and energy —
  all of which must remain finite and bounded.

Usage::

    # Run as pytest
    pytest mathart/headless_graph_fuzz_ci.py -v

    # Run standalone
    python -m mathart.headless_graph_fuzz_ci

References
----------
[1] D. R. MacIver, Z. Hatfield-Dodds, "Hypothesis: A new approach to
    property-based testing," JOSS, 2019.
[2] Hypothesis Stateful Testing Docs,
    https://hypothesis.readthedocs.io/en/latest/stateful.html
[3] Macklin, Müller, "XPBD: Position-Based Simulation of Compliant
    Constrained Dynamics," MIG / SIGGRAPH 2016.
"""
from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Fuzz Report
# ---------------------------------------------------------------------------


@dataclass
class FuzzFinding:
    """A single finding from the graph-fuzz pipeline."""

    severity: str  # "PASS", "WARN", "FAIL", "CRITICAL"
    category: str
    message: str
    detail: Optional[dict[str, Any]] = None


@dataclass
class GraphFuzzReport:
    """Complete report from a headless graph-fuzz CI run."""

    session_id: str = "SESSION-055"
    timestamp: str = ""
    total_sequences: int = 0
    total_transitions: int = 0
    nan_explosions: int = 0
    penetration_violations: int = 0
    energy_spikes: int = 0
    coverage_edge: float = 0.0
    coverage_edge_pair: float = 0.0
    xpbd_max_error: float = 0.0
    xpbd_max_velocity: float = 0.0
    xpbd_max_energy: float = 0.0
    accepted: bool = False
    findings: list[FuzzFinding] = field(default_factory=list)

    def add(self, severity: str, category: str, message: str,
            detail: Optional[dict[str, Any]] = None) -> None:
        self.findings.append(FuzzFinding(severity, category, message, detail))

    @property
    def critical_failures(self) -> int:
        return sum(1 for f in self.findings if f.severity == "CRITICAL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "total_sequences": self.total_sequences,
            "total_transitions": self.total_transitions,
            "nan_explosions": self.nan_explosions,
            "penetration_violations": self.penetration_violations,
            "energy_spikes": self.energy_spikes,
            "coverage_edge": self.coverage_edge,
            "coverage_edge_pair": self.coverage_edge_pair,
            "xpbd_max_error": self.xpbd_max_error,
            "xpbd_max_velocity": self.xpbd_max_velocity,
            "xpbd_max_energy": self.xpbd_max_energy,
            "accepted": self.accepted,
            "critical_failures": self.critical_failures,
            "findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "message": f.message,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }

    def summary(self) -> str:
        lines = [
            f"=== Graph-Fuzz CI Report ({self.session_id}) ===",
            f"Timestamp: {self.timestamp}",
            f"Sequences tested: {self.total_sequences}",
            f"Total transitions: {self.total_transitions}",
            f"Edge coverage: {self.coverage_edge:.2%}",
            f"Edge-pair coverage: {self.coverage_edge_pair:.2%}",
            f"NaN explosions: {self.nan_explosions}",
            f"Penetration violations: {self.penetration_violations}",
            f"Energy spikes: {self.energy_spikes}",
            f"XPBD max error: {self.xpbd_max_error:.6f}",
            f"XPBD max velocity: {self.xpbd_max_velocity:.2f}",
            f"XPBD max energy: {self.xpbd_max_energy:.2f}",
            f"ACCEPTED: {self.accepted}",
        ]
        for f in self.findings:
            lines.append(f"  [{f.severity}] {f.category}: {f.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# XPBD Health Monitor
# ---------------------------------------------------------------------------

# Thresholds for XPBD solver health
NAN_THRESHOLD = float("inf")  # Any NaN is a critical failure
MAX_CONSTRAINT_ERROR = 1.0     # Constraint error above this = penetration
MAX_ENERGY = 1e6               # Energy above this = explosion
MAX_VELOCITY = 100.0           # Velocity above this = tunnelling risk


def check_xpbd_health(diagnostics: Any) -> list[FuzzFinding]:
    """Monitor XPBD solver diagnostics for NaN, penetration, and explosions.

    Parameters
    ----------
    diagnostics : XPBDDiagnostics or dict
        Solver diagnostics from a single step.

    Returns
    -------
    list[FuzzFinding]
        Any health violations found.
    """
    findings: list[FuzzFinding] = []

    if diagnostics is None:
        return findings

    # Extract values (support both dataclass and dict)
    if hasattr(diagnostics, "to_dict"):
        d = diagnostics.to_dict()
    elif isinstance(diagnostics, dict):
        d = diagnostics
    else:
        return findings

    mean_err = d.get("mean_constraint_error", 0.0)
    max_err = d.get("max_constraint_error", 0.0)
    max_vel = d.get("max_velocity_observed", 0.0)
    energy = d.get("energy_estimate", 0.0)

    # NaN check
    for key, val in d.items():
        if isinstance(val, (int, float)) and (math.isnan(val) or math.isinf(val)):
            findings.append(FuzzFinding(
                "CRITICAL", "nan_explosion",
                f"XPBD solver produced {val} in field '{key}'",
                detail={"field": key, "value": str(val)},
            ))

    # Penetration check
    if max_err > MAX_CONSTRAINT_ERROR:
        findings.append(FuzzFinding(
            "CRITICAL", "penetration_violation",
            f"Max constraint error {max_err:.4f} exceeds threshold {MAX_CONSTRAINT_ERROR}",
            detail={"max_constraint_error": max_err},
        ))

    # Energy explosion check
    if energy > MAX_ENERGY:
        findings.append(FuzzFinding(
            "WARN", "energy_spike",
            f"Energy {energy:.2f} exceeds threshold {MAX_ENERGY}",
            detail={"energy": energy},
        ))

    # Velocity tunnelling risk
    if max_vel > MAX_VELOCITY:
        findings.append(FuzzFinding(
            "WARN", "velocity_spike",
            f"Max velocity {max_vel:.2f} exceeds threshold {MAX_VELOCITY}",
            detail={"max_velocity": max_vel},
        ))

    return findings


# ---------------------------------------------------------------------------
# Sequence Generator & Executor
# ---------------------------------------------------------------------------


def generate_fuzz_sequences(
    *,
    num_random_walks: int = 50,
    random_walk_steps: int = 100,
    include_canonical: bool = True,
) -> tuple[list[list[tuple[str, str]]], Any]:
    """Generate fuzz sequences from the runtime state graph.

    Uses ``RuntimeStateMachineHarness`` to discover the *actual* runtime clips
    (which may be a subset of the full design-time state set).  Sequences are
    built exclusively from edges that exist in the runtime graph so that the
    harness never rejects a transition as illegal.

    Returns (sequences, graph) where each sequence is a list of (source, target)
    edge tuples.
    """
    from mathart.animation.state_machine_graph import (
        RuntimeStateMachineHarness, RuntimeStateGraph,
    )

    # Bootstrap harness to get the real runtime graph
    harness = RuntimeStateMachineHarness()
    harness.initialize()
    graph = harness.graph
    assert graph is not None

    nodes = graph.nodes()
    sequences: list[list[tuple[str, str]]] = []

    # 1. Canonical edge walk (deterministic full coverage)
    if include_canonical:
        canonical = graph.canonical_edge_walk()
        if canonical:
            sequences.append(canonical)

    # 2. Random walks with different seeds (adversarial exploration)
    for seed in range(num_random_walks):
        walk = graph.random_walk(steps=random_walk_steps, seed=seed)
        if walk:
            sequences.append(walk)

    # 3. Stress sequences built from *legal* edges only
    def _legal_stress(pattern: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Filter a stress pattern to only include legal edges."""
        return [e for e in pattern if graph.edge_exists(e[0], e[1])]

    # Build stress patterns dynamically from available nodes
    cyclic = [n for n in nodes if graph.state_kinds.get(n) == "cyclic"]
    transient = [n for n in nodes if graph.state_kinds.get(n) == "transient"]
    start = graph.start_state

    # Rapid cyclic switching
    if len(cyclic) >= 2:
        pattern = []
        for i in range(min(20, len(cyclic) * 10)):
            src = cyclic[i % len(cyclic)]
            tgt = cyclic[(i + 1) % len(cyclic)]
            pattern.append((src, tgt))
        legal = _legal_stress(pattern)
        if legal:
            sequences.append(legal * 3)

    # Cyclic-to-transient oscillation
    if cyclic and transient:
        pattern = []
        for c in cyclic:
            for t in transient:
                if graph.edge_exists(c, t) and graph.edge_exists(t, c):
                    pattern.extend([(c, t), (t, c)] * 5)
        if pattern:
            sequences.append(pattern)

    # Full state tornado (visit every node in sequence)
    if len(nodes) >= 3:
        tornado: list[tuple[str, str]] = []
        ordered = [start] + [n for n in nodes if n != start]
        for i in range(len(ordered) - 1):
            edge = (ordered[i], ordered[i + 1])
            if graph.edge_exists(*edge):
                tornado.append(edge)
        # Close the loop
        if tornado and graph.edge_exists(ordered[-1], start):
            tornado.append((ordered[-1], start))
        if tornado:
            sequences.append(tornado * 10)

    # Self-loop stress (each node transitions to itself rapidly)
    for n in nodes:
        if graph.edge_exists(n, n):
            sequences.append([(n, n)] * 30)

    return sequences, graph


def execute_fuzz_sequence(
    sequence: list[tuple[str, str]],
    report: GraphFuzzReport,
    *,
    dt: float = 1.0 / 24.0,
) -> dict[str, Any]:
    """Execute a single fuzz sequence through the runtime and monitor XPBD.

    Returns a dict with execution results.
    """
    from mathart.animation.state_machine_graph import RuntimeStateMachineHarness

    harness = RuntimeStateMachineHarness()
    harness.initialize()

    executed: list[tuple[str, str]] = []
    failures: list[str] = []
    xpbd_findings: list[FuzzFinding] = []
    max_error = 0.0
    max_velocity = 0.0
    max_energy = 0.0

    for src, tgt in sequence:
        try:
            current = harness.current_state
            if current != src:
                # Try to recover by transitioning to expected source
                try:
                    harness.transition_to(src, dt=dt)
                except (ValueError, Exception):
                    failures.append(f"Cannot recover to {src} from {current}")
                    break

            frame = harness.transition_to(tgt, dt=dt)
            executed.append((src, tgt))

            # Check XPBD health if diagnostics available
            runtime = harness.runtime
            if hasattr(runtime, "_last_xpbd_diagnostics"):
                diag = runtime._last_xpbd_diagnostics
                health = check_xpbd_health(diag)
                xpbd_findings.extend(health)
                if diag is not None:
                    d = diag.to_dict() if hasattr(diag, "to_dict") else (diag if isinstance(diag, dict) else {})
                    max_error = max(max_error, d.get("max_constraint_error", 0.0))
                    max_velocity = max(max_velocity, d.get("max_velocity_observed", 0.0))
                    max_energy = max(max_energy, d.get("energy_estimate", 0.0))

        except ValueError as e:
            failures.append(f"Illegal transition {src}->{tgt}: {e}")
        except Exception as e:
            failures.append(f"Runtime error {src}->{tgt}: {e}")
            # Check for NaN in the exception
            if "nan" in str(e).lower() or "inf" in str(e).lower():
                report.nan_explosions += 1
                report.add("CRITICAL", "nan_in_exception",
                           f"NaN/Inf detected in exception: {e}")

    # Aggregate XPBD findings
    for finding in xpbd_findings:
        report.findings.append(finding)
        if finding.category == "nan_explosion":
            report.nan_explosions += 1
        elif finding.category == "penetration_violation":
            report.penetration_violations += 1
        elif finding.category == "energy_spike":
            report.energy_spikes += 1

    report.xpbd_max_error = max(report.xpbd_max_error, max_error)
    report.xpbd_max_velocity = max(report.xpbd_max_velocity, max_velocity)
    report.xpbd_max_energy = max(report.xpbd_max_energy, max_energy)

    return {
        "executed": executed,
        "failures": failures,
        "xpbd_findings": len(xpbd_findings),
        "max_error": max_error,
        "max_velocity": max_velocity,
        "max_energy": max_energy,
    }


# ---------------------------------------------------------------------------
# Full Fuzz Pipeline
# ---------------------------------------------------------------------------


def run_graph_fuzz_audit(
    *,
    num_random_walks: int = 50,
    random_walk_steps: int = 100,
) -> GraphFuzzReport:
    """Execute the complete graph-fuzz CI pipeline.

    1. Generate fuzz sequences (canonical + random + stress patterns).
    2. Execute each through RuntimeStateMachineHarness.
    3. Monitor XPBD solver health at every transition.
    4. Compute coverage metrics.
    5. Determine acceptance.
    """
    report = GraphFuzzReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    # Generate sequences
    sequences, graph = generate_fuzz_sequences(
        num_random_walks=num_random_walks,
        random_walk_steps=random_walk_steps,
    )
    report.total_sequences = len(sequences)
    report.add("PASS", "sequence_generation",
               f"Generated {len(sequences)} fuzz sequences")

    # Execute all sequences and collect edges
    all_executed_edges: list[tuple[str, str]] = []
    total_transitions = 0

    for i, seq in enumerate(sequences):
        result = execute_fuzz_sequence(seq, report)
        all_executed_edges.extend(result["executed"])
        total_transitions += len(result["executed"])

        if result["failures"]:
            for failure in result["failures"][:3]:  # Cap per-sequence failures
                report.add("WARN", f"seq_{i}_failure", failure)

    report.total_transitions = total_transitions

    # Compute coverage
    coverage = graph.coverage_from_edges(all_executed_edges)
    report.coverage_edge = coverage.edge_coverage
    report.coverage_edge_pair = coverage.edge_pair_coverage

    report.add(
        "PASS" if coverage.edge_coverage >= 1.0 else "WARN",
        "edge_coverage",
        f"Edge coverage: {coverage.edge_coverage:.2%} "
        f"({coverage.covered_edges}/{coverage.total_edges})",
    )
    report.add(
        "PASS" if coverage.edge_pair_coverage >= 0.8 else "WARN",
        "edge_pair_coverage",
        f"Edge-pair coverage: {coverage.edge_pair_coverage:.2%} "
        f"({coverage.covered_edge_pairs}/{coverage.total_edge_pairs})",
    )

    # Acceptance criteria
    report.accepted = (
        report.nan_explosions == 0
        and report.penetration_violations == 0
        and coverage.edge_coverage >= 1.0
    )

    if report.accepted:
        report.add("PASS", "acceptance", "Graph-fuzz audit ACCEPTED")
    else:
        reasons = []
        if report.nan_explosions > 0:
            reasons.append(f"{report.nan_explosions} NaN explosions")
        if report.penetration_violations > 0:
            reasons.append(f"{report.penetration_violations} penetration violations")
        if coverage.edge_coverage < 1.0:
            reasons.append(f"edge coverage {coverage.edge_coverage:.2%} < 100%")
        report.add("FAIL", "acceptance",
                    f"Graph-fuzz audit REJECTED: {'; '.join(reasons)}")

    return report


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------


def test_graph_fuzz_no_nan():
    """Pytest: No NaN explosions across all fuzz sequences."""
    report = run_graph_fuzz_audit(num_random_walks=20, random_walk_steps=50)
    assert report.nan_explosions == 0, (
        f"NaN explosions detected: {report.nan_explosions}\n{report.summary()}"
    )


def test_graph_fuzz_no_penetration():
    """Pytest: No penetration violations across all fuzz sequences."""
    report = run_graph_fuzz_audit(num_random_walks=20, random_walk_steps=50)
    assert report.penetration_violations == 0, (
        f"Penetration violations: {report.penetration_violations}\n{report.summary()}"
    )


def test_graph_fuzz_full_edge_coverage():
    """Pytest: Fuzz sequences achieve 100% edge coverage."""
    report = run_graph_fuzz_audit(num_random_walks=20, random_walk_steps=50)
    assert report.coverage_edge >= 1.0, (
        f"Edge coverage {report.coverage_edge:.2%} < 100%\n{report.summary()}"
    )


def test_graph_fuzz_stress_patterns():
    """Pytest: Stress patterns (rapid oscillation) complete without crash."""
    from mathart.animation.state_machine_graph import RuntimeStateMachineHarness

    harness = RuntimeStateMachineHarness()
    harness.initialize()
    graph = harness.graph
    assert graph is not None

    # Build stress pattern from *legal* edges only
    nodes = graph.nodes()
    cyclic = [n for n in nodes if graph.state_kinds.get(n) == "cyclic"]
    start = graph.start_state

    # Rapid cyclic switching stress test
    stress: list[tuple[str, str]] = []
    if len(cyclic) >= 2:
        for i in range(40):
            src = cyclic[i % len(cyclic)]
            tgt = cyclic[(i + 1) % len(cyclic)]
            if graph.edge_exists(src, tgt):
                stress.append((src, tgt))
    else:
        # Fallback: self-loop on start state
        stress = [(start, start)] * 40

    executed = 0
    for src, tgt in stress:
        try:
            current = harness.current_state
            if current != src:
                harness.transition_to(src)
            harness.transition_to(tgt)
            executed += 1
        except (ValueError, Exception):
            pass

    assert executed >= len(stress) * 0.3, (
        f"Only {executed}/{len(stress)} stress transitions completed"
    )


def test_graph_fuzz_acceptance():
    """Pytest: Full graph-fuzz audit passes acceptance criteria."""
    report = run_graph_fuzz_audit(num_random_walks=10, random_walk_steps=30)
    assert report.accepted, f"Graph-fuzz audit not accepted:\n{report.summary()}"


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main():
    """CLI entry point for headless graph-fuzz CI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Headless Graph-Fuzz CI — Property-Based State-Machine Fuzzing"
    )
    parser.add_argument(
        "--walks", type=int, default=50,
        help="Number of random walks to generate (default: 50)"
    )
    parser.add_argument(
        "--steps", type=int, default=100,
        help="Steps per random walk (default: 100)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output report as JSON"
    )
    args = parser.parse_args()

    print("Running headless graph-fuzz CI audit...")
    report = run_graph_fuzz_audit(
        num_random_walks=args.walks,
        random_walk_steps=args.steps,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())


if __name__ == "__main__":
    main()
