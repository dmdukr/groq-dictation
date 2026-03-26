/* popup.js — AI Polyglot Kit extension popup logic */

(function () {
  "use strict";

  const statusEl = document.getElementById("status");
  const statusText = document.getElementById("status-text");
  const controlsEl = document.getElementById("controls");
  const langSelect = document.getElementById("lang-select");
  const translateBtn = document.getElementById("translate-btn");
  const progressEl = document.getElementById("progress");

  let isTranslated = false;

  /* ---------- Badge helpers ---------- */

  function setBadge(text, color) {
    chrome.action.setBadgeText({ text });
    if (color) chrome.action.setBadgeBackgroundColor({ color });
  }

  function setTitle(title) {
    chrome.action.setTitle({ title });
  }

  /* ---------- Ensure content script ---------- */

  async function ensureContentScript(tabId) {
    try {
      await chrome.tabs.sendMessage(tabId, { action: "getState" });
      return true;
    } catch {
      // Content script not injected, inject it now
      try {
        await chrome.scripting.executeScript({
          target: { tabId },
          files: ["content.js"],
        });
        // Wait for content script to initialize its message listener
        await new Promise((r) => setTimeout(r, 200));
        // Verify it's ready
        await chrome.tabs.sendMessage(tabId, { action: "getState" });
        return true;
      } catch (e) {
        console.error("[APK:popup] Cannot inject content script:", e);
        return false;
      }
    }
  }

  /* ---------- Init ---------- */

  async function init() {
    // Restore saved language
    const { apkLang } = await chrome.storage.local.get("apkLang");
    if (apkLang) langSelect.value = apkLang;

    // Save language on change
    langSelect.addEventListener("change", () => {
      chrome.storage.local.set({ apkLang: langSelect.value });
    });

    // Check health
    const resp = await chrome.runtime.sendMessage({ action: "health" });
    if (resp && resp.connected) {
      setConnected();
      setBadge("T", "#607D8B");
      await checkPageState();
    } else {
      setDisconnected();
      setBadge("", "");
    }

    // Button handler
    translateBtn.addEventListener("click", onButtonClick);
  }

  /* ---------- Status ---------- */

  function setConnected() {
    statusEl.className = "status connected";
    statusText.textContent = "Connected to AI Polyglot Kit";
    controlsEl.classList.remove("hidden");
  }

  function setDisconnected() {
    statusEl.className = "status disconnected";
    statusText.textContent = "App not running \u2014 launch AI Polyglot Kit";
    controlsEl.classList.add("hidden");
  }

  /* ---------- Page state ---------- */

  async function checkPageState() {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id) return;

      const resp = await chrome.tabs.sendMessage(tab.id, { action: "getState" });
      if (resp && resp.translated) {
        isTranslated = true;
        setRevertMode();
        const lang = langSelect.value || "?";
        setBadge(lang, "#4CAF50");
        setTitle(`AI Polyglot Kit — translated to ${lang}`);
      } else {
        isTranslated = false;
        setTranslateMode();
      }
    } catch {
      isTranslated = false;
      setTranslateMode();
    }
  }

  /* ---------- Button modes ---------- */

  function setTranslateMode() {
    translateBtn.textContent = "Translate page";
    translateBtn.className = "btn primary";
  }

  function setRevertMode() {
    translateBtn.textContent = "Show original";
    translateBtn.className = "btn revert";
  }

  /* ---------- Actions ---------- */

  async function onButtonClick() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    console.log("[APK:popup] Button click, tab:", tab?.id, "url:", tab?.url);
    if (!tab || !tab.id) {
      console.error("[APK:popup] No active tab!");
      return;
    }

    if (isTranslated) {
      // Revert
      console.log("[APK:popup] Reverting...");
      try {
        await chrome.tabs.sendMessage(tab.id, { action: "revert" });
      } catch (e) {
        console.error("[APK:popup] Revert failed:", e);
      }
      isTranslated = false;
      setTranslateMode();
      setBadge("T", "#607D8B");
      setTitle("AI Polyglot Kit");
      progressEl.classList.add("hidden");
    } else {
      // Translate
      const lang = langSelect.value;
      console.log("[APK:popup] Translating, lang=", lang);
      translateBtn.disabled = true;
      progressEl.textContent = "Translating...";
      progressEl.classList.remove("hidden");
      setBadge("0%", "#FF9800");
      setTitle(`AI Polyglot Kit — translating to ${lang}...`);

      // Ensure content script is injected
      const ready = await ensureContentScript(tab.id);
      if (!ready) {
        progressEl.textContent = "Error: cannot access this page";
        translateBtn.disabled = false;
        setBadge("!", "#F44336");
        return;
      }

      try {
        const result = await chrome.tabs.sendMessage(tab.id, {
          action: "startTranslation",
          lang,
        });
        console.log("[APK:popup] Content script response:", result);

        if (result && result.ok && result.stats && result.stats.applied === 0 && result.stats.total === 0) {
          // Content script returned OK but translated nothing — likely stale script
          console.warn("[APK:popup] Zero translations applied, retrying with fresh injection...");
          // Force re-inject content script
          try {
            await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              func: () => { window.__apkContentScriptLoaded = false; },
            });
            await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              files: ["content.js"],
            });
            await new Promise((r) => setTimeout(r, 200));
            const retry = await chrome.tabs.sendMessage(tab.id, {
              action: "startTranslation",
              lang,
            });
            console.log("[APK:popup] Retry response:", retry);
          } catch (retryErr) {
            console.error("[APK:popup] Retry failed:", retryErr);
          }
        }

        isTranslated = true;
        setRevertMode();
        progressEl.textContent = "Done!";
        setBadge(lang, "#4CAF50");
        setTitle(`AI Polyglot Kit — translated to ${lang}`);
        setTimeout(() => progressEl.classList.add("hidden"), 2000);
      } catch (e) {
        progressEl.textContent = "Error: " + e.message;
        console.error("[APK:popup] Translation failed:", e);
        setBadge("!", "#F44336");
      }

      translateBtn.disabled = false;
    }
  }

  /* ---------- Progress listener ---------- */

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === "progress") {
      const pct = Math.round((msg.done / msg.total) * 100);
      progressEl.textContent = `Translating... ${pct}%`;
      progressEl.classList.remove("hidden");
      setBadge(`${pct}%`, "#FF9800");
    }
  });

  /* ---------- Start ---------- */

  init();
})();
