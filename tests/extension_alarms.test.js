import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const alarmName = 'swiperboxd-periodic-sync';
const sixHours = 6 * 60 * 60 * 1000;
const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));

async function worker({ enabled = true, period = 15 } = {}) {
  const now = Date.UTC(2026, 8, 14, 0, 0, 0);
  const local = { autoSync: enabled, username: 'fixture-user', sessionToken: 'fixture-token' };
  const synced = { autoSync: enabled };
  const alarms = new Map(period == null ? [] : [[alarmName, {
    name: alarmName, periodInMinutes: period, scheduledTime: now + period * 60_000,
  }]]);
  alarms.set('unrelated-alarm', { name: 'unrelated-alarm', periodInMinutes: 1 });
  const hooks = {};
  const syncs = [];
  const changes = [];
  const event = key => ({ addListener(callback) { hooks[key] = callback; } });
  const storage = values => ({
    async get(keys) {
      return Object.fromEntries((Array.isArray(keys) ? keys : [keys])
        .filter(key => Object.hasOwn(values, key)).map(key => [key, clone(values[key])]));
    },
    async set(value) { Object.assign(values, clone(value)); },
    async remove(key) { (Array.isArray(key) ? key : [key]).forEach(k => delete values[k]); },
  });
  const context = vm.createContext({
    console: { log() {}, warn() {}, error() {} }, URL,
    Date: class extends Date { static now() { return now; } },
    setTimeout, clearTimeout,
    fetch() { throw new Error('No network is permitted in extension alarm fixtures'); },
    chrome: {
      storage: { local: storage(local), sync: storage(synced) },
      cookies: { get() { throw new Error('Alarm reconciliation must not read cookies'); } },
      runtime: { onMessage: event('message'), onInstalled: event('installed'),
        onStartup: event('startup'), onConnect: event('connect'), async sendMessage() {} },
      alarms: {
        onAlarm: event('alarm'),
        async get(name) { return clone(alarms.get(name)); },
        async create(name, options) {
          changes.push(['create', name]);
          alarms.set(name, { name, ...clone(options), scheduledTime: now +
            (options.delayInMinutes ?? options.periodInMinutes) * 60_000 });
        },
        async clear(name) { changes.push(['clear', name]); return alarms.delete(name); },
      },
    },
    recordSync(options) { syncs.push(clone(options)); },
  });
  context.importScripts = filename => vm.runInContext(
    readFileSync(new URL(`../extension/${filename}`, import.meta.url), 'utf8'), context);
  vm.runInContext(readFileSync(new URL('../extension/background.js', import.meta.url), 'utf8'), context);
  // Observe the real message/alarm dispatch without running collection.
  vm.runInContext('runSync = async (options = {}) => { recordSync(options); return { ok: true }; };', context);
  await new Promise(resolve => setImmediate(resolve));
  return { now, alarms, local, synced, changes, hooks, syncs,
    message: payload => new Promise(resolve => hooks.message(payload, {}, value => resolve(clone(value)))),
    setRunning: value => vm.runInContext(`syncState.running = ${Boolean(value)};`, context) };
}

test('enabling automatic sync schedules six hours and preserves an existing correct deadline', async () => {
  const w = await worker({ enabled: false, period: null });
  assert.equal((await w.message({ type: 'SET_AUTO_SYNC', value: true })).ok, true);
  const alarm = w.alarms.get(alarmName);
  assert.equal(alarm.periodInMinutes * 60_000, sixHours);
  assert.equal(alarm.scheduledTime - w.now, sixHours);
  await w.message({ type: 'SET_AUTO_SYNC', value: true });
  assert.equal(w.changes.filter(([action]) => action === 'create').length, 1);
  assert.equal(w.alarms.get(alarmName).scheduledTime, alarm.scheduledTime);
  assert.equal(w.syncs.length, 0);
});

for (const lifecycle of ['installed', 'startup']) {
  test(`${lifecycle} migrates an enabled legacy 15-minute alarm without starting collection`, async () => {
    const w = await worker();
    await w.hooks[lifecycle]();
    assert.equal(w.alarms.get(alarmName).scheduledTime - w.now, sixHours);
    assert.equal(w.alarms.get(alarmName).periodInMinutes * 60_000, sixHours);
    assert.equal(w.syncs.length, 0);
    assert.ok(w.alarms.has('unrelated-alarm'));
  });

  test(`${lifecycle} removes a stale automatic alarm when the user disabled sync`, async () => {
    const w = await worker({ enabled: false });
    await w.hooks[lifecycle]();
    assert.equal(w.alarms.has(alarmName), false);
    assert.ok(w.alarms.has('unrelated-alarm'));
    assert.equal(w.syncs.length, 0);
  });
}

test('only an enabled six-hour alarm starts sync; stale queued legacy alarms are reconciled', async () => {
  const w = await worker();
  await w.hooks.alarm(clone(w.alarms.get(alarmName)));
  assert.equal(w.syncs.length, 0);
  assert.equal(w.alarms.get(alarmName).periodInMinutes * 60_000, sixHours);
  await w.hooks.alarm(clone(w.alarms.get(alarmName)));
  assert.equal(w.syncs.length, 1);
  w.setRunning(true);
  await w.hooks.alarm(clone(w.alarms.get(alarmName)));
  assert.equal(w.syncs.length, 1);
});

test('disabling automatic sync cancels a queued alarm and keeps manual full sync available', async () => {
  const w = await worker({ period: 360 });
  const queued = clone(w.alarms.get(alarmName));
  await w.message({ type: 'SET_AUTO_SYNC', value: false });
  await w.hooks.alarm(queued);
  assert.equal(w.syncs.length, 0);
  assert.equal(w.alarms.has(alarmName), false);
  assert.equal((await w.message({ type: 'START_SYNC', syncWatchlist: true, syncDiary: true,
    discoverLists: true, fillLists: true })).ok, true);
  assert.deepEqual(w.syncs, [{ syncWatchlist: true, syncDiary: true, discoverLists: true, fillLists: true }]);
});

test('auth credentials land in storage.local and never in storage.sync', async () => {
  const w = await worker();
  const response = await w.message({
    type: 'SWIPERBOXD_AUTH',
    apiBase: 'https://swiperboxd.vercel.app',
    username: 'alice',
    sessionToken: 'secret-token',
  });
  assert.equal(response.ok, true);
  assert.equal(w.synced.sessionToken, undefined);
  assert.equal(w.synced.username, undefined);
  assert.equal(w.synced.apiBase, undefined);
  assert.equal(w.local.sessionToken, 'secret-token');
  assert.equal(w.local.username, 'alice');
  assert.equal(w.local.apiBase, 'https://swiperboxd.vercel.app');
});

test('non-secret preferences still sync across devices', async () => {
  const w = await worker();
  await w.message({ type: 'SET_AUTO_SYNC', value: true });
  assert.equal(w.synced.autoSync, true);
  assert.equal(w.local.autoSync, true);
});

test('startup merge purges legacy credentials from storage.sync', async () => {
  const w = await worker();
  w.synced.username = 'legacy-user';
  w.synced.sessionToken = 'legacy-token';
  w.synced.apiBase = 'https://legacy.example';
  await w.hooks.startup();
  assert.equal(w.synced.username, undefined);
  assert.equal(w.synced.sessionToken, undefined);
  assert.equal(w.synced.apiBase, undefined);
  assert.equal(w.local.sessionToken, 'fixture-token');
});

function contentScript() {
  const sent = [];
  const listeners = {};
  const window = {
    location: { href: 'https://swiperboxd.vercel.app/', origin: 'https://swiperboxd.vercel.app' },
    addEventListener(type, callback) { listeners[type] = callback; },
    postMessage() {},
  };
  const context = vm.createContext({
    console: { log() {}, warn() {}, error() {} },
    setTimeout, clearTimeout, Date,
    chrome: {
      runtime: {
        lastError: undefined,
        sendMessage(message, callback) { sent.push(clone(message)); if (callback) callback({ ok: true }); },
      },
    },
    window,
  });
  vm.runInContext(readFileSync(new URL('../extension/content.js', import.meta.url), 'utf8'), context);
  return { sent, message: event => listeners.message({ source: window, ...event }) };
}

test('content script drops auth messages from untrusted origins', () => {
  const c = contentScript();
  c.message({ origin: 'https://evil.example', data: { type: 'SWIPERBOXD_AUTH', username: 'u', sessionToken: 'secret' } });
  c.message({ origin: 'https://evil.example', data: { type: 'SWIPERBOXD_GET_AUTH', requestId: 'r1' } });
  assert.deepEqual(c.sent, []);
});

test('content script forwards auth messages from trusted app origins', () => {
  const c = contentScript();
  c.message({ origin: 'https://swiperboxd.vercel.app', data: { type: 'SWIPERBOXD_AUTH', username: 'u', sessionToken: 'secret' } });
  assert.equal(c.sent.length, 1);
  assert.equal(c.sent[0].type, 'SWIPERBOXD_AUTH');
  assert.equal(c.sent[0].sessionToken, 'secret');
  c.message({ origin: 'http://localhost:3000', data: { type: 'SWIPERBOXD_GET_AUTH', requestId: 'r1' } });
  assert.equal(c.sent.length, 2);
  assert.equal(c.sent[1].type, 'GET_WEBAPP_AUTH');
});
