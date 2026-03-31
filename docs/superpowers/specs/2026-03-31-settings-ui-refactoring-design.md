# Settings UI Refactoring — Design Spec

Date: 2026-03-31
Status: Draft
Goal: Maximum code simplification without losing functionality or design.

## 1. Problem Statement

The Settings UI is a 11400-line, 766KB codebase with ~60% duplication:

| Asset | Copies | Where |
|---|---|---|
| CSS (619 lines) | 2 | inline in `index.html` + `css/styles.css` |
| JS controller (3309 lines) | 2 | inline in `index.html` + `js/app.js` (diverged) |
| i18n data (584×2 keys, ~75KB) | 3 | inline in `index.html` + `i18n.json` + `js/i18n-data.js` |
| Entry points | 2 | `settings_window.py` (prepared HTML) + `_settings_main.py` (raw HTML, broken i18n) |

Beyond duplication, the SPA has a structural defect: `populateForm()` and `collectFormData()` manually map ~60 form fields to config keys across ~200 lines of code. Adding a new setting requires editing 4 places (HTML, populateForm, collectFormData, web_bridge.py). Fields that aren't mapped show empty values and overwrite real config with blanks on save.

The two inline JS copies have diverged: the index.html copy reads `data-initial-lang` (newer, correct); the standalone `app.js` reads URL params only (older). Fixing bugs in one does not fix the other.

## 2. Target Architecture

### 2.1. File Structure After Refactoring

```
src/ui/
  settings_window.py    — single entry point (refactored, ~120 lines)
  web_bridge.py         — Python API bridge (refactored, ~600 lines)
  web/
    index.html          — clean HTML only (~2100 lines)
    css/
      styles.css        — single CSS source (unchanged, 619 lines)
    js/
      app.js            — main controller (~800 lines)
      form-bind.js      — declarative form ↔ config binding (~120 lines)
      i18n.js           — i18n logic (~120 lines)
    i18n.json           — single i18n source (unchanged)
  build_settings.py     — bundler script (~60 lines)
```

**Deleted files:**
- `_settings_main.py` — removed, single entry point via `settings_window.py`
- `js/pages/` — empty directory, removed

**Generated files (gitignored):**
- `_bundled.html` — produced by `build_settings.py` for release
- `js/i18n-data.js` — auto-generated from `i18n.json` by `build_settings.py` (also serves as dev fallback for `file://` CORS, see section 13.1)

**Estimated totals:** ~4500 lines (from 11400). Zero duplication.

### 2.2. Dev vs Release Modes

```
DEV MODE (zero-build):
  settings_window.py
    → detects dev mode (no sys.frozen / env var)
    → webview.create_window(url="file:///...index.html?lang=uk")
    → index.html loads external CSS/JS via <link>/<script src>
    → JS reads ?lang= param, applies translations from i18n.json via fetch()

RELEASE MODE (PyInstaller):
  build_settings.py runs as pre-build step
    → reads index.html, inlines CSS/JS/i18n
    → writes _bundled.html to web/ (gitignored)
  settings_window.py
    → detects frozen mode (sys.frozen or _bundled.html exists)
    → reads _bundled.html, injects data-initial-lang
    → webview.create_window(html=content)
```

Dev mode detection in `settings_window.py`:

```python
def _is_dev_mode() -> bool:
    return not getattr(sys, "frozen", False)
```

### 2.3. index.html Structure

The HTML file becomes a clean template with no inline CSS, JS, or data:

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Polyglot Kit — Settings</title>
<link rel="stylesheet" href="css/styles.css">
</head>
<body>
  <div id="app" class="window">
    <!-- sidebar + 14 content pages, unchanged structure -->
    <!-- only change: add data-cfg attributes to form elements -->
  </div>
  <script src="js/i18n-data.js"></script>
  <script src="js/i18n.js"></script>
  <script src="js/form-bind.js"></script>
  <script src="js/app.js"></script>
</body>
</html>
```

All 14 pages retain their current HTML structure, classes, IDs, and layout. The only HTML change is adding `data-cfg` attributes to form elements (see section 3).

## 3. Declarative Form Binding

### 3.1. Concept

Instead of 60+ manual `setSelectValue('lang-select', config.language)` / `getSelectValue('lang-select')` calls, each form element declares its config path:

```html
<select id="lang-select" data-cfg="language">
<select id="theme-select" data-cfg="theme">
<label id="toggle-autostart"><input type="checkbox" data-cfg="autostart"></label>
<label id="toggle-telemetry"><input type="checkbox" data-cfg="telemetry"></label>
<select id="mic-select" data-cfg="audio.device_id">
<input type="range" id="rms-slider" data-cfg="audio.target_volume">
<select id="vad-select" data-cfg="stt.vad_sensitivity">
<input type="range" id="beam-slider" data-cfg="stt.beam_size">
<input type="checkbox" data-cfg="llm.enabled">
<input type="range" id="temp-slider" data-cfg="llm.temperature" data-divisor="100">
<select id="injection-method-select" data-cfg="dictation.injection_method">
<input type="checkbox" data-cfg="speaker_lock.enabled">
<input type="number" id="stt-timeout-input" data-cfg="network.stt_timeout">
```

### 3.2. form-bind.js API

```javascript
// form-bind.js — Declarative form ↔ config binding
// All elements with [data-cfg] are automatically bound.

var FormBind = {
  /**
   * Populate all [data-cfg] elements from a config object.
   * @param {Object} config — flat or nested config from backend
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
   * Collect all [data-cfg] elements into a config object.
   * @returns {Object} nested config object
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

  // Resolve "audio.device_id" → config.audio.device_id
  _resolve: function(obj, path) {
    var parts = path.split('.');
    var current = obj;
    for (var i = 0; i < parts.length; i++) {
      if (current == null) return undefined;
      current = current[parts[i]];
    }
    return current;
  },

  // Assign "audio.device_id" = value → config.audio = { device_id: value }
  _assign: function(obj, path, value) {
    var parts = path.split('.');
    var current = obj;
    for (var i = 0; i < parts.length - 1; i++) {
      if (!current[parts[i]]) current[parts[i]] = {};
      current = current[parts[i]];
    }
    current[parts[parts.length - 1]] = value;
  },

  // Read value from element based on its type
  _getValue: function(el) {
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'range') {
      var divisor = parseFloat(el.getAttribute('data-divisor')) || 1;
      return parseFloat(el.value) / divisor;
    }
    if (el.type === 'number') return parseInt(el.value, 10) || 0;
    return el.value;
  },

  // Write value to element based on its type
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

### 3.3. What This Replaces

Current `populateForm()`: 100 lines of manual `setSelectValue`, `setBoolToggle`, `setSliderValue`, `setInputValue` calls → replaced by `FormBind.populate(config)`.

Current `collectFormData()`: 130 lines of manual `getSelectValue`, `getBoolToggle`, `getSliderInt`, `getInputValue` calls → replaced by `FormBind.collect()`.

Current helper functions removed (no longer needed):
- `setSelectValue`, `setSelectByText`, `getSelectValue`
- `setInputValue`, `getInputValue`, `getInputInt`
- `setBoolToggle`, `getBoolToggle`
- `setSliderValue`, `getSliderInt`, `getSliderFloat`
- `setTextContent`, `setHotkeyDisplay`

That's ~170 lines of helpers eliminated.

### 3.4. Exceptions — Fields That Need Custom Logic

Some fields cannot be purely declarative:

| Field | Reason | Handling |
|---|---|---|
| Hotkey capture (`hotkey-record`, etc.) | Needs keydown listener + display formatting | Keep as custom setup in app.js, no `data-cfg` |
| Provider cards (STT/LLM/Translate × 3 slots) | Dynamic card structure with auto-detect | Keep `collectProviderCards()` / `populateProviderCards()` in app.js |
| Audio device list (`mic-select`) | Populated dynamically from `get_audio_devices()` | Populate options first, then FormBind sets the selected value |
| Model selects | Populated from provider API | Same as audio — populate options, then bind |
| Dictionary/Replacements/History tables | CRUD tables, not config fields | Keep separate setup functions |
| Stats page | Read-only display, not config | Keep `setupStats()` |

Estimated: ~15 fields are custom, ~45 fields are declarative. The custom fields keep their existing logic in app.js.

## 4. Config Contract Simplification

### 4.1. Problem

`_config_to_web()` in `web_bridge.py` manually reshapes `AppConfig` into a flat-ish dict with ad-hoc translations (`sound_feedback`, `show_overlay`, etc.). The SPA expects nested objects like `dictation`, `speaker_lock`, `translate`, `network`, `history`, `offline` — but the bridge doesn't produce them, so those sections are always empty.

`_normalize_web_config()` does the reverse transformation with ~70 lines of manual field mapping.

### 4.2. Solution: Pass-through config + thin adapter

The SPA's config shape should match `AppConfig.to_dict()` as closely as possible. The bridge becomes a thin adapter:

```python
# web_bridge.py — simplified get_config
@_safe
def get_config(self) -> dict[str, Any]:
    data = asdict(self._config)
    # Add non-config state
    data["autostart"] = self._get_autostart()
    data["theme"] = self._load_theme()
    # Flatten language to top level for convenience
    data["language"] = data.get("ui", {}).get("language", "uk")
    return data
```

The `_config_to_web()` and `_normalize_web_config()` methods (currently ~130 lines combined) are deleted. `save_config()` receives the same nested structure and applies it directly via `_apply_dict()` with minor adjustments.

```python
# web_bridge.py — simplified save_config
@_safe
def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
    # Handle top-level shortcuts
    if "language" in data:
        data.setdefault("ui", {})["language"] = data.pop("language")
    if "theme" in data:
        self._save_theme(data.pop("theme"))
    if "autostart" in data:
        self._set_autostart(bool(data.pop("autostart")))

    self._config._apply_dict(data)
    self._write_config()
    self._write_env()

    if self._on_save is not None:
        self._on_save(restart=True)
    return {"success": True}
```

This eliminates `_config_to_web`, `_normalize_web_config`, `_apply_config`, `_apply_providers`, `_apply_audio`, `_apply_dictation`, `_apply_ui` — ~300 lines total.

### 4.3. Config Path Mapping (data-cfg → AppConfig)

The `data-cfg` paths in HTML must match `asdict(AppConfig)` keys:

| data-cfg path | AppConfig field | Type |
|---|---|---|
| `language` | `ui.language` | top-level shortcut |
| `theme` | external (translate_settings.json) | top-level shortcut |
| `autostart` | external (Windows registry) | top-level shortcut |
| `hotkey` | `hotkey` | str |
| `hotkey_mode` | `hotkey_mode` | str |
| `audio.mic_device_index` | `audio.mic_device_index` | int\|null |
| `audio.vad_aggressiveness` | `audio.vad_aggressiveness` | int |
| `audio.silence_threshold_ms` | `audio.silence_threshold_ms` | int |
| `audio.gain` | `audio.gain` | float |
| `normalization.enabled` | `normalization.enabled` | bool |
| `normalization.temperature` | `normalization.temperature` | float |
| `text_injection.method` | `text_injection.method` | str |
| `telemetry.enabled` | `telemetry.enabled` | bool |
| `ui.show_notifications` | `ui.show_notifications` | bool |
| `ui.sound_on_start` | `ui.sound_on_start` | bool |
| `ui.sound_on_stop` | `ui.sound_on_stop` | bool |
| `ui.language` | `ui.language` | str |
| `providers.stt` | `providers.stt` | list (custom) |
| `providers.llm` | `providers.llm` | list (custom) |
| `providers.translation` | `providers.translation` | list (custom) |

Fields on the SPA that don't exist in `AppConfig` (e.g., `dictation.context_aware`, `speaker_lock.*`, `network.*`, `history.*`, `offline.*`) are **aspirational UI** — the HTML shows them but the backend doesn't support them yet. Handling:
- These elements get **no `data-cfg` attribute** — FormBind ignores them entirely.
- They keep their current HTML (design preserved) but receive a CSS class `disabled-field` that greys them out and blocks interaction via `pointer-events: none; opacity: 0.5`.
- When the backend adds support for a field, the fix is: (1) add to AppConfig dataclass, (2) add `data-cfg` attribute to the HTML element, (3) remove `disabled-field` class. No JS changes needed.

Note: `dictation.injection_method` maps to `text_injection.method` and `dictation.typing_speed` maps to `text_injection.typing_delay_ms` — these two DO work and get `data-cfg` with an alias comment in HTML.

## 5. i18n Simplification

### 5.1. Current State (3 mechanisms)

1. **Server-side regex** in `settings_window.py`: reads `i18n.json`, regex-replaces `data-i18n` element text in HTML string before loading. ~40 lines.
2. **Early JS bootstrap** (inline in index.html): reads `data-initial-lang` attr, applies `_EMBEDDED_I18N` before bridge is ready.
3. **Full JS i18n** in `app.js`: `walkAndTranslate()` + `loadTranslations()` + `refreshSliderLabels()`. ~120 lines.

Three mechanisms, three copies of data, overlapping responsibility.

### 5.2. After: Single JS i18n

One mechanism: JS applies translations from `i18n.json`.

```
i18n.json (single source of truth)
    ↓
build_settings.py generates js/i18n-data.js: var _EMBEDDED_I18N = <contents of i18n.json>;
    ↓
Dev: <script src="js/i18n-data.js"> loads _EMBEDDED_I18N (no fetch, no CORS issues)
Release: bundler inlines i18n-data.js into _bundled.html
    ↓
i18n.js reads language from:
  1. document.documentElement.getAttribute('data-initial-lang')  (set by Python in release)
  2. URL param ?lang=xx  (set by Python in dev)
  3. 'uk' (default)
    ↓
Walks [data-i18n] + [data-i18n-placeholder] elements, translates
```

**Deleted:**
- Server-side regex translation in `settings_window.py` (~40 lines)
- `earlyLang()` IIFE in app.js (~20 lines)
- `_loadEmbeddedI18n()`, `loadTranslations()` in app.js (~15 lines)
- Dual `origTexts` text-matching mechanism in `walkAndTranslate()` (~30 lines)

### 5.3. i18n.js

```javascript
// i18n.js — single i18n module
var I18n = {
  lang: 'uk',
  data: {},

  init: function() {
    // Resolve language
    this.lang =
      document.documentElement.getAttribute('data-initial-lang') ||
      (new URLSearchParams(window.location.search)).get('lang') ||
      'uk';

    // _EMBEDDED_I18N is always available: loaded via <script src="js/i18n-data.js">
    // in dev, or inlined in release. No fetch() needed.
    if (typeof _EMBEDDED_I18N !== 'undefined') {
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
    // Notify backend
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.set_language(lang);
    }
  }
};
```

Slider labels (SLIDER_UK map) stay in app.js — they're controller logic, not i18n infrastructure.

## 6. app.js Refactoring

### 6.1. Current Structure (3309 lines, 1 IIFE)

```
IIFE
  ├─ earlyLang()                    → delete (moved to i18n.js)
  ├─ state variables (6)            → keep
  ├─ init()                         → simplify
  ├─ waitForBridge()                → keep
  ├─ setupTitlebar()                → keep (~20 lines)
  ├─ setupNavigation()              → keep (~35 lines)
  ├─ setupTheme()                   → keep (~25 lines)
  ├─ setTheme()                     → keep (~25 lines)
  ├─ SLIDER_UK map                  → keep (~20 lines)
  ├─ setupI18n()                    → simplify (delegate to I18n)
  ├─ loadTranslations()             → delete (in i18n.js)
  ├─ walkAndTranslate()             → delete (in i18n.js)
  ├─ refreshSliderLabels()          → keep (~15 lines)
  ├─ setLang()                      → simplify (delegate to I18n)
  ├─ loadConfig() + populateForm()  → replace with FormBind.populate()
  ├─ collectFormData()              → replace with FormBind.collect()
  ├─ saveConfig()                   → simplify (use FormBind.collect())
  ├─ setupProviderCards()           → keep (~80 lines)
  ├─ detectProvider()               → keep (~25 lines)
  ├─ collectProviderCards()         → keep (~30 lines)
  ├─ setupAudio()                   → keep (~40 lines)
  ├─ setupDictionary()              → keep (~80 lines)
  ├─ setupReplacements()            → keep (~80 lines)
  ├─ setupPerAppInstructions()      → keep (~100 lines)
  ├─ setupHistory()                 → keep (~120 lines)
  ├─ setupModals()                  → keep (~30 lines)
  ├─ setupStats()                   → keep (~40 lines)
  ├─ setupBrowserExtension()        → keep (~40 lines)
  ├─ setupFooter()                  → keep (~15 lines)
  ├─ setupSliders()                 → keep (~40 lines)
  ├─ setupToggles()                 → keep (~25 lines)
  ├─ setupHotkeyCapture()           → keep (~80 lines)
  ├─ setupSpeakerLock()             → keep (~30 lines)
  ├─ setupOffline()                 → keep (~40 lines)
  ├─ setupNetwork()                 → keep (~20 lines)
  ├─ setupImportDropZones()         → keep (~40 lines)
  ├─ 15 getter/setter helpers       → delete (replaced by FormBind)
  └─ showToast(), bindIfExists()    → keep (~20 lines)
```

### 6.2. After: ~800 lines

Deleted code:
- `earlyLang()` — 20 lines → i18n.js
- `loadTranslations()` — 15 lines → i18n.js
- `walkAndTranslate()` — 65 lines → i18n.js (simplified `I18n.apply`)
- `populateForm()` — 100 lines → `FormBind.populate(config)`
- `collectFormData()` — 130 lines → `FormBind.collect()`
- 15 getter/setter helpers — 170 lines → FormBind internals
- Duplicated `origTexts` text-matching i18n — 40 lines → gone

**Total removed from app.js: ~540 lines.**

Plus the entire inline copy in index.html is gone: **−3300 lines.**

### 6.3. init() After Refactoring

```javascript
async function init() {
  if (window.pywebview && window.pywebview.api) {
    bridgeReady = true;
    api = window.pywebview.api;
  }

  I18n.init();

  setupTitlebar();
  setupNavigation();
  setupTheme();
  setupModals();
  setupHotkeyCapture();
  setupSliders();
  setupToggles();
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

  if (bridgeReady) {
    await loadConfig();
    await loadVersion();
  }
}

async function loadConfig() {
  if (!api) return;
  try {
    var config = await api.get_config();
    if (!config) return;
    FormBind.populate(config);
    // Custom fields that need special handling
    populateProviderCards(config);
    populateAudioDevices(config);
    if (config.language) {
      I18n.setLang(config.language);
    }
    refreshSliderLabels();
  } catch (e) {
    console.warn('[config] Load error:', e.message);
  }
}

async function saveConfig() {
  if (!api) { showToast('Backend not connected', 'error'); return; }
  var data = FormBind.collect();
  // Add custom fields
  data.providers = collectAllProviders();
  data.language = I18n.lang;
  try {
    var result = await api.save_config(data);
    if (result && result.success) {
      showToast('Settings saved', 'success');
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
}
```

## 7. settings_window.py Refactoring

### 7.1. Current (245 lines)

- `show_settings()` — queue-based signal to main thread (keep)
- `run_settings_loop()` — main thread event loop (keep)
- `shutdown_settings_loop()` — shutdown signal (keep)
- `_open_webview_window()` — loads HTML, does regex i18n, creates window (~100 lines)
- `set_titlebar_theme()` — Win32 DWM API (~30 lines, keep)
- `_find_web_dir()` — finds web/ directory (keep)

### 7.2. After (~120 lines)

`_open_webview_window()` simplifies from ~100 to ~30 lines:

```python
def _open_webview_window(config, audio_capture=None, on_save=None):
    import webview
    from src.ui.web_bridge import WebBridge

    bridge = WebBridge(config, audio_capture, on_save)
    web_dir = _find_web_dir()
    if web_dir is None:
        logger.error("Cannot find web UI directory")
        return

    lang = config.ui.language if hasattr(config, "ui") else "uk"

    if _is_dev_mode():
        # Dev: load from file, JS handles everything
        url = (web_dir / "index.html").as_uri() + f"?lang={lang}"
        window = webview.create_window(
            "AI Polyglot Kit — Settings",
            url=url, js_api=bridge,
            width=900, height=640, resizable=True,
            min_size=(700, 500), background_color="#1e1e2e",
        )
    else:
        # Release: load bundled HTML with injected language
        bundled = web_dir / "_bundled.html"
        html = bundled.read_text(encoding="utf-8")
        html = html.replace(
            '<html lang="en"',
            f'<html lang="{lang}" data-initial-lang="{lang}"',
        )
        window = webview.create_window(
            "AI Polyglot Kit — Settings",
            html=html, js_api=bridge,
            width=900, height=640, resizable=True,
            min_size=(700, 500), background_color="#1e1e2e",
        )

    bridge.set_window(window)
    webview.start(debug=not getattr(sys, "frozen", False))
```

**Deleted:**
- All regex-based server-side i18n (~40 lines)
- YAML disk language debug logging (~15 lines)
- WebView2 cache clearing (~5 lines)
- `on_shown` titlebar callback → called from bridge after window loads

## 8. build_settings.py

A simple Python script that runs as a pre-PyInstaller step:

```python
"""Bundle Settings UI for release + generate i18n-data.js for dev.

Two outputs:
  1. js/i18n-data.js — auto-generated from i18n.json (used in dev via <script src>)
  2. _bundled.html   — everything inlined (used in release via html=)

Both are gitignored. Run before PyInstaller build.

Usage: python -m src.ui.build_settings
"""
import json
import re
from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"

def generate_i18n_data_js():
    """Generate js/i18n-data.js from i18n.json."""
    i18n = (WEB_DIR / "i18n.json").read_text(encoding="utf-8")
    out = WEB_DIR / "js" / "i18n-data.js"
    out.write_text(f"var _EMBEDDED_I18N = {i18n.strip()};\n", encoding="utf-8")
    print(f"Generated: {out}")

def build_bundle():
    """Bundle index.html with all external assets inlined."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    # Inline CSS: <link rel="stylesheet" href="css/styles.css">
    css = (WEB_DIR / "css" / "styles.css").read_text(encoding="utf-8")
    html = re.sub(
        r'<link\s+rel="stylesheet"\s+href="css/styles\.css"\s*/?>',
        f"<style>\n{css}\n</style>",
        html,
    )

    # Inline all JS files: <script src="js/xxx.js"></script>
    def inline_js(match):
        src = match.group(1)
        js_path = WEB_DIR / src
        if js_path.exists():
            js = js_path.read_text(encoding="utf-8")
            return f"<script>\n{js}\n</script>"
        return match.group(0)

    html = re.sub(r'<script src="(js/[^"]+)"></script>', inline_js, html)

    out = WEB_DIR / "_bundled.html"
    out.write_text(html, encoding="utf-8")
    print(f"Bundled: {out} ({len(html):,} bytes)")

def build():
    generate_i18n_data_js()
    build_bundle()

if __name__ == "__main__":
    build()
```

Add `_bundled.html` to `.gitignore`. Call from PyInstaller build script or Makefile.

## 9. web_bridge.py Refactoring

### 9.1. Methods Deleted

| Method | Lines | Reason |
|---|---|---|
| `_config_to_web()` | 38 | Replaced by `asdict()` pass-through |
| `_normalize_web_config()` | 70 | Replaced by `_apply_dict()` |
| `_apply_config()` | 5 | Orchestrator, no longer needed |
| `_apply_providers()` | 45 | Folded into simplified save_config |
| `_apply_audio()` | 12 | Handled by `_apply_dict()` |
| `_apply_dictation()` | 12 | Handled by `_apply_dict()` |
| `_apply_ui()` | 15 | Handled by `_apply_dict()` |
| **Total** | **~200** | |

### 9.2. Methods Kept (unchanged)

All bridge methods that are direct API endpoints for the SPA are unchanged:
- `get_audio_devices()`, `test_audio()`
- `detect_provider()`, `fetch_models()`
- `get_dictionary()`, `add_dictionary_term()`, `remove_dictionary_term()`, `import_dictionary()`, `export_dictionary()`
- `get_replacements()`, `add_replacement()`, `remove_replacement()`
- `get_scripts()`, `save_script()`, `get_app_rules()`, `save_app_rule()`
- `get_history()`, `delete_history()`
- `find_browsers()`, `install_extension()`
- `get_translations()`, `set_language()`
- `get_stats()`
- `check_update()`, `open_logs_folder()`, `open_url()`
- `get_version()`
- Window management: `window_minimize()`, `window_maximize()`, `window_close()`, `window_set_theme()`

### 9.3. Provider Handling

Provider slots need special handling because the SPA has a card UI with dynamic fields (API key → auto-detect provider → fetch models → select model). They cannot use `data-cfg` because there are 3 slots per category, each with 4 sub-fields.

In `save_config`, provider data arrives as:
```python
data["providers"] = {
    "stt": [{"api_key": "...", "provider": "Groq", "model": "..."}, ...],
    "llm": [...],
    "translation": [...]
}
```

This matches `ProvidersConfig` structure exactly, so `_apply_dict()` handles it. The only extra logic needed is backward-compat migration (copy first STT key to `groq.api_key`):

```python
# In save_config, after _apply_dict:
if self._config.providers.stt and self._config.providers.stt[0].get("api_key"):
    self._config.groq.api_key = self._config.providers.stt[0]["api_key"]
```

Provider auto-detection (`detect_provider`, `fetch_models`) stays as-is — they are API calls, not config fields.

## 10. PyInstaller Changes

Update `groq_dictation.spec`:

```python
datas=[
    ('config.yaml', '.'),
    ('extension', 'extension'),
    # ... other datas ...
    ('src/ui/web', 'src/ui/web'),  # unchanged — includes _bundled.html
    # REMOVE: ('src/ui/_settings_main.py', 'src/ui'),
],
hiddenimports=[
    # ... keep all ...
    # REMOVE: 'src.ui._settings_main',
],
```

The build pipeline becomes:
1. `python -m src.ui.build_settings` (creates `_bundled.html`)
2. `pyinstaller groq_dictation.spec` (packages everything including `_bundled.html`)
3. Inno Setup (unchanged)

## 11. Migration Checklist

### Phase 0: Tests (before any code changes)
- [ ] WebBridge unit tests: `get_config()` round-trip with default AppConfig
- [ ] WebBridge unit tests: `save_config()` with all sections populated
- [ ] WebBridge unit tests: language, theme, autostart handling

### Phase 1: Deduplication (smallest safe step)
- [ ] Delete inline CSS from index.html, add `<link href="css/styles.css">`
- [ ] Delete inline `_EMBEDDED_I18N` from index.html
- [ ] Delete inline JS copy from index.html, add `<script src="js/i18n-data.js">` + `<script src="js/app.js">`
- [ ] Sync diverged changes from inline JS into standalone `app.js` (3 diffs: earlyLang, error handling, getHotkeyValue)
- [ ] Verify: dev mode opens and renders correctly
- [ ] Commit

### Phase 2: Single entry point
- [ ] Delete `_settings_main.py`
- [ ] Add dev/release mode detection to `settings_window.py`
- [ ] Dev mode: use `url=` with `?lang=` param
- [ ] Release mode: use `html=` with bundled content
- [ ] Remove from `groq_dictation.spec`
- [ ] Commit

### Phase 3: i18n consolidation
- [ ] Create `js/i18n.js` with `I18n` module
- [ ] Remove `earlyLang()` from app.js
- [ ] Remove `walkAndTranslate()`, `loadTranslations()`, `_loadEmbeddedI18n()` from app.js
- [ ] Remove server-side regex i18n from `settings_window.py`
- [ ] Wire `setupI18n()` to `I18n` module
- [ ] Verify: both languages work in dev mode
- [ ] Commit

### Phase 4: Declarative form binding
- [ ] Create `js/form-bind.js` with `FormBind` module
- [ ] Add `data-cfg` attributes to all bindable HTML elements
- [ ] Replace `populateForm()` with `FormBind.populate()` + custom fields
- [ ] Replace `collectFormData()` with `FormBind.collect()` + custom fields
- [ ] Delete 15 getter/setter helper functions
- [ ] Commit

### Phase 5: Config contract simplification
- [ ] Simplify `get_config()` to use `asdict()` pass-through
- [ ] Simplify `save_config()` to use `_apply_dict()`
- [ ] Delete `_config_to_web`, `_normalize_web_config`, `_apply_*` methods
- [ ] Verify: all existing config fields save and load correctly
- [ ] Commit

### Phase 6: Build bundler
- [ ] Create `build_settings.py`
- [ ] Add `_bundled.html` to `.gitignore`
- [ ] Update `settings_window.py` release path to use `_bundled.html`
- [ ] Test bundled output renders correctly
- [ ] Update build pipeline documentation
- [ ] Commit

### Phase 7: Cleanup
- [ ] Remove empty `js/pages/` directory
- [ ] Remove debug logging from `settings_window.py`
- [ ] Run ruff, mypy, bandit on all changed Python files
- [ ] Final line count audit

## 12. Acceptance Criteria

1. **No visual changes:** Settings window looks identical in both themes (dark/light).
2. **Language switching:** EN↔UK works, persists across restart.
3. **Config round-trip:** Every field that was working before still works. No data lost on save.
4. **Dev mode:** `python -m src.ui._settings_main` equivalent works via settings_window.py with external files.
5. **Release mode:** PyInstaller build produces working bundled Settings.
6. **Line count:** Total UI code < 5000 lines (from 11400).
7. **Zero duplication:** No asset exists in more than one file.
8. **Tests pass:** WebBridge round-trip tests cover all config sections.

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| WebView2 caching in dev mode (file:// URLs) | Stale JS/CSS after edit | Add `?v=timestamp` cache-buster to `<script>`/`<link>` in dev mode, or disable cache via pywebview debug=True |
| `_apply_dict` doesn't handle all field types | Silent config corruption | Unit tests in Phase 0 cover every config section |
| Provider cards need custom collect/populate | Can't be fully declarative | Explicitly carve out as custom handling (documented in 3.4) |
| `fetch()` doesn't work on `file://` in WebView2 | i18n.json fails to load in dev | Solved: `i18n-data.js` (auto-generated from `i18n.json`) loaded via `<script src>` — no `fetch()` needed |

### 13.1. i18n Loading Without fetch()

WebView2 blocks `fetch()` on `file://` origins due to CORS. Solution: `build_settings.py generate_i18n_data_js()` converts `i18n.json` → `js/i18n-data.js` (a simple `var _EMBEDDED_I18N = ...;` wrapper). Both files are gitignored — the single source of truth is `i18n.json`, and `i18n-data.js` is a build artifact.

Developer workflow: run `python -m src.ui.build_settings` once after editing `i18n.json`. The `<script src="js/i18n-data.js">` tag in `index.html` loads the data synchronously — no fetch, no CORS, no race conditions.

## 14. Line Count Summary

| File | Before | After | Delta |
|---|---|---|---|
| `index.html` | 6124 | ~2100 | −4024 |
| `app.js` | 3309 | ~800 | −2509 |
| `form-bind.js` | 0 | ~120 | +120 |
| `i18n.js` | 0 | ~120 | +120 |
| `i18n-data.js` | 1 | 1 | 0 (auto-generated, gitignored) |
| `styles.css` | 619 | 619 | 0 |
| `i18n.json` | — | — | 0 |
| `web_bridge.py` | 1079 | ~600 | −479 |
| `settings_window.py` | 245 | ~120 | −125 |
| `_settings_main.py` | 46 | 0 | −46 |
| `build_settings.py` | 0 | ~60 | +60 |
| **Total** | **11423** | **~4540** | **−6883** |

**60% reduction.** Zero duplication. Every asset has exactly one source of truth.
