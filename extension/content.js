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

contentLog("content script injected", { href: window.location.href, origin: window.location.origin });

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || !data.type) return;

  // Webapp requesting auth state from extension
  if (data.type === "SWIPERBOXD_GET_AUTH") {
    contentLog("auth request received", {
      requestId: data.requestId || null,
      sentAt: data.sentAt || null,
      href: window.location.href,
    });
    try {
      chrome.runtime.sendMessage({ type: "GET_WEBAPP_AUTH" }, (resp) => {
        if (chrome.runtime.lastError) {
          const mapped = normalizeRuntimeError(chrome.runtime.lastError.message, 'auth bridge runtime error');
          contentLog("auth bridge runtime error", { requestId: data.requestId || null, error: mapped });
          window.postMessage({
            type: "SWIPERBOXD_AUTH_RESULT",
            ok: false,
            error: mapped.message,
            errorCode: mapped.code,
            originalError: mapped.original,
            requestId: data.requestId || null,
          }, window.location.origin);
          return;
        }
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
    } catch (e) {
      contentLog("auth bridge threw", { requestId: data.requestId || null, error: e.message });
      window.postMessage({
        type: "SWIPERBOXD_AUTH_RESULT",
        ok: false,
        error: e.message,
        requestId: data.requestId || null,
      }, window.location.origin);
    }
    return;
  }

  // Forward auth credentials to service worker
  if (data.type === "SWIPERBOXD_AUTH") {
    if (!data.username || !data.sessionToken) return;
    try {
      chrome.runtime.sendMessage({
        type: "SWIPERBOXD_AUTH",
        username: data.username,
        sessionToken: data.sessionToken,
        apiBase: data.apiBase || window.location.origin,
      });
      contentLog("credentials forwarded to service worker", {
        username: data.username,
        apiBase: data.apiBase || window.location.origin,
      });
    } catch (e) {
      console.warn(`${CONTENT_LOG_PREFIX} auth forward failed:`, e);
    }
  }

  // Forward swipe actions to service worker so it can write to Letterboxd
  if (data.type === "SWIPERBOXD_SWIPE") {
    if (!data.action || !data.movieSlug) return;
    contentLog("forwarding swipe to service worker", {
      requestId: data.requestId || null,
      action: data.action,
      movieSlug: data.movieSlug,
    });
    const replyFail = (error) => window.postMessage({
      type: "SWIPERBOXD_SWIPE_RESULT",
      action: data.action,
      movieSlug: data.movieSlug,
      lbSynced: false,
      error,
      requestId: data.requestId || null,
    }, window.location.origin);

    try {
      chrome.runtime.sendMessage({
        type: "LB_WRITE",
        action: data.action,
        movieSlug: data.movieSlug,
      }, (resp) => {
        if (chrome.runtime.lastError) {
          const mapped = normalizeRuntimeError(chrome.runtime.lastError.message, 'service worker message error');
          console.warn(`${CONTENT_LOG_PREFIX} SW message error:`, mapped);
          replyFail(mapped.message);
          return;
        }
        contentLog("LB_WRITE response", {
          requestId: data.requestId || null,
          action: data.action,
          movieSlug: data.movieSlug,
          ok: resp?.ok === true,
          error: resp?.error || null,
        });
        window.postMessage({
          type: "SWIPERBOXD_SWIPE_RESULT",
          action: data.action,
          movieSlug: data.movieSlug,
          lbSynced: resp?.ok === true,
          error: resp?.error || null,
          requestId: data.requestId || null,
        }, window.location.origin);
      });
    } catch (e) {
      console.warn(`${CONTENT_LOG_PREFIX} swipe forward failed:`, e);
      replyFail(e.message);
    }
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

    try {
      chrome.runtime.sendMessage({
        type: "LB_CROSS_SYNC",
        maxPushPerKind: data.maxPushPerKind,
        historyMaxPages: data.historyMaxPages,
      }, (resp) => {
        if (chrome.runtime.lastError) {
          const mapped = normalizeRuntimeError(chrome.runtime.lastError.message, 'cross-sync runtime error');
          contentLog("LB_CROSS_SYNC runtime error", {
            requestId: data.requestId || null,
            error: mapped,
          });
          reply({
            ok: false,
            error: mapped.message,
            errorCode: mapped.code,
            originalError: mapped.original,
            summary: null,
          });
          return;
        }

        contentLog("LB_CROSS_SYNC response", {
          requestId: data.requestId || null,
          ok: resp?.ok === true,
          error: resp?.error || null,
        });
        reply({
          ok: resp?.ok === true,
          error: resp?.error || null,
          summary: resp || null,
        });
      });
    } catch (e) {
      contentLog("LB_CROSS_SYNC threw", {
        requestId: data.requestId || null,
        error: e.message,
      });
      reply({ ok: false, error: e.message, summary: null });
    }
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
