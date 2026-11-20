"""Jupiter Swap API (Quote + Swap-Transaktion)."""
from __future__ import annotations

from typing import Any

from .http import RateLimitedClient


class Jupiter:
    def __init__(self, base: str = "https://api.jup.ag/swap/v1", api_key: str = "",
                 min_interval_s: float = 1.1, client: RateLimitedClient | None = None):
        headers = {"x-api-key": api_key} if api_key else {}
        if not api_key:
            min_interval_s = max(min_interval_s, 2.1)  # keyless: 0,5 RPS
        self.http = client or RateLimitedClient(base, min_interval_s=min_interval_s, headers=headers)

    async def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict[str, Any] | None:
        if amount_raw <= 0:
            return None
        try:
            q = await self.http.get("/quote", params={
                "inputMint": input_mint, "outputMint": output_mint, "amount": str(int(amount_raw)),
                "slippageBps": str(int(slippage_bps)), "restrictIntermediateTokens": "true",
            })
        except Exception:
            return None
        if not q or "outAmount" not in q:
            return None
        return q

    @staticmethod
    def impact_pct(quote: dict[str, Any]) -> float:
        try:
            return abs(float(quote.get("priceImpactPct") or 0)) * 100
        except (TypeError, ValueError):
            return 0.0

    async def swap_tx(self, quote: dict[str, Any], user_pubkey: str, max_priority_lamports: int) -> str:
        res = await self.http.post("/swap", json={
            "quoteResponse": quote,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "dynamicSlippage": True,
            "prioritizationFeeLamports": {
                "priorityLevelWithMaxLamports": {"maxLamports": int(max_priority_lamports), "priorityLevel": "veryHigh"}
            },
        }, retries=2)
        tx = (res or {}).get("swapTransaction")
        if not tx:
            raise RuntimeError(f"Jupiter /swap ohne Transaktion: {res}")
        return tx
