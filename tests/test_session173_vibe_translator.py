"""SESSION-173: Unit tests for offline vibe translation and prompt armor.

Verifies:
1. Exact-match Chinese vibe phrases translate correctly.
2. Token-level fallback works for compound vibes.
3. Unknown vibes pass through unmodified (Graceful Fallback).
4. Empty/None vibes produce clean prompts without residual commas.
5. Already-English vibes pass through unchanged.
6. SESSION-172 base prompt armor is preserved.
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mathart.backend.ai_render_stream_backend import (
    _translate_vibe,
    _armor_prompt,
    _BASE_POSITIVE_PROMPT,
    VIBE_TRANSLATION_MAP,
)


class TestTranslateVibe:
    """Tests for _translate_vibe()."""

    def test_exact_match_composite(self):
        result = _translate_vibe("活泼的跳跃")
        assert result == VIBE_TRANSLATION_MAP["活泼的跳跃"]
        assert "lively jumping" in result

    def test_exact_match_single(self):
        result = _translate_vibe("受击")
        assert "getting hit" in result

    def test_token_level_fallback(self):
        """Compound vibe not in dict should be split and translated per-token."""
        result = _translate_vibe("活泼,夸张")
        assert "lively" in result
        assert "exaggerated" in result

    def test_token_level_with_chinese_delimiter(self):
        result = _translate_vibe("活泼，弹性")
        assert "lively" in result
        assert "bouncy" in result

    def test_unknown_vibe_passthrough(self):
        """Unknown Chinese should pass through unmodified."""
        result = _translate_vibe("超级无敌旋风腿")
        assert result == "超级无敌旋风腿"

    def test_english_passthrough(self):
        """Already-English vibes should pass through (tokens may be comma-joined)."""
        result = _translate_vibe("dynamic jumping")
        assert "dynamic" in result
        assert "jumping" in result

    def test_empty_string(self):
        assert _translate_vibe("") == ""

    def test_none_like_empty(self):
        assert _translate_vibe("   ") == ""

    def test_mixed_known_unknown(self):
        """Mix of known and unknown tokens."""
        result = _translate_vibe("活泼,未知词汇")
        assert "lively" in result
        assert "未知词汇" in result


class TestArmorPrompt:
    """Tests for _armor_prompt() with SESSION-173 translation."""

    def test_chinese_vibe_translated_and_armored(self):
        result = _armor_prompt("活泼的跳跃")
        assert result.startswith(_BASE_POSITIVE_PROMPT)
        assert "lively jumping" in result
        # Must NOT contain Chinese
        assert "活泼" not in result

    def test_empty_vibe_no_residual_commas(self):
        result = _armor_prompt("")
        assert result == _BASE_POSITIVE_PROMPT
        assert not result.endswith(", ")
        assert ", ," not in result

    def test_english_vibe_preserved(self):
        result = _armor_prompt("dynamic jumping pose")
        assert "dynamic" in result
        assert "jumping" in result
        assert "pose" in result
        assert result.startswith(_BASE_POSITIVE_PROMPT)

    def test_session172_base_prompt_intact(self):
        """SESSION-172 base prompt must be preserved."""
        result = _armor_prompt("受击")
        assert "masterpiece" in result
        assert "best quality" in result
        assert "3d game character asset" in result


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
