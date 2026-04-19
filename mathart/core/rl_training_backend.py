"""Registry-native RL training backend for pre-baked UMR imitation learning.

SESSION-083 / P1-B4-1
---------------------
This backend closes the first end-to-end RL training loop on top of the
SESSION-080 reference-buffer substrate.  It adheres to the project red lines:

- No trunk ``if/else`` routing changes in AssetPipeline / orchestrators.
- Registered as an independent plugin via ``@register_backend``.
- Returns a strongly typed ``ArtifactManifest`` with a dedicated
  ``TRAINING_REPORT`` artifact family.
- Uses lazy optional imports for heavyweight RL libraries so registry scanning
  remains safe in minimal CI environments.
"""
from __future__ import annotations

import importlib.util
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from mathart.animation.rl_gym_env import LocomotionRLEnv, LocomotionRLEnvConfig
from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import BackendCapability, BackendMeta, register_backend
from mathart.core.backend_types import BackendType

_SESSION_ID = "SESSION-083"
_SCHEMA_VERSION = "1.0.0"


@dataclass(slots=True)
class EpisodeStats:
    """Per-episode rollout statistics."""

    reward: float
    length: int
    terminated: bool
    truncated: bool
    terminated_reason: str
    truncated_reason: str
    final_phase: float
    max_pose_l2: float
    max_com_l2: float


@register_backend(
    BackendType.RL_TRAINING,
    display_name="RL Training Backend (P1-B4-1)",
    version=_SCHEMA_VERSION,
    artifact_families=(ArtifactFamily.TRAINING_REPORT.value,),
    capabilities=(BackendCapability.RL_TRAINING,),
    input_requirements=("state", "frame_count", "fps"),
    dependencies=(BackendType.UNIFIED_MOTION,),
    author="MarioTrickster-MathArt",
    session_origin=_SESSION_ID,
    schema_version=_SCHEMA_VERSION,
)
class RLTrainingBackend:
    """Run a micro-batch RL rollout and emit a strong typed training report."""

    @property
    def name(self) -> str:
        return BackendType.RL_TRAINING.value

    @property
    def meta(self) -> BackendMeta:
        return BackendMeta(
            name=BackendType.RL_TRAINING,
            display_name="RL Training Backend (P1-B4-1)",
            version=_SCHEMA_VERSION,
            artifact_families=(ArtifactFamily.TRAINING_REPORT.value,),
            capabilities=(BackendCapability.RL_TRAINING,),
            input_requirements=("state", "frame_count", "fps"),
            dependencies=(BackendType.UNIFIED_MOTION,),
            session_origin=_SESSION_ID,
            schema_version=_SCHEMA_VERSION,
        )

    def validate_config(self, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Normalize context into a CI-safe micro-batch configuration."""
        output_dir = Path(str(context.get("output_dir", "artifacts/rl_training")))
        output_dir.mkdir(parents=True, exist_ok=True)

        episodes = max(1, int(context.get("episodes", 3)))
        max_episode_steps = max(4, int(context.get("max_episode_steps", 32)))
        frame_count = max(8, int(context.get("frame_count", 30)))
        fps = max(1, int(context.get("fps", 12)))
        seed = int(context.get("seed", 7))
        trainer_mode = str(context.get("trainer_mode", "auto")).strip() or "auto"

        normalized = dict(context)
        normalized.update(
            {
                "output_dir": str(output_dir),
                "name": str(context.get("name", "rl_training")),
                "state": str(context.get("state", "walk")),
                "episodes": episodes,
                "max_episode_steps": max_episode_steps,
                "frame_count": frame_count,
                "fps": fps,
                "seed": seed,
                "trainer_mode": trainer_mode,
            }
        )
        return normalized, []

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        ctx, _warnings = self.validate_config(context)
        output_dir = Path(ctx["output_dir"])
        reference_manifest = ctx.get(f"{BackendType.UNIFIED_MOTION.value}_manifest")

        env = LocomotionRLEnv(
            config=LocomotionRLEnvConfig(
                motion_state=ctx["state"],
                frame_count=ctx["frame_count"],
                fps=ctx["fps"],
                max_episode_steps=ctx["max_episode_steps"],
            ),
            output_dir=output_dir,
            project_root=ctx.get("project_root"),
            session_id=_SESSION_ID,
            reference_manifest=reference_manifest,
            runtime_distillation_bus=ctx.get("runtime_distillation_bus"),
        )

        report = self._run_training(env, ctx)
        report_path = self._write_report(output_dir, ctx["name"], report)

        return ArtifactManifest(
            artifact_family=ArtifactFamily.TRAINING_REPORT.value,
            backend_type=BackendType.RL_TRAINING,
            version=_SCHEMA_VERSION,
            session_id=_SESSION_ID,
            outputs={"report_file": str(report_path)},
            metadata={
                "mean_reward": float(report["mean_reward"]),
                "episode_length": float(report["mean_episode_length"]),
                "episodes_run": int(report["episodes_run"]),
                "trainer_mode": str(report["trainer_mode"]),
                "reference_state": str(report["reference_state"]),
                "obs_dim": int(report["obs_dim"]),
                "act_dim": int(report["act_dim"]),
            },
            quality_metrics={
                "mean_reward": float(report["mean_reward"]),
                "completion_rate": float(report["completion_rate"]),
            },
        )

    # ------------------------------------------------------------------
    # Training modes
    # ------------------------------------------------------------------

    def _run_training(
        self,
        env: LocomotionRLEnv,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        requested_mode = ctx["trainer_mode"]
        sb3_available = importlib.util.find_spec("stable_baselines3") is not None

        if requested_mode in {"auto", "stable_baselines3", "sb3"} and sb3_available:
            try:
                return self._run_stable_baselines3(env, ctx)
            except Exception:
                # Graceful downgrade path mandated by the task.
                pass
        return self._run_random_actor(env, ctx, sb3_available=sb3_available)

    def _run_random_actor(
        self,
        env: LocomotionRLEnv,
        ctx: dict[str, Any],
        *,
        sb3_available: bool,
    ) -> dict[str, Any]:
        episodes: list[EpisodeStats] = []
        t0 = time.perf_counter()
        base_seed = int(ctx["seed"])

        for episode_idx in range(int(ctx["episodes"])):
            obs, info = env.reset(seed=base_seed + episode_idx)
            del obs, info
            total_reward = 0.0
            max_pose_l2 = 0.0
            max_com_l2 = 0.0
            final_info: dict[str, Any] = {}
            terminated = False
            truncated = False

            for _ in range(env.config.max_episode_steps):
                action = env.action_space.sample()
                _, reward, terminated, truncated, step_info = env.step(action)
                total_reward += float(reward)
                max_pose_l2 = max(max_pose_l2, float(step_info.get("pose_l2", 0.0)))
                max_com_l2 = max(max_com_l2, float(step_info.get("com_l2", 0.0)))
                final_info = step_info
                if terminated or truncated:
                    break

            episodes.append(
                EpisodeStats(
                    reward=total_reward,
                    length=int(final_info.get("step_count", env.config.max_episode_steps)),
                    terminated=bool(terminated),
                    truncated=bool(truncated),
                    terminated_reason=str(final_info.get("terminated_reason", "")),
                    truncated_reason=str(final_info.get("truncated_reason", "")),
                    final_phase=float(final_info.get("phase", env._phase)),
                    max_pose_l2=float(max_pose_l2),
                    max_com_l2=float(max_com_l2),
                )
            )

        wall_time_s = float(time.perf_counter() - t0)
        return self._aggregate_report(
            env,
            ctx,
            episodes,
            trainer_mode="random_actor",
            wall_time_s=wall_time_s,
            sb3_available=sb3_available,
            policy_timesteps=0,
        )

    def _run_stable_baselines3(
        self,
        env: LocomotionRLEnv,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        from stable_baselines3 import PPO

        n_steps = max(8, min(int(ctx["max_episode_steps"]), 32))
        batch_size = max(8, min(n_steps, 32))
        total_timesteps = max(n_steps, int(ctx["episodes"]) * int(ctx["max_episode_steps"]))

        t0 = time.perf_counter()
        model = PPO(
            "MlpPolicy",
            env,
            n_steps=n_steps,
            batch_size=batch_size,
            n_epochs=1,
            gamma=0.95,
            learning_rate=3e-4,
            verbose=0,
            device="cpu",
            seed=int(ctx["seed"]),
        )
        model.learn(total_timesteps=total_timesteps, progress_bar=False)

        def _predict(obs: np.ndarray) -> np.ndarray:
            action, _ = model.predict(obs, deterministic=False)
            return np.asarray(action, dtype=np.float32)

        episodes = self._evaluate_policy(
            env,
            _predict,
            episodes=int(ctx["episodes"]),
            seed=int(ctx["seed"]),
        )
        wall_time_s = float(time.perf_counter() - t0)
        return self._aggregate_report(
            env,
            ctx,
            episodes,
            trainer_mode="stable_baselines3_ppo",
            wall_time_s=wall_time_s,
            sb3_available=True,
            policy_timesteps=total_timesteps,
        )

    def _evaluate_policy(
        self,
        env: LocomotionRLEnv,
        policy_fn: Callable[[np.ndarray], np.ndarray],
        *,
        episodes: int,
        seed: int,
    ) -> list[EpisodeStats]:
        results: list[EpisodeStats] = []
        for episode_idx in range(episodes):
            obs, _ = env.reset(seed=seed + episode_idx)
            total_reward = 0.0
            max_pose_l2 = 0.0
            max_com_l2 = 0.0
            terminated = False
            truncated = False
            final_info: dict[str, Any] = {}

            for _ in range(env.config.max_episode_steps):
                action = policy_fn(obs)
                obs, reward, terminated, truncated, step_info = env.step(action)
                total_reward += float(reward)
                max_pose_l2 = max(max_pose_l2, float(step_info.get("pose_l2", 0.0)))
                max_com_l2 = max(max_com_l2, float(step_info.get("com_l2", 0.0)))
                final_info = step_info
                if terminated or truncated:
                    break

            results.append(
                EpisodeStats(
                    reward=total_reward,
                    length=int(final_info.get("step_count", env.config.max_episode_steps)),
                    terminated=bool(terminated),
                    truncated=bool(truncated),
                    terminated_reason=str(final_info.get("terminated_reason", "")),
                    truncated_reason=str(final_info.get("truncated_reason", "")),
                    final_phase=float(final_info.get("phase", env._phase)),
                    max_pose_l2=float(max_pose_l2),
                    max_com_l2=float(max_com_l2),
                )
            )
        return results

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------

    def _aggregate_report(
        self,
        env: LocomotionRLEnv,
        ctx: dict[str, Any],
        episodes: list[EpisodeStats],
        *,
        trainer_mode: str,
        wall_time_s: float,
        sb3_available: bool,
        policy_timesteps: int,
    ) -> dict[str, Any]:
        rewards = [ep.reward for ep in episodes]
        lengths = [ep.length for ep in episodes]
        terminated_count = sum(1 for ep in episodes if ep.terminated)
        truncated_count = sum(1 for ep in episodes if ep.truncated)
        completion_rate = 0.0
        if episodes:
            completion_rate = float(
                sum(1 for ep in episodes if ep.terminated or ep.truncated) / len(episodes)
            )

        return {
            "backend_type": BackendType.RL_TRAINING.value,
            "session_id": _SESSION_ID,
            "trainer_mode": trainer_mode,
            "sb3_available": bool(sb3_available),
            "episodes_run": len(episodes),
            "mean_reward": float(np.mean(rewards) if rewards else 0.0),
            "min_reward": float(np.min(rewards) if rewards else 0.0),
            "max_reward": float(np.max(rewards) if rewards else 0.0),
            "mean_episode_length": float(np.mean(lengths) if lengths else 0.0),
            "max_episode_length": int(max(lengths) if lengths else 0),
            "terminated_episodes": int(terminated_count),
            "truncated_episodes": int(truncated_count),
            "completion_rate": completion_rate,
            "reference_state": env.config.motion_state,
            "reference_clip_id": env.reference_buffers.clip_id,
            "frame_count": env.reference_buffers.num_frames,
            "fps": env.reference_buffers.fps,
            "obs_dim": env.obs_dim,
            "act_dim": env.act_dim,
            "max_episode_steps": env.config.max_episode_steps,
            "seed": int(ctx["seed"]),
            "policy_timesteps": int(policy_timesteps),
            "rollout_wall_time_s": float(wall_time_s),
            "episodes": [asdict(ep) for ep in episodes],
        }

    def _write_report(
        self,
        output_dir: Path,
        stem: str,
        payload: dict[str, Any],
    ) -> Path:
        report_path = output_dir / f"{stem}_training_report.json"
        report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return report_path


__all__ = ["RLTrainingBackend", "EpisodeStats"]
