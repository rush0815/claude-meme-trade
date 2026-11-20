"""SQLite-Speicher für Positionen, Fills, Agenten-Log, Kapitalverlauf und Einstellungen."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    mint TEXT NOT NULL,
    symbol TEXT,
    name TEXT,
    pair_address TEXT,
    dex TEXT,
    status TEXT NOT NULL DEFAULT 'open',          -- open | closed
    opened_at REAL NOT NULL,
    closed_at REAL,
    size_sol REAL NOT NULL,                       -- eingesetzte SOL inkl. Gebühren
    tokens_total REAL NOT NULL,
    tokens_remaining REAL NOT NULL,
    entry_price REAL NOT NULL,                    -- SOL pro Token
    entry_liquidity_usd REAL,
    last_price REAL,
    high_price REAL,
    sol_received REAL NOT NULL DEFAULT 0,
    pnl_sol REAL,
    pnl_pct REAL,
    tp_hits INTEGER NOT NULL DEFAULT 0,
    score REAL,
    score_details TEXT,
    security TEXT,
    exit_reason TEXT,
    force_close INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pos_status ON positions(status);
CREATE INDEX IF NOT EXISTS ix_pos_mint ON positions(mint);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL REFERENCES positions(id),
    ts REAL NOT NULL,
    side TEXT NOT NULL,                           -- buy | sell
    sol_amount REAL NOT NULL,
    token_amount REAL NOT NULL,
    price REAL NOT NULL,
    reason TEXT,
    signature TEXT,
    fee_sol REAL NOT NULL DEFAULT 0,
    market_price REAL                             -- Marktpreis (DexScreener) im Moment der Entscheidung
);
CREATE INDEX IF NOT EXISTS ix_fills_pos ON fills(position_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    mint TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS candidates (
    mint TEXT PRIMARY KEY,
    source TEXT,
    symbol TEXT,
    first_seen REAL NOT NULL,
    last_checked REAL,
    status TEXT NOT NULL DEFAULT 'new',           -- new | waiting | rejected | bought | expired
    reason TEXT,
    score REAL,
    details TEXT
);

CREATE TABLE IF NOT EXISTS equity (
    ts REAL PRIMARY KEY,
    equity_sol REAL NOT NULL,
    cash_sol REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Spalten nachrüsten, die in älteren Datenbanken fehlen."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(candidates)")}
        if "details" not in cols:
            self.conn.execute("ALTER TABLE candidates ADD COLUMN details TEXT")
        fcols = {r[1] for r in self.conn.execute("PRAGMA table_info(fills)")}
        if "market_price" not in fcols:
            self.conn.execute("ALTER TABLE fills ADD COLUMN market_price REAL")

    # ------------------------------------------------------------- basics
    def q(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple | list = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    # ------------------------------------------------------------- settings
    def get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set(self, key: str, value: Any) -> None:
        self.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

    # ------------------------------------------------------------- events
    def log(self, agent: str, message: str, level: str = "info", mint: str | None = None) -> None:
        self.x("INSERT INTO events(ts,agent,level,message,mint) VALUES(?,?,?,?,?)",
               (time.time(), agent, level, message, mint))

    def prune(self, keep_events: int = 20000) -> None:
        self.x("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0) - ? FROM events)", (keep_events,))
        self.x("DELETE FROM candidates WHERE status IN ('rejected','expired') AND first_seen < ?",
               (time.time() - 3 * 86400,))

    # ------------------------------------------------------------- positions
    def open_positions(self, mode: str) -> list[dict]:
        return self.q("SELECT * FROM positions WHERE status='open' AND mode=? ORDER BY opened_at", (mode,))

    def position(self, pid: int) -> dict | None:
        return self.one("SELECT * FROM positions WHERE id=?", (pid,))

    def update_position(self, pid: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE positions SET {cols} WHERE id=?", (*fields.values(), pid))

    def insert_position(self, **fields: Any) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        return self.x(f"INSERT INTO positions({cols}) VALUES({marks})", tuple(fields.values()))

    def add_fill(self, position_id: int, side: str, sol_amount: float, token_amount: float,
                 price: float, reason: str, signature: str | None = None, fee_sol: float = 0.0,
                 market_price: float | None = None) -> None:
        self.x("INSERT INTO fills(position_id,ts,side,sol_amount,token_amount,price,reason,signature,fee_sol,"
               "market_price) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (position_id, time.time(), side, sol_amount, token_amount, price, reason, signature, fee_sol,
                market_price))

    def realized_pnl_since(self, mode: str, since_ts: float) -> float:
        r = self.one("SELECT COALESCE(SUM(pnl_sol),0) AS s FROM positions WHERE mode=? AND status='closed'"
                     " AND closed_at>=?", (mode, since_ts))
        return float(r["s"]) if r else 0.0

    def last_trade_of(self, mint: str) -> dict | None:
        return self.one("SELECT * FROM positions WHERE mint=? ORDER BY opened_at DESC LIMIT 1", (mint,))

    # ------------------------------------------------------------- candidates
    def upsert_candidate(self, mint: str, source: str, symbol: str | None = None) -> bool:
        """True, wenn der Kandidat neu ist."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO candidates(mint,source,symbol,first_seen,status) VALUES(?,?,?,?, 'new')",
                (mint, source, symbol, time.time()))
            return cur.rowcount > 0

    def set_candidate(self, mint: str, status: str, reason: str = "", score: float | None = None,
                      symbol: str | None = None, details: dict | None = None) -> None:
        self.x("UPDATE candidates SET status=?, reason=?, score=COALESCE(?,score), symbol=COALESCE(?,symbol),"
               " details=COALESCE(?,details), last_checked=? WHERE mint=?",
               (status, reason, score, symbol, json.dumps(details) if details is not None else None,
                time.time(), mint))

    def due_candidates(self, recheck_s: float, limit: int) -> list[dict]:
        return self.q("SELECT * FROM candidates WHERE status IN ('new','waiting') AND"
                      " (last_checked IS NULL OR last_checked < ?)"
                      " ORDER BY last_checked IS NOT NULL, last_checked ASC LIMIT ?",   # neue zuerst, dann am längsten ungeprüfte
                      (time.time() - recheck_s, limit))

    # ------------------------------------------------------------- equity
    def snapshot_equity(self, equity: float, cash: float) -> None:
        self.x("INSERT OR REPLACE INTO equity(ts,equity_sol,cash_sol) VALUES(?,?,?)", (time.time(), equity, cash))
