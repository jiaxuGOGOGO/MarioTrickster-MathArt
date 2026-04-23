"""
SESSION-174: Unit tests for Asset Governance module.

Tests cover:
  1. Batch triage logic (golden vs junk classification)
  2. Directory size calculation
  3. Safe path assertion (blast-radius containment)
  4. Vault extraction (flat copy)
  5. Safe nuke (junk deletion with PermissionError resilience)
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mathart.factory.asset_governance import (
    BatchInfo,
    ScanReport,
    _assert_safe_path,
    _dir_size_bytes,
    _find_deliverables,
    _triage_batch,
    extract_vault,
    safe_nuke_junk_batches,
    scan_production_output,
)


@pytest.fixture
def tmp_production_dir(tmp_path):
    """Create a temporary production directory with test batch folders."""
    prod_dir = tmp_path / "output" / "production"
    prod_dir.mkdir(parents=True)

    # Golden batch: has batch_summary.json + final video
    golden_dir = prod_dir / "mass_production_batch_20260424_001018"
    golden_dir.mkdir()
    char_dir = golden_dir / "char_slime_001"
    char_dir.mkdir()
    render_dir = char_dir / "anti_flicker_render"
    render_dir.mkdir()
    # Write a fake MP4 (> 100KB)
    (render_dir / "final_output.mp4").write_bytes(b"\x00" * 150_000)
    # Write a fake high-res PNG (> 50KB)
    (render_dir / "frame_001.png").write_bytes(b"\x00" * 80_000)
    # Write batch_summary.json
    summary = {
        "session_id": "SESSION-164",
        "artifact_family": "mass_production_batch",
        "batch_dir": str(golden_dir),
        "character_count": 1,
        "skip_ai_render": False,
        "records": [],
    }
    with open(golden_dir / "batch_summary.json", "w") as f:
        json.dump(summary, f)

    # Junk batch: no batch_summary, no deliverables
    junk_dir = prod_dir / "mass_production_batch_20260424_001537"
    junk_dir.mkdir()
    (junk_dir / "partial_data.txt").write_bytes(b"incomplete" * 100)

    # Another junk batch: has summary but no deliverables
    junk2_dir = prod_dir / "mass_production_batch_20260423_235959"
    junk2_dir.mkdir()
    summary2 = {
        "session_id": "SESSION-164",
        "character_count": 0,
        "skip_ai_render": True,
        "records": [],
    }
    with open(junk2_dir / "batch_summary.json", "w") as f:
        json.dump(summary2, f)

    return prod_dir


class TestDirSizeBytes:
    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert _dir_size_bytes(d) == 0

    def test_dir_with_files(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        (d / "a.txt").write_bytes(b"hello")
        (d / "b.txt").write_bytes(b"world!")
        assert _dir_size_bytes(d) == 11

    def test_nonexistent_dir(self, tmp_path):
        d = tmp_path / "nonexistent"
        assert _dir_size_bytes(d) == 0


class TestFindDeliverables:
    def test_finds_video_and_image(self, tmp_production_dir):
        golden_dir = tmp_production_dir / "mass_production_batch_20260424_001018"
        deliverables = _find_deliverables(golden_dir)
        assert len(deliverables) == 2
        exts = {d.suffix for d in deliverables}
        assert ".mp4" in exts
        assert ".png" in exts

    def test_ignores_small_files(self, tmp_path):
        d = tmp_path / "batch"
        d.mkdir()
        # Tiny PNG (below threshold)
        (d / "tiny.png").write_bytes(b"\x00" * 100)
        assert len(_find_deliverables(d)) == 0

    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty_batch"
        d.mkdir()
        assert len(_find_deliverables(d)) == 0


class TestTriageBatch:
    def test_golden_batch(self, tmp_production_dir):
        golden_dir = tmp_production_dir / "mass_production_batch_20260424_001018"
        info = _triage_batch(golden_dir)
        assert info.is_golden is True
        assert info.has_summary is True
        assert info.has_final_video is True
        assert info.character_count == 1
        assert "黄金" in info.status_label
        assert info.status_emoji == "🟢"

    def test_junk_batch_no_summary(self, tmp_production_dir):
        junk_dir = tmp_production_dir / "mass_production_batch_20260424_001537"
        info = _triage_batch(junk_dir)
        assert info.is_golden is False
        assert info.has_summary is False
        assert "废弃" in info.status_label
        assert info.status_emoji == "🔴"

    def test_junk_batch_empty_summary(self, tmp_production_dir):
        junk_dir = tmp_production_dir / "mass_production_batch_20260423_235959"
        info = _triage_batch(junk_dir)
        assert info.is_golden is False
        assert info.has_summary is True
        assert info.character_count == 0


class TestScanProductionOutput:
    def test_full_scan(self, tmp_production_dir):
        report = scan_production_output(tmp_production_dir)
        assert len(report.batches) == 3
        assert report.golden_count == 1
        assert report.junk_count == 2
        assert report.total_size_bytes > 0

    def test_empty_dir(self, tmp_path):
        d = tmp_path / "output" / "production"
        d.mkdir(parents=True)
        report = scan_production_output(d)
        assert len(report.batches) == 0

    def test_nonexistent_dir(self, tmp_path):
        d = tmp_path / "nonexistent"
        report = scan_production_output(d)
        assert len(report.batches) == 0

    def test_sorted_newest_first(self, tmp_production_dir):
        report = scan_production_output(tmp_production_dir)
        names = [b.batch_id for b in report.batches]
        assert names == sorted(names, reverse=True)


class TestAssertSafePath:
    def test_valid_output_path(self, tmp_path):
        p = tmp_path / "output" / "production" / "batch_001"
        p.mkdir(parents=True)
        _assert_safe_path(p)  # Should not raise

    def test_invalid_path_raises(self, tmp_path):
        p = tmp_path / "src" / "mathart"
        p.mkdir(parents=True)
        with pytest.raises(AssertionError, match="BLAST RADIUS"):
            _assert_safe_path(p)


class TestSafeNukeJunkBatches:
    def test_deletes_junk(self, tmp_production_dir):
        report = scan_production_output(tmp_production_dir)
        junk = [b for b in report.batches if not b.is_golden]
        output = MagicMock()
        deleted = safe_nuke_junk_batches(junk, output_fn=output)
        assert deleted == 2
        # Verify junk dirs are gone
        remaining = list(tmp_production_dir.iterdir())
        assert len(remaining) == 1
        assert "001018" in remaining[0].name

    def test_empty_list(self):
        output = MagicMock()
        deleted = safe_nuke_junk_batches([], output_fn=output)
        assert deleted == 0


class TestExtractVault:
    def test_extracts_deliverables(self, tmp_production_dir, tmp_path):
        report = scan_production_output(tmp_production_dir)
        golden = [b for b in report.batches if b.is_golden]
        vault_root = tmp_path / "output" / "export_vault"
        output = MagicMock()
        count = extract_vault(golden, vault_root, output_fn=output)
        assert count == 2  # 1 mp4 + 1 png
        assert vault_root.is_dir()
        batch_vault = vault_root / "mass_production_batch_20260424_001018"
        assert batch_vault.is_dir()
        files = list(batch_vault.iterdir())
        assert len(files) == 2

    def test_empty_golden_list(self, tmp_path):
        vault_root = tmp_path / "vault"
        output = MagicMock()
        count = extract_vault([], vault_root, output_fn=output)
        assert count == 0


class TestBatchInfoProperties:
    def test_size_display_mb(self):
        info = BatchInfo(path=Path("/tmp/test"), batch_id="test", size_bytes=5_242_880)
        assert "5.0 MB" in info.size_display

    def test_size_display_gb(self):
        info = BatchInfo(path=Path("/tmp/test"), batch_id="test", size_bytes=2_147_483_648)
        assert "2.00 GB" in info.size_display

    def test_size_mb(self):
        info = BatchInfo(path=Path("/tmp/test"), batch_id="test", size_bytes=1_048_576)
        assert info.size_mb == 1.0
