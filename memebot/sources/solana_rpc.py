"""Minimaler Solana-JSON-RPC-Client (Helius o. ä.)."""
from __future__ import annotations

import asyncio
import base64
import itertools
import time
from typing import Any

from ..models import LAMPORTS
from .http import RateLimitedClient


class SolanaRPC:
    def __init__(self, url: str, client: RateLimitedClient | None = None):
        self.http = client or RateLimitedClient(url, min_interval_s=0.12)
        self._ids = itertools.count(1)
        self._decimals: dict[str, int] = {}

    async def call(self, method: str, params: list | None = None) -> Any:
        res = await self.http.post("", json={"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                                             "params": params or []})
        if "error" in res:
            raise RuntimeError(f"RPC {method}: {res['error']}")
        return res.get("result")

    async def sol_balance(self, pubkey: str) -> float:
        r = await self.call("getBalance", [pubkey, {"commitment": "confirmed"}])
        return int(r["value"]) / LAMPORTS

    async def decimals(self, mint: str) -> int:
        if mint not in self._decimals:
            r = await self.call("getTokenSupply", [mint])
            self._decimals[mint] = int(r["value"]["decimals"])
        return self._decimals[mint]

    async def token_balance_raw(self, owner: str, mint: str) -> int:
        r = await self.call("getTokenAccountsByOwner",
                            [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        total = 0
        for acc in (r or {}).get("value", []):
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            total += int(info["amount"])
        return total

    async def send_raw(self, tx_bytes: bytes) -> str:
        return await self.call("sendTransaction", [base64.b64encode(tx_bytes).decode(),
                                                   {"encoding": "base64", "skipPreflight": True, "maxRetries": 3}])

    async def confirm(self, sig: str, timeout_s: float = 60) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            r = await self.call("getSignatureStatuses", [[sig], {"searchTransactionHistory": False}])
            st = (r or {}).get("value", [None])[0]
            if st:
                if st.get("err"):
                    raise RuntimeError(f"Transaktion fehlgeschlagen: {st['err']}")
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return True
            await asyncio.sleep(1.5)
        return False
