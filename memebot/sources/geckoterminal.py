"""GeckoTerminal (öffentliche API, ohne Schlüssel): neue und trendende Solana-Pools als Kandidatenquelle.

Öffentliches Limit ca. 30 Anfragen/Minute. Die Marktdaten selbst kommen weiter von DexScreener.
"""
from __future__ import annotations

from ..models import SOL_MINT
from .http import RateLimitedClient

SKIP_DEXES = {"pump-fun"}      # noch auf der Bonding Curve -> nicht graduiert


class GeckoTerminal:
    def __init__(self, base: str = "https://api.geckoterminal.com/api/v2", client: RateLimitedClient | None = None):
        self.http = client or RateLimitedClient(base, min_interval_s=2.5,
                                                headers={"Accept": "application/json;version=20230302"})

    @staticmethod
    def _mints(payload: dict | None) -> list[str]:
        out = []
        for pool in (payload or {}).get("data") or []:
            rel = pool.get("relationships") or {}
            dex = ((rel.get("dex") or {}).get("data") or {}).get("id", "")
            if dex in SKIP_DEXES:
                continue
            tid = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
            if not tid.startswith("solana_"):
                continue
            mint = tid.split("_", 1)[1]
            if mint and mint != SOL_MINT:
                out.append(mint)
        return out

    async def latest_mints(self) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for path, params, src in (
            ("/networks/solana/trending_pools", {"duration": "5m"}, "gt-trend-5m"),
            ("/networks/solana/trending_pools", {"duration": "1h"}, "gt-trend-1h"),
            ("/networks/solana/new_pools", {"page": "1"}, "gt-new"),
            ("/networks/solana/new_pools", {"page": "2"}, "gt-new"),
        ):
            try:
                payload = await self.http.get(path, params=params, retries=2)
            except Exception:
                continue
            found += [(m, src) for m in self._mints(payload)]
        return found
