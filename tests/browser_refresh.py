"""Real Chromium UI with synthetic API/extension responses and no live writes.

Run directly, after `python -m playwright install chromium`.
SWIPER_BROWSER_URL optionally verifies deployed assets with the same intercepted API.
"""
import asyncio
import json
import os
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect

WEB = Path(os.environ.get("SWIPER_WEB_ROOT", Path(__file__).resolve().parents[1] / "src" / "web"))
BASE = os.environ.get("SWIPER_BROWSER_URL", "https://swiper.test").rstrip("/")
POSTER = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='2' height='3'%3E%3C/svg%3E"


class RefreshBrowser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch()
        self.context = await self.browser.new_context(viewport={"width": int(os.environ.get("SWIPER_WIDTH", "1440")), "height": 900})
        self.page = await self.context.new_page()
        self.page.set_default_timeout(5000)
        self.calls, self.errors, self.holds = [], [], {}
        self.status_code, self.synced = 200, True
        self.deck_mode = {}
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        await self.context.add_init_script("""
          window.fixtureUser = 'fixture'; window.fixtureCrossSyncs = 0;
          localStorage.setItem('swiperboxd.cross-sync.fixture', String(Date.now()));
          window.addEventListener('message', event => {
            const d = event.data;
            if (d?.type === 'SWIPERBOXD_GET_AUTH') window.postMessage({
              type: 'SWIPERBOXD_AUTH_RESULT', ok: true, username: window.fixtureUser,
              sessionToken: 'synthetic-browser-token', requestId: d.requestId
            }, location.origin);
            if (d?.type === 'SWIPERBOXD_CROSS_SYNC') {
              window.fixtureCrossSyncs++;
              if (window.fixtureSyncSuccess) window.postMessage({
                type: 'SWIPERBOXD_CROSS_SYNC_RESULT', ok: true, requestId: d.requestId,
                summary: {watchlistPulled: 1}
              }, location.origin);
            }
          });
        """)
        await self.context.route("**/*", self.route)

    async def asyncTearDown(self):
        for _, release in self.holds.values():
            release.set()
        await self.context.close()
        await self.browser.close()
        await self.pw.stop()
        self.assertEqual(self.errors, [])

    async def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == "/" or path.startswith("/web/"):
            if BASE != "https://swiper.test":
                await route.continue_()
                return
            file = WEB / ("index.html" if path == "/" else path.removeprefix("/web/"))
            mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
            await route.fulfill(path=str(file), content_type=mime.get(file.suffix, "application/octet-stream"))
            return
        self.assertEqual(request.method, "GET", "Browser fixture must never send writes")
        self.calls.append(request.url)
        status = 200
        if path == "/lists/catalog":
            data = {"results": [{"list_id": key, "title": title, "description": description,
                                 "owner_name": "Fixture", "film_count": 2, "is_ready": True}
                                for key, title, description in [("alpha", "Alpha list", "dark films"),
                                                                 ("beta", "Beta list", "bright films")]]}
            query = parse_qs(urlparse(request.url).query).get("q", [""])[0].lower()
            data["results"] = [item for item in data["results"]
                               if query in item["title"].lower() or query in item["description"].lower()]
        elif path.endswith("/sync-status"):
            status = self.status_code
            data = {"has_synced": self.synced, "watchlist_count": int(self.synced), "diary_count": 0}
        elif path.endswith("/deck"):
            name = path.split("/")[2] if path.startswith("/lists/") else "discovery"
            mode = self.deck_mode.get(name)
            status = 503 if mode == "error" else 200
            data = {"results": [] if mode == "empty" else [{"slug": name, "title": name.title(),
                                                           "poster_url": POSTER, "rating": 4.5}]}
        else:
            raise AssertionError(f"Unexpected external request: {path}")
        hold = self.holds.pop(path, None)
        if hold:
            started, release = hold
            started.set()
            await release.wait()
        try:
            await route.fulfill(status=status, json=data)
        except Exception:
            if not request.failure:
                raise

    async def start(self):
        await self.page.goto(BASE)
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "alpha")

    async def hold(self, path):
        started, release = asyncio.Event(), asyncio.Event()
        self.holds[path] = (started, release)
        return started, release

    async def refresh(self):
        await self.page.locator("#refresh-btn").dispatch_event("click")

    async def select_beta(self):
        await self.page.locator("#profile-btn").click()
        await self.page.locator('[data-list-id="beta"]').click()
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "beta")

    async def test_search_filters_without_requests_or_swipes(self):
        await self.start()
        count = len(self.calls)
        await self.page.locator("#profile-btn").click()
        search = self.page.locator("#list-search-input")
        await search.press_sequentially("dark", delay=40)
        await expect(self.page.locator(".profile-option")).to_have_count(1)
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "alpha")
        self.assertEqual(len(self.calls), count)
        await search.fill("no matches")
        await expect(self.page.locator("#profile-options")).to_contain_text("No matching lists")
        await expect(self.page.locator("#card-stack")).to_be_visible()
        self.assertEqual(len(self.calls), count)

    async def test_old_success_cannot_replace_new_list(self):
        await self.start()
        started, release = await self.hold("/lists/alpha/deck")
        await self.refresh()
        await started.wait()
        await self.select_beta()
        release.set()
        await self.page.wait_for_timeout(150)
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "beta")

    async def test_old_empty_does_not_launch_fallback(self):
        await self.start()
        self.deck_mode["alpha"] = "empty"
        started, release = await self.hold("/lists/alpha/deck")
        await self.refresh()
        await started.wait()
        await self.select_beta()
        count = len(self.calls)
        release.set()
        await self.page.wait_for_timeout(150)
        self.assertEqual(len(self.calls), count)
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "beta")

    async def test_old_failure_cannot_hide_new_list(self):
        await self.start()
        self.deck_mode["alpha"] = "error"
        started, release = await self.hold("/lists/alpha/deck")
        await self.refresh()
        await started.wait()
        await self.select_beta()
        release.set()
        await self.page.wait_for_timeout(150)
        await expect(self.page.locator("#empty-state")).to_be_hidden()

    async def test_repeated_refresh_joins_pending_read(self):
        await self.start()
        started, release = await self.hold("/lists/alpha/deck")
        count = len(self.calls)
        await self.refresh()
        await started.wait()
        await self.refresh()
        await self.refresh()
        await self.page.wait_for_timeout(100)
        self.assertEqual(len(self.calls), count + 1)
        release.set()
        await expect(self.page.locator("#card-stack")).to_be_visible()

    async def test_unavailable_status_does_not_start_cross_sync(self):
        self.status_code = 503
        await self.start()
        self.assertEqual(await self.page.evaluate("window.fixtureCrossSyncs"), 0)
        await expect(self.page.locator("#browse-mode-hint")).to_contain_text("status")
        await expect(self.page.locator("#btn-watchlist")).to_be_disabled()

    async def test_status_retry_recovers_without_automatic_polling(self):
        self.status_code = 503
        await self.start()
        self.status_code = 200
        await self.page.locator("#status-retry-btn").click()
        await expect(self.page.locator("#btn-watchlist")).to_be_enabled()
        self.assertEqual(sum("sync-status" in url for url in self.calls), 2)
        self.assertEqual(await self.page.evaluate("window.fixtureCrossSyncs"), 0)

    async def test_disconnect_cancels_pending_fallback(self):
        await self.start()
        self.deck_mode["alpha"] = "empty"
        started, release = await self.hold("/lists/alpha/deck")
        await self.refresh()
        await started.wait()
        await self.page.locator("#logout-btn").click()
        count = len(self.calls)
        release.set()
        await self.page.wait_for_timeout(150)
        self.assertEqual(len(self.calls), count)
        await expect(self.page.locator("#setup-screen")).to_be_visible()

    async def test_read_timeout_allows_manual_retry(self):
        await self.start()
        await self.page.clock.install()
        started, release = await self.hold("/lists/alpha/deck")
        await self.refresh()
        await started.wait()
        await self.page.clock.fast_forward(15100)
        await expect(self.page.locator("#empty-state")).to_contain_text("Request timed out")
        release.set()
        await self.page.locator("#empty-retry-btn").click()
        await expect(self.page.locator("#card-stack")).to_be_visible()

    async def test_first_sync_refreshes_status_and_enables_saving(self):
        self.synced = False
        started, release = await self.hold("/users/fixture/sync-status")
        await self.page.goto(BASE)
        await started.wait()
        self.synced = True
        await self.page.evaluate("window.fixtureSyncSuccess = true")
        release.set()
        await expect(self.page.locator("#btn-watchlist")).to_be_enabled()
        self.assertEqual(sum("sync-status" in url for url in self.calls), 2)
        self.assertEqual(await self.page.evaluate("window.fixtureCrossSyncs"), 1)

    async def test_current_empty_list_keeps_existing_fallbacks(self):
        self.deck_mode["alpha"] = "empty"
        await self.page.goto(BASE)
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "discovery")
        decks = [url for url in self.calls if "/deck?" in url]
        self.assertEqual(len(decks), 3)
        self.assertIn("include_seen=true", decks[1])

    async def test_account_switch_ignores_old_status(self):
        self.synced = False
        started, release = await self.hold("/users/fixture/sync-status")
        await self.page.goto(BASE)
        await started.wait()
        await self.page.locator("#logout-btn").click()
        self.synced = True
        await self.page.evaluate("""() => {
          window.fixtureUser = 'second';
          localStorage.setItem('swiperboxd.cross-sync.second', String(Date.now()));
        }""")
        await self.page.locator("#setup-connect-btn").click()
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "alpha")
        count = len(self.calls)
        release.set()
        await self.page.wait_for_timeout(150)
        await expect(self.page.locator("#btn-watchlist")).to_be_enabled()
        self.assertEqual(len(self.calls), count)
        self.assertEqual(await self.page.evaluate("window.fixtureCrossSyncs"), 0)

    async def test_catalogue_failure_retry_reloads_catalogue(self):
        # Abort one catalogue request before allowing the fixture transport.
        async def abort_once(route):
            await self.context.unroute("**/lists/catalog", abort_once)
            await route.abort("failed")
        await self.context.route("**/lists/catalog", abort_once)
        await self.page.goto(BASE)
        await expect(self.page.locator("#empty-state")).to_contain_text("Error loading lists")
        await self.page.locator("#empty-retry-btn").click()
        await expect(self.page.locator(".movie-card")).to_have_attribute("data-slug", "alpha")


if __name__ == "__main__":
    unittest.main(verbosity=2)
