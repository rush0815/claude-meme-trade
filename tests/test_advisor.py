"""Einstellungs-Berater mit nachgebauter Anthropic-API."""
import asyncio
import json
import time

import httpx
from fastapi.testclient import TestClient

from memebot.agents.ai_analyst import save_settings
from memebot.web.app import create_app
from tests.test_web_and_sim import make_bot

ANSWER = {
    "confidence": "medium", "summary_de": "Zu viele Stop-Losses.", "summary_en": "Too many stop-losses.",
    "changes": [
        {"key": "strategy.min_score", "value": 72, "reason_de": "Unter 70 verliert.", "reason_en": "Below 70 loses."},
        {"key": "exit.stop_loss_pct", "value": 500, "reason_de": "ungültig", "reason_en": "invalid"},     # außerhalb
        {"key": "risk.api_key", "value": 1, "reason_de": "gibt es nicht", "reason_en": "no such"},          # unbekannt
        {"key": "exit.mode", "value": "simple", "reason_de": "einfacher", "reason_en": "simpler"},
    ],
    "watch_de": "Trefferquote", "watch_en": "win rate"}


def add_trades(bot, n, pnl=0.001, t0=None):
    t0 = t0 or time.time() - 86400
    for i in range(n):
        bot.db.insert_position(mode="paper", mint=f"A{t0}{i}", status="closed", opened_at=t0 + i * 60,
                               closed_at=t0 + i * 60 + 30, size_sol=0.03, tokens_total=1, tokens_remaining=0,
                               entry_price=1, pnl_sol=pnl if i % 2 else -pnl / 2, pnl_pct=10 if i % 2 else -5,
                               exit_reason="Stop-Loss" if i % 3 == 0 else "Trailing-Stop", score=70)


def wire(bot, calls):
    def h(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(ANSWER)}],
                                         "stop_reason": "end_turn", "usage": {"input_tokens": 6000, "output_tokens": 600}})
    bot.ai.client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    save_settings(bot.db, {"api_key": "sk-ant-test-1234567890"})


def test_advisor_requires_enough_trades_and_filters_invalid(tmp_path):
    bot = make_bot(tmp_path)
    calls = []
    wire(bot, calls)
    c = TestClient(create_app(bot))
    add_trades(bot, 10)
    assert c.post("/api/advisor/suggest", json={}).status_code == 400          # zu wenig Trades
    add_trades(bot, 25, t0=time.time() - 50000)                                  # alle vor der Übernahme
    r = c.post("/api/advisor/suggest", json={"model": "claude-haiku-5-5"})
    assert r.status_code == 200, r.text
    j = r.json()
    keys = [ch["key"] for ch in j["changes"]]
    assert keys == ["strategy.min_score", "exit.mode"]                          # ungültige verworfen
    sent = calls[0]["messages"][0]["content"]
    assert "sk-ant" not in sent and "editable_settings" in sent                 # keine Keys im Prompt
    assert calls[0]["model"] == "claude-haiku-5-5"
    assert bot.cfg["strategy"]["min_score"] == 65                               # nichts automatisch übernommen
    r = c.post(f"/api/advisor/{j['id']}/apply", json={"keys": ["strategy.min_score"]})
    assert r.status_code == 200 and bot.cfg["strategy"]["min_score"] == 72
    bot.db.x("UPDATE advisor_runs SET applied_at=?", (time.time() - 1000,))
    add_trades(bot, 6, t0=time.time() - 900)                                    # Trades nach der Übernahme
    hist = c.get("/api/advisor").json()["history"]
    assert hist[0]["applied"][0]["old"] == 65 and hist[0]["applied"][0]["new"] == 72
    assert hist[0]["after"]["trades"] == 6 and hist[0]["before"]["trades"] >= 6
    usage = c.get("/api/ai").json()["usage"]
    assert usage["recent"][0]["symbol"] == "Einstellungs-Vorschlag" and usage["recent"][0]["cost_usd"] > 0
