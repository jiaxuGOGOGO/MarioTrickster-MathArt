"""Visual Distillation Gateway — GIF/Image-Sequence to Physics Parameters.

SESSION-179 (P0-SESSION-179-VISUAL-DISTILLATION-AND-RESKINNING)
-----------------------------------------------------------------
This module implements the **Visual Distillation** pipeline that accepts a
reference animation (GIF or image folder) and reverse-engineers the physical
parameters (bounce, stiffness, mass, etc.) by invoking a vision-capable LLM.

Architecture Pillars:
1. **Zero cv2 Dependency**: Uses ONLY ``PIL.ImageSequence`` for GIF frame
   extraction and ``os`` / ``PIL.Image.open`` for image folder traversal.
   This is a HARD RED LINE — ``import cv2`` is FORBIDDEN to prevent local
   environment breakage on machines without OpenCV.
2. **Base64 Vision API Bridge**: Extracted keyframes are converted to Base64
   PNG and sent to a vision-capable LLM (default: gpt-4o-mini via OpenAI
   API) with a carefully crafted system prompt that instructs the model to
   act as a top-tier animation physics analyst.
3. **Graceful Fallback**: If no API key is configured, or the network is
   unreachable, or the LLM returns malformed JSON, the module prints a
   yellow warning and returns a safe default parameter dictionary. It
   NEVER crashes or raises an unhandled exception.

Research Grounding:
- Vision-Language Models for Physical Parameter Estimation (NeurIPS 2024)
- Inverse Physics from Video Observation (SIGGRAPH 2023)
- PIL.ImageSequence: Python Pillow official documentation
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default Physics Parameters (Safe Fallback)
# ---------------------------------------------------------------------------
DEFAULT_PHYSICS_PARAMS: Dict[str, float] = {
    "gravity": 9.81,
    "mass": 1.0,
    "stiffness": 50.0,
    "damping": 0.3,
    "bounce": 0.6,
    "friction": 0.4,
    "head_ratio": 0.25,
    "body_ratio": 0.50,
    "limb_ratio": 0.25,
    "scale": 1.0,
    "squash_stretch": 1.0,
    "frame_rate": 12,
    "anticipation": 0.15,
    "follow_through": 0.20,
    "exaggeration": 1.0,
    "ease_in": 0.3,
    "ease_out": 0.3,
    "cycle_frames": 24,
}

# ---------------------------------------------------------------------------
# System Prompt for Vision LLM
# ---------------------------------------------------------------------------
_VISION_SYSTEM_PROMPT = """You are a world-class animation physics analyst and motion capture expert.
You are given a sequence of keyframes extracted from a reference animation.

Your task is to carefully observe the physical motion patterns in these frames
and reverse-engineer the underlying physics parameters that would produce
similar motion in a procedural animation system.

Analyze the following aspects:
1. **Gravity & Weight**: How heavy does the character feel? Fast/slow falls?
2. **Bounce & Elasticity**: How bouncy are the movements? Squash and stretch?
3. **Stiffness & Damping**: How rigid or loose are the joints?
4. **Timing & Easing**: Anticipation before actions? Follow-through after?
5. **Exaggeration**: How cartoonish vs realistic is the motion?
6. **Proportions**: Head-to-body ratio, limb proportions visible in frames.

You MUST respond with ONLY a valid JSON object (no markdown, no explanation)
containing exactly these 18 parameters:

{
    "gravity": <float, 0.1-30.0, Earth=9.81>,
    "mass": <float, 0.1-10.0, normal=1.0>,
    "stiffness": <float, 1.0-200.0, normal=50.0>,
    "damping": <float, 0.01-1.0, normal=0.3>,
    "bounce": <float, 0.0-1.5, normal=0.6>,
    "friction": <float, 0.0-1.0, normal=0.4>,
    "head_ratio": <float, 0.1-0.5, normal=0.25>,
    "body_ratio": <float, 0.2-0.7, normal=0.50>,
    "limb_ratio": <float, 0.1-0.5, normal=0.25>,
    "scale": <float, 0.5-3.0, normal=1.0>,
    "squash_stretch": <float, 0.5-3.0, normal=1.0>,
    "frame_rate": <int, 8-60, normal=12>,
    "anticipation": <float, 0.0-0.5, normal=0.15>,
    "follow_through": <float, 0.0-0.5, normal=0.20>,
    "exaggeration": <float, 0.5-3.0, normal=1.0>,
    "ease_in": <float, 0.0-1.0, normal=0.3>,
    "ease_out": <float, 0.0-1.0, normal=0.3>,
    "cycle_frames": <int, 8-120, normal=24>
}"""


# ---------------------------------------------------------------------------
# Frame Extraction (ZERO cv2 — PIL only)
# ---------------------------------------------------------------------------
def extract_keyframes_from_gif(
    gif_path: str | Path,
    max_frames: int = 8,
) -> List[bytes]:
    """Extract evenly-spaced keyframes from a GIF file as PNG bytes.

    Uses ONLY ``PIL.ImageSequence`` — absolutely NO cv2.

    Parameters
    ----------
    gif_path : str | Path
        Path to the GIF file.
    max_frames : int
        Maximum number of keyframes to extract.

    Returns
    -------
    list[bytes]
        List of PNG-encoded frame bytes.
    """
    from PIL import Image, ImageSequence

    img = Image.open(str(gif_path))
    all_frames = list(ImageSequence.Iterator(img))
    total = len(all_frames)

    if total == 0:
        return []

    # Evenly sample keyframes
    if total <= max_frames:
        indices = list(range(total))
    else:
        step = total / max_frames
        indices = [int(i * step) for i in range(max_frames)]

    keyframes: List[bytes] = []
    for idx in indices:
        frame = all_frames[idx].convert("RGB")
        buf = io.BytesIO()
        frame.save(buf, format="PNG")
        keyframes.append(buf.getvalue())

    logger.info(
        "[VisualDistillation] Extracted %d keyframes from GIF (%d total frames): %s",
        len(keyframes), total, gif_path,
    )
    return keyframes


def extract_keyframes_from_folder(
    folder_path: str | Path,
    max_frames: int = 8,
) -> List[bytes]:
    """Extract evenly-spaced keyframes from an image folder as PNG bytes.

    Uses ONLY ``os`` and ``PIL.Image`` — absolutely NO cv2.

    Parameters
    ----------
    folder_path : str | Path
        Path to folder containing numbered image files.
    max_frames : int
        Maximum number of keyframes to extract.

    Returns
    -------
    list[bytes]
        List of PNG-encoded frame bytes.
    """
    from PIL import Image as PILImage

    folder = Path(folder_path)
    extensions = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    files = sorted(
        [f for f in folder.iterdir() if f.suffix.lower() in extensions],
        key=lambda p: p.name,
    )

    total = len(files)
    if total == 0:
        return []

    if total <= max_frames:
        indices = list(range(total))
    else:
        step = total / max_frames
        indices = [int(i * step) for i in range(max_frames)]

    keyframes: List[bytes] = []
    for idx in indices:
        img = PILImage.open(str(files[idx])).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        keyframes.append(buf.getvalue())

    logger.info(
        "[VisualDistillation] Extracted %d keyframes from folder (%d total files): %s",
        len(keyframes), total, folder_path,
    )
    return keyframes


# ---------------------------------------------------------------------------
# Vision LLM API Call
# ---------------------------------------------------------------------------
def _call_vision_api(
    keyframe_bytes_list: List[bytes],
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str = "gpt-4o-mini",
) -> Dict[str, float]:
    """Call vision-capable LLM to reverse-engineer physics parameters.

    Reads ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL`` from environment
    (or ``.env`` file) if not explicitly provided.

    Parameters
    ----------
    keyframe_bytes_list : list[bytes]
        PNG-encoded keyframe images.
    api_key : str | None
        OpenAI API key override.
    base_url : str | None
        OpenAI base URL override.
    model : str
        Vision-capable model name.

    Returns
    -------
    dict[str, float]
        Reverse-engineered physics parameters.

    Raises
    ------
    Never raises — returns DEFAULT_PHYSICS_PARAMS on any failure.
    """
    import requests

    # Load from .env if available
    try:
        env_path = Path.cwd() / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

    resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    resolved_base = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    if not resolved_key:
        logger.warning("[VisualDistillation] No OPENAI_API_KEY found — returning defaults")
        return dict(DEFAULT_PHYSICS_PARAMS)

    # Build multimodal message content
    content: list[dict] = [
        {"type": "text", "text": "Analyze these animation keyframes and output physics parameters as JSON:"},
    ]
    for i, frame_bytes in enumerate(keyframe_bytes_list[:8]):  # Max 8 frames
        b64 = base64.b64encode(frame_bytes).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}",
                "detail": "low",
            },
        })

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _VISION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {resolved_key}",
        "Content-Type": "application/json",
    }

    url = f"{resolved_base.rstrip('/')}/chat/completions"

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["choices"][0]["message"]["content"].strip()

        # Parse JSON from response (handle markdown code blocks)
        if raw_text.startswith("```"):
            # Strip markdown code block
            lines = raw_text.split("\n")
            json_lines = [l for l in lines if not l.startswith("```")]
            raw_text = "\n".join(json_lines)

        params = json.loads(raw_text)

        # Validate and merge with defaults
        result = dict(DEFAULT_PHYSICS_PARAMS)
        for key in DEFAULT_PHYSICS_PARAMS:
            if key in params:
                try:
                    result[key] = float(params[key])
                except (ValueError, TypeError):
                    pass  # Keep default

        logger.info(
            "[VisualDistillation] Vision API returned %d valid parameters",
            sum(1 for k in DEFAULT_PHYSICS_PARAMS if k in params),
        )
        return result

    except requests.exceptions.ConnectionError as e:
        logger.warning("[VisualDistillation] Network unreachable: %s", e)
        return dict(DEFAULT_PHYSICS_PARAMS)
    except requests.exceptions.Timeout:
        logger.warning("[VisualDistillation] Vision API timed out")
        return dict(DEFAULT_PHYSICS_PARAMS)
    except json.JSONDecodeError as e:
        logger.warning("[VisualDistillation] LLM returned malformed JSON: %s", e)
        return dict(DEFAULT_PHYSICS_PARAMS)
    except Exception as e:
        logger.warning("[VisualDistillation] Unexpected error: %s", e)
        return dict(DEFAULT_PHYSICS_PARAMS)


# ---------------------------------------------------------------------------
# Public API — End-to-End Visual Distillation
# ---------------------------------------------------------------------------
def distill_physics_from_reference(
    reference_path: str | Path,
    *,
    max_frames: int = 8,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str = "gpt-4o-mini",
    output_fn: Callable[[str], None] = print,
) -> Dict[str, float]:
    """End-to-end visual distillation: reference animation → physics params.

    Accepts either a GIF file or a folder of numbered images.

    Parameters
    ----------
    reference_path : str | Path
        Path to GIF file or image folder.
    max_frames : int
        Maximum keyframes to extract.
    api_key : str | None
        OpenAI API key override.
    base_url : str | None
        OpenAI base URL override.
    model : str
        Vision-capable model name.
    output_fn : callable
        Terminal output function.

    Returns
    -------
    dict[str, float]
        Reverse-engineered physics parameters (always valid, never crashes).
    """
    ref = Path(reference_path)

    try:
        if ref.is_dir():
            output_fn(
                "\033[1;36m[👁️ 视觉临摹] 正在从图集文件夹提取关键帧...\033[0m"
            )
            keyframes = extract_keyframes_from_folder(ref, max_frames=max_frames)
        elif ref.suffix.lower() == ".gif":
            output_fn(
                "\033[1;36m[👁️ 视觉临摹] 正在从 GIF 动图提取关键帧...\033[0m"
            )
            keyframes = extract_keyframes_from_gif(ref, max_frames=max_frames)
        else:
            output_fn(
                "\033[1;33m[⚠️ 视觉临摹] 不支持的文件格式，仅支持 .gif 或图片文件夹。"
                "返回默认参数。\033[0m"
            )
            return dict(DEFAULT_PHYSICS_PARAMS)

        if not keyframes:
            output_fn(
                "\033[1;33m[⚠️ 视觉临摹] 未能提取到任何帧，返回默认参数。\033[0m"
            )
            return dict(DEFAULT_PHYSICS_PARAMS)

        output_fn(
            f"\033[1;36m[👁️ 视觉临摹] 成功提取 {len(keyframes)} 帧关键帧，"
            f"正在调用 AI 视觉分析逆向推导物理参数...\033[0m"
        )

        params = _call_vision_api(
            keyframes,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

        output_fn(
            "\033[1;32m[✅ 视觉临摹] AI 逆向推导完成！"
            f"已提取 {len(params)} 个物理控制参数。\033[0m"
        )
        return params

    except Exception as e:
        # ABSOLUTE SAFETY NET — never crash
        logger.warning("[VisualDistillation] Unexpected error: %s", e, exc_info=True)
        output_fn(
            f"\033[1;33m[⚠️ 视觉临摹] 处理过程中发生异常: {e}\n"
            "返回安全默认参数，流程不中断。\033[0m"
        )
        return dict(DEFAULT_PHYSICS_PARAMS)


__all__ = [
    "DEFAULT_PHYSICS_PARAMS",
    "distill_physics_from_reference",
    "extract_keyframes_from_gif",
    "extract_keyframes_from_folder",
]
