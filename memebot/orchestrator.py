"""Orchestrator: verbindet die Agenten zu einer Pipeline und taktet den Bot."""
from __future__ import annotations

import asyncio
import logging
import time

from .agents.analyst import Analyst
from .agents.base import Ctx
from .agents.executor import LiveExecutor, PaperExecutor
from .agents.position_manager import PositionManager
from .agents.risk_manager import Portfolio, RiskManager
from .agents.rug_analyst import RugAnalyst
from .agents.scout import Scout
from .agents.strategist import Strategist
from .models import Signal
from .sources.pumpportal import stream_migrations

log = logging.getLogger("memebot")


class Bot:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.db = ctx.db
        self.stop = asyncio.Event()
        wallet = None
        if ctx.mode == "live":
            self.portfolio = Portfolio(ctx)
            self.executor = LiveExecutor(ctx, self.portfolio)
            self.portfolio.wallet = self.executor.pubkey
            wallet = self.executor.pubkey
        else:
            self.portfolio = Portfolio(ctx)
            self.executor = PaperExecutor(ctx)
            if self.db.get("paper_cash") is None:
                self.db.set("paper_cash", float(self.cfg.paper_start_sol))
        self.wallet = wallet
        self.scout = Scout(ctx)
        self.rug = RugAnalyst(ctx)
        self.strategist = Strategist(ctx)
        self.risk = RiskManager(ctx, self.portfolio)
        self.positions = PositionManager(ctx, self.executor, self.portfolio)
        self.analyst = Analyst(ctx)
        self.cycle_no = 0
        self.last_cycle_ts = 0.0
        self.status = "startet"

    # ------------------------------------------------------------------ Pipeline
    async def evaluate(self, cand: dict, m) -> None:
        mint = cand["mint"]
        if m is None:
            self.db.set_candidate(mint, "waiting", "noch keine Marktdaten")
            return
        v = self.strategist.prefilter(m)
        if not v.ok:
            self.db.set_candidate(mint, "waiting" if v.retry else "expired", v.reason, symbol=m.symbol)
            return
        cap = self.risk.capacity()
        if not cap.ok:
            self.db.set_candidate(mint, "waiting", cap.reason, symbol=m.symbol)
            return
        score, details = self.strategist.score(m)
        if score < self.cfg.strategy.min_score:
            self.db.set_candidate(mint, "waiting", f"Score {score:.0f} unter {self.cfg.strategy.min_score}",
                                  score, m.symbol)
            return
        sec = await self.rug.check(m)
        if not sec.ok:
            self.db.set_candidate(mint, "waiting" if sec.retry else "rejected", sec.reason, score, m.symbol)
            return
        appr = await self.risk.approve(m, score)
        if not appr.ok:
            self.db.set_candidate(mint, "waiting" if appr.retry else "rejected", appr.reason, score, m.symbol)
            return
        size = appr.details["size_sol"]
        dec = await self.executor.decimals(m.mint, sec.details.get("decimals"))
        st = await self.rug.sell_test(m, size, dec)
        if not st.ok:
            self.db.set_candidate(mint, "waiting" if st.retry else "rejected", st.reason, score, m.symbol)
            self.rug.log(f"{m.symbol}: {st.reason}", mint=mint)
            return
        self.strategist.log(f"{m.symbol}: Signal Score {score:.0f} – {details}", mint=mint)
        pid = await self.positions.open(Signal(m, score, details, sec.details), size)
        self.db.set_candidate(mint, "bought" if pid else "waiting", "gekauft" if pid else "Kauf fehlgeschlagen",
                              score, m.symbol)

    async def cycle(self) -> None:
        self.cycle_no += 1
        open_pos = self.db.open_positions(self.ctx.mode)
        if open_pos:
            markets = await self.ctx.dex.markets([p["mint"] for p in open_pos])
            await self.positions.tick(markets)
        if self.cycle_no % 4 == 1:
            await self.scout.discover_lists()
            self.scout.trim_watchlist()
        for cand, m in await self.scout.due():
            if self.stop.is_set():
                break
            try:
                await self.evaluate(cand, m)
            except Exception as e:
                log.exception("Fehler bei %s", cand["mint"])
                self.db.set_candidate(cand["mint"], "waiting", f"Fehler: {e}")
        await self.risk.update_limits()
        equity, cash = await self.portfolio.equity()
        last = self.db.one("SELECT MAX(ts) AS t FROM equity")
        if not last or not last["t"] or time.time() - last["t"] >= 60:
            self.db.snapshot_equity(equity, cash)
        if self.cycle_no % 240 == 0:
            self.db.prune()
        self.last_cycle_ts = time.time()

    async def run(self) -> None:
        self.db.log("Orchestrator", f"Bot gestartet im Modus {self.ctx.mode.upper()}"
                    + (f", Wallet {self.wallet}" if self.wallet else ""))
        tasks = []
        if self.cfg.scout.use_pumpportal:
            tasks.append(asyncio.create_task(
                stream_migrations(self.cfg.api.pumpportal_ws, self.scout.on_migration, self.stop)))
        self.status = "läuft"
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                await self.cycle()
            except Exception as e:
                log.exception("Zyklus-Fehler")
                self.db.log("Orchestrator", f"Zyklus-Fehler: {e}", "error")
            wait = max(1.0, self.cfg.poll_seconds - (time.monotonic() - t0))
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
        self.status = "gestoppt"
        for t in tasks:
            t.cancel()
