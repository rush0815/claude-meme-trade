"""PumpPortal-WebSocket: Graduations (pump.fun -> PumpSwap/Raydium) live mitlesen. Kostenlos.

PumpPortal erlaubt nur EINE Verbindung pro IP und sperrt bei zu vielen Neuverbindungen.
Deshalb: lange Pausen nach Fehlern. Fällt PumpPortal aus, findet der Scout weiter
Kandidaten über DexScreener.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets

log = logging.getLogger("memebot.pumpportal")


async def probe(url: str, timeout: float = 10) -> str:
    """Verbindungstest für --check. Gibt eine kurze Beschreibung zurück oder wirft."""
    async with websockets.connect(url, open_timeout=timeout) as ws:
        await ws.send(json.dumps({"method": "subscribeMigration"}))
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            return f"verbunden, erste Nachricht: {str(msg)[:80]}"
        except asyncio.TimeoutError:
            return "verbunden (in 10 s keine Nachricht – normal, Graduations sind selten)"


async def stream_migrations(url: str, on_mint: Callable[[str], Awaitable[None] | None],
                            stop: asyncio.Event,
                            on_status: Callable[[str, str], None] | None = None) -> None:
    backoff = 15
    failures = 0
    status = on_status or (lambda msg, level: None)
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=30, open_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log.info("PumpPortal verbunden")
                if failures:
                    status(f"PumpPortal wieder verbunden (nach {failures} Fehlversuchen)", "info")
                else:
                    status("PumpPortal verbunden", "info")
                failures, backoff = 0, 15
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
        except Exception as e:
            failures += 1
            text = f"{type(e).__name__}: {e}"
            log.warning("PumpPortal nicht erreichbar (%s), nächster Versuch in %ss", text, backoff)
            if failures == 1 or failures % 10 == 0:
                status(f"PumpPortal nicht erreichbar ({text}) – Scout nutzt weiter DexScreener", "warn")
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 600)
