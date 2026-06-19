// Swiperboxd Sync — content script
// Runs on the Swiperboxd web app and listens for postMessages from the page.

const CONTENT_LOG_PREFIX = "[swiperboxd-ext/content]";

function contentLog(message, meta) {
  if (meta !== undefined) console.log(`${CONTENT_LOG_PREFIX} ${message}`, meta);
  else console.log(`${CONTENT_LOG_PREFIX} ${message}`);
}

function normalizeRuntimeError(rawMessage, fallback = 'Extension runtime error') {
  const original = String(rawMessage || '').trim();
  const lower = original.toLowerCase();

  // Special case for context invalidation (extension updated/reloaded)
  if (lower.includes("extension context invalidated") || lower.includes("context invalidated")) {
    return {
      code: 'context_invalidated',
      message: 'Extension context invalidated (probably updated or reloaded). Please refresh this page to reconnect.',
      original,
    };
  }

  if (lower.includes('unknown error occurred when fetching the script')) {
    return {
      code: 'script_fetch_failed',
      message:
        'Chrome failed to load an extension script. Reload the extension on chrome://extensions and refresh this page.',
      original,
    };
  }

  if (lower.includes('receiving end does not exist')) {
    return {
      code: 'receiving_end_missing',
      message: 'Extension background is not ready. Open the extension popup once, then retry.',
      original,
    };
  }

  if (lower.includes('message port closed before a response was received')) {
    return {
      code: 'message_port_closed',
      message: 'Extension worker stopped before replying. Reopen the extension popup and retry.',
      original,
    };
  }

  return {
    code: 'runtime_error',
    message: original || fallback,
    original,
  };
}

/**
 * Robust wrapper for chrome.runtime.sendMessage that handles 
 * context invalidation and disconnect errors gracefully.
 */
function safeSendMessage(message, callback) {
  const requestId = message.requestId || 'unknown';

  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage) {
    const mapped = normalizeRuntimeError("Extension context invalidated");
    contentLog("ERROR: " + mapped.message, { requestId });
    if (callback) callback({ ok: false, error: mapped.message, errorCode: mapped.code });
    return false;
  }

  try {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        const mapped = normalizeRuntimeError(chrome.runtime.lastError.message);
        if (!(message.type === 'PING' && mapped.code === 'receiving_end_missing')) {
          contentLog("Runtime error:", { requestId, error: mapped });
        }
        if (callback) callback({ ok: false, error: mapped.message, errorCode: mapped.code });
        return;
      }
      if (callback) callback(response);
    });
    return true;
  } catch (e) {
    const mapped = normalizeRuntimeError(e.message);
    contentLog("Exception in sendMessage:", { requestId, error: mapped });
    if (callback) callback({ ok: false, error: mapped.message, errorCode: mapped.code });
    return false;
  }
}

contentLog("content script injected", { href: window.location.href, origin: window.location.origin });

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || !data.type) return;

  // Heartbeat to keep extension service worker alive
  if (data.type === "SWIPERBOXD_PING") {
    safeSendMessage({ type: "PING" });
    return;
  }

  // Webapp requesting auth state from extension
  if (data.type === "SWIPERBOXD_GET_AUTH") {
    contentLog("auth request received", {
      requestId: data.requestId || null,
      sentAt: data.sentAt || null,
      href: window.location.href,
    });
    
    safeSendMessage({ type: "GET_WEBAPP_AUTH", requestId: data.requestId }, (resp) => {
      contentLog("auth bridge response", {
        requestId: data.requestId || null,
        ok: resp?.ok,
        username: resp?.username || null,
        error: resp?.error || null,
      });
      window.postMessage({
        type: "SWIPERBOXD_AUTH_RESULT",
        ...resp,
        requestId: data.requestId || null,
      }, window.location.origin);
    });
    return;
  }

  // Forward auth credentials to service worker
  if (data.type === "SWIPERBOXD_AUTH") {
    if (!data.username || !data.sessionToken) return;
    safeSendMessage({
      type: "SWIPERBOXD_AUTH",
      username: data.username,
      sessionToken: data.sessionToken,
      apiBase: data.apiBase || window.location.origin,
      requestId: data.requestId,
    }, (resp) => {
      if (resp?.ok) {
        contentLog("credentials forwarded to service worker", {
          username: data.username,
          apiBase: data.apiBase || window.location.origin,
        });
      }
    });
  }

  // Forward swipe actions to service worker so it can write to Letterboxd
  if (data.type === "SWIPERBOXD_SWIPE") {
    if (!data.action || !data.movieSlug) return;
    contentLog("forwarding swipe to service worker", {
      requestId: data.requestId || null,
      action: data.action,
      movieSlug: data.movieSlug,
    });

    const reply = (payload) => window.postMessage({
      type: "SWIPERBOXD_SWIPE_RESULT",
      action: data.action,
      movieSlug: data.movieSlug,
      requestId: data.requestId || null,
      ...payload,
    }, window.location.origin);

    safeSendMessage({
      type: "LB_WRITE",
      action: data.action,
      movieSlug: data.movieSlug,
      requestId: data.requestId,
    }, (resp) => {
      contentLog("LB_WRITE response", {
        requestId: data.requestId || null,
        action: data.action,
        movieSlug: data.movieSlug,
        ok: resp?.ok === true,
        error: resp?.error || null,
      });
      reply({
        lbSynced: resp?.ok === true,
        error: resp?.error || null,
        errorCode: resp?.code || null,
      });
    });
  }

  // Trigger initial bidirectional cross-sync from the web app.
  if (data.type === "SWIPERBOXD_CROSS_SYNC") {
    contentLog("forwarding cross-sync request to service worker", {
      requestId: data.requestId || null,
      maxPushPerKind: data.maxPushPerKind || null,
      historyMaxPages: data.historyMaxPages || null,
    });

    const reply = (payload) => window.postMessage({
      type: "SWIPERBOXD_CROSS_SYNC_RESULT",
      requestId: data.requestId || null,
      ...payload,
    }, window.location.origin);

    safeSendMessage({
      type: "LB_CROSS_SYNC",
      maxPushPerKind: data.maxPushPerKind,
      historyMaxPages: data.historyMaxPages,
      requestId: data.requestId,
      force: data.force,
    }, (resp) => {
      contentLog("LB_CROSS_SYNC response", {
        requestId: data.requestId || null,
        ok: resp?.ok === true,
        error: resp?.error || null,
      });
      reply({
        ok: resp?.ok === true,
        error: resp?.error || null,
        errorCode: resp?.code || null,
        originalError: resp?.original || null,
        summary: resp?.ok ? resp : null,
      });
    });
  }
});

// Advertise presence so the web app knows the extension is installed.
window.postMessage({
  type: "SWIPERBOXD_EXT_PRESENT",
  source: "content-script",
  href: window.location.href,
  emittedAt: Date.now(),
}, window.location.origin);
contentLog("presence signal posted", { href: window.location.href, origin: window.location.origin });
