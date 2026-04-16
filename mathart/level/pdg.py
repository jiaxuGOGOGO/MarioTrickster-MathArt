"""Lightweight Procedural Dependency Graph (PDG).

This module implements a compact DAG executor inspired by Houdini PDG/TOPs.
It is intentionally simple: enough to orchestrate procedural generation steps
inside this repository without introducing heavyweight runtime dependencies.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class PDGError(RuntimeError):
    """Raised when the graph is invalid or execution fails."""


@dataclass
class PDGNode:
    """A single node in the procedural dependency graph."""

    name: str
    operation: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    dependencies: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class PDGTraceEntry:
    """Execution trace for one node."""

    node_name: str
    dependencies: list[str]
    duration_ms: float
    output_keys: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_name": self.node_name,
            "dependencies": list(self.dependencies),
            "duration_ms": round(self.duration_ms, 3),
            "output_keys": list(self.output_keys),
        }


class ProceduralDependencyGraph:
    """A minimal DAG runtime for procedural content workflows."""

    def __init__(self, name: str = "pdg") -> None:
        self.name = name
        self._nodes: dict[str, PDGNode] = {}

    def add_node(self, node: PDGNode) -> None:
        if node.name in self._nodes:
            raise PDGError(f"Duplicate node name: {node.name}")
        self._nodes[node.name] = node

    def node_names(self) -> list[str]:
        return list(self._nodes.keys())

    def execution_order(self, targets: Optional[list[str]] = None) -> list[str]:
        if not self._nodes:
            return []

        requested = targets or list(self._nodes.keys())
        for target in requested:
            if target not in self._nodes:
                raise PDGError(f"Unknown target node: {target}")

        order: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise PDGError(f"Cycle detected at node: {name}")
            visiting.add(name)
            node = self._nodes[name]
            for dep in node.dependencies:
                if dep not in self._nodes:
                    raise PDGError(f"Node '{name}' depends on unknown node '{dep}'")
                visit(dep)
            visiting.remove(name)
            visited.add(name)
            order.append(name)

        for target in requested:
            visit(target)
        return order

    def run(
        self,
        targets: Optional[list[str]] = None,
        *,
        initial_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute the requested subgraph and return outputs plus trace."""
        context = dict(initial_context or {})
        order = self.execution_order(targets)
        results: dict[str, dict[str, Any]] = {}
        trace: list[PDGTraceEntry] = []

        for name in order:
            node = self._nodes[name]
            dep_results = {dep: results[dep] for dep in node.dependencies}
            start = time.perf_counter()
            output = node.operation(context, dep_results)
            duration_ms = (time.perf_counter() - start) * 1000.0
            if output is None:
                output = {}
            if not isinstance(output, dict):
                raise PDGError(f"Node '{name}' returned non-dict output: {type(output)!r}")
            results[name] = output
            context[name] = output
            trace.append(
                PDGTraceEntry(
                    node_name=name,
                    dependencies=list(node.dependencies),
                    duration_ms=duration_ms,
                    output_keys=list(output.keys()),
                )
            )

        requested = targets or order[-1:]
        target_outputs = {name: results[name] for name in requested}
        return {
            "graph_name": self.name,
            "execution_order": order,
            "trace": [entry.to_dict() for entry in trace],
            "results": results,
            "target_outputs": target_outputs,
        }
