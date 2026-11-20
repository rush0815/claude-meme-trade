"""Konfiguration laden: TOML + ${ENV}-Platzhalter."""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")
ROOT = Path(__file__).resolve().parent.parent


def _subst(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_subst(v) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v) for k, v in value.items()}
    return value


class Section(dict):
    """Dict mit Attributzugriff: cfg.risk.position_pct"""

    def __getattr__(self, name: str) -> Any:
        try:
            v = self[name]
        except KeyError as e:
            raise AttributeError(name) from e
        return Section(v) if isinstance(v, dict) and not isinstance(v, Section) else v


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | os.PathLike | None = None) -> Section:
    """Lädt config.example.toml als Standardwerte und legt die eigene config.toml darüber.
    So stehen neue Einstellungen nach einem Update automatisch zur Verfügung."""
    example = ROOT / "config.example.toml"
    p = Path(path or os.environ.get("MEMEBOT_CONFIG") or ROOT / "config.toml")
    if not p.exists():
        p = example
    with open(example, "rb") as f:
        raw = tomllib.load(f)
    if p != example:
        with open(p, "rb") as f:
            raw = _merge(raw, tomllib.load(f))
    cfg = Section(_subst(raw))
    db = Path(cfg["db_path"])
    if not db.is_absolute():
        db = ROOT / db
    cfg["db_path"] = str(db)
    cfg["_source"] = str(p)
    return cfg
