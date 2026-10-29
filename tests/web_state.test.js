import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createProgressState,
  createSuppressionStore,
  createSyncQueueStore,
  getIngestPollingState,
  parseLetterboxdCsv,
  parseLetterboxdZip,
  slugifyTitle,
} from '../src/web/state.js';

import { deflateRawSync } from 'node:zlib';

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

const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buf) {
  let crc = -1;
  for (let i = 0; i < buf.length; i++) crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ buf[i]) & 0xff];
  return (crc ^ -1) >>> 0;
}

function buildZip(entries) {
  const encoder = new TextEncoder();
  const parts = [];
  const centrals = [];
  let offset = 0;

  for (const entry of entries) {
    const nameBytes = encoder.encode(entry.name);
    const crc = crc32(entry.data);
    const payload = entry.method === 8 ? deflateRawSync(entry.data) : entry.data;

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(entry.method, 8);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(payload.length, 18);
    local.writeUInt32LE(entry.data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    parts.push(local, nameBytes, payload);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(entry.method, 10);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(payload.length, 20);
    central.writeUInt32LE(entry.data.length, 24);
    central.writeUInt16LE(nameBytes.length, 28);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, nameBytes);

    offset += 30 + nameBytes.length + payload.length;
  }

  const centralSize = centrals.reduce((sum, part) => sum + part.length, 0);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(centralSize, 12);
  eocd.writeUInt32LE(offset, 16);

  return Buffer.concat([...parts, ...centrals, eocd]);
}

function toArrayBuffer(buf) {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

const WATCHLIST_CSV = 'Date,Name,Year,Letterboxd URI\n2024-03-01,"Dune: Part Two",2024,https://letterboxd.com/film/dune-part-two/\n';
const DIARY_CSV = 'Date,Name,Year,Letterboxd URI\n2024-01-10,"Oppenheimer",2023,https://letterboxd.com/film/oppenheimer/\n';

test('parseLetterboxdZip extracts stored CSV entries', async () => {
  const zip = buildZip([
    { name: 'watchlist.csv', method: 0, data: new TextEncoder().encode(WATCHLIST_CSV) },
    { name: 'diary.csv', method: 0, data: new TextEncoder().encode(DIARY_CSV) },
    { name: 'notes.txt', method: 0, data: new TextEncoder().encode('ignored\n') },
  ]);
  const entries = await parseLetterboxdZip(toArrayBuffer(zip));
  assert.deepEqual(entries.map(entry => entry.name), ['watchlist.csv', 'diary.csv']);
  assert.deepEqual(parseLetterboxdCsv(entries.find(entry => entry.name === 'watchlist.csv').text), ['dune-part-two']);
  assert.deepEqual(parseLetterboxdCsv(entries.find(entry => entry.name === 'diary.csv').text), ['oppenheimer']);
});

test('parseLetterboxdZip extracts deflate CSV entries', async () => {
  const zip = buildZip([
    { name: 'diary.csv', method: 8, data: new TextEncoder().encode(DIARY_CSV) },
  ]);
  const entries = await parseLetterboxdZip(toArrayBuffer(zip));
  assert.equal(entries.length, 1);
  assert.equal(entries[0].name, 'diary.csv');
  assert.deepEqual(parseLetterboxdCsv(entries[0].text), ['oppenheimer']);
});

test('parseLetterboxdZip returns [] for invalid input', async () => {
  assert.deepEqual(await parseLetterboxdZip(new ArrayBuffer(0)), []);
  assert.deepEqual(await parseLetterboxdZip(toArrayBuffer(Buffer.from('not a zip'))), []);
});
