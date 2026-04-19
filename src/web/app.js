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

// ==================== STATE ====================

const state = {
  username: null,
  encryptedSession: null,
  deck: [],
  currentIndex: 0,
  isSyncing: false,
  lists: [],
  selectedListId: null,
  selectedListTitle: 'Choose a List',
  listSearchQuery: '',
};

function requestExtensionSwipe(action, movieSlug, timeoutMs = 8000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      window.removeEventListener('message', handler);
      resolve({ lbSynced: false, error: 'extension timed out — is it installed and signed in to Letterboxd?' });
    }, timeoutMs);
    function handler(event) {
      if (event.source !== window) return;
      const d = event.data;
      if (d?.type === 'SWIPERBOXD_SWIPE_RESULT' && d.movieSlug === movieSlug && d.action === action) {
        clearTimeout(timer);
        window.removeEventListener('message', handler);
        resolve({ lbSynced: d.lbSynced, error: d.error });
      }
    }
    window.addEventListener('message', handler);
    window.postMessage({ type: 'SWIPERBOXD_SWIPE', action, movieSlug }, window.location.origin);
  });
}

// ==================== DOM REFS ====================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const setupScreen     = $('#setup-screen');
const authScreen      = $('#auth-screen');
const discoveryScreen = $('#discovery-screen');
const loginForm       = $('#letterboxd-login-form');
const errorDiv        = $('#auth-error');
const successDiv      = $('#auth-success');
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
  $('#setup-continue-btn')?.addEventListener('click', () => {
    setupScreen.classList.remove('active');
    authScreen.classList.add('active');
  });

  $('#back-to-setup-btn')?.addEventListener('click', () => {
    authScreen.classList.remove('active');
    setupScreen.classList.add('active');
  });

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#letterboxd-username').value.trim();
    const sessionCookie = $('#letterboxd-session-cookie').value.trim();
    if (!username || !sessionCookie) return;

    try {
      const res = await api('/auth/session', {
        method: 'POST',
        body: { username, session_cookie: sessionCookie },
      });
      state.username = username;
      state.encryptedSession = res.encrypted_session_cookie;
      localStorage.setItem('swiperboxd_username', username);
      localStorage.setItem('swiperboxd_token', res.encrypted_session_cookie);
      broadcastAuthToExtension();
      showSuccess('Connected!');
      setTimeout(() => showDiscovery(), 800);
    } catch (err) {
      showError(err.message || 'Failed to connect. Check your credentials.');
    }
  });

  $('#logout-btn')?.addEventListener('click', () => {
    localStorage.removeItem('swiperboxd_username');
    localStorage.removeItem('swiperboxd_token');
    state.username = null;
    state.encryptedSession = null;
    showAuth();
  });
}

function checkSavedSession() {
  const username = localStorage.getItem('swiperboxd_username');
  const session  = localStorage.getItem('swiperboxd_token');
  if (username && session) {
    state.username = username;
    state.encryptedSession = session;
    broadcastAuthToExtension();
    showDiscovery();
  } else {
    // No saved session — show setup guide first
    setupScreen.classList.add('active');
    authScreen.classList.remove('active');
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
  setupScreen.classList.remove('active');
  discoveryScreen.classList.remove('active');
  authScreen.classList.add('active');
}

function showDiscovery() {
  setupScreen.classList.remove('active');
  authScreen.classList.remove('active');
  discoveryScreen.classList.add('active');
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
    state.selectedListId = state.lists[0].list_id;
    state.selectedListTitle = state.lists[0].title;
    currentProfileSpan.textContent = state.selectedListTitle;
    loadDeck();
  }
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
    state.deck = raw.filter(m => !suppression.isSuppressed(m.slug));
    state.currentIndex = 0;

    loadingSkeleton.classList.add('hidden');

    if (state.deck.length > 0) {
      renderDeck();
    } else {
      setEmptyState(
        '🎬',
        'No movies to show',
        'Open the Chrome extension popup and click Start Sync to load your Letterboxd data into Swiperboxd.',
      );
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
  card.innerHTML = `
    <img class="card-poster" src="${esc(movie.poster_url)}" alt="${esc(movie.title)}" />
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
