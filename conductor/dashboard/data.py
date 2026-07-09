"""Read-only data access for the dashboard.

Every function opens its own short-lived read-only SQLite connection and
returns plain Python data (dataclasses / lists / dicts) so it can be unit
tested against a tmp db with no proxy and no terminal.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass


ROW_COLS = (
    "id, ts, harness, tag, rule, requested_model, routed_model, "
    "input_tokens, output_tokens, cost_usd, latency_ms, stream, "
    "status, est_input_tokens"
)


@dataclass
class RequestRow:
    """One ledger row, typed. Field order mirrors SELECT order in ROW_COLS."""

    id: int
    ts: float
    harness: str | None
    tag: str | None
    rule: str | None
    requested_model: str | None
    routed_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    stream: int | None
    status: int | None
    est_input_tokens: int | None

    @property
    def escalated(self) -> bool:
        """True when this row is an escalation retry (rule 'escalated:<reason>')."""
        return bool(self.rule and self.rule.startswith("escalated:"))


def connect_ro(db_path: str) -> sqlite3.Connection:
    """Open db read-only via URI. Raises sqlite3.OperationalError if the file
    does not exist or cannot be opened (callers decide how to surface it)."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def db_exists(db_path: str) -> bool:
    """os.path.isfile check so the live view can show 'waiting for ledger'."""
    return os.path.isfile(db_path)


def _row_from_tuple(t: tuple) -> RequestRow:
    return RequestRow(*t)


def fetch_new_rows(db_path: str, after_id: int, limit: int = 500) -> list[RequestRow]:
    """Tail cursor. Rows with id > after_id, ascending, capped at limit."""
    with connect_ro(db_path) as c:
        rows = c.execute(
            f"SELECT {ROW_COLS} FROM requests WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()
    return [_row_from_tuple(r) for r in rows]


def fetch_recent_rows(db_path: str, n: int) -> list[RequestRow]:
    """Last n rows by id (for `tail` and initial live backfill), ascending order."""
    with connect_ro(db_path) as c:
        rows = c.execute(
            f"""SELECT * FROM (
                SELECT {ROW_COLS} FROM requests ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC""",
            (n,),
        ).fetchall()
    return [_row_from_tuple(r) for r in rows]


def fetch_row(db_path: str, row_id: int) -> RequestRow | None:
    """Single row for `show`; None when the id doesn't exist."""
    with connect_ro(db_path) as c:
        row = c.execute(
            f"SELECT {ROW_COLS} FROM requests WHERE id = ?",
            (row_id,),
        ).fetchone()
    return _row_from_tuple(row) if row else None
