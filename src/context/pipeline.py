"""Dictation pipeline — 7-stage orchestration for the Context Engine.

Stages:
1. Audio capture (external — we receive text from STT)
2. STT (external — we receive raw text)
3. Replacements (voice macros — placeholder, no-op for now)
4. Context Engine (resolve terms + build prompt)
5. LLM normalization (with assembled prompt)
6. Local post-processing (exact dictionary replacements)
7. History + context update
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

from src.context.corrections import learn_from_correction, mock_encrypt
from src.context.dictionary import apply_exact_replacements, get_exact_terms
from src.context.engine import ContextEngine, ContextResult
from src.context.prompt_builder import build_llm_prompt

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the dictation pipeline."""

    toggles: dict[str, bool] = field(
        default_factory=lambda: {
            "punctuation": True,
            "grammar": True,
            "capitalize": True,
            "terminology": True,
        }
    )
    enable_llm: bool = True


@dataclass
class PipelineResult:
    """Result of a full pipeline run."""

    raw_text: str
    normalized_text: str
    thread_id: int | None = None
    cluster_id: int | None = None
    keywords: list[str] = field(default_factory=list)
    resolved_terms: set[str] = field(default_factory=set)
    llm_called: bool = False
    llm_prompt: str | None = None


class MockSTT:
    """Mock STT provider for testing."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def transcribe(self, _audio: bytes) -> str:
        """Return pre-configured text regardless of audio input."""
        return self._text


class MockLLM:
    """Mock LLM provider for testing."""

    def __init__(self, response: str | None = None) -> None:
        self._response = response
        self.calls: list[dict[str, str]] = []

    def normalize(self, system_prompt: str, text: str) -> str:
        """Record the call and return configured response or echo text."""
        self.calls.append({"system": system_prompt, "text": text})
        if self._response is not None:
            return self._response
        return text  # Echo back by default


class DictationPipeline:
    """Orchestrates the 7-stage dictation pipeline.

    Stages:
    1. Audio capture (external — we receive text from STT)
    2. STT (external — we receive raw text)
    3. Replacements (voice macros — skip for now, placeholder)
    4. Context Engine (resolve terms + build prompt)
    5. LLM normalization (with assembled prompt)
    6. Local post-processing (exact dictionary replacements)
    7. History + context update
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        config: PipelineConfig | None = None,
        llm: MockLLM | None = None,
    ) -> None:
        self._db = db
        self._config = config or PipelineConfig()
        self._llm = llm
        self._context_engine = ContextEngine(db)

    def process(self, raw_text: str, app: str, window_title: str = "") -> PipelineResult:
        """Full pipeline execution.

        1. Skip if raw_text is empty
        2. Stage 3: Apply replacements (placeholder — no-op for now)
        3. Stage 4: Context Engine resolve
        4. Stage 5: LLM normalization (if enabled and LLM available)
        5. Stage 6: Local post-processing (exact dictionary replacements, skip resolved terms)
        6. Stage 7: Save to history
        7. Return PipelineResult
        """
        if not raw_text.strip():
            return PipelineResult(raw_text=raw_text, normalized_text=raw_text)

        # Stage 3: Replacements (placeholder)
        replaced_text = raw_text

        # Stage 4: Context Engine
        ctx: ContextResult = self._context_engine.resolve(replaced_text, app)

        # Stage 5: LLM normalization
        normalized_text = replaced_text
        llm_called = False
        llm_prompt: str | None = None

        if self._config.enable_llm and self._llm is not None:
            # Build prompt with context
            app_script = self._get_app_script(app)
            llm_prompt = build_llm_prompt(
                raw_text=replaced_text,
                toggles=self._config.toggles,
                app_script=app_script,
                app_name=app,
            )
            normalized_text = self._llm.normalize(llm_prompt, replaced_text)
            llm_called = True

        # Stage 6: Local post-processing
        exact_terms = get_exact_terms(self._db)
        if exact_terms:
            normalized_text = apply_exact_replacements(
                normalized_text,
                exact_terms,
                ctx.resolved_terms,
            )

        # Stage 7: Save history
        self._save_history(
            raw_text=raw_text,
            normalized_text=normalized_text,
            app=app,
            window_title=window_title,
            thread_id=ctx.thread_id,
            cluster_id=ctx.cluster_id,
        )

        return PipelineResult(
            raw_text=raw_text,
            normalized_text=normalized_text,
            thread_id=ctx.thread_id,
            cluster_id=ctx.cluster_id,
            keywords=ctx.keywords,
            resolved_terms=ctx.resolved_terms,
            llm_called=llm_called,
            llm_prompt=llm_prompt,
        )

    def process_correction(
        self,
        raw: str,
        normalized: str,
        corrected: str,
        app: str,
        thread_id: int | None,
        cluster_id: int | None,
    ) -> bool:
        """Process user correction feedback."""
        return learn_from_correction(
            self._db,
            raw,
            normalized,
            corrected,
            app,
            thread_id,
            cluster_id,
            encrypt_fn=mock_encrypt,
        )

    def _get_app_script(self, app: str) -> str | None:
        """Look up custom script for this app."""
        row = self._db.execute(
            """SELECT s.body FROM app_rules ar
               JOIN scripts s ON ar.script_id = s.id
               WHERE ar.app_name = ?""",
            [app],
        ).fetchone()
        return str(row["body"]) if row else None

    def _save_history(
        self,
        raw_text: str,
        normalized_text: str,
        app: str,
        window_title: str,
        thread_id: int | None,
        cluster_id: int | None,
    ) -> None:
        """Save dictation to history table."""
        self._db.execute(
            """INSERT INTO history
               (raw_text_enc, normalized_text_enc, app, window_title, thread_id, cluster_id, word_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                mock_encrypt(raw_text),
                mock_encrypt(normalized_text),
                app,
                window_title,
                thread_id,
                cluster_id,
                len(raw_text.split()),
            ],
        )
        self._db.commit()
