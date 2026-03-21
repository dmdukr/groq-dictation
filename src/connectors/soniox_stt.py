"""Soniox STT connector — REST API for async transcription.

Uses POST /transcribe for batch transcription of audio files.
Soniox also has WebSocket for real-time, but batch is simpler for
dictation use case (short audio chunks).
"""

from __future__ import annotations

import logging

import httpx

from .base import STTConnector

logger = logging.getLogger(__name__)


class SonioxSTT(STTConnector):
    """Soniox speech-to-text via REST API."""

    def __init__(self, api_key: str, model: str = "stt-async-v4"):
        self._api_key = api_key
        self._model = model
        self._http = httpx.Client(
            base_url="https://api.soniox.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        self._used_seconds = 0
        self._limit_seconds = 0
        logger.info("SonioxSTT: model=%s", model)

    def transcribe(self, wav_bytes, language="", previous_text=""):
        try:
            data = {"model": self._model}
            if language:
                # Soniox uses language codes directly
                lang = language.split(",")[0].strip() if "," in language else language
                data["language"] = lang

            resp = self._http.post(
                "/transcribe",
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data=data,
            )

            if resp.status_code == 401:
                logger.error("Soniox auth failed")
                return None
            if resp.status_code == 429:
                logger.warning("Soniox rate limited")
                return None
            if resp.status_code >= 400:
                logger.error("Soniox API error %d: %s", resp.status_code, resp.text[:200])
                return None

            result = resp.json()
            text = result.get("text", "").strip()
            if not text:
                return None

            logger.debug("Soniox transcription: %r", text[:80])
            return text

        except Exception as e:
            logger.error("Soniox error: %s", e)
            return None

    def get_usage(self):
        return (self._used_seconds, self._limit_seconds)

    def close(self):
        self._http.close()
