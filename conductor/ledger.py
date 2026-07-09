"""Append-only SQLite ledger. Every request through the proxy lands here."""

import sqlite3
import time
from pathlib import Path

import yaml

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    harness TEXT,              -- from user-agent, best effort
    tag TEXT,                  -- conductor tag if provided
    rule TEXT,                 -- policy rule that fired
    requested_model TEXT,      -- what the client asked for
    routed_model TEXT,         -- what we actually called
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,             -- NULL when pricing unknown
    latency_ms INTEGER,
    stream INTEGER,
    status INTEGER,
    est_input_tokens INTEGER   -- pre-call estimate used for routing
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
"""


class Pricing:
    def __init__(self, path: str | Path):
        cfg = yaml.safe_load(Path(path).read_text()) or {}
        self.models = cfg.get("models", {})

    def cost(self, model: str, in_tok: int | None, out_tok: int | None) -> float | None:
        p = self.models.get(model)
        if not p or in_tok is None or out_tok is None:
            return None
        if not p.get("input") and not p.get("output"):
            return None  # placeholder zeros -> treat as unknown
        return (in_tok * p["input"] + out_tok * p["output"]) / 1_000_000


class Ledger:
    def __init__(self, db_path: str | Path = "conductor.db"):
        self.db_path = str(db_path)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def record(self, **row) -> None:
        row.setdefault("ts", time.time())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._conn() as c:
            c.execute(f"INSERT INTO requests ({cols}) VALUES ({marks})", list(row.values()))

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._conn() as c:
            return c.execute(sql, params).fetchall()
