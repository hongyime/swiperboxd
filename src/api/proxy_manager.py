"""
Proxy Manager - Smart proxy rotation and fallback system.

This module provides a unified interface for managing multiple proxy sources:
- WebShare proxy pool (primary)
- Scrape.do API (fallback)
- Direct requests (last resort)
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Protocol
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


class ScrapeDoProxy:
    """Scrape.do API proxy service as fallback."""
    
    def __init__(self, api_token: str, credits_limit: int = 1000):
        self.api_token = api_token
        self.credits_limit = credits_limit
        self.credits_used = 0
        self.api_url = "http://api.scrape.do"
        
    def get_proxy_url(self) -> str | None:
        """Build Scrape.do proxy URL for given target URL."""
        if self.credits_used >= self.credits_limit:
            print("[proxy] Scrape.do credits exhausted", flush=True)
            return None
        
        # Return None - Scrape.do uses a different pattern (full URL construction)
        # We handle this specially in the requester
        return None
        
    def build_url(self, target_url: str) -> str:
        """Build complete Scrape.do request URL."""
        if self.credits_used >= self.credits_limit:
            raise RuntimeError("scrape_do_credits_exceeded")
        
        encoded_url = quote(target_url, safe='')
        full_url = f"{self.api_url}/?token={self.api_token}&url={encoded_url}"
        return full_url
    
    def record_success(self) -> None:
        """Track credit usage after successful request."""
        self.credits_used += 1
        if self.credits_used >= self.credits_limit:
            print(f"[proxy] Scrape.do credits depleted: {self.credits_used}/{self.credits_limit}", flush=True)
    
    def record_failure(self) -> None:
        """Record failed request (don't count credit)."""
        pass  # Only count credits for successful requests
    
    @property
    def credits_remaining(self) -> int:
        return max(0, self.credits_limit - self.credits_used)


class ProxyManager:
    """
    Unified proxy manager with intelligent fallback strategy.
    
    Fallback order:
    1. WebShare proxy pool (if configured)
    2. Scrape.do API (if configured)
    3. Direct request (no proxy)
    """
    
    # How many consecutive 403s before a tier is skipped for the rest of the session.
    _SKIP_THRESHOLD = 3

    def __init__(self):
        self._webshare: WebShareProxyPool | None = None
        self._scrape_do: ScrapeDoProxy | None = None
        self._current_source: str = "none"  # "webshare", "scrape_do", "direct"
        self._retry_count = 0
        self._source_failures: dict[str, int] = {"webshare": 0, "scrape_do": 0}
        # Per-tier consecutive-failure counter. Reset to 0 on any success.
        self._consecutive_failures: dict[str, int] = {}

        # Initialize available proxy sources
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
                    print(f"[proxy] WebShare pool initialized with {len(proxy_list)} proxies", flush=True)
                except Exception as e:
                    print(f"[proxy] Failed to init WebShare pool: {e}", flush=True)
    
    def _init_scrape_do(self) -> None:
        """Initialize Scrape.do from environment."""
        token = os.getenv("SCRAPEDO_TOKEN", "")
        if token:
            credits_limit = int(os.getenv("SCRAPEDO_CREDITS_LIMIT", "1000"))
            self._scrape_do = ScrapeDoProxy(token, credits_limit)
            print(f"[proxy] Scrape.do initialized ({credits_limit} credits)", flush=True)
    
    def iter_tiers(
        self,
        target_url: str,
        session_cookie: str | None = None,
    ) -> list[tuple[str, dict]]:
        """Return ordered (tier_name, extra_client_kwargs) pairs to try.

        Tier order:
          1. "cookie"    – direct request carrying the user's session cookie
          2. "webshare"  – WebShare rotating proxy pool (+ cookie if available)
          3. "scrape_do" – Scrape.do URL-wrapping service; dict includes
                           "scrape_do_url" key instead of a proxy kwarg
          4. "direct"    – raw direct request, no proxy, no cookie

        The caller should pop "scrape_do_url" from the dict (if present) and
        use it as the request URL rather than as a client constructor kwarg.
        """
        tiers: list[tuple[str, dict]] = []

        def _skipped(name: str) -> bool:
            fails = self._consecutive_failures.get(name, 0)
            if fails >= self._SKIP_THRESHOLD:
                print(f"[scraper] tier={name} skipped ({fails} consecutive failures)", flush=True)
                return True
            return False

        # Tier 1: session-cookie direct
        if session_cookie and not _skipped("cookie"):
            tiers.append((
                "cookie",
                {"cookies": {"letterboxd.user.CURRENT": session_cookie}},
            ))

        # Tier 2: WebShare proxy (attach cookie too — helps avoid bot detection)
        if self._webshare and not _skipped("webshare"):
            proxy_url = self._webshare.get_proxy_url()
            if proxy_url:
                kwargs: dict = {"proxy": proxy_url}
                if session_cookie:
                    kwargs["cookies"] = {"letterboxd.user.CURRENT": session_cookie}
                tiers.append(("webshare", kwargs))

        # Tier 3: Scrape.do (URL-wrapping; can't forward cookies)
        if self._scrape_do and self._scrape_do.credits_remaining > 0 and not _skipped("scrape_do"):
            tiers.append((
                "scrape_do",
                {"scrape_do_url": self._scrape_do.build_url(target_url)},
            ))

        # Tier 4: raw direct fallback
        tiers.append(("direct", {}))

        return tiers

    def record_success_for(self, tier: str) -> None:
        """Record a successful request for the named tier."""
        if tier == "webshare" and self._webshare:
            self._webshare.record_success()
            self._source_failures["webshare"] = 0
        elif tier == "scrape_do" and self._scrape_do:
            self._scrape_do.record_success()
            self._source_failures["scrape_do"] = 0
        prev = self._consecutive_failures.get(tier, 0)
        self._consecutive_failures[tier] = 0
        if prev >= self._SKIP_THRESHOLD:
            print(f"[scraper] tier={tier} recovered — re-enabling", flush=True)
        self._retry_count = 0

    def record_failure_for(self, tier: str) -> None:
        """Record a failed request for the named tier."""
        if tier == "webshare" and self._webshare:
            self._webshare.record_failure()
            self._source_failures["webshare"] += 1
        elif tier == "scrape_do" and self._scrape_do:
            self._scrape_do.record_failure()
            self._source_failures["scrape_do"] += 1
        self._consecutive_failures[tier] = self._consecutive_failures.get(tier, 0) + 1
        if self._consecutive_failures[tier] == self._SKIP_THRESHOLD:
            print(f"[scraper] tier={tier} circuit-opened after {self._SKIP_THRESHOLD} consecutive failures", flush=True)
        self._retry_count += 1

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
        if source == "none" and self._scrape_do:
            if self._source_failures["scrape_do"] < 3:
                if self._scrape_do.credits_remaining > 0:
                    self._current_source = "scrape_do"
                    if target_url:
                        return {"use_proxy": True, "proxy_url": None,
                                "scrape_do_url": self._scrape_do.build_url(target_url),
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
    
    @property
    def stats(self) -> dict:
        """Get statistics for all proxy sources."""
        result = {
            "current_source": self._current_source,
            "retry_count": self._retry_count,
            "source_failures": self._source_failures.copy()
        }
        
        if self._webshare:
            result["webshare"] = self._webshare.stats
        
        if self._scrape_do:
            result["scrape_do"] = {
                "credits_used": self._scrape_do.credits_used,
                "credits_remaining": self._scrape_do.credits_remaining,
                "credits_limit": self._scrape_do.credits_limit
            }
        
        return result


def get_default_proxy_manager() -> ProxyManager:
    """Get or create the global proxy manager instance."""
    if not hasattr(get_default_proxy_manager, "_instance"):
        get_default_proxy_manager._instance = ProxyManager()
    return get_default_proxy_manager._instance