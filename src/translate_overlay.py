"""Quick-translate overlay — triggered by double Ctrl+C.

Shows clipboard text translated to selected language in a floating overlay.
2026 design: elevated neutrals, warm tones, frosted glass effect.
"""

import json
import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
import httpx
import pyperclip

from .config import GroqConfig, APP_DIR
from .i18n import t

logger = logging.getLogger(__name__)

# ── Color Scheme (2026 Material Elevated Neutrals) ────────────────────

BG_PRIMARY = "#2b2d31"       # warm dark grey (not pure black)
BG_SURFACE = "#383a40"       # elevated surface
BG_CARD = "#404249"          # card/input background
ACCENT = "#5dadec"           # soft sky blue
ACCENT_HOVER = "#7abfff"     # lighter blue hover
TEXT_PRIMARY = "#f2f3f5"     # warm white
TEXT_SECONDARY = "#b5bac1"   # muted text
TEXT_DIM = "#80848e"         # dimmed text
SUCCESS = "#57d59f"          # mint green
DANGER = "#ed4245"           # soft red
BORDER = "#4e5058"           # subtle border

# ── Languages ─────────────────────────────────────────────────────────

LANGUAGES = [
    ("English", "en"),
    ("Ukrainian", "uk"),
    ("Russian", "ru"),
    ("German", "de"),
    ("French", "fr"),
    ("Spanish", "es"),
    ("Polish", "pl"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Japanese", "ja"),
    ("Chinese", "zh"),
    ("Korean", "ko"),
    ("Turkish", "tr"),
    ("Arabic", "ar"),
]

TRANSLATE_PROMPT = """\
Translate the following text to {language}.
Return ONLY the translation, no explanations or commentary.
Preserve formatting, line breaks, and punctuation style."""

# Persisted settings
_SETTINGS_FILE = APP_DIR / "translate_settings.json"


def _load_settings() -> dict:
    """Load all translate settings."""
    try:
        if _SETTINGS_FILE.exists():
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_settings(updates: dict) -> None:
    """Merge updates into translate settings file."""
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = _load_settings()
        data.update(updates)
        _SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


class TranslateOverlay:
    """Floating overlay window for quick translation."""

    def __init__(self, groq_config: GroqConfig):
        self._groq = groq_config
        self._window: tk.Tk | None = None
        self._thread: threading.Thread | None = None
        self._source_text = ""
        self._target_lang = _load_settings().get("target_lang", "en")
        self._deepl_rotation_idx = 0

    def show(self, text: str) -> None:
        """Show overlay with text to translate."""
        if not text.strip():
            return

        self._source_text = text.strip()

        # Close existing overlay if open
        self.hide()

        self._thread = threading.Thread(target=self._build_and_run, daemon=True)
        self._thread.start()

    def hide(self) -> None:
        """Close overlay window."""
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None

    def _build_and_run(self) -> None:
        try:
            root = tk.Tk()
            self._window = root
            root.title("Groq Translate")
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.96)
            root.configure(bg=BG_PRIMARY)
            root.minsize(400, 300)

            # Load saved size or use defaults
            settings = _load_settings()
            w = settings.get("window_w", 620)
            h = settings.get("window_h", 420)
            root.update_idletasks()
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            x = (screen_w - w) // 2
            y = (screen_h - h) // 2
            root.geometry(f"{w}x{h}+{x}+{y}")

            # Save size on close
            def on_close():
                try:
                    _save_settings({
                        "window_w": root.winfo_width(),
                        "window_h": root.winfo_height(),
                    })
                except Exception:
                    pass
                root.destroy()

            root.protocol("WM_DELETE_WINDOW", on_close)

            # Main frame with padding
            frame = tk.Frame(root, bg=BG_PRIMARY, padx=20, pady=14)
            frame.pack(fill="both", expand=True)

            # ── Header ──────────────────────────────────────────────
            header = tk.Frame(frame, bg=BG_PRIMARY)
            header.pack(fill="x", pady=(0, 12))

            # Title
            tk.Label(
                header, text="Translate",
                fg=ACCENT, bg=BG_PRIMARY, font=("Segoe UI", 13, "bold"),
            ).pack(side="left")

            # Close button
            close_btn = tk.Label(
                header, text="  \u2715  ", fg=TEXT_DIM, bg=BG_SURFACE,
                font=("Segoe UI", 10), cursor="hand2",
            )
            close_btn.pack(side="right", padx=(8, 0))
            close_btn.bind("<Button-1>", lambda e: on_close())
            close_btn.bind("<Enter>", lambda e: close_btn.config(fg=DANGER, bg=BG_CARD))
            close_btn.bind("<Leave>", lambda e: close_btn.config(fg=TEXT_DIM, bg=BG_SURFACE))

            # Replace button — paste translation over original selected text
            replace_btn = tk.Label(
                header, text="  Replace  ", fg=TEXT_SECONDARY, bg=BG_SURFACE,
                font=("Segoe UI", 9), cursor="hand2",
            )
            replace_btn.pack(side="right", padx=(6, 0))
            replace_btn.bind("<Enter>", lambda e: replace_btn.config(fg=ACCENT_HOVER, bg=BG_CARD))
            replace_btn.bind("<Leave>", lambda e: replace_btn.config(fg=TEXT_SECONDARY, bg=BG_SURFACE))

            # Copy button
            copy_btn = tk.Label(
                header, text="  Copy  ", fg=TEXT_SECONDARY, bg=BG_SURFACE,
                font=("Segoe UI", 9), cursor="hand2",
            )
            copy_btn.pack(side="right", padx=(6, 0))
            copy_btn.bind("<Enter>", lambda e: copy_btn.config(fg=SUCCESS, bg=BG_CARD))
            copy_btn.bind("<Leave>", lambda e: copy_btn.config(fg=TEXT_SECONDARY, bg=BG_SURFACE))

            # Language selector
            lang_frame = tk.Frame(header, bg=BG_PRIMARY)
            lang_frame.pack(side="right", padx=(6, 0))

            tk.Label(
                lang_frame, text="\u2192", fg=TEXT_DIM, bg=BG_PRIMARY,
                font=("Segoe UI", 12),
            ).pack(side="left", padx=(0, 6))

            lang_var = tk.StringVar()
            lang_names = [name for name, code in LANGUAGES]
            lang_combo = ttk.Combobox(
                lang_frame, textvariable=lang_var, values=lang_names,
                width=12, state="readonly",
            )
            # Set saved language
            for i, (name, code) in enumerate(LANGUAGES):
                if code == self._target_lang:
                    lang_combo.current(i)
                    break
            lang_combo.pack(side="left")

            # ── Source text ─────────────────────────────────────────
            src_label = tk.Label(
                frame, text="Original", fg=TEXT_DIM, bg=BG_PRIMARY,
                font=("Segoe UI", 8), anchor="w",
            )
            src_label.pack(fill="x", pady=(0, 2))

            src_frame = tk.Frame(frame, bg=BG_SURFACE, padx=10, pady=8)
            src_frame.pack(fill="x", pady=(0, 10))

            src_text = tk.Text(
                src_frame, height=4, wrap="word",
                fg=TEXT_SECONDARY, bg=BG_SURFACE, font=("Segoe UI", 10),
                borderwidth=0, highlightthickness=0, selectbackground=ACCENT,
            )
            src_text.insert("1.0", self._source_text[:1000])
            src_text.config(state="disabled")
            src_text.pack(fill="x")

            # ── Translation result ──────────────────────────────────
            result_label = tk.Label(
                frame, text="Translation", fg=TEXT_DIM, bg=BG_PRIMARY,
                font=("Segoe UI", 8), anchor="w",
            )
            result_label.pack(fill="x", pady=(0, 2))

            result_frame = tk.Frame(frame, bg=BG_SURFACE, padx=10, pady=8)
            result_frame.pack(fill="both", expand=True)

            result_text = tk.Text(
                result_frame, wrap="word",
                fg=TEXT_PRIMARY, bg=BG_SURFACE, font=("Segoe UI", 11),
                borderwidth=0, highlightthickness=0, selectbackground=ACCENT,
            )
            result_text.insert("1.0", t("translate.loading"))
            result_text.config(state="disabled")
            result_text.pack(fill="both", expand=True)

            # ── Status bar ──────────────────────────────────────────
            status_var = tk.StringVar(value="")
            tk.Label(
                frame, textvariable=status_var,
                fg=TEXT_DIM, bg=BG_PRIMARY, font=("Segoe UI", 8), anchor="w",
            ).pack(fill="x", pady=(6, 0))

            # ── Handlers ────────────────────────────────────────────

            def do_copy(event=None):
                result_text.config(state="normal")
                text = result_text.get("1.0", "end").strip()
                result_text.config(state="disabled")
                if text and text != t("translate.loading"):
                    pyperclip.copy(text)
                    status_var.set(t("translate.copied"))
                    copy_btn.config(fg=SUCCESS)

            copy_btn.bind("<Button-1>", do_copy)

            def do_replace(event=None):
                """Replace original selected text with translation."""
                result_text.config(state="normal")
                translated = result_text.get("1.0", "end").strip()
                result_text.config(state="disabled")
                if not translated or translated == t("translate.loading"):
                    return

                # Copy translation to clipboard
                pyperclip.copy(translated)

                # Close overlay first
                on_close()

                # Small delay, then paste (Ctrl+V) over the still-selected text
                import ctypes
                time.sleep(0.2)

                user32 = ctypes.windll.user32
                VK_CONTROL = 0x11
                VK_V = 0x56
                KEYEVENTF_KEYUP = 0x0002

                # Ctrl+V
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_V, 0, 0, 0)
                user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

                logger.info("Replaced selection with translation (%d chars)", len(translated))

            replace_btn.bind("<Button-1>", do_replace)

            def do_translate(lang_name=None):
                if lang_name is None:
                    lang_name = lang_var.get()

                # Save selected language
                for name, code in LANGUAGES:
                    if name == lang_name:
                        self._target_lang = code
                        _save_settings({"target_lang": code})
                        break

                result_text.config(state="normal")
                result_text.delete("1.0", "end")
                result_text.insert("1.0", t("translate.loading"))
                result_text.config(state="disabled")
                status_var.set("")

                def _api_call():
                    try:
                        start = time.monotonic()
                        translated = self._translate(self._source_text, lang_name)
                        elapsed = time.monotonic() - start

                        def _update():
                            result_text.config(state="normal")
                            result_text.delete("1.0", "end")
                            result_text.insert("1.0", translated)
                            result_text.config(state="disabled")
                            status_var.set(f"{elapsed:.1f}s  \u2022  {len(translated)} chars")

                        root.after(0, _update)

                    except Exception as e:
                        logger.error(f"Translation failed: {e}")

                        def _error():
                            result_text.config(state="normal")
                            result_text.delete("1.0", "end")
                            result_text.insert("1.0", f"Error: {e}")
                            result_text.config(state="disabled")

                        root.after(0, _error)

                threading.Thread(target=_api_call, daemon=True).start()

            def on_lang_change(event=None):
                do_translate(lang_var.get())

            lang_combo.bind("<<ComboboxSelected>>", on_lang_change)

            # Keyboard shortcuts
            root.bind("<Escape>", lambda e: root.destroy())
            root.bind("<Control-c>", do_copy)

            # Start first translation
            do_translate()

            root.mainloop()

        except Exception as e:
            logger.error(f"Translate overlay error: {e}")
        finally:
            self._window = None

    def _translate(self, text: str, target_language: str) -> str:
        """Translate text using DeepL (primary, key rotation) with Groq LLM fallback."""
        # Find language code
        lang_code = "en"
        for name, code in LANGUAGES:
            if name == target_language:
                lang_code = code
                break

        # Try DeepL with key rotation
        keys = self._load_deepl_keys()
        if keys:
            # Try each key starting from rotation index
            for attempt in range(len(keys)):
                key = self._next_deepl_key(keys)
                try:
                    result = self._translate_deepl(text, lang_code, key)
                    logger.info("DeepL OK (key #%d)", (self._deepl_rotation_idx - 1) % len(keys) + 1)
                    return result
                except ValueError as e:
                    if "quota exceeded" in str(e).lower():
                        logger.warning("DeepL key #%d quota exceeded, trying next",
                                       (self._deepl_rotation_idx - 1) % len(keys) + 1)
                        continue
                    raise
                except Exception as e:
                    logger.warning("DeepL key #%d failed: %s",
                                   (self._deepl_rotation_idx - 1) % len(keys) + 1, e)
                    continue

            logger.warning("All %d DeepL keys exhausted, falling back to Groq", len(keys))

        # Fallback to Groq LLM
        return self._translate_groq(text, target_language)

    def _load_deepl_keys(self) -> list[str]:
        """Load all DeepL API keys from settings."""
        try:
            if _SETTINGS_FILE.exists():
                data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
                keys = data.get("deepl_keys", [])
                # Migration: single key → list
                if not keys and data.get("deepl_key"):
                    keys = [data["deepl_key"]]
                return [k for k in keys if k.strip()]
        except Exception:
            pass
        return []

    def _next_deepl_key(self, keys: list[str]) -> str:
        """Round-robin key rotation."""
        if not keys:
            return ""
        idx = self._deepl_rotation_idx % len(keys)
        self._deepl_rotation_idx += 1
        return keys[idx]

    def _translate_deepl(self, text: str, target_lang: str, api_key: str) -> str:
        """Translate via DeepL API (free or pro)."""
        base_url = "https://api-free.deepl.com" if api_key.endswith(":fx") else "https://api.deepl.com"

        deepl_lang = target_lang.upper()
        lang_map = {"EN": "EN-US", "PT": "PT-BR", "ZH": "ZH-HANS"}
        deepl_lang = lang_map.get(deepl_lang, deepl_lang)

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{base_url}/v2/translate",
                data={
                    "auth_key": api_key,
                    "text": text,
                    "target_lang": deepl_lang,
                },
            )
            if resp.status_code == 456:
                raise ValueError("DeepL quota exceeded for this key")
            resp.raise_for_status()
            data = resp.json()
            translations = data.get("translations", [])
            if translations:
                return translations[0].get("text", "")
            raise ValueError("No translations in DeepL response")

    def _translate_groq(self, text: str, target_language: str) -> str:
        """Translate via Groq LLM (fallback)."""
        with httpx.Client(
            base_url="https://api.groq.com/openai/v1",
            headers={"Authorization": f"Bearer {self._groq.api_key}"},
            timeout=30.0,
        ) as client:
            resp = client.post(
                "/chat/completions",
                json={
                    "model": self._groq.llm_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": TRANSLATE_PROMPT.format(language=target_language),
                        },
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()