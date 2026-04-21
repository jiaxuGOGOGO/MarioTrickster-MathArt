"""SESSION-109 (P1-ARCH-6) — Tensor-based level topology test suite.

Three layers of validation:

1. **Unit tests** — exercise the pure algorithmic core
   (:class:`TopologyExtractor` and the frozen dataclass contracts).
2. **Integration tests** — run the full
   :class:`LevelTopologyBackend` through the
   :class:`MicrokernelPipelineBridge` and validate the produced
   :class:`ArtifactManifest` against the
   :data:`ArtifactFamily.LEVEL_TOPOLOGY` schema.
3. **Performance benchmark** — assert the extractor stays under the
   256x256 wall-time budget and parallelises cleanly across 16 threads
   via a :class:`concurrent.futures.ThreadPoolExecutor`.  These tests
   defend the project's red-line discipline against Python-loop OOM and
   GIL-bound regressions.
"""
from __future__ import annotations

import concurrent.futures as _cf
import json
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from mathart.core.artifact_schema import ArtifactFamily, validate_artifact
from mathart.core.backend_registry import BackendRegistry, get_registry
from mathart.core.backend_types import BackendType
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge
from mathart.level.topology_extractor import TopologyExtractor
from mathart.level.topology_types import (
    ANCHOR_CEILING,
    ANCHOR_CORNER_CONCAVE,
    ANCHOR_CORNER_CONVEX,
    ANCHOR_FLOOR_TOP,
    ANCHOR_WALL_LEFT,
    ANCHOR_WALL_RIGHT,
    KNOWN_ANCHOR_TYPES,
    KNOWN_SURFACE_KINDS,
    SemanticAnchor,
    TopologyExtractionResult,
    TraversalLane,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _make_simple_room(rows: int = 10, cols: int = 20) -> np.ndarray:
    """Hollow room with floor + ceiling + side walls."""
    g = np.zeros((rows, cols), dtype=np.int32)
    g[-2:, :] = 1   # floor (2 rows tall to expose convex corners)
    g[:2, :] = 1    # ceiling
    g[:, 0] = 1     # left wall
    g[:, -1] = 1    # right wall
    return g


def _make_platform_grid() -> np.ndarray:
    g = np.zeros((10, 20), dtype=np.int32)
    g[8:, :] = 1
    g[5, 5:10] = 2  # platform tile
    return g


# ---------------------------------------------------------------------------
# Unit tests — frozen dataclass contracts
# ---------------------------------------------------------------------------


class TestFrozenContracts:
    def test_semantic_anchor_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            SemanticAnchor(
                x=0.0, y=0.0, anchor_type="MARIO_SAYS_HI",
            )

    def test_semantic_anchor_is_immutable(self):
        anchor = SemanticAnchor(x=1.0, y=2.0, anchor_type=ANCHOR_FLOOR_TOP)
        with pytest.raises(Exception):
            anchor.x = 99.0  # type: ignore[misc]

    def test_semantic_anchor_transform_matrix_y_up(self):
        anchor = SemanticAnchor(
            x=3.0,
            y=4.0,
            anchor_type=ANCHOR_FLOOR_TOP,
            up_x=0.0,
            up_y=1.0,
        )
        m = anchor.transform_matrix()
        assert m.shape == (4, 4)
        assert m[0, 3] == pytest.approx(3.0)
        assert m[1, 3] == pytest.approx(4.0)

    def test_traversal_lane_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            TraversalLane(
                lane_id=1,
                surface_kind="LAVA_RIVER",
                bounds=(0, 0, 1, 1),
                area=1,
                centroid_x=0.5,
                centroid_y=0.5,
            )

    def test_known_vocabulary_is_frozen(self):
        with pytest.raises(AttributeError):
            KNOWN_ANCHOR_TYPES.add("rogue_type")  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            KNOWN_SURFACE_KINDS.add("rogue_kind")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Unit tests — TopologyExtractor algorithm
# ---------------------------------------------------------------------------


class TestTopologyExtractor:
    def test_extract_simple_floor(self):
        g = np.zeros((6, 12), dtype=np.int32)
        g[5, :] = 1
        result = TopologyExtractor().extract(g)
        assert isinstance(result, TopologyExtractionResult)
        assert result.grid_rows == 6 and result.grid_cols == 12
        # Every floor cell with empty above is walkable.
        assert int(result.tensors.is_walkable_surface.sum()) == 12
        assert result.tensors.component_count == 1
        assert any(a.anchor_type == ANCHOR_FLOOR_TOP for a in result.anchors)

    def test_extract_room_emits_walls_and_corners(self):
        # Hollow room PLUS a free-standing protruding block in mid-air to
        # guarantee at least one **outer** convex corner (a solid cell
        # with two adjacent empty cardinal neighbours).
        g = _make_simple_room()
        # Free-standing 2x2 block away from any wall.
        g[4:6, 8:10] = 1
        result = TopologyExtractor().extract(g)
        kinds = {a.anchor_type for a in result.anchors}
        assert ANCHOR_FLOOR_TOP in kinds
        assert ANCHOR_CEILING in kinds
        assert ANCHOR_WALL_LEFT in kinds
        assert ANCHOR_WALL_RIGHT in kinds
        # The free-standing block guarantees four convex outer corners.
        assert ANCHOR_CORNER_CONVEX in kinds
        # Hollow room has many concave inner corners along the edges.
        assert ANCHOR_CORNER_CONCAVE in kinds

    def test_extract_platform_lane_is_kind_platform(self):
        g = _make_platform_grid()
        result = TopologyExtractor(platform_tile_ids={2}).extract(g)
        platform_lanes = [
            lane for lane in result.lanes if lane.surface_kind == "platform"
        ]
        assert platform_lanes, "expected at least one platform lane"
        assert platform_lanes[0].area >= 1

    def test_extract_no_python_for_loop_overhead(self):
        """Sanity: extractor finishes 256x256 in well under a second."""
        rng = np.random.default_rng(seed=42)
        g = (rng.random((256, 256)) > 0.6).astype(np.int32)
        t0 = time.perf_counter()
        result = TopologyExtractor().extract(g)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"extractor too slow: {elapsed:.3f}s"
        assert result.tensors.shape() == (256, 256)

    def test_extract_handles_empty_grid_gracefully(self):
        g = np.zeros((5, 5), dtype=np.int32)
        result = TopologyExtractor().extract(g)
        assert result.tensors.component_count == 0
        assert len(result.anchors) == 0
        assert len(result.lanes) == 0

    def test_extract_handles_all_solid_grid_gracefully(self):
        g = np.ones((4, 4), dtype=np.int32)
        result = TopologyExtractor().extract(g)
        # Top-row cells are walkable because their "above" is implicit air.
        assert int(result.tensors.is_walkable_surface.sum()) >= 4

    def test_normals_are_unit_or_zero(self):
        g = _make_simple_room()
        result = TopologyExtractor().extract(g)
        nx = result.tensors.surface_normal_x
        ny = result.tensors.surface_normal_y
        magnitudes = np.sqrt(nx * nx + ny * ny)
        # Every magnitude must be either ~0 or ~1.
        assert np.all((magnitudes < 1e-3) | (np.abs(magnitudes - 1.0) < 1e-3))


# ---------------------------------------------------------------------------
# Integration tests — registry / manifest contract
# ---------------------------------------------------------------------------


class TestLevelTopologyBackend:
    def test_backend_is_discoverable(self):
        reg = get_registry()
        assert reg.get(BackendType.LEVEL_TOPOLOGY.value) is not None
        meta, _cls = reg.get_or_raise(BackendType.LEVEL_TOPOLOGY.value)
        assert ArtifactFamily.LEVEL_TOPOLOGY.value in meta.artifact_families

    def test_backend_produces_valid_manifest_via_bridge(self):
        get_registry()  # ensure builtins loaded
        with tempfile.TemporaryDirectory(prefix="lvl_topo_") as tmpdir:
            bridge = MicrokernelPipelineBridge(
                project_root=tmpdir, session_id="SESSION-109",
            )
            ctx = {
                "logical_grid": _make_simple_room().tolist(),
                "output_dir": tmpdir,
                "name": "ci_room",
                "session_id": "SESSION-109",
            }
            manifest = bridge.run_backend(BackendType.LEVEL_TOPOLOGY.value, ctx)
            errors = validate_artifact(manifest)
            assert errors == [], f"manifest validation failed: {errors}"
            assert "topology_json" in manifest.outputs
            assert "tensors_npz" in manifest.outputs

            # Verify both artifacts exist on disk.
            assert Path(manifest.outputs["topology_json"]).exists()
            assert Path(manifest.outputs["tensors_npz"]).exists()

            # JSON payload must be self-describing.
            payload = json.loads(
                Path(manifest.outputs["topology_json"]).read_text(encoding="utf-8")
            )
            assert payload["schema_version"] == "1.0.0"
            assert "anchors" in payload and isinstance(payload["anchors"], list)
            assert "lanes" in payload and isinstance(payload["lanes"], list)

    def test_backend_can_read_upstream_wfc_manifest(self):
        get_registry()
        with tempfile.TemporaryDirectory(prefix="wfc_topo_") as tmpdir:
            tmp = Path(tmpdir)
            tilemap_json = tmp / "tilemap.json"
            tilemap_json.write_text(
                json.dumps({"grid": _make_simple_room().tolist()}),
                encoding="utf-8",
            )
            bridge = MicrokernelPipelineBridge(
                project_root=tmpdir, session_id="SESSION-109",
            )
            ctx = {
                "tilemap_json": str(tilemap_json),
                "output_dir": tmpdir,
                "name": "wfc_to_topo",
            }
            manifest = bridge.run_backend(BackendType.LEVEL_TOPOLOGY.value, ctx)
            assert validate_artifact(manifest) == []
            assert manifest.metadata["source"]["source"] == "tilemap_json_path"

    def test_backend_metadata_carries_strong_attribute_keys(self):
        get_registry()
        with tempfile.TemporaryDirectory(prefix="lvl_meta_") as tmpdir:
            bridge = MicrokernelPipelineBridge(project_root=tmpdir)
            manifest = bridge.run_backend(
                BackendType.LEVEL_TOPOLOGY.value,
                {
                    "logical_grid": _make_simple_room().tolist(),
                    "output_dir": tmpdir,
                    "name": "meta_check",
                },
            )
            for key in (
                "grid_rows",
                "grid_cols",
                "anchor_count",
                "lane_count",
                "connected_component_count",
                "extraction_wall_ms",
                "solid_tile_ids",
                "anchors_by_type",
                "lanes_by_kind",
                "tensor_summary",
            ):
                assert key in manifest.metadata, f"missing metadata key {key!r}"


# ---------------------------------------------------------------------------
# Performance benchmark — 16-thread parallel pressure test
# ---------------------------------------------------------------------------


class TestTopologyParallelPressure:
    """Validates the extractor against the project's anti-OOM red line.

    A 200x200 random tile grid is processed 32 times across a thread
    pool with 16 workers, mimicking the sandbox's hardware parallelism.
    The test asserts:

    * total wall-time stays within a generous headroom multiplier so any
      O(N²) Python-loop regression trips the budget;
    * every result returns a valid :class:`TopologyExtractionResult`
      (no thread silently swallowed an exception).
    """

    GRID_SIZE = 200
    BATCH = 32
    WORKERS = 16
    BUDGET_SECONDS = 30.0

    def _job(self, seed: int) -> TopologyExtractionResult:
        rng = np.random.default_rng(seed=seed)
        g = (rng.random((self.GRID_SIZE, self.GRID_SIZE)) > 0.65).astype(np.int32)
        return TopologyExtractor().extract(g)

    def test_parallel_extraction_within_budget(self):
        t0 = time.perf_counter()
        with _cf.ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            results = list(pool.map(self._job, range(self.BATCH)))
        elapsed = time.perf_counter() - t0
        assert len(results) == self.BATCH
        assert all(isinstance(r, TopologyExtractionResult) for r in results)
        assert elapsed < self.BUDGET_SECONDS, (
            f"16-thread topology extraction blew the budget: "
            f"{elapsed:.2f}s > {self.BUDGET_SECONDS:.1f}s"
        )
