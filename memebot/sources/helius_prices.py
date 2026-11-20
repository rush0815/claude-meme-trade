"""Echtzeit-Kurse offener Positionen direkt aus der Blockchain (Helius-WebSocket, Free-Plan reicht).

Für jeden Pool werden die beiden Bestandskonten (Token und SOL) per accountSubscribe abonniert.
Bei jedem Trade im Pool schickt Helius die neuen Bestände -> Preis = SOL-Bestand / Token-Bestand.

Funktioniert für Pools, deren Bestandskonten dem Pool selbst gehören (PumpSwap). Bei anderen Pools
(z. B. Raydium) oder wenn der errechnete Preis nicht zum DexScreener-Preis passt, wird der Coin als
"nicht unterstützt" markiert und der Bot nutzt weiter DexScreener.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from typing import Callable

import websockets

from ..models import LAMPORTS, SOL_MINT
from .helius_migrations import ws_url

log = logging.getLogger("memebot.prices")
MAX_DEVIATION = 0.35        # Abweichung zum DexScreener-Preis, ab der wir dem Pool-Preis nicht trauen


def pool_price(base_raw: int, base_dec: int, quote_lamports: int) -> float:
    """SOL pro Token aus den Pool-Beständen."""
    if base_raw <= 0:
        return 0.0
    return (quote_lamports / LAMPORTS) / (base_raw / 10 ** base_dec)


class PoolPriceFeed:
    def __init__(self, rpc, rpc_url: str, on_price: Callable[[str, float], None],
                 on_status: Callable[[str, str], None] | None = None):
        self.rpc = rpc
        self.url = ws_url(rpc_url)
        self.on_price = on_price
        self.status = on_status or (lambda msg, level: None)
        self.want: dict[str, tuple[str, float]] = {}     # mint -> (pool, DexScreener-Preis)
        self.pools: dict[str, dict] = {}                 # mint -> Zustand
        self.unsupported: dict[str, str] = {}            # mint -> Grund
        self.connected = False
        self._ids = itertools.count(100)
        self._pending: dict[int, tuple[str, str]] = {}   # Request-ID -> (mint, "base"/"quote")
        self._subs: dict[int, tuple[str, str]] = {}      # Abo-ID -> (mint, Seite)

    # ------------------------------------------------------------------ öffentlich
    def set_positions(self, items: dict[str, tuple[str, float]]) -> None:
        self.want = dict(items)

    def info(self, mint: str) -> dict:
        """Für die Oberfläche: woher kommt der Kurs dieser Position?"""
        if mint in self.unsupported:
            return {"source": "dexscreener", "note": self.unsupported[mint]}
        st = self.pools.get(mint)
        if st and st.get("ts") and self.connected:
            return {"source": "helius", "age_s": round(time.time() - st["ts"], 1)}
        return {"source": "dexscreener", "note": "Live-Abo wird aufgebaut" if mint in self.want else ""}

    # ------------------------------------------------------------------ Pool auflösen
    async def _vault(self, pool: str, mint: str) -> tuple[str, int, int] | None:
        r = await self.rpc.call("getTokenAccountsByOwner",
                                [pool, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        best = None
        for acc in (r or {}).get("value", []):
            ta = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            amt, dec = int(ta["amount"]), int(ta["decimals"])
            if best is None or amt > best[1]:
                best = (acc["pubkey"], amt, dec)
        return best

    async def _resolve(self, mint: str, pool: str, dex_price: float) -> None:
        try:
            base = await self._vault(pool, mint)
            quote = await self._vault(pool, SOL_MINT)
        except Exception as e:
            self.unsupported[mint] = f"Pool nicht lesbar ({type(e).__name__})"
            return
        if not base or not quote or base[1] <= 0 or quote[1] <= 0:
            self.unsupported[mint] = "Pool-Typ nicht unterstützt (z. B. Raydium)"
            return
        price = pool_price(base[1], base[2], quote[1])
        if dex_price > 0 and abs(price / dex_price - 1) > MAX_DEVIATION:
            self.unsupported[mint] = f"Pool-Preis weicht {abs(price / dex_price - 1):.0%} ab"
            return
        self.pools[mint] = {"pool": pool, "base_vault": base[0], "quote_vault": quote[0], "base_raw": base[1],
                            "dec": base[2], "quote_raw": quote[1], "ts": time.time(), "subs": {}}
        self.on_price(mint, price)

    # ------------------------------------------------------------------ Abos
    async def _subscribe(self, ws, mint: str) -> None:
        st = self.pools[mint]
        for side, acct in (("base", st["base_vault"]), ("quote", st["quote_vault"])):
            if side in st["subs"]:
                continue
            rid = next(self._ids)
            self._pending[rid] = (mint, side)
            st["subs"][side] = None
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "accountSubscribe",
                                      "params": [acct, {"encoding": "jsonParsed", "commitment": "confirmed"}]}))

    async def _unsubscribe(self, ws, mint: str) -> None:
        st = self.pools.pop(mint, None)
        for sid in (st or {}).get("subs", {}).values():
            if sid is not None:
                self._subs.pop(sid, None)
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": next(self._ids), "method": "accountUnsubscribe",
                                          "params": [sid]}))

    async def _reconcile(self, ws) -> None:
        for mint in [m for m in self.pools if m not in self.want]:
            await self._unsubscribe(ws, mint)
        for mint in [m for m in self.unsupported if m not in self.want]:
            self.unsupported.pop(mint, None)
        for mint, (pool, dex_price) in self.want.items():
            if mint in self.unsupported or not pool:
                continue
            if mint not in self.pools:
                await self._resolve(mint, pool, dex_price)
            if mint in self.pools:
                await self._subscribe(ws, mint)

    def handle(self, msg: dict) -> None:
        """Eine WebSocket-Nachricht verarbeiten (auch für Tests)."""
        if "id" in msg and msg.get("id") in self._pending:
            mint, side = self._pending.pop(msg["id"])
            if "result" in msg and mint in self.pools:
                self.pools[mint]["subs"][side] = msg["result"]
                self._subs[msg["result"]] = (mint, side)
            return
        if msg.get("method") != "accountNotification":
            return
        params = msg.get("params") or {}
        ref = self._subs.get(params.get("subscription"))
        if not ref:
            return
        mint, side = ref
        st = self.pools.get(mint)
        if not st:
            return
        try:
            ta = params["result"]["value"]["data"]["parsed"]["info"]["tokenAmount"]
            amount = int(ta["amount"])
        except (KeyError, TypeError, ValueError):
            return
        st["base_raw" if side == "base" else "quote_raw"] = amount
        st["ts"] = time.time()
        price = pool_price(st["base_raw"], st["dec"], st["quote_raw"])
        if price > 0:
            self.on_price(mint, price)

    # ------------------------------------------------------------------ Hauptschleife
    async def run(self, stop: asyncio.Event) -> None:
        backoff, failures = 5, 0
        while not stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=30, ping_timeout=30, open_timeout=20) as ws:
                    self.connected = True
                    for st in self.pools.values():
                        st["subs"] = {}           # nach Neuverbindung neu abonnieren
                    self._subs.clear()
                    self._pending.clear()
                    if failures:
                        self.status(f"Live-Kurse wieder verbunden (nach {failures} Fehlversuchen)", "info")
                    backoff, failures = 5, 0
                    last_rec = 0.0
                    while not stop.is_set():
                        if time.monotonic() - last_rec > 2:
                            await self._reconcile(ws)
                            last_rec = time.monotonic()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            self.handle(json.loads(raw))
                        except ValueError:
                            continue
            except Exception as e:
                self.connected = False
                failures += 1
                log.warning("Live-Kurse getrennt (%s), neuer Versuch in %ss", e, backoff)
                if failures == 1 or failures % 10 == 0:
                    self.status(f"Live-Kurse getrennt ({type(e).__name__}: {e}) – nutze DexScreener", "warn")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 120)
        self.connected = False
