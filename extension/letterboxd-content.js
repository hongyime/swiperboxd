// Swiperboxd Sync — Letterboxd content script
// Adds a floating "Sync to Swiperboxd" button on list pages and film pages.
// Uses DOM access (runs in the page context) so it's resilient to server-
// rendering quirks the background worker's regex-on-HTML approach might miss.

(function () {
  const LIST_PATH_RE = /^\/[^/]+\/list\/[^/]+\//;
  const FILM_PATH_RE = /^\/film\/[^/]+\/?$/;

  function detectPageKind() {
    const path = location.pathname.replace(/\/+$/, "/");
    if (LIST_PATH_RE.test(path)) return "list";
    if (FILM_PATH_RE.test(path)) return "film";
    return null;
  }

  function buildButton(label) {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.setAttribute("data-sbx", "sync-btn");
    Object.assign(btn.style, {
      position: "fixed",
      right: "20px",
      bottom: "20px",
      zIndex: "99999",
      padding: "10px 16px",
      background: "#ff8000",
      color: "#000",
      border: "none",
      borderRadius: "8px",
      fontFamily: "system-ui, sans-serif",
      fontSize: "13px",
      fontWeight: "600",
      cursor: "pointer",
      boxShadow: "0 4px 14px rgba(0,0,0,0.4)",
    });
    btn.addEventListener("mouseenter", () => { btn.style.background = "#e27400"; });
    btn.addEventListener("mouseleave", () => { btn.style.background = "#ff8000"; });
    return btn;
  }

  function injectList() {
    if (document.querySelector('[data-sbx="sync-btn"]')) return;
    const btn = buildButton("Sync list to Swiperboxd");
    btn.addEventListener("click", async () => {
      btn.textContent = "Syncing…";
      btn.disabled = true;
      try {
        const resp = await chrome.runtime.sendMessage({
          type: "SCRAPE_LIST",
          listUrl: location.origin + location.pathname,
          fetchMetadata: false,
        });
        btn.textContent = resp?.ok ? `Synced ${resp.found} films` : `Failed: ${resp?.error || "?"}`;
      } catch (e) {
        btn.textContent = `Failed: ${e.message}`;
      }
      setTimeout(() => {
        btn.textContent = "Sync list to Swiperboxd";
        btn.disabled = false;
      }, 4000);
    });
    document.body.appendChild(btn);
  }

  function extractFilmSlug() {
    const parts = location.pathname.replace(/\/+$/, "").split("/");
    // /film/<slug>/
    return parts[2] || null;
  }

  function injectFilm() {
    if (document.querySelector('[data-sbx="sync-btn"]')) return;
    const slug = extractFilmSlug();
    if (!slug) return;
    const btn = buildButton("Save metadata to Swiperboxd");
    btn.addEventListener("click", async () => {
      btn.textContent = "Saving…";
      btn.disabled = true;
      try {
        const resp = await chrome.runtime.sendMessage({ type: "SCRAPE_MOVIES", slugs: [slug] });
        btn.textContent = resp?.ok ? "Saved" : `Failed: ${resp?.error || "?"}`;
      } catch (e) {
        btn.textContent = `Failed: ${e.message}`;
      }
      setTimeout(() => {
        btn.textContent = "Save metadata to Swiperboxd";
        btn.disabled = false;
      }, 3000);
    });
    document.body.appendChild(btn);
  }

  const kind = detectPageKind();
  if (kind === "list") injectList();
  else if (kind === "film") injectFilm();
})();
