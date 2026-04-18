# Swiperboxd Sync (Chrome extension)

Scrapes your Letterboxd watchlist and diary using **your** logged-in browser
session, then pushes the film slugs to the Swiperboxd backend in batches.

Letterboxd blocks Vercel's AWS IP ranges with 403, so server-side scraping
fails in production. This extension sidesteps that by running in your browser.

## Install (developer mode)

1. Open `chrome://extensions`
2. Enable **Developer mode** (top-right)
3. **Load unpacked** → pick this `extension/` directory
4. Pin the Swiperboxd Sync icon to the toolbar
5. Sign in to Letterboxd in the same browser profile

## Configure

Open the extension popup and either:

- **Auto**: sign in to your Swiperboxd site in the same browser — the content
  script picks up credentials via `postMessage` automatically, or
- **Manual**: paste your API base URL, username, and session token into the
  popup fields, then click **Save credentials**.

Click **Start sync**. The popup shows live progress per page. **Stop sync**
halts after the in-flight batch flushes.

Tick **Auto-sync every 6 hours** to run periodically in the background.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 config (cookies + storage + alarms, letterboxd + swiperboxd host perms) |
| `popup.html` / `popup.js` | UI + controller |
| `background.js` | Service worker — scrapes Letterboxd, pushes batches |
| `content.js` | Forwards login credentials from the Swiperboxd web app |
| `icons/` | 48/128px PNG icons |
