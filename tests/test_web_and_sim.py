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
