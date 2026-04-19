"""Cognitive science and biological-motion distillation backend.

This module implements **P1-DISTILL-4** as a registry-native microkernel
plugin. It searches cognition-oriented parameters against real continuous
telemetry traces exported by ``UnifiedMotionBackend`` and distils the best
configuration into ``knowledge/cognitive_science_rules.json``.

Design references
-----------------
[1] Disney animation principles — Anticipation and follow-through must be
    measured over temporal build-up and residual motion, not isolated poses.
[2] DeepPhase / periodic autoencoders — motion quality is evaluated from the
    smoothness of a continuous phase manifold rather than a single scalar.
[3] Biological motion perception / point-light walkers — naturalness depends on
    continuous velocity, jerk, and centre-of-mass micro-motion trajectories.
[4] EA Frostbite data-driven configuration — distilled search results are
    persisted as typed JSON assets and preloaded at runtime.

Architecture discipline
-----------------------
- Registered via ``@register_backend`` with ``BackendCapability.EVOLUTION_DOMAIN``.
- Produces ``ArtifactFamily.EVOLUTION_REPORT`` manifests.
- Reuses SESSION-076 ``DistillSearchAxis`` / ``DistillFitnessResult`` /
  Pareto sorting infrastructure instead of inventing a parallel optimizer.
- Consumes real sidecar traces emitted by ``UnifiedMotionBackend``; no
  fabricated single-frame cognition scores are allowed.
- Does not modify orchestrator or kernel hot-path code.
"""
from __future__ import annotations

import json
import logging
import time as _time
from itertools import product
from pathlib import Path
from typing import Any, Sequence

from mathart.animation.principles_quantifier import PrincipleScorer
from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import BackendCapability, BackendMeta, register_backend
from mathart.core.backend_types import BackendType
from mathart.core.physics_gait_distill_backend import (
    DistillFitnessResult,
    DistillSearchAxis,
    _compute_combined_fitness,
    _pareto_rank,
)

logger = logging.getLogger(__name__)

_SESSION_ID = "SESSION-078"
_COGNITIVE_MODULE = "cognitive_motion"

_DEFAULT_COGNITIVE_AXES: tuple[DistillSearchAxis, ...] = (
    DistillSearchAxis(
        name="anticipation_bias",
        values=(0.05, 0.10, 0.15, 0.20),
        unit="ratio",
        description="Target pre-action anticipation ratio derived from Disney anticipation staging.",
    ),
    DistillSearchAxis(
        name="phase_salience",
        values=(0.60, 0.85, 1.00, 1.20),
        unit="weight",
        description="Relative salience of DeepPhase-style phase manifold channels.",
    ),
    DistillSearchAxis(
        name="jerk_tolerance",
        values=(0.04, 0.06, 0.08, 0.12),
        unit="normalized",
        description="Tolerance for biological-motion jerk before perceptual quality degrades.",
    ),
    DistillSearchAxis(
        name="contact_expectation_weight",
        values=(0.60, 0.90, 1.20),
        unit="weight",
        description="Penalty weight for unstable contact expectation transitions in locomotion traces.",
    ),
)

_DEFAULT_REFERENCE_CONTEXTS: tuple[dict[str, Any], ...] = (
    {"state": "walk", "frame_count": 48, "fps": 24},
    {"state": "run", "frame_count": 48, "fps": 24},
    {"state": "jump", "frame_count": 36, "fps": 24},
    {"state": "hit", "frame_count": 36, "fps": 24},
)


def _enumerate_search_configs(
    axes: Sequence[DistillSearchAxis],
    *,
    max_combos: int,
) -> list[dict[str, float]]:
    if not axes:
        return []
    keys = [axis.name for axis in axes]
    value_lists = [tuple(float(v) for v in axis.values) for axis in axes]
    configs: list[dict[str, float]] = []
    for values in product(*value_lists):
        configs.append({name: float(value) for name, value in zip(keys, values)})
        if len(configs) >= max(1, int(max_combos)):
            break
    return configs


def _load_trace_bundle(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("traces"), list):
        raise ValueError(f"Trace bundle missing 'traces': {path}")
    return data


def _reference_bundle_manifest(bundle: dict[str, Any]) -> dict[str, Any]:
    summary = dict(bundle.get("summary", {}))
    return {
        "state": str(bundle.get("state", "unknown")),
        "frame_count": int(bundle.get("frame_count", 0)),
        "fps": int(bundle.get("fps", 0)),
        "trace_count": int(len(bundle.get("traces", []))),
        "summary": summary,
    }


def _evaluate_trace_bundle(
    config: dict[str, float],
    bundle: dict[str, Any],
    scorer: PrincipleScorer,
) -> dict[str, float]:
    traces = bundle.get("traces", [])
    summary = dict(bundle.get("summary", {}))

    anticipation = scorer.score_trace_anticipation(
        traces,
        anticipation_bias=float(config.get("anticipation_bias", 0.12)),
    )
    follow_through = scorer.score_trace_follow_through(traces)
    phase_manifold = scorer.score_phase_manifold_consistency(
        traces,
        phase_salience=float(config.get("phase_salience", 1.0)),
    )
    perceptual_naturalness = scorer.score_perceptual_naturalness(
        traces,
        jerk_tolerance=float(config.get("jerk_tolerance", 0.08)),
        contact_expectation_weight=float(config.get("contact_expectation_weight", 1.0)),
    )

    cognitive_score = (
        0.30 * anticipation
        + 0.25 * follow_through
        + 0.25 * phase_manifold
        + 0.20 * perceptual_naturalness
    )
    perceptual_score = (
        0.55 * perceptual_naturalness
        + 0.25 * follow_through
        + 0.20 * phase_manifold
    )

    trace_count = max(len(traces), 1)
    contact_transitions = float(summary.get("contact_transition_count", 0.0))
    reference_cost_ms = 0.10 * trace_count + 0.50 * contact_transitions
    contact_budget = max(1.0, 0.12 * trace_count)
    perceptual_mismatch = max(0.0, contact_transitions - contact_budget)

    return {
        "anticipation": float(anticipation),
        "follow_through": float(follow_through),
        "phase_manifold_consistency": float(phase_manifold),
        "perceptual_naturalness": float(perceptual_naturalness),
        "cognitive_score": float(cognitive_score),
        "perceptual_score": float(perceptual_score),
        "reference_cost_ms": float(reference_cost_ms),
        "contact_mismatch": float(perceptual_mismatch),
    }


def write_cognitive_science_rules(
    output_path: Path,
    pareto_results: list[DistillFitnessResult],
    *,
    session_id: str = _SESSION_ID,
    search_metadata: dict[str, Any] | None = None,
    reference_bundles: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist cognitive-science distillation results as typed JSON knowledge."""
    pareto_front = [r for r in pareto_results if r.pareto_rank == 0 and r.is_valid()]
    if not pareto_front:
        valid = sorted([r for r in pareto_results if r.is_valid()], key=lambda r: r.combined_fitness)
        pareto_front = valid[:3] if valid else []

    best = pareto_front[0] if pareto_front else None
    best_config = dict(best.config) if best else {}
    best_metrics = dict(getattr(best, "cognitive_metrics", {})) if best is not None else {}

    frontier_records: list[dict[str, Any]] = []
    for idx, result in enumerate(pareto_front):
        frontier_records.append({
            "rank": idx,
            "config": dict(result.config),
            "fitness": {
                "cognitive_loss": float(result.physics_error),
                "perceptual_penalty": float(result.gait_sliding),
                "evaluation_wall_time_ms": float(result.wall_time_ms),
                "contact_mismatch": float(result.ccd_sweep_count),
                "combined": float(result.combined_fitness),
            },
            "metrics": dict(getattr(result, "cognitive_metrics", {})),
            "pareto_rank": int(result.pareto_rank),
        })

    constraints: dict[str, dict[str, Any]] = {}
    if pareto_front:
        param_names = set()
        for result in pareto_front:
            param_names.update(result.config.keys())
        for param in sorted(param_names):
            values = [float(r.config[param]) for r in pareto_front if param in r.config]
            if values:
                constraints[f"{_COGNITIVE_MODULE}.{param}"] = {
                    "param_name": f"{_COGNITIVE_MODULE}.{param}",
                    "min_value": float(min(values)),
                    "max_value": float(max(values)),
                    "default_value": float(best_config.get(param, values[0])),
                    "is_hard": False,
                    "source_rule_id": f"distill_{session_id}_{param}",
                }

    asset = {
        "schema_version": "1.0.0",
        "session_id": session_id,
        "timestamp": _time.time(),
        "backend_type": BackendType.EVOLUTION_COGNITIVE_DISTILL.value,
        "distillation_method": "grid_search_pareto",
        "references": [
            "Disney Animation Principles — anticipation and follow-through",
            "DeepPhase periodic autoencoders for motion phase manifolds",
            "Biological motion perception / point-light walkers",
            "EA Frostbite Data-Oriented Design and data-driven configuration",
        ],
        "search_metadata": search_metadata or {},
        "reference_bundles": [_reference_bundle_manifest(b) for b in (reference_bundles or ())],
        "pareto_frontier": frontier_records,
        "best_config": best_config,
        "best_metrics": best_metrics,
        "parameter_space_constraints": constraints,
        "total_configurations_evaluated": len(pareto_results),
        "valid_configurations": len([r for r in pareto_results if r.is_valid()]),
        "nan_rejected": len([r for r in pareto_results if r.nan_detected]),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(asset, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote cognitive-science knowledge to %s", output_path)
    return asset


@register_backend(
    BackendType.EVOLUTION_COGNITIVE_DISTILL,
    display_name="Cognitive Science Distillation (P1-DISTILL-4)",
    version="1.0.0",
    artifact_families=(ArtifactFamily.EVOLUTION_REPORT.value,),
    capabilities=(BackendCapability.EVOLUTION_DOMAIN,),
    input_requirements=("output_dir",),
    author="MarioTrickster-MathArt",
    session_origin=_SESSION_ID,
)
class CognitiveDistillationBackend:
    """Distil cognition-aware motion parameters from exported telemetry traces."""

    @property
    def name(self) -> str:
        return BackendType.EVOLUTION_COGNITIVE_DISTILL.value

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        root = Path(context.get("output_dir", ".")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        axes = context.get("cognitive_axes", _DEFAULT_COGNITIVE_AXES)
        max_combos = int(context.get("max_cognitive_combos", 48))
        scorer = PrincipleScorer()

        trace_bundles = self._collect_trace_bundles(root, context)
        search_configs = _enumerate_search_configs(axes, max_combos=max_combos)

        all_results: list[DistillFitnessResult] = []
        for config in search_configs:
            t0 = _time.perf_counter()
            bundle_metrics = [_evaluate_trace_bundle(config, bundle, scorer) for bundle in trace_bundles]
            elapsed_ms = (_time.perf_counter() - t0) * 1000.0
            if not bundle_metrics:
                result = DistillFitnessResult(config=dict(config), nan_detected=True)
                result.cognitive_metrics = {"error": "no_trace_bundles"}
                all_results.append(result)
                continue

            anticipation = sum(m["anticipation"] for m in bundle_metrics) / len(bundle_metrics)
            follow = sum(m["follow_through"] for m in bundle_metrics) / len(bundle_metrics)
            phase = sum(m["phase_manifold_consistency"] for m in bundle_metrics) / len(bundle_metrics)
            natural = sum(m["perceptual_naturalness"] for m in bundle_metrics) / len(bundle_metrics)
            cognitive_score = sum(m["cognitive_score"] for m in bundle_metrics) / len(bundle_metrics)
            perceptual_score = sum(m["perceptual_score"] for m in bundle_metrics) / len(bundle_metrics)
            reference_cost_ms = sum(m["reference_cost_ms"] for m in bundle_metrics) / len(bundle_metrics)
            contact_mismatch = sum(m["contact_mismatch"] for m in bundle_metrics)

            cognitive_loss = max(0.0, 1.0 - cognitive_score)
            perceptual_penalty = max(0.0, 1.0 - perceptual_score)
            wall_time_ms = float(elapsed_ms + reference_cost_ms)
            result = DistillFitnessResult(
                config=dict(config),
                physics_error=float(cognitive_loss),
                gait_sliding=float(perceptual_penalty),
                wall_time_ms=wall_time_ms,
                ccd_sweep_count=float(contact_mismatch),
                combined_fitness=_compute_combined_fitness(
                    cognitive_loss,
                    perceptual_penalty,
                    wall_time_ms,
                    contact_mismatch,
                ),
            )
            result.cognitive_metrics = {
                "anticipation": float(anticipation),
                "follow_through": float(follow),
                "phase_manifold_consistency": float(phase),
                "perceptual_naturalness": float(natural),
                "cognitive_score": float(cognitive_score),
                "perceptual_score": float(perceptual_score),
                "reference_cost_ms": float(reference_cost_ms),
                "contact_mismatch": float(contact_mismatch),
            }
            all_results.append(result)

        all_results = _pareto_rank(all_results)

        knowledge_path = root / "knowledge" / "cognitive_science_rules.json"
        search_metadata = {
            "cognitive_axes_count": len(axes),
            "total_combos_evaluated": len(all_results),
            "trace_bundle_count": len(trace_bundles),
            "reference_states": [str(bundle.get("state", "unknown")) for bundle in trace_bundles],
        }
        asset = write_cognitive_science_rules(
            knowledge_path,
            all_results,
            session_id=_SESSION_ID,
            search_metadata=search_metadata,
            reference_bundles=trace_bundles,
        )

        report_path = root / "cognitive_science_distill_report.json"
        best_fitness = min((r.combined_fitness for r in all_results if r.is_valid()), default=1.0)
        report = {
            "backend_type": BackendType.EVOLUTION_COGNITIVE_DISTILL.value,
            "timestamp": _time.time(),
            "search_wall_time_s": float(sum(r.wall_time_ms for r in all_results if r.is_valid()) / 1000.0),
            "total_evaluated": len(all_results),
            "valid_count": len([r for r in all_results if r.is_valid()]),
            "pareto_front_size": len([r for r in all_results if r.pareto_rank == 0]),
            "best_config": asset.get("best_config", {}),
            "best_metrics": asset.get("best_metrics", {}),
            "best_fitness": float(best_fitness),
            "knowledge_path": str(knowledge_path),
            "reference_bundle_count": len(trace_bundles),
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

        return ArtifactManifest(
            artifact_family=ArtifactFamily.EVOLUTION_REPORT.value,
            backend_type=BackendType.EVOLUTION_COGNITIVE_DISTILL.value,
            version="1.0.0",
            session_id=_SESSION_ID,
            outputs={
                "report_file": str(report_path),
                "knowledge_file": str(knowledge_path),
            },
            metadata={
                "cycle_count": 1,
                "best_fitness": float(best_fitness),
                "knowledge_rules_distilled": len(asset.get("pareto_frontier", [])),
                "knowledge_path": str(knowledge_path),
                "trace_bundle_count": len(trace_bundles),
            },
            quality_metrics={
                "best_combined_fitness": float(best_fitness),
                "pareto_front_size": float(len([r for r in all_results if r.pareto_rank == 0])),
            },
        )

    def _collect_trace_bundles(self, root: Path, context: dict[str, Any]) -> list[dict[str, Any]]:
        bundles: list[dict[str, Any]] = []

        for payload in context.get("cognitive_telemetry_payloads", []) or []:
            if isinstance(payload, dict) and isinstance(payload.get("traces"), list):
                bundles.append(dict(payload))

        for path_like in context.get("cognitive_telemetry_files", []) or []:
            path = Path(path_like)
            if path.exists():
                bundles.append(_load_trace_bundle(path))

        if bundles:
            return bundles

        from mathart.core.builtin_backends import UnifiedMotionBackend

        backend = UnifiedMotionBackend()
        reference_dir = root / "cognitive_reference_motion"
        reference_dir.mkdir(parents=True, exist_ok=True)
        reference_contexts = context.get("reference_contexts", _DEFAULT_REFERENCE_CONTEXTS)

        for idx, spec in enumerate(reference_contexts):
            state = str(spec.get("state", "idle"))
            name = str(spec.get("name", f"cognitive_ref_{idx}_{state}"))
            backend_context = {
                "state": state,
                "frame_count": int(spec.get("frame_count", 48)),
                "fps": int(spec.get("fps", 24)),
                "name": name,
                "output_dir": str(reference_dir),
                "runtime_distillation_bus": context.get("runtime_distillation_bus"),
            }
            manifest = backend.execute(backend_context)
            sidecar_path = Path(manifest.outputs["cognitive_telemetry_json"])
            bundle = _load_trace_bundle(sidecar_path)
            bundle["motion_clip_json"] = str(manifest.outputs.get("motion_clip_json", ""))
            bundles.append(bundle)

        return bundles


__all__ = [
    "CognitiveDistillationBackend",
    "write_cognitive_science_rules",
]
