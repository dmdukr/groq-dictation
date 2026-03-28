"""LLM prompt assembly for the Context Engine.

Provides:
- build_llm_prompt(): assemble system prompt from toggles, script, context, and term candidates
- format_term_candidates(): format ambiguous terms as a structured block
- sanitize(): strip control characters from user-provided values
- estimate_tokens(): rough token count approximation
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

BASE_PROMPT: str = "You are a dictation text normalizer. Clean up the following dictated text."

TOGGLE_INSTRUCTIONS: dict[str, str] = {
    "punctuation": "Add proper punctuation and sentence breaks.",
    "grammar": "Fix grammar errors while preserving the speaker's intent.",
    "capitalize": "Capitalize sentences and proper nouns appropriately.",
    "terminology": "Resolve technical terms using the context provided.",
}


def build_llm_prompt(
    raw_text: str,
    toggles: dict[str, bool],
    app_script: str | None,
    app_name: str,
    thread_context: list[str] | None = None,
    unresolved_terms: list[dict[str, object]] | None = None,
) -> str:
    """Assemble LLM system prompt from toggles + script + context + candidates.

    Base prompt always present: "You are a dictation text normalizer.
    Clean up the following dictated text."

    Toggles add sections:
    - punctuation: "Add proper punctuation and sentence breaks."
    - grammar: "Fix grammar errors while preserving the speaker's intent."
    - capitalize: "Capitalize sentences and proper nouns appropriately."
    - terminology: "Resolve technical terms using the context provided."
      (only if unresolved_terms exist)

    app_script: if not None, wrap in delimiters:
      "[FORMATTING RULES]\\n{script}\\n[/FORMATTING RULES]"
    thread_context: if not None, wrap:
      "[CONVERSATION CONTEXT]\\n{messages}\\n[/CONVERSATION CONTEXT]"
    unresolved_terms: format as candidates block

    Returns assembled prompt string.
    """
    active_toggles = [k for k, v in toggles.items() if v]
    logger.debug(
        "[prompt_builder] build_llm_prompt: app=%s, text_len=%d, toggles=%s, script=%s",
        app_name,
        len(raw_text),
        active_toggles,
        app_script is not None,
    )
    parts: list[str] = [BASE_PROMPT]

    for toggle_name, instruction in TOGGLE_INSTRUCTIONS.items():
        if not toggles.get(toggle_name, False):
            continue
        # terminology toggle only adds instruction when unresolved_terms exist
        if toggle_name == "terminology" and not unresolved_terms:
            continue
        parts.append(instruction)

    if app_script is not None:
        sanitized_script = sanitize(app_script)
        parts.append(f"[FORMATTING RULES]\n{sanitized_script}\n[/FORMATTING RULES]")

    if thread_context:
        messages = sanitize("\n".join(thread_context))
        parts.append(f"[CONVERSATION CONTEXT]\n{messages}\n[/CONVERSATION CONTEXT]")

    if unresolved_terms and toggles.get("terminology", False):
        parts.append(format_term_candidates(unresolved_terms))

    prompt = "\n\n".join(parts)
    logger.debug("[prompt_builder] build_llm_prompt: token_estimate=%d", estimate_tokens(prompt))
    return prompt


def format_term_candidates(terms: list[dict[str, object]]) -> str:
    """Format unresolved terms as candidates block.

    Each term dict has: term, candidates (list of {meaning, cluster, score})
    Output:
    [AMBIGUOUS TERMS]
    Term: замок
    Candidates:
    - door_lock | household door lock | score 0.71
    - mutex_lock | software lock | score 0.54
    [/AMBIGUOUS TERMS]
    """
    lines: list[str] = ["[AMBIGUOUS TERMS]"]
    for entry in terms:
        term = entry.get("term", "")
        lines.append(f"Term: {term}")
        lines.append("Candidates:")
        candidates = entry.get("candidates", [])
        if isinstance(candidates, list):
            for cand in candidates:
                if isinstance(cand, dict):
                    meaning = cand.get("meaning", "")
                    cluster = cand.get("cluster", "")
                    score = cand.get("score", 0.0)
                    lines.append(f"- {meaning} | {cluster} | score {score}")
    lines.append("[/AMBIGUOUS TERMS]")
    return "\n".join(lines)


def sanitize(value: str) -> str:
    """Strip control characters and excessive whitespace from user-provided values."""
    # Remove control chars except newline/tab
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    # Collapse multiple newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def estimate_tokens(prompt: str) -> int:
    """Rough token count: len(prompt) // 4."""
    return len(prompt) // 4
