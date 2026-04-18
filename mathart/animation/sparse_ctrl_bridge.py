"""SESSION-065 — SparseCtrl Integration Bridge for Anti-Flicker Video Generation.

Research-to-code implementation distilled from:
    Yuwei Guo et al., "SparseCtrl: Adding Sparse Controls to Text-to-Video
    Diffusion Models" (arXiv:2311.16933, 2023)

And complementary research:
    - Ondřej Jamriška et al., "Stylizing Video by Example" (SIGGRAPH 2019)
    - AnimateDiff (Guo et al., 2023) — temporal attention for video diffusion
    - OnlyFlow (Koroglu et al., CVPR 2025W) — optical flow conditioning
    - MotionPrompt (Nam et al., CVPR 2025) — flow-guided prompt optimization

Core Insight (Guo et al. 2023):
    SparseCtrl enables conditioning video diffusion models on sparse control
    signals (e.g., only keyframes 0, 10, 20 out of 30 total frames). A
    lightweight condition encoder propagates information from sparse frames
    to all frames via temporal attention, eliminating the need for dense
    per-frame conditioning while maintaining temporal consistency.

    This is critical for our pipeline because:
    1. Our math engine generates perfect control signals (depth, normal,
       motion vectors) but only at sparse keyframes
    2. SparseCtrl propagates these sparse signals to fill intermediate frames
    3. Combined with EbSynth-style temporal propagation, this creates a
       two-stage anti-flicker pipeline

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  SparseCtrlConfig                                                    │
    │  ├─ condition_type: depth | normal | motion_vector | edge | rgb     │
    │  ├─ sparse_indices: list of keyframe indices                        │
    │  ├─ propagation_mode: temporal_attention | linear_interp | flow     │
    │  └─ encoder_scale: conditioning strength [0, 1]                     │
    ├─────────────────────────────────────────────────────────────────────┤
    │  SparseCtrlBridge                                                    │
    │  ├─ prepare_sparse_conditions(frames, indices) → condition batch    │
    │  ├─ build_comfyui_workflow(config) → workflow JSON                  │
    │  ├─ build_condition_mask(total_frames, sparse_indices) → mask       │
    │  ├─ interpolate_missing_conditions(conditions, mask) → dense        │
    │  └─ compute_temporal_consistency_score(frames) → float              │
    ├─────────────────────────────────────────────────────────────────────┤
    │  MotionVectorConditioner                                             │
    │  ├─ encode_motion_vectors(mv_sequence) → condition tensor           │
    │  ├─ compute_flow_warp_error(frame_a, frame_b, flow) → float        │
    │  └─ adaptive_keyframe_selection(mv_sequence) → sparse indices       │
    └─────────────────────────────────────────────────────────────────────┘

Integration with existing modules:
    - headless_comfy_ebsynth.py: SparseCtrlBridge provides workflow configs
    - MotionVectorBaker (GAP_C3): Provides ground-truth motion vectors
    - breakwall_evolution_bridge.py: Temporal metrics feed into evolution loop
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class ConditionType(Enum):
    """Types of sparse control conditions."""
    DEPTH = "depth"
    NORMAL = "normal"
    MOTION_VECTOR = "motion_vector"
    EDGE = "edge"
    RGB = "rgb"
    SEGMENTATION = "segmentation"


class PropagationMode(Enum):
    """How sparse conditions are propagated to dense frames."""
    TEMPORAL_ATTENTION = "temporal_attention"  # SparseCtrl native
    LINEAR_INTERPOLATION = "linear_interpolation"  # Simple fallback
    FLOW_WARP = "flow_warp"  # Motion vector guided
    EBSYNTH_PROPAGATION = "ebsynth_propagation"  # EbSynth NNF


@dataclass
class SparseCtrlConfig:
    """Configuration for SparseCtrl conditioning pipeline."""
    condition_types: List[ConditionType] = field(
        default_factory=lambda: [ConditionType.DEPTH, ConditionType.EDGE]
    )
    sparse_indices: List[int] = field(default_factory=list)
    propagation_mode: PropagationMode = PropagationMode.TEMPORAL_ATTENTION
    encoder_scale: float = 1.0
    temporal_attention_layers: int = 4
    condition_resolution: Tuple[int, int] = (512, 512)
    max_keyframe_gap: int = 8
    min_keyframe_density: float = 0.1
    motion_energy_threshold: float = 0.3
    identity_lock_weight: float = 0.8
    use_ip_adapter: bool = True
    ip_adapter_scale: float = 0.6


@dataclass
class SparseConditionBatch:
    """A batch of sparse conditions for video generation."""
    total_frames: int
    sparse_indices: List[int]
    conditions: Dict[str, List[Optional[np.ndarray]]]
    condition_mask: np.ndarray  # (total_frames,) bool
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def density(self) -> float:
        """Fraction of frames with conditions."""
        return float(np.sum(self.condition_mask)) / max(self.total_frames, 1)

    @property
    def max_gap(self) -> int:
        """Maximum gap between consecutive conditioned frames."""
        if not self.sparse_indices:
            return self.total_frames
        sorted_idx = sorted(self.sparse_indices)
        max_g = sorted_idx[0]  # Gap before first
        for i in range(1, len(sorted_idx)):
            gap = sorted_idx[i] - sorted_idx[i - 1]
            max_g = max(max_g, gap)
        max_g = max(max_g, self.total_frames - 1 - sorted_idx[-1])
        return max_g

    def to_dict(self) -> dict:
        return {
            "total_frames": self.total_frames,
            "sparse_indices": self.sparse_indices,
            "density": self.density,
            "max_gap": self.max_gap,
            "condition_types": list(self.conditions.keys()),
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# SparseCtrl Bridge
# ---------------------------------------------------------------------------

class SparseCtrlBridge:
    """Bridge between MarioTrickster math engine and SparseCtrl video generation.

    Prepares sparse control conditions from the math engine's output
    (depth maps, normal maps, motion vectors) and formats them for
    ComfyUI/AnimateDiff/SparseCtrl workflows.
    """

    def __init__(self, config: Optional[SparseCtrlConfig] = None):
        self.config = config or SparseCtrlConfig()

    def prepare_sparse_conditions(
        self,
        total_frames: int,
        condition_frames: Dict[str, Dict[int, np.ndarray]],
        sparse_indices: Optional[List[int]] = None,
    ) -> SparseConditionBatch:
        """Prepare a batch of sparse conditions for video generation.

        Args:
            total_frames: Total number of frames in the video.
            condition_frames: Dict mapping condition type name to
                            dict of {frame_index: condition_array}.
            sparse_indices: Override sparse indices. If None, uses
                          indices from condition_frames.

        Returns:
            SparseConditionBatch ready for workflow generation.
        """
        if sparse_indices is None:
            # Collect all unique indices from conditions
            all_indices = set()
            for frames_dict in condition_frames.values():
                all_indices.update(frames_dict.keys())
            sparse_indices = sorted(all_indices)

        # Build condition mask
        mask = np.zeros(total_frames, dtype=bool)
        for idx in sparse_indices:
            if 0 <= idx < total_frames:
                mask[idx] = True

        # Organize conditions
        conditions: Dict[str, List[Optional[np.ndarray]]] = {}
        for ctype_name, frames_dict in condition_frames.items():
            cond_list: List[Optional[np.ndarray]] = [None] * total_frames
            for idx, arr in frames_dict.items():
                if 0 <= idx < total_frames:
                    cond_list[idx] = arr
            conditions[ctype_name] = cond_list

        return SparseConditionBatch(
            total_frames=total_frames,
            sparse_indices=sparse_indices,
            conditions=conditions,
            condition_mask=mask,
            metadata={
                "config": {
                    "propagation_mode": self.config.propagation_mode.value,
                    "encoder_scale": self.config.encoder_scale,
                    "temporal_attention_layers":
                        self.config.temporal_attention_layers,
                }
            }
        )

    def build_comfyui_workflow(
        self,
        batch: SparseConditionBatch,
        prompt: str = "",
        negative_prompt: str = "",
        model_name: str = "sd15_animatediff_v3",
    ) -> Dict[str, Any]:
        """Build a ComfyUI workflow JSON for SparseCtrl generation.

        Generates a workflow that:
        1. Loads AnimateDiff temporal model
        2. Configures SparseCtrl condition encoder
        3. Feeds sparse conditions at specified indices
        4. Generates temporally consistent video frames

        Args:
            batch: Prepared sparse condition batch.
            prompt: Text prompt for generation.
            negative_prompt: Negative text prompt.
            model_name: Base model identifier.

        Returns:
            ComfyUI workflow as a dictionary.
        """
        workflow: Dict[str, Any] = {
            "session": "SESSION-065-SparseCtrl",
            "pipeline": "animatediff_sparsectrl",
            "model": model_name,
            "prompt": prompt,
            "negative_prompt": negative_prompt or
                "blurry, flickering, inconsistent, low quality",
            "total_frames": batch.total_frames,
            "sparse_ctrl": {
                "enabled": True,
                "condition_indices": batch.sparse_indices,
                "encoder_scale": self.config.encoder_scale,
                "propagation_mode": self.config.propagation_mode.value,
                "temporal_attention_layers":
                    self.config.temporal_attention_layers,
            },
            "conditions": {},
            "identity_lock": {
                "enabled": self.config.use_ip_adapter,
                "ip_adapter_scale": self.config.ip_adapter_scale,
                "reference_frame_index": batch.sparse_indices[0]
                if batch.sparse_indices else 0,
            },
            "anti_flicker": {
                "temporal_smoothing": True,
                "motion_vector_guided": ConditionType.MOTION_VECTOR.value
                in [ct.value for ct in self.config.condition_types],
                "max_keyframe_gap": batch.max_gap,
                "density": batch.density,
            }
        }

        # Add condition metadata
        for ctype_name in batch.conditions:
            conditioned_indices = [
                i for i in range(batch.total_frames)
                if batch.conditions[ctype_name][i] is not None
            ]
            workflow["conditions"][ctype_name] = {
                "type": ctype_name,
                "indices": conditioned_indices,
                "count": len(conditioned_indices),
                "resolution": list(self.config.condition_resolution),
            }

        return workflow

    def build_condition_mask(
        self, total_frames: int,
        sparse_indices: Optional[List[int]] = None
    ) -> np.ndarray:
        """Build a binary mask indicating which frames have conditions.

        Args:
            total_frames: Total number of frames.
            sparse_indices: Indices of conditioned frames.

        Returns:
            (total_frames,) boolean array.
        """
        indices = sparse_indices or self.config.sparse_indices
        mask = np.zeros(total_frames, dtype=bool)
        for idx in indices:
            if 0 <= idx < total_frames:
                mask[idx] = True
        return mask

    def interpolate_missing_conditions(
        self,
        conditions: List[Optional[np.ndarray]],
        mask: np.ndarray,
    ) -> List[np.ndarray]:
        """Fill missing conditions using interpolation.

        For frames without conditions, interpolate between the nearest
        conditioned frames. This is used as a fallback when SparseCtrl
        temporal attention is not available.

        Args:
            conditions: List of condition arrays (None for missing).
            mask: Boolean mask of conditioned frames.

        Returns:
            List of condition arrays with all frames filled.
        """
        N = len(conditions)
        result: List[np.ndarray] = []

        # Find conditioned frame indices
        conditioned = [i for i in range(N) if mask[i] and
                       conditions[i] is not None]

        if not conditioned:
            # No conditions at all; return zeros
            shape = (self.config.condition_resolution[1],
                     self.config.condition_resolution[0], 3)
            return [np.zeros(shape, dtype=np.float32) for _ in range(N)]

        for i in range(N):
            if mask[i] and conditions[i] is not None:
                result.append(conditions[i])
            else:
                # Find nearest conditioned frames
                prev_idx = None
                next_idx = None
                for ci in conditioned:
                    if ci <= i:
                        prev_idx = ci
                    if ci >= i and next_idx is None:
                        next_idx = ci

                if prev_idx is not None and next_idx is not None and \
                        prev_idx != next_idx:
                    # Linear interpolation
                    alpha = (i - prev_idx) / (next_idx - prev_idx)
                    interp = (1.0 - alpha) * conditions[prev_idx].astype(
                        np.float32) + alpha * conditions[next_idx].astype(
                        np.float32)
                    result.append(interp.astype(conditions[prev_idx].dtype))
                elif prev_idx is not None:
                    result.append(conditions[prev_idx])
                elif next_idx is not None:
                    result.append(conditions[next_idx])
                else:
                    shape = (self.config.condition_resolution[1],
                             self.config.condition_resolution[0], 3)
                    result.append(np.zeros(shape, dtype=np.float32))

        return result

    def compute_temporal_consistency_score(
        self, frames: List[np.ndarray]
    ) -> float:
        """Compute temporal consistency score for generated frames.

        Measures frame-to-frame stability using pixel-level differences.
        Lower difference = higher consistency = less flicker.

        Args:
            frames: List of generated frame arrays.

        Returns:
            Consistency score in [0, 1] (1 = perfectly consistent).
        """
        if len(frames) < 2:
            return 1.0

        diffs = []
        for i in range(1, len(frames)):
            f1 = frames[i - 1].astype(np.float32) / 255.0
            f2 = frames[i].astype(np.float32) / 255.0
            diff = np.mean(np.abs(f2 - f1))
            diffs.append(diff)

        mean_diff = float(np.mean(diffs))
        # Map to [0, 1]: 0 diff → 1.0, 0.5 diff → ~0.0
        score = float(math.exp(-5.0 * mean_diff))
        return score


# ---------------------------------------------------------------------------
# Motion Vector Conditioner
# ---------------------------------------------------------------------------

class MotionVectorConditioner:
    """Prepare motion vector conditions for SparseCtrl/AnimateDiff.

    Uses the math engine's ground-truth motion vectors (from FK/IK)
    as conditioning signals for video diffusion models.
    """

    def __init__(self, resolution: Tuple[int, int] = (512, 512)):
        self.resolution = resolution

    def encode_motion_vectors(
        self, mv_sequence: List[np.ndarray]
    ) -> List[np.ndarray]:
        """Encode motion vectors as RGB images for conditioning.

        Uses the normalized RGB encoding:
            R = dx * 0.5 + 0.5
            G = dy * 0.5 + 0.5
            B = 0 (or magnitude)

        Args:
            mv_sequence: List of (H, W, 2) motion vector arrays.

        Returns:
            List of (H, W, 3) uint8 RGB encoded arrays.
        """
        encoded = []
        for mv in mv_sequence:
            if mv is None:
                encoded.append(np.full(
                    (*self.resolution[::-1], 3), 128, dtype=np.uint8
                ))
                continue

            h, w = mv.shape[:2]
            rgb = np.zeros((h, w, 3), dtype=np.float32)

            # Normalize to [-1, 1] range
            max_disp = max(np.max(np.abs(mv)), 1e-6)
            normalized = mv / max_disp

            rgb[:, :, 0] = normalized[:, :, 0] * 0.5 + 0.5  # R = dx
            rgb[:, :, 1] = normalized[:, :, 1] * 0.5 + 0.5  # G = dy
            rgb[:, :, 2] = np.sqrt(
                normalized[:, :, 0] ** 2 + normalized[:, :, 1] ** 2
            ) * 0.5  # B = magnitude

            encoded.append(
                np.clip(rgb * 255, 0, 255).astype(np.uint8)
            )

        return encoded

    def compute_flow_warp_error(
        self, frame_a: np.ndarray, frame_b: np.ndarray,
        flow: np.ndarray
    ) -> float:
        """Compute warp error between two frames using motion vectors.

        Warps frame_a using the flow field and compares with frame_b.
        Lower error indicates better temporal consistency.

        Args:
            frame_a: Source frame (H, W, C).
            frame_b: Target frame (H, W, C).
            flow: Motion vector field (H, W, 2).

        Returns:
            Mean absolute warp error in [0, 1].
        """
        h, w = frame_a.shape[:2]
        if flow.shape[:2] != (h, w):
            return 1.0

        # Create sampling grid
        y_coords, x_coords = np.mgrid[0:h, 0:w].astype(np.float32)
        x_warped = x_coords + flow[:, :, 0]
        y_warped = y_coords + flow[:, :, 1]

        # Clamp to image bounds
        x_warped = np.clip(x_warped, 0, w - 1).astype(int)
        y_warped = np.clip(y_warped, 0, h - 1).astype(int)

        # Warp frame_a
        warped = frame_a[y_warped, x_warped]

        # Compute error
        fa = frame_a.astype(np.float32) / 255.0
        fb = frame_b.astype(np.float32) / 255.0
        fw = warped.astype(np.float32) / 255.0

        error = float(np.mean(np.abs(fw - fb)))
        return error

    def adaptive_keyframe_selection(
        self, mv_sequence: List[np.ndarray],
        max_gap: int = 8,
        energy_threshold: float = 0.3,
    ) -> List[int]:
        """Select sparse keyframe indices based on motion energy.

        Frames with high motion energy (large displacements) are selected
        as keyframes. This ensures that fast-moving parts of the animation
        receive more conditioning.

        Args:
            mv_sequence: List of (H, W, 2) motion vector arrays.
            max_gap: Maximum allowed gap between keyframes.
            energy_threshold: Motion energy threshold for keyframe selection.

        Returns:
            List of selected keyframe indices.
        """
        N = len(mv_sequence)
        if N == 0:
            return []
        if N <= 2:
            return list(range(N))

        # Always include first and last
        keyframes = {0, N - 1}

        # Compute motion energy per frame
        energies = []
        for mv in mv_sequence:
            if mv is None:
                energies.append(0.0)
            else:
                energy = float(np.mean(np.sqrt(
                    mv[:, :, 0] ** 2 + mv[:, :, 1] ** 2
                )))
                energies.append(energy)

        max_energy = max(energies) if energies else 1.0
        if max_energy < 1e-10:
            max_energy = 1.0

        # Select high-energy frames
        for i, energy in enumerate(energies):
            if energy / max_energy > energy_threshold:
                keyframes.add(i)

        # Fill gaps
        sorted_kf = sorted(keyframes)
        filled = set(sorted_kf)
        for i in range(len(sorted_kf) - 1):
            gap = sorted_kf[i + 1] - sorted_kf[i]
            if gap > max_gap:
                # Insert intermediate keyframes
                n_insert = math.ceil(gap / max_gap) - 1
                for j in range(1, n_insert + 1):
                    insert_idx = sorted_kf[i] + int(
                        j * gap / (n_insert + 1)
                    )
                    filled.add(insert_idx)

        return sorted(filled)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def create_sparse_ctrl_bridge(
    condition_types: Optional[List[str]] = None,
    encoder_scale: float = 1.0,
) -> SparseCtrlBridge:
    """Create a SparseCtrlBridge with common settings."""
    ctypes = []
    for ct_name in (condition_types or ["depth", "edge"]):
        try:
            ctypes.append(ConditionType(ct_name))
        except ValueError:
            pass

    config = SparseCtrlConfig(
        condition_types=ctypes,
        encoder_scale=encoder_scale,
    )
    return SparseCtrlBridge(config=config)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "ConditionType",
    "PropagationMode",
    "SparseCtrlConfig",
    "SparseConditionBatch",
    "SparseCtrlBridge",
    "MotionVectorConditioner",
    "create_sparse_ctrl_bridge",
]
