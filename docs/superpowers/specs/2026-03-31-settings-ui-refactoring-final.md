# Settings UI Refactoring — Final Spec

Date: 2026-03-31
Status: Approved
Goal: Maximum code simplification without losing functionality or design.
Merges: `settings-ui-refactoring-design.md` + `ui-refactor-simplicity-spec.md`

---

## 1. Problem Statement

The Settings UI is 11400 lines / 766KB with ~60% duplication:

| Asset | Copies | Where |
|---|---|---|
| CSS (619 lines) | 2 | inline in `index.html` + `css/styles.css` |
| JS controller (3309 lines) | 2 | inline in `index.html` + `js/app.js` (diverged) |
| i18n data (584×2 keys, ~75KB) | 3 | inline in `index.html` + `i18n.json` + `js/i18n-data.js` |
| Entry points | 2 | `settings_window.py` (prepared HTML) + `_settings_main.py` (raw HTML, broken i18n) |

Beyond duplication, there are structural problems:

- `populateForm()` and `collectFormData()` manually map ~60 form fields across ~200 lines. Adding a setting requires editing 4 places (HTML, populateForm, collectFormData, web_bridge.py). Unmapped fields show empty values and overwrite real config with blanks on save.
- Python mutates HTML with string replacement and regex-based translation injection before opening the window.
- The config contract between Python and JS is implicit and partially flattened inside the bridge.
- Two startup paths with different locale initialization cause the bug documented in `docs/reviews/2026-03-31-settings-localization-findings.md`.

---

## 2. Principles

### 2.1. Simplicity over cleverness

- Prefer explicit small modules over magic initialization.
- Prefer data-driven startup over regex HTML mutation.
- Prefer a stable contract over ad-hoc object reshaping.

### 2.2. Preserve the stack

- Keep PyWebView. Keep static HTML. Keep plain CSS. Keep vanilla JS.
- The current problems are structural duplication and weak boundaries, not lack of framework abstractions.

### 2.3. Preserve the visual product

- Colors, spacing, layout, class semantics, sidebar structure, cards, toggles, modals, and overall interaction model remain unchanged.
- Visual reference: `docs/mockups/settings-ui.html` until refactor is complete.

### 2.4. One source of truth per concern

| Concern | Source of truth |
|---|---|
| Translations | `i18n.json` |
| Stylesheet | `css/styles.css` |
| Runtime | JS modules under `js/` |
| Startup payload | `settings_bootstrap.py` |
| Config contract | `settings_contract.py` |

---

## 3. Decisions

| Concern | Decision | Rejected alternative | Why |
|---|---|---|---|
| UI technology | Keep vanilla JS | React/Vue migration | Adds churn without solving root problems |
| Startup model | Bootstrap payload (one JSON object) | Regex-translate whole HTML | Simpler, testable, deterministic |
| HTML entry | One prepared HTML path | Two entry points | Removes split behavior |
| i18n source | `i18n.json` → generated `i18n-data.js` | Embedded full dict in HTML | Eliminates duplication, avoids fetch/CORS |
| CSS source | `styles.css` as canonical | Duplicate inline in `index.html` | Removes bulk |
| Form binding | Declarative `data-cfg` attributes | 200 lines of manual mapping | Biggest code reduction, eliminates entire bug class |
| Config mapping | Dedicated `settings_contract.py` | Hidden in bridge methods | Makes payload explicit and testable |
| JS modules | 5 focused files | 10+ micro-files | Enough separation without over-modularization |
| Build step | Required for release only | No build at all | `file://` + `html=` reality requires it |

---

## 4. Target Architecture

### 4.1. File structure

```
src/ui/
  settings_window.py        — window creation only (~80 lines)
  settings_bootstrap.py     — prepare HTML + bootstrap payload (~80 lines)
  settings_contract.py      — AppConfig ↔ UI payload mapping (~100 lines)
  web_bridge.py             — pywebview API actions only (~550 lines)
  build_settings.py         — bundler for release (~60 lines)
  web/
    index.html              — markup + data-cfg attributes only (~2100 lines)
    css/
      styles.css            — canonical stylesheet (619 lines, unchanged)
    js/
      i18n-data.js          — generated from i18n.json (gitignored)
      i18n.js               — translation application (~100 lines)
      form-bind.js          — declarative form ↔ config binding (~100 lines)
      ui-core.js            — theme, navigation, modals, toasts (~150 lines)
      app.js                — composition root + feature logic (~700 lines)
    i18n.json               — canonical translations (unchanged)
```

**Deleted:**
- `_settings_main.py` — single entry point via `settings_window.py`
- `js/pages/` — empty directory

**Generated (gitignored):**
- `_bundled.html` — everything inlined for release
- `js/i18n-data.js` — `var _EMBEDDED_I18N = <i18n.json contents>;`

**Totals: ~4540 lines (from 11400). Zero duplication.**

### 4.2. Dev vs Release modes

```
DEV MODE (zero-build, except one-time i18n-data.js generation):
  settings_window.py
    → _is_dev_mode() returns True
    → webview.create_window(url="file:///.../index.html")
    → JS auto-starts, _BOOTSTRAP is undefined
    → i18n uses _EMBEDDED_I18N (from i18n-data.js <script src>)
    → bridge poll finds window.pywebview.api
    → loadConfig() fetches config from bridge (same as current behavior)

RELEASE MODE (PyInstaller):
  build_settings.py runs as pre-build step:
    → generates js/i18n-data.js from i18n.json
    → bundles index.html + all CSS/JS into _bundled.html
  settings_window.py
    → _is_dev_mode() returns False
    → settings_bootstrap.py prepares HTML string with _BOOTSTRAP payload inlined
    → webview.create_window(html=prepared_content)
    → JS auto-starts, reads _BOOTSTRAP immediately
    → first paint is instant: correct lang, theme, config — no bridge wait
```

The two modes converge: same JS code, same init logic. Release mode has `_BOOTSTRAP` for instant first paint; dev mode falls back to bridge `get_config()` which is imperceptibly delayed.

---

## 5. Bootstrap Payload

### 5.1. Concept

Python constructs one JSON object containing everything the UI needs for first paint. No regex HTML mutation, no multiple injection points.

```json
{
  "lang": "uk",
  "theme": "dark",
  "config": { "...full AppConfig as dict..." },
  "translations": { "uk": {"...584 keys..."}, "en": {"...584 keys..."} }
}
```

### 5.2. Injection

**Release mode:** injected as `<script>var _BOOTSTRAP = {...};</script>` into the HTML string before `html=`.

**Dev mode:** not used. JS auto-starts without `_BOOTSTRAP`, uses `_EMBEDDED_I18N` for translations and fetches config from bridge via `get_config()`. This avoids a timing race between `evaluate_js` and JS auto-start.

### 5.3. settings_bootstrap.py

```python
"""Prepare bootstrap payload for Settings UI.

Single place responsible for:
- Loading translations
- Building bootstrap JSON
- In release mode: injecting into HTML string
- In dev mode: not used (JS falls back to bridge)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ui.settings_contract import config_to_ui

if TYPE_CHECKING:
    from src.config import AppConfig


def build_payload(config: AppConfig) -> dict[str, Any]:
    """Build the bootstrap payload dict."""
    lang = config.ui.language if hasattr(config, "ui") else "uk"
    translations = _load_translations()
    return {
        "lang": lang,
        "theme": _load_theme(),
        "config": config_to_ui(config),
        "translations": translations,
    }


def prepare_html(config: AppConfig, html: str) -> str:
    """Inject bootstrap payload into HTML string (release mode only).

    Dev mode does not use this — JS falls back to bridge.
    """
    payload = build_payload(config)
    script = f"<script>var _BOOTSTRAP = {json.dumps(payload, ensure_ascii=False)};</script>"
    return html.replace("</head>", f"{script}\n</head>")


def _load_translations() -> dict[str, dict[str, str]]:
    """Load translations from i18n.json."""
    i18n_path = Path(__file__).parent / "web" / "i18n.json"
    if i18n_path.exists():
        return json.loads(i18n_path.read_text(encoding="utf-8"))
    return {}


def _load_theme() -> str:
    from src.utils import load_translate_settings
    return load_translate_settings().get("theme", "dark")
```

### 5.4. Rules

- Bootstrap contains only what is needed for first paint.
- No server-side regex translation of the document.
- No duplicated embedded dictionaries.
- Both entry modes use the same `build_payload()` function.

---

## 6. Config Contract

### 6.1. Problem

`_config_to_web()` in `web_bridge.py` manually reshapes `AppConfig` into a flat-ish dict with ad-hoc translations (`sound_feedback`, `show_overlay`, etc.). The SPA expects nested objects (`dictation`, `speaker_lock`, `network`, `history`, `offline`) that the bridge doesn't produce — so those sections are always empty.

`_normalize_web_config()` does the reverse with ~70 lines of manual field mapping.

### 6.2. settings_contract.py

One module, two functions, explicit and testable:

```python
"""AppConfig ↔ Settings UI payload contract.

Single place where config shape is adapted for the Settings SPA.
All other modules pass through — no hidden reshaping.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import AppConfig


def config_to_ui(config: AppConfig) -> dict[str, Any]:
    """Convert AppConfig to the flat dict the Settings SPA expects.

    The SPA's FormBind reads paths like 'audio.vad_aggressiveness'
    directly from this dict via dot-path resolution.
    """
    data = asdict(config)

    # Top-level shortcuts the SPA expects
    data["language"] = data.get("ui", {}).get("language", "uk")
    data["autostart"] = _get_autostart()

    return data


def ui_to_config(data: dict[str, Any], config: AppConfig) -> None:
    """Apply Settings SPA payload back onto a live AppConfig.

    Handles top-level shortcuts, then delegates to AppConfig._apply_dict().
    """
    # Resolve top-level shortcuts back to nested paths
    if "language" in data:
        data.setdefault("ui", {})["language"] = data.pop("language")

    if "autostart" in data:
        _set_autostart(bool(data.pop("autostart")))

    # Provider backward-compat: copy first STT key to groq.api_key
    providers = data.get("providers", {})
    if isinstance(providers, dict):
        stt_slots = providers.get("stt", [])
        if stt_slots and stt_slots[0].get("api_key"):
            data.setdefault("groq", {})["api_key"] = stt_slots[0]["api_key"]

    config._apply_dict(data)


def _get_autostart() -> bool:
    """Check Windows autostart registry."""
    import sys
    if sys.platform != "win32":
        return False
    try:
        import winreg
        from src.config import APP_NAME
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except Exception:
        return False


def _set_autostart(enabled: bool) -> None:
    """Set Windows autostart registry."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import winreg
        from src.config import APP_NAME
        reg_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, reg_key, 0, winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" -m src.main'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe}"')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        pass
```

### 6.3. What this deletes from web_bridge.py

| Method | Lines | Replaced by |
|---|---|---|
| `_config_to_web()` | 38 | `settings_contract.config_to_ui()` |
| `_normalize_web_config()` | 70 | `settings_contract.ui_to_config()` |
| `_apply_config()` | 5 | `ui_to_config()` calls `_apply_dict()` |
| `_apply_providers()` | 45 | `ui_to_config()` + `_apply_dict()` |
| `_apply_audio()` | 12 | `_apply_dict()` |
| `_apply_dictation()` | 12 | `_apply_dict()` |
| `_apply_ui()` | 15 | `_apply_dict()` |
| `_get_autostart()` | 15 | moved to `settings_contract.py` |
| `_set_autostart()` | 20 | moved to `settings_contract.py` |
| `_load_theme()` | 5 | moved to `settings_bootstrap.py` |
| `_save_theme()` | 5 | stays in bridge (called on save) |
| **Total removed** | **~240** | |

### 6.4. Simplified web_bridge.py

```python
@_safe
def get_config(self) -> dict[str, Any]:
    """Return config as UI payload — uses bootstrap contract."""
    from src.ui.settings_contract import config_to_ui
    return config_to_ui(self._config)

@_safe
def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
    """Apply UI payload back to config and persist."""
    from src.ui.settings_contract import ui_to_config

    # Extract non-config fields
    theme = data.pop("theme", None)
    if theme:
        self._save_theme(theme)

    ui_to_config(data, self._config)
    self._write_config()
    self._write_env()

    if self._on_save is not None:
        self._on_save(restart=True)
    return {"success": True}
```

### 6.5. Aspirational UI fields

Fields on the SPA that don't exist in `AppConfig` (e.g., `speaker_lock.*`, `network.*`, `history.*`, `offline.*`):
- Get **no `data-cfg` attribute** — FormBind ignores them.
- Receive CSS class `disabled-field` (greyed out, `pointer-events: none; opacity: 0.5`).
- When backend adds support: (1) add to AppConfig dataclass, (2) add `data-cfg` to HTML, (3) remove `disabled-field`. No JS changes.

Note: `dictation.injection_method` → `text_injection.method` and `dictation.typing_speed` → `text_injection.typing_delay_ms` DO work — these get `data-cfg` with path aliases.

---

## 7. Declarative Form Binding

### 7.1. Concept

Instead of 60+ manual `setSelectValue` / `getSelectValue` calls, each form element declares its config path:

```html
<select id="lang-select" data-cfg="language">
<label><input type="checkbox" data-cfg="telemetry.enabled"></label>
<select id="mic-select" data-cfg="audio.mic_device_index">
<input type="range" id="rms-slider" data-cfg="audio.gain">
<input type="range" id="temp-slider" data-cfg="normalization.temperature" data-divisor="100">
<select id="injection-method-select" data-cfg="text_injection.method">
```

### 7.2. form-bind.js

```javascript
var FormBind = {
  /**
   * Populate all [data-cfg] elements from a config object.
   */
  populate: function(config) {
    document.querySelectorAll('[data-cfg]').forEach(function(el) {
      var path = el.getAttribute('data-cfg');
      var value = FormBind._resolve(config, path);
      if (value === undefined) return;
      FormBind._setValue(el, value);
    });
  },

  /**
   * Collect all [data-cfg] elements into a nested config object.
   */
  collect: function() {
    var config = {};
    document.querySelectorAll('[data-cfg]').forEach(function(el) {
      var path = el.getAttribute('data-cfg');
      var value = FormBind._getValue(el);
      FormBind._assign(config, path, value);
    });
    return config;
  },

  _resolve: function(obj, path) {
    return path.split('.').reduce(function(cur, key) {
      return cur == null ? undefined : cur[key];
    }, obj);
  },

  _assign: function(obj, path, value) {
    var parts = path.split('.');
    var target = parts.slice(0, -1).reduce(function(cur, key) {
      if (!cur[key]) cur[key] = {};
      return cur[key];
    }, obj);
    target[parts[parts.length - 1]] = value;
  },

  _getValue: function(el) {
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'range') {
      var divisor = parseFloat(el.getAttribute('data-divisor')) || 1;
      return parseFloat(el.value) / divisor;
    }
    if (el.type === 'number') return parseInt(el.value, 10) || 0;
    return el.value;
  },

  _setValue: function(el, value) {
    if (value === undefined || value === null) return;
    if (el.type === 'checkbox') {
      el.checked = !!value;
    } else if (el.type === 'range') {
      var divisor = parseFloat(el.getAttribute('data-divisor')) || 1;
      el.value = value * divisor;
      el.dispatchEvent(new Event('input'));
    } else {
      el.value = value;
    }
  }
};
```

### 7.3. What this replaces

- `populateForm()`: 100 lines → `FormBind.populate(config)` + ~10 lines custom fields
- `collectFormData()`: 130 lines → `FormBind.collect()` + ~10 lines custom fields
- 15 getter/setter helpers: 170 lines → deleted entirely

**Total: ~400 lines → ~100 lines of form-bind.js**

### 7.4. Fields that stay custom (not data-cfg)

| Field | Reason |
|---|---|
| Hotkey capture (`hotkey-record`, etc.) | Needs keydown listener + display formatting |
| Provider cards (STT/LLM/Translate × 3 slots) | Dynamic card structure with auto-detect |
| Audio device list (`mic-select` options) | Populated from `get_audio_devices()` |
| Dictionary/Replacements/History tables | CRUD tables, not config fields |
| Stats page | Read-only display |

~15 custom fields, ~45 declarative. Custom fields keep existing logic in app.js.

---

## 8. i18n Simplification

### 8.1. Current state (3 mechanisms)

1. Server-side regex in `settings_window.py` (~40 lines)
2. Early JS bootstrap (inline in index.html) reading `data-initial-lang`
3. Full JS i18n in `app.js`: `walkAndTranslate()` + `loadTranslations()` (~120 lines)

Three mechanisms, three copies of data, overlapping responsibility.

### 8.2. After: Single JS i18n

```
i18n.json (single source of truth)
    ↓
build_settings.py generates js/i18n-data.js (var _EMBEDDED_I18N = ...)
    ↓
Dev: <script src="js/i18n-data.js"> loads synchronously, no fetch, no CORS
Release: bundler inlines i18n-data.js into _bundled.html
    ↓
Bootstrap payload carries translations too (for immediate first-paint in release mode)
    ↓
i18n.js applies translations from _BOOTSTRAP.translations or _EMBEDDED_I18N
```

### 8.3. i18n.js

```javascript
var I18n = {
  lang: 'uk',
  data: {},

  init: function(bootstrap) {
    // Language from bootstrap payload or default
    this.lang = (bootstrap && bootstrap.lang) || 'uk';

    // Translations: prefer bootstrap (release), fall back to embedded (dev)
    if (bootstrap && bootstrap.translations) {
      this.data = bootstrap.translations;
    } else if (typeof _EMBEDDED_I18N !== 'undefined') {
      this.data = _EMBEDDED_I18N;
    }

    this.apply(this.lang);
  },

  apply: function(lang) {
    this.lang = lang;
    var tr = this.data[lang] || {};
    document.querySelectorAll('[data-i18n]').forEach(function(el) {
      var key = el.getAttribute('data-i18n');
      if (tr[key]) el.textContent = tr[key];
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function(el) {
      var key = el.getAttribute('data-i18n-placeholder');
      if (tr[key]) el.placeholder = tr[key];
    });
    document.documentElement.lang = lang;
  },

  setLang: function(lang) {
    this.apply(lang);
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.set_language(lang);
    }
  }
};
```

**Deleted:**
- Server-side regex translation in `settings_window.py` (~40 lines)
- `earlyLang()` IIFE (~20 lines)
- `_loadEmbeddedI18n()`, `loadTranslations()` (~15 lines)
- `walkAndTranslate()` with origTexts text-matching (~65 lines)
- Inline `_EMBEDDED_I18N` in index.html (~75KB)

Slider labels (`SLIDER_UK` map) stay in app.js — controller logic, not i18n infrastructure.

---

## 9. JS Module Structure

### 9.1. Five files, clear boundaries

| File | Lines | Responsibility |
|---|---|---|
| `i18n-data.js` | 1 | Generated: `var _EMBEDDED_I18N = {...}` |
| `i18n.js` | ~100 | Apply translations, language switching |
| `form-bind.js` | ~100 | Declarative form ↔ config binding |
| `ui-core.js` | ~150 | Theme, navigation, modals, toasts, slider labels |
| `app.js` | ~700 | Composition root + all feature setup functions |

### 9.2. Why 5 and not 10

- `state.js` (30 lines) and `bridge.js` (40 lines) proposed in the other spec are too small to justify separate files. State is 3 variables in app.js. Bridge is `window.pywebview.api`.
- `features/providers.js`, `features/history.js`, etc. are viable future extractions but premature now — each is ~80-120 lines, not complex enough to warrant isolation.
- Five files is enough to have one-responsibility modules without over-modularization.

### 9.3. app.js after refactoring

```javascript
(function() {
  'use strict';

  var api = null;

  async function init(bootstrap) {
    if (window.pywebview && window.pywebview.api) {
      api = window.pywebview.api;
    }

    // Init modules from bootstrap payload
    I18n.init(bootstrap);
    UiCore.init(bootstrap);

    // Feature setup (unchanged logic, just cleaner)
    setupHotkeyCapture();
    setupDictionary();
    setupReplacements();
    setupPerAppInstructions();
    setupHistory();
    setupSpeakerLock();
    setupAudio();
    setupProviderCards();
    setupBrowserExtension();
    setupOffline();
    setupNetwork();
    setupStats();
    setupFooter();
    setupImportDropZones();

    // Load config into form
    if (api) {
      await loadConfig();
      await loadVersion();
    } else if (bootstrap && bootstrap.config) {
      // Use bootstrap config if bridge not yet ready
      FormBind.populate(bootstrap.config);
      populateCustomFields(bootstrap.config);
    }
  }

  async function loadConfig() {
    try {
      var config = await api.get_config();
      if (!config) return;
      FormBind.populate(config);
      populateCustomFields(config);
    } catch (e) {
      console.warn('[config] Load error:', e.message);
    }
  }

  async function saveConfig() {
    if (!api) { UiCore.toast('Backend not connected', 'error'); return; }
    var data = FormBind.collect();
    // Add custom fields not handled by data-cfg
    data.providers = collectAllProviders();
    data.language = I18n.lang;
    data.theme = UiCore.theme;
    try {
      var result = await api.save_config(data);
      if (result && result.success) {
        UiCore.toast('Settings saved', 'success');
      }
    } catch (e) {
      UiCore.toast('Error: ' + e.message, 'error');
    }
  }

  // ... setup functions stay here, each ~20-120 lines ...
  // ... removed: populateForm, collectFormData, 15 helpers ...

  // --- Startup ---
  function start() {
    var bootstrap = (typeof _BOOTSTRAP !== 'undefined') ? _BOOTSTRAP : null;
    if (window.pywebview && window.pywebview.api) {
      init(bootstrap);
    } else {
      // Poll for bridge (pywebviewready event is unreliable)
      var attempts = 0;
      var poll = setInterval(function() {
        attempts++;
        if (window.pywebview && window.pywebview.api) {
          clearInterval(poll);
          init(bootstrap);
        } else if (attempts > 100) {
          clearInterval(poll);
          init(bootstrap); // run without bridge
        }
      }, 50);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
```

---

## 10. settings_window.py After Refactoring

```python
"""Settings window launcher using PyWebView.

Only responsible for:
- Queue-based signal from tray to main thread
- Creating the PyWebView window
- Attaching the bridge
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from src.audio_capture import AudioCapture
    from src.config import AppConfig

logger = logging.getLogger(__name__)

_settings_queue: queue.Queue[
    tuple[AppConfig, AudioCapture | None, Callable[..., None] | None] | None
] = queue.Queue()
_window_open = threading.Event()


def show_settings(
    config: AppConfig,
    audio_capture: AudioCapture | None = None,
    on_save: Callable[..., None] | None = None,
) -> None:
    if _window_open.is_set():
        logger.info("Settings window already open")
        return
    _settings_queue.put((config, audio_capture, on_save))


def run_settings_loop() -> None:
    while True:
        request = _settings_queue.get()
        if request is None:
            break
        config, audio_capture, on_save = request
        _window_open.set()
        try:
            _open_window(config, audio_capture, on_save)
        except Exception:
            logger.exception("Settings window error")
        finally:
            _window_open.clear()


def shutdown_settings_loop() -> None:
    _settings_queue.put(None)


def _open_window(
    config: AppConfig,
    audio_capture: AudioCapture | None = None,
    on_save: Callable[..., None] | None = None,
) -> None:
    import webview
    from src.ui.web_bridge import WebBridge
    from src.ui.settings_bootstrap import prepare_html

    bridge = WebBridge(config, audio_capture, on_save)
    web_dir = _find_web_dir()
    if web_dir is None:
        logger.error("Cannot find web UI directory")
        return

    if _is_dev_mode():
        # Dev: load from file, JS auto-starts, uses bridge for config
        url = (web_dir / "index.html").as_uri()
        window = webview.create_window(
            "AI Polyglot Kit — Settings",
            url=url, js_api=bridge,
            width=900, height=640, resizable=True,
            min_size=(700, 500), background_color="#1e1e2e",
        )
        bridge.set_window(window)
        webview.start(debug=True)
    else:
        bundled = web_dir / "_bundled.html"
        html = bundled.read_text(encoding="utf-8")
        html = prepare_html(config, html)
        window = webview.create_window(
            "AI Polyglot Kit — Settings",
            html=html, js_api=bridge,
            width=900, height=640, resizable=True,
            min_size=(700, 500), background_color="#1e1e2e",
        )
        bridge.set_window(window)
        webview.start(debug=False)


def _is_dev_mode() -> bool:
    return not getattr(sys, "frozen", False)


def _find_web_dir() -> Path | None:
    candidates = [
        Path(__file__).parent / "web",
        Path(getattr(sys, "_MEIPASS", "")) / "src" / "ui" / "web",
    ]
    for c in candidates:
        if c.is_dir() and (c / "index.html").exists():
            return c
    return None
```

**Deleted from current settings_window.py:**
- Regex-based server-side i18n (~40 lines)
- YAML disk language debug logging (~15 lines)
- WebView2 cache clearing (~5 lines)
- Hardcoded `selected` attribute patching (~5 lines)
- `set_titlebar_theme()` Win32 DWM — moved to bridge or ui-core.js call

---

## 11. build_settings.py

```python
"""Bundle Settings UI for release + generate i18n-data.js for dev.

Outputs (both gitignored):
  1. js/i18n-data.js — generated from i18n.json
  2. _bundled.html   — everything inlined for release

Usage: python -m src.ui.build_settings
"""
from __future__ import annotations

import re
from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"


def generate_i18n_data_js() -> None:
    """Generate js/i18n-data.js from i18n.json."""
    i18n = (WEB_DIR / "i18n.json").read_text(encoding="utf-8")
    out = WEB_DIR / "js" / "i18n-data.js"
    out.write_text(f"var _EMBEDDED_I18N = {i18n.strip()};\n", encoding="utf-8")
    print(f"Generated: {out}")


def build_bundle() -> None:
    """Bundle index.html with all external assets inlined."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    # Inline CSS
    css = (WEB_DIR / "css" / "styles.css").read_text(encoding="utf-8")
    html = re.sub(
        r'<link\s+rel="stylesheet"\s+href="css/styles\.css"\s*/?>',
        f"<style>\n{css}\n</style>",
        html,
    )

    # Inline all JS
    def inline_js(match: re.Match[str]) -> str:
        src = match.group(1)
        js_path = WEB_DIR / src
        if js_path.exists():
            return f"<script>\n{js_path.read_text(encoding='utf-8')}\n</script>"
        return match.group(0)

    html = re.sub(r'<script src="(js/[^"]+)"></script>', inline_js, html)

    out = WEB_DIR / "_bundled.html"
    out.write_text(html, encoding="utf-8")
    print(f"Bundled: {out} ({len(html):,} bytes)")


def build() -> None:
    generate_i18n_data_js()
    build_bundle()


if __name__ == "__main__":
    build()
```

PyInstaller build pipeline:
1. `python -m src.ui.build_settings`
2. `pyinstaller groq_dictation.spec`
3. Inno Setup (unchanged)

---

## 12. PyInstaller Changes

```python
# groq_dictation.spec — changes
datas=[
    # ...
    ('src/ui/web', 'src/ui/web'),  # includes _bundled.html
    # REMOVE: ('src/ui/_settings_main.py', 'src/ui'),
],
hiddenimports=[
    # ...
    # REMOVE: 'src.ui._settings_main',
    # ADD:
    'src.ui.settings_bootstrap',
    'src.ui.settings_contract',
],
```

---

## 13. Migration Plan

### Phase 0 — Tests (before any code changes)

- [ ] `settings_contract.py`: unit tests for `config_to_ui()` round-trip with default AppConfig
- [ ] `settings_contract.py`: unit tests for `ui_to_config()` with all populated sections
- [ ] `settings_contract.py`: test language, theme, autostart handling
- [ ] Manual smoke checklist for all 14 settings pages
- [ ] Reference screenshots for visual baseline

### Phase 1 — Unify startup path + bootstrap payload

- [ ] Create `settings_bootstrap.py`
- [ ] Create `settings_contract.py` (extract from web_bridge.py)
- [ ] Refactor `settings_window.py` to use bootstrap + dev/release modes
- [ ] Delete `_settings_main.py`
- [ ] Remove from `groq_dictation.spec`
- [ ] Verify: default `uk` and explicit `en` both render correctly
- [ ] Commit

### Phase 2 — Deduplicate assets

- [ ] Delete inline CSS from index.html → `<link href="css/styles.css">`
- [ ] Delete inline `_EMBEDDED_I18N` from index.html
- [ ] Delete inline JS copy from index.html → `<script src="js/app.js">`
- [ ] Add `<script src="js/i18n-data.js">` to index.html
- [ ] Sync 3 diverged changes from inline JS into standalone app.js
- [ ] Verify: dev mode opens and renders correctly
- [ ] Commit

### Phase 3 — i18n consolidation

- [ ] Create `js/i18n.js` with `I18n` module
- [ ] Remove `earlyLang()`, `walkAndTranslate()`, `loadTranslations()`, `_loadEmbeddedI18n()` from app.js
- [ ] Wire `setupI18n()` to delegate to `I18n` module
- [ ] Remove server-side regex i18n from `settings_window.py` (already done in Phase 1)
- [ ] Verify: both languages work, switching persists
- [ ] Commit

### Phase 4 — Declarative form binding

- [ ] Create `js/form-bind.js` with `FormBind` module
- [ ] Add `data-cfg` attributes to all bindable HTML elements (~45 elements)
- [ ] Replace `populateForm()` with `FormBind.populate()` + `populateCustomFields()`
- [ ] Replace `collectFormData()` with `FormBind.collect()` + custom provider/language fields
- [ ] Delete 15 getter/setter helper functions from app.js
- [ ] Verify: all existing config fields save and load correctly
- [ ] Commit

### Phase 5 — Extract ui-core.js

- [ ] Move to `js/ui-core.js`: theme switching, navigation, modals, toasts, slider label refresh
- [ ] app.js init calls `UiCore.init(bootstrap)`
- [ ] Commit

### Phase 6 — Simplify web_bridge.py

- [ ] Replace `get_config` with `config_to_ui()` call
- [ ] Replace `save_config` with `ui_to_config()` call
- [ ] Delete `_config_to_web`, `_normalize_web_config`, all `_apply_*` methods
- [ ] Delete `_get_autostart`, `_set_autostart`, `_load_theme` (moved to contract/bootstrap)
- [ ] Verify: config round-trip works
- [ ] Commit

### Phase 7 — Build bundler + cleanup

- [ ] Create `build_settings.py`
- [ ] Add `_bundled.html` and `js/i18n-data.js` to `.gitignore`
- [ ] Update `groq_dictation.spec`
- [ ] Remove empty `js/pages/` directory
- [ ] Remove debug logging from settings_window.py
- [ ] Run ruff, mypy, bandit on all changed Python files
- [ ] Final line count audit
- [ ] Commit

---

## 14. Simplicity Budgets

These are design constraints, not aspirations.

### 14.1. File size budgets

| File | Max lines |
|---|---|
| `index.html` | 2600 |
| `app.js` | 700 |
| `ui-core.js` | 200 |
| `form-bind.js` | 150 |
| `i18n.js` | 120 |
| `settings_window.py` | 100 |
| `settings_bootstrap.py` | 100 |
| `settings_contract.py` | 120 |
| `web_bridge.py` | 600 |

### 14.2. Source-of-truth budgets

- Exactly 1 startup path for prepared settings HTML.
- Exactly 1 translation source for the web UI.
- Exactly 1 settings runtime entry.
- Exactly 1 config adapter layer.
- Exactly 0 server-side regex HTML mutations.

---

## 15. Acceptance Criteria

1. **No visual changes:** Settings window looks identical in dark/light themes.
2. **Language switching:** EN↔UK works, persists across restart.
3. **Config round-trip:** Every field that was working before still works. No data lost on save.
4. **Dev mode:** Settings open from source with external files, no build needed (except `i18n-data.js` generation).
5. **Release mode:** PyInstaller build produces working bundled Settings.
6. **Startup determinism:** `document.lang`, `currentLang`, and `lang-select` all match before first paint.
7. **Line count:** Total UI code < 5000 lines (from 11400).
8. **Zero duplication:** No asset exists in more than one file.
9. **Tests pass:** Contract round-trip tests cover all config sections.

---

## 16. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Breaking first paint or startup timing | UI renders wrong language/theme | Bootstrap payload makes first paint deterministic; Phase 0 tests |
| Visual regressions from CSS movement | Layout drift | Promote existing `styles.css` before any changes; don't rename classes |
| Config save/load regressions | Silent data loss | Contract module tested independently; Phase 0 tests |
| Dev mode has no `_BOOTSTRAP` | Slower first paint in dev | Acceptable: bridge poll adds ~50-200ms; `_EMBEDDED_I18N` gives instant translations |
| WebView2 caching stale files in dev | Old JS/CSS after edit | `debug=True` in dev disables WebView2 cache |
| Over-engineering the refactor itself | Scope creep | No framework, 5 JS files not 10, phased migration |

---

## 17. Line Count Summary

| File | Before | After | Delta |
|---|---|---|---|
| `index.html` | 6124 | ~2100 | −4024 |
| `app.js` | 3309 | ~700 | −2609 |
| `ui-core.js` | 0 | ~150 | +150 |
| `form-bind.js` | 0 | ~100 | +100 |
| `i18n.js` | 0 | ~100 | +100 |
| `i18n-data.js` | 1 | 1 | 0 (generated) |
| `styles.css` | 619 | 619 | 0 |
| `web_bridge.py` | 1079 | ~550 | −529 |
| `settings_window.py` | 245 | ~80 | −165 |
| `settings_bootstrap.py` | 0 | ~80 | +80 |
| `settings_contract.py` | 0 | ~100 | +100 |
| `build_settings.py` | 0 | ~60 | +60 |
| `_settings_main.py` | 46 | 0 | −46 |
| **Total** | **11423** | **~4640** | **−6783** |

**59% reduction. Zero duplication. Every concern has exactly one owner.**
