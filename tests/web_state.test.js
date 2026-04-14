import test from 'node:test';
import assert from 'node:assert/strict';

import { createProgressState, createSuppressionStore } from '../src/web/state.js';

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
