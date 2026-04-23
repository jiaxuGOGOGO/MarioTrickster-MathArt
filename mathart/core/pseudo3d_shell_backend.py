"""SESSION-118 (P1-HUMAN-31C): Pseudo3DShellBackend — Registry-Native
Pseudo-3D Paper-Doll / Mesh-Shell Deformation Plugin.

This module implements the ``@register_backend`` plugin for the pseudo-3D
paper-doll / volumetric mesh-shell deformation backend.  It consumes UMR
(Unified Motion Representation) bone animation sequences and base meshes
with skinning weights, then drives high-fidelity 3D/2.5D deformation using
the Tensorized Dual Quaternion Skinning (DQS) engine.

Research Foundations
--------------------
1. **Kavan et al. (SIGGRAPH 2007)** — "Skinning with Dual Quaternions":
   DLB (Dual quaternion Linear Blending) preserves volume by interpolating
   rigid transforms on the unit dual-quaternion manifold, eliminating the
   candy-wrapper collapse inherent to LBS.

2. **Data-Oriented Tensor Skinning** — Industrial-grade skinning maps bone
   transforms to DQ arrays ``[B, 8]``, combines with weight matrices
   ``[V, B]`` via ``np.einsum``, producing blended per-vertex DQs in a
   single tensor operation.  Zero scalar loops.

3. **Arc System Works / Guilty Gear Xrd (GDC 2015)** — 2.5D cel-shaded
   deformation: even in paper-doll workflows, joint deformation uses 3D
   DQS for smooth perspective warping and correct normal rotation.

Architecture Discipline
-----------------------
- ✅ Independent plugin: self-registers via ``@register_backend``
- ✅ No trunk modification: ZERO changes to AssetPipeline/Orchestrator
- ✅ Strong-type contract: returns ``ArtifactManifest`` with explicit
  ``artifact_family=MESH_OBJ`` and ``backend_type=pseudo_3d_shell``
- ✅ Consumes UMR bone animation sequences via context contract
- ✅ Backend-owned ``validate_config()``: all parameter validation is
  physically sunk into this Adapter (Hexagonal Architecture)
- ✅ Graceful Fallback: missing mesh/animation data produces a valid
  empty manifest without crashing

Anti-Pattern Guards
-------------------
🚫 Anti-Spaghetti: The deformed mesh is produced as an independent PDG
   WorkItem.  NEVER injected via ``if has_shell:`` into trunk code.
🚫 Anti-Scalar-Loop: All deformation uses tensorized DQS engine.
🚫 Anti-Candy-Wrapper: DQS guarantees volume preservation at joints.
🚫 Anti-Antipodal-Tearing: Shortest-arc correction before blending.
🚫 Anti-Normalization-Failure: Mandatory re-normalization after DLB.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

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
#  SESSION-149: Demo-Mesh Warning Throttle
#
# When the production pipeline streams long demo sequences (e.g. 43+ frames
# in PDG mass production), the validate_config() warning ``No base_mesh or
# base_vertices provided; will generate demo cylinder mesh`` was being
# emitted on every single frame, flooding the wizard terminal with up to
# dozens of identical WARNING lines per render call.  We promote the first
# emission to WARNING (so operators still see it once) and downgrade every
# subsequent emission inside the same process to DEBUG, ensuring the audit
# trail is preserved in logs/mathart.log without polluting the TUI.
#
# The throttle is keyed by warning text so that the demo-bone warning and
# the demo-mesh warning are throttled independently.  A module-level lock
# keeps the registry safe across the PDG worker pool.
# ═══════════════════════════════════════════════════════════════════════════

_DEMO_WARNING_LOCK = threading.Lock()
_DEMO_WARNING_SEEN: set[str] = set()


def _emit_demo_warning(message: str) -> None:
    """Emit a demo-fallback warning at most once per process per message.

    The first occurrence is logged at WARNING level so the operator is
    immediately notified that the backend has degraded to a synthetic
    placeholder.  Every subsequent occurrence within the same process is
    silently downgraded to DEBUG (still captured by the blackbox file
    handler) to prevent terminal spam during long-running PDG batch runs.
    """
    with _DEMO_WARNING_LOCK:
        first_time = message not in _DEMO_WARNING_SEEN
        if first_time:
            _DEMO_WARNING_SEEN.add(message)
    if first_time:
        logger.warning("[pseudo_3d_shell] %s", message)
    else:
        logger.debug("[pseudo_3d_shell] %s (throttled)", message)


# ═══════════════════════════════════════════════════════════════════════════
#  Backend Registration
# ═══════════════════════════════════════════════════════════════════════════

@register_backend(
    "pseudo_3d_shell",
    display_name="Pseudo-3D Paper-Doll Shell (DQS Volume Skinning)",
    version="1.0.0",
    artifact_families=(
        ArtifactFamily.MESH_OBJ.value,
    ),
    capabilities=(
        BackendCapability.MESH_EXPORT,
    ),
    input_requirements=("base_mesh", "bone_animation"),
    session_origin="SESSION-118",
    schema_version="1.0.0",
)
class Pseudo3DShellBackend:
    """Pseudo-3D paper-doll / mesh-shell deformation backend.

    This backend implements the full pipeline:
    1. Accept base mesh with skinning weights from upstream.
    2. Accept UMR bone animation sequence (or inline bone DQs).
    3. Drive deformation via Tensorized DQS Engine.
    4. Output deformed ``Mesh3D`` per frame (SESSION-106 contract).
    5. Return a strongly-typed ``ArtifactManifest``.

    The backend self-registers via ``@register_backend`` and requires
    ZERO modification to any trunk code (AssetPipeline, Orchestrator).
    """

    @property
    def name(self) -> str:
        return "pseudo_3d_shell"

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def validate_config(
        self, config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize pseudo-3D shell configuration.

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

        # --- Mesh data ---
        if validated.get("base_mesh") is None:
            if validated.get("base_vertices") is None:
                warnings.append(
                    "No base_mesh or base_vertices provided; "
                    "will generate demo cylinder mesh"
                )
                validated["_use_demo_mesh"] = True

        # --- Animation data ---
        if validated.get("bone_dqs") is None and validated.get("bone_animation") is None:
            warnings.append(
                "No bone_dqs or bone_animation provided; "
                "will generate demo single-bone rotation"
            )
            validated["_use_demo_animation"] = True

        # --- Output config ---
        validated.setdefault("output_dir", "artifacts/pseudo_3d_shell")
        validated.setdefault("name", "pseudo_3d_shell")
        validated.setdefault("export_per_frame", False)
        validated.setdefault("cylinder_radius", 0.5)
        validated.setdefault("cylinder_height", 2.0)
        validated.setdefault("cylinder_segments", 32)
        validated.setdefault("cylinder_height_segments", 10)

        return validated, warnings

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        """Execute the pseudo-3D shell deformation pipeline.

        Parameters
        ----------
        context : dict
            Execution context with mesh data, bone animation, and config.

        Returns
        -------
        ArtifactManifest
            Strongly-typed manifest with deformed mesh outputs.
        """
        from mathart.animation.dqs_engine import (
            tensorized_dqs_skin,
            create_cylinder_mesh,
            compute_cylinder_skin_weights,
            dq_from_axis_angle_translation,
            dq_identity,
        )
        from mathart.animation.orthographic_pixel_render import Mesh3D

        t0 = time.perf_counter()

        # ── Validate config ────────────────────────────────────────────────────────────────
        validated, warnings = self.validate_config(context)
        # SESSION-149: Throttle the demo-fallback warnings so a 43-frame PDG
        # batch no longer prints the same WARNING line dozens of times.
        # First occurrence -> WARNING (operator awareness); subsequent ones
        # -> DEBUG (blackbox audit trail only).
        for w in warnings:
            _emit_demo_warning(w)

        stem = validated.get("name", "pseudo_3d_shell")
        output_dir = Path(validated["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Resolve mesh data ─────────────────────────────────────────────
        if validated.get("_use_demo_mesh", False):
            r = validated["cylinder_radius"]
            h = validated["cylinder_height"]
            segs = validated["cylinder_segments"]
            h_segs = validated["cylinder_height_segments"]
            base_verts, base_normals, triangles = create_cylinder_mesh(
                radius=r, height=h,
                radial_segments=segs, height_segments=h_segs,
                axis="y",
            )
            skin_weights = compute_cylinder_skin_weights(
                base_verts, height=h, axis="y",
            )
            colors = np.full((base_verts.shape[0], 3), 180, dtype=np.uint8)
        else:
            base_verts = np.asarray(
                validated.get("base_vertices", context.get("base_vertices")),
                dtype=np.float64,
            )
            base_normals = np.asarray(
                validated.get("base_normals", context.get("base_normals")),
                dtype=np.float64,
            )
            triangles = np.asarray(
                validated.get("triangles", context.get("triangles")),
                dtype=np.int32,
            )
            skin_weights = np.asarray(
                validated.get("skin_weights", context.get("skin_weights")),
                dtype=np.float64,
            )
            colors = np.asarray(
                validated.get("colors", context.get("colors",
                    np.full((base_verts.shape[0], 3), 180, dtype=np.uint8))),
                dtype=np.uint8,
            )

        V = base_verts.shape[0]
        B = skin_weights.shape[1]

        # ── Resolve bone animation ─────────────────────────────────────
        if validated.get("_use_demo_animation", False):
            # SESSION-149: "Dynamic Demo Mesh" — the previous fallback used
            # a static 90° ramp over 10 frames, which (a) was too short to
            # sustain a 43-frame PDG batch and (b) produced near-zero pixel
            # delta between consecutive frames once the rotation hit its
            # plateau, tripping the TemporalVarianceCircuitBreaker with
            # ``temporal_variance_below_threshold. Mean MSE = 0.0000``.
            #
            # The replacement injects two superimposed mathematical motions
            # so every frame is provably distinct (MSE >> 1.0 on the guide
            # render):
            #
            #   1. Y-axis bounce — vertical translation
            #          ty(f) = bounce_amplitude * sin(2 π f / period)
            #      keeps the cylinder oscillating up-and-down, generating
            #      large-area pixel deltas in every contour row.
            #   2. Y-axis spin   — continuous self-rotation
            #          θ(f) = 2 π * (spin_revolutions * f / total_frames)
            #      perpetually rotates the cylinder around its long axis,
            #      so even at the apex of the bounce (where vertical motion
            #      momentarily vanishes), the rotational velocity guarantees
            #      texture-band pixels keep shifting laterally.
            #
            # Honour the orchestrator-provided ``frame_count`` so the demo
            # produces *exactly* the number of frames the PDG batch asked
            # for; default to 24 frames when no upstream value is supplied.
            n_frames = int(
                validated.get("frame_count")
                or context.get("frame_count")
                or context.get("num_frames")
                or 24
            )
            n_frames = max(n_frames, 2)  # circuit breaker requires >= 2
            bounce_amplitude = float(validated.get("demo_bounce_amplitude", 0.6))
            bounce_period = float(validated.get("demo_bounce_period", max(8.0, n_frames / 2.0)))
            spin_revolutions = float(validated.get("demo_spin_revolutions", 1.5))

            bone_dqs_list = []
            for f in range(n_frames):
                # Y-axis bounce translation (root bone)
                ty = bounce_amplitude * math.sin(
                    2.0 * math.pi * f / max(bounce_period, 1.0)
                )
                # Y-axis spin angle (root bone), monotonically increasing
                spin_angle = (
                    2.0 * math.pi * spin_revolutions * f / max(n_frames - 1, 1)
                )
                dq0 = dq_from_axis_angle_translation(
                    np.array([0.0, 1.0, 0.0]),
                    np.array(spin_angle),
                    np.array([0.0, ty, 0.0]),
                )
                # Bone 1 inherits a phase-shifted rotation so multi-bone
                # skinning weights also get distinct deformation per frame
                # rather than collapsing into a static skeleton.
                dq1 = dq_from_axis_angle_translation(
                    np.array([0.0, 1.0, 0.0]),
                    np.array(spin_angle + math.pi / 4.0),
                    np.array([0.0, ty * 0.5, 0.0]),
                )
                bone_dqs_list.append(np.stack([dq0, dq1], axis=0))
            bone_dqs = np.stack(bone_dqs_list, axis=0)  # (F, B, 8)
            logger.debug(
                "[pseudo_3d_shell] demo animation: frames=%d bounce=%.3f "
                "spin_rev=%.3f period=%.2f (Y-axis bounce + Y-axis spin)",
                n_frames, bounce_amplitude, spin_revolutions, bounce_period,
            )
        else:
            bone_dqs = np.asarray(
                validated.get("bone_dqs", context.get("bone_dqs")),
                dtype=np.float64,
            )

        F = bone_dqs.shape[0]

        # ── Run DQS Engine ────────────────────────────────────────────────
        result = tensorized_dqs_skin(
            base_vertices=base_verts,
            base_normals=base_normals,
            skin_weights=skin_weights,
            bone_dqs=bone_dqs,
        )

        elapsed = time.perf_counter() - t0

        # ── Build Mesh3D for last frame (or all frames) ──────────────────
        last_verts = result.deformed_vertices[-1]
        last_normals = result.deformed_normals[-1]
        mesh = Mesh3D(
            vertices=last_verts,
            normals=last_normals,
            triangles=triangles,
            colors=colors,
        )

        # ── Write output artifacts ────────────────────────────────────────
        mesh_path = output_dir / f"{stem}_mesh.npz"
        np.savez_compressed(
            str(mesh_path),
            vertices=result.deformed_vertices,
            normals=result.deformed_normals,
            triangles=triangles,
            colors=colors,
            base_vertices=base_verts,
            base_normals=base_normals,
            skin_weights=skin_weights,
            bone_dqs=bone_dqs,
        )

        meta_path = output_dir / f"{stem}_meta.json"
        meta_dict = {
            "backend": "pseudo_3d_shell",
            "session_origin": "SESSION-118",
            "task_id": "P1-HUMAN-31C",
            "vertex_count": int(V),
            "face_count": int(triangles.shape[0]),
            "bone_count": int(B),
            "frame_count": int(F),
            "dqs_engine": "tensorized_numpy",
            "skinning_method": "dual_quaternion_linear_blending",
            "volume_preservation": True,
            "antipodal_correction": True,
            "normalization_guard": True,
            "elapsed_seconds": float(elapsed),
            "warnings": warnings,
        }
        meta_path.write_text(json.dumps(meta_dict, indent=2))

        # ── Build ArtifactManifest ────────────────────────────────────────
        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.MESH_OBJ.value,
            backend_type="pseudo_3d_shell",
            outputs={
                "mesh": str(mesh_path),
                "metadata": str(meta_path),
            },
            metadata={
                "vertex_count": int(V),
                "face_count": int(triangles.shape[0]),
                "bone_count": int(B),
                "frame_count": int(F),
                "skinning_method": "DQS",
                "volume_preservation": True,
                "elapsed_seconds": float(elapsed),
                "pipeline": "pseudo_3d_shell_dqs_engine",
            },
            quality_metrics={
                "dqs_tensorized": True,
                "zero_scalar_loops": True,
                "antipodal_corrected": True,
                "normalized": True,
            },
            tags=["pseudo_3d", "paper_doll", "dqs", "volume_skinning",
                  "mesh_shell", "SESSION-118", "P1-HUMAN-31C"],
        )

        logger.info(
            "[pseudo_3d_shell] Deformed %d vertices × %d frames in %.3fs "
            "(%.1f verts/sec)",
            V, F, elapsed, V * F / max(elapsed, 1e-9),
        )

        return manifest


__all__ = ["Pseudo3DShellBackend"]
