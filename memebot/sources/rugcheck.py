"""RugCheck: Sicherheitsbericht eines Tokens (kostenlos)."""
from __future__ import annotations

from typing import Any

from .http import RateLimitedClient


class RugCheck:
    def __init__(self, base: str = "https://api.rugcheck.xyz/v1", client: RateLimitedClient | None = None):
        self.http = client or RateLimitedClient(base, min_interval_s=0.5)

    async def report(self, mint: str) -> dict[str, Any] | None:
        try:
            return await self.http.get(f"/tokens/{mint}/report")
        except Exception:
            return None


def _addresses(value: Any) -> list[str]:
    """Adressen aus einem Feld holen – RugCheck liefert mal Strings, mal Objekte (z. B. liquidityAAccount)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [v for k, v in value.items() if k in ("pubkey", "address", "owner") and isinstance(v, str)]
    return []


def summarize_report(rep: dict[str, Any]) -> dict[str, Any]:
    """Robuste Auswertung des RugCheck-Berichts (Felder können fehlen oder anders aufgebaut sein)."""
    known = rep.get("knownAccounts") or {}
    skip: set[str] = {addr for addr, info in known.items()
                      if isinstance(info, dict) and str(info.get("type", "")).upper() in {"AMM", "LOCKER", "DEX"}}
    for m in rep.get("markets") or []:
        if not isinstance(m, dict):
            continue
        for k in ("pubkey", "liquidityA", "liquidityB", "liquidityAAccount", "liquidityBAccount"):
            skip.update(_addresses(m.get(k)))

    holders = []
    for h in rep.get("topHolders") or []:
        if not isinstance(h, dict):
            continue
        if set(_addresses(h.get("address")) + _addresses(h.get("owner"))) & skip:
            continue
        holders.append(float(h.get("pct") or 0))
    holders.sort(reverse=True)

    lp_locked = [float((m.get("lp") or {}).get("lpLockedPct") or 0) for m in rep.get("markets") or []]
    risks = [{"name": r.get("name", ""), "level": r.get("level", "")} for r in rep.get("risks") or []]
    token = rep.get("token") or {}
    return {
        "score": float(rep.get("score") or 0),
        "score_normalised": float(rep.get("score_normalised") or 0),
        "mint_authority": rep.get("mintAuthority") or token.get("mintAuthority"),
        "freeze_authority": rep.get("freezeAuthority") or token.get("freezeAuthority"),
        "top10_pct": round(sum(holders[:10]), 2),
        "max_holder_pct": round(holders[0], 2) if holders else 0.0,
        "lp_locked_pct": max(lp_locked) if lp_locked else 0.0,
        "risks": risks,
        "rugged": bool(rep.get("rugged")),
        "decimals": token.get("decimals"),
    }
