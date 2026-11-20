import asyncio

from fastapi.testclient import TestClient

from memebot.__main__ import build
from memebot.config import load_config
from memebot.sim import apply_sim_overrides
from memebot.web.app import create_app


def make_bot(tmp_path, password=""):
    cfg = load_config()
    apply_sim_overrides(cfg)
    cfg["db_path"] = str(tmp_path / "t.db")
    cfg["web"]["password"] = password
    cfg["web"]["user"] = "admin"
    return build(cfg, simulate=True)


def test_simulation_cycles_produce_trades(tmp_path):
    bot = make_bot(tmp_path)
    bot.cfg["scout"]["recheck_seconds"] = 0

    async def go():
        for _ in range(150):
            bot.ctx.dex._last = 0          # Markt bei jedem Zyklus weiterlaufen lassen
            await bot.cycle()
            await bot.position_tick()
    asyncio.run(go())
    n = bot.db.one("SELECT COUNT(*) AS n FROM positions")["n"]
    assert n > 0
    assert bot.db.one("SELECT COUNT(*) AS n FROM positions WHERE status='closed'")["n"] > 0
    c = bot.db.one("SELECT * FROM candidates WHERE details IS NOT NULL LIMIT 1")
    assert c and c["score"] is not None
    cash = bot.db.get("paper_cash")
    assert 0 < cash < 2


def test_web_api(tmp_path):
    bot = make_bot(tmp_path)
    asyncio.run(bot.cycle())
    c = TestClient(create_app(bot))
    r = c.get("/")
    assert r.status_code == 200 and "app.js?v=" in r.text and r.headers["cache-control"] == "no-store"
    s = c.get("/api/summary").json()
    assert s["sim"] and s["mode"] == "paper"
    for path in ("/api/positions?state=open", "/api/positions?state=closed", "/api/equity", "/api/events",
                 "/api/candidates", "/api/analysis", "/api/config", "/api/funnel"):
        assert c.get(path).status_code == 200, path
    assert "api" not in c.get("/api/config").json()       # keine Schlüssel ausliefern
    assert c.post("/api/control/pause").json()["ok"]
    assert bot.db.get("paused") is True


def test_web_auth(tmp_path):
    bot = make_bot(tmp_path, password="geheim")
    c = TestClient(create_app(bot))
    assert c.get("/api/summary").status_code == 401
    assert c.get("/api/summary", auth=("admin", "geheim")).status_code == 200


def test_funnel_groups_reasons(tmp_path):
    bot = make_bot(tmp_path)
    for i, r in enumerate(["Liquidität $3,535 zu gering", "Liquidität $18,977 zu gering", "kein Momentum"]):
        bot.db.upsert_candidate(f"M{i}", "t")
        bot.db.set_candidate(f"M{i}", "waiting", r, 40 + i)
    f = TestClient(create_app(bot)).get("/api/funnel").json()
    assert f["kandidaten"] == 3 and f["gruende"][0]["anzahl"] == 2 and "Liquidität" in f["gruende"][0]["grund"]


def test_settings_change_applies_live_and_persists(tmp_path):
    bot = make_bot(tmp_path)
    c = TestClient(create_app(bot))
    items = {i["key"]: i["value"] for i in c.get("/api/settings").json()}
    assert items["exit.tp1_pct"] == 30
    r = c.post("/api/settings", json={"exit.tp1_pct": 25, "exit.stop_loss_pct": 15})
    assert r.status_code == 200
    assert bot.cfg["exit"]["take_profit_levels"][0] == [25.0, 0.33]
    assert bot.cfg["exit"]["stop_loss_pct"] == 15
    assert c.post("/api/settings", json={"exit.stop_loss_pct": 500}).status_code == 400
    assert c.post("/api/settings", json={"exit.tp1_fraction": 0.8}).status_code == 400   # 0.8 + 0.33 > 1
    # Neustart: Werte kommen aus der Datenbank zurück
    bot2 = make_bot(tmp_path)
    assert bot2.cfg["exit"]["take_profit_levels"][0][0] == 25 and bot2.cfg["exit"]["stop_loss_pct"] == 15


def test_exit_v2_migration_overrides_old_config(tmp_path):
    cfg_old = {"take_profit_levels": [[40, 0.33], [100, 0.33]], "trailing_activate_pct": 30,
               "trailing_stop_pct": 18, "max_hold_min": 180}
    bot = make_bot(tmp_path)
    assert bot.cfg["exit"]["take_profit_levels"][0][0] == 30
    # Simuliert alte config.toml-Werte + Neustart: Migration läuft nur einmal, gespeicherte Werte gewinnen
    bot.cfg["exit"].update(cfg_old)
    bot2 = make_bot(tmp_path)
    e = bot2.cfg["exit"]
    assert e["take_profit_levels"] == [[30.0, 0.33], [80.0, 0.33]] and e["trailing_activate_pct"] == 50
    assert e["max_hold_min"] == 120 and e["breakeven_activate_pct"] == 25


def test_settings_mode_choice(tmp_path):
    bot = make_bot(tmp_path)
    c = TestClient(create_app(bot))
    assert c.post("/api/settings", json={"exit.mode": "simple", "exit.simple_take_profit_pct": 35}).status_code == 200
    assert bot.cfg["exit"]["mode"] == "simple" and bot.cfg["exit"]["simple_take_profit_pct"] == 35
    assert c.post("/api/settings", json={"exit.mode": "quatsch"}).status_code == 400


def test_timing_migration_and_ui_edit(tmp_path):
    bot = make_bot(tmp_path)
    assert bot.cfg["scout"]["recheck_seconds"] == 30 and bot.cfg["poll_seconds"] == 5
    c = TestClient(create_app(bot))
    assert c.post("/api/settings", json={"bot.poll_seconds": 8, "scout.recheck_seconds": 20}).status_code == 200
    assert bot.cfg["poll_seconds"] == 8 and bot.cfg["scout"]["recheck_seconds"] == 20
    bot2 = make_bot(tmp_path)                      # Migration läuft nicht erneut, eigene Werte bleiben
    assert bot2.cfg["poll_seconds"] == 8


def test_mcap_spark_and_chart(tmp_path):
    bot = make_bot(tmp_path)
    bot.cfg["scout"]["recheck_seconds"] = 0

    async def go():
        for _ in range(150):
            bot.ctx.dex._last = 0
            await bot.cycle()
            await bot.position_tick()
            if bot.db.one("SELECT COUNT(*) AS n FROM positions")["n"]:
                break
    asyncio.run(go())
    p = bot.db.one("SELECT * FROM positions ORDER BY id LIMIT 1")
    assert p and p["supply"] and abs(p["supply"] - 1e9) / 1e9 < 1e-6     # Simulation: 1 Mrd. Token
    assert p["entry_mcap"] > 0 and bot.ctx.sol_usd == 150
    c = TestClient(create_app(bot))
    row = c.get(f"/api/positions/{p['id']}").json()
    assert row["entry_mcap"] and row["last_mcap"] and row["fills"][0]["mcap_usd"]
    if p["status"] == "open":
        assert len(row.get("spark") or []) >= 1
    ch = c.get(f"/api/positions/{p['id']}/chart").json()
    assert ch["is_mcap"] and ch["candles"] and ch["fills"][0]["side"] == "buy"
    assert all(k in ch["candles"][0] for k in ("time", "open", "high", "low", "close"))


def test_chart_timeframes_and_supply_backfill(tmp_path):
    import time as _t
    from memebot.models import Market, Signal
    bot = make_bot(tmp_path)
    m = Market(mint="OLD1", symbol="OLD", price_sol=1e-6, price_usd=1.5e-4, fdv=150_000,
               liquidity_usd=40_000, created_at=_t.time() - 3600)

    async def go():
        pid = await bot.positions.open(Signal(m, 70, {"ki_hinweis": "KI aus"}, {"decimals": 6}), 0.05)
        bot.db.update_position(pid, supply=None, entry_mcap=None)          # wie eine Position von vor dem Update
        bot.ctx.sol_usd = 0
        bot._last_markets["OLD1"] = m
        return pid
    pid = asyncio.run(go())
    c = TestClient(create_app(bot))
    for tf in (1, 5, 15, 60):
        ch = c.get(f"/api/positions/{pid}/chart?tf={tf}").json()
        assert ch["tf"] == tf and ch["is_mcap"] and ch["entry_level"] > 0
    p = bot.db.position(pid)
    assert abs(p["supply"] - 1e9) < 1 and p["entry_mcap"] > 0            # nachgerüstet und gespeichert
    det = c.get(f"/api/positions/{pid}").json()
    assert det["score_details"]["ki_hinweis"] == "KI aus" and det["fills"][0]["mcap_usd"] > 0
