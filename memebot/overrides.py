"""Einstellungen, die über die Web-Oberfläche geändert werden können.

Sie werden in der Datenbank gespeichert, beim Start über die config.toml gelegt
und gelten sofort – auch für bereits offene Positionen.
"""
from __future__ import annotations

from typing import Any

# Schlüssel -> (Typ, min, max, Beschreibung)
EDITABLE: dict[str, tuple[type, float, float, str]] = {
    "exit.stop_loss_pct": (float, 3, 90, "Stop-Loss in %"),
    "exit.breakeven_activate_pct": (float, 0, 1000, "Break-even-Stop aktiv ab Gewinn in % (0 = aus)"),
    "exit.breakeven_floor_pct": (float, -10, 100, "Break-even-Stop: verkaufen bei Gewinn in %"),
    "exit.tp1_pct": (float, 5, 1000, "Take-Profit 1: Gewinn in %"),
    "exit.tp1_fraction": (float, 0, 1, "Take-Profit 1: Anteil verkaufen (0–1)"),
    "exit.tp2_pct": (float, 5, 5000, "Take-Profit 2: Gewinn in %"),
    "exit.tp2_fraction": (float, 0, 1, "Take-Profit 2: Anteil verkaufen (0–1)"),
    "exit.trailing_activate_pct": (float, 1, 1000, "Trailing-Stop aktiv ab Gewinn in %"),
    "exit.trailing_stop_pct": (float, 2, 90, "Trailing-Stop: Abstand vom Hoch in %"),
    "exit.max_hold_min": (float, 1, 1440, "Zeitlimit in Minuten"),
    "strategy.min_score": (float, 0, 100, "Kaufschwelle (Score)"),
    "risk.position_pct": (float, 0.005, 0.25, "Positionsgröße (Anteil vom Kapital)"),
    "risk.max_open_positions": (int, 1, 20, "max. offene Positionen"),
}


def current(cfg) -> dict[str, Any]:
    e = cfg["exit"]
    lv = e.get("take_profit_levels") or []
    tp = lambda i, j, d: lv[i][j] if len(lv) > i else d  # noqa: E731
    return {
        "exit.stop_loss_pct": e["stop_loss_pct"],
        "exit.breakeven_activate_pct": e.get("breakeven_activate_pct", 0),
        "exit.breakeven_floor_pct": e.get("breakeven_floor_pct", 3),
        "exit.tp1_pct": tp(0, 0, 40), "exit.tp1_fraction": tp(0, 1, 0.33),
        "exit.tp2_pct": tp(1, 0, 100), "exit.tp2_fraction": tp(1, 1, 0.33),
        "exit.trailing_activate_pct": e["trailing_activate_pct"],
        "exit.trailing_stop_pct": e["trailing_stop_pct"],
        "exit.max_hold_min": e["max_hold_min"],
        "strategy.min_score": cfg["strategy"]["min_score"],
        "risk.position_pct": cfg["risk"]["position_pct"],
        "risk.max_open_positions": cfg["risk"]["max_open_positions"],
    }


def validate(values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in values.items():
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
        if not key.startswith("tp"):
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


def load_into(cfg, db) -> None:
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
