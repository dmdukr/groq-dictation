"""Shared utilities — extracted from duplicated code across modules."""

from __future__ import annotations

import json
import logging
import math
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLE_WIDTH = 2  # bytes per 16-bit sample


# ── Audio helpers ────────────────────────────────────────────────────────


def compute_rms(data: bytes) -> float:
    """Compute RMS amplitude of raw 16-bit signed PCM data."""
    if not data or len(data) < SAMPLE_WIDTH:
        return 0.0
    n_samples = len(data) // SAMPLE_WIDTH
    samples = struct.unpack(f"<{n_samples}h", data[: n_samples * SAMPLE_WIDTH])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / n_samples)


# ── Windows helpers ──────────────────────────────────────────────────────


def set_dwm_dark_title_bar(window) -> None:
    """Enable dark title bar on Windows 11 for a tkinter window.

    Args:
        window: tkinter.Tk or Toplevel instance (must be already updated).
    """
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
    except Exception:
        pass


def detect_windows_theme() -> str:
    """Detect Windows light/dark theme from registry. Returns 'light' or 'dark'."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if val == 1 else "dark"
    except Exception:
        return "light"


# ── Keyboard helpers ─────────────────────────────────────────────────────


def normalize_key_name(name: str) -> str:
    """Normalize keyboard event name to a consistent short format."""
    mapping = {
        "left windows": "win", "right windows": "win",
        "left ctrl": "ctrl", "right ctrl": "ctrl",
        "left alt": "alt", "right alt": "alt",
        "left shift": "shift", "right shift": "shift",
        "caps lock": "caps lock",
        "print screen": "print screen",
        "scroll lock": "scroll lock",
        "page up": "page up", "page down": "page down",
    }
    return mapping.get(name.lower(), name.lower())


# ── Translate settings (shared between overlay and settings UI) ──────────


def load_translate_settings() -> dict:
    """Load translate_settings.json from APPDATA."""
    from .config import APP_DIR

    path = APP_DIR / "translate_settings.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_translate_settings(updates: dict) -> None:
    """Merge updates into translate_settings.json."""
    from .config import APP_DIR

    path = APP_DIR / "translate_settings.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = load_translate_settings()
        data.update(updates)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def load_deepl_keys() -> list[str]:
    """Load DeepL API keys from translate_settings.json."""
    data = load_translate_settings()
    keys = data.get("deepl_keys", [])
    if not keys and data.get("deepl_key"):
        keys = [data["deepl_key"]]
    return [k for k in keys if k.strip()]
