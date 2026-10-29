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
