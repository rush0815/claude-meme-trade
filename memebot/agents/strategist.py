"""Stratege: Filter und Bewertung (0–100) für Momentum nach der Graduation.

Idee der Strategie
------------------
* Nur Coins, die die ersten 15 Minuten überlebt haben (Sniper- und Rug-Phase vorbei).
* Einstieg nur bei echtem, *frischem* Kaufdruck: mehr Käufe als Verkäufe, Volumen
  beschleunigt sich gegenüber der letzten Stunde, Kurs steigt – aber noch nicht parabolisch.
* Keine FOMO-Spitzen kaufen (5-Min-Anstieg > max_change_m5_pct).
* Gesunde Liquidität im Verhältnis zur Bewertung, damit Ausstiege möglich bleiben.
Gewinne entstehen über die Exit-Regeln (Position-Manager): Verluste klein halten,
Teilgewinne sichern, Rest mit Trailing-Stop laufen lassen.
"""
from __future__ import annotations

from ..models import Market, Verdict
from .base import Agent


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


WEIGHTS = {
    "kaufdruck": 25,
    "volumen_beschleunigung": 20,
    "momentum": 20,
    "trend": 15,
    "beteiligung": 10,
    "liquiditaet": 10,
}


class Strategist(Agent):
    name = "Stratege"

    def prefilter(self, m: Market) -> Verdict:
        sc, f, st = self.cfg.scout, self.cfg.filters, self.cfg.strategy
        if m.age_min > sc.max_age_min:
            return Verdict(False, f"zu alt ({m.age_min / 60:.0f} h)")
        dead_after = float(sc.get("dead_after_min", 60))
        if m.age_min > dead_after and (m.liquidity_usd < float(sc.get("dead_liquidity_usd", 5000))
                                       or m.volume_h1 < float(sc.get("dead_volume_h1_usd", 2000))):
            return Verdict(False, "eingeschlafen (kaum Liquidität/Volumen)")   # endgültig raus
        if m.age_min < sc.min_age_min:
            return Verdict(False, f"zu jung ({m.age_min:.0f} min)", retry=True)
        if m.price_sol <= 0:
            return Verdict(False, "kein Preis", retry=True)
        if m.liquidity_usd < f.min_liquidity_usd:
            return Verdict(False, f"Liquidität ${m.liquidity_usd:,.0f} zu gering", retry=True)
        if m.liquidity_usd > f.max_liquidity_usd:
            return Verdict(False, "Liquidität zu groß (kein Meme-Momentum)", retry=True)
        if m.fdv > f.max_fdv_usd:
            return Verdict(False, f"Bewertung ${m.fdv:,.0f} zu hoch", retry=True)
        if m.volume_h1 < f.min_volume_h1_usd:
            return Verdict(False, f"Volumen 1h ${m.volume_h1:,.0f} zu gering", retry=True)
        if m.buys_m5 + m.sells_m5 < f.min_txns_m5:
            return Verdict(False, "zu wenig Trades (5 min)", retry=True)
        if m.change_h6 < -float(st.get("max_drop_h6_pct", 35)):
            return Verdict(False, f"Abwärtstrend 6 h ({m.change_h6:.0f}%) – Hype vorbei", retry=True)
        if m.change_h1 < -float(st.get("max_drop_h1_pct", 25)):
            return Verdict(False, f"Abverkauf 1 h ({m.change_h1:.0f}%)", retry=True)
        if m.change_m5 > st.max_change_m5_pct:
            return Verdict(False, f"FOMO-Spitze (+{m.change_m5:.0f}% in 5 min)", retry=True)
        if m.change_m5 < st.min_change_m5_pct:
            return Verdict(False, "kein Momentum", retry=True)
        ratio = m.buys_m5 / max(m.sells_m5, 1)
        if ratio < st.min_buy_sell_ratio_m5:
            return Verdict(False, f"Kauf/Verkauf {ratio:.2f} zu schwach", retry=True)
        return Verdict(True, "Filter bestanden")

    def score(self, m: Market) -> tuple[float, dict]:
        st = self.cfg.strategy
        ratio = m.buys_m5 / max(m.sells_m5, 1)
        accel = (m.volume_m5 * 12) / max(m.volume_h1, 1.0)

        lo, hi = st.min_change_m5_pct, st.max_change_m5_pct
        c = m.change_m5
        if c < lo or c > hi:
            mom = 0.0
        elif c <= 10:
            mom = _clamp((c - lo) / max(10 - lo, 0.1))
        else:
            mom = 1.0 - 0.6 * _clamp((c - 10) / max(hi - 10, 0.1))

        h1 = m.change_h1
        trend = 0.3 if h1 > 300 else _clamp((h1 + 10) / 60)
        if m.change_h6 < -50:
            trend *= 0.5

        parts = {
            "kaufdruck": _clamp((ratio - 1.0) / 1.0),
            "volumen_beschleunigung": _clamp((accel - 0.8) / 1.2),
            "momentum": mom,
            "trend": trend,
            "beteiligung": _clamp((m.buys_m5 - 20) / 80),
            "liquiditaet": _clamp((m.liquidity_usd / max(m.fdv, 1.0) - 0.05) / 0.15),
        }
        total = sum(WEIGHTS[k] * v for k, v in parts.items()) / sum(WEIGHTS.values()) * 100
        details = {k: round(v * 100) for k, v in parts.items()}
        details.update({"kauf_verkauf": round(ratio, 2), "vol_beschl": round(accel, 2),
                        "chg_m5": round(m.change_m5, 1), "chg_h1": round(m.change_h1, 1),
                        "liq_usd": round(m.liquidity_usd), "fdv_usd": round(m.fdv), "alter_min": round(m.age_min)})
        return round(total, 1), details
