import json
import threading
import time
from pathlib import Path

from mathart.level import (
    LevelSpec,
    LevelTheme,
    PDGFanOutResult,
    PDGNode,
    ProceduralDependencyGraph,
    RenderMode,
    UniversalSceneDescription,
    WorkItem,
)
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



def test_pdg_v2_hermetic_cache_short_circuits_recomputation(tmp_path: Path):
    calls = {"source": 0, "expensive": 0}

    def source(_ctx: dict, _deps: dict) -> dict:
        calls["source"] += 1
        return {"value": 7}

    def expensive(_ctx: dict, deps: dict) -> dict:
        calls["expensive"] += 1
        return {"value": deps["source"]["value"] * 3}

    graph = ProceduralDependencyGraph(name="cache_demo", cache_dir=tmp_path / ".pdg_cache")
    graph.add_node(PDGNode(name="source", operation=source, cache_enabled=False))
    graph.add_node(PDGNode(name="expensive", dependencies=["source"], operation=expensive))

    first = graph.run(["expensive"], initial_context={"seed": 42, "mode": "stable"})
    second = graph.run(["expensive"], initial_context={"seed": 42, "mode": "stable"})

    assert first["target_outputs"]["expensive"]["value"] == 21
    assert second["target_outputs"]["expensive"]["value"] == 21
    assert calls["source"] == 2
    assert calls["expensive"] == 1
    assert second["cache_stats"]["hits"] >= 1

    expensive_trace = [entry for entry in second["trace"] if entry["node_name"] == "expensive"]
    assert len(expensive_trace) == 1
    assert expensive_trace[0]["cache_hit"] is True
    assert len(second["work_items"]["expensive"]) == 1
    assert len(second["work_items"]["expensive"][0]["cache_key"]) == 64



def test_pdg_v2_fan_out_and_collect_preserves_all_work_items(tmp_path: Path):
    graph = ProceduralDependencyGraph(name="fanout_demo", cache_dir=tmp_path / ".pdg_cache")

    graph.add_node(PDGNode(name="seed", operation=lambda _ctx, _deps: {"values": [1, 2, 3]}))

    def fan_out(_ctx: dict, deps: dict) -> PDGFanOutResult:
        values = deps["seed"]["values"]
        return PDGFanOutResult.from_payloads(
            [{"value": value} for value in values],
            partition_keys=[f"p{index}" for index in range(len(values))],
            labels=[f"branch_{index}" for index in range(len(values))],
            attributes=[{"branch_index": index} for index in range(len(values))],
        )

    def scale(ctx: dict, deps: dict) -> dict:
        return {
            "value": deps["fan_out"]["value"] * 10,
            "partition": ctx["_pdg"]["partition_key"],
        }

    def collect(_ctx: dict, deps: dict) -> dict:
        branches = deps["scale"]
        return {
            "count": len(branches),
            "values": [item["value"] for item in branches],
            "partitions": [item["partition"] for item in branches],
            "sum": sum(item["value"] for item in branches),
        }

    graph.add_node(PDGNode(name="fan_out", dependencies=["seed"], operation=fan_out))
    graph.add_node(PDGNode(name="scale", dependencies=["fan_out"], operation=scale))
    graph.add_node(PDGNode(name="collect", dependencies=["scale"], operation=collect, topology="collect"))

    result = graph.run(["collect"], initial_context={"mode": "fanout_test"})
    collected = result["target_outputs"]["collect"]

    assert collected["count"] == 3
    assert collected["sum"] == 60
    assert collected["values"] == [10, 20, 30]
    assert collected["partitions"] == ["p0", "p1", "p2"]
    assert result["topology_summary"]["fan_out"]["work_items"] == 3
    assert result["topology_summary"]["scale"]["work_items"] == 3

    scale_trace = [entry for entry in result["trace"] if entry["node_name"] == "scale"]
    collect_trace = [entry for entry in result["trace"] if entry["node_name"] == "collect"]
    assert len(scale_trace) == 3
    assert len(collect_trace) == 1
    assert len(collect_trace[0]["upstream_item_ids"]) == 3

    fan_out_items = result["work_items"]["fan_out"]
    assert [item["partition_key"] for item in fan_out_items] == ["p0", "p1", "p2"]
    assert all(len(item["cache_key"]) == 64 for item in fan_out_items)



def test_pdg_v2_bounded_fan_out_respects_max_workers_and_collects_without_contamination(tmp_path: Path):
    graph = ProceduralDependencyGraph(
        name="bounded_fanout_demo",
        cache_dir=tmp_path / ".pdg_cache",
        max_workers=2,
    )

    graph.add_node(PDGNode(name="seed", operation=lambda _ctx, _deps: {"values": [1, 2, 3, 4]}))

    def fan_out(_ctx: dict, deps: dict) -> PDGFanOutResult:
        values = deps["seed"]["values"]
        return PDGFanOutResult.from_payloads(
            [{"value": value} for value in values],
            partition_keys=[f"p{index}" for index in range(len(values))],
            labels=[f"branch_{index}" for index in range(len(values))],
        )

    counters = {"active": 0, "max_active": 0}
    seen: list[tuple[str, int]] = []
    lock = threading.Lock()

    def scale(ctx: dict, deps: dict) -> dict:
        partition_key = ctx["_pdg"]["partition_key"]
        value = deps["fan_out"]["value"]
        with lock:
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
            seen.append((partition_key, value))
        time.sleep(0.05)
        with lock:
            counters["active"] -= 1
        return {"partition": partition_key, "value": value * 10}

    def collect(_ctx: dict, deps: dict) -> dict:
        items = sorted(deps["scale"], key=lambda item: item["partition"])
        return {
            "partitions": [item["partition"] for item in items],
            "values": [item["value"] for item in items],
            "sum": sum(item["value"] for item in items),
        }

    graph.add_node(PDGNode(name="fan_out", dependencies=["seed"], operation=fan_out))
    graph.add_node(PDGNode(name="scale", dependencies=["fan_out"], operation=scale))
    graph.add_node(PDGNode(name="collect", dependencies=["scale"], operation=collect, topology="collect"))

    result = graph.run(["collect"], initial_context={"mode": "bounded_concurrency_test"})

    assert result["scheduler"]["max_workers"] == 2
    assert result["scheduler"]["backend"] == "thread"
    assert counters["active"] == 0
    assert counters["max_active"] == 2
    assert sorted(seen) == [("p0", 1), ("p1", 2), ("p2", 3), ("p3", 4)]
    assert result["target_outputs"]["collect"]["partitions"] == ["p0", "p1", "p2", "p3"]
    assert result["target_outputs"]["collect"]["values"] == [10, 20, 30, 40]
    assert result["target_outputs"]["collect"]["sum"] == 100
    assert len([entry for entry in result["trace"] if entry["node_name"] == "scale"]) == 4



def test_work_item_contract_is_frozen_and_dict_roundtrips() -> None:
    work_item = WorkItem(
        item_id="node:0:branch:abcdef123456",
        node_name="demo_node",
        payload={"value": 5},
        attributes={"kind": "unit"},
        parent_ids=("root",),
        upstream_item_ids=("root",),
        partition_key="p0",
        cache_key="a" * 64,
        payload_digest="b" * 64,
    )

    serialized = work_item.to_dict()

    assert serialized["node_name"] == "demo_node"
    assert serialized["partition_key"] == "p0"
    assert serialized["payload"]["value"] == 5
    assert serialized["attributes"]["kind"] == "unit"
    assert serialized["cache_key"] == "a" * 64
    assert serialized["payload_digest"] == "b" * 64



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
