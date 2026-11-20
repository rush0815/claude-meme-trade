"""Orchestrator: verbindet die Agenten zu einer Pipeline und taktet den Bot."""
from __future__ import annotations

import asyncio
import logging
import time

from .agents.analyst import Analyst
from .agents.base import Ctx
from .agents.executor import LiveExecutor, PaperExecutor
from .agents.phase_analyst import PhaseAnalyst
from .agents.position_manager import PositionManager
from .agents.risk_manager import Portfolio, RiskManager
from .agents.rug_analyst import RugAnalyst
from .agents.scout import Scout
from .agents.strategist import Strategist
from .models import Signal
from .sources import helius_migrations
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
        self.phase = PhaseAnalyst(ctx)
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
        # Score immer berechnen (billig) und speichern -> in der Watchlist nachvollziehbar
        score, details = self.strategist.score(m)

        def mark(status: str, reason: str) -> None:
            self.db.set_candidate(mint, status, reason, score, m.symbol, details)

        v = self.strategist.prefilter(m)
        if not v.ok:
            mark("waiting" if v.retry else "expired", v.reason)
            return
        cap = self.risk.capacity()
        if not cap.ok:
            mark("waiting", cap.reason)
            return
        if score < self.cfg.strategy.min_score:
            mark("waiting", f"Score {score:.0f} unter {self.cfg.strategy.min_score}")
            return
        sec = await self.rug.check(m)
        if not sec.ok:
            mark("waiting" if sec.retry else "rejected", sec.reason)
            return
        ph = await self.phase.check(m)
        if ph.details:
            details.update({k: v for k, v in ph.details.items() if k in ("vom_hoch_pct", "std_seit_hoch")})
        if not ph.ok:
            mark("waiting" if ph.retry else "rejected", ph.reason)
            return
        appr = await self.risk.approve(m, score)
        if not appr.ok:
            mark("waiting" if appr.retry else "rejected", appr.reason)
            return
        size = appr.details["size_sol"]
        dec = await self.executor.decimals(m.mint, sec.details.get("decimals"))
        st = await self.rug.sell_test(m, size, dec)
        if not st.ok:
            mark("waiting" if st.retry else "rejected", st.reason)
            self.rug.log(f"{m.symbol}: {st.reason}", mint=mint)
            return
        self.strategist.log(f"{m.symbol}: Signal Score {score:.0f}", mint=mint)
        pid = await self.positions.open(Signal(m, score, details, sec.details), size)
        mark("bought" if pid else "waiting", "gekauft" if pid else "Kauf fehlgeschlagen")

    async def position_tick(self) -> None:
        open_pos = self.db.open_positions(self.ctx.mode)
        if open_pos:
            markets = await self.ctx.dex.markets([p["mint"] for p in open_pos])
            await self.positions.tick(markets)

    async def position_loop(self) -> None:
        """Eigene schnelle Schleife für offene Positionen (Stop-Loss, Take-Profit, Trailing)."""
        interval = float(self.cfg.get("position_poll_seconds", 3))
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                await self.position_tick()
            except Exception as e:
                log.exception("Positions-Schleife")
                self.db.log("Position-Manager", f"Fehler: {e}", "error")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=max(0.5, interval - (time.monotonic() - t0)))
            except asyncio.TimeoutError:
                pass

    async def cycle(self) -> None:
        self.cycle_no += 1
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
        tasks = [asyncio.create_task(self.position_loop())]
        if self.cfg.scout.get("use_helius_migrations", True) and self.ctx.rpc is not None:
            tasks.append(asyncio.create_task(helius_migrations.stream_migrations(
                self.ctx.rpc, self.cfg.api.rpc_url, self.scout.on_migration, self.stop,
                on_status=lambda msg, lvl: self.db.log("Scout", msg, lvl))))
        if self.cfg.scout.use_pumpportal:
            tasks.append(asyncio.create_task(
                stream_migrations(self.cfg.api.pumpportal_ws, self.scout.on_migration, self.stop,
                                  on_status=lambda msg, lvl: self.db.log("Scout", msg, lvl))))
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
