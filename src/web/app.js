/**
 * Swiperboxd - Movie Discovery App
 * Uses Letterboxd authentication (username/password)
 */

import { createSuppressionStore } from './state.js';

const suppression = createSuppressionStore(() => Date.now());

// State
const state = {
  username: null,
  encryptedSession: null,
  deck: [],
  currentIndex: 0,
  isSyncing: false,
  flipped: false,
  profiles: [],
  currentProfile: 'gold-standard'
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
    const password = $('#letterboxd-password').value;

    if (!username || !password) return;

    console.log('[auth] attempting login for user:', username);
    try {
      const res = await api('/auth/session', {
        method: 'POST',
        body: { username, password }
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
  // loadProfiles resolves first, then loadDeck auto-starts
  loadProfiles();
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

async function loadProfiles() {
  console.log('[profiles] fetching available profiles');
  try {
    const res = await api('/discovery/profiles');
    state.profiles = res.profiles;
    console.log('[profiles] loaded:', state.profiles);
    renderProfiles();
  } catch (err) {
    console.error('[profiles] failed to load:', err.message);
    // Still attempt deck load even if profile fetch fails
  }
  // Always auto-start deck load after profiles resolve (success or failure)
  loadDeck();
}

function renderProfiles() {
  profileOptions.innerHTML = state.profiles.map(p => `
    <div class="profile-option ${p === state.currentProfile ? 'active' : ''}" data-profile="${p}">
      ${formatProfileName(p)}
    </div>
  `).join('');

  profileOptions.querySelectorAll('.profile-option').forEach(opt => {
    opt.addEventListener('click', () => {
      state.currentProfile = opt.dataset.profile;
      currentProfileSpan.textContent = formatProfileName(state.currentProfile);
      profileDropdown.classList.add('hidden');
      $$('.profile-option').forEach(p => p.classList.remove('active'));
      opt.classList.add('active');
      console.log('[profiles] switched to:', state.currentProfile);
      loadDeck();
    });
  });

  currentProfileSpan.textContent = formatProfileName(state.currentProfile);
}

function formatProfileName(name) {
  return name.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
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

  console.log('[deck] starting load for profile:', state.currentProfile);
  cardStack.classList.add('hidden');
  emptyState.classList.add('hidden');
  loadingSkeleton.classList.add('hidden');

  try {
    console.log('[ingest] starting ingest...');
    await api('/ingest/start', {
      method: 'POST',
      body: { user_id: state.username, source: 'trending', depth_pages: 2 }
    });

    await pollProgress();

    console.log('[deck] fetching discovery deck...');
    const res = await api(`/discovery/deck?user_id=${encodeURIComponent(state.username)}&profile=${encodeURIComponent(state.currentProfile)}`);

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

  while (true) {
    try {
      const res = await api(`/ingest/progress?user_id=${encodeURIComponent(state.username)}`);
      const progress = res.progress;

      updateProgressBar(progress);
      console.log(`[ingest] progress=${progress}%`);

      if (progress >= 100) {
        console.log('[ingest] complete');
        break;
      }
      if (progress === -1) {
        console.error('[ingest] server reported ingest failure');
        break;
      }

      await delay(500);
    } catch (err) {
      console.error('[ingest] progress poll error:', err.message);
      break;
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
    </div>
  `;
  document.body.appendChild(progressOverlay);
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
  if (fill && percent) {
    const pct = Math.min(100, Math.max(0, progress));
    fill.style.width = `${pct}%`;
    percent.textContent = `${pct}%`;
  }
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
    <img class="card-poster" src="${movie.poster_url}" alt="${movie.title}" />
    <div class="card-overlay">
      <h2 class="card-title">${movie.title}</h2>
      <p class="card-meta">★ ${movie.rating} · ${movie.popularity} popularity</p>
    </div>
    <div class="card-back">
      <h2 class="card-title">${movie.title}</h2>
      <p class="card-meta">★ ${movie.rating} · ${movie.popularity} popularity</p>
      <div class="card-genres">
        ${(movie.genres || []).slice(0, 3).map(g => `<span class="genre-tag">${g}</span>`).join('')}
      </div>
      <p class="card-synopsis">${movie.synopsis || 'No synopsis available.'}</p>
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
