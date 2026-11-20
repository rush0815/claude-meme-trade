"""Analyst: wertet abgeschlossene Trades aus und schlägt Einstellungen vor (wendet nichts automatisch an)."""
from __future__ import annotations

import json
import time

from .base import Agent

BUCKETS = [(0, 70), (70, 80), (80, 90), (90, 101)]


def _agg(rows: list[dict]) -> dict:
    n = len(rows)
    pnl = [r["pnl_sol"] or 0 for r in rows]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    return {
        "anzahl": n,
        "gewinner": len(wins),
        "trefferquote": round(len(wins) / n * 100, 1) if n else 0,
        "pnl_sol": round(sum(pnl), 5),
        "avg_pct": round(sum(r["pnl_pct"] or 0 for r in rows) / n, 1) if n else 0,
        "avg_gewinn_sol": round(sum(wins) / len(wins), 5) if wins else 0,
        "avg_verlust_sol": round(sum(losses) / len(losses), 5) if losses else 0,
        # None = nicht berechenbar (keine Verluste oder keine Trades)
        "profit_faktor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
    }


class Analyst(Agent):
    name = "Analyst"

    def stats(self, mode: str | None = None) -> dict:
        mode = mode or self.ctx.mode
        closed = self.db.q("SELECT * FROM positions WHERE mode=? AND status='closed' ORDER BY closed_at", (mode,))
        out = {"gesamt": _agg(closed)}
        if closed:
            out["gesamt"]["bester_pct"] = round(max(r["pnl_pct"] or 0 for r in closed), 1)
            out["gesamt"]["schlechtester_pct"] = round(min(r["pnl_pct"] or 0 for r in closed), 1)
            out["gesamt"]["avg_haltedauer_min"] = round(
                sum((r["closed_at"] - r["opened_at"]) for r in closed) / len(closed) / 60, 1)
        reasons: dict[str, list] = {}
        for r in closed:
            key = (r["exit_reason"] or "?").split(" (")[0]
            reasons.setdefault(key, []).append(r)
        out["nach_ausstieg"] = {k: _agg(v) for k, v in sorted(reasons.items())}
        out["nach_score"] = {f"{lo}–{min(hi, 100)}": _agg([r for r in closed if lo <= (r["score"] or 0) < hi])
                             for lo, hi in BUCKETS}
        def ki(r):
            try:
                return json.loads(r["score_details"] or "{}").get("ki_score")
            except ValueError:
                return None
        ki_buckets = {"ohne KI": [r for r in closed if ki(r) is None]}
        for lo, hi in ((0, 40), (40, 60), (60, 80), (80, 101)):
            ki_buckets[f"KI {lo}–{min(hi, 100)}"] = [r for r in closed if ki(r) is not None and lo <= ki(r) < hi]
        out["nach_ki_score"] = {k: _agg(v) for k, v in ki_buckets.items()}
        eq = self.db.q("SELECT equity_sol FROM equity ORDER BY ts")
        peak, mdd = 0.0, 0.0
        for row in eq:
            peak = max(peak, row["equity_sol"])
            if peak > 0:
                mdd = max(mdd, 1 - row["equity_sol"] / peak)
        out["gesamt"]["max_drawdown_pct"] = round(mdd * 100, 1)
        day = time.time() - 86400
        out["letzte_24h"] = _agg([r for r in closed if r["closed_at"] >= day])
        out["vorschlaege"] = self.suggestions(out)
        return out

    def suggestions(self, s: dict) -> list[str]:
        g = s["gesamt"]
        n = g["anzahl"]
        tips: list[str] = []
        if n < 20:
            return [f"Noch zu wenig Daten ({n} Trades). Aussagekräftig wird es ab ca. 30–50 Trades."]
        pf = g["profit_faktor"]
        if pf is not None and pf < 1:
            tips.append(f"Profit-Faktor {pf} < 1: Die Strategie verliert gerade Geld. Nicht live schalten.")
        elif pf is not None and pf >= 1.3 and n >= 50:
            tips.append(f"Profit-Faktor {pf} über {n} Trades: Kandidat für einen vorsichtigen Live-Test mit kleiner Wallet.")
        sl = s["nach_ausstieg"].get("Stop-Loss", {}).get("anzahl", 0)
        if sl / n > 0.55:
            tips.append("Über die Hälfte der Trades endet im Stop-Loss: Einstieg strenger machen – unter Konfiguration "
                        "„Kaufschwelle (Score)“ oder „Mindest-Verhältnis Käufe/Verkäufe (5 min)“ erhöhen bzw. "
                        "„FOMO-Grenze: max. Kursanstieg in 5 min (%)“ senken.")
        tl = s["nach_ausstieg"].get("Zeitlimit")
        if tl and tl["anzahl"] >= 5 and tl["avg_pct"] < 0:
            tips.append("Trades, die ins Zeitlimit laufen, verlieren im Schnitt: unter Konfiguration "
                        "„Zeitlimit in Minuten“ verkürzen.")
        tr = s["nach_ausstieg"].get("Trailing-Stop")
        if tr and tr["anzahl"] >= 5 and tr["avg_pct"] < 10:
            tips.append("Trailing-Stops sichern kaum Gewinn: unter Konfiguration „Trailing-Stop: Abstand vom Hoch in %“ "
                        "etwas größer stellen oder „Trailing-Stop aktiv ab Gewinn in %“ erhöhen.")
        buckets = [(k, v) for k, v in s["nach_score"].items() if v["anzahl"] >= 8]
        for i, (k, v) in enumerate(buckets[:-1]):
            better = [b for _, b in buckets[i + 1:] if b["pnl_sol"] > 0]
            if v["pnl_sol"] < 0 and better:
                tips.append(f"Score-Bereich {k} verliert, höhere Scores gewinnen: unter Konfiguration "
                            f"„Kaufschwelle (Score)“ auf {k.split('–')[1]} anheben.")
                break
        return tips or ["Keine Auffälligkeiten – weiter Daten sammeln."]
