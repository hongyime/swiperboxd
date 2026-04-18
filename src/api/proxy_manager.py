"""
Proxy Manager - Smart proxy rotation and fallback system.

This module provides a unified interface for managing multiple proxy sources:
- WebShare proxy pool (primary)
- Scrape.do API key pool (fallback) — supports multiple keys with rotation
- Direct requests (last resort)
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import quote

from .resilience import exponential_backoff_seconds


@dataclass
class ProxyStats:
    """Statistics tracking for individual proxies."""
    url: str
    success_count: int = 0
    failure_count: int = 0
    last_used: float = 0
    last_success: float = 0
    is_healthy: bool = True

    @property
    def total_usage(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        if self.total_usage == 0:
            return 1.0
        return self.success_count / self.total_usage

    def record_success(self) -> None:
        self.success_count += 1
        self.last_success = time.time()
        self.is_healthy = True

    def record_failure(self) -> None:
        self.failure_count += 1
        # Mark as unhealthy if success rate drops below 50% over many uses
        if self.total_usage > 5 and self.success_rate < 0.5:
            self.is_healthy = False


class ProxySource(Protocol):
    """Protocol for proxy sources."""

    def get_proxy_url(self) -> str | None:
        """Get a proxy URL from this source."""
        ...

    def record_success(self) -> None:
        """Record a successful request."""
        ...

    def record_failure(self) -> None:
        """Record a failed request."""
        ...


class WebShareProxyPool:
    """Manages a pool of WebShare proxies with intelligent rotation."""

    def __init__(self, proxy_urls: list[str]):
        if not proxy_urls:
            raise ValueError("WebShare proxy list cannot be empty")

        self._proxy_stats: list[ProxyStats] = [
            ProxyStats(url=url.strip()) for url in proxy_urls if url.strip()
        ]
        self._last_used_index = -1

    def get_proxy_url(self) -> str | None:
        """Get the best available proxy from the pool."""
        if not self._proxy_stats:
            return None

        # Filter to healthy proxies only
        healthy_proxies = [p for p in self._proxy_stats if p.is_healthy]

        if not healthy_proxies:
            # All failed, reset health status and try again
            for p in self._proxy_stats:
                p.is_healthy = True
            healthy_proxies = self._proxy_stats

        # Score proxies by success rate and recency
        scored = []
        current_time = time.time()

        for proxy in healthy_proxies:
            # Base score from success rate (0-1)
            score = proxy.success_rate

            # Bonus for not being the last used (avoid repetition)
            if proxy.url != self._get_last_used_url():
                score += 0.1

            # Small bonus for very recent success (within 5 minutes)
            if (current_time - proxy.last_success) < 300:
                score += 0.05

            scored.append((score, proxy))

        # Sort by score (descending) and select from top 3 randomly
        scored.sort(key=lambda x: x[0], reverse=True)
        top_proxies = [p for _, p in scored[:3]]
        selected = random.choice(top_proxies)

        selected.last_used = current_time
        self._last_used_index = self._proxy_stats.index(selected)

        return selected.url

    def record_success(self) -> None:
        """Record successful request for currently selected proxy."""
        if self._last_used_index >= 0 and self._last_used_index < len(self._proxy_stats):
            self._proxy_stats[self._last_used_index].record_success()

    def record_failure(self) -> None:
        """Record failed request for currently selected proxy."""
        if self._last_used_index >= 0 and self._last_used_index < len(self._proxy_stats):
            self._proxy_stats[self._last_used_index].record_failure()

    def _get_last_used_url(self) -> str:
        """Get the URL of the last used proxy."""
        if self._last_used_index >= 0 and self._last_used_index < len(self._proxy_stats):
            return self._proxy_stats[self._last_used_index].url
        return ""

    @property
    def stats(self) -> dict[str, dict]:
        """Get statistics for all proxies in the pool."""
        return {
            proxy.url: {
                "success_count": proxy.success_count,
                "failure_count": proxy.failure_count,
                "success_rate": proxy.success_rate,
                "is_healthy": proxy.is_healthy,
                "last_success": proxy.last_success,
            }
            for proxy in self._proxy_stats
        }


# Per-token state for ScrapeDoKeyPool
TokenState = Literal["active", "session_exhausted", "invalid"]


class ScrapeDoKeyPool:
    """Pool of Scrape.do API tokens with round-robin rotation and per-key state tracking.

    Token lifecycle within a single program run:
      active            – token is usable, include it in rotation
      session_exhausted – API returned 402 (quota depleted); skip for this run,
                          but keep in pool so it can be tested again next run
                          (Scrape.do quotas refill monthly)
      invalid           – API returned 401 (bad token); skip for this run

    Startup probe:
      When probe=True (default), each token is tested with a lightweight request
      before the first real scrape so exhausted/invalid keys are discovered early.
    """

    _PROBE_URL = "https://letterboxd.com/"

    def __init__(self, tokens: list[str], probe: bool = True):
        self._tokens: list[str] = [t.strip() for t in tokens if t.strip()]
        if not self._tokens:
            raise ValueError("ScrapeDoKeyPool requires at least one token")
        self._state: dict[str, TokenState] = {t: "active" for t in self._tokens}
        self._rr_index = 0  # round-robin cursor into the active list
        self.api_url = "http://api.scrape.do"

        print(
            f"[proxy] Scrape.do pool: {len(self._tokens)} token(s) configured",
            flush=True,
        )
        if probe:
            self._probe_all()

    # ── public API ─────────────────────────────────────────────────────────

    @property
    def has_active(self) -> bool:
        return any(s == "active" for s in self._state.values())

    def next_token(self) -> str | None:
        """Return the next active token in round-robin order, or None if all exhausted."""
        active = [t for t in self._tokens if self._state[t] == "active"]
        if not active:
            return None
        token = active[self._rr_index % len(active)]
        self._rr_index += 1
        return token

    def build_url(self, token: str, target_url: str) -> str:
        """Build a complete Scrape.do request URL for *target_url* using *token*."""
        return f"{self.api_url}/?token={token}&url={quote(target_url, safe='')}"

    def record_response(self, token: str, status_code: int) -> None:
        """Update token state based on the HTTP status returned by Scrape.do itself.

        402 → quota depleted for this billing cycle (session_exhausted)
        401 → invalid token (invalid)
        anything else → no state change (200 = success, 403 = proxied-site block, etc.)
        """
        if token not in self._state:
            return
        if status_code == 401:
            self._state[token] = "invalid"
            print(
                f"[proxy] scrape.do ...{token[-8:]} → invalid (401), "
                "skipping for session",
                flush=True,
            )
        elif status_code == 402:
            self._state[token] = "session_exhausted"
            print(
                f"[proxy] scrape.do ...{token[-8:]} → quota exhausted (402), "
                "skipping for session (refills monthly)",
                flush=True,
            )

    @property
    def stats(self) -> dict[str, str]:
        return {f"...{t[-8:]}": self._state[t] for t in self._tokens}

    # ── startup probe ───────────────────────────────────────────────────────

    def _probe_all(self) -> None:
        """Test every token once at startup to surface exhausted/invalid keys early.

        Uses a cheap GET to letterboxd.com via each token.  A 200 response means
        the token is active; 402 means quota depleted; 401 means invalid.
        Probing costs 1 Scrape.do credit per active token — intentional.
        """
        import httpx as _httpx

        print(f"[proxy] Scrape.do probe: testing {len(self._tokens)} token(s)…", flush=True)
        for token in self._tokens:
            try:
                url = self.build_url(token, self._PROBE_URL)
                with _httpx.Client(timeout=15.0, follow_redirects=True) as client:
                    resp = client.get(url)
                self.record_response(token, resp.status_code)
                state = self._state[token]
                print(
                    f"[proxy] scrape.do probe ...{token[-8:]} "
                    f"http={resp.status_code} → {state}",
                    flush=True,
                )
            except Exception as exc:
                # Network error during probe — treat as temporarily unusable
                self._state[token] = "session_exhausted"
                print(
                    f"[proxy] scrape.do probe ...{token[-8:]} "
                    f"error={exc!r} → session_exhausted",
                    flush=True,
                )

        active_count = sum(1 for s in self._state.values() if s == "active")
        print(
            f"[proxy] Scrape.do probe complete: "
            f"{active_count}/{len(self._tokens)} token(s) active",
            flush=True,
        )


class ProxyManager:
    """
    Unified proxy manager with intelligent fallback strategy.

    Fallback order:
    1. Session-cookie direct
    2. WebShare proxy pool (if configured)
    3. Scrape.do API key pool (if configured) — rotates across multiple keys
    4. Raw direct request
    """

    # How many consecutive non-scrape_do 403s before a tier is circuit-broken.
    _SKIP_THRESHOLD = 3

    def __init__(self):
        self._webshare: WebShareProxyPool | None = None
        self._scrape_do: ScrapeDoKeyPool | None = None
        self._current_source: str = "none"
        self._retry_count = 0
        self._source_failures: dict[str, int] = {"webshare": 0, "scrape_do": 0}
        # Per-tier consecutive-failure counter (non-scrape_do errors). Reset on success.
        self._consecutive_failures: dict[str, int] = {}
        # Track tiers we've already logged a circuit-open for, so every subsequent
        # iter_tiers() call doesn't re-print the same skip message.
        self._circuit_logged: set[str] = set()

        self._init_webshare()
        self._init_scrape_do()

    def _init_webshare(self) -> None:
        """Initialize WebShare proxy pool from environment."""
        proxy_env = os.getenv("WEBSHARE_PROXIES", "")
        if proxy_env:
            proxy_list = [p.strip() for p in proxy_env.split(",") if p.strip()]
            if proxy_list:
                try:
                    self._webshare = WebShareProxyPool(proxy_list)
                    print(
                        f"[proxy] WebShare pool initialized with {len(proxy_list)} proxies",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[proxy] Failed to init WebShare pool: {e}", flush=True)

    def _init_scrape_do(self) -> None:
        """Initialize Scrape.do key pool from environment.

        Reads SCRAPEDO_TOKENS (comma-separated, preferred) or falls back to the
        legacy SCRAPEDO_TOKEN single-value variable.  SCRAPEDO_CREDITS_LIMIT is
        no longer used — exhaustion is detected from API response codes instead.
        """
        # Prefer multi-key variable; fall back to legacy single-key variable
        raw = os.getenv("SCRAPEDO_TOKENS", "").strip() or os.getenv("SCRAPEDO_TOKEN", "").strip()
        if not raw:
            return
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        if not tokens:
            return
        probe = os.getenv("SCRAPEDO_PROBE_ON_STARTUP", "true").lower() != "false"
        try:
            self._scrape_do = ScrapeDoKeyPool(tokens, probe=probe)
        except Exception as e:
            print(f"[proxy] Failed to init Scrape.do pool: {e}", flush=True)

    def iter_tiers(
        self,
        target_url: str,
        session_cookie: str | None = None,
    ) -> list[tuple[str, dict]]:
        """Return ordered (tier_name, extra_client_kwargs) pairs to try.

        Tier order:
          1. "cookie"    – direct request with session cookie
          2. "webshare"  – WebShare rotating proxy pool (+ cookie if available)
          3. "scrape_do" – Scrape.do key pool; dict includes "scrape_do_url" and
                           "scrape_do_token" — caller must pop both before passing
                           kwargs to the HTTP client
          4. "direct"    – raw direct request

        Circuit-breaker: a tier is skipped after _SKIP_THRESHOLD consecutive
        failures (resets on success).
        """
        tiers: list[tuple[str, dict]] = []

        def _circuit_open(name: str) -> bool:
            fails = self._consecutive_failures.get(name, 0)
            if fails >= self._SKIP_THRESHOLD:
                if name not in self._circuit_logged:
                    print(
                        f"[scraper] tier={name} skipped ({fails} consecutive failures) — "
                        f"suppressing further skip logs until recovery",
                        flush=True,
                    )
                    self._circuit_logged.add(name)
                return True
            return False

        # Tier 1: session-cookie direct
        if session_cookie and not _circuit_open("cookie"):
            tiers.append((
                "cookie",
                {"cookies": {"letterboxd.user.CURRENT": session_cookie}},
            ))

        # Tier 2: WebShare proxy (+ cookie when available)
        if self._webshare and not _circuit_open("webshare"):
            proxy_url = self._webshare.get_proxy_url()
            if proxy_url:
                kwargs: dict = {"proxy": proxy_url}
                if session_cookie:
                    kwargs["cookies"] = {"letterboxd.user.CURRENT": session_cookie}
                tiers.append(("webshare", kwargs))

        # Tier 3: Scrape.do key pool (round-robin across active tokens)
        if self._scrape_do and self._scrape_do.has_active and not _circuit_open("scrape_do"):
            token = self._scrape_do.next_token()
            if token:
                tiers.append((
                    "scrape_do",
                    {
                        "scrape_do_url": self._scrape_do.build_url(token, target_url),
                        "scrape_do_token": token,
                    },
                ))

        # Tier 4: raw direct fallback
        tiers.append(("direct", {}))

        return tiers

    def record_scrape_do_response(self, token: str, status_code: int) -> None:
        """Forward a Scrape.do HTTP status to the key pool for state tracking."""
        if self._scrape_do:
            self._scrape_do.record_response(token, status_code)

    def record_success_for(self, tier: str) -> None:
        """Record a successful request for the named tier."""
        if tier == "webshare" and self._webshare:
            self._webshare.record_success()
            self._source_failures["webshare"] = 0
        elif tier == "scrape_do":
            self._source_failures["scrape_do"] = 0
        prev = self._consecutive_failures.get(tier, 0)
        self._consecutive_failures[tier] = 0
        if prev >= self._SKIP_THRESHOLD:
            print(f"[scraper] tier={tier} recovered — re-enabling", flush=True)
        self._circuit_logged.discard(tier)
        self._retry_count = 0

    def record_failure_for(self, tier: str) -> None:
        """Record a failed request for the named tier."""
        if tier == "webshare" and self._webshare:
            self._webshare.record_failure()
            self._source_failures["webshare"] += 1
        elif tier == "scrape_do":
            self._source_failures["scrape_do"] += 1
        self._consecutive_failures[tier] = self._consecutive_failures.get(tier, 0) + 1
        if self._consecutive_failures[tier] == self._SKIP_THRESHOLD:
            print(
                f"[scraper] tier={tier} circuit-opened after "
                f"{self._SKIP_THRESHOLD} consecutive failures",
                flush=True,
            )
        self._retry_count += 1

    @property
    def stats(self) -> dict:
        """Get statistics for all proxy sources."""
        result = {
            "current_source": self._current_source,
            "retry_count": self._retry_count,
            "source_failures": self._source_failures.copy(),
        }
        if self._webshare:
            result["webshare"] = self._webshare.stats
        if self._scrape_do:
            result["scrape_do"] = self._scrape_do.stats
        return result

    # ------------------------------------------------------------------ legacy
    # Kept for any callers that haven't migrated to iter_tiers yet.

    def get_proxy_config(self, target_url: str | None = None) -> dict:
        """Legacy single-shot proxy config. Prefer iter_tiers for new code."""
        if self._retry_count >= 3:
            self._current_source = "none"
            self._retry_count = 0
        source = self._current_source
        if source == "none" and self._webshare:
            if self._source_failures["webshare"] < 3:
                self._current_source = "webshare"
                proxy_url = self._webshare.get_proxy_url()
                if proxy_url:
                    return {"use_proxy": True, "proxy_url": proxy_url,
                            "scrape_do_url": None, "source": "webshare"}
        if source == "none" and self._scrape_do and self._scrape_do.has_active:
            if self._source_failures["scrape_do"] < 3 and target_url:
                token = self._scrape_do.next_token()
                if token:
                    self._current_source = "scrape_do"
                    return {"use_proxy": True, "proxy_url": None,
                            "scrape_do_url": self._scrape_do.build_url(token, target_url),
                            "source": "scrape_do"}
        self._current_source = "none"
        return {"use_proxy": False, "proxy_url": None, "scrape_do_url": None, "source": "direct"}

    def record_success(self) -> None:
        """Legacy success recording. Prefer record_success_for."""
        self.record_success_for(self._current_source)

    def record_failure(self) -> None:
        """Legacy failure recording. Prefer record_failure_for."""
        self.record_failure_for(self._current_source)
        if self._current_source != "none":
            self._current_source = "none"


def get_default_proxy_manager() -> ProxyManager:
    """Get or create the global proxy manager instance."""
    if not hasattr(get_default_proxy_manager, "_instance"):
        get_default_proxy_manager._instance = ProxyManager()
    return get_default_proxy_manager._instance
