// Swiperboxd Sync — service worker (MV3)
//
// Responsibilities
//   1. Self-register the user via their Letterboxd cookie (no web-app login
//      required). POST /api/extension/register exchanges the Letterboxd
//      session cookie for a Swiperboxd session token.
//   2. Scrape the authenticated user's watchlist + diary using
//      credentials: "include" so Letterboxd treats the request as
//      coming from the user's own browser.
//   3. Scrape public lists (any /owner/list/<slug>/) — multi-page.
//   4. Scrape individual /film/<slug>/ pages for metadata (JSON-LD primary,
//      HTML selectors fallback).
//   5. Batch-push each scraped unit to the API with retries + exponential
//      backoff. No intermediate files or queues — each batch is an immediate
//      round-trip to Supabase via the backend.

const DEFAULT_API_BASE = "https://swiperboxd.vercel.app";
const LB_BASE = "https://letterboxd.com";
const BATCH_FLUSH_THRESHOLD = 50;   // flush slug buffer every N slugs or every page
const METADATA_BATCH_SIZE = 10;     // /film/<slug>/ is heavy; keep batches small
const MAX_PAGES_HARD_CAP = 300;
const PAGE_DELAY_MS = 900;
const MOVIE_DELAY_MS = 600;
const ALARM_NAME = "swiperboxd-periodic-sync";

// Public-list discovery defaults (overridable via chrome.storage)
const DEFAULT_DISCOVER_PAGES = 3;    // /lists/popular/page/1..N
const DEFAULT_FILL_MAX_LISTS = 25;   // how many under-scraped lists to fill per run
const DEFAULT_FILL_LIST_PAGES = 10;  // cap films-per-list at ~300 films

let syncState = {
  running: false,
  stopRequested: false,
  phase: "idle",
  currentPage: 0,
  totalPages: 0,
  watchlistFound: 0,
  diaryFound: 0,
  listFound: 0,
  listsDiscovered: 0,
  listsFilled: 0,
  moviesProcessed: 0,
  percent: 0,
  lastLog: null,
};

function resetState(phase = "starting", log = "sync starting") {
  syncState = {
    running: true,
    stopRequested: false,
    phase,
    currentPage: 0,
    totalPages: 0,
    watchlistFound: 0,
    diaryFound: 0,
    listFound: 0,
    listsDiscovered: 0,
    listsFilled: 0,
    moviesProcessed: 0,
    percent: 0,
    lastLog: log,
  };
}

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

async function getLetterboxdCookie() {
  try {
    const c = await chrome.cookies.get({ url: LB_BASE, name: "letterboxd.user.CURRENT" });
    return c && c.value ? c.value : null;
  } catch {
    return null;
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function fetchWithRetry(url, opts = {}, maxAttempts = 3) {
  let lastErr;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await fetch(url, opts);
      if (res.status === 429 || res.status >= 500) {
        lastErr = new Error(`http ${res.status}`);
      } else {
        return res;
      }
    } catch (e) {
      lastErr = e;
    }
    if (attempt < maxAttempts) {
      const backoff = 400 * Math.pow(2, attempt - 1) + Math.random() * 300;
      await sleep(backoff);
    }
  }
  throw lastErr || new Error("fetchWithRetry failed");
}

// ── Letterboxd page fetching ────────────────────────────────────────────────

async function fetchLetterboxdPage(path) {
  const url = path.startsWith("http") ? path : `${LB_BASE}${path}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 20000); // 20s timeout
  try {
    const res = await fetchWithRetry(url, {
      credentials: "include",
      headers: {
        "Accept": "text/html",
        "User-Agent": navigator.userAgent,
      },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`letterboxd ${res.status} on ${path}`);
    return await res.text();
  } finally {
    clearTimeout(timeoutId);
  }
}

function extractSlugsFromHtml(html) {
  // Mirror _extract_film_slugs in src/api/providers/letterboxd.py
  const slugs = [];
  const seen = new Set();
  const add = (s) => { if (s && !seen.has(s)) { seen.add(s); slugs.push(s); } };
  let m;

  // Strategy 1: react-component data-item-slug (2024+ poster grid)
  const re1 = /data-item-slug="([^"]+)"/g;
  while ((m = re1.exec(html)) !== null) add(m[1]);

  // Strategy 2: film-poster data-film-slug (older poster grid)
  const re2 = /data-film-slug="([^"]+)"/g;
  while ((m = re2.exec(html)) !== null) add(m[1]);

  // Strategy 3: diary table — <td class="td-film-details"><a href="/film/<slug>/...">
  const re3 = /class="[^"]*td-film-details[^"]*"[^>]*>[\s\S]*?href="\/film\/([a-z0-9][a-z0-9-]*)(?:\/[^"]*)?"[^>]*>/g;
  while ((m = re3.exec(html)) !== null) add(m[1]);

  // Strategy 4: generic /film/<slug>/ hrefs — only if nothing found yet
  if (slugs.length === 0) {
    const re4 = /href="\/film\/([a-z0-9][a-z0-9-]*)\/?"/g;
    while ((m = re4.exec(html)) !== null) add(m[1]);
  }

  return slugs;
}

function detectTotalPages(html) {
  const matches = [...html.matchAll(/class="paginate-page[^"]*"[^>]*>\s*<[^>]*>(\d+)/g)];
  if (matches.length === 0) return null;
  const nums = matches.map((m) => parseInt(m[1], 10)).filter(Number.isFinite);
  return nums.length ? Math.max(...nums) : null;
}

// ── Movie metadata parsing ──────────────────────────────────────────────────

function parseMovieFromHtml(slug, html) {
  const movie = {
    slug,
    title: "",
    poster_url: "",
    rating: 0,
    popularity: 0,
    genres: [],
    synopsis: "",
    cast: [],
    year: null,
    director: null,
  };

  // JSON-LD primary
  const ldMatches = [...html.matchAll(/<script[^>]+type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g)];
  let ld = null;
  for (const m of ldMatches) {
    const raw = m[1].replace(/\/\*[\s\S]*?\*\//g, "").trim();
    try {
      ld = JSON.parse(raw);
      break;
    } catch { /* try next */ }
  }
  if (ld) {
    movie.title = ld.name || "";
    movie.poster_url = ld.image || "";
    if (ld.aggregateRating) {
      movie.rating = parseFloat(ld.aggregateRating.ratingValue || 0) || 0;
      movie.popularity = parseInt(ld.aggregateRating.ratingCount || 0, 10) || 0;
    }
    if (Array.isArray(ld.genre)) movie.genres = ld.genre.map(String);
    else if (typeof ld.genre === "string") movie.genres = [ld.genre];
    movie.synopsis = ld.description || "";
    const actors = ld.actors || ld.actor || [];
    if (Array.isArray(actors)) {
      movie.cast = actors.slice(0, 5).filter((a) => a && a.name).map((a) => a.name);
    }
    // Director: directors or director field
    const directors = ld.directors || ld.director || [];
    if (Array.isArray(directors) && directors.length) {
      movie.director = directors[0]?.name || null;
    } else if (directors && directors.name) {
      movie.director = directors.name;
    }
    // Year from releasedEvent.startDate
    if (ld.releasedEvent && ld.releasedEvent[0] && ld.releasedEvent[0].startDate) {
      const y = parseInt(String(ld.releasedEvent[0].startDate).slice(0, 4), 10);
      if (Number.isFinite(y)) movie.year = y;
    }
  }

  // HTML fallbacks
  if (!movie.title) {
    const h1 = html.match(/<h1[^>]*class="[^"]*primaryname[^"]*"[^>]*>([\s\S]*?)<\/h1>/)
      || html.match(/<h1[^>]*class="[^"]*headline-1[^"]*"[^>]*>([\s\S]*?)<\/h1>/);
    if (h1) movie.title = h1[1].replace(/<[^>]+>/g, "").trim();
  }
  if (!movie.poster_url) {
    const og = html.match(/<meta[^>]+property="og:image"[^>]+content="([^"]+)"/);
    if (og) movie.poster_url = og[1];
  }
  if (!movie.genres.length) {
    const genreRe = /<a[^>]+href="\/films\/genre\/[^"]+\/"[^>]*>([^<]+)<\/a>/g;
    const genres = [];
    let gm;
    while ((gm = genreRe.exec(html)) !== null) genres.push(gm[1].trim());
    if (genres.length) movie.genres = [...new Set(genres)];
  }
  if (movie.year === null) {
    const ym = html.match(/<a[^>]+href="\/films\/year\/(\d{4})\/"/);
    if (ym) movie.year = parseInt(ym[1], 10);
  }
  return movie;
}

// ── API ──────────────────────────────────────────────────────────────────────

async function apiPost(cfg, endpoint, body) {
  const url = `${cfg.apiBase || DEFAULT_API_BASE}${endpoint}`;
  const res = await fetchWithRetry(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(cfg.sessionToken ? { "X-Session-Token": cfg.sessionToken } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    const err = new Error(`${endpoint} → ${res.status}: ${txt.slice(0, 200)}`);
    err.status = res.status;
    throw err;
  }
  return await res.json().catch(() => ({}));
}

async function registerExtension({ apiBase } = {}) {
  const cookie = await getLetterboxdCookie();
  if (!cookie) throw new Error("Not signed in to Letterboxd — sign in at letterboxd.com first.");

  const base = (apiBase || DEFAULT_API_BASE).replace(/\/$/, "");
  const res = await fetchWithRetry(`${base}/api/extension/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ letterboxd_session_cookie: cookie }),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`register ${res.status}: ${txt.slice(0, 300)}`);
  }
  const data = await res.json();
  await chrome.storage.local.set({
    apiBase: data.api_base || base,
    username: data.username,
    sessionToken: data.session_token,
  });
  return { username: data.username, apiBase: data.api_base || base };
}

async function reportStatus(cfg, phase, extras = {}) {
  if (!cfg.username || !cfg.sessionToken) return;
  try {
    await apiPost(cfg, "/api/extension/sync-status", { user_id: cfg.username, phase, ...extras });
  } catch (e) {
    console.warn("[swiperboxd-ext] sync-status push failed:", e);
  }
}

// ── Public list discovery (popular lists) ────────────────────────────────────

function parseMemberCount(text) {
  // "382K" → 382000, "1.2M" → 1200000
  if (!text) return 0;
  const s = text.trim().replace(/,/g, "");
  const m = s.match(/^([\d.]+)\s*([KMB])?$/i);
  if (!m) {
    const n = parseInt(s, 10);
    return Number.isFinite(n) ? n : 0;
  }
  const val = parseFloat(m[1]) || 0;
  const unit = (m[2] || "").toUpperCase();
  return Math.round(val * ({ K: 1e3, M: 1e6, B: 1e9 }[unit] || 1));
}

function stripTags(s) {
  return (s || "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
}

function splitEntries(html) {
  // Split into chunks starting at each "list-summary" article. Returns chunk bodies
  // so subsequent regexes don't cross list boundaries.
  const chunks = [];
  const re = /<article[^>]*class="[^"]*list-summary[^"]*"[^>]*>/g;
  let prev = -1;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (prev !== -1) chunks.push(html.slice(prev, m.index));
    prev = m.index;
  }
  if (prev !== -1) chunks.push(html.slice(prev));
  return chunks;
}

function parseListSummariesFromHtml(html) {
  // Mirrors discover_site_lists() in src/api/providers/letterboxd.py using
  // regex-only extraction so the code runs inside an MV3 service worker
  // (no DOM APIs guaranteed).
  const out = [];
  for (const chunk of splitEntries(html)) {
    try {
      const titleMatch = chunk.match(/<h2[^>]*class="[^"]*name[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/)
        || chunk.match(/<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/);
      if (!titleMatch) continue;
      const href = titleMatch[1];
      const parts = href.replace(/^\/+|\/+$/g, "").split("/");
      if (parts.length < 3 || parts[1] !== "list") continue;

      const ownerSlug = parts[0];
      const listSlug = parts[2];
      const listId = `${ownerSlug}-${listSlug}`;
      const listUrl = `${LB_BASE}/${ownerSlug}/list/${listSlug}/`;
      const title = stripTags(titleMatch[2]);

      const ownerMatch = chunk.match(/<strong[^>]+class="[^"]*displayname[^"]*"[^>]*>([\s\S]*?)<\/strong>/)
        || chunk.match(/<a[^>]+class="[^"]*owner[^"]*"[^>]*>([\s\S]*?)<\/a>/);
      const ownerName = ownerMatch ? stripTags(ownerMatch[1]) : ownerSlug;

      const descMatch = chunk.match(/<div[^>]+class="[^"]*notes[^"]*"[^>]*>[\s\S]*?<p[^>]*>([\s\S]*?)<\/p>/)
        || chunk.match(/<div[^>]+class="[^"]*body-text[^"]*"[^>]*>[\s\S]*?<p[^>]*>([\s\S]*?)<\/p>/);
      const description = descMatch ? stripTags(descMatch[1]) : "";

      let filmCount = 0;
      const countMatch = chunk.match(/<span[^>]+class="[^"]*value[^"]*"[^>]*>([\s\S]*?)<\/span>/);
      if (countMatch) {
        const text = stripTags(countMatch[1]).replace(/,/g, "");
        const nums = text.match(/\d+/);
        if (nums) filmCount = parseInt(nums[0], 10) || 0;
      }

      let likeCount = 0;
      const likeMatch = chunk.match(/<a[^>]+href="[^"]*\/likes\/"[^>]*>[\s\S]*?<span[^>]+class="[^"]*label[^"]*"[^>]*>([\s\S]*?)<\/span>/);
      if (likeMatch) likeCount = parseMemberCount(stripTags(likeMatch[1]));

      let commentCount = 0;
      const commentMatch = chunk.match(/<a[^>]+href="[^"]*#comments"[^>]*>[\s\S]*?<span[^>]+class="[^"]*label[^"]*"[^>]*>([\s\S]*?)<\/span>/);
      if (commentMatch) commentCount = parseMemberCount(stripTags(commentMatch[1]));

      out.push({
        list_id: listId,
        slug: listSlug,
        url: listUrl,
        title,
        owner_name: ownerName,
        owner_slug: ownerSlug,
        description,
        film_count: filmCount,
        like_count: likeCount,
        comment_count: commentCount,
        is_official: ["letterboxd", "official"].includes(ownerSlug.toLowerCase()),
        tags: [],
      });
    } catch (e) {
      console.warn("[swiperboxd-ext] list parse error:", e);
    }
  }
  return out;
}

async function discoverPublicLists(cfg, { maxPages = DEFAULT_DISCOVER_PAGES } = {}) {
  syncState.phase = "discover";
  syncState.currentPage = 0;
  syncState.totalPages = maxPages;
  broadcast();

  let totalPushed = 0;
  for (let page = 1; page <= maxPages; page++) {
    if (syncState.stopRequested) break;
    const path = page === 1 ? "/lists/popular/" : `/lists/popular/page/${page}/`;
    let html;
    try {
      html = await fetchLetterboxdPage(path);
    } catch (e) {
      log(`ERROR discover page ${page}: ${e.message}`);
      break;
    }
    const summaries = parseListSummariesFromHtml(html);
    log(`discover page ${page}/${maxPages}: ${summaries.length} lists`);
    if (!summaries.length) break;
    syncState.currentPage = page;
    try {
      const result = await apiPost(cfg, "/api/extension/batch/list-summaries", {
        lists: summaries,
        source: "popular",
        page,
      });
      totalPushed += result.stored || summaries.length;
      syncState.listsDiscovered = totalPushed;
      syncState.percent = Math.min(99, Math.floor((page / maxPages) * 100));
      broadcast();
    } catch (e) {
      log(`ERROR discover push page ${page}: ${e.message}`);
    }
    if (page < maxPages) await sleep(PAGE_DELAY_MS);
  }
  return { discovered: totalPushed };
}

async function fillUnderscrapedLists(
  cfg,
  { maxLists = DEFAULT_FILL_MAX_LISTS, maxListPages = DEFAULT_FILL_LIST_PAGES } = {},
) {
  syncState.phase = "fill_lists";
  syncState.listsFilled = 0;
  broadcast();

  let lists = [];
  try {
    const url = `${cfg.apiBase || DEFAULT_API_BASE}/api/extension/lists-needing-scrape?limit=${maxLists}`;
    const res = await fetchWithRetry(url, {
      method: "GET",
      headers: cfg.sessionToken ? { "X-Session-Token": cfg.sessionToken } : {},
    });
    if (res.ok) {
      const data = await res.json();
      lists = data.lists || [];
    } else {
      log(`fill: lookup ${res.status}`);
      return { filled: 0 };
    }
  } catch (e) {
    log(`fill: lookup failed ${e.message}`);
    return { filled: 0 };
  }

  log(`fill_lists: ${lists.length} under-scraped lists to refresh`);
  syncState.totalPages = lists.length;

  let filled = 0;
  for (let i = 0; i < lists.length; i++) {
    if (syncState.stopRequested) break;
    const lst = lists[i];
    if (!lst.url) continue;
    syncState.currentPage = i + 1;
    syncState.percent = Math.min(99, Math.floor(((i + 1) / Math.max(1, lists.length)) * 100));
    broadcast();

    try {
      await scrapeOneListForFill(cfg, lst, maxListPages);
      filled += 1;
      syncState.listsFilled = filled;
      broadcast();
    } catch (e) {
      log(`fill ${lst.list_id} failed: ${e.message}`);
    }
    await sleep(PAGE_DELAY_MS);
  }
  return { filled };
}

async function scrapeOneListForFill(cfg, listRow, maxPages) {
  const info = parseListUrl(listRow.url);
  if (!info) return;
  let page = 1;
  let totalPages = null;
  let pushedAny = false;
  const seen = new Set();

  while (page <= Math.min(maxPages, MAX_PAGES_HARD_CAP)) {
    if (syncState.stopRequested) break;
    let html;
    try {
      html = await fetchLetterboxdPage(page === 1 ? `${info.basePath}/` : `${info.basePath}/page/${page}/`);
    } catch (e) {
      log(`fill ${info.listId} page ${page}: ${e.message}`);
      break;
    }
    if (totalPages === null) totalPages = detectTotalPages(html) || 1;
    const slugs = extractSlugsFromHtml(html).filter((s) => !seen.has(s));
    if (!slugs.length) break;
    slugs.forEach((s) => seen.add(s));

    try {
      await apiPost(cfg, "/api/extension/batch/list-movies", {
        list_id: info.listId,
        list_url: info.url,
        title: listRow.title,
        owner_slug: listRow.owner_slug || info.ownerSlug,
        film_count: listRow.film_count,
        slugs,
        page,
        total_pages: totalPages,
        replace_memberships: page === 1,
      });
      pushedAny = true;
    } catch (e) {
      log(`fill ${info.listId} push page ${page}: ${e.message}`);
      break;
    }
    if (page >= totalPages) break;
    page += 1;
    await sleep(PAGE_DELAY_MS);
  }
  if (pushedAny) log(`fill ${info.listId}: ${seen.size} films`);
}

// ── Sync routines ────────────────────────────────────────────────────────────

async function scrapeListType({ cfg, pathFn, batchEndpoint, phaseName, onFound, onSlugsCollected }) {
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
    log(`${phaseName}: fetching page ${page}${totalPages ? `/${totalPages}` : ""} …`);
    let html;
    try {
      html = await fetchLetterboxdPage(path);
    } catch (e) {
      log(`ERROR: ${phaseName} page ${page} fetch failed: ${e.message}`);
      await reportStatus(cfg, "error", { message: e.message });
      throw e;
    }
    if (totalPages === null) {
      totalPages = detectTotalPages(html) || 1;
      syncState.totalPages = totalPages;
      log(`${phaseName}: ${totalPages} page(s) total`);
    }
    syncState.currentPage = page;
    const slugs = extractSlugsFromHtml(html);
    log(`${phaseName} page ${page}/${totalPages}: ${slugs.length} slugs found`);

    if (slugs.length === 0) {
      log(`${phaseName}: no slugs on page ${page} — stopping`);
      break;
    }

    buffer.push(...slugs);
    totalFound += slugs.length;
    onFound(totalFound);

    // NEW: Callback to collect slugs for metadata fetching
    if (onSlugsCollected) {
      onSlugsCollected(slugs);
    }

    if (buffer.length >= BATCH_FLUSH_THRESHOLD || page === totalPages) {
      const toPush = buffer.splice(0, buffer.length);
      log(`${phaseName}: pushing ${toPush.length} slugs to API…`);
      try {
        const result = await apiPost(cfg, batchEndpoint, {
          user_id: cfg.username,
          slugs: toPush,
          page,
          total_pages: totalPages,
        });
        log(`${phaseName}: pushed ${toPush.length} → added=${result?.result?.added ?? "?"}`);
      } catch (e) {
        log(`ERROR: ${phaseName} push failed: ${e.message}`);
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

    if (page >= totalPages) {
      log(`${phaseName}: all ${totalPages} page(s) done, ${totalFound} total slugs`);
      break;
    }
    page += 1;
    log(`${phaseName}: waiting before page ${page}…`);
    await sleep(PAGE_DELAY_MS);
  }

  if (buffer.length) {
    const toPush = buffer.splice(0, buffer.length);
    log(`${phaseName}: flushing final ${toPush.length} slugs…`);
    try {
      const result = await apiPost(cfg, batchEndpoint, {
        user_id: cfg.username,
        slugs: toPush,
        page,
        total_pages: totalPages,
      });
      log(`${phaseName}: flushed → added=${result?.result?.added ?? "?"}`);
    } catch (e) {
      log(`ERROR: ${phaseName} final flush failed: ${e.message}`);
      throw e;
    }
  }
  return totalFound;
}

async function scrapeUserHistory(cfg, settings = {}) {
  const doWatchlist = settings.syncWatchlist !== false;
  const doDiary = settings.syncDiary !== false;
  let wl = 0, diary = 0;
  const allSlugs = new Set();

  if (doWatchlist) {
    syncState.phase = "watchlist";
    syncState.currentPage = 0;
    syncState.totalPages = 0;
    broadcast();
    wl = await scrapeListType({
      cfg,
      pathFn: (p) => p === 1
        ? `/${encodeURIComponent(cfg.username)}/watchlist/`
        : `/${encodeURIComponent(cfg.username)}/watchlist/page/${p}/`,
      batchEndpoint: "/api/extension/batch/watchlist",
      phaseName: "watchlist",
      onFound: (n) => {
        syncState.watchlistFound = n;
        syncState.percent = Math.min(33, Math.floor((syncState.currentPage / Math.max(1, syncState.totalPages)) * 30));
        broadcast();
      },
      onSlugsCollected: (slugs) => {
        slugs.forEach(s => allSlugs.add(s));
      },
    });
    if (syncState.stopRequested) return { watchlist: wl, diary: 0, stopped: true };
  }

  if (doDiary) {
    syncState.phase = "diary";
    syncState.currentPage = 0;
    syncState.totalPages = 0;
    broadcast();
    diary = await scrapeListType({
      cfg,
      pathFn: (p) => p === 1
        ? `/${encodeURIComponent(cfg.username)}/diary/`
        : `/${encodeURIComponent(cfg.username)}/diary/page/${p}/`,
      batchEndpoint: "/api/extension/batch/diary",
      phaseName: "diary",
      onFound: (n) => {
        syncState.diaryFound = n;
        syncState.percent = 33 + Math.min(33, Math.floor((syncState.currentPage / Math.max(1, syncState.totalPages)) * 30));
        broadcast();
      },
      onSlugsCollected: (slugs) => {
        slugs.forEach(s => allSlugs.add(s));
      },
    });
    if (syncState.stopRequested) return { watchlist: wl, diary, stopped: true };
  }

  // NEW: Fetch metadata for all collected slugs
  const slugsArray = Array.from(allSlugs);
  let metadataFetched = 0;
  if (slugsArray.length > 0) {
    syncState.phase = "metadata";
    syncState.percent = 66;
    broadcast();
    log(`Fetching metadata for ${slugsArray.length} movies...`);
    
    try {
      const result = await scrapeMoviesMetadata(cfg, slugsArray);
      metadataFetched = result.processed;
      log(`Metadata fetch complete: ${result.processed} movies processed`);
    } catch (e) {
      log(`ERROR: Metadata fetch failed: ${e.message}`);
      // Non-fatal: slugs are already stored, metadata can be retried
    }
  }

  syncState.percent = 100;
  broadcast();
  return { watchlist: wl, diary, stopped: false, metadata_fetched: metadataFetched };
}

function parseListUrl(rawUrl) {
  // Accepts:
  //   https://letterboxd.com/<owner>/list/<slug>/
  //   /owner/list/slug/
  //   owner/list/slug
  let path = rawUrl.replace(/^https?:\/\/letterboxd\.com/i, "").replace(/^\/+|\/+$/g, "");
  const parts = path.split("/").filter(Boolean);
  if (parts.length < 3 || parts[1] !== "list") return null;
  const [ownerSlug, , listSlug] = parts;
  return {
    ownerSlug,
    listSlug,
    listId: `${ownerSlug}-${listSlug}`,
    url: `${LB_BASE}/${ownerSlug}/list/${listSlug}/`,
    basePath: `/${ownerSlug}/list/${listSlug}`,
  };
}

async function scrapePublicList(cfg, listUrl) {
  const info = parseListUrl(listUrl);
  if (!info) throw new Error("Invalid list URL — expected https://letterboxd.com/<owner>/list/<slug>/");

  syncState.phase = "list";
  syncState.currentPage = 0;
  syncState.totalPages = 0;
  syncState.listFound = 0;
  broadcast();

  let page = 1;
  let totalPages = null;
  let allSlugs = [];
  let title = null;
  let filmCount = null;

  while (page <= MAX_PAGES_HARD_CAP) {
    if (syncState.stopRequested) { log(`Stop requested at list page ${page}`); break; }
    const path = page === 1 ? `${info.basePath}/` : `${info.basePath}/page/${page}/`;
    let html;
    try {
      html = await fetchLetterboxdPage(path);
    } catch (e) {
      log(`ERROR list page ${page}: ${e.message}`);
      break;
    }
    if (totalPages === null) {
      totalPages = detectTotalPages(html) || 1;
      syncState.totalPages = totalPages;
      const titleMatch = html.match(/<meta\s+property="og:title"\s+content="([^"]+)"/);
      if (titleMatch) title = titleMatch[1].replace(/&amp;/g, "&").replace(/&#039;/g, "'");
      const countMatch = html.match(/(\d+(?:,\d{3})*)\s+films?/i);
      if (countMatch) filmCount = parseInt(countMatch[1].replace(/,/g, ""), 10);
    }
    syncState.currentPage = page;
    const slugs = extractSlugsFromHtml(html);
    if (slugs.length === 0) break;

    const newSlugs = slugs.filter((s) => !allSlugs.includes(s));
    allSlugs.push(...newSlugs);
    syncState.listFound = allSlugs.length;
    syncState.percent = Math.min(99, Math.floor((page / Math.max(1, totalPages)) * 95));
    log(`list ${info.listId} page ${page}/${totalPages}: ${slugs.length} slugs`);

    try {
      await apiPost(cfg, "/api/extension/batch/list-movies", {
        list_id: info.listId,
        list_url: info.url,
        title,
        owner_slug: info.ownerSlug,
        film_count: filmCount,
        slugs: newSlugs,
        page,
        total_pages: totalPages,
        replace_memberships: false,
      });
    } catch (e) {
      log(`ERROR list batch push: ${e.message}`);
      throw e;
    }

    broadcast();
    if (page >= totalPages) break;
    page += 1;
    await sleep(PAGE_DELAY_MS);
  }

  return { listId: info.listId, url: info.url, found: allSlugs.length, slugs: allSlugs };
}

async function scrapeMoviesMetadata(cfg, slugs) {
  syncState.phase = "movies";
  syncState.currentPage = 0;
  syncState.totalPages = slugs.length;
  syncState.moviesProcessed = 0;
  broadcast();

  let processed = 0;
  for (let i = 0; i < slugs.length; i += METADATA_BATCH_SIZE) {
    if (syncState.stopRequested) break;
    const chunk = slugs.slice(i, i + METADATA_BATCH_SIZE);
    const movies = [];
    for (const slug of chunk) {
      if (syncState.stopRequested) break;
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 20000);
        let lbFilmId = "";
        try {
          const res = await fetchWithRetry(`${LB_BASE}/film/${slug}/`, {
            credentials: "include",
            headers: { "Accept": "text/html", "User-Agent": navigator.userAgent },
            signal: controller.signal,
          });
          lbFilmId = res.headers.get("x-letterboxd-identifier") || "";
          const html = await res.text();
          const movie = parseMovieFromHtml(slug, html);
          movie.lb_film_id = lbFilmId;
          movies.push(movie);
        } finally {
          clearTimeout(timeoutId);
        }
      } catch (e) {
        log(`ERROR metadata ${slug}: ${e.message}`);
      }
      processed += 1;
      syncState.currentPage = processed;
      syncState.moviesProcessed = processed;
      syncState.percent = Math.min(99, Math.floor((processed / Math.max(1, slugs.length)) * 100));
      broadcast();
      await sleep(MOVIE_DELAY_MS);
    }
    if (movies.length) {
      try {
        const result = await apiPost(cfg, "/api/extension/batch/movies", { movies });
        log(`metadata batch pushed ${movies.length} → stored=${result.stored}`);
      } catch (e) {
        log(`ERROR metadata batch push: ${e.message}`);
      }
    }
  }
  return { processed };
}

// ── Letterboxd write-back (runs in browser, uses real session cookie) ────────

async function writeToLetterboxd(action, movieSlug) {
  // Step 1: fetch the film page to get the CSRF token (browser session included)
  const filmUrl = `${LB_BASE}/film/${movieSlug}/`;
  const filmRes = await fetch(filmUrl, {
    credentials: "include",
    headers: { "Accept": "text/html" },
  });
  if (!filmRes.ok) throw new Error(`film page ${filmRes.status}`);
  const html = await filmRes.text();

  // Extract __csrf token — present in a hidden input on every authenticated page
  const csrfMatch = html.match(/name="__csrf"\s+value="([^"]+)"/)
    || html.match(/<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"/);
  if (!csrfMatch) throw new Error("CSRF token not found — are you signed in to Letterboxd?");
  const csrf = csrfMatch[1];

  if (action === "watchlist") {
    // Letterboxd watchlist toggle endpoint (AJAX, used by the web UI)
    const res = await fetch(`${LB_BASE}/s/save-film-watch`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": filmUrl,
      },
      body: new URLSearchParams({
        __csrf: csrf,
        filmSlug: movieSlug,
        inWatchlist: "true",
      }).toString(),
    });
    if (!res.ok) throw new Error(`watchlist write ${res.status}`);
    log(`[lb-write] watchlist added: ${movieSlug}`);
    return true;
  }

  if (action === "log") {
    // Diary save endpoint — logs the film as watched today
    const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
    const res = await fetch(`${LB_BASE}/s/save-film-watch`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": filmUrl,
      },
      body: new URLSearchParams({
        __csrf: csrf,
        filmSlug: movieSlug,
        viewingDateStr: today,
        specifiedDate: "true",
        rewatch: "false",
        rating: "",
        review: "",
        containsSpoilers: "false",
      }).toString(),
    });
    if (!res.ok) throw new Error(`diary write ${res.status}`);
    log(`[lb-write] diary logged: ${movieSlug}`);
    return true;
  }

  return false; // dismiss — no Letterboxd write needed
}

// ── Letterboxd LID backfill ──────────────────────────────────────────────────

async function backfillLbFilmIds() {
  const cfg = await getConfig();
  if (!cfg.username || !cfg.sessionToken) throw new Error("Not registered — connect first");

  log("backfill: fetching movies missing lb_film_id…");

  // Ask the API for slugs that have no lb_film_id yet
  const url = `${cfg.apiBase || DEFAULT_API_BASE}/api/extension/movies-missing-lb-id?limit=500`;
  const res = await fetchWithRetry(url, {
    headers: cfg.sessionToken ? { "X-Session-Token": cfg.sessionToken } : {},
  });
  if (!res.ok) throw new Error(`movies-missing-lb-id ${res.status}`);
  const { slugs } = await res.json();

  log(`backfill: ${slugs.length} movies to update`);
  let updated = 0, failed = 0;

  for (let i = 0; i < slugs.length; i++) {
    if (syncState.stopRequested) break;
    const slug = slugs[i];
    try {
      // Fetch the film page — x-letterboxd-identifier header contains the LID
      const filmRes = await fetchWithRetry(`${LB_BASE}/film/${slug}/`, {
        credentials: "include",
        headers: { "Accept": "text/html", "User-Agent": navigator.userAgent },
      });
      const lbFilmId = filmRes.headers.get("x-letterboxd-identifier") || "";
      if (lbFilmId) {
        await apiPost(cfg, "/actions/cache-lb-id", { movie_slug: slug, lb_film_id: lbFilmId });
        updated++;
      } else {
        failed++;
      }
    } catch (e) {
      failed++;
    }

    if ((i + 1) % 20 === 0) {
      log(`backfill: ${i + 1}/${slugs.length} — updated=${updated} failed=${failed}`);
    }
    await sleep(300);
  }

  log(`backfill: done — updated=${updated} failed=${failed}`);
  return { updated, failed };
}

// ── Entry points ─────────────────────────────────────────────────────────────

async function ensureConfig() {
  let cfg = await getConfig();
  if (!cfg.username || !cfg.sessionToken) {
    log("No credentials — auto-registering via Letterboxd cookie…");
    const reg = await registerExtension({ apiBase: cfg.apiBase });
    cfg = await getConfig();
    log(`Registered as ${reg.username}`);
  }
  return cfg;
}

async function runSync(opts = {}) {
  if (syncState.running) return { ok: false, error: "already running" };

  const stored = await chrome.storage.local.get([
    "syncHistory",
    "syncWatchlist",
    "syncDiary",
    "discoverLists",
    "fillLists",
    "discoverPages",
    "fillMaxLists",
    "fillListPages",
  ]);
  const settings = {
    syncWatchlist: opts.syncWatchlist ?? (stored.syncWatchlist ?? true),
    syncDiary: opts.syncDiary ?? (stored.syncDiary ?? true),
    discoverLists: opts.discoverLists ?? (stored.discoverLists ?? true),
    fillLists: opts.fillLists ?? (stored.fillLists ?? true),
    discoverPages: opts.discoverPages ?? stored.discoverPages ?? DEFAULT_DISCOVER_PAGES,
    fillMaxLists: opts.fillMaxLists ?? stored.fillMaxLists ?? DEFAULT_FILL_MAX_LISTS,
    fillListPages: opts.fillListPages ?? stored.fillListPages ?? DEFAULT_FILL_LIST_PAGES,
  };

  resetState("starting", "sync starting");
  broadcast();

  const summary = { watchlist: 0, diary: 0, discovered: 0, filled: 0, stopped: false };

  try {
    const cfg = await ensureConfig();

    if ((settings.syncWatchlist || settings.syncDiary) && !syncState.stopRequested) {
      const history = await scrapeUserHistory(cfg, settings);
      summary.watchlist = history.watchlist;
      summary.diary = history.diary;
      if (history.stopped) summary.stopped = true;
    }

    if (settings.discoverLists && !syncState.stopRequested) {
      const d = await discoverPublicLists(cfg, { maxPages: settings.discoverPages });
      summary.discovered = d.discovered;
    }

    if (settings.fillLists && !syncState.stopRequested) {
      const f = await fillUnderscrapedLists(cfg, {
        maxLists: settings.fillMaxLists,
        maxListPages: settings.fillListPages,
      });
      summary.filled = f.filled;
    }

    if (summary.stopped || syncState.stopRequested) {
      syncState.phase = "idle";
      syncState.percent = 0;
      await reportStatus(cfg, "idle", { message: "stopped by user" });
    } else {
      syncState.phase = "complete";
      syncState.percent = 100;
      await chrome.storage.local.set({ lastSync: Date.now() });
      await reportStatus(cfg, "complete", { slugs_found: summary.watchlist + summary.diary });
    }
    log(
      `Sync done — watchlist=${summary.watchlist} diary=${summary.diary} ` +
      `discovered=${summary.discovered} filled=${summary.filled}`,
    );
    return { ok: true, ...summary };
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

async function runListScrape(listUrl, opts = {}) {
  if (syncState.running) return { ok: false, error: "already running" };
  syncState = { ...syncState, running: true, stopRequested: false, phase: "list", percent: 0, lastLog: "list scrape starting" };
  broadcast();
  try {
    const cfg = await ensureConfig();
    const result = await scrapePublicList(cfg, listUrl);
    if (opts.fetchMetadata && result.slugs?.length) {
      await scrapeMoviesMetadata(cfg, result.slugs);
    }
    syncState.phase = "complete";
    syncState.percent = 100;
    broadcast();
    return { ok: true, ...result };
  } catch (e) {
    syncState.phase = "error";
    log(`FATAL: ${e.message}`);
    return { ok: false, error: e.message };
  } finally {
    syncState.running = false;
    broadcast();
  }
}

async function runMetadataScrape(slugs) {
  if (syncState.running) return { ok: false, error: "already running" };
  syncState = { ...syncState, running: true, stopRequested: false, phase: "movies", percent: 0, lastLog: `metadata scrape (${slugs.length})` };
  broadcast();
  try {
    const cfg = await ensureConfig();
    const result = await scrapeMoviesMetadata(cfg, slugs);
    syncState.phase = "complete";
    syncState.percent = 100;
    broadcast();
    return { ok: true, ...result };
  } catch (e) {
    syncState.phase = "error";
    log(`FATAL: ${e.message}`);
    return { ok: false, error: e.message };
  } finally {
    syncState.running = false;
    broadcast();
  }
}

// ── Messaging + alarms ──────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (!msg || !msg.type) return sendResponse({ ok: false });
    switch (msg.type) {
      case "GET_STATE":
        sendResponse({ ok: true, state: syncState });
        return;
      case "START_SYNC":
        sendResponse(await runSync({
          syncWatchlist: msg.syncWatchlist,
          syncDiary: msg.syncDiary,
          discoverLists: msg.discoverLists,
          fillLists: msg.fillLists,
        }));
        return;
      case "STOP_SYNC":
        syncState.stopRequested = true;
        sendResponse({ ok: true });
        return;
      case "REGISTER":
        try {
          const reg = await registerExtension({ apiBase: msg.apiBase });
          sendResponse({ ok: true, ...reg });
        } catch (e) {
          sendResponse({ ok: false, error: e.message });
        }
        return;
      case "SCRAPE_LIST":
        sendResponse(await runListScrape(msg.listUrl, { fetchMetadata: !!msg.fetchMetadata }));
        return;
      case "SCRAPE_MOVIES":
        sendResponse(await runMetadataScrape(msg.slugs || []));
        return;
      case "DISCOVER_LISTS":
        sendResponse(await runSync({
          syncHistory: false,
          discoverLists: true,
          fillLists: false,
          discoverPages: msg.pages,
        }));
        return;
      case "FILL_LISTS":
        sendResponse(await runSync({
          syncHistory: false,
          discoverLists: false,
          fillLists: true,
          fillMaxLists: msg.maxLists,
          fillListPages: msg.listPages,
        }));
        return;
      case "SET_AUTO_SYNC":
        if (msg.value) chrome.alarms.create(ALARM_NAME, { periodInMinutes: 360 });
        else chrome.alarms.clear(ALARM_NAME);
        sendResponse({ ok: true });
        return;
      case "SWIPERBOXD_AUTH":
        await chrome.storage.local.set({
          apiBase: msg.apiBase,
          username: msg.username,
          sessionToken: msg.sessionToken,
        });
        sendResponse({ ok: true });
        return;
      case "LB_WRITE":
        try {
          const ok = await writeToLetterboxd(msg.action, msg.movieSlug);
          sendResponse({ ok });
        } catch (e) {
          sendResponse({ ok: false, error: e.message });
        }
        return;
      case "BACKFILL_LB_IDS":
        try {
          const result = await backfillLbFilmIds();
          sendResponse({ ok: true, ...result });
        } catch (e) {
          sendResponse({ ok: false, error: e.message });
        }
        return;
      default:
        sendResponse({ ok: false, error: "unknown type" });
    }
  })();
  return true;
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== ALARM_NAME) return;
  if (syncState.running) return;
  console.log("[swiperboxd-ext] periodic sync fired");
  await runSync();
});

chrome.runtime.onInstalled.addListener(async () => {
  const cfg = await getConfig();
  if (cfg.autoSync) chrome.alarms.create(ALARM_NAME, { periodInMinutes: 360 });
});
