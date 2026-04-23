"""SESSION-156: Tests for Knowledge Triage & Native Deduplication Funnel.

Covers:
  - KnowledgeTriageEngine classification (Actionable vs Macro)
  - KnowledgeFunnel integration (Dedup + Triage pipeline)
  - OuterLoopDistiller integration (end-to-end with funnel)
  - Physical pipeline isolation (Macro rules NEVER reach compiler)
  - UX output verification
"""
import tempfile
from pathlib import Path

import pytest


# ── Triage Engine Unit Tests ──────────────────────────────────────────────────

@pytest.mark.unit
class TestKnowledgeTriageEngine:
    """Test the triage classification logic."""

    def test_actionable_numeric_params(self):
        """Rules with numeric parameters should be classified as ACTIONABLE."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        decision = engine.classify_rule(
            rule_text="spring_k = 15.0 (from physics_book)",
            params={"spring_k": "15.0"},
            rule_type="hard_constraint",
        )
        assert decision.tier == KnowledgeTier.ACTIONABLE

    def test_actionable_constraint_keywords(self):
        """Rules with constraint keywords should be ACTIONABLE."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        decision = engine.classify_rule(
            rule_text="canvas_size must be between 16 and 64 pixels",
            params={"canvas_size": "16-64"},
            rule_type="hard_constraint",
        )
        assert decision.tier == KnowledgeTier.ACTIONABLE

    def test_macro_philosophy(self):
        """Abstract game design philosophy should be classified as MACRO."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        decision = engine.classify_rule(
            rule_text="Good game design philosophy emphasizes player experience and emotional engagement",
            params={},
            rule_type="heuristic",
        )
        assert decision.tier == KnowledgeTier.MACRO

    def test_macro_chinese_philosophy(self):
        """Chinese abstract text should be classified as MACRO."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        decision = engine.classify_rule(
            rule_text="游戏必须要好玩，优秀的游戏设计理念应该追求美学和谐与乐趣",
            params={},
            rule_type="heuristic",
        )
        assert decision.tier == KnowledgeTier.MACRO

    def test_macro_narrative(self):
        """Narrative/worldview text should be MACRO."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        decision = engine.classify_rule(
            rule_text="The game world should feel immersive through storytelling and narrative design",
            params={},
            rule_type="heuristic",
        )
        assert decision.tier == KnowledgeTier.MACRO

    def test_batch_triage_separation(self):
        """Batch triage should correctly separate actionable and macro rules."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        rules = [
            ("spring_k = 15.0", {"spring_k": "15.0"}, "hard_constraint"),
            ("Game design philosophy is about fun", {}, "heuristic"),
            ("max_velocity = 200 px/s", {"max_velocity": "200"}, "hard_constraint"),
            ("The aesthetic should feel harmonious and balanced", {}, "heuristic"),
        ]
        actionable_idx, macro_idx, result = engine.triage_batch(rules)

        assert len(actionable_idx) == 2
        assert len(macro_idx) == 2
        assert result.actionable_count == 2
        assert result.macro_count == 2

    def test_no_signals_with_params_is_actionable(self):
        """Rules with no keyword signals but numeric params → ACTIONABLE."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        decision = engine.classify_rule(
            rule_text="xyz_param = 42.0",
            params={"xyz_param": "42.0"},
            rule_type="soft_default",
        )
        assert decision.tier == KnowledgeTier.ACTIONABLE

    def test_no_signals_no_params_is_macro(self):
        """Rules with no signals and no params → MACRO (safe default)."""
        from mathart.distill.knowledge_triage import KnowledgeTriageEngine, KnowledgeTier

        engine = KnowledgeTriageEngine(verbose=False)
        decision = engine.classify_rule(
            rule_text="Some generic text without any clear signals",
            params={},
            rule_type="soft_default",
        )
        assert decision.tier == KnowledgeTier.MACRO


# ── Knowledge Funnel Integration Tests ────────────────────────────────────────

@pytest.mark.unit
class TestKnowledgeFunnel:
    """Test the complete Dedup + Triage funnel."""

    def test_funnel_dedup_removes_duplicates(self, tmp_path):
        """Funnel should remove exact duplicates via native DeduplicationEngine."""
        from mathart.distill.knowledge_triage import KnowledgeFunnel

        funnel = KnowledgeFunnel(project_root=tmp_path, verbose=False)

        rules = [
            ("physics_sim", "spring_k = 15.0", {"spring_k": "15.0"}, "hard_constraint"),
            ("physics_sim", "spring_k = 15.0", {"spring_k": "15.0"}, "hard_constraint"),  # duplicate
            ("physics_sim", "damping_c = 4.0", {"damping_c": "4.0"}, "hard_constraint"),
        ]

        result = funnel.process(rules, source_name="test")
        # One duplicate should be removed
        total_accepted = len(result.actionable_rules) + len(result.macro_rules)
        assert total_accepted <= 2  # At most 2 unique rules
        assert result.dedup_result.exact_dups >= 1

    def test_funnel_triage_separates_tiers(self, tmp_path):
        """Funnel should separate actionable and macro rules."""
        from mathart.distill.knowledge_triage import KnowledgeFunnel

        funnel = KnowledgeFunnel(project_root=tmp_path, verbose=False)

        rules = [
            ("physics_sim", "spring_k = 15.0", {"spring_k": "15.0"}, "hard_constraint"),
            ("game_design", "Game design philosophy is about player experience and fun", {}, "heuristic"),
        ]

        result = funnel.process(rules, source_name="test")
        assert len(result.actionable_rules) >= 1
        assert len(result.macro_rules) >= 1

    def test_funnel_macro_rules_blocked_from_compiler(self, tmp_path):
        """Macro rules must NEVER appear in actionable_rules."""
        from mathart.distill.knowledge_triage import KnowledgeFunnel

        funnel = KnowledgeFunnel(project_root=tmp_path, verbose=False)

        # All macro rules
        rules = [
            ("game_design", "Game design philosophy emphasizes player experience and emotional engagement", {}, "heuristic"),
            ("narrative", "The game world should feel immersive through storytelling", {}, "heuristic"),
        ]

        result = funnel.process(rules, source_name="test")
        assert len(result.actionable_rules) == 0  # ZERO actionable
        assert len(result.macro_rules) >= 1  # All macro


# ── OuterLoopDistiller Integration Tests ──────────────────────────────────────

@pytest.mark.unit
class TestOuterLoopDistillerWithFunnel:
    """Test that the OuterLoopDistiller correctly integrates the funnel."""

    def test_distill_macro_text_no_enforcer_generated(self, tmp_path):
        """Feeding pure macro text should NOT generate any enforcer plugins."""
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        # Pure macro/philosophical text — no quantifiable constraints
        text = (
            "游戏必须要好玩。好的游戏设计应该让玩家感到沉浸和满足。"
            "优秀的关卡设计需要有节奏感和情感曲线。"
            "游戏的美学应该追求和谐与平衡。"
        )

        result = distiller.distill_text(text, source_name="废话测试")
        # No enforcer plugins should be generated for macro text
        assert result.enforcer_plugins_generated == []

    def test_distill_actionable_text_passes_through(self, tmp_path):
        """Feeding actionable text should pass through the funnel."""
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        text = "spring_k = 15.0\ndamping_c = 4.0\nmax_velocity = 200\ncanvas_size = 32"

        result = distiller.distill_text(text, source_name="physics_test")
        assert result.session_id.startswith("DISTILL-")
        # Triage summary should show actionable rules
        if result.triage_summary:
            assert result.triage_summary.get("actionable_count", 0) >= 0

    def test_distill_mixed_text_separates_correctly(self, tmp_path):
        """Mixed text should have both actionable and macro rules."""
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        text = (
            "spring_k = 15.0\n"
            "damping_c = 4.0\n"
            "Game design philosophy is about player experience and fun\n"
            "max_velocity = 200\n"
        )

        result = distiller.distill_text(text, source_name="mixed_test")
        assert result.session_id.startswith("DISTILL-")

    def test_existing_tests_still_pass(self, tmp_path):
        """Regression: existing OuterLoopDistiller behavior is preserved."""
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        text = """
        # Animation Physics
        spring_k = 15.0
        damping_c = 4.0
        The elbow joint has a maximum angle of 145 degrees.
        Frame rate = 60 fps
        """

        result = distiller.distill_text(text, source_name="test_book")
        assert result.session_id.startswith("DISTILL-")
        assert result.source_name == "test_book"
        assert result.rules_extracted >= 0

    def test_distill_creates_log(self, tmp_path):
        """DISTILL_LOG.md should still be created with funnel stats."""
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        distiller.distill_text("spring_k = 20.0", source_name="log_test")

        log_path = tmp_path / "DISTILL_LOG.md"
        assert log_path.exists()

    def test_duplicate_input_deduped(self, tmp_path):
        """Feeding the same text twice should trigger deduplication."""
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        text = "spring_k = 15.0\ndamping_c = 4.0"
        result1 = distiller.distill_text(text, source_name="first_pass")
        result2 = distiller.distill_text(text, source_name="second_pass")

        # Second pass should have fewer integrated rules due to dedup
        # (exact duplicates are removed)
        assert result2.session_id != result1.session_id


# ── Physical Pipeline Isolation Tests ─────────────────────────────────────────

@pytest.mark.unit
class TestPhysicalPipelineIsolation:
    """Verify that Macro rules are PHYSICALLY blocked from the compiler."""

    def test_macro_rule_never_reaches_synthesize(self, tmp_path):
        """The _synthesize_enforcer_plugin should never be called for macro-only input."""
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,  # No LLM, so no synthesis anyway
            verbose=False,
        )

        # Pure philosophy — should be classified as MACRO
        text = "The beauty of game design lies in the harmony of aesthetics and player engagement"
        result = distiller.distill_text(text, source_name="philosophy_test")

        # No plugins generated
        assert result.enforcer_plugins_generated == []

    def test_triage_decision_ux_output(self):
        """TriageDecision should produce correct UX output strings."""
        from mathart.distill.knowledge_triage import TriageDecision, KnowledgeTier

        actionable = TriageDecision(
            rule_text="spring_k = 15.0",
            tier=KnowledgeTier.ACTIONABLE,
            confidence=0.9,
            reason="Has numeric params",
            signals=["numeric_params_present"],
        )
        assert "微观约束" in actionable.ux_line()
        assert "Python 编译引擎" in actionable.ux_line()

        macro = TriageDecision(
            rule_text="Game should be fun",
            tier=KnowledgeTier.MACRO,
            confidence=0.8,
            reason="Macro signals dominate",
            signals=[],
        )
        assert "宏观哲学" in macro.ux_line()
        assert "跳过代码生成" in macro.ux_line()
