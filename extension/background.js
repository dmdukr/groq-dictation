/* background.js — Service Worker for AI Polyglot Kit extension */

const API_BASE = "http://127.0.0.1:19378";
const BATCH_SIZE = 50;
const MAX_PARALLEL = 3;

/* ---------- Token management ---------- */

async function getToken() {
  const { apkToken } = await chrome.storage.local.get("apkToken");
  if (apkToken) return apkToken;
  return fetchToken();
}

async function fetchToken() {
  const resp = await fetch(`${API_BASE}/token`, { signal: AbortSignal.timeout(5000) });
  if (!resp.ok) throw new Error("Failed to fetch token");
  const data = await resp.json();
  const token = data.token || data.access_token || "";
  if (token) await chrome.storage.local.set({ apkToken: token });
  return token;
}

async function clearToken() {
  await chrome.storage.local.remove("apkToken");
}

/* ---------- Health check ---------- */

async function healthCheck() {
  try {
    const resp = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    return resp.ok;
  } catch {
    return false;
  }
}

/* ---------- Translation ---------- */

async function translateBatch(texts, lang, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(`${API_BASE}/translate`, {
    method: "POST",
    headers,
    body: JSON.stringify({ texts, lang }),
    signal: AbortSignal.timeout(60000),
  });

  if (resp.status === 401) {
    // Token expired — clear and re-fetch
    await clearToken();
    const newToken = await fetchToken();
    const retry = await fetch(`${API_BASE}/translate`, {
      method: "POST",
      headers: { ...headers, Authorization: `Bearer ${newToken}` },
      body: JSON.stringify({ texts, lang }),
      signal: AbortSignal.timeout(60000),
    });
    if (!retry.ok) throw new Error(`Translation failed: ${retry.status}`);
    return retry.json();
  }

  if (!resp.ok) throw new Error(`Translation failed: ${resp.status}`);
  return resp.json();
}

function chunkArray(arr, size) {
  const chunks = [];
  for (let i = 0; i < arr.length; i += size) {
    chunks.push(arr.slice(i, i + size));
  }
  return chunks;
}

async function translateAll(texts, lang) {
  const token = await getToken();
  const batches = chunkArray(texts, BATCH_SIZE);
  const results = [];
  let done = 0;
  const total = batches.length;

  // Process batches in groups of MAX_PARALLEL
  for (let i = 0; i < batches.length; i += MAX_PARALLEL) {
    const group = batches.slice(i, i + MAX_PARALLEL);
    const groupResults = await Promise.all(
      group.map((batch) => translateBatch(batch, lang, token))
    );

    for (const res of groupResults) {
      const translated = res.translations || res.texts || res;
      if (Array.isArray(translated)) {
        results.push(...translated);
      }
      done++;
      // Report progress — fire and forget
      chrome.runtime.sendMessage({ action: "progress", done, total }).catch(() => {});
    }
  }

  return results;
}

/* ---------- Message handler ---------- */

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "health") {
    healthCheck().then((ok) => sendResponse({ connected: ok }));
    return true; // async response
  }

  if (msg.action === "translate") {
    translateAll(msg.texts, msg.lang)
      .then((translations) => sendResponse({ translations }))
      .catch((err) => sendResponse({ error: err.message }));
    return true;
  }

  if (msg.action === "clearToken") {
    clearToken().then(() => sendResponse({ ok: true }));
    return true;
  }
});
