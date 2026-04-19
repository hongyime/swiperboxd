// Swiperboxd Sync — content script
// Runs on the Swiperboxd web app and listens for postMessages from the page.

console.log("[swiperboxd-ext] content script injected on", window.location.href);

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || !data.type) return;

  // Webapp requesting auth state from extension
  if (data.type === "SWIPERBOXD_GET_AUTH") {
    try {
      chrome.runtime.sendMessage({ type: "GET_WEBAPP_AUTH" }, (resp) => {
        if (chrome.runtime.lastError) {
          window.postMessage({ type: "SWIPERBOXD_AUTH_RESULT", ok: false, error: chrome.runtime.lastError.message }, window.location.origin);
          return;
        }
        window.postMessage({ type: "SWIPERBOXD_AUTH_RESULT", ...resp }, window.location.origin);
      });
    } catch (e) {
      window.postMessage({ type: "SWIPERBOXD_AUTH_RESULT", ok: false, error: e.message }, window.location.origin);
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
      console.log("[swiperboxd-ext] credentials forwarded to service worker");
    } catch (e) {
      console.warn("[swiperboxd-ext] auth forward failed:", e);
    }
  }

  // Forward swipe actions to service worker so it can write to Letterboxd
  if (data.type === "SWIPERBOXD_SWIPE") {
    if (!data.action || !data.movieSlug) return;
    console.log("[swiperboxd-ext] forwarding swipe to SW:", data.action, data.movieSlug);
    const replyFail = (error) => window.postMessage({
      type: "SWIPERBOXD_SWIPE_RESULT",
      action: data.action,
      movieSlug: data.movieSlug,
      lbSynced: false,
      error,
    }, window.location.origin);

    try {
      chrome.runtime.sendMessage({
        type: "LB_WRITE",
        action: data.action,
        movieSlug: data.movieSlug,
      }, (resp) => {
        if (chrome.runtime.lastError) {
          console.warn("[swiperboxd-ext] SW message error:", chrome.runtime.lastError.message);
          replyFail(chrome.runtime.lastError.message);
          return;
        }
        console.log("[swiperboxd-ext] LB_WRITE response:", resp);
        window.postMessage({
          type: "SWIPERBOXD_SWIPE_RESULT",
          action: data.action,
          movieSlug: data.movieSlug,
          lbSynced: resp?.ok === true,
          error: resp?.error || null,
        }, window.location.origin);
      });
    } catch (e) {
      console.warn("[swiperboxd-ext] swipe forward failed:", e);
      replyFail(e.message);
    }
  }
});

// Advertise presence so the web app knows the extension is installed.
window.postMessage({ type: "SWIPERBOXD_EXT_PRESENT" }, window.location.origin);
