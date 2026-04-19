import { createSuppressionStore } from './state.js';

const suppression = createSuppressionStore(() => Date.now());

function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

function buildPosterVariants(url) {
  const base = String(url || '').trim();
  if (!base) {
    return { display: '', backdrop: '', srcset: '' };
  }

  const sizePattern = /\/image-(\d+)\//i;
  if (!sizePattern.test(base)) {
    return { display: base, backdrop: base, srcset: '' };
  }

  const withSize = (n) => base.replace(sizePattern, `/image-${n}/`);
  const display = withSize(600);
  const backdrop = withSize(1200);
  const srcset = `${withSize(300)} 300w, ${display} 600w, ${withSize(1200)} 1200w`;
  return { display, backdrop, srcset };
}

function toCssUrl(value) {
  return `url("${String(value || '').replace(/"/g, '\\"')}")`;
}

// ==================== STATE ====================

const state = {
  username: null,
  encryptedSession: null,
  hasSynced: false,
  deck: [],
  currentIndex: 0,
  isSyncing: false,
  lists: [],
  selectedListId: null,
  selectedListTitle: 'Choose a List',
  listSearchQuery: '',
};

const EXT_LOG_PREFIX = '[swiperboxd-web/ext]';
const EXT_MAX_PRESENT_SIGNALS = 20;

const extensionBridge = {
  presentSignals: [],
  authAttempts: 0,
  swipeAttempts: 0,
  crossSyncAttempts: 0,
  lastAuthRequestId: null,
  lastAuthError: null,
  lastAuthStartedAt: null,
  lastAuthCompletedAt: null,
};

const CROSS_SYNC_COOLDOWN_MS = 6 * 60 * 60 * 1000;
const crossSyncAttemptedUsers = new Set();

function extLog(message, meta) {
  const ts = new Date().toISOString();
  if (meta !== undefined) console.info(`${EXT_LOG_PREFIX} ${ts} ${message}`, meta);
  else console.info(`${EXT_LOG_PREFIX} ${ts} ${message}`);
}

function noteExtensionPresence(payload = {}) {
  extensionBridge.presentSignals.push({
    seenAt: Date.now(),
    payload,
  });
  if (extensionBridge.presentSignals.length > EXT_MAX_PRESENT_SIGNALS) {
    extensionBridge.presentSignals.splice(0, extensionBridge.presentSignals.length - EXT_MAX_PRESENT_SIGNALS);
  }
}

function extensionDiagnostics() {
  return {
    origin: window.location.origin,
    href: window.location.href,
    userAgent: navigator.userAgent,
    presentSignalCount: extensionBridge.presentSignals.length,
    lastPresenceAt: extensionBridge.presentSignals.length
      ? new Date(extensionBridge.presentSignals[extensionBridge.presentSignals.length - 1].seenAt).toISOString()
      : null,
    lastAuthRequestId: extensionBridge.lastAuthRequestId,
    lastAuthError: extensionBridge.lastAuthError,
    lastAuthStartedAt: extensionBridge.lastAuthStartedAt ? new Date(extensionBridge.lastAuthStartedAt).toISOString() : null,
    lastAuthCompletedAt: extensionBridge.lastAuthCompletedAt ? new Date(extensionBridge.lastAuthCompletedAt).toISOString() : null,
  };
}

window.addEventListener('message', (event) => {
  if (event.source !== window) return;
  const d = event.data;
  if (d?.type === 'SWIPERBOXD_EXT_PRESENT') {
    noteExtensionPresence(d);
    extLog('received extension presence signal', {
      source: d.source || 'unknown',
      emittedAt: d.emittedAt || null,
      href: d.href || null,
    });
  }
});

function requestExtensionSwipe(action, movieSlug, timeoutMs = 20000) {
  return new Promise((resolve) => {
    const requestId = `swipe-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    extensionBridge.swipeAttempts += 1;
    extLog('starting swipe bridge request', {
      requestId,
      action,
      movieSlug,
      timeoutMs,
      attempt: extensionBridge.swipeAttempts,
      diagnostics: extensionDiagnostics(),
    });

    const timer = setTimeout(() => {
      window.removeEventListener('message', handler);
      extLog('swipe bridge timed out', {
        requestId,
        action,
        movieSlug,
        diagnostics: extensionDiagnostics(),
      });
      resolve({
        lbSynced: false,
        error: 'extension timed out — is it installed and signed in to Letterboxd?',
        requestId,
      });
    }, timeoutMs);

    function handler(event) {
      if (event.source !== window) return;
      const d = event.data;
      if (d?.type === 'SWIPERBOXD_EXT_PRESENT') noteExtensionPresence(d);
      if (d?.type === 'SWIPERBOXD_SWIPE_RESULT' && d.movieSlug === movieSlug && d.action === action) {
        if (d.requestId && d.requestId !== requestId) {
          extLog('ignoring swipe result for different request id', {
            expected: requestId,
            actual: d.requestId,
            action,
            movieSlug,
          });
          return;
        }
        clearTimeout(timer);
        window.removeEventListener('message', handler);
        extLog('received swipe bridge response', {
          requestId,
          action,
          movieSlug,
          lbSynced: d.lbSynced,
          error: d.error || null,
        });
        resolve({ lbSynced: d.lbSynced, error: d.error, requestId: d.requestId || requestId });
      }
    }

    window.addEventListener('message', handler);
    window.postMessage({
      type: 'SWIPERBOXD_SWIPE',
      action,
      movieSlug,
      requestId,
      sentAt: Date.now(),
    }, window.location.origin);
  });
}

function crossSyncStorageKey(username) {
  return `swiperboxd.cross-sync.${String(username || '').toLowerCase()}`;
}

function shouldRunCrossSync(username) {
  if (!username) return false;
  try {
    const key = crossSyncStorageKey(username);
    const last = Number(localStorage.getItem(key) || '0');
    if (!Number.isFinite(last) || last <= 0) return true;
    return (Date.now() - last) >= CROSS_SYNC_COOLDOWN_MS;
  } catch {
    return true;
  }
}

function markCrossSyncSuccess(username) {
  if (!username) return;
  try {
    localStorage.setItem(crossSyncStorageKey(username), String(Date.now()));
  } catch {
    // ignore storage errors; cross-sync can still run again later
  }
}

function setCrossSyncBadge(variant = 'idle', text = 'Sync pending') {
  const badge = $('#cross-sync-badge');
  if (!badge) return;
  badge.classList.remove('sync-badge-idle', 'sync-badge-running', 'sync-badge-success', 'sync-badge-error');
  badge.classList.add(`sync-badge-${variant}`);
  badge.textContent = text;
}

function requestExtensionCrossSync(timeoutMs = 180000, maxPushPerKind = 300) {
  return new Promise((resolve) => {
    const requestId = `cross-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    extensionBridge.crossSyncAttempts += 1;
    extLog('starting cross-sync bridge request', {
      requestId,
      timeoutMs,
      maxPushPerKind,
      attempt: extensionBridge.crossSyncAttempts,
      diagnostics: extensionDiagnostics(),
    });

    const timer = setTimeout(() => {
      window.removeEventListener('message', handler);
      extLog('cross-sync bridge timed out', {
        requestId,
        diagnostics: extensionDiagnostics(),
      });
      resolve({
        ok: false,
        error: 'cross-sync timed out — open extension popup and run Start Sync once, then retry.',
        requestId,
        summary: null,
      });
    }, timeoutMs);

    function handler(event) {
      if (event.source !== window) return;
      const d = event.data;
      if (d?.type === 'SWIPERBOXD_EXT_PRESENT') noteExtensionPresence(d);
      if (d?.type === 'SWIPERBOXD_CROSS_SYNC_RESULT') {
        if (d.requestId && d.requestId !== requestId) {
          extLog('ignoring cross-sync result for different request id', {
            expected: requestId,
            actual: d.requestId,
          });
          return;
        }
        clearTimeout(timer);
        window.removeEventListener('message', handler);
        extLog('received cross-sync bridge response', {
          requestId,
          ok: d.ok === true,
          error: d.error || null,
        });
        resolve({
          ok: d.ok === true,
          error: d.error || null,
          summary: d.summary || null,
          requestId: d.requestId || requestId,
        });
      }
    }

    window.addEventListener('message', handler);
    window.postMessage({
      type: 'SWIPERBOXD_CROSS_SYNC',
      requestId,
      maxPushPerKind,
      sentAt: Date.now(),
    }, window.location.origin);
  });
}

// ==================== DOM REFS ====================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const setupScreen     = $('#setup-screen');
const discoveryScreen = $('#discovery-screen');
const cardStack       = $('#card-stack');
const loadingSkeleton = $('#loading-skeleton');
const emptyState      = $('#empty-state');
const profileOptions  = $('#profile-options');
const profileDropdown = $('#profile-dropdown');
const currentProfileSpan = $('#current-profile');
const listSearchInput = $('#list-search-input');

// ==================== INIT ====================

document.addEventListener('DOMContentLoaded', () => {
  initAuth();
  initDiscovery();
  checkSavedSession();
});

// ==================== AUTH ====================

function initAuth() {
  $('#setup-connect-btn')?.addEventListener('click', connectViaExtension);
  $('#logout-btn')?.addEventListener('click', () => {
    state.username = null;
    state.encryptedSession = null;
    state.hasSynced = false;
    discoveryScreen.classList.remove('active');
    setupScreen.classList.add('active');
  });
}

function requestExtensionAuth(timeoutMs = 4000) {
  return new Promise((resolve) => {
    const requestId = `auth-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    extensionBridge.authAttempts += 1;
    extensionBridge.lastAuthRequestId = requestId;
    extensionBridge.lastAuthStartedAt = Date.now();
    extensionBridge.lastAuthCompletedAt = null;
    extensionBridge.lastAuthError = null;

    extLog('starting auth bridge request', {
      requestId,
      timeoutMs,
      attempt: extensionBridge.authAttempts,
      diagnostics: extensionDiagnostics(),
    });

    const timer = setTimeout(() => {
      window.removeEventListener('message', handler);
      extensionBridge.lastAuthCompletedAt = Date.now();
      const diag = extensionDiagnostics();
      const noPresenceSignal = diag.presentSignalCount === 0;
      const error = noPresenceSignal
        ? `Extension bridge timed out on ${window.location.origin}. The extension content script did not respond on this page. Check extension Site access for this URL, reload the page, then click Connect again.`
        : 'Extension responded earlier but auth did not return in time — open extension popup, click Connect, then retry.';
      extensionBridge.lastAuthError = error;
      extLog('auth bridge timed out', { requestId, diagnostics: diag });
      resolve({ ok: false, error, requestId, diagnostics: diag });
    }, timeoutMs);

    function handler(event) {
      if (event.source !== window) return;
      const d = event.data;
      if (d?.type === 'SWIPERBOXD_EXT_PRESENT') noteExtensionPresence(d);
      if (d?.type === 'SWIPERBOXD_AUTH_RESULT') {
        if (d.requestId && d.requestId !== requestId) {
          extLog('ignoring auth result for different request id', {
            expected: requestId,
            actual: d.requestId,
          });
          return;
        }
        clearTimeout(timer);
        window.removeEventListener('message', handler);
        extensionBridge.lastAuthCompletedAt = Date.now();
        extensionBridge.lastAuthError = d.ok ? null : (d.error || 'unknown auth bridge error');
        extLog('received auth bridge response', {
          requestId,
          ok: d.ok,
          username: d.username || null,
          error: d.error || null,
        });
        resolve(d.requestId ? d : { ...d, requestId });
      }
    }

    window.addEventListener('message', handler);
    window.postMessage({
      type: 'SWIPERBOXD_GET_AUTH',
      requestId,
      sentAt: Date.now(),
    }, window.location.origin);
  });
}

async function connectViaExtension() {
  const btn = $('#setup-connect-btn');
  const errDiv = $('#setup-error');
  btn.disabled = true;
  btn.textContent = 'Connecting…';
  errDiv.classList.add('hidden');

  const result = await requestExtensionAuth();
  btn.disabled = false;
  btn.textContent = 'Connect via extension →';

  if (!result.ok) {
    extLog('connectViaExtension failed', {
      requestId: result.requestId || null,
      error: result.error,
      diagnostics: result.diagnostics || extensionDiagnostics(),
    });
    errDiv.textContent = `${result.error} (see DevTools console logs tagged ${EXT_LOG_PREFIX})`;
    errDiv.classList.remove('hidden');
    return;
  }

  extLog('connectViaExtension succeeded', {
    requestId: result.requestId || null,
    username: result.username,
  });

  state.username = result.username;
  state.encryptedSession = result.sessionToken;
  showDiscovery();
}

function checkSavedSession() {
  // Try silent auto-connect on load — succeeds if extension is installed and connected
  requestExtensionAuth(3000).then((result) => {
    extLog('silent auth attempt completed', {
      ok: result.ok,
      requestId: result.requestId || null,
      error: result.error || null,
    });
    if (result.ok) {
      state.username = result.username;
      state.encryptedSession = result.sessionToken;
      showDiscovery();
    }
    // On failure just stay on setup screen — user clicks Connect manually
  });
}

async function maybeRunInitialCrossSync() {
  const username = state.username;
  if (!username || username === '_guest_') return;
  if (crossSyncAttemptedUsers.has(username)) return;
  if (!shouldRunCrossSync(username)) {
    setCrossSyncBadge('success', 'Synced');
    return;
  }

  crossSyncAttemptedUsers.add(username);
  setCrossSyncBadge('running', 'Syncing…');
  showToast('Syncing Letterboxd ↔ Supabase…');

  let lastError = null;
  for (let attempt = 1; attempt <= 2; attempt++) {
    const result = await requestExtensionCrossSync(180000, 300);
    if (result.ok) {
      markCrossSyncSuccess(username);
      const summary = result.summary || {};
      const pulled = Number(summary.watchlistPulled || 0) + Number(summary.diaryPulled || 0);
      const pushed = Number(summary.watchlistPushed || 0) + Number(summary.diaryPushed || 0);
      setCrossSyncBadge('success', `Synced • +${pulled}/${pushed}`);
      showToast(`Cross-sync complete • pulled ${pulled}, pushed ${pushed}`);
      // Clear suppression store after successful sync — user may want to re-see movies
      extLog('clearing suppression store after sync', { suppressedCount: suppression.size() });
      // Re-load the deck to clear any suppressed movies
      await loadLists(state.listSearchQuery || '');
      return;
    }
    lastError = result.error;
    extLog(`initial cross-sync attempt ${attempt}/2 failed`, {
      username,
      requestId: result.requestId || null,
      error: result.error || null,
    });
    if (attempt < 2) {
      showToast('Sync failed, retrying…');
      await new Promise(r => setTimeout(r, 1500 * attempt));
    }
  }

  setCrossSyncBadge('error', 'Sync failed');
  extLog('initial cross-sync FAILED after retries', {
    username,
    error: lastError,
  });
  showToast(lastError || 'Cross-sync failed — open extension popup and run Start Sync');
}

function applyWriteAccess() {
  const hint = $('#browse-mode-hint');
  const watchlistBtn = $('#btn-watchlist');
  const logBtn = $('#btn-log');
  const reason = 'Sync your Letterboxd data via the extension first to enable saving';

  watchlistBtn.disabled = !state.hasSynced;
  logBtn.disabled = !state.hasSynced;
  if (!state.hasSynced) {
    watchlistBtn.title = reason;
    logBtn.title = reason;
    if (hint) { hint.textContent = 'Sync extension to enable saving'; hint.classList.remove('hidden'); }
  } else {
    watchlistBtn.title = 'Watchlist  [→]';
    logBtn.title = 'Watched  [↑]';
    if (hint) hint.classList.add('hidden');
  }
}

function showDiscovery() {
  setupScreen.classList.remove('active');
  discoveryScreen.classList.add('active');
  setCrossSyncBadge('idle', 'Sync pending');
  loadLists();
}

// ==================== DISCOVERY ====================

function initDiscovery() {
  $('#profile-btn')?.addEventListener('click', () => {
    profileDropdown.classList.toggle('hidden');
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#profile-btn') && !e.target.closest('#profile-dropdown')) {
      profileDropdown.classList.add('hidden');
    }
  });

  listSearchInput?.addEventListener('input', async (e) => {
    state.listSearchQuery = e.target.value.trim();
    await loadLists(state.listSearchQuery);
  });

  $('#refresh-btn')?.addEventListener('click', () => loadDeck());

  // Action buttons
  $('#btn-dismiss')?.addEventListener('click', () => executeSwipe('dismiss'));
  $('#btn-watchlist')?.addEventListener('click', () => executeSwipe('watchlist'));
  $('#btn-log')?.addEventListener('click', () => executeSwipe('log'));
  $('#btn-flip')?.addEventListener('click', toggleInfoPill);

  // Info pill: dismiss on click outside
  $('#info-pill-overlay')?.addEventListener('click', (e) => {
    if (!e.target.closest('#info-pill')) hideInfoPill();
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (!discoveryScreen.classList.contains('active')) return;
    if (e.key === 'Escape') { hideInfoPill(); return; }
    if (state.deck.length === 0 || state.currentIndex >= state.deck.length) return;
    switch (e.key) {
      case 'ArrowLeft':  case 'a': executeSwipe('dismiss');   break;
      case 'ArrowRight': case 'd': executeSwipe('watchlist'); break;
      case 'ArrowUp':    case 'w': executeSwipe('log');       break;
      case ' ': e.preventDefault(); toggleInfoPill(); break;
    }
  });
}

// ==================== LISTS ====================

async function loadLists(query = '') {
  try {
    const res = await api(`/lists/catalog?q=${encodeURIComponent(query)}`);
    state.lists = res.results || [];
    renderLists();

    if (state.lists.length === 0) {
      cardStack.classList.add('hidden');
      setEmptyState(
        '📋',
        'No lists available',
        'Use the Chrome extension to sync Letterboxd lists into Swiperboxd.',
      );
      return;
    }
  } catch (err) {
    console.error('[lists] failed:', err.message);
    setEmptyState('⚠️', 'Error loading lists', err.message || 'Check your connection.');
    return;
  }

  if (!state.selectedListId && state.lists.length > 0) {
    // Pick the top-ranked list deterministically (backend already sorts by
    // official + popularity). Random selection can land users on an empty list
    // and create a false "sync is broken" impression.
    const pick = state.lists[0];
    state.selectedListId = pick.list_id;
    state.selectedListTitle = pick.title;
    currentProfileSpan.textContent = state.selectedListTitle;
  }

  // Check if this user has synced Letterboxd data into Supabase
  if (state.username && state.username !== '_guest_') {
    try {
      const status = await api(`/users/${encodeURIComponent(state.username)}/sync-status`);
      state.hasSynced = status.has_synced;
    } catch (_) {
      state.hasSynced = false;
    }
  } else {
    state.hasSynced = false;
  }

  applyWriteAccess();
  if (!state.username || state.username === '_guest_') {
    setCrossSyncBadge('idle', 'Connect extension');
  } else if (!shouldRunCrossSync(state.username)) {
    setCrossSyncBadge('success', 'Synced');
  } else {
    setCrossSyncBadge('idle', 'Sync pending');
  }
  void maybeRunInitialCrossSync();
  if (state.selectedListId) loadDeck();
}

function renderLists() {
  profileOptions.innerHTML = state.lists.map(item => `
    <div class="profile-option ${item.list_id === state.selectedListId ? 'active' : ''}"
         data-list-id="${esc(item.list_id)}">
      <div class="list-option-title">${esc(item.title)}</div>
      <div class="list-option-meta">${esc(item.owner_name)} · ${esc(item.film_count)} films</div>
    </div>
  `).join('');

  profileOptions.querySelectorAll('.profile-option').forEach(opt => {
    opt.addEventListener('click', () => {
      const selected = state.lists.find(item => item.list_id === opt.dataset.listId);
      state.selectedListId = opt.dataset.listId;
      state.selectedListTitle = selected?.title || 'Choose a List';
      currentProfileSpan.textContent = state.selectedListTitle;
      profileDropdown.classList.add('hidden');
      $$('.profile-option').forEach(p => p.classList.remove('active'));
      opt.classList.add('active');
      loadDeck();
    });
  });

  currentProfileSpan.textContent = state.selectedListTitle;
}

// ==================== DECK ====================

async function loadDeck() {
  if (!state.username || !state.selectedListId) return;
  if (state.isSyncing) return;

  hideInfoPill();
  cardStack.classList.add('hidden');
  emptyState.classList.add('hidden');
  loadingSkeleton.classList.remove('hidden');

  try {
    const res = await api(
      `/lists/${encodeURIComponent(state.selectedListId)}/deck?user_id=${encodeURIComponent(state.username)}`
    );
    const raw = res.results || [];
    const filtered = raw.filter(m => !suppression.isSuppressed(m.slug));
    state.deck = filtered;
    state.currentIndex = 0;

    loadingSkeleton.classList.add('hidden');

    // Detailed logging for debugging empty deck issues
    if (raw.length > 0 && filtered.length === 0) {
      const suppressedCount = raw.length;
      console.warn(`[deck] WARNING: All ${suppressedCount} movies filtered by suppression store!`, {
        suppressedCount,
        suppressedSlugs: raw.slice(0, 5).map(m => m.slug),
        username: state.username,
        listId: state.selectedListId,
      });
    }

    extLog('deck loaded', {
      username: state.username,
      listId: state.selectedListId,
      total: raw.length,
      afterSuppression: filtered.length,
      hasSynced: state.hasSynced,
    });

    if (state.deck.length > 0) {
      renderDeck();
    } else {
      if (state.hasSynced) {
        const listLabel = state.selectedListTitle && state.selectedListTitle !== 'Choose a List'
          ? `"${state.selectedListTitle}"`
          : 'This list';
        setEmptyState(
          '🎬',
          'No movies left in this list',
          `${listLabel} has no unseen movies for your account right now. Try another list, or run Start Sync to pull in more data.`,
        );
      } else {
        setEmptyState(
          '🎬',
          'No movies to show',
          'Open the Chrome extension popup and click Start Sync to load your Letterboxd data into Swiperboxd.',
        );
      }
    }
  } catch (err) {
    console.error('[deck] load failed:', err.message);
    loadingSkeleton.classList.add('hidden');
    setEmptyState('⚠️', 'Failed to load deck', err.message);
  }
}

function setEmptyState(icon, title, body) {
  emptyState.innerHTML = `
    <span class="empty-icon">${icon}</span>
    <h2>${esc(title)}</h2>
    <p>${esc(body)}</p>
    <button id="empty-retry-btn" class="btn-secondary" style="margin-top:0.5rem">Retry</button>
  `;
  emptyState.querySelector('#empty-retry-btn')?.addEventListener('click', () => loadDeck());
  emptyState.classList.remove('hidden');
}

function renderDeck() {
  cardStack.classList.remove('hidden');
  cardStack.innerHTML = '';
  emptyState.classList.add('hidden');
  if (state.currentIndex < state.deck.length) {
    cardStack.appendChild(createCard(state.deck[state.currentIndex]));
  }
}

function createCard(movie) {
  const card = document.createElement('div');
  card.className = 'movie-card';
  card.dataset.slug = movie.slug;

  const poster = buildPosterVariants(movie.poster_url);
  const posterDisplay = poster.display || movie.poster_url || '';
  const posterBackdrop = poster.backdrop || posterDisplay;
  const posterSrcsetAttr = poster.srcset ? `srcset="${esc(poster.srcset)}" sizes="100vw"` : '';
  if (posterBackdrop) {
    card.style.setProperty('--poster-backdrop', toCssUrl(posterBackdrop));
  }

  card.innerHTML = `
    <div class="card-backdrop" aria-hidden="true"></div>
    <img class="card-poster" src="${esc(posterDisplay)}" ${posterSrcsetAttr} alt="${esc(movie.title)}" />
    <div class="card-overlay">
      <h2 class="card-title">${esc(movie.title)}${movie.year ? ` <span style="font-weight:400;opacity:.7">(${esc(String(movie.year))})</span>` : ''}</h2>
      <p class="card-meta">★ ${esc(String(movie.rating ?? ''))}${movie.director ? ` · ${esc(movie.director)}` : ''}</p>
    </div>
  `;
  return card;
}

// ==================== INFO PILL ====================

function toggleInfoPill() {
  const overlay = $('#info-pill-overlay');
  if (overlay.classList.contains('hidden')) showInfoPill();
  else hideInfoPill();
}

function showInfoPill() {
  if (state.deck.length === 0 || state.currentIndex >= state.deck.length) return;
  const movie = state.deck[state.currentIndex];

  $('#pill-title').textContent = movie.title + (movie.year ? ` (${movie.year})` : '');

  const meta = [];
  if (movie.rating) meta.push(`★ ${movie.rating}`);
  if (movie.director) meta.push(movie.director);
  $('#pill-meta').textContent = meta.join(' · ');

  $('#pill-genres').innerHTML = (movie.genres || []).slice(0, 5)
    .map(g => `<span class="genre-tag">${esc(g)}</span>`).join('');

  $('#pill-synopsis').textContent = movie.synopsis || 'No synopsis available.';

  $('#pill-cast').textContent = movie.cast?.length
    ? 'Cast: ' + movie.cast.slice(0, 5).join(', ')
    : '';

  $('#info-pill-overlay').classList.remove('hidden');
}

function hideInfoPill() {
  $('#info-pill-overlay').classList.add('hidden');
}

// ==================== SWIPE ====================

async function executeSwipe(action) {
  if (state.isSyncing || state.deck.length === 0 || state.currentIndex >= state.deck.length) return;
  if (action !== 'dismiss' && !state.hasSynced) {
    showToast('Sync your Letterboxd data via the extension before saving');
    return;
  }

  hideInfoPill();
  state.isSyncing = true;

  const topCard = cardStack.querySelector('.movie-card');
  const movie   = state.deck[state.currentIndex];

  topCard.classList.add(
    action === 'watchlist' ? 'swiping-right' :
    action === 'dismiss'   ? 'swiping-left'  : 'swiping-up'
  );

  let advanceCard = true;
  try {
    if (action === 'dismiss') {
      suppression.dismiss(movie.slug);
      await api('/actions/swipe', {
        method: 'POST',
        body: { user_id: state.username, movie_slug: movie.slug, action },
      });
    } else {
      const result = await requestExtensionSwipe(action, movie.slug);
      if (!result.lbSynced) {
        showToast(result.error || 'Letterboxd write failed — extension not responding');
        advanceCard = false;
        topCard.classList.remove('swiping-right', 'swiping-left', 'swiping-up');
      } else {
        try {
          await api('/actions/swipe', {
            method: 'POST',
            body: { user_id: state.username, movie_slug: movie.slug, action },
          });
        } catch (err) {
          if (err.status === 409) {
            const code = err.code || err.data?.code;
            showToast(
              code === 'already_in_watchlist' ? 'Already in your watchlist' :
              code === 'already_in_diary'     ? 'Already in your diary'     : 'Already saved'
            );
          } else {
            console.error('[swipe] supabase write failed:', err.message);
          }
        }
      }
    }
  } catch (err) {
    console.error('[swipe] failed:', err.message);
    advanceCard = false;
    topCard.classList.remove('swiping-right', 'swiping-left', 'swiping-up');
  }

  try {
    if (advanceCard) {
      state.currentIndex++;
      await delay(400);
      topCard.remove();

      if (state.currentIndex >= state.deck.length) {
        setEmptyState('🎬', "You've seen everything", 'Try another list, or sync more via the Chrome extension.');
      } else {
        renderDeck();
      }
    }
  } finally {
    state.isSyncing = false;
  }
}

// ==================== API ====================

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.encryptedSession) headers['X-Session-Token'] = state.encryptedSession;

  const res = await fetch(path, {
    method: options.method || 'GET',
    headers,
    ...(options.body ? { body: JSON.stringify(options.body) } : {}),
  });

  if (!res.ok) {
    const data   = await res.json().catch(() => ({}));
    const detail = data?.detail;
    const msg    = (typeof detail === 'string' ? detail : detail?.reason || detail?.code) || `HTTP ${res.status}`;
    const err    = new Error(msg);
    err.status   = res.status;
    err.code     = typeof detail === 'object' ? detail?.code : (data?.code || null);
    err.data     = data;
    throw err;
  }

  return res.json();
}

// ==================== UTILS ====================

function showToast(message, durationMs = 2200) {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    el.className = 'toast hidden';
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(el._toastTimer);
  el._toastTimer = setTimeout(() => el.classList.add('hidden'), durationMs);
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
