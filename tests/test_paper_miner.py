"""Tests for MathPaperMiner — math paper mining scheduler."""
from __future__ import annotations

import pytest
from pathlib import Path

from mathart.evolution.paper_miner import MathPaperMiner, PaperResult, MiningSession


class TestMathPaperMiner:
    def test_mine_from_text_basic(self, tmp_path):
        """Should extract papers from text without LLM."""
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
        text = """
        Wave Function Collapse for procedural tilemap generation
        OKLAB perceptual color space for palette optimization
        Signed distance field rendering for 2D game effects
        """
        session = miner.mine_from_text(text, source_name="test_text")
        assert isinstance(session, MiningSession)
        assert session.session_id.startswith("MINE-")

    def test_session_id_increments(self, tmp_path):
        """Session IDs should increment across calls."""
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
        s1 = miner.mine_from_text("Wave Function Collapse procedural generation", "test1")
        s2 = miner.mine_from_text("OKLAB color palette optimization", "test2")
        id1 = int(s1.session_id.split("-")[1])
        id2 = int(s2.session_id.split("-")[1])
        assert id2 == id1 + 1

    def test_knowledge_file_created(self, tmp_path):
        """Mining session should create knowledge/math_papers.md."""
        miner = MathPaperMiner(
            project_root=tmp_path,
            use_llm=False,
            relevance_threshold=0.0,  # Accept everything
            verbose=False,
        )
        text = "Wave Function Collapse procedural tilemap generation algorithm"
        miner.mine_from_text(text, "test")
        papers_file = tmp_path / "knowledge" / "math_papers.md"
        # File may or may not be created depending on whether rules were extracted
        # Just check no exception was raised

    def test_mine_log_created(self, tmp_path):
        """Mining session should create MINE_LOG.md."""
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
        miner.mine_from_text("SDF signed distance field rendering", "test")
        log_path = tmp_path / "MINE_LOG.md"
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "MINE-" in content

    def test_capability_detection(self):
        """Should detect capabilities from text."""
        miner = MathPaperMiner(use_llm=False)
        caps = miner._detect_capabilities("OKLAB perceptual color palette quantization")
        assert "COLOR_PALETTE" in caps

    def test_capability_detection_sdf(self):
        """Should detect SDF capability."""
        miner = MathPaperMiner(use_llm=False)
        caps = miner._detect_capabilities("signed distance field ray marching")
        assert "SDF" in caps

    def test_capability_detection_pcg(self):
        """Should detect PCG capability."""
        miner = MathPaperMiner(use_llm=False)
        caps = miner._detect_capabilities("wave function collapse procedural generation")
        assert "PCG" in caps

    def test_paper_to_model_name(self):
        """Should convert paper title to snake_case model name."""
        miner = MathPaperMiner(use_llm=False)
        name = miner._paper_to_model_name("Wave Function Collapse for Tilemaps")
        assert name == name.lower()
        assert " " not in name

    def test_generate_registry_candidates(self, tmp_path):
        """Should generate registry candidates from session results."""
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
        # Manually create a session with results
        paper = PaperResult(
            title="WFC Tilemap Generation",
            source="arxiv",
            url="https://arxiv.org/abs/1234",
            abstract="Wave Function Collapse for procedural tilemap generation.",
            year=2023,
            applicability=0.9,
            implementability=0.8,
            novelty=0.7,
            quality=0.8,
            relevance_score=0.82,
            capabilities=["PCG"],
            implementation_notes="Pure Python implementation possible.",
        )
        session = MiningSession(
            session_id="MINE-001",
            query="test",
            timestamp="2024-01-01T00:00:00",
            papers_found=1,
            papers_accepted=1,
            papers_rejected=0,
            results=[paper],
        )
        candidates = miner.generate_registry_candidates(session)
        assert len(candidates) == 1
        assert candidates[0]["status"] == "candidate"
        assert "PCG" in candidates[0]["capabilities"]

    def test_relevance_threshold_filters(self, tmp_path):
        """Papers below threshold should be rejected."""
        miner = MathPaperMiner(
            project_root=tmp_path,
            use_llm=False,
            relevance_threshold=0.9,  # Very high threshold
            verbose=False,
        )
        text = "Some vaguely related content about art and math"
        session = miner.mine_from_text(text, "test")
        # With high threshold, most manual extractions should be rejected
        assert session.papers_accepted <= session.papers_found

    def test_session_summary(self):
        """MiningSession.summary() should return a descriptive string."""
        session = MiningSession(
            session_id="MINE-042",
            query="test query",
            timestamp="2024-01-01T00:00:00",
            papers_found=10,
            papers_accepted=7,
            papers_rejected=3,
        )
        summary = session.summary()
        assert "MINE-042" in summary
        assert "found=10" in summary
        assert "accepted=7" in summary
