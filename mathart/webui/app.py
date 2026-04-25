"""
SESSION-202 P0-ZERO-DEFECT-WEB-WORKSPACE — Gradio Visual Production Console.

Pure Python Gradio application implementing the full-featured Web UI:
- **Left panel**: Control area with dynamic action dropdown, reference image
  upload, VFX toggle switches, and vibe text input.
- **Right panel**: Live progress monitoring, sequence frame gallery, and
  video playback component.

Architecture:
- Uses ``yield`` / generator pattern for streaming progress (反页面假死红线).
- Reads actions dynamically from ``OpenPoseGaitRegistry`` (严禁前端硬编码列表).
- Persists uploaded images via ``shutil.copy`` (反幽灵路径红线).
- Bridges to pipeline via ``WebUIBridge`` (Headless Dispatcher Bridge).

Industrial References:
- Gradio Reactive State Management (Blocks API + event listeners)
- Event-Driven UI Update (yield/generator streaming)
- Registry Pattern (IoC dynamic dropdown population)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy import to allow the module to be imported without Gradio installed
# (e.g., during testing with mocks).
_gr = None


def _import_gradio():
    """Lazy-import Gradio to avoid hard dependency at import time."""
    global _gr
    if _gr is None:
        import gradio as gr
        _gr = gr
    return _gr


def create_app() -> "gradio.Blocks":
    """Create and return the Gradio Blocks application.

    This is the main factory function. Call ``app = create_app()`` and then
    ``app.launch()`` to start the Web UI server.

    Returns
    -------
    gradio.Blocks
        The configured Gradio application ready to launch.
    """
    gr = _import_gradio()
    from mathart.webui.bridge import WebUIBridge

    bridge = WebUIBridge()
    available_actions = bridge.get_available_actions()

    # ═══════════════════════════════════════════════════════════════════
    #  Gradio Blocks Layout
    # ═══════════════════════════════════════════════════════════════════

    with gr.Blocks(
        title="MarioTrickster MathArt — 核动力渲染操作台",
        theme=gr.themes.Soft(),
        css="""
        .main-title {
            text-align: center;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 2em;
            font-weight: bold;
            margin-bottom: 0.5em;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 1em;
        }
        """,
    ) as app:

        # ── Header ───────────────────────────────────────────────────
        gr.HTML(
            '<div class="main-title">'
            "MarioTrickster MathArt"
            "</div>"
            '<div class="subtitle">'
            "SESSION-202 | 全功能可视化生产操作台 | "
            "Powered by IoC Registry Pattern + Catmull-Rom Spline Interpolation"
            "</div>"
        )

        with gr.Row():
            # ══════════════════════════════════════════════════════════
            #  LEFT PANEL — Control Area
            # ══════════════════════════════════════════════════════════
            with gr.Column(scale=1):
                gr.Markdown("## 🎮 控制面板")

                # Action dropdown — dynamically populated from registry
                action_dropdown = gr.Dropdown(
                    choices=available_actions,
                    value=available_actions[0] if available_actions else None,
                    label="🏃 动作选择 (OpenPoseGaitRegistry)",
                    info="从底层动态注册表读取，严禁前端硬编码",
                    interactive=True,
                )

                # Reference image upload with drag-and-drop
                ref_image = gr.Image(
                    label="🖼️ 参考图拖拽上传 (IPAdapter Reference)",
                    type="filepath",
                    sources=["upload"],
                    interactive=True,
                )

                # VFX Toggle switches
                gr.Markdown("### ⚡ VFX 物理特效开关")
                with gr.Row():
                    force_fluid = gr.Checkbox(
                        label="💧 流体 (Fluid)",
                        value=False,
                        interactive=True,
                    )
                    force_physics = gr.Checkbox(
                        label="🔮 物理 (Physics 3D)",
                        value=False,
                        interactive=True,
                    )
                with gr.Row():
                    force_cloth = gr.Checkbox(
                        label="🧵 布料 (Cloth)",
                        value=False,
                        interactive=True,
                    )
                    force_particles = gr.Checkbox(
                        label="✨ 粒子 (Particles)",
                        value=False,
                        interactive=True,
                    )

                # Vibe text input
                vibe_input = gr.Textbox(
                    label="🎨 氛围描述 (Vibe Description)",
                    placeholder="例如: 赛博朋克风格的像素角色在雨中奔跑...",
                    lines=2,
                    interactive=True,
                )

                # Launch button
                launch_btn = gr.Button(
                    "🚀 启动核动力渲染",
                    variant="primary",
                    size="lg",
                )

            # ══════════════════════════════════════════════════════════
            #  RIGHT PANEL — Progress & Gallery
            # ══════════════════════════════════════════════════════════
            with gr.Column(scale=2):
                gr.Markdown("## 📊 渲染监控与成片展示")

                # Progress display
                progress_text = gr.Textbox(
                    label="⏳ 实时进度",
                    value="等待启动...",
                    interactive=False,
                    lines=3,
                )
                progress_bar = gr.Slider(
                    minimum=0.0,
                    maximum=1.0,
                    value=0.0,
                    label="渲染进度",
                    interactive=False,
                )

                # Gallery for sequence frames
                gallery = gr.Gallery(
                    label="🖼️ 序列帧画廊 (outputs/final_renders/)",
                    columns=4,
                    rows=2,
                    height="auto",
                    object_fit="contain",
                )

                # Video playback
                video_output = gr.Video(
                    label="🎬 视频回放 (MP4)",
                    autoplay=True,
                )

        # ══════════════════════════════════════════════════════════════
        #  Event Handlers (Generator-based streaming)
        # ══════════════════════════════════════════════════════════════

        def on_launch(
            action: str,
            ref_img: str | None,
            fluid: bool,
            physics: bool,
            cloth: bool,
            particles: bool,
            vibe: str,
        ):
            """Generator handler for the launch button.

            Uses ``yield`` to stream progress updates to the frontend,
            preventing page freeze during long-running pipeline execution
            (反页面假死红线: 强制使用异步或生成器刷新 UI).
            """
            for event in bridge.dispatch_render(
                action_name=action or "",
                reference_image=ref_img,
                force_fluid=fluid,
                force_physics=physics,
                force_cloth=cloth,
                force_particles=particles,
                raw_vibe=vibe or "",
            ):
                stage = event.get("stage", "")
                progress = event.get("progress", 0.0)
                message = event.get("message", "")
                gallery_files = event.get("gallery", [])
                video_file = event.get("video")
                error = event.get("error")

                status_text = f"[{stage}] {message}"
                if error:
                    status_text = f"❌ 错误: {error}"

                yield (
                    status_text,
                    progress,
                    gallery_files if gallery_files else None,
                    video_file,
                )

        launch_btn.click(
            fn=on_launch,
            inputs=[
                action_dropdown,
                ref_image,
                force_fluid,
                force_physics,
                force_cloth,
                force_particles,
                vibe_input,
            ],
            outputs=[
                progress_text,
                progress_bar,
                gallery,
                video_output,
            ],
        )

    return app


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Launch the Web UI server."""
    logging.basicConfig(level=logging.INFO)
    logger.info("[SESSION-202] Starting MathArt Web UI...")
    app = create_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
    )


if __name__ == "__main__":
    main()
