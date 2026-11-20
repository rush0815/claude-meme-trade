"""Datenmodelle, die zwischen den Agenten ausgetauscht werden."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000


@dataclass
class Market:
    """Marktdaten eines Tokens (bestes SOL-Paar laut DexScreener)."""
    mint: str
    symbol: str = ""
    name: str = ""
    pair_address: str = ""
    dex: str = ""
    price_usd: float = 0.0
    price_sol: float = 0.0            # SOL pro Token
    liquidity_usd: float = 0.0
    fdv: float = 0.0
    created_at: float = 0.0           # Unix-Sekunden
    change_m5: float = 0.0
    change_h1: float = 0.0
    change_h6: float = 0.0
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    volume_m5: float = 0.0
    volume_h1: float = 0.0
    volume_h6: float = 0.0

    @property
    def age_min(self) -> float:
        return (time.time() - self.created_at) / 60 if self.created_at else 0.0

    @classmethod
    def from_dexscreener(cls, p: dict[str, Any]) -> "Market":
        g = lambda d, k: (p.get(d) or {}).get(k) or 0  # noqa: E731
        txm5 = (p.get("txns") or {}).get("m5") or {}
        txh1 = (p.get("txns") or {}).get("h1") or {}
        base = p.get("baseToken") or {}
        return cls(
            mint=base.get("address", ""),
            symbol=base.get("symbol", "") or "",
            name=base.get("name", "") or "",
            pair_address=p.get("pairAddress", "") or "",
            dex=p.get("dexId", "") or "",
            price_usd=float(p.get("priceUsd") or 0),
            price_sol=float(p.get("priceNative") or 0),
            liquidity_usd=float(g("liquidity", "usd")),
            fdv=float(p.get("fdv") or p.get("marketCap") or 0),
            created_at=float(p.get("pairCreatedAt") or 0) / 1000,
            change_m5=float(g("priceChange", "m5")),
            change_h1=float(g("priceChange", "h1")),
            change_h6=float(g("priceChange", "h6")),
            buys_m5=int(txm5.get("buys") or 0),
            sells_m5=int(txm5.get("sells") or 0),
            buys_h1=int(txh1.get("buys") or 0),
            sells_h1=int(txh1.get("sells") or 0),
            volume_m5=float(g("volume", "m5")),
            volume_h1=float(g("volume", "h1")),
            volume_h6=float(g("volume", "h6")),
        )


@dataclass
class Verdict:
    """Ergebnis eines Agenten: ok/abgelehnt + Begründung + Details."""
    ok: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    retry: bool = False               # True = später erneut prüfen (z. B. noch zu jung)


@dataclass
class Signal:
    market: Market
    score: float
    details: dict[str, Any]
    security: dict[str, Any]


@dataclass
class Fill:
    sol_amount: float                 # bei Kauf: ausgegebene SOL, bei Verkauf: erhaltene SOL (netto)
    token_amount: float
    price: float                      # SOL pro Token
    fee_sol: float = 0.0
    signature: str | None = None
