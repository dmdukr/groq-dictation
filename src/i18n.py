"""Internationalization — UI string localization (UA/EN)."""

from __future__ import annotations

_STRINGS: dict[str, dict[str, str]] = {
    # ── Tray menu ────────────────────────────────────────────────
    "tray.ready": {
        "en": "Ready",
        "uk": "Готово",
    },
    "tray.recording": {
        "en": "Recording...",
        "uk": "Запис...",
    },
    "tray.processing": {
        "en": "Processing...",
        "uk": "Обробка...",
    },
    "tray.typing": {
        "en": "Typing...",
        "uk": "Введення...",
    },
    "tray.error": {
        "en": "Error",
        "uk": "Помилка",
    },
    "tray.settings": {
        "en": "Settings",
        "uk": "Налаштування",
    },
    "tray.open_profile": {
        "en": "Open Profile",
        "uk": "Відкрити профіль",
    },
    "tray.open_logs": {
        "en": "Open Logs",
        "uk": "Відкрити логи",
    },
    "tray.quit": {
        "en": "Quit",
        "uk": "Вихід",
    },
    "tray.title": {
        "en": "Groq Dictation",
        "uk": "Groq Dictation",
    },
    "tray.mic": {
        "en": "Mic",
        "uk": "Мік",
    },

    # ── Notifications ────────────────────────────────────────────
    "notify.no_speech": {
        "en": "No speech detected",
        "uk": "Мовлення не виявлено",
    },
    "notify.too_short": {
        "en": "Recording too short",
        "uk": "Запис занадто короткий",
    },
    "notify.api_error": {
        "en": "API error",
        "uk": "Помилка API",
    },
    "notify.feedback_ok": {
        "en": "Correction saved",
        "uk": "Виправлення збережено",
    },
    "notify.feedback_empty": {
        "en": "Could not read text",
        "uk": "Не вдалося прочитати текст",
    },
    "notify.feedback_same": {
        "en": "Text unchanged",
        "uk": "Текст не змінено",
    },
    "notify.update_available": {
        "en": "Update available: v{version}",
        "uk": "Доступне оновлення: v{version}",
    },
    "notify.already_running": {
        "en": "Groq Dictation is already running.",
        "uk": "Groq Dictation вже запущено.",
    },

    # ── Settings window ──────────────────────────────────────────
    "settings.title": {
        "en": "Groq Dictation — Settings",
        "uk": "Groq Dictation — Налаштування",
    },
    "settings.tab_api": {
        "en": "API",
        "uk": "API",
    },
    "settings.tab_audio": {
        "en": "Audio",
        "uk": "Аудіо",
    },
    "settings.tab_normalization": {
        "en": "Normalization",
        "uk": "Нормалізація",
    },
    "settings.tab_ui": {
        "en": "Interface",
        "uk": "Інтерфейс",
    },
    "settings.api_key": {
        "en": "API Key:",
        "uk": "API Ключ:",
    },
    "settings.stt_model": {
        "en": "STT Model:",
        "uk": "STT Модель:",
    },
    "settings.llm_model": {
        "en": "LLM Model:",
        "uk": "LLM Модель:",
    },
    "settings.language": {
        "en": "Language:",
        "uk": "Мова:",
    },
    "settings.hotkey": {
        "en": "Hotkey:",
        "uk": "Гаряча клавіша:",
    },
    "settings.mic_device": {
        "en": "Microphone:",
        "uk": "Мікрофон:",
    },
    "settings.normalization_enabled": {
        "en": "Enable normalization",
        "uk": "Увімкнути нормалізацію",
    },
    "settings.known_terms": {
        "en": "Known terms:",
        "uk": "Відомі терміни:",
    },
    "settings.save": {
        "en": "Save",
        "uk": "Зберегти",
    },
    "settings.cancel": {
        "en": "Cancel",
        "uk": "Скасувати",
    },
    "settings.saved_ok": {
        "en": "Settings saved. Restart to apply.",
        "uk": "Налаштування збережено. Перезапустіть для застосування.",
    },
    "settings.ui_language": {
        "en": "UI Language:",
        "uk": "Мова інтерфейсу:",
    },
    "settings.sound_on_start": {
        "en": "Beep on recording start",
        "uk": "Звук на початку запису",
    },
    "settings.profile_path": {
        "en": "Profile:",
        "uk": "Профіль:",
    },

    # ── Overlay ──────────────────────────────────────────────────
    "overlay.rec": {
        "en": "REC",
        "uk": "ЗАП",
    },
}

# Current language
_current_lang: str = "uk"


def set_language(lang: str) -> None:
    """Set UI language ('uk' or 'en')."""
    global _current_lang
    _current_lang = lang if lang in ("uk", "en") else "uk"


def get_language() -> str:
    """Get current UI language."""
    return _current_lang


def t(key: str, **kwargs) -> str:
    """Get translated string by key. Supports {placeholder} formatting."""
    entry = _STRINGS.get(key)
    if not entry:
        return key

    text = entry.get(_current_lang) or entry.get("en") or key
    if kwargs:
        text = text.format(**kwargs)
    return text
