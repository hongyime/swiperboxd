// Shared extension helpers used by background + popup.

function normalizeApiBase(raw, fallbackBase = "https://swiperboxd.vercel.app") {
  const base = String(raw || fallbackBase).trim().replace(/\/$/, "");
  try {
    const u = new URL(base);
    const host = (u.hostname || "").toLowerCase();
    if (!["http:", "https:"].includes(u.protocol)) return null;
    // Prevent direct Supabase endpoints from being configured in the extension.
    if (host.endsWith(".supabase.co") || host === "supabase.co") return null;
    return `${u.protocol}//${u.host}`;
  } catch {
    return null;
  }
}
