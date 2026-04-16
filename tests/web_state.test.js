import test from 'node:test';
import assert from 'node:assert/strict';

import { createProgressState, createSuppressionStore, getIngestPollingState } from '../src/web/state.js';

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
