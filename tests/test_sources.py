"""API-Clients gegen nachgebaute Antworten (ohne Internet)."""
import asyncio
import json

import httpx

from memebot.models import SOL_MINT
from memebot.sources.dexscreener import DexScreener
from memebot.sources.http import RateLimitedClient
from memebot.sources.jupiter import Jupiter
from memebot.sources.solana_rpc import SolanaRPC

PAIR = {
    "chainId": "solana", "dexId": "pumpswap", "pairAddress": "PAIR1",
    "baseToken": {"address": "MINT1", "name": "Test", "symbol": "TST"},
    "quoteToken": {"address": SOL_MINT, "symbol": "SOL"},
    "priceNative": "0.00000123", "priceUsd": "0.000185",
    "txns": {"m5": {"buys": 50, "sells": 20}, "h1": {"buys": 400, "sells": 300}},
    "volume": {"m5": 9000, "h1": 50000, "h6": 120000},
    "priceChange": {"m5": 6.5, "h1": 22, "h6": 80},
    "liquidity": {"usd": 45000}, "fdv": 185000, "pairCreatedAt": 1700000000000,
}


def client(base, handler):
    return RateLimitedClient(base, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_dexscreener_best_sol_pair():
    small = dict(PAIR, pairAddress="SMALL", liquidity={"usd": 1000})
    usdc = dict(PAIR, pairAddress="USDC", quoteToken={"address": "USDC"}, liquidity={"usd": 999999})

    def h(req):
        assert req.url.path == "/tokens/v1/solana/MINT1"
        return httpx.Response(200, json=[small, PAIR, usdc])
    dex = DexScreener(client=client("https://api.dexscreener.com", h))
    m = asyncio.run(dex.markets(["MINT1"]))["MINT1"]
    assert m.pair_address == "PAIR1" and m.symbol == "TST"
    assert m.price_sol == 1.23e-6 and m.buys_m5 == 50 and m.volume_h1 == 50000 and m.created_at == 1700000000


def test_jupiter_quote_and_retry_on_429():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        assert req.headers["x-api-key"] == "K"
        return httpx.Response(200, json={"outAmount": "12345", "priceImpactPct": "0.021"})
    jup = Jupiter(api_key="K", client=client("https://api.jup.ag/swap/v1", h))
    jup.http.client.headers["x-api-key"] = "K"
    q = asyncio.run(jup.quote(SOL_MINT, "MINT1", 1000, 500))
    assert q["outAmount"] == "12345" and abs(jup.impact_pct(q) - 2.1) < 1e-9 and calls["n"] == 2


def test_rpc_token_balance_and_confirm():
    def h(req):
        body = json.loads(req.content)
        if body["method"] == "getTokenAccountsByOwner":
            acc = {"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "500"}}}}}}
            return httpx.Response(200, json={"result": {"value": [acc, acc]}})
        if body["method"] == "getSignatureStatuses":
            return httpx.Response(200, json={"result": {"value": [{"confirmationStatus": "confirmed", "err": None}]}})
        return httpx.Response(200, json={"error": {"message": "nope"}})
    rpc = SolanaRPC("https://rpc", client=client("https://rpc", h))
    assert asyncio.run(rpc.token_balance_raw("OWNER", "MINT")) == 1000
    assert asyncio.run(rpc.confirm("SIG", 5)) is True


def test_geckoterminal_mints_skip_bonding_curve():
    from memebot.sources.geckoterminal import GeckoTerminal

    def pool(mint, dex):
        return {"relationships": {"base_token": {"data": {"id": f"solana_{mint}"}},
                                  "dex": {"data": {"id": dex}}}}

    def h(req):
        assert req.url.path.startswith("/api/v2/networks/solana/")
        return httpx.Response(200, json={"data": [pool("AAApump", "pumpswap"), pool("BBBpump", "pump-fun"),
                                                  pool(SOL_MINT, "raydium")]})
    gt = GeckoTerminal(client=client("https://api.geckoterminal.com/api/v2", h))
    found = asyncio.run(gt.latest_mints())
    mints = {m for m, _ in found}
    assert mints == {"AAApump"} and len(found) == 4      # 4 Abfragen, je 1 Treffer


def test_helius_migration_parsing():
    from memebot.sources.helius_migrations import is_migration, mint_from_tx, ws_url

    assert ws_url("https://mainnet.helius-rpc.com/?api-key=K") == "wss://mainnet.helius-rpc.com/?api-key=K"
    assert is_migration(["Program 6EF8... invoke [1]", "Program log: Instruction: Migrate"])
    assert not is_migration(["Program log: Instruction: Buy"])
    tx = {"meta": {"postTokenBalances": [{"mint": SOL_MINT}, {"mint": "TOKpump"}, {"mint": "TOKpump"},
                                         {"mint": "LPMINTxyz"}], "preTokenBalances": [{"mint": "TOKpump"}]}}
    assert mint_from_tx(tx) == "TOKpump"
    assert mint_from_tx({"meta": {"postTokenBalances": [{"mint": SOL_MINT}]}}) is None
