# SESSION-043 Browser Research Notes

## GitHub Repository Snapshot
- Repository: `jiaxuGOGOGO/MarioTrickster-MathArt`
- Default branch: `main`
- Latest visible short commit on web: `9f1c02b`
- Local full commit hash after clone: `9f1c02bdaa4c794da09b0503d985ccc957aa9317`

## DeepMimic Key Findings
Source: https://xbpeng.github.io/projects/DeepMimic/index.html

1. DeepMimic combines a **motion-imitation objective** with a **task objective**, which is directly applicable to this project's Layer 3 transition tuning loop.
2. The paper frames animation quality as an optimization target over physical behavior rather than as a fixed handcrafted rule list only.
3. The practical mapping for this repository is to convert current transition diagnostics into a scalar loss built from:
   - foot sliding distance,
   - pose discontinuity / bone jerk,
   - contact instability,
   - target-style mismatch.
4. For MarioTrickster-MathArt, we do not need full RL training to benefit from the insight. We can keep the existing runtime query + transition synthesizer pipeline and only optimize transition parameters in a black-box loop.
5. This supports an Optuna-based implementation where each trial proposes transition parameters, synthesizes a transition, evaluates quality, and records the best result for write-back.

## Eureka Key Findings
Source: https://arxiv.org/abs/2310.12931

1. Eureka demonstrates a **zero-human-in-the-loop improvement loop**: generate reward/program candidate -> evaluate in environment -> reflect using metrics -> regenerate improved candidate.
2. The most important transferable idea for this repository is not full RL, but the **closed-loop coding pattern**: parameter proposal, black-box execution, scalar fitness feedback, iterative refinement, and persistent adoption of the best candidate.
3. MarioTrickster-MathArt can map this into a deterministic engineering loop:
   - query a difficult runtime transition,
   - synthesize a candidate transition,
   - score it with Layer 3 diagnostics,
   - update parameters via optimizer,
   - write the best configuration back into the transition rule store.
4. The paper validates that code-generating / parameter-generating loops can outperform static human heuristics when the objective is measurable.

## Optuna Key Findings
Source: https://optuna.readthedocs.io/en/stable/

1. Optuna provides a **define-by-run** API that fits the repository's dynamic search spaces for different transition pairs.
2. The basic engineering structure matches the desired implementation exactly:
   - `objective(trial)` suggests parameters,
   - objective runs the black-box synthesis/evaluation,
   - `study.optimize(objective, n_trials=N)` searches best parameters.
3. Optuna supports pruning and persistent studies, which can later power iterative self-improvement sessions beyond one-off tuning.
4. For this repository, a minimal viable loop can use an in-memory study first, then persist:
   - best params,
   - best loss,
   - trial diagnostics,
   - affected transition key,
   into project JSON artifacts for distillation write-back and audit.

## Research Framing Block

| Field | Value |
|------|------|
| subsystem | Layer 3 closed-loop evolution for runtime transition tuning |
| decision_needed | Whether to implement a passive evaluator extension or an active Optuna-driven synthesize-score-writeback loop |
| already_known | Repo already has RuntimeMotionQuery, TransitionSynthesizer, PhysicsTestBattery, ContractEvolutionBridge, convergence_bridge write path, and lightweight three-layer bookkeeping in evolution_loop.py |
| duplicate_forbidden | Re-researching inertialization basics, motion matching basics, or generic three-layer loop descriptions already absorbed in SESSION-039/040/042 |
| success_signal | A source or design is useful only if it gives a concrete objective function, optimizer pattern, persistence strategy, or deterministic write-back path |

## Gap 4 Design Decision

The project should implement a **new active Layer 3 controller** instead of overloading `ContractEvolutionBridge` directly. The bridge remains the passive compliance judge, while the new subsystem becomes the active coach:

1. **TransitionAutoTuner / Layer3ClosedLoopDistiller**
   - Input: difficult transition specification (`source_state`, `target_state`, optional phase/context)
   - Search space: `blend_time`, `decay_halflife`, `contact_weight`, `velocity_weight`, `phase_weight`, `foot_velocity_weight`, optional strategy choice
   - Engine: Optuna study with deterministic seed and bounded trial count

2. **Objective Function**
   - build runtime query result,
   - synthesize the transition,
   - compute scalar loss from:
     - entry frame cost,
     - inverse transition quality,
     - pose/root discontinuity,
     - contact instability,
     - foot slip proxy.

3. **Write-back Contract**
   - Persist best result into a new rule store such as `transition_rules.json`.
   - Include provenance fields: research/session id, optimizer metadata, best loss, diagnostics, and rule confidence.
   - Feed active rule parameters into `LAYER3_CONVERGENCE_BRIDGE.json` and pipeline contract context so the tuned behavior participates in deterministic hashing.

4. **Three-Layer Evolution Upgrade**
   - Layer 1: identify hard transitions / pending tuning targets.
   - Layer 2: persist source provenance and distillation record for DeepMimic + Eureka + Optuna.
   - Layer 3: execute runtime query -> synthesize -> score -> optimize -> write back -> verify tests.

5. **Audit Requirement**
   - Add tests for optimizer convergence artifact generation, write-back stability, pipeline bridge propagation, and report generation.
