"""
SESSION-202 Telemetry Adapter — WebSocket → Gradio Progress Pump.

This module implements the **双向遥测进度回显** bridge that translates
SESSION-200's WebSocket telemetry events (progress / executing / status)
into Gradio-compatible progress updates via Python generators.

Architecture:
- Consumes the ``telemetry_log`` list from ``ComfyUIClient._execute_live_pipeline``
- Transforms raw WS events into structured progress dicts for the Gradio UI
- Uses yield/generator pattern to stream updates (反页面假死红线)
- Falls back gracefully when WebSocket is unavailable (pure CPU mode)

Industrial References:
- Event-Driven UI Update (Python yield/generator mechanism)
- WebSocket telemetry pump pattern (SESSION-200)
- Gradio gr.Progress streaming integration
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Telemetry Event Transformer
# ═══════════════════════════════════════════════════════════════════════════

def transform_telemetry_event(event: dict[str, Any]) -> dict[str, Any]:
    """Transform a raw WebSocket telemetry event into a Gradio progress dict.

    Parameters
    ----------
    event : dict
        Raw telemetry event from ComfyUI WebSocket, typically containing:
        - ``event_type``: "progress" | "executing" | "status" | "error"
        - ``data``: event-specific payload

    Returns
    -------
    dict
        Gradio-compatible progress dict with keys:
        - ``stage``, ``progress``, ``message``, ``gallery``, ``video``, ``error``
    """
    event_type = event.get("event_type", "unknown")
    data = event.get("data", {})

    if event_type == "progress":
        value = data.get("value", 0)
        max_val = data.get("max", 100)
        pct = value / max_val if max_val > 0 else 0.0
        return {
            "stage": "ai_render",
            "progress": 0.3 + pct * 0.6,  # Map to 30%–90% range
            "message": f"[🎨 AI 渲染] 推流进度: {value}/{max_val} ({pct*100:.0f}%)",
            "gallery": [],
            "video": None,
            "error": None,
        }

    elif event_type == "executing":
        node_id = data.get("node")
        if node_id is None:
            return {
                "stage": "ai_render_complete",
                "progress": 0.9,
                "message": "[✅ AI 渲染] 推流完成，正在回收输出...",
                "gallery": [],
                "video": None,
                "error": None,
            }
        return {
            "stage": "ai_render",
            "progress": 0.5,
            "message": f"[🎨 AI 渲染] 正在执行节点 {node_id}...",
            "gallery": [],
            "video": None,
            "error": None,
        }

    elif event_type == "status":
        queue_remaining = data.get("status", {}).get("exec_info", {}).get(
            "queue_remaining", 0
        )
        return {
            "stage": "queue",
            "progress": 0.25,
            "message": f"[📋 队列] 剩余任务: {queue_remaining}",
            "gallery": [],
            "video": None,
            "error": None,
        }

    elif event_type == "error":
        return {
            "stage": "error",
            "progress": 0.0,
            "message": f"[❌ 错误] {data.get('message', 'Unknown error')}",
            "gallery": [],
            "video": None,
            "error": data.get("message", "Unknown error"),
        }

    else:
        return {
            "stage": event_type,
            "progress": 0.5,
            "message": f"[📡 遥测] {event_type}: {data}",
            "gallery": [],
            "video": None,
            "error": None,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Output Collector
# ═══════════════════════════════════════════════════════════════════════════

def collect_render_outputs(
    outputs_dir: str | Path,
) -> tuple[list[str], str | None]:
    """Scan the outputs directory for rendered frames and videos.

    Parameters
    ----------
    outputs_dir : str or Path
        Path to ``outputs/final_renders/`` directory.

    Returns
    -------
    tuple[list[str], str | None]
        (gallery_paths, video_path) — list of image paths and optional
        video path for Gradio Gallery and Video components.
    """
    outputs_path = Path(outputs_dir)
    gallery: list[str] = []
    video: str | None = None

    if not outputs_path.exists():
        return gallery, video

    # Collect images
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        gallery.extend(str(p) for p in sorted(outputs_path.glob(ext)))

    # Find most recent video
    videos = sorted(outputs_path.glob("*.mp4"), key=os.path.getmtime)
    if videos:
        video = str(videos[-1])

    # Cap gallery at 50 most recent
    gallery = gallery[-50:]

    return gallery, video


def stream_telemetry_log(
    telemetry_log: list[dict[str, Any]],
) -> Generator[dict[str, Any], None, None]:
    """Stream telemetry log entries as Gradio progress events.

    This generator transforms a list of raw telemetry events into
    Gradio-compatible progress dicts, suitable for use with the
    ``yield`` pattern in Gradio event handlers.

    Parameters
    ----------
    telemetry_log : list[dict]
        List of raw WebSocket telemetry events.

    Yields
    ------
    dict
        Gradio-compatible progress events.
    """
    for event in telemetry_log:
        yield transform_telemetry_event(event)
