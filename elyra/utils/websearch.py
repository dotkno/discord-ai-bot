"""
utils/websearch.py — Web Search Provider with Failover Support
Supports Ollama (free) and Tavily (backup) with themed language integration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Optional

import httpx

log = logging.getLogger("bot.websearch")


# ─── Abstract Base Provider ───────────────────────────────────────────────────────
class _BaseWebSearchProvider(ABC):
    """All web search providers implement search(). name is used in logs only."""
    name: str = "unknown"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """
        Perform a web search and return results.
        
        Args:
            query: The search query
            max_results: Maximum number of results to return
            
        Returns:
            List of dicts with 'title', 'url', and 'snippet' keys
        """
        raise NotImplementedError


# ─── Ollama Web Search Provider ────────────────────────────────────────────────────
class OllamaWebSearchProvider(_BaseWebSearchProvider):
    """Ollama web search provider (free, local or remote)."""
    
    def __init__(self, base_url: str = "http://localhost:11434", api_key_index: int = 1) -> None:
        self.name = f"ollama/websearch (key {api_key_index})"
        self._base_url = base_url.rstrip("/")
        # Try numbered key first, fall back to default
        self._api_key = os.environ.get(f"OLLAMA_API_KEY_{api_key_index}") or os.environ.get("OLLAMA_API_KEY", "")
        
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """
        Search using Ollama's built-in web search capability.
        Note: Ollama models don't have built-in web search. This provider is not functional
        and will always fail. Use Tavily instead for actual web search.
        """
        log.warning("Ollama web search is not supported - Ollama models don't have built-in web search capabilities")
        raise NotImplementedError("Ollama does not support web search. Use Tavily instead.")


# ─── Tavily Web Search Provider ────────────────────────────────────────────────────
class TavilyWebSearchProvider(_BaseWebSearchProvider):
    """Tavily web search provider (backup, API-based)."""
    
    def __init__(self, api_key_index: int = 1) -> None:
        self.name = f"tavily/websearch (key {api_key_index})"
        # Try numbered key first, fall back to default
        api_key = os.environ.get(f"TAVILY_API_KEY_{api_key_index}") or os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise KeyError(f"Neither TAVILY_API_KEY_{api_key_index} nor TAVILY_API_KEY found in .env")
        self._api_key = api_key
        self._base_url = "https://api.tavily.com/search"
        
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """
        Search using Tavily API.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                }
                
                response = await client.post(self._base_url, json=payload)
                response.raise_for_status()
                
                data = response.json()
                
                results = []
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", "")[:500]  # Limit snippet length
                    })
                
                return results
                
        except httpx.HTTPError as e:
            log.warning("Tavily web search failed: %s", e)
            raise
        except Exception as e:
            log.exception("Tavily web search error: %s", e)
            raise


# ─── Web Search Failover Chain ─────────────────────────────────────────────────────
_WEB_COOLDOWN_QUOTA = 300  # 5 minutes for quota/rate-limit failures
_WEB_COOLDOWN_GENERIC = 60  # 1 minute for any other transient failure

_WEB_QUOTA_KEYWORDS = {"429", "quota", "exhausted", "rate", "limit", "resource_exhausted"}


class WebSearchFailoverChain:
    """
    Wraps an ordered list of web search providers. On each call to search():
      1. Try every provider in order, skipping ones still in cooldown.
      2. On success → return the results immediately.
      3. On failure → log silently, put that provider on cooldown, try the next.
      4. If every provider fails → return empty list (no web search results).
    """

    def __init__(self, providers: list[_BaseWebSearchProvider]) -> None:
        self._providers = providers
        self._cooldown_until: dict[str, float] = {}

    def _is_on_cooldown(self, provider: _BaseWebSearchProvider) -> bool:
        until = self._cooldown_until.get(provider.name, 0.0)
        return time.monotonic() < until

    def _put_on_cooldown(self, provider: _BaseWebSearchProvider, error_text: str) -> None:
        is_quota = any(kw in error_text.lower() for kw in _WEB_QUOTA_KEYWORDS)
        duration = _WEB_COOLDOWN_QUOTA if is_quota else _WEB_COOLDOWN_GENERIC
        self._cooldown_until[provider.name] = time.monotonic() + duration
        log.warning(
            "Web search provider %s put on cooldown for %ds (%s)",
            provider.name,
            duration,
            "quota/rate-limit" if is_quota else "generic error",
        )

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        for provider in self._providers:
            if self._is_on_cooldown(provider):
                log.debug("Skipping web search provider %s — still in cooldown", provider.name)
                continue

            try:
                log.debug("Trying web search provider: %s", provider.name)
                results = await provider.search(query, max_results)
                log.info("Web search results from %s: %d results", provider.name, len(results))
                return results

            except Exception as exc:
                log.warning(
                    "Web search provider %s failed: %s — trying next in chain",
                    provider.name,
                    exc,
                )
                self._put_on_cooldown(provider, str(exc))

        log.warning("All web search providers failed for query: %r", query[:60])
        return []

    def status(self) -> list[dict]:
        now = time.monotonic()
        result = []
        for p in self._providers:
            until = self._cooldown_until.get(p.name, 0.0)
            on_cd = now < until
            result.append({
                "name": p.name,
                "on_cooldown": on_cd,
                "resumes_in": max(0.0, until - now) if on_cd else 0.0,
            })
        return result


# ─── Web Search Chain Builder ───────────────────────────────────────────────────────
def _build_web_search_chain() -> Optional[WebSearchFailoverChain]:
    """Build the web search failover chain from environment configuration."""
    chain_str = os.environ.get("WEB_SEARCH_PROVIDER_CHAIN", "").strip()
    entries: list[tuple[str, int]] = []

    if chain_str:
        for entry in chain_str.split(","):
            entry = entry.strip()
            if ":" not in entry:
                log.warning("Skipping malformed WEB_SEARCH_PROVIDER_CHAIN entry: %r", entry)
                continue
            
            # Split from the right to handle provider names with colons
            parts = entry.rsplit(":", 1)
            
            if len(parts) == 1:
                log.warning("Skipping malformed WEB_SEARCH_PROVIDER_CHAIN entry (missing index): %r", entry)
                continue
            
            provider_type, index_str = parts
            try:
                api_key_index = int(index_str.strip())
                entries.append((provider_type.strip().lower(), api_key_index))
            except ValueError:
                log.warning("Skipping malformed WEB_SEARCH_PROVIDER_CHAIN entry (invalid index): %r", entry)
                continue
    else:
        # Default: try Ollama first, then Tavily
        entries = [("ollama", 1), ("tavily", 1)]

    providers: list[_BaseWebSearchProvider] = []
    for provider_type, api_key_index in entries:
        try:
            if provider_type == "ollama":
                base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
                providers.append(OllamaWebSearchProvider(base_url, api_key_index))
            elif provider_type == "tavily":
                providers.append(TavilyWebSearchProvider(api_key_index))
            else:
                log.warning("Unknown web search provider type %r — skipping", provider_type)
                continue
            log.info("  ✓ Web search failover chain: registered %s (API key %d)", provider_type, api_key_index)
        except Exception:
            log.exception("  ✗ Failed to init web search provider %s (API key %d) — skipping", provider_type, api_key_index)

    if not providers:
        log.warning("No web search providers could be initialized. Web search disabled.")
        return None

    return WebSearchFailoverChain(providers)


# ─── Module-level singleton ─────────────────────────────────────────────────────────
_web_search_chain: Optional[WebSearchFailoverChain] = None


def get_web_search_chain() -> Optional[WebSearchFailoverChain]:
    """Get the singleton web search chain instance."""
    global _web_search_chain
    if _web_search_chain is None:
        _web_search_chain = _build_web_search_chain()
    return _web_search_chain


def reload_web_search_chain() -> Optional[WebSearchFailoverChain]:
    """Force reload the web search chain."""
    global _web_search_chain
    _web_search_chain = _build_web_search_chain()
    return _web_search_chain
