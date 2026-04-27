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

SESSION-203-HOTFIX:
- Moved ``theme`` and ``css`` from ``gr.Blocks()`` constructor to ``app.launch()``
  to resolve Gradio 6.0 UserWarning about parameter migration.
"""

from __future__ import annotations

import logging

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


# ═══════════════════════════════════════════════════════════════════════════
#  CSS Theme (extracted for reuse in launch())
# ═══════════════════════════════════════════════════════════════════════════

_CUSTOM_CSS = """
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
"""


def create_app() -> "gradio.Blocks":
    """Create and return the Gradio Blocks application.

    This is the main factory function. Call ``app = create_app()`` and then
    ``app.launch()`` to start the Web UI server.

    SESSION-203-HOTFIX: ``theme`` and ``css`` are no longer passed to the
    ``gr.Blocks()`` constructor.  In Gradio >= 6.0 these parameters have
    been moved to ``launch()``.  We store them as module-level constants
    and apply them in ``main()`` → ``app.launch(theme=..., css=...)``.

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
    #  SESSION-203-HOTFIX: theme/css removed from gr.Blocks() constructor
    #  and moved to launch() to silence Gradio 6.0 UserWarning.
    # ═══════════════════════════════════════════════════════════════════

    with gr.Blocks(
        title="MarioTrickster MathArt — 核动力渲染操作台",
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

                # Knowledge Feed JSON input
                gr.Markdown("### 📚 知识进食区 (Knowledge Feed)")
                knowledge_feed = gr.Textbox(
                    label="书籍/论文提炼 JSON",
                    placeholder='例如: {"source_book":"Animation + Fluid Notes", "fluid":{"glow_intensity":2.4}, "cloth":{"damping":0.62}}',
                    lines=8,
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

                knowledge_summary = gr.Textbox(
                    label="📚 当前研读理论与干涉权重",
                    value="等待 Knowledge Feed...",
                    interactive=False,
                    lines=14,
                )

                # Gallery for sequence frames
                gallery = gr.Gallery(
                    label="🖼️ V6 资产帧画廊 (outputs/v6_webui/)",
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
            knowledge_json: str,
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
                knowledge_feed=knowledge_json or "",
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
                    event.get("knowledge_summary", ""),
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
                knowledge_feed,
                vibe_input,
            ],
            outputs=[
                progress_text,
                progress_bar,
                gallery,
                video_output,
                knowledge_summary,
            ],
        )

    return app


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Launch the Web UI server.

    SESSION-203-HOTFIX: ``theme`` and ``css`` are now passed to ``launch()``
    instead of ``gr.Blocks()`` to comply with Gradio >= 6.0 API changes.
    """
    gr = _import_gradio()
    logging.basicConfig(level=logging.INFO)
    logger.info("[SESSION-202] Starting MathArt Web UI...")
    app = create_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        # SESSION-203-HOTFIX: theme/css moved from gr.Blocks() to launch()
        # per Gradio 6.0 migration guide.
        theme=gr.themes.Soft(),
        css=_CUSTOM_CSS,
    )


if __name__ == "__main__":
    main()
