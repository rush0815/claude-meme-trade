"""Risk-Manager: Positionsgröße, Limits, Tagesverlust, Drawdown-Not-Aus. Hat Vetorecht."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..models import Market, Verdict
from .base import Agent


def utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class Portfolio:
    """Kasse + Bewertung offener Positionen."""

    def __init__(self, ctx, wallet_pubkey: str | None = None):
        self.ctx = ctx
        self.wallet = wallet_pubkey
        self._live_cash: tuple[float, float] | None = None

    async def cash(self) -> float:
        if self.ctx.mode == "paper":
            return float(self.ctx.db.get("paper_cash", self.ctx.cfg.paper_start_sol))
        if self._live_cash and time.time() - self._live_cash[0] < 20:
            return self._live_cash[1]
        bal = await self.ctx.rpc.sol_balance(self.wallet)
        self._live_cash = (time.time(), bal)
        return bal

    def invalidate(self) -> None:
        self._live_cash = None

    def positions_value(self) -> float:
        return sum((p["tokens_remaining"] or 0) * (p["last_price"] or p["entry_price"] or 0)
                   for p in self.ctx.db.open_positions(self.ctx.mode))

    async def equity(self) -> tuple[float, float]:
        cash = await self.cash()
        return cash + self.positions_value(), cash


class RiskManager(Agent):
    name = "Risk-Manager"

    def __init__(self, ctx, portfolio: Portfolio):
        super().__init__(ctx)
        self.pf = portfolio

    # -------------------------------------------------------------- Zustand
    def killed(self) -> bool:
        return bool(self.db.get("killed", False))

    def paused(self) -> bool:
        return bool(self.db.get("paused", False))

    async def update_limits(self) -> None:
        """Höchststand / Tagesstart pflegen, Drawdown-Not-Aus auslösen."""
        equity, _ = await self.pf.equity()
        key = f"peak_{self.ctx.mode}"
        peak = max(float(self.db.get(key) or equity), equity)
        self.db.set(key, peak)
        day = self.db.get(f"day_{self.ctx.mode}")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not day or day.get("date") != today:
            self.db.set(f"day_{self.ctx.mode}", {"date": today, "equity": equity})
        dd = 1 - equity / peak if peak > 0 else 0
        if dd >= self.cfg.risk.max_drawdown_pct and not self.killed():
            self.db.set("killed", True)
            self.log(f"NOT-AUS: Drawdown {dd:.0%} vom Höchststand {peak:.3f} SOL. Keine neuen Käufe.", "error")

    def daily_loss_hit(self) -> bool:
        day = self.db.get(f"day_{self.ctx.mode}") or {}
        start = float(day.get("equity") or 0)
        if start <= 0:
            return False
        pnl = self.db.realized_pnl_since(self.ctx.mode, utc_midnight())
        return pnl <= -self.cfg.risk.max_daily_loss_pct * start

    def capacity(self) -> Verdict:
        if self.killed():
            return Verdict(False, "Not-Aus aktiv")
        if self.paused():
            return Verdict(False, "pausiert")
        if len(self.db.open_positions(self.ctx.mode)) >= self.cfg.risk.max_open_positions:
            return Verdict(False, "max. offene Positionen erreicht", retry=True)
        if self.daily_loss_hit():
            return Verdict(False, "Tagesverlust-Limit erreicht", retry=True)
        return Verdict(True)

    # -------------------------------------------------------------- Freigabe
    async def approve(self, m: Market, score: float) -> Verdict:
        r = self.cfg.risk
        cap = self.capacity()
        if not cap.ok:
            return cap
        if any(p["mint"] == m.mint for p in self.db.open_positions(self.ctx.mode)):
            return Verdict(False, "bereits im Bestand")
        last = self.db.last_trade_of(m.mint)
        if last and time.time() - last["opened_at"] < r.cooldown_hours * 3600:
            return Verdict(False, "Cooldown für diesen Coin")

        equity, cash = await self.pf.equity()
        min_score = self.cfg.strategy.min_score
        factor = 0.75 + 0.5 * max(0.0, min(1.0, (score - min_score) / max(100 - min_score, 1)))
        size = equity * r.position_pct * factor
        size = min(size, r.max_position_sol, cash - r.sol_reserve)
        if size < r.min_position_sol:
            return Verdict(False, f"zu wenig Kapital (verfügbar {cash:.3f} SOL)")
        return Verdict(True, f"Freigabe {size:.4f} SOL", {"size_sol": round(size, 6), "equity": equity})
