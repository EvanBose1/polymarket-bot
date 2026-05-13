"""
SQLite-backed ledger for signals, simulated fills, and real fills.

Every strategy tick should call ledger.record_signal(...) BEFORE deciding to trade,
and ledger.record_fill(...) when an order actually fills (or simulated under DRY_RUN).
This is the source of truth for the dashboard and the backtester.
"""
import sqlite3
import time
import json
import threading
from contextlib import contextmanager
from typing import Optional

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        strategy TEXT NOT NULL,
        market_id TEXT,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        intended_price REAL NOT NULL,
        edge REAL,
        meta TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        signal_id INTEGER,
        strategy TEXT NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        price REAL NOT NULL,
        size_shares REAL NOT NULL,
        usd REAL NOT NULL,
        dry_run INTEGER NOT NULL DEFAULT 1,
        order_id TEXT,
        FOREIGN KEY(signal_id) REFERENCES signals(id)
    )""",
    """CREATE TABLE IF NOT EXISTS resolutions (
        token_id TEXT PRIMARY KEY,
        resolved_price REAL NOT NULL,
        ts INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts)",
    "CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts)",
    "CREATE INDEX IF NOT EXISTS idx_fills_token ON fills(token_id)",
]

_lock = threading.Lock()


class Ledger:
    def __init__(self, path: str = "state/ledger.db"):
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        with self._conn() as c:
            for stmt in SCHEMA:
                c.execute(stmt)

    @contextmanager
    def _conn(self):
        with _lock:
            con = sqlite3.connect(self.path)
            con.row_factory = sqlite3.Row
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def record_signal(self, *, strategy: str, token_id: str, side: str,
                      intended_price: float, edge: Optional[float] = None,
                      market_id: Optional[str] = None,
                      meta: Optional[dict] = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO signals(ts, strategy, market_id, token_id, side, intended_price, edge, meta) VALUES(?,?,?,?,?,?,?,?)",
                (int(time.time()), strategy, market_id, token_id, side,
                 float(intended_price), edge, json.dumps(meta or {})),
            )
            return cur.lastrowid

    def record_fill(self, *, signal_id: Optional[int], strategy: str,
                    token_id: str, side: str, price: float,
                    size_shares: float, dry_run: bool,
                    order_id: Optional[str] = None) -> int:
        usd = price * size_shares
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO fills(ts, signal_id, strategy, token_id, side, price, size_shares, usd, dry_run, order_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), signal_id, strategy, token_id, side,
                 float(price), float(size_shares), float(usd),
                 1 if dry_run else 0, order_id),
            )
            return cur.lastrowid

    def record_resolution(self, token_id: str, resolved_price: float):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO resolutions(token_id, resolved_price, ts) VALUES(?,?,?)",
                (token_id, float(resolved_price), int(time.time())),
            )

    # ----- read helpers used by dashboard / backtester -----

    def open_positions(self):
        """Aggregate fills into net positions per token."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT token_id, strategy,
                       SUM(CASE WHEN side='BUY' THEN size_shares ELSE -size_shares END) AS net_shares,
                       SUM(CASE WHEN side='BUY' THEN usd ELSE -usd END) AS net_usd
                FROM fills GROUP BY token_id, strategy
            """).fetchall()
            return [dict(r) for r in rows if abs(r["net_shares"]) > 1e-6]

    def realised_pnl(self):
        """P&L for resolved markets only (sum of position * resolved_price - cost)."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT f.token_id, f.strategy,
                       SUM(CASE WHEN f.side='BUY' THEN f.size_shares ELSE -f.size_shares END) AS net_shares,
                       SUM(CASE WHEN f.side='BUY' THEN f.usd ELSE -f.usd END) AS cost,
                       r.resolved_price
                FROM fills f JOIN resolutions r ON r.token_id = f.token_id
                GROUP BY f.token_id, f.strategy
            """).fetchall()
            total = 0.0
            by_strategy = {}
            for r in rows:
                pnl = r["net_shares"] * r["resolved_price"] - r["cost"]
                total += pnl
                by_strategy[r["strategy"]] = by_strategy.get(r["strategy"], 0) + pnl
            return {"total": total, "by_strategy": by_strategy}

    def recent_signals(self, limit: int = 50):
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_fills(self, limit: int = 50):
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM fills ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
