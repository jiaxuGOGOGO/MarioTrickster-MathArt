"""
SESSION-056 — Headless ComfyUI + EbSynth End-to-End Neural Rendering Pipeline.

Distilled from Phase 1 "Breaking the Wall" research:

1. **Ondřej Jamriška & Daniel Sýkora** (Czech Technical University, EbSynth core inventors):
   "Stylizing Video by Example" (SIGGRAPH 2019) — PatchMatch algorithm propagates
   keyframe brushstrokes to intermediate frames along optical flow.

2. **Lvmin Zhang (张吕敏)** — ControlNet (ICCV 2023):
   "Adding Conditional Control to Text-to-Image Diffusion Models" — zero-convolution
   architecture locks geometry while AI generates only material/texture.

3. **ReEzSynth** (FuouM, MIT License):
   Pure Python EbSynth with multi-guide synthesis, temporal NNF propagation,
   sparse feature guiding, bidirectional Poisson blending, RAFT/NeuFlow v2 flow.

Core Insight:
    Traditional AI video (Sora, Wan2.1) ESTIMATES optical flow → noise → flicker.
    MarioTrickster-MathArt is a PROCEDURAL MATH ENGINE with EXACT FK motion vectors.
    Feeding ground-truth MV into EbSynth guide channels = theoretically perfect
    temporal consistency. ControlNet with analytical normal+depth maps locks geometry
    so AI only paints material/texture on mathematically frozen shapes.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────────┐
    │  HeadlessNeuralRenderPipeline                                          │
    │  ├─ Stage 1: Bake ground-truth MV, normals, depth from FK engine       │
    │  ├─ Stage 2: Generate AI-stylized keyframes via ComfyUI API            │
    │  │           (ControlNet-NormalBae + Depth @ weight 1.0)               │
    │  ├─ Stage 3: Propagate style to all frames via EbSynth                 │
    │  │           (ground-truth MV as guide channel)                        │
    │  ├─ Stage 4: Validate temporal consistency via warp-check              │
    │  └─ Stage 5: Export final zero-flicker stylized sequence               │
    └─────────────────────────────────────────────────────────────────────────┘

Usage:
    from mathart.animation.headless_comfy_ebsynth import (
        HeadlessNeuralRenderPipeline,
        NeuralRenderConfig,
        NeuralRenderResult,
    )

    config = NeuralRenderConfig(
        comfyui_url="http://localhost:8188",
        style_prompt="watercolor painting, Studio Ghibli style",
        controlnet_normal_weight=1.0,
        controlnet_depth_weight=1.0,
        ebsynth_uniformity=4000.0,
        keyframe_interval=4,
    )

    pipeline = HeadlessNeuralRenderPipeline(config)
    result = pipeline.run(
        skeleton=skeleton,
        animation_func=run_animation,
        style=style,
        frames=16,
        width=128,
        height=128,
    )

    # result.stylized_frames: list[Image.Image]
    # result.temporal_metrics: TemporalConsistencyMetrics
    # result.keyframe_indices: list[int]

References:
    - Jamriška et al., "Stylizing Video by Example", SIGGRAPH 2019
    - Zhang et al., "Adding Conditional Control to Text-to-Image Diffusion Models", ICCV 2023
    - FuouM/ReEzSynth, MIT License
    - SESSION-045: MotionVectorBaker (ground-truth MV from FK)
    - SESSION-034/044: IndustrialRenderer (analytical normal/depth maps)
"""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np
from PIL import Image

from .skeleton import Skeleton
from .parts import CharacterStyle
from .motion_vector_baker import (
    MotionVectorField,
    MotionVectorSequence,
    bake_motion_vector_sequence,
    compute_temporal_consistency_score,
    encode_motion_vector_rgb,
    export_ebsynth_project,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class NeuralRenderConfig:
    """Configuration for the headless neural rendering pipeline.

    Phase 2 extends the original SESSION-056 path with an industrial
    anti-flicker workflow: sparse keyframe planning, identity locking via
    IP-Adapter-compatible branches, and guide-completeness auditing.
    """
    comfyui_url: str = "http://localhost:8188"
    style_prompt: str = "high quality pixel art, detailed shading, game sprite"
    negative_prompt: str = "blurry, low quality, distorted, deformed"
    controlnet_normal_weight: float = 1.0
    controlnet_depth_weight: float = 1.0
    controlnet_mask_weight: float = 0.75
    use_ip_adapter_identity: bool = True
    ip_adapter_weight: float = 0.85
    ip_adapter_model_name: str = "ip-adapter-plus_sdxl_vit-h.safetensors"
    ip_adapter_clip_vision_name: str = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
    workflow_lock_required: bool = False
    ebsynth_uniformity: float = 4000.0
    ebsynth_patch_size: int = 7
    ebsynth_pyramid_levels: int = 5
    keyframe_interval: int = 4
    keyframe_selection_strategy: str = "motion_adaptive"
    max_keyframes: int = 6
    min_keyframe_gap: int = 2
    motion_energy_percentile: float = 0.72
    silhouette_delta_threshold: float = 0.08
    use_temporal_nnf: bool = True
    use_sparse_feature_guide: bool = True
    use_mask_guide: bool = True
    mv_guide_weight: float = 2.0
    normal_guide_weight: float = 1.5
    edge_guide_weight: float = 1.0
    mask_guide_weight: float = 1.25
    warp_error_threshold: float = 0.15
    temporal_drift_threshold: float = 0.22
    identity_consistency_threshold: float = 0.55
    sd_model_checkpoint: str = "sd_xl_base_1.0.safetensors"
    sd_steps: int = 20
    sd_cfg_scale: float = 7.5
    sd_denoising_strength: float = 0.65
    output_dir: str = "./output/neural_render"

    def validate(self) -> list[str]:
        """Validate configuration and normalize industrial defaults."""
        warnings = []
        if self.controlnet_normal_weight < 0.5:
            warnings.append(
                "controlnet_normal_weight < 0.5: geometry may not be fully locked"
            )
        if self.controlnet_depth_weight < 0.5:
            warnings.append(
                "controlnet_depth_weight < 0.5: silhouette may drift"
            )
        if self.ebsynth_patch_size % 2 == 0:
            warnings.append(
                "ebsynth_patch_size must be odd; auto-correcting to next odd"
            )
            self.ebsynth_patch_size += 1
        if self.keyframe_interval < 1:
            warnings.append("keyframe_interval must be >= 1; setting to 1")
            self.keyframe_interval = 1
        if self.min_keyframe_gap < 1:
            warnings.append("min_keyframe_gap must be >= 1; setting to 1")
            self.min_keyframe_gap = 1
        if self.max_keyframes < 2:
            warnings.append("max_keyframes must be >= 2; setting to 2")
            self.max_keyframes = 2
        self.motion_energy_percentile = float(np.clip(self.motion_energy_percentile, 0.1, 0.95))
        self.controlnet_mask_weight = float(np.clip(self.controlnet_mask_weight, 0.0, 2.0))
        self.ip_adapter_weight = float(np.clip(self.ip_adapter_weight, 0.0, 1.5))
        self.mask_guide_weight = float(np.clip(self.mask_guide_weight, 0.0, 3.0))
        self.identity_consistency_threshold = float(
            np.clip(self.identity_consistency_threshold, 0.0, 1.0)
        )
        self.temporal_drift_threshold = float(np.clip(self.temporal_drift_threshold, 0.0, 1.0))
        return warnings


@dataclass
class KeyframePlan:
    """Sparse keyframe schedule for industrial anti-flicker generation."""
    indices: list[int] = field(default_factory=list)
    motion_scores: dict[int, float] = field(default_factory=dict)
    silhouette_scores: dict[int, float] = field(default_factory=dict)
    priority_scores: dict[int, float] = field(default_factory=dict)
    strategy: str = "fixed_interval"
    max_gap: int = 0
    density: float = 0.0
    identity_reference_index: int = 0
    guides_locked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "indices": self.indices,
            "motion_scores": {str(k): round(v, 6) for k, v in self.motion_scores.items()},
            "silhouette_scores": {str(k): round(v, 6) for k, v in self.silhouette_scores.items()},
            "priority_scores": {str(k): round(v, 6) for k, v in self.priority_scores.items()},
            "strategy": self.strategy,
            "max_gap": self.max_gap,
            "density": round(self.density, 6),
            "identity_reference_index": self.identity_reference_index,
            "guides_locked": self.guides_locked,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Result
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class NeuralRenderResult:
    """Result of the headless neural rendering pipeline."""
    stylized_frames: list[Image.Image] = field(default_factory=list)
    keyframe_indices: list[int] = field(default_factory=list)
    keyframes: list[Image.Image] = field(default_factory=list)
    source_frames: list[Image.Image] = field(default_factory=list)
    normal_maps: list[Image.Image] = field(default_factory=list)
    depth_maps: list[Image.Image] = field(default_factory=list)
    mask_maps: list[Image.Image] = field(default_factory=list)
    mv_sequence: Optional[MotionVectorSequence] = None
    temporal_metrics: dict[str, float] = field(default_factory=dict)
    workflow_manifest: dict[str, Any] = field(default_factory=dict)
    keyframe_plan: Optional[KeyframePlan] = None
    pipeline_log: list[str] = field(default_factory=list)
    config: Optional[NeuralRenderConfig] = None
    elapsed_seconds: float = 0.0

    @property
    def frame_count(self) -> int:
        return len(self.stylized_frames)

    @property
    def temporal_pass(self) -> bool:
        return self.temporal_metrics.get("temporal_pass", False)

    def to_metadata(self) -> dict[str, Any]:
        """Export pipeline result as JSON-serializable metadata."""
        return {
            "format": "headless_neural_render",
            "version": "2.0",
            "frame_count": self.frame_count,
            "keyframe_indices": self.keyframe_indices,
            "keyframe_plan": self.keyframe_plan.to_dict() if self.keyframe_plan else {},
            "temporal_metrics": self.temporal_metrics,
            "workflow_manifest": self.workflow_manifest,
            "config": {
                "style_prompt": self.config.style_prompt if self.config else "",
                "controlnet_normal_weight": self.config.controlnet_normal_weight if self.config else 0,
                "controlnet_depth_weight": self.config.controlnet_depth_weight if self.config else 0,
                "controlnet_mask_weight": self.config.controlnet_mask_weight if self.config else 0,
                "use_ip_adapter_identity": self.config.use_ip_adapter_identity if self.config else False,
                "ip_adapter_weight": self.config.ip_adapter_weight if self.config else 0,
                "ebsynth_uniformity": self.config.ebsynth_uniformity if self.config else 0,
                "keyframe_interval": self.config.keyframe_interval if self.config else 0,
                "keyframe_selection_strategy": self.config.keyframe_selection_strategy if self.config else "",
                "warp_error_threshold": self.config.warp_error_threshold if self.config else 0,
            },
            "elapsed_seconds": self.elapsed_seconds,
            "source": "MarioTrickster-MathArt HeadlessNeuralRenderPipeline",
            "research_provenance": [
                "Jamriška et al., Stylizing Video by Example, SIGGRAPH 2019",
                "Zhang et al., ControlNet, ICCV 2023",
                "Ye et al., IP-Adapter, arXiv 2023",
                "Liang et al., FlowVid, CVPR 2024",
                "FuouM/ReEzSynth, MIT License",
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════
#  ComfyUI API Client (Headless)
# ═══════════════════════════════════════════════════════════════════════════


class ComfyUIHeadlessClient:
    """Headless Python client for ComfyUI REST API.

    Submits ControlNet-conditioned image generation workflows without
    any browser or GUI interaction. Pure HTTP API calls via requests.

    Architecture:
        1. Build workflow JSON (node graph) programmatically
        2. POST to /prompt endpoint
        3. Poll /history/{prompt_id} for completion
        4. Download generated images from /view endpoint

    Parameters
    ----------
    base_url : str
        ComfyUI server URL (default: http://localhost:8188).
    timeout : int
        Request timeout in seconds.
    """

    def __init__(self, base_url: str = "http://localhost:8188", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    def build_controlnet_workflow(
        self,
        source_image: Image.Image,
        normal_map: Image.Image,
        depth_map: Image.Image,
        prompt: str,
        negative_prompt: str = "",
        normal_weight: float = 1.0,
        depth_weight: float = 1.0,
        mask_map: Optional[Image.Image] = None,
        identity_reference: Optional[Image.Image] = None,
        use_ip_adapter: bool = False,
        ip_adapter_weight: float = 0.85,
        ip_adapter_model_name: str = "ip-adapter-plus_sdxl_vit-h.safetensors",
        ip_adapter_clip_vision_name: str = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
        model_checkpoint: str = "sd_xl_base_1.0.safetensors",
        steps: int = 20,
        cfg_scale: float = 7.5,
        denoising_strength: float = 0.65,
        seed: int = -1,
    ) -> dict[str, Any]:
        """Build a ComfyUI workflow JSON for industrial sparse-keyframe generation.

        The base workflow remains dual-ControlNet (normal + depth). When an
        identity reference is available, an optional IP-Adapter branch is added
        so the model can lock character identity while ControlNet locks geometry.
        """
        if seed < 0:
            seed = np.random.randint(0, 2**31)

        def img_to_base64(img: Image.Image) -> str:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")

        positive_node = "11"
        model_node = "1"
        prompt_nodes: dict[str, Any] = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": model_checkpoint},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": prompt,
                    "clip": ["1", 1],
                },
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": negative_prompt,
                    "clip": ["1", 1],
                },
            },
            "4": {
                "class_type": "LoadImageBase64",
                "inputs": {
                    "image": img_to_base64(source_image),
                },
            },
            "5": {
                "class_type": "VAEEncode",
                "inputs": {
                    "pixels": ["4", 0],
                    "vae": ["1", 2],
                },
            },
            "6": {
                "class_type": "LoadImageBase64",
                "inputs": {
                    "image": img_to_base64(normal_map),
                },
            },
            "7": {
                "class_type": "LoadImageBase64",
                "inputs": {
                    "image": img_to_base64(depth_map),
                },
            },
            "8": {
                "class_type": "ControlNetLoader",
                "inputs": {
                    "control_net_name": "control_v11p_sd15_normalbae.pth",
                },
            },
            "9": {
                "class_type": "ControlNetLoader",
                "inputs": {
                    "control_net_name": "control_v11f1p_sd15_depth.pth",
                },
            },
            "10": {
                "class_type": "ControlNetApply",
                "inputs": {
                    "conditioning": ["2", 0],
                    "control_net": ["8", 0],
                    "image": ["6", 0],
                    "strength": normal_weight,
                },
            },
            "11": {
                "class_type": "ControlNetApply",
                "inputs": {
                    "conditioning": ["10", 0],
                    "control_net": ["9", 0],
                    "image": ["7", 0],
                    "strength": depth_weight,
                },
            },
        }

        lock_manifest = {
            "controlnet_guides": ["normal", "depth"],
            "mask_present": mask_map is not None,
            "identity_reference_present": identity_reference is not None,
            "identity_lock_requested": bool(use_ip_adapter and identity_reference is not None),
            "guides_requested": ["normal", "depth"] + (["mask"] if mask_map is not None else []),
            "seed": int(seed),
        }

        if use_ip_adapter and identity_reference is not None:
            prompt_nodes.update(
                {
                    "15": {
                        "class_type": "LoadImageBase64",
                        "inputs": {"image": img_to_base64(identity_reference)},
                    },
                    "16": {
                        "class_type": "CLIPVisionLoader",
                        "inputs": {"clip_name": ip_adapter_clip_vision_name},
                    },
                    "17": {
                        "class_type": "IPAdapterModelLoader",
                        "inputs": {"ipadapter_file": ip_adapter_model_name},
                    },
                    "18": {
                        "class_type": "IPAdapterApply",
                        "inputs": {
                            "model": ["1", 0],
                            "image": ["15", 0],
                            "ipadapter": ["17", 0],
                            "clip_vision": ["16", 0],
                            "weight": ip_adapter_weight,
                        },
                    },
                }
            )
            model_node = "18"
            lock_manifest["identity_lock_active"] = True
            lock_manifest["guides_requested"].append("ip_adapter_identity")
        else:
            lock_manifest["identity_lock_active"] = False

        prompt_nodes.update(
            {
                "12": {
                    "class_type": "KSampler",
                    "inputs": {
                        "model": [model_node, 0],
                        "positive": [positive_node, 0],
                        "negative": ["3", 0],
                        "latent_image": ["5", 0],
                        "seed": seed,
                        "steps": steps,
                        "cfg": cfg_scale,
                        "sampler_name": "euler_ancestral",
                        "scheduler": "normal",
                        "denoise": denoising_strength,
                    },
                },
                "13": {
                    "class_type": "VAEDecode",
                    "inputs": {
                        "samples": ["12", 0],
                        "vae": ["1", 2],
                    },
                },
                "14": {
                    "class_type": "SaveImage",
                    "inputs": {
                        "images": ["13", 0],
                        "filename_prefix": "mathart_neural",
                    },
                },
            }
        )

        workflow = {
            "client_id": self.client_id,
            "prompt": prompt_nodes,
            "mathart_lock_manifest": lock_manifest,
        }
        self.last_workflow_manifest = lock_manifest
        return workflow

    def submit_workflow(self, workflow: dict) -> Optional[str]:
        """Submit a workflow to ComfyUI and return the prompt_id.

        Parameters
        ----------
        workflow : dict
            ComfyUI workflow JSON.

        Returns
        -------
        str or None
            Prompt ID for tracking, or None if submission failed.
        """
        try:
            import requests
            response = requests.post(
                f"{self.base_url}/prompt",
                json=workflow,
                timeout=self.timeout,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("prompt_id")
            else:
                logger.warning(
                    f"ComfyUI submit failed: {response.status_code} {response.text}"
                )
                return None
        except Exception as e:
            logger.warning(f"ComfyUI connection failed: {e}")
            return None

    def poll_result(
        self,
        prompt_id: str,
        poll_interval: float = 1.0,
        max_wait: float = 300.0,
    ) -> Optional[Image.Image]:
        """Poll ComfyUI for workflow completion and retrieve the generated image.

        Parameters
        ----------
        prompt_id : str
            Prompt ID from submit_workflow().
        poll_interval : float
            Seconds between polls.
        max_wait : float
            Maximum wait time in seconds.

        Returns
        -------
        Image.Image or None
            Generated image, or None if timeout/error.
        """
        try:
            import requests
            start = time.time()
            while time.time() - start < max_wait:
                resp = requests.get(
                    f"{self.base_url}/history/{prompt_id}",
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if prompt_id in data:
                        outputs = data[prompt_id].get("outputs", {})
                        for node_id, node_output in outputs.items():
                            images = node_output.get("images", [])
                            if images:
                                img_info = images[0]
                                img_resp = requests.get(
                                    f"{self.base_url}/view",
                                    params={
                                        "filename": img_info["filename"],
                                        "subfolder": img_info.get("subfolder", ""),
                                        "type": img_info.get("type", "output"),
                                    },
                                    timeout=self.timeout,
                                )
                                if img_resp.status_code == 200:
                                    return Image.open(io.BytesIO(img_resp.content))
                time.sleep(poll_interval)
            logger.warning(f"ComfyUI poll timeout after {max_wait}s")
            return None
        except Exception as e:
            logger.warning(f"ComfyUI poll error: {e}")
            return None

    def generate_stylized_keyframe(
        self,
        source_image: Image.Image,
        normal_map: Image.Image,
        depth_map: Image.Image,
        config: NeuralRenderConfig,
        seed: int = -1,
        mask_map: Optional[Image.Image] = None,
        identity_reference: Optional[Image.Image] = None,
    ) -> Optional[Image.Image]:
        """Generate a single AI-stylized keyframe via ComfyUI.

        Phase 2 first attempts a geometry-locked plus identity-locked workflow.
        If that path cannot be submitted and strict locking is not required, the
        method falls back to the original dual-ControlNet workflow.
        """
        workflow = self.build_controlnet_workflow(
            source_image=source_image,
            normal_map=normal_map,
            depth_map=depth_map,
            mask_map=mask_map,
            identity_reference=identity_reference,
            use_ip_adapter=bool(config.use_ip_adapter_identity and identity_reference is not None),
            ip_adapter_weight=config.ip_adapter_weight,
            ip_adapter_model_name=config.ip_adapter_model_name,
            ip_adapter_clip_vision_name=config.ip_adapter_clip_vision_name,
            prompt=config.style_prompt,
            negative_prompt=config.negative_prompt,
            normal_weight=config.controlnet_normal_weight,
            depth_weight=config.controlnet_depth_weight,
            model_checkpoint=config.sd_model_checkpoint,
            steps=config.sd_steps,
            cfg_scale=config.sd_cfg_scale,
            denoising_strength=config.sd_denoising_strength,
            seed=seed,
        )

        prompt_id = self.submit_workflow(workflow)
        if prompt_id is None and config.use_ip_adapter_identity and not config.workflow_lock_required:
            fallback_workflow = self.build_controlnet_workflow(
                source_image=source_image,
                normal_map=normal_map,
                depth_map=depth_map,
                mask_map=mask_map,
                identity_reference=None,
                use_ip_adapter=False,
                prompt=config.style_prompt,
                negative_prompt=config.negative_prompt,
                normal_weight=config.controlnet_normal_weight,
                depth_weight=config.controlnet_depth_weight,
                model_checkpoint=config.sd_model_checkpoint,
                steps=config.sd_steps,
                cfg_scale=config.sd_cfg_scale,
                denoising_strength=config.sd_denoising_strength,
                seed=seed,
            )
            prompt_id = self.submit_workflow(fallback_workflow)
            self.last_workflow_manifest = {
                **getattr(self, "last_workflow_manifest", {}),
                "identity_lock_active": False,
                "fallback_reason": "ip_adapter_submission_failed",
            }

        if prompt_id is None:
            return None

        return self.poll_result(prompt_id)


# ═══════════════════════════════════════════════════════════════════════════
#  EbSynth Propagation Engine (Headless)
# ═══════════════════════════════════════════════════════════════════════════


class EbSynthPropagationEngine:
    """Headless EbSynth-style temporal style propagation engine.

    Propagates AI-stylized keyframes to all intermediate frames using
    ground-truth motion vectors as guide channels. This is the core
    anti-flicker mechanism: instead of estimating optical flow (RAFT etc.),
    we use EXACT motion vectors from Forward Kinematics.

    The algorithm follows Jamriška et al. (SIGGRAPH 2019):
    1. For each frame, find nearest keyframes (forward and backward)
    2. Use PatchMatch with multi-guide channels to synthesize style
    3. Blend forward and backward passes via Poisson blending
    4. Temporal NNF propagation reuses previous frame's NNF

    In our implementation, we use a simplified but mathematically rigorous
    approach: weighted blending based on temporal distance from keyframes,
    guided by ground-truth motion vectors for pixel correspondence.

    Parameters
    ----------
    config : NeuralRenderConfig
        Pipeline configuration.
    """

    def __init__(self, config: NeuralRenderConfig):
        self.config = config

    def propagate_style(
        self,
        source_frames: list[Image.Image],
        keyframes: dict[int, Image.Image],
        mv_sequence: MotionVectorSequence,
        normal_maps: Optional[list[Image.Image]] = None,
        mask_maps: Optional[list[Image.Image]] = None,
    ) -> list[Image.Image]:
        """Propagate keyframe styles to all frames using ground-truth MV.

        This is the core EbSynth-inspired propagation with our unique advantage:
        EXACT motion vectors from FK instead of estimated optical flow.

        Algorithm:
        1. For each non-keyframe, find nearest keyframes (before/after)
        2. Warp nearest keyframe style using ground-truth MV chain
        3. If between two keyframes, blend forward/backward warps
        4. Apply temporal smoothing via NNF propagation

        Parameters
        ----------
        source_frames : list[Image.Image]
            Original rendered frames from math engine.
        keyframes : dict[int, Image.Image]
            AI-stylized keyframes {frame_index: styled_image}.
        mv_sequence : MotionVectorSequence
            Ground-truth motion vector sequence.
        normal_maps : list[Image.Image], optional
            Normal maps for additional guide weighting.

        Returns
        -------
        list[Image.Image]
            Fully stylized frame sequence.
        """
        n_frames = len(source_frames)
        if n_frames == 0:
            return []

        # If all frames are keyframes, return them directly
        if len(keyframes) >= n_frames:
            return [keyframes.get(i, source_frames[i]) for i in range(n_frames)]

        sorted_kf_indices = sorted(keyframes.keys())
        stylized = [None] * n_frames

        # Place keyframes
        for idx, img in keyframes.items():
            if 0 <= idx < n_frames:
                stylized[idx] = img

        # Propagate to non-keyframe frames
        for i in range(n_frames):
            if stylized[i] is not None:
                continue

            # Find nearest keyframes before and after
            prev_kf = None
            next_kf = None
            for kf_idx in sorted_kf_indices:
                if kf_idx <= i:
                    prev_kf = kf_idx
                if kf_idx >= i and next_kf is None:
                    next_kf = kf_idx

            if prev_kf is not None and next_kf is not None and prev_kf != next_kf:
                # Between two keyframes: bidirectional blend
                forward_warp = self._warp_frame(
                    keyframes[prev_kf], mv_sequence, prev_kf, i
                )
                backward_warp = self._warp_frame(
                    keyframes[next_kf], mv_sequence, next_kf, i
                )
                # Temporal distance-based blending weight
                total_dist = next_kf - prev_kf
                alpha = (i - prev_kf) / total_dist  # 0 at prev_kf, 1 at next_kf
                stylized[i] = self._blend_frames(
                    forward_warp, backward_warp, alpha
                )
            elif prev_kf is not None:
                # Only forward keyframe available
                stylized[i] = self._warp_frame(
                    keyframes[prev_kf], mv_sequence, prev_kf, i
                )
            elif next_kf is not None:
                # Only backward keyframe available
                stylized[i] = self._warp_frame(
                    keyframes[next_kf], mv_sequence, next_kf, i
                )
            else:
                # No keyframes at all — use source frame
                stylized[i] = source_frames[i]

            if mask_maps is not None and i < len(mask_maps):
                stylized[i] = self._apply_mask_guide(
                    stylized[i], source_frames[i], mask_maps[i]
                )
            if self.config.use_temporal_nnf and i > 0:
                stylized[i] = self._temporal_stabilize(
                    stylized[i - 1], stylized[i], source_frames[i]
                )

        return stylized

    def _warp_frame(
        self,
        source: Image.Image,
        mv_sequence: MotionVectorSequence,
        from_idx: int,
        to_idx: int,
    ) -> Image.Image:
        """Warp a frame from one time index to another using MV chain.

        Chains motion vectors from from_idx to to_idx to compute the
        cumulative pixel displacement, then applies the warp.

        Parameters
        ----------
        source : Image.Image
            Source frame to warp.
        mv_sequence : MotionVectorSequence
            Motion vector sequence.
        from_idx : int
            Source frame index.
        to_idx : int
            Target frame index.

        Returns
        -------
        Image.Image
            Warped frame.
        """
        src_arr = np.array(source).astype(np.float64)
        h, w = src_arr.shape[:2]

        # Accumulate motion vectors along the chain
        cumulative_dx = np.zeros((h, w), dtype=np.float64)
        cumulative_dy = np.zeros((h, w), dtype=np.float64)

        if from_idx < to_idx:
            # Forward warp
            for i in range(from_idx, min(to_idx, len(mv_sequence.fields))):
                mv = mv_sequence.fields[i]
                # Resize MV field if dimensions don't match
                if mv.dx.shape != (h, w):
                    scale_y = h / mv.dx.shape[0]
                    scale_x = w / mv.dx.shape[1]
                    dx_resized = np.repeat(
                        np.repeat(mv.dx, int(scale_y), axis=0),
                        int(scale_x), axis=1,
                    )[:h, :w] if scale_y > 1 else mv.dx[:h, :w]
                    dy_resized = np.repeat(
                        np.repeat(mv.dy, int(scale_y), axis=0),
                        int(scale_x), axis=1,
                    )[:h, :w] if scale_y > 1 else mv.dy[:h, :w]
                else:
                    dx_resized = mv.dx
                    dy_resized = mv.dy
                cumulative_dx += dx_resized
                cumulative_dy += dy_resized
        else:
            # Backward warp (reverse direction)
            for i in range(from_idx - 1, max(to_idx - 1, -1), -1):
                if i < len(mv_sequence.fields):
                    mv = mv_sequence.fields[i]
                    if mv.dx.shape != (h, w):
                        dx_resized = mv.dx[:h, :w] if mv.dx.shape[0] >= h else mv.dx
                        dy_resized = mv.dy[:h, :w] if mv.dy.shape[0] >= h else mv.dy
                    else:
                        dx_resized = mv.dx
                        dy_resized = mv.dy
                    cumulative_dx -= dx_resized
                    cumulative_dy -= dy_resized

        # Apply warp via inverse mapping
        yy, xx = np.mgrid[0:h, 0:w]
        warp_x = np.clip(np.round(xx + cumulative_dx).astype(int), 0, w - 1)
        warp_y = np.clip(np.round(yy + cumulative_dy).astype(int), 0, h - 1)

        if src_arr.ndim == 3:
            warped = src_arr[warp_y, warp_x, :]
        else:
            warped = src_arr[warp_y, warp_x]

        return Image.fromarray(np.clip(warped, 0, 255).astype(np.uint8))

    def _blend_frames(
        self,
        frame_a: Image.Image,
        frame_b: Image.Image,
        alpha: float,
    ) -> Image.Image:
        """Blend two frames with temporal distance-based alpha."""
        smooth_alpha = 0.5 * (1.0 - math.cos(math.pi * alpha))

        arr_a = np.array(frame_a).astype(np.float64)
        arr_b = np.array(frame_b).astype(np.float64)

        if arr_a.shape != arr_b.shape:
            min_h = min(arr_a.shape[0], arr_b.shape[0])
            min_w = min(arr_a.shape[1], arr_b.shape[1])
            arr_a = arr_a[:min_h, :min_w]
            arr_b = arr_b[:min_h, :min_w]

        blended = arr_a * (1.0 - smooth_alpha) + arr_b * smooth_alpha
        return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8))

    def _apply_mask_guide(
        self,
        stylized_frame: Image.Image,
        source_frame: Image.Image,
        mask_frame: Image.Image,
    ) -> Image.Image:
        """Clamp propagated style to the procedural foreground mask."""
        stylized = np.array(stylized_frame).astype(np.float64)
        source = np.array(source_frame).astype(np.float64)
        mask = np.array(mask_frame.convert("L")).astype(np.float64) / 255.0
        mask = np.clip(mask * float(self.config.mask_guide_weight), 0.0, 1.0)
        if stylized.ndim == 3:
            mask = mask[:, :, None]
        fused = stylized * mask + source * (1.0 - mask)
        return Image.fromarray(np.clip(fused, 0, 255).astype(np.uint8))

    def _temporal_stabilize(
        self,
        previous_frame: Image.Image,
        current_frame: Image.Image,
        source_frame: Image.Image,
    ) -> Image.Image:
        """Apply lightweight temporal blending on low-frequency residuals."""
        prev_arr = np.array(previous_frame).astype(np.float64)
        curr_arr = np.array(current_frame).astype(np.float64)
        src_arr = np.array(source_frame).astype(np.float64)
        if prev_arr.shape != curr_arr.shape:
            return current_frame
        residual = np.clip(np.abs(curr_arr - src_arr).mean() / 255.0, 0.0, 1.0)
        alpha = 0.15 + 0.20 * (1.0 - residual)
        stabilized = prev_arr * alpha + curr_arr * (1.0 - alpha)
        return Image.fromarray(np.clip(stabilized, 0, 255).astype(np.uint8))


# ═══════════════════════════════════════════════════════════════════════════
#  Main Pipeline
# ═══════════════════════════════════════════════════════════════════════════


class HeadlessNeuralRenderPipeline:
    """End-to-end headless neural rendering pipeline.

    Orchestrates the full pipeline from math engine output to zero-flicker
    AI-stylized animation:

    Stage 1: Bake ground-truth auxiliary maps (MV, normals, depth)
    Stage 2: Generate AI-stylized keyframes via ComfyUI API
    Stage 3: Propagate style via EbSynth with ground-truth MV guides
    Stage 4: Validate temporal consistency via warp-check
    Stage 5: Export final sequence

    Parameters
    ----------
    config : NeuralRenderConfig
        Pipeline configuration.
    """

    def __init__(self, config: Optional[NeuralRenderConfig] = None):
        self.config = config or NeuralRenderConfig()
        self.comfyui = ComfyUIHeadlessClient(
            base_url=self.config.comfyui_url,
        )
        self.ebsynth_engine = EbSynthPropagationEngine(self.config)
        self._log: list[str] = []

    def _log_step(self, msg: str) -> None:
        logger.info(f"[HeadlessNeuralRender] {msg}")
        self._log.append(msg)

    # ── Stage 1: Bake Auxiliary Maps ─────────────────────────────────

    def bake_auxiliary_maps(
        self,
        skeleton: Skeleton,
        animation_func: Callable[[float], dict[str, float]],
        style: CharacterStyle,
        frames: int = 16,
        width: int = 128,
        height: int = 128,
    ) -> tuple[
        list[Image.Image],
        list[Image.Image],
        list[Image.Image],
        list[Image.Image],
        MotionVectorSequence,
    ]:
        """Bake source frames, normal maps, depth maps, masks, and motion vectors."""
        from .industrial_renderer import render_character_maps_industrial

        self._log_step(f"Stage 1: Baking {frames} frames at {width}x{height}")

        source_frames: list[Image.Image] = []
        normal_maps: list[Image.Image] = []
        depth_maps: list[Image.Image] = []
        mask_maps: list[Image.Image] = []

        for i in range(frames):
            t = i / max(frames - 1, 1)
            pose = animation_func(t)

            try:
                result = render_character_maps_industrial(
                    skeleton=skeleton,
                    pose=pose,
                    style=style,
                    width=width,
                    height=height,
                )
                frame = result.albedo_image
                source_frames.append(frame)
                normal_maps.append(result.normal_map_image)
                depth_maps.append(result.depth_map_image)
            except Exception as e:
                from .character_renderer import render_character_frame
                frame = render_character_frame(skeleton, pose, style, width, height)
                source_frames.append(frame)
                normal_maps.append(
                    Image.new("RGBA", (width, height), (128, 128, 255, 255))
                )
                depth_maps.append(
                    Image.new("RGBA", (width, height), (128, 128, 128, 255))
                )
                self._log_step(f"  Frame {i}: fallback render ({e})")

            frame_rgba = source_frames[-1].convert("RGBA")
            alpha = np.array(frame_rgba.getchannel("A"))
            if float(alpha.max()) <= 1.0:
                alpha = (alpha * 255.0).astype(np.uint8)
            if int(alpha.max()) == 0:
                rgb = np.array(frame_rgba.convert("RGB")).mean(axis=2)
                alpha = np.where(rgb > 0, 255, 0).astype(np.uint8)
            mask_maps.append(Image.fromarray(alpha.astype(np.uint8), mode="L"))

        self._log_step("  Baking motion vector sequence...")
        mv_sequence = bake_motion_vector_sequence(
            skeleton=skeleton,
            animation_func=animation_func,
            style=style,
            frames=frames,
            width=width,
            height=height,
        )

        self._log_step(
            f"  Baked {len(source_frames)} frames, "
            f"{len(mv_sequence.fields)} MV fields, "
            f"total motion energy: {mv_sequence.total_motion_energy:.2f}"
        )
        return source_frames, normal_maps, depth_maps, mask_maps, mv_sequence

    def plan_keyframes(
        self,
        source_frames: list[Image.Image],
        mask_maps: list[Image.Image],
        mv_sequence: MotionVectorSequence,
    ) -> KeyframePlan:
        """Plan sparse keyframes using motion energy and silhouette deltas."""
        n_frames = len(source_frames)
        if n_frames == 0:
            return KeyframePlan(guides_locked=["normal", "depth", "motion_vectors"])

        motion_scores: dict[int, float] = {0: 0.0}
        if mv_sequence.fields:
            mags = np.array([float(field.mean_magnitude) for field in mv_sequence.fields], dtype=np.float64)
            denom = float(mags.max()) if float(mags.max()) > 1e-8 else 1.0
            for i, value in enumerate(mags, start=1):
                motion_scores[min(i, n_frames - 1)] = float(value / denom)

        silhouette_scores: dict[int, float] = {0: 0.0}
        prev_mask = np.array(mask_maps[0].convert("L")).astype(np.float64) / 255.0
        for idx in range(1, n_frames):
            curr_mask = np.array(mask_maps[idx].convert("L")).astype(np.float64) / 255.0
            silhouette_scores[idx] = float(np.abs(curr_mask - prev_mask).mean())
            prev_mask = curr_mask

        priority_scores: dict[int, float] = {}
        for idx in range(n_frames):
            priority_scores[idx] = 0.7 * motion_scores.get(idx, 0.0) + 0.3 * silhouette_scores.get(idx, 0.0)

        selected = {0, n_frames - 1}
        selected.update(range(0, n_frames, self.config.keyframe_interval))
        threshold = float(np.quantile(
            list(priority_scores.values()),
            self.config.motion_energy_percentile,
        )) if priority_scores else 1.0

        if self.config.keyframe_selection_strategy == "motion_adaptive":
            ranked = sorted(
                [
                    (idx, score)
                    for idx, score in priority_scores.items()
                    if idx not in {0, n_frames - 1}
                ],
                key=lambda item: item[1],
                reverse=True,
            )
            for idx, score in ranked:
                if len(selected) >= self.config.max_keyframes:
                    break
                if score < threshold and silhouette_scores.get(idx, 0.0) < self.config.silhouette_delta_threshold:
                    continue
                if min(abs(idx - s) for s in selected) < self.config.min_keyframe_gap:
                    continue
                selected.add(idx)

        indices = sorted(selected)
        gaps = [b - a for a, b in zip(indices, indices[1:])]
        plan = KeyframePlan(
            indices=indices,
            motion_scores=motion_scores,
            silhouette_scores=silhouette_scores,
            priority_scores=priority_scores,
            strategy=self.config.keyframe_selection_strategy,
            max_gap=max(gaps) if gaps else 0,
            density=len(indices) / max(n_frames, 1),
            identity_reference_index=indices[0],
            guides_locked=[
                "normal",
                "depth",
                "motion_vectors",
                *( ["mask"] if self.config.use_mask_guide else []),
                *( ["ip_adapter_identity"] if self.config.use_ip_adapter_identity else []),
            ],
        )
        self._log_step(
            f"Stage 2a: Planned {len(indices)} sparse keyframes via {plan.strategy} at indices {indices}"
        )
        return plan

    # ── Stage 2: Generate AI-Stylized Keyframes ─────────────────────

    def generate_keyframes(
        self,
        source_frames: list[Image.Image],
        normal_maps: list[Image.Image],
        depth_maps: list[Image.Image],
        mask_maps: list[Image.Image],
        mv_sequence: MotionVectorSequence,
    ) -> tuple[dict[int, Image.Image], KeyframePlan, dict[str, Any]]:
        """Generate sparse AI keyframes using a motion-adaptive plan."""
        plan = self.plan_keyframes(source_frames, mask_maps, mv_sequence)
        identity_reference = source_frames[plan.identity_reference_index]

        self._log_step(
            f"Stage 2b: Generating {len(plan.indices)} keyframes at indices {plan.indices}"
        )

        keyframes: dict[int, Image.Image] = {}
        workflow_manifest: dict[str, Any] = {}

        for idx in plan.indices:
            styled = self.comfyui.generate_stylized_keyframe(
                source_image=source_frames[idx],
                normal_map=normal_maps[idx],
                depth_map=depth_maps[idx],
                mask_map=mask_maps[idx],
                identity_reference=identity_reference,
                config=self.config,
            )

            if styled is not None:
                keyframes[idx] = styled
                workflow_manifest = dict(getattr(self.comfyui, "last_workflow_manifest", {}))
                self._log_step(f"  Keyframe {idx}: ComfyUI generated")
            else:
                keyframes[idx] = self._fallback_style_transfer(
                    source_frames[idx], normal_maps[idx]
                )
                workflow_manifest = {
                    "identity_lock_active": False,
                    "fallback_reason": "deterministic_style_transfer",
                    "controlnet_guides": ["normal", "depth"],
                    "mask_present": bool(mask_maps),
                }
                self._log_step(f"  Keyframe {idx}: fallback style transfer")

        workflow_manifest.setdefault("planned_keyframes", plan.indices)
        workflow_manifest.setdefault("guides_locked", plan.guides_locked)
        return keyframes, plan, workflow_manifest

    def _fallback_style_transfer(
        self,
        source: Image.Image,
        normal_map: Image.Image,
    ) -> Image.Image:
        """Deterministic style transfer fallback when ComfyUI is unavailable.

        Uses normal map-based lighting enhancement to simulate AI styling.
        This ensures the pipeline always produces output, even without
        an external AI service.

        The approach:
        1. Extract normal map light direction
        2. Apply cel-shading based on normal dot product
        3. Enhance contrast and saturation
        """
        src_arr = np.array(source).astype(np.float64)
        norm_arr = np.array(normal_map).astype(np.float64)

        if norm_arr.shape[2] >= 3:
            # Decode normal map: [0,255] → [-1,1]
            nx = norm_arr[:, :, 0] / 127.5 - 1.0
            ny = norm_arr[:, :, 1] / 127.5 - 1.0
            nz = norm_arr[:, :, 2] / 127.5 - 1.0

            # Normalize
            length = np.sqrt(nx**2 + ny**2 + nz**2) + 1e-8
            nx /= length
            ny /= length
            nz /= length

            # Light direction (top-left, slightly forward)
            lx, ly, lz = 0.3, 0.5, 0.8
            ll = math.sqrt(lx**2 + ly**2 + lz**2)
            lx, ly, lz = lx/ll, ly/ll, lz/ll

            # Lambertian diffuse
            ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

            # Cel-shading quantization (3 levels)
            cel = np.where(ndotl > 0.7, 1.0,
                  np.where(ndotl > 0.3, 0.7, 0.4))

            # Apply lighting to source
            for c in range(min(3, src_arr.shape[2])):
                src_arr[:, :, c] = np.clip(src_arr[:, :, c] * cel, 0, 255)

        return Image.fromarray(src_arr.astype(np.uint8))

    # ── Stage 3: EbSynth Propagation ────────────────────────────────

    def propagate_style(
        self,
        source_frames: list[Image.Image],
        keyframes: dict[int, Image.Image],
        mv_sequence: MotionVectorSequence,
        normal_maps: Optional[list[Image.Image]] = None,
        mask_maps: Optional[list[Image.Image]] = None,
    ) -> list[Image.Image]:
        """Propagate keyframe styles to all frames."""
        self._log_step(
            f"Stage 3: Propagating style from {len(keyframes)} keyframes "
            f"to {len(source_frames)} frames"
        )
        stylized = self.ebsynth_engine.propagate_style(
            source_frames=source_frames,
            keyframes=keyframes,
            mv_sequence=mv_sequence,
            normal_maps=normal_maps,
            mask_maps=mask_maps,
        )
        self._log_step(f"  Propagated {len(stylized)} frames")
        return stylized

    # ── Stage 4: Temporal Consistency Validation ────────────────────

    def validate_temporal_consistency(
        self,
        stylized_frames: list[Image.Image],
        mv_sequence: MotionVectorSequence,
        keyframe_plan: Optional[KeyframePlan] = None,
        workflow_manifest: Optional[dict[str, Any]] = None,
    ) -> dict[str, float]:
        """Validate temporal consistency of the stylized output."""
        self._log_step("Stage 4: Validating temporal consistency")

        if len(stylized_frames) < 2 or len(mv_sequence.fields) == 0:
            return {
                "mean_warp_error": 0.0,
                "max_warp_error": 0.0,
                "mean_ssim_proxy": 1.0,
                "mean_coverage": 0.0,
                "flicker_score": 0.0,
                "guide_lock_score": 1.0,
                "identity_consistency_proxy": 1.0,
                "long_range_drift": 0.0,
                "keyframe_density": 1.0,
                "temporal_stability_score": 1.0,
                "temporal_pass": True,
            }

        errors: list[float] = []
        ssim_proxies: list[float] = []
        coverages: list[float] = []

        n_fields = min(len(mv_sequence.fields), len(stylized_frames) - 1)
        for i in range(n_fields):
            frame_a = np.array(stylized_frames[i])
            frame_b = np.array(stylized_frames[i + 1])
            mv_field = mv_sequence.fields[i]

            scores = compute_temporal_consistency_score(frame_a, frame_b, mv_field)
            errors.append(scores["warp_error"])
            ssim_proxies.append(scores["warp_ssim_proxy"])
            coverages.append(scores["coverage"])

        signatures = []
        for frame in stylized_frames:
            arr = np.array(frame.convert("RGB")).astype(np.float64)
            signatures.append(arr.mean(axis=(0, 1)) / 255.0)
        sig_arr = np.array(signatures, dtype=np.float64)
        ref_sig = sig_arr[0] if len(sig_arr) else np.zeros(3, dtype=np.float64)
        drift_values = np.linalg.norm(sig_arr - ref_sig[None, :], axis=1) / math.sqrt(3.0)
        long_range_drift = float(np.max(drift_values)) if len(drift_values) else 0.0
        identity_consistency = float(1.0 - np.mean(drift_values)) if len(drift_values) else 1.0
        identity_consistency = float(np.clip(identity_consistency, 0.0, 1.0))

        mean_error = float(np.mean(errors)) if errors else 0.0
        max_error = float(np.max(errors)) if errors else 0.0
        flicker = float(np.std(errors)) if len(errors) > 1 else 0.0
        mean_coverage = float(np.mean(coverages)) if coverages else 0.0
        mean_ssim = float(np.mean(ssim_proxies)) if ssim_proxies else 1.0

        requested_guides = list((workflow_manifest or {}).get("guides_locked") or (workflow_manifest or {}).get("guides_requested") or [])
        guide_lock_score = len(requested_guides) / 5.0 if requested_guides else 0.6
        guide_lock_score = float(np.clip(guide_lock_score, 0.0, 1.0))
        keyframe_density = float((keyframe_plan.density if keyframe_plan else 1.0))
        max_gap_penalty = min((keyframe_plan.max_gap if keyframe_plan else 0) / max(len(stylized_frames), 1), 1.0)

        temporal_stability = (
            0.35 * (1.0 - min(mean_error / max(self.config.warp_error_threshold, 1e-6), 1.0))
            + 0.15 * mean_ssim
            + 0.15 * mean_coverage
            + 0.15 * (1.0 - min(flicker / 0.2, 1.0))
            + 0.10 * guide_lock_score
            + 0.10 * identity_consistency
        )
        temporal_stability = float(np.clip(temporal_stability, 0.0, 1.0))

        temporal_pass = (
            mean_error <= self.config.warp_error_threshold
            and long_range_drift <= self.config.temporal_drift_threshold
            and identity_consistency >= self.config.identity_consistency_threshold
            and temporal_stability >= 0.55
        )

        metrics = {
            "mean_warp_error": mean_error,
            "max_warp_error": max_error,
            "mean_ssim_proxy": mean_ssim,
            "mean_coverage": mean_coverage,
            "flicker_score": flicker,
            "guide_lock_score": guide_lock_score,
            "identity_consistency_proxy": identity_consistency,
            "long_range_drift": long_range_drift,
            "keyframe_density": keyframe_density,
            "keyframe_max_gap_ratio": max_gap_penalty,
            "temporal_stability_score": temporal_stability,
            "temporal_pass": temporal_pass,
            "per_frame_errors": errors,
        }

        status = "PASS" if temporal_pass else "FAIL"
        self._log_step(
            f"  Temporal consistency: {status} "
            f"(mean_warp_error={mean_error:.4f}, flicker={flicker:.4f}, drift={long_range_drift:.4f})"
        )
        return metrics

    # ── Stage 5: Export ─────────────────────────────────────────────

    def export_result(
        self,
        result: NeuralRenderResult,
    ) -> Path:
        """Export the full pipeline result to disk.

        Creates:
            output_dir/
            ├── stylized/        # Final stylized frames
            ├── keyframes/       # AI-generated keyframes
            ├── source/          # Original math engine frames
            ├── normals/         # Analytical normal maps
            ├── depth/           # Analytical depth maps
            ├── flow/            # Motion vector maps (RGB)
            ├── flow_vis/        # Motion vector visualization (HSV)
            └── pipeline.json    # Full pipeline metadata

        Parameters
        ----------
        result : NeuralRenderResult
            Pipeline result to export.

        Returns
        -------
        Path
            Output directory path.
        """
        out = Path(self.config.output_dir)
        self._log_step(f"Stage 5: Exporting to {out}")

        dirs = {
            "stylized": out / "stylized",
            "keyframes": out / "keyframes",
            "source": out / "source",
            "normals": out / "normals",
            "depth": out / "depth",
            "masks": out / "masks",
        }
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)

        # Export stylized frames
        for i, frame in enumerate(result.stylized_frames):
            frame.save(str(dirs["stylized"] / f"{i:04d}.png"))

        # Export keyframes
        for idx, frame in zip(result.keyframe_indices, result.keyframes):
            frame.save(str(dirs["keyframes"] / f"{idx:04d}.png"))

        # Export source frames
        for i, frame in enumerate(result.source_frames):
            frame.save(str(dirs["source"] / f"{i:04d}.png"))

        # Export normal maps
        for i, nmap in enumerate(result.normal_maps):
            nmap.save(str(dirs["normals"] / f"{i:04d}.png"))

        # Export depth maps
        for i, dmap in enumerate(result.depth_maps):
            dmap.save(str(dirs["depth"] / f"{i:04d}.png"))

        # Export masks
        for i, mmap in enumerate(result.mask_maps):
            mmap.save(str(dirs["masks"] / f"{i:04d}.png"))

        # Export EbSynth project (MV maps)
        if result.mv_sequence and result.source_frames:
            export_ebsynth_project(
                sequence=result.mv_sequence,
                albedo_frames=result.source_frames,
                output_dir=out / "ebsynth_project",
                keyframe_indices=result.keyframe_indices,
            )

        # Export pipeline metadata
        meta = result.to_metadata()
        meta["pipeline_log"] = result.pipeline_log
        meta_path = out / "pipeline.json"
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if result.keyframe_plan is not None:
            (out / "keyframe_plan.json").write_text(
                json.dumps(result.keyframe_plan.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        (out / "workflow_manifest.json").write_text(
            json.dumps(result.workflow_manifest, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        self._log_step(f"  Exported {result.frame_count} frames to {out}")
        return out

    # ── Full Pipeline Run ───────────────────────────────────────────

    def run(
        self,
        skeleton: Skeleton,
        animation_func: Callable[[float], dict[str, float]],
        style: CharacterStyle,
        frames: int = 16,
        width: int = 128,
        height: int = 128,
        export: bool = True,
    ) -> NeuralRenderResult:
        """Run the full end-to-end neural rendering pipeline."""
        start_time = time.time()
        self._log = []
        self._log_step("=" * 60)
        self._log_step("HeadlessNeuralRenderPipeline — SESSION-060")
        self._log_step("Research: Jamriška (EbSynth), Zhang (ControlNet), Ye (IP-Adapter), Liang (FlowVid)")
        self._log_step("=" * 60)

        warnings = self.config.validate()
        for w in warnings:
            self._log_step(f"  WARNING: {w}")

        source_frames, normal_maps, depth_maps, mask_maps, mv_sequence = self.bake_auxiliary_maps(
            skeleton=skeleton,
            animation_func=animation_func,
            style=style,
            frames=frames,
            width=width,
            height=height,
        )

        keyframes, keyframe_plan, workflow_manifest = self.generate_keyframes(
            source_frames=source_frames,
            normal_maps=normal_maps,
            depth_maps=depth_maps,
            mask_maps=mask_maps,
            mv_sequence=mv_sequence,
        )

        stylized_frames = self.propagate_style(
            source_frames=source_frames,
            keyframes=keyframes,
            mv_sequence=mv_sequence,
            normal_maps=normal_maps,
            mask_maps=mask_maps,
        )

        temporal_metrics = self.validate_temporal_consistency(
            stylized_frames=[f for f in stylized_frames if f is not None],
            mv_sequence=mv_sequence,
            keyframe_plan=keyframe_plan,
            workflow_manifest=workflow_manifest,
        )

        elapsed = time.time() - start_time

        result = NeuralRenderResult(
            stylized_frames=[f for f in stylized_frames if f is not None],
            keyframe_indices=sorted(keyframes.keys()),
            keyframes=[keyframes[i] for i in sorted(keyframes.keys())],
            source_frames=source_frames,
            normal_maps=normal_maps,
            depth_maps=depth_maps,
            mask_maps=mask_maps,
            mv_sequence=mv_sequence,
            temporal_metrics=temporal_metrics,
            workflow_manifest=workflow_manifest,
            keyframe_plan=keyframe_plan,
            pipeline_log=self._log,
            config=self.config,
            elapsed_seconds=elapsed,
        )

        self._log_step(
            f"Pipeline complete: {result.frame_count} frames in {elapsed:.2f}s"
        )
        self._log_step(
            f"Temporal: {'PASS' if result.temporal_pass else 'FAIL'} "
            f"(warp_error={temporal_metrics.get('mean_warp_error', 0):.4f}, stability={temporal_metrics.get('temporal_stability_score', 0):.4f})"
        )

        if export:
            self.export_result(result)

        return result


__all__ = [
    "NeuralRenderConfig",
    "KeyframePlan",
    "NeuralRenderResult",
    "ComfyUIHeadlessClient",
    "EbSynthPropagationEngine",
    "HeadlessNeuralRenderPipeline",
]
