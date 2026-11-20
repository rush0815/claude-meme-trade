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
        from .sources.jupiter import Jupiter
        from .sources.rugcheck import RugCheck
        from .sources.solana_rpc import SolanaRPC

        a = cfg.api
        rpc_url = "" if a.rpc_url.endswith("api-key=") else a.rpc_url   # kein Key gesetzt
        ctx = Ctx(cfg=cfg, db=db, dex=DexScreener(a.dexscreener_base), rug=RugCheck(a.rugcheck_base),
                  jup=Jupiter(a.jupiter_base, a.jupiter_api_key, a.jupiter_min_interval_s),
                  rpc=SolanaRPC(rpc_url) if rpc_url else None)
        if cfg.mode == "live" and ctx.rpc is None:
            raise SystemExit("Live-Modus braucht eine RPC-URL (HELIUS_API_KEY setzen).")
    return Bot(ctx)


async def main_async(args) -> None:
    cfg = load_config(args.config)
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
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
