import sqlite3

from memebot.config import load_config
from memebot.db import DB


def test_user_config_overlays_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('mode = "paper"\n[risk]\nmax_open_positions = 2\n')   # alte Config ohne neue Felder
    cfg = load_config(p)
    assert cfg.risk.max_open_positions == 2                 # eigener Wert gewinnt
    assert cfg.risk.position_pct == 0.03                    # Rest aus den Standardwerten
    assert cfg["position_poll_seconds"] == 2                # neue Einstellung automatisch da


def test_db_migrates_old_candidates_table(tmp_path):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE candidates (mint TEXT PRIMARY KEY, source TEXT, symbol TEXT, first_seen REAL NOT NULL,"
                " last_checked REAL, status TEXT NOT NULL DEFAULT 'new', reason TEXT, score REAL)")
    con.execute("INSERT INTO candidates(mint, first_seen) VALUES('A', 1)")
    con.commit(); con.close()
    db = DB(path)
    db.set_candidate("A", "waiting", "x", 52, "SYM", {"trend": 0})
    row = db.one("SELECT * FROM candidates WHERE mint='A'")
    assert row["score"] == 52 and '"trend"' in row["details"]
