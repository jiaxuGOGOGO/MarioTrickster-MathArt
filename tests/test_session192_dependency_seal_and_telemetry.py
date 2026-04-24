"""SESSION-192 regression tests:

- P0-SESSION-192-DEPENDENCY-SEAL-AND-LOOKDEV-HOTFIX
  - [Dependency Vanguard] websocket-client / watchdog / tabulate are listed
    in ``[project].dependencies`` of pyproject.toml.
  - The new ``[project.optional-dependencies].all`` group aggregates the
    heavy/optional accelerators (taichi, mujoco, stable-baselines3, anthropic).

- [Modal Override] Depth/Normal ControlNet strength is hardened to >= 0.85
  (the SESSION-192 lower-bound red line) and RGB strength stays at 0.0.

- [Physics Telemetry Audit] ``emit_physics_telemetry_handshake`` produces
  the bright-green [🔬 物理总线审计] banner with the exact phrasing the
  director ordered ("16帧日漫抽帧机制已激活", "捕捉到纯数学骨骼位移张量",
  "空间控制网强度拉升至 0.85+", "AI 渲染器已被数学骨架彻底接管").

- [UX Zero-Degradation] ``emit_industrial_baking_banner`` keeps the
  SESSION-191 [⚙️ 工业烘焙网关] / Catmull-Rom user-facing UX contract.

These tests intentionally avoid hitting the real ComfyUI / GPU stack ─
they exercise pure-Python contracts so they run < 1 s on CPU sandboxes.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


# --------------------------------------------------------------------------- #
# Dependency Vanguard
# --------------------------------------------------------------------------- #


def _read_pyproject() -> str:
    assert PYPROJECT.exists(), f"pyproject.toml missing at {PYPROJECT}"
    return PYPROJECT.read_text(encoding="utf-8")


def _extract_block(text: str, header: str) -> str:
    """Return the substring starting at ``header`` until the next top-level
    bracketed table (``[xxx]`` at column 0). Tolerant to whitespace.
    """
    idx = text.find(header)
    assert idx >= 0, f"section {header!r} not found in pyproject.toml"
    # Find the next top-level [xxx] header on its own line after idx + len(header).
    rest = text[idx + len(header):]
    nxt = re.search(r"\n\[[A-Za-z0-9_.\-]+\]", rest)
    end = idx + len(header) + (nxt.start() if nxt else len(rest))
    return text[idx:end]


@pytest.mark.parametrize(
    "package,minimum",
    [
        ("websocket-client", "1.6.0"),
        ("watchdog", "3.0.0"),
        ("tabulate", "0.9.0"),
    ],
)
def test_session192_core_dependency_listed(package: str, minimum: str) -> None:
    text = _read_pyproject()
    deps_block = _extract_block(text, "dependencies = [")
    needle = f'"{package}>={minimum}"'
    assert needle in deps_block, (
        f"SESSION-192 Dependency Vanguard: expected {needle} in core "
        f"dependencies. Block was:\n{deps_block}"
    )


def test_session192_optional_all_group_aggregates_heavy_extras() -> None:
    text = _read_pyproject()
    all_block = _extract_block(text, "all = [")
    for pkg in (
        "taichi>=1.7.0",
        "mujoco>=3.0.0",
        "stable-baselines3>=2.0.0",
        "anthropic>=0.18.0",
    ):
        assert f'"{pkg}"' in all_block, (
            f"SESSION-192: optional-dependencies.all is missing {pkg!r}.\n"
            f"Block was:\n{all_block}"
        )


# --------------------------------------------------------------------------- #
# Modal Override – Depth/Normal hardening
# --------------------------------------------------------------------------- #


def test_session192_depth_normal_strength_at_or_above_redline() -> None:
    from mathart.core.anti_flicker_runtime import (
        DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH,
        DECOUPLED_DEPTH_NORMAL_STRENGTH,
        DECOUPLED_RGB_STRENGTH,
    )

    assert DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH >= 0.85, (
        "SESSION-192 directive: Depth/Normal lower bound must be >= 0.85."
    )
    assert DECOUPLED_DEPTH_NORMAL_STRENGTH >= DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH, (
        "SESSION-192 directive: default Depth/Normal strength must satisfy "
        "the >= 0.85 red line so the diffusion model obeys the math skeleton."
    )
    assert DECOUPLED_RGB_STRENGTH == 0.0, (
        "SESSION-192 directive: RGB ControlNet strength must stay at 0.0 "
        "to fully strip cylinder colour pollution."
    )


def test_session192_force_decouple_payload_reports_min_strength() -> None:
    from mathart.core.anti_flicker_runtime import (
        DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH,
        force_decouple_dummy_mesh_payload,
    )

    # A toy ComfyUI-shaped workflow: one KSampler, one Depth ControlNet, one RGB.
    workflow = {
        "1": {
            "class_type": "KSampler",
            "inputs": {"denoise": 0.4, "seed": 0},
        },
        "2": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {"strength": 0.45},
            "_meta": {"title": "Depth ControlNet"},
        },
        "3": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {"strength": 0.7},
            "_meta": {"title": "SparseCtrl RGB"},
        },
    }
    report = force_decouple_dummy_mesh_payload(workflow)
    assert report["depth_normal_min_strength"] == pytest.approx(
        DECOUPLED_DEPTH_NORMAL_MIN_STRENGTH
    )
    # KSampler denoise must be slammed to 1.0.
    assert workflow["1"]["inputs"]["denoise"] == pytest.approx(1.0)
    # Depth ControlNet strength must hit the >= 0.85 band.
    assert workflow["2"]["inputs"]["strength"] >= 0.85
    # RGB ControlNet strength must be killed to 0.0.
    assert workflow["3"]["inputs"]["strength"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Physics Telemetry Audit handshake
# --------------------------------------------------------------------------- #


def test_session192_telemetry_handshake_text_contract() -> None:
    from mathart.core.anti_flicker_runtime import (
        emit_physics_telemetry_handshake,
        DECOUPLED_DEPTH_NORMAL_STRENGTH,
    )

    text = emit_physics_telemetry_handshake(
        action_name="jump",
        depth_normal_strength=DECOUPLED_DEPTH_NORMAL_STRENGTH,
        rgb_strength=0.0,
        frames=16,
        skeleton_tensor_shape=(16, 24, 3),
    )
    # Director-mandated phrasing — keep these substrings stable.
    assert "物理总线审计" in text
    assert "动作已锁定=jump" in text
    assert "16帧日漫抽帧机制已激活" in text
    assert "捕捉到纯数学骨骼位移张量" in text
    assert "空间控制网强度拉升至" in text
    assert "0.90" in text  # default depth_normal strength formatted
    assert ">= 0.85" in text
    assert "AI 渲染器已被数学骨架彻底接管" in text


def test_session192_telemetry_handshake_writes_ansi_to_stream() -> None:
    from mathart.core.anti_flicker_runtime import emit_physics_telemetry_handshake

    buf = io.StringIO()
    emit_physics_telemetry_handshake(
        action_name="walk",
        skeleton_tensor_shape=(16, 24, 3),
        stream=buf,
    )
    output = buf.getvalue()
    # Bright-green ANSI envelope must be present so it actually pops in
    # the operator's terminal.
    assert "\033[1;92m" in output
    assert "\033[0m" in output
    assert "物理总线审计" in output


def test_session192_telemetry_warns_when_strength_below_redline() -> None:
    from mathart.core.anti_flicker_runtime import emit_physics_telemetry_handshake

    text = emit_physics_telemetry_handshake(
        action_name="idle",
        depth_normal_strength=0.45,  # deliberately below the 0.85 red line
        rgb_strength=0.0,
        frames=16,
    )
    # Warning sigil must appear when the strength falls below the red line.
    assert "⚠️" in text


# --------------------------------------------------------------------------- #
# UX zero-degradation – industrial baking banner contract
# --------------------------------------------------------------------------- #


def test_session192_industrial_baking_banner_keeps_ux_contract() -> None:
    from mathart.core.anti_flicker_runtime import emit_industrial_baking_banner

    plain = emit_industrial_baking_banner()
    assert "工业烘焙网关" in plain
    assert "Catmull-Rom" in plain
    assert "纯 CPU" in plain


def test_session192_industrial_baking_banner_streams_cyan_ansi() -> None:
    from mathart.core.anti_flicker_runtime import emit_industrial_baking_banner

    buf = io.StringIO()
    emit_industrial_baking_banner(stream=buf)
    out = buf.getvalue()
    assert "\033[1;36m" in out  # bold cyan
    assert "工业烘焙网关" in out
