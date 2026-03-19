"""Multi-layer hallucination filter for Whisper STT output.

Layers:
  1. Audio RMS check — reject silence before sending to API
  2. Segment metrics — no_speech_prob, avg_logprob, compression_ratio
  3. Repetition detection — exact match + n-gram overlap
  4. Blocklist — known hallucination phrases + regex patterns
  5. Length ratio — text too long for audio duration
  6. Punctuation-only — dots, commas with no real words
"""

import logging
import math
import re
import struct
from collections import Counter

logger = logging.getLogger(__name__)

# ── Blocklist ────────────────────────────────────────────────────────────

HALLUCINATION_PHRASES: set[str] = {
    "thank you for watching",
    "thanks for watching",
    "subscribe",
    "like and subscribe",
    "please subscribe",
    "mbc news",
    "подписывайтесь",
    "спасибо за просмотр",
    "дякую за перегляд",
    "подпишитесь на канал",
    "ставьте лайки",
    "продовження наступне",
    "редактор субтитрів",
    "переклад субтитрів",
    "субтитри зроблено",
    "субтитры сделаны",
    "редактор субтитров",
    "корректор",
    "you",
    "thank you",
    "thanks",
    "bye",
    "bye bye",
    "the end",
    "end",
    "silence",
    "тишина",
    "продолжение следует",
    "конец",
    "говоріть будь ласка",
    "говоріть будь ласка.",
    "русский, українська, english",
}

# Regex patterns for common hallucination structures
HALLUCINATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\.+$"),                    # just dots
    re.compile(r"^[\s.,!?;:\-…]+$"),         # punctuation only
    re.compile(r"^(.{2,30})\1{1,}$"),         # same phrase repeated 2+ times
    re.compile(r"редактор.*субтитр", re.I),  # subtitle editor credits
    re.compile(r"переклад.*субтитр", re.I),  # subtitle translation credits
    re.compile(r"корректор.*[А-Я]", re.I),   # corrector + name (credits)
]

# ── Thresholds ───────────────────────────────────────────────────────────

MIN_AUDIO_RMS = 50              # Minimum RMS to consider audio has content
NO_SPEECH_THRESHOLD = 0.5       # Segments with no_speech_prob above this → drop
AVG_LOGPROB_THRESHOLD = -3.0    # Segments with avg_logprob below this → drop
COMPRESSION_RATIO_THRESHOLD = 2.2  # Segments with compression_ratio above → suspicious
MAX_CHARS_PER_SECOND = 25       # Max reasonable speech rate (~150 wpm)
NGRAM_REPEAT_THRESHOLD = 0.5    # If >50% of 3-grams are repeated → hallucination


def check_audio_has_speech(wav_bytes: bytes) -> bool:
    """Check if WAV audio contains actual sound (not silence).

    Args:
        wav_bytes: Raw WAV file bytes.

    Returns:
        True if audio has content above noise floor.
    """
    # Skip WAV header (44 bytes)
    pcm_data = wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes
    if len(pcm_data) < 4:
        return False

    n_samples = len(pcm_data) // 2
    try:
        samples = struct.unpack(f"<{n_samples}h", pcm_data[:n_samples * 2])
    except struct.error:
        return False

    rms = math.sqrt(sum(s * s for s in samples) / n_samples) if n_samples > 0 else 0
    has_speech = rms > MIN_AUDIO_RMS
    if not has_speech:
        logger.debug("Audio RMS %.0f below threshold %d — no speech", rms, MIN_AUDIO_RMS)
    return has_speech


def filter_segments(segments: list, audio_duration_s: float = 0) -> list[str]:
    """Filter Whisper segments using quality metrics.

    Args:
        segments: List of segment dicts or objects from verbose_json response.
        audio_duration_s: Duration of audio in seconds (for length ratio check).

    Returns:
        List of accepted text strings.
    """
    accepted = []

    for seg in segments:
        # Handle both dict and object
        if isinstance(seg, dict):
            no_speech = seg.get("no_speech_prob", 0.0)
            avg_logprob = seg.get("avg_logprob", 0.0)
            compression_ratio = seg.get("compression_ratio", 1.0)
            text = seg.get("text", "").strip()
        else:
            no_speech = getattr(seg, "no_speech_prob", 0.0)
            avg_logprob = getattr(seg, "avg_logprob", 0.0)
            compression_ratio = getattr(seg, "compression_ratio", 1.0)
            text = getattr(seg, "text", "").strip()

        if not text:
            continue

        # Layer 2: Segment metrics
        if no_speech > NO_SPEECH_THRESHOLD:
            logger.info("Segment dropped (no_speech=%.2f): %r", no_speech, text[:50])
            continue

        if avg_logprob < AVG_LOGPROB_THRESHOLD:
            logger.info("Segment dropped (logprob=%.2f): %r", avg_logprob, text[:50])
            continue

        if compression_ratio > COMPRESSION_RATIO_THRESHOLD:
            logger.info("Segment dropped (compression=%.1f): %r", compression_ratio, text[:50])
            continue

        accepted.append(text)

    return accepted


def check_text_quality(text: str, previous_text: str = "",
                       audio_duration_s: float = 0) -> str | None:
    """Apply text-level hallucination filters.

    Args:
        text: Combined transcription text.
        previous_text: Previous chunk's text for repetition check.
        audio_duration_s: Audio duration for length ratio check.

    Returns:
        Cleaned text, or None if it's a hallucination.
    """
    if not text.strip():
        return None

    text_lower = text.strip().lower()

    # Layer 4: Blocklist (exact match)
    if text_lower in HALLUCINATION_PHRASES:
        logger.info("Blocklist hit: %r", text[:50])
        return None

    # Layer 4: Regex patterns
    for pattern in HALLUCINATION_PATTERNS:
        if pattern.search(text):
            logger.info("Pattern match hallucination: %r", text[:50])
            return None

    # Layer 3: Exact repetition of previous chunk
    if previous_text and text.strip() == previous_text.strip():
        logger.info("Exact repetition of previous chunk")
        return None

    # Layer 3: N-gram repetition (same 3-word phrases repeated)
    words = text_lower.split()
    if len(words) >= 6:
        trigrams = [" ".join(words[i:i+3]) for i in range(len(words) - 2)]
        counts = Counter(trigrams)
        if trigrams:
            repeat_ratio = sum(1 for c in counts.values() if c > 1) / len(counts)
            if repeat_ratio > NGRAM_REPEAT_THRESHOLD:
                logger.info("High n-gram repetition (%.0f%%): %r", repeat_ratio * 100, text[:50])
                return None

    # Layer 7: Gibberish / mixed-script noise detection
    # Real speech has mostly letters; hallucinations often have high punctuation/symbol ratio
    letters = sum(1 for c in text if c.isalpha())
    total = len(text.strip())
    if total > 0 and letters / total < 0.5:
        logger.info("Low letter ratio (%.0f%%): %r", letters / total * 100, text[:50])
        return None

    # Layer 5: Length ratio check
    if audio_duration_s > 0:
        chars_per_sec = len(text) / audio_duration_s
        if chars_per_sec > MAX_CHARS_PER_SECOND:
            logger.info("Suspicious length ratio: %.1f chars/sec for %.1fs audio: %r",
                        chars_per_sec, audio_duration_s, text[:50])
            return None

    # Layer 6: Punctuation-only
    if all(c in " .,!?;:-…\"'()" for c in text):
        logger.debug("Punctuation-only: %r", text)
        return None

    return text.strip()
