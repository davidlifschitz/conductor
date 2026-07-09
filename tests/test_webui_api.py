"""Tests for conductor.webui JSON API."""

from __future__ import annotations

import time

import pytest
import yaml
from fastapi.testclient import TestClient

from conductor.ledger import Ledger
from conductor.webui.server import create_app


@pytest.fixture
def seeded_db(tmp_path):
    """Build a ledger with a known mix of rows (mirrors test_dashboard)."""
    db = tmp_path / "test.db"
    ledger = Ledger(db)
    now = time.time()
    old = now - 10 * 86400

    ledger.record(
        ts=old,
        harness="old-harness/1",
        tag=None,
        rule="default",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-haiku-4-5",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.001,
        latency_ms=200,
        stream=0,
        status=200,
        est_input_tokens=100,
    )
    ledger.record(
        ts=now - 100,
        harness="claude-cli/1.2",
        tag=None,
        rule="default",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-haiku-4-5",
        input_tokens=1204,
        output_tokens=310,
        cost_usd=0.0021,
        latency_ms=842,
        stream=0,
        status=200,
        est_input_tokens=1200,
    )
    ledger.record(
        ts=now - 90,
        harness="claude-cli/1.2",
        tag=None,
        rule="planning-language",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-sonnet-4-6",
        input_tokens=8401,
        output_tokens=1922,
        cost_usd=0.0412,
        latency_ms=5100,
        stream=0,
        status=200,
        est_input_tokens=8400,
    )
    ledger.record(
        ts=now - 80,
        harness="claude-cli/1.2",
        tag=None,
        rule="escalated:truncated",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-sonnet-4-6",
        input_tokens=455,
        output_tokens=388,
        cost_usd=0.0071,
        latency_ms=1922,
        stream=0,
        status=200,
        est_input_tokens=460,
    )
    ledger.record(
        ts=now - 70,
        harness="aider/0.60",
        tag=None,
        rule="default",
        requested_model="mystery-model",
        routed_model="mystery-model",
        input_tokens=10022,
        output_tokens=2113,
        cost_usd=None,
        latency_ms=900,
        stream=0,
        status=200,
        est_input_tokens=10000,
    )
    ledger.record(
        ts=now - 60,
        harness="claude-cli/1.2",
        tag=None,
        rule="default",
        requested_model="claude-haiku-4-5",
        routed_model="claude-haiku-4-5",
        input_tokens=50,
        output_tokens=0,
        cost_usd=0.0001,
        latency_ms=100,
        stream=0,
        status=500,
        est_input_tokens=50,
    )
    return db


@pytest.fixture
def client(seeded_db, tmp_path, monkeypatch):
    from conductor.dashboard.data import Health

    policy = tmp_path / "policy.yaml"
    policy.write_text(
        yaml.dump(
            {
                "escalation": {
                    "ladder": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-fable-5"]
                }
            }
        )
    )
    monkeypatch.setattr(
        "conductor.webui.server.fetch_health",
        lambda url, timeout=0.8: Health(
            up=True, default_model="claude-haiku-4-5", error=None
        ),
    )
    app = create_app(str(seeded_db), "http://localhost:8484", str(policy))
    return TestClient(app)


def test_rows_cursor_after_id(client):
    r = client.get("/api/rows", params={"after_id": 0})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 6
    assert [row["id"] for row in rows] == list(range(1, 7))

    max_id = rows[-1]["id"]
    r2 = client.get("/api/rows", params={"after_id": max_id})
    assert r2.status_code == 200
    assert r2.json()["rows"] == []


def test_rows_recent_order_and_limit(client):
    r = client.get("/api/rows/recent", params={"n": 3})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 3
    assert [row["id"] for row in rows] == [4, 5, 6]


def test_row_by_id_and_404(client):
    r = client.get("/api/rows/4")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 4
    assert body["rule"] == "escalated:truncated"
    assert "escalated" in body

    r404 = client.get("/api/rows/9999")
    assert r404.status_code == 404
    assert r404.json() == {"detail": "not found"}


def test_summary_counts(client):
    r = client.get("/api/summary", params={"days": 7})
    assert r.status_code == 200
    s = r.json()
    assert s["total_calls"] == 5
    assert s["escalation_count"] == 1
    assert s["error_count"] == 1


def test_escalated_bool(client):
    r = client.get("/api/rows/4")
    assert r.json()["escalated"] is True

    r2 = client.get("/api/rows/2")
    assert r2.json()["escalated"] is False


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["up"] is True
    assert body["default_model"] == "claude-haiku-4-5"
    assert body["error"] is None


def test_ladder(client):
    r = client.get("/api/ladder")
    assert r.status_code == 200
    assert r.json()["ladder"] == [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
    ]


def test_spend_trend_buckets(client, seeded_db):
    r = client.get("/api/spend_trend", params={"days": 7, "buckets": 7})
    assert r.status_code == 200
    buckets = r.json()["buckets"]
    assert len(buckets) == 7

    timestamps = [b["ts"] for b in buckets]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == 7

    total = sum(b["cost"] or 0.0 for b in buckets)
    assert total == pytest.approx(0.0505, rel=1e-3)


def test_missing_db_empty_shapes(tmp_path, monkeypatch):
    from conductor.dashboard.data import Health

    missing = tmp_path / "missing.db"
    policy = tmp_path / "policy.yaml"
    policy.write_text("escalation:\n  ladder: []\n")
    monkeypatch.setattr(
        "conductor.webui.server.fetch_health",
        lambda url, timeout=0.8: Health(up=False, default_model=None, error="ConnectError"),
    )
    app = create_app(str(missing), "http://localhost:8484", str(policy))
    client = TestClient(app)

    assert client.get("/api/rows").json() == {"rows": []}
    assert client.get("/api/rows/recent").json() == {"rows": []}

    summary = client.get("/api/summary").json()
    assert summary["total_calls"] == 0
    assert summary["escalation_count"] == 0
    assert summary["error_count"] == 0
    assert summary["by_model"] == []
    assert summary["by_rule"] == []

    trend = client.get("/api/spend_trend", params={"days": 1, "buckets": 4}).json()
    assert len(trend["buckets"]) == 4
    assert all(b["cost"] == 0.0 for b in trend["buckets"])

    assert client.get("/api/rows/1").status_code == 404

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ladder").status_code == 200
