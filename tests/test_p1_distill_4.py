"""End-to-end tests for P1-DISTILL-4 cognitive science distillation.

This suite validates the required closed loop:

    UnifiedMotionBackend → cognitive telemetry sidecar →
    CognitiveDistillationBackend → knowledge/cognitive_science_rules.json →
    knowledge_preloader → RuntimeDistillationBus.resolve_scalar()

It also proves that ``cognitive_motion`` and ``physics_gait`` distilled spaces
coexist on the same runtime bus without overwriting one another.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    (tmp_path / "knowledge").mkdir()
    return tmp_path


@pytest.fixture
def telemetry_records() -> list[dict[str, Any]]:
    return [
        {
            "solver_type": "xpbd_3d",
            "device": "cpu",
            "frame_count": 60,
            "wall_time_ms": 12.5,
            "ccd_sweep_count": 48,
            "throughput_per_s": 4800.0,
        },
        {
            "solver_type": "xpbd_3d",
            "device": "gpu",
            "frame_count": 60,
            "wall_time_ms": 3.2,
            "ccd_sweep_count": 48,
            "throughput_per_s": 18750.0,
        },
    ]


class TestBackendRegistration:
    def test_backend_type_and_aliases_exist(self):
        from mathart.core.backend_types import BackendType, backend_type_value

        assert hasattr(BackendType, "EVOLUTION_COGNITIVE_DISTILL")
        assert BackendType.EVOLUTION_COGNITIVE_DISTILL.value == "evolution_cognitive_distill"
        assert backend_type_value("cognitive_distill") == "evolution_cognitive_distill"
        assert backend_type_value("biomotion_distill") == "evolution_cognitive_distill"

    def test_registry_discovery(self):
        from mathart.core.backend_registry import BackendCapability, get_registry

        registry = get_registry()
        result = registry.get("evolution_cognitive_distill")
        assert result is not None
        meta, _cls = result
        assert BackendCapability.EVOLUTION_DOMAIN in meta.capabilities


class TestUnifiedMotionTelemetrySidecar:
    def test_unified_motion_emits_cognitive_telemetry(self, work_dir: Path):
        from mathart.core.artifact_schema import validate_artifact
        from mathart.core.builtin_backends import UnifiedMotionBackend

        backend = UnifiedMotionBackend()
        manifest = backend.execute(
            {
                "state": "walk",
                "frame_count": 24,
                "fps": 24,
                "name": "telemetry_probe",
                "output_dir": str(work_dir),
            }
        )

        sidecar_path = Path(manifest.outputs["cognitive_telemetry_json"])
        assert sidecar_path.exists()
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar["frame_count"] == 24
        assert len(sidecar["traces"]) == 24
        assert "root_jerk" in sidecar["traces"][0]
        assert "contact_expectation" in sidecar["traces"][0]
        assert manifest.metadata["cognitive_telemetry"]["summary"]["contact_transition_count"] >= 0
        assert validate_artifact(manifest) == []

    def test_trace_scoring_depends_on_continuous_order(self, work_dir: Path):
        from mathart.animation.principles_quantifier import PrincipleScorer
        from mathart.core.builtin_backends import UnifiedMotionBackend

        backend = UnifiedMotionBackend()
        manifest = backend.execute(
            {
                "state": "jump",
                "frame_count": 32,
                "fps": 24,
                "name": "trace_scoring_probe",
                "output_dir": str(work_dir),
            }
        )
        sidecar = json.loads(Path(manifest.outputs["cognitive_telemetry_json"]).read_text(encoding="utf-8"))
        traces = sidecar["traces"]
        scorer = PrincipleScorer()

        ordered = scorer.score_phase_manifold_consistency(traces, phase_salience=1.0)
        shuffled = scorer.score_phase_manifold_consistency(list(reversed(traces)), phase_salience=1.0)

        assert 0.0 <= ordered <= 1.0
        assert 0.0 <= shuffled <= 1.0
        assert ordered != pytest.approx(shuffled), "Continuous trace order had no effect on the score"


class TestCognitiveKnowledgeClosedLoop:
    def test_backend_writes_knowledge_and_preloads(self, work_dir: Path):
        from mathart.core.cognitive_distillation_backend import CognitiveDistillationBackend
        from mathart.distill.knowledge_preloader import (
            COGNITIVE_MOTION_MODULE,
            register_cognitive_science_knowledge,
        )
        from mathart.distill.runtime_bus import RuntimeDistillationBus

        backend = CognitiveDistillationBackend()
        manifest = backend.execute(
            {
                "output_dir": str(work_dir),
                "reference_contexts": [
                    {"state": "walk", "frame_count": 24, "fps": 24},
                    {"state": "jump", "frame_count": 24, "fps": 24},
                ],
                "max_cognitive_combos": 8,
            }
        )

        knowledge_path = Path(manifest.outputs["knowledge_file"])
        assert knowledge_path.exists()

        bus = RuntimeDistillationBus(project_root=str(work_dir))
        compiled = register_cognitive_science_knowledge(bus, knowledge_path)
        assert compiled.dimensions > 0
        assert bus.get_compiled_space(COGNITIVE_MOTION_MODULE) is not None

        resolved = bus.resolve_scalar(
            ["cognitive_motion.anticipation_bias", "anticipation_bias"],
            default=-1.0,
        )
        assert resolved != -1.0
        assert 0.0 < resolved <= 1.0

    def test_cognitive_and_physics_spaces_coexist(self, work_dir: Path, telemetry_records):
        from mathart.core.cognitive_distillation_backend import CognitiveDistillationBackend
        from mathart.core.physics_gait_distill_backend import DistillSearchAxis, PhysicsGaitDistillationBackend
        from mathart.distill.knowledge_preloader import (
            COGNITIVE_MOTION_MODULE,
            PHYSICS_GAIT_MODULE,
            preload_all_distilled_knowledge,
        )
        from mathart.distill.runtime_bus import RuntimeDistillationBus

        physics_backend = PhysicsGaitDistillationBackend()
        physics_backend.execute(
            {
                "output_dir": str(work_dir),
                "telemetry_records": telemetry_records,
                "physics_axes": [
                    DistillSearchAxis("compliance_distance", (1e-4, 1e-3)),
                    DistillSearchAxis("compliance_bending", (1e-3, 1e-2)),
                    DistillSearchAxis("damping", (0.01, 0.05)),
                    DistillSearchAxis("sub_steps", (2, 4)),
                ],
                "gait_axes": [
                    DistillSearchAxis("blend_time", (0.1, 0.2)),
                    DistillSearchAxis("phase_weight", (0.5, 1.0)),
                ],
                "max_physics_combos": 4,
                "max_gait_combos": 4,
            }
        )

        cognitive_backend = CognitiveDistillationBackend()
        cognitive_backend.execute(
            {
                "output_dir": str(work_dir),
                "reference_contexts": [
                    {"state": "walk", "frame_count": 20, "fps": 20},
                    {"state": "run", "frame_count": 20, "fps": 20},
                ],
                "max_cognitive_combos": 6,
            }
        )

        bus = RuntimeDistillationBus(project_root=str(work_dir))
        loaded = preload_all_distilled_knowledge(bus)

        assert PHYSICS_GAIT_MODULE in loaded
        assert COGNITIVE_MOTION_MODULE in loaded

        physics_value = bus.resolve_scalar(
            ["physics_gait.blend_time", "blend_time"],
            default=-1.0,
        )
        cognitive_value = bus.resolve_scalar(
            ["cognitive_motion.anticipation_bias", "anticipation_bias"],
            default=-1.0,
        )

        assert physics_value != -1.0
        assert cognitive_value != -1.0
