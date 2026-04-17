/**
 * Swiperboxd - Movie Discovery App
 * Uses Letterboxd authentication (username/password)
 */

import { createSuppressionStore, getIngestPollingState } from './state.js';

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
  flipped: false,
  lists: [],
  selectedListId: null,
  selectedListTitle: 'Choose a List',
  listSearchQuery: ''
};

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
    localStorage.removeItem('swiperboxd_username');
    localStorage.removeItem('swiperboxd_token');
    state.username = null;
    state.encryptedSession = null;
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
    showDiscovery();
  } else {
    console.log('[session] no saved session, showing auth screen');
    showAuth();
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

  $$('.action-btn[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (state.isSyncing || state.deck.length === 0) return;
      executeSwipe(btn.dataset.action);
    });
  });

  $('#flip-btn')?.addEventListener('click', () => flipCard());

  $('#refresh-btn')?.addEventListener('click', () => {
    console.log('[deck] manual refresh triggered');
    loadDeck();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.profile-selector')) {
      profileDropdown.classList.add('hidden');
    }
  });

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
  // Add refresh button at top
  const refreshSection = `
    <div class="list-refresh-section">
      <button id="refresh-lists-btn" class="btn-text refresh-btn">
        <span class="refresh-icon">↻</span>
        Refresh Lists from Letterboxd
      </button>
      <p class="refresh-hint">Updates automatically every 24 hours</p>
    </div>`;
  
  profileOptions.innerHTML = refreshSection + state.lists.map(item => `
    <div class="profile-option ${item.list_id === state.selectedListId ? 'active' : ''}" data-list-id="${esc(item.list_id)}">
      <div class="list-option-title">${esc(item.title)}</div>
      <div class="list-option-meta">${esc(item.owner_name)} · ${esc(item.film_count)} films</div>
    </div>
  `).join('');

  // Add refresh button click handler
  document.getElementById('refresh-lists-btn')?.addEventListener('click', async () => {
    const btn = document.getElementById('refresh-lists-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="refresh-icon spinning">↻</span> Refreshing...';
    
    try {
      const res = await api('/lists/refresh', { method: 'POST' });
      console.log('[lists] refresh result:', res);
      
      await loadLists();
      
      btn.innerHTML = '<span class="refresh-icon">✓</span> Refreshed!';
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = '<span class="refresh-icon">↻</span> Refresh Lists from Letterboxd';
      }, 2000);
    } catch (err) {
      console.error('[lists] refresh failed:', err);
      btn.innerHTML = '<span class="refresh-icon">✗</span> Try Again';
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = '<span class="refresh-icon">↻</span> Refresh Lists from Letterboxd';
      }, 2000);
      
      if (err.message.includes('rate_limited')) {
        alert('Letterboxd is rate limiting requests. Lists will be updated automatically every 24 hours.');
      }
    }
  });

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
  loadingSkeleton.classList.add('hidden');

  try {
    console.log('[ingest] starting ingest...');
    await api('/ingest/start', {
      method: 'POST',
      body: { user_id: state.username, source: 'trending', depth_pages: 2 }
    });

    const ingestResult = await pollProgress();
    if (ingestResult.status !== 'completed') {
      const reason = ingestResult.reason || 'ingest_failed';
      console.error('[deck] aborting deck fetch due to ingest status:', ingestResult.status, reason);
      throw new Error(reason === 'server_reported_failure'
        ? 'Letterboxd sync failed before deck load.'
        : 'Unable to complete ingest before deck load.');
    }

    console.log('[deck] fetching list deck...');
    const res = await api(`/lists/${encodeURIComponent(state.selectedListId)}/deck?user_id=${encodeURIComponent(state.username)}`);

    const raw = res.results || [];
    state.deck = raw.filter(m => !suppression.isSuppressed(m.slug));
    state.currentIndex = 0;

    console.log(`[deck] received ${raw.length} films, ${state.deck.length} after suppression filter`);
    hideProgress();

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
    hideProgress();
  }
}

async function pollProgress() {
  // Remove any existing overlay before creating a new one
  const existing = $('#ingest-progress-overlay');
  if (existing) existing.remove();

  showProgress('Loading your Letterboxd data...');
  console.log('[ingest] polling progress...');

  // Give the background thread a moment to start before the first poll,
  // so we don't read the initial 0 or a stale value set just before thread start.
  await delay(300);

  while (true) {
    try {
      const res = await api(`/ingest/progress?user_id=${encodeURIComponent(state.username)}`);
      const progress = res.progress;
      const ingestState = getIngestPollingState(progress);

      updateProgressBar(progress);
      console.log(`[ingest] progress=${progress}%`);

      if (ingestState.status === 'completed') {
        console.log('[ingest] complete');
        return ingestState;
      }
      if (ingestState.status === 'failed') {
        console.error('[ingest] server reported ingest failure');
        return ingestState;
      }

      await delay(500);
    } catch (err) {
      console.error('[ingest] progress poll error:', err.message);
      return { status: 'interrupted', reason: err.message };
    }
  }
}

function showProgress(message) {
  const progressOverlay = document.createElement('div');
  progressOverlay.id = 'ingest-progress-overlay';
  progressOverlay.className = 'ingest-progress-container';
  progressOverlay.innerHTML = `
    <div class="ingest-progress">
      <div class="progress-logo">
        <img src="/web/logo.svg" alt="Swiperboxd" />
      </div>
      <h3>${message}</h3>
      <div class="progress-bar-container">
        <div class="progress-bar">
          <div class="progress-fill" style="width: 0%"></div>
        </div>
      </div>
      <p class="progress-percent">0%</p>
      <button id="progress-disconnect-btn" class="btn-text progress-disconnect">Disconnect</button>
    </div>
  `;
  document.body.appendChild(progressOverlay);

  document.getElementById('progress-disconnect-btn')?.addEventListener('click', () => {
    console.log('[auth] disconnect from loading screen');
    hideProgress();
    localStorage.removeItem('swiperboxd_username');
    localStorage.removeItem('swiperboxd_token');
    state.username = null;
    state.encryptedSession = null;
    state.isSyncing = false;
    showAuth();
  });
}

function hideProgress() {
  const overlay = $('#ingest-progress-overlay');
  if (overlay) {
    overlay.classList.add('hidden');
    setTimeout(() => overlay.remove(), 300);
  }
}

function updateProgressBar(progress) {
  const fill = $('.progress-fill');
  const percent = $('.progress-percent');
  if (!fill || !percent) return;
  if (progress === -1) {
    fill.style.width = '100%';
    fill.style.background = 'var(--accent-red, #e74c3c)';
    percent.textContent = 'Failed — check connection';
    return;
  }
  const pct = Math.min(100, Math.max(0, progress));
  fill.style.width = `${pct}%`;
  percent.textContent = `${pct}%`;
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
    card.addEventListener('click', (e) => {
      if (!e.target.closest('.action-btn')) flipCard();
    });
    initCardTouch(card);
  }

  return card;
}

function initCardTouch(card) {
  let startX = 0, startY = 0, currentX = 0, currentY = 0;
  const threshold = 100;

  card.addEventListener('touchstart', (e) => {
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
  }, { passive: true });

  card.addEventListener('touchmove', (e) => {
    currentX = e.touches[0].clientX - startX;
    currentY = e.touches[0].clientY - startY;
    card.style.transform = `translate(${currentX}px, ${currentY}px) rotate(${currentX * 0.1}deg)`;
  }, { passive: true });

  card.addEventListener('touchend', () => {
    if (currentX > threshold) executeSwipe('watchlist');
    else if (currentX < -threshold) executeSwipe('dismiss');
    else if (currentY < -threshold) executeSwipe('log');
    else card.style.transform = '';
    currentX = 0;
    currentY = 0;
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

  state.isSyncing = true;
  const topCard = cardStack.querySelector('.movie-card:last-child');
  const movie = state.deck[state.currentIndex];

  console.log(`[swipe] action=${action} slug=${movie.slug} remaining=${state.deck.length - state.currentIndex - 1}`);

  topCard.classList.add(`swiping-${action === 'watchlist' ? 'right' : action === 'dismiss' ? 'left' : 'up'}`);

  if (navigator.vibrate) {
    navigator.vibrate(action === 'watchlist' ? 50 : action === 'dismiss' ? 10 : 30);
  }

  syncOverlay.classList.remove('hidden');

  try {
    await api('/actions/swipe', {
      method: 'POST',
      body: { user_id: state.username, movie_slug: movie.slug, action }
    });

    if (action === 'dismiss') {
      suppression.dismiss(movie.slug);
      console.log(`[suppression] ${movie.slug} suppressed for 24h`);
    }

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
  } catch (err) {
    console.error('[swipe] failed:', err.message);
    topCard.classList.remove('swiping-right', 'swiping-left', 'swiping-up');
    topCard.style.transform = '';
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
    throw new Error(msg);
  }

  return res.json();
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
