const DAY_MS = 24 * 60 * 60 * 1000;

export function createProgressState() {
  return {
    update(value) {
      return Math.min(100, Math.max(0, value));
    }
  };
}

export function getIngestPollingState(progress) {
  if (progress >= 100) {
    return { status: 'completed' };
  }
  if (progress === -1) {
    return { status: 'failed', reason: 'server_reported_failure' };
  }
  return { status: 'pending' };
}

export function createSuppressionStore(now = () => Date.now()) {
  const entries = new Map();

  return {
    dismiss(slug) {
      entries.set(slug, now() + DAY_MS);
    },
    isSuppressed(slug) {
      const expiry = entries.get(slug);
      if (!expiry) return false;
      if (expiry <= now()) {
        entries.delete(slug);
        return false;
      }
      return true;
    },
    size() {
      return entries.size;
    }
  };
}

export function createSyncQueueStore(storageKey = 'swiperboxd.pending_lb_sync', storage = null) {
  const getStorage = () => {
    if (storage) return storage;
    try {
      return typeof localStorage !== 'undefined' ? localStorage : null;
    } catch {
      return null;
    }
  };

  const readQueue = () => {
    const s = getStorage();
    if (!s) return [];
    try {
      const raw = s.getItem(storageKey);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  };

  const writeQueue = (queue) => {
    const s = getStorage();
    if (!s) return;
    try {
      s.setItem(storageKey, JSON.stringify(queue.slice(0, 500)));
    } catch {}
  };

  return {
    enqueue(item) {
      const queue = readQueue();
      const exists = queue.some(q => q.action === item.action && q.slug === item.slug);
      if (!exists) {
        queue.push({
          action: item.action,
          slug: item.slug,
          timestamp: item.timestamp || Date.now(),
        });
        writeQueue(queue);
      }
      return queue.length;
    },
    peek() {
      return readQueue();
    },
    remove(action, slug) {
      const queue = readQueue().filter(q => !(q.action === action && q.slug === slug));
      writeQueue(queue);
      return queue.length;
    },
    clear() {
      writeQueue([]);
    },
    size() {
      return readQueue().length;
    }
  };
}

export function slugifyTitle(title) {
  return String(title || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function parseLetterboxdCsv(csvText) {
  if (!csvText || typeof csvText !== 'string') return [];
  const lines = csvText.split(/\r?\n/).filter(line => line.trim().length > 0);
  if (lines.length < 2) return [];

  const parseRow = (line) => {
    const fields = [];
    let current = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const char = line[i];
      if (char === '"') {
        if (inQuotes && line[i + 1] === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === ',' && !inQuotes) {
        fields.push(current.trim());
        current = '';
      } else {
        current += char;
      }
    }
    fields.push(current.trim());
    return fields;
  };

  const headers = parseRow(lines[0]).map(h => h.toLowerCase());
  const uriIdx = headers.findIndex(h => h.includes('letterboxd uri') || h === 'uri' || h === 'url');
  const nameIdx = headers.findIndex(h => h === 'name' || h === 'title');

  const slugs = [];
  const seen = new Set();

  for (let i = 1; i < lines.length; i++) {
    const row = parseRow(lines[i]);
    let slug = '';
    if (uriIdx !== -1 && row[uriIdx]) {
      const uri = row[uriIdx];
      if (uri.includes('/film/')) {
        slug = uri.split('/film/')[1].split('/')[0].trim();
      }
    }
    if (!slug && nameIdx !== -1 && row[nameIdx]) {
      slug = slugifyTitle(row[nameIdx]);
    }
    if (slug && !seen.has(slug)) {
      seen.add(slug);
      slugs.push(slug);
    }
  }

  return slugs;
}

async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * Extract CSV entries from a Letterboxd export ZIP (browser + Node).
 * Supports stored (method 0) and deflate (method 8) entries.
 * Returns [{ name, text }] for *.csv entries; [] on invalid input.
 */
export async function parseLetterboxdZip(arrayBuffer) {
  try {
    const bytes = new Uint8Array(arrayBuffer);
    if (bytes.length < 22) return [];
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

    // Locate the End Of Central Directory record (scan back up to 64 KiB for the comment).
    let eocd = -1;
    const minEocd = bytes.length - 22;
    const maxScan = Math.max(0, minEocd - 0xffff);
    for (let i = minEocd; i >= maxScan; i--) {
      if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd === -1) return [];

    const entryCount = view.getUint16(eocd + 10, true);
    let offset = view.getUint32(eocd + 16, true);
    const decoder = new TextDecoder();
    const entries = [];

    for (let n = 0; n < entryCount; n++) {
      if (offset + 46 > bytes.length || view.getUint32(offset, true) !== 0x02014b50) break;
      const method = view.getUint16(offset + 10, true);
      const compressedSize = view.getUint32(offset + 20, true);
      const nameLen = view.getUint16(offset + 28, true);
      const extraLen = view.getUint16(offset + 30, true);
      const commentLen = view.getUint16(offset + 32, true);
      const localOffset = view.getUint32(offset + 42, true);
      const name = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLen));
      offset += 46 + nameLen + extraLen + commentLen;

      if (!/\.csv$/i.test(name) || (method !== 0 && method !== 8)) continue;
      if (localOffset + 30 > bytes.length || view.getUint32(localOffset, true) !== 0x04034b50) continue;
      const lhNameLen = view.getUint16(localOffset + 26, true);
      const lhExtraLen = view.getUint16(localOffset + 28, true);
      const dataStart = localOffset + 30 + lhNameLen + lhExtraLen;
      const dataEnd = dataStart + compressedSize;
      if (dataEnd > bytes.length) continue;

      const raw = bytes.subarray(dataStart, dataEnd);
      const data = method === 0 ? raw : await inflateRaw(raw);
      entries.push({ name, text: decoder.decode(data) });
    }
    return entries;
  } catch {
    return [];
  }
}
