"""Echtzeit-Kurse (Helius-Pool-Abo) und Live-Push zur Oberfläche."""
import asyncio
import dataclasses
import time

from fastapi.testclient import TestClient

from memebot.models import SOL_MINT, Market
from memebot.sources.helius_prices import PoolPriceFeed, pool_price
from tests.test_web_and_sim import make_bot
from memebot.web.app import create_app


def parsed(pubkey, amount, dec):
    return {"pubkey": pubkey, "account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": str(amount),
                                                                                     "decimals": dec}}}}}}


class FakeRPC:
    def __init__(self, base=1_000_000 * 10**6, quote=50 * 10**9):
        self.base, self.quote = base, quote

    async def call(self, method, params):
        assert method == "getTokenAccountsByOwner"
        mint = params[1]["mint"]
        if mint == SOL_MINT:
            return {"value": [parsed("QV", self.quote, 9)]}
        return {"value": [parsed("BV", self.base, 6), parsed("DUST", 5, 6)]}


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(raw)


def test_pool_price_math():
    assert abs(pool_price(1_000_000 * 10**6, 6, 50 * 10**9) - 5e-5) < 1e-15


def test_feed_resolves_subscribes_and_updates():
    got = []
    feed = PoolPriceFeed(FakeRPC(), "https://x/?api-key=K", lambda m, p: got.append((m, p)))
    feed.set_positions({"MINT": ("POOL", 5e-5)})
    ws = FakeWS()
    asyncio.run(feed._reconcile(ws))
    assert got and abs(got[0][1] - 5e-5) < 1e-15 and len(ws.sent) == 2
    # Abo-Bestätigungen
    rids = sorted(feed._pending)
    feed.handle({"jsonrpc": "2.0", "id": rids[0], "result": 11})
    feed.handle({"jsonrpc": "2.0", "id": rids[1], "result": 12})
    # Trade: SOL im Pool steigt auf 60 -> Preis steigt
    note = {"method": "accountNotification", "params": {"subscription": 12, "result": {"value": {
        "data": {"parsed": {"info": {"tokenAmount": {"amount": str(60 * 10**9), "decimals": 9}}}}}}}}
    feed.handle(note)
    assert abs(got[-1][1] - 6e-5) < 1e-15
    feed.connected = True
    assert feed.info("MINT")["source"] == "helius"
    # Position geschlossen -> Abo wird beendet
    feed.set_positions({})
    asyncio.run(feed._reconcile(ws))
    assert "MINT" not in feed.pools and "accountUnsubscribe" in ws.sent[-1]


def test_feed_rejects_mismatching_pool():
    feed = PoolPriceFeed(FakeRPC(), "https://x/?api-key=K", lambda m, p: None)
    feed.set_positions({"MINT": ("POOL", 1e-4)})          # DexScreener sagt doppelt so viel
    asyncio.run(feed._reconcile(FakeWS()))
    assert "MINT" in feed.unsupported and feed.info("MINT")["source"] == "dexscreener"


def test_live_price_triggers_stop_loss_immediately(tmp_path):
    bot = make_bot(tmp_path)
    m = Market(mint="LIVE1", symbol="LV", price_sol=1e-6, liquidity_usd=50_000, fdv=300_000,
               created_at=time.time() - 3600)
    from memebot.models import Signal

    async def go():
        pid = await bot.positions.open(Signal(m, 80, {}, {"decimals": 6}), 0.05)
        bot._last_markets["LIVE1"] = m
        entry = bot.db.position(pid)["entry_price"]
        task = asyncio.create_task(bot.live_loop())
        bot._on_live_price("LIVE1", entry * 0.7)          # -30 % live
        for _ in range(20):
            await asyncio.sleep(0.05)
            if bot.db.position(pid)["status"] == "closed":
                break
        bot.stop.set()
        await task
        return bot.db.position(pid)
    p = asyncio.run(go())
    assert p["status"] == "closed" and p["exit_reason"] == "Stop-Loss"


def test_websocket_push(tmp_path):
    bot = make_bot(tmp_path, password="pw")
    asyncio.run(bot.cycle())
    c = TestClient(create_app(bot))
    tok = c.get("/api/ws-token", auth=("admin", "pw")).json()["token"]
    with c.websocket_connect(f"/ws?token={tok}") as ws:
        msg = ws.receive_json()
        assert "summary" in msg and "open" in msg and "equity_sol" in msg["summary"]
    # ohne gültiges Token kein Zugang
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws?token=falsch") as ws:
            ws.receive_json()
