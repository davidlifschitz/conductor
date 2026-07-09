"""FastAPI JSON API for the Conductor web dashboard."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from conductor.dashboard.data import (
    RequestRow,
    Summary,
    connect_ro,
    db_exists,
    fetch_health,
    fetch_new_rows,
    fetch_recent_rows,
    fetch_row,
    fetch_summary,
    load_ladder,
)


def _row_to_dict(row: RequestRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "ts": row.ts,
        "harness": row.harness,
        "tag": row.tag,
        "rule": row.rule,
        "requested_model": row.requested_model,
        "routed_model": row.routed_model,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd": row.cost_usd,
        "latency_ms": row.latency_ms,
        "stream": row.stream,
        "status": row.status,
        "est_input_tokens": row.est_input_tokens,
        "escalated": row.escalated,
    }


def _summary_to_dict(summary: Summary) -> dict[str, Any]:
    return {
        "by_model": [list(entry) for entry in summary.by_model],
        "by_rule": [list(entry) for entry in summary.by_rule],
        "total_calls": summary.total_calls,
        "total_cost": summary.total_cost,
        "escalation_count": summary.escalation_count,
        "error_count": summary.error_count,
    }


def _empty_summary_dict() -> dict[str, Any]:
    return _summary_to_dict(
        Summary(
            by_model=[],
            by_rule=[],
            total_calls=0,
            total_cost=None,
            escalation_count=0,
            error_count=0,
        )
    )


def _with_db_retry(fn, *args, **kwargs):
    """Retry once after 0.2s on sqlite3.OperationalError."""
    try:
        return fn(*args, **kwargs)
    except sqlite3.OperationalError:
        time.sleep(0.2)
        return fn(*args, **kwargs)


def _bucket_timestamps(days: float, buckets: int) -> list[float]:
    now = time.time()
    since = now - days * 86400
    width = (days * 86400) / buckets
    return [since + i * width for i in range(buckets)]


def _empty_spend_trend(days: float, buckets: int) -> list[dict[str, Any]]:
    return [{"ts": ts, "cost": 0.0} for ts in _bucket_timestamps(days, buckets)]


def fetch_spend_trend(db_path: str, days: float, buckets: int) -> list[dict[str, Any]]:
    """SUM(cost_usd) in equal time slices over the last `days` days."""
    now = time.time()
    since = now - days * 86400
    width = (days * 86400) / buckets
    result: list[dict[str, Any]] = []

    with closing(connect_ro(db_path)) as c:
        for i in range(buckets):
            start = since + i * width
            end = since + (i + 1) * width
            total_cost, row_count, null_count = c.execute(
                """SELECT SUM(cost_usd), COUNT(*), SUM(cost_usd IS NULL)
                   FROM requests WHERE ts >= ? AND ts < ?""",
                (start, end),
            ).fetchone()
            if row_count == 0:
                cost: float | None = 0.0
            elif total_cost is None:
                cost = None
            else:
                cost = float(total_cost)
            result.append({"ts": start, "cost": cost})

    return result


def create_app(db_path: str, proxy_url: str, policy_path: str) -> FastAPI:
    app = FastAPI()

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        h = fetch_health(proxy_url)
        return {"up": h.up, "default_model": h.default_model, "error": h.error}

    @app.get("/api/ladder")
    def api_ladder() -> dict[str, list[str]]:
        return {"ladder": load_ladder(policy_path)}

    @app.get("/api/rows")
    def api_rows(after_id: int = 0, limit: int = 500) -> dict[str, list[dict[str, Any]]]:
        if not db_exists(db_path):
            return {"rows": []}
        try:
            rows = _with_db_retry(fetch_new_rows, db_path, after_id, limit)
        except sqlite3.OperationalError:
            return {"rows": []}
        return {"rows": [_row_to_dict(r) for r in rows]}

    @app.get("/api/rows/recent")
    def api_rows_recent(n: int = 200) -> dict[str, list[dict[str, Any]]]:
        if not db_exists(db_path):
            return {"rows": []}
        try:
            rows = _with_db_retry(fetch_recent_rows, db_path, n)
        except sqlite3.OperationalError:
            return {"rows": []}
        return {"rows": [_row_to_dict(r) for r in rows]}

    @app.get("/api/rows/{row_id}")
    def api_row(row_id: int) -> dict[str, Any]:
        if not db_exists(db_path):
            raise HTTPException(status_code=404, detail="not found")
        try:
            row = _with_db_retry(fetch_row, db_path, row_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=404, detail="not found") from None
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        return _row_to_dict(row)

    @app.get("/api/summary")
    def api_summary(days: float = 1) -> dict[str, Any]:
        if not db_exists(db_path):
            return _empty_summary_dict()
        since = time.time() - days * 86400
        try:
            summary = _with_db_retry(fetch_summary, db_path, since)
        except sqlite3.OperationalError:
            return _empty_summary_dict()
        return _summary_to_dict(summary)

    @app.get("/api/spend_trend")
    def api_spend_trend(days: float = 1, buckets: int = 24) -> dict[str, list[dict[str, Any]]]:
        if not db_exists(db_path):
            return {"buckets": _empty_spend_trend(days, buckets)}
        try:
            trend = _with_db_retry(fetch_spend_trend, db_path, days, buckets)
        except sqlite3.OperationalError:
            return {"buckets": _empty_spend_trend(days, buckets)}
        return {"buckets": trend}

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True))

    return app


def run_web(args) -> int:
    """Serve the web dashboard; args has db, proxy, policy, host, port."""
    import uvicorn

    app = create_app(args.db, args.proxy, args.policy)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
