// Swiperboxd Sync — content script
// Runs on the Swiperboxd web app and listens for a postMessage from the page
// after the user signs in, so we can stash credentials for the service worker.

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || data.type !== "SWIPERBOXD_AUTH") return;
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
    console.warn("[swiperboxd-ext] forward failed:", e);
  }
});

// Advertise presence so the web app can skip manual paste instructions.
window.postMessage({ type: "SWIPERBOXD_EXT_PRESENT" }, window.location.origin);
