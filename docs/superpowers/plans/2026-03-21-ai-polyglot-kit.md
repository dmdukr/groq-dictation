# AI Polyglot Kit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand Groq Dictation → AI Polyglot Kit with multi-provider STT/LLM support, fallback chains, and per-provider usage tracking.

**Architecture:** Provider-agnostic connector layer with abstract base classes. Each service (Groq, Soniox, Deepgram, etc.) implements a connector behind a unified interface. ProviderManager holds 3 prioritized slots per service type (STT, LLM-normalization, LLM-translation), auto-fallback on quota exhaustion. Settings UI auto-detects provider from API key prefix, fetches models via GET /models.

**Tech Stack:** Python 3.12, httpx, tkinter/sv_ttk, PyAudio, webrtcvad, PyInstaller

**Build pipeline (MUST follow after each phase):**
1. `ruff check src/ --select E,W,F --ignore E501,W291,F401`
2. `bandit -r src/ -ll --quiet`
3. `pyinstaller groq_dictation.spec --distpath C:/tmp/groq-dist --workpath C:/tmp/groq-work`
4. Copy fresh dist to `C:/tmp/dist/`, build installer with ISCC
5. Manual smoke test (launch, check tray, About, Settings)

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `src/providers.py` | Provider registry: key prefix → (name, base_url, type). Model fetcher via GET /models. |
| `src/connectors/__init__.py` | Package init |
| `src/connectors/base.py` | Abstract base: `STTConnector.transcribe(wav) → str`, `LLMConnector.chat(messages) → str` |
| `src/connectors/openai_stt.py` | OpenAI-compatible STT (Groq, OpenAI) — POST /audio/transcriptions |
| `src/connectors/openai_llm.py` | OpenAI-compatible LLM (all providers) — POST /chat/completions |
| `src/connectors/soniox_stt.py` | Soniox STT — WebSocket wss://stt-rt.soniox.com |
| `src/connectors/deepgram_stt.py` | Deepgram STT — POST /v1/listen |
| `src/connectors/gladia_stt.py` | Gladia STT — POST /v2/transcription |
| `src/connectors/speechmatics_stt.py` | Speechmatics STT — POST /v2/jobs |
| `src/connectors/assembly_stt.py` | AssemblyAI STT — POST /v2/transcript |
| `src/provider_manager.py` | 3-slot fallback manager per service type, usage tracking, duplicate key detection |

### Modified files

| File | Changes |
|------|---------|
| `src/config.py` | APP_NAME → "AIPolyglotKit", new ProviderSlot/ProvidersConfig dataclasses, remove GroqConfig dependency from STT/LLM |
| `src/engine.py` | Use ProviderManager.transcribe() instead of GroqSTT directly |
| `src/normalizer.py` | Accept LLMConnector instead of hardcoded Groq httpx client |
| `src/translate_overlay.py` | Use LLMConnector for Groq-fallback translation |
| `src/settings_ui.py` | New tabs (STT, Normalization, Translation, Interface), 3-slot provider UI, model dropdown |
| `src/tray_app.py` | Update branding, About dialog |
| `src/main.py` | Update branding |
| `src/i18n.py` | New translation keys for provider UI |
| `src/updater.py` | Update GITHUB_REPO if renamed |
| `installer.iss` | Update AppName, AppId, paths |
| `groq_dictation.spec` | Update name references |

### Deleted files (after migration)

| File | Reason |
|------|--------|
| `src/groq_stt.py` | Replaced by `src/connectors/openai_stt.py` |

---

## Phase 1: Rebrand + Provider Infrastructure

### Task 1.1: Rebrand APP_NAME and constants

**Files:**
- Modify: `src/config.py`
- Modify: `src/main.py`
- Modify: `src/tray_app.py`
- Modify: `src/i18n.py`
- Modify: `installer.iss`
- Modify: `groq_dictation.spec`

- [ ] **Step 1:** In `src/config.py` change:
  - `APP_NAME = "AIPolyglotKit"`
  - `GITHUB_REPO = "dmdukr/ai-polyglot-kit"` (or keep old until repo renamed)
  - Bump `APP_VERSION = "4.0.0"`

- [ ] **Step 2:** In `src/main.py` update log message "AI Polyglot Kit starting..."

- [ ] **Step 3:** In `src/tray_app.py` update About dialog text, menu title

- [ ] **Step 4:** In `src/i18n.py` update `tray.title` to "AI Polyglot Kit"

- [ ] **Step 5:** In `installer.iss` update `MyAppName`, `MyAppVersion`, generate new `AppId` GUID

- [ ] **Step 6:** Run ruff + bandit, verify clean

- [ ] **Step 7:** Commit: `"rebrand: GroqDictation → AI Polyglot Kit v4.0.0"`

### Task 1.2: Create provider registry

**Files:**
- Create: `src/providers.py`

- [ ] **Step 1:** Create `src/providers.py`:

```python
"""Provider registry — maps API key prefixes to service metadata."""

from __future__ import annotations
import logging
from dataclasses import dataclass
import httpx

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ProviderInfo:
    name: str
    base_url: str
    supports_stt: bool = False
    supports_llm: bool = True

# Key prefix → provider info
PROVIDER_REGISTRY: dict[str, ProviderInfo] = {
    "gsk_": ProviderInfo("Groq", "https://api.groq.com/openai/v1", supports_stt=True),
    "AIzaSy": ProviderInfo("Google AI Studio", "https://generativelanguage.googleapis.com/v1beta/openai", supports_stt=False),
    "sk-proj-": ProviderInfo("OpenAI", "https://api.openai.com/v1", supports_stt=True),
    "sk-": ProviderInfo("OpenAI", "https://api.openai.com/v1", supports_stt=True),
    "csk-": ProviderInfo("Cerebras", "https://api.cerebras.ai/v1", supports_stt=False),
    "sk-or-": ProviderInfo("OpenRouter", "https://openrouter.ai/api/v1", supports_stt=False),
    "xai-": ProviderInfo("xAI", "https://api.x.ai/v1", supports_stt=False),
    "ghp_": ProviderInfo("GitHub Models", "https://models.inference.ai.azure.com", supports_stt=False),
    "github_pat_": ProviderInfo("GitHub Models", "https://models.inference.ai.azure.com", supports_stt=False),
}

# Non-OpenAI-compatible STT providers (need specific connectors)
STT_ONLY_PROVIDERS: dict[str, ProviderInfo] = {
    "Soniox": ProviderInfo("Soniox", "https://stt-rt.soniox.com", supports_stt=True, supports_llm=False),
    "Deepgram": ProviderInfo("Deepgram", "https://api.deepgram.com/v1", supports_stt=True, supports_llm=False),
    "Gladia": ProviderInfo("Gladia", "https://api.gladia.io/v2", supports_stt=True, supports_llm=False),
    "Speechmatics": ProviderInfo("Speechmatics", "https://asr.api.speechmatics.com/v2", supports_stt=True, supports_llm=False),
    "AssemblyAI": ProviderInfo("AssemblyAI", "https://api.assemblyai.com/v2", supports_stt=True, supports_llm=False),
}

def detect_provider(api_key: str) -> ProviderInfo | None:
    """Detect provider from API key prefix. Returns None if unknown."""
    # Longer prefixes first to avoid sk- matching before sk-or- or sk-proj-
    for prefix in sorted(PROVIDER_REGISTRY, key=len, reverse=True):
        if api_key.startswith(prefix):
            return PROVIDER_REGISTRY[prefix]
    return None

def fetch_models(base_url: str, api_key: str, stt: bool = False) -> list[str]:
    """Fetch available model IDs from provider's GET /models endpoint."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                logger.warning("Failed to fetch models: %d", resp.status_code)
                return []
            data = resp.json()
            models = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                if stt:
                    # Only include whisper/audio models
                    if "whisper" in model_id.lower() or "transcri" in model_id.lower():
                        models.append(model_id)
                else:
                    # Exclude whisper/audio/embedding models
                    if not any(x in model_id.lower() for x in ("whisper", "embed", "tts", "dall")):
                        models.append(model_id)
            return sorted(models)
    except Exception as e:
        logger.warning("Cannot fetch models from %s: %s", base_url, e)
        return []
```

- [ ] **Step 2:** Run ruff + bandit, verify clean

- [ ] **Step 3:** Commit: `"feat: provider registry with key prefix detection"`

### Task 1.3: Create connector base classes

**Files:**
- Create: `src/connectors/__init__.py`
- Create: `src/connectors/base.py`

- [ ] **Step 1:** Create `src/connectors/__init__.py` (empty)

- [ ] **Step 2:** Create `src/connectors/base.py`:

```python
"""Abstract base classes for STT and LLM connectors."""

from __future__ import annotations
from abc import ABC, abstractmethod

class STTConnector(ABC):
    """Interface for speech-to-text providers."""

    @abstractmethod
    def transcribe(self, wav_bytes: bytes, language: str = "",
                   previous_text: str = "") -> str | None:
        """Transcribe WAV audio to text. Returns None on failure/silence."""

    @abstractmethod
    def get_usage(self) -> tuple[int, int]:
        """Return (used, limit) for quota display. Units vary by provider."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""

class LLMConnector(ABC):
    """Interface for LLM chat providers (normalization, translation)."""

    @abstractmethod
    def chat(self, messages: list[dict], model: str = "",
             temperature: float = 0.1, max_tokens: int = 2000) -> str | None:
        """Send chat completion request. Returns response text or None."""

    @abstractmethod
    def get_usage(self) -> tuple[int, int]:
        """Return (used_tokens, limit_tokens) for quota display."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""
```

- [ ] **Step 3:** Commit: `"feat: connector base classes (STTConnector, LLMConnector)"`

### Task 1.4: Create OpenAI-compatible STT connector

**Files:**
- Create: `src/connectors/openai_stt.py`

- [ ] **Step 1:** Create `src/connectors/openai_stt.py` — extract logic from `src/groq_stt.py` into the new STTConnector interface. Keep hallucination_filter integration. Accept `base_url` and `api_key` as constructor params instead of GroqConfig. Keep retry logic, quota tracking.

Key differences from groq_stt.py:
- Constructor takes `(base_url, api_key, model, language, temperature)` not GroqConfig
- Implements STTConnector interface
- Works for any OpenAI-compatible whisper endpoint (Groq, OpenAI)

- [ ] **Step 2:** Run ruff + bandit

- [ ] **Step 3:** Commit: `"feat: OpenAI-compatible STT connector"`

### Task 1.5: Create OpenAI-compatible LLM connector

**Files:**
- Create: `src/connectors/openai_llm.py`

- [ ] **Step 1:** Create `src/connectors/openai_llm.py`:

```python
"""OpenAI-compatible LLM connector — works with all providers."""

from __future__ import annotations
import logging
import time
import httpx
from .base import LLMConnector

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 2.0

class OpenAICompatibleLLM(LLMConnector):
    def __init__(self, base_url: str, api_key: str, default_model: str = ""):
        self._base_url = base_url
        self._api_key = api_key
        self._default_model = default_model
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        self._tokens_used = 0
        self._tokens_limit = 0

    def chat(self, messages, model="", temperature=0.1, max_tokens=2000):
        model = model or self._default_model
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._http.post("/chat/completions", json={
                    "model": model, "messages": messages,
                    "temperature": temperature, "max_tokens": max_tokens,
                })
                if resp.status_code == 429:
                    time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                    continue
                if resp.status_code == 401:
                    logger.error("LLM auth failed")
                    return None
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                self._tokens_used += usage.get("total_tokens", 0)
                return data["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.warning("LLM error (attempt %d): %s", attempt + 1, e)
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
        return None

    def get_usage(self):
        return (self._tokens_used, self._tokens_limit)

    def close(self):
        self._http.close()
```

- [ ] **Step 2:** Run ruff + bandit

- [ ] **Step 3:** Commit: `"feat: OpenAI-compatible LLM connector"`

### Task 1.6: New config structure for provider slots

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1:** Add new dataclasses to `src/config.py`:

```python
@dataclass
class ProviderSlot:
    api_key: str = ""
    provider: str = ""       # auto-detected or user-selected
    base_url: str = ""       # auto-resolved or manual
    model: str = ""          # selected model
    enabled: bool = True

@dataclass
class ProvidersConfig:
    stt: list[ProviderSlot] = field(default_factory=lambda: [
        ProviderSlot(),  # slot 1
        ProviderSlot(),  # slot 2
        ProviderSlot(),  # slot 3
    ])
    llm: list[ProviderSlot] = field(default_factory=lambda: [
        ProviderSlot(),  # slot 1
        ProviderSlot(),  # slot 2
        ProviderSlot(),  # slot 3
    ])
    translation: list[ProviderSlot] = field(default_factory=lambda: [
        ProviderSlot(),  # slot 1
        ProviderSlot(),  # slot 2
        ProviderSlot(),  # slot 3
    ])
```

- [ ] **Step 2:** Add `providers: ProvidersConfig` to AppConfig, keep `groq: GroqConfig` for backward compatibility during migration

- [ ] **Step 3:** Update `_apply_dict` and `to_dict` for nested list[ProviderSlot] serialization

- [ ] **Step 4:** Run ruff + bandit

- [ ] **Step 5:** Commit: `"feat: ProviderSlot config structure"`

### Task 1.7: Provider manager with fallback

**Files:**
- Create: `src/provider_manager.py`

- [ ] **Step 1:** Create `src/provider_manager.py` — manages 3 slots, creates connectors, handles fallback:

Key logic:
- `get_stt_connector() → STTConnector` — returns first working slot
- `get_llm_connector(service_type) → LLMConnector` — for normalization or translation
- `on_quota_exhausted(slot_idx)` — marks slot as exhausted, returns next
- `check_duplicate_keys(slots) → list[str]` — returns error messages if duplicates found

- [ ] **Step 2:** Run ruff + bandit

- [ ] **Step 3:** Commit: `"feat: ProviderManager with 3-slot fallback"`

### Task 1.8: Wire engine.py to use ProviderManager

**Files:**
- Modify: `src/engine.py`
- Modify: `src/normalizer.py`

- [ ] **Step 1:** In `engine.py`: replace `GroqSTT` usage with `provider_manager.get_stt_connector()`. Keep hallucination filter integration.

- [ ] **Step 2:** In `normalizer.py`: accept `LLMConnector` instead of building its own httpx client. Constructor: `__init__(self, llm: LLMConnector, norm_config, profile)`

- [ ] **Step 3:** Run ruff + bandit

- [ ] **Step 4:** Commit: `"refactor: engine + normalizer use connectors"`

### Task 1.9: Extract Interface tab in settings_ui.py

**Files:**
- Modify: `src/settings_ui.py`
- Modify: `src/i18n.py`

- [ ] **Step 1:** In `src/i18n.py` add keys: `settings.tab_interface`, `settings.tab_stt`, `settings.tab_normalization`, `settings.tab_translation`

- [ ] **Step 2:** In `src/settings_ui.py`: move UI-related settings (language, theme, autostart, sounds, notifications) from Dictation tab to new Interface tab

- [ ] **Step 3:** Run ruff + bandit

- [ ] **Step 4:** Commit: `"feat: Interface tab extracted from Dictation"`

### Phase 1 Gate

- [ ] Run full build pipeline (ruff → bandit → PyInstaller → ISCC)
- [ ] Manual smoke test: launch, About shows "AI Polyglot Kit v4.0.0", Settings opens, all tabs work
- [ ] Commit + tag `v4.0.0-alpha.1`

---

## Phase 2: Settings UI — Provider Slots

### Task 2.1: STT providers tab with 3 slots

**Files:**
- Modify: `src/settings_ui.py`
- Modify: `src/i18n.py`

- [ ] **Step 1:** Add new i18n keys for provider slot UI

- [ ] **Step 2:** Build STT tab with 3 provider slots, each having:
  - `#N` label
  - API Key entry (show/hide)
  - Provider combobox (auto-detected or user picks)
  - Model combobox (populated via GET /models after key entered)
  - Usage label (e.g. "185h/2000h")
  - Info text: "При вичерпанні лімітів #1 → #2 → #3"
  - Link: "Рекомендовані сервіси" → GitHub README

- [ ] **Step 3:** On API key change: detect provider → set combobox → fetch models → populate model combobox

- [ ] **Step 4:** Duplicate key detection: if same key in 2+ slots → show warning, disable Save

- [ ] **Step 5:** Run ruff + bandit

- [ ] **Step 6:** Commit: `"feat: STT provider slots UI (3 slots with fallback)"`

### Task 2.2: LLM normalization tab with 3 slots

**Files:**
- Modify: `src/settings_ui.py`

- [ ] **Step 1:** Same 3-slot pattern as STT but for LLM. Provider combobox only shows LLM-capable providers. Model list filters out whisper/embed models.

- [ ] **Step 2:** Keep normalization toggle and prompt settings below the slots

- [ ] **Step 3:** Commit: `"feat: Normalization provider slots UI"`

### Task 2.3: Translation tab with 3 LLM slots + DeepL

**Files:**
- Modify: `src/settings_ui.py`
- Modify: `src/translate_overlay.py`

- [ ] **Step 1:** Translation tab: 3 LLM slots (same pattern) + existing DeepL keys section below

- [ ] **Step 2:** Update `translate_overlay.py` to use LLM connector from ProviderManager instead of hardcoded Groq client

- [ ] **Step 3:** Commit: `"feat: Translation provider slots + DeepL migration"`

### Task 2.4: Save/Load provider config

**Files:**
- Modify: `src/settings_ui.py`
- Modify: `src/config.py`

- [ ] **Step 1:** Wire Save button to persist all provider slots to config.yaml (API keys to .env or APPDATA)

- [ ] **Step 2:** Wire Load to populate slots from saved config on Settings open

- [ ] **Step 3:** Backward compatibility: if old `groq.api_key` exists and no providers configured, auto-migrate to providers.stt[0] + providers.llm[0]

- [ ] **Step 4:** Commit: `"feat: save/load provider slots config"`

### Phase 2 Gate

- [ ] Run full build pipeline
- [ ] Manual test: add Groq key to STT slot #1, verify model dropdown populates, save, restart, verify settings persist
- [ ] Test duplicate key warning
- [ ] Commit + tag `v4.0.0-alpha.2`

---

## Phase 3: Soniox + Deepgram STT Connectors

### Task 3.1: Soniox STT connector

**Files:**
- Create: `src/connectors/soniox_stt.py`

- [ ] **Step 1:** Implement SonioxSTT(STTConnector) — WebSocket-based real-time transcription. Endpoint: `wss://stt-rt.soniox.com/transcribe-websocket`. Send WAV audio, receive JSON with transcription.

- [ ] **Step 2:** Register in provider_manager's connector factory

- [ ] **Step 3:** Commit: `"feat: Soniox STT connector"`

### Task 3.2: Deepgram STT connector

**Files:**
- Create: `src/connectors/deepgram_stt.py`

- [ ] **Step 1:** Implement DeepgramSTT(STTConnector) — REST POST to `/v1/listen`. Headers: `Authorization: Token <key>`. Query params: `model=nova-3&language=uk`.

- [ ] **Step 2:** Register in provider_manager

- [ ] **Step 3:** Commit: `"feat: Deepgram STT connector"`

### Phase 3 Gate

- [ ] Run full build pipeline
- [ ] Test Soniox with real API key (if available)
- [ ] Test Deepgram with real API key (if available)
- [ ] Test fallback: Groq → Soniox when Groq quota exhausted
- [ ] Commit + tag `v4.0.0-alpha.3`

---

## Phase 4: Gladia + Speechmatics + AssemblyAI Connectors

### Task 4.1: Gladia STT connector

**Files:**
- Create: `src/connectors/gladia_stt.py`

- [ ] **Step 1:** Implement GladiaSTT — POST `/v2/pre-recorded` with audio file upload. Auth: `x-gladia-key: <key>`.

- [ ] **Step 2:** Commit: `"feat: Gladia STT connector"`

### Task 4.2: Speechmatics STT connector

**Files:**
- Create: `src/connectors/speechmatics_stt.py`

- [ ] **Step 1:** Implement SpeechmaticsSTT — POST `/v2/jobs` with audio + config JSON. Auth: `Authorization: Bearer <key>`.

- [ ] **Step 2:** Commit: `"feat: Speechmatics STT connector"`

### Task 4.3: AssemblyAI STT connector

**Files:**
- Create: `src/connectors/assembly_stt.py`

- [ ] **Step 1:** Implement AssemblySTT — upload audio → POST `/v2/transcript` → poll GET `/v2/transcript/{id}` until completed. Auth: `authorization: <key>`.

- [ ] **Step 2:** Commit: `"feat: AssemblyAI STT connector"`

### Phase 4 Gate

- [ ] Run full build pipeline
- [ ] Test each new connector with real keys (if available)
- [ ] Commit + tag `v4.0.0-beta.1`

---

## Phase 5: Delete legacy + final release

### Task 5.1: Remove groq_stt.py and old GroqConfig dependencies

- [ ] **Step 1:** Delete `src/groq_stt.py` — all callers now use connectors
- [ ] **Step 2:** Remove `groq` field from AppConfig (keep only `providers`)
- [ ] **Step 3:** Clean up engine.py, normalizer.py from any remaining Groq-specific code
- [ ] **Step 4:** Update README with recommended services table + setup guide

### Task 5.2: Final build + release

- [ ] Run full build pipeline
- [ ] Full smoke test all features
- [ ] Bump version to `v4.0.0`
- [ ] Commit, tag, push, `gh release create v4.0.0`

---

## Migration Path (backward compatibility)

On first launch with old config:
1. If `groq.api_key` exists and `providers.stt[0].api_key` is empty:
   - Copy `groq.api_key` → `providers.stt[0].api_key`
   - Set `providers.stt[0].provider = "Groq"`
   - Set `providers.stt[0].model = groq.stt_model`
   - Copy same key → `providers.llm[0].api_key`
   - Set `providers.llm[0].model = groq.llm_model`
2. If DeepL keys exist: keep in translate_settings.json alongside new LLM translation slots
3. Log migration: "Migrated Groq config → provider slots"
