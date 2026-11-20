"""KI-Analyst mit nachgebauter Anthropic-API (ohne Internet)."""
import asyncio
import json
import time

import httpx
from fastapi.testclient import TestClient

from memebot.agents.ai_analyst import AIAnalyst, cost, parse_json, save_settings, settings
from memebot.agents.base import Ctx
from memebot.config import load_config
from memebot.db import DB
from memebot.models import Market
from memebot.web.app import create_app
from tests.test_web_and_sim import make_bot


def fake_api(score=72, status=200, calls=None, text=None):
    def h(req):
        body = json.loads(req.content)
        if calls is not None:
            calls.append(body)
        assert req.headers["x-api-key"] == "sk-ant-test-1234567890"
        if status != 200:
            return httpx.Response(status, text="overloaded")
        out = text if text is not None else "Here you go:\n" + json.dumps({
            "score": score, "checks": {"name": {"score": 60, "note": "ok"}}, "red_flags": ["copycat of PEPE"],
            "verdict_de": "Solide Story.", "verdict_en": "Solid story."})
        return httpx.Response(200, json={"content": [{"type": "text", "text": out}], "stop_reason": "end_turn",
                                         "usage": {"input_tokens": 4000, "output_tokens": 400,
                                                   "server_tool_use": {"web_search_requests": 1}}})
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


def make(client, **ai):
    cfg = load_config()
    db = DB(":memory:")
    save_settings(db, {"enabled": True, "api_key": "sk-ant-test-1234567890", **ai})
    return AIAnalyst(Ctx(cfg=cfg, db=db), client=client), db


M = Market(mint="MINTX", symbol="XYZ", name="Xyz", price_sol=1e-6, created_at=time.time() - 3600,
           websites=["https://xyz.fun"], socials=[{"type": "twitter", "url": "https://x.com/xyz"}])


def test_parse_json_with_text_around():
    assert parse_json('blah {"a": 1} text {"score": 55, "x": {"y": 2}} end')["score"] == 55
    assert parse_json("no json") is None


def test_observe_mode_records_usage_and_never_blocks():
    calls = []
    ai, db = make(fake_api(score=10, calls=calls), mode="observe")
    v = asyncio.run(ai.check(M, {"description": "a frog"}))
    assert v.ok and v.details["ki_score"] == 10 and v.details["ki_urteil_en"] == "Solid story."
    row = db.one("SELECT * FROM ai_calls")
    assert row["input_tokens"] == 4000 and row["searches"] == 1
    assert abs(row["cost_usd"] - cost("claude-haiku-5-5", 4000, 400, 1)) < 1e-12
    assert abs(row["cost_usd"] - (0.0004 + 0.0002 + 0.01)) < 1e-9
    # Standard-Prüfungen: Website an -> web_fetch, Trend aus -> keine Websuche
    tools = [t["name"] for t in calls[0].get("tools", [])]
    assert tools == ["web_fetch"] and calls[0]["model"] == "claude-haiku-5-5"


def test_filter_mode_blocks_low_score_and_checks_toggle():
    calls = []
    ai, db = make(fake_api(score=30, calls=calls), mode="filter", min_score=50,
                  checks={"website": False, "trend": True, "red_flags": False})
    v = asyncio.run(ai.check(M, {}))
    assert not v.ok and "30" in v.reason
    prompt = calls[0]["messages"][0]["content"]
    assert "red flags" not in prompt.lower() and "trending" in prompt
    assert [t["name"] for t in calls[0]["tools"]] == ["web_search"]


def test_disabled_budget_and_failures_never_block():
    ai, db = make(fake_api(), enabled=False)
    assert asyncio.run(ai.check(M, {})).reason == "KI aus"
    ai, db = make(fake_api(status=529))
    v = asyncio.run(ai.check(M, {}))
    assert v.ok and db.one("SELECT ok FROM ai_calls")["ok"] == 0
    ai, db = make(fake_api(text="I cannot answer"))
    assert asyncio.run(ai.check(M, {})).ok
    ai, db = make(fake_api(), daily_budget_usd=0.001)
    db.x("INSERT INTO ai_calls(ts,cost_usd) VALUES(?,?)", (time.time(), 0.01))
    assert asyncio.run(ai.check(M, {})).reason == "KI-Budget erreicht"


def test_key_handling():
    db = DB(":memory:")
    save_settings(db, {"api_key": "sk-ant-abcdefghijklmnop"})
    assert settings(db)["api_key"] == "sk-ant-abcdefghijklmnop"
    save_settings(db, {"api_key": "sk-ant-…mnop"})          # maskierter Wert überschreibt nicht
    assert settings(db)["api_key"] == "sk-ant-abcdefghijklmnop"
    save_settings(db, {"clear_key": True})
    assert not (db.get("ai") or {}).get("api_key")


def test_ai_web_api(tmp_path):
    bot = make_bot(tmp_path)
    c = TestClient(create_app(bot))
    r = c.post("/api/ai", json={"enabled": True, "mode": "filter", "api_key": "sk-ant-abcdefghijklmnop",
                                "checks": {"trend": True}})
    assert r.status_code == 200
    d = c.get("/api/ai").json()
    assert d["settings"]["enabled"] and d["settings"]["checks"]["trend"]
    assert "api_key" not in d["settings"] and d["settings"]["api_key_masked"].endswith("mnop")
    assert "abcdefghij" not in json.dumps(d)                 # Key wird nie ausgeliefert
    assert set(d["usage"]) >= {"today", "week", "total", "daily", "recent"}
    assert c.post("/api/ai", json={"api_key": "falsch"}).status_code == 400


def test_credit_tracking_and_csv(tmp_path):
    ai, db = make(fake_api(score=80))
    save_settings(db, {"credit_usd": 0.02})
    asyncio.run(ai.check(M, {}))                          # kostet ~0,0106 $
    cr = ai.credit()
    assert cr["set_usd"] == 0.02 and cr["calls"] == 1 and abs(cr["remaining"] - (0.02 - cr["used_usd"])) < 1e-9
    ai._cache.clear()
    asyncio.run(ai.check(Market(mint="M2", symbol="B", price_sol=1e-6, created_at=time.time()), {}))
    ai._cache.clear()
    assert asyncio.run(ai.check(Market(mint="M3", symbol="C", price_sol=1e-6, created_at=time.time()), {})).reason \
        == "KI-Guthaben aufgebraucht"
    bot = make_bot(tmp_path)
    c = TestClient(create_app(bot))
    r = c.get("/api/ai/calls.csv")
    assert r.status_code == 200 and r.text.startswith("zeit_utc;token")
    assert c.post("/api/ai", json={"credit_usd": "5"}).json()["usage"]["credit"]["remaining"] == 5
