// ui-core.js — Theme, navigation, modals, toasts, slider labels
// Extracted from app.js to separate UI infrastructure from business logic.

var UiCore = {
  /** Current theme ('dark' | 'light'). */
  theme: 'dark',

  /** Dynamic stylesheet for pseudo-element styling (Chrome limitation). */
  dynamicStyle: null,

  /**
   * Initialize UI core — theme, navigation, modals.
   * @param {Object|null} bootstrap - Bootstrap data from backend
   */
  init: function (bootstrap) {
    this.dynamicStyle = document.createElement('style');
    document.head.appendChild(this.dynamicStyle);

    // Apply theme from bootstrap or default
    var theme = (bootstrap && bootstrap.theme) || 'dark';
    this.setTheme(theme);

    var self = this;
    var themeSelect = document.getElementById('theme-select');
    if (themeSelect) {
      themeSelect.addEventListener('change', function () {
        self.setTheme(this.value);
      });
    }

    this._setupNavigation();
    this._setupModals();
  },

  /**
   * Apply theme to the document.
   * @param {string} theme - 'dark' or 'light'
   */
  setTheme: function (theme) {
    // Normalize: 'auto' or unknown → 'dark'
    if (theme !== 'dark' && theme !== 'light') theme = 'dark';
    this.theme = theme;
    document.documentElement.setAttribute('data-theme', theme);
    document.body.style.background = theme === 'light' ? '#ddd8ce' : '#16161e';

    // Inject CSS for range input pseudo-elements (only way Chrome respects them)
    var track = theme === 'light' ? '#d4cec4' : '#333348';
    var thumb = theme === 'light' ? '#a07010' : '#c49520';
    if (this.dynamicStyle) {
      this.dynamicStyle.textContent =
        'input[type="range"]::-webkit-slider-runnable-track{background:' + track + '!important}' +
        'input[type="range"]::-webkit-slider-thumb{background:' + thumb + '!important}';
    }

    // Repaint native Windows title bar to match theme
    if (window.pywebview && window.pywebview.api && window.pywebview.api.window_set_theme) {
      window.pywebview.api.window_set_theme(theme);
    }

    try {
      localStorage.setItem('apk_theme', theme);
    } catch (e) { /* ignore */ }
  },

  /**
   * Show a toast notification.
   * @param {string} message - Toast message text
   * @param {string} [type] - 'success', 'error', or 'info'
   */
  toast: function (message, type) {
    var container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.style.cssText =
        'position:fixed;top:16px;right:16px;z-index:10000;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
      document.body.appendChild(container);
    }

    var borderColor = '#c49520';
    if (type === 'error') borderColor = '#f44336';
    if (type === 'success') borderColor = '#4caf50';

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + (type || 'info');
    toast.style.cssText =
      'padding:10px 18px;border-radius:8px;font-size:13px;pointer-events:auto;' +
      'opacity:0;transform:translateX(20px);transition:all 0.3s ease;' +
      'background:var(--card-bg, #1e1e2e);border:1px solid ' + borderColor + ';' +
      'color:var(--text-primary, #e0dcd3);box-shadow:0 4px 12px rgba(0,0,0,0.3);';
    toast.textContent = message;

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(function () {
      toast.style.opacity = '1';
      toast.style.transform = 'translateX(0)';
    });

    // Auto-dismiss
    setTimeout(function () {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(20px)';
      setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 300);
    }, 3000);
  },

  /**
   * Show a modal by its element id.
   * @param {string} id - Modal element id
   */
  openModal: function (id) {
    var modal = document.getElementById(id);
    if (modal) modal.classList.add('show');
  },

  /**
   * Hide a modal by its element id.
   * @param {string} id - Modal element id
   */
  closeModal: function (id) {
    var modal = document.getElementById(id);
    if (modal) modal.classList.remove('show');
  },

  /** Set up sidebar navigation and page switching. */
  _setupNavigation: function () {
    var sidebarItems = document.querySelectorAll('.sidebar-item');
    var contentPages = document.querySelectorAll('.content');

    sidebarItems.forEach(function (item) {
      item.addEventListener('click', function () {
        var targetPage = item.dataset.page;
        if (!targetPage) return;

        // Update sidebar active state
        sidebarItems.forEach(function (i) { i.classList.remove('active'); });
        item.classList.add('active');

        // Show target page, hide others
        contentPages.forEach(function (c) { c.classList.remove('active'); });
        var page = document.getElementById('page-' + targetPage);
        if (page) page.classList.add('active');

        // Remember last active page
        try {
          localStorage.setItem('apk_last_page', targetPage);
        } catch (e) { /* localStorage may be unavailable in file:// */ }
      });
    });

    // Restore last active page
    try {
      var lastPage = localStorage.getItem('apk_last_page');
      if (lastPage) {
        var target = document.querySelector('.sidebar-item[data-page="' + lastPage + '"]');
        if (target) target.click();
      }
    } catch (e) { /* ignore */ }
  },

  /** Set up generic modal infrastructure (close/open/overlay/escape). */
  _setupModals: function () {
    var self = this;

    // Close buttons with data-close attribute
    document.querySelectorAll('[data-close]').forEach(function (el) {
      el.addEventListener('click', function () {
        self.closeModal(el.dataset.close);
      });
    });

    // Open buttons with data-open attribute
    document.querySelectorAll('[data-open]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        self.openModal(btn.getAttribute('data-open'));
      });
    });

    // Click overlay to close modal
    document.querySelectorAll('.modal-overlay').forEach(function (overlay) {
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) overlay.classList.remove('show');
      });
    });

    // Escape key to close topmost modal
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        var modals = document.querySelectorAll('.modal-overlay.show');
        if (modals.length) {
          modals[modals.length - 1].classList.remove('show');
        }
      }
    });
  }
};


// ---- Slider labels (global, used by app.js) ----

/**
 * Slider label translations (Ukrainian).
 * Keys are the English labels used in slider value displays.
 */
var SLIDER_UK = {
  'Whisper': '\u0428\u0435\u043f\u0456\u0442',
  'Soft': '\u0422\u0438\u0445\u0438\u0439',
  'Quiet': '\u0422\u0438\u0445\u043e',
  'Clear voice': '\u0427\u0456\u0442\u043a\u0438\u0439 \u0433\u043e\u043b\u043e\u0441',
  'Loud': '\u0413\u0443\u0447\u043d\u0438\u0439',
  'Maximum': '\u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c',
  'Fastest': '\u041d\u0430\u0439\u0448\u0432\u0438\u0434\u0448\u0435',
  'Fast': '\u0428\u0432\u0438\u0434\u043a\u043e',
  'Good': '\u0414\u043e\u0431\u0440\u0435',
  'High': '\u0412\u0438\u0441\u043e\u043a\u0435',
  'Best': '\u041d\u0430\u0439\u043a\u0440\u0430\u0449\u0435',
  'Minimal': '\u041c\u0456\u043d\u0456\u043c\u0430\u043b\u044c\u043d\u0430',
  'Low': '\u041d\u0438\u0437\u044c\u043a\u0430',
  'Medium': '\u0421\u0435\u0440\u0435\u0434\u043d\u044f',
  'Balanced': '\u0417\u0431\u0430\u043b\u0430\u043d\u0441\u043e\u0432\u0430\u043d\u0435',
  'Very high': '\u0414\u0443\u0436\u0435 \u0432\u0438\u0441\u043e\u043a\u0435',
  'Stable': '\u0421\u0442\u0430\u0431\u0456\u043b\u044c\u043d\u043e',
  'Creative': '\u041a\u0440\u0435\u0430\u0442\u0438\u0432\u043d\u043e'
};

/**
 * Get slider label in the current language.
 * @param {string} key - English label
 * @returns {string}
 */
function sliderLabel(key) {
  if (I18n.lang === 'uk' && SLIDER_UK[key]) return SLIDER_UK[key];
  return key;
}

/**
 * Refresh slider value labels after a language change.
 */
function refreshSliderLabels() {
  var sliderConfigs = [
    { sliderId: 'cpu-slider', valueId: 'cpu-value', labels: ['Low', 'Balanced', 'High', 'Very high', 'Maximum'] },
    { sliderId: 'beam-slider', valueId: 'beam-value', labels: ['Fastest', 'Fast', 'Good', 'High', 'Best'] },
    { sliderId: 'whisper-temp-slider', valueId: 'whisper-temp-value', labels: ['Minimal', 'Low', 'Medium', 'High', 'Maximum'] },
    { sliderId: 'rms-slider', valueId: 'rms-value', labels: ['Whisper', 'Soft', 'Clear voice', 'Loud', 'Maximum'] }
  ];
  sliderConfigs.forEach(function (cfg) {
    var slider = document.getElementById(cfg.sliderId);
    var display = document.getElementById(cfg.valueId);
    if (slider && display) {
      display.textContent = sliderLabel(cfg.labels[slider.value]);
    }
  });
}
