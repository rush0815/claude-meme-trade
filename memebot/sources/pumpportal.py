"""PumpPortal-WebSocket: Graduations (pump.fun -> PumpSwap/Raydium) live mitlesen. Kostenlos."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets

log = logging.getLogger("memebot.pumpportal")


async def stream_migrations(url: str, on_mint: Callable[[str], Awaitable[None] | None],
                            stop: asyncio.Event) -> None:
    backoff = 2
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log.info("PumpPortal verbunden")
                backoff = 2
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    mint = msg.get("mint") if isinstance(msg, dict) else None
                    if mint:
                        res = on_mint(mint)
                        if asyncio.iscoroutine(res):
                            await res
        except Exception as e:  # Verbindungsabbruch -> neu verbinden
            log.warning("PumpPortal getrennt (%s), neuer Versuch in %ss", e, backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60)
