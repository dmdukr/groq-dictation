"""Quick-translate overlay — triggered by double Ctrl+C.

Shows clipboard text translated to selected language.
Design: flat UI — navy header, light cards, orange accents.
"""

import logging
import threading
import time
import tkinter as tk
from tkinter import ttk

import httpx
import pyperclip

from .config import GroqConfig
from .i18n import t
from .utils import detect_windows_theme, load_translate_settings, save_translate_settings, load_deepl_keys

logger = logging.getLogger(__name__)

# ── Color Themes ──────────────────────────────────────────────────────

THEME_LIGHT = {
    "header": "#f0f0f0",         # Windows light title bar
    "header_text": "#1a1a1a",
    "bg": "#f3f3f3",             # Windows light background
    "card": "#ffffff",           # white card
    "card_input": "#f9f9f9",     # light grey text area (like Windows input)
    "text": "#1a1a1a",           # primary text
    "text_mid": "#666666",       # secondary
    "text_dim": "#999999",       # dimmed
    "accent": "#0078d4",         # Windows blue accent
    "accent_hover": "#106ebe",
    "success": "#107c10",        # Windows green
    "danger": "#d13438",         # Windows red
    "info": "#0078d4",           # Windows blue
    "border": "#e5e5e5",         # Windows border
    "btn_text": "#ffffff",
}

THEME_DARK = {
    "header": "#202020",         # Windows dark title bar (Explorer)
    "header_text": "#ffffff",
    "bg": "#191919",             # Windows dark background
    "card": "#2d2d2d",           # dark card (Explorer panels)
    "card_input": "#1e1e1e",     # dark input area
    "text": "#ffffff",           # primary text
    "text_mid": "#999999",       # secondary
    "text_dim": "#666666",       # dimmed
    "accent": "#4cc2ff",         # Windows dark blue accent
    "accent_hover": "#2eaadc",
    "success": "#6ccb5f",        # green
    "danger": "#ff6b6b",         # red
    "info": "#4cc2ff",           # blue
    "border": "#3d3d3d",         # dark border
    "btn_text": "#1a1a1a",
}


def _get_theme() -> dict:
    """Get active theme dict based on user setting (auto/light/dark)."""
    pref = load_translate_settings().get("theme", "auto")
    if pref == "light":
        return THEME_LIGHT
    elif pref == "dark":
        return THEME_DARK
    else:  # auto
        return THEME_LIGHT if detect_windows_theme() == "light" else THEME_DARK

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



class TranslateOverlay:
    """Floating overlay for quick translation — flat UI dashboard style."""

    def __init__(self, groq_config: GroqConfig):
        self._groq = groq_config
        self._window: tk.Tk | None = None
        self._thread: threading.Thread | None = None
        self._source_text = ""
        self._target_lang = load_translate_settings().get("target_lang", "en")
        self._deepl_rotation_idx = 0

    def show(self, text: str) -> None:
        if not text.strip():
            return
        self._source_text = text.strip()
        self.hide()
        # Wait for previous window to fully close
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = threading.Thread(target=self._build_and_run, daemon=True)
        self._thread.start()

    def hide(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None

    def _build_and_run(self) -> None:
        try:
            # Get theme (auto/light/dark)
            T = _get_theme()

            root = tk.Tk()
            self._window = root
            root.title("Groq Translate")
            root.attributes("-topmost", True)
            root.configure(bg=T["bg"])
            root.minsize(450, 350)

            # Load saved size
            settings = load_translate_settings()
            w = settings.get("window_w", 640)
            h = settings.get("window_h", 460)
            root.update_idletasks()
            sx = (root.winfo_screenwidth() - w) // 2
            sy = (root.winfo_screenheight() - h) // 2
            root.geometry(f"{w}x{h}+{sx}+{sy}")

            def on_close():
                try:
                    save_translate_settings({
                        "window_w": root.winfo_width(),
                        "window_h": root.winfo_height(),
                    })
                except Exception:
                    pass
                root.destroy()

            root.protocol("WM_DELETE_WINDOW", on_close)

            # ── Header ──────────────────────────────────────────────
            header = tk.Frame(root, bg=T["header"], height=48)
            header.pack(fill="x")
            header.pack_propagate(False)

            tk.Label(
                header, text="\u2630  Groq Translate",
                fg=T["header_text"], bg=T["header"], font=("Segoe UI", 12, "bold"),
                padx=16,
            ).pack(side="left")

            # Header buttons
            btn_frame = tk.Frame(header, bg=T["header"])
            btn_frame.pack(side="right", padx=8)

            replace_btn = tk.Label(
                btn_frame, text="  \u21c4 Replace  ",
                fg=T["btn_text"], bg=T["accent"], font=("Segoe UI", 9, "bold"),
                cursor="hand2", padx=10, pady=6,
            )
            replace_btn.pack(side="left", padx=(0, 8), pady=8)
            replace_btn.bind("<Enter>", lambda e: replace_btn.config(bg=T["accent_hover"]))
            replace_btn.bind("<Leave>", lambda e: replace_btn.config(bg=T["accent"]))

            copy_btn = tk.Label(
                btn_frame, text="  \u2398 Copy  ",
                fg=T["btn_text"], bg=T["info"], font=("Segoe UI", 9, "bold"),
                cursor="hand2", padx=10, pady=6,
            )
            copy_btn.pack(side="left", padx=(0, 8), pady=8)
            copy_btn.bind("<Enter>", lambda e: copy_btn.config(bg=T["accent_hover"]))
            copy_btn.bind("<Leave>", lambda e: copy_btn.config(bg=T["info"]))

            # ── Toolbar ─────────────────────────────────────────────
            toolbar = tk.Frame(root, bg=T["bg"], padx=16, pady=10)
            toolbar.pack(fill="x")

            tk.Label(
                toolbar, text="Translate to:",
                fg=T["text_mid"], bg=T["bg"], font=("Segoe UI", 10),
            ).pack(side="left")

            lang_var = tk.StringVar()
            lang_names = [name for name, code in LANGUAGES]

            lang_combo = ttk.Combobox(
                toolbar, textvariable=lang_var, values=lang_names,
                width=14, state="readonly",
            )
            for i, (name, code) in enumerate(LANGUAGES):
                if code == self._target_lang:
                    lang_combo.current(i)
                    break
            lang_combo.pack(side="left", padx=(8, 0))

            engine_var = tk.StringVar(value="")
            tk.Label(
                toolbar, textvariable=engine_var,
                fg=T["text_dim"], bg=T["bg"], font=("Segoe UI", 8),
            ).pack(side="right")

            # ── Content ─────────────────────────────────────────────
            content = tk.Frame(root, bg=T["bg"], padx=16)
            content.pack(fill="both", expand=True)

            # Source card
            src_card = tk.Frame(content, bg=T["card"], bd=1, relief="solid",
                                highlightbackground=T["border"], highlightthickness=1)
            src_card.pack(fill="x", pady=(0, 8))

            src_header = tk.Frame(src_card, bg=T["card"])
            src_header.pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(
                src_header, text="ORIGINAL",
                fg=T["text_dim"], bg=T["card"], font=("Segoe UI", 8, "bold"),
            ).pack(side="left")

            src_text = tk.Text(
                src_card, height=4, wrap="word",
                fg=T["text_mid"], bg=T["card_input"], font=("Segoe UI", 10),
                borderwidth=0, highlightthickness=0, padx=12, pady=8,
                selectbackground=T["accent"],
            )
            src_text.insert("1.0", self._source_text[:2000])
            src_text.config(state="disabled")
            src_text.pack(fill="x")

            # Translation card
            result_card = tk.Frame(content, bg=T["card"], bd=1, relief="solid",
                                   highlightbackground=T["border"], highlightthickness=1)
            result_card.pack(fill="both", expand=True, pady=(0, 8))

            result_header = tk.Frame(result_card, bg=T["card"])
            result_header.pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(
                result_header, text="TRANSLATION",
                fg=T["text_dim"], bg=T["card"], font=("Segoe UI", 8, "bold"),
            ).pack(side="left")

            result_text = tk.Text(
                result_card, wrap="word",
                fg=T["text"], bg=T["card_input"], font=("Segoe UI", 11),
                borderwidth=0, highlightthickness=0, padx=12, pady=8,
                selectbackground=T["accent"],
            )
            result_text.insert("1.0", t("translate.loading"))
            result_text.config(state="disabled")
            result_text.pack(fill="both", expand=True)

            # ── Status bar ──────────────────────────────────────────
            status_frame = tk.Frame(root, bg=T["bg"], padx=16)
            status_frame.pack(fill="x", pady=(0, 8))

            status_var = tk.StringVar(value="")
            tk.Label(
                status_frame, textvariable=status_var,
                fg=T["text_dim"], bg=T["bg"], font=("Segoe UI", 8), anchor="w",
            ).pack(side="left")

            # ── Handlers ────────────────────────────────────────────

            def do_copy(event=None):
                result_text.config(state="normal")
                text = result_text.get("1.0", "end").strip()
                result_text.config(state="disabled")
                if text and text != t("translate.loading"):
                    pyperclip.copy(text)
                    status_var.set("\u2713 " + t("translate.copied"))
                    copy_btn.config(bg=T["success"])
                    root.after(2000, lambda: copy_btn.config(bg=T["info"]))

            copy_btn.bind("<Button-1>", do_copy)

            def do_replace(event=None):
                result_text.config(state="normal")
                translated = result_text.get("1.0", "end").strip()
                result_text.config(state="disabled")
                if not translated or translated == t("translate.loading"):
                    return
                pyperclip.copy(translated)
                on_close()

                import ctypes
                time.sleep(0.2)
                user32 = ctypes.windll.user32
                VK_CONTROL, VK_V = 0x11, 0x56
                KEYEVENTF_KEYUP = 0x0002
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_V, 0, 0, 0)
                user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                logger.info("Replaced selection with translation (%d chars)", len(translated))

            replace_btn.bind("<Button-1>", do_replace)

            def do_translate(lang_name=None):
                if lang_name is None:
                    lang_name = lang_var.get()

                for name, code in LANGUAGES:
                    if name == lang_name:
                        self._target_lang = code
                        save_translate_settings({"target_lang": code})
                        break

                result_text.config(state="normal")
                result_text.delete("1.0", "end")
                result_text.insert("1.0", t("translate.loading"))
                result_text.config(state="disabled")
                status_var.set("")
                engine_var.set("")

                def _api_call():
                    try:
                        start = time.monotonic()
                        translated, engine = self._translate(self._source_text, lang_name)
                        elapsed = time.monotonic() - start

                        def _update():
                            result_text.config(state="normal")
                            result_text.delete("1.0", "end")
                            result_text.insert("1.0", translated)
                            result_text.config(state="disabled")
                            status_var.set(f"{elapsed:.1f}s  \u2022  {len(translated)} chars")
                            engine_var.set(f"via {engine}")

                        root.after(0, _update)

                    except Exception as exc:
                        logger.error("Translation failed: %s", exc)
                        err_msg = str(exc)

                        def _error(msg=err_msg):
                            result_text.config(state="normal")
                            result_text.delete("1.0", "end")
                            result_text.insert("1.0", f"Error: {msg}")
                            result_text.config(state="disabled")
                            status_var.set("Failed")

                        root.after(0, _error)

                threading.Thread(target=_api_call, daemon=True).start()

            def on_lang_change(event=None):
                do_translate(lang_var.get())

            lang_combo.bind("<<ComboboxSelected>>", on_lang_change)

            # Keyboard shortcuts
            root.bind("<Escape>", lambda e: on_close())
            root.bind("<Control-c>", do_copy)

            # Start first translation
            do_translate()

            root.mainloop()

        except Exception as e:
            logger.error(f"Translate overlay error: {e}")
        finally:
            self._window = None

    # ── Translation engines ─────────────────────────────────────────

    def _translate(self, text: str, target_language: str) -> tuple[str, str]:
        """Translate text. Returns (translated_text, engine_name)."""
        lang_code = "en"
        for name, code in LANGUAGES:
            if name == target_language:
                lang_code = code
                break

        # Try DeepL with key rotation
        keys = load_deepl_keys()
        if keys:
            for attempt in range(len(keys)):
                key = self._next_deepl_key(keys)
                try:
                    result = self._translate_deepl(text, lang_code, key)
                    key_num = (self._deepl_rotation_idx - 1) % len(keys) + 1
                    logger.info("DeepL OK (key #%d)", key_num)
                    return result, f"DeepL #{key_num}"
                except ValueError as e:
                    if "quota exceeded" in str(e).lower():
                        logger.warning("DeepL key quota exceeded, trying next")
                        continue
                    raise
                except Exception as e:
                    logger.warning("DeepL failed: %s", e)
                    continue

            logger.warning("All DeepL keys exhausted, falling back to Groq")

        # Fallback to Groq LLM
        result = self._translate_groq(text, target_language)
        return result, "Groq LLM"


    def _next_deepl_key(self, keys: list[str]) -> str:
        if not keys:
            return ""
        idx = self._deepl_rotation_idx % len(keys)
        self._deepl_rotation_idx += 1
        return keys[idx]

    def _translate_deepl(self, text: str, target_lang: str, api_key: str) -> str:
        base_url = "https://api-free.deepl.com" if api_key.endswith(":fx") else "https://api.deepl.com"
        deepl_lang = target_lang.upper()
        lang_map = {"EN": "EN-US", "PT": "PT-BR", "ZH": "ZH-HANS"}
        deepl_lang = lang_map.get(deepl_lang, deepl_lang)

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{base_url}/v2/translate",
                headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
                data={"text": text, "target_lang": deepl_lang},
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
                        {"role": "system", "content": TRANSLATE_PROMPT.format(language=target_language)},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
