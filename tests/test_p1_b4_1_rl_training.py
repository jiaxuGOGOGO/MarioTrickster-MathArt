"""SESSION-083 / P1-B4-1 regression tests.

These tests verify that the new RL environment and backend satisfy the task's
hard contracts:

- strict Gymnasium ``Env`` API shape
- Reference State Initialization (RSI) on reset
- correct ``terminated`` vs ``truncated`` semantics
- registry-native training backend producing a strong typed training report
- graceful random-actor fallback when Stable-Baselines3 is unavailable
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mathart.animation.rl_gym_env import LocomotionRLEnv, LocomotionRLEnvConfig
from mathart.core.artifact_schema import ArtifactFamily, validate_artifact
from mathart.core.backend_registry import BackendCapability, get_registry
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_rl_env_reset_uses_rsi_and_changes_initial_observation(tmp_path: Path):
    env = LocomotionRLEnv(
        config=LocomotionRLEnvConfig(
            motion_state="walk",
            frame_count=12,
            fps=12,
            max_episode_steps=8,
        ),
        output_dir=tmp_path,
        project_root=_PROJECT_ROOT,
    )

    obs_a, info_a = env.reset(seed=1)
    obs_b, info_b = env.reset(seed=2)

    assert obs_a.shape == (env.obs_dim,)
    assert obs_b.shape == (env.obs_dim,)
    assert info_a["rsi_enabled"] is True
    assert info_b["rsi_enabled"] is True
    assert info_a["reference_phase"] != info_b["reference_phase"]
    assert not np.allclose(obs_a, obs_b)


@pytest.mark.unit
def test_rl_env_step_returns_gymnasium_five_tuple_and_truncates_on_horizon(tmp_path: Path):
    env = LocomotionRLEnv(
        config=LocomotionRLEnvConfig(
            motion_state="walk",
            frame_count=10,
            fps=10,
            max_episode_steps=1,
        ),
        output_dir=tmp_path,
        project_root=_PROJECT_ROOT,
    )

    env.reset(seed=5)
    obs, reward, terminated, truncated, info = env.step(np.zeros(env.act_dim, dtype=np.float32))

    assert obs.shape == (env.obs_dim,)
    assert isinstance(reward, float)
    assert terminated is False
    assert truncated is True
    assert info["truncated_reason"] == "max_episode_steps"


@pytest.mark.unit
def test_rl_env_early_termination_sets_terminated_not_truncated(tmp_path: Path):
    env = LocomotionRLEnv(
        config=LocomotionRLEnvConfig(
            motion_state="walk",
            frame_count=8,
            fps=8,
            max_episode_steps=8,
            action_scale=0.35,
            early_termination_pose_l2_threshold=0.10,
        ),
        output_dir=tmp_path,
        project_root=_PROJECT_ROOT,
    )

    env.reset(seed=9)
    action = np.ones(env.act_dim, dtype=np.float32)
    _, _, terminated, truncated, info = env.step(action)

    assert terminated is True
    assert truncated is False
    assert info["terminated_reason"] in {"pose_diverged", "reward_collapse", "com_diverged", "root_height_diverged"}


@pytest.mark.unit
def test_rl_training_backend_registered_and_produces_training_report(tmp_path: Path):
    reg = get_registry()
    backend_entry = reg.get("rl_training")
    assert backend_entry is not None
    meta, _cls = backend_entry
    assert BackendCapability.RL_TRAINING in meta.capabilities
    assert ArtifactFamily.TRAINING_REPORT.value in meta.artifact_families

    bridge = MicrokernelPipelineBridge(project_root=str(_PROJECT_ROOT), session_id="SESSION-083")
    manifest = bridge.run_backend(
        "rl_training",
        {
            "output_dir": str(tmp_path),
            "name": "rl_backend_ci",
            "state": "walk",
            "frame_count": 8,
            "fps": 8,
        },
    )

    assert manifest.artifact_family == ArtifactFamily.TRAINING_REPORT.value
    assert manifest.outputs["report_file"]
    assert validate_artifact(manifest) == []

    report_path = Path(manifest.outputs["report_file"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["episodes_run"] >= 1
    assert report["trainer_mode"] == "random_actor"
    assert report["reference_state"] == "walk"
    assert report["obs_dim"] > 0
    assert report["act_dim"] > 0
