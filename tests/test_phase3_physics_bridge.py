"""Tests for SESSION-058 Phase 3 physics evolution bridge."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.evolution.phase3_physics_bridge import (
    Phase3PhysicsEvolutionBridge,
    collect_phase3_physics_status,
)


def test_phase3_bridge_runs_full_cycle(tmp_path):
    bridge = Phase3PhysicsEvolutionBridge(project_root=tmp_path, verbose=False)
    metrics, knowledge_path, bonus = bridge.run_full_cycle()

    assert metrics.cycle_id == 1
    assert knowledge_path.exists()
    assert 0.0 <= bonus <= 0.40
    assert bridge.state.total_cycles == 1


def test_phase3_status_collector_detects_real_project_exports():
    project_root = Path(__file__).resolve().parent.parent
    status = collect_phase3_physics_status(project_root)

    assert status.module_exists is True
    assert status.bridge_exists is True
    assert status.animation_api_exports is True
    assert status.evolution_api_exports is True
    assert status.tests_exist is True
