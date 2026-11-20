"""DexScreener: neue Token-Listen und Marktdaten (kostenlos, rate-limitiert)."""
from __future__ import annotations

from ..models import SOL_MINT, Market
from .http import RateLimitedClient


class DexScreener:
    def __init__(self, base: str = "https://api.dexscreener.com", client: RateLimitedClient | None = None):
        # Token-Paare: 300 req/min (5/s). Profile/Boosts: 60 req/min, werden aber nur 2x pro Minute abgefragt.
        self.http = client or RateLimitedClient(base, min_interval_s=0.25)

    async def latest_mints(self) -> list[tuple[str, str]]:
        """(mint, quelle) aus neuen Token-Profilen und Boosts."""
        out: list[tuple[str, str]] = []
        for path, src in (("/token-profiles/latest/v1", "dex-profile"), ("/token-boosts/latest/v1", "dex-boost")):
            try:
                data = await self.http.get(path)
            except Exception:
                continue
            for t in data or []:
                if t.get("chainId") == "solana" and t.get("tokenAddress"):
                    out.append((t["tokenAddress"], src))
        return out

    async def markets(self, mints: list[str]) -> dict[str, Market]:
        """Bestes SOL-Paar (höchste Liquidität) je Token, in 30er-Batches."""
        result: dict[str, Market] = {}
        uniq = list(dict.fromkeys(mints))
        for i in range(0, len(uniq), 30):
            batch = uniq[i:i + 30]
            try:
                pairs = await self.http.get(f"/tokens/v1/solana/{','.join(batch)}")
            except Exception:
                continue
            best: dict[str, dict] = {}
            for p in pairs or []:
                if p.get("chainId") != "solana":
                    continue
                if (p.get("quoteToken") or {}).get("address") != SOL_MINT:
                    continue
                mint = (p.get("baseToken") or {}).get("address")
                liq = ((p.get("liquidity") or {}).get("usd")) or 0
                if mint and (mint not in best or liq > ((best[mint].get("liquidity") or {}).get("usd") or 0)):
                    best[mint] = p
            for mint, p in best.items():
                result[mint] = Market.from_dexscreener(p)
        return result
