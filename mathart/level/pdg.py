"""PDG v2 runtime for lightweight procedural graphs.

This module upgrades the repository's compact DAG executor with a stronger
runtime contract inspired by Houdini PDG/TOPs, Bazel remote caching, and
Airflow dynamic task mapping.

Key properties
--------------
1. Immutable-ish ``WorkItem`` objects form the runtime unit of execution.
2. Hermetic SHA-256 cache keys are computed from node contract, runtime
   context, and upstream work-item products.
3. Fan-out and fan-in are implemented as explicit runtime topology semantics,
   not as ad-hoc loops hidden inside node logic.
4. The public ``ProceduralDependencyGraph.run()`` API remains backward
   compatible: existing linear graphs still behave as the ``fan-out = 1`` case.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Optional


class PDGError(RuntimeError):
    """Raised when the graph is invalid or execution fails."""


@dataclass(frozen=True)
class PDGFanOutItem:
    """One child work item emitted by a fan-out execution."""

    payload: dict[str, Any]
    attributes: dict[str, Any] = field(default_factory=dict)
    partition_key: Optional[str] = None
    label: Optional[str] = None


@dataclass(frozen=True)
class PDGFanOutResult:
    """Explicit runtime signal that a node fans out into multiple work items."""

    items: tuple[PDGFanOutItem, ...]

    @classmethod
    def from_payloads(
        cls,
        payloads: list[dict[str, Any]],
        *,
        partition_keys: Optional[list[Optional[str]]] = None,
        labels: Optional[list[Optional[str]]] = None,
        attributes: Optional[list[dict[str, Any]]] = None,
    ) -> "PDGFanOutResult":
        built: list[PDGFanOutItem] = []
        for index, payload in enumerate(payloads):
            built.append(
                PDGFanOutItem(
                    payload=dict(payload),
                    attributes=dict((attributes or [{}] * len(payloads))[index]),
                    partition_key=(partition_keys or [None] * len(payloads))[index],
                    label=(labels or [None] * len(payloads))[index],
                )
            )
        return cls(items=tuple(built))


@dataclass(frozen=True)
class WorkItem:
    """Frozen runtime execution product passed between PDG nodes."""

    item_id: str
    node_name: str
    payload: MappingProxyType
    attributes: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    parent_ids: tuple[str, ...] = field(default_factory=tuple)
    upstream_item_ids: tuple[str, ...] = field(default_factory=tuple)
    partition_key: Optional[str] = None
    cache_key: str = ""
    payload_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
        object.__setattr__(self, "parent_ids", tuple(self.parent_ids))
        object.__setattr__(self, "upstream_item_ids", tuple(self.upstream_item_ids))

    def payload_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    def attributes_dict(self) -> dict[str, Any]:
        return dict(self.attributes)

    def contract_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "node_name": self.node_name,
            "partition_key": self.partition_key,
            "parent_ids": list(self.parent_ids),
            "upstream_item_ids": list(self.upstream_item_ids),
            "cache_key": self.cache_key,
            "payload_digest": self.payload_digest,
            "attributes": _stable_hash_serialize(self.attributes_dict()),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "node_name": self.node_name,
            "partition_key": self.partition_key,
            "parent_ids": list(self.parent_ids),
            "upstream_item_ids": list(self.upstream_item_ids),
            "cache_key": self.cache_key,
            "payload_digest": self.payload_digest,
            "payload": self.payload_dict(),
            "attributes": self.attributes_dict(),
        }


@dataclass
class PDGNode:
    """A single node in the procedural dependency graph."""

    name: str
    operation: Callable[[dict[str, Any], dict[str, Any]], Any]
    dependencies: list[str] = field(default_factory=list)
    description: str = ""
    topology: str = "task"  # task | collect
    config: dict[str, Any] = field(default_factory=dict)
    cache_enabled: bool = True
    collect_by_partition: bool = False
    requires_gpu: bool = False


@dataclass
class PDGTraceEntry:
    """Execution trace for one work-item level node invocation."""

    node_name: str
    dependencies: list[str]
    duration_ms: float
    output_keys: list[str]
    item_id: str = ""
    partition_key: Optional[str] = None
    cache_key: Optional[str] = None
    cache_hit: bool = False
    topology: str = "task"
    parent_ids: list[str] = field(default_factory=list)
    upstream_item_ids: list[str] = field(default_factory=list)
    requires_gpu: bool = False
    resource_wait_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_name": self.node_name,
            "dependencies": list(self.dependencies),
            "duration_ms": round(self.duration_ms, 3),
            "output_keys": list(self.output_keys),
            "item_id": self.item_id,
            "partition_key": self.partition_key,
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
            "topology": self.topology,
            "parent_ids": list(self.parent_ids),
            "upstream_item_ids": list(self.upstream_item_ids),
            "requires_gpu": self.requires_gpu,
            "resource_wait_ms": round(self.resource_wait_ms, 3),
        }


def _stable_hash_serialize(value: Any) -> Any:
    """Convert arbitrary Python values into a stable hashable JSON structure."""
    if isinstance(value, WorkItem):
        return value.contract_dict()
    if isinstance(value, PDGFanOutItem):
        return {
            "payload": _stable_hash_serialize(value.payload),
            "attributes": _stable_hash_serialize(value.attributes),
            "partition_key": value.partition_key,
            "label": value.label,
        }
    if isinstance(value, PDGFanOutResult):
        return {"items": [_stable_hash_serialize(item) for item in value.items]}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": value.__class__.__qualname__,
            "fields": _stable_hash_serialize(asdict(value)),
        }
    if isinstance(value, Path):
        return {"__path__": value.as_posix()}
    if isinstance(value, Enum):
        return {"__enum__": f"{value.__class__.__qualname__}:{value.value}"}
    if isinstance(value, MappingProxyType):
        return _stable_hash_serialize(dict(value))
    if isinstance(value, dict):
        return {
            str(key): _stable_hash_serialize(subvalue)
            for key, subvalue in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_hash_serialize(item) for item in value]
    if isinstance(value, set):
        serialized = [_stable_hash_serialize(item) for item in value]
        return sorted(serialized, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return {
            "__class__": value.__class__.__qualname__,
            "to_dict": _stable_hash_serialize(value.to_dict()),
        }
    if hasattr(value, "tolist") and callable(value.tolist):
        try:
            return {"__class__": value.__class__.__qualname__, "tolist": _stable_hash_serialize(value.tolist())}
        except Exception:
            pass
    return {"__class__": value.__class__.__qualname__, "repr": repr(value)}


def _json_safe_payload(value: Any) -> Any:
    """Convert values into reloadable JSON payloads.

    Unlike ``_stable_hash_serialize`` this function is strict: if a value cannot
    be restored from JSON in a meaningful way, ``TypeError`` is raised and the
    runtime simply disables disk caching for that invocation.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": value.__class__.__qualname__,
            "fields": _json_safe_payload(asdict(value)),
        }
    if isinstance(value, Path):
        return {"__path__": value.as_posix()}
    if isinstance(value, Enum):
        return {"__enum__": f"{value.__class__.__qualname__}:{value.value}"}
    if isinstance(value, dict):
        return {
            str(key): _json_safe_payload(subvalue)
            for key, subvalue in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Value of type {type(value)!r} is not JSON-cacheable")


def _json_dumps_stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_from_data(value: Any) -> str:
    return hashlib.sha256(_json_dumps_stable(value).encode("utf-8")).hexdigest()


def _operation_identity(operation: Callable[..., Any]) -> dict[str, Any]:
    try:
        source = inspect.getsource(operation)
    except (OSError, TypeError):
        source = None
    return {
        "module": getattr(operation, "__module__", "<unknown>"),
        "qualname": getattr(operation, "__qualname__", getattr(operation, "__name__", "<anonymous>")),
        "source": source,
    }


def _legacy_context_value(items: list[WorkItem]) -> Any:
    payloads = [item.payload_dict() for item in items]
    return payloads[0] if len(payloads) == 1 else payloads


class _PDGDiskCache:
    """Bazel-style Action Cache + CAS using JSON-serializable payloads."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.action_dir = self.root / "ac"
        self.cas_dir = self.root / "cas"
        self.action_dir.mkdir(parents=True, exist_ok=True)
        self.cas_dir.mkdir(parents=True, exist_ok=True)

    def _action_path(self, cache_key: str) -> Path:
        return self.action_dir / f"{cache_key}.json"

    def _payload_path(self, payload_digest: str) -> Path:
        return self.cas_dir / f"{payload_digest}.json"

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        temp_path = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def load(self, cache_key: str) -> Optional[dict[str, Any]]:
        action_path = self._action_path(cache_key)
        if not action_path.exists():
            return None
        try:
            record = json.loads(action_path.read_text(encoding="utf-8"))
            items: list[dict[str, Any]] = []
            for entry in record.get("items", []):
                payload_path = self._payload_path(entry["payload_digest"])
                if not payload_path.exists():
                    return None
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
                items.append({**entry, "payload": payload})
            return {**record, "items": items}
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def save(self, cache_key: str, action_record: dict[str, Any], items: list[dict[str, Any]]) -> bool:
        try:
            persisted_entries: list[dict[str, Any]] = []
            for item in items:
                payload_json = _json_safe_payload(item["payload"])
                payload_digest = _sha256_from_data(payload_json)
                payload_path = self._payload_path(payload_digest)
                if not payload_path.exists():
                    self._write_json_atomic(payload_path, payload_json)
                persisted_entries.append(
                    {
                        "item_id": item["item_id"],
                        "partition_key": item.get("partition_key"),
                        "parent_ids": list(item.get("parent_ids", [])),
                        "upstream_item_ids": list(item.get("upstream_item_ids", [])),
                        "attributes": _json_safe_payload(item.get("attributes", {})),
                        "payload_digest": payload_digest,
                        "cache_key": item["cache_key"],
                    }
                )
            action_path = self._action_path(cache_key)
            self._write_json_atomic(action_path, {**action_record, "items": persisted_entries})
            return True
        except (TypeError, OSError):
            return False


@dataclass(frozen=True)
class _Invocation:
    partition_key: Optional[str]
    dependencies: dict[str, WorkItem]

    @property
    def upstream_item_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.dependencies.values())


@dataclass(frozen=True)
class _InvocationResult:
    items: list[WorkItem]
    trace: PDGTraceEntry
    cache_hit: bool


class _PDGv2RuntimeFacade:
    """Facade that upgrades legacy PDG graphs to the PDG v2 runtime."""

    def __init__(self, graph: "ProceduralDependencyGraph") -> None:
        self.graph = graph
        cache_root = graph.cache_dir or (Path.cwd() / ".pdg_cache" / graph.name)
        self.cache = _PDGDiskCache(cache_root)
        self._trace: list[PDGTraceEntry] = []
        self._results: dict[str, list[WorkItem]] = {}
        self._context: dict[str, Any] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._gpu_semaphore = threading.Semaphore(graph.gpu_slots)
        self._gpu_state_lock = threading.Lock()
        self._gpu_inflight = 0
        self._gpu_max_inflight = 0

    def run(
        self,
        targets: Optional[list[str]] = None,
        *,
        initial_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self._trace = []
        self._results = {}
        self._context = dict(initial_context or {})
        self._cache_hits = 0
        self._cache_misses = 0

        order = self.graph.execution_order(targets)
        for name in order:
            node = self.graph._nodes[name]
            if node.topology == "collect":
                self._results[name] = self._execute_collect_node(node)
            else:
                self._results[name] = self._execute_task_node(node)
            self._context[name] = _legacy_context_value(self._results[name])

        requested = targets or order[-1:]
        results = {name: _legacy_context_value(self._results[name]) for name in order}
        target_outputs = {name: results[name] for name in requested}
        return {
            "graph_name": self.graph.name,
            "runtime_version": "pdg_v2",
            "artifact_family": "pdg_runtime_trace",
            "backend_type": "pdg_v2_facade",
            "execution_order": order,
            "trace": [entry.to_dict() for entry in self._trace],
            "results": results,
            "target_outputs": target_outputs,
            "work_items": {name: [item.to_dict() for item in self._results[name]] for name in order},
            "cache_stats": {"hits": self._cache_hits, "misses": self._cache_misses},
            "scheduler": {
                "backend": self.graph.scheduler_backend,
                "max_workers": self.graph.max_workers,
                "host_cpu_count": os.cpu_count() or 1,
                "bounded_submission": True,
                "gpu_slots": self.graph.gpu_slots,
                "gpu_max_inflight_observed": self._gpu_max_inflight,
            },
            "topology_summary": {
                name: {
                    "topology": self.graph._nodes[name].topology,
                    "work_items": len(self._results[name]),
                    "partition_keys": [item.partition_key for item in self._results[name]],
                }
                for name in order
            },
        }

    def _execute_task_node(self, node: PDGNode) -> list[WorkItem]:
        invocations = self._build_mapped_invocations(node)
        if len(invocations) <= 1 or self.graph.max_workers <= 1:
            execution_results = [
                self._execute_invocation(node, invocation, invocation_index=index)
                for index, invocation in enumerate(invocations)
            ]
        else:
            execution_results = self._execute_task_invocations_concurrently(node, invocations)

        produced: list[WorkItem] = []
        for execution in execution_results:
            produced.extend(execution.items)
            if execution.cache_hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1
            self._trace.append(execution.trace)
        return produced

    def _execute_collect_node(self, node: PDGNode) -> list[WorkItem]:
        dep_items = {dep: self._results[dep] for dep in node.dependencies}
        if node.collect_by_partition:
            partitions: dict[Optional[str], dict[str, list[WorkItem]]] = {}
            for dep_name, items in dep_items.items():
                for item in items:
                    partitions.setdefault(item.partition_key, {}).setdefault(dep_name, []).append(item)
            groups = sorted(partitions.items(), key=lambda entry: str(entry[0]))
        else:
            groups = [(None, dep_items)]

        produced: list[WorkItem] = []
        for index, (partition_key, grouped_deps) in enumerate(groups):
            invocation_context = dict(self._context)
            deps_payload = {
                dep_name: [item.payload_dict() for item in items]
                for dep_name, items in grouped_deps.items()
            }
            invocation_context["_pdg"] = {
                "graph_name": self.graph.name,
                "node_name": node.name,
                "topology": node.topology,
                "partition_key": partition_key,
                "dependency_item_ids": {
                    dep_name: [item.item_id for item in items]
                    for dep_name, items in grouped_deps.items()
                },
            }
            execution_cache_key = self._build_execution_cache_key(
                node=node,
                partition_key=partition_key,
                deps_contract={dep: [item.contract_dict() for item in items] for dep, items in grouped_deps.items()},
                context_snapshot=invocation_context,
            )
            cached = self._load_cached_items(node, execution_cache_key)
            if cached is not None:
                self._cache_hits += 1
                produced.extend(cached)
                self._trace.append(
                    PDGTraceEntry(
                        node_name=node.name,
                        dependencies=list(node.dependencies),
                        duration_ms=0.0,
                        output_keys=sorted({key for item in cached for key in item.payload.keys()}),
                        item_id="|".join(item.item_id for item in cached),
                        partition_key=partition_key,
                        cache_key=execution_cache_key,
                        cache_hit=True,
                        topology=node.topology,
                        parent_ids=[],
                        upstream_item_ids=[
                            item_id
                            for items in grouped_deps.values()
                            for item_id in [dep_item.item_id for dep_item in items]
                        ],
                        requires_gpu=node.requires_gpu,
                        resource_wait_ms=0.0,
                    )
                )
                continue

            output, duration_ms, resource_wait_ms = self._execute_operation(
                node,
                invocation_context,
                deps_payload,
            )
            items = self._normalize_operation_output(
                node=node,
                output=output,
                execution_cache_key=execution_cache_key,
                partition_key=partition_key,
                parent_ids=(),
                upstream_item_ids=tuple(
                    dep_item.item_id for items in grouped_deps.values() for dep_item in items
                ),
                invocation_index=index,
            )
            self._cache_misses += 1
            self._persist_items(node, execution_cache_key, items)
            produced.extend(items)
            self._trace.append(
                PDGTraceEntry(
                    node_name=node.name,
                    dependencies=list(node.dependencies),
                    duration_ms=duration_ms,
                    output_keys=sorted({key for item in items for key in item.payload.keys()}),
                    item_id="|".join(item.item_id for item in items),
                    partition_key=partition_key,
                    cache_key=execution_cache_key,
                    cache_hit=False,
                    topology=node.topology,
                    parent_ids=[],
                    upstream_item_ids=[
                        item_id
                        for items in grouped_deps.values()
                        for item_id in [dep_item.item_id for dep_item in items]
                    ],
                    requires_gpu=node.requires_gpu,
                    resource_wait_ms=resource_wait_ms,
                )
            )
        return produced

    def _execute_operation(
        self,
        node: PDGNode,
        invocation_context: dict[str, Any],
        deps_payload: dict[str, Any],
    ) -> tuple[Any, float, float]:
        resource_wait_ms = 0.0
        acquired_gpu = False
        if node.requires_gpu:
            wait_start = time.perf_counter()
            self._gpu_semaphore.acquire()
            resource_wait_ms = (time.perf_counter() - wait_start) * 1000.0
            acquired_gpu = True
            with self._gpu_state_lock:
                self._gpu_inflight += 1
                self._gpu_max_inflight = max(self._gpu_max_inflight, self._gpu_inflight)

        start = time.perf_counter()
        try:
            output = node.operation(invocation_context, deps_payload)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            if acquired_gpu:
                with self._gpu_state_lock:
                    self._gpu_inflight -= 1
                self._gpu_semaphore.release()
        return output, duration_ms, resource_wait_ms

    def _execute_task_invocations_concurrently(
        self,
        node: PDGNode,
        invocations: list[_Invocation],
    ) -> list[_InvocationResult]:
        if self.graph.scheduler_backend != "thread":
            raise PDGError(f"Unsupported scheduler_backend '{self.graph.scheduler_backend}'")

        ordered_results: dict[int, _InvocationResult] = {}
        in_flight: dict[Any, int] = {}
        next_index = 0
        max_workers = min(self.graph.max_workers, max(1, len(invocations)))

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"pdg-{node.name}") as executor:
            while next_index < len(invocations) or in_flight:
                while next_index < len(invocations) and len(in_flight) < max_workers:
                    future = executor.submit(
                        self._execute_invocation,
                        node,
                        invocations[next_index],
                        invocation_index=next_index,
                    )
                    in_flight[future] = next_index
                    next_index += 1

                done, _pending = wait(tuple(in_flight.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    index = in_flight.pop(future)
                    ordered_results[index] = future.result()

        return [ordered_results[index] for index in range(len(invocations))]

    def _build_mapped_invocations(self, node: PDGNode) -> list[_Invocation]:
        if not node.dependencies:
            return [_Invocation(partition_key=None, dependencies={})]

        dep_lists = {dep: self._results[dep] for dep in node.dependencies}
        broadcast_deps = {
            dep for dep, items in dep_lists.items() if len(items) == 1 and items[0].partition_key is None
        }
        partitioned_deps = {dep: items for dep, items in dep_lists.items() if dep not in broadcast_deps}

        if not partitioned_deps:
            return [
                _Invocation(
                    partition_key=None,
                    dependencies={dep: items[0] for dep, items in dep_lists.items()},
                )
            ]

        partition_keys = {
            item.partition_key if item.partition_key is not None else item.item_id
            for items in partitioned_deps.values()
            for item in items
        }
        invocations: list[_Invocation] = []
        for partition_key in sorted(partition_keys, key=str):
            deps_for_key: dict[str, WorkItem] = {}
            for dep_name, items in dep_lists.items():
                if dep_name in broadcast_deps:
                    deps_for_key[dep_name] = items[0]
                    continue
                matched = [
                    item for item in items
                    if (item.partition_key if item.partition_key is not None else item.item_id) == partition_key
                ]
                if not matched:
                    raise PDGError(
                        f"Node '{node.name}' cannot map dependency '{dep_name}' for partition '{partition_key}'"
                    )
                if len(matched) != 1:
                    raise PDGError(
                        f"Node '{node.name}' received ambiguous mapped dependency '{dep_name}' for partition '{partition_key}'"
                    )
                deps_for_key[dep_name] = matched[0]
            invocations.append(_Invocation(partition_key=str(partition_key), dependencies=deps_for_key))
        return invocations

    def _execute_invocation(
        self,
        node: PDGNode,
        invocation: _Invocation,
        *,
        invocation_index: int,
    ) -> _InvocationResult:
        invocation_context = dict(self._context)
        deps_payload = {dep_name: item.payload_dict() for dep_name, item in invocation.dependencies.items()}
        invocation_context["_pdg"] = {
            "graph_name": self.graph.name,
            "node_name": node.name,
            "topology": node.topology,
            "partition_key": invocation.partition_key,
            "dependency_item_ids": {dep_name: item.item_id for dep_name, item in invocation.dependencies.items()},
            "dependency_work_items": dict(invocation.dependencies),
            "requires_gpu": node.requires_gpu,
            "gpu_slots": self.graph.gpu_slots,
        }
        execution_cache_key = self._build_execution_cache_key(
            node=node,
            partition_key=invocation.partition_key,
            deps_contract={dep: item.contract_dict() for dep, item in invocation.dependencies.items()},
            context_snapshot=invocation_context,
        )
        cached = self._load_cached_items(node, execution_cache_key)
        if cached is not None:
            return _InvocationResult(
                items=cached,
                cache_hit=True,
                trace=PDGTraceEntry(
                    node_name=node.name,
                    dependencies=list(node.dependencies),
                    duration_ms=0.0,
                    output_keys=sorted({key for item in cached for key in item.payload.keys()}),
                    item_id="|".join(item.item_id for item in cached),
                    partition_key=invocation.partition_key,
                    cache_key=execution_cache_key,
                    cache_hit=True,
                    topology=node.topology,
                    parent_ids=[],
                    upstream_item_ids=list(invocation.upstream_item_ids),
                    requires_gpu=node.requires_gpu,
                    resource_wait_ms=0.0,
                ),
            )

        output, duration_ms, resource_wait_ms = self._execute_operation(
            node,
            invocation_context,
            deps_payload,
        )
        items = self._normalize_operation_output(
            node=node,
            output=output,
            execution_cache_key=execution_cache_key,
            partition_key=invocation.partition_key,
            parent_ids=(),
            upstream_item_ids=invocation.upstream_item_ids,
            invocation_index=invocation_index,
        )
        self._persist_items(node, execution_cache_key, items)
        return _InvocationResult(
            items=items,
            cache_hit=False,
            trace=PDGTraceEntry(
                node_name=node.name,
                dependencies=list(node.dependencies),
                duration_ms=duration_ms,
                output_keys=sorted({key for item in items for key in item.payload.keys()}),
                item_id="|".join(item.item_id for item in items),
                partition_key=invocation.partition_key,
                cache_key=execution_cache_key,
                cache_hit=False,
                topology=node.topology,
                parent_ids=[],
                upstream_item_ids=list(invocation.upstream_item_ids),
                requires_gpu=node.requires_gpu,
                resource_wait_ms=resource_wait_ms,
            ),
        )

    def _load_cached_items(self, node: PDGNode, execution_cache_key: str) -> Optional[list[WorkItem]]:
        if not node.cache_enabled:
            return None
        cached = self.cache.load(execution_cache_key)
        if cached is None:
            return None
        return [
            WorkItem(
                item_id=entry["item_id"],
                node_name=node.name,
                payload=entry["payload"],
                attributes=entry.get("attributes", {}),
                parent_ids=tuple(entry.get("parent_ids", [])),
                upstream_item_ids=tuple(entry.get("upstream_item_ids", [])),
                partition_key=entry.get("partition_key"),
                cache_key=entry["cache_key"],
                payload_digest=entry["payload_digest"],
            )
            for entry in cached["items"]
        ]

    def _persist_items(self, node: PDGNode, execution_cache_key: str, items: list[WorkItem]) -> None:
        if not node.cache_enabled:
            return
        self.cache.save(
            execution_cache_key,
            action_record={
                "graph_name": self.graph.name,
                "node_name": node.name,
                "topology": node.topology,
                "cached_at_ms": round(time.time() * 1000.0, 3),
            },
            items=[item.to_dict() for item in items],
        )

    def _normalize_operation_output(
        self,
        *,
        node: PDGNode,
        output: Any,
        execution_cache_key: str,
        partition_key: Optional[str],
        parent_ids: tuple[str, ...],
        upstream_item_ids: tuple[str, ...],
        invocation_index: int,
    ) -> list[WorkItem]:
        if output is None:
            output = {}
        if isinstance(output, dict):
            output_items = [PDGFanOutItem(payload=output, partition_key=partition_key)]
        elif isinstance(output, PDGFanOutResult):
            output_items = list(output.items)
        else:
            raise PDGError(
                f"Node '{node.name}' returned unsupported output type {type(output)!r}; expected dict or PDGFanOutResult"
            )

        work_items: list[WorkItem] = []
        for item_index, child in enumerate(output_items):
            child_partition_key = child.partition_key if child.partition_key is not None else partition_key
            item_digest_contract = {
                "execution_cache_key": execution_cache_key,
                "item_index": item_index,
                "partition_key": child_partition_key,
                "label": child.label,
                "payload": _stable_hash_serialize(child.payload),
                "attributes": _stable_hash_serialize(child.attributes),
            }
            item_cache_key = _sha256_from_data(item_digest_contract)
            payload_digest = _sha256_from_data(_stable_hash_serialize(child.payload))
            label = child.label or f"i{item_index}"
            item_id = f"{node.name}:{invocation_index}:{label}:{item_cache_key[:12]}"
            work_items.append(
                WorkItem(
                    item_id=item_id,
                    node_name=node.name,
                    payload=child.payload,
                    attributes=child.attributes,
                    parent_ids=parent_ids,
                    upstream_item_ids=upstream_item_ids,
                    partition_key=child_partition_key,
                    cache_key=item_cache_key,
                    payload_digest=payload_digest,
                )
            )
        return work_items

    def _build_execution_cache_key(
        self,
        *,
        node: PDGNode,
        partition_key: Optional[str],
        deps_contract: dict[str, Any],
        context_snapshot: dict[str, Any],
    ) -> str:
        contract = {
            "graph_name": self.graph.name,
            "node_name": node.name,
            "node_description": node.description,
            "topology": node.topology,
            "requires_gpu": node.requires_gpu,
            "node_config": _stable_hash_serialize(node.config),
            "dependencies": list(node.dependencies),
            "partition_key": partition_key,
            "operation_identity": _operation_identity(node.operation),
            "context": _stable_hash_serialize(context_snapshot),
            "upstream": _stable_hash_serialize(deps_contract),
        }
        return _sha256_from_data(contract)


class ProceduralDependencyGraph:
    """A lightweight PDG runtime with PDG v2 execution semantics."""

    def __init__(
        self,
        name: str = "pdg",
        *,
        cache_dir: Optional[str | Path] = None,
        max_workers: int = 1,
        scheduler_backend: str = "thread",
        gpu_slots: int = 1,
    ) -> None:
        if max_workers < 1:
            raise PDGError("max_workers must be >= 1")
        if gpu_slots < 1:
            raise PDGError("gpu_slots must be >= 1")
        if scheduler_backend != "thread":
            raise PDGError(f"Unsupported scheduler_backend '{scheduler_backend}'")
        self.name = name
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.max_workers = int(max_workers)
        self.scheduler_backend = scheduler_backend
        self.gpu_slots = int(gpu_slots)
        self._nodes: dict[str, PDGNode] = {}

    def add_node(self, node: PDGNode) -> None:
        if node.name in self._nodes:
            raise PDGError(f"Duplicate node name: {node.name}")
        if node.topology not in {"task", "collect"}:
            raise PDGError(f"Unsupported topology '{node.topology}' on node '{node.name}'")
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
        """Execute the requested subgraph and return outputs plus trace.

        Backward compatibility:
        - Linear graphs continue to expose node outputs as dictionaries.
        - Fan-out nodes expose lists of payload dictionaries under ``results``.
        - Fan-in/collect nodes reduce back to single dictionaries unless they fan
          out again intentionally.
        """
        facade = _PDGv2RuntimeFacade(self)
        return facade.run(targets, initial_context=initial_context)
