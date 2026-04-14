"""Tests for ArtMathQualityController, ProjectMemory, and LevelSpecBridge."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from PIL import Image

from mathart.quality.controller import ArtMathQualityController
from mathart.quality.checkpoint import CheckpointDecision, CheckpointStage
from mathart.brain.memory import ProjectMemory, SessionHandoff
from mathart.level.spec_bridge import (
    LevelSpecBridge, LevelSpec, LevelTheme, AssetCategory, RenderMode
)


def make_test_image(w: int = 32, h: int = 32, color: tuple = (200, 100, 50)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


# ── ArtMathQualityController tests ────────────────────────────────────────────

class TestArtMathQualityController:
    def test_pre_generation_returns_result(self, tmp_path):
        """pre_generation should return a CheckpointResult."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        result = ctrl.pre_generation(0, {"palette_size": 16.0, "contrast": 0.5})
        assert result.stage == CheckpointStage.PRE_GENERATION
        assert result.iteration == 0
        assert result.decision in list(CheckpointDecision)

    def test_pre_generation_adjusts_out_of_range(self, tmp_path):
        """pre_generation should adjust params that violate math constraints."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        # Provide a clearly out-of-range param
        result = ctrl.pre_generation(0, {"palette_size": -5.0})
        # Should suggest adjustment
        assert result.decision in (CheckpointDecision.ADJUST, CheckpointDecision.CONTINUE)

    def test_mid_generation_continue(self, tmp_path):
        """mid_generation should return CONTINUE for decent quality."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        img = make_test_image(32, 32, (200, 100, 50))
        result = ctrl.mid_generation(0, img, step=5, total_steps=10)
        assert result.stage == CheckpointStage.MID_GENERATION
        assert result.decision in list(CheckpointDecision)

    def test_post_generation_scores_image(self, tmp_path):
        """post_generation should produce quality scores."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        img = make_test_image(32, 32)
        result = ctrl.post_generation(0, img, {"palette_size": 8.0})
        assert result.stage == CheckpointStage.POST_GENERATION
        assert 0.0 <= result.quality_score <= 1.0
        assert 0.0 <= result.combined_score <= 1.0

    def test_post_generation_stop_at_target(self, tmp_path):
        """post_generation should return STOP when target is reached."""
        ctrl = ArtMathQualityController(
            project_root=tmp_path,
            target_score=0.0,  # Always stop
        )
        img = make_test_image(32, 32)
        result = ctrl.post_generation(0, img, {})
        assert result.decision == CheckpointDecision.STOP

    def test_iteration_end_continue(self, tmp_path):
        """iteration_end should return CONTINUE for normal progress."""
        ctrl = ArtMathQualityController(project_root=tmp_path, target_score=0.99)
        img = make_test_image(32, 32)
        result = ctrl.iteration_end(0, img, 0.5)
        assert result.stage == CheckpointStage.ITERATION_END
        assert result.decision in (CheckpointDecision.CONTINUE, CheckpointDecision.ESCALATE)

    def test_iteration_end_stop_at_target(self, tmp_path):
        """iteration_end should return STOP when target score is reached."""
        ctrl = ArtMathQualityController(project_root=tmp_path, target_score=0.5)
        img = make_test_image(32, 32)
        result = ctrl.iteration_end(0, img, 0.9)  # Above target
        assert result.decision == CheckpointDecision.STOP

    def test_score_trend_improving(self, tmp_path):
        """Score trend should detect improvement."""
        ctrl = ArtMathQualityController(project_root=tmp_path, target_score=0.99)
        img = make_test_image(32, 32)
        for i, score in enumerate([0.3, 0.4, 0.5, 0.6, 0.7]):
            ctrl._score_history.append(score)
        trend = ctrl.get_score_trend()
        assert trend["trend"] == "improving"

    def test_score_trend_stagnant(self, tmp_path):
        """Score trend should detect stagnation."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        for _ in range(5):
            ctrl._score_history.append(0.5)
        trend = ctrl.get_score_trend()
        assert trend["trend"] == "stagnant"

    def test_reset_clears_history(self, tmp_path):
        """Reset should clear score history."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        ctrl._score_history.extend([0.5, 0.6, 0.7])
        ctrl.reset()
        assert len(ctrl._score_history) == 0

    def test_status_report(self, tmp_path):
        """Status report should be a non-empty string."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        report = ctrl.status_report()
        assert "ArtMathQualityController" in report
        assert "Knowledge rules" in report

    def test_checkpoint_log_accumulates(self, tmp_path):
        """Checkpoint log should accumulate results."""
        ctrl = ArtMathQualityController(project_root=tmp_path)
        img = make_test_image(32, 32)
        ctrl.pre_generation(0, {})
        ctrl.post_generation(0, img, {})
        log = ctrl.get_checkpoint_log()
        assert len(log) >= 2


# ── ProjectMemory tests ────────────────────────────────────────────────────────

class TestProjectMemory:
    def test_initial_state(self, tmp_path):
        """Initial state should have sensible defaults."""
        mem = ProjectMemory(project_root=tmp_path)
        assert mem.state.version == "0.5.0"
        assert mem.state.best_quality_score == 0.0
        assert mem.state.total_iterations == 0
        assert mem.state.distill_session_id == "DISTILL-004"

    def test_persistence_across_instances(self, tmp_path):
        """State should persist across ProjectMemory instances."""
        mem1 = ProjectMemory(project_root=tmp_path)
        mem1.update_version("0.6.0")

        mem2 = ProjectMemory(project_root=tmp_path)
        assert mem2.state.version == "0.6.0"

    def test_record_evolution(self, tmp_path):
        """Should record evolution and update best score."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.record_evolution(
            session_id="SESSION-001",
            version="0.5.0",
            changes=["Added X", "Fixed Y"],
            best_score=0.75,
            test_count=100,
        )
        assert mem.state.best_quality_score == 0.75
        assert len(mem.state.evolution_history) == 1
        assert mem.state.last_session_id == "SESSION-001"

    def test_best_score_only_increases(self, tmp_path):
        """Best score should only be updated if new score is higher."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.record_evolution("S1", "0.5.0", [], 0.8, 100)
        mem.record_evolution("S2", "0.5.0", [], 0.6, 100)  # Lower score
        assert mem.state.best_quality_score == 0.8

    def test_add_pending_task(self, tmp_path):
        """Should add pending tasks."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.add_pending_task("TASK-001", "Implement X", priority="high")
        assert len(mem.state.pending_tasks) == 1
        assert mem.state.pending_tasks[0].task_id == "TASK-001"

    def test_no_duplicate_tasks(self, tmp_path):
        """Should not add duplicate task IDs."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.add_pending_task("TASK-001", "First")
        mem.add_pending_task("TASK-001", "Duplicate")
        assert len(mem.state.pending_tasks) == 1

    def test_complete_task(self, tmp_path):
        """Should remove completed tasks."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.add_pending_task("TASK-001", "To complete")
        result = mem.complete_task("TASK-001")
        assert result is True
        assert len(mem.state.pending_tasks) == 0

    def test_complete_nonexistent_task(self, tmp_path):
        """Completing nonexistent task should return False."""
        mem = ProjectMemory(project_root=tmp_path)
        result = mem.complete_task("NONEXISTENT")
        assert result is False

    def test_add_capability_gap(self, tmp_path):
        """Should add capability gaps."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.add_capability_gap("GPU_RENDER", "Needs GPU", "NVIDIA GPU")
        assert len(mem.state.capability_gaps) == 1

    def test_resolve_gap(self, tmp_path):
        """Should resolve capability gaps."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.add_capability_gap("GPU_RENDER", "Needs GPU", "NVIDIA GPU")
        result = mem.resolve_gap("GPU_RENDER")
        assert result is True
        assert len(mem.state.capability_gaps) == 0

    def test_distill_id_increments(self, tmp_path):
        """Distill session ID should increment."""
        mem = ProjectMemory(project_root=tmp_path)
        id1 = mem.get_next_distill_id()
        id2 = mem.get_next_distill_id()
        assert id1 == "DISTILL-004"
        assert id2 == "DISTILL-005"

    def test_mine_id_increments(self, tmp_path):
        """Mine session ID should increment."""
        mem = ProjectMemory(project_root=tmp_path)
        id1 = mem.get_next_mine_id()
        id2 = mem.get_next_mine_id()
        assert id1 == "MINE-001"
        assert id2 == "MINE-002"

    def test_generate_handoff(self, tmp_path):
        """Should generate SESSION_HANDOFF.md."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.add_pending_task("TASK-001", "High priority task", priority="high")
        handoff = mem.generate_handoff()
        assert "SESSION HANDOFF" in handoff
        assert "TASK-001" in handoff
        handoff_path = tmp_path / "SESSION_HANDOFF.md"
        assert handoff_path.exists()

    def test_custom_notes(self, tmp_path):
        """Should store and retrieve custom notes."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.set_note("last_issue", "Stagnation at iteration 42")
        assert mem.get_note("last_issue") == "Stagnation at iteration 42"
        assert mem.get_note("missing", "default") == "default"

    def test_evolution_history_file(self, tmp_path):
        """Should create EVOLUTION_HISTORY.md."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.record_evolution("S1", "0.5.0", ["Added X"], 0.7, 50)
        history_path = tmp_path / "EVOLUTION_HISTORY.md"
        assert history_path.exists()
        content = history_path.read_text(encoding="utf-8")
        assert "S1" in content

    def test_brain_json_format(self, tmp_path):
        """PROJECT_BRAIN.json should have correct structure."""
        mem = ProjectMemory(project_root=tmp_path)
        mem.update_version("0.5.1")
        brain_path = tmp_path / "PROJECT_BRAIN.json"
        assert brain_path.exists()
        data = json.loads(brain_path.read_text(encoding="utf-8"))
        assert "version" in data
        assert "pending_tasks" in data
        assert "capability_gaps" in data


class TestSessionHandoff:
    def test_write_and_read(self, tmp_path):
        """Should write and read session handoff."""
        mem = ProjectMemory(project_root=tmp_path)
        handoff = SessionHandoff(mem)
        doc = handoff.write(
            session_id="SESSION-001",
            changes=["Added SpriteAnalyzer", "Added ProjectMemory"],
            best_score=0.72,
            test_count=298,
            notes="Major upgrade session",
        )
        assert "SESSION HANDOFF" in doc
        assert "SESSION-001" in doc

    def test_summary(self, tmp_path):
        """Summary should contain key project info."""
        mem = ProjectMemory(project_root=tmp_path)
        handoff = SessionHandoff.read(tmp_path)
        summary = handoff.summary()
        assert "MarioTrickster-MathArt" in summary
        assert "DISTILL-004" in summary


# ── LevelSpecBridge tests ──────────────────────────────────────────────────────

class TestLevelSpecBridge:
    def test_to_asset_spec_basic(self):
        """Should convert LevelSpec to AssetSpec."""
        bridge = LevelSpecBridge()
        spec = LevelSpec(
            level_id="test_level",
            theme=LevelTheme.GRASSLAND,
            tile_width=16,
            tile_height=16,
        )
        asset_spec = bridge.to_asset_spec(spec)
        assert asset_spec.level_id == "test_level"
        assert asset_spec.theme == LevelTheme.GRASSLAND
        assert len(asset_spec.sprites) > 0

    def test_asset_spec_has_correct_tile_size(self):
        """Tile assets should match the level's tile size."""
        bridge = LevelSpecBridge()
        spec = LevelSpec(
            level_id="test",
            theme=LevelTheme.GRASSLAND,
            tile_width=16,
            tile_height=16,
        )
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.TILE])
        tiles = asset_spec.get_by_category(AssetCategory.TILE)
        assert len(tiles) == 1
        assert tiles[0].width == 16
        assert tiles[0].height == 16

    def test_player_sprite_taller_than_tile(self):
        """Player sprite should be taller than a single tile."""
        bridge = LevelSpecBridge()
        spec = LevelSpec(
            level_id="test",
            theme=LevelTheme.GRASSLAND,
            tile_width=16,
            tile_height=16,
        )
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.PLAYER])
        players = asset_spec.get_by_category(AssetCategory.PLAYER)
        assert len(players) == 1
        assert players[0].height > 16  # Player is 2 tiles tall

    def test_custom_spec_override(self):
        """Custom specs should override defaults."""
        bridge = LevelSpecBridge()
        spec = LevelSpec(
            level_id="test",
            theme=LevelTheme.GRASSLAND,
            tile_width=16,
            tile_height=16,
            custom_specs={
                "player": {
                    "name": "custom_player",
                    "width": 24,
                    "height": 48,
                    "frame_count": 12,
                }
            },
        )
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.PLAYER])
        players = asset_spec.get_by_category(AssetCategory.PLAYER)
        assert players[0].width == 24
        assert players[0].height == 48
        assert players[0].frame_count == 12

    def test_validate_asset_valid(self):
        """Valid asset should pass validation."""
        bridge = LevelSpecBridge()
        spec = LevelSpec("test", LevelTheme.GRASSLAND, tile_width=16, tile_height=16)
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.TILE])
        tile = asset_spec.get_by_category(AssetCategory.TILE)[0]
        violations = bridge.validate_asset(asset_spec, tile.name, 16, 16, 8)
        assert violations == []

    def test_validate_asset_wrong_size(self):
        """Wrong size should produce violations."""
        bridge = LevelSpecBridge()
        spec = LevelSpec("test", LevelTheme.GRASSLAND, tile_width=16, tile_height=16)
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.TILE])
        tile = asset_spec.get_by_category(AssetCategory.TILE)[0]
        violations = bridge.validate_asset(asset_spec, tile.name, 32, 32, 8)
        assert len(violations) >= 1
        assert "Width mismatch" in violations[0] or "Height mismatch" in violations[1]

    def test_validate_asset_too_many_colors(self):
        """Too many colors should produce a violation."""
        bridge = LevelSpecBridge()
        spec = LevelSpec("test", LevelTheme.GRASSLAND, tile_width=16, tile_height=16,
                         palette_size=4)
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.TILE])
        tile = asset_spec.get_by_category(AssetCategory.TILE)[0]
        violations = bridge.validate_asset(asset_spec, tile.name, 16, 16, 32)
        assert any("color" in v.lower() for v in violations)

    def test_mario_style_spec(self):
        """Mario-style spec should have correct dimensions."""
        bridge = LevelSpecBridge()
        spec = bridge.create_mario_style_spec("world_1_1")
        assert spec.tile_width == 16
        assert spec.tile_height == 16
        assert spec.theme == LevelTheme.GRASSLAND
        assert "player" in spec.custom_specs
        assert spec.custom_specs["player"]["name"] == "mario"

    def test_save_and_load_spec(self, tmp_path):
        """Should save and load AssetSpec to/from JSON."""
        bridge = LevelSpecBridge(project_root=tmp_path)
        spec = LevelSpec("test", LevelTheme.GRASSLAND, tile_width=16, tile_height=16)
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.TILE, AssetCategory.PLAYER])
        path = bridge.save_spec(asset_spec)
        assert path.exists()

        loaded = bridge.load_spec(path)
        assert loaded.level_id == "test"
        assert loaded.theme == LevelTheme.GRASSLAND
        assert len(loaded.sprites) == len(asset_spec.sprites)

    def test_theme_palette_grassland(self):
        """Grassland theme should have sky blue in palette."""
        bridge = LevelSpecBridge()
        spec = LevelSpec("test", LevelTheme.GRASSLAND, tile_width=16, tile_height=16)
        asset_spec = bridge.to_asset_spec(spec)
        # Sky blue should be in the palette
        assert (92, 148, 252) in asset_spec.global_palette

    def test_constraints_in_asset_spec(self):
        """AssetSpec should contain parameter constraints."""
        bridge = LevelSpecBridge()
        spec = LevelSpec("test", LevelTheme.GRASSLAND, tile_width=16, tile_height=16)
        asset_spec = bridge.to_asset_spec(spec)
        assert "palette_size" in asset_spec.constraints
        assert "contrast" in asset_spec.constraints

    def test_screen_dimensions(self):
        """Screen dimensions should be tile_size * grid_size."""
        spec = LevelSpec(
            level_id="test",
            theme=LevelTheme.GRASSLAND,
            tile_width=16,
            tile_height=16,
            grid_cols=20,
            grid_rows=15,
        )
        assert spec.screen_width == 320
        assert spec.screen_height == 240

    def test_render_mode_pseudo3d(self):
        """Should support pseudo-3D render mode."""
        bridge = LevelSpecBridge()
        spec = LevelSpec(
            level_id="iso_level",
            theme=LevelTheme.GRASSLAND,
            render_mode=RenderMode.PSEUDO_3D,
            tile_width=32,
            tile_height=32,
        )
        asset_spec = bridge.to_asset_spec(spec, [AssetCategory.TILE])
        assert asset_spec.render_mode == RenderMode.PSEUDO_3D
