"""Pipeline Bridge — Backward-Compatible Integration Layer.

SESSION-064: Bridges the new microkernel architecture with the existing
AssetPipeline and EvolutionOrchestrator without breaking any current code.

This module provides:
1. ``MicrokernelPipelineBridge``: Wraps the microkernel orchestrator
   to be callable from the existing pipeline.
2. ``legacy_to_manifest()``: Converts legacy output dicts to typed
   ArtifactManifest objects.
3. ``manifest_to_legacy()``: Converts ArtifactManifest back to legacy
   format for backward compatibility.

The bridge ensures:
- All existing tests continue to pass
- All existing bridges continue to work
- New microkernel features are accessible from old code
- Gradual migration path from legacy to microkernel
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    validate_artifact,
)
from mathart.core.backend_registry import BackendCapability, get_registry
from mathart.core.niche_registry import get_niche_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TelemetrySink Protocol — SESSION-072 (P1-DISTILL-1A)
# ---------------------------------------------------------------------------

@runtime_checkable
class TelemetrySink(Protocol):
    """Duck-typed sink for hot-path telemetry data.

    Design reference: eBPF / DTrace zero-overhead dynamic tracing.  The sink
    is injected into the backend context *only* when the caller explicitly
    requests instrumented execution via
    :meth:`MicrokernelPipelineBridge.run_backend_with_telemetry`.  Backends
    interact with the sink through this protocol alone — they never import
    the concrete class, preserving absolute purity of the plugin boundary.

    The ``record`` method is intentionally minimal (a single key-value pair)
    so the per-frame overhead stays in the nanosecond range.  Batch
    aggregation and serialization happen *outside* the hot loop.
    """

    def record(self, key: str, value: Any) -> None:
        """Record a single telemetry datum."""
        ...


def legacy_to_manifest(
    legacy_output: dict[str, Any],
    backend_type: str = "legacy",
    session_id: str = "SESSION-064",
) -> ArtifactManifest:
    """Convert a legacy output dict to a typed ArtifactManifest.

    Parameters
    ----------
    legacy_output : dict
        Legacy output dict with keys like 'output_paths', 'metrics', etc.
    backend_type : str
        Backend that produced this output.
    session_id : str
        Session identifier.

    Returns
    -------
    ArtifactManifest
        Typed manifest with proper artifact_family and backend_type.
    """
    # Infer artifact family from legacy output
    output_paths = legacy_output.get("output_paths", [])
    metrics = legacy_output.get("metrics", {})

    family = ArtifactFamily.COMPOSITE.value
    outputs: dict[str, str] = {}

    for i, path in enumerate(output_paths):
        path_str = str(path)
        if path_str.endswith((".png", ".jpg", ".webp")):
            if "sprite" in path_str.lower() or "sheet" in path_str.lower():
                family = ArtifactFamily.SPRITE_SHEET.value
            outputs[f"image_{i}"] = path_str
        elif path_str.endswith((".obj", ".fbx")):
            family = ArtifactFamily.MESH_OBJ.value
            outputs[f"mesh_{i}"] = path_str
        elif path_str.endswith((".hlsl", ".shader", ".glsl")):
            family = ArtifactFamily.SHADER_HLSL.value
            outputs[f"shader_{i}"] = path_str
        elif path_str.endswith(".json"):
            outputs[f"data_{i}"] = path_str
        else:
            outputs[f"file_{i}"] = path_str

    quality_metrics = {}
    for key, val in metrics.items():
        if isinstance(val, (int, float)):
            quality_metrics[key] = float(val)

    return ArtifactManifest(
        artifact_family=family,
        backend_type=backend_type,
        session_id=session_id,
        outputs=outputs,
        metadata=legacy_output.get("metadata", {}),
        quality_metrics=quality_metrics,
    )


def manifest_to_legacy(manifest: ArtifactManifest) -> dict[str, Any]:
    """Convert an ArtifactManifest back to legacy output format.

    Parameters
    ----------
    manifest : ArtifactManifest
        Typed manifest to convert.

    Returns
    -------
    dict
        Legacy-compatible output dict.
    """
    return {
        "output_paths": list(manifest.outputs.values()),
        "metrics": manifest.quality_metrics,
        "metadata": manifest.metadata,
        "artifact_family": manifest.artifact_family,
        "backend_type": manifest.backend_type,
        "schema_hash": manifest.schema_hash,
    }


class MicrokernelPipelineBridge:
    """Bridge between the microkernel and existing AssetPipeline.

    This class can be used as a drop-in replacement or wrapper for
    the existing pipeline, providing microkernel features while
    maintaining backward compatibility.
    """

    def __init__(
        self,
        project_root: Optional[str | Path] = None,
        session_id: str = "SESSION-064",
    ) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.session_id = session_id
        self.backend_registry = get_registry()
        self.niche_registry = get_niche_registry()

    def run_backend(
        self, backend_name: str, context: dict[str, Any],
    ) -> ArtifactManifest:
        """Run a registered backend and return a validated manifest.

        Parameters
        ----------
        backend_name : str
            Name of the registered backend.
        context : dict
            Execution context.

        Returns
        -------
        ArtifactManifest
            Validated artifact manifest.

        Raises
        ------
        KeyError
            If backend is not registered.
        """
        meta, cls = self.backend_registry.get_or_raise(backend_name)

        # Resolve dependencies
        dep_order = self.backend_registry.resolve_dependencies(backend_name)
        for dep_name in dep_order[:-1]:
            dep_meta, dep_cls = self.backend_registry.get_or_raise(dep_name)
            dep_instance = dep_cls()
            dep_manifest = dep_instance.execute(context)
            context[f"{dep_name}_manifest"] = dep_manifest

        # Execute target backend
        instance = cls()

        # SESSION-068: If the backend implements validate_config(), call it
        # before execution. This follows Duck Typing / Ports-and-Adapters:
        # the bridge does NOT know what the backend validates — it just
        # checks for the method and delegates.
        if hasattr(instance, "validate_config") and callable(instance.validate_config):
            validated_ctx, config_warnings = instance.validate_config(context)
            for cw in config_warnings:
                logger.info("Backend %s config: %s", backend_name, cw)
            context = validated_ctx

        manifest = instance.execute(context)

        # Validate
        errors = validate_artifact(manifest)
        if errors:
            logger.warning(
                "Backend %s produced invalid artifact: %s",
                backend_name, errors,
            )

        return manifest

    def run_all_backends(
        self, context: dict[str, Any],
    ) -> list[ArtifactManifest]:
        """Run all registered backends and return validated manifests."""
        manifests: list[ArtifactManifest] = []
        for name in self.backend_registry.all_backends():
            try:
                manifest = self.run_backend(name, dict(context))
                manifests.append(manifest)
            except Exception as e:
                logger.error("Backend %s failed: %s", name, e)
        return manifests

    # ------------------------------------------------------------------ telemetry
    # SESSION-072 (P1-DISTILL-1A): eBPF / DTrace-inspired opt-in telemetry
    # injection.  The bridge injects a TelemetrySink into the context under
    # the reserved key ``__telemetry_sink__`` *only* when the target backend
    # declares ``BackendCapability.HOT_PATH_INSTRUMENTED``.  Backends that do
    # not declare the capability never see the sink, guaranteeing zero
    # overhead on the normal production path.

    _TELEMETRY_SINK_KEY: str = "__telemetry_sink__"

    def run_backend_with_telemetry(
        self,
        backend_name: str,
        context: dict[str, Any],
        sink: "TelemetrySink",
    ) -> ArtifactManifest:
        """Run a backend with an attached TelemetrySink.

        Parameters
        ----------
        backend_name : str
            Name of the registered backend.
        context : dict
            Execution context (will be shallow-copied before mutation).
        sink : TelemetrySink
            Duck-typed sink instance.  Must expose ``record(key, value)``.

        Returns
        -------
        ArtifactManifest
            Validated artifact manifest.

        Raises
        ------
        RuntimeError
            If the target backend does not declare
            ``BackendCapability.HOT_PATH_INSTRUMENTED``.
        """
        meta, _cls = self.backend_registry.get_or_raise(backend_name)
        if BackendCapability.HOT_PATH_INSTRUMENTED not in meta.capabilities:
            raise RuntimeError(
                f"Backend {backend_name!r} does not declare "
                f"HOT_PATH_INSTRUMENTED; telemetry injection is forbidden."
            )
        ctx = dict(context)
        ctx[self._TELEMETRY_SINK_KEY] = sink
        return self.run_backend(backend_name, ctx)

    def get_registry_summary(self) -> str:
        """Get a Markdown summary of all registered backends and niches."""
        lines = [
            "# Microkernel Registry Summary",
            "",
            "## Backends",
            self.backend_registry.summary_table(),
            "",
            "## Niches",
            self.niche_registry.summary_table(),
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------

def test_legacy_to_manifest():
    """Legacy output converts to typed manifest."""
    legacy = {
        "output_paths": ["/tmp/sprite.png", "/tmp/data.json"],
        "metrics": {"quality": 0.85, "diversity": 0.72},
    }
    manifest = legacy_to_manifest(legacy, backend_type="test")
    assert manifest.backend_type == "test"
    assert len(manifest.outputs) == 2
    assert manifest.quality_metrics["quality"] == 0.85


def test_manifest_to_legacy():
    """ArtifactManifest converts back to legacy format."""
    manifest = ArtifactManifest(
        artifact_family=ArtifactFamily.SPRITE_SHEET.value,
        backend_type="motion_2d",
        outputs={"spritesheet": "/tmp/sheet.png"},
        metadata={"frame_count": 8, "frame_width": 32, "frame_height": 32},
        quality_metrics={"diversity": 0.85},
    )
    legacy = manifest_to_legacy(manifest)
    assert "/tmp/sheet.png" in legacy["output_paths"]
    assert legacy["metrics"]["diversity"] == 0.85
    assert legacy["artifact_family"] == "sprite_sheet"


def test_pipeline_bridge_registry_summary():
    """Pipeline bridge generates registry summary."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="bridge_test_") as tmpdir:
        bridge = MicrokernelPipelineBridge(project_root=tmpdir)
        summary = bridge.get_registry_summary()
        assert "Microkernel Registry Summary" in summary
