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
        sources = []
        if self.cfg.scout.use_dexscreener_lists:
            sources.append(("DexScreener", self.ctx.dex))
        if self.cfg.scout.get("use_geckoterminal", True) and getattr(self.ctx, "gecko", None) is not None:
            sources.append(("GeckoTerminal", self.ctx.gecko))
        total = 0
        for label, src in sources:
            try:
                found = await src.latest_mints()
            except Exception as e:
                self.log(f"{label} nicht erreichbar: {e}", "warn")
                continue
            new = sum(1 for mint, s in found if self.db.upsert_candidate(mint, s))
            if new:
                self.log(f"{new} neue Kandidaten von {label}")
            total += new
        return total

    async def due(self) -> list[tuple[dict, Market | None]]:
        sc = self.cfg.scout
        cands = self.db.due_candidates(sc.recheck_seconds, limit=int(sc.get("batch_per_cycle", 150)))
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
