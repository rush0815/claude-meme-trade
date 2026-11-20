"""Phasen-Analyst: In welcher Phase ist der Coin? Kauft nicht in tote Coins nach dem Hype.

Holt die 15-Minuten-Kerzen seit Launch (GeckoTerminal) und prüft:
* Abstand des aktuellen Kurses zum bisherigen Hoch (Schlusskurse, damit einzelne Dochte nicht zählen)
* wie lange das Hoch zurückliegt
Nur für Coins, die alle anderen Prüfungen bestanden haben -> wenige Anfragen.
"""
from __future__ import annotations

import time

from ..models import Market, Verdict
from .base import Agent


def analyse(candles: list[list[float]], price_usd: float | None = None) -> dict:
    closes = [c[4] for c in candles if c[4] > 0]
    if not closes:
        return {}
    i_peak = max(range(len(closes)), key=lambda i: closes[i])
    peak = closes[i_peak]
    now = price_usd if price_usd and price_usd > 0 else closes[-1]
    return {
        "hoch_usd": peak,
        "vom_hoch_pct": round((now / peak - 1) * 100, 1),
        "std_seit_hoch": round((time.time() - candles[i_peak][0]) / 3600, 1),
        "kerzen": len(closes),
    }


class PhaseAnalyst(Agent):
    name = "Phasen-Analyst"
    CACHE_S = 5 * 60

    def __init__(self, ctx):
        super().__init__(ctx)
        self._cache: dict[str, tuple[float, dict]] = {}

    def judge(self, info: dict) -> Verdict:
        ph = self.cfg.get("phase") or {}
        max_dd = float(ph.get("max_drawdown_from_high_pct", 40))
        max_h = float(ph.get("max_hours_since_high", 3))
        if not info:
            return Verdict(False, "keine Kursdaten für den Phasen-Check", retry=True)
        dd = -info["vom_hoch_pct"]
        if dd > max_dd:
            return Verdict(False, f"{dd:.0f}% unter dem Hoch – Hype vorbei", info, retry=True)   # Erholung möglich
        if info["std_seit_hoch"] > max_h and dd > 25:
            return Verdict(False, f"Hoch vor {info['std_seit_hoch']:.0f} h, {dd:.0f}% darunter – Hype vorbei", info,
                           retry=True)
        return Verdict(True, f"Phase ok ({info['vom_hoch_pct']:+.0f}% vom Hoch)", info)

    async def check(self, m: Market) -> Verdict:
        ph = self.cfg.get("phase") or {}
        if not ph.get("enabled", True) or getattr(self.ctx, "gecko", None) is None:
            return Verdict(True, "Phasen-Check aus")
        hit = self._cache.get(m.pair_address)
        if hit and time.time() - hit[0] < self.CACHE_S:
            info = hit[1]
        else:
            try:
                candles = await self.ctx.gecko.candles(m.pair_address)
            except Exception:
                return Verdict(False, "Kerzen nicht abrufbar (GeckoTerminal-Limit)", retry=True)
            info = analyse(candles, m.price_usd)
            self._cache[m.pair_address] = (time.time(), info)
        v = self.judge(info)
        if not v.ok and v.details:
            self.log(f"{m.symbol}: abgelehnt – {v.reason}", mint=m.mint)
        return v
