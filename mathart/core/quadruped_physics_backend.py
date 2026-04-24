"""Quadruped Physics Backend — Multi-Species Skeleton Topology Engine.

SESSION-188: P0-SESSION-188-QUADRUPED-AWAKENING-AND-VAT-BRIDGE

This module is the **Quadruped Physics Engine** that provides a parallel
skeleton solver alongside the default biped engine.  It is registered to
``BackendRegistry`` as an independent topology module, enabling dynamic
switching between biped and quadruped skeleton topologies at the
Orchestrator/Weaver dispatch layer — WITHOUT modifying any biped engine
internals.

Research Foundations
--------------------
1. **AnyTop (Gat et al., 2025, arXiv:2502.17327)**: Diffusion framework
   for arbitrary skeletal structures.  Each joint is embedded independently,
   with topology conditioning via graph characteristics integrated into
   attention maps.  Textual joint descriptions bridge similarly-behaved
   parts across different skeletons.

2. **Spatio-Temporal Motion Retargeting for Quadruped Robots (Yoon et al.,
   2025, IEEE Transactions)**: Two-stage kinematic retargeting with common
   skeleton concept as intermediate latent space.

3. **Dog Code: Human to Quadruped Embodiment (Egan et al., 2024, ACM)**:
   Shared codebooks between human and quadruped skeletons with dynamic
   state switching between bipedal and quadrupedal stances.

4. **Motion Strategy Generation for Quadruped Robots (Zhang et al., 2026,
   Biomimetics)**: Multimodal motion primitives with mapping between
   skeleton and mechanical topology.

Architecture Discipline
-----------------------
- This module is a **standalone Backend** — it does NOT modify any biped
  engine internal logic (隐式切换红线).
- All topology switching happens at the external dispatch layer
  (Weaver / Orchestrator).
- Registered via ``@register_backend`` with ``BackendCapability.ANIMATION_EXPORT``
  and ``BackendCapability.PHYSICS_SIMULATION``.
- Consumes quadruped gait profiles from ``mathart.animation.nsm_gait``
  and terrain IK from ``mathart.animation.terrain_ik_2d``.

Red-Line Enforcement
--------------------
- 🔴 **Implicit Switching Red Line**: ZERO modification to biped physics
  engine internals.  All dispatch at Weaver/Orchestrator level.
- 🔴 **Dimension Alignment Red Line**: Dynamic reshape logic for
  quadruped (4-limb) vs biped (2-limb) joint count mismatch.
- 🔴 **Zero Regression Red Line**: All 94+ existing tests must stay green.
"""
from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════

_QUADRUPED_BACKEND_TYPE = "quadruped_physics"

# Standard quadruped limb names (diagonal-pair trot convention)
QUADRUPED_LIMBS = ("front_left", "front_right", "hind_left", "hind_right")
BIPED_LIMBS = ("l_foot", "r_foot")

# Topology descriptor for the LLM / Orchestrator
SKELETON_TOPOLOGIES = {
    "biped": {
        "limb_count": 2,
        "limbs": list(BIPED_LIMBS),
        "default_gait": "biped_walk",
        "description": "Standard bipedal humanoid skeleton with 2 legs.",
    },
    "quadruped": {
        "limb_count": 4,
        "limbs": list(QUADRUPED_LIMBS),
        "default_gait": "quadruped_trot",
        "description": "Four-legged creature skeleton with diagonal-pair trot gait.",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
#  Quadruped Physics Solver
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class QuadrupedPhysicsResult:
    """Result of quadruped physics simulation."""
    frames: int = 0
    vertices: int = 0
    channels: int = 3
    positions: Optional[np.ndarray] = None
    gait_type: str = "quadruped_trot"
    contact_sequence: List[Dict[str, float]] = field(default_factory=list)
    diagonal_error: float = 0.0
    topology: str = "quadruped"

    def to_dict(self) -> dict:
        return {
            "frames": self.frames,
            "vertices": self.vertices,
            "channels": self.channels,
            "gait_type": self.gait_type,
            "diagonal_error": self.diagonal_error,
            "topology": self.topology,
            "contact_frame_count": len(self.contact_sequence),
        }


def solve_quadruped_physics(
    num_frames: int = 30,
    num_vertices: int = 64,
    channels: int = 3,
    *,
    gait_profile_name: str = "quadruped_trot",
    speed: float = 1.0,
    body_length: float = 1.2,
    step_height: float = 0.14,
    seed: int = 42,
) -> QuadrupedPhysicsResult:
    """Solve quadruped physics to produce real vertex animation positions.

    This function generates physically-grounded quadruped locomotion data
    by consuming the NSM gait engine and projecting through the 2D pipeline.
    The output is a (frames, vertices, channels) numpy array suitable for
    direct consumption by the VAT baking pipeline.

    Parameters
    ----------
    num_frames : int
        Number of animation frames to generate.
    num_vertices : int
        Number of mesh vertices to simulate.
    channels : int
        Position channels (typically 3 for x, y, z).
    gait_profile_name : str
        Name of the gait profile ('quadruped_trot' or 'quadruped_pace').
    speed : float
        Locomotion speed multiplier.
    body_length : float
        Body length for stride calculation.
    step_height : float
        Step height for swing phase.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    QuadrupedPhysicsResult
        Contains the position array and gait metadata.
    """
    from mathart.animation.nsm_gait import (
        QUADRUPED_TROT_PROFILE,
        QUADRUPED_PACE_PROFILE,
        DistilledNeuralStateMachine,
    )

    # Select gait profile
    if gait_profile_name == "quadruped_pace":
        profile = QUADRUPED_PACE_PROFILE
    else:
        profile = QUADRUPED_TROT_PROFILE

    nsm = DistilledNeuralStateMachine()
    rng = np.random.RandomState(seed)

    # Generate gait frames
    contact_sequence: List[Dict[str, float]] = []
    positions = np.zeros((num_frames, num_vertices, channels), dtype=np.float64)

    for frame_idx in range(num_frames):
        phase = frame_idx / max(num_frames - 1, 1)

        # Evaluate NSM gait
        gait_frame = nsm.evaluate(
            profile,
            phase=phase,
            speed=speed,
            base_stride=body_length * 0.35,
            base_height=step_height,
        )

        contact_sequence.append(dict(gait_frame.contact_labels))

        # Map gait frame to vertex positions
        # Each limb group gets a portion of the vertex budget
        limb_names = list(profile.limbs.keys())
        verts_per_limb = max(num_vertices // len(limb_names), 1)

        for limb_idx, limb_name in enumerate(limb_names):
            limb_state = gait_frame.limb_states.get(limb_name)
            contact = gait_frame.contact_labels.get(limb_name, 0.5)

            # Extract dynamics from LimbContactState
            local_limb_phase = limb_state.local_phase if limb_state else 0.0
            target_offset = limb_state.target_offset if limb_state else (0.0, 0.0)
            stride_x = target_offset[0] if target_offset else 0.0
            lift_y = target_offset[1] if target_offset else 0.0

            v_start = limb_idx * verts_per_limb
            v_end = min(v_start + verts_per_limb, num_vertices)

            for v in range(v_start, v_end):
                # Base position with limb offset
                base_x = (limb_idx / len(limb_names)) * body_length
                base_y = 0.0
                base_z = 0.0

                # Apply gait dynamics from NSM
                jitter = rng.uniform(-0.02, 0.02)
                dx = stride_x * speed * 0.5
                dy = step_height * (1.0 - contact) * np.sin(local_limb_phase * np.pi)
                dz = gait_frame.root_bounce * np.sin((phase + jitter) * np.pi * 2)

                # Torso twist contribution
                dx += gait_frame.torso_twist * np.sin(phase * np.pi) * 0.05

                positions[frame_idx, v, 0] = base_x + dx
                if channels > 1:
                    positions[frame_idx, v, 1] = base_y + dy
                if channels > 2:
                    positions[frame_idx, v, 2] = base_z + dz

    # Compute diagonal error (trot quality metric)
    diagonal_error = 0.0
    if len(contact_sequence) > 0 and "front_left" in contact_sequence[0]:
        for cs in contact_sequence:
            fl = cs.get("front_left", 0.5)
            hr = cs.get("hind_right", 0.5)
            fr = cs.get("front_right", 0.5)
            hl = cs.get("hind_left", 0.5)
            diagonal_error = max(diagonal_error, abs(fl - hr), abs(fr - hl))

    return QuadrupedPhysicsResult(
        frames=num_frames,
        vertices=num_vertices,
        channels=channels,
        positions=positions,
        gait_type=gait_profile_name,
        contact_sequence=contact_sequence,
        diagonal_error=diagonal_error,
        topology="quadruped",
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Dynamic Reshape for Cross-Topology VAT Feeding
# ═══════════════════════════════════════════════════════════════════════════

def reshape_positions_for_vat(
    positions: np.ndarray,
    target_vertices: int,
    target_channels: int = 3,
) -> np.ndarray:
    """Dynamically reshape position arrays for VAT consumption.

    This handles the dimension mismatch between quadruped (4-limb, more
    joints) and biped (2-limb, fewer joints) data when feeding into the
    shared VAT baking pipeline.

    [防幻觉桥接红线] Strict Numpy Shape alignment — if dimensions don't
    match, apply dynamic reshape/interpolation rather than crashing.

    Parameters
    ----------
    positions : np.ndarray
        Input position array of shape (frames, vertices, channels).
    target_vertices : int
        Target vertex count for the VAT texture.
    target_channels : int
        Target channel count (default 3).

    Returns
    -------
    np.ndarray
        Reshaped array of shape (frames, target_vertices, target_channels).
    """
    positions = np.asarray(positions, dtype=np.float64)

    if positions.ndim != 3:
        raise ValueError(
            f"Expected 3D array (frames, vertices, channels), got {positions.ndim}D"
        )

    frames, src_verts, src_channels = positions.shape

    # Channel alignment
    if src_channels < target_channels:
        # Pad with zeros
        pad = np.zeros((frames, src_verts, target_channels - src_channels), dtype=np.float64)
        positions = np.concatenate([positions, pad], axis=2)
    elif src_channels > target_channels:
        # Truncate
        positions = positions[:, :, :target_channels]

    # Vertex alignment via linear interpolation
    if src_verts != target_vertices:
        from scipy.interpolate import interp1d

        src_indices = np.linspace(0, 1, src_verts)
        tgt_indices = np.linspace(0, 1, target_vertices)

        reshaped = np.zeros((frames, target_vertices, target_channels), dtype=np.float64)
        for f in range(frames):
            for c in range(target_channels):
                interp_fn = interp1d(
                    src_indices, positions[f, :, c],
                    kind="linear", fill_value="extrapolate",
                )
                reshaped[f, :, c] = interp_fn(tgt_indices)
        positions = reshaped

    return positions


# ═══════════════════════════════════════════════════════════════════════════
#  Skeleton Topology Inference
# ═══════════════════════════════════════════════════════════════════════════

# Keywords that trigger quadruped topology inference
QUADRUPED_KEYWORDS = {
    # Chinese
    "四足", "机械狗", "赛博狗", "机械犬", "赛博犬", "机械兽",
    "四足兽", "四脚", "狗", "犬", "猫", "马", "狼", "虎", "豹",
    "龙", "恐龙", "蜥蜴", "爬行", "奔跑的狗", "机械马",
    "赛博机械狗", "四足生物", "四足动物", "四足机器人",
    # English
    "quadruped", "four-legged", "four legged", "dog", "cat", "horse",
    "wolf", "tiger", "leopard", "dragon", "dinosaur", "lizard",
    "mech dog", "cyber dog", "mechanical beast", "creature",
    "robot dog", "robo dog", "beast", "animal",
}


def infer_skeleton_topology(vibe_text: str) -> str:
    """Infer skeleton topology from natural language vibe text.

    Returns 'quadruped' if any quadruped keyword is detected, otherwise
    returns 'biped' (the default).

    Parameters
    ----------
    vibe_text : str
        The user's natural-language description.

    Returns
    -------
    str
        Either 'biped' or 'quadruped'.
    """
    if not vibe_text:
        return "biped"

    lower = vibe_text.strip().lower()
    for keyword in QUADRUPED_KEYWORDS:
        if keyword in lower:
            logger.info(
                "[QuadrupedBackend] Topology inference: detected '%s' → quadruped",
                keyword,
            )
            return "quadruped"

    return "biped"


# ═══════════════════════════════════════════════════════════════════════════
#  Backend Registration
# ═══════════════════════════════════════════════════════════════════════════

@register_backend(
    _QUADRUPED_BACKEND_TYPE,
    display_name="Quadruped Physics Engine",
    version="1.0.0",
    artifact_families=("quadruped_motion", "vat_bundle"),
    capabilities=(
        BackendCapability.ANIMATION_EXPORT,
        BackendCapability.PHYSICS_SIMULATION,
    ),
)
class QuadrupedPhysicsBackend:
    """Quadruped physics engine backend for the microkernel registry.

    This backend provides four-legged creature physics simulation as a
    parallel solver to the default biped engine.  It is invoked when the
    SemanticOrchestrator infers ``skeleton_topology == "quadruped"`` from
    user intent.

    The backend:
    1. Runs the NSM quadruped gait solver
    2. Generates vertex position arrays
    3. Optionally feeds them to the VAT baking pipeline
    4. Returns an ArtifactManifest with all outputs
    """

    @property
    def name(self) -> str:
        return _QUADRUPED_BACKEND_TYPE

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict | None = None) -> ArtifactManifest:
        """Execute quadruped physics simulation and optional VAT baking.

        Context keys:
        - output_dir (str): Output directory path
        - num_frames (int): Number of frames (default 30)
        - num_vertices (int): Number of vertices (default 64)
        - gait_profile (str): 'quadruped_trot' or 'quadruped_pace'
        - speed (float): Locomotion speed (default 1.0)
        - bake_vat (bool): Whether to also bake VAT textures (default False)
        - verbose (bool): Verbose logging
        """
        context = context or {}
        output_dir = Path(context.get("output_dir", ".")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        verbose = bool(context.get("verbose", False))

        num_frames = int(context.get("num_frames", 30))
        num_vertices = int(context.get("num_vertices", 64))
        gait_profile = context.get("gait_profile", "quadruped_trot")
        speed = float(context.get("speed", 1.0))
        bake_vat = bool(context.get("bake_vat", False))

        # ── UX: Quadruped Engine Banner ──────────────────────────────
        print(
            "\n\033[1;36m[🐾 SESSION-188 四足物理引擎] "
            "正在唤醒四足骨架拓扑解算器...\033[0m"
        )
        print(
            f"\033[90m    ↳ 步态配置: {gait_profile} | "
            f"帧数: {num_frames} | 顶点: {num_vertices} | "
            f"速度: {speed}x\033[0m"
        )

        # ── Solve quadruped physics ──────────────────────────────────
        t_start = _time.perf_counter()
        result = solve_quadruped_physics(
            num_frames=num_frames,
            num_vertices=num_vertices,
            channels=3,
            gait_profile_name=gait_profile,
            speed=speed,
        )
        t_elapsed = _time.perf_counter() - t_start

        print(
            f"\033[1;32m[✅ 四足物理解算完成] "
            f"耗时 {t_elapsed:.2f}s | "
            f"对角误差: {result.diagonal_error:.6f}\033[0m"
        )

        if verbose:
            logger.info(
                "[QuadrupedBackend] Solved %d frames in %.2fs, "
                "diagonal_error=%.6f",
                num_frames, t_elapsed, result.diagonal_error,
            )

        # ── Save positions as NPY ───────────────────────────────────
        npy_path = output_dir / "quadruped_positions.npy"
        np.save(str(npy_path), result.positions)

        # ── Save gait report ────────────────────────────────────────
        report_path = output_dir / "quadruped_physics_report.json"
        report_data = {
            "status": "success",
            "backend": _QUADRUPED_BACKEND_TYPE,
            "session": "SESSION-188",
            "elapsed_s": round(t_elapsed, 3),
            "result": result.to_dict(),
        }
        report_path.write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # ── Optional VAT baking ─────────────────────────────────────
        outputs: dict[str, str] = {
            "positions_npy": str(npy_path),
            "physics_report": str(report_path),
        }

        if bake_vat and result.positions is not None:
            print(
                "\033[1;33m[⚙️ 工业烘焙网关] 正在通过 Catmull-Rom 样条插值，"
                "纯 CPU 解算高精度工业级贴图动作序列...\033[0m"
            )
            try:
                from mathart.animation.high_precision_vat import (
                    HighPrecisionVATConfig,
                    bake_high_precision_vat,
                )

                # Dynamic reshape for VAT consumption
                vat_positions = reshape_positions_for_vat(
                    result.positions,
                    target_vertices=num_vertices,
                    target_channels=3,
                )

                vat_config = HighPrecisionVATConfig(
                    asset_name=f"quadruped_{gait_profile}",
                    fps=int(context.get("fps", 24)),
                    export_hdr=True,
                    export_npy=True,
                    export_hilo_png=True,
                    include_preview=True,
                )

                vat_result = bake_high_precision_vat(
                    positions=vat_positions,
                    output_dir=output_dir / "vat",
                    config=vat_config,
                )

                if vat_result.npy_path:
                    outputs["vat_npy"] = str(vat_result.npy_path)
                if vat_result.hdr_path:
                    outputs["vat_hdr"] = str(vat_result.hdr_path)
                if vat_result.manifest_path:
                    outputs["vat_manifest"] = str(vat_result.manifest_path)

                print(
                    "\033[1;32m[✅ 四足 VAT 烘焙完成] "
                    "高精度浮点纹理已落盘\033[0m"
                )

            except Exception as vat_err:
                logger.warning(
                    "[QuadrupedBackend] VAT baking failed: %s", vat_err
                )
                print(
                    f"\033[1;33m[⚠️ VAT 烘焙降级] {vat_err} "
                    f"(物理数据已保存，VAT 可后续补烘)\033[0m"
                )

        # ── Build ArtifactManifest ───────────────────────────────────
        metadata: dict[str, Any] = {
            "frame_count": result.frames,
            "vertex_count": result.vertices,
            "channels": result.channels,
            "gait_type": result.gait_type,
            "diagonal_error": result.diagonal_error,
            "topology": result.topology,
            "speed": speed,
            "bake_elapsed_s": round(t_elapsed, 3),
            "backend_type": _QUADRUPED_BACKEND_TYPE,
            "session_origin": "SESSION-188",
        }

        manifest = ArtifactManifest(
            artifact_family="quadruped_motion",
            backend_type=_QUADRUPED_BACKEND_TYPE,
            outputs=outputs,
            metadata=metadata,
        )

        return manifest


# ═══════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "QuadrupedPhysicsBackend",
    "QuadrupedPhysicsResult",
    "QUADRUPED_LIMBS",
    "BIPED_LIMBS",
    "SKELETON_TOPOLOGIES",
    "QUADRUPED_KEYWORDS",
    "infer_skeleton_topology",
    "solve_quadruped_physics",
    "reshape_positions_for_vat",
]
