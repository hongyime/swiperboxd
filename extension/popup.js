// Swiperboxd Sync — popup controller
// Talks to the service worker via chrome.runtime messages.

const el = (id) => document.getElementById(id);

const DEFAULT_API_BASE = "https://swiperboxd.vercel.app";

async function getStorage() {
  return await chrome.storage.local.get([
    "apiBase",
    "username",
    "sessionToken",
    "autoSync",
    "lastSync",
    "syncState",
  ]);
}

async function setStorage(obj) {
  await chrome.storage.local.set(obj);
}

function logLine(msg) {
  const log = el("log");
  const time = new Date().toLocaleTimeString();
  log.textContent = `[${time}] ${msg}\n` + log.textContent;
}

async function checkLetterboxdSession() {
  try {
    const cookie = await chrome.cookies.get({
      url: "https://letterboxd.com",
      name: "letterboxd.user.CURRENT",
    });
    if (cookie && cookie.value) {
      el("lb-dot").className = "dot ok";
      el("lb-status").textContent = "Signed in";
      return cookie.value;
    }
  } catch (e) { /* ignore */ }
  el("lb-dot").className = "dot err";
  el("lb-status").textContent = "Not signed in";
  return null;
}

function checkApiConfig(cfg) {
  if (cfg.apiBase && cfg.username && cfg.sessionToken) {
    el("api-dot").className = "dot ok";
    el("api-status").textContent = "Configured";
    return true;
  }
  el("api-dot").className = "dot warn";
  el("api-status").textContent = "Needs setup";
  return false;
}

function renderProgress(state) {
  if (!state) return;
  el("phase").textContent = state.phase || "idle";
  if (state.currentPage && state.totalPages) {
    el("page").textContent = `${state.currentPage} / ${state.totalPages}`;
  } else if (state.currentPage) {
    el("page").textContent = `${state.currentPage}`;
  }
  if (typeof state.watchlistFound === "number") {
    el("wl-count").textContent = state.watchlistFound;
  }
  if (typeof state.diaryFound === "number") {
    el("diary-count").textContent = state.diaryFound;
  }
  if (typeof state.percent === "number") {
    el("progress-fill").style.width = `${Math.max(0, Math.min(100, state.percent))}%`;
  }
  el("start-btn").disabled = !!state.running;
  el("stop-btn").disabled = !state.running;
  if (state.lastLog) logLine(state.lastLog);
}

async function refreshStatus() {
  const cfg = await getStorage();
  el("api-base").value = cfg.apiBase || DEFAULT_API_BASE;
  el("username").value = cfg.username || "";
  el("session-token").value = cfg.sessionToken || "";
  el("auto-sync").checked = !!cfg.autoSync;
  checkApiConfig(cfg);
  await checkLetterboxdSession();

  const resp = await chrome.runtime.sendMessage({ type: "GET_STATE" }).catch(() => null);
  if (resp && resp.state) renderProgress(resp.state);
}

el("save-config").addEventListener("click", async () => {
  await setStorage({
    apiBase: el("api-base").value.trim() || DEFAULT_API_BASE,
    username: el("username").value.trim(),
    sessionToken: el("session-token").value.trim(),
  });
  logLine("Credentials saved");
  await refreshStatus();
});

el("start-btn").addEventListener("click", async () => {
  const cfg = await getStorage();
  if (!checkApiConfig(cfg)) {
    logLine("ERROR: configure API base, username, and session token first");
    return;
  }
  const lbCookie = await checkLetterboxdSession();
  if (!lbCookie) {
    logLine("ERROR: not signed in to Letterboxd in this browser");
    return;
  }
  logLine("Starting sync…");
  const resp = await chrome.runtime.sendMessage({ type: "START_SYNC" });
  if (resp && resp.ok) {
    el("start-btn").disabled = true;
    el("stop-btn").disabled = false;
  } else {
    logLine(`ERROR: ${resp?.error || "could not start"}`);
  }
});

el("stop-btn").addEventListener("click", async () => {
  logLine("Stop requested");
  await chrome.runtime.sendMessage({ type: "STOP_SYNC" });
});

el("auto-sync").addEventListener("change", async () => {
  const on = el("auto-sync").checked;
  await setStorage({ autoSync: on });
  await chrome.runtime.sendMessage({ type: "SET_AUTO_SYNC", value: on });
  logLine(`Auto-sync ${on ? "enabled (6h)" : "disabled"}`);
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "SYNC_STATE") {
    renderProgress(msg.state);
  }
});

refreshStatus();
