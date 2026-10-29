import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createProgressState,
  createSuppressionStore,
  createSyncQueueStore,
  getIngestPollingState,
  parseLetterboxdCsv,
  slugifyTitle,
} from '../src/web/state.js';

test('ingest polling state maps progress to statuses', () => {
  assert.deepEqual(getIngestPollingState(100), { status: 'completed' });
  assert.deepEqual(getIngestPollingState(-1), { status: 'failed', reason: 'server_reported_failure' });
  assert.deepEqual(getIngestPollingState(35), { status: 'pending' });
});

test('progress state clamps values', () => {
  const state = createProgressState();
  assert.equal(state.update(150), 100);
  assert.equal(state.update(-5), 0);
});

test('suppression expires after ttl', () => {
  let now = 1_000;
  const store = createSuppressionStore(() => now);

  store.dismiss('movie-1');
  assert.equal(store.isSuppressed('movie-1'), true);

  now += 24 * 60 * 60 * 1000 + 1;
  assert.equal(store.isSuppressed('movie-1'), false);
});

test('ingest polling helper keeps pending before completion', () => {
  assert.equal(getIngestPollingState(99).status, 'pending');
  assert.equal(getIngestPollingState(0).status, 'pending');
});

test('sync queue store manages pending write-backs and deduplicates', () => {
  const mockStorage = {
    data: {},
    getItem(k) { return this.data[k] || null; },
    setItem(k, v) { this.data[k] = v; }
  };
  const queue = createSyncQueueStore('test.queue', mockStorage);

  assert.equal(queue.size(), 0);
  assert.equal(queue.enqueue({ action: 'watchlist', slug: 'dune-part-two' }), 1);
  // Duplicate should not increase size
  assert.equal(queue.enqueue({ action: 'watchlist', slug: 'dune-part-two' }), 1);
  assert.equal(queue.enqueue({ action: 'log', slug: 'oppenheimer' }), 2);

  const items = queue.peek();
  assert.equal(items.length, 2);
  assert.equal(items[0].slug, 'dune-part-two');

  assert.equal(queue.remove('watchlist', 'dune-part-two'), 1);
  assert.equal(queue.size(), 1);
  assert.equal(queue.peek()[0].slug, 'oppenheimer');

  queue.clear();
  assert.equal(queue.size(), 0);
});

test('slugifyTitle cleans and normalizes film titles', () => {
  assert.equal(slugifyTitle('Dune: Part Two'), 'dune-part-two');
  assert.equal(slugifyTitle('Amélie'), 'amelie');
  assert.equal(slugifyTitle('Everything Everywhere All at Once'), 'everything-everywhere-all-at-once');
  assert.equal(slugifyTitle('  2001: A Space Odyssey!  '), '2001-a-space-odyssey');
});

test('parseLetterboxdCsv extracts slugs from Letterboxd export CSVs', () => {
  const watchlistCsv = `Date,Name,Year,Letterboxd URI
2024-03-01,"Dune: Part Two",2024,https://boxd.it/h2eA
2024-03-02,"Poor Things",2023,https://letterboxd.com/film/poor-things/
`;
  const slugs = parseLetterboxdCsv(watchlistCsv);
  assert.deepEqual(slugs, ['dune-part-two', 'poor-things']);

  const diaryCsv = `Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date
2024-01-10,"Oppenheimer",2023,https://letterboxd.com/film/oppenheimer/,5,,bio,2024-01-09
2024-01-12,"Past Lives",2023,https://letterboxd.com/film/past-lives/,4.5,,,2024-01-11
`;
  const diarySlugs = parseLetterboxdCsv(diaryCsv);
  assert.deepEqual(diarySlugs, ['oppenheimer', 'past-lives']);

  assert.deepEqual(parseLetterboxdCsv(''), []);
  assert.deepEqual(parseLetterboxdCsv('invalid data'), []);
});
