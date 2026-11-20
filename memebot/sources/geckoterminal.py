"""GeckoTerminal (öffentliche API, ohne Schlüssel): neue und trendende Solana-Pools als Kandidatenquelle.

Das öffentliche Limit ist in der Praxis knapp (429 schon bei wenigen Anfragen in Folge). Deshalb pro Aufruf
nur 2 Anfragen: immer die neuesten Pools + abwechselnd eine Trend-Liste. Bei 429 wird eine Runde ausgesetzt.
"""
from __future__ import annotations

from ..models import SOL_MINT
from .http import RateLimitedClient

SKIP_DEXES = {"pump-fun"}      # noch auf der Bonding Curve -> nicht graduiert


class GeckoTerminal:
    def __init__(self, base: str = "https://api.geckoterminal.com/api/v2", client: RateLimitedClient | None = None):
        self.http = client or RateLimitedClient(base, min_interval_s=6.0,
                                                headers={"Accept": "application/json;version=20230302"})
        self._turn = 0
        self._pause_until = 0.0

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

    ROTATION = (
        ("/networks/solana/trending_pools", {"duration": "5m"}, "gt-trend-5m"),
        ("/networks/solana/trending_pools", {"duration": "1h"}, "gt-trend-1h"),
        ("/networks/solana/new_pools", {"page": "2"}, "gt-new"),
    )

    async def latest_mints(self) -> list[tuple[str, str]]:
        import time
        if time.monotonic() < self._pause_until:
            return []
        rot = self.ROTATION[self._turn % len(self.ROTATION)]
        self._turn += 1
        found: list[tuple[str, str]] = []
        for path, params, src in (("/networks/solana/new_pools", {"page": "1"}, "gt-new"), rot):
            try:
                payload = await self.http.get(path, params=params, retries=1)
            except Exception:
                self._pause_until = time.monotonic() + 90   # Limit erreicht -> kurz Ruhe
                break
            found += [(m, src) for m in self._mints(payload)]
        return found

    async def candles(self, pool: str, minutes: int = 15, limit: int = 100,
                      before: float | None = None) -> list[list[float]]:
        """OHLCV-Kerzen [ts, o, h, l, c, v] eines Pools in USD, älteste zuerst."""
        params = {"aggregate": str(minutes), "limit": str(limit)}
        if before:
            params["before_timestamp"] = str(int(before))
        payload = await self.http.get(f"/networks/solana/pools/{pool}/ohlcv/minute", params=params, retries=2)
        rows = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        return sorted(([float(x) for x in r[:6]] for r in rows if len(r) >= 6), key=lambda r: r[0])
