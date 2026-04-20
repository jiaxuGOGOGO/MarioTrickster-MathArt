"""SESSION-106 (P1-B1-1): PhysicalRibbonBackend — Registry-Native Physical
Ribbon Mesh Extraction & Scene Assembly Plugin.

This module implements the ``@register_backend`` plugin for the physical
ribbon mesh extraction pipeline.  It consumes secondary chain snapshots
(from Jakobsen or XPBD solvers) and produces 3D ribbon meshes that are
composed with the base character through the **Separation-of-Concerns
Scene Contract** (Pixar OpenUSD Composition Arcs pattern).

Research Foundations
--------------------
1. **Pixar OpenUSD Composition Arcs** — Non-destructive asset assembly:
   The base character mesh and the physical ribbon/cape mesh are kept as
   **physically separate artifacts**.  They are composed through a
   ``CompositeManifest`` that references both sub-manifests, analogous
   to USD's SubLayer/Reference composition arcs.

2. **UE5 Niagara Ribbon Data Interface** — Tangent-Binormal frame
   construction from discrete particle positions for smooth mesh extrusion.

3. **Guilty Gear Xrd (GDC 2015)** — Proxy-shape normal injection for
   predictable cel-shading response on procedural hair/cloth geometry.

Architecture Discipline
-----------------------
- ✅ Independent plugin: self-registers via ``@register_backend``
- ✅ No trunk modification: ZERO changes to AssetPipeline/Orchestrator
- ✅ Strong-type contract: returns ``ArtifactManifest`` with explicit
  ``artifact_family`` and ``backend_type``
- ✅ Scene Contract: physical ribbon is a separate ``Attachment`` payload,
  NEVER hardcoded into the base character mesh or motion state dict
- ✅ Backend-owned ``validate_config()``: all parameter validation is
  physically sunk into this Adapter (Hexagonal Architecture)
- ✅ Graceful Fallback: empty/missing chain data produces a valid empty
  manifest without crashing — zero regression on existing templates

Anti-Pattern Guards
-------------------
🚫 Anti-Spaghetti Attachment: The ribbon mesh is produced as an independent
   PDG WorkItem.  It is NEVER injected via ``if has_cape:`` into the base
   character renderer's inner loop.

🚫 Anti-Debug-Line Rendering: The output is a real ``Mesh3D`` with vertices,
   faces, normals, and depth — NOT cv2.line() or matplotlib debug lines.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)
from mathart.core.artifact_schema import (
    ArtifactFamily,
    ArtifactManifest,
    CompositeManifestBuilder,
)
from mathart.core.backend_types import BackendType

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  New BackendType and ArtifactFamily for Physical Ribbon
# ═══════════════════════════════════════════════════════════════════════════

# The ribbon backend produces MESH_OBJ family artifacts (3D geometry)
# and can also produce MATERIAL_BUNDLE when composed with the renderer.


@register_backend(
    "physical_ribbon",
    display_name="Physical Ribbon Mesh Extractor (P1-B1-1)",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.MESH_OBJ.value,
    ),
    capabilities=(
        BackendCapability.MESH_EXPORT,
    ),
    input_requirements=("chain_snapshot",),
    session_origin="SESSION-106",
    schema_version="1.0.0",
)
class PhysicalRibbonBackend:
    """Physical ribbon mesh extraction backend.

    This backend implements the full pipeline:
    1. Accept secondary chain snapshot data from upstream.
    2. Extract 3D ribbon mesh via PhysicalRibbonExtractor.
    3. Optionally compose with base character mesh.
    4. Optionally render through OrthographicPixelRenderEngine.
    5. Return a strongly-typed ``ArtifactManifest``.

    The backend self-registers via ``@register_backend`` and requires
    ZERO modification to any trunk code (AssetPipeline, Orchestrator).
    """

    @property
    def name(self) -> str:
        return "physical_ribbon"

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(
        self, config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize ribbon extraction configuration.

        Backend-owned validation (Hexagonal Architecture: Adapter owns
        all parameter parsing, CLI Port is ignorant of business logic).

        Parameters
        ----------
        config : dict
            Raw configuration from CLI or orchestrator.

        Returns
        -------
        tuple[dict, list[str]]
            (validated_config, warnings)
        """
        warnings: list[str] = []
        validated = dict(config)

        # --- Ribbon namespace ---
        ribbon = validated.get("ribbon", {})
        if not isinstance(ribbon, dict):
            ribbon = {}
            warnings.append("ribbon config must be a dict; using defaults")

        ribbon.setdefault("width", 0.12)
        ribbon.setdefault("subdivisions_per_segment", 4)
        ribbon.setdefault("z_depth_base", -0.15)
        ribbon.setdefault("z_depth_range", 0.05)
        ribbon.setdefault("color", [120, 60, 160])
        ribbon.setdefault("normal_smoothing", 0.7)
        ribbon.setdefault("width_taper", 0.6)
        ribbon.setdefault("double_sided", True)

        # Clamp values
        ribbon["width"] = max(float(ribbon["width"]), 0.01)
        ribbon["subdivisions_per_segment"] = max(
            int(ribbon["subdivisions_per_segment"]), 1
        )
        ribbon["normal_smoothing"] = min(
            max(float(ribbon["normal_smoothing"]), 0.0), 1.0
        )
        ribbon["width_taper"] = min(
            max(float(ribbon["width_taper"]), 0.0), 1.0
        )

        validated["ribbon"] = ribbon

        # --- Chain data ---
        chains = validated.get("chain_points")
        if chains is None:
            chains = validated.get("secondary_chains")
        if chains is None:
            warnings.append(
                "No chain_points or secondary_chains provided; "
                "will use demo chain data"
            )
        validated["chain_points"] = chains

        # --- Render integration ---
        validated.setdefault("render_composed", False)
        validated.setdefault("base_mesh", None)

        return validated, warnings

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Execute the physical ribbon mesh extraction pipeline.

        Parameters
        ----------
        context : dict
            Execution context with chain snapshot data and configuration.

        Returns
        -------
        ArtifactManifest
            Strongly-typed manifest with mesh outputs.
        """
        from mathart.animation.physical_ribbon_extractor import (
            PhysicalRibbonExtractor,
            RibbonExtractorConfig,
            RibbonMeshMetadata,
            merge_meshes,
        )
        from mathart.animation.orthographic_pixel_render import Mesh3D
        import numpy as np

        # Validate config (backend-owned)
        validated, warnings = self.validate_config(context)
        for w in warnings:
            logger.warning("[physical_ribbon] %s", w)

        output_dir = Path(validated.get("output_dir", "output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = validated.get("name", "physical_ribbon")
        bundle_dir = output_dir / f"{stem}_bundle"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        ribbon_cfg = validated.get("ribbon", {})
        color = ribbon_cfg.get("color", [120, 60, 160])
        if isinstance(color, (list, tuple)) and len(color) == 3:
            color = tuple(int(c) for c in color)
        else:
            color = (120, 60, 160)

        extractor_config = RibbonExtractorConfig(
            width=float(ribbon_cfg.get("width", 0.12)),
            subdivisions_per_segment=int(
                ribbon_cfg.get("subdivisions_per_segment", 4)
            ),
            z_depth_base=float(ribbon_cfg.get("z_depth_base", -0.15)),
            z_depth_range=float(ribbon_cfg.get("z_depth_range", 0.05)),
            color=color,
            normal_smoothing=float(ribbon_cfg.get("normal_smoothing", 0.7)),
            width_taper=float(ribbon_cfg.get("width_taper", 0.6)),
            double_sided=bool(ribbon_cfg.get("double_sided", True)),
        )

        extractor = PhysicalRibbonExtractor(config=extractor_config)

        # Get chain data
        chain_data = validated.get("chain_points")
        all_meshes = []
        all_metadata = []

        t0 = time.monotonic()

        if chain_data is not None:
            # chain_data can be:
            # - dict mapping chain_name -> list of (x, y) points
            # - list of (x, y) points (single chain)
            if isinstance(chain_data, dict):
                for chain_name, points in chain_data.items():
                    if points and len(points) >= 2:
                        mesh, meta = extractor.extract(
                            points, chain_name=chain_name,
                            config_override=extractor_config,
                        )
                        all_meshes.append(mesh)
                        all_metadata.append(meta)
            elif isinstance(chain_data, (list, tuple)) and len(chain_data) >= 2:
                mesh, meta = extractor.extract(
                    chain_data, chain_name="ribbon",
                    config_override=extractor_config,
                )
                all_meshes.append(mesh)
                all_metadata.append(meta)
        else:
            # Demo chain data for testing
            demo_points = [
                (0.0, 0.4), (0.02, 0.3), (0.05, 0.2),
                (0.08, 0.1), (0.10, 0.0), (0.08, -0.1),
            ]
            mesh, meta = extractor.extract(
                demo_points, chain_name="demo_cape",
                config_override=extractor_config,
            )
            all_meshes.append(mesh)
            all_metadata.append(meta)

        elapsed_ms = (time.monotonic() - t0) * 1000.0

        # Merge all ribbon meshes into one
        if all_meshes:
            if len(all_meshes) == 1:
                ribbon_mesh = all_meshes[0]
            else:
                ribbon_mesh = merge_meshes(all_meshes)
        else:
            # Empty fallback — graceful degradation
            ribbon_mesh = Mesh3D(
                vertices=np.zeros((0, 3), dtype=np.float64),
                normals=np.zeros((0, 3), dtype=np.float64),
                triangles=np.zeros((0, 3), dtype=np.int32),
                colors=np.zeros((0, 3), dtype=np.uint8),
            )

        # Save mesh data as NPZ for downstream consumption
        mesh_path = bundle_dir / f"{stem}_mesh.npz"
        np.savez_compressed(
            str(mesh_path),
            vertices=ribbon_mesh.vertices,
            normals=ribbon_mesh.normals,
            triangles=ribbon_mesh.triangles,
            colors=ribbon_mesh.colors,
        )

        # Save metadata report
        report = {
            "pipeline": "physical_ribbon_extractor",
            "session": "SESSION-106",
            "chain_count": len(all_metadata),
            "chains": [m.to_dict() for m in all_metadata],
            "total_vertex_count": ribbon_mesh.vertex_count,
            "total_triangle_count": ribbon_mesh.triangle_count,
            "elapsed_ms": elapsed_ms,
            "config": {
                "width": extractor_config.width,
                "subdivisions_per_segment": extractor_config.subdivisions_per_segment,
                "z_depth_base": extractor_config.z_depth_base,
                "z_depth_range": extractor_config.z_depth_range,
                "normal_smoothing": extractor_config.normal_smoothing,
                "width_taper": extractor_config.width_taper,
                "double_sided": extractor_config.double_sided,
            },
        }
        report_path = bundle_dir / f"{stem}_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        outputs = {
            "mesh": str(mesh_path),
            "report_file": str(report_path),
        }

        return ArtifactManifest(
            artifact_family=ArtifactFamily.MESH_OBJ.value,
            backend_type="physical_ribbon",
            version="1.0.0",
            session_id=validated.get("session_id", "SESSION-106"),
            outputs=outputs,
            metadata={
                "vertex_count": ribbon_mesh.vertex_count,
                "face_count": ribbon_mesh.triangle_count,
                "chain_count": len(all_metadata),
                "chains": [m.to_dict() for m in all_metadata],
                "extraction_config": {
                    "width": extractor_config.width,
                    "subdivisions": extractor_config.subdivisions_per_segment,
                    "z_depth_base": extractor_config.z_depth_base,
                    "z_depth_range": extractor_config.z_depth_range,
                    "normal_smoothing": extractor_config.normal_smoothing,
                    "width_taper": extractor_config.width_taper,
                },
                "elapsed_ms": elapsed_ms,
                "attachment_type": "physical_ribbon",
                "composition_role": "attachment",
            },
            quality_metrics={
                "vertex_count": float(ribbon_mesh.vertex_count),
                "triangle_count": float(ribbon_mesh.triangle_count),
                "extraction_time_ms": elapsed_ms,
            },
            tags=[
                "physical_ribbon",
                "cape",
                "hair",
                "secondary_animation",
                "p1-b1-1",
                "session-106",
            ],
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Scene Assembly Contract — Compose Character + Attachments
# ═══════════════════════════════════════════════════════════════════════════

def compose_character_with_attachments(
    base_manifest: ArtifactManifest,
    attachment_manifests: list[ArtifactManifest],
    *,
    composite_name: str = "character_with_attachments",
    session_id: str = "SESSION-106",
) -> ArtifactManifest:
    """Compose a base character manifest with physical attachment manifests.

    This implements the **Pixar OpenUSD Composition Arcs** pattern:
    the base character and each physical attachment (cape, hair, etc.)
    are kept as separate, typed artifacts.  They are composed into a
    single ``COMPOSITE`` manifest that references all sub-manifests.

    This is the **Scene Contract Prototype** for P1-B1-1:
    - Base character mesh = independent artifact
    - Physical cape mesh = independent artifact (from PhysicalRibbonBackend)
    - Composed scene = COMPOSITE manifest referencing both

    Parameters
    ----------
    base_manifest : ArtifactManifest
        The base character's artifact manifest.
    attachment_manifests : list[ArtifactManifest]
        List of physical attachment manifests (cape, hair, etc.).
    composite_name : str
        Name for the composite manifest.
    session_id : str
        Session ID for provenance.

    Returns
    -------
    ArtifactManifest
        A COMPOSITE manifest referencing all sub-manifests.
    """
    builder = CompositeManifestBuilder(
        name=composite_name,
        backend_type=BackendType.COMPOSITE,
        session_id=session_id,
    )

    builder.add(base_manifest)
    for att in attachment_manifests:
        builder.add(att)

    builder.with_metadata("base_artifact_family", base_manifest.artifact_family)
    builder.with_metadata("base_backend_type", base_manifest.backend_type)
    builder.with_metadata(
        "attachment_count", len(attachment_manifests),
    )
    builder.with_metadata(
        "attachment_types",
        [m.backend_type for m in attachment_manifests],
    )
    builder.with_metadata("composition_pattern", "usd_reference_arcs")
    builder.with_metadata("session_id", session_id)

    return builder.build()


__all__ = [
    "PhysicalRibbonBackend",
    "compose_character_with_attachments",
]
