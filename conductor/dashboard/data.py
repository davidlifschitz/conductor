"""Read-only data access for the dashboard.

Every function opens its own short-lived read-only SQLite connection and
returns plain Python data (dataclasses / lists / dicts) so it can be unit
tested against a tmp db with no proxy and no terminal.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

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


@dataclass
class Summary:
    """Aggregates over a window: per-model rows, per-rule rows, totals."""

    by_model: list[tuple[str, int, int, int, float | None, int]]
    # (routed_model, calls, in_tok, out_tok, cost_or_None, unpriced_count)
    by_rule: list[tuple[str, int, float | None]]
    # (rule, calls, cost_or_None)
    total_calls: int
    total_cost: float | None  # None when every row is unpriced
    escalation_count: int
    error_count: int  # status is not 200 (NULL counts as error)


@dataclass
class Health:
    """Proxy /health probe result."""

    up: bool
    default_model: str | None  # from /health JSON when up
    error: str | None  # short reason when down ('connect timeout', ...)


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
    with contextlib.closing(connect_ro(db_path)) as c:
        rows = c.execute(
            f"SELECT {ROW_COLS} FROM requests WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()
    return [_row_from_tuple(r) for r in rows]


def fetch_recent_rows(db_path: str, n: int) -> list[RequestRow]:
    """Last n rows by id (for `tail` and initial live backfill), ascending order."""
    with contextlib.closing(connect_ro(db_path)) as c:
        rows = c.execute(
            f"""SELECT * FROM (
                SELECT {ROW_COLS} FROM requests ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC""",
            (n,),
        ).fetchall()
    return [_row_from_tuple(r) for r in rows]


def fetch_row(db_path: str, row_id: int) -> RequestRow | None:
    """Single row for `show`; None when the id doesn't exist."""
    with contextlib.closing(connect_ro(db_path)) as c:
        row = c.execute(
            f"SELECT {ROW_COLS} FROM requests WHERE id = ?",
            (row_id,),
        ).fetchone()
    return _row_from_tuple(row) if row else None


def fetch_summary(db_path: str, since_ts: float) -> Summary:
    """All aggregate queries for the stats pane / `stats` subcommand."""
    with contextlib.closing(connect_ro(db_path)) as c:
        by_model = c.execute(
            """SELECT routed_model, COUNT(*), COALESCE(SUM(input_tokens),0),
                      COALESCE(SUM(output_tokens),0), SUM(cost_usd), SUM(cost_usd IS NULL)
               FROM requests WHERE ts >= ?
               GROUP BY routed_model ORDER BY SUM(cost_usd) DESC""",
            (since_ts,),
        ).fetchall()
        by_rule = c.execute(
            """SELECT rule, COUNT(*), SUM(cost_usd)
               FROM requests WHERE ts >= ?
               GROUP BY rule ORDER BY 2 DESC""",
            (since_ts,),
        ).fetchall()
        totals = c.execute(
            """SELECT COUNT(*),
                      SUM(cost_usd),
                      SUM(CASE WHEN rule LIKE 'escalated:%' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN status IS NULL OR status != 200 THEN 1 ELSE 0 END)
               FROM requests WHERE ts >= ?""",
            (since_ts,),
        ).fetchone()

    total_calls = int(totals[0] or 0)
    total_cost = totals[1]  # may be None when all unpriced / no rows
    escalation_count = int(totals[2] or 0)
    error_count = int(totals[3] or 0)

    return Summary(
        by_model=[
            (m, int(n), int(i or 0), int(o or 0), cost, int(unpriced or 0))
            for m, n, i, o, cost, unpriced in by_model
        ],
        by_rule=[(r, int(n), cost) for r, n, cost in by_rule],
        total_calls=total_calls,
        total_cost=total_cost,
        escalation_count=escalation_count,
        error_count=error_count,
    )


def fetch_health(proxy_url: str, timeout: float = 0.8) -> Health:
    """Sync GET {proxy_url}/health via httpx. Never raises: any exception or
    non-200 becomes Health(up=False, error=<short class name / status>)."""
    url = proxy_url.rstrip("/") + "/health"
    try:
        r = httpx.get(url, timeout=timeout)
        if r.status_code != 200:
            return Health(up=False, default_model=None, error=str(r.status_code))
        data = r.json()
        return Health(
            up=True,
            default_model=data.get("default_model"),
            error=None,
        )
    except Exception as e:
        return Health(up=False, default_model=None, error=type(e).__name__)


def load_ladder(policy_path: str) -> list[str]:
    """escalation.ladder from policy.yaml; [] when file missing/unparseable."""
    try:
        cfg = yaml.safe_load(Path(policy_path).read_text()) or {}
        ladder = (cfg.get("escalation") or {}).get("ladder") or []
        return list(ladder)
    except Exception:
        return []
