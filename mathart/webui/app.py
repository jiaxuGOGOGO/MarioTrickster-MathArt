"""Omniscient Gradio dashboard for the V6 knowledge-to-Unity pipeline."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
_gr = None


def _import_gradio():
    global _gr
    if _gr is None:
        import gradio as gr
        _gr = gr
    return _gr


_CUSTOM_CSS = """
.gradio-container {
    background: radial-gradient(circle at top left, #102040 0%, #050712 38%, #02030a 100%) !important;
    color: #e6f7ff !important;
}
.omni-hero {
    padding: 24px;
    border: 1px solid rgba(0, 245, 255, 0.24);
    border-radius: 22px;
    background: linear-gradient(135deg, rgba(11, 23, 51, 0.92), rgba(45, 16, 75, 0.72));
    box-shadow: 0 0 48px rgba(0, 220, 255, 0.16), inset 0 0 34px rgba(120, 70, 255, 0.10);
    margin-bottom: 18px;
}
.omni-title {
    margin: 0;
    font-size: 34px;
    font-weight: 900;
    letter-spacing: 0.04em;
    background: linear-gradient(90deg, #7df9ff, #b277ff, #48ff9b);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.omni-subtitle {
    margin-top: 8px;
    color: #9fdcf2;
    font-size: 15px;
}
.omni-section {
    border: 1px solid rgba(125, 249, 255, 0.18) !important;
    border-radius: 18px !important;
    background: rgba(3, 9, 22, 0.74) !important;
    box-shadow: 0 0 30px rgba(0, 180, 255, 0.10);
    padding: 14px !important;
}
.omni-card {
    border-radius: 16px;
    padding: 16px;
    margin: 8px 0 14px 0;
}
.omni-card-muted {
    border: 1px solid rgba(130, 150, 170, 0.28);
    background: rgba(20, 28, 45, 0.68);
    color: #b9c8d8;
}
.omni-card-success {
    border: 1px solid rgba(72, 255, 155, 0.42);
    background: linear-gradient(135deg, rgba(20, 90, 70, 0.58), rgba(16, 35, 65, 0.72));
    box-shadow: 0 0 26px rgba(72, 255, 155, 0.18);
}
.omni-card-title {
    font-size: 18px;
    font-weight: 800;
    margin-bottom: 8px;
}
.omni-card-body {
    color: #e9fbff;
}
.omni-chip-row {
    margin-top: 10px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}
.omni-chip {
    border: 1px solid rgba(125, 249, 255, 0.32);
    background: rgba(0, 245, 255, 0.10);
    border-radius: 999px;
    padding: 5px 10px;
    color: #bdfcff;
    font-size: 12px;
}
.omni-green-light {
    color: #48ff9b;
    font-weight: 900;
    text-shadow: 0 0 12px rgba(72,255,155,0.8);
}
#incubate_btn button {
    min-height: 64px;
    font-size: 20px;
    font-weight: 900;
    border-radius: 18px;
    background: linear-gradient(90deg, #00e5ff, #8a5cff, #00ff9d) !important;
    border: 0 !important;
    box-shadow: 0 0 30px rgba(0, 229, 255, 0.35);
}
textarea, input, .wrap, .container { border-radius: 14px !important; }
"""


def create_app() -> "gradio.Blocks":
    gr = _import_gradio()
    from mathart.webui.bridge import WebUIBridge, build_knowledge_card, build_knowledge_report

    bridge = WebUIBridge()
    available_actions = bridge.get_available_actions()

    with gr.Blocks(title="V6 Omniscient Dashboard") as app:
        gr.HTML(
            '<div class="omni-hero">'
            '<h1 class="omni-title">The Omniscient Dashboard · V6 全自动进化飞轮</h1>'
            '<div class="omni-subtitle">知识蒸馏 → 自动迭代 → 零后期 Unity 资产交付。UI 仅触发 run_v6_omniscient_pipeline.py。</div>'
            '</div>'
        )

        with gr.Row():
            with gr.Column(scale=1, elem_classes=["omni-section"]):
                gr.Markdown("## 🧠 第一区：知识大脑进食槽")
                knowledge_feed = gr.Textbox(
                    label="外部大模型蒸馏书籍理论 JSON",
                    placeholder='{"source_book":"Animator Survival Kit + Fluid Timing Notes","fluid":{"viscosity":0.8},"timing":{"hold_ratio":0.35},"cloth":{"damping":0.62}}',
                    lines=10,
                )
                knowledge_file = gr.File(label="上传理论 JSON 文件", file_types=[".json"], type="filepath")
                knowledge_card = gr.HTML(value=build_knowledge_card({}), label="知识同化指示卡")
                knowledge_summary = gr.Code(
                    label="当前研读理论与干涉权重",
                    value=build_knowledge_report({}),
                    language="json",
                    lines=12,
                )

                def on_knowledge_change(text: str, file_path: str | None):
                    import json
                    from pathlib import Path
                    from mathart.webui.bridge import _summarize_knowledge

                    try:
                        payload = {}
                        if file_path:
                            loaded = json.loads(Path(file_path).read_text(encoding="utf-8"))
                            if isinstance(loaded, dict):
                                payload.update(loaded)
                        if text and text.strip():
                            loaded = json.loads(text)
                            if isinstance(loaded, dict):
                                payload.update(loaded)
                        if not payload:
                            return build_knowledge_card({}), build_knowledge_report({})
                        summary = _summarize_knowledge(None, payload)
                        summary["book"] = str(payload.get("source_book", summary.get("book", "默认 V6 动画/物理理论")))
                        summary["viscosity"] = payload.get("fluid", {}).get("viscosity") if isinstance(payload.get("fluid"), dict) else None
                        summary["damping"] = payload.get("cloth", {}).get("damping") if isinstance(payload.get("cloth"), dict) else None
                        summary["hold_ratio"] = payload.get("timing", {}).get("hold_ratio") if isinstance(payload.get("timing"), dict) else None
                        return build_knowledge_card(summary), build_knowledge_report(summary)
                    except Exception as exc:
                        return f'<div class="omni-card omni-card-muted">JSON 解析失败：{exc}</div>', "{}"

                knowledge_feed.change(on_knowledge_change, [knowledge_feed, knowledge_file], [knowledge_card, knowledge_summary])
                knowledge_file.change(on_knowledge_change, [knowledge_feed, knowledge_file], [knowledge_card, knowledge_summary])

            with gr.Column(scale=1, elem_classes=["omni-section"]):
                gr.Markdown("## ⚙️ 第二区：意图与进化沙盒")
                action_dropdown = gr.Dropdown(
                    choices=available_actions,
                    value=available_actions[0] if available_actions else None,
                    label="动作语义锚点",
                )
                ref_image = gr.Image(label="可选参考图", type="filepath", sources=["upload"])
                vibe_input = gr.Textbox(
                    label="自然语言 Vibe 意图",
                    placeholder="例如：粘滞流体拖尾的赛博像素骗术师，攻击瞬间卡肉顿帧，Unity 2D 直接可用",
                    lines=5,
                )
                dry_runs = gr.Slider(
                    minimum=1,
                    maximum=1024,
                    value=128,
                    step=1,
                    label="多进程干跑代数 (Dry-runs)",
                    info="默认 128 代，最高 1024 代",
                )
                with gr.Row():
                    force_fluid = gr.Checkbox(label="强制流体", value=False)
                    force_physics = gr.Checkbox(label="强制物理", value=False)
                with gr.Row():
                    force_cloth = gr.Checkbox(label="强制布料", value=False)
                    force_particles = gr.Checkbox(label="强制粒子", value=False)
                launch_btn = gr.Button("🚀 启动数字生命孵化", variant="primary", size="lg", elem_id="incubate_btn")

        with gr.Row(elem_classes=["omni-section"]):
            with gr.Column(scale=1):
                gr.Markdown("## 📺 第三区：大一统零后期验收台")
                progress_text = gr.Textbox(label="实时进度流", value="等待启动...", lines=14, interactive=False)
                progress_bar = gr.Slider(minimum=0.0, maximum=1.0, value=0.0, label="孵化进度", interactive=False)
                status_light = gr.HTML(value='<div class="omni-green-light">待机</div>')
            with gr.Column(scale=1):
                hero_asset = gr.Image(label="高清精灵图集预览 · 1_FINAL_UNITY_ASSETS", type="filepath", interactive=False)
                gallery = gr.Gallery(label="最终 Unity 资产区", columns=3, rows=2, object_fit="contain", height="auto")

        def on_launch(action, ref_img, fluid, physics, cloth, particles, knowledge_json, knowledge_json_file, vibe, generations):
            for event in bridge.dispatch_render(
                action_name=action or "",
                reference_image=ref_img,
                force_fluid=fluid,
                force_physics=physics,
                force_cloth=cloth,
                force_particles=particles,
                raw_vibe=vibe or "",
                knowledge_feed=knowledge_json or "",
                knowledge_file=knowledge_json_file,
                dry_runs=int(generations or 128),
            ):
                light = event.get("status_light", "")
                if event.get("stage") == "complete":
                    light = "Unity 零后期对齐契约已生效，请直接拖入引擎使用！"
                if event.get("error"):
                    light = f"错误：{event['error']}"
                yield (
                    event.get("message", ""),
                    event.get("progress", 0.0),
                    event.get("knowledge_card", build_knowledge_card({})),
                    event.get("knowledge_summary", build_knowledge_report({})),
                    event.get("hero_asset"),
                    event.get("gallery") or [],
                    f'<div class="omni-green-light">{light}</div>',
                )

        launch_btn.click(
            fn=on_launch,
            inputs=[action_dropdown, ref_image, force_fluid, force_physics, force_cloth, force_particles, knowledge_feed, knowledge_file, vibe_input, dry_runs],
            outputs=[progress_text, progress_bar, knowledge_card, knowledge_summary, hero_asset, gallery, status_light],
        )

    return app


def main() -> None:
    gr = _import_gradio()
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting V6 Omniscient Dashboard...")
    create_app().launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        theme=gr.themes.Soft(primary_hue="cyan", secondary_hue="violet", neutral_hue="slate"),
        css=_CUSTOM_CSS,
    )


if __name__ == "__main__":
    main()
