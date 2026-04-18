// Swiperboxd Sync — popup controller

const el = (id) => document.getElementById(id);
const DEFAULT_API_BASE = "https://swiperboxd.vercel.app";

async function getStorage() {
  return await chrome.storage.local.get([
    "apiBase", "username", "sessionToken", "autoSync", "lastSync",
    "syncHistory", "discoverLists", "fillLists",
    "discoverPages", "fillMaxLists", "fillListPages",
  ]);
}
async function setStorage(obj) { await chrome.storage.local.set(obj); }

function logLine(target, msg) {
  const log = el(target);
  const time = new Date().toLocaleTimeString();
  log.textContent = `[${time}] ${msg}\n` + log.textContent;
}

async function checkLetterboxdSession() {
  try {
    const cookie = await chrome.cookies.get({ url: "https://letterboxd.com", name: "letterboxd.user.CURRENT" });
    if (cookie && cookie.value) {
      el("lb-dot").className = "dot ok";
      el("lb-status").textContent = "Signed in";
      return cookie.value;
    }
  } catch { /* ignore */ }
  el("lb-dot").className = "dot err";
  el("lb-status").textContent = "Not signed in";
  return null;
}

function renderApiStatus(cfg) {
  if (cfg.username && cfg.sessionToken) {
    el("api-dot").className = "dot ok";
    el("api-status").textContent = cfg.username;
  } else {
    el("api-dot").className = "dot warn";
    el("api-status").textContent = "Not connected";
  }
}

function renderProgress(state) {
  if (!state) return;
  el("phase").textContent = state.phase || "idle";
  if (state.currentPage && state.totalPages) {
    el("page").textContent = `${state.currentPage} / ${state.totalPages}`;
    el("list-page").textContent = `${state.currentPage} / ${state.totalPages}`;
  } else if (state.currentPage) {
    el("page").textContent = `${state.currentPage}`;
    el("list-page").textContent = `${state.currentPage}`;
  }
  if (typeof state.watchlistFound === "number") el("wl-count").textContent = state.watchlistFound;
  if (typeof state.diaryFound === "number") el("diary-count").textContent = state.diaryFound;
  if (typeof state.listFound === "number") el("list-count").textContent = state.listFound;
  if (typeof state.listsDiscovered === "number") el("lists-discovered").textContent = state.listsDiscovered;
  if (typeof state.listsFilled === "number") el("lists-filled").textContent = state.listsFilled;
  if (typeof state.percent === "number") {
    const p = `${Math.max(0, Math.min(100, state.percent))}%`;
    el("progress-fill").style.width = p;
    el("list-progress-fill").style.width = p;
  }
  el("start-btn").disabled = !!state.running;
  el("stop-btn").disabled = !state.running;
  el("scrape-list-btn").disabled = !!state.running;
  if (state.lastLog) {
    const target = (state.phase === "list" || state.phase === "movies") ? "list-log" : "log";
    logLine(target, state.lastLog);
  }
}

async function refreshStatus() {
  const cfg = await getStorage();
  el("api-base").value = cfg.apiBase || DEFAULT_API_BASE;
  el("auto-sync").checked = !!cfg.autoSync;
  el("opt-history").checked = cfg.syncHistory !== false;
  el("opt-discover").checked = cfg.discoverLists !== false;
  el("opt-fill").checked = cfg.fillLists !== false;
  el("discover-pages").value = cfg.discoverPages ?? "";
  el("fill-max-lists").value = cfg.fillMaxLists ?? "";
  el("fill-list-pages").value = cfg.fillListPages ?? "";
  renderApiStatus(cfg);
  await checkLetterboxdSession();
  const resp = await chrome.runtime.sendMessage({ type: "GET_STATE" }).catch(() => null);
  if (resp && resp.state) renderProgress(resp.state);
}

el("connect-btn").addEventListener("click", async () => {
  const lbCookie = await checkLetterboxdSession();
  if (!lbCookie) {
    logLine("log", "ERROR: sign in to letterboxd.com first, then reopen this popup");
    return;
  }
  logLine("log", "Registering with Swiperboxd…");
  el("connect-btn").disabled = true;
  const apiBase = el("api-base").value.trim() || DEFAULT_API_BASE;
  const resp = await chrome.runtime.sendMessage({ type: "REGISTER", apiBase });
  el("connect-btn").disabled = false;
  if (resp && resp.ok) {
    logLine("log", `Connected as ${resp.username}`);
    await refreshStatus();
  } else {
    logLine("log", `ERROR: ${resp?.error || "register failed"}`);
  }
});

el("save-api-base").addEventListener("click", async () => {
  await setStorage({ apiBase: el("api-base").value.trim() || DEFAULT_API_BASE });
  logLine("log", "API base saved");
});

async function persistSyncOptions() {
  await setStorage({
    syncHistory: el("opt-history").checked,
    discoverLists: el("opt-discover").checked,
    fillLists: el("opt-fill").checked,
  });
}

["opt-history", "opt-discover", "opt-fill"].forEach((id) => {
  el(id).addEventListener("change", persistSyncOptions);
});

el("save-caps").addEventListener("click", async () => {
  const toInt = (v) => { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 ? n : null; };
  const caps = {
    discoverPages: toInt(el("discover-pages").value),
    fillMaxLists: toInt(el("fill-max-lists").value),
    fillListPages: toInt(el("fill-list-pages").value),
  };
  await setStorage(caps);
  logLine("log", "Caps saved");
});

el("start-btn").addEventListener("click", async () => {
  const lbCookie = await checkLetterboxdSession();
  if (!lbCookie) { logLine("log", "ERROR: not signed in to Letterboxd"); return; }
  await persistSyncOptions();
  logLine("log", "Starting sync…");
  const resp = await chrome.runtime.sendMessage({ type: "START_SYNC" });
  if (!resp?.ok) logLine("log", `ERROR: ${resp?.error || "could not start"}`);
});

el("stop-btn").addEventListener("click", async () => {
  logLine("log", "Stop requested");
  await chrome.runtime.sendMessage({ type: "STOP_SYNC" });
});

el("auto-sync").addEventListener("change", async () => {
  const on = el("auto-sync").checked;
  await setStorage({ autoSync: on });
  await chrome.runtime.sendMessage({ type: "SET_AUTO_SYNC", value: on });
  logLine("log", `Auto-sync ${on ? "enabled (6h)" : "disabled"}`);
});

el("scrape-list-btn").addEventListener("click", async () => {
  const lbCookie = await checkLetterboxdSession();
  if (!lbCookie) { logLine("list-log", "ERROR: not signed in to Letterboxd"); return; }
  const listUrl = el("list-url").value.trim();
  if (!listUrl) { logLine("list-log", "ERROR: paste a list URL first"); return; }
  const fetchMeta = el("fetch-metadata").checked;
  logLine("list-log", `Scraping ${listUrl}${fetchMeta ? " (+metadata)" : ""}…`);
  const resp = await chrome.runtime.sendMessage({ type: "SCRAPE_LIST", listUrl, fetchMetadata: fetchMeta });
  if (resp?.ok) logLine("list-log", `Done — ${resp.found} films`);
  else logLine("list-log", `ERROR: ${resp?.error || "scrape failed"}`);
});

// Tabs
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".pane").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    el(`pane-${t.dataset.pane}`).classList.add("active");
  });
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "SYNC_STATE") renderProgress(msg.state);
});

refreshStatus();
