# SESSION-083 RL Reference Notes

## Repository and current context

- GitHub repository confirmed: `jiaxuGOGOGO/MarioTrickster-MathArt`
- Default branch: `main`
- Latest visible GitHub commit at inspection time: `78d45dd`
- Local cloned HEAD: `78d45dd7e302e41a017e3408c267730a294ae740`
- Root context files present: `SESSION_HANDOFF.md`, `PROJECT_BRAIN.json`

## Gymnasium Env contract

Source consulted: <https://gymnasium.farama.org/api/env/>

- Custom environments must implement the `gymnasium.Env` contract.
- `reset(seed=None, options=None)` returns `(observation, info)`.
- The first line of custom `reset()` should be `super().reset(seed=seed)` so seeding is handled correctly.
- `step(action)` returns `(observation, reward, terminated, truncated, info)`.
- `observation_space` and `action_space` must define valid observation/action domains.
- After an episode reaches terminal or truncated state, callers must `reset()` before continuing.

## Current project-state findings

- `SESSION_HANDOFF.md` still describes `P3-GPU-BENCH-1` as not fully closed.
- User instruction explicitly overrides this and requests updating `P3-GPU-BENCH-1` to `CLOSED / DONE` based on successful local RTX 4070 validation.
- `PROJECT_BRAIN.json` still lists top priorities beginning with `P3-GPU-BENCH-1`, then `P1-B4-1`, and currently marks `P3-GPU-BENCH-1` as `TODO`.
- `P1-B4-1` is still pending and is the implementation target for this session.

## DeepMimic and termination semantics

Source consulted: `research/deepmimic_2018.pdf` extracted to `research/deepmimic_2018.txt` from <https://xbpeng.github.io/projects/DeepMimic/DeepMimic_2018.pdf>.

DeepMimic states that **Reference State Initialization (RSI)** samples initial states from the reference motion so the agent can encounter desirable states early in training instead of always having to discover them from a fixed start. The paper explains that RSI exposes the agent to promising states along the motion and can be interpreted as a more informative initial-state distribution.

DeepMimic also states that **Early Termination (ET)** ends an episode when failure conditions are triggered, such as the torso or head contacting the ground. The paper emphasizes that once ET is triggered, the character receives zero reward for the remainder of the episode, discouraging undesirable behavior and preventing early training data from being dominated by failed, ground-struggling states.

Source consulted: <https://farama.org/Gymnasium-Terminated-Truncated-Step-API>

Farama distinguishes **termination** from **truncation**. Termination corresponds to a task-defined terminal state, while truncation corresponds to an external limit such as a maximum step count. Therefore, the RL environment for this repository should use `terminated=True` for imitation failure / fall / reward-collapse conditions, and `truncated=True` only for rollout horizon exhaustion or other exogenous cutoffs.

## Repository-specific implementation constraints and design plan

Inspection of `mathart/animation/umr_rl_adapter.py` shows that the repository already contains the key hot-path substrate required by this task: `flatten_umr_to_rl_state()` pre-bakes UMR clips into contiguous struct-of-arrays buffers, `interpolate_reference()` provides O(1) phase-indexed reference lookup, and `compute_imitation_reward()` implements DeepMimic-style exponential imitation reward terms. Therefore the new Gymnasium environment should reuse this adapter directly rather than introducing a second reference representation.

Inspection of `mathart/core/backend_registry.py` and `tests/test_ci_backend_schemas.py` shows that a new backend must be a registry-native plugin returning a strongly typed `ArtifactManifest`. Because `get_registry()` only auto-imports a fixed set of backend modules, the new RL backend must either become import-reachable from that bootstrap list or be placed inside an already auto-imported backend module. To preserve plugin isolation while keeping discovery automatic, the cleanest landing path is to implement a dedicated `mathart.core.rl_training_backend` module and add one auto-import hook for it in the registry bootstrap sequence.

Inspection of `mathart/core/artifact_schema.py` shows that the project’s artifact discipline is family-driven. Therefore the RL backend should not return an ad-hoc JSON file; it should introduce a dedicated `ArtifactFamily.TRAINING_REPORT` schema with explicit required metadata such as `mean_reward`, `episode_length`, `episodes_run`, and `trainer_mode`. The backend should also declare a dedicated canonical `BackendType.RL_TRAINING` and a capability marker so the registry can expose it without any trunk routing branches.

Inspection of the legacy `mathart/animation/rl_locomotion.py` shows that the repository already has reward composition, rollout statistics, and early termination concepts, but the module predates the current Gymnasium and registry contracts. The new implementation should therefore borrow only concepts such as rollout accounting and reward telemetry, while keeping the actual environment Gymnasium-compliant and the training backend microkernel-native.
