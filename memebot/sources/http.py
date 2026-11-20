"""Gemeinsamer HTTP-Client mit Rate-Limit und Retry."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("memebot.http")


class RateLimitedClient:
    def __init__(self, base: str, min_interval_s: float = 0.0, headers: dict | None = None,
                 timeout: float = 15.0, client: httpx.AsyncClient | None = None):
        self.base = base.rstrip("/")
        self.min_interval = min_interval_s
        self._last = 0.0
        self._lock = asyncio.Lock()
        self.client = client or httpx.AsyncClient(timeout=timeout,
                                                  headers={"User-Agent": "claude-meme-trade/1.0", **(headers or {})})

    async def _wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()

    async def request(self, method: str, path: str, *, retries: int = 3, **kw: Any) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        last_err: Exception | None = None
        for attempt in range(retries):
            await self._wait()
            try:
                r = await self.client.request(method, url, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as e:
                last_err = e
                if isinstance(e, httpx.HTTPStatusError) and e.response is not None and 400 <= e.response.status_code < 500 \
                        and e.response.status_code != 429:
                    break  # Client-Fehler: nicht wiederholen
                await asyncio.sleep(1.0 * (attempt + 1))
        log.debug("%s %s fehlgeschlagen: %s", method, url, last_err)
        raise last_err or RuntimeError(f"{method} {url} fehlgeschlagen")

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    async def aclose(self) -> None:
        await self.client.aclose()
