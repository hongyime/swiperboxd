// Swiperboxd Sync — content script
// Runs on the Swiperboxd web app and listens for postMessages from the page.

const CONTENT_LOG_PREFIX = "[swiperboxd-ext/content]";

function contentLog(message, meta) {
  if (meta !== undefined) console.log(`${CONTENT_LOG_PREFIX} ${message}`, meta);
  else console.log(`${CONTENT_LOG_PREFIX} ${message}`);
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
          const error = chrome.runtime.lastError.message;
          contentLog("auth bridge runtime error", { requestId: data.requestId || null, error });
          window.postMessage({
            type: "SWIPERBOXD_AUTH_RESULT",
            ok: false,
            error,
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
          console.warn(`${CONTENT_LOG_PREFIX} SW message error:`, chrome.runtime.lastError.message);
          replyFail(chrome.runtime.lastError.message);
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
});

// Advertise presence so the web app knows the extension is installed.
window.postMessage({
  type: "SWIPERBOXD_EXT_PRESENT",
  source: "content-script",
  href: window.location.href,
  emittedAt: Date.now(),
}, window.location.origin);
contentLog("presence signal posted", { href: window.location.href, origin: window.location.origin });
