"""Orchestrator: verbindet die Agenten zu einer Pipeline und taktet den Bot."""
from __future__ import annotations

import asyncio
import dataclasses
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
from .sources.helius_prices import PoolPriceFeed
from .sources.pumpportal import stream_migrations

log = logging.getLogger("memebot")


class Bot:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.db = ctx.db
        from .overrides import load_into
        load_into(self.cfg, self.db)          # Änderungen aus der Web-Oberfläche
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
        # Echtzeit-Kurse (Helius) + Benachrichtigung der Web-Oberfläche
        self.feed: PoolPriceFeed | None = None
        self.live: dict[str, tuple[float, float]] = {}      # mint -> (Preis, Zeitpunkt)
        self._live_dirty: set[str] = set()
        self._live_event = asyncio.Event()
        self.ui_event = asyncio.Event()
        self._last_markets: dict = {}
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

    LIVE_MAX_AGE_S = 10

    def price_info(self, mint: str) -> dict:
        if self.feed is None:
            return {"source": "dexscreener", "note": "Live-Kurse aus (kein Helius-Key)"}
        return self.feed.info(mint)

    def _on_live_price(self, mint: str, price: float) -> None:
        self.live[mint] = (price, time.time())
        self._live_dirty.add(mint)
        self._live_event.set()

    async def live_loop(self) -> None:
        """Reagiert sofort auf Live-Kurse aus der Blockchain (max. 4x pro Sekunde)."""
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self._live_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                continue
            self._live_event.clear()
            mints, self._live_dirty = self._live_dirty, set()
            markets = {}
            for mint in mints:
                base = self._last_markets.get(mint)
                if base is not None:          # Liquidität etc. aus DexScreener, Preis live
                    markets[mint] = dataclasses.replace(base, price_sol=self.live[mint][0])
            if markets:
                try:
                    await self.positions.tick(markets, only=set(markets))
                except Exception as e:
                    log.exception("Live-Kurs-Verarbeitung")
                    self.db.log("Position-Manager", f"Fehler (Live): {e}", "error")
                self.ui_event.set()
            await asyncio.sleep(0.25)

    async def position_tick(self) -> None:
        open_pos = self.db.open_positions(self.ctx.mode)
        if not open_pos:
            if self.feed:
                self.feed.set_positions({})
            return
        markets = await self.ctx.dex.markets([p["mint"] for p in open_pos])
        self._last_markets.update(markets)
        now = time.time()
        for mint, m in list(markets.items()):
            lv = self.live.get(mint)
            if lv and now - lv[1] < self.LIVE_MAX_AGE_S:   # frischer Live-Kurs schlägt DexScreener
                markets[mint] = dataclasses.replace(m, price_sol=lv[0])
        if self.feed:
            self.feed.set_positions({p["mint"]: (p["pair_address"] or "",
                                                 self._last_markets[p["mint"]].price_sol
                                                 if p["mint"] in self._last_markets else 0.0) for p in open_pos})
        await self.positions.tick(markets)
        self.ui_event.set()

    async def position_loop(self) -> None:
        """Eigene schnelle Schleife für offene Positionen (Stop-Loss, Take-Profit, Trailing)."""
        interval = float(self.cfg.get("position_poll_seconds", 2))
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
        if self.ctx.rpc is not None and self.cfg.get("live_prices", True) and not self.cfg.get("_sim"):
            self.feed = PoolPriceFeed(self.ctx.rpc, self.cfg.api.rpc_url, self._on_live_price,
                                      on_status=lambda msg, lvl: self.db.log("Position-Manager", msg, lvl))
            tasks += [asyncio.create_task(self.feed.run(self.stop)), asyncio.create_task(self.live_loop())]
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
