const DAY_MS = 24 * 60 * 60 * 1000;

export function getIngestPollingState(progress) {
  if (progress >= 100) {
    return { status: 'completed' };
  }
  if (progress === -1) {
    return { status: 'failed', reason: 'server_reported_failure' };
  }
  return { status: 'pending' };
}

export function createProgressState() {
  return {
    progress: 0,
    update(value) {
      this.progress = Math.max(0, Math.min(100, value));
      return this.progress;
    }
  };
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
