from __future__ import annotations

import json
from pathlib import Path

from mathart.level import LevelSpec, LevelTheme, RenderMode, PDGNode, ProceduralDependencyGraph, UniversalSceneDescription
from mathart.pipeline import AssetPipeline, LevelPipelineSpec


def test_procedural_dependency_graph_executes_in_dependency_order():
    graph = ProceduralDependencyGraph(name="demo")
    graph.add_node(PDGNode(name="source", operation=lambda _ctx, _deps: {"value": 3}))
    graph.add_node(
        PDGNode(
            name="double",
            dependencies=["source"],
            operation=lambda _ctx, deps: {"value": deps["source"]["value"] * 2},
        )
    )
    graph.add_node(
        PDGNode(
            name="sum",
            dependencies=["source", "double"],
            operation=lambda _ctx, deps: {"value": deps["source"]["value"] + deps["double"]["value"]},
        )
    )

    result = graph.run(["sum"])

    assert result["execution_order"] == ["source", "double", "sum"]
    assert result["target_outputs"]["sum"]["value"] == 9


def test_universal_scene_description_derives_metrics_and_shader_recipe():
    level_spec = LevelSpec(
        level_id="demo",
        theme=LevelTheme.GRASSLAND,
        render_mode=RenderMode.FLAT_2D,
        tile_width=16,
        tile_height=16,
        grid_cols=5,
        grid_rows=4,
        palette_size=8,
    )
    ascii_level = ".....\n.M..G\n..E..\n#####"
    scene = UniversalSceneDescription.from_ascii_level(ascii_level, level_spec)
    recipe = scene.derive_shader_recipe()

    assert scene.metrics["counts"]["enemy"] == 1
    assert scene.metrics["counts"]["spawn"] == 1
    assert scene.metrics["counts"]["goal"] == 1
    assert recipe["shader_type"] == "sprite_lit"
    assert any(overlay["shader_type"] == "outline" for overlay in recipe["overlays"])


def test_asset_pipeline_produces_level_pack_with_scene_shader_and_export(tmp_path: Path):
    pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False, seed=7)
    spec = LevelPipelineSpec(
        level_id="gap1_demo",
        width=12,
        height=6,
        tile_size=12,
        theme="grassland",
        render_mode="flat_2d",
        palette_size=8,
        export_preview=True,
    )

    result = pipeline.produce_level_pack(spec)

    assert result.metadata["pipeline_type"] == "level_pdg"
    assert result.metadata["scene_format"] == "usd_like_scene_v1"
    assert "scene_describe" in result.metadata["pdg_execution_order"]
    assert "shader_generate" in result.metadata["pdg_execution_order"]
    assert result.image is not None

    bundle_path = Path(tmp_path) / "levels" / "gap1_demo" / "gap1_demo_bundle.json"
    assert bundle_path.exists()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert Path(bundle["scene_path"]).exists()
    assert Path(bundle["preview_path"]).exists()
    assert Path(bundle["shader_plan_path"]).exists()
    assert bundle["shader_files"]
    assert bundle["distilled_knowledge_rules"]

    knowledge_file = Path(tmp_path) / "knowledge" / "procedural_pipeline.md"
    assert knowledge_file.exists()


def test_asset_pack_summary_counts_pdg_levels(tmp_path: Path):
    pipeline = AssetPipeline(output_dir=str(tmp_path), verbose=False, seed=11)
    results = pipeline.produce_asset_pack(
        pack_name="combo",
        sprites=[],
        animations=[],
        characters=[],
        levels=[LevelPipelineSpec(level_id="combo_level", width=10, height=6, export_preview=False)],
        include_textures=False,
    )

    assert len(results) == 1
    assert results[0].metadata["pipeline_type"] == "level_pdg"

    summary_path = Path(tmp_path) / "combo_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["total_levels"] == 1
    assert summary["assets"][0]["pipeline_type"] == "level_pdg"
