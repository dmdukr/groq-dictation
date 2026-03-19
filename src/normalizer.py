"""Text normalization via Groq LLM post-processing.

Two-pass normalization:
  Pass 1: Fix recognition errors, remove fillers, restore words from context
  Pass 2: Polish grammar, ensure coherent sentences
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import httpx

from .config import GroqConfig, NormalizationConfig

logger = logging.getLogger(__name__)

PASS1_PROMPT = """\
You are a speech-to-text post-processor. Your job is to fix a raw voice transcription.

RULES:
1. Remove ALL filler words: ну, эм, ээ, типа, как бы, значит, короче, вот, ну типа, like, uh, um, ehm, so, you know, basically
2. CRITICALLY IMPORTANT: Fix misrecognized words. Whisper often confuses similar-sounding syllables (е/и, а/о, в/б, etc.). If a word looks wrong but sounds similar to the correct one, FIX IT. Examples: "вашел"→"вышел", "побегает"→"выбегает", "расказ"→"рассказ". Use surrounding context, common phrases, idioms, well-known quotes, songs, and poems to determine the correct word.
3. If you see random Latin letters, gibberish, or keyboard sequences — this is a recognition error. Replace with the intended Cyrillic word.
4. Fix punctuation: add periods, commas, question marks where natural pauses occur.
5. Fix capitalization: start of sentences, proper nouns.
6. Format numbers and dates properly (e.g., "двадцать третье марта" → "23 марта").
7. Preserve the speaker's intent, but DO fix wrong words that are clearly recognition errors.
8. Do NOT translate between languages — if someone mixes Russian, Ukrainian, and English, keep it mixed.
9. Do NOT add any text that wasn't in the original.
10. Return ONLY the corrected text, no explanations.

{profile_instruction}
{terms_instruction}
{context_instruction}

RAW TRANSCRIPTION:
{text}"""

PASS2_PROMPT = """\
You are a text editor. Polish this dictated text to ensure it reads as coherent, complete sentences or paragraphs.

RULES:
1. Fix any remaining grammatical issues.
2. Ensure sentences are complete and logically connected.
3. If a sentence is clearly unfinished or broken, reconstruct it to the most likely intended meaning.
4. Keep the style natural — this is dictated speech, not formal writing.
5. Do NOT change the meaning or add new information.
6. Do NOT translate between languages.
7. Return ONLY the polished text, no explanations.

{context_instruction}

TEXT TO POLISH:
{text}"""


class Normalizer:
    """Normalizes transcribed text using Groq LLM with two-pass processing."""

    def __init__(self, groq_config: GroqConfig, norm_config: NormalizationConfig,
                 profile=None):
        self._groq_config = groq_config
        self._norm_config = norm_config
        self._profile = profile  # UserProfile instance (optional)
        self._http = httpx.Client(
            base_url="https://api.groq.com/openai/v1",
            headers={"Authorization": f"Bearer {groq_config.api_key}"},
            timeout=30.0,
        )

    def normalize(self, raw_text: str, context: str = "") -> str:
        """Two-pass normalization: fix errors, then polish.

        Args:
            raw_text: Raw transcription from Whisper.
            context: Text from active window (paragraph before cursor) for reference.
        """
        if not raw_text.strip():
            return raw_text

        if not self._norm_config.enabled:
            return raw_text

        # Build instructions
        profile_instruction = ""
        if self._profile:
            profile_instruction = self._profile.get_prompt_context()
            if profile_instruction:
                logger.info("Profile context injected (%d chars)", len(profile_instruction))

        terms_instruction = ""
        if self._norm_config.known_terms:
            terms = ", ".join(self._norm_config.known_terms)
            terms_instruction = f"KNOWN TERMS (preserve exactly): {terms}"

        context_instruction = ""
        if context.strip():
            context_instruction = f"PRECEDING TEXT IN DOCUMENT (for context only, do not include in output):\n{context.strip()}"

        # Pass 1: Fix recognition errors and fillers
        pass1_prompt = PASS1_PROMPT.format(
            profile_instruction=profile_instruction,
            terms_instruction=terms_instruction,
            context_instruction=context_instruction,
            text=raw_text,
        )
        pass1_result = self._call_llm(pass1_prompt, "pass1")
        if not pass1_result:
            return raw_text

        # Pass 2: Polish grammar and coherence
        pass2_prompt = PASS2_PROMPT.format(
            context_instruction=context_instruction,
            text=pass1_result,
        )
        pass2_result = self._call_llm(pass2_prompt, "pass2")

        final = pass2_result or pass1_result
        logger.info(
            "Normalized: %d chars → pass1: %d → pass2: %d",
            len(raw_text), len(pass1_result), len(final),
        )
        return final

    def normalize_async(
        self,
        raw_text: str,
        callback: Callable[[str], None],
        executor: ThreadPoolExecutor | None = None,
        context: str = "",
    ) -> None:
        """Submit normalization to thread pool."""
        if executor:
            future = executor.submit(self.normalize, raw_text, context)
            future.add_done_callback(
                lambda f: callback(f.result() if not f.exception() else raw_text)
            )
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.normalize, raw_text, context)
                callback(future.result())

    def _call_llm(self, prompt: str, pass_name: str) -> str | None:
        """Call Groq LLM with retry."""
        try:
            # Use compiled prompt from profile if available, else default
            system_prompt = (
                "You are a speech-to-text error corrector. "
                "Whisper often mishears syllables: е↔а, и↔ы, в↔б, пре↔при↔вы, etc. "
                "You MUST fix these phonetic errors. If the text contains a well-known "
                "phrase, poem, song, or idiom with wrong words — fix them to the canonical version. "
                "NEVER translate words between languages — keep English words in English as-is. "
                "Output ONLY the corrected text."
            )
            if self._profile:
                compiled = self._profile.get_prompt_context()
                if compiled:
                    system_prompt = compiled

            resp = self._http.post(
                "/chat/completions",
                json={
                    "model": self._groq_config.llm_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": self._norm_config.temperature,
                    "max_tokens": 2000,
                },
            )

            if resp.status_code == 429:
                logger.warning("Rate limit in %s, retrying...", pass_name)
                return self._retry_llm(prompt, pass_name)
            if resp.status_code == 401:
                logger.error("Auth failed in %s", pass_name)
                return None

            resp.raise_for_status()
            data = resp.json()
            result = data["choices"][0]["message"]["content"]
            if result:
                cleaned = result.strip()
                logger.info("LLM %s: '%s'", pass_name, cleaned[:100])
                return cleaned
            return None

        except httpx.HTTPStatusError as e:
            logger.error("LLM %s HTTP error: %s", pass_name, e)
            return None
        except Exception as e:
            logger.error("LLM %s error: %s", pass_name, e)
            return None

    def _retry_llm(self, prompt: str, pass_name: str) -> str | None:
        """Retry with backoff."""
        import time
        for i, delay in enumerate([2, 4]):
            time.sleep(delay)
            try:
                resp = self._http.post(
                    "/chat/completions",
                    json={
                        "model": self._groq_config.llm_model,
                        "messages": [
                            {"role": "system", "content": (
                                "You are a speech-to-text error corrector. "
                                "Whisper often mishears syllables. "
                                "Fix phonetic errors and restore well-known phrases. "
                                "NEVER translate words between languages — keep English words in English. "
                                "Output ONLY the corrected text."
                            )},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": self._norm_config.temperature,
                        "max_tokens": 2000,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                result = data["choices"][0]["message"]["content"]
                if result:
                    return result.strip()
            except Exception as e:
                logger.warning("Retry %d for %s failed: %s", i + 1, pass_name, e)
        return None
