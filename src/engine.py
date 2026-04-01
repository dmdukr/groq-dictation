"""DictationEngine — central state machine coordinating all modules."""

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Callable

from .config import AppConfig
from .audio_capture import AudioCapture
from .chunk_manager import ChunkManager
from .normalizer import Normalizer
from .text_injector import TextInjector
from .recording_overlay import RecordingOverlay
from .user_profile import UserProfile
from .telemetry import TelemetryCollector
from .provider_manager import ProviderManager

logger = logging.getLogger(__name__)


class DictationState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    TYPING = "typing"
    ERROR = "error"


class DictationEngine:
    """Orchestrates the entire dictation lifecycle.

    State machine: IDLE → RECORDING → PROCESSING → TYPING → IDLE

    During RECORDING:
      - AudioCapture records from mic
      - ChunkManager splits audio on pauses via VAD
      - Each chunk → STT provider → text typed into active window (draft)

    On toggle (stop):
      - Flush remaining audio
      - Concatenate all session text
      - Send to Normalizer (Groq LLM)
      - Replace draft with normalized text
    """

    def __init__(self, config: AppConfig):
        logger.debug("engine: __init__ — creating DictationEngine")
        self._config = config
        self._state = DictationState.IDLE
        self._state_lock = threading.Lock()
        self._last_toggle = 0.0

        # Modules
        self._audio = AudioCapture(config.audio)
        self._chunker: ChunkManager | None = None
        # User profile (learns speech patterns over time)
        self._profile = UserProfile(
            enabled=config.profile.enabled,
            min_correction_count=config.profile.min_correction_count,
            max_prompt_tokens=config.profile.max_prompt_tokens,
            decay_days=config.profile.decay_days,
        )
        self._profile.load()
        logger.debug("engine: __init__ — user profile loaded, enabled=%s", config.profile.enabled)
        # Provider manager (multi-slot fallback for STT + LLM)
        self._quota_callback: Callable[[int, int], None] | None = None
        self._providers = ProviderManager(
            config.providers,
            on_quota_warning=self._on_quota_warning,
            stt_language=config.groq.stt_language or "",
        )
        logger.debug("engine: __init__ — provider manager created, stt_language=%s", config.groq.stt_language or "")

        # Normalizer uses LLM connector from provider manager
        llm = self._providers.get_llm()
        self._normalizer = Normalizer(llm, config.normalization, profile=self._profile)
        self._injector = TextInjector(config.text_injection)
        self._overlay = RecordingOverlay()
        self._telemetry = TelemetryCollector(enabled=config.telemetry.enabled)
        logger.debug("engine: __init__ — all modules initialized, normalization_enabled=%s, telemetry_enabled=%s",
                      config.normalization.enabled, config.telemetry.enabled)
        self._viz_queue: queue.Queue[bytes] | None = None

        # Thread pool for API calls
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="stt")

        # Session state
        self._session_text: list[str] = []
        self._session_lock = threading.Lock()
        self._next_chunk_id = 0
        self._next_type_id = 0
        self._pending_results: dict[int, str] = {}
        self._typing_lock = threading.Lock()
        self._previous_text = ""
        self._last_normalized_text = ""  # for feedback learning
        self._last_typed_len = 0  # length of last typed text for grab
        self._session_raw_text = ""
        self._last_tap_count = 0  # tap counter for double-tap

        # State change callback (for TrayApp)
        self._state_callback: Callable[[DictationState], None] | None = None
        self._error_callback: Callable[[str], None] | None = None
        self._suppress_ptt_callback: Callable[[bool], None] | None = None

    @property
    def state(self) -> DictationState:
        return self._state

    def set_state_callback(self, callback: Callable[[DictationState], None]) -> None:
        """Register callback for state changes (used by TrayApp)."""
        logger.debug("engine: set_state_callback — registered")
        self._state_callback = callback

    def set_error_callback(self, callback: Callable[[str], None]) -> None:
        """Register callback for error notifications."""
        logger.debug("engine: set_error_callback — registered")
        self._error_callback = callback

    def set_suppress_ptt_callback(self, callback: Callable[[bool], None]) -> None:
        """Register callback to suppress/unsuppress PTT during feedback capture."""
        logger.debug("engine: set_suppress_ptt_callback — registered")
        self._suppress_ptt_callback = callback

    def set_quota_callback(self, callback: Callable[[int, int], None]) -> None:
        """Register callback for quota warnings. callback(remaining_sec, limit_sec)."""
        logger.debug("engine: set_quota_callback — registered")
        self._quota_callback = callback

    def _on_quota_warning(self, remaining: int, limit: int) -> None:
        """Called by STT connector when quota threshold is reached."""
        logger.info("engine: quota warning — remaining=%d, limit=%d", remaining, limit)
        if self._quota_callback:
            self._quota_callback(remaining, limit)

    def get_audio_capture(self) -> AudioCapture:
        """Access AudioCapture for mic management from TrayApp."""
        return self._audio

    def toggle(self) -> None:
        """Handle hotkey press. Toggle between IDLE↔RECORDING.

        Double-tap in IDLE (within 0.8s, when we have recent typed text)
        triggers feedback capture — reads user-edited text and learns corrections.
        """
        now = time.monotonic()
        gap = now - self._last_toggle
        logger.debug("engine: toggle — current_state=%s, gap=%.3fs", self._state.value, gap)

        # Debounce: ignore if < 0.2s
        if gap < 0.2:
            logger.debug("engine: toggle — debounced (gap=%.3fs < 0.2s)", gap)
            return
        self._last_toggle = now

        with self._state_lock:
            if self._state == DictationState.IDLE:
                # Double-tap detection: if pressed within 0.8s and we have typed text
                if gap < 0.8 and self._last_normalized_text:
                    logger.info("engine: toggle — double-tap detected, starting feedback capture")
                    threading.Thread(
                        target=self._capture_feedback, name="Feedback", daemon=True
                    ).start()
                    return
                logger.debug("engine: toggle — IDLE → starting recording")
                self._start_recording()
            elif self._state == DictationState.RECORDING:
                logger.debug("engine: toggle — RECORDING → stopping recording")
                threading.Thread(target=self._stop_recording, name="StopRec", daemon=True).start()
            elif self._state in (DictationState.PROCESSING, DictationState.TYPING):
                logger.info("Ignoring toggle during processing/typing")
            elif self._state == DictationState.ERROR:
                logger.debug("engine: toggle — ERROR → resetting")
                self._reset()

    def start_if_idle(self) -> None:
        """Start recording only if currently idle. Called after hold timer (0.5s)."""
        logger.debug("engine: start_if_idle — current_state=%s", self._state.value)
        with self._state_lock:
            if self._state != DictationState.IDLE:
                logger.debug("engine: start_if_idle — not idle, ignoring")
                return
            self._start_recording()

    def cancel_recording(self) -> None:
        """Cancel recording silently (tap detected, discard audio)."""
        logger.debug("engine: cancel_recording — current_state=%s", self._state.value)
        with self._state_lock:
            if self._state == DictationState.RECORDING:
                logger.info("Recording cancelled (tap)")
                self._cleanup()
                self._set_state(DictationState.IDLE)
            else:
                logger.debug("engine: cancel_recording — not recording, ignoring")

    def stop_if_recording(self) -> None:
        """Stop recording on hold release."""
        logger.debug("engine: stop_if_recording — current_state=%s", self._state.value)
        with self._state_lock:
            if self._state == DictationState.RECORDING:
                logger.debug("engine: stop_if_recording — stopping recording")
                threading.Thread(target=self._stop_recording, name="StopRec", daemon=True).start()
            else:
                logger.debug("engine: stop_if_recording — not recording, ignoring")

    def on_tap(self) -> None:
        """Handle a tap (press < 0.5s). Called from tray_app hook.

        Increments tap counter. On second tap within 0.5s,
        if there's recent typed text → capture feedback.
        """
        self._last_tap_count = getattr(self, "_last_tap_count", 0) + 1
        logger.info("Tap #%d detected", self._last_tap_count)
        logger.debug("engine: on_tap — tap_count=%d, has_normalized_text=%s",
                      self._last_tap_count, bool(self._last_normalized_text))

        if self._last_tap_count == 1:
            # First tap — wait 0.5s for second
            def _wait_for_second_tap():
                time.sleep(0.5)
                if self._last_tap_count >= 2 and self._last_normalized_text:
                    logger.info("Double-tap confirmed — capturing feedback")
                    text_to_check = self._last_normalized_text
                    self._last_normalized_text = ""
                    self._last_tap_count = 0
                    self._capture_feedback(text_to_check)
                else:
                    if self._last_tap_count >= 2:
                        logger.info("Double-tap but no recent text to compare")
                    else:
                        logger.debug("engine: on_tap — single tap, no action taken")
                    self._last_tap_count = 0
            threading.Thread(target=_wait_for_second_tap, name="TapWait", daemon=True).start()

    def _set_state(self, new_state: DictationState) -> None:
        """Update state and notify callback."""
        old = self._state
        self._state = new_state
        logger.info("engine: state transition — %s → %s", old.value, new_state.value)
        if self._state_callback:
            try:
                logger.debug("engine: _set_state — notifying state callback")
                self._state_callback(new_state)
            except Exception as e:
                logger.error(f"State callback error: {e}")

    def _start_recording(self) -> None:
        """Begin recording session."""
        logger.info("engine: _start_recording — starting dictation session")

        # Reset session state
        self._session_text.clear()
        self._pending_results.clear()
        self._next_chunk_id = 0
        self._next_type_id = 0
        self._previous_text = ""
        self._injector.reset_counter()
        logger.debug("engine: _start_recording — session state reset")

        # Set state early so stop_if_recording can find us
        self._set_state(DictationState.RECORDING)
        self._recording_start_time = time.monotonic()

        # Validate API key early
        errors = self._config.validate()
        if errors:
            for err in errors:
                logger.error("engine: _start_recording — config error: %s", err)
            self._set_state(DictationState.ERROR)
            if self._error_callback:
                self._error_callback(errors[0])
            return

        try:
            # Get STT connector from provider manager
            stt = self._providers.get_stt()
            if stt is None:
                raise RuntimeError("No STT provider configured. Add API key in Settings → STT.")
            logger.debug("engine: _start_recording — STT provider acquired: %s", type(stt).__name__)

            # Start audio stream if not already running (kept open between recordings)
            if not self._audio.is_running:
                logger.debug("engine: _start_recording — starting audio stream")
                self._audio.start()
            else:
                logger.debug("engine: _start_recording — audio stream already running")

            # Drain stale frames from queue before starting chunk manager
            q = self._audio.get_frame_queue()
            drained = 0
            while not q.empty():
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                logger.debug("engine: _start_recording — drained %d stale frames from queue", drained)

            # Create and start chunk manager
            self._chunker = ChunkManager(
                q,
                self._config.audio,
            )
            self._chunker.start(self._on_chunk_ready)
            logger.debug("engine: _start_recording — chunk manager started")

            # Show recording overlay with waveform (non-critical)
            try:
                self._viz_queue = self._audio.add_listener_queue()
                mic_name = self._audio.get_active_device_name()
                self._overlay.show(mic_name, self._viz_queue)
                logger.debug("engine: _start_recording — overlay shown, mic=%s", mic_name)
            except Exception as e:
                logger.warning(f"Overlay failed to start (non-critical): {e}")

            # Audio feedback (non-blocking)
            if self._config.ui.sound_on_start:
                logger.debug("engine: _start_recording — playing start beep")
                threading.Thread(target=self._play_beep, args=(800, 50), daemon=True).start()

            logger.info("engine: _start_recording — recording session started successfully")

        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self._set_state(DictationState.ERROR)
            if self._error_callback:
                self._error_callback(f"Failed to start: {e}")
            self._cleanup()

    def _stop_recording(self) -> None:
        """Stop recording and begin normalization."""
        logger.info("engine: _stop_recording — stopping dictation session")

        # Hide overlay
        try:
            self._overlay.hide()
            logger.debug("engine: _stop_recording — overlay hidden")
        except Exception:
            pass

        if self._viz_queue:
            try:
                self._audio.remove_listener_queue(self._viz_queue)
            except Exception:
                pass
            self._viz_queue = None
            logger.debug("engine: _stop_recording — viz queue removed")

        # Check minimum recording duration (avoid hallucinations on short press)
        recording_duration = time.monotonic() - getattr(self, '_recording_start_time', 0)
        logger.debug("engine: _stop_recording — recording_duration=%.2fs", recording_duration)
        if recording_duration < 1.0:
            logger.info("engine: _stop_recording — too short (%.1fs < 1.0s), discarding", recording_duration)
            self._cleanup()
            self._set_state(DictationState.IDLE)
            return

        # Audio feedback
        if self._config.ui.sound_on_stop:
            logger.debug("engine: _stop_recording — playing stop beep")
            self._play_beep(frequency=600, duration=100)

        try:
            # Don't stop audio stream — keep it open for instant next recording
            # Just stop the chunk manager

            # Flush remaining audio from chunker
            if self._chunker:
                remaining = self._chunker.flush()
                if remaining:
                    logger.debug("engine: _stop_recording — flushed remaining chunk, %d bytes", len(remaining))
                    self._on_chunk_ready(remaining)
                else:
                    logger.debug("engine: _stop_recording — no remaining audio to flush")
                self._chunker.stop()
                self._chunker = None
                logger.debug("engine: _stop_recording — chunk manager stopped")

            # Wait for pending transcriptions
            self._set_state(DictationState.PROCESSING)
            logger.debug("engine: _stop_recording — waiting for pending transcriptions, chunks=%d, typed=%d",
                          self._next_chunk_id, self._next_type_id)
            self._wait_for_pending_transcriptions()

            # Concatenate all session text
            with self._session_lock:
                full_text = " ".join(self._session_text)
                logger.debug("engine: _stop_recording — joined %d segments", len(self._session_text))

            if not full_text.strip():
                logger.info("engine: _stop_recording — no speech detected in session")
                self._cleanup()
                if self._error_callback:
                    self._error_callback("No speech detected")
                self._set_state(DictationState.IDLE)
                return

            word_count = len(full_text.split())
            logger.info("engine: _stop_recording — session text ready, chars=%d, words=%d, preview='%s'",
                         len(full_text), word_count, full_text[:100])

            # Store raw text for profile learning (before normalization)
            self._session_raw_text = full_text

            if self._config.normalization.enabled:
                logger.debug("engine: _stop_recording — submitting to normalizer")
                self._normalizer.normalize_async(
                    full_text,
                    self._on_normalization_ready,
                    self._executor,
                )
            else:
                logger.debug("engine: _stop_recording — normalization disabled, skipping")
                self._cleanup()
                self._set_state(DictationState.IDLE)

        except Exception as e:
            logger.error(f"Error stopping recording: {e}")
            self._cleanup()
            self._set_state(DictationState.ERROR)
            if self._error_callback:
                self._error_callback(f"Error: {e}")

    def _on_chunk_ready(self, wav_bytes: bytes) -> None:
        """Called by ChunkManager when a speech chunk is ready."""
        chunk_id = self._next_chunk_id
        self._next_chunk_id += 1
        logger.debug("engine: _on_chunk_ready — chunk_id=%d, size=%d bytes, total_chunks=%d",
                      chunk_id, len(wav_bytes), self._next_chunk_id)

        self._executor.submit(self._process_chunk, chunk_id, wav_bytes)

    def _process_chunk(self, chunk_id: int, wav_bytes: bytes) -> None:
        """Transcribe a chunk and queue it for typing."""
        logger.debug("engine: _process_chunk — chunk_id=%d, wav_size=%d bytes", chunk_id, len(wav_bytes))
        try:
            stt = self._providers.get_stt()
            if stt is None:
                logger.error("engine: _process_chunk — no STT connector available for chunk %d", chunk_id)
                with self._typing_lock:
                    self._pending_results[chunk_id] = ""
                    self._flush_pending_typing()
                return

            stt_provider_name = type(stt).__name__
            logger.debug("engine: _process_chunk — calling STT provider=%s, chunk_id=%d, previous_text='%s'",
                          stt_provider_name, chunk_id, self._previous_text[:50] if self._previous_text else "")
            t0 = time.monotonic()
            text = stt.transcribe(wav_bytes, previous_text=self._previous_text)
            stt_latency_ms = (time.monotonic() - t0) * 1000

            if text:
                logger.info(
                    "engine: _process_chunk — STT id=%d %s %.0fms chars=%d '%s'",
                    chunk_id, stt_provider_name, stt_latency_ms, len(text), text[:80],
                )
                with self._typing_lock:
                    self._pending_results[chunk_id] = text
                    self._flush_pending_typing()
            else:
                logger.debug("engine: _process_chunk — STT empty result, chunk_id=%d, provider=%s, latency=%.0fms",
                              chunk_id, stt_provider_name, stt_latency_ms)
                with self._typing_lock:
                    # Mark as empty so ordering still works
                    self._pending_results[chunk_id] = ""
                    self._flush_pending_typing()

        except Exception as e:
            logger.error("engine: _process_chunk — error chunk_id=%d: %s", chunk_id, e)
            with self._typing_lock:
                self._pending_results[chunk_id] = ""
                self._flush_pending_typing()

    def _flush_pending_typing(self) -> None:
        """Type all consecutive pending results in order."""
        flushed_count = 0
        while self._next_type_id in self._pending_results:
            text = self._pending_results.pop(self._next_type_id)
            self._next_type_id += 1
            flushed_count += 1

            if text:
                with self._session_lock:
                    self._session_text.append(text)
                    logger.debug(
                        "engine: _flush — appended id=%d len=%d segs=%d",
                        self._next_type_id - 1, len(text), len(self._session_text),
                    )

                # Don't type raw text — wait for normalization to type final text
                self._previous_text = text
            else:
                logger.debug("engine: _flush_pending_typing — skipped empty chunk type_id=%d", self._next_type_id - 1)
        if flushed_count:
            logger.debug("engine: _flush_pending_typing — flushed %d results, pending_remaining=%d",
                          flushed_count, len(self._pending_results))

    def _wait_for_pending_transcriptions(self, timeout: float = 10.0) -> None:
        """Wait for all submitted chunks to be processed."""
        logger.debug("engine: _wait_for_pending_transcriptions — waiting, timeout=%.1fs, expected=%d, completed=%d",
                      timeout, self._next_chunk_id, self._next_type_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._typing_lock:
                if self._next_type_id >= self._next_chunk_id:
                    elapsed = timeout - (deadline - time.monotonic())
                    logger.debug("engine: _wait_for_pending_transcriptions — all done in %.2fs", elapsed)
                    return
            time.sleep(0.1)
        logger.warning("engine: _wait_for_pending_transcriptions — timed out after %.1fs, completed=%d/%d",
                         timeout, self._next_type_id, self._next_chunk_id)

    def _on_normalization_ready(self, normalized_text: str) -> None:
        """Called when normalization completes."""
        logger.info("engine: _on_normalization_ready — chars=%d, preview='%s'",
                      len(normalized_text), normalized_text[:80])

        try:
            self._set_state(DictationState.TYPING)
            # Suppress PTT during typing (SendInput keys would trigger hook)
            if self._suppress_ptt_callback:
                logger.debug("engine: _on_normalization_ready — suppressing PTT for typing")
                self._suppress_ptt_callback(True)
            logger.debug("engine: _on_normalization_ready — injecting text, length=%d", len(normalized_text))
            self._injector.replace_draft(normalized_text)
            logger.info("engine: _on_normalization_ready — text injected successfully, length=%d", len(normalized_text))
            # Store for feedback learning (double-tap)
            self._last_normalized_text = normalized_text
            self._last_typed_len = len(normalized_text)
            # Unsuppress PTT
            if self._suppress_ptt_callback:
                logger.debug("engine: _on_normalization_ready — unsuppressing PTT")
                self._suppress_ptt_callback(False)
            # Learn from this session (raw vs normalized diff)
            raw = getattr(self, "_session_raw_text", "")
            if raw and self._profile:
                try:
                    logger.debug("engine: profile record raw=%d norm=%d", len(raw), len(normalized_text))
                    self._profile.record_session(raw, normalized_text)
                    self._profile.add_history(raw, normalized_text)
                    self._maybe_optimize_prompt()
                except Exception as e:
                    logger.warning(f"Profile update failed: {e}")
            # Telemetry
            duration = time.monotonic() - self._recording_start_time if hasattr(self, "_recording_start_time") else 0
            word_count = len(normalized_text.split())
            logger.info("engine: session complete — duration=%.1fs, words=%d, chars=%d",
                         duration, word_count, len(normalized_text))
            self._telemetry.record_session(
                audio_duration_s=duration,
                latency_ms=(
                    (time.monotonic() - self._recording_start_time) * 1000
                    if hasattr(self, "_recording_start_time") else 0
                ),
                stt_model=self._config.groq.stt_model,
                llm_model=self._config.groq.llm_model,
                char_count=len(normalized_text),
            )
            logger.debug("engine: _on_normalization_ready — telemetry recorded, stt_model=%s, llm_model=%s",
                          self._config.groq.stt_model, self._config.groq.llm_model)
            # Close mic stream (opens on demand at next key press)
            if self._audio.is_running:
                try:
                    self._audio.stop()
                    logger.info("engine: _on_normalization_ready — mic stream closed")
                except Exception:
                    pass
            self._set_state(DictationState.IDLE)
        except Exception as e:
            logger.error("engine: _on_normalization_ready — error during text replacement: %s", e)
            self._cleanup()
            self._set_state(DictationState.ERROR)
            if self._error_callback:
                self._error_callback(f"Text replacement failed: {e}")

    def _capture_feedback(self, original: str = "") -> None:
        """Capture user-edited text and learn corrections from manual edits.

        Called on double-tap hotkey. Reads the text the user may have edited
        and diffs it against what we originally typed.
        """
        logger.debug("engine: _capture_feedback — entry, original_len=%d", len(original) if original else 0)
        if not original:
            logger.debug("engine: _capture_feedback — no original text, returning")
            return

        char_count = getattr(self, "_last_typed_len", 0)
        logger.info("engine: _capture_feedback — reading %d chars of user-edited text", char_count)
        # Flash icon to show feedback activated
        self._flash_icon("processing")

        # Suppress PTT hook during text grab (we send synthetic keys)
        if self._suppress_ptt_callback:
            logger.debug("engine: _capture_feedback — suppressing PTT for text grab")
            self._suppress_ptt_callback(True)

        try:
            logger.debug("engine: _capture_feedback — calling grab_typed_text, char_count=%d", char_count)
            edited = self._injector.grab_typed_text(char_count=char_count)
            if not edited:
                logger.info("engine: _capture_feedback — could not grab text (empty)")
                self._flash_icon("error")
                return

            logger.debug("engine: _capture_feedback — grabbed text, length=%d", len(edited))

            if edited.strip() == original.strip():
                logger.info("engine: _capture_feedback — text unchanged, no corrections to learn")
                self._flash_icon("unchanged")
                self._last_normalized_text = ""
                return

            # User edited the text — learn from their corrections
            logger.info(
                "engine: _capture_feedback — learning from user edits, original_chars=%d, edited_chars=%d",
                len(original), len(edited),
            )
            if self._profile:
                self._profile.record_session(original, edited, from_feedback=True)
                self._profile.update_history_edited(edited)
                logger.info("engine: _capture_feedback — corrections recorded to profile")
                self._maybe_optimize_prompt()

            self._telemetry.record_feedback(corrections_count=1)
            self._telemetry.send_profile_triads()
            logger.debug("engine: _capture_feedback — telemetry feedback recorded")
            self._flash_icon("success")

        except Exception as e:
            logger.error("engine: _capture_feedback — failed: %s", e)
            self._flash_icon("error")
        finally:
            # Always unsuppress PTT
            if self._suppress_ptt_callback:
                logger.debug("engine: _capture_feedback — unsuppressing PTT")
                self._suppress_ptt_callback(False)

    def _flash_icon(self, result: str) -> None:
        """Flash the tray icon to visually confirm feedback result.

        Flashes icon colors directly without changing engine state,
        so PTT events are not blocked during the flash.

        Args:
            result: "processing", "success", "unchanged", "error"
        """
        logger.debug("engine: _flash_icon — result=%s", result)
        cb = self._state_callback
        if not cb:
            logger.debug("engine: _flash_icon — no state callback, skipping")
            return

        def _do_flash():
            patterns = {
                "processing": [(DictationState.PROCESSING, 0.15)] * 2,
                "success":    [(DictationState.TYPING, 0.15), (DictationState.IDLE, 0.1)] * 3,
                "unchanged":  [(DictationState.IDLE, 0.15), (DictationState.PROCESSING, 0.15)] * 2,
                "error":      [(DictationState.ERROR, 0.2)] * 3,
            }
            for state, duration in patterns.get(result, []):
                try:
                    cb(state)  # update icon only, not engine state
                except Exception:
                    pass
                time.sleep(duration)
            try:
                cb(DictationState.IDLE)
            except Exception:
                pass

        threading.Thread(target=_do_flash, name="IconFlash", daemon=True).start()

    def _maybe_optimize_prompt(self) -> None:
        """Recompile prompt from profile facts (instant, no LLM calls)."""
        if not self._profile or not self._profile.needs_recompile:
            logger.debug("engine: _maybe_optimize_prompt — no recompile needed")
            return
        logger.debug("engine: _maybe_optimize_prompt — recompiling prompt from profile facts")
        self._profile.compile_prompt()
        logger.info("engine: _maybe_optimize_prompt — prompt recompiled from facts")

    def _reset(self) -> None:
        """Reset engine to IDLE state."""
        logger.info("engine: _reset — resetting from state=%s", self._state.value)
        self._cleanup()
        self._set_state(DictationState.IDLE)

    def _cleanup(self) -> None:
        """Clean up recording resources and close mic stream."""
        logger.debug("engine: _cleanup — starting resource cleanup")
        try:
            self._overlay.hide()
            logger.debug("engine: _cleanup — overlay hidden")
        except Exception:
            pass
        if self._viz_queue:
            try:
                self._audio.remove_listener_queue(self._viz_queue)
            except Exception:
                pass
            self._viz_queue = None
            logger.debug("engine: _cleanup — viz queue removed")
        if self._chunker:
            try:
                self._chunker.stop()
                logger.debug("engine: _cleanup — chunk manager stopped")
            except Exception:
                pass
            self._chunker = None
        # Close mic stream (opens on demand at next key press)
        if self._audio.is_running:
            try:
                self._audio.stop()
                logger.info("engine: _cleanup — mic stream closed")
            except Exception:
                pass
        logger.debug("engine: _cleanup — resource cleanup complete")

    def get_provider_manager(self) -> ProviderManager:
        """Access ProviderManager for Settings UI and tray."""
        logger.debug("engine: get_provider_manager — accessed")
        return self._providers

    def shutdown(self) -> None:
        """Graceful shutdown — close everything."""
        logger.info("engine: shutdown — starting graceful shutdown, current_state=%s", self._state.value)
        self._cleanup()
        try:
            self._audio.stop()
            logger.debug("engine: shutdown — audio stopped")
        except Exception:
            pass
        self._executor.shutdown(wait=False)
        logger.debug("engine: shutdown — thread pool shut down")
        self._providers.shutdown()
        logger.debug("engine: shutdown — providers shut down")
        if self._profile:
            try:
                self._profile.save(force=True)
                logger.debug("engine: shutdown — profile saved")
            except Exception:
                pass
        logger.info("engine: shutdown — complete")

    @staticmethod
    def _play_beep(frequency: int = 800, duration: int = 100) -> None:
        """Play a beep sound for audio feedback."""
        logger.debug("engine: _play_beep — frequency=%d, duration=%d", frequency, duration)
        try:
            import winsound
            winsound.Beep(frequency, duration)
        except Exception:
            pass
