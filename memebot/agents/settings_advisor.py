"""Einstellungs-Berater: Claude schlägt anhand der gesammelten Statistik neue Einstellungen vor.

* Nur auf Knopfdruck und erst ab genug abgeschlossenen Trades (Standard 30).
* An die API gehen nur zusammengefasste Kennzahlen und die aktuellen Einstellungen – keine Wallet, keine Keys.
* Nichts wird automatisch übernommen: der Nutzer wählt Zeilen aus, die Werte laufen durch dieselbe
  Prüfung wie manuelle Änderungen.
* Jeder Vorschlag und jede Übernahme wird gespeichert, damit sich später zeigt, ob es besser wurde.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .. import overrides
from .ai_analyst import parse_json, settings as ai_settings
from .base import Agent

MIN_TRADES = 30


def _kpis(rows: list[dict]) -> dict:
    n = len(rows)
    pnl = [r["pnl_sol"] or 0 for r in rows]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    return {
        "trades": n,
        "winrate_pct": round(len(wins) / n * 100, 1) if n else None,
        "avg_pnl_pct": round(sum(r["pnl_pct"] or 0 for r in rows) / n, 2) if n else None,
        "pnl_sol": round(sum(pnl), 5),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
    }


class SettingsAdvisor(Agent):
    name = "Einstellungs-Berater"

    def __init__(self, ctx, ai):
        super().__init__(ctx)
        self.ai = ai                      # nutzt API-Aufruf und Kostenprotokoll des KI-Analysten

    def min_trades(self) -> int:
        return int((self.cfg.get("advisor") or {}).get("min_trades", MIN_TRADES))

    def closed(self, since: float = 0, until: float | None = None) -> list[dict]:
        return self.db.q("SELECT * FROM positions WHERE mode=? AND status='closed' AND closed_at>=? AND closed_at<=?"
                         " ORDER BY closed_at", (self.ctx.mode, since, until or time.time() + 1))

    # -------------------------------------------------------------- Eingabe für die KI
    def gather(self, analyst) -> dict:
        st = analyst.stats()
        closed = self.closed()
        by_hour: dict[int, list] = {}
        for r in closed:
            by_hour.setdefault(time.gmtime(r["opened_at"]).tm_hour, []).append(r)
        funnel: dict[str, int] = {}
        import re
        for r in self.db.q("SELECT reason FROM candidates WHERE COALESCE(last_checked, first_seen) >= ?",
                           (time.time() - 86400,)):
            key = re.sub(r"[-+]?\$?[\d.,]+\s*(%|h|min|SOL)?", "…", r["reason"] or "?").strip()
            funnel[key] = funnel.get(key, 0) + 1
        cur = overrides.current(self.cfg)
        editable = {k: {"label": overrides.LABELS_EN.get(v[3], v[3]), "min": v[1], "max": v[2], "current": cur[k]}
                    for k, v in overrides.EDITABLE.items()}
        editable.update({k: {"label": overrides.LABELS_EN.get(lbl, lbl), "options": list(opts), "current": cur[k]}
                         for k, (opts, lbl) in overrides.CHOICES.items()})
        return {
            "overall": st["gesamt"], "last_24h": st["letzte_24h"],
            "by_exit_reason": st["nach_ausstieg"], "by_entry_score": st["nach_score"],
            "by_ai_score": st.get("nach_ki_score", {}),
            "by_entry_hour_utc": {f"{h:02d}": _kpis(v) for h, v in sorted(by_hour.items())},
            "candidate_rejections_24h": dict(sorted(funnel.items(), key=lambda kv: -kv[1])[:15]),
            "editable_settings": editable,
            "fixed_rules": "Entries only 15 min–8 h after launch; rug checks (mint/freeze revoked, LP locked, holder "
                           "concentration); phase check rejects coins far below their high. Paper trading with real quotes.",
        }

    # -------------------------------------------------------------- Vorschlag holen
    async def suggest(self, analyst, model: str = "claude-sonnet-5-5", force: bool = False) -> dict:
        n = len(self.closed())
        if n < self.min_trades() and not force:
            raise ValueError(f"erst {n} abgeschlossene Trades – Vorschläge ab {self.min_trades()}")
        # Temperatur 0: gleiche Daten -> möglichst gleicher Vorschlag
        cfg = {**ai_settings(self.db), "model": model, "max_tokens": 2500, "temperature": 0}
        if not cfg["api_key"]:
            raise ValueError("kein Anthropic-API-Key gesetzt (Reiter KI-Analyst)")
        data = self.gather(analyst)
        low_data = n < self.min_trades()
        if low_data:
            data["warning"] = (f"Only {n} closed trades (recommended minimum {self.min_trades()}). Statistics are "
                               "unreliable: suggest at most 1–2 cautious changes or none, and set confidence to low.")
        system = ("You are a careful quantitative trading coach for a Solana meme-coin bot. You receive aggregated "
                  "paper-trading statistics and the bot's adjustable settings. Suggest a FEW changes (0–5) that are "
                  "clearly supported by the data; prefer no change over guessing. Small samples (<10 trades in a "
                  "group) are noise. Every value must be inside the given min/max. Answer with ONE JSON object only.")
        user = ("Statistics and settings:\n" + json.dumps(data, ensure_ascii=False) + "\n\nReturn JSON exactly like:\n"
                '{"confidence": "low|medium|high", "summary_de": "<2-3 Sätze>", "summary_en": "<2-3 sentences>", '
                '"changes": [{"key": "<setting key>", "value": <new value>, "reason_de": "<1 Satz>", '
                '"reason_en": "<1 sentence>"}], "watch_de": "<worauf achten>", "watch_en": "<what to watch>"}')
        try:
            text, usage = await self.ai._call(cfg, system, user, [])
        except Exception as e:
            self.ai._record(None, model, {}, ok=False, error=str(e), label="Einstellungs-Vorschlag")
            raise RuntimeError(f"KI nicht erreichbar: {e}") from None
        res = parse_json(text, key="changes") if text else None
        cost = self.ai._record(None, model, usage, verdict="Einstellungs-Vorschlag", ok=bool(res),
                               error="" if res else "Antwort ohne JSON", label="Einstellungs-Vorschlag")
        if not res:
            raise RuntimeError("KI-Antwort nicht lesbar")
        cur = overrides.current(self.cfg)
        changes = []
        for ch in res.get("changes") or []:
            key = ch.get("key")
            try:
                val = overrides.validate({key: ch.get("value")})[key]
            except (ValueError, TypeError, KeyError):
                continue                                   # unbekannt oder außerhalb des Bereichs -> verwerfen
            if val == cur.get(key):
                continue
            label = overrides.CHOICES[key][1] if key in overrides.CHOICES else overrides.EDITABLE[key][3]
            changes.append({"key": key, "label": label, "label_en": overrides.LABELS_EN.get(label, label),
                            "current": cur.get(key), "value": val,
                            "reason_de": str(ch.get("reason_de") or "")[:300],
                            "reason_en": str(ch.get("reason_en") or "")[:300]})
        res["changes"] = changes
        res["confidence"] = res.get("confidence") if res.get("confidence") in ("low", "medium", "high") else "low"
        if low_data:
            res["confidence"] = "low"                      # bei zu wenig Daten nie mehr als "niedrig"
            res["low_data"] = True
        rid = self.db.x("INSERT INTO advisor_runs(ts,model,trades,baseline,response,cost_usd) VALUES(?,?,?,?,?,?)",
                        (time.time(), model, n, json.dumps(data["overall"]), json.dumps(res), cost))
        self.log(f"Einstellungs-Vorschlag #{rid}: {len(changes)} Änderung(en), Vertrauen {res['confidence']} "
                 f"({cost * 100:.2f} ct)")
        return {"id": rid, "trades": n, "cost_usd": cost, "model": model, **res}

    # -------------------------------------------------------------- Übernehmen + Verlauf
    def apply(self, run_id: int, keys: list[str]) -> dict:
        run = self.db.one("SELECT * FROM advisor_runs WHERE id=?", (run_id,))
        if not run:
            raise ValueError("Vorschlag nicht gefunden")
        res = json.loads(run["response"] or "{}")
        chosen = [c for c in res.get("changes", []) if c["key"] in keys]
        if not chosen:
            raise ValueError("nichts ausgewählt")
        before = overrides.current(self.cfg)
        overrides.save(self.cfg, self.db, {c["key"]: c["value"] for c in chosen})
        applied = [{"key": c["key"], "label": c["label"], "label_en": c.get("label_en"), "old": before.get(c["key"]),
                    "new": c["value"]} for c in chosen]
        prev = [a for a in json.loads(run["applied"] or "[]") if a["key"] not in keys]
        self.db.x("UPDATE advisor_runs SET applied=?, applied_at=?, reverted=NULL, reverted_at=NULL WHERE id=?",
                  (json.dumps(prev + applied), run["applied_at"] if prev else time.time(), run_id))
        self.log(f"Einstellungs-Vorschlag #{run_id} übernommen: " +
                 ", ".join(f"{a['label']} {a['old']} → {a['new']}" for a in applied))
        return {"ok": True, "applied": applied}

    def revert(self, run_id: int) -> dict:
        """Übernommene Änderungen eines Vorschlags rückgängig machen (alte Werte wiederherstellen)."""
        run = self.db.one("SELECT * FROM advisor_runs WHERE id=?", (run_id,))
        applied = json.loads((run or {}).get("applied") or "[]")
        if not run or not applied or run.get("reverted_at"):
            raise ValueError("nichts zum Rückgängigmachen")
        now = overrides.current(self.cfg)
        overrides.save(self.cfg, self.db, {a["key"]: a["old"] for a in applied})
        reverted = [{**a, "was": now.get(a["key"])} for a in applied]
        self.db.x("UPDATE advisor_runs SET reverted=?, reverted_at=? WHERE id=?", (json.dumps(reverted), time.time(), run_id))
        self.log(f"Einstellungs-Vorschlag #{run_id} rückgängig gemacht: " +
                 ", ".join(f"{a['label']} {a['new']} → {a['old']}" for a in applied))
        return {"ok": True, "reverted": reverted}

    def history(self) -> list[dict[str, Any]]:
        runs = self.db.q("SELECT * FROM advisor_runs ORDER BY id DESC LIMIT 30")
        out = []
        for r in runs:
            res = json.loads(r["response"] or "{}")
            item = {"id": r["id"], "ts": r["ts"], "model": r["model"], "trades": r["trades"], "cost_usd": r["cost_usd"],
                    "confidence": res.get("confidence"), "summary_de": res.get("summary_de"),
                    "summary_en": res.get("summary_en"), "changes": res.get("changes", []),
                    "applied": json.loads(r["applied"] or "[]"), "applied_at": r["applied_at"],
                    "reverted_at": r.get("reverted_at"), "watch_de": res.get("watch_de"), "watch_en": res.get("watch_en"),
                    "low_data": res.get("low_data", False)}
            if r["applied_at"]:
                # Vorher: Trades vor der Übernahme (max. 50) – nachher: bis heute bzw. bis zum Rückgängigmachen
                after = self.closed(since=r["applied_at"], until=r.get("reverted_at"))
                before = self.closed(until=r["applied_at"])[-max(len(after), 20):][-50:]
                item["before"], item["after"] = _kpis(before), _kpis(after)
            out.append(item)
        return out
