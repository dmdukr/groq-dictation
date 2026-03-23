"""Persistent Tk event loop host — single root, single mainloop, single thread.

All tkinter windows (Settings, Translate, About) MUST use this module
instead of creating their own Tk() roots. This prevents sv_ttk theme
corruption and thread-safety issues.

Usage:
    from . import tk_host
    tk_host.start()                      # call once at app startup
    tk_host.run_on_tk(my_build_func)     # schedule UI work on Tk thread
    root = tk_host.get_root()            # get root for Toplevel(root)
"""

import logging
import threading
import tkinter as tk

logger = logging.getLogger(__name__)

_root: tk.Tk | None = None
_thread: threading.Thread | None = None
_ready = threading.Event()
_theme_applied: str = ""


def start() -> None:
    """Start the Tk host thread. Call once at app startup. Blocks until root is ready."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _ready.clear()
    _thread = threading.Thread(target=_run, name="TkHost", daemon=True)
    _thread.start()
    _ready.wait()


def _run() -> None:
    global _root
    _root = tk.Tk()
    _root.withdraw()
    _apply_theme_inner()
    _ready.set()
    _root.mainloop()


def _apply_theme_inner() -> None:
    """Apply sv_ttk theme based on user settings. Call from Tk thread only."""
    global _theme_applied
    from .utils import detect_windows_theme, load_translate_settings

    pref = load_translate_settings().get("theme", "auto")
    if pref == "dark":
        target = "dark"
    elif pref == "light":
        target = "light"
    else:
        target = "dark" if detect_windows_theme() == "dark" else "light"

    if _theme_applied != target:
        try:
            import sv_ttk
            sv_ttk.set_theme(target)
            _theme_applied = target
            # Update root bg so new Toplevels inherit correct color
            if _root:
                from tkinter import ttk
                bg = ttk.Style().lookup("TFrame", "background")
                if bg:
                    _root.configure(bg=bg)
            logger.info("sv_ttk theme: %s", target)
        except ImportError:
            pass


def is_dark() -> bool:
    """Check if current theme is dark."""
    return _theme_applied == "dark"


def refresh_theme() -> None:
    """Re-apply theme after settings change. Thread-safe."""
    if _root:
        _root.after(0, _apply_theme_inner)


def run_on_tk(callback) -> None:
    """Schedule a callback on the Tk event loop. Thread-safe. Auto-starts host."""
    global _root
    if _root is None:
        start()
    if _root:
        _root.after(0, callback)


def get_root() -> tk.Tk:
    """Get the persistent Tk root. Starts host if not running."""
    if _root is None:
        start()
    return _root


def stop() -> None:
    """Stop the Tk event loop."""
    if _root:
        _root.after(0, _root.quit)
