"""FastAPI JSON API for the Conductor web dashboard."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from conductor.dashboard.data import (
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
from conductor.webui import agents_data
from conductor.webui.agents_data import request_row_to_dict


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
            total_cost, row_count, _null_count = c.execute(
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


class ProjectBody(BaseModel):
    path: str


class McpCustomBody(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)


def create_app(
    db_path: str,
    proxy_url: str,
    policy_path: str,
    project: str | None = None,
    home: str | None = None,
) -> FastAPI:
    app = FastAPI()
    home_path = agents_data.conductor_home(home)
    # Mutable project state for this process (also persisted on PUT).
    app.state.project = agents_data.resolve_project(project, home=home_path)
    app.state.proxy_url = proxy_url.rstrip("/")
    app.state.home = home_path

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
        return {"rows": [request_row_to_dict(r) for r in rows]}

    @app.get("/api/rows/recent")
    def api_rows_recent(n: int = 200) -> dict[str, list[dict[str, Any]]]:
        if not db_exists(db_path):
            return {"rows": []}
        try:
            rows = _with_db_retry(fetch_recent_rows, db_path, n)
        except sqlite3.OperationalError:
            return {"rows": []}
        return {"rows": [request_row_to_dict(r) for r in rows]}

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
        return request_row_to_dict(row)

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

    @app.get("/api/project")
    def api_project_get() -> dict[str, Any]:
        return app.state.project.to_dict()

    @app.put("/api/project")
    def api_project_put(body: ProjectBody) -> dict[str, Any]:
        try:
            info = agents_data.set_project(body.path, home=app.state.home)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        app.state.project = info
        return info.to_dict()

    @app.get("/api/agents")
    def api_agents() -> dict[str, Any]:
        project = app.state.project
        agents = agents_data.list_agents(project.path, app.state.proxy_url)
        return {
            "proxy_url": app.state.proxy_url,
            "project": project.to_dict(),
            "agents": [a.to_dict() for a in agents],
        }

    @app.get("/api/agents/{agent_id}/sessions")
    def api_agent_sessions(agent_id: str, limit: int = 8, mode: str = "cli") -> dict[str, Any]:
        if agents_data.agent_by_id(agent_id) is None:
            raise HTTPException(status_code=404, detail="unknown agent")
        try:
            preview = agents_data.fetch_agent_sessions(
                db_path, agent_id, mode=mode, limit=limit
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent") from None
        except sqlite3.OperationalError:
            preview = agents_data.SessionPreview(
                agent_id=agent_id, mode=mode, routed_model=None, rule=None, rows=[], empty=True
            )
        return preview.to_dict()

    @app.get("/api/mcp")
    def api_mcp_get() -> dict[str, Any]:
        stored = agents_data.load_mcp(app.state.home)
        return {
            "integrations": agents_data.list_integrations(),
            "custom": stored["custom"],
        }

    @app.post("/api/mcp/custom")
    def api_mcp_add(body: McpCustomBody) -> dict[str, Any]:
        try:
            entry = agents_data.add_custom_mcp(body.name, body.url, home=app.state.home)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return entry

    @app.delete("/api/mcp/custom/{mcp_id}")
    def api_mcp_delete(mcp_id: str) -> dict[str, Any]:
        ok = agents_data.remove_custom_mcp(mcp_id, home=app.state.home)
        if not ok:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True))

    return app


def run_web(args) -> int:
    """Serve the web dashboard; args has db, proxy, policy, host, port, project."""
    import uvicorn

    project = getattr(args, "project", None) or os.environ.get("CONDUCTOR_PROJECT")
    home = os.environ.get("CONDUCTOR_HOME")
    app = create_app(
        args.db,
        args.proxy,
        args.policy,
        project=project,
        home=home,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
