"""Browser extension installer — detect Chromium browsers and manage HKCU policy."""

from __future__ import annotations

import logging
import shutil
import winreg
from dataclasses import dataclass
from pathlib import Path

from .config import APP_DIR

logger = logging.getLogger(__name__)

# Extension ID placeholder (will be computed from PEM key later)
EXTENSION_ID = "apkpgtranslatorext"

# The update URL served by our local HTTP server
UPDATE_URL = "http://127.0.0.1:19378/extension/update.xml"

# Full forcelist value: "<id>;<update_url>"
_FORCELIST_VALUE = f"{EXTENSION_ID};{UPDATE_URL}"

# Where the extension files are copied for serving
_EXTENSION_DEST = APP_DIR / "extension"

# Source extension directory (relative to project root)
_EXTENSION_SRC = Path(__file__).parent.parent / "extension"


@dataclass
class BrowserInfo:
    """Detected Chromium-based browser."""

    name: str
    exe_path: Path | None
    policy_key: str  # HKCU registry path for ExtensionInstallForcelist


# ── Browser definitions ───────────────────────────────────────────────

_LOCALAPPDATA = Path.home() / "AppData" / "Local"
_PROGRAMFILES = Path("C:/Program Files")
_PROGRAMFILES86 = Path("C:/Program Files (x86)")


def _find_exe_registry(subkey: str, value_name: str = "") -> Path | None:
    """Try to read an exe path from HKLM App Paths or similar registry key."""
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, value_name)
                p = Path(val.strip('"'))
                if p.exists():
                    return p
        except (FileNotFoundError, OSError):
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
            name="Chrome",
            exe_path=exe,
            policy_key=r"SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist",
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
            name="Edge",
            exe_path=exe,
            policy_key=r"SOFTWARE\Policies\Microsoft\Edge\ExtensionInstallForcelist",
        )
    return None


def _detect_vivaldi() -> BrowserInfo | None:
    exe = _find_exe_paths(
        _LOCALAPPDATA / "Vivaldi" / "Application" / "vivaldi.exe",
        _PROGRAMFILES / "Vivaldi" / "Application" / "vivaldi.exe",
    )
    if exe:
        # Vivaldi uses Chrome's policy key
        return BrowserInfo(
            name="Vivaldi",
            exe_path=exe,
            policy_key=r"SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist",
        )
    return None


def _detect_brave() -> BrowserInfo | None:
    exe = _find_exe_paths(
        _LOCALAPPDATA / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        _PROGRAMFILES / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    )
    if exe:
        return BrowserInfo(
            name="Brave",
            exe_path=exe,
            policy_key=r"SOFTWARE\Policies\BraveSoftware\Brave\ExtensionInstallForcelist",
        )
    return None


def _detect_opera() -> BrowserInfo | None:
    exe = _find_exe_paths(
        _LOCALAPPDATA / "Programs" / "Opera" / "opera.exe",
        _PROGRAMFILES / "Opera" / "opera.exe",
    )
    if exe:
        return BrowserInfo(
            name="Opera",
            exe_path=exe,
            policy_key=r"SOFTWARE\Policies\Opera Software\Opera Stable\ExtensionInstallForcelist",
        )
    return None


# ── Public API ────────────────────────────────────────────────────────


def find_browsers() -> list[BrowserInfo]:
    """Scan for installed Chromium-based browsers.

    Returns deduplicated list — Vivaldi shares Chrome's policy key,
    so both appear but only one registry entry is needed.
    """
    browsers: list[BrowserInfo] = []
    for detector in (_detect_chrome, _detect_edge, _detect_vivaldi,
                     _detect_brave, _detect_opera):
        try:
            info = detector()
            if info:
                browsers.append(info)
        except Exception as exc:
            logger.debug("Browser detection error in %s: %s", detector.__name__, exc)
    return browsers


def _read_forcelist_values(policy_key: str) -> dict[str, str]:
    """Read all values from an ExtensionInstallForcelist registry key.

    Returns {value_name: value_data} dict.
    """
    result: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, policy_key, 0,
                            winreg.KEY_READ) as key:
            idx = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, idx)
                    result[name] = data
                    idx += 1
                except OSError:
                    break
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Cannot read %s: %s", policy_key, exc)
    return result


def is_extension_installed(browser: BrowserInfo) -> bool:
    """Check if our extension's update URL is present in the browser's forcelist."""
    values = _read_forcelist_values(browser.policy_key)
    return any(UPDATE_URL in v for v in values.values())


def _copy_extension_files() -> None:
    """Copy extension/ directory to %APPDATA%/AIPolyglotKit/extension/."""
    if not _EXTENSION_SRC.is_dir():
        logger.warning("Extension source not found at %s", _EXTENSION_SRC)
        return
    _EXTENSION_DEST.parent.mkdir(parents=True, exist_ok=True)
    if _EXTENSION_DEST.exists():
        shutil.rmtree(_EXTENSION_DEST)
    shutil.copytree(_EXTENSION_SRC, _EXTENSION_DEST)
    logger.info("Extension files copied to %s", _EXTENSION_DEST)


def _create_key_recursive(hive, subkey: str):
    """Create registry key and all intermediate parents under hive."""
    return winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ)


def install_extension(browser: BrowserInfo) -> None:
    """Install the extension for a browser via HKCU registry policy.

    1. Copies extension files to %APPDATA%/AIPolyglotKit/extension/
    2. Adds our forcelist entry to the browser's policy key
    """
    # Step 1: copy extension files
    _copy_extension_files()

    # Step 2: find next available slot number in forcelist
    values = _read_forcelist_values(browser.policy_key)

    # Don't install if already present
    if any(UPDATE_URL in v for v in values.values()):
        logger.info("Extension already installed for %s", browser.name)
        return

    # Find next numeric slot (values are named "1", "2", "3", ...)
    used_slots = set()
    for name in values:
        try:
            used_slots.add(int(name))
        except ValueError:
            pass
    next_slot = 1
    while next_slot in used_slots:
        next_slot += 1

    # Step 3: write the registry value
    try:
        with _create_key_recursive(winreg.HKEY_CURRENT_USER, browser.policy_key) as key:
            winreg.SetValueEx(key, str(next_slot), 0, winreg.REG_SZ, _FORCELIST_VALUE)
        logger.info("Extension installed for %s (slot %d)", browser.name, next_slot)
    except OSError as exc:
        logger.error("Failed to install extension for %s: %s", browser.name, exc)
        raise


def uninstall_extension(browser: BrowserInfo) -> None:
    """Remove our extension entry from the browser's ExtensionInstallForcelist."""
    values = _read_forcelist_values(browser.policy_key)
    to_remove = [name for name, data in values.items() if UPDATE_URL in data]

    if not to_remove:
        logger.info("Extension not found in %s forcelist", browser.name)
        return

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, browser.policy_key, 0,
                            winreg.KEY_SET_VALUE) as key:
            for name in to_remove:
                winreg.DeleteValue(key, name)
                logger.info("Removed extension slot '%s' from %s", name, browser.name)
    except OSError as exc:
        logger.error("Failed to uninstall extension for %s: %s", browser.name, exc)
        raise
