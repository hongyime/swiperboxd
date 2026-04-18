// Swiperboxd Sync — service worker
// Scrapes Letterboxd using the logged-in user's cookie (no 403 since the
// request is sourced from the user's browser, not Vercel's AWS IPs).
// Pushes slugs to the Swiperboxd backend in batches as each page is parsed.

const DEFAULT_API_BASE = "https://swiperboxd.vercel.app";
const LB_BASE = "https://letterboxd.com";
const BATCH_FLUSH_THRESHOLD = 50; // push every N slugs OR every page
const MAX_PAGES_HARD_CAP = 200;   // safety stop
const PAGE_DELAY_MS = 900;        // polite throttling
const ALARM_NAME = "swiperboxd-periodic-sync";

let syncState = {
  running: false,
  stopRequested: false,
  phase: "idle",
  currentPage: 0,
  totalPages: 0,
  watchlistFound: 0,
  diaryFound: 0,
  percent: 0,
  lastLog: null,
};

function broadcast() {
  chrome.runtime.sendMessage({ type: "SYNC_STATE", state: syncState }).catch(() => {});
}

function log(msg) {
  console.log("[swiperboxd-ext]", msg);
  syncState.lastLog = msg;
  broadcast();
}

async function getConfig() {
  return await chrome.storage.local.get(["apiBase", "username", "sessionToken", "autoSync"]);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function extractSlugsFromHtml(html) {
  // Mirror _extract_film_slugs in src/api/providers/letterboxd.py
  // Strategy 1: data-item-slug on react-component divs
  // Strategy 2: data-film-slug attributes
  // Strategy 3: /film/<slug>/ href fallback
  const slugs = [];
  const seen = new Set();
  const add = (s) => {
    if (s && !seen.has(s)) {
      seen.add(s);
      slugs.push(s);
    }
  };
  const re1 = /data-item-slug="([^"]+)"/g;
  let m;
  while ((m = re1.exec(html)) !== null) add(m[1]);
  const re2 = /data-film-slug="([^"]+)"/g;
  while ((m = re2.exec(html)) !== null) add(m[1]);
  if (slugs.length === 0) {
    const re3 = /href="\/film\/([a-z0-9][a-z0-9-]*)\/?"/g;
    while ((m = re3.exec(html)) !== null) add(m[1]);
  }
  return slugs;
}

function detectTotalPages(html) {
  // Letterboxd paginator uses class="paginate-page" — the last one is the total
  const matches = [...html.matchAll(/class="paginate-page[^"]*"[^>]*>\s*<[^>]*>(\d+)/g)];
  if (matches.length === 0) return null;
  const nums = matches.map((m) => parseInt(m[1], 10)).filter(Number.isFinite);
  return nums.length ? Math.max(...nums) : null;
}

async function fetchLetterboxdPage(path) {
  const url = `${LB_BASE}${path}`;
  const res = await fetch(url, {
    credentials: "include",
    headers: {
      "Accept": "text/html",
      "User-Agent": navigator.userAgent,
    },
  });
  if (!res.ok) {
    throw new Error(`letterboxd ${res.status} on ${path}`);
  }
  return await res.text();
}

async function pushBatch(endpoint, cfg, slugs, page, totalPages) {
  if (!slugs.length) return;
  const url = `${cfg.apiBase || DEFAULT_API_BASE}${endpoint}`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-Token": cfg.sessionToken,
    },
    body: JSON.stringify({
      user_id: cfg.username,
      slugs,
      page,
      total_pages: totalPages,
    }),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`batch push ${res.status}: ${txt.slice(0, 160)}`);
  }
  return await res.json().catch(() => ({}));
}

async function reportStatus(cfg, phase, extras = {}) {
  try {
    await fetch(`${cfg.apiBase || DEFAULT_API_BASE}/api/extension/sync-status`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Session-Token": cfg.sessionToken,
      },
      body: JSON.stringify({
        user_id: cfg.username,
        phase,
        ...extras,
      }),
    });
  } catch (e) {
    console.warn("[swiperboxd-ext] sync-status push failed:", e);
  }
}

async function scrapeListType({ cfg, pathFn, batchEndpoint, phaseName, onFound }) {
  let page = 1;
  let totalPages = null;
  let totalFound = 0;
  let buffer = [];

  while (page <= MAX_PAGES_HARD_CAP) {
    if (syncState.stopRequested) {
      log(`Stop requested at ${phaseName} page ${page}`);
      break;
    }
    const path = pathFn(page);
    let html;
    try {
      html = await fetchLetterboxdPage(path);
    } catch (e) {
      log(`ERROR: ${e.message}`);
      await reportStatus(cfg, "error", { message: e.message });
      throw e;
    }
    if (totalPages === null) {
      totalPages = detectTotalPages(html) || 1;
      syncState.totalPages = totalPages;
    }
    syncState.currentPage = page;
    const slugs = extractSlugsFromHtml(html);
    log(`${phaseName} page ${page}/${totalPages}: ${slugs.length} slugs`);

    if (slugs.length === 0) {
      // End of list (or private page) — stop
      break;
    }

    buffer.push(...slugs);
    totalFound += slugs.length;
    onFound(totalFound);

    if (buffer.length >= BATCH_FLUSH_THRESHOLD || page === totalPages) {
      const toPush = buffer.splice(0, buffer.length);
      try {
        const result = await pushBatch(batchEndpoint, cfg, toPush, page, totalPages);
        log(`pushed ${toPush.length} → added=${result?.result?.added ?? "?"}`);
      } catch (e) {
        log(`ERROR pushing batch: ${e.message}`);
        await reportStatus(cfg, "error", { message: e.message });
        throw e;
      }
    }

    await reportStatus(cfg, phaseName, {
      current_page: page,
      total_pages: totalPages,
      slugs_found: totalFound,
    });

    broadcast();

    if (page >= totalPages) break;
    page += 1;
    await sleep(PAGE_DELAY_MS);
  }

  // Flush residual buffer
  if (buffer.length) {
    try {
      const result = await pushBatch(batchEndpoint, cfg, buffer, page, totalPages);
      log(`flushed final ${buffer.length} → added=${result?.result?.added ?? "?"}`);
    } catch (e) {
      log(`ERROR flushing: ${e.message}`);
      throw e;
    }
  }

  return totalFound;
}

async function runSync() {
  if (syncState.running) {
    return { ok: false, error: "already running" };
  }
  const cfg = await getConfig();
  if (!cfg.apiBase || !cfg.username || !cfg.sessionToken) {
    return { ok: false, error: "missing credentials" };
  }

  syncState = {
    running: true,
    stopRequested: false,
    phase: "watchlist",
    currentPage: 0,
    totalPages: 0,
    watchlistFound: 0,
    diaryFound: 0,
    percent: 0,
    lastLog: "sync starting",
  };
  broadcast();

  try {
    // Watchlist
    syncState.phase = "watchlist";
    syncState.currentPage = 0;
    syncState.totalPages = 0;
    broadcast();
    const wlCount = await scrapeListType({
      cfg,
      pathFn: (p) => `/${encodeURIComponent(cfg.username)}/watchlist/page/${p}/`,
      batchEndpoint: "/api/extension/batch/watchlist",
      phaseName: "watchlist",
      onFound: (n) => {
        syncState.watchlistFound = n;
        syncState.percent = Math.min(49, Math.floor((syncState.currentPage / Math.max(1, syncState.totalPages)) * 45));
        broadcast();
      },
    });

    if (syncState.stopRequested) {
      syncState.phase = "idle";
      await reportStatus(cfg, "idle", { message: "stopped by user" });
      return { ok: true, stopped: true };
    }

    // Diary
    syncState.phase = "diary";
    syncState.currentPage = 0;
    syncState.totalPages = 0;
    broadcast();
    const diaryCount = await scrapeListType({
      cfg,
      pathFn: (p) => `/${encodeURIComponent(cfg.username)}/films/diary/page/${p}/`,
      batchEndpoint: "/api/extension/batch/diary",
      phaseName: "diary",
      onFound: (n) => {
        syncState.diaryFound = n;
        syncState.percent = 50 + Math.min(49, Math.floor((syncState.currentPage / Math.max(1, syncState.totalPages)) * 45));
        broadcast();
      },
    });

    syncState.phase = "complete";
    syncState.percent = 100;
    log(`Sync done — watchlist=${wlCount} diary=${diaryCount}`);
    await reportStatus(cfg, "complete", { slugs_found: wlCount + diaryCount });
    await chrome.storage.local.set({ lastSync: Date.now() });
    return { ok: true, watchlist: wlCount, diary: diaryCount };
  } catch (e) {
    syncState.phase = "error";
    syncState.percent = -1;
    log(`FATAL: ${e.message}`);
    return { ok: false, error: e.message };
  } finally {
    syncState.running = false;
    broadcast();
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (!msg || !msg.type) return sendResponse({ ok: false });
    switch (msg.type) {
      case "GET_STATE":
        sendResponse({ ok: true, state: syncState });
        return;
      case "START_SYNC": {
        const res = await runSync();
        sendResponse(res);
        return;
      }
      case "STOP_SYNC":
        syncState.stopRequested = true;
        sendResponse({ ok: true });
        return;
      case "SET_AUTO_SYNC":
        if (msg.value) {
          chrome.alarms.create(ALARM_NAME, { periodInMinutes: 360 });
        } else {
          chrome.alarms.clear(ALARM_NAME);
        }
        sendResponse({ ok: true });
        return;
      case "SWIPERBOXD_AUTH":
        // Credentials pushed from content script after a successful login
        await chrome.storage.local.set({
          apiBase: msg.apiBase,
          username: msg.username,
          sessionToken: msg.sessionToken,
        });
        sendResponse({ ok: true });
        return;
      default:
        sendResponse({ ok: false, error: "unknown type" });
    }
  })();
  return true; // keep channel open for async sendResponse
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== ALARM_NAME) return;
  if (syncState.running) return;
  console.log("[swiperboxd-ext] periodic sync fired");
  await runSync();
});

chrome.runtime.onInstalled.addListener(async () => {
  const cfg = await getConfig();
  if (cfg.autoSync) {
    chrome.alarms.create(ALARM_NAME, { periodInMinutes: 360 });
  }
});
