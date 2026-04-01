"""Browser extension installer — detect Chromium browsers and install extension."""

from __future__ import annotations

import logging
import subprocess
import sys
import winreg
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_extension_dir() -> Path:
    """Return path to the bundled extension directory."""
    if getattr(sys, "frozen", False):
        ext_dir = Path(sys._MEIPASS) / "extension"  # type: ignore[attr-defined]
        logger.debug("browser_installer: extension dir (frozen) — path=%s", ext_dir)
        return ext_dir
    ext_dir = Path(__file__).parent.parent / "extension"
    logger.debug("browser_installer: extension dir (dev) — path=%s", ext_dir)
    return ext_dir


@dataclass
class BrowserInfo:
    """Detected Chromium-based browser."""

    name: str
    exe_path: Path | None
    extensions_url: str  # e.g. chrome://extensions
    profile_dir: Path | None = None


# ── Browser definitions ───────────────────────────────────────────────

import os as _os

_LOCALAPPDATA = Path(_os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
_PROGRAMFILES = Path(_os.environ.get("PROGRAMFILES", "C:/Program Files"))
_PROGRAMFILES86 = Path(_os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))


def _find_exe_registry(subkey: str, value_name: str = "") -> Path | None:
    """Try to read an exe path from HKLM App Paths or similar registry key."""
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        hive_name = "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU"
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, value_name)
                p = Path(val.strip('"'))
                if p.exists():
                    logger.debug("browser_installer: registry hit — hive=%s, key=%s, path=%s",
                                 hive_name, subkey, p)
                    return p
                else:
                    logger.debug("browser_installer: registry path not found on disk — hive=%s, key=%s, path=%s",
                                 hive_name, subkey, p)
        except (FileNotFoundError, OSError):
            logger.debug("browser_installer: registry key not found — hive=%s, key=%s", hive_name, subkey)
            continue
    return None


def _find_exe_paths(*candidates: Path) -> Path | None:
    """Return the first existing path from candidates."""
    for p in candidates:
        if p.exists():
            return p
    return None


def _detect_chrome() -> BrowserInfo | None:
    exe = _find_exe_registry(
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    ) or _find_exe_paths(
        _PROGRAMFILES / "Google" / "Chrome" / "Application" / "chrome.exe",
        _PROGRAMFILES86 / "Google" / "Chrome" / "Application" / "chrome.exe",
        _LOCALAPPDATA / "Google" / "Chrome" / "Application" / "chrome.exe",
    )
    if exe:
        return BrowserInfo(
            name="Chrome", exe_path=exe, extensions_url="chrome://extensions",
            profile_dir=_LOCALAPPDATA / "Google" / "Chrome" / "User Data",
        )
    return None


def _detect_edge() -> BrowserInfo | None:
    exe = _find_exe_registry(
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
    ) or _find_exe_paths(
        _PROGRAMFILES / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        _PROGRAMFILES86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        _LOCALAPPDATA / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    )
    if exe:
        return BrowserInfo(
            name="Edge", exe_path=exe, extensions_url="edge://extensions",
            profile_dir=_LOCALAPPDATA / "Microsoft" / "Edge" / "User Data",
        )
    return None


def _detect_vivaldi() -> BrowserInfo | None:
    exe = _find_exe_paths(
        _LOCALAPPDATA / "Vivaldi" / "Application" / "vivaldi.exe",
        _PROGRAMFILES / "Vivaldi" / "Application" / "vivaldi.exe",
    )
    if exe:
        return BrowserInfo(
            name="Vivaldi", exe_path=exe, extensions_url="vivaldi://extensions",
            profile_dir=_LOCALAPPDATA / "Vivaldi" / "User Data",
        )
    return None


def _detect_brave() -> BrowserInfo | None:
    exe = _find_exe_paths(
        _LOCALAPPDATA / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        _PROGRAMFILES / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    )
    if exe:
        return BrowserInfo(
            name="Brave", exe_path=exe, extensions_url="brave://extensions",
            profile_dir=_LOCALAPPDATA / "BraveSoftware" / "Brave-Browser" / "User Data",
        )
    return None


def _detect_opera() -> BrowserInfo | None:
    exe = _find_exe_paths(
        _LOCALAPPDATA / "Programs" / "Opera" / "opera.exe",
        _PROGRAMFILES / "Opera" / "opera.exe",
    )
    if exe:
        return BrowserInfo(
            name="Opera", exe_path=exe, extensions_url="opera://extensions",
            profile_dir=_LOCALAPPDATA / "Opera Software" / "Opera Stable",
        )
    return None


# ── Public API ────────────────────────────────────────────────────────


def find_browsers() -> list[BrowserInfo]:
    """Scan for installed Chromium-based browsers.

    Returns deduplicated list — Vivaldi shares Chrome's policy key,
    so both appear but only one registry entry is needed.
    """
    logger.debug("browser_installer: scanning for Chromium browsers")
    browsers: list[BrowserInfo] = []
    for detector in (_detect_chrome, _detect_edge, _detect_vivaldi,
                     _detect_brave, _detect_opera):
        try:
            info = detector()
            if info:
                browsers.append(info)
                logger.debug("browser_installer: detected — name=%s, exe=%s", info.name, info.exe_path)
            else:
                logger.debug("browser_installer: not found — detector=%s", detector.__name__)
        except Exception as exc:
            logger.debug("browser_installer: detection error — detector=%s, error=%s", detector.__name__, exc)
    logger.info("browser_installer: scan complete — found %d browser(s): %s",
                len(browsers), [b.name for b in browsers])
    return browsers


def is_extension_installed(browser: BrowserInfo) -> bool:
    """Check if AI Polyglot Kit extension is loaded in the browser profile."""
    if not browser.profile_dir:
        logger.debug("browser_installer: no profile_dir for %s — cannot check extension", browser.name)
        return False
    logger.debug("browser_installer: checking extension installation — browser=%s, profile=%s",
                 browser.name, browser.profile_dir)
    # Chrome stores extension data in Secure Preferences (not Preferences)
    for prefs_name in ("Secure Preferences", "Preferences"):
        prefs = browser.profile_dir / "Default" / prefs_name
        if not prefs.exists():
            logger.debug("browser_installer: prefs file not found — %s", prefs)
            continue
        try:
            import json
            data = json.loads(prefs.read_text(encoding="utf-8"))
            extensions = data.get("extensions", {}).get("settings", {})
            logger.debug("browser_installer: checking %d extensions in %s",
                         len(extensions), prefs_name)
            for _ext_id, ext_data in extensions.items():
                # Check by manifest name
                manifest = ext_data.get("manifest", {})
                if "AI Polyglot Kit" in manifest.get("name", ""):
                    logger.debug("browser_installer: extension found via manifest — browser=%s, ext_id=%s",
                                 browser.name, _ext_id)
                    return True
                # Check by path (unpacked extensions may lack manifest in prefs)
                ext_path = ext_data.get("path", "")
                if "extension" in ext_path and "AI Polyglot Kit" in ext_path:
                    logger.debug("browser_installer: extension found via path — browser=%s, path=%s",
                                 browser.name, ext_path)
                    return True
        except Exception as e:
            logger.debug("browser_installer: could not check extension status — browser=%s, error=%s",
                         browser.name, e)
    logger.debug("browser_installer: extension not installed — browser=%s", browser.name)
    return False


def install_extension(browser: BrowserInfo) -> None:
    """Show install instructions dialog with action buttons."""
    logger.info("browser_installer: showing install dialog — browser=%s", browser.name)
    ext_dir = _get_extension_dir()
    if not ext_dir.exists() or not (ext_dir / "manifest.json").exists():
        logger.error("browser_installer: extension directory missing — path=%s", ext_dir)
        raise FileNotFoundError(f"Extension not found at {ext_dir}")

    ext_path = str(ext_dir)
    logger.debug("browser_installer: extension path for install — %s", ext_path)

    import tkinter as tk
    from tkinter import ttk

    dlg = tk.Toplevel()
    dlg.title(f"Install Extension — {browser.name}")
    dlg.attributes("-topmost", True)
    dlg.resizable(False, False)

    frame = ttk.Frame(dlg, padding=16)
    frame.pack(fill="both", expand=True)

    def _copy_to_clipboard(text: str, label: ttk.Label) -> None:
        dlg.clipboard_clear()
        dlg.clipboard_append(text)
        label.config(text="Copied!")
        dlg.after(1500, lambda: label.config(text=""))

    def _open_browser() -> None:
        if browser.exe_path:
            try:
                logger.debug("browser_installer: opening browser — exe=%s", browser.exe_path)
                subprocess.Popen([str(browser.exe_path)])
            except Exception as e:
                logger.error("browser_installer: failed to open browser — name=%s, error=%s", browser.name, e)

    # Step 1: Open extensions page
    ttk.Label(frame, text="Step 1:", font=("Segoe UI", 9, "bold")).grid(
        row=0, column=0, sticky="nw", pady=(0, 4))
    step1_frame = ttk.Frame(frame)
    step1_frame.grid(row=0, column=1, sticky="w", pady=(0, 4))
    ttk.Label(step1_frame, text=f"Open {browser.extensions_url} in {browser.name}").pack(
        side="left")

    s1_status = ttk.Label(step1_frame, text="", foreground="green")
    s1_status.pack(side="left", padx=(8, 0))

    btn_frame1 = ttk.Frame(frame)
    btn_frame1.grid(row=1, column=1, sticky="w", pady=(0, 12))
    ttk.Button(btn_frame1, text=f"Copy URL",
               command=lambda: _copy_to_clipboard(browser.extensions_url, s1_status)).pack(
        side="left", padx=(0, 8))
    ttk.Button(btn_frame1, text=f"Open {browser.name}",
               command=_open_browser).pack(side="left")

    # Step 2: Developer mode
    ttk.Label(frame, text="Step 2:", font=("Segoe UI", 9, "bold")).grid(
        row=2, column=0, sticky="nw", pady=(0, 4))
    ttk.Label(frame, text="Enable 'Developer mode' (top-right toggle)").grid(
        row=2, column=1, sticky="w", pady=(0, 12))

    # Step 3: Load unpacked
    ttk.Label(frame, text="Step 3:", font=("Segoe UI", 9, "bold")).grid(
        row=3, column=0, sticky="nw", pady=(0, 4))
    step3_frame = ttk.Frame(frame)
    step3_frame.grid(row=3, column=1, sticky="w", pady=(0, 4))
    ttk.Label(step3_frame, text="Click 'Load unpacked' and select extension folder:").pack(
        side="left")

    s3_status = ttk.Label(step3_frame, text="", foreground="green")
    s3_status.pack(side="left", padx=(8, 0))

    path_frame = ttk.Frame(frame)
    path_frame.grid(row=4, column=1, sticky="w", pady=(0, 4))
    path_entry = ttk.Entry(path_frame, width=50)
    path_entry.insert(0, ext_path)
    path_entry.config(state="readonly")
    path_entry.pack(side="left")

    ttk.Button(path_frame, text="Copy Path",
               command=lambda: _copy_to_clipboard(ext_path, s3_status)).pack(
        side="left", padx=(8, 0))

    # Close button
    ttk.Button(frame, text="Close", command=dlg.destroy).grid(
        row=5, column=0, columnspan=2, pady=(16, 0))

    # Center dialog on screen
    dlg.update_idletasks()
    w = dlg.winfo_width()
    h = dlg.winfo_height()
    x = (dlg.winfo_screenwidth() // 2) - (w // 2)
    y = (dlg.winfo_screenheight() // 2) - (h // 2)
    dlg.geometry(f"+{x}+{y}")

    dlg.grab_set()
    logger.debug("browser_installer: install dialog displayed — waiting for user action")
    dlg.wait_window()
    logger.debug("browser_installer: install dialog closed")
