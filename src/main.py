"""AI Polyglot Kit — Windows system-wide Speech-to-Text service.

Entry point: bootstraps config, engine, and tray app.
"""

import faulthandler
import logging
import sys
import tempfile
from pathlib import Path

from .config import APP_DIR, AppConfig, setup_logging
from .engine import DictationEngine
from .tray_app import TrayApp

logger = logging.getLogger(__name__)

LOCK_FILE = APP_DIR / "groq-dictation.lock"


def _check_single_instance() -> bool:
    """Ensure only one instance is running. Returns True if we can proceed."""
    import ctypes
    # Use a named mutex on Windows for reliable single-instance check
    mutex_name = "Global\\GroqDictation_SingleInstance"
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183

    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return False

    # Store handle to prevent GC (keeps mutex alive for process lifetime)
    _check_single_instance._mutex_handle = handle
    return True


def release_single_instance():
    """Release the mutex so a restart can acquire it."""
    handle = getattr(_check_single_instance, '_mutex_handle', None)
    if handle:
        import ctypes
        ctypes.windll.kernel32.ReleaseMutex(handle)
        ctypes.windll.kernel32.CloseHandle(handle)
        _check_single_instance._mutex_handle = None


def main() -> None:
    """Main entry point."""
    # Single instance check
    logger.debug("main: checking single instance via Windows mutex")
    if not _check_single_instance():
        # Silently exit — no dialog (avoids annoying popups on boot)
        logger.info("main: another instance already running — exiting")
        sys.exit(0)
    logger.debug("main: single instance check passed — mutex acquired")

    # Cleanup duplicate autostart entries
    try:
        from .settings_ui import _cleanup_duplicate_autostart
        _cleanup_duplicate_autostart()
        logger.debug("main: duplicate autostart cleanup completed")
    except Exception:
        logger.debug("main: autostart cleanup skipped (settings_ui unavailable)")

    # Cleanup stale PyInstaller _MEI* temp dirs
    if getattr(sys, "frozen", False):
        logger.debug("main: frozen mode detected — cleaning stale _MEI* temp dirs")
        try:
            import shutil
            tmp = Path(tempfile.gettempdir())
            my_mei = Path(sys._MEIPASS).name if hasattr(sys, "_MEIPASS") else ""
            cleaned_count = 0
            for d in tmp.glob("_MEI*"):
                if d.is_dir() and d.name != my_mei:
                    try:
                        shutil.rmtree(d, ignore_errors=True)
                        cleaned_count += 1
                    except Exception:
                        pass
            logger.debug("main: temp dir cleanup — removed=%d, my_mei=%s", cleaned_count, my_mei)
        except Exception as e:
            logger.debug("main: temp dir cleanup failed — error=%s", e)
    else:
        logger.debug("main: not frozen — skipping _MEI cleanup")

    # Enable faulthandler for segfault debugging
    _fault_log = APP_DIR / "logs" / "crash.log"
    _fault_log.parent.mkdir(parents=True, exist_ok=True)
    _fault_file = open(_fault_log, "a")
    faulthandler.enable(file=_fault_file)
    logger.debug("main: faulthandler enabled — crash_log=%s", _fault_log)

    # Load configuration
    config = AppConfig.load()

    # Setup logging
    setup_logging(config.logging)

    # Catch unhandled exceptions in threads
    import threading
    def _thread_excepthook(args):
        logger.error(
            f"Unhandled exception in thread '{args.thread.name}': {args.exc_type.__name__}: {args.exc_value}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
    threading.excepthook = _thread_excepthook

    logger.info("AI Polyglot Kit starting...")

    # ── CRITICAL: Disable GC globally ──
    # Python 3.13 has a bug where GC firing during abc.__subclasscheck__
    # in worker threads causes "Windows fatal exception: code 0x80000003".
    # This affects httpx, pydantic, and any library using isinstance() with
    # ABCs. Disabling GC is safe for a desktop app — refcounting handles
    # most cleanup. We do manual gc.collect() when engine is idle.
    import gc
    gc.disable()
    logger.info("GC disabled (Python 3.13 ABC/GC crash workaround)")

    # Set UI language
    from .i18n import set_language
    set_language(config.ui.language)

    # Validate config (non-fatal warnings)
    errors = config.validate()
    for err in errors:
        logger.warning(f"Config issue: {err}")
    logger.debug("main: config validation complete — error_count=%d", len(errors))

    # Create engine
    engine = DictationEngine(config)

    # Telemetry: app start
    engine._telemetry.app_start()

    # Create tray app
    tray = TrayApp(engine, config)

    # Run tray in a daemon thread (frees main thread for PyWebView)
    logger.info("Starting tray application (threaded)")
    tray_thread = threading.Thread(target=tray.run, name="TrayApp", daemon=True)
    tray_thread.start()

    # Main thread: wait for Settings window requests (PyWebView needs main thread)
    try:
        from .ui.settings_window import run_settings_loop, shutdown_settings_loop
        logger.info("main: main thread entering PyWebView settings loop")
        run_settings_loop()  # blocks until shutdown signal
        logger.debug("main: settings loop exited normally")
    except ImportError:
        logger.info("main: PyWebView not available — main thread waiting on tray_thread")
        # Keep main thread alive while tray runs
        try:
            tray_thread.join()
        except KeyboardInterrupt:
            pass
    except KeyboardInterrupt:
        logger.info("main: interrupted by user (KeyboardInterrupt)")
    except Exception as e:
        logger.critical("main: fatal error — %s", e, exc_info=True)
        sys.exit(1)
    finally:
        logger.debug("main: shutdown sequence started")
        engine._telemetry.app_stop()
        engine.shutdown()
        logger.info("main: AI Polyglot Kit stopped")


if __name__ == "__main__":
    main()
