"""Gemeinsamer Kontext aller Agenten."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..config import Section
from ..db import DB

log = logging.getLogger("memebot")


@dataclass
class Ctx:
    cfg: Section
    db: DB
    dex: Any = None          # DexScreener
    rug: Any = None          # RugCheck
    jup: Any = None          # Jupiter
    rpc: Any = None          # SolanaRPC
    gecko: Any = None        # GeckoTerminal

    @property
    def mode(self) -> str:
        return self.cfg.mode


class Agent:
    name = "agent"

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.db = ctx.db

    def log(self, msg: str, level: str = "info", mint: str | None = None) -> None:
        getattr(log, "warning" if level == "warn" else level if level in ("info", "error") else "info")(
            "[%s] %s", self.name, msg)
        self.db.log(self.name, msg, level, mint)
