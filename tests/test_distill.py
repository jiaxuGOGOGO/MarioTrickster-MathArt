"""Tests for the knowledge distillation pipeline."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mathart.distill.parser import (
    KnowledgeParser,
    KnowledgeRule,
    RuleType,
    TargetModule,
)
from mathart.distill.compiler import (
    RuleCompiler,
    ParameterSpace,
    Constraint,
)
from mathart.distill.optimizer import (
    EvolutionaryOptimizer,
    FitnessResult,
    constraint_satisfaction_fitness,
    combined_fitness,
)


# ── Parser tests ──────────────────────────────────────────────────────


class TestKnowledgeRule:
    """Tests for KnowledgeRule data class."""

    @pytest.mark.unit
    def test_rule_creation(self):
        rule = KnowledgeRule(
            id="test_001",
            description="Test rule",
            rule_type=RuleType.HARD_CONSTRAINT,
            target_module=TargetModule.ANIMATION,
            target_param="shoulder",
            constraint={"type": "range", "min": 0, "max": 180},
        )
        assert rule.id == "test_001"
        assert rule.rule_type == RuleType.HARD_CONSTRAINT

    @pytest.mark.unit
    def test_rule_serialization_roundtrip(self):
        rule = KnowledgeRule(
            id="test_002",
            description="Roundtrip test",
            rule_type=RuleType.SOFT_DEFAULT,
            target_module=TargetModule.OKLAB,
            target_param="hue_shift",
            constraint={"type": "range", "min": -30, "max": 30},
            source="Test Book",
            tags=["color", "test"],
        )
        d = rule.to_dict()
        restored = KnowledgeRule.from_dict(d)
        assert restored.id == rule.id
        assert restored.rule_type == rule.rule_type
        assert restored.constraint == rule.constraint
        assert restored.tags == rule.tags


class TestKnowledgeParser:
    """Tests for the knowledge parser."""

    @pytest.fixture
    def sample_md(self, tmp_path):
        """Create a sample knowledge Markdown file."""
        content = """# Test Knowledge

来源: Test Book / Test Author

## Joint Ranges

| 参数 | 值 | 说明 |
|------|-----|------|
| 肩关节 | 0-180 | 肩部活动范围 |
| 肘关节 | 0-145 | 肘部活动范围 |
| 髋关节 | -30-120 | 髋部活动范围 |

## Color Rules

| 参数 | 值 | 说明 |
|------|-----|------|
| 色相偏移 | -30-30 | 暖光偏移范围 |
| 饱和度 | 0.2-0.8 | 推荐饱和度范围 |

## Unity Export

| 参数 | 值 | 说明 |
|------|-----|------|
| PPU | 32 | 必须为32像素每单位 |
"""
        filepath = tmp_path / "test_knowledge.md"
        filepath.write_text(content, encoding="utf-8")
        return filepath

    @pytest.mark.unit
    def test_parse_markdown_extracts_rules(self, sample_md):
        parser = KnowledgeParser()
        rules = parser.parse_markdown(sample_md)
        assert len(rules) >= 5

    @pytest.mark.unit
    def test_parse_detects_range_constraints(self, sample_md):
        parser = KnowledgeParser()
        rules = parser.parse_markdown(sample_md)
        range_rules = [r for r in rules if r.constraint.get("type") == "range"]
        assert len(range_rules) >= 3

    @pytest.mark.unit
    def test_parse_detects_exact_constraints(self, sample_md):
        parser = KnowledgeParser()
        rules = parser.parse_markdown(sample_md)
        exact_rules = [r for r in rules if r.constraint.get("type") == "exact"]
        assert len(exact_rules) >= 1  # PPU = 32

    @pytest.mark.unit
    def test_parse_detects_modules(self, sample_md):
        parser = KnowledgeParser()
        rules = parser.parse_markdown(sample_md)
        modules = {r.target_module for r in rules}
        # "肩关节"/"肘关节"/"髋关节" match ANATOMY ("\u5173\u8282") more strongly
        assert TargetModule.ANATOMY in modules
        assert TargetModule.EXPORT in modules

    @pytest.mark.unit
    def test_parse_directory(self, tmp_path):
        """parse_directory should handle multiple files."""
        for name in ["anat.md", "color.md"]:
            (tmp_path / name).write_text(
                "## Section\n\n| 参数 | 值 | 说明 |\n|---|---|---|\n| test | 0-10 | desc |\n",
                encoding="utf-8",
            )
        parser = KnowledgeParser()
        rules = parser.parse_directory(tmp_path)
        assert len(rules) >= 2

    @pytest.mark.unit
    def test_save_and_load_rules(self, sample_md, tmp_path):
        parser = KnowledgeParser()
        rules = parser.parse_markdown(sample_md)
        out_path = tmp_path / "rules.json"
        parser.save_rules(rules, out_path)
        loaded = parser.load_rules(out_path)
        assert len(loaded) == len(rules)
        assert loaded[0].id == rules[0].id

    @pytest.mark.unit
    def test_rules_summary(self, sample_md):
        parser = KnowledgeParser()
        rules = parser.parse_markdown(sample_md)
        summary = parser.rules_summary(rules)
        assert "total_rules" in summary
        assert summary["total_rules"] == len(rules)
        assert "by_module" in summary
        assert "by_type" in summary

    @pytest.mark.unit
    def test_parse_game_design_keywords(self, tmp_path):
        """Parser should route game design keywords to GAME_DESIGN module."""
        content = """# 游戏设计

## 核心循环与机制设计

| 参数 | 值 | 说明 |
|---|---|---|
| 反馈延迟 | 100-300 | 游戏设计中的反馈循环延迟毫秒 |
"""
        filepath = tmp_path / "game_design.md"
        filepath.write_text(content, encoding="utf-8")
        parser = KnowledgeParser()
        rules = parser.parse_markdown(filepath)
        modules = {r.target_module for r in rules}
        assert TargetModule.GAME_DESIGN in modules

    @pytest.mark.unit
    def test_parse_game_feel_keywords(self, tmp_path):
        """Parser should route game feel keywords to GAME_FEEL module."""
        content = """# 游戏手感

## 输入缓冲

| 参数 | 值 | 说明 |
|---|---|---|
| 土狼时间 | 4-8 | 帧数 |
"""
        filepath = tmp_path / "game_feel.md"
        filepath.write_text(content, encoding="utf-8")
        parser = KnowledgeParser()
        rules = parser.parse_markdown(filepath)
        modules = {r.target_module for r in rules}
        assert TargetModule.GAME_FEEL in modules

    @pytest.mark.unit
    def test_parse_level_design_keywords(self, tmp_path):
        """Parser should route level design keywords to LEVEL_DESIGN module."""
        content = """# 关卡设计

## 空间语言

| 参数 | 值 | 说明 |
|---|---|---|
| 平台最小宽度 | 3-5 | 格子 |
"""
        filepath = tmp_path / "level_design.md"
        filepath.write_text(content, encoding="utf-8")
        parser = KnowledgeParser()
        rules = parser.parse_markdown(filepath)
        modules = {r.target_module for r in rules}
        assert TargetModule.LEVEL_DESIGN in modules

    @pytest.mark.unit
    def test_parse_programming_keywords(self, tmp_path):
        """Parser should route programming keywords to PROGRAMMING module."""
        content = """# 程序架构

## 状态机

| 参数 | 值 | 说明 |
|---|---|---|
| 状态数量 | 5-12 | FSM 状态数 |
"""
        filepath = tmp_path / "programming.md"
        filepath.write_text(content, encoding="utf-8")
        parser = KnowledgeParser()
        rules = parser.parse_markdown(filepath)
        modules = {r.target_module for r in rules}
        assert TargetModule.PROGRAMMING in modules

    @pytest.mark.unit
    def test_all_target_modules_have_keywords(self):
        """Every TargetModule should have keyword mappings in the parser."""
        parser = KnowledgeParser()
        for module in TargetModule:
            assert module in parser._MODULE_KEYWORDS, (
                f"TargetModule.{module.name} has no keyword mapping in parser"
            )

    @pytest.mark.unit
    def test_parse_real_knowledge_directory(self):
        """Parse the actual project knowledge directory if it exists."""
        knowledge_dir = Path("knowledge")
        if knowledge_dir.exists():
            parser = KnowledgeParser()
            rules = parser.parse_directory(knowledge_dir)
            assert len(rules) >= 0  # May be empty if no tables


# ── Compiler tests ────────────────────────────────────────────────────


class TestConstraint:
    """Tests for the Constraint class."""

    @pytest.mark.unit
    def test_range_constraint_contains(self):
        c = Constraint(param_name="test", min_value=0, max_value=100)
        assert c.contains(50)
        assert c.contains(0)
        assert c.contains(100)
        assert not c.contains(-1)
        assert not c.contains(101)

    @pytest.mark.unit
    def test_constraint_serialization(self):
        c = Constraint(
            param_name="animation.skeleton.shoulder_rom",
            min_value=0,
            max_value=180,
            default_value=90,
            is_hard=True,
        )
        d = c.to_dict()
        restored = Constraint.from_dict(d)
        assert restored.param_name == c.param_name
        assert restored.min_value == c.min_value
        assert restored.is_hard == c.is_hard


class TestParameterSpace:
    """Tests for the ParameterSpace class."""

    @pytest.mark.unit
    def test_add_constraint(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10)
        )
        assert space.dimensions == 1

    @pytest.mark.unit
    def test_merge_constraints_tightens_range(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=100)
        )
        space.add_constraint(
            Constraint(param_name="x", min_value=10, max_value=80)
        )
        # Should tighten to [10, 80]
        c = space.constraints["x"]
        assert c.min_value == 10
        assert c.max_value == 80

    @pytest.mark.unit
    def test_get_ranges(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10)
        )
        space.add_constraint(
            Constraint(param_name="y", min_value=-5, max_value=5)
        )
        ranges = space.get_ranges()
        assert ranges["x"] == (0, 10)
        assert ranges["y"] == (-5, 5)

    @pytest.mark.unit
    def test_get_defaults(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10, default_value=3)
        )
        defaults = space.get_defaults()
        assert defaults["x"] == 3

    @pytest.mark.unit
    def test_validate_valid_params(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10)
        )
        violations = space.validate({"x": 5})
        assert len(violations) == 0

    @pytest.mark.unit
    def test_validate_invalid_params(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10)
        )
        violations = space.validate({"x": 15})
        assert len(violations) == 1

    @pytest.mark.unit
    def test_space_serialization_roundtrip(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10, default_value=5)
        )
        d = space.to_dict()
        restored = ParameterSpace.from_dict(d)
        assert restored.name == space.name
        assert restored.dimensions == space.dimensions


class TestRuleCompiler:
    """Tests for the rule compiler."""

    @pytest.mark.unit
    def test_compile_range_rule(self):
        rules = [
            KnowledgeRule(
                id="test_001",
                description="Shoulder ROM",
                rule_type=RuleType.SOFT_DEFAULT,
                target_module=TargetModule.ANIMATION,
                target_param="肩关节",
                constraint={"type": "range", "min": 0, "max": 180},
            )
        ]
        compiler = RuleCompiler()
        space = compiler.compile(rules, "test")
        assert space.dimensions == 1
        ranges = space.get_ranges()
        assert "animation.skeleton.shoulder_rom" in ranges

    @pytest.mark.unit
    def test_compile_exact_rule(self):
        rules = [
            KnowledgeRule(
                id="test_002",
                description="PPU must be 32",
                rule_type=RuleType.HARD_CONSTRAINT,
                target_module=TargetModule.EXPORT,
                target_param="PPU",
                constraint={"type": "exact", "value": 32},
            )
        ]
        compiler = RuleCompiler()
        space = compiler.compile(rules, "test")
        c = space.constraints.get("export.unity.ppu")
        assert c is not None
        assert c.is_hard
        assert c.default_value == 32

    @pytest.mark.unit
    def test_compile_game_feel_rule(self):
        """Compiler should map game feel params correctly."""
        rules = [
            KnowledgeRule(
                id="gf_001",
                description="Coyote time",
                rule_type=RuleType.SOFT_DEFAULT,
                target_module=TargetModule.GAME_FEEL,
                target_param="土狼时间",
                constraint={"type": "range", "min": 4, "max": 8},
            )
        ]
        compiler = RuleCompiler()
        space = compiler.compile(rules, "game_feel")
        assert space.dimensions == 1
        ranges = space.get_ranges()
        assert "game_feel.input.coyote_frames" in ranges

    @pytest.mark.unit
    def test_compile_level_design_rule(self):
        """Compiler should map level design params correctly."""
        rules = [
            KnowledgeRule(
                id="ld_001",
                description="Platform min width",
                rule_type=RuleType.SOFT_DEFAULT,
                target_module=TargetModule.LEVEL_DESIGN,
                target_param="平台最小宽度",
                constraint={"type": "range", "min": 3, "max": 5},
            )
        ]
        compiler = RuleCompiler()
        space = compiler.compile(rules, "level_design")
        ranges = space.get_ranges()
        assert "level_design.platform.min_width" in ranges

    @pytest.mark.unit
    def test_compile_programming_rule(self):
        """Compiler should map programming params correctly."""
        rules = [
            KnowledgeRule(
                id="prog_001",
                description="FSM state count",
                rule_type=RuleType.SOFT_DEFAULT,
                target_module=TargetModule.PROGRAMMING,
                target_param="状态数量",
                constraint={"type": "range", "min": 5, "max": 12},
            )
        ]
        compiler = RuleCompiler()
        space = compiler.compile(rules, "programming")
        ranges = space.get_ranges()
        assert "programming.fsm.state_count" in ranges

    @pytest.mark.unit
    def test_compile_by_module(self):
        rules = [
            KnowledgeRule(
                id="t1", description="d1",
                rule_type=RuleType.SOFT_DEFAULT,
                target_module=TargetModule.ANIMATION,
                target_param="肩关节",
                constraint={"type": "range", "min": 0, "max": 180},
            ),
            KnowledgeRule(
                id="t2", description="d2",
                rule_type=RuleType.SOFT_DEFAULT,
                target_module=TargetModule.OKLAB,
                target_param="色相偏移",
                constraint={"type": "range", "min": -30, "max": 30},
            ),
        ]
        compiler = RuleCompiler()
        spaces = compiler.compile_by_module(rules)
        assert "animation" in spaces
        assert "oklab" in spaces

    @pytest.mark.unit
    def test_save_and_load_space(self, tmp_path):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10)
        )
        filepath = tmp_path / "space.json"
        RuleCompiler.save_space(space, filepath)
        loaded = RuleCompiler.load_space(filepath)
        assert loaded.dimensions == 1


# ── Optimizer tests ──────────────────────────────────────────────────


class TestEvolutionaryOptimizer:
    """Tests for the evolutionary optimizer."""

    @pytest.fixture
    def simple_space(self):
        space = ParameterSpace(name="test")
        space.add_constraint(
            Constraint(param_name="x", min_value=0, max_value=10, default_value=5)
        )
        space.add_constraint(
            Constraint(param_name="y", min_value=-5, max_value=5, default_value=0)
        )
        return space

    @pytest.mark.unit
    def test_optimizer_runs(self, simple_space):
        """Optimizer should complete without error."""
        def fitness(params):
            # Simple: maximize x + y
            return FitnessResult(score=params["x"] + params["y"])

        opt = EvolutionaryOptimizer(
            simple_space, population_size=10, seed=42
        )
        best = opt.run(fitness, generations=10)
        assert best.fitness > 0

    @pytest.mark.unit
    def test_optimizer_improves(self, simple_space):
        """Fitness should generally improve over generations."""
        def fitness(params):
            return FitnessResult(score=params["x"] + params["y"])

        opt = EvolutionaryOptimizer(
            simple_space, population_size=20, seed=42
        )
        best = opt.run(fitness, generations=50)
        history = opt.history
        assert len(history) > 0
        # Best fitness at end should be >= start
        assert history[-1]["best_fitness"] >= history[0]["best_fitness"]

    @pytest.mark.unit
    def test_optimizer_respects_constraints(self, simple_space):
        """All parameters should stay within bounds."""
        def fitness(params):
            return FitnessResult(score=1.0)

        opt = EvolutionaryOptimizer(
            simple_space, population_size=10, seed=42
        )
        best = opt.run(fitness, generations=20)
        assert 0 <= best.params["x"] <= 10
        assert -5 <= best.params["y"] <= 5

    @pytest.mark.unit
    def test_optimizer_early_stop(self, simple_space):
        """Optimizer should stop early when target fitness is reached."""
        def fitness(params):
            return FitnessResult(score=params["x"])

        opt = EvolutionaryOptimizer(
            simple_space, population_size=20, seed=42
        )
        best = opt.run(fitness, generations=1000, target_fitness=9.0)
        assert best.fitness >= 9.0
        # Should not have run all 1000 generations
        assert len(opt.history) < 1000

    @pytest.mark.unit
    def test_optimizer_callback(self, simple_space):
        """Callback should be called each generation."""
        call_count = [0]

        def callback(gen, best):
            call_count[0] += 1

        def fitness(params):
            return FitnessResult(score=1.0)

        opt = EvolutionaryOptimizer(
            simple_space, population_size=10, seed=42
        )
        opt.run(fitness, generations=10, callback=callback)
        assert call_count[0] == 10

    @pytest.mark.unit
    def test_constraint_satisfaction_fitness(self, simple_space):
        """constraint_satisfaction_fitness should score valid params as 1.0."""
        fn = constraint_satisfaction_fitness(simple_space)
        result = fn({"x": 5, "y": 0})
        assert result.score == 1.0

    @pytest.mark.unit
    def test_constraint_satisfaction_fitness_violation(self, simple_space):
        """Violated constraints should reduce fitness score."""
        fn = constraint_satisfaction_fitness(simple_space)
        result = fn({"x": 15, "y": 0})  # x out of range
        assert result.score < 1.0

    @pytest.mark.unit
    def test_combined_fitness(self):
        """combined_fitness should average component scores."""
        fn1 = lambda p: FitnessResult(score=0.8)
        fn2 = lambda p: FitnessResult(score=0.6)
        combined = combined_fitness(fn1, fn2)
        result = combined({"x": 1})
        assert abs(result.score - 0.7) < 0.01

    @pytest.mark.unit
    def test_combined_fitness_with_weights(self):
        """Weighted combined fitness should respect weights."""
        fn1 = lambda p: FitnessResult(score=1.0)
        fn2 = lambda p: FitnessResult(score=0.0)
        combined = combined_fitness(fn1, fn2, weights=[3.0, 1.0])
        result = combined({"x": 1})
        assert abs(result.score - 0.75) < 0.01

    @pytest.mark.unit
    def test_fitness_result_is_valid(self):
        assert FitnessResult(score=0.5).is_valid
        assert FitnessResult(score=0.0).is_valid
        assert not FitnessResult(score=-0.1).is_valid

    @pytest.mark.unit
    def test_optimizer_adaptive_history_contains_search_metrics(self, simple_space):
        def fitness(params):
            return FitnessResult(score=params["x"] + params["y"])

        opt = EvolutionaryOptimizer(
            simple_space,
            population_size=12,
            seed=42,
            strategy="adaptive_ga",
        )
        opt.run(fitness, generations=6)
        last = opt.history[-1]
        assert last["strategy"] == "adaptive_ga"
        assert "diversity" in last
        assert "mutation_rate" in last
        assert "mutation_strength" in last
        assert "local_search_count" in last

    @pytest.mark.unit
    def test_optimizer_increases_exploration_when_stagnant(self, simple_space):
        def fitness(params):
            return FitnessResult(score=1.0)

        opt = EvolutionaryOptimizer(
            simple_space,
            population_size=12,
            seed=42,
            strategy="adaptive_ga",
            mutation_rate=0.05,
            stagnation_window=2,
            immigrant_ratio=0.25,
        )
        opt.run(fitness, generations=6)
        assert any(entry["mutation_rate"] > 0.05 for entry in opt.history)
        assert any(entry["immigrant_count"] > 0 for entry in opt.history)

    @pytest.mark.unit
    def test_optimizer_supports_cma_es_like_strategy(self):
        space = ParameterSpace(name="high_dim")
        for i in range(6):
            space.add_constraint(
                Constraint(
                    param_name=f"p{i}",
                    min_value=0.0,
                    max_value=1.0,
                    default_value=0.5,
                )
            )

        target = {f"p{i}": 0.85 for i in range(6)}

        def fitness(params):
            error = sum((params[name] - value) ** 2 for name, value in target.items()) / len(target)
            return FitnessResult(score=max(0.0, 1.0 - error))

        opt = EvolutionaryOptimizer(
            space,
            population_size=24,
            seed=42,
            strategy="cma_es_like",
            local_search_ratio=0.25,
        )
        best = opt.run(fitness, generations=25)
        assert best.fitness > 0.9
        assert any(entry["local_search_count"] > 0 for entry in opt.history)
        assert all(entry["strategy"] == "cma_es_like" for entry in opt.history)
