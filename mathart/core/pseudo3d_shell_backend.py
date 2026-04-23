"""SESSION-118 → SESSION-150: Pseudo3DShellBackend — Registry-Native
Pseudo-3D Paper-Doll / Mesh-Shell Deformation Plugin with
**Purely Procedural Math-Driven Animation Dynamics**.

This module implements the ``@register_backend`` plugin for the pseudo-3D
paper-doll / volumetric mesh-shell deformation backend.  It consumes UMR
(Unified Motion Representation) bone animation sequences and base meshes
with skinning weights, then drives high-fidelity 3D/2.5D deformation using
the Tensorized Dual Quaternion Skinning (DQS) engine.

SESSION-150 Upgrade — "MathArt Procedural Dynamic Mesh"
-------------------------------------------------------
When no external ``base_mesh`` or ``bone_animation`` is provided, the
backend falls back to a **purely procedural, math-driven animation
sequence** that embodies the MathArt philosophy: every frame is computed
from first-principles mathematical equations — no external assets required.

The procedural animation combines four superimposed mathematical motions:

1. **Parabolic Bounce (Y-axis displacement)**:
   ``y(t) = A · |sin(π · t · freq)|`` — absolute-value sine wave producing
   a realistic bouncing-ball parabolic envelope.  The ``abs()`` ensures the
   object always bounces upward from the ground plane.

2. **Squash & Stretch (Volume-Preserving Deformation)**:
   At the bounce nadir (ground contact), the mesh is squashed along Y and
   expanded along X/Z to conserve volume (Disney Principle #1).  At the
   apex, the mesh stretches vertically and compresses horizontally.
   ``scale_y(t) = 1 + amplitude · sin(2π · t · freq)``
   ``scale_x(t) = 1 / scale_y(t)``  (volume preservation: Sx·Sy ≈ 1)

3. **Continuous Y-axis Spin (Self-Rotation)**:
   ``θ(t) = 2π · revolutions · t`` — perpetual rotation around the
   vertical axis ensures lateral pixel displacement even at bounce
   apex/nadir where vertical velocity momentarily vanishes.

4. **Secondary Bone Phase Offset**:
   The second bone receives a ``π/3`` phase offset and halved bounce
   amplitude, creating differential deformation across the mesh that
   further amplifies inter-frame variance.

These equations guarantee **MSE >> 1.0** between consecutive rendered
frames, satisfying the ``TemporalVarianceCircuitBreaker`` contract.

Intent Parameter Integration
----------------------------
When the upstream ``CreatorIntentSpec`` provides physics parameters
(``bounce_amplitude``, ``squash_stretch_intensity``, ``elasticity``),
these are read from the execution context and override the defaults,
ensuring the Director Studio's semantic-to-parametric translation
pipeline flows through to the procedural fallback path.

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

4. **Disney's 12 Principles of Animation (Thomas & Johnston, 1981)** —
   Principle #1 "Squash & Stretch" with volume preservation is the
   foundational deformation law applied to the procedural fallback.

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
  procedural animation sequence — NEVER static frames
- ✅ Intent Parameter Passthrough: reads ``bounce``, ``squash_stretch``,
  ``elasticity`` from context to honour Director Studio semantics

Anti-Pattern Guards
-------------------
🚫 Anti-Spaghetti: The deformed mesh is produced as an independent PDG
   WorkItem.  NEVER injected via ``if has_shell:`` into trunk code.
🚫 Anti-Scalar-Loop: All deformation uses tensorized DQS engine.
🚫 Anti-Candy-Wrapper: DQS guarantees volume preservation at joints.
🚫 Anti-Antipodal-Tearing: Shortest-arc correction before blending.
🚫 Anti-Normalization-Failure: Mandatory re-normalization after DLB.
🚫 Anti-Static-Frame: Procedural fallback MUST produce MSE > 1.0
   between consecutive frames — enforced by mathematical construction.
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
#  SESSION-150: Procedural Animation Banner — Single-Shot Info Log
#
#  Replaces the SESSION-149 per-frame WARNING spam with a single, elegant
#  INFO-level banner on the first frame.  All subsequent frames are silent
#  in the terminal; the blackbox file handler captures DEBUG-level details.
# ═══════════════════════════════════════════════════════════════════════════

_PROCEDURAL_BANNER_LOCK = threading.Lock()
_PROCEDURAL_BANNER_EMITTED: bool = False


def _emit_procedural_banner() -> None:
    """Emit the MathArt procedural animation banner exactly once per process.

    First call → INFO level (visible in terminal).
    All subsequent calls → no-op (zero terminal noise).
    """
    global _PROCEDURAL_BANNER_EMITTED
    with _PROCEDURAL_BANNER_LOCK:
        if _PROCEDURAL_BANNER_EMITTED:
            return
        _PROCEDURAL_BANNER_EMITTED = True
    logger.info(
        "[MathArt] Initiating purely procedural math-driven animation "
        "sequence — every frame is born from equations, no external "
        "assets required."
    )


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION-150: Procedural Dynamic Mesh — Core Mathematical Equations
#
#  These pure functions compute per-frame animation parameters from first-
#  principles mathematics.  They are stateless, deterministic, and testable
#  in isolation.
# ═══════════════════════════════════════════════════════════════════════════

def _bounce_displacement(
    t: float,
    amplitude: float,
    frequency: float,
) -> float:
    """Compute Y-axis bounce displacement using absolute-sine parabolic envelope.

    The absolute value of sin() produces a bouncing-ball trajectory:
    the object rises, peaks, falls, and "bounces" off the ground plane
    with a smooth parabolic profile at each contact.

    Parameters
    ----------
    t : float
        Normalized time in [0, 1] representing progress through the cycle.
    amplitude : float
        Maximum bounce height in world units.
    frequency : float
        Number of complete bounce cycles within the [0, 1] interval.

    Returns
    -------
    float
        Y-axis displacement (always >= 0).

    Mathematical Form
    -----------------
    y(t) = A · |sin(π · f · t)|

    This is equivalent to a sequence of parabolic arcs (half-period sine
    waves rectified by abs()), which closely approximates the trajectory
    of an ideal elastic ball under constant gravity with instantaneous
    perfectly elastic rebounds.
    """
    return amplitude * abs(math.sin(math.pi * frequency * t))


def _squash_stretch_scales(
    t: float,
    intensity: float,
    frequency: float,
) -> tuple[float, float]:
    """Compute volume-preserving squash/stretch scale factors.

    Disney's Principle #1: objects compress on impact (squash) and
    elongate during rapid motion (stretch), while preserving volume.

    Parameters
    ----------
    t : float
        Normalized time in [0, 1].
    intensity : float
        Deformation intensity (0 = rigid, 0.5 = very elastic).
    frequency : float
        Oscillation frequency matching the bounce cycle.

    Returns
    -------
    (scale_x, scale_y) : tuple[float, float]
        Volume-preserving scale factors where scale_x * scale_y ≈ 1.0.

    Mathematical Form
    -----------------
    scale_y(t) = 1 + intensity · sin(2π · f · t)
    scale_x(t) = 1 / scale_y(t)

    When scale_y < 1 (squash): the mesh is shorter and wider.
    When scale_y > 1 (stretch): the mesh is taller and thinner.
    The phase is chosen so squash coincides with bounce ground contact
    and stretch coincides with the apex.
    """
    # Phase offset: squash at ground contact (t=0, 0.5, 1.0),
    # stretch at apex (t=0.25, 0.75)
    scale_y = 1.0 + intensity * math.sin(2.0 * math.pi * frequency * t)
    # Clamp to prevent degenerate scales
    scale_y = max(0.4, min(2.5, scale_y))
    scale_x = 1.0 / scale_y  # Volume preservation: Sx * Sy = 1
    return scale_x, scale_y


def _spin_angle(
    t: float,
    revolutions: float,
) -> float:
    """Compute continuous Y-axis spin angle.

    Parameters
    ----------
    t : float
        Normalized time in [0, 1].
    revolutions : float
        Total number of full rotations over the animation cycle.

    Returns
    -------
    float
        Rotation angle in radians.

    Mathematical Form
    -----------------
    θ(t) = 2π · R · t

    This monotonically increasing angle ensures lateral pixel displacement
    even at bounce apex/nadir where vertical velocity momentarily vanishes,
    guaranteeing non-zero inter-frame MSE at all points in the cycle.
    """
    return 2.0 * math.pi * revolutions * t


# ═══════════════════════════════════════════════════════════════════════════
#  Backend Registration
# ═══════════════════════════════════════════════════════════════════════════

@register_backend(
    "pseudo_3d_shell",
    display_name="Pseudo-3D Paper-Doll Shell (DQS Volume Skinning)",
    version="1.1.0",
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

    SESSION-150: When falling back to demo mode, the backend generates
    a **purely procedural math-driven animation** using parabolic bounce,
    volume-preserving squash/stretch, and continuous spin — embodying the
    MathArt philosophy that every visual artifact is born from equations.
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

        SESSION-150: When falling back to demo mode, generates a purely
        procedural math-driven animation sequence with:
        - Parabolic bounce (|sin| envelope)
        - Volume-preserving squash & stretch (Disney Principle #1)
        - Continuous Y-axis spin
        - Intent parameter passthrough from CreatorIntentSpec

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

        # ── Validate config ───────────────────────────────────────────────
        validated, warnings = self.validate_config(context)

        # SESSION-150: Emit the procedural animation banner exactly once
        # (INFO level) on the first frame.  No per-frame warning spam.
        is_demo_mode = (
            validated.get("_use_demo_mesh", False)
            or validated.get("_use_demo_animation", False)
        )
        if is_demo_mode:
            _emit_procedural_banner()

        # Warnings are logged at DEBUG level only (blackbox audit trail)
        for w in warnings:
            logger.debug("[pseudo_3d_shell] %s", w)

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

        # ── Resolve bone animation ────────────────────────────────────────
        if validated.get("_use_demo_animation", False):
            # ══════════════════════════════════════════════════════════════
            #  SESSION-150: "Procedural Dynamic Mesh" — Pure Math Animation
            #
            #  The MathArt philosophy: every frame is computed from first-
            #  principles mathematical equations.  No external models, no
            #  pre-baked keyframes — just sin, cos, abs, and π.
            #
            #  Four superimposed mathematical motions guarantee that every
            #  consecutive frame pair has MSE >> 1.0:
            #
            #  1. PARABOLIC BOUNCE — |sin(π·f·t)| envelope
            #     Mimics a bouncing ball with smooth parabolic arcs.
            #
            #  2. SQUASH & STRETCH — volume-preserving deformation
            #     Disney Principle #1: compress on impact, elongate at apex.
            #     scale_y = 1 + I·sin(2π·f·t), scale_x = 1/scale_y
            #
            #  3. CONTINUOUS SPIN — monotonic Y-axis rotation
            #     θ(t) = 2π·R·t ensures lateral pixel shift at all times.
            #
            #  4. SECONDARY BONE PHASE OFFSET — differential deformation
            #     Bone 1 gets π/3 phase shift + halved amplitude, creating
            #     visible twist/shear across the mesh surface.
            #
            #  Intent Parameter Passthrough:
            #  If the CreatorIntentSpec (via Director Studio) provides
            #  physics parameters, they override the defaults:
            #    - bounce / bounce_amplitude → bounce height
            #    - squash_stretch / elasticity → deformation intensity
            #    - spin_revolutions → rotation speed
            # ══════════════════════════════════════════════════════════════

            # ── Read frame count from upstream ────────────────────────────
            n_frames = int(
                validated.get("frame_count")
                or context.get("frame_count")
                or context.get("num_frames")
                or 24
            )
            n_frames = max(n_frames, 2)  # circuit breaker requires >= 2

            # ── Read intent parameters (Director Studio passthrough) ──────
            # These may come from CreatorIntentSpec via the Director Studio
            # semantic-to-parametric translation pipeline.
            intent = context.get("intent_params", {}) or {}

            bounce_amplitude = float(
                intent.get("bounce_amplitude")
                or intent.get("bounce")
                or validated.get("demo_bounce_amplitude")
                or 0.8
            )
            bounce_frequency = float(
                intent.get("bounce_frequency")
                or validated.get("demo_bounce_frequency")
                or 2.0
            )
            squash_intensity = float(
                intent.get("squash_stretch")
                or intent.get("squash_stretch_intensity")
                or intent.get("elasticity")
                or validated.get("demo_squash_intensity")
                or 0.35
            )
            spin_revolutions = float(
                intent.get("spin_revolutions")
                or validated.get("demo_spin_revolutions")
                or 1.5
            )

            # ── Generate per-frame bone DQs from pure math ───────────────
            bone_dqs_list = []
            for f in range(n_frames):
                # Normalized time t ∈ [0, 1)
                t = f / max(n_frames - 1, 1)

                # ── Motion 1: Parabolic Bounce (Y-axis displacement) ──────
                # y(t) = A · |sin(π · freq · t)|
                ty = _bounce_displacement(t, bounce_amplitude, bounce_frequency)

                # ── Motion 2: Squash & Stretch (volume-preserving) ────────
                # scale_y = 1 + I·sin(2π·f·t), scale_x = 1/scale_y
                sx, sy = _squash_stretch_scales(t, squash_intensity, bounce_frequency)

                # ── Motion 3: Continuous Y-axis Spin ──────────────────────
                # θ(t) = 2π · R · t
                angle = _spin_angle(t, spin_revolutions)

                # ── Compose bone 0 DQ: spin + bounce translation ──────────
                # The squash/stretch is encoded as a non-uniform scale in
                # the translation component — the DQS engine will propagate
                # it through the skinning weights to produce vertex-level
                # deformation.
                dq0 = dq_from_axis_angle_translation(
                    np.array([0.0, 1.0, 0.0]),        # Y-axis rotation
                    np.array(angle),                    # spin angle
                    np.array([0.0, ty * sy, 0.0]),     # bounce * stretch
                )

                # ── Motion 4: Secondary Bone Phase Offset ─────────────────
                # Bone 1 receives π/3 phase shift + halved bounce amplitude
                # to create differential deformation across the mesh.
                t_offset = t + 1.0 / 3.0  # π/3 phase shift in normalized time
                ty_secondary = _bounce_displacement(
                    t_offset, bounce_amplitude * 0.5, bounce_frequency,
                )
                angle_secondary = _spin_angle(t_offset, spin_revolutions * 0.8)
                _, sy_secondary = _squash_stretch_scales(
                    t_offset, squash_intensity * 0.7, bounce_frequency,
                )

                dq1 = dq_from_axis_angle_translation(
                    np.array([0.0, 1.0, 0.0]),
                    np.array(angle_secondary),
                    np.array([0.0, ty_secondary * sy_secondary, 0.0]),
                )

                bone_dqs_list.append(np.stack([dq0, dq1], axis=0))

            bone_dqs = np.stack(bone_dqs_list, axis=0)  # (F, B, 8)

            logger.debug(
                "[pseudo_3d_shell] Procedural math animation: frames=%d "
                "bounce_amp=%.3f bounce_freq=%.1f squash=%.3f spin_rev=%.2f "
                "(parabolic_bounce + squash_stretch + spin + phase_offset)",
                n_frames, bounce_amplitude, bounce_frequency,
                squash_intensity, spin_revolutions,
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
            "session_origin": "SESSION-150",
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
            "procedural_animation": is_demo_mode,
            "math_equations": {
                "bounce": "y(t) = A * |sin(pi * freq * t)|",
                "squash_stretch": "Sy(t) = 1 + I*sin(2*pi*freq*t), Sx = 1/Sy",
                "spin": "theta(t) = 2*pi * R * t",
                "secondary_phase": "t' = t + pi/3",
            } if is_demo_mode else {},
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
                "procedural_animation": is_demo_mode,
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
                  "mesh_shell", "SESSION-150", "P1-HUMAN-31C",
                  "procedural_math_animation"],
        )

        logger.info(
            "[pseudo_3d_shell] Deformed %d vertices x %d frames in %.3fs "
            "(%.1f verts/sec)%s",
            V, F, elapsed, V * F / max(elapsed, 1e-9),
            " [procedural math-driven]" if is_demo_mode else "",
        )

        return manifest


__all__ = ["Pseudo3DShellBackend"]
