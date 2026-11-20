"""Einstellungen, die über die Web-Oberfläche geändert werden können.

Sie werden in der Datenbank gespeichert, beim Start über die config.toml gelegt
und gelten sofort – auch für bereits offene Positionen.
"""
from __future__ import annotations

from typing import Any

# Schlüssel -> (Typ, min, max, Beschreibung)
CHOICES: dict[str, tuple[dict[str, str], str]] = {
    "exit.mode": ({"ladder": "Leiter (Break-even, 2 TPs, Trailing)", "simple": "Einfach (ein TP + SL)"},
                  "Ausstiegs-Modus"),
}

EDITABLE: dict[str, tuple[type, float, float, str]] = {
    "exit.stop_loss_pct": (float, 3, 90, "Stop-Loss in %"),
    "exit.simple_take_profit_pct": (float, 1, 1000, "Einfach-Modus: Take-Profit in % (alles verkaufen)"),
    "exit.breakeven_activate_pct": (float, 0, 1000, "SL über Entry ziehen, sobald Gewinn erreicht in % (0 = aus)"),
    "exit.breakeven_floor_pct": (float, -10, 100, "Neuer SL: Prozent über Entry"),
    "exit.tp1_pct": (float, 5, 1000, "Take-Profit 1: Gewinn in %"),
    "exit.tp1_fraction": (float, 0, 1, "Take-Profit 1: Anteil verkaufen (0–1)"),
    "exit.tp2_pct": (float, 5, 5000, "Take-Profit 2: Gewinn in %"),
    "exit.tp2_fraction": (float, 0, 1, "Take-Profit 2: Anteil verkaufen (0–1)"),
    "exit.trailing_activate_pct": (float, 1, 1000, "Trailing-Stop aktiv ab Gewinn in %"),
    "exit.trailing_stop_pct": (float, 2, 90, "Trailing-Stop: Abstand vom Hoch in %"),
    "exit.max_hold_min": (float, 1, 1440, "Zeitlimit in Minuten"),
    "strategy.min_score": (float, 0, 100, "Kaufschwelle (Score)"),
    "scout.max_age_min": (float, 30, 2880, "Coins höchstens so alt (Minuten)"),
    "scout.recheck_seconds": (float, 5, 600, "Watchlist: Coin erneut prüfen nach (Sekunden)"),
    "bot.poll_seconds": (float, 2, 120, "Hauptzyklus (Sekunden)"),
    "risk.position_pct": (float, 0.005, 0.25, "Positionsgröße (Anteil vom Kapital)"),
    "risk.max_open_positions": (int, 1, 20, "max. offene Positionen"),
}


def current(cfg) -> dict[str, Any]:
    e = cfg["exit"]
    lv = e.get("take_profit_levels") or []
    tp = lambda i, j, d: lv[i][j] if len(lv) > i else d  # noqa: E731
    return {
        "exit.mode": e.get("mode", "ladder"),
        "exit.simple_take_profit_pct": e.get("simple_take_profit_pct", 30),
        "exit.stop_loss_pct": e["stop_loss_pct"],
        "exit.breakeven_activate_pct": e.get("breakeven_activate_pct", 0),
        "exit.breakeven_floor_pct": e.get("breakeven_floor_pct", 3),
        "exit.tp1_pct": tp(0, 0, 40), "exit.tp1_fraction": tp(0, 1, 0.33),
        "exit.tp2_pct": tp(1, 0, 100), "exit.tp2_fraction": tp(1, 1, 0.33),
        "exit.trailing_activate_pct": e["trailing_activate_pct"],
        "exit.trailing_stop_pct": e["trailing_stop_pct"],
        "exit.max_hold_min": e["max_hold_min"],
        "strategy.min_score": cfg["strategy"]["min_score"],
        "scout.max_age_min": cfg["scout"]["max_age_min"],
        "scout.recheck_seconds": cfg["scout"]["recheck_seconds"],
        "bot.poll_seconds": cfg["poll_seconds"],
        "risk.position_pct": cfg["risk"]["position_pct"],
        "risk.max_open_positions": cfg["risk"]["max_open_positions"],
    }


def validate(values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in values.items():
        if k in CHOICES:
            opts, label = CHOICES[k]
            if v not in opts:
                raise ValueError(f"{label}: erlaubt {', '.join(opts)}")
            out[k] = v
            continue
        if k not in EDITABLE:
            raise ValueError(f"unbekannte Einstellung: {k}")
        typ, lo, hi, label = EDITABLE[k]
        try:
            v = typ(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"{label}: keine Zahl") from None
        if not lo <= v <= hi:
            raise ValueError(f"{label}: erlaubt {lo}–{hi}")
        out[k] = v
    return out


def apply(cfg, values: dict[str, Any]) -> None:
    cur = current(cfg)
    cur.update(values)
    for k, v in values.items():
        sec, key = k.split(".", 1)
        if sec == "bot":
            cfg[key] = v
        elif not key.startswith("tp"):
            cfg[sec][key] = v
    levels = [[cur["exit.tp1_pct"], cur["exit.tp1_fraction"]], [cur["exit.tp2_pct"], cur["exit.tp2_fraction"]]]
    cfg["exit"]["take_profit_levels"] = [lv for lv in sorted(levels) if lv[1] > 0]


# Einmalige Umstellung auf die stimmige Ausstiegs-Leiter (v2). Wird beim ersten Start nach dem
# Update in die gespeicherten Einstellungen geschrieben und hat damit Vorrang vor alten Werten
# in der config.toml. Danach lassen sich die Werte wie gewohnt in der Web-UI ändern.
EXIT_V2 = {
    "exit.stop_loss_pct": 20, "exit.breakeven_activate_pct": 25, "exit.breakeven_floor_pct": 3,
    "exit.tp1_pct": 30, "exit.tp1_fraction": 0.33, "exit.tp2_pct": 80, "exit.tp2_fraction": 0.33,
    "exit.trailing_activate_pct": 50, "exit.trailing_stop_pct": 20, "exit.max_hold_min": 120,
}


# Einmalig: schnellere Watchlist-Takte (alte config.toml hatte 60 s / 15 s / 24 h)
TIMING_V3 = {"scout.recheck_seconds": 30, "bot.poll_seconds": 5, "scout.max_age_min": 480}


def load_into(cfg, db) -> None:
    if not db.get("migration_timing_v3"):
        db.set("overrides", {**(db.get("overrides") or {}), **TIMING_V3})
        db.set("migration_timing_v3", True)
        db.log("Orchestrator", "Takte umgestellt: Watchlist alle 30 s, Hauptzyklus 5 s, max. Alter 8 h")
    if not db.get("migration_exit_v2"):
        db.set("overrides", {**(db.get("overrides") or {}), **EXIT_V2})
        db.set("migration_exit_v2", True)
        db.log("Orchestrator", "Ausstiegsregeln auf v2 umgestellt (Break-even ab +25 %, TP1 +30 %, "
                               "Trailing ab +50 %, TP2 +80 %, Zeitlimit 2 h)")
    saved = db.get("overrides") or {}
    if saved:
        try:
            apply(cfg, validate(saved))
        except ValueError:
            pass


def save(cfg, db, values: dict[str, Any]) -> dict[str, Any]:
    clean = validate({**(db.get("overrides") or {}), **values})
    check = {**current(cfg), **clean}
    if check["exit.tp1_fraction"] + check["exit.tp2_fraction"] > 1:
        raise ValueError("Take-Profit-Anteile zusammen höchstens 1")
    apply(cfg, clean)
    db.set("overrides", clean)
    return current(cfg)
