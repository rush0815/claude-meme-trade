"""Position-Manager: überwacht offene Positionen und löst Ausstiege aus."""
from __future__ import annotations

import asyncio
import json
import time

from ..models import Market, Signal
from .base import Agent

DUST_SOL = 0.002


class PositionManager(Agent):
    name = "Position-Manager"

    def __init__(self, ctx, executor, portfolio):
        super().__init__(ctx)
        self.ex = executor
        self.pf = portfolio
        self._fail: dict[int, int] = {}
        self._lock = asyncio.Lock()     # DexScreener-Schleife und Live-Kurse nie gleichzeitig verkaufen lassen
        self._last_tick: dict[str, float] = {}

    def mcap(self, p: dict, price: float) -> float | None:
        """Marktkapitalisierung in USD zu einem Preis (SOL pro Token)."""
        if p.get("supply") and self.ctx.sol_usd > 0 and price:
            return price * p["supply"] * self.ctx.sol_usd
        return None

    def _record_tick(self, mint: str, price: float) -> None:
        now = time.time()
        if now - self._last_tick.get(mint, 0) >= 2:           # max. ein Punkt alle 2 s
            self._last_tick[mint] = now
            self.db.add_tick(mint, price)

    # ------------------------------------------------------------ Eröffnen
    async def open(self, sig: Signal, size_sol: float) -> int | None:
        m = sig.market
        try:
            fill = await self.ex.buy(m, size_sol, sig.security.get("decimals"))
        except Exception as e:
            self.log(f"{m.symbol}: Kauf fehlgeschlagen – {e}", "warn", m.mint)
            return None
        sol_usd = m.price_usd / m.price_sol if m.price_usd > 0 and m.price_sol > 0 else self.ctx.sol_usd
        if sol_usd > 0:
            self.ctx.sol_usd = sol_usd
        supply = m.fdv / m.price_usd if m.fdv > 0 and m.price_usd > 0 else None
        entry_mcap = fill.price * supply * sol_usd if supply and sol_usd > 0 else None
        pid = self.db.insert_position(
            mode=self.ctx.mode, mint=m.mint, symbol=m.symbol, name=m.name, pair_address=m.pair_address,
            dex=m.dex, status="open", opened_at=time.time(), size_sol=fill.sol_amount,
            tokens_total=fill.token_amount, tokens_remaining=fill.token_amount, entry_price=fill.price,
            entry_liquidity_usd=m.liquidity_usd, last_price=m.price_sol, high_price=fill.price,
            score=sig.score, score_details=json.dumps(sig.details), security=json.dumps(sig.security),
            supply=supply, entry_mcap=entry_mcap)
        self.db.add_fill(pid, "buy", fill.sol_amount, fill.token_amount, fill.price, "Einstieg",
                         fill.signature, fill.fee_sol, market_price=m.price_sol, mcap_usd=entry_mcap)
        self.db.add_tick(m.mint, m.price_sol)
        self.log(f"KAUF {m.symbol}: {fill.sol_amount:.4f} SOL, Score {sig.score:.0f}", mint=m.mint)
        return pid

    # ------------------------------------------------------------ Überwachen
    def decide(self, p: dict, m: Market | None, now: float) -> tuple[str, float] | None:
        """Gibt (Grund, Anteil der Restmenge 0..1) zurück oder None."""
        e = self.cfg.exit
        price = (m.price_sol if m and m.price_sol > 0 else p["last_price"]) or p["entry_price"]
        entry = p["entry_price"]
        pnl = (price / entry - 1) * 100 if entry else 0
        high = max(p["high_price"] or entry, price)
        if p["force_close"]:
            return "Manuell geschlossen", 1.0
        if m and p["entry_liquidity_usd"] and m.liquidity_usd < p["entry_liquidity_usd"] * e.liquidity_drop_factor:
            return "Liquidität eingebrochen", 1.0
        if pnl <= -e.stop_loss_pct:
            return "Stop-Loss", 1.0
        # Stop-Loss nachziehen (Break-even): gilt in beiden Ausstiegs-Modi
        be_on = float(e.get("breakeven_activate_pct", 0) or 0)
        if be_on > 0 and (high / entry - 1) * 100 >= be_on and pnl <= float(e.get("breakeven_floor_pct", 3)):
            return "Break-even-Stop", 1.0
        if e.get("mode", "ladder") == "simple":
            if pnl >= float(e.get("simple_take_profit_pct", 30)):
                return f"Take-Profit (+{float(e.get('simple_take_profit_pct', 30)):g}%)", 1.0
            if now - p["opened_at"] > e.max_hold_min * 60:
                return "Zeitlimit", 1.0
            return None
        levels = e.take_profit_levels
        hits = int(p["tp_hits"] or 0)
        if hits < len(levels) and pnl >= levels[hits][0]:
            frac_total = float(levels[hits][1])
            frac_rest = min(1.0, frac_total * p["tokens_total"] / max(p["tokens_remaining"], 1e-12))
            return f"Take-Profit {hits + 1} (+{levels[hits][0]}%)", frac_rest
        if (high / entry - 1) * 100 >= e.trailing_activate_pct and price <= high * (1 - e.trailing_stop_pct / 100):
            return "Trailing-Stop", 1.0
        if now - p["opened_at"] > e.max_hold_min * 60:
            return "Zeitlimit", 1.0
        return None

    async def tick(self, markets: dict[str, Market], only: set[str] | None = None) -> None:
        async with self._lock:
            await self._tick(markets, only)

    async def _tick(self, markets: dict[str, Market], only: set[str] | None) -> None:
        now = time.time()
        for p in self.db.open_positions(self.ctx.mode):
            if only is not None and p["mint"] not in only:
                continue
            m = markets.get(p["mint"])
            if m and m.price_sol > 0:
                self._record_tick(p["mint"], m.price_sol)
                p["last_price"] = m.price_sol
                p["high_price"] = max(p["high_price"] or 0, m.price_sol)
                self.db.update_position(p["id"], last_price=p["last_price"], high_price=p["high_price"])
            d = self.decide(p, m, now)
            if not d:
                continue
            reason, frac = d
            await self._sell(p, reason, frac)

    async def _sell(self, p: dict, reason: str, frac: float) -> None:
        tokens = p["tokens_remaining"] * frac
        price = p["last_price"] or p["entry_price"]
        if (p["tokens_remaining"] - tokens) * price < DUST_SOL:
            tokens = p["tokens_remaining"]
        try:
            fill = await self.ex.sell(p, tokens, reason, price)
        except Exception as e:
            n = self._fail.get(p["id"], 0) + 1
            self._fail[p["id"]] = n
            self.log(f"{p['symbol']}: Verkauf fehlgeschlagen ({n}x) – {e}", "warn", p["mint"])
            return
        self._fail.pop(p["id"], None)
        self.db.add_fill(p["id"], "sell", fill.sol_amount, fill.token_amount, fill.price, reason,
                         fill.signature, fill.fee_sol, market_price=price, mcap_usd=self.mcap(p, fill.price))
        remaining = max(0.0, p["tokens_remaining"] - fill.token_amount)
        received = (p["sol_received"] or 0) + fill.sol_amount
        fields = {"tokens_remaining": remaining, "sol_received": received}
        if reason.startswith("Take-Profit"):
            fields["tp_hits"] = int(p["tp_hits"] or 0) + 1
        closed = remaining * price < DUST_SOL or remaining <= 0
        if closed:
            pnl = received - p["size_sol"]
            fields.update(status="closed", closed_at=time.time(), tokens_remaining=0.0, exit_reason=reason,
                          pnl_sol=pnl, pnl_pct=pnl / p["size_sol"] * 100 if p["size_sol"] else 0)
            self.log(f"VERKAUF {p['symbol']} ({reason}): {pnl:+.4f} SOL ({fields['pnl_pct']:+.0f}%)", mint=p["mint"])
        else:
            self.log(f"TEILVERKAUF {p['symbol']} ({reason}): {fill.sol_amount:.4f} SOL", mint=p["mint"])
        self.db.update_position(p["id"], **fields)
