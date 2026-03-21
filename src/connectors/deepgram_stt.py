"""Deepgram STT connector — REST API POST /v1/listen.

Sends WAV audio, receives JSON with transcription.
Supports Nova-3, Nova-2, and Whisper Cloud models.
"""

from __future__ import annotations

import logging

import httpx

from .base import STTConnector

logger = logging.getLogger(__name__)


class DeepgramSTT(STTConnector):
    """Deepgram speech-to-text via REST API."""

    def __init__(self, api_key: str, model: str = "nova-3"):
        self._api_key = api_key
        self._model = model
        self._http = httpx.Client(
            base_url="https://api.deepgram.com/v1",
            headers={"Authorization": f"Token {api_key}"},
            timeout=30.0,
        )
        self._used_seconds = 0
        self._limit_seconds = 0
        logger.info("DeepgramSTT: model=%s", model)

    def transcribe(self, wav_bytes, language="", previous_text=""):
        try:
            params = {
                "model": self._model,
                "smart_format": "true",
                "punctuate": "true",
            }
            if language:
                lang = language.split(",")[0].strip() if "," in language else language
                params["language"] = lang

            resp = self._http.post(
                "/listen",
                params=params,
                content=wav_bytes,
                headers={
                    "Content-Type": "audio/wav",
                    "Authorization": f"Token {self._api_key}",
                },
            )

            if resp.status_code == 401:
                logger.error("Deepgram auth failed")
                return None
            if resp.status_code == 429:
                logger.warning("Deepgram rate limited")
                return None
            if resp.status_code >= 400:
                logger.error("Deepgram API error %d: %s", resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            # Deepgram response: results.channels[0].alternatives[0].transcript
            channels = data.get("results", {}).get("channels", [])
            if channels:
                alternatives = channels[0].get("alternatives", [])
                if alternatives:
                    text = alternatives[0].get("transcript", "").strip()
                    if text:
                        logger.debug("Deepgram transcription: %r", text[:80])
                        return text
            return None

        except Exception as e:
            logger.error("Deepgram error: %s", e)
            return None

    def get_usage(self):
        return (self._used_seconds, self._limit_seconds)

    def close(self):
        self._http.close()
