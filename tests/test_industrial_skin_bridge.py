import json

from mathart.evolution.industrial_skin_bridge import IndustrialSkinBridge


def test_industrial_skin_bridge_runs_closed_loop_cycle(tmp_path):
    bridge = IndustrialSkinBridge(tmp_path)

    result = bridge.run_cycle()

    assert result["accepted"] is True
    assert result["metrics"]["case_count"] == 5
    assert result["metrics"]["mean_inside_analytic_coverage"] >= 0.85
    assert result["metrics"]["export_success_ratio"] == 1.0

    knowledge_path = tmp_path / "knowledge" / "industrial_skin.md"
    state_path = tmp_path / "workspace" / "evolution_states" / "industrial_skin_state.json"
    assert knowledge_path.exists()
    assert state_path.exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["total_cycles"] == 1
    assert state["total_passes"] == 1
    assert state["best_export_success_ratio"] == 1.0
