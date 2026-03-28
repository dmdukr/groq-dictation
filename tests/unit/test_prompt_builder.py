"""Tests for src/context/prompt_builder.py — LLM prompt assembly."""

from __future__ import annotations

from src.context.prompt_builder import (
    BASE_PROMPT,
    build_llm_prompt,
    estimate_tokens,
    format_term_candidates,
    sanitize,
)

# =============================================================================
# Prompt assembly
# =============================================================================


class TestBuildLLMPrompt:
    """Tests for build_llm_prompt()."""

    def test_base_prompt_always_present(self) -> None:
        """All toggles False -> base prompt present."""
        toggles = {"punctuation": False, "grammar": False, "capitalize": False, "terminology": False}
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe")
        assert BASE_PROMPT in result

    def test_punctuation_toggle(self) -> None:
        """toggles['punctuation']=True -> punctuation instruction in prompt."""
        toggles = {"punctuation": True, "grammar": False, "capitalize": False, "terminology": False}
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe")
        assert "Add proper punctuation and sentence breaks." in result

    def test_grammar_toggle(self) -> None:
        """toggles['grammar']=True -> grammar instruction."""
        toggles = {"punctuation": False, "grammar": True, "capitalize": False, "terminology": False}
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe")
        assert "Fix grammar errors while preserving the speaker's intent." in result

    def test_capitalize_toggle(self) -> None:
        """toggles['capitalize']=True -> capitalization instruction."""
        toggles = {"punctuation": False, "grammar": False, "capitalize": True, "terminology": False}
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe")
        assert "Capitalize sentences and proper nouns appropriately." in result

    def test_terminology_toggle_with_terms(self) -> None:
        """toggles['terminology']=True + unresolved terms -> candidates block."""
        toggles = {"punctuation": False, "grammar": False, "capitalize": False, "terminology": True}
        terms: list[dict[str, object]] = [
            {
                "term": "замок",
                "candidates": [
                    {"meaning": "door_lock", "cluster": "household", "score": 0.71},
                ],
            },
        ]
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe", unresolved_terms=terms)
        assert "Resolve technical terms using the context provided." in result
        assert "[AMBIGUOUS TERMS]" in result
        assert "замок" in result

    def test_terminology_toggle_without_terms(self) -> None:
        """toggles['terminology']=True + no terms -> no candidates block."""
        toggles = {"punctuation": False, "grammar": False, "capitalize": False, "terminology": True}
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe")
        assert "Resolve technical terms" not in result
        assert "[AMBIGUOUS TERMS]" not in result

    def test_all_toggles_on(self) -> None:
        """All toggles True -> all sections present."""
        toggles = {"punctuation": True, "grammar": True, "capitalize": True, "terminology": True}
        terms: list[dict[str, object]] = [
            {
                "term": "lock",
                "candidates": [
                    {"meaning": "door_lock", "cluster": "household", "score": 0.71},
                ],
            },
        ]
        result = build_llm_prompt(
            "hello",
            toggles,
            app_script=None,
            app_name="notepad.exe",
            unresolved_terms=terms,
        )
        assert "punctuation" in result.lower()
        assert "grammar" in result.lower()
        assert "Capitalize" in result
        assert "Resolve technical terms" in result
        assert "[AMBIGUOUS TERMS]" in result


# =============================================================================
# Script inclusion
# =============================================================================


class TestScriptInclusion:
    """Tests for app_script handling in build_llm_prompt()."""

    def test_script_delimiter_wrapped(self) -> None:
        """Script body wrapped in [FORMATTING RULES] delimiters."""
        toggles: dict[str, bool] = {}
        result = build_llm_prompt("hello", toggles, app_script="Use sentence case.", app_name="notepad.exe")
        assert "[FORMATTING RULES]" in result
        assert "Use sentence case." in result
        assert "[/FORMATTING RULES]" in result

    def test_no_script_no_block(self) -> None:
        """app_script=None -> no formatting rules block."""
        toggles: dict[str, bool] = {}
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe")
        assert "[FORMATTING RULES]" not in result


# =============================================================================
# Thread context
# =============================================================================


class TestThreadContext:
    """Tests for thread_context handling in build_llm_prompt()."""

    def test_thread_context_included(self) -> None:
        """Messages list -> [CONVERSATION CONTEXT] block present."""
        toggles: dict[str, bool] = {}
        messages = ["Hello there", "How are you?"]
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe", thread_context=messages)
        assert "[CONVERSATION CONTEXT]" in result
        assert "Hello there" in result
        assert "How are you?" in result
        assert "[/CONVERSATION CONTEXT]" in result

    def test_thread_context_empty_excluded(self) -> None:
        """Empty/None -> no context block."""
        toggles: dict[str, bool] = {}
        result = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe", thread_context=None)
        assert "[CONVERSATION CONTEXT]" not in result

        result2 = build_llm_prompt("hello", toggles, app_script=None, app_name="notepad.exe", thread_context=[])
        assert "[CONVERSATION CONTEXT]" not in result2


# =============================================================================
# Token estimation
# =============================================================================


class TestEstimateTokens:
    """Tests for estimate_tokens()."""

    def test_estimate_tokens_rough(self) -> None:
        """400-char prompt -> ~100 tokens."""
        prompt = "a" * 400
        assert estimate_tokens(prompt) == 100

    def test_estimate_tokens_empty(self) -> None:
        """Empty string -> 0."""
        assert estimate_tokens("") == 0


# =============================================================================
# Sanitize
# =============================================================================


class TestSanitize:
    """Tests for sanitize()."""

    def test_sanitize_strips_control_chars(self) -> None:
        """Control chars removed."""
        text = "Hello\x00World\x07!\x1fEnd"
        result = sanitize(text)
        assert result == "HelloWorld!End"

    def test_sanitize_preserves_normal_text(self) -> None:
        """Normal text unchanged."""
        text = "Use sentence case. No Oxford comma."
        result = sanitize(text)
        assert result == text


# =============================================================================
# format_term_candidates
# =============================================================================


class TestFormatTermCandidates:
    """Tests for format_term_candidates()."""

    def test_format_single_term_with_candidates(self) -> None:
        """Single term with candidates formats correctly."""
        terms: list[dict[str, object]] = [
            {
                "term": "замок",
                "candidates": [
                    {"meaning": "door_lock", "cluster": "household door lock", "score": 0.71},
                    {"meaning": "mutex_lock", "cluster": "software lock", "score": 0.54},
                ],
            },
        ]
        result = format_term_candidates(terms)
        assert "[AMBIGUOUS TERMS]" in result
        assert "Term: замок" in result
        assert "- door_lock | household door lock | score 0.71" in result
        assert "- mutex_lock | software lock | score 0.54" in result
        assert "[/AMBIGUOUS TERMS]" in result
