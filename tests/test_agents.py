import asyncio
import time

import pytest

from memebot.agents.base import Ctx
from memebot.agents.executor import PaperExecutor
from memebot.agents.position_manager import PositionManager
from memebot.agents.risk_manager import Portfolio, RiskManager
from memebot.agents.rug_analyst import RugAnalyst
from memebot.agents.strategist import Strategist
from memebot.config import load_config
from memebot.db import DB
from memebot.models import Market, Signal
from memebot.sources.rugcheck import summarize_report


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ctx():
    cfg = load_config()
    cfg["mode"] = "paper"
    db = DB(":memory:")
    db.set("paper_cash", 1.0)
    return Ctx(cfg=cfg, db=db, jup=None)


def good_market(**kw):
    base = dict(mint="M1", symbol="GOOD", price_sol=1e-6, liquidity_usd=60_000, fdv=400_000,
                created_at=time.time() - 45 * 60, change_m5=8, change_h1=30, change_h6=40,
                buys_m5=90, sells_m5=40, volume_m5=12_000, volume_h1=60_000)
    base.update(kw)
    return Market(**base)


# ---------------------------------------------------------------- Stratege
def test_prefilter_accepts_good(ctx):
    assert Strategist(ctx).prefilter(good_market()).ok


@pytest.mark.parametrize("kw,retry", [
    ({"created_at": time.time() - 5 * 60}, True),          # zu jung
    ({"created_at": time.time() - 30 * 3600}, False),      # zu alt
    ({"change_m5": 60}, True),                             # FOMO-Spitze
    ({"buys_m5": 30, "sells_m5": 40}, True),               # Verkaufsdruck
    ({"liquidity_usd": 5000}, True),
])
def test_prefilter_rejects(ctx, kw, retry):
    v = Strategist(ctx).prefilter(good_market(**kw))
    assert not v.ok and v.retry == retry


def test_score_orders_quality(ctx):
    s = Strategist(ctx)
    strong, _ = s.score(good_market())
    weak, _ = s.score(good_market(buys_m5=45, sells_m5=40, volume_m5=3000, change_m5=2, change_h1=-5))
    assert 0 <= weak < strong <= 100
    assert strong >= ctx.cfg.strategy.min_score


# ---------------------------------------------------------------- Rug-Analyst
def report(**kw):
    r = {"score": 200, "mintAuthority": None, "freezeAuthority": None, "rugged": False,
         "topHolders": [{"address": "POOL", "pct": 40}] + [{"address": f"a{i}", "pct": 2} for i in range(10)],
         "knownAccounts": {"POOL": {"type": "AMM"}}, "markets": [{"lp": {"lpLockedPct": 100}}], "risks": []}
    r.update(kw)
    return r


def test_rug_pool_excluded_and_passes(ctx):
    s = summarize_report(report())
    assert s["top10_pct"] == 20 and s["max_holder_pct"] == 2
    assert RugAnalyst(ctx).evaluate(s).ok


def test_rug_market_accounts_as_objects(ctx):
    """Echte RugCheck-Berichte enthalten Konten als Objekte – darf nicht abstürzen."""
    rep = report(markets=[{"pubkey": "MKT", "liquidityA": "LA",
                           "liquidityAAccount": {"mint": "M", "owner": "POOLAUTH", "amount": 5},
                           "liquidityBAccount": {"mint": "SOL", "owner": "POOLAUTH"},
                           "lp": {"lpLockedPct": 100}}],
                 topHolders=[{"address": "LA", "owner": "POOLAUTH", "pct": 30}]
                 + [{"address": f"a{i}", "owner": f"o{i}", "pct": 2} for i in range(10)])
    s = summarize_report(rep)
    assert s["top10_pct"] == 20                 # Pool-Konto nicht als Holder gezählt
    assert RugAnalyst(ctx).evaluate(s).ok


@pytest.mark.parametrize("kw", [
    {"mintAuthority": "X"}, {"freezeAuthority": "X"}, {"rugged": True},
    {"markets": [{"lp": {"lpLockedPct": 10}}]},
    {"risks": [{"name": "Single holder ownership", "level": "danger"}]},
    {"topHolders": [{"address": "w", "pct": 30}]},
])
def test_rug_rejects(ctx, kw):
    assert not RugAnalyst(ctx).evaluate(summarize_report(report(**kw))).ok


# ---------------------------------------------------------------- Risk-Manager
def test_risk_sizing_and_limits(ctx):
    pf = Portfolio(ctx)
    rm = RiskManager(ctx, pf)
    v = run(rm.approve(good_market(), 80))
    assert v.ok
    size = v.details["size_sol"]
    assert ctx.cfg.risk.min_position_sol <= size <= 1.0 * ctx.cfg.risk.position_pct * 1.25 + 1e-9
    ctx.db.set("paused", True)
    assert not run(rm.approve(good_market(), 80)).ok


def test_drawdown_kill_switch(ctx):
    rm = RiskManager(ctx, Portfolio(ctx))
    run(rm.update_limits())
    ctx.db.set("paper_cash", 0.6)   # -40 %
    run(rm.update_limits())
    assert ctx.db.get("killed") is True


def test_daily_loss_limit(ctx):
    rm = RiskManager(ctx, Portfolio(ctx))
    run(rm.update_limits())
    ctx.db.insert_position(mode="paper", mint="X", status="closed", opened_at=time.time(), closed_at=time.time(),
                           size_sol=0.2, tokens_total=1, tokens_remaining=0, entry_price=1, pnl_sol=-0.15)
    assert not rm.capacity().ok


# ---------------------------------------------------------------- Position-Manager
def open_pos(ctx, price=1e-6):
    pm = PositionManager(ctx, PaperExecutor(ctx), Portfolio(ctx))
    m = good_market(price_sol=price)
    pid = run(pm.open(Signal(m, 80, {}, {"decimals": 6}), 0.1))
    return pm, pid


def test_paper_buy_reduces_cash(ctx):
    pm, pid = open_pos(ctx)
    p = ctx.db.position(pid)
    assert abs(ctx.db.get("paper_cash") - 0.9) < 1e-9
    assert p["tokens_total"] > 0 and p["entry_price"] > 1e-6   # Abschlag macht Einstieg teurer


def test_stop_loss(ctx):
    pm, pid = open_pos(ctx)
    entry = ctx.db.position(pid)["entry_price"]
    run(pm.tick({"M1": good_market(price_sol=entry * 0.75)}))
    p = ctx.db.position(pid)
    assert p["status"] == "closed" and p["exit_reason"] == "Stop-Loss" and p["pnl_sol"] < 0


def test_take_profit_then_trailing(ctx):
    ctx.cfg["exit"].update(take_profit_levels=[[40, 0.33], [100, 0.33]], trailing_activate_pct=30,
                           trailing_stop_pct=18, breakeven_activate_pct=0)
    pm, pid = open_pos(ctx)
    entry = ctx.db.position(pid)["entry_price"]
    run(pm.tick({"M1": good_market(price_sol=entry * 1.45)}))
    p = ctx.db.position(pid)
    assert p["status"] == "open" and p["tp_hits"] == 1
    assert abs(p["tokens_remaining"] / p["tokens_total"] - 0.67) < 0.01
    run(pm.tick({"M1": good_market(price_sol=entry * 1.9)}))                # neues Hoch
    run(pm.tick({"M1": good_market(price_sol=entry * 1.9 * 0.8)}))          # -20 % vom Hoch
    p = ctx.db.position(pid)
    assert p["status"] == "closed" and p["exit_reason"] == "Trailing-Stop" and p["pnl_sol"] > 0


def test_liquidity_drop_exit(ctx):
    pm, pid = open_pos(ctx)
    run(pm.tick({"M1": good_market(liquidity_usd=10_000)}))
    assert ctx.db.position(pid)["exit_reason"] == "Liquidität eingebrochen"


def test_force_close(ctx):
    pm, pid = open_pos(ctx)
    ctx.db.update_position(pid, force_close=1)
    run(pm.tick({}))
    assert ctx.db.position(pid)["status"] == "closed"


# ---------------------------------------------------------------- Phasen-Analyst
from memebot.agents.phase_analyst import PhaseAnalyst, analyse  # noqa: E402


def candles_from(closes, step_s=900):
    t0 = time.time() - len(closes) * step_s
    return [[t0 + i * step_s, c, c * 1.5, c * 0.9, c, 1000] for i, c in enumerate(closes)]


def test_phase_rejects_dead_coin_like_ogta(ctx):
    # Hype auf 1,2M, Absturz auf 0,16M, kleiner Hüpfer am Ende (wie OGTA)
    closes = [0.1, 0.8, 1.2, 1.0, 0.9, 0.6, 0.45, 0.5, 0.6, 0.4, 0.3, 0.2, 0.17, 0.16, 0.19]
    v = PhaseAnalyst(ctx).judge(analyse(candles_from(closes)))
    assert not v.ok and "Hype vorbei" in v.reason


def test_phase_accepts_early_trend(ctx):
    closes = [0.1, 0.12, 0.15, 0.14, 0.18, 0.22, 0.21, 0.25]
    v = PhaseAnalyst(ctx).judge(analyse(candles_from(closes)))
    assert v.ok


def test_phase_ignores_single_wick(ctx):
    c = candles_from([0.2, 0.25, 0.3, 0.28, 0.31])
    c[2][2] = 3.0                                   # Docht bis 10x – zählt nicht als Hoch
    assert PhaseAnalyst(ctx).judge(analyse(c)).ok


def test_prefilter_rejects_6h_downtrend(ctx):
    v = Strategist(ctx).prefilter(good_market(change_h6=-60))
    assert not v.ok and "Hype vorbei" in v.reason


def test_breakeven_stop(ctx):
    ctx.cfg["exit"].update(breakeven_activate_pct=25, breakeven_floor_pct=3, take_profit_levels=[[30, 0.33]],
                           trailing_activate_pct=50)
    pm, pid = open_pos(ctx)
    entry = ctx.db.position(pid)["entry_price"]
    run(pm.tick({"M1": good_market(price_sol=entry * 1.27)}))    # +27 %: Break-even scharf, noch kein TP
    assert ctx.db.position(pid)["status"] == "open"
    run(pm.tick({"M1": good_market(price_sol=entry * 1.02)}))    # zurück auf +2 %
    p = ctx.db.position(pid)
    assert p["status"] == "closed" and p["exit_reason"] == "Break-even-Stop"


def test_default_exit_ladder_is_consistent(ctx):
    e = ctx.cfg["exit"]
    tp1 = e["take_profit_levels"][0][0]
    assert e["breakeven_activate_pct"] < tp1 < e["trailing_activate_pct"]
    # frühester Trailing-Ausstieg liegt im Plus und über dem Break-even-Boden
    assert (1 + e["trailing_activate_pct"] / 100) * (1 - e["trailing_stop_pct"] / 100) - 1 > e["breakeven_floor_pct"] / 100
