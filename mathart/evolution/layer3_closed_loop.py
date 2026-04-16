"""
SESSION-043: Active Layer 3 Closed Loop for runtime transition tuning.

This module closes the gap between passive Layer 3 evaluation and active
self-improving transition synthesis. The design is intentionally practical:
it reuses the repository's existing runtime motion query and inertialized
transition stack, but replaces fixed hand-tuned parameters with a deterministic
black-box optimization loop powered by Optuna.

The loop follows the project-specific interpretation of DeepMimic, Eureka,
and Bayesian optimization.

1. RuntimeMotionQuery selects the best entry frame in the target clip.
2. TransitionSynthesizer applies the candidate transition parameters.
3. A scalar loss is computed from entry cost, displacement, smoothness,
   pose/root discontinuity, and a planted-foot slip proxy.
4. Optuna searches the parameter space.
5. The best candidate is distilled into ``transition_rules.json`` and merged
   into ``LAYER3_CONVERGENCE_BRIDGE.json`` so later runs can consume the tuned
   parameters through the deterministic pipeline contract.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import optuna
except ImportError:  # pragma: no cover - exercised only when dependency missing
    optuna = None

from ..animation.runtime_motion_query import (
    RuntimeFeatureWeights,
    RuntimeMotionDatabase,
    RuntimeMotionQuery,
)
from ..animation.transition_synthesizer import (
    TransitionStrategy,
    TransitionSynthesizer,
)
from ..animation.unified_motion import MotionContactState, UnifiedMotionFrame


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_project_root(start: Optional[str | Path] = None) -> Path:
    base = Path(start).resolve() if start is not None else Path(__file__).resolve()
    if base.is_file():
        search_root = base.parent
    else:
        search_root = base
    for candidate in [search_root, *search_root.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return search_root


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return float(value)
        return None
    try:
        import numpy as np  # local import to avoid hard dependency in type checking
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:  # pragma: no cover - defensive only
        pass
    return str(value)


@dataclass(frozen=True)
class TransitionTuningTarget:
    """A concrete runtime transition that should be optimized."""

    source_state: str
    target_state: str
    source_phase: float = 0.8
    source_clip_name: str = ""
    target_clip_name: str = ""
    evaluation_window_frames: int = 6
    note: str = ""

    @property
    def transition_key(self) -> str:
        return f"{self.source_state}->{self.target_state}"

    @property
    def resolved_source_clip(self) -> str:
        return self.source_clip_name or self.source_state

    @property
    def resolved_target_clip(self) -> str:
        return self.target_clip_name or self.target_state

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransitionLossWeights:
    """Weights for the scalar objective minimized by the closed loop."""

    entry_cost: float = 0.32
    displacement: float = 0.18
    quality_loss: float = 1.50
    pose_gap: float = 0.10
    root_gap: float = 0.18
    jerk: float = 0.06
    foot_slip: float = 0.22
    contact_instability: float = 0.10

    def to_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


@dataclass
class ClosedLoopRuleRecord:
    """Persisted transition rule produced by active Layer 3 tuning."""

    transition_key: str
    source_state: str
    target_state: str
    source_phase: float
    strategy: str
    params: dict[str, float]
    best_loss: float
    best_trial_number: int
    n_trials: int
    objective_breakdown: dict[str, float] = field(default_factory=dict)
    query_diagnostics: dict[str, Any] = field(default_factory=dict)
    transition_quality: float = 0.0
    research_basis: list[str] = field(default_factory=list)
    session_id: str = "SESSION-043"
    updated_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return _to_json_safe(asdict(self))


@dataclass
class ClosedLoopOptimizationResult:
    """Full result returned by one active closed-loop tuning run."""

    target: TransitionTuningTarget
    rule: ClosedLoopRuleRecord
    report_path: str
    bridge_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "rule": self.rule.to_dict(),
            "report_path": self.report_path,
            "bridge_payload": _to_json_safe(self.bridge_payload),
        }


@dataclass
class Layer3ClosedLoopState:
    """Persistent state for active Layer 3 transition tuning."""

    total_runs: int = 0
    total_rules_written: int = 0
    last_transition_key: str = ""
    last_best_loss: float = 0.0
    last_updated: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _to_json_safe(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Layer3ClosedLoopState":
        return cls(
            total_runs=int(payload.get("total_runs", 0)),
            total_rules_written=int(payload.get("total_rules_written", 0)),
            last_transition_key=str(payload.get("last_transition_key", "")),
            last_best_loss=float(payload.get("last_best_loss", 0.0)),
            last_updated=str(payload.get("last_updated", "")),
            history=list(payload.get("history", []))[-20:],
        )


class TransitionRuleStore:
    """JSON-backed store for distilled runtime transition rules."""

    def __init__(self, project_root: str | Path):
        self.project_root = _find_project_root(project_root)
        self.path = self.project_root / "transition_rules.json"
        self.bridge_path = self.project_root / "LAYER3_CONVERGENCE_BRIDGE.json"

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "version": "1.0",
            "last_updated": "",
            "rule_count": 0,
            "rules": {},
        }

    def get_rule(self, transition_key: str) -> Optional[dict[str, Any]]:
        payload = self.load()
        rules = payload.get("rules", {})
        rule = rules.get(transition_key)
        return _to_json_safe(rule) if rule is not None else None

    def upsert_rule(self, record: ClosedLoopRuleRecord) -> dict[str, Any]:
        payload = self.load()
        rules = dict(payload.get("rules", {}))
        rules[record.transition_key] = record.to_dict()
        payload.update({
            "version": "1.0",
            "last_updated": _utcnow(),
            "rule_count": len(rules),
            "rules": rules,
        })
        self.path.write_text(
            json.dumps(_to_json_safe(payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return payload

    def write_bridge(self, record: ClosedLoopRuleRecord) -> dict[str, Any]:
        existing: dict[str, Any] = {}
        if self.bridge_path.exists():
            try:
                existing = json.loads(self.bridge_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}

        payload = dict(existing)
        payload.update({
            "transition_rule_key": record.transition_key,
            "transition_source_state": record.source_state,
            "transition_target_state": record.target_state,
            "transition_source_phase": record.source_phase,
            "transition_strategy": record.strategy,
            "transition_blend_time": float(record.params.get("blend_time", 0.2)),
            "transition_decay_halflife": float(record.params.get("decay_halflife", 0.05)),
            "runtime_query_velocity_weight": float(record.params.get("velocity_weight", 1.0)),
            "runtime_query_foot_contact_weight": float(record.params.get("foot_contact_weight", 2.0)),
            "runtime_query_phase_weight": float(record.params.get("phase_weight", 0.8)),
            "runtime_query_joint_pose_weight": float(record.params.get("joint_pose_weight", 0.6)),
            "runtime_query_trajectory_weight": float(record.params.get("trajectory_weight", 1.0)),
            "runtime_query_foot_velocity_weight": float(record.params.get("foot_velocity_weight", 1.5)),
            "transition_best_loss": float(record.best_loss),
            "transition_quality": float(record.transition_quality),
            "transition_best_trial_number": int(record.best_trial_number),
            "transition_n_trials": int(record.n_trials),
            "transition_tuned_at": record.updated_at,
            "transition_tuned_session": record.session_id,
            "transition_objective_breakdown": record.objective_breakdown,
        })
        self.bridge_path.write_text(
            json.dumps(_to_json_safe(payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return payload


class Layer3ClosedLoopDistiller:
    """Active coach for runtime transition tuning and distillation write-back."""

    def __init__(
        self,
        project_root: Optional[str | Path] = None,
        session_id: str = "SESSION-043",
        random_seed: int = 42,
        verbose: bool = False,
        loss_weights: Optional[TransitionLossWeights] = None,
    ):
        self.project_root = _find_project_root(project_root)
        self.session_id = session_id
        self.random_seed = int(random_seed)
        self.verbose = bool(verbose)
        self.loss_weights = loss_weights or TransitionLossWeights()
        self.rule_store = TransitionRuleStore(self.project_root)
        self.state_path = self.project_root / ".layer3_closed_loop_state.json"
        self.report_dir = self.project_root / "evolution_reports"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def optimize_transition(
        self,
        target: TransitionTuningTarget,
        n_trials: int = 50,
    ) -> ClosedLoopOptimizationResult:
        self._ensure_optuna()
        sampler = optuna.samplers.TPESampler(seed=self.random_seed)
        study = optuna.create_study(
            direction="minimize",
            sampler=sampler,
            study_name=f"layer3-{target.transition_key}",
        )

        def objective(trial: Any) -> float:
            params = self._suggest_params(trial)
            evaluation = self.evaluate_transition(target, params)
            trial.set_user_attr("objective_breakdown", evaluation["objective_breakdown"])
            trial.set_user_attr("query_diagnostics", evaluation["query_diagnostics"])
            trial.set_user_attr("transition_quality", evaluation["transition_quality"])
            return float(evaluation["loss"])

        study.optimize(objective, n_trials=max(int(n_trials), 1), show_progress_bar=False)
        best_trial = study.best_trial
        best_params = self._normalize_params(best_trial.params)
        rule = ClosedLoopRuleRecord(
            transition_key=target.transition_key,
            source_state=target.source_state,
            target_state=target.target_state,
            source_phase=float(target.source_phase),
            strategy=str(best_params["transition_strategy"]),
            params={k: float(v) for k, v in best_params.items() if k != "transition_strategy"},
            best_loss=float(best_trial.value),
            best_trial_number=int(best_trial.number),
            n_trials=int(n_trials),
            objective_breakdown=_to_json_safe(best_trial.user_attrs.get("objective_breakdown", {})),
            query_diagnostics=_to_json_safe(best_trial.user_attrs.get("query_diagnostics", {})),
            transition_quality=float(best_trial.user_attrs.get("transition_quality", 0.0)),
            research_basis=[
                "DeepMimic (SIGGRAPH 2018): reward-style motion quality objective",
                "Eureka (ICLR 2024): zero-human-in-the-loop improvement loop",
                "Optuna (KDD 2019): define-by-run Bayesian optimization",
            ],
            session_id=self.session_id,
        )

        self.rule_store.upsert_rule(rule)
        bridge_payload = self.rule_store.write_bridge(rule)
        report_path = self._save_report(target, rule)
        self._record_state(rule)

        return ClosedLoopOptimizationResult(
            target=target,
            rule=rule,
            report_path=str(report_path),
            bridge_payload=_to_json_safe(bridge_payload),
        )

    def evaluate_transition(
        self,
        target: TransitionTuningTarget,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self._normalize_params(params)
        database = self._build_database(self._weights_from_params(normalized))
        source_frames = list(database._clip_frames.get(target.resolved_source_clip, []))
        target_frames = list(database._clip_frames.get(target.resolved_target_clip, []))
        if not source_frames or not target_frames:
            raise ValueError(
                f"Missing clips for {target.transition_key}: "
                f"source={target.resolved_source_clip!r}, target={target.resolved_target_clip!r}"
            )

        dt = 1.0 / 24.0
        src_idx = self._phase_to_frame_index(len(source_frames), target.source_phase)
        prev_idx = max(0, src_idx - 1)
        source_frame = source_frames[src_idx]
        prev_source = source_frames[prev_idx] if prev_idx != src_idx else None

        query = RuntimeMotionQuery(database)
        entry = query.query_best_entry(
            current_frame=source_frame,
            target_clip_name=target.resolved_target_clip,
            prev_frame=prev_source,
            dt=dt,
        )
        target_start = database.get_clip_frame(target.resolved_target_clip, entry.entry_frame_idx)
        if target_start is None or not math.isfinite(entry.cost):
            return {
                "loss": 1.0e6,
                "objective_breakdown": {"entry_cost": 1.0e6},
                "query_diagnostics": _to_json_safe(query.get_diagnostics()),
                "transition_quality": 0.0,
            }

        synthesizer = TransitionSynthesizer(
            strategy=TransitionStrategy(str(normalized["transition_strategy"])),
            blend_time=float(normalized["blend_time"]),
            decay_halflife=float(normalized["decay_halflife"]),
        )
        synthesizer.request_transition(
            source_frame=source_frame,
            target_frame=target_start,
            prev_source_frame=prev_source,
            dt=dt,
        )

        prev_output = source_frame
        pose_gap_sum = 0.0
        root_gap_sum = 0.0
        jerk_sum = 0.0
        foot_slip_sum = 0.0
        contact_instability_sum = 0.0
        steps = max(4, int(target.evaluation_window_frames))

        for offset in range(steps):
            clip_idx = min(entry.entry_frame_idx + offset, len(target_frames) - 1)
            target_frame = target_frames[clip_idx]
            output_frame = synthesizer.update(target_frame, dt)
            pose_gap = self._joint_delta(output_frame, target_frame)
            root_gap = self._root_delta(output_frame, target_frame)
            planted = self._planted_feet_count(target_frame.contact_tags)
            foot_slip_sum += root_gap * max(planted, 1)
            contact_instability_sum += self._contact_toggle(prev_output.contact_tags, output_frame.contact_tags)
            pose_gap_sum += pose_gap
            root_gap_sum += root_gap
            jerk_sum += self._joint_delta(output_frame, prev_output) + self._root_delta(output_frame, prev_output)
            prev_output = output_frame

        metrics = synthesizer.get_transition_quality()
        displacement_avg = float(metrics.total_displacement) / max(metrics.frames_processed, 1)
        quality_loss = (1.0 - float(metrics.smoothness)) + (1.0 - float(metrics.contact_preservation))
        pose_gap_avg = pose_gap_sum / max(steps, 1)
        root_gap_avg = root_gap_sum / max(steps, 1)
        jerk_avg = jerk_sum / max(steps, 1)
        foot_slip_avg = foot_slip_sum / max(steps, 1)
        contact_instability_avg = contact_instability_sum / max(steps, 1)

        lw = self.loss_weights
        objective_breakdown = {
            "entry_cost": float(entry.cost),
            "displacement": displacement_avg,
            "quality_loss": quality_loss,
            "pose_gap": pose_gap_avg,
            "root_gap": root_gap_avg,
            "jerk": jerk_avg,
            "foot_slip": foot_slip_avg,
            "contact_instability": contact_instability_avg,
        }
        loss = (
            lw.entry_cost * objective_breakdown["entry_cost"]
            + lw.displacement * objective_breakdown["displacement"]
            + lw.quality_loss * objective_breakdown["quality_loss"]
            + lw.pose_gap * objective_breakdown["pose_gap"]
            + lw.root_gap * objective_breakdown["root_gap"]
            + lw.jerk * objective_breakdown["jerk"]
            + lw.foot_slip * objective_breakdown["foot_slip"]
            + lw.contact_instability * objective_breakdown["contact_instability"]
        )

        transition_quality = max(
            0.0,
            min(
                1.0,
                float(metrics.smoothness)
                * float(metrics.contact_preservation)
                * math.exp(-0.25 * displacement_avg),
            ),
        )

        return {
            "loss": float(loss),
            "objective_breakdown": _to_json_safe(objective_breakdown),
            "query_diagnostics": _to_json_safe(query.get_diagnostics()),
            "transition_quality": float(transition_quality),
        }

    def _suggest_params(self, trial: Any) -> dict[str, Any]:
        strategy = trial.suggest_categorical(
            "transition_strategy",
            [TransitionStrategy.DEAD_BLENDING.value, TransitionStrategy.INERTIALIZATION.value],
        )
        params = {
            "transition_strategy": strategy,
            "blend_time": trial.suggest_float("blend_time", 0.05, 0.40),
            "velocity_weight": trial.suggest_float("velocity_weight", 0.50, 2.00),
            "foot_contact_weight": trial.suggest_float("foot_contact_weight", 1.00, 4.00),
            "phase_weight": trial.suggest_float("phase_weight", 0.20, 1.50),
            "joint_pose_weight": trial.suggest_float("joint_pose_weight", 0.20, 1.20),
            "trajectory_weight": trial.suggest_float("trajectory_weight", 0.20, 1.50),
            "foot_velocity_weight": trial.suggest_float("foot_velocity_weight", 0.50, 3.00),
        }
        if strategy == TransitionStrategy.DEAD_BLENDING.value:
            params["decay_halflife"] = trial.suggest_float("decay_halflife", 0.02, 0.12)
        else:
            params["decay_halflife"] = 0.05
        return params

    @staticmethod
    def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(params)
        normalized.setdefault("transition_strategy", TransitionStrategy.DEAD_BLENDING.value)
        normalized.setdefault("blend_time", 0.2)
        normalized.setdefault("decay_halflife", 0.05)
        normalized.setdefault("velocity_weight", 1.0)
        normalized.setdefault("foot_contact_weight", 2.0)
        normalized.setdefault("phase_weight", 0.8)
        normalized.setdefault("joint_pose_weight", 0.6)
        normalized.setdefault("trajectory_weight", 1.0)
        normalized.setdefault("foot_velocity_weight", 1.5)
        return normalized

    @staticmethod
    def _weights_from_params(params: dict[str, Any]) -> RuntimeFeatureWeights:
        return RuntimeFeatureWeights(
            velocity=float(params["velocity_weight"]),
            foot_contact=float(params["foot_contact_weight"]),
            phase=float(params["phase_weight"]),
            joint_pose=float(params["joint_pose_weight"]),
            trajectory=float(params["trajectory_weight"]),
            foot_velocity=float(params["foot_velocity_weight"]),
        )

    def _build_database(self, weights: RuntimeFeatureWeights) -> RuntimeMotionDatabase:
        database = RuntimeMotionDatabase(weights=weights)
        database.add_from_reference_library()
        database.normalize()
        return database

    @staticmethod
    def _phase_to_frame_index(frame_count: int, source_phase: float) -> int:
        if frame_count <= 1:
            return 0
        phase = min(max(float(source_phase), 0.0), 1.0)
        return int(round(phase * (frame_count - 1)))

    @staticmethod
    def _joint_delta(a: UnifiedMotionFrame, b: UnifiedMotionFrame) -> float:
        joint_names = set(a.joint_local_rotations) | set(b.joint_local_rotations)
        if not joint_names:
            return 0.0
        total = 0.0
        for joint_name in joint_names:
            total += abs(
                float(a.joint_local_rotations.get(joint_name, 0.0))
                - float(b.joint_local_rotations.get(joint_name, 0.0))
            )
        return total / max(len(joint_names), 1)

    @staticmethod
    def _root_delta(a: UnifiedMotionFrame, b: UnifiedMotionFrame) -> float:
        dx = float(a.root_transform.x) - float(b.root_transform.x)
        dy = float(a.root_transform.y) - float(b.root_transform.y)
        dvx = float(a.root_transform.velocity_x) - float(b.root_transform.velocity_x)
        dvy = float(a.root_transform.velocity_y) - float(b.root_transform.velocity_y)
        return math.hypot(dx, dy) + 0.25 * math.hypot(dvx, dvy)

    @staticmethod
    def _planted_feet_count(contact_state: MotionContactState) -> int:
        return int(bool(contact_state.left_foot)) + int(bool(contact_state.right_foot))

    @staticmethod
    def _contact_toggle(prev_state: MotionContactState, next_state: MotionContactState) -> float:
        return float(prev_state.left_foot != next_state.left_foot) + float(
            prev_state.right_foot != next_state.right_foot
        )

    def _save_report(
        self,
        target: TransitionTuningTarget,
        rule: ClosedLoopRuleRecord,
    ) -> Path:
        report_path = self.report_dir / (
            f"layer3_closed_loop_{target.source_state}_to_{target.target_state}.json"
        )
        report_payload = {
            "session_id": self.session_id,
            "generated_at": _utcnow(),
            "target": target.to_dict(),
            "loss_weights": self.loss_weights.to_dict(),
            "rule": rule.to_dict(),
        }
        report_path.write_text(
            json.dumps(_to_json_safe(report_payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return report_path

    def _record_state(self, rule: ClosedLoopRuleRecord) -> None:
        self.state.total_runs += 1
        self.state.total_rules_written += 1
        self.state.last_transition_key = rule.transition_key
        self.state.last_best_loss = float(rule.best_loss)
        self.state.last_updated = _utcnow()
        self.state.history.append({
            "transition_key": rule.transition_key,
            "best_loss": float(rule.best_loss),
            "transition_quality": float(rule.transition_quality),
            "updated_at": rule.updated_at,
            "session_id": rule.session_id,
        })
        self.state.history = self.state.history[-20:]
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_state(self) -> Layer3ClosedLoopState:
        if self.state_path.exists():
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
                return Layer3ClosedLoopState.from_dict(payload)
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        return Layer3ClosedLoopState()

    @staticmethod
    def _ensure_optuna() -> None:
        if optuna is None:
            raise RuntimeError(
                "Optuna is required for Layer 3 active tuning. Install it with `pip install optuna`."
            )


def load_distilled_transition_params(
    project_root: Optional[str | Path] = None,
    transition_key: str = "",
) -> dict[str, Any]:
    """Load the best known transition rule or bridge defaults.

    If ``transition_key`` is provided and present in ``transition_rules.json``,
    the stored rule is returned. Otherwise the function falls back to
    ``LAYER3_CONVERGENCE_BRIDGE.json`` so call sites can benefit from the latest
    distilled parameters without caring about the persistence backend.
    """
    root = _find_project_root(project_root)
    store = TransitionRuleStore(root)
    if transition_key:
        rule = store.get_rule(transition_key)
        if rule is not None:
            params = dict(rule.get("params", {}))
            params["transition_strategy"] = rule.get("strategy", TransitionStrategy.DEAD_BLENDING.value)
            return _to_json_safe(params)

    if store.bridge_path.exists():
        try:
            bridge = json.loads(store.bridge_path.read_text(encoding="utf-8"))
            return {
                "transition_strategy": bridge.get("transition_strategy", TransitionStrategy.DEAD_BLENDING.value),
                "blend_time": float(bridge.get("transition_blend_time", 0.2)),
                "decay_halflife": float(bridge.get("transition_decay_halflife", 0.05)),
                "velocity_weight": float(bridge.get("runtime_query_velocity_weight", 1.0)),
                "foot_contact_weight": float(bridge.get("runtime_query_foot_contact_weight", 2.0)),
                "phase_weight": float(bridge.get("runtime_query_phase_weight", 0.8)),
                "joint_pose_weight": float(bridge.get("runtime_query_joint_pose_weight", 0.6)),
                "trajectory_weight": float(bridge.get("runtime_query_trajectory_weight", 1.0)),
                "foot_velocity_weight": float(bridge.get("runtime_query_foot_velocity_weight", 1.5)),
            }
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    return {
        "transition_strategy": TransitionStrategy.DEAD_BLENDING.value,
        "blend_time": 0.2,
        "decay_halflife": 0.05,
        "velocity_weight": 1.0,
        "foot_contact_weight": 2.0,
        "phase_weight": 0.8,
        "joint_pose_weight": 0.6,
        "trajectory_weight": 1.0,
        "foot_velocity_weight": 1.5,
    }


__all__ = [
    "TransitionTuningTarget",
    "TransitionLossWeights",
    "ClosedLoopRuleRecord",
    "ClosedLoopOptimizationResult",
    "Layer3ClosedLoopState",
    "TransitionRuleStore",
    "Layer3ClosedLoopDistiller",
    "load_distilled_transition_params",
]
