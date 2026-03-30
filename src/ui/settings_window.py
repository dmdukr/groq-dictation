"""Settings window launcher using PyWebView.

Architecture: main thread is kept free for PyWebView windows.
Tray app signals this module when Settings should open.
The main thread event loop in main.py picks up the signal.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.audio_capture import AudioCapture
    from src.config import AppConfig

logger = logging.getLogger(__name__)

# Signal queue: main thread waits on this for "open settings" commands
_settings_queue: queue.Queue[AppConfig | None] = queue.Queue()
_window_open = threading.Event()


def show_settings(
    config: AppConfig,
    audio_capture: AudioCapture | None = None,  # noqa: ARG001
    on_save: Callable[..., None] | None = None,  # noqa: ARG001
) -> None:
    """Request the main thread to open Settings window.

    Called from tray menu callback (runs in pystray thread).
    Puts config on the queue; main thread picks it up.
    """
    if _window_open.is_set():
        logger.info("Settings window already open")
        return

    logger.info("Requesting Settings window open")
    _settings_queue.put(config)


def run_settings_loop() -> None:
    """Main-thread event loop that opens PyWebView windows on demand.

    Called from main.py after tray.run() starts in a thread.
    Blocks the main thread, waiting for settings open requests.
    """
    while True:
        config = _settings_queue.get()  # blocks until signal
        if config is None:
            break  # shutdown signal

        _window_open.set()
        try:
            _open_webview_window(config)
        except Exception:
            logger.exception("Settings window error")
        finally:
            _window_open.clear()


def shutdown_settings_loop() -> None:
    """Signal the main-thread loop to exit."""
    _settings_queue.put(None)


def _open_webview_window(config: AppConfig) -> None:
    """Create and show a PyWebView window. Runs on main thread."""
    import webview  # noqa: PLC0415

    from src.ui.web_bridge import SettingsBridge  # noqa: PLC0415

    bridge = SettingsBridge(config, None, None)

    web_dir = _find_web_dir()
    if web_dir is None:
        logger.error("Cannot find web UI directory")
        return

    window = webview.create_window(
        "AI Polyglot Kit \u2014 Settings",
        url=str(web_dir / "index.html"),
        js_api=bridge,
        width=900,
        height=640,
        resizable=True,
        min_size=(700, 500),
        background_color="#1e1e2e",
        on_top=True,
    )
    bridge.set_window(window)

    logger.info("PyWebView Settings window created")

    def _on_shown() -> None:
        """Paint native title bar dark after window is shown."""
        try:
            _set_dark_titlebar(window)
        except Exception:
            logger.debug("Could not set dark titlebar", exc_info=True)

    window.events.shown += _on_shown
    webview.start(debug=False)
    logger.info("PyWebView Settings window closed")


def _set_dark_titlebar(window: object) -> None:
    """Paint native Windows title bar dark using DWM API."""
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes  # noqa: PLC0415

    # Get the HWND from pywebview window
    hwnd = None
    import contextlib  # noqa: PLC0415

    with contextlib.suppress(Exception):
        hwnd = window.gui.BrowserView.Handle.ToInt32()  # type: ignore[union-attr]

    if not hwnd:
        with contextlib.suppress(Exception):
            hwnd = ctypes.windll.user32.FindWindowW(None, "AI Polyglot Kit \u2014 Settings")

    if not hwnd:
        return

    # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 20H1+)
    dwmwa_use_immersive_dark_mode = 20
    value = ctypes.c_int(1)  # 1 = dark
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd,
        dwmwa_use_immersive_dark_mode,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )

    # DWMWA_CAPTION_COLOR = 35 (Windows 11 22H2+)
    dwmwa_caption_color = 35
    # Color: #1e1e2e in BGR (COLORREF)
    caption_color = ctypes.c_int(0x002E1E1E)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd,
        dwmwa_caption_color,
        ctypes.byref(caption_color),
        ctypes.sizeof(caption_color),
    )

    # DWMWA_TEXT_COLOR = 36 (Windows 11 22H2+)
    dwmwa_text_color = 36
    text_color = ctypes.c_int(0x00E8E0E0)  # #e0e0e8 in BGR
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd,
        dwmwa_text_color,
        ctypes.byref(text_color),
        ctypes.sizeof(text_color),
    )


def _find_web_dir() -> Path | None:
    """Find the web UI directory."""
    import sys  # noqa: PLC0415

    candidates = [
        Path(__file__).parent / "web",
        Path(getattr(sys, "_MEIPASS", "")) / "src" / "ui" / "web",
    ]
    for c in candidates:
        if c.is_dir() and (c / "index.html").exists():
            return c
    return None
