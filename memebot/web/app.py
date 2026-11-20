"""Web-Oberfläche: JSON-API + statisches Frontend."""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING

import asyncio

from fastapi import Body, Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from ..orchestrator import Bot

STATIC = Path(__file__).parent / "static"


def create_app(bot: "Bot") -> FastAPI:
    app = FastAPI(title="claude-meme-trade", docs_url=None, redoc_url=None)
    security = HTTPBasic(auto_error=False)
    user = bot.cfg.web.user or ""
    pw = bot.cfg.web.password or ""
    db = bot.db

    def auth(cred: HTTPBasicCredentials | None = Depends(security)) -> None:
        if not pw:  # kein Passwort gesetzt -> offen (nur im eigenen Netz/VPN!)
            return
        ok = cred is not None and secrets.compare_digest(cred.username, user) and \
            secrets.compare_digest(cred.password, pw)
        if not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})

    stats_cache: dict = {"t": 0.0, "v": None}

    def cached_stats() -> dict:
        """Statistik höchstens alle 10 s neu berechnen – die Oberfläche fragt alle 2 s."""
        if time.time() - stats_cache["t"] > 10 or stats_cache["v"] is None:
            stats_cache["v"], stats_cache["t"] = bot.analyst.stats(), time.time()
        return stats_cache["v"]

    def mode() -> str:
        return bot.ctx.mode

    def enrich(p: dict) -> dict:
        p = dict(p)
        for k in ("score_details", "security"):
            try:
                p[k] = json.loads(p[k]) if p.get(k) else {}
            except ValueError:
                p[k] = {}
        if p["status"] == "open":
            price = p.get("last_price") or p["entry_price"]
            value = p["tokens_remaining"] * price
            p["value_sol"] = value
            p["unrealized_sol"] = value + (p["sol_received"] or 0) - p["size_sol"]
            p["unrealized_pct"] = p["unrealized_sol"] / p["size_sol"] * 100 if p["size_sol"] else 0
            p["price"] = bot.price_info(p["mint"])
        return p

    def asset_version() -> str:
        # ändert sich bei jedem Update -> Browser lädt neue JS/CSS statt alter Kopie aus dem Cache
        return str(int(max((STATIC / f).stat().st_mtime for f in ("app.js", "style.css", "index.html"))))

    @app.get("/", include_in_schema=False)
    def index(_: None = Depends(auth)):
        v = asset_version()
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace("/static/app.js", f"/static/app.js?v={v}").replace("/static/style.css",
                                                                              f"/static/style.css?v={v}")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/api/summary")
    async def summary(_: None = Depends(auth)):
        return await build_summary()

    async def build_summary() -> dict:
        equity, cash = await bot.portfolio.equity()
        st = cached_stats()
        open_pos = [enrich(p) for p in db.open_positions(mode())]
        start = float(bot.cfg.paper_start_sol) if mode() == "paper" else None
        first = db.one("SELECT equity_sol FROM equity ORDER BY ts LIMIT 1")
        base = start or (first["equity_sol"] if first else equity)
        return {
            "mode": mode(), "sim": bool(bot.cfg.get("_sim")), "status": bot.status, "wallet": bot.wallet,
            "paused": bool(db.get("paused", False)), "killed": bool(db.get("killed", False)),
            "last_cycle": bot.last_cycle_ts, "now": time.time(),
            "equity_sol": equity, "cash_sol": cash, "start_sol": base,
            "total_return_pct": (equity / base - 1) * 100 if base else 0,
            "unrealized_sol": sum(p["unrealized_sol"] for p in open_pos),
            "open_count": len(open_pos), "min_score": bot.cfg.strategy.min_score,
            "feed_enabled": bot.feed is not None, "feed_connected": bool(bot.feed and bot.feed.connected),
            "watchlist": db.one("SELECT COUNT(*) AS n FROM candidates WHERE status IN ('new','waiting')")["n"],
            "watch_oldest_s": (lambda r: time.time() - r["t"] if r and r["t"] else None)(
                db.one("SELECT MIN(last_checked) AS t FROM candidates WHERE status='waiting'")),
            "stats": st["gesamt"], "last24h": st["letzte_24h"],
        }

    # ------------------------------------------------------------ Live-Push per WebSocket
    ws_tokens: dict[str, float] = {}

    @app.get("/api/ws-token")
    def ws_token(_: None = Depends(auth)):
        """Kurzlebiges Token, weil Browser beim WebSocket kein Login (Basic Auth) mitschicken."""
        now = time.time()
        for t in [t for t, exp in ws_tokens.items() if exp < now]:
            ws_tokens.pop(t)
        tok = secrets.token_urlsafe(24)
        ws_tokens[tok] = now + 3600
        return {"token": tok}

    @app.websocket("/ws")
    async def ws_stream(ws: WebSocket, token: str = ""):
        if pw and ws_tokens.get(token, 0) < time.time():
            await ws.close(code=4401)
            return
        await ws.accept()
        last = 0.0
        try:
            while True:
                try:
                    await asyncio.wait_for(bot.ui_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                if time.monotonic() - last < 0.25:      # höchstens 4 Updates pro Sekunde
                    await asyncio.sleep(0.25 - (time.monotonic() - last))
                bot.ui_event.clear()
                last = time.monotonic()
                open_pos = [enrich(p) for p in db.open_positions(mode())]
                await ws.send_json({"summary": await build_summary(), "open": open_pos})
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.get("/api/positions")
    def positions(state: str = "open", limit: int = 200, _: None = Depends(auth)):
        if state == "open":
            rows = db.open_positions(mode())
        else:
            rows = db.q("SELECT * FROM positions WHERE mode=? AND status='closed' ORDER BY closed_at DESC LIMIT ?",
                        (mode(), min(limit, 2000)))
        return [enrich(r) for r in rows]

    @app.get("/api/positions/{pid}")
    def position(pid: int, _: None = Depends(auth)):
        p = db.position(pid)
        if not p:
            raise HTTPException(404)
        p = enrich(p)
        p["fills"] = db.q("SELECT * FROM fills WHERE position_id=? ORDER BY ts", (pid,))
        return p

    @app.post("/api/positions/{pid}/close")
    def close_position(pid: int, _: None = Depends(auth)):
        p = db.position(pid)
        if not p or p["status"] != "open":
            raise HTTPException(404)
        db.update_position(pid, force_close=1)
        db.log("Web-UI", f"Manuelles Schließen angefordert: {p['symbol']}", mint=p["mint"])
        return {"ok": True}

    @app.get("/api/equity")
    def equity(hours: float = 168, _: None = Depends(auth)):
        return db.q("SELECT ts, equity_sol, cash_sol FROM equity WHERE ts>=? ORDER BY ts",
                    (time.time() - hours * 3600,))

    @app.get("/api/events")
    def events(limit: int = 200, agent: str | None = None, _: None = Depends(auth)):
        if agent:
            return db.q("SELECT * FROM events WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, min(limit, 2000)))
        return db.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(limit, 2000),))

    @app.get("/api/candidates")
    def candidates(limit: int = 100, _: None = Depends(auth)):
        rows = db.q("SELECT * FROM candidates ORDER BY COALESCE(last_checked, first_seen) DESC LIMIT ?",
                    (min(limit, 1000),))
        for r in rows:
            try:
                r["details"] = json.loads(r["details"]) if r.get("details") else {}
            except ValueError:
                r["details"] = {}
        return rows

    @app.get("/api/funnel")
    def funnel(hours: float = 24, _: None = Depends(auth)):
        """Woran scheitern die Kandidaten? Letzte Entscheidung je Coin, Gründe ohne Zahlen gruppiert."""
        import re
        rows = db.q("SELECT status, reason, score FROM candidates WHERE COALESCE(last_checked, first_seen) >= ?",
                    (time.time() - hours * 3600,))
        groups: dict[str, dict] = {}
        for r in rows:
            key = re.sub(r"[-+]?\$?[\d.,]+\s*(%|h|min|SOL)?", "…", r["reason"] or "?").strip()
            key = re.sub(r"(…\s*)+", "… ", key).strip()
            g = groups.setdefault(key, {"grund": key, "anzahl": 0, "scores": []})
            g["anzahl"] += 1
            if r["score"] is not None:
                g["scores"].append(r["score"])
        out = []
        for g in groups.values():
            sc = g.pop("scores")
            g["avg_score"] = round(sum(sc) / len(sc)) if sc else None
            g["max_score"] = round(max(sc)) if sc else None
            out.append(g)
        out.sort(key=lambda g: -g["anzahl"])
        scores = [r["score"] for r in rows if r["score"] is not None]
        buckets = {f"{lo}–{lo + 9}": sum(1 for x in scores if lo <= x < lo + 10) for lo in range(0, 100, 10)}
        return {"stunden": hours, "kandidaten": len(rows), "gruende": out, "score_verteilung": buckets,
                "min_score": bot.cfg.strategy.min_score}

    @app.get("/api/analysis")
    def analysis(_: None = Depends(auth)):
        return bot.analyst.stats()

    @app.get("/api/config")
    def config(_: None = Depends(auth)):
        hide = {"api", "web"}
        return {k: v for k, v in bot.cfg.items() if k not in hide and not k.startswith("_")}

    @app.get("/api/settings")
    def get_settings(_: None = Depends(auth)):
        from ..overrides import CHOICES, EDITABLE, current
        cur = current(bot.cfg)
        items = [{"key": k, "label": lbl, "options": opts, "value": cur[k]} for k, (opts, lbl) in CHOICES.items()]
        return items + [{"key": k, "label": v[3], "min": v[1], "max": v[2], "value": cur[k]}
                        for k, v in EDITABLE.items()]

    @app.post("/api/settings")
    def set_settings(values: dict = Body(...), _: None = Depends(auth)):
        from ..overrides import save
        try:
            new = save(bot.cfg, db, values)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        db.log("Web-UI", "Einstellungen geändert: " + ", ".join(f"{k.split('.')[1]}={v}" for k, v in values.items()))
        stats_cache["t"] = 0
        return new

    @app.post("/api/control/{action}")
    def control(action: str, _: None = Depends(auth)):
        if action == "pause":
            db.set("paused", True)
        elif action == "resume":
            db.set("paused", False)
            db.set("killed", False)
        elif action == "kill":
            db.set("killed", True)
            for p in db.open_positions(mode()):
                db.update_position(p["id"], force_close=1)
        elif action == "reset-peak":
            db.set(f"peak_{mode()}", None)
        else:
            raise HTTPException(400, "unbekannte Aktion")
        db.log("Web-UI", f"Steuerung: {action}", "warn" if action == "kill" else "info")
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
