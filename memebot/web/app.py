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

    def mc(p: dict, price) -> float | None:
        sol_usd = bot.ctx.sol_usd
        return price * p["supply"] * sol_usd if p.get("supply") and sol_usd > 0 and price else None

    def spark(p: dict, points: int = 40) -> list:
        """Kleiner Kursverlauf seit Einstieg (Marktkapitalisierung, sonst Preis)."""
        rows = db.ticks(p["mint"], p["opened_at"] - 60)
        if len(rows) > points:
            step = len(rows) / points
            rows = [rows[int(i * step)] for i in range(points)] + [rows[-1]]
        return [[round(r["ts"]), mc(p, r["price"]) or r["price"]] for r in rows]

    def enrich(p: dict) -> dict:
        p = dict(p)
        p["entry_mcap"] = p.get("entry_mcap") or mc(p, p["entry_price"])
        p["last_mcap"] = mc(p, p.get("last_price"))
        p["high_mcap"] = mc(p, p.get("high_price"))
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
            p["spark"] = spark(p)
        return p

    def asset_version() -> str:
        # ändert sich bei jedem Update -> Browser lädt neue JS/CSS statt alter Kopie aus dem Cache
        return str(int(max((STATIC / f).stat().st_mtime for f in ("app.js", "i18n.js", "style.css", "index.html"))))

    @app.get("/", include_in_schema=False)
    def index(_: None = Depends(auth)):
        v = asset_version()
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for f in ("app.js", "i18n.js", "style.css"):
            html = html.replace(f"/static/{f}", f"/static/{f}?v={v}")
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
    async def position(pid: int, _: None = Depends(auth)):
        p = db.position(pid)
        if not p:
            raise HTTPException(404)
        p = enrich(await ensure_supply(p))
        p["fills"] = db.q("SELECT * FROM fills WHERE position_id=? ORDER BY ts", (pid,))
        for f in p["fills"]:
            if not f.get("mcap_usd"):
                f["mcap_usd"] = mc(p, f["price"])
        return p

    candle_cache: dict[str, tuple[float, list]] = {}

    async def ensure_supply(p: dict) -> dict:
        """Ältere Positionen ohne Token-Menge nachrüsten, SOL-Kurs besorgen (für Marktkapitalisierung)."""
        if p.get("supply") and bot.ctx.sol_usd > 0:
            return p
        m = bot._last_markets.get(p["mint"])
        if m is None:
            try:
                m = (await bot.ctx.dex.markets([p["mint"]])).get(p["mint"])
            except Exception:
                m = None
        if m is None or m.price_sol <= 0 or m.price_usd <= 0:
            return p
        if bot.ctx.sol_usd <= 0:
            bot.ctx.sol_usd = m.price_usd / m.price_sol
        if not p.get("supply") and m.fdv > 0:
            supply = m.fdv / m.price_usd
            entry_mcap = p["entry_price"] * supply * bot.ctx.sol_usd
            db.update_position(p["id"], supply=supply, entry_mcap=entry_mcap)
            p = {**p, "supply": supply, "entry_mcap": entry_mcap}
        return p

    TF = {1: ("minute", 1), 5: ("minute", 5), 15: ("minute", 15), 60: ("hour", 1)}

    @app.get("/api/positions/{pid}/chart")
    async def position_chart(pid: int, tf: int = 1, _: None = Depends(auth)):
        """Kerzen als Marktkapitalisierung (USD) + Ein-/Ausstiege + aktuelle Ausstiegs-Level."""
        p = db.position(pid)
        if not p:
            raise HTTPException(404)
        tf = tf if tf in TF else 1
        p = enrich(await ensure_supply(p))
        supply, sol_usd = p.get("supply"), bot.ctx.sol_usd
        is_mcap = bool(supply and sol_usd > 0)
        # Einheit: Marktkapitalisierung in USD; ohne Token-Menge ersatzweise Preis in USD
        conv_usd = (lambda x: x * supply) if is_mcap else (lambda x: x)
        conv_sol = (lambda x: x * supply * sol_usd) if is_mcap else (lambda x: x * (sol_usd or 1))
        step = tf * 60
        end = (p["closed_at"] or time.time()) + (20 * step if p["closed_at"] else 0)
        start = p["opened_at"] - max(90 * 60, 60 * step)
        candles: list[dict] = []
        source = "ticks"
        gecko = getattr(bot.ctx, "gecko", None)
        if gecko is not None and p.get("pair_address"):
            key = f"{p['pair_address']}:{tf}:{int(end // step) if p['closed_at'] else 'live'}"
            hit = candle_cache.get(key)
            if hit and time.time() - hit[0] < (3600 if p["closed_at"] else 60):
                raw = hit[1]
            else:
                try:
                    unit, agg = TF[tf]
                    raw = await gecko.candles_tf(p["pair_address"], unit, agg, limit=300,
                                                 before=end if p["closed_at"] else None)
                    candle_cache[key] = (time.time(), raw)
                except Exception:
                    raw = []
            candles = [{"time": int(c[0]), "open": conv_usd(c[1]), "high": conv_usd(c[2]), "low": conv_usd(c[3]),
                        "close": conv_usd(c[4])} for c in raw if start <= c[0] <= end]
            if candles:
                source = "geckoterminal"
        # eigene Kursaufzeichnung ergänzt die Zeit nach der letzten GeckoTerminal-Kerze (Echtzeit)
        last_t = candles[-1]["time"] if candles else 0
        buckets: dict[int, dict] = {}
        for t in db.ticks(p["mint"], max(start, last_t + step), end):
            b = int(t["ts"] // step * step)
            v = conv_sol(t["price"])
            c = buckets.get(b)
            if c is None:
                buckets[b] = {"time": b, "open": v, "high": v, "low": v, "close": v}
            else:
                c["high"], c["low"], c["close"] = max(c["high"], v), min(c["low"], v), v
        candles += [buckets[k] for k in sorted(buckets)]
        fills = [{"time": int(f["ts"]), "side": f["side"], "reason": f["reason"],
                  "mcap": (f.get("mcap_usd") if is_mcap and f.get("mcap_usd") else conv_sol(f["price"]))}
                 for f in db.q("SELECT * FROM fills WHERE position_id=? ORDER BY ts", (pid,))]
        entry_level = p["entry_mcap"] if is_mcap and p.get("entry_mcap") else conv_sol(p["entry_price"])
        levels = []
        if p["status"] == "open" and entry_level:
            e, em = bot.cfg["exit"], entry_level
            levels.append({"label": "SL", "value": em * (1 - e["stop_loss_pct"] / 100), "kind": "sl"})
            hi_pct = ((p["high_price"] or p["entry_price"]) / p["entry_price"] - 1) * 100
            be = float(e.get("breakeven_activate_pct", 0) or 0)
            if be > 0 and hi_pct >= be:
                levels.append({"label": "BE", "value": em * (1 + float(e.get("breakeven_floor_pct", 3)) / 100), "kind": "be"})
            if e.get("mode") == "simple":
                levels.append({"label": "TP", "value": em * (1 + float(e.get("simple_take_profit_pct", 30)) / 100), "kind": "tp"})
            else:
                for i, (lvl, _) in enumerate(e.get("take_profit_levels") or []):
                    if i >= int(p["tp_hits"] or 0):
                        levels.append({"label": f"TP{i + 1}", "value": em * (1 + lvl / 100), "kind": "tp"})
                if hi_pct >= float(e["trailing_activate_pct"]):
                    hi = conv_sol(p["high_price"])
                    levels.append({"label": "Trail", "value": hi * (1 - e["trailing_stop_pct"] / 100), "kind": "trail"})
        return {"candles": candles, "fills": fills, "levels": levels, "entry_level": entry_level, "tf": tf,
                "is_mcap": is_mcap, "source": source, "status": p["status"], "supply": supply, "sol_usd": sol_usd}

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
        from ..overrides import CHOICES, CHOICES_EN, EDITABLE, LABELS_EN, current
        cur = current(bot.cfg)
        items = [{"key": k, "label": lbl, "label_en": LABELS_EN.get(lbl, lbl), "options": opts,
                  "options_en": CHOICES_EN.get(k, opts), "value": cur[k]} for k, (opts, lbl) in CHOICES.items()]
        return items + [{"key": k, "label": v[3], "label_en": LABELS_EN.get(v[3], v[3]), "min": v[1], "max": v[2],
                         "value": cur[k]} for k, v in EDITABLE.items()]

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

    # ------------------------------------------------------------ KI-Analyst
    @app.get("/api/ai")
    def ai_get(_: None = Depends(auth)):
        from ..agents.ai_analyst import CHECKS, MODELS, mask, settings
        cfg = settings(db)
        from_env = bool(cfg["api_key"]) and not (db.get("ai") or {}).get("api_key")
        return {
            "settings": {**{k: v for k, v in cfg.items() if k != "api_key"}, "api_key_masked": mask(cfg["api_key"]),
                         "api_key_from_env": from_env},
            "checks": {k: {"de": v[0], "en": v[1]} for k, v in CHECKS.items()},
            "models": MODELS,
            "usage": bot.ai.usage(),
        }

    @app.post("/api/ai")
    def ai_set(values: dict = Body(...), _: None = Depends(auth)):
        from ..agents.ai_analyst import save_settings
        try:
            save_settings(db, values)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e)) from None
        changed = [k for k in values if k != "api_key"] + (["api_key"] if values.get("api_key") else [])
        db.log("Web-UI", "KI-Einstellungen geändert: " + ", ".join(changed))
        return ai_get()

    @app.get("/api/ai/calls")
    def ai_calls(limit: int = 200, offset: int = 0, _: None = Depends(auth)):
        return bot.ai.calls(min(limit, 5000), max(offset, 0))

    @app.get("/api/ai/calls.csv")
    def ai_calls_csv(_: None = Depends(auth)):
        import csv
        import io
        from fastapi.responses import Response
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["zeit_utc", "token", "mint", "modell", "input_tokens", "output_tokens", "websuchen", "webabrufe",
                    "kosten_usd", "score", "urteil", "ok", "fehler"])
        for r in reversed(bot.ai.calls(100000)):
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["ts"])), r["symbol"], r["mint"], r["model"],
                        r["input_tokens"], r["output_tokens"], r["searches"], r["fetches"],
                        f"{r['cost_usd']:.6f}".replace(".", ","), r["score"], r["verdict"], r["ok"], r["error"]])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=ki-aufrufe.csv"})

    @app.post("/api/ai/test")
    async def ai_test(_: None = Depends(auth)):
        try:
            return await bot.ai.test()
        except Exception as e:
            raise HTTPException(400, f"Test fehlgeschlagen: {e}") from None

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
