/**
 * CineSwipe - Movie Discovery App
 * Tinder-style swipeable movie cards
 */

// State
const state = {
  user: null,
  token: null,
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
const authTabs = $$('.auth-tab');
const loginForm = $('#login-form');
const registerForm = $('#register-form');
const authError = $('#auth-error');
const authSuccess = $('#auth-success');

// Discovery Elements
const cardStack = $('#card-stack');
const loadingSkeleton = $('#loading-skeleton');
const emptyState = $('#empty-state');
const syncOverlay = $('#sync-overlay');
const profileOptions = $('#profile-options');
const profileDropdown = $('#profile-dropdown');
const currentProfileSpan = $('#current-profile');
const userEmailSpan = $('#user-email');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  initAuth();
  initDiscovery();
  checkAuth();
});

// ==================== AUTH ====================

function initAuth() {
  // Tab switching
  authTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      authTabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      
      if (tab.dataset.tab === 'login') {
        loginForm.classList.remove('hidden');
        registerForm.classList.add('hidden');
      } else {
        loginForm.classList.add('hidden');
        registerForm.classList.remove('hidden');
      }
      hideAuthMessages();
    });
  });

  // Login form
  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = $('#login-email').value;
    const password = $('#login-password').value;
    
    try {
      const res = await api('/auth/login', {
        method: 'POST',
        body: { email, password }
      });
      
      setAuth(res);
      showSuccess('Login successful!');
      setTimeout(() => showDiscovery(), 1000);
    } catch (err) {
      showError(err.message);
    }
  });

  // Register form
  registerForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = $('#register-email').value;
    const password = $('#register-password').value;
    
    try {
      const res = await api('/auth/register', {
        method: 'POST',
        body: { email, password }
      });
      
      setAuth(res);
      showSuccess('Account created!');
      setTimeout(() => showDiscovery(), 1000);
    } catch (err) {
      showError(err.message);
    }
  });

  // Logout
  $('#logout-btn')?.addEventListener('click', () => {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('user_id');
    localStorage.removeItem('user_email');
    state.user = null;
    state.token = null;
    showAuth();
  });
}

function checkAuth() {
  const token = localStorage.getItem('auth_token');
  const userId = localStorage.getItem('user_id');
  const email = localStorage.getItem('user_email');
  
  if (token && userId) {
    state.token = token;
    state.user = { user_id: userId, email };
    showDiscovery();
  } else {
    showAuth();
  }
}

function setAuth(data) {
  state.token = data.access_token;
  state.user = { user_id: data.user_id, email: data.email };
  
  localStorage.setItem('auth_token', data.access_token);
  localStorage.setItem('user_id', data.user_id);
  localStorage.setItem('user_email', data.email);
}

function showAuth() {
  authScreen.classList.add('active');
  discoveryScreen.classList.remove('active');
}

function showDiscovery() {
  authScreen.classList.remove('active');
  discoveryScreen.classList.add('active');
  userEmailSpan.textContent = state.user?.email || '';
  loadProfiles();
}

function showError(msg) {
  authError.textContent = msg;
  authError.classList.remove('hidden');
  authSuccess.classList.add('hidden');
}

function showSuccess(msg) {
  authSuccess.textContent = msg;
  authSuccess.classList.remove('hidden');
  authError.classList.add('hidden');
}

function hideAuthMessages() {
  authError.classList.add('hidden');
  authSuccess.classList.add('hidden');
}

// ==================== DISCOVERY ====================

function initDiscovery() {
  // Profile selector
  $('#profile-btn')?.addEventListener('click', () => {
    profileDropdown.classList.toggle('hidden');
  });

  // Action buttons
  $$('.action-btn[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (state.isSyncing || state.deck.length === 0) return;
      const action = btn.dataset.action;
      executeSwipe(action);
    });
  });

  // Flip button
  $('#flip-btn')?.addEventListener('click', () => {
    flipCard();
  });

  // Refresh button
  $('#refresh-btn')?.addEventListener('click', () => {
    loadDeck();
  });

  // Close dropdown on outside click
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.profile-selector')) {
      profileDropdown.classList.add('hidden');
    }
  });

  // Keyboard controls
  document.addEventListener('keydown', (e) => {
    if (discoveryScreen.classList.contains('active') && state.deck.length > 0) {
      switch(e.key) {
        case 'ArrowLeft':
        case 'a':
          executeSwipe('dismiss');
          break;
        case 'ArrowRight':
        case 'd':
          executeSwipe('watchlist');
          break;
        case 'ArrowUp':
        case 'w':
          executeSwipe('log');
          break;
        case ' ':
          e.preventDefault();
          flipCard();
          break;
      }
    }
  });

  // Touch swipe support
  initTouchSwipe();
}

async function loadProfiles() {
  try {
    const res = await api('/discovery/profiles');
    state.profiles = res.profiles;
    renderProfiles();
  } catch (err) {
    console.error('Failed to load profiles:', err);
  }
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
      loadDeck();
    });
  });

  currentProfileSpan.textContent = formatProfileName(state.currentProfile);
}

function formatProfileName(name) {
  return name.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

async function loadDeck() {
  if (!state.user) return;

  cardStack.classList.add('hidden');
  emptyState.classList.add('hidden');
  loadingSkeleton.classList.remove('hidden');

  try {
    // Start ingest
    await api('/ingest/start', {
      method: 'POST',
      body: { user_id: state.user.user_id, source: 'trending', depth_pages: 2 }
    });

    // Wait a bit for ingest
    await delay(500);

    // Get deck
    const res = await api(`/discovery/deck?user_id=${encodeURIComponent(state.user.user_id)}&profile=${encodeURIComponent(state.currentProfile)}`);
    
    state.deck = res.results || [];
    state.currentIndex = 0;
    
    loadingSkeleton.classList.add('hidden');
    
    if (state.deck.length > 0) {
      renderDeck();
    } else {
      emptyState.classList.remove('hidden');
    }
  } catch (err) {
    console.error('Failed to load deck:', err);
    loadingSkeleton.classList.add('hidden');
    emptyState.classList.remove('hidden');
  }
}

function renderDeck() {
  cardStack.classList.remove('hidden');
  cardStack.innerHTML = '';
  
  // Show up to 3 cards for stack effect
  const cardsToShow = state.deck.slice(0, 3);
  
  cardsToShow.reverse().forEach((movie, i) => {
    const isTop = i === cardsToShow.length - 1;
    const card = createCard(movie, isTop);
    card.style.zIndex = cardsToShow.length - i;
    card.style.transform = `scale(${1 - (cardsToShow.length - 1 - i) * 0.05}) translateY(${(cardsToShow.length - 1 - i) * 10}px)`;
    cardStack.appendChild(card);
  });
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
      if (!e.target.closest('.action-btn')) {
        flipCard();
      }
    });

    // Touch events
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
    
    const rotation = currentX * 0.1;
    card.style.transform = `translate(${currentX}px, ${currentY}px) rotate(${rotation}deg)`;
  }, { passive: true });

  card.addEventListener('touchend', () => {
    if (currentX > threshold) {
      executeSwipe('watchlist');
    } else if (currentX < -threshold) {
      executeSwipe('dismiss');
    } else if (currentY < -threshold) {
      executeSwipe('log');
    } else {
      card.style.transform = '';
    }
    currentX = 0;
    currentY = 0;
  });
}

function initTouchSwipe() {
  // Global touch handler for keyboard fallbacks
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
  
  // Animate card
  topCard.classList.add(`swiping-${action === 'watchlist' ? 'right' : action === 'dismiss' ? 'left' : 'up'}`);
  
  // Haptic feedback
  if (navigator.vibrate) {
    navigator.vibrate(action === 'watchlist' ? 50 : action === 'dismiss' ? 10 : 30);
  }

  // Show sync overlay
  syncOverlay.classList.remove('hidden');

  try {
    // Send to backend
    await api('/actions/swipe', {
      method: 'POST',
      body: { 
        user_id: state.user.user_id, 
        movie_slug: movie.slug, 
        action 
      }
    });

    // Move to next card
    state.currentIndex++;
    state.flipped = false;

    await delay(400);

    // Remove card and show next
    topCard.remove();
    
    if (state.currentIndex < state.deck.length) {
      const nextCard = createCard(state.deck[state.currentIndex], true);
      nextCard.style.transform = 'scale(0.95) translateY(10px)';
      cardStack.appendChild(nextCard);
      
      // Animate in
      await delay(10);
      nextCard.style.transition = 'transform 0.2s ease-out';
      nextCard.style.transform = '';
    }

    // Check if deck is empty
    if (state.currentIndex >= state.deck.length) {
      cardStack.classList.add('hidden');
      emptyState.classList.remove('hidden');
    }
  } catch (err) {
    console.error('Swipe failed:', err);
    // Reset card position
    topCard.classList.remove('swiping-right', 'swiping-left', 'swiping-up');
    topCard.style.transform = '';
  } finally {
    syncOverlay.classList.add('hidden');
    state.isSyncing = false;
  }
}

// ==================== API ====================

async function api(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(state.token ? { 'Authorization': `Bearer ${state.token}` } : {})
  };

  const res = await fetch(path, {
    method: options.method || 'GET',
    headers,
    ...(options.body ? { body: JSON.stringify(options.body) } : {})
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const msg = data?.detail?.reason || data?.detail?.code || `HTTP ${res.status}`;
    throw new Error(msg);
  }

  return res.json();
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
