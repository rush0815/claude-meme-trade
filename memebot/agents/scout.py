"""Scout: findet Kandidaten (pump.fun-Graduations, DexScreener-Listen) und holt Marktdaten."""
from __future__ import annotations

import time

from ..models import Market
from .base import Agent


class Scout(Agent):
    name = "Scout"

    async def on_migration(self, mint: str) -> None:
        if self.db.upsert_candidate(mint, "pump-graduation"):
            self.log("Neue Graduation entdeckt", mint=mint)

    async def discover_lists(self) -> int:
        if not self.cfg.scout.use_dexscreener_lists:
            return 0
        new = 0
        for mint, src in await self.ctx.dex.latest_mints():
            if self.db.upsert_candidate(mint, src):
                new += 1
        if new:
            self.log(f"{new} neue Kandidaten von DexScreener")
        return new

    async def due(self) -> list[tuple[dict, Market | None]]:
        sc = self.cfg.scout
        cands = self.db.due_candidates(sc.recheck_seconds, limit=60)
        if not cands:
            return []
        markets = await self.ctx.dex.markets([c["mint"] for c in cands])
        out = []
        now = time.time()
        for c in cands:
            m = markets.get(c["mint"])
            if m is None and now - c["first_seen"] > 3 * 3600:
                self.db.set_candidate(c["mint"], "expired", "kein SOL-Paar gefunden")
                continue
            out.append((c, m))
        return out

    def trim_watchlist(self) -> None:
        limit = int(self.cfg.scout.max_watchlist)
        self.db.x("UPDATE candidates SET status='expired', reason='Watchlist voll' WHERE mint IN ("
                  " SELECT mint FROM candidates WHERE status IN ('new','waiting') ORDER BY first_seen DESC"
                  " LIMIT -1 OFFSET ?)", (limit,))
