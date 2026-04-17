"""Tests for SESSION-058 Taichi XPBD cloth backend.

These tests verify that the new Taichi JIT path is operational without
regressing the repository's existing NumPy-first XPBD stack.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.animation.xpbd_taichi import (
    get_taichi_xpbd_backend_status,
    TaichiXPBDClothConfig,
    TaichiXPBDClothSystem,
    create_default_taichi_cloth_config,
)


def test_taichi_backend_status_available():
    """Taichi backend should be importable and initializable."""
    status = get_taichi_xpbd_backend_status(prefer_gpu=True)
    assert status.available, f"Taichi unavailable: {status.import_error}"
    assert status.initialized, f"Taichi init failed: {status.import_error}"


def test_default_config_budget_square():
    """Budget helper should produce a near-square cloth grid."""
    cfg = create_default_taichi_cloth_config(4096)
    assert cfg.width == cfg.height
    assert cfg.particle_count >= 4000


def test_cloth_step_preserves_pinned_corners():
    """Pinned corners must remain anchored after simulation steps."""
    cfg = TaichiXPBDClothConfig(width=12, height=12, prefer_gpu=True, pin_corners=True, pin_top_row=False)
    cloth = TaichiXPBDClothSystem(cfg)
    before = cloth.positions_numpy().copy()
    for _ in range(10):
        cloth.step(1.0 / 60.0)
    after = cloth.positions_numpy()

    np.testing.assert_allclose(after[0, 0], before[0, 0], atol=1e-5)
    np.testing.assert_allclose(after[-1, 0], before[-1, 0], atol=1e-5)


def test_cloth_sags_under_gravity():
    """Mean cloth height should decrease under gravity over time."""
    cfg = TaichiXPBDClothConfig(width=16, height=16, prefer_gpu=True)
    cloth = TaichiXPBDClothSystem(cfg)
    initial = cloth.positions_numpy()[:, :, 1].mean()
    for _ in range(20):
        diag = cloth.step(1.0 / 60.0)
    final_mean = cloth.positions_numpy()[:, :, 1].mean()

    assert final_mean < initial, f"Expected sagging cloth, got initial={initial}, final={final_mean}"
    assert diag.max_constraint_error >= 0.0
    assert diag.particle_count == 256


def test_taichi_cloth_benchmark_runs():
    """Short benchmark run should complete and report positive throughput."""
    cfg = TaichiXPBDClothConfig(width=20, height=20, prefer_gpu=True)
    cloth = TaichiXPBDClothSystem(cfg)
    result = cloth.run(frames=3, dt=1.0 / 60.0)

    assert result.frames == 3
    assert result.particle_count == 400
    assert result.fps > 0.0
