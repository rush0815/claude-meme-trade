"""Start: python -m memebot [--config config.toml] [--simulate] [--no-web]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

import uvicorn

from .agents.base import Ctx
from .config import load_config
from .db import DB
from .orchestrator import Bot


def build(cfg, simulate: bool = False) -> Bot:
    db = DB(cfg["db_path"])
    if simulate:
        from .sim import SimDex, SimJupiter, SimRug, SimWorld

        world = SimWorld()
        ctx = Ctx(cfg=cfg, db=db, dex=SimDex(world), rug=SimRug(world), jup=SimJupiter(world), rpc=None)
    else:
        from .sources.dexscreener import DexScreener
        from .sources.geckoterminal import GeckoTerminal
        from .sources.jupiter import Jupiter
        from .sources.rugcheck import RugCheck
        from .sources.solana_rpc import SolanaRPC

        a = cfg.api
        rpc_url = "" if a.rpc_url.endswith("api-key=") else a.rpc_url   # kein Key gesetzt
        ctx = Ctx(cfg=cfg, db=db, dex=DexScreener(a.dexscreener_base), rug=RugCheck(a.rugcheck_base),
                  jup=Jupiter(a.jupiter_base, a.jupiter_api_key, a.jupiter_min_interval_s),
                  rpc=SolanaRPC(rpc_url) if rpc_url else None,
                  gecko=GeckoTerminal(a.get("geckoterminal_base", "https://api.geckoterminal.com/api/v2")))
        if cfg.mode == "live" and ctx.rpc is None:
            raise SystemExit("Live-Modus braucht eine RPC-URL (HELIUS_API_KEY setzen).")
    return Bot(ctx)


async def check(cfg) -> None:
    """Verbindungstest aller Datenquellen (python -m memebot --check)."""
    from .models import SOL_MINT
    from .sources.dexscreener import DexScreener
    from .sources.jupiter import Jupiter
    from .sources.pumpportal import probe
    from .sources.rugcheck import RugCheck
    from .sources.solana_rpc import SolanaRPC

    a = cfg.api
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    bonk = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    async def dex():
        n = len(await DexScreener(a.dexscreener_base).latest_mints())
        m = await DexScreener(a.dexscreener_base).markets([bonk])
        if not m:
            raise RuntimeError("keine Marktdaten erhalten")
        return f"{n} neue Token in den Listen, BONK-Preis {m[bonk].price_sol:.3e} SOL"

    async def rug():
        r = await RugCheck(a.rugcheck_base).report(bonk)
        if not r:
            raise RuntimeError("kein Bericht erhalten")
        return f"Bericht ok (Score {r.get('score')})"

    async def jup():
        j = Jupiter(a.jupiter_base, a.jupiter_api_key, a.jupiter_min_interval_s)
        q = await j.quote(SOL_MINT, usdc, 10_000_000, 100)
        if not q:
            raise RuntimeError("keine Quote (API-Key falsch oder Limit erreicht?)")
        key = "mit API-Key" if a.jupiter_api_key else "OHNE API-Key (langsam)"
        return f"0,01 SOL = {int(q['outAmount']) / 1e6:.2f} USDC, {key}"

    async def rpc():
        if a.rpc_url.endswith("api-key="):
            raise RuntimeError("HELIUS_API_KEY nicht gesetzt (für Paper optional, für Live nötig)")
        slot = await SolanaRPC(a.rpc_url).call("getSlot")
        return f"Slot {slot}"

    async def gecko():
        from .sources.geckoterminal import GeckoTerminal
        found = await GeckoTerminal(a.get("geckoterminal_base", "https://api.geckoterminal.com/api/v2")).latest_mints()
        if not found:
            raise RuntimeError("keine Pools erhalten")
        return f"{len(found)} Pools (neu + trendend)"

    async def pump():
        return await probe(a.pumpportal_ws)

    checks = [("DexScreener", dex), ("GeckoTerminal", gecko), ("RugCheck", rug), ("Jupiter", jup), ("Helius/RPC", rpc), ("PumpPortal", pump)]
    print(f"Konfiguration: {cfg['_source']}  Modus: {cfg.mode}\n")
    for name, fn in checks:
        try:
            print(f"  OK      {name:<12} {await asyncio.wait_for(fn(), 30)}")
        except Exception as e:
            print(f"  FEHLER  {name:<12} {type(e).__name__}: {e}")
    print()


async def main_async(args) -> None:
    cfg = load_config(args.config)
    if args.check:
        await check(cfg)
        return
    if args.simulate:
        from .sim import apply_sim_overrides

        apply_sim_overrides(cfg)
    bot = build(cfg, args.simulate)
    logging.getLogger("memebot").info("Konfiguration: %s, Modus: %s%s", cfg["_source"], cfg.mode,
                                      " (Simulation)" if args.simulate else "")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.stop.set)
        except NotImplementedError:
            pass
    tasks = [asyncio.create_task(bot.run())]
    server = None
    if not args.no_web:
        from .web.app import create_app

        server = uvicorn.Server(uvicorn.Config(create_app(bot), host=cfg.web.host, port=int(args.port or cfg.web.port),
                                               log_level="warning"))
        server.install_signal_handlers = lambda: None
        tasks.append(asyncio.create_task(server.serve()))
        logging.getLogger("memebot").info("Web-Oberfläche: http://%s:%s", cfg.web.host, args.port or cfg.web.port)
        if not cfg.web.password:
            logging.getLogger("memebot").warning("WEB_PASSWORD nicht gesetzt – Oberfläche ist ohne Login erreichbar!")
    await bot.stop.wait()
    if server:
        server.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    p = argparse.ArgumentParser(prog="memebot", description="Solana-Meme-Coin-Bot mit Agenten und Web-UI")
    p.add_argument("--config", help="Pfad zur config.toml")
    p.add_argument("--simulate", action="store_true", help="Demo mit simuliertem Markt (ohne Internet)")
    p.add_argument("--no-web", action="store_true", help="ohne Web-Oberfläche")
    p.add_argument("--port", type=int, help="Port der Web-Oberfläche")
    p.add_argument("--check", action="store_true", help="nur Verbindungen zu allen APIs testen")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
