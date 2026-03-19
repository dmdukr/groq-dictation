# Groq Dictation — Windows Speech-to-Text Service

System-wide dictation for Windows using Groq Whisper API with AI-powered text normalization and self-learning user profile.

> 🤖 Built by **dmdukr** with [Claude](https://claude.ai) (Anthropic) as AI co-author — architecture, code, and documentation were developed collaboratively.

---

*[Українська версія нижче / Ukrainian version below](#groq-dictation--windows-сервіс-голосового-введення)*

---

## Features

- **Hold-to-record** — hold hotkey to record, release to stop
- **Groq Whisper** — fast cloud STT via Groq API (free tier available)
- **AI normalization** — two-pass LLM post-processing fixes Whisper errors, restores idioms, formats text
- **Self-learning profile** — learns from your corrections via double-tap feedback
- **Prompt tournament** — auto-optimizes the normalization prompt (3 candidates + judge)
- **Multilingual** — Ukrainian, English in any mix
- **Auto-update** — checks GitHub releases, downloads and installs new versions
- **System tray** — runs silently, shows recording overlay with waveform

## How it works

```
🎤 Hold hotkey → Record speech
    ↓
☁️ Groq Whisper API → Raw transcription
    ↓
🤖 LLM Pass 1 → Fix recognition errors (uses compiled prompt from profile)
    ↓
🤖 LLM Pass 2 → Polish grammar
    ↓
⌨️ Type into active window
    ↓
✏️ User edits text → Double-tap hotkey → Feedback captured
    ↓
🧠 Profile updated → Prompt re-optimized (tournament)
```

## Installation

### Option 1: Installer (recommended)

1. Download `GroqDictation-X.X.X-setup.exe` from [Releases](https://github.com/dmdukr/groq-dictation/releases)
2. Run the installer
3. Get a free API key at [console.groq.com](https://console.groq.com)
4. On first launch, enter your API key in the settings window

### Option 2: From source

```bash
git clone https://github.com/dmdukr/groq-dictation.git
cd groq-dictation
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

## Configuration

Settings are stored in `%APPDATA%\GroqDictation\config.yaml`.

Right-click the tray icon → **Settings** to open the configuration window.

| Setting | Description | Default |
|---------|-------------|---------|
| `groq.api_key` | Groq API key ([get one free](https://console.groq.com)) | — |
| `groq.stt_model` | Whisper model | `whisper-large-v3-turbo` |
| `groq.llm_model` | LLM for normalization | `llama-3.3-70b-versatile` |
| `hotkey` | Hold-to-record hotkey | `f12` |
| `audio.mic_device_index` | Microphone device index (null = auto) | `null` |
| `normalization.enabled` | Enable AI post-processing | `true` |

## Usage

| Action | How |
|--------|-----|
| **Record** | Hold the hotkey (default: `F12`) — speak — release |
| **Feedback** | After text is typed, edit it, then double-tap the hotkey |
| **Settings** | Right-click tray icon → Settings |
| **Profile** | Right-click tray icon → Open Profile |
| **Quit** | Right-click tray icon → Quit |

## Self-learning system

The app maintains a user profile at `%APPDATA%\GroqDictation\user_profile.md`.

### Profile structure

```
📄 user_profile.md
├── ## Meta          — session count, language mix
├── ## Rules         — auto-generated behavioral rules
├── ## Corrections   — wrong → right pairs (auto + feedback)
├── ## Vocabulary    — frequent domain terms
├── ## History       — triads: raw → normalized → user-edited
└── ## Compiled Prompt — tournament-winning system prompt
```

### How learning works

1. **Auto-diff** — after each session, diffs raw Whisper output vs LLM normalized text → learns Whisper error patterns
2. **Feedback** — user edits text, double-taps → diffs normalized vs user-edited → learns user preferences
3. **Rule compiler** — detects patterns in corrections → generates rules (e.g., "never translate English words")
4. **Prompt tournament** — 3 LLM sessions generate candidate prompts from triads (raw/normalized/edited) → 4th session picks the best one

### Conflict resolution

- Feedback corrections always override auto-corrections
- If a reverse correction exists (A→B and B→A), the newer one wins
- Rules are re-compiled after every update

## Architecture

```
src/
├── main.py              — entry point, single-instance, GC workaround
├── config.py            — YAML config, dataclasses
├── engine.py            — state machine (idle → recording → processing → typing)
├── audio_capture.py     — PyAudio callback, auto device selection, gain calibration
├── chunk_manager.py     — VAD-based chunking, silence detection
├── groq_stt.py          — Whisper API client (httpx), retry, hallucination filter
├── hallucination_filter.py — multi-layer filter (RMS, logprob, blocklist, n-gram)
├── normalizer.py        — two-pass LLM normalization
├── text_injector.py     — keyboard simulation (pynput + win32 SendInput)
├── user_profile.py      — MD-based profile, rule compiler, prompt tournament
├── recording_overlay.py — tkinter waveform overlay
├── tray_app.py          — pystray system tray, hotkey handling (hold/tap)
├── updater.py           — GitHub release checker, auto-download
└── settings_ui.py       — tkinter settings window
```

## Building

### EXE

```bash
pip install pyinstaller
pyinstaller groq_dictation.spec
# Output: dist/GroqDictation.exe
```

### Installer

Requires [Inno Setup 6](https://jrsoftware.org/isinfo.php):

```bash
iscc installer.iss
# Output: installer_output/GroqDictation-X.X.X-setup.exe
```

## Bug reports

Found a bug? Open an issue:

**[github.com/dmdukr/groq-dictation/issues](https://github.com/dmdukr/groq-dictation/issues)**

Please include:

| Field | Where to find |
|-------|---------------|
| **App version** | Tray icon tooltip |
| **Windows version** | Settings → System → About |
| **Logs** | `%APPDATA%\GroqDictation\logs\groq-dictation.log` |
| **Profile** | `%APPDATA%\GroqDictation\user_profile.md` |

---

# Groq Dictation — Windows сервіс голосового введення

Системний голосовий ввід для Windows через Groq Whisper API з AI-нормалізацією та самонавчальним профілем.

> 🤖 Створено **dmdukr** за участі [Claude](https://claude.ai) (Anthropic) як AI-співавтора — архітектура, код та документація розроблені спільно.

---

## Можливості

- **Утримання для запису** — утримуй гарячу клавішу для запису, відпусти для зупинки
- **Groq Whisper** — швидке хмарне розпізнавання через Groq API (є безкоштовний план)
- **AI-нормалізація** — двопрохідна LLM обробка виправляє помилки Whisper, відновлює ідіоми, форматує текст
- **Самонавчальний профіль** — вчиться з ваших виправлень через подвійний тап
- **Турнір промптів** — автоматично оптимізує промпт нормалізації (3 кандидати + суддя)
- **Мультимовність** — українська, англійська у будь-якому міксі
- **Автооновлення** — перевіряє релізи на GitHub, завантажує та встановлює нові версії
- **Системний трей** — працює тихо, показує оверлей запису з формою хвилі

## Встановлення

### Варіант 1: Інсталятор (рекомендовано)

1. Завантажте `GroqDictation-X.X.X-setup.exe` з [Releases](https://github.com/dmdukr/groq-dictation/releases)
2. Запустіть інсталятор
3. Отримайте безкоштовний API ключ на [console.groq.com](https://console.groq.com)
4. При першому запуску введіть API ключ у вікні налаштувань

### Варіант 2: З вихідного коду

```bash
git clone https://github.com/dmdukr/groq-dictation.git
cd groq-dictation
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

## Використання

| Дія | Як |
|-----|-----|
| **Запис** | Утримуйте гарячу клавішу (за замовчуванням: `Alt Gr`) — говоріть — відпустіть |
| **Зворотній зв'язок** | Після введення тексту відредагуйте його, потім двічі натисніть гарячу клавішу |
| **Налаштування** | Правий клік на іконку в треї → Settings |
| **Профіль** | Правий клік на іконку в треї → Open Profile |
| **Вихід** | Правий клік на іконку в треї → Quit |

## Система самонавчання

Додаток веде профіль користувача у `%APPDATA%\GroqDictation\user_profile.md`.

### Як працює навчання

1. **Авто-diff** — після кожної сесії порівнює вихід Whisper з нормалізованим текстом LLM → вивчає патерни помилок
2. **Зворотній зв'язок** — користувач редагує текст, робить подвійний тап → порівнює нормалізований з відредагованим → вивчає вподобання
3. **Компілятор правил** — виявляє патерни у виправленнях → генерує правила
4. **Турнір промптів** — 3 LLM сесії генерують кандидатів з тріад (raw/normalized/edited) → 4-та обирає найкращий

### Вирішення конфліктів

- Виправлення від зворотнього зв'язку завжди мають пріоритет
- Якщо існує зворотне виправлення (A→B та B→A) — новіше перемагає
- Правила перекомпілюються після кожного оновлення

---

## Повідомлення про помилки

Знайшли баг? Відкрийте issue:

**[github.com/dmdukr/groq-dictation/issues](https://github.com/dmdukr/groq-dictation/issues)**

Будь ласка, вкажіть:

| Поле | Де знайти |
|------|-----------|
| **Версія додатку** | Тултіп іконки в треї |
| **Версія Windows** | Налаштування → Система → Про систему |
| **Логи** | `%APPDATA%\GroqDictation\logs\groq-dictation.log` |
| **Профіль** | `%APPDATA%\GroqDictation\user_profile.md` |

---

## Privacy Policy

Groq Dictation collects **anonymous usage statistics** to improve the application. This can be disabled in Settings → Telemetry.

### What is collected
- Session count, audio duration, latency (aggregated numbers only)
- STT/LLM model names used
- Hallucination filter hit rates
- App version, OS version

### What is NOT collected
- No speech audio
- No transcribed text
- No API keys or credentials
- No personal information
- No IP addresses (Amplitude handles anonymization)

### Data destination
Anonymous events are sent to [Amplitude](https://amplitude.com) analytics. No data is sold or shared with third parties.

### Opt-out
Disable telemetry in Settings → Telemetry tab, or set `telemetry.enabled: false` in `config.yaml`.

---

## License

[GNU GPL v3](LICENSE) — Copyright (c) 2026 Dmytro Dubinko

Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org)
