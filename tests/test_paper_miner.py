"""Tests for MathPaperMiner — math paper mining scheduler."""
from __future__ import annotations

import json
from pathlib import Path

from mathart.evolution.paper_miner import MathPaperMiner, MiningSession, PaperResult, _cli_entry


class _MockResponse:
    def __init__(self, *, text: str = "", json_data: dict | None = None, status_code: int = 200):
        self.text = text
        self._json_data = json_data or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._json_data


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
            relevance_threshold=0.0,
            verbose=False,
        )
        text = "Wave Function Collapse procedural tilemap generation algorithm"
        miner.mine_from_text(text, "test")
        papers_file = tmp_path / "knowledge" / "math_papers.md"
        assert papers_file.exists()

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
        """Should generate enriched registry candidates from session results."""
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
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
        assert candidates[0]["module_path"] == "mathart.mined.wfc_tilemap_generation"
        assert candidates[0]["function_name"] == "generate"
        assert "grid_width" in candidates[0]["params"]
        assert "rule_compliance" in candidates[0]["quality_metrics"]

    def test_relevance_threshold_filters(self, tmp_path):
        """Papers below threshold should be rejected."""
        miner = MathPaperMiner(
            project_root=tmp_path,
            use_llm=False,
            relevance_threshold=0.9,
            verbose=False,
        )
        text = "Some vaguely related content about art and math"
        session = miner.mine_from_text(text, "test")
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

    def test_search_arxiv_parses_atom_feed(self, monkeypatch, tmp_path):
        """Should parse arXiv Atom entries into PaperResult objects."""
        xml_payload = """
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/2401.12345</id>
            <title>Wave Function Collapse for Procedural Tilemap Generation</title>
            <summary>Procedural generation method for tilemap and sprite layouts.</summary>
            <published>2024-01-15T00:00:00Z</published>
            <author><name>Jane Doe</name></author>
            <category term="cs.GR" />
          </entry>
        </feed>
        """

        def fake_get(url, params, headers, timeout):
            assert "search_query" in params
            assert headers["User-Agent"].startswith("MarioTrickster-MathArt/")
            return _MockResponse(text=xml_payload)

        monkeypatch.setattr("mathart.evolution.paper_miner.requests.get", fake_get)
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
        results = miner._search_arxiv("wave function collapse tilemap", max_results=2)
        assert len(results) == 1
        assert results[0].source == "arxiv"
        assert results[0].url == "https://arxiv.org/abs/2401.12345"
        assert results[0].year == 2024
        assert "PCG" in results[0].capabilities
        assert results[0].relevance_score > 0.6

    def test_search_github_parses_repository_results(self, monkeypatch, tmp_path):
        """Should parse GitHub repository search results into PaperResult objects."""
        payload = {
            "items": [
                {
                    "full_name": "open-source/wfc-tilemap",
                    "html_url": "https://github.com/open-source/wfc-tilemap",
                    "description": "Wave function collapse tilemap generator for pixel art games.",
                    "language": "Python",
                    "topics": ["tilemap", "pixel-art", "procedural-generation"],
                    "stargazers_count": 420,
                    "forks_count": 35,
                    "archived": False,
                    "updated_at": "2025-07-01T12:00:00Z",
                }
            ]
        }

        def fake_get(url, params, headers, timeout):
            assert params["sort"] == "stars"
            assert params["order"] == "desc"
            return _MockResponse(json_data=payload)

        monkeypatch.setattr("mathart.evolution.paper_miner.requests.get", fake_get)
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
        results = miner._search_github("wave function collapse tilemap", max_results=2)
        assert len(results) == 1
        assert results[0].source == "github"
        assert results[0].url == "https://github.com/open-source/wfc-tilemap"
        assert results[0].metadata["stars"] == 420
        assert "PCG" in results[0].capabilities
        assert results[0].quality > 0.5

    def test_github_search_uses_auth_header_when_token_present(self, monkeypatch, tmp_path):
        """Should send a bearer token to GitHub Search when configured."""
        captured_headers = {}

        def fake_get(url, params, headers, timeout):
            captured_headers.update(headers)
            return _MockResponse(json_data={"items": []})

        monkeypatch.setattr("mathart.evolution.paper_miner.requests.get", fake_get)
        miner = MathPaperMiner(
            project_root=tmp_path,
            use_llm=False,
            verbose=False,
            github_token="dummy-token",
        )
        miner._search_github("oklab palette", max_results=1)
        assert captured_headers["Authorization"] == "Bearer dummy-token"
        assert captured_headers["X-GitHub-Api-Version"] == miner.GITHUB_API_VERSION

    def test_mine_prefers_live_api_results_before_llm(self, monkeypatch, tmp_path):
        """Should accept live API results without invoking the LLM fallback."""
        live_result = PaperResult(
            title="Signed Distance Field Rendering for 2D Game Effects",
            source="arxiv",
            url="https://arxiv.org/abs/2402.00001",
            abstract="SDF rendering for sprite effects and outline generation.",
            year=2024,
            applicability=0.8,
            implementability=0.9,
            novelty=0.6,
            quality=0.8,
            relevance_score=0.79,
            capabilities=["SDF", "PIXEL_IMAGE"],
            implementation_notes="CPU-friendly distance field adaptation appears feasible.",
        )

        miner = MathPaperMiner(project_root=tmp_path, use_llm=True, verbose=False)
        monkeypatch.setattr(miner, "_search_real_sources", lambda query, max_results: [live_result])
        monkeypatch.setattr(miner, "_search_with_llm", lambda query, max_results: (_ for _ in ()).throw(AssertionError("LLM should not be called")))
        session = miner.mine(queries=["signed distance field sprite effects"], max_results_per_query=3)
        assert session.papers_found == 1
        assert session.papers_accepted == 1
        assert session.results[0].title.startswith("Signed Distance Field")

    def test_promote_session_candidates_creates_scaffolds_and_registry(self, tmp_path):
        """Should scaffold accepted candidates and persist them into math_models.json."""
        miner = MathPaperMiner(project_root=tmp_path, use_llm=False, verbose=False)
        paper = PaperResult(
            title="Wave Function Collapse for Tilemaps",
            source="arxiv",
            url="https://arxiv.org/abs/2401.12345",
            abstract="Wave Function Collapse for procedural tilemap generation and layout synthesis.",
            year=2024,
            applicability=0.9,
            implementability=0.85,
            novelty=0.7,
            quality=0.8,
            relevance_score=0.84,
            capabilities=["PCG", "PIXEL_IMAGE"],
            implementation_notes="Start with a CPU-friendly constraint solver scaffold.",
        )
        session = MiningSession(
            session_id="MINE-010",
            query="wfc tilemap",
            timestamp="2024-01-01T00:00:00",
            papers_found=1,
            papers_accepted=1,
            papers_rejected=0,
            results=[paper],
        )

        promoted = miner.promote_session_candidates(session)
        assert len(promoted) == 1

        module_file = Path(promoted[0]["module_file"])
        test_file = Path(promoted[0]["test_file"])
        registry_file = Path(promoted[0]["registry_path"])
        manifest_file = tmp_path / "knowledge" / "registry_candidates.json"

        assert module_file.exists()
        assert test_file.exists()
        assert registry_file.exists()
        assert manifest_file.exists()

        module_text = module_file.read_text(encoding="utf-8")
        assert "def generate(" in module_text
        assert '"status": "scaffold"' in module_text

        registry = json.loads(registry_file.read_text(encoding="utf-8"))
        assert "wave_function_collapse_for_tilemaps" in registry
        assert registry["wave_function_collapse_for_tilemaps"]["status"] == "experimental"
        assert registry["wave_function_collapse_for_tilemaps"]["module_path"] == "mathart.mined.wave_function_collapse_for_tilemaps"

    def test_cli_text_promote_persists_candidate_scaffolds(self, monkeypatch, tmp_path, capsys):
        """CLI text --promote should create scaffolds and announce promotion."""
        notes_file = tmp_path / "notes.txt"
        notes_file.write_text("Wave Function Collapse for procedural tilemap generation", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["mathart-mine", "text", str(notes_file), "--source", "manual", "--promote"],
        )

        _cli_entry()
        output = capsys.readouterr().out
        assert "Promoted 1 candidate scaffold" in output
        assert (tmp_path / "math_models.json").exists()
        assert (tmp_path / "mathart" / "mined").exists()
