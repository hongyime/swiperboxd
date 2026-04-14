import { createProgressState, createSuppressionStore } from './state.js';

const progressState = createProgressState();
const suppressionStore = createSuppressionStore();
let deck = [];
let isSyncing = false;
let flipped = false;

const userIdInput = document.getElementById('userId');
const profileSelect = document.getElementById('profile');
const loadDeckBtn = document.getElementById('loadDeck');
const progressEl = document.getElementById('progress');
const card = document.getElementById('card');
const poster = document.getElementById('poster');
const title = document.getElementById('title');
const meta = document.getElementById('meta');
const detailBox = document.getElementById('details');
const msg = document.getElementById('message');

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body?.detail?.code || `HTTP ${response.status}`);
  }
  return response.json();
}

function haptic(pattern = 10) {
  if (navigator.vibrate) navigator.vibrate(pattern);
}

function renderCard() {
  const current = deck[0];
  flipped = false;
  card.classList.remove('flipped');
  detailBox.innerHTML = '';

  if (!current) {
    card.classList.add('hidden');
    msg.textContent = 'No more results for this profile.';
    return;
  }

  card.classList.remove('hidden');
  poster.src = current.poster_url;
  poster.style.objectFit = 'cover';
  title.textContent = current.title;
  meta.textContent = `Rating ${current.rating} · Popularity ${current.popularity}`;
}

async function loadProfiles() {
  const payload = await request('/discovery/profiles');
  profileSelect.innerHTML = payload.profiles.map((p) => `<option value="${p}">${p}</option>`).join('');
}

async function refreshProgress() {
  const userId = userIdInput.value.trim();
  if (!userId) return;

  const payload = await request(`/ingest/progress?user_id=${encodeURIComponent(userId)}`);
  progressEl.textContent = progressState.update(payload.progress);
}

async function loadDeck() {
  const userId = userIdInput.value.trim();
  const profile = profileSelect.value;
  if (!userId || !profile) return;

  document.getElementById('skeleton').classList.remove('hidden');
  await request('/ingest/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, source: 'trending', depth_pages: 2 })
  });

  await new Promise((r) => setTimeout(r, 300));
  const payload = await request(`/discovery/deck?user_id=${encodeURIComponent(userId)}&profile=${encodeURIComponent(profile)}`);
  deck = payload.results.filter((item) => !suppressionStore.isSuppressed(item.slug));
  document.getElementById('skeleton').classList.add('hidden');
  renderCard();
  await refreshProgress();
}

async function flipForInfo() {
  const current = deck[0];
  if (!current) return;
  if (flipped) {
    card.classList.remove('flipped');
    flipped = false;
    detailBox.innerHTML = '';
    return;
  }

  const details = await request(`/discovery/details?slug=${encodeURIComponent(current.slug)}`);
  detailBox.innerHTML = `
    <p><strong>Genres:</strong> ${details.genres.join(', ')}</p>
    <p><strong>Cast:</strong> ${details.cast.join(', ')}</p>
    <p>${details.synopsis}</p>
  `;
  card.classList.add('flipped');
  flipped = true;
}

async function sendAction(action) {
  if (isSyncing || deck.length === 0) return;
  isSyncing = true;

  const current = deck.shift();
  const userId = userIdInput.value.trim();

  if (action === 'dismiss') {
    suppressionStore.dismiss(current.slug);
    haptic(10);
  } else if (action === 'watchlist') {
    haptic([20, 30, 20]);
  } else {
    haptic(15);
  }

  try {
    await request('/actions/swipe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, movie_slug: current.slug, action })
    });
    msg.textContent = `Action synced: ${action}`;
  } catch (error) {
    msg.textContent = `Sync failed: ${error.message}`;
  }

  renderCard();
  setTimeout(() => {
    isSyncing = false;
  }, 500);
}

loadDeckBtn.addEventListener('click', loadDeck);
card.addEventListener('click', flipForInfo);
card.querySelectorAll('button[data-action]').forEach((btn) => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    sendAction(btn.dataset.action);
  });
});

await loadProfiles();
setInterval(refreshProgress, 1200);
