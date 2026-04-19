// Swiperboxd Sync — content script
// Runs on the Swiperboxd web app and listens for postMessages from the page.

console.log("[swiperboxd-ext] content script injected on", window.location.href);

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || !data.type) return;

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
