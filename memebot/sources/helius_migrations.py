"""Graduations direkt aus der Blockchain (Helius-WebSocket, ohne Drittanbieter).

logsSubscribe auf das pump.fun-Migrationskonto. Bei einer Migration wird die Transaktion
einmal per getTransaction gelesen, um die Token-Mint zu bestimmen (wenige Credits pro Tag).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from typing import Any, Awaitable, Callable

import websockets

from ..models import SOL_MINT

log = logging.getLogger("memebot.helius")
MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"


def ws_url(rpc_url: str) -> str:
    return rpc_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def is_migration(logs: list[str]) -> bool:
    return any("Instruction: Migrate" in line for line in logs or [])


def mint_from_tx(tx: dict[str, Any] | None) -> str | None:
    """Token-Mint aus einer Migrations-Transaktion: häufigste Nicht-SOL-Mint in den Token-Salden."""
    if not tx:
        return None
    meta = tx.get("meta") or {}
    mints = [b.get("mint") for b in (meta.get("postTokenBalances") or []) + (meta.get("preTokenBalances") or [])]
    mints = [m for m in mints if m and m != SOL_MINT]
    if not mints:
        return None
    counts = Counter(mints)
    pump = [m for m in counts if m.endswith("pump")]
    return max(pump or counts, key=lambda m: counts[m])


async def _subscribe(ws) -> None:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                              "params": [{"mentions": [MIGRATION_ACCOUNT]}, {"commitment": "confirmed"}]}))


async def probe(rpc_url: str, timeout: float = 10) -> str:
    async with websockets.connect(ws_url(rpc_url), open_timeout=timeout) as ws:
        await _subscribe(ws)
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return f"Abo aktiv (ID {msg.get('result')})"


async def stream_migrations(rpc, rpc_url: str, on_mint: Callable[[str], Awaitable[None] | None],
                            stop: asyncio.Event, on_status: Callable[[str, str], None] | None = None) -> None:
    status = on_status or (lambda msg, level: None)
    backoff, failures = 5, 0
    seen: set[str] = set()
    while not stop.is_set():
        try:
            async with websockets.connect(ws_url(rpc_url), ping_interval=30, ping_timeout=30, open_timeout=20) as ws:
                await _subscribe(ws)
                status("Helius-Graduations verbunden" + (f" (nach {failures} Fehlversuchen)" if failures else ""),
                       "info")
                backoff, failures = 5, 0
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("method") != "logsNotification":
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        continue
                    val = ((msg.get("params") or {}).get("result") or {}).get("value") or {}
                    sig = val.get("signature")
                    if val.get("err") or not sig or sig in seen or not is_migration(val.get("logs")):
                        continue
                    seen.add(sig)
                    if len(seen) > 5000:
                        seen.clear()
                    asyncio.create_task(_resolve(rpc, sig, on_mint))
        except Exception as e:
            failures += 1
            log.warning("Helius-WebSocket getrennt (%s), neuer Versuch in %ss", e, backoff)
            if failures == 1 or failures % 10 == 0:
                status(f"Helius-Graduations getrennt ({type(e).__name__}: {e})", "warn")
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 300)


async def _resolve(rpc, sig: str, on_mint) -> None:
    for attempt in range(3):
        try:
            tx = await rpc.call("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                         "maxSupportedTransactionVersion": 0}])
        except Exception as e:
            log.debug("getTransaction %s: %s", sig, e)
            tx = None
        mint = mint_from_tx(tx)
        if mint:
            res = on_mint(mint)
            if asyncio.iscoroutine(res):
                await res
            return
        await asyncio.sleep(2 * (attempt + 1))
