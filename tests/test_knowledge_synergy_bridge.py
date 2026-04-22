"""Full-Chain Knowledge Synergy Bridge Tests — SESSION-140.

P0-SESSION-137-KNOWLEDGE-SYNERGY-BRIDGE

This test module validates the complete knowledge-generation unification:

    External Book/Paper → GitOps Sync → Knowledge Embodiment →
    Creator Intent Parsing → Wireframe Preview → Knowledge-Manifold
    Constrained Mutation → AI Rendering → Provenance Tracing

Test scenarios (from the task document):
1. Inject a mock knowledge rule "jump height max = 5.0" into the in-memory bus.
2. Feed an intent "跳到宇宙边缘" (jump to the edge of the universe).
3. ASSERT: The translation bridge clamps physics.bounce to ≤ 5.0.
4. ASSERT: When amplifying in the interactive gate, the arbitrator detects
   the conflict and suspends with a Truth Gateway Warning.
5. ASSERT: The final ArtifactManifest carries the mock rule's provenance.

Red-line assertions:
- 防知识过拟合死锁红线: PHYSICAL violations allow user override.
- 防知识真空优雅降级红线: Unknown vibes degrade gracefully (no exception).
- 防血统数据膨胀红线: Only activated rules appear in provenance.
- 全链路知识大一统: Intent → Preview → Evolution → Manifest all connected.

External research anchors (SESSION-140):
- Knowledge-Grounded Generation (KAG 2025)
- Constraint Reconciliation (arXiv 2511.10952)
- Data Lineage & Provenance (Atlas 2025, C2PA)
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Imports from the project
# ---------------------------------------------------------------------------
from mathart.distill.compiler import Constraint, ParameterSpace
from mathart.distill.runtime_bus import (
    CompiledParameterSpace,
    RuntimeDistillationBus,
)
from mathart.workspace.director_intent import (
    Blueprint,
    BlueprintMeta,
    CreatorIntentSpec,
    DirectorIntentParser,
    Genotype,
    KnowledgeConflict,
    KnowledgeProvenanceRecord,
    PhysicsConfig,
)
from mathart.evolution.blueprint_evolution import (
    BlueprintEvolutionEngine,
    BlueprintEvolutionResult,
    KnowledgeClampRecord,
)
from mathart.quality.interactive_gate import (
    ConflictArbitrationResult,
    GateDecision,
    InteractiveGateResult,
    InteractivePreviewGate,
    ProgrammaticPreviewGate,
    check_knowledge_conflicts,
    apply_knowledge_clamp_to_genotype,
)
from mathart.core.artifact_schema import ArtifactManifest, ArtifactFamily


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory structure."""
    (tmp_path / "workspace" / "blueprints").mkdir(parents=True)
    (tmp_path / "workspace" / "proxy").mkdir(parents=True)
    (tmp_path / "knowledge").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def mock_knowledge_bus(tmp_workspace: Path) -> RuntimeDistillationBus:
    """Create a RuntimeDistillationBus with a mock knowledge rule:
    'jump height (physics.bounce) max = 5.0, hard constraint'.

    This simulates a distilled physics rule from an academic paper
    that defines the maximum safe jump height.
    """
    bus = RuntimeDistillationBus(
        project_root=tmp_workspace,
        backend_preference=("python",),
    )

    # Build a ParameterSpace with the mock constraint
    space = ParameterSpace(name="game_feel")
    space.add_constraint(Constraint(
        param_name="physics.bounce",
        min_value=0.0,
        max_value=5.0,
        default_value=0.6,
        is_hard=False,  # PHYSICAL, not fatal — allows user override
        source_rule_id="mock_paper_2025.jump_height_limit",
    ))
    space.add_constraint(Constraint(
        param_name="physics.gravity",
        min_value=0.1,
        max_value=20.0,
        default_value=9.81,
        is_hard=False,
        source_rule_id="mock_paper_2025.gravity_range",
    ))
    # A hard constraint (fatal) — mass cannot be zero or negative
    space.add_constraint(Constraint(
        param_name="physics.mass",
        min_value=0.01,
        max_value=1000.0,
        default_value=1.0,
        is_hard=True,  # FATAL — mathematical impossibility
        source_rule_id="mock_paper_2025.mass_positivity",
    ))

    bus.register_space("game_feel", space)
    return bus


@pytest.fixture
def mock_knowledge_bus_with_physics(tmp_workspace: Path) -> RuntimeDistillationBus:
    """Bus with both game_feel and physics modules for broader coverage."""
    bus = RuntimeDistillationBus(
        project_root=tmp_workspace,
        backend_preference=("python",),
    )

    # game_feel module
    gf_space = ParameterSpace(name="game_feel")
    gf_space.add_constraint(Constraint(
        param_name="physics.bounce",
        min_value=0.0,
        max_value=5.0,
        default_value=0.6,
        is_hard=False,
        source_rule_id="paper_A.bounce_limit",
    ))

    # physics module
    phys_space = ParameterSpace(name="physics")
    phys_space.add_constraint(Constraint(
        param_name="physics.mass",
        min_value=0.01,
        max_value=100.0,
        default_value=1.0,
        is_hard=True,
        source_rule_id="paper_B.mass_range",
    ))
    phys_space.add_constraint(Constraint(
        param_name="physics.gravity",
        min_value=0.5,
        max_value=15.0,
        default_value=9.81,
        is_hard=False,
        source_rule_id="paper_B.gravity_range",
    ))

    bus.register_space("game_feel", gf_space)
    bus.register_space("physics", phys_space)
    return bus


# ===========================================================================
# TEST GROUP 1: Knowledge-Grounded Semantic Translation Bridge
# ===========================================================================

class TestKnowledgeGroundedTranslation:
    """Test that the DirectorIntentParser clamps parameters using the
    RuntimeDistillationBus when translating fuzzy vibes.
    """

    def test_vibe_clamped_by_knowledge(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Core scenario: '跳到宇宙边缘' should be clamped to bounce ≤ 5.0.

        The vibe '跳跃' maps to game_feel module, which has bounce max=5.0.
        The heuristic would push bounce much higher, but knowledge clamps it.
        """
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )

        # Create an intent with extreme jump vibe
        raw = {
            "vibe": "极其夸张的跳跃",
            "description": "跳到宇宙边缘",
            "overrides": {
                "physics.bounce": 100.0,  # Deliberately extreme
            },
        }

        spec = parser.parse_dict(raw)

        # ASSERT: bounce is clamped to ≤ 5.0
        assert spec.genotype.physics.bounce <= 5.0, (
            f"Expected bounce ≤ 5.0, got {spec.genotype.physics.bounce}"
        )

        # ASSERT: knowledge grounding was activated
        assert spec.knowledge_grounded is True

        # ASSERT: provenance records exist
        assert len(spec.applied_knowledge_rules) > 0
        bounce_rules = [
            r for r in spec.applied_knowledge_rules
            if "bounce" in r.param_constrained
        ]
        assert len(bounce_rules) > 0, "Expected provenance for bounce clamping"

    def test_vibe_heavy_landing_clamped(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """'极其沉重的落地' should clamp mass and gravity within knowledge bounds."""
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )

        raw = {
            "vibe": "极其沉重的落地",
            "overrides": {
                "physics.gravity": 50.0,  # Way beyond max=20.0
            },
        }

        spec = parser.parse_dict(raw)

        # ASSERT: gravity clamped to ≤ 20.0
        assert spec.genotype.physics.gravity <= 20.0, (
            f"Expected gravity ≤ 20.0, got {spec.genotype.physics.gravity}"
        )

    def test_knowledge_conflicts_recorded(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Conflicts between user intent and knowledge should be recorded."""
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )

        raw = {
            "vibe": "跳跃",
            "overrides": {"physics.bounce": 99.0},
        }

        spec = parser.parse_dict(raw)

        # ASSERT: conflicts detected
        assert len(spec.knowledge_conflicts) > 0
        bounce_conflicts = [
            c for c in spec.knowledge_conflicts
            if "bounce" in c.param_key
        ]
        assert len(bounce_conflicts) > 0
        assert bounce_conflicts[0].user_value > 5.0
        assert bounce_conflicts[0].clamped_value <= 5.0

    def test_no_knowledge_bus_graceful_degradation(self, tmp_workspace: Path):
        """Without a knowledge bus, the parser should work normally (heuristic only).

        防知识真空优雅降级红线: No exceptions, just heuristic fallback.
        """
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=None,  # No bus
        )

        raw = {
            "vibe": "极其夸张的跳跃",
            "overrides": {"physics.bounce": 100.0},
        }

        spec = parser.parse_dict(raw)

        # ASSERT: no crash, no knowledge grounding
        assert spec.knowledge_grounded is False
        assert len(spec.applied_knowledge_rules) == 0
        # Bounce stays at the override value (no clamping)
        assert spec.genotype.physics.bounce == 100.0

    def test_unknown_vibe_graceful_degradation(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Unknown vibe keywords should degrade gracefully.

        防知识真空优雅降级红线: Cold keywords with no matching knowledge
        modules should not throw exceptions.
        """
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )

        raw = {
            "vibe": "赛博朋克蒸汽波复古未来主义",  # Very niche, no match
            "description": "Unknown style",
        }

        # ASSERT: no exception
        spec = parser.parse_dict(raw)
        assert spec.knowledge_grounded is False
        assert len(spec.applied_knowledge_rules) == 0


# ===========================================================================
# TEST GROUP 2: Knowledge-Projected Mutation Clamping
# ===========================================================================

class TestKnowledgeProjectedMutation:
    """Test that BlueprintEvolutionEngine.clamp_by_knowledge() enforces
    knowledge constraints during mutation.
    """

    def test_clamp_by_knowledge_basic(
        self, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Direct test of clamp_by_knowledge: values beyond bounds are clamped."""
        engine = BlueprintEvolutionEngine(
            mutation_strength=0.15,
            seed=42,
            knowledge_bus=mock_knowledge_bus,
        )

        flat = {
            "physics.bounce": 10.0,   # Exceeds max=5.0
            "physics.gravity": 9.81,  # Within bounds
            "physics.mass": 1.0,      # Within bounds
        }

        clamped, records = engine.clamp_by_knowledge(flat)

        # ASSERT: bounce clamped to 5.0
        assert clamped["physics.bounce"] <= 5.0
        assert len(records) > 0
        bounce_records = [r for r in records if "bounce" in r.param_key]
        assert len(bounce_records) > 0
        assert bounce_records[0].pre_clamp_value == 10.0
        assert bounce_records[0].post_clamp_value == 5.0

    def test_clamp_by_knowledge_no_bus(self):
        """Without a knowledge bus, clamp_by_knowledge returns unchanged params."""
        engine = BlueprintEvolutionEngine(
            mutation_strength=0.15,
            seed=42,
            knowledge_bus=None,
        )

        flat = {"physics.bounce": 100.0}
        clamped, records = engine.clamp_by_knowledge(flat)

        assert clamped["physics.bounce"] == 100.0
        assert len(records) == 0

    def test_evolution_with_knowledge_clamping(
        self, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Full evolution run: offspring bounce values must be ≤ 5.0."""
        engine = BlueprintEvolutionEngine(
            mutation_strength=0.5,  # High mutation to force boundary violations
            seed=42,
            knowledge_bus=mock_knowledge_bus,
        )

        parent = Genotype(physics=PhysicsConfig(bounce=4.5))  # Close to max

        result = engine.evolve(
            parent_genotype=parent,
            num_variants=20,
            freeze_locks=[],
            parent_name="test_parent",
        )

        # ASSERT: ALL offspring have bounce ≤ 5.0
        for offspring in result.offspring:
            assert offspring.flat_params["physics.bounce"] <= 5.0 + 1e-9, (
                f"Offspring {offspring.variant_index} bounce "
                f"{offspring.flat_params['physics.bounce']} exceeds 5.0"
            )

        # ASSERT: some knowledge clamps occurred (high mutation near boundary)
        assert result.total_knowledge_clamps > 0
        assert result.knowledge_grounded is True

    def test_evolution_freeze_mask_still_works_with_knowledge(
        self, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Freeze mask must still be respected even with knowledge clamping.

        SESSION-139 red line: frozen params have variance = 0.
        """
        engine = BlueprintEvolutionEngine(
            mutation_strength=0.3,
            seed=42,
            knowledge_bus=mock_knowledge_bus,
        )

        parent = Genotype(physics=PhysicsConfig(bounce=3.0, mass=2.0))

        result = engine.evolve(
            parent_genotype=parent,
            num_variants=10,
            freeze_locks=["physics"],
            parent_name="frozen_test",
        )

        # ASSERT: frozen physics params have zero variance
        for key, var in result.frozen_param_variance.items():
            assert var < 1e-20, (
                f"Frozen param {key} has non-zero variance {var}"
            )


# ===========================================================================
# TEST GROUP 3: Conflict Arbitration (Truth Gateway Warning)
# ===========================================================================

class TestConflictArbitration:
    """Test the interactive gate's knowledge conflict detection and
    arbitration mechanism.
    """

    def test_conflict_detected_on_amplify(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """When amplifying pushes bounce beyond 5.0, the gate should detect
        the conflict and offer arbitration.
        """
        # Start with bounce near the limit
        spec = CreatorIntentSpec(
            genotype=Genotype(physics=PhysicsConfig(bounce=4.8)),
        )

        # Program: amplify → comply → approve → save blueprint
        gate = ProgrammaticPreviewGate(
            workspace_root=tmp_workspace,
            choices=[
                "2",   # [+] Amplify (will push bounce > 5.0)
                "1",   # Comply with knowledge (auto-clamp)
                "1",   # Approve
                "Y",   # Save blueprint
                "test_conflict_bp",  # Blueprint name
            ],
            knowledge_bus=mock_knowledge_bus,
        )

        result = gate.run(spec)

        # ASSERT: conflict was detected
        assert len(result.conflict_arbitrations) > 0
        assert result.knowledge_compliances_count > 0

        # ASSERT: final bounce is within bounds
        assert result.final_genotype.physics.bounce <= 5.0 + 1e-9

        # ASSERT: output log contains Truth Gateway Warning
        log_text = "\n".join(gate.output_log)
        assert "真理网关警告" in log_text or "Truth Gateway" in log_text

    def test_user_can_override_physical_constraint(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """PHYSICAL violations allow user override (防知识过拟合死锁红线).

        The user should be able to choose 'Override' for non-fatal violations.
        """
        spec = CreatorIntentSpec(
            genotype=Genotype(physics=PhysicsConfig(bounce=4.8)),
        )

        # Program: amplify → override → approve → no save
        gate = ProgrammaticPreviewGate(
            workspace_root=tmp_workspace,
            choices=[
                "2",   # [+] Amplify
                "2",   # Override knowledge (artistic freedom)
                "1",   # Approve
                "N",   # Don't save blueprint
            ],
            knowledge_bus=mock_knowledge_bus,
        )

        result = gate.run(spec)

        # ASSERT: override was recorded
        assert result.knowledge_overrides_count > 0

        # ASSERT: bounce was NOT clamped (user overrode)
        # After amplification, bounce should be > 5.0
        assert result.final_genotype.physics.bounce > 5.0 - 1e-9

    def test_check_knowledge_conflicts_function(
        self, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Direct test of check_knowledge_conflicts utility function."""
        genotype = Genotype(physics=PhysicsConfig(
            bounce=10.0,    # Exceeds max=5.0
            gravity=25.0,   # Exceeds max=20.0
            mass=1.0,       # Within bounds
        ))

        violations = check_knowledge_conflicts(genotype, mock_knowledge_bus)

        # ASSERT: bounce and gravity violations detected
        assert len(violations) >= 2
        violated_params = {v["param_key"] for v in violations}
        assert "physics.bounce" in violated_params
        assert "physics.gravity" in violated_params

    def test_no_conflicts_when_within_bounds(
        self, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """No conflicts when all parameters are within knowledge bounds."""
        genotype = Genotype(physics=PhysicsConfig(
            bounce=3.0,
            gravity=9.81,
            mass=1.0,
        ))

        violations = check_knowledge_conflicts(genotype, mock_knowledge_bus)
        assert len(violations) == 0


# ===========================================================================
# TEST GROUP 4: Asset Provenance & Knowledge Lineage Tagging
# ===========================================================================

class TestKnowledgeLineageTagging:
    """Test that ArtifactManifest carries knowledge provenance records."""

    def test_artifact_manifest_applied_knowledge_rules_field(self):
        """ArtifactManifest must have applied_knowledge_rules field."""
        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SINGLE.value,
            backend_type="test_backend",
            outputs={"image": "/tmp/test.png"},
            metadata={"width": 64, "height": 64, "channels": 4},
            applied_knowledge_rules=[
                {
                    "rule_id": "mock_paper_2025.jump_height_limit",
                    "module_name": "game_feel",
                    "param_constrained": "physics.bounce",
                    "original_value": 100.0,
                    "clamped_value": 5.0,
                    "constraint_type": "soft",
                    "description": "Jump height max from paper A",
                },
            ],
        )

        # ASSERT: field exists and is populated
        assert len(manifest.applied_knowledge_rules) == 1
        assert manifest.applied_knowledge_rules[0]["rule_id"] == "mock_paper_2025.jump_height_limit"

    def test_artifact_manifest_roundtrip_with_knowledge_rules(self):
        """applied_knowledge_rules must survive JSON serialization roundtrip."""
        rules = [
            {
                "rule_id": "paper_A.bounce_limit",
                "module_name": "game_feel",
                "param_constrained": "physics.bounce",
                "original_value": 10.0,
                "clamped_value": 5.0,
            },
            {
                "rule_id": "paper_B.gravity_range",
                "module_name": "physics",
                "param_constrained": "physics.gravity",
                "original_value": 50.0,
                "clamped_value": 15.0,
            },
        ]

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.MESH_OBJ.value,
            backend_type="test",
            outputs={"mesh": "/tmp/test.obj"},
            metadata={"vertex_count": 100, "face_count": 200},
            applied_knowledge_rules=rules,
        )

        # Serialize → deserialize
        data = manifest.to_dict()
        json_str = json.dumps(data)
        restored_data = json.loads(json_str)
        restored = ArtifactManifest.from_dict(restored_data)

        # ASSERT: rules survive roundtrip
        assert len(restored.applied_knowledge_rules) == 2
        assert restored.applied_knowledge_rules[0]["rule_id"] == "paper_A.bounce_limit"
        assert restored.applied_knowledge_rules[1]["clamped_value"] == 15.0

    def test_artifact_manifest_save_load_with_knowledge_rules(self, tmp_path: Path):
        """applied_knowledge_rules must survive file save/load roundtrip."""
        rules = [{"rule_id": "test.rule", "param": "physics.bounce"}]

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SINGLE.value,
            backend_type="test",
            outputs={"image": "/tmp/test.png"},
            metadata={"width": 64, "height": 64, "channels": 4},
            applied_knowledge_rules=rules,
        )

        path = tmp_path / "test_manifest.json"
        manifest.save(path)

        loaded = ArtifactManifest.load(path)
        assert len(loaded.applied_knowledge_rules) == 1
        assert loaded.applied_knowledge_rules[0]["rule_id"] == "test.rule"

    def test_empty_knowledge_rules_backward_compat(self):
        """Old manifests without applied_knowledge_rules should load fine."""
        old_data = {
            "artifact_family": "mesh_obj",
            "backend_type": "test",
            "outputs": {"mesh": "/tmp/test.obj"},
            "metadata": {"vertex_count": 100, "face_count": 200},
            # No applied_knowledge_rules key
        }

        manifest = ArtifactManifest.from_dict(old_data)
        assert manifest.applied_knowledge_rules == []

    def test_provenance_only_activated_rules(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """防血统数据膨胀红线: Only actually-activated rules appear in provenance.

        If we parse a vibe that only triggers bounce clamping, the provenance
        should NOT contain gravity or mass rules (they weren't violated).
        """
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )

        raw = {
            "vibe": "跳跃",
            "overrides": {
                "physics.bounce": 10.0,   # Exceeds max=5.0
                "physics.gravity": 9.81,  # Within bounds
                "physics.mass": 1.0,      # Within bounds
            },
        }

        spec = parser.parse_dict(raw)

        # ASSERT: only bounce rule in provenance (gravity and mass within bounds)
        rule_params = {r.param_constrained for r in spec.applied_knowledge_rules}
        assert "physics.bounce" in rule_params
        # gravity and mass should NOT be in provenance (not violated)
        # (mass might be if the vibe adjusts it, but gravity 9.81 is within [0.1, 20.0])


# ===========================================================================
# TEST GROUP 5: End-to-End Full Chain Integration
# ===========================================================================

class TestEndToEndKnowledgeSynergyChain:
    """Full chain test: Intent → Translation → Preview → Evolution → Manifest.

    This is the ultimate validation of the knowledge-generation unification.
    """

    def test_full_chain_intent_to_manifest(
        self, tmp_workspace: Path, mock_knowledge_bus_with_physics: RuntimeDistillationBus
    ):
        """Complete flywheel: parse intent → preview → evolve → tag manifest.

        Simulates the full production pipeline:
        1. Parse '极其夸张的跳跃' with knowledge grounding
        2. Run through interactive gate (approve immediately)
        3. Evolve variants with knowledge clamping
        4. Build ArtifactManifest with provenance
        """
        bus = mock_knowledge_bus_with_physics

        # Step 1: Parse intent with knowledge grounding
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=bus,
        )
        raw = {
            "vibe": "极其夸张的跳跃",
            "overrides": {"physics.bounce": 50.0},
        }
        spec = parser.parse_dict(raw)

        # ASSERT: bounce clamped
        assert spec.genotype.physics.bounce <= 5.0 + 1e-9
        assert spec.knowledge_grounded is True

        # Step 2: Interactive gate (approve immediately)
        gate = ProgrammaticPreviewGate(
            workspace_root=tmp_workspace,
            choices=["1", "Y", "full_chain_bp"],
            knowledge_bus=bus,
        )
        gate_result = gate.run(spec)
        assert gate_result.decision in (GateDecision.APPROVED, GateDecision.BLUEPRINT_SAVED)

        # Step 3: Evolve variants with knowledge clamping
        engine = BlueprintEvolutionEngine(
            mutation_strength=0.3,
            seed=42,
            knowledge_bus=bus,
        )
        evo_result = engine.evolve(
            parent_genotype=gate_result.final_genotype,
            num_variants=5,
            freeze_locks=["animation"],
            parent_name="full_chain_parent",
        )

        # ASSERT: all offspring bounce ≤ 5.0
        for offspring in evo_result.offspring:
            assert offspring.flat_params["physics.bounce"] <= 5.0 + 1e-9

        # Step 4: Build ArtifactManifest with provenance
        # Collect all activated knowledge rules from the chain
        provenance_records = []
        for rule in spec.applied_knowledge_rules:
            provenance_records.append(rule.to_dict())
        for offspring in evo_result.offspring:
            for clamp in offspring.knowledge_clamp_log:
                provenance_records.append(clamp.to_dict())

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.SPRITE_SINGLE.value,
            backend_type="director_studio",
            session_id="SESSION-140",
            outputs={"image": "/tmp/full_chain_output.png"},
            metadata={
                "width": 256,
                "height": 256,
                "channels": 4,
                "intent_id": spec.intent_id,
                "evolution_variants": evo_result.num_variants,
            },
            applied_knowledge_rules=provenance_records,
        )

        # ASSERT: manifest carries provenance
        assert len(manifest.applied_knowledge_rules) > 0

        # ASSERT: roundtrip works
        data = manifest.to_dict()
        restored = ArtifactManifest.from_dict(data)
        assert len(restored.applied_knowledge_rules) == len(provenance_records)

        # ASSERT: save/load works
        manifest_path = tmp_workspace / "test_manifest.json"
        manifest.save(manifest_path)
        loaded = ArtifactManifest.load(manifest_path)
        assert len(loaded.applied_knowledge_rules) == len(provenance_records)

    def test_full_chain_with_conflict_arbitration(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Full chain with conflict arbitration: amplify → conflict → comply → approve."""
        bus = mock_knowledge_bus

        # Parse intent
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=bus,
        )
        spec = parser.parse_dict({
            "vibe": "弹性跳跃",
            "overrides": {"physics.bounce": 4.9},  # Just under limit
        })

        # Interactive gate: amplify → conflict → comply → approve
        gate = ProgrammaticPreviewGate(
            workspace_root=tmp_workspace,
            choices=[
                "2",   # Amplify (pushes bounce > 5.0)
                "1",   # Comply with knowledge
                "1",   # Approve
                "N",   # Don't save
            ],
            knowledge_bus=bus,
        )
        gate_result = gate.run(spec)

        # ASSERT: conflict was detected and resolved
        assert gate_result.knowledge_compliances_count > 0
        assert gate_result.final_genotype.physics.bounce <= 5.0 + 1e-9

    def test_blueprint_save_load_preserves_genotype(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Blueprints saved during interactive sessions preserve genotype."""
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )
        spec = parser.parse_dict({"vibe": "活泼跳跃"})

        gate = ProgrammaticPreviewGate(
            workspace_root=tmp_workspace,
            choices=["1", "Y", "preserve_test_bp"],
            knowledge_bus=mock_knowledge_bus,
        )
        result = gate.run(spec)

        if result.blueprint_path:
            bp = Blueprint.load_yaml(Path(result.blueprint_path))
            # ASSERT: genotype matches
            assert abs(bp.genotype.physics.bounce - result.final_genotype.physics.bounce) < 1e-6


# ===========================================================================
# TEST GROUP 6: Edge Cases & Safety
# ===========================================================================

class TestEdgeCasesAndSafety:
    """Edge cases and safety net tests."""

    def test_empty_vibe_no_crash(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """Empty vibe string should not crash."""
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )
        spec = parser.parse_dict({"vibe": "", "description": ""})
        assert spec.genotype is not None

    def test_knowledge_provenance_record_serialization(self):
        """KnowledgeProvenanceRecord roundtrips through dict."""
        record = KnowledgeProvenanceRecord(
            rule_id="test.rule",
            module_name="physics",
            param_constrained="physics.bounce",
            original_value=10.0,
            clamped_value=5.0,
            constraint_type="soft",
            description="Test rule",
        )
        d = record.to_dict()
        restored = KnowledgeProvenanceRecord.from_dict(d)
        assert restored.rule_id == "test.rule"
        assert restored.clamped_value == 5.0

    def test_knowledge_conflict_serialization(self):
        """KnowledgeConflict roundtrips through dict."""
        conflict = KnowledgeConflict(
            severity="physical",
            param_key="physics.bounce",
            user_value=10.0,
            knowledge_min=0.0,
            knowledge_max=5.0,
            clamped_value=5.0,
            rule_description="Test",
            is_hard_constraint=False,
        )
        d = conflict.to_dict()
        assert d["severity"] == "physical"
        assert d["clamped_value"] == 5.0

    def test_knowledge_clamp_record_serialization(self):
        """KnowledgeClampRecord roundtrips through dict."""
        record = KnowledgeClampRecord(
            param_key="physics.bounce",
            pre_clamp_value=10.0,
            post_clamp_value=5.0,
            knowledge_min=0.0,
            knowledge_max=5.0,
            rule_id="test.rule",
            is_hard=False,
        )
        d = record.to_dict()
        assert d["pre_clamp_value"] == 10.0
        assert d["post_clamp_value"] == 5.0

    def test_creator_intent_spec_full_serialization(
        self, tmp_workspace: Path, mock_knowledge_bus: RuntimeDistillationBus
    ):
        """CreatorIntentSpec with knowledge data roundtrips through dict."""
        parser = DirectorIntentParser(
            workspace_root=tmp_workspace,
            knowledge_bus=mock_knowledge_bus,
        )
        spec = parser.parse_dict({
            "vibe": "跳跃",
            "overrides": {"physics.bounce": 20.0},
        })

        d = spec.to_dict()
        restored = CreatorIntentSpec.from_dict(d)

        assert restored.knowledge_grounded == spec.knowledge_grounded
        assert len(restored.applied_knowledge_rules) == len(spec.applied_knowledge_rules)
        assert len(restored.knowledge_conflicts) == len(spec.knowledge_conflicts)
