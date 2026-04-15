"""Tests for the Self-Evolution Engine modules."""
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


# ── Inner Loop Tests ──

@pytest.mark.unit
class TestInnerLoopRunner:
    def test_run_returns_result(self):
        from mathart.evolution.inner_loop import InnerLoopRunner
        from mathart.distill.compiler import ParameterSpace, Constraint

        runner = InnerLoopRunner(max_iterations=5, population_size=5, verbose=False)
        space = ParameterSpace(name="test")
        space.add_constraint(Constraint(
            param_name="brightness", min_value=0.0, max_value=1.0, default_value=0.5
        ))

        def generator(params):
            val = int(params.get("brightness", 0.5) * 255)
            arr = np.full((16, 16, 4), [val, val, val, 255], dtype=np.uint8)
            return Image.fromarray(arr, mode="RGBA")

        result = runner.run(generator, space)
        assert result is not None
        assert 0.0 <= result.best_score <= 1.0
        assert result.iterations > 0
        assert isinstance(result.best_params, dict)

    def test_convergence_flag(self):
        from mathart.evolution.inner_loop import InnerLoopRunner
        from mathart.distill.compiler import ParameterSpace, Constraint

        # Very low threshold should converge quickly
        runner = InnerLoopRunner(
            quality_threshold=0.01,  # Almost any image passes
            max_iterations=20,
            population_size=5,
            verbose=False,
        )
        space = ParameterSpace(name="test")
        space.add_constraint(Constraint(param_name="x", min_value=0.0, max_value=1.0))

        def generator(params):
            arr = np.full((8, 8, 4), [200, 200, 200, 255], dtype=np.uint8)
            return Image.fromarray(arr, mode="RGBA")

        result = runner.run(generator, space)
        assert result.converged  # Should converge with threshold=0.01

    def test_history_length(self):
        from mathart.evolution.inner_loop import InnerLoopRunner
        from mathart.distill.compiler import ParameterSpace, Constraint

        n_iters = 8
        runner = InnerLoopRunner(
            quality_threshold=0.99,  # Never converge
            max_iterations=n_iters,
            population_size=4,
            verbose=False,
        )
        space = ParameterSpace(name="test")
        space.add_constraint(Constraint(param_name="x", min_value=0.0, max_value=1.0))

        def generator(params):
            arr = np.zeros((8, 8, 4), dtype=np.uint8)
            arr[:, :, 3] = 255
            return Image.fromarray(arr, mode="RGBA")

        result = runner.run(generator, space)
        assert len(result.history) == n_iters

    def test_result_summary(self):
        from mathart.evolution.inner_loop import InnerLoopRunner
        from mathart.distill.compiler import ParameterSpace, Constraint

        runner = InnerLoopRunner(max_iterations=3, population_size=3, verbose=False)
        space = ParameterSpace(name="test")
        space.add_constraint(Constraint(param_name="x", min_value=0.0, max_value=1.0))

        def generator(params):
            arr = np.full((8, 8, 4), [128, 128, 128, 255], dtype=np.uint8)
            return Image.fromarray(arr, mode="RGBA")

        result = runner.run(generator, space)
        summary = result.summary()
        assert "InnerLoop" in summary
        assert "score=" in summary

    def test_mid_generation_checkpoint_is_invoked(self, tmp_path):
        from mathart.evolution.inner_loop import InnerLoopRunner
        from mathart.distill.compiler import ParameterSpace, Constraint
        from mathart.quality.checkpoint import CheckpointStage

        runner = InnerLoopRunner(
            max_iterations=3,
            population_size=3,
            verbose=False,
            project_root=tmp_path,
        )
        space = ParameterSpace(name="test")
        space.add_constraint(Constraint(param_name="x", min_value=0.0, max_value=1.0))

        def generator(params, progress_callback=None):
            preview = Image.new("RGBA", (8, 8), (40, 40, 40, 255))
            if progress_callback:
                progress_callback(preview, 1, 2)
            final = Image.new("RGBA", (8, 8), (200, 200, 200, 255))
            if progress_callback:
                progress_callback(final, 2, 2)
            return final

        result = runner.run(generator, space)
        assert any(cp.stage == CheckpointStage.MID_GENERATION for cp in result.checkpoint_log)


# ── Outer Loop Tests ──

@pytest.mark.unit
class TestOuterLoopDistiller:
    def test_distill_text_no_llm(self, tmp_path):
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
        assert result.rules_extracted >= 0  # May be 0 for short text

    def test_distill_creates_knowledge_files(self, tmp_path):
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        text = "spring_k = 20.0\ndamping_c = 5.0\nmass = 1.5"
        distiller.distill_text(text, source_name="physics_test")

        # DISTILL_LOG.md should be created
        log_path = tmp_path / "DISTILL_LOG.md"
        assert log_path.exists()

    def test_distill_log_appends(self, tmp_path):
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        distiller.distill_text("param_a = 1.0", source_name="source1")
        distiller.distill_text("param_b = 2.0", source_name="source2")

        log_path = tmp_path / "DISTILL_LOG.md"
        content = log_path.read_text(encoding="utf-8")
        assert "source1" in content
        assert "source2" in content

    def test_session_id_increments(self, tmp_path):
        from mathart.evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )

        result1 = distiller.distill_text("x = 1", source_name="s1")
        result2 = distiller.distill_text("y = 2", source_name="s2")

        # Session IDs should be different
        assert result1.session_id != result2.session_id

    def test_distill_file_markdown(self, tmp_path):
        from mathart.evolution.outer_loop import OuterLoopDistiller

        # Create a test markdown file
        md_file = tmp_path / "test_knowledge.md"
        md_file.write_text(
            "# Physics\nspring_k = 15.0\ndamping = 4.0\n",
            encoding="utf-8"
        )

        distiller = OuterLoopDistiller(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
        )
        result = distiller.distill_file(md_file)
        assert result.source_name == "test_knowledge"

    def test_chunk_text(self):
        from mathart.evolution.outer_loop import OuterLoopDistiller

        text = "paragraph\n\n" * 100  # Long text
        chunks = OuterLoopDistiller._chunk_text(text, max_chars=200)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 300  # Allow some overflow at paragraph boundaries


# ── Math Registry Tests ──

@pytest.mark.unit
class TestMathModelRegistry:
    def test_registry_has_builtin_models(self):
        from mathart.evolution.math_registry import MathModelRegistry
        registry = MathModelRegistry()
        models = registry.list_all()
        assert len(models) > 0

    def test_get_model_by_name(self):
        from mathart.evolution.math_registry import MathModelRegistry
        registry = MathModelRegistry()
        model = registry.get("oklab_palette_generator")
        assert model is not None
        assert model.name == "oklab_palette_generator"

    def test_find_by_capability(self):
        from mathart.evolution.math_registry import MathModelRegistry, ModelCapability
        registry = MathModelRegistry()
        color_models = registry.find_by_capability(ModelCapability.COLOR_PALETTE)
        assert len(color_models) > 0
        for m in color_models:
            assert ModelCapability.COLOR_PALETTE in m.capabilities

    def test_register_new_model(self):
        from mathart.evolution.math_registry import MathModelRegistry, ModelEntry, ModelCapability
        registry = MathModelRegistry()
        new_model = ModelEntry(
            name="test_model",
            version="1.0.0",
            description="Test model",
            capabilities=[ModelCapability.TEXTURE],
        )
        registry.register(new_model)
        assert registry.get("test_model") is not None

    def test_save_and_load(self, tmp_path):
        from mathart.evolution.math_registry import MathModelRegistry
        registry = MathModelRegistry()
        filepath = tmp_path / "registry.json"
        registry.save(filepath)
        assert filepath.exists()

        loaded = MathModelRegistry.load(filepath)
        assert len(loaded.list_all()) == len(registry.list_all())

    def test_summary_table_is_markdown(self):
        from mathart.evolution.math_registry import MathModelRegistry
        registry = MathModelRegistry()
        table = registry.summary_table()
        assert "|" in table
        assert "---" in table

    def test_find_by_status(self):
        from mathart.evolution.math_registry import MathModelRegistry
        registry = MathModelRegistry()
        stable = registry.find_by_status("stable")
        experimental = registry.find_by_status("experimental")
        assert len(stable) > 0
        for m in stable:
            assert m.status == "stable"


# ── Self-Evolution Engine Tests ──

@pytest.mark.unit
class TestSelfEvolutionEngine:
    def test_engine_initializes(self, tmp_path):
        from mathart.evolution.engine import SelfEvolutionEngine
        engine = SelfEvolutionEngine(project_root=tmp_path, verbose=False)
        assert engine.inner_loop is not None
        assert engine.outer_loop is not None
        assert engine.math_registry is not None

    def test_status_returns_string(self, tmp_path):
        from mathart.evolution.engine import SelfEvolutionEngine
        engine = SelfEvolutionEngine(project_root=tmp_path, verbose=False)
        status = engine.status()
        assert isinstance(status, str)
        assert "MarioTrickster" in status

    def test_capability_gap_report(self, tmp_path):
        from mathart.evolution.engine import SelfEvolutionEngine
        engine = SelfEvolutionEngine(project_root=tmp_path, verbose=False)
        report = engine.capability_gap_report()
        assert "covered" in report
        assert "missing" in report
        assert "experimental" in report
        assert isinstance(report["covered"], list)

    def test_save_registry(self, tmp_path):
        from mathart.evolution.engine import SelfEvolutionEngine
        engine = SelfEvolutionEngine(project_root=tmp_path, verbose=False)
        filepath = engine.save_registry()
        assert filepath.exists()
        content = json.loads(filepath.read_text())
        assert len(content) > 0


@pytest.mark.unit
class TestEvolutionCLI:
    def test_run_command_saves_image_and_metadata(self, tmp_path, monkeypatch, capsys):
        from mathart.evolution.cli import main

        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
        (tmp_path / "knowledge").mkdir()
        monkeypatch.chdir(tmp_path)

        out_path = tmp_path / "output" / "textures" / "run_cli.png"
        main([
            "run",
            "--preset", "terrain",
            "--iterations", "2",
            "--population", "4",
            "--size", "16",
            "--seed", "7",
            "--output", str(out_path),
        ])

        captured = capsys.readouterr()
        assert "Saved best image" in captured.out
        assert out_path.exists()
        meta_path = out_path.with_suffix(".json")
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["preset"] == "terrain"
        assert meta["iterations"] > 0
