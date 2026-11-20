"""Rug-Analyst: Sicherheitsprüfung (RugCheck) und Verkaufstest (Honeypot-Schutz)."""
from __future__ import annotations

import time

from ..models import LAMPORTS, SOL_MINT, Market, Verdict
from ..sources.rugcheck import summarize_report
from .base import Agent


class RugAnalyst(Agent):
    name = "Rug-Analyst"
    CACHE_S = 20 * 60

    def __init__(self, ctx):
        super().__init__(ctx)
        self._cache: dict[str, tuple[float, Verdict]] = {}

    def evaluate(self, s: dict) -> Verdict:
        sec = self.cfg.security
        if s["rugged"]:
            return Verdict(False, "als rugged markiert", s)
        if sec.require_mint_revoked and s["mint_authority"]:
            return Verdict(False, "Mint-Authority aktiv (Dev kann nachdrucken)", s)
        if sec.require_freeze_revoked and s["freeze_authority"]:
            return Verdict(False, "Freeze-Authority aktiv (Dev kann sperren)", s)
        if s["lp_locked_pct"] < sec.min_lp_locked_pct:
            return Verdict(False, f"Liquidität nur {s['lp_locked_pct']:.0f}% gelockt", s)
        if s["top10_pct"] > sec.max_top10_holder_pct:
            return Verdict(False, f"Top-10-Holder halten {s['top10_pct']:.0f}%", s)
        if s["max_holder_pct"] > sec.max_single_holder_pct:
            return Verdict(False, f"Ein Holder hält {s['max_holder_pct']:.0f}%", s)
        bad = [r["name"] for r in s["risks"] if str(r["level"]).lower() in sec.reject_risk_levels]
        if bad:
            return Verdict(False, "Risiken: " + ", ".join(bad[:3]), s)
        if s["score"] > sec.max_rugcheck_score:
            return Verdict(False, f"RugCheck-Score {s['score']:.0f} zu hoch", s)
        return Verdict(True, "Sicherheitsprüfung bestanden", s)

    async def check(self, m: Market) -> Verdict:
        hit = self._cache.get(m.mint)
        if hit and time.time() - hit[0] < self.CACHE_S:
            return hit[1]
        rep = await self.ctx.rug.report(m.mint)
        if rep is None:
            return Verdict(False, "RugCheck nicht erreichbar", retry=True)
        v = self.evaluate(summarize_report(rep))
        self._cache[m.mint] = (time.time(), v)
        if not v.ok:
            self.log(f"{m.symbol}: abgelehnt – {v.reason}", mint=m.mint)
        return v

    async def sell_test(self, m: Market, size_sol: float, decimals: int) -> Verdict:
        """Prüft per Jupiter-Quote, ob sich die Position wieder verkaufen lässt."""
        if m.price_sol <= 0:
            return Verdict(False, "kein Preis")
        tokens_raw = int(size_sol / m.price_sol * 10 ** decimals)
        q = await self.ctx.jup.quote(m.mint, SOL_MINT, tokens_raw, self.cfg.execution.slippage_bps)
        if q is None:
            return Verdict(False, "Verkaufstest: keine Route (möglicher Honeypot)", retry=True)
        impact = self.ctx.jup.impact_pct(q)
        out_sol = int(q["outAmount"]) / LAMPORTS
        if impact > self.cfg.security.max_sell_impact_pct:
            return Verdict(False, f"Verkaufstest: Preiseinfluss {impact:.1f}%", {"impact": impact})
        if out_sol < size_sol * 0.7:
            return Verdict(False, f"Verkaufstest: nur {out_sol:.4f} SOL zurück (Steuer/Honeypot?)",
                           {"out_sol": out_sol})
        return Verdict(True, "Verkaufstest ok", {"impact": impact, "out_sol": out_sol})
