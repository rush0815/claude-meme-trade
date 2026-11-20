"""Simulierter Markt für Demo und Tests ohne Internet (python -m memebot --simulate).

Erzeugt fiktive Meme-Coins mit Zufallsverläufen (Pumps, Dumps, Rugs). Die Ergebnisse sagen
NICHTS über die echte Profitabilität aus – das prüft nur der Papiermodus mit echten Daten.
"""
from __future__ import annotations

import math
import random
import string
import time

from .models import LAMPORTS, SOL_MINT, Market


class SimWorld:
    def __init__(self, seed: int | None = None, n_tokens: int = 40):
        self.rng = random.Random(seed)
        self.tokens: dict[str, dict] = {}
        self.step_no = 0
        for _ in range(n_tokens):
            self._spawn()

    def _spawn(self) -> str:
        r = self.rng
        mint = "".join(r.choices(string.ascii_letters + string.digits, k=43)) + "pump"
        sym = "".join(r.choices(string.ascii_uppercase, k=r.randint(3, 6)))
        price = r.uniform(2e-7, 4e-6)
        liq = r.uniform(15_000, 200_000)
        self.tokens[mint] = {
            "symbol": sym, "price": price, "hist": [price] * 61, "liq": liq, "liq0": liq,
            "supply": 1e9, "created": time.time() - r.uniform(5, 900) * 60,
            "drift": r.gauss(0.0, 0.01), "vol": r.uniform(0.02, 0.06),
            "quality": r.random(),   # schlechte Coins rugen öfter
            "rugged": False, "bm5": 0, "sm5": 0, "vm5": 0.0, "vh1": 0.0,
        }
        return mint

    def step(self) -> None:
        self.step_no += 1
        r = self.rng
        for t in self.tokens.values():
            if t["rugged"]:
                t["hist"].append(t["price"]); t["hist"] = t["hist"][-61:]
                continue
            if r.random() < 0.08:
                t["drift"] = r.gauss(0.004 if t["quality"] > 0.5 else -0.006, 0.02)
            ret = t["drift"] + r.gauss(0, t["vol"])
            if r.random() < 0.004 * (1.5 - t["quality"]):
                ret = math.log(0.08)
                t["rugged"] = True
                t["liq"] *= 0.1
            t["price"] *= math.exp(ret)
            t["liq"] = max(1000.0, t["liq"] * math.exp(ret * 0.5))
            t["hist"].append(t["price"]); t["hist"] = t["hist"][-61:]
            activity = max(5, int(abs(ret) * 2000 + r.uniform(10, 60)))
            buy_share = min(0.9, max(0.1, 0.5 + ret * 6 + r.gauss(0, 0.05)))
            t["bm5"] = int(activity * buy_share); t["sm5"] = activity - t["bm5"]
            t["vm5"] = activity * r.uniform(40, 160)
            t["vh1"] = t["vh1"] * 0.92 + t["vm5"]
        if r.random() < 0.3:
            self._spawn()

    def market(self, mint: str) -> Market | None:
        t = self.tokens.get(mint)
        if not t:
            return None
        h = t["hist"]
        ch = lambda n: (h[-1] / h[-1 - n] - 1) * 100  # noqa: E731
        sol_usd = 150.0
        return Market(mint=mint, symbol=t["symbol"], name=t["symbol"] + " Coin", pair_address=mint[:20],
                      dex="pumpswap", price_sol=t["price"], price_usd=t["price"] * sol_usd,
                      liquidity_usd=t["liq"], fdv=t["price"] * t["supply"] * sol_usd, created_at=t["created"],
                      change_m5=ch(5), change_h1=ch(60), change_h6=ch(60), buys_m5=t["bm5"], sells_m5=t["sm5"],
                      buys_h1=t["bm5"] * 12, sells_h1=t["sm5"] * 12, volume_m5=t["vm5"], volume_h1=t["vh1"],
                      volume_h6=t["vh1"] * 6)


class SimDex:
    def __init__(self, world: SimWorld):
        self.w = world
        self._last = 0.0

    async def latest_mints(self):
        return [(m, "sim") for m in list(self.w.tokens)[-15:]]

    async def markets(self, mints):
        if time.monotonic() - self._last > 2:
            self._last = time.monotonic()
            self.w.step()
        return {m: mk for m in mints if (mk := self.w.market(m))}


class SimRug:
    def __init__(self, world: SimWorld):
        self.w = world

    async def report(self, mint):
        t = self.w.tokens.get(mint)
        if not t:
            return None
        bad = t["quality"] < 0.25
        return {"score": 3000 if bad else 300, "rugged": t["rugged"],
                "mintAuthority": None, "freezeAuthority": None,
                "topHolders": [{"address": f"h{i}", "pct": (6 if bad else 2.5)} for i in range(10)],
                "markets": [{"lp": {"lpLockedPct": 100}}], "risks": [], "token": {"decimals": 6}}


class SimJupiter:
    def __init__(self, world: SimWorld):
        self.w = world

    async def quote(self, input_mint, output_mint, amount_raw, slippage_bps):
        mint = output_mint if input_mint == SOL_MINT else input_mint
        t = self.w.tokens.get(mint)
        if not t:
            return None
        sol_usd = 150.0
        if input_mint == SOL_MINT:
            sol_in = amount_raw / LAMPORTS
            impact = sol_in * sol_usd / t["liq"] * 100
            out = sol_in / t["price"] * (1 - impact / 100) * 0.997 * 1e6
        else:
            tokens = amount_raw / 1e6
            impact = tokens * t["price"] * sol_usd / t["liq"] * 100
            out = tokens * t["price"] * (1 - impact / 100) * 0.997 * LAMPORTS
        return {"outAmount": str(int(max(out, 0))), "priceImpactPct": str(impact / 100)}

    @staticmethod
    def impact_pct(q):
        return abs(float(q.get("priceImpactPct") or 0)) * 100


def apply_sim_overrides(cfg) -> None:
    """Kürzere Zeiten, damit in der Demo schnell Trades entstehen."""
    cfg["_sim"] = True
    cfg["mode"] = "paper"
    cfg["poll_seconds"] = 3
    cfg["scout"]["use_pumpportal"] = False
    cfg["scout"]["recheck_seconds"] = 3
    cfg["exit"]["max_hold_min"] = 4
    cfg["risk"]["cooldown_hours"] = 0.05
    cfg["db_path"] = cfg["db_path"].replace(".db", "-sim.db")
