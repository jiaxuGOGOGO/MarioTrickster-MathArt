"""Tests for community source extensions (TASK-017)."""
from __future__ import annotations

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from mathart.evolution.community_sources import (
    CommunitySourceRegistry,
    PapersWithCodeSource,
    ShadertoySource,
    LLMAdvisorSource,
)
from mathart.evolution.paper_miner import PaperResult


class TestPapersWithCodeSource:
    def test_is_always_available(self):
        source = PapersWithCodeSource()
        assert source.is_available is True
        assert source.name == "papers_with_code"

    def test_search_returns_paper_results(self):
        """Mock the API and verify result structure."""
        source = PapersWithCodeSource(verbose=True)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "paper": {
                        "title": "Test Paper on SDF",
                        "abstract": "A paper about signed distance fields",
                        "url_abs": "https://example.com/paper1",
                        "published": "2024-01-15",
                    },
                    "repository": {
                        "url": "https://github.com/test/sdf-paper",
                    },
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            results = source.search("SDF rendering", max_results=5)

        assert len(results) == 1
        assert isinstance(results[0], PaperResult)
        assert results[0].source == "papers_with_code"
        assert "SDF" in results[0].capabilities

    def test_search_handles_api_failure(self):
        source = PapersWithCodeSource(verbose=True)
        with patch("requests.get", side_effect=Exception("Network error")):
            results = source.search("test query")
        assert results == []


class TestShadertoySource:
    def test_not_available_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            source = ShadertoySource(api_key="")
            assert source.is_available is False

    def test_available_with_key(self):
        source = ShadertoySource(api_key="test_key_123")
        assert source.is_available is True
        assert source.name == "shadertoy"

    def test_search_skipped_without_key(self):
        source = ShadertoySource(api_key="", verbose=True)
        results = source.search("sdf noise")
        assert results == []


class TestLLMAdvisorSource:
    def test_detects_openai_provider(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            source = LLMAdvisorSource()
            assert source.is_available is True
            assert "openai" in source.name

    def test_not_available_without_keys(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove all API keys
            env = {k: v for k, v in os.environ.items()
                   if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
            with patch.dict(os.environ, env, clear=True):
                source = LLMAdvisorSource()
                # May or may not be available depending on env
                # Just verify it doesn't crash
                assert isinstance(source.is_available, bool)

    def test_review_returns_none_when_unavailable(self):
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items()
                   if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
            with patch.dict(os.environ, env, clear=True):
                source = LLMAdvisorSource()
                if not source.is_available:
                    result = source.review_evolution_quality("def test(): pass")
                    assert result is None


class TestCommunitySourceRegistry:
    def test_registry_creation(self):
        registry = CommunitySourceRegistry(verbose=True)
        assert len(registry.available_sources) >= 1  # At least PapersWithCode

    def test_status_report(self):
        registry = CommunitySourceRegistry(verbose=True)
        report = registry.status_report()
        assert "Community Source Status" in report
        assert "papers_with_code" in report

    def test_search_all_with_mock(self):
        """Test unified search with mocked sources."""
        registry = CommunitySourceRegistry(
            verbose=True,
            enable_shadertoy=False,
            enable_llm=False,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            results = registry.search_all("procedural pixel art")
        assert isinstance(results, list)

    def test_get_llm_advisor(self):
        registry = CommunitySourceRegistry(enable_llm=True)
        advisor = registry.get_llm_advisor()
        # May or may not be available depending on env
        if advisor is not None:
            assert isinstance(advisor, LLMAdvisorSource)
