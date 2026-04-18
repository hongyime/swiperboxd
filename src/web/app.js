/**
 * Swiperboxd - Movie Discovery App
 * Uses Letterboxd authentication (username/password)
 */

import { createSuppressionStore } from './state.js';

const suppression = createSuppressionStore(() => Date.now());

// HTML-escape helper — prevents XSS when interpolating server data into innerHTML
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

// State
const state = {
  username: null,
  encryptedSession: null,
  deck: [],
  currentIndex: 0,
  isSyncing: false,
  syncComplete: false,
  flipped: false,
  lists: [],
  selectedListId: null,
  selectedListTitle: 'Choose a List',
  listSearchQuery: ''
};

// Cancel token for the background ingest poll loop
let _ingestPollCancelled = false;

// DOM Elements
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Screens
const authScreen = $('#auth-screen');
const discoveryScreen = $('#discovery-screen');

// Auth Elements
const loginForm = $('#letterboxd-login-form');
const errorDiv = $('#auth-error');
const successDiv = $('#auth-success');

// Discovery Elements
const cardStack = $('#card-stack');
const loadingSkeleton = $('#loading-skeleton');
const emptyState = $('#empty-state');
const syncOverlay = $('#sync-overlay');
const profileOptions = $('#profile-options');
const profileDropdown = $('#profile-dropdown');
const currentProfileSpan = $('#current-profile');
const listSearchInput = $('#list-search-input');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  initLetterboxdAuth();
  initDiscovery();
  checkSavedSession();
});

// ==================== LETTERBOXD AUTH ====================

function initLetterboxdAuth() {
  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#letterboxd-username').value.trim();
    const sessionCookie = $('#letterboxd-session-cookie').value.trim();

    if (!username || !sessionCookie) return;

    console.log('[auth] attempting session validation for user:', username);
    try {
      const res = await api('/auth/session', {
        method: 'POST',
        body: { username, session_cookie: sessionCookie }
      });

      state.username = username;
      state.encryptedSession = res.encrypted_session_cookie;

      localStorage.setItem('swiperboxd_username', username);
      localStorage.setItem('swiperboxd_token', res.encrypted_session_cookie);

      broadcastAuthToExtension();

      console.log('[auth] login success, transitioning to discovery');
      showSuccess('Connected to Letterboxd!');
      setTimeout(() => showDiscovery(), 1000);
    } catch (err) {
      console.error('[auth] login failed:', err.message);
      showError(err.message || 'Failed to connect to Letterboxd. Check your credentials.');
    }
  });

  $('#logout-btn')?.addEventListener('click', () => {
    console.log('[auth] logging out');
    _ingestPollCancelled = true;
    hideSyncBadge();
    localStorage.removeItem('swiperboxd_username');
    localStorage.removeItem('swiperboxd_token');
    localStorage.removeItem('swiperboxd_initial_sync_done');
    state.username = null;
    state.encryptedSession = null;
    state.syncComplete = false;
    showAuth();
  });
}

function checkSavedSession() {
  const username = localStorage.getItem('swiperboxd_username');
  const session = localStorage.getItem('swiperboxd_token');

  if (username && session) {
    console.log('[session] restored from localStorage for user:', username);
    state.username = username;
    state.encryptedSession = session;
    broadcastAuthToExtension();
    showDiscovery();
  } else {
    console.log('[session] no saved session, showing auth screen');
    showAuth();
  }
}

function broadcastAuthToExtension() {
  if (!state.username || !state.encryptedSession) return;
  try {
    window.postMessage({
      type: 'SWIPERBOXD_AUTH',
      username: state.username,
      sessionToken: state.encryptedSession,
      apiBase: window.location.origin,
    }, window.location.origin);
  } catch (e) {
    console.warn('[auth] extension postMessage failed:', e);
  }
}

function showAuth() {
  authScreen.classList.add('active');
  discoveryScreen.classList.remove('active');
}

function showDiscovery() {
  authScreen.classList.remove('active');
  discoveryScreen.classList.add('active');
  // Load list catalog first, then auto-load deck for the first list
  loadLists();
}

function showError(msg) {
  errorDiv.textContent = msg;
  errorDiv.classList.remove('hidden');
  successDiv.classList.add('hidden');
}

function showSuccess(msg) {
  successDiv.textContent = msg;
  successDiv.classList.remove('hidden');
  errorDiv.classList.add('hidden');
}

// ==================== DISCOVERY ====================

function initDiscovery() {
  $('#profile-btn')?.addEventListener('click', () => {
    profileDropdown.classList.toggle('hidden');
  });

  listSearchInput?.addEventListener('input', async (e) => {
    state.listSearchQuery = e.target.value.trim();
    await loadLists(state.listSearchQuery);
  });

  $('#refresh-btn')?.addEventListener('click', () => {
    console.log('[deck] manual refresh triggered');
    loadDeck();
  });

  // Refresh lists button (in header)
  $('#refresh-lists-btn')?.addEventListener('click', async () => {
    const btn = $('#refresh-lists-btn');
    btn.disabled = true;
    btn.classList.add('spinning');
    try {
      await api('/lists/refresh', { method: 'POST' });
      await loadLists();
    } catch (err) {
      console.error('[lists] refresh failed:', err.message);
    } finally {
      btn.disabled = false;
      btn.classList.remove('spinning');
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#profile-btn') && !e.target.closest('#profile-dropdown')) {
      profileDropdown.classList.add('hidden');
    }
  });

  // Action bar buttons
  $('#btn-dismiss')?.addEventListener('click', () => executeSwipe('dismiss'));
  $('#btn-watchlist')?.addEventListener('click', () => executeSwipe('watchlist'));
  $('#btn-log')?.addEventListener('click', () => executeSwipe('log'));
  $('#btn-flip')?.addEventListener('click', () => flipCard());

  document.addEventListener('keydown', (e) => {
    if (discoveryScreen.classList.contains('active') && state.deck.length > 0) {
      switch (e.key) {
        case 'ArrowLeft': case 'a': executeSwipe('dismiss'); break;
        case 'ArrowRight': case 'd': executeSwipe('watchlist'); break;
        case 'ArrowUp': case 'w': executeSwipe('log'); break;
        case ' ': e.preventDefault(); flipCard(); break;
      }
    }
  });

  initTouchSwipe();
}

async function loadLists(query = '') {
  console.log('[lists] fetching available lists');
  try {
    const res = await api(`/lists/catalog?q=${encodeURIComponent(query)}`);
    state.lists = res.results || [];
    console.log('[lists] loaded:', state.lists);
    renderLists();
    
    if (state.lists.length === 0) {
      console.warn('[lists] no lists available - showing empty state');
      cardStack.classList.add('hidden');
      emptyState.innerHTML = `
        <span class="empty-icon">📋</span>
        <h2>No Lists Available</h2>
        <p>No movie lists are currently available. Please try again later.</p>
        <p class="text-sm text-gray">Letterboxd lists may be temporarily rate-limited.</p>
      `;
      emptyState.classList.remove('hidden');
    }
  } catch (err) {
    console.error('[lists] failed to load:', err.message);
    cardStack.classList.add('hidden');
    emptyState.innerHTML = `
      <span class="empty-icon">⚠️</span>
      <h2>Error Loading Lists</h2>
      <p>${err.message || 'Please check your connection and try again.'}</p>
      <button onclick="loadLists()" class="btn-secondary">Retry</button>
    `;
    emptyState.classList.remove('hidden');
  }

  if (!state.selectedListId && state.lists.length > 0) {
    state.selectedListId = state.lists[0].list_id;
    state.selectedListTitle = state.lists[0].title;
    currentProfileSpan.textContent = state.selectedListTitle;
    loadDeck();
  }
}

function renderLists() {
  profileOptions.innerHTML = state.lists.map(item => `
    <div class="profile-option ${item.list_id === state.selectedListId ? 'active' : ''}" data-list-id="${esc(item.list_id)}">
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
      console.log('[lists] switched to:', state.selectedListId);
      loadDeck();
    });
  });

  currentProfileSpan.textContent = state.selectedListTitle;
}

async function loadDeck() {
  if (!state.username) {
    console.warn('[deck] loadDeck called with no username, aborting');
    return;
  }
  if (state.isSyncing) {
    console.log('[deck] already syncing, skipping duplicate loadDeck');
    return;
  }
  if (!state.selectedListId) {
    console.warn('[deck] loadDeck called with no selected list, aborting');
    return;
  }

  console.log('[deck] starting load for list:', state.selectedListId);
  cardStack.classList.add('hidden');
  emptyState.classList.add('hidden');
  loadingSkeleton.classList.remove('hidden');

  // First sync: show blocking overlay until complete, then load deck
  // Subsequent syncs: non-blocking badge
  const isFirstSync = !localStorage.getItem('swiperboxd_initial_sync_done');
  if (isFirstSync && !state.syncComplete) {
    console.log('[deck] first sync — showing blocking overlay');
    syncOverlay.classList.remove('hidden');
    $('#sync-overlay-text').textContent = 'Syncing your Letterboxd data...';
    $('#sync-overlay-detail').textContent = 'This may take a moment on first login';
    await _startIngestBackground();
    state.syncComplete = true;
    localStorage.setItem('swiperboxd_initial_sync_done', '1');
    syncOverlay.classList.add('hidden');
    console.log('[deck] initial sync done — loading deck');
  } else {
    state.syncComplete = true;
    _startIngestBackground(); // non-blocking
  }

  try {
    console.log('[deck] fetching list deck from DB...');
    const res = await api(`/lists/${encodeURIComponent(state.selectedListId)}/deck?user_id=${encodeURIComponent(state.username)}`);
    const raw = res.results || [];
    state.deck = raw.filter(m => !suppression.isSuppressed(m.slug));
    state.currentIndex = 0;
    console.log(`[deck] received ${raw.length} films, ${state.deck.length} after suppression filter`);

    loadingSkeleton.classList.add('hidden');
    if (state.deck.length > 0) {
      renderDeck();
    } else {
      console.warn('[deck] deck is empty after filtering');
      emptyState.classList.remove('hidden');
    }
  } catch (err) {
    console.error('[deck] load failed:', err.message);
    loadingSkeleton.classList.add('hidden');
    emptyState.classList.remove('hidden');
  }
}

async function _startIngestBackground() {
  // Cancel any previous poll loop still running
  _ingestPollCancelled = true;
  await delay(0); // yield so any awaiting loop iteration can check the flag
  _ingestPollCancelled = false;

  let startRes;
  try {
    showSyncBadge(0);
    startRes = await api('/ingest/start', {
      method: 'POST',
      body: { user_id: state.username, source: 'trending', depth_pages: 2 }
    });
    console.log(`[ingest] start → ${startRes.status}`);
    if (startRes.sync_stats) {
      const s = startRes.sync_stats;
      console.log(`[ingest] sync_stats: watchlist=${s.watchlist_count} diary=${s.diary_count} errors=${(s.errors||[]).length}`);
      if (s.errors && s.errors.length > 0) {
        s.errors.forEach(e => console.warn(`[ingest] sync error: ${e}`));
      }
    }
  } catch (err) {
    console.warn('[ingest] start failed (non-blocking):', err.message);
    hideSyncBadge();
    return;
  }

  // Vercel: endpoint ran the sync inline and returned "completed" — no polling needed
  if (startRes.status === 'completed') {
    const s = startRes.sync_stats || {};
    console.log(`[ingest] sync completed inline (Vercel) — watchlist=${s.watchlist_count||0} diary=${s.diary_count||0}`);
    if (s.watchlist_count === 0 && s.diary_count === 0) {
      console.warn('[ingest] WARNING: both watchlist and diary are empty — use the Chrome extension to sync, or check Vercel function logs');
    }
    const detail = $('#sync-overlay-detail');
    if (detail) detail.textContent = `Watchlist: ${s.watchlist_count||0} films, Diary: ${s.diary_count||0} films`;
    hideSyncBadge();
    return;
  }

  // Long-running server: poll until done
  await delay(300);

  while (!_ingestPollCancelled) {
    try {
      const res = await api(`/ingest/progress?user_id=${encodeURIComponent(state.username)}`);
      const { progress } = res;
      console.log(`[ingest] progress=${progress}%`);

      if (progress === 100) {
        hideSyncBadge();
        console.log('[ingest] sync complete');
        return;
      }
      if (progress === -1) {
        hideSyncBadge();
        console.warn('[ingest] sync failed on server');
        return;
      }
      showSyncBadge(progress);
    } catch (err) {
      console.warn('[ingest] poll error:', err.message);
      hideSyncBadge();
      return;
    }
    await delay(1000);
  }
}

function showSyncBadge(progress) {
  let badge = $('#sync-badge');
  if (!badge) {
    badge = document.createElement('div');
    badge.id = 'sync-badge';
    badge.className = 'sync-badge';
    document.body.appendChild(badge);
  }
  const label = (progress > 0 && progress < 100) ? `Syncing… ${progress}%` : 'Syncing watchlist…';
  badge.textContent = label;
  badge.classList.remove('hidden');
}

function hideSyncBadge() {
  const badge = $('#sync-badge');
  if (!badge) return;
  badge.classList.add('hidden');
  setTimeout(() => badge.remove(), 450);
}

function renderDeck() {
  cardStack.classList.remove('hidden');
  cardStack.innerHTML = '';

  const cardsToShow = state.deck.slice(0, 3);
  cardsToShow.reverse().forEach((movie, i) => {
    const isTop = i === cardsToShow.length - 1;
    const card = createCard(movie, isTop);
    card.style.zIndex = cardsToShow.length - i;
    card.style.transform = `scale(${1 - (cardsToShow.length - 1 - i) * 0.05}) translateY(${(cardsToShow.length - 1 - i) * 10}px)`;
    cardStack.appendChild(card);
  });

  console.log(`[deck] rendered ${cardsToShow.length} card(s), ${state.deck.length} total in deck`);
}

function createCard(movie, isTop = false) {
  const card = document.createElement('div');
  card.className = 'movie-card';
  card.dataset.slug = movie.slug;

  card.innerHTML = `
    <img class="card-poster" src="${esc(movie.poster_url)}" alt="${esc(movie.title)}" />
    <div class="card-overlay">
      <h2 class="card-title">${esc(movie.title)}</h2>
      <p class="card-meta">★ ${esc(movie.rating)} · ${esc(movie.popularity)} popularity</p>
    </div>
    <div class="card-back">
      <h2 class="card-title">${esc(movie.title)}</h2>
      <p class="card-meta">★ ${esc(movie.rating)} · ${esc(movie.popularity)} popularity</p>
      <div class="card-genres">
        ${(movie.genres || []).slice(0, 3).map(g => `<span class="genre-tag">${esc(g)}</span>`).join('')}
      </div>
      <p class="card-synopsis">${esc(movie.synopsis) || 'No synopsis available.'}</p>
    </div>
    <div class="swipe-indicator watchlist">WATCHLIST</div>
    <div class="swipe-indicator dismiss">SKIP</div>
    <div class="swipe-indicator log">LOGGED</div>
  `;

  if (isTop) {
    let _cardDragged = false;
    card.addEventListener('click', (e) => {
      if (_cardDragged || e.target.closest('.action-btn')) return;
      flipCard();
    });
    initCardTouch(card, (wasDrag) => { _cardDragged = wasDrag; });
  }

  return card;
}

function initCardTouch(card, onDragState) {
  let startX = 0, startY = 0, currentX = 0, currentY = 0, dragging = false;
  const threshold = 100;
  const dragDeadzone = 5;

  function onStart(x, y) {
    startX = x;
    startY = y;
    currentX = 0;
    currentY = 0;
    dragging = true;
    if (onDragState) onDragState(false);
    card.style.transition = 'none';
  }

  function onMove(x, y) {
    if (!dragging) return;
    currentX = x - startX;
    currentY = y - startY;
    if (Math.abs(currentX) > dragDeadzone || Math.abs(currentY) > dragDeadzone) {
      if (onDragState) onDragState(true);
    }
    card.style.transform = `translate(${currentX}px, ${currentY}px) rotate(${currentX * 0.1}deg)`;
    // Show swipe indicators
    const watchEl = card.querySelector('.swipe-indicator.watchlist');
    const dismissEl = card.querySelector('.swipe-indicator.dismiss');
    const logEl = card.querySelector('.swipe-indicator.log');
    if (watchEl) watchEl.style.opacity = currentX > 30 ? Math.min((currentX - 30) / 70, 1) : 0;
    if (dismissEl) dismissEl.style.opacity = currentX < -30 ? Math.min((-currentX - 30) / 70, 1) : 0;
    if (logEl) logEl.style.opacity = currentY < -30 ? Math.min((-currentY - 30) / 70, 1) : 0;
  }

  function onEnd() {
    if (!dragging) return;
    dragging = false;
    card.style.transition = '';
    const watchEl = card.querySelector('.swipe-indicator.watchlist');
    const dismissEl = card.querySelector('.swipe-indicator.dismiss');
    const logEl = card.querySelector('.swipe-indicator.log');
    if (watchEl) watchEl.style.opacity = 0;
    if (dismissEl) dismissEl.style.opacity = 0;
    if (logEl) logEl.style.opacity = 0;
    if (currentX > threshold) executeSwipe('watchlist');
    else if (currentX < -threshold) executeSwipe('dismiss');
    else if (currentY < -threshold) executeSwipe('log');
    else card.style.transform = '';
    currentX = 0;
    currentY = 0;
  }

  // Touch
  card.addEventListener('touchstart', (e) => onStart(e.touches[0].clientX, e.touches[0].clientY), { passive: true });
  card.addEventListener('touchmove', (e) => onMove(e.touches[0].clientX, e.touches[0].clientY), { passive: true });
  card.addEventListener('touchend', onEnd);

  // Mouse (desktop drag)
  card.addEventListener('mousedown', (e) => {
    e.preventDefault();
    onStart(e.clientX, e.clientY);
    function onMouseMove(ev) { onMove(ev.clientX, ev.clientY); }
    function onMouseUp() {
      onEnd();
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    }
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });
}

function initTouchSwipe() {
  // Global touch fallbacks handled per-card in initCardTouch
}

function flipCard() {
  if (state.deck.length === 0) return;
  const topCard = cardStack.querySelector('.movie-card:last-child');
  if (topCard) {
    topCard.classList.toggle('flipped');
    state.flipped = !state.flipped;
  }
}

async function executeSwipe(action) {
  if (state.isSyncing || state.deck.length === 0) return;
  if (!state.syncComplete) {
    console.warn('[swipe] blocked — initial sync not complete yet');
    return;
  }

  state.isSyncing = true;
  const topCard = cardStack.querySelector('.movie-card:last-child');
  const movie = state.deck[state.currentIndex];

  console.log(`[swipe] action=${action} slug=${movie.slug} remaining=${state.deck.length - state.currentIndex - 1}`);

  topCard.classList.add(`swiping-${action === 'watchlist' ? 'right' : action === 'dismiss' ? 'left' : 'up'}`);

  if (navigator.vibrate) {
    navigator.vibrate(action === 'watchlist' ? 50 : action === 'dismiss' ? 10 : 30);
  }

  syncOverlay.classList.remove('hidden');

  let advanceCard = true;
  try {
    await api('/actions/swipe', {
      method: 'POST',
      body: { user_id: state.username, movie_slug: movie.slug, action }
    });

    if (action === 'dismiss') {
      suppression.dismiss(movie.slug);
      console.log(`[suppression] ${movie.slug} suppressed for 24h`);
    }
  } catch (err) {
    // 409 = duplicate (already in watchlist / diary). Still advance, show toast.
    if (err.status === 409) {
      const code = err.code || err.data?.code;
      if (code === 'already_in_watchlist') showToast('Already in your watchlist');
      else if (code === 'already_in_diary') showToast('Already in your diary');
      else showToast('Already saved');
      console.log(`[swipe] 409 duplicate — advancing anyway code=${code}`);
    } else {
      console.error('[swipe] failed:', err.message);
      advanceCard = false;
      topCard.classList.remove('swiping-right', 'swiping-left', 'swiping-up');
      topCard.style.transform = '';
    }
  }

  try {
    if (advanceCard) {
      state.currentIndex++;
      state.flipped = false;

      await delay(400);
      topCard.remove();

      if (state.currentIndex < state.deck.length) {
        const nextCard = createCard(state.deck[state.currentIndex], true);
        nextCard.style.transform = 'scale(0.95) translateY(10px)';
        cardStack.appendChild(nextCard);
        await delay(10);
        nextCard.style.transition = 'transform 0.2s ease-out';
        nextCard.style.transform = '';
      }

      if (state.currentIndex >= state.deck.length) {
        console.log('[deck] all cards exhausted');
        cardStack.classList.add('hidden');
        emptyState.classList.remove('hidden');
      }
    }
  } finally {
    syncOverlay.classList.add('hidden');
    state.isSyncing = false;
  }
}

// ==================== API ====================

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.encryptedSession) {
    headers['X-Session-Token'] = state.encryptedSession;
  }
  const res = await fetch(path, {
    method: options.method || 'GET',
    headers,
    ...(options.body ? { body: JSON.stringify(options.body) } : {})
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data?.detail;
    const msg = (typeof detail === 'string' ? detail : detail?.reason || detail?.code) || `HTTP ${res.status}`;
    console.error(`[api] ${options.method || 'GET'} ${path} → ${res.status}:`, msg);
    const err = new Error(msg);
    err.status = res.status;
    err.code = typeof detail === 'object' ? detail?.code : (data?.code || null);
    err.data = data;
    throw err;
  }

  return res.json();
}

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
