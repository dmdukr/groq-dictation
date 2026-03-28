"""Integration tests for the DictationPipeline.

Tests the full 7-stage pipeline including context engine integration,
dictionary replacements, LLM normalization, correction feedback loop,
and history persistence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from src.context import corrections as corrections_module
from src.context.pipeline import (
    DictationPipeline,
    MockLLM,
    PipelineConfig,
    PipelineResult,
)

from tests.factories import (
    create_app_rule,
    create_cluster,
    create_cooccurrence,
    create_correction_count,
    create_dictionary_term,
    create_script,
    seed_mature_graph,
)

if TYPE_CHECKING:
    import sqlite3


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Reset the correction rate limiter between tests."""
    corrections_module._correction_timestamps.clear()  # noqa: SLF001


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Full pipeline integration tests."""

    def test_full_pipeline_with_llm(self, db_with_schema: sqlite3.Connection) -> None:
        """MockLLM + raw text -> all stages execute, result has normalized_text."""
        llm = MockLLM(response="Clean output text.")
        pipeline = DictationPipeline(db_with_schema, llm=llm)

        result = pipeline.process("some raw dictation text", app="telegram.exe")

        assert isinstance(result, PipelineResult)
        assert result.raw_text == "some raw dictation text"
        assert result.normalized_text == "Clean output text."
        assert result.llm_called is True
        assert result.llm_prompt is not None
        assert len(llm.calls) == 1
        assert llm.calls[0]["text"] == "some raw dictation text"

    def test_full_pipeline_without_llm(self, db_with_schema: sqlite3.Connection) -> None:
        """No LLM -> raw text returned with dictionary replacements only."""
        create_dictionary_term(db_with_schema, "пайтон", "Python")
        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        result = pipeline.process("пишу код на пайтон", app="vscode.exe")

        assert result.llm_called is False
        assert result.llm_prompt is None
        assert "Python" in result.normalized_text

    def test_pipeline_history_saved(self, db_with_schema: sqlite3.Connection) -> None:
        """After process(), history row exists in DB."""
        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        pipeline.process("test dictation", app="notepad.exe", window_title="Untitled")

        row = db_with_schema.execute("SELECT * FROM history").fetchone()
        assert row is not None
        assert row["app"] == "notepad.exe"
        assert row["window_title"] == "Untitled"
        assert row["word_count"] == 2


# ---------------------------------------------------------------------------
# Context integration
# ---------------------------------------------------------------------------


class TestContextIntegration:
    """Tests for context engine integration within the pipeline."""

    def test_pipeline_context_populates_result(self, db_with_schema: sqlite3.Connection) -> None:
        """Seeded graph -> result has thread_id, cluster_id, keywords."""
        seed_mature_graph(db_with_schema, num_clusters=2, edges_per_cluster=20)

        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        # Use terms from cluster 0 (git/deploy/pr/merge/branch...)
        result = pipeline.process("git deploy merge branch code", app="telegram.exe")

        assert result.thread_id is not None
        assert len(result.keywords) > 0

    def test_pipeline_exact_dict_applied(self, db_with_schema: sqlite3.Connection) -> None:
        """Add dictionary term, process text -> replacement applied in output."""
        create_dictionary_term(db_with_schema, "пайтон", "Python")
        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        result = pipeline.process("пишу код на пайтон сьогодні", app="vscode.exe")

        assert "Python" in result.normalized_text
        assert "пайтон" not in result.normalized_text


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------


class TestFeedbackLoop:
    """Tests for the correction feedback loop."""

    def test_correction_stores_in_db(self, db_with_schema: sqlite3.Connection) -> None:
        """process_correction() -> corrections table has row."""
        pipeline = DictationPipeline(db_with_schema)

        stored = pipeline.process_correction(
            raw="тест",
            normalized="test",
            corrected="Test",
            app="telegram.exe",
            thread_id=None,
            cluster_id=None,
        )

        assert stored is True
        row = db_with_schema.execute("SELECT * FROM corrections").fetchone()
        assert row is not None
        assert row["app"] == "telegram.exe"

    def test_correction_auto_promotes(self, db_with_schema: sqlite3.Connection) -> None:
        """Correct same term 3x -> promoted to dictionary -> next process uses it."""
        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        # Pre-seed correction_counts to 2, so the next correction promotes
        create_correction_count(db_with_schema, "пайтон", "Python", count=2)

        # This 3rd correction should trigger auto-promote
        pipeline.process_correction(
            raw="пишу на пайтон",
            normalized="пишу на пайтон",
            corrected="пишу на Python",
            app="vscode.exe",
            thread_id=None,
            cluster_id=None,
        )

        # Verify auto-promote to dictionary
        row = db_with_schema.execute(
            "SELECT * FROM dictionary WHERE source_text = ? AND target_text = ?",
            ["пайтон", "Python"],
        ).fetchone()
        assert row is not None
        assert row["origin"] == "auto_promoted"

        # Now the pipeline should apply this dictionary term
        result = pipeline.process("пишу код на пайтон", app="vscode.exe")
        assert "Python" in result.normalized_text


# ---------------------------------------------------------------------------
# Cross-app scenario
# ---------------------------------------------------------------------------


class TestCrossApp:
    """Tests for cross-application thread persistence."""

    def test_cross_app_thread_persistence(self, db_with_schema: sqlite3.Connection) -> None:
        """Dictation in app A with keywords -> dictation in app B with same keywords -> same thread."""
        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        # First dictation in telegram
        result_a = pipeline.process("git deploy merge branch refactor", app="telegram.exe")
        thread_a = result_a.thread_id

        # Second dictation in vscode with overlapping keywords
        result_b = pipeline.process("git deploy merge branch staging", app="vscode.exe")
        thread_b = result_b.thread_id

        # Both should be in the same thread (cross-app matching with overlap)
        assert thread_a is not None
        assert thread_b is not None
        assert thread_a == thread_b


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


class TestColdStart:
    """Tests for cold start (empty database) scenarios."""

    def test_cold_start_empty_db(self, db_with_schema: sqlite3.Connection) -> None:
        """Fresh DB, no data -> pipeline still produces output."""
        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        result = pipeline.process("hello world this is a test", app="notepad.exe")

        assert result.raw_text == "hello world this is a test"
        assert result.normalized_text == "hello world this is a test"
        assert isinstance(result.keywords, list)

    def test_cold_start_graph_grows(self, db_with_schema: sqlite3.Connection) -> None:
        """Seed minimal graph, simulate dictations -> co-occurrence edges grow."""
        # Seed a small cluster so detect_cluster can find it and update_cooccurrence fires
        cid = create_cluster(db_with_schema, display_name="dev_ops")
        for a, b in [("git", "deploy"), ("deploy", "merge"), ("merge", "branch")]:
            create_cooccurrence(db_with_schema, a, b, cluster_id=cid, weight=5)

        initial_count: int = db_with_schema.execute("SELECT COUNT(*) AS cnt FROM term_cooccurrence").fetchone()["cnt"]

        pipeline = DictationPipeline(
            db_with_schema,
            config=PipelineConfig(enable_llm=False),
        )

        texts = [
            "git deploy merge branch code",
            "git deploy staging prod ci",
            "deploy merge refactor code branch",
            "git staging deploy branch merge",
            "code refactor deploy git merge",
        ]

        for text in texts:
            pipeline.process(text, app="telegram.exe")

        # Verify co-occurrence edges grew from the initial seed
        final_count: int = db_with_schema.execute("SELECT COUNT(*) AS cnt FROM term_cooccurrence").fetchone()["cnt"]
        assert final_count > initial_count


# ---------------------------------------------------------------------------
# Degraded modes
# ---------------------------------------------------------------------------


class TestDegradedModes:
    """Tests for degraded/edge-case pipeline modes."""

    def test_all_toggles_off_skip_llm(self, db_with_schema: sqlite3.Connection) -> None:
        """enable_llm=False -> llm_called=False."""
        llm = MockLLM(response="should not be called")
        config = PipelineConfig(enable_llm=False)
        pipeline = DictationPipeline(db_with_schema, config=config, llm=llm)

        result = pipeline.process("some text here", app="notepad.exe")

        assert result.llm_called is False
        assert len(llm.calls) == 0

    def test_empty_text_noop(self, db_with_schema: sqlite3.Connection) -> None:
        """Empty string -> empty result, no DB writes."""
        pipeline = DictationPipeline(db_with_schema)

        result = pipeline.process("", app="notepad.exe")

        assert result.raw_text == ""
        assert result.normalized_text == ""
        assert result.llm_called is False
        assert result.thread_id is None

        # No history should be written
        row = db_with_schema.execute("SELECT COUNT(*) AS cnt FROM history").fetchone()
        assert row is not None
        assert row["cnt"] == 0

    def test_whitespace_only_noop(self, db_with_schema: sqlite3.Connection) -> None:
        """Whitespace-only string -> empty result, no DB writes."""
        pipeline = DictationPipeline(db_with_schema)

        result = pipeline.process("   \t\n  ", app="notepad.exe")

        assert result.llm_called is False
        assert result.thread_id is None

        row = db_with_schema.execute("SELECT COUNT(*) AS cnt FROM history").fetchone()
        assert row is not None
        assert row["cnt"] == 0


# ---------------------------------------------------------------------------
# App script
# ---------------------------------------------------------------------------


class TestAppScript:
    """Tests for app-specific script inclusion in LLM prompts."""

    def test_app_script_included_in_prompt(self, db_with_schema: sqlite3.Connection) -> None:
        """Create script + app_rule -> llm_prompt contains script body."""
        script_body = "Always use markdown formatting for code blocks."
        script_id = create_script(db_with_schema, "vscode_format", script_body)
        create_app_rule(db_with_schema, "vscode.exe", script_id)

        llm = MockLLM(response="formatted output")
        pipeline = DictationPipeline(db_with_schema, llm=llm)

        result = pipeline.process("write a function", app="vscode.exe")

        assert result.llm_called is True
        assert result.llm_prompt is not None
        assert "[FORMATTING RULES]" in result.llm_prompt
        assert script_body in result.llm_prompt

    def test_no_app_script_no_formatting_rules(self, db_with_schema: sqlite3.Connection) -> None:
        """No app_rule for this app -> no [FORMATTING RULES] in prompt."""
        llm = MockLLM(response="output")
        pipeline = DictationPipeline(db_with_schema, llm=llm)

        result = pipeline.process("some text", app="unknown_app.exe")

        assert result.llm_called is True
        assert result.llm_prompt is not None
        assert "[FORMATTING RULES]" not in result.llm_prompt
